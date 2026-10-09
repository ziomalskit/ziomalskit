"""Private PTY supervisor: gated launch, parent-death cleanup and child reaping.

This is a subprocess helper, never an HTTP command endpoint. Keeping a subreaper
outside Bash lets disconnect also reap background children after the shell exits.
"""
from __future__ import annotations

import ctypes
import fcntl
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import termios
import time


def descendants() -> dict[int, str]:
    processes = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / "stat").read_text(errors="replace").rsplit(")", 1)[1].split()
            processes[int(path.name)] = (int(fields[1]), fields[19])
        except (FileNotFoundError, ProcessLookupError):
            pass
    owned = {os.getpid()}
    while True:
        children = {pid for pid, (parent, _start) in processes.items() if parent in owned}
        if children <= owned:
            break
        owned.update(children)
    return {pid: processes[pid][1] for pid in owned if pid != os.getpid()}


def signal_children(sig: int) -> None:
    for pid, start in descendants().items():
        descriptor = None
        try:
            descriptor = os.pidfd_open(pid)
            # A /proc enumeration is not a signalling capability. Recheck the
            # birth identity after capture, then signal through the pidfd only.
            current = Path(f"/proc/{pid}/stat").read_text(errors="replace").rsplit(")", 1)[1].split()
            if current[19] == start and descendants().get(pid) == start:
                signal.pidfd_send_signal(descriptor, sig)
        except (FileNotFoundError, ProcessLookupError):
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)


def reap(shell) -> bool:
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if not pid:
            return False
        if shell is not None and pid == shell.pid:
            shell.returncode = os.waitstatus_to_exitcode(status)


def cleanup(shell) -> None:
    for sig, seconds in ((signal.SIGTERM, 0.4), (signal.SIGKILL, 3.0)):
        deadline = time.monotonic() + seconds
        while True:
            signal_children(sig)
            if reap(shell):
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    raise RuntimeError("Terminal descendants did not exit")


def run_shell() -> int:
    gate, parent = map(int, sys.argv[2:])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Shell parent supervision unavailable")
    if os.read(gate, 1) != b"G" or os.getppid() != parent:
        return 0
    os.close(gate)
    # This child is already its session/group leader. Interactive Bash cannot
    # move the shell away from the controller's original pidfd group capability.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execv("/bin/bash", ["bash", "--noprofile", "--norc", "-i"])
    return 0


def main() -> int:
    control, reply, parent = map(int, sys.argv[1:])
    closing = False
    shell = None
    shell_gate = None

    def stop(_sig, _frame):
        nonlocal closing
        closing = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    # PTY-generated Ctrl+C belongs to the foreground shell/command. A caught
    # handler becomes SIG_DFL across exec, so Bash's children can receive it.
    signal.signal(signal.SIGINT, lambda *_args: None)
    libc = ctypes.CDLL(None, use_errno=True)
    for option, value in ((1, signal.SIGTERM), (36, 1)):  # PDEATHSIG, CHILD_SUBREAPER
        if libc.prctl(option, value, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Terminal supervision unavailable")
    try:
        if os.read(control, 1) != b"G" or os.getppid() != parent or closing:
            return 0
        # No startup files or persistent history; no job control by default.
        # The supervisor still owns descendants if an admin enables job control
        # or a program creates its own process group/session.
        # Bash enables job control after parsing invocation options. The fixed
        # PROMPT_COMMAND disables it and history before the first input, then
        # unsets itself. --norc avoids both user and system startup files.
        shell_read, shell_gate = os.pipe2(os.O_CLOEXEC)
        try:
            shell = subprocess.Popen([sys.executable, "-I", __file__, "--shell", str(shell_read), str(os.getpid())],
                                     start_new_session=True, close_fds=True, pass_fds=(shell_read,))
        finally:
            os.close(shell_read)
        os.write(reply, f"{shell.pid}\n".encode())
        os.close(reply)
        reply = None
        # Keep the shell unreaped/gated until the controller captures its own
        # pidfd. Even supervisor failure cannot lose the shell-group capability.
        if os.read(control, 1) != b"A" or closing:
            return 0
        os.write(shell_gate, b"G")
        os.close(shell_gate)
        shell_gate = None
        os.set_blocking(control, False)
        poll = select.poll()
        poll.register(control, select.POLLIN | select.POLLHUP)
        while not closing:
            reap(shell)
            if shell.returncode is not None:
                break
            try:
                events = poll.poll(100)
            except InterruptedError:
                continue
            if events:
                try:
                    if not os.read(control, 1024):
                        break
                except BlockingIOError:
                    pass
        return 0
    finally:
        for descriptor in (shell_gate, reply):
            if descriptor is not None:
                os.close(descriptor)
        cleanup(shell)
        os.close(control)


if __name__ == "__main__":
    try:
        raise SystemExit(run_shell() if sys.argv[1] == "--shell" else main())
    except Exception:
        # No traceback, environment, input or credentials in PTY/controller logs.
        raise SystemExit(1)
