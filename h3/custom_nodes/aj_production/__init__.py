"""AJ-owned lifecycle dependencies. No downloads, inference or Vast actions."""
import uuid
import re
from pathlib import Path
from .temporal import normalize_plan, final_timeline, final_answer, validate_duration
from .execution_journal import ExecutionJournal, PROTOCOL
from .compiler import generate as generate_compiler, completion_response

from aiohttp import web
import comfy.model_management
import nodes
from server import PromptServer

SERVICE_EPOCH = uuid.uuid4().hex
_journal = None


def execution_journal():
    global _journal
    if _journal is None:
        import folder_paths
        server = PromptServer.instance
        port = getattr(server, 'port', None)
        if port is None:
            from comfy.cli_args import args
            port = args.port
        _journal = ExecutionJournal(server.prompt_queue, Path(folder_paths.get_output_directory()) / '.aj-results' / str(port))
    return _journal


# Pinned PromptServer constructs its queue before custom-node imports, and the
# prompt worker starts afterward. Install before any worker can cache get/done.
# CPU-only node stubs intentionally have no queue; real workers always do.
if hasattr(PromptServer.instance, 'prompt_queue'):
    execution_journal()


@PromptServer.instance.routes.get('/aj/execution_protocol')
async def execution_protocol(_request):
    journal = execution_journal()
    return web.json_response({'protocol': PROTOCOL, 'quiesced': journal.quiesced})


@PromptServer.instance.routes.get('/aj/results/{prompt_id}')
async def execution_result(request):
    return web.json_response(execution_journal().read(request.match_info['prompt_id']))


@PromptServer.instance.routes.post('/aj/cancel/{prompt_id}')
async def execution_cancel(request):
    return web.json_response(execution_journal().cancel(request.match_info['prompt_id']))


@PromptServer.instance.routes.post('/aj/quiesce')
async def execution_quiesce(request):
    body = await request.json()
    if not isinstance(body.get('owned_ids'), list) or any(not isinstance(value, str) for value in body['owned_ids']):
        raise web.HTTPBadRequest()
    return web.json_response(execution_journal().quiesce(set(body['owned_ids'])))

# Native output-file framing in the pinned llama.cpp cli-context.cpp. This
# never mixes stderr or prompt/timing substrings into the final channel.
COMPILER_SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
                     "overall_soundscape", "non_diegetic_music")


def compiler_file_response(body, completion=None):
    # Framing alone is never completion evidence, including six valid headers.
    verified, _, _ = completion_response(completion)
    if not body.startswith("Assistant:\n") or not body.endswith("\n\n"):
        raise ValueError("incomplete native compiler output file")
    content = body[len("Assistant:\n"):].rstrip("\n")
    reasoning = ""
    if content.startswith("[Start thinking]\n\n"):
        content = content[len("[Start thinking]\n\n"):]
        candidates = []
        delimiter = "[End thinking]\n\n"
        for match in re.finditer(re.escape(delimiter), content):
            answer = content[match.end():]
            if answer.startswith("subject_definitions:"):
                candidates.append((content[:match.start()], answer))
        if len(candidates) != 1:
            raise ValueError("ambiguous native compiler reasoning boundary")
        reasoning, content = candidates[0]
        if "[Start thinking]" in reasoning or "[End thinking]" in reasoning:
            raise ValueError("nested native compiler reasoning boundary")
    final_answer(content)
    if content != verified:
        raise ValueError('compiler file differs from machine-readable final channel')
    return content, reasoning, ""


class AJCompilerTextProcessor:
    """Step 4 requires the pinned engine's machine-readable EOS receipt."""
    @classmethod
    def INPUT_TYPES(cls):
        return nodes.NODE_CLASS_MAPPINGS["LLMTextProcessor"].INPUT_TYPES()
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("RESPONSE", "REASONING", "PERF")
    FUNCTION = "generate"
    CATEGORY = "AJ/production"
    @classmethod
    def VALIDATE_INPUTS(cls, model, mmproj, system_prompt):
        return nodes.NODE_CLASS_MAPPINGS["LLMTextProcessor"].VALIDATE_INPUTS(model, mmproj, system_prompt)
    def generate(self, **values):
        if values.get("enable_processing", True) is not True or values.get("reasoning") != "on" or values.get("mmproj") != "none" or values.get("image") is not None:
            raise ValueError("compiler requires native thinking and text-only processing")
        return generate_compiler(nodes.NODE_CLASS_MAPPINGS['LLMTextProcessor'], values)


