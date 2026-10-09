"""Bounded tails of service_ctl's three known regular log files."""
from __future__ import annotations

import os
from pathlib import Path
import stat

LOG_FILES = {"render": "comfyui-render.log", "prompt": "comfyui-prompt.log", "panel": "h3-mobile.log"}
MAX_LOG_LINES = 1000
MAX_LOG_BYTES = 256 * 1024


def tail_log(workspace: Path, service: str, lines: int) -> dict:
    if service not in LOG_FILES:
        raise ValueError("unknown log service")
    if type(lines) is not int or not 1 <= lines <= MAX_LOG_LINES:
        raise ValueError("invalid line count")
    result = {"service": service, "requested_lines": lines, "line_count": 0,
              "available": False, "status": "not_created", "text": "", "truncated": False}
    descriptor = None
    try:
        # Reject symlinks and non-regular files, including FIFOs. A replaced log
        # cannot turn the fixed identifier into arbitrary-file disclosure/hang.
        descriptor = os.open(workspace / LOG_FILES[service], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("log is not a regular file")
        offset = max(0, info.st_size - MAX_LOG_BYTES)
        os.lseek(descriptor, offset, os.SEEK_SET)
        body = os.read(descriptor, MAX_LOG_BYTES)
        if offset:
            # The first line can start partway through a UTF-8 code point or a
            # giant log line. Omit that partial line and mark the tail truncated.
            body = body.partition(b"\n")[2]
        entries = body.decode("utf-8", errors="replace").splitlines()
        tail = entries[-lines:]
        result.update(available=True, status="available", text="\n".join(tail), line_count=len(tail),
                      truncated=bool(offset or len(entries) > lines))
    except FileNotFoundError:
        pass
    except OSError:
        result.update(status="unavailable", warning="Log cannot be read safely")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return result
