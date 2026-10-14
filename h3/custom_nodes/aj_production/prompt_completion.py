"""Step 0–3: pinned native chat/vision with machine-readable completion proof.

The exact pinned CLI uses this same server engine and message representation.
Keep the pinned node's input validation, tensor conversion and command builder;
replace only its lossy stdout completion boundary. One invocation, one child,
one inherited GPU lease; no persistent inference server or engine modification.
"""
import base64
from pathlib import Path

from .compiler import natural_response, run_completion


def generate(native_class, values):
    namespace = native_class.generate.__globals__
    # Manual Final bypasses this worker entirely. A production model stage may
    # not relabel passthrough text as verified generation or cache it as analysis.
    if not values.get('enable_processing', True):
        raise ValueError('prompt stage requires native completion; passthrough is not generation evidence')
    for key, options in (('model', 'model_options'), ('mmproj', 'mmproj_options'), ('system_prompt', 'system_prompt_options')):
        if values[key] not in namespace[options]():
            raise ValueError('prompt input is not a registered native selection')
    arguments = {key: values[key] for key in ('prompt', 'max_tokens', 'temperature', 'top_p', 'top_k',
                 'repeat_penalty', 'ctx_size', 'memory_mode', 'n_gpu_layers', 'n_cpu_moe_layers', 'seed', 'reasoning')}
    system = namespace['full_system_prompt_path'](values['system_prompt'])
    command, cleanup = namespace['build_command'](
        model_path=namespace['full_model_path'](values['model']),
        mmproj_path=namespace['full_mmproj_path'](values['mmproj']), system_prompt_path=system,
        image=values.get('image'), extra_args=namespace['split_extra_args'](values.get('extra_args', '')), **arguments)
    try:
        messages = []
        if system is not None:
            messages.append({'role': 'system', 'content': Path(system).read_text()})
        text = Path(command[command.index('-f') + 1]).read_text()
        content = text
        if '--image' in command:
            # Same as cli_context::stage_media_file/push_user_message at the pin:
            # ordered image parts first, then the exact prompt text.
            content = [{'type': 'image_url', 'image_url': {'url': 'data:image/unknown;base64,' +
                       base64.b64encode(Path(path).read_bytes()).decode('ascii')}}
                       for path in command[command.index('--image') + 1].split(',')]
            content.append({'type': 'text', 'text': text})
        messages.append({'role': 'user', 'content': content})
        request_values = {**values, 'native_seed': int(command[command.index('--seed') + 1])}
        return run_completion(command, messages, request_values, response_parser=natural_response)
    finally:
        for path in cleanup:
            if path is not None:
                path.unlink(missing_ok=True)