class AJFrozenDuration:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"legal_frame_count": ("INT",), "effective_duration_seconds": ("FLOAT",)}}
    RETURN_TYPES = ("INT", "FLOAT")
    FUNCTION = "value"
    CATEGORY = "AJ/production"
    def value(self, legal_frame_count, effective_duration_seconds):
        from .temporal import milliseconds
        validate_duration({"duration_contract": "v20_24fps_v1", "requested_duration_seconds": effective_duration_seconds,
                           "legal_frame_count": legal_frame_count, "effective_duration_seconds": effective_duration_seconds,
                           "effective_duration_ms": milliseconds(effective_duration_seconds)})
        return legal_frame_count, effective_duration_seconds


class AJCreativeTimelineGuard:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"creative_plan": ("STRING", {"forceInput": True}), "effective_duration_seconds": ("FLOAT",)}}
    RETURN_TYPES = ("STRING",)
    FUNCTION = "guard"
    CATEGORY = "AJ/production"
    def guard(self, creative_plan, effective_duration_seconds):
        return (normalize_plan(creative_plan, effective_duration_seconds),)


class AJFinalPromptTimeGuard:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"final_h3_prompt": ("STRING", {"forceInput": True}),
                             "canonical_creative_plan": ("STRING", {"forceInput": True}), "effective_duration_seconds": ("FLOAT",)}}
    RETURN_TYPES = ("STRING",)
    FUNCTION = "guard"
    CATEGORY = "AJ/production"
    def guard(self, final_h3_prompt, canonical_creative_plan, effective_duration_seconds):
        return (final_timeline(final_h3_prompt, canonical_creative_plan, effective_duration_seconds, canonicalize=True),)


@PromptServer.instance.routes.get("/aj/service_epoch")
async def service_epoch(_request):
    return web.json_response({"service_epoch": SERVICE_EPOCH})


def release_managed_models():
    # This affects only this ComfyUI process. The separate Prompt Worker has its
    # own model lifecycle. Conditioning tensors are deliberately retained.
    comfy.model_management.unload_all_models()
    comfy.model_management.soft_empty_cache()


class AJAnalysisText:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"multiline": True})}}
    RETURN_TYPES = ("STRING",)
    FUNCTION = "value"
    CATEGORY = "AJ/production"
    def value(self, text):
        return (text,)


class AJConditioningBoundary:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"first": ("CONDITIONING",), "second": ("CONDITIONING",),
                             "release_encoder": ("BOOLEAN", {"default": True})}}
    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "INT")
    FUNCTION = "release"
    CATEGORY = "AJ/production"
    @classmethod
    def IS_CHANGED(cls, **_kwargs):
        # A cached conditioning result must still release a previously resident
        # render model before the next profile's transformer can load.
        return float("nan")
    def release(self, first, second, release_encoder):
        if release_encoder:
            release_managed_models()
        return (first, second, 1)


class AJLateUNETLoader(nodes.UNETLoader):
    @classmethod
    def INPUT_TYPES(cls):
        result = super().INPUT_TYPES()
        result["required"]["after_conditioning"] = ("INT", {"forceInput": True})
        return result
    FUNCTION = "load_after_conditioning"
    CATEGORY = "AJ/production"
    def load_after_conditioning(self, unet_name, weight_dtype, after_conditioning):
        if after_conditioning != 1:
            raise ValueError("conditioning release boundary was not completed")
        return super().load_unet(unet_name, weight_dtype)


class AJVideoBoundary:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"video": ("VIDEO",), "release_models": ("BOOLEAN", {"default": True})}}
    RETURN_TYPES = ("VIDEO",)
    FUNCTION = "release"
    CATEGORY = "AJ/production"
    @classmethod
    def IS_CHANGED(cls, **_kwargs):
        return float("nan")
    def release(self, video, release_models):
        if release_models:
            release_managed_models()
        return (video,)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    AJAnalysisText, AJCompilerTextProcessor, AJFrozenDuration, AJCreativeTimelineGuard, AJFinalPromptTimeGuard,
    AJConditioningBoundary, AJLateUNETLoader, AJVideoBoundary)}
