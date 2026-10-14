#!/usr/bin/env python3
"""Check a manually captured native completion; never loads models or retires gates."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from app.temporal import duration_identity, canonical_plan, final_timeline


def native_contract():
    # Load the exact production parser without importing ComfyUI's node package.
    directory = PACKAGE/'custom_nodes/aj_production'
    name = '_aj_acceptance_native_contract'
    package = types.ModuleType(name);package.__path__ = [str(directory)]
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name+'.compiler', directory/'compiler.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def bounded_text(path, limit=16*1024*1024):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_size > limit:
            raise ValueError('completion evidence must be a bounded regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            body = stream.read(limit+1)
        if len(body) > limit:
            raise ValueError('completion evidence grew beyond the limit')
        return body.decode()
    finally:
        os.close(descriptor)


def check(body, plan, requested_duration):
    identity = duration_identity(requested_duration)
    canonical_plan(plan, identity['effective_duration_seconds'])
    final, reasoning, _ = native_contract().completion_response(body)
    final_timeline(final, plan, identity['effective_duration_seconds'])
    return {'native_eos': True, 'truncated': False, 'final_sections': 6, 'timeline': 'verified',
            'reasoning_present': bool(reasoning.strip()), **identity,
            'submissions': False, 'production_gate_retired': False, 'gpu_acceptance': 'not_attested'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--completion', type=Path, required=True)
    parser.add_argument('--timeline', type=Path, required=True)
    parser.add_argument('--duration', type=float, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(check(json.loads(bounded_text(args.completion)), bounded_text(args.timeline), args.duration), sort_keys=True))
        return 0
    except Exception:
        print('Completion evidence rejected: native EOS/final/timeline contract failed. No GPU acceptance attested.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
