"""Explicit per-instance Docker stages. No provider API or render submission."""
from __future__ import annotations

import argparse
import csv
import fcntl
import getpass
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import threading

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
sys.path.insert(0, str(PACKAGE))
import deployment
import runtime_config
import sageattention_runtime as sage
from provision_models import disk_plan, default_lora_destinations
from model_downloads import partial_bytes
from app.artifacts import signature, validate_manifest

ROOT = Path('/workspace')
SEED = Path('/opt/aj/seed/ComfyUI')
IMAGE = Path('/etc/aj-image.json')
BOOT = Path('/run/aj-boot-id')
RESERVE = 32 * 1024**3
MUTABLE = {'models', 'input', 'output', 'temp-render', 'temp-prompt', 'user-render', 'user-prompt', 'temp', 'user'}


def environment():
    # Fixed paths: operator shell settings cannot move weights to a second store
    # or propagate download/provider credentials to service workers.
    env = {key: os.environ[key] for key in ('SSL_CERT_FILE',
        'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY') if key in os.environ}
    env.update(LANG='C.UTF-8', LC_ALL='C.UTF-8',
        PATH=str(Path(sys.executable).parent) + ':/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        CUDA_HOME='/usr/local/cuda', LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64',
        WORKSPACE=str(ROOT), H3_PERSISTENT_ROOT=str(ROOT), H3_PERSISTENCE_MODE='ephemeral',
        COMFY_ROOT=str(ROOT / 'ComfyUI'), PANEL_ROOT=str(ROOT / 'H3_VAST_MOBILE'),
        PYTHON_BIN=sys.executable, COMFY_PYTHON=sys.executable, H3_CUDA_VERSION='13.0',
        COMFY_MODELS_DIR=str(ROOT / 'ComfyUI/models'), COMFY_INPUT_DIR=str(ROOT / 'ComfyUI/input'),
        COMFY_OUTPUT_DIR=str(ROOT / 'ComfyUI/output'), PID_DIR=str(ROOT / '.h3-service-pids'),
        RENDER_PORT='8188', PROMPT_PORT='8189', H3_PANEL_PORT='7860',
        RENDER_COMFY_URL='http://127.0.0.1:8188', PROMPT_COMFY_URL='http://127.0.0.1:8189',
        H3_PANEL_URL='http://127.0.0.1:7860', H3_ENABLE_TERMINAL='0', H3_ALLOW_SUBMISSIONS='0',
        SERVICE_CTL=str(ROOT / 'H3_VAST_MOBILE/scripts/service_ctl.sh'), VAST_CLI='/bin/false',
        H3_INSTALL_TIMING_FILE=str(ROOT / 'install-timings.jsonl'),
        H3_MODEL_CHECKSUMS_FILE='', H3_PERSISTENT_VOLUME_PROOF='')
    return env


def download_progress():
    _, _, items = selected_models()
    observed = 0
    for item in items.values():
        path = ROOT / 'ComfyUI/models' / item['destination']
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = partial_bytes(ROOT / 'ComfyUI/models', item)
        observed += min(size, item['size_bytes'])
    total = sum(item['size_bytes'] for item in items.values())
    print(f'Model transfer / hash verification: {observed:,} / {total:,} bytes present; integrity PASS waits for completion.', flush=True)


def run(args, *, token=None, progress=False):
    env = environment()
    if token:
        env['HF_TOKEN'] = token
    stop = threading.Event()
    reporter = None
    if progress:
        def report():
            while not stop.is_set():
                try:
                    download_progress()
                except (OSError, ValueError):
                    print('Transfer/hash verification running; byte snapshot unavailable.', flush=True)
                if stop.wait(15):
                    break
        reporter = threading.Thread(target=report, daemon=True)
        reporter.start()
    try:
        subprocess.run([str(v) for v in args], check=True, env=env)
    finally:
        stop.set()
        if reporter is not None:
            reporter.join(timeout=2)


def output(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=30,
                                   env=environment()).strip()


