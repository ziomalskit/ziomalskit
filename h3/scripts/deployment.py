"""Stage immutable deployment files and atomically exchange the release directory.

Retired directories are retained: they can contain live mutable state and a
running process's cwd. Never garbage-collect them during provisioning.
"""
from __future__ import annotations

import ast
import ctypes
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        primary = sys.exception()
        try:
            os.close(fd)
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f'Directory close failed: {error!r}')


def exchange(left: Path, right: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def validate(stage: Path) -> None:
    for required in ('app/main.py', 'scripts/service_ctl.sh', 'requirements.txt'):
        if not (stage / required).is_file():
            raise ValueError('Incomplete deployment: ' + required)
    for path in stage.rglob('*.py'):
        ast.parse(path.read_bytes(), filename=str(path))
    for path in (stage / 'config').glob('*.json'):
        json.loads(path.read_text())


def deploy(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.absolute()
    if source == target.resolve():
        return
    if source in target.resolve().parents or target.resolve() in source.parents:
        raise ValueError('Package and deployment paths must not contain each other')
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / ('.' + target.name + '.deploy.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink() or (target.exists() and any(target.iterdir())
                                  and not (target / 'app/main.py').is_file()):
            raise ValueError('Refusing unrelated deployment directory')
        stage = Path(tempfile.mkdtemp(prefix='.' + target.name + '.release-', dir=target.parent))
        committed = False
        primary = None
        try:
            shutil.copytree(source, stage, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('state', 'runtime.env', '__pycache__', '.venv', '.git'))
            validate(stage)
            # Mutable directories are not copied. After exchange, these links
            # refer to exactly the original inodes, including concurrent writes.
            if target.exists():
                (target / 'state').mkdir(exist_ok=True)
                for name in ('state', 'runtime.env'):
                    old = target / name
                    if old.exists() or old.is_symlink():
                        location = old.resolve() if old.is_symlink() else stage / name
                        (stage / name).symlink_to(location, target_is_directory=name == 'state')
            else:
                (stage / 'state').mkdir()
            for directory, _dirs, files in os.walk(stage, followlinks=False):
                for name in files:
                    path = Path(directory) / name
                    if not path.is_symlink():
                        with path.open('rb') as stream:
                            os.fsync(stream.fileno())
                sync_directory(Path(directory))
            if target.exists():
                exchange(stage, target)
            else:
                os.rename(stage, target)
            committed = True
            sync_directory(target.parent)
        except BaseException as error:
            primary = error
            raise
        finally:
            if not committed:
                # A failed copy/validation never modified immutable active files.
                try:
                    shutil.rmtree(stage)
                except BaseException as error:
                    if primary is None:
                        raise
                    primary.add_note(f'Staging cleanup failed: {error!r}')


if __name__ == '__main__':
    deploy(*map(Path, sys.argv[1:3]))
