"""Native queue/phase hooks and per-invocation Prompt Worker admission."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path

from .gpu_gate import GPUGate

_gate = None
_lease = ContextVar("aj_native_gpu_lease", default=None)


def current_lease():
    return _lease.get()


@contextmanager
def native_lease(descriptor):
    token = _lease.set(descriptor)
    try:
        yield
    finally:
        _lease.reset(token)


class LeaseSubprocess:
    """Keep the kernel lease in the actual CLI child even if its parent dies.

    Only the pinned llama_cli module's local subprocess binding is wrapped;
    Python's subprocess module and unrelated calls are never monkeypatched.
    All native invocation/parsing/timeout/cleanup logic remains the same.
    """
    def __init__(self, module):
        self.module = module
    def __getattr__(self, name):
        return getattr(self.module, name)
    def Popen(self, *args, **kwargs):
        descriptor = current_lease()
        if descriptor is not None:
            kwargs["pass_fds"] = tuple(sorted(set(kwargs.get("pass_fds", ())) | {descriptor}))
            kwargs["close_fds"] = True
        return self.module.Popen(*args, **kwargs)


def generate_native(native_class, values, descriptor):
    namespace = native_class.generate.__globals__["run_llama_cli"].__globals__
    if not isinstance(namespace["subprocess"], LeaseSubprocess):
        namespace["subprocess"] = LeaseSubprocess(namespace["subprocess"])
    with native_lease(descriptor):
        return native_class().generate(**values)


def gate():
    global _gate
    if _gate is None:
        import folder_paths
        import comfy.model_management as memory
        def check():
            memory.throw_exception_if_processing_interrupted()
        _gate = GPUGate(Path(folder_paths.get_output_directory()) / ".aj-gpu", check=check)
    return _gate


def prompt_lease(headroom_mb, allow_sampling, timeout):
    from server import PromptServer
    if not hasattr(PromptServer.instance, "prompt_queue"):
        raise ValueError("native Prompt Worker ownership is unavailable")
    return gate().prompt(headroom_mb, allow_sampling, timeout)


def install_renderer(server, port):
    render_port = int(os.environ.get("RENDER_PORT") or "8188")
    prompt_port = int(os.environ.get("PROMPT_PORT") or "8189")
    if not 1 <= render_port <= 65535 or not 1 <= prompt_port <= 65535 or render_port == prompt_port:
        raise ValueError("GPU gate requires distinct configured worker ports")
    if port != render_port:
        return
    gpu = gate()
    gpu.start_renderer()
    queue = server.prompt_queue
    original_get, original_done, original_send = queue.get, queue.task_done, server.send_sync
    graph = {}
    healthy = True
    sampling_node = None

    def get(timeout=None):
        # Startup/settled idle ownership is backed by the actual native queue,
        # not by a controller's possibly stale WebSocket event.
        if healthy and not queue.currently_running:
            gpu.transition("idle")
        result = original_get(timeout)
        if result is not None:
            graph.clear()
            graph.update(result[0][2])
            gpu.transition("conditioning")
        return result

    def task_done(item_id, history_result, status, process_item=None):
        nonlocal healthy
        # Preserve the journal's fsync-before-ownership-release contract.
        result = original_done(item_id, history_result, status, process_item)
        settled = status._asdict() if status else {}
        healthy = settled.get("completed") is True and settled.get("status_str") == "success"
        gpu.transition("idle" if healthy else "recovery")
        return result

    def send_sync(event, data, sid=None):
        nonlocal sampling_node
        if event == "executing":
            identifier = str(data.get("node"))
            node = graph.get(identifier, {})
            kind = node.get("class_type")
            sampling_node = identifier if kind == "SamplerCustomAdvanced" else None
            # ComfyUI may load the transformer onto CUDA inside the sampler.
            # An executing event alone does not prove post-load free VRAM.
            # Open only after the sampler reports actual progress.
            phase = "decoding" if kind in {
                "VAEDecode", "VAEDecodeAudio", "CreateVideo", "SaveVideo", "AJVideoBoundary"} else "unknown"
            gpu.transition(phase)
        elif event == "progress" and sampling_node is not None and str(data.get("node")) == sampling_node:
            if type(data.get("value")) is int and data["value"] > 0:
                gpu.transition("sampling")
        elif event in {"execution_error", "execution_interrupted"}:
            sampling_node = None
            gpu.transition("recovery")
        return original_send(event, data, sid)

    queue.get, queue.task_done, server.send_sync = get, task_done, send_sync
