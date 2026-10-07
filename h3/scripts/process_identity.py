"""Own Linux services by pidfd identity, with Popen cleanup for spawned children."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import tempfile
import time


def require_pidfd() -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Linux pidfd support is required for safe service control")
    descriptor = os.pidfd_open(os.getpid())
    os.close(descriptor)


def process_snapshot(pid: int) -> dict:
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid service PID")
    root = Path(f"/proc/{pid}")
    stat = (root / "stat").read_text()
    # comm may contain spaces and parentheses; fields after its last ')' start
    # with field 3 (state), making field 22 (starttime) index 19 here.
    fields = stat[stat.rindex(")") + 2:].split()
    if fields[0] in {"Z", "X"}:
        raise ProcessLookupError("service process is no longer running")
    command = (root / "cmdline").read_bytes()
    if not command:
        raise ProcessLookupError("service command is unavailable")
    return {"pid": pid, "start_time": int(fields[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "command_sha256": hashlib.sha256(command).hexdigest(),
            "argv": [os.fsdecode(arg) for arg in command.rstrip(b"\0").split(b"\0")],
            "cwd": (root / "cwd").resolve(strict=True)}


def expected_service(snapshot: dict, args) -> bool:
    argv = snapshot["argv"]
    selected_python = Path(args.python).resolve()
    interpreter_matches = any(Path(arg).resolve() == selected_python for arg in argv[:2])
    if not interpreter_matches:
        return False
    if args.service == "panel":
        return snapshot["cwd"] == Path(args.panel_root).resolve() and any(
            argv[index:index + 3] == ["-m", "uvicorn", "app.main:app"]
            for index in range(len(argv))
        )
    return snapshot["cwd"] == Path(args.comfy_root).resolve() and "main.py" in argv and any(
        argv[index:index + 2] == ["--port", str(args.port)] for index in range(len(argv))
    )


def terminate_owned_process(descriptor: int) -> None:
    """Wait for this process instance to exit, escalating through the same fd."""
    poll = select.poll()
    poll.register(descriptor, select.POLLIN)
    try:
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
    except ProcessLookupError:
        return
    if poll.poll(2000):
        return
    try:
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    except ProcessLookupError:
        return
    if not poll.poll(2000):
        raise RuntimeError("Owned child did not exit after pidfd SIGKILL")


def record_process(args, *, artifacts: list[Path] | None = None) -> None:
    descriptor = os.pidfd_open(args.pid)
    temporary = None
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                signal.pidfd_send_signal(descriptor, 0)
                snapshot = process_snapshot(args.pid)
                signal.pidfd_send_signal(descriptor, 0)
                if expected_service(snapshot, args):
                    break
            except (FileNotFoundError, ProcessLookupError):
                # /proc/cmdline may be briefly empty during exec. Check the
                # original instance through its fd before waiting for exec.
                signal.pidfd_send_signal(descriptor, 0)
            if time.monotonic() >= deadline:
                raise RuntimeError("Spawned process does not match the expected service")
            time.sleep(0.02)
        record = {key: snapshot[key] for key in ("pid", "start_time", "boot_id", "command_sha256")}
        record["service"] = args.service
        fd, temporary = tempfile.mkstemp(prefix=".service-pid-", dir=args.pid_file.parent)
        if artifacts is not None:
            artifacts.append(Path(temporary))
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.pid_file)
        directory = os.open(args.pid_file.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # After capture, every registration failure owns this pidfd cleanup
        # obligation, including /proc reads and identity validation.
        try:
            terminate_owned_process(descriptor)
        finally:
            args.pid_file.unlink(missing_ok=True)
        raise
    finally:
        try:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        finally:
            os.close(descriptor)


def checked_pidfd(args) -> tuple[int, int]:
    record = json.loads(args.pid_file.read_text())
    if not isinstance(record, dict) or record.get("service") != args.service:
        raise ValueError("stale service PID record")
    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid service PID record")
    descriptor = os.pidfd_open(pid)
    try:
        snapshot = process_snapshot(pid)
        if not expected_service(snapshot, args) or any(
            record.get(key) != snapshot[key] for key in ("start_time", "boot_id", "command_sha256")
        ):
            raise ValueError("service process identity no longer matches")
    except Exception:
        os.close(descriptor)
        raise
    return pid, descriptor


def _terminate_launched_child(child: subprocess.Popen) -> None:
    """Reap this parent's child if registration could not clean it via pidfd."""
    if child.poll() is not None:
        child.wait(timeout=5)
        return
    try:
        child.terminate()
        child.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        child.kill()
        child.wait(timeout=5)


def launch_process(args, command: list[str]) -> None:
    """Keep the spawned child unreaped until registration opens its pidfd.

    Looking up a shell's old $! in a later helper could open a reused PID.
    This parent owns the Popen child and never polls/waits before pidfd_open,
    so even a child that exits immediately still pins its PID as a zombie.
    """
    if not command:
        raise ValueError("missing service command")
    cwd = args.panel_root if args.service == "panel" else args.comfy_root
    with args.log_file.open("wb") as log:
        child = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    args.pid = child.pid
    artifacts = [args.pid_file]
    try:
        record_process(args, artifacts=artifacts)
    except BaseException as error:
        # record_process normally terminates through its original pidfd. The
        # parent still owns cleanup if capture itself failed or that cleanup
        # left the child alive. Never resolve a PID from the registration file.
        try:
            _terminate_launched_child(child)
        except BaseException as cleanup_error:
            error.add_note(f"Spawned child cleanup failed: {cleanup_error!r}")
        finally:
            # Only this attempt's exact paths; other services may share the
            # directory, so a glob over registration tempfiles is unsafe.
            for artifact in artifacts:
                try:
                    artifact.unlink(missing_ok=True)
                except BaseException as cleanup_error:
                    error.add_note(f"Registration artifact cleanup failed: {cleanup_error!r}")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("support", "launch", "record", "check", "signal"))
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--service", choices=("render", "prompt", "panel"))
    parser.add_argument("--comfy-root")
    parser.add_argument("--panel-root")
    parser.add_argument("--python")
    parser.add_argument("--port", type=int)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--signal", choices=("TERM", "KILL"), default="TERM")
    args, command = parser.parse_known_args()
    if command and command[0] == "--":
        command = command[1:]
    if command and args.command != "launch":
        parser.error("unexpected service command")
    try:
        require_pidfd()
        if args.command == "support":
            return 0
        if args.command == "launch":
            launch_process(args, command)
            return 0
        if args.command == "record":
            record_process(args)
            return 0
        pid, descriptor = checked_pidfd(args)
        try:
            if args.command == "signal":
                signal.pidfd_send_signal(descriptor, getattr(signal, "SIG" + args.signal))
            else:
                print(pid)
        finally:
            os.close(descriptor)
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        # No argv/environment dump: panel credentials live only in its env.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
