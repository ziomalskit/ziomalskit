"""Hash the complete trusted native Python package without importing worker code.

Directory/file descriptors reject symlinks and special files. Ownership receipts
are never an authority for expected behavior; only the frozen repository source
manifest is. Python caches are excluded from source identity.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat


def read_native_sources(package: Path, *, deployed=False) -> dict[str, bytes]:
    bodies = {}
    def walk(directory_fd, prefix):
        for name in sorted(os.listdir(directory_fd)):
            if name == "__pycache__":
                continue
            relative = prefix + name
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("native package source is symlinked")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    walk(child, relative + "/")
                finally:
                    os.close(child)
            elif name.endswith(".py"):
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("native Python source is not a regular file")
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as handle:
                    before = os.fstat(handle.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("native source is not a regular file")
                    body = handle.read()
                    after = os.fstat(handle.fileno())
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError("native source changed during verification")
                bodies[relative] = body
            elif deployed and name != ".aj-owner.json":
                raise ValueError("deployed native package contains ambiguous extra files")
    # Walk the complete path with dirfds: no check-then-follow race in a parent.
    path = Path(package).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        walk(fd, "")
        opened, current = os.fstat(fd), os.stat(path, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("native package was replaced during verification")
    finally:
        os.close(fd)
    if "__init__.py" not in bodies:
        raise ValueError("native package entrypoint is missing")
    return dict(sorted(bodies.items()))


def native_manifest(package: Path, *, deployed=False) -> dict[str, str]:
    return {name: hashlib.sha256(body).hexdigest() for name, body in read_native_sources(package, deployed=deployed).items()}


def validate_manifest_paths(expected):
    if not isinstance(expected, dict) or not expected or "__init__.py" not in expected:
        raise ValueError("native implementation manifest missing")
    for name, digest in expected.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or path.as_posix() != name or any(part in {".", "..", "__pycache__"} for part in path.parts)
                or not name.endswith(".py") or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ValueError("native implementation manifest is invalid")


def verify_deployed_native(package: Path, expected: dict[str, str]) -> None:
    validate_manifest_paths(expected)
    if native_manifest(package, deployed=True) != expected:
        raise ValueError("deployed native implementation differs from frozen repository implementation")
