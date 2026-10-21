#!/usr/bin/env python3
"""Curated native-model acceptance store; never authorizes production or inference."""
from __future__ import annotations

import argparse
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from app.artifacts import atomic_json, parent_directory, read_json, signature, validate_artifact, validate_manifest, verify_file
from app.production import read_profiles
try:
    from .model_downloads import ensure_download, partial_bytes
    from .runtime_config import LLAMA_COMMIT, LLAMA_TAG, LLM_NODE_COMMIT
    from .llama_runtime import verify_llama, sha256
    from .patch_llama_binary import patch_source
except ImportError:
    from model_downloads import ensure_download, partial_bytes
    from runtime_config import LLAMA_COMMIT, LLAMA_TAG, LLM_NODE_COMMIT
    from llama_runtime import verify_llama, sha256
    from patch_llama_binary import patch_source

SCOPE = 'native_llm_acceptance_only'
MARKER = '.aj-model-acceptance.json'
SHARED = {'joycaption', 'joycaption-mmproj', 'verifier', 'verifier-mmproj', 'gemma', 'gemma-mmproj', 'analysis-mmproj'}
ROUTES = {'h3_full': ('h3-analysis', 'h3-writer-replacement'),
          '10eros_full': ('10eros-analysis', '10eros-writer-replacement')}
APPROVED = SHARED | {identifier for pair in ROUTES.values() for identifier in pair}
FACTS = ('id', 'provider', 'repository', 'revision', 'repository_path', 'destination',
         'size_bytes', 'sha256', 'required', 'profiles', 'provenance_status')


def select_artifacts(manifest, approval, profiles):
    """Exact curated sources and routes from the strictly valid active manifest."""
    if (approval.get('schema_version') != 1 or approval.get('scope') != SCOPE
            or approval.get('submissions') is not False or approval.get('automatic_inference') is not False
            or approval.get('runtime') != {'llama_cpp_revision': LLAMA_COMMIT, 'llm_node_revision': LLM_NODE_COMMIT}
            or profiles.get('writer_runtime') != approval['runtime']):
        raise ValueError('acceptance scope/runtime approval differs')
    validate_manifest(manifest)  # Pending/unknown provenance still fails closed.
    entries, destinations = {}, set()
    for item in manifest.get('artifacts', []):
        validate_artifact(item)
        if item['id'] in entries or item['destination'] in destinations:
            raise ValueError('duplicate acceptance source identity/destination')
        entries[item['id']] = item
        destinations.add(item['destination'])
    approved = approval.get('approved_artifacts', [])
    if len(approved) != len(APPROVED) or {item['id'] for item in approved} != APPROVED:
        raise ValueError('only the explicitly approved eleven model artifacts are allowed')
    selected = []
    for expected in approved:
        validate_artifact({**expected, 'role': 'Approved native acceptance model', 'gpu_validation': 'pending'})
        actual = entries.get(expected['id'], {})
        if (actual.get('provider') != 'huggingface' or actual.get('required') is not True
                or actual.get('provenance_status') != 'verified_source_metadata'
                or not actual.get('destination', '').startswith('LLM/') or not actual['destination'].endswith('.gguf')
                or set(expected) != set(FACTS) or any(actual.get(key) != expected[key] for key in FACTS)):
            raise ValueError('selected acceptance model lacks exact approved source metadata')
        selected.append(copy.deepcopy(actual))
    by_id = {item['id']: item for item in selected}
    if set(approval.get('profiles', {})) != set(ROUTES):
        raise ValueError('acceptance profile approval missing')
    for name, (analysis, writer) in ROUTES.items():
        route, current = approval['profiles'][name], profiles['profiles'][name]
        if (route != {'analysis': analysis, 'writer': writer, 'projector': 'analysis-mmproj', 'writer_stages': current['writer_stages']}
                or current['compiler'] != 'profile_writer'
                or any('LLM/' + current[field] != by_id[identifier]['destination'] for field, identifier in
                       (('analysis_model', analysis), ('writer', writer), ('analysis_mmproj', 'analysis-mmproj')))):
            raise ValueError('acceptance profile route/stages differ from approved models')
    return selected


