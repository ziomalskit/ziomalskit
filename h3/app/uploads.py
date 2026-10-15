"""Private staged uploads, published only after a complete durable copy."""
import os
from pathlib import Path
import uuid

from .artifacts import parent_directory
from .file_publication import publish_noreplace


def store_upload(root: Path, filename: str, source) -> str:
    safe = Path(filename or 'upload.bin').name
    if safe in {'', '.', '..'} or '\0' in safe:
        raise ValueError('invalid upload filename')
    absolute = root.absolute()
    relative = absolute.as_posix().lstrip('/') + '/.anchor'
    # Create and open every component with dirfds, without following symlinks.
    with parent_directory(Path(absolute.anchor), relative, create=True) as (directory, _):
        temporary = '.aj-upload-' + uuid.uuid4().hex + '.partial'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                while block := source.read(1024 * 1024):
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            destination = safe
            for _ in range(32):
                try:
                    publish_noreplace(directory, temporary, destination)
                    return destination
                except FileExistsError:
                    # Preserve files and both live/dangling symlinks unchanged.
                    path = Path(safe)
                    destination = f'{path.stem}_{uuid.uuid4().hex}{path.suffix}'
            raise FileExistsError('upload name collisions exhausted')
        finally:
            # Only this invocation's private temporary is eligible for cleanup.
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