def bootstrap():
    """Small immutable links; real mutable directories live on the instance disk."""
    ROOT.mkdir(parents=True, exist_ok=True)
    repo = ROOT / 'ziomalskit'
    source = PACKAGE.parent
    if not repo.exists() and not repo.is_symlink():
        repo.symlink_to(source, target_is_directory=True)
    if repo.resolve() != source:
        raise ValueError('Existing /workspace/ziomalskit is unrelated; use a fresh instance disk')
    comfy = ROOT / 'ComfyUI'
    if comfy.is_symlink():
        raise ValueError('ComfyUI must be a real directory on the instance disk')
    comfy.mkdir(exist_ok=True)
    for child in SEED.iterdir():
        if child.name in MUTABLE:
            continue
        target = comfy / child.name
        if target.is_symlink() and target.resolve() == child.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise ValueError('Existing ComfyUI files are unrelated; preserve them and use a fresh disk')
        target.symlink_to(child, target_is_directory=child.is_dir())
    for name in MUTABLE:
        target = comfy / name
        if target.is_symlink():
            raise ValueError('Mutable ComfyUI paths must be real instance directories')
        target.mkdir(exist_ok=True)
        if target.stat().st_dev != ROOT.stat().st_dev:
            raise ValueError('Mutable data must share the /workspace instance disk')
    panel = ROOT / 'H3_VAST_MOBILE'
    revision = json.loads(IMAGE.read_text())['revision']
    release = panel / '.aj-docker-image'
    if not panel.exists():
        deployment.deploy(PACKAGE, panel)
        runtime_config.atomic_private_text(release, revision)
    elif panel.is_symlink() or not (panel / 'docker/instance.py').is_file():
        raise ValueError('Existing panel is unrelated; use a fresh disk')
    elif not release.is_file() or release.read_text() != revision:
        raise ValueError('Panel belongs to another image; preserve the disk and use its matching image')


