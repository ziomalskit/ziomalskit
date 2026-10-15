"""Linux atomic publication without replacing any existing directory entry."""
import ctypes
import errno
import os


def publish_noreplace(directory: int, temporary: str, destination: str,
                      destination_directory: int | None = None) -> None:
    """Rename a caller-owned, fsynced file on one filesystem; fail closed.

    RENAME_NOREPLACE treats even a dangling destination symlink as a collision.
    There is no hardlink fallback: a crash must leave either the temporary or
    the final name, never both names sharing the published inode.
    """
    for name in (temporary, destination):
        if not name or name in {'.', '..'} or '/' in name or '\0' in name:
            raise ValueError('publication requires single directory entries')
    target = directory if destination_directory is None else destination_directory
    if os.fstat(directory).st_dev != os.fstat(target).st_dev:
        raise OSError(errno.EXDEV, 'atomic publication requires one filesystem')
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        raise OSError(errno.ENOSYS, 'atomic no-overwrite rename is unavailable')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(directory, os.fsencode(temporary), target, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)
    os.fsync(target)
    if target != directory:
        os.fsync(directory)