def load_selection(package=PACKAGE):
    manifest = json.loads((package / 'config/models_manifest.json').read_text())
    approval = json.loads((package / 'config/model_acceptance.json').read_text())
    profiles = read_profiles(package / 'config/production_profiles.json')
    return select_artifacts(manifest, approval, profiles), approval


def no_symlink_prefix(path):
    path = Path(path)
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('acceptance path has an unsafe ancestor')


def isolated_root(root, environment=None):
    env = os.environ if environment is None else environment
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts or len(root.parts) < 3:
        raise ValueError('acceptance needs its own absolute subdirectory')
    no_symlink_prefix(root)
    base = Path(env.get('H3_PERSISTENT_ROOT') or env.get('WORKSPACE') or '/workspace').resolve()
    protected = [PACKAGE, *[base / name for name in
        ('ComfyUI', 'H3_VAST_MOBILE', '.h3-service-pids', '.h3-venv', '.aj-download-venv', '.h3-build')]]
    protected.extend(Path(env[name]).absolute() for name in
        ('COMFY_ROOT', 'PANEL_ROOT', 'PID_DIR', 'H3_BUILD_ROOT', 'COMFY_MODELS_DIR', 'COMFY_INPUT_DIR', 'COMFY_OUTPUT_DIR') if env.get(name))
    protected = [path.resolve() for path in protected]
    if root == base or base.is_relative_to(root) or any(
        root == path or root.is_relative_to(path) or path.is_relative_to(root) for path in protected):
        raise ValueError('acceptance and production/package paths must be separate')
    for name in ('models', 'native', '.h3-venv', '.aj-download-venv', '.h3-build'):
        no_symlink_prefix(root / name)
    return root