def cgroup_limits(root=Path('/sys/fs/cgroup'), proc=Path('/proc')):
    """Bound by affinity and every visible current/ancestor resource limit."""
    if not (root / 'cgroup.controllers').is_file():
        return cgroup_v1_limits(root, proc)
    mounts = [line.split() for line in (proc / 'self/mountinfo').read_text().splitlines() if ' - cgroup2 ' in line]
    membership = next(line.split(':', 2)[2] for line in (proc / 'self/cgroup').read_text().splitlines() if line.startswith('0::'))
    if len(mounts) != 1 or mounts[0][4] != str(root):
        raise ValueError('Cannot resolve cgroup v2 mount')
    fsroot = mounts[0][3]
    if membership == '/':
        current = root
    elif fsroot == '/':
        current = root / membership.lstrip('/')
    elif membership == fsroot or membership.startswith(fsroot.rstrip('/') + '/'):
        current = root / membership[len(fsroot):].lstrip('/')
    else:
        raise ValueError('Cannot map current cgroup; ask the host for actual allocation limits')
    current = current.resolve()
    if not current.is_relative_to(root.resolve()) or not current.is_dir():
        raise ValueError('Cannot resolve current cgroup')
    ram = next(int(line.split()[1]) * 1024 for line in (proc / 'meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    cpu = float(len(os.sched_getaffinity(0)))
    rows = []
    while True:
        row = {'path': str(current)}
        for name in ('memory.max', 'memory.high', 'memory.current', 'cpu.max'):
            row[name] = (current / name).read_text().strip()
        for key in ('memory.max', 'memory.high'):
            if row[key] != 'max':
                ram = min(ram, max(0, int(row[key]) - int(row['memory.current'])))
        quota, period = row['cpu.max'].split()
        if quota != 'max':
            cpu = min(cpu, int(quota) / int(period))
        rows.append(row)
        if current == root.resolve():
            break
        current = current.parent
    return ram, cpu, rows


def cgroup_v1_limits(root, proc):
    """Resolve both legacy controllers; retain soft RAM budgets conservatively."""
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)

    memberships = {}
    for line in (proc / 'self/cgroup').read_text().splitlines():
        _, controllers, path = line.split(':', 2)
        for controller in controllers.split(','):
            memberships[controller] = path
    mounts = []
    for line in (proc / 'self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        fields, filesystem = before.split(), after.split()
        if filesystem[0] == 'cgroup':
            mounts.append((unescape(fields[3]), Path(unescape(fields[4])),
                           set(filesystem[2].split(','))))
    ram = next(int(line.split()[1]) * 1024 for line in (proc / 'meminfo').read_text().splitlines()
               if line.startswith('MemAvailable:'))
    cpu = float(len(os.sched_getaffinity(0)))
    rows = []
    for controller in ('memory', 'cpu'):
        candidates = [mount for mount in mounts if controller in mount[2]
                      and mount[1].resolve().is_relative_to(root.resolve())]
        if len(candidates) != 1 or controller not in memberships:
            raise ValueError('Cannot resolve cgroup v1 ' + controller + ' controller')
        fsroot, mount, _ = candidates[0]
        membership = memberships[controller]
        if membership == '/':
            current = mount
        elif fsroot == '/':
            current = mount / membership.lstrip('/')
        elif membership == fsroot or membership.startswith(fsroot.rstrip('/') + '/'):
            current = mount / membership[len(fsroot):].lstrip('/')
        else:
            raise ValueError('Cannot map current cgroup v1 ' + controller)
        mount, current = mount.resolve(), current.resolve()
        if not current.is_relative_to(mount) or not current.is_dir():
            raise ValueError('Cannot resolve current cgroup v1 ' + controller)
        while True:
            row = {'version': 1, 'controller': controller, 'path': str(current)}
            if controller == 'memory':
                for name in ('memory.limit_in_bytes', 'memory.soft_limit_in_bytes', 'memory.usage_in_bytes'):
                    row[name] = (current / name).read_text().strip()
                usage = int(row['memory.usage_in_bytes'])
                hard = int(row['memory.limit_in_bytes'])
                soft = int(row['memory.soft_limit_in_bytes'])
                if min(usage, hard, soft) < 0:
                    raise ValueError('Invalid cgroup v1 memory limit')
                # PAGE_COUNTER_MAX is effectively unlimited; MemAvailable remains
                # an independent bound. A positive soft limit is an extra budget.
                for limit in [hard] + ([soft] if soft > 0 else []):
                    if limit < 2**60:
                        ram = min(ram, max(0, limit - usage))
            else:
                for name in ('cpu.cfs_quota_us', 'cpu.cfs_period_us'):
                    row[name] = (current / name).read_text().strip()
                quota, period = int(row['cpu.cfs_quota_us']), int(row['cpu.cfs_period_us'])
                if period <= 0 or quota < -1 or quota == 0:
                    raise ValueError('Invalid cgroup v1 CPU quota')
                if quota != -1:
                    cpu = min(cpu, quota / period)
            rows.append(row)
            if current == mount:
                break
            current = current.parent
    return ram, cpu, rows


def selected_models():
    manifest = json.loads((PACKAGE / 'config/models_manifest.json').read_text())
    optional = default_lora_destinations(PACKAGE / 'config/lora_registry.json')
    items = {key: item for key, item in validate_manifest(manifest).items()
             if item['required'] or item['destination'] in optional}
    return manifest, optional, items


def health():
    ram, cpu, rows = cgroup_limits()
    print(json.dumps({'available_ram_bytes': ram, 'effective_cpus': cpu, 'visible_cgroups': rows}), flush=True)
    if ram < 96 * 1024**3 or cpu < 8:
        raise ValueError('Need at least 96 GiB available RAM and 8 effective CPUs; choose a larger allocation')
    mount = json.loads(output(['findmnt', '--json', '--target', str(ROOT), '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS']))['filesystems'][0]
    if 'rw' not in mount['options'].split(','):
        raise ValueError('/workspace is not writable')
    manifest, optional, _ = selected_models()
    plan = disk_plan(manifest, ROOT / 'ComfyUI/models', optional_destinations=optional)
    print(json.dumps({'mount': mount, 'disk': plan, 'reserve_bytes': RESERVE}), flush=True)
    if plan['free_bytes'] < plan['minimum_free_bytes'] + RESERVE:
        raise ValueError('Insufficient instance disk: remaining weights + 32 GiB reserve required; retain partial downloads')
    gpu = list(csv.reader(output(['nvidia-smi', '--query-gpu=uuid,name,driver_version,memory.total,compute_cap',
                                 '--format=csv,noheader,nounits']).splitlines()))
    if len(gpu) != 1 or len(gpu[0]) != 5:
        raise ValueError('Expose exactly one RTX PRO 6000 Blackwell GPU with NVIDIA Container Toolkit')
    uuid, name, driver, memory, capability = map(str.strip, gpu[0])
    if 'RTX PRO 6000' not in name or capability != '12.0' or float(memory) < 90000:
        raise ValueError('Need RTX PRO 6000 Blackwell 96 GB, sm_120; no model substitution is allowed')
    if tuple(map(int, driver.split('.'))) < (580, 65, 6):
        raise ValueError('Host driver >=580.65.06 required for CUDA 13.0; a container cannot upgrade the host driver')
    print(json.dumps({'gpu': uuid, 'name': name, 'driver': driver}), flush=True)
    run([sys.executable, PACKAGE / 'scripts/process_identity.py', 'support'])
    run([sys.executable, PACKAGE / 'scripts/runtime_dependencies.py', 'verify', PACKAGE, ROOT / 'ComfyUI', '13.0'])
    run([sys.executable, PACKAGE / 'scripts/llama_runtime.py', ROOT / 'ComfyUI/custom_nodes/ComfyUI-LLM-text-processor'])
    # Validates toolkit/torch/ABI/file hashes and runs the exact pinned KJ kernel.
    sage.install(ROOT / 'ComfyUI', Path('/opt/aj/build'), '13.0')
    print('Runtime health PASS; Full renders and long-sequence VRAM still PENDING.', flush=True)


def identity():
    models = ROOT / 'ComfyUI/models'
    info = models.stat()
    return {'image': json.loads(IMAGE.read_text())['revision'], 'boot': BOOT.read_text(),
            'models_device': info.st_dev, 'models_inode': info.st_ino}


def model_signatures():
    _, _, items = selected_models()
    result = {}
    for item in items.values():
        path = ROOT / 'ComfyUI/models' / item['destination']
        if path.is_symlink() or path.stat().st_size != item['size_bytes']:
            raise ValueError('Model changed or missing; rerun aj-docker models')
        result[item['destination']] = signature(path.stat())
    return result


def ready_path():
    return ROOT / '.aj-docker-ready.json'


def service_check():
    saved = json.loads(ready_path().read_text())
    if saved != {'identity': identity(), 'models': model_signatures()}:
        raise ValueError('Instance/model identity changed; rerun aj-docker init and aj-docker models')


def write_environment():
    path = ROOT / 'H3_VAST_MOBILE/runtime.env'
    env = environment()
    if not path.exists():
        password = getpass.getpass('Choose private panel password (at least 16 characters): ')
        if len(password) < 16 or '\n' in password or '\r' in password:
            raise ValueError('Panel password must have at least 16 characters and no newlines')
        env['H3_PANEL_PASSWORD'] = password
    runtime_config.write_runtime(path, env)


def fixtures():
    import base64
    import wave
    root = ROOT / 'ComfyUI/input'
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    for i in range(1, 7):
        path = root / f'__h3_preflight_{i}.png'
        if not path.exists():
            path.write_bytes(png)
    path = root / '__h3_silence_1s.wav'
    if not path.exists():
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000); handle.writeframes(bytes(32000))


def execute(action):
    if action == 'plan':
        manifest, optional, _ = selected_models()
        print(json.dumps({'cpu_plan_only': True, 'gpu_smoke': 'PENDING',
            'disk': disk_plan(manifest, ROOT / 'ComfyUI/models', optional_destinations=optional)}, indent=2))
        return
    if action == 'service-check':
        service_check()
        return
    bootstrap()
    if action in {'init', 'models'}:
        ready_path().unlink(missing_ok=True)
        health()  # Always before token input or downloader, including resume.
        write_environment()  # Always restores submissions OFF.
        if action == 'init':
            print('No weights downloaded. To explicitly transfer/resume models: aj-docker models')
            return
        token = getpass.getpass('One-time HF read token (Enter if unnecessary): ')
        try:
            run([Path('/opt/aj/seed/.aj-download-venv/bin/python'), PACKAGE / 'scripts/provision_models.py',
                '--manifest', PACKAGE / 'config/models_manifest.json', '--models-root', ROOT / 'ComfyUI/models',
                '--toolchain', '/opt/aj/build/llama.cpp-' + runtime_config.LLAMA_TAG], token=token, progress=True)
        finally:
            token = ''
        fixtures()
        runtime_config.atomic_private_text(ready_path(), json.dumps({'identity': identity(), 'models': model_signatures()}))
        print('Model size/SHA256 verification PASS. Next: aj-docker start (submissions OFF).')
    elif action == 'start':
        service_check()
        # Existing ownership, locking, pidfds and service readiness remain authoritative.
        write_environment()
        run(['bash', ROOT / 'H3_VAST_MOBILE/scripts/service_ctl.sh', 'start', 'all'])
        # Apply submissions OFF even when retrying against an already-running
        # panel. Comfy workers keep their original owned processes.
        run(['bash', ROOT / 'H3_VAST_MOBILE/scripts/service_ctl.sh', 'restart', 'panel'])
        run(['bash', ROOT / 'H3_VAST_MOBILE/scripts/preflight.sh'])
        run(['bash', ROOT / 'H3_VAST_MOBILE/scripts/smoke_test.sh'])
        print('Preflight/smoke PASS; submissions OFF. Use the private SSH panel tunnel. GPU render acceptance PENDING.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'init', 'models', 'start', 'service-check'])
    action = parser.parse_args().action
    # SSH does not necessarily inherit Docker ENV. Restore only the fixed image
    # runtime paths and strip unrelated caller credentials before any imports.
    env = environment()
    os.environ.clear()
    os.environ.update(env)
    if action in {'plan', 'service-check'}:
        execute(action)
    else:
        # Do not replace/unlink lock inodes or bypass the service manager lock.
        ROOT.mkdir(parents=True, exist_ok=True)
        with (ROOT / '.aj-docker.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            execute(action)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError, subprocess.SubprocessError, StopIteration, KeyError) as error:
        # Third-party exceptions can include credentials/URLs; print no raw error.
        if isinstance(error, ValueError):
            print('AJ Docker blocked: ' + str(error), file=sys.stderr)
        else:
            print('AJ Docker blocked: missing image/runtime capability or failed stage. Check GPU exposure, driver, cgroups, disk and stage output; rerun the same command. No render submitted.', file=sys.stderr)
        raise SystemExit(1)
