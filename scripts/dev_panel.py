"""Run the original controller locally, using isolated state and no Vast CLI."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5'
LOCAL = ROOT / '.local'
STAGE = LOCAL / 'panel'
PID_FILE = LOCAL / 'panel.pid'
AUTH_FILE = LOCAL / 'dev-auth.json'
LOG_FILE = LOCAL / 'panel.log'


def owned_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text())
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if str(STAGE).encode() in args and b'app.main:app' in args:
            return pid
    except (OSError, ValueError):
        pass
    return None


def credentials() -> dict:
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text())
    LOCAL.mkdir(exist_ok=True)
    auth = {'user': 'h3', 'password': secrets.token_urlsafe(32)}
    fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(auth, handle)
    return auth


def check_http(port: int, auth: dict) -> None:
    token = base64.b64encode(f"{auth['user']}:{auth['password']}".encode()).decode()
    for route in ('/', '/api/config'):
        req = urllib.request.Request(f'http://127.0.0.1:{port}{route}', headers={'Authorization': 'Basic ' + token})
        with urllib.request.urlopen(req, timeout=3) as response:
            body = response.read()
            assert response.status == 200
        if route == '/api/config':
            data = json.loads(body)
            assert data['version'] == 'pre-rental-final-rc5'
            assert data['batch'] == {'prompts_per_batch': 10, 'auto_approved': 5, 'review': 5}
        else:
            assert b'<html' in body.lower()


def runtime_environment(auth: dict, port: int) -> dict:
    environment = os.environ.copy()
    environment.update({
        'H3_PANEL_USER': auth['user'], 'H3_PANEL_PASSWORD': auth['password'],
        'COMFY_ROOT': str(LOCAL / 'ComfyUI'),
        'COMFY_INPUT_DIR': str(LOCAL / 'ComfyUI/input'),
        'COMFY_OUTPUT_DIR': str(LOCAL / 'ComfyUI/output'),
        'COMFY_MODELS_DIR': str(LOCAL / 'ComfyUI/models'),
        'RENDER_COMFY_URL': 'http://127.0.0.1:8188',
        'PROMPT_COMFY_URL': 'http://127.0.0.1:8189',
        'VAST_CLI': '/usr/bin/false',
        'SERVICE_CTL': '/usr/bin/false',
        'RENDER_RESTART_CMD': '/usr/bin/false',
        'PROMPT_RESTART_CMD': '/usr/bin/false',
        'H3_PERSISTENT_ROOT': '', 'H3_PERSISTENCE_MODE': '',
        'H3_PANEL_PORT': str(port),
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    return environment


def stop() -> None:
    pid = owned_pid()
    if pid is not None:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if owned_pid() is None:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('Panel did not terminate; preserved PID file for diagnosis')
    PID_FILE.unlink(missing_ok=True)
    print('Local development panel stopped.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'status', 'check'))
    parser.add_argument('--port', type=int, default=int(os.getenv('AJ_DEV_PORT', '7860')))
    args = parser.parse_args()
    if args.action == 'stop':
        stop()
        return
    if args.action == 'status':
        print('Local development panel running.' if owned_pid() else 'Local development panel stopped.')
        return
    auth = credentials()
    if args.action == 'check':
        check_http(args.port, auth)
        print('Authenticated HTML and configuration requests passed. GPU workers are not installed.')
        return
    if owned_pid():
        check_http(args.port, auth)
        print('Local development panel already running and responsive.')
        return
    shutil.copytree(SOURCE, STAGE, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'runtime.env'))
    (LOCAL / 'ComfyUI/input').mkdir(parents=True, exist_ok=True)
    (LOCAL / 'ComfyUI/output').mkdir(parents=True, exist_ok=True)
    (LOCAL / 'ComfyUI/models').mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open('ab') as log:
        process = subprocess.Popen([
            sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', str(STAGE),
            '--host', '127.0.0.1', '--port', str(args.port),
        ], cwd=STAGE, env=runtime_environment(auth, args.port),
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    PID_FILE.write_text(str(process.pid))
    for _ in range(100):
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise RuntimeError('Panel startup failed. Inspect .local/panel.log.')
        try:
            check_http(args.port, auth)
            break
        except (OSError, AssertionError):
            time.sleep(0.1)
    else:
        stop()
        raise RuntimeError('Panel HTTP readiness timed out. Inspect .local/panel.log.')
    print('Local development panel started; authenticated HTML/configuration checks passed.')
    print('Authentication is local in ignored .local/dev-auth.json. GPU workers are not installed.')


if __name__ == '__main__':
    main()
