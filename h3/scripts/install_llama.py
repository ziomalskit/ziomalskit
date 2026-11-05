"""Publish a validated executable; incomplete copies never touch the old binary."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    from .runtime_config import atomic_private_text
    from .deployment import sync_directory
except ImportError:
    from runtime_config import atomic_private_text
    from deployment import sync_directory


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def install(binary: Path, signature: Path, vendor: Path, tag: str, commit: str, cuda: str, *, build_only=False) -> None:
    with (vendor.parent / '.h3-llama.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _install(binary, signature, vendor, tag, commit, cuda, build_only=build_only)


def _install(binary: Path, signature: Path, vendor: Path, tag: str, commit: str, cuda: str, *, build_only=False) -> None:
    fd, name = tempfile.mkstemp(prefix='.llama-candidate-', dir=vendor.parent)
    os.close(fd)
    candidate = Path(name)
    primary = None
    try:
        subprocess.run(['cp', '--', str(binary), str(candidate)], check=True)
        candidate.chmod(0o755)
        checksum = digest(candidate)
        if checksum != digest(binary):
            raise ValueError('Incomplete llama executable copy')
        if not build_only:
            version = subprocess.check_output([str(candidate), '--version'], text=True,
                                             stderr=subprocess.STDOUT, timeout=30)
            if commit[:7] not in version:
                raise ValueError('llama.cpp runtime commit failed verification')
        meta = {'tag': tag, 'commit': commit, 'cuda': cuda, 'arch': '120', 'sha256': checksum}
        if build_only:
            meta['runtime_execution'] = 'PENDING'
        meta = json.dumps(meta)
        with candidate.open('rb') as stream:
            os.fsync(stream.fileno())
        # Metadata for both old and new content survives a crash between binary
        # and conventional sidecar publication. Runtime resolves it by checksum.
        atomic_private_text(vendor.parent / ('.llama-' + checksum + '.build.json'), meta)
        os.replace(candidate, vendor)
        sync_directory(vendor.parent)
        atomic_private_text(vendor.with_suffix('.build.json'), meta)
        atomic_private_text(signature, meta)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            candidate.unlink(missing_ok=True)
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f'Candidate cleanup failed: {error!r}')


if __name__ == '__main__':
    if len(sys.argv) not in (7, 8) or (len(sys.argv) == 8 and sys.argv[7] != '--build-only'):
        raise SystemExit('Expected BINARY SIGNATURE VENDOR TAG COMMIT CUDA [--build-only]')
    install(*map(Path, sys.argv[1:4]), *sys.argv[4:7], build_only=len(sys.argv) == 8)