def scope_identity(approval):
    return hashlib.sha256(json.dumps(approval, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def models_directory(root, environment=None):
    """An explicit shared store is the real ComfyUI models directory, not a link."""
    env = os.environ if environment is None else environment
    value = env.get('H3_MODEL_ACCEPTANCE_MODELS_ROOT')
    if not value:
        return Path(root) / 'models'
    path = Path(value)
    base = Path(env.get('H3_PERSISTENT_ROOT') or env.get('WORKSPACE') or '/workspace')
    comfy = Path(env.get('COMFY_ROOT') or base / 'ComfyUI')
    if (not path.is_absolute() or '..' in path.parts or path != comfy / 'models'
            or env.get('COMFY_MODELS_DIR', str(path)) != str(path)
            or path == root or path.is_relative_to(root) or Path(root).is_relative_to(path)
            or path == PACKAGE or path.is_relative_to(PACKAGE) or PACKAGE.is_relative_to(path)):
        raise ValueError('shared acceptance files must use the actual separate ComfyUI models directory')
    no_symlink_prefix(path)
    return path


def marker_value(root, approval, environment=None):
    value = {'schema_version': 1, 'scope': SCOPE, 'root': str(root), 'approval_sha256': scope_identity(approval),
             'submissions': False, 'automatic_inference': False}
    if (os.environ if environment is None else environment).get('H3_MODEL_ACCEPTANCE_MODELS_ROOT'):
        value['models_root'] = str(models_directory(root, environment))
    return value


def require_store(root, approval, *, create=False, environment=None):
    root = isolated_root(root, environment)
    expected = marker_value(root, approval, environment)
    if not root.exists():
        if not create:
            raise ValueError('acceptance store was not explicitly prepared')
        root.mkdir(parents=True, mode=0o700)
    if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError('acceptance store must be owned and private')
    try:
        info = (root / '.environment.lock').lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('acceptance environment lock is linked/unsafe')
    try:
        with parent_directory(root, MARKER) as (directory, name):
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                raise ValueError('acceptance marker is unsafe')
        actual = read_json(root, MARKER)
    except FileNotFoundError:
        if not create or any(root.iterdir()):
            raise ValueError('unmarked/unrelated acceptance directory is preserved') from None
        atomic_json(root, MARKER, expected)
    else:
        if actual != expected:
            raise ValueError('acceptance scope changed; select a new isolated store')
    return root


def check_model_path(root, item, environment=None):
    path = models_directory(root, environment) / item['destination']
    no_symlink_prefix(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('acceptance model cannot share a linked production inode')


def plan(root, items, approval, environment=None):
    root = isolated_root(root, environment)
    if root.exists():
        require_store(root, approval, environment=environment)
    missing = []
    model_root = models_directory(root, environment)
    for item in items:
        check_model_path(root, item, environment)
        if not (model_root / item['destination']).exists():
            missing.append(item)
    target = model_root
    while not target.exists():
        target = target.parent
    retained = sum(partial_bytes(model_root, item) for item in missing)
    download = sum(item['size_bytes'] for item in missing) - retained
    staging = max((item['size_bytes'] for item in missing), default=0)
    return {'scope': SCOPE, 'submissions': False, 'automatic_inference': False,
            'root': str(root), 'models_root': str(model_root), 'approved_artifacts': [item['id'] for item in items],
            'known_model_bytes': sum(item['size_bytes'] for item in items), 'missing_download_bytes': download,
            'download_staging_bytes': 0, 'largest_download_bytes': staging,
            'retained_partial_bytes': retained, 'minimum_free_bytes': download,
            'free_bytes': shutil.disk_usage(target).free, 'gpu_acceptance': 'not_run'}


def download_models(root, items, approval, *, downloader=ensure_download, environment=None):
    root = require_store(root, approval, environment=environment)
    budget = plan(root, items, approval, environment)
    if budget['free_bytes'] < budget['minimum_free_bytes']:
        raise ValueError('acceptance disk cannot hold approved models and staging')
    results = []
    for item in items:
        check_model_path(root, item, environment)
        results.append(downloader(models_directory(root, environment), item))
    return {'scope': SCOPE, 'submissions': False, 'results': results, 'gpu_acceptance': 'not_run'}


def verify_native_source(node):
    no_symlink_prefix(node)
    no_symlink_prefix(node / '.git')
    repository = 'https://github.com/KingManiya/ComfyUI-LLM-text-processor.git'
    def git(*args):
        return subprocess.check_output(['git', '-C', str(node), *args], text=True, stderr=subprocess.DEVNULL).strip()
    if git('rev-parse', 'HEAD') != LLM_NODE_COMMIT or git('config', '--get', 'remote.origin.url') != repository:
        raise ValueError('acceptance native node differs from exact pin/origin')
    subprocess.run(['git', '-C', str(node), 'diff', '--quiet', 'HEAD', '--', '.', ':(exclude)llama_binary.py'], check=True)
    original = subprocess.check_output(['git', '-C', str(node), 'show', 'HEAD:llama_binary.py'], text=True)
    info = (node / 'llama_binary.py').lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('acceptance native selector has linked/unsafe source')
    current = (node / 'llama_binary.py').read_text()
    if current not in (original, patch_source(original)):
        raise ValueError('acceptance native node contains unrelated local edits')


def prepare_native_node(root, approval):
    root = require_store(root, approval)
    node = root / 'native/custom_nodes/ComfyUI-LLM-text-processor'
    no_symlink_prefix(node)
    if not node.exists():
        node.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.acceptance-node-', dir=node.parent))
        try:
            subprocess.run(['git', 'clone', '--no-checkout', 'https://github.com/KingManiya/ComfyUI-LLM-text-processor.git', str(stage)], check=True)
            subprocess.run(['git', '-C', str(stage), 'fetch', '--depth', '1', 'origin', LLM_NODE_COMMIT], check=True)
            subprocess.run(['git', '-C', str(stage), 'checkout', '--detach', LLM_NODE_COMMIT], check=True)
            verify_native_source(stage)
            # Linux renameat2: no accidental replacement if another owner appears.
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            if rename(-100, os.fsencode(stage), -100, os.fsencode(node), 1) != 0:
                raise OSError(ctypes.get_errno(), 'acceptance node publication refused')
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    verify_native_source(node)
    return node


def verify_models(root, items, approval, *, cuda_version='13.0', environment=None):
    root = require_store(root, approval, environment=environment)
    model_root = models_directory(root, environment)
    models = []
    for item in items:
        check_model_path(root, item, environment)
        verified = verify_file(model_root, item['destination'], item)
        with parent_directory(model_root, item['destination']) as (directory, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                if signature(os.fstat(descriptor)) != verified['signature']:
                    raise ValueError('model changed after checksum verification')
                header = os.read(descriptor, 24)
                if (len(header) != 24 or header[:4] != b'GGUF' or struct.unpack('<I', header[4:8])[0] not in (2, 3)
                        or not all(struct.unpack('<QQ', header[8:24]))):
                    raise ValueError('verified acceptance artifact has invalid GGUF framing')
            finally:
                os.close(descriptor)
        models.append({'id': item['id'], 'revision': item['revision'], 'sha256': verified['sha256'],
                       'size_bytes': verified['size_bytes'], 'signature': verified['signature']})
    node = root / 'native/custom_nodes/ComfyUI-LLM-text-processor'
    verify_native_source(node)
    vendor = node / 'vendor/llama.cpp' / LLAMA_TAG / 'linux-x64-cuda'
    no_symlink_prefix(vendor)
    for filename in ('llama-cli', 'llama-server', 'llama-cli.build.json', 'llama-server.build.json'):
        info = (vendor / filename).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('acceptance native binary/receipt is linked or unsafe')
    cli = verify_llama(node, cuda_version)
    server = Path(cli).with_name('llama-server')
    if server.is_symlink() or not server.is_file() or server.with_suffix('.build.json').is_symlink():
        raise ValueError('acceptance completion runtime missing/unsafe')
    receipt = json.loads(server.with_suffix('.build.json').read_text())
    if any(receipt.get(key) != value for key, value in
           {'commit': LLAMA_COMMIT, 'tag': LLAMA_TAG, 'cuda': cuda_version, 'arch': '120', 'sha256': sha256(server)}.items()):
        raise ValueError('acceptance completion runtime differs from pinned verified build')
    version = subprocess.check_output([str(server), '--version'], stderr=subprocess.STDOUT, timeout=30)
    if LLAMA_COMMIT[:7].encode() not in version:
        raise ValueError('acceptance completion runtime revision mismatch')
    return {'scope': SCOPE, 'submissions': False, 'model_files': models, 'runtime': approval['runtime'],
            'gpu_acceptance': 'not_run',
            'unverified': ['GGUF tensors/template/context', 'real model load/vision/generation', 'reasoning/final/EOS/timeline', 'VRAM/render/lifecycle']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--models-root', type=Path, help='explicit shared COMFY_ROOT/models; evidence/runtime remain separate')
    actions = parser.add_mutually_exclusive_group()
    for action in ('plan', 'prepare', 'prepare-native-node', 'download', 'verify'):
        actions.add_argument('--' + action, action='store_true')
    args = parser.parse_args(argv)
    if args.models_root is not None:
        os.environ['H3_MODEL_ACCEPTANCE_MODELS_ROOT'] = str(args.models_root)
    os.environ.update(H3_ALLOW_SUBMISSIONS='0', H3_ALLOW_DIAGNOSTIC_SUBMISSIONS='0',
                      H3_ENABLE_TERMINAL='0', VAST_CLI='/usr/bin/false', SERVICE_CTL='/usr/bin/false')
    os.environ.pop('VAST_API_KEY', None)
    for name in ('HF_HUB_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
        os.environ.pop(name, None)
    if not args.download:
        os.environ.pop('HF_TOKEN', None)
    try:
        items, approval = load_selection()
        if args.prepare:
            budget = plan(args.root, items, approval)
            if budget['free_bytes'] < budget['minimum_free_bytes']:
                raise ValueError('acceptance disk is insufficient before environment preparation')
            result = {'scope': SCOPE, 'root': str(require_store(args.root, approval, create=True)), 'submissions': False}
        elif args.prepare_native_node:
            result = {'scope': SCOPE, 'node': str(prepare_native_node(args.root, approval)), 'submissions': False}
        elif args.download:
            result = download_models(args.root, items, approval)
        elif args.verify:
            result = verify_models(args.root, items, approval, cuda_version=os.environ.get('H3_CUDA_VERSION', '13.0'))
        else:
            result = plan(args.root, items, approval)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Provider/tool exceptions may expose credentials or private runtime data.
        print('Acceptance blocked: scope, isolation, provenance, disk or file/runtime verification failed. Production remains closed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
