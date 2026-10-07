"""Run the working controller locally, using isolated state and no submissions."""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
from types import SimpleNamespace
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
sys.path.insert(0, str(ROOT / 'h3/scripts'))
import process_identity as identity
SOURCE = ROOT / 'h3'
LOCAL = ROOT / '.local'
STAGE = LOCAL / 'panel'
PID_FILE = LOCAL / 'panel.pid'
AUTH_FILE = LOCAL / 'dev-auth.json'
LOG_FILE = LOCAL / 'panel.log'


@contextlib.contextmanager
def control_lock():
    LOCAL.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCAL / 'panel.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        identity.close_preserving(fd)


def process_args(port=7860):
    return SimpleNamespace(pid_file=PID_FILE, service='panel', python=sys.executable,
        panel_root=str(STAGE), comfy_root=str(LOCAL / 'ComfyUI'), port=port, log_file=LOG_FILE)


def owned_pid() -> int | None:
    try:
        pid, fd = identity.checked_pidfd(process_args())
    except identity.StaleIdentity:
        return None
    identity.close_preserving(fd)
    return pid


def owned_readiness(port, auth):
    args = process_args(port)
    pid, fd = identity.checked_pidfd(args)
    try:
        identity.require_owned_listener(args, pid, fd)
        check_http(port, auth)
        identity.require_owned_listener(args, pid, fd)
    finally:
        identity.close_preserving(fd)


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
        'H3_ALLOW_SUBMISSIONS': '0',
    })
    return environment


def stop_unlocked() -> None:
    try:
        pid, fd = identity.checked_pidfd(process_args(), allow_gate=True)
    except identity.StaleIdentity:
        PID_FILE.unlink(missing_ok=True)
    else:
        try:
            identity.stop_group(pid, fd)
            PID_FILE.unlink(missing_ok=True)
        finally:
            identity.close_preserving(fd)
    print('Local development panel stopped.')


def stop() -> None:
    with control_lock():
        stop_unlocked()


def main_unlocked() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'status', 'check'))
    parser.add_argument('--port', type=int, default=int(os.getenv('AJ_DEV_PORT', '7860')))
    args = parser.parse_args()
    if args.action == 'stop':
        stop_unlocked()
        return
    if args.action == 'status':
        print('Local development panel running.' if owned_pid() else 'Local development panel stopped.')
        return
    auth = credentials()
    if args.action == 'check':
        owned_readiness(args.port, auth)
        print('Authenticated HTML and configuration requests passed. GPU workers are not installed.')
        return
    if owned_pid():
        owned_readiness(args.port, auth)
        print('Local development panel already running and responsive.')
        return
    shutil.copytree(SOURCE, STAGE, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'runtime.env', 'state'))
    (LOCAL / 'ComfyUI/input').mkdir(parents=True, exist_ok=True)
    (LOCAL / 'ComfyUI/output').mkdir(parents=True, exist_ok=True)
    (LOCAL / 'ComfyUI/models').mkdir(parents=True, exist_ok=True)
    launch = process_args(args.port)
    launch.environment = runtime_environment(auth, args.port)
    identity.require_group_control()
    identity.require_free_service_port(args.port)
    identity.launch_process(launch, [sys.executable, '-m', 'uvicorn', 'app.main:app',
        '--app-dir', str(STAGE), '--host', '127.0.0.1', '--port', str(args.port)])
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                owned_readiness(args.port, auth)
                break
            except (OSError, AssertionError, identity.IndeterminateIdentity):
                time.sleep(.1)
        else:
            raise RuntimeError('Panel owned HTTP readiness timed out. Inspect .local/panel.log.')
    except BaseException as primary:
        try:
            stop_unlocked()
        except BaseException as cleanup:
            primary.add_note(f'Panel startup cleanup failed: {cleanup!r}')
        raise
    print('Local development panel started; authenticated HTML/configuration checks passed.')
    print('Authentication is local in ignored .local/dev-auth.json. GPU workers are not installed.')


def main() -> None:
    with control_lock():
        main_unlocked()


if __name__ == '__main__':
    main()
