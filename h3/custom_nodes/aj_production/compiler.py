"""One-shot local JSON completion using the SAME pinned llama.cpp engine.

The pinned CLI itself wraps this server engine but discards finish status. This
adapter uses its sibling executable for one request, then reaps it. No shared
server, native source patch, model download, or revision change is involved.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request

from .temporal import final_answer

REVISION = '60eeeb6082c1126bb8bc72902c83123cd056811b'
MAX_RESPONSE = 16 * 1024 * 1024


def completion_response(body):
    if not isinstance(body, dict) or body.get('error'):
        raise ValueError('native compiler completion failed')
    detail = body.get('__verbose', {})
    choices = body.get('choices')
    if (not isinstance(detail, dict) or detail.get('stop') is not True or detail.get('stop_type') != 'eos'
            or detail.get('truncated') is not False or not isinstance(choices, list) or len(choices) != 1):
        raise ValueError('native compiler lacks natural, untruncated completion evidence')
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get('finish_reason') != 'stop' or choice.get('index') != 0:
        raise ValueError('native compiler completion did not finish naturally')
    message = choice.get('message')
    if not isinstance(message, dict) or message.get('role') != 'assistant' or message.get('tool_calls'):
        raise ValueError('invalid native compiler final channel')
    content, reasoning = message.get('content'), message.get('reasoning_content', '')
    if not isinstance(reasoning, str):
        raise ValueError('invalid native compiler reasoning channel')
    final_answer(content)
    return content, reasoning, ''


def verified_server(cli):
    binary = Path(cli).with_name('llama-server')
    if binary.is_symlink() or not binary.is_file():
        raise ValueError('verified pinned completion runtime missing; run provisioning')
    digest = hashlib.sha256()
    with binary.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    digest = digest.hexdigest()
    metadata = binary.with_suffix('.build.json')
    if metadata.is_symlink():
        raise ValueError('unsafe completion runtime metadata')
    receipt = json.loads(metadata.read_text())
    if (receipt.get('commit') != REVISION or receipt.get('tag') != 'b10472' or receipt.get('sha256') != digest
            or receipt.get('arch') != '120' or receipt.get('cuda') != os.environ.get('H3_CUDA_VERSION', '13.0')):
        raise ValueError('completion runtime differs from pinned verified build')
    version = subprocess.check_output([str(binary), '--version'], stderr=subprocess.STDOUT, timeout=30)
    if REVISION[:7].encode() not in version:
        raise ValueError('completion runtime reports a different revision')
    return binary


def post_completion(url, payload, timeout):
    request = urllib.request.Request(url + '/v1/chat/completions', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    # Loopback never goes through deployment HTTP proxy configuration.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise ValueError('oversized native compiler response')
    return json.loads(body)


def run_completion(command, messages, values):
    server = verified_server(command[0])
    arguments, index = [], 1
    while index < len(command):
        flag = command[index]
        if flag == '--single-turn':
            index += 1
        elif flag in {'-f', '-sysf'}:
            index += 2
        else:
            arguments.append(flag)
            index += 1
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    command = [str(server), *arguments, '--host', '127.0.0.1', '--port', str(port), '--parallel', '1', '--no-context-shift']
    payload = {'messages': messages, 'stream': False, 'verbose': True, 'max_tokens': values['max_tokens'],
               'temperature': values['temperature'], 'top_p': values['top_p'], 'top_k': values['top_k'],
               'repeat_penalty': values['repeat_penalty']}
    import comfy.model_management as memory
    deadline = time.monotonic() + values['timeout_seconds']
    process, thread = None, None
    future = concurrent.futures.Future()

    def check():
        if memory.processing_interrupted():
            memory.throw_exception_if_processing_interrupted()
            raise RuntimeError('native compiler interrupted')
        if time.monotonic() >= deadline:
            raise TimeoutError('native compiler timed out')
        if process.poll() is not None:
            raise RuntimeError('native compiler runtime exited before completion')

    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False, close_fds=True)
        url = f'http://127.0.0.1:{port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            check()
            try:
                with opener.open(url + '/health', timeout=.2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.05)

        def request():
            try:
                future.set_result(post_completion(url, payload, max(.1, deadline - time.monotonic())))
            except Exception:
                future.set_exception(ValueError('native compiler JSON transport/runtime failure'))
        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        while not future.done():
            check()
            time.sleep(.05)
        check()
        return completion_response(future.result())
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            else:
                process.wait(timeout=3)
        if thread is not None:
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError('native compiler request did not close after runtime cleanup')


def generate(native_class, values):
    namespace = native_class.generate.__globals__
    for key, options in (('model', 'model_options'), ('mmproj', 'mmproj_options'), ('system_prompt', 'system_prompt_options')):
        if values[key] not in namespace[options]():
            raise ValueError('compiler input is not a registered native selection')
    extra = namespace['split_extra_args'](values.get('extra_args', ''))
    if (len(extra) != 4 or extra[:3] != ['--reasoning-format', 'deepseek', '--reasoning-budget']
            or not extra[3].isdigit() or not 0 < int(extra[3]) <= 16384
            or values['max_tokens'] < int(extra[3]) + 8192 + 64):
        raise ValueError('compiler requires the frozen reasoning/final-answer budget')
    arguments = {key: values[key] for key in ('prompt', 'max_tokens', 'temperature', 'top_p', 'top_k', 'repeat_penalty',
                 'ctx_size', 'memory_mode', 'n_gpu_layers', 'n_cpu_moe_layers', 'seed', 'reasoning')}
    system_path = namespace['full_system_prompt_path'](values['system_prompt'])
    command, cleanup = namespace['build_command'](model_path=namespace['full_model_path'](values['model']),
        mmproj_path=None, system_prompt_path=system_path, image=None, extra_args=extra, **arguments)
    try:
        messages = []
        if system_path is not None:
            messages.append({'role': 'system', 'content': Path(system_path).read_text()})
        prompt_path = Path(command[command.index('-f') + 1])
        messages.append({'role': 'user', 'content': prompt_path.read_text()})
        return run_completion(command, messages, values)
    finally:
        for path in cleanup:
            if path is not None:
                path.unlink(missing_ok=True)
