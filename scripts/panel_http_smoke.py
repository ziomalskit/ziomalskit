"""Functional HTTP smoke checks against real uvicorn, without submitting work."""
from __future__ import annotations
import base64
from pathlib import Path
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5'


def serve(stage: Path, password: str, port: int, log):
    environment = os.environ.copy()
    environment.update({
        'H3_PANEL_USER': 'h3', 'H3_PANEL_PASSWORD': password,
        'COMFY_INPUT_DIR': str(stage / 'input'), 'COMFY_ROOT': str(stage / 'comfy'),
        'COMFY_MODELS_DIR': str(stage / 'comfy/models'),
        'COMFY_OUTPUT_DIR': str(stage / 'comfy/output'),
        'VAST_CLI': '/usr/bin/false', 'SERVICE_CTL': '/usr/bin/false',
        'RENDER_RESTART_CMD': '/usr/bin/false', 'PROMPT_RESTART_CMD': '/usr/bin/false',
        'H3_PERSISTENT_ROOT': '', 'H3_PERSISTENCE_MODE': '',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    return subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
                             '--host', '127.0.0.1', '--port', str(port)],
                            cwd=stage, env=environment, stdout=log, stderr=log)


def ready(client: httpx.Client, process: subprocess.Popen):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('HTTP smoke server exited during startup')
        try:
            return client.get('/api/config')
        except httpx.ConnectError:
            time.sleep(0.1)
    raise RuntimeError('HTTP smoke server did not become ready')


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    checks = 0
    with tempfile.TemporaryDirectory(prefix='aj-http-') as temporary:
        stage = Path(temporary) / 'panel'
        shutil.copytree(SOURCE, stage, ignore=shutil.ignore_patterns('__pycache__', 'runtime.env'))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        password = secrets.token_urlsafe(24)
        with (Path(temporary) / 'server.log').open('wb') as log, httpx.Client(
            base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False
        ) as public:
            process = serve(stage, password, port, log)
            try:
                assert ready(public, process).status_code == 401
                checks += 1
                assert public.post('/api/upload', files={'file': ('blocked.txt', b'blocked')}).status_code == 401
                checks += 1
                assert not (stage / 'input/blocked.txt').exists()
                checks += 1
                with httpx.Client(base_url=public.base_url, auth=('h3', password), timeout=5, trust_env=False) as client:
                    config = client.get('/api/config')
                    assert config.status_code == 200 and config.json()['version'] == 'pre-rental-final-rc5'
                    checks += 1
                    assert config.json()['batch'] == {'prompts_per_batch': 10, 'auto_approved': 5, 'review': 5}
                    checks += 1
                    page = client.get('/')
                    assert page.status_code == 200 and '<html' in page.text.lower()
                    checks += 1
                    assert client.get('/api/jobs').json() == {'jobs': [], 'batches': []}
                    checks += 1
                    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
                    upload = client.post('/api/upload', files={'file': ('../anchor.png', png, 'image/png')})
                    assert upload.status_code == 200 and upload.json()['filename'] == 'anchor.png'
                    assert (stage / 'input/anchor.png').read_bytes() == png
                    assert not (stage / 'anchor.png').exists()
                    checks += 1
                    duplicate = client.post('/api/upload', files={'file': ('anchor.png', png, 'image/png')})
                    assert duplicate.status_code == 200 and duplicate.json()['filename'] != 'anchor.png'
                    assert (stage / 'input/anchor.png').read_bytes() == png
                    checks += 1
                    assert client.post('/api/batches', json={'prompt': ''}).status_code == 400
                    checks += 1
                    assert client.post('/api/batches', json={'prompt': 'scene', 'pictures': ['anchor.png']}).status_code == 400
                    checks += 1
                    assert client.post('/api/vast/action', json={'action': 'stop_now', 'confirm': 'incorrect'}).status_code == 400
                    checks += 1
                    assert client.post('/api/vast/action', json={'action': 'destroy_now', 'confirm': 'incorrect'}).status_code == 400
                    checks += 1
                    assert client.post('/api/vast/action', json={'action': 'destroy_after_queue_keep_data'}).status_code == 409
                    checks += 1
                    assert client.get('/api/jobs').json() == {'jobs': [], 'batches': []}
                    checks += 1
            finally:
                stop(process)
            process = serve(stage, '', port, log)
            try:
                assert ready(public, process).status_code == 503
                checks += 1
            finally:
                stop(process)
    print(f'HTTP SMOKE PASS: {checks} functional checks; no ComfyUI submission or Vast lifecycle action executed')


if __name__ == '__main__':
    main()
