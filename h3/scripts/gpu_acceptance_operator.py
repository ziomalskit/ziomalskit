#!/usr/bin/env python3
"""Readonly Phase 1 preflight and explicit telemetry; never runs models or Vast."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import time

try:
    from . import model_acceptance as models
    from .check_model_completion import bounded_text
except ImportError:
    import model_acceptance as models
    from check_model_completion import bounded_text

PACKAGE = models.PACKAGE
TEMPLATE = PACKAGE / 'config/gpu_acceptance_session.example.json'
V20_SHA = '276621a1992a8b8da40d981a79417fd5eb3617b44166d890c8c82ffd8eb96222'
UTC = timezone.utc


def timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('UTC timestamp required')
    result = datetime.fromisoformat(value[:-1] + '+00:00')
    if result.tzinfo != UTC:
        raise ValueError('timezone-aware UTC timestamp required')
    return result


def number(value, *, positive=False, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('nonnegative finite number required')
    if positive and value <= 0 or integer and type(value) is not int:
        raise ValueError('positive/integer limit required')
    return Decimal(str(value))


def read_session(path):
    models.no_symlink_prefix(Path(path).absolute().parent)
    return json.loads(bounded_text(path))


def validate_session(session, phase='A', now=None):
    """No guessed approval, quote, reserve or time/budget limit."""
    expected = json.loads(TEMPLATE.read_text())
    def shape(value, example, prefix=''):
        if not isinstance(value, dict) or set(value) != set(example):
            raise ValueError('unknown/missing session fields')
        missing = []
        for key, sample in example.items():
            name = prefix + key
            if isinstance(sample, dict):
                missing.extend(shape(value[key], sample, name + '.'))
            elif value[key] is None and not name.startswith('billing.'):
                missing.append(name)
        return missing
    missing = shape(session, expected)
    if type(session['schema_version']) is not int or session['schema_version'] != 1 or session['scope'] != 'gpu_acceptance_phase1':
        raise ValueError('wrong Phase 1 scope')
    if any(type(value) is not bool for value in session['approvals'].values()):
        raise ValueError('explicit boolean approvals required')
    needed = ['first_cost', 'services_off', 'provider_attachment']
    if phase == 'B': needed.append('prepare_models')
    if phase == 'C': needed.append('model_probes')
    missing.extend('approval.' + key for key in needed if not session['approvals'][key])
    if missing:
        return missing
    if not re.fullmatch('[0-9a-f]{40}', session['checkout_sha']):
        raise ValueError('exact checkout SHA required')
    models.isolated_root(Path(session['root']))
    store = session['storage']
    mount = Path(store['mount'])
    if not mount.is_absolute() or '..' in mount.parts or mount == Path('/'):
        raise ValueError('separate actual mount required')
    models.no_symlink_prefix(mount)
    if not Path(session['root']).is_relative_to(mount) or Path(session['root']) == mount:
        raise ValueError('acceptance root must be inside retained mount')
    for key in ('volume_id', 'machine_id'):
        if not isinstance(store[key], str) or not re.fullmatch('[0-9]+', store[key]):
            raise ValueError('real provider IDs required')
    for key in ('device', 'inode', 'build_reserve_bytes', 'evidence_reserve_bytes'):
        number(store[key], positive=True, integer=True)
    if store['fstype'] not in {'ext4', 'xfs', 'btrfs', 'zfs'}:
        raise ValueError('retained filesystem required')
    for key in ('source', 'fsroot'):
        if not isinstance(store[key], str) or not store[key] or '\n' in store[key]:
            raise ValueError('exact mount identity required')
    hardware = session['hardware']
    if (not isinstance(hardware['gpu_name'], str) or not hardware['gpu_name'].startswith('NVIDIA RTX PRO 6000 Blackwell')
            or hardware['cuda'] != '13.0'):
        raise ValueError('Phase 1 targets RTX PRO 6000 Blackwell and configured CUDA 13.0')
    for key in ('min_vram_mib', 'min_ram_bytes', 'min_cpu_cores'):
        number(hardware[key], positive=True, integer=True)
    for key, value in session['limits'].items():
        number(value, positive=True, integer=key.endswith(('seconds', 'bytes')) and key != 'poll_seconds')
    limits = session['limits']
    if (limits['max_download_seconds'] + limits['max_build_seconds'] > limits['max_session_seconds']
            or limits['max_probe_seconds'] > limits['max_session_seconds']
            or limits['max_preflight_seconds'] > limits['max_session_seconds']
            or limits['max_review_seconds'] > limits['max_session_seconds']
            or limits['poll_seconds'] > min(limits['max_probe_seconds'], limits['max_download_seconds'])
            or limits['shutdown_grace_seconds'] >= limits['max_probe_seconds']):
        raise ValueError('inconsistent operator time limits')
    quote = session['quote']
    if not isinstance(quote['reference'], str) or not quote['reference'].strip():
        raise ValueError('operator quote reference required')
    if timestamp(quote['valid_until_utc']) <= (now or datetime.now(UTC)):
        raise ValueError('expired quote')
    for key, value in quote.items():
        if key not in {'reference', 'valid_until_utc'}:
            number(value, integer=key.endswith('bytes_cap'))
    return []


def cost_ceiling(session):
    """USD; provider billing GB is explicitly decimal 10**9 bytes."""
    q, limits = session['quote'], session['limits']
    hours = number(limits['max_session_seconds'], positive=True, integer=True) / 3600
    retention = number(q['retention_hours'])
    return (hours * sum(number(q[key]) for key in ('gpu_usd_per_hour', 'instance_disk_usd_per_hour', 'volume_usd_per_hour'))
            + retention * sum(number(q[key]) for key in ('stopped_disk_usd_per_hour', 'volume_usd_per_hour'))
            + number(q['download_bytes_cap']) / 10**9 * number(q['ingress_usd_per_gb'])
            + number(q['upload_bytes_cap']) / 10**9 * number(q['egress_usd_per_gb'])
            + number(q['external_preparation_usd']))


def plan(session, phase='A', now=None, *, live=False):
    items, approval = models.load_selection()
    missing = validate_session(session, phase, now)
    result = {'status': 'BLOCKED' if missing else 'PLAN_READY', 'phase': phase, 'blocked': missing,
              'submissions': False, 'gpu_acceptance': 'NOT RUN', 'production_gate_retired': False,
              'approved_artifacts': [item['id'] for item in items],
              'known_model_bytes': sum(item['size_bytes'] for item in items)}
    if missing:
        # A human may review a bounded quote before renting. Actual mount/device
        # facts remain pending until access exists; never fabricate them or issue GO.
        try:
            cost = cost_ceiling(session)
            limit = number(session['limits']['max_total_usd'], positive=True)
            rate = sum(number(session['quote'][key]) for key in ('gpu_usd_per_hour', 'instance_disk_usd_per_hour', 'volume_usd_per_hour'))
            cap = number(session['limits']['max_instance_usd_per_hour'], positive=True)
            if timestamp(session['quote']['valid_until_utc']) <= (now or datetime.now(UTC)):
                raise ValueError('expired quote')
            result['cost_ceiling_usd'] = str(cost)
            if cost > limit: result['blocked'].append('BUDGET_EXCEEDS_LIMIT')
            if rate > cap: result['blocked'].append('INSTANCE_RATE_EXCEEDS_LIMIT')
        except (TypeError, KeyError, ValueError):
            pass
        return result
    disk = models.plan(Path(session['root']), items, approval)
    reserve = session['storage']['build_reserve_bytes'] + session['storage']['evidence_reserve_bytes']
    required = reserve if live else disk['minimum_free_bytes'] + reserve
    result.update(minimum_free_bytes=required, operator_min_free_bytes=session['limits']['min_free_bytes'],
                  free_bytes=disk['free_bytes'], cost_ceiling_usd=str(cost_ceiling(session)))
    for condition, reason in (
        (sum(number(session['quote'][key]) for key in ('gpu_usd_per_hour', 'instance_disk_usd_per_hour', 'volume_usd_per_hour'))
         > number(session['limits']['max_instance_usd_per_hour']), 'INSTANCE_RATE_EXCEEDS_LIMIT'),
        (cost_ceiling(session) > number(session['limits']['max_total_usd']), 'BUDGET_EXCEEDS_LIMIT'),
        (session['quote']['download_bytes_cap'] < disk['missing_download_bytes'], 'TRANSFER_CAP_TOO_SMALL'),
        (disk['free_bytes'] < max(required, session['limits']['min_free_bytes']), 'INSUFFICIENT_DISK')):
        if condition: result['blocked'].append(reason)
    if result['blocked']: result['status'] = 'BLOCKED'
    return result


def command(args):
    # Only fixed readonly diagnostic commands; never pass inherited credentials.
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=30,
                                   env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C', 'LC_ALL': 'C'})


def observe(session):
    mount = Path(session['storage']['mount'])
    info = mount.stat()
    filesystem = json.loads(command(['findmnt', '--json', '--target', str(mount),
        '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS,FSROOT']))['filesystems']
    if len(filesystem) != 1: raise ValueError('ambiguous mount')
    gpu_rows = list(csv.reader(command(['nvidia-smi', '--query-gpu=uuid,name,driver_version,memory.total,memory.used,memory.free,compute_cap',
                                      '--format=csv,noheader,nounits']).splitlines()))
    if len(gpu_rows) != 1 or len(gpu_rows[0]) != 7: raise ValueError('one visible GPU required')
    gpu = dict(zip(('uuid', 'name', 'driver', 'total_mib', 'used_mib', 'free_mib', 'compute_cap'), map(str.strip, gpu_rows[0])))
    for key in ('total_mib', 'used_mib', 'free_mib'):
        value = float(gpu[key]); number(value); gpu[key] = value
    processes = command(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits']).splitlines()
    pids = {int(value.strip()) for value in processes if value.strip()}
    toolkit = re.search(r'release (\d+\.\d+)', command(['nvcc', '--version']))
    if not toolkit: raise ValueError('unknown toolkit')
    mem = dict(re.findall(r'^(MemAvailable):\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M))
    ram = int(mem['MemAvailable']) * 1024
    cap = Path('/sys/fs/cgroup/memory.max')
    if cap.exists() and cap.read_text().strip() != 'max':
        ram = min(ram, max(0, int(cap.read_text()) - int(cap.with_name('memory.current').read_text())))
    cpu = len(os.sched_getaffinity(0))
    quota = Path('/sys/fs/cgroup/cpu.max')
    if quota.exists():
        value, period = quota.read_text().split()
        if value != 'max': cpu = min(cpu, int(value) / int(period))
    occupied = []
    for port in (7860, 8188, 8189):
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1', port))
            except OSError: occupied.append(port)
    return {'mount': filesystem[0], 'device': info.st_dev, 'inode': info.st_ino,
            'container_root_device': Path('/').stat().st_dev, 'gpu': gpu, 'gpu_pids': sorted(pids),
            'cuda': toolkit.group(1), 'kernel': platform.release(), 'python': list(sys.version_info[:3]),
            'ram_available_bytes': ram, 'cpu_cores': cpu, 'free_bytes': shutil.disk_usage(mount).free,
            'production_ports': occupied}


def inspect(session, phase='A', *, allowed_pids=(), observer=observe, now=None, live=False, verify_models=False):
    result = plan(session, phase, now, live=live)
    if result['blocked']: return result
    observed = observer(session); store = session['storage']; hardware = session['hardware']
    gpu = observed['gpu']; mount = observed['mount']
    mismatched_mount = (any(mount.get(key) != store[expected] for key, expected in
        (('target', 'mount'), ('source', 'source'), ('fstype', 'fstype'), ('fsroot', 'fsroot')))
        or observed['device'] != store['device'] or observed['inode'] != store['inode']
        or observed['device'] == observed['container_root_device'] or 'rw' not in mount.get('options', '').split(','))
    driver = tuple(map(int, gpu['driver'].split('.')))
    kernel = tuple(map(int, re.match(r'(\d+)\.(\d+)', observed['kernel']).groups()))
    for condition, reason in (
        (mismatched_mount, 'PERSISTENT_MOUNT_CHANGED_OR_UNPROVEN'),
        (gpu['name'] != hardware['gpu_name'] or gpu['compute_cap'] != '12.0' or gpu['total_mib'] < hardware['min_vram_mib'], 'GPU_MISMATCH'),
        (driver < (580, 65, 6) or observed['cuda'] != hardware['cuda'], 'DRIVER_CUDA_MISMATCH'),
        # Isolated native acceptance has no production service/group restarts.
        # Full deployment still requires >=6.9 and the actual pidfd group probe.
        (kernel < (6, 8) or observed['python'] < [3, 11, 0], 'KERNEL_PYTHON_MISMATCH'),
        (observed['ram_available_bytes'] < hardware['min_ram_bytes'] or observed['cpu_cores'] < hardware['min_cpu_cores'], 'RAM_CPU_INSUFFICIENT'),
        (observed['free_bytes'] < max(result['minimum_free_bytes'], session['limits']['min_free_bytes']), 'INSUFFICIENT_DISK'),
        (bool(set(observed['gpu_pids']) - set(allowed_pids)), 'UNEXPECTED_GPU_PROCESS'),
        (bool(observed['production_ports']), 'PRODUCTION_SERVICE_PRESENT')):
        if condition: result['blocked'].append(reason)
    if phase == 'C':
        items, approval = models.load_selection()
        root = models.require_store(Path(session['root']), approval)
        for item in items:
            models.check_model_path(root, item)
            path = root / 'models' / item['destination']
            if not path.is_file() or path.stat().st_size != item['size_bytes']:
                result['blocked'].append('MODELS_INCOMPLETE'); break
        if not result['blocked'] and verify_models:
            result['file_runtime_verification'] = models.verify_models(root, items, approval, cuda_version=session['hardware']['cuda'])
    result.update(status='BLOCKED' if result['blocked'] else 'PREFLIGHT_READY', observations=observed,
                  model_sha256_verification='VERIFIED' if 'file_runtime_verification' in result else 'NOT RUN; use existing --verify before model probes')
    return result


def live_budget(session, phase, now, elapsed):
    billing = session['billing']; limits = session['limits']; q = session['quote']
    start = timestamp(billing['session_started_utc']); seen = timestamp(billing['observed_at_utc'])
    age = (now - seen).total_seconds(); session_age = (now - start).total_seconds()
    if age < 0 or session_age < 0 or seen < start or age > limits['billing_max_age_seconds']:
        raise ValueError('stale/invalid actual billing observation')
    actual = number(billing['observed_total_usd'])
    transfer = number(q['download_bytes_cap']) / 10**9 * number(q['ingress_usd_per_gb']) + number(q['upload_bytes_cap']) / 10**9 * number(q['egress_usd_per_gb'])
    retention = number(q['retention_hours']) * (number(q['stopped_disk_usd_per_hour']) + number(q['volume_usd_per_hour']))
    rate = sum(number(q[key]) for key in ('gpu_usd_per_hour', 'instance_disk_usd_per_hour', 'volume_usd_per_hour'))
    estimate = actual + Decimal(str(age)) / 3600 * rate + transfer + retention + number(q['external_preparation_usd'])
    phase_limit = limits['max_probe_seconds'] if phase == 'C' else limits['max_build_seconds'] + limits['max_download_seconds']
    if session_age >= limits['max_session_seconds'] or elapsed >= phase_limit or estimate >= number(limits['max_total_usd']):
        raise ValueError('approved time/cost ceiling reached')
    return {'observed_bill_usd': str(actual), 'estimated_usd_with_reserves': str(estimate),
            'billing_age_seconds': age, 'session_elapsed_seconds': session_age}


def watch(path, session, phase, output, allowed_pids=(), *, observer=observe, clock=time.monotonic,
          wall_clock=lambda: datetime.now(UTC), pause=time.sleep):
    """Collect only; STOP never signals inference or changes provider billing."""
    if phase not in {'B', 'C'}: raise ValueError('watch requires an individually approved B or C phase')
    start = clock()
    result = inspect(session, phase, allowed_pids=allowed_pids, observer=observer, now=wall_clock(), live=True, verify_models=phase=='C')
    if result['blocked']: return result
    output = Path(output).absolute(); root = Path(session['root'])
    models.no_symlink_prefix(output.parent)
    evidence = root / 'evidence'
    if not output.parent.is_relative_to(evidence) or not output.parent.is_dir():
        raise ValueError('prepare a private evidence directory inside the acceptance root')
    if output.parent.stat().st_uid != os.geteuid() or output.parent.stat().st_mode & 0o077:
        raise ValueError('evidence directory must be owned/private')
    peak = 0; status = 'STOP'; samples = 0; stop_reasons = []
    verified_signatures = {item['id']: item['signature'] for item in
                           result.get('file_runtime_verification', {}).get('model_files', [])}
    if phase == 'C' and set(verified_signatures) != models.APPROVED:
        raise ValueError('full model verification signatures required before C observation')
    live_budget(session, phase, wall_clock(), clock() - start)
    last_bill_time = timestamp(session['billing']['observed_at_utc'])
    last_bill_total = number(session['billing']['observed_total_usd'])
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        def save(record):
            stream.write(json.dumps(record, sort_keys=True) + '\n'); stream.flush(); os.fsync(stream.fileno())
        save({'event': 'START', 'phase': phase, 'checkout_sha': session['checkout_sha'], 'submissions': False,
              'gpu_acceptance': 'NOT RUN', 'production_gate_retired': False, 'status': 'NOT RUN'})
        try:
            while True:
                # Billing counters may be refreshed by the operator, but approvals,
                # source lock, quote and limits cannot change during observation.
                stop_reasons = ['SESSION_FILE_CHANGED_OR_INVALID']
                latest = read_session(path)
                if validate_session(latest, phase, wall_clock()):
                    raise ValueError('session fields/approvals became incomplete')
                if {k:v for k,v in latest.items() if k!='billing'} != {k:v for k,v in session.items() if k!='billing'}:
                    raise ValueError('session approval changed')
                stop_reasons = ['BILLING_COUNTER_CHANGED_OR_MOVED_BACKWARD']
                billing = latest['billing']
                if (billing['session_started_utc'] != session['billing']['session_started_utc']
                        or timestamp(billing['observed_at_utc']) < last_bill_time
                        or number(billing['observed_total_usd']) < last_bill_total):
                    raise ValueError('session start/billing counters cannot reset during observation')
                stop_reasons = ['TIME_OR_BUDGET_GATE']
                budget = live_budget(latest, phase, wall_clock(), clock() - start)
                stop_reasons = ['OBSERVATION_OR_EVIDENCE_FAILURE']
                check = inspect(session, phase, allowed_pids=allowed_pids, observer=observer, now=wall_clock(), live=True)
                if check['blocked']:
                    stop_reasons = check['blocked']
                    raise ValueError('readonly safety gate failed')
                if phase == 'C':
                    stop_reasons = ['MODEL_CHANGED_AFTER_VERIFICATION']
                    items, _ = models.load_selection()
                    for item in items:
                        current = (root/'models'/item['destination']).lstat()
                        if models.signature(current) != verified_signatures[item['id']]:
                            raise ValueError('model changed after full SHA256 verification')
                stop_reasons = ['OBSERVATION_OR_EVIDENCE_FAILURE']
                gpu = check['observations']['gpu']; peak = max(peak, gpu['used_mib']); samples += 1
                save({'event': 'SAMPLE', 'utc': wall_clock().isoformat(), 'gpu': gpu,
                      'gpu_pids': check['observations']['gpu_pids'], **budget})
                last_bill_time = timestamp(billing['observed_at_utc'])
                last_bill_total = number(billing['observed_total_usd'])
                print(json.dumps({'status':'OBSERVING', 'sampled_peak_vram_mib':peak, **budget}), flush=True)
                pause(session['limits']['poll_seconds'])
        except KeyboardInterrupt:
            status = 'INTERRUPTED'
            stop_reasons = ['INTERRUPTED_BY_OPERATOR']
        except Exception:
            status = 'STOP'
        try:
            save({'event': 'END', 'status': status, 'sampled_peak_vram_mib': peak, 'samples': samples,
                  'gpu_acceptance': 'NOT RUN', 'production_gate_retired': False, 'stop_reasons': stop_reasons})
        except OSError:
            status = 'STOP; evidence incomplete'
    return {'status':status, 'gpu_acceptance':'NOT RUN', 'production_gate_retired':False,
            'sampled_peak_vram_mib':peak, 'billing_continues':True, 'stop_reasons':stop_reasons,
            'operator_action':'Stop owned process; verify retained data; separately stop rented compute in provider console. No automatic lifecycle action.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--phase', choices=['A','B','C','D'], default='A')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--inspect', action='store_true')
    actions.add_argument('--watch', type=Path)
    parser.add_argument('--allow-gpu-pid', type=int, action='append', default=[])
    args = parser.parse_args(argv)
    os.environ.update(H3_ALLOW_SUBMISSIONS='0', H3_ALLOW_DIAGNOSTIC_SUBMISSIONS='0', H3_ENABLE_TERMINAL='0',
                      VAST_CLI='/usr/bin/false', SERVICE_CTL='/usr/bin/false')
    for key in ('HF_TOKEN','HF_HUB_TOKEN','HUGGING_FACE_HUB_TOKEN','VAST_API_KEY'):
        os.environ.pop(key, None)
    try:
        if args.allow_gpu_pid and (not args.watch or args.phase!='C' or any(pid<=0 for pid in args.allow_gpu_pid)):
            raise ValueError('GPU PID observations require explicit C watch; never grant production admission')
        session = read_session(args.session)
        if not plan(session,args.phase)['blocked']:
            checkout = command(['git','-C',str(PACKAGE.parent),'rev-parse','HEAD']).strip()
            dirty = command(['git','-C',str(PACKAGE.parent),'status','--porcelain','--untracked-files=normal']).strip()
            baseline = PACKAGE/'workflows/H3_v20_heretic_MASTER_DURATION_EXACT.json'
            if checkout!=session['checkout_sha'] or dirty or hashlib.sha256(baseline.read_bytes()).hexdigest()!=V20_SHA:
                raise ValueError('reviewed checkout or canonical v20 changed')
        if args.watch:
            result=watch(args.session,session,args.phase,args.watch,args.allow_gpu_pid)
        elif args.inspect:
            result=inspect(session,args.phase,verify_models=args.phase=='C')
        else:
            result=plan(session,args.phase)
        print(json.dumps(result,sort_keys=True))
        return 0 if result['status'] in {'PLAN_READY','PREFLIGHT_READY'} else 1
    except KeyboardInterrupt:
        print('INTERRUPTED: no GPU acceptance or lifecycle action; provider billing may continue.',file=sys.stderr)
        return 130
    except Exception:
        # Never echo caller JSON, diagnostic stderr, prompts, URLs or credentials.
        print('BLOCKED: missing/invalid approval, source, quote, budget, hardware, mount, models or evidence. No GPU acceptance or lifecycle action.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
