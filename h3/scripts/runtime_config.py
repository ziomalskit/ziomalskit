"""Local deployment configuration; never evaluate or display credential values."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shlex
import tempfile
import sys


LLAMA_TAG = "b10472"
LLAMA_COMMIT = "60eeeb6082c1126bb8bc72902c83123cd056811b"
LLM_NODE_COMMIT = "65983de33681816f856db7e16767da906084c688"
COMFY_COMMIT = "6b747c0428c343e1417219641db93a4fb7cb69ae"


def read_env(path: Path) -> dict[str, str]:
    """Read our literal, shlex-quoted assignments without sourcing shell code."""
    result = {}
    if not path.exists():
        return result
    lexer = shlex.shlex(path.read_text(), posix=True)
    lexer.whitespace_split = True
    for token in lexer:
        key, separator, value = token.partition("=")
        if not separator or not key.isidentifier():
            raise ValueError("invalid/unquoted runtime assignment")
        result[key] = value
    return result


def atomic_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    handle = None
    primary = None
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w")
        descriptor = None
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            error = sys.exception()
            try:
                os.close(directory)
            except BaseException as cleanup:
                if error is None:
                    raise
                error.add_note(f'Directory close failed: {cleanup!r}')
    except BaseException as error:
        primary = error
        raise
    finally:
        for cleanup in (lambda: handle.close() if handle is not None else None,
                        lambda: os.close(descriptor) if descriptor is not None else None,
                        lambda: Path(temporary).unlink(missing_ok=True)):
            try:
                cleanup()
            except BaseException as error:
                if primary is None:
                    raise
                primary.add_note(f'Atomic file cleanup failed: {error!r}')


def write_runtime(path: Path, environment: dict[str, str]) -> None:
    previous = read_env(path)
    names = (
        "WORKSPACE", "COMFY_ROOT", "PANEL_ROOT", "PID_DIR", "PYTHON_BIN", "COMFY_PYTHON",
        "RENDER_PORT", "PROMPT_PORT", "H3_PANEL_PORT", "RENDER_COMFY_URL",
        "PROMPT_COMFY_URL", "H3_PANEL_URL", "VAST_CLI", "SERVICE_CTL",
        "H3_PERSISTENCE_MODE", "H3_PERSISTENT_ROOT", "COMFY_INPUT_DIR",
        "COMFY_OUTPUT_DIR", "COMFY_MODELS_DIR", "H3_CUDA_VERSION",
        "H3_MODEL_CHECKSUMS_FILE", "H3_PERSISTENT_VOLUME_PROOF",
        "H3_PROMPT_PREFETCH", "H3_ENABLE_TERMINAL",
    )
    values = {name: environment.get(name, "") for name in names}
    for name, default in (("H3_PROMPT_PREFETCH", "3"), ("H3_ENABLE_TERMINAL", "0")):
        values[name] = environment.get(name) or previous.get(name) or default
    values["H3_PANEL_USER"] = environment.get("H3_PANEL_USER") or previous.get("H3_PANEL_USER") or "h3"
    values["H3_PANEL_PASSWORD"] = (
        environment.get("H3_PANEL_PASSWORD") or previous.get("H3_PANEL_PASSWORD")
        or secrets.token_urlsafe(24)
    )
    atomic_private_text(path, "".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()))


def build_parallelism(requested: str | None, cpus: int, memory_bytes: int) -> int:
    memory_limit = max(1, memory_bytes // (3 * 1024**3))
    limit = min(max(1, cpus), memory_limit, 8)
    if requested:
        value = int(requested)
        if value < 1:
            raise ValueError("H3_BUILD_JOBS must be positive")
        return min(value, limit)
    return limit


def main() -> None:
    import sys
    if sys.argv[1] == "write-env":
        write_runtime(Path(sys.argv[2]), dict(os.environ))
    elif sys.argv[1] == "build-jobs":
        memory = next(
            (int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
             if line.startswith("MemAvailable:")), 3 * 1024**3,
        )
        print(build_parallelism(os.environ.get("H3_BUILD_JOBS"), os.cpu_count() or 1, memory))
    elif sys.argv[1] == "constant":
        print(globals()[sys.argv[2]])
    else:
        raise SystemExit("unknown runtime_config command")


if __name__ == "__main__":
    main()
