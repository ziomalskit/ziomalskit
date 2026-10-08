"""Own Linux services by pidfd identity, with Popen cleanup for spawned children."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import select
import secrets
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

PIDFD_SIGNAL_PROCESS_GROUP = 4


def close_preserving(descriptor: int) -> None:
    primary = sys.exception()
    try:
        os.close(descriptor)
    except BaseException as error:
        if primary is None:
            raise
        primary.add_note(f'Descriptor cleanup failed: {error!r}')


def require_group_control() -> None:
    require_pidfd()
    fd = os.pidfd_open(os.getpid())
    try:
        try:
            signal.pidfd_send_signal(fd, 0, None, PIDFD_SIGNAL_PROCESS_GROUP)
        except OSError as error:
            import errno
            if error.errno != errno.ESRCH:
                raise RuntimeError('Safe group control requires Linux >=6.9') from error
    finally:
        close_preserving(fd)


@contextmanager
def record_control(path: Path):
    """A surviving helper cannot race a later controller's record mutations.

    This descriptor belongs to the helper, is close-on-exec, and is never
    inherited by a worker. Contention is UNKNOWN, never service absence.
    """
    fd = os.open(path.with_name(path.name + '.control.lock'),
                 os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise IndeterminateIdentity('Another helper owns this service control attempt') from None
        yield
    finally:
        close_preserving(fd)


def require_live_launch_controller(expected: int | None) -> None:
    parent = expected if expected is not None else os.getppid()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != parent:
        raise IndeterminateIdentity('Launch controller exited before authorization')


class StaleIdentity(ValueError):
    """The recorded process is definitively absent or is a different instance."""


class IndeterminateIdentity(RuntimeError):
    """Ownership cannot currently be established; preserve the record."""


def command_digest(argv: list[str]) -> str:
    return hashlib.sha256(b"\0".join(os.fsencode(arg) for arg in argv) + b"\0").hexdigest()


def expected_launch_argv(command: list[str]) -> list[str]:
    """Pin the exact Linux argv, including an approved Python script wrapper."""
    with Path(command[0]).open("rb") as executable:
        header = executable.read(256).split(b"\n", 1)[0]
    if not header.startswith(b"#!"):
        return list(command)
    fields = header[2:].strip().split(None, 1)
    if not fields or Path(os.fsdecode(fields[0])).resolve() != Path(sys.executable).resolve():
        raise ValueError("Selected Python wrapper must name the launching Python interpreter")
    prefix = [os.fsdecode(field) for field in fields]
    return [*prefix, *command]


def launch_gate(read_fd: int, ready_fd: int, command: list[str]) -> None:
    """EOF cannot authorize exec; only the durable owner's COMMIT byte can."""
    os.write(ready_fd, b"R")
    os.close(ready_fd)
    authorization = os.read(read_fd, 1)
    os.close(read_fd)
    if authorization != b"C":
        raise SystemExit(1)
    os.execvp(command[0], command)


def require_free_service_port(port: int) -> None:
    """An unrelated listener cannot be accepted as a new worker's readiness."""
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError("invalid service port")
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        lines = table.read_text().splitlines()
        if not lines or "local_address" not in lines[0].split():
            raise IndeterminateIdentity("TCP listener table is unavailable")
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4:
                raise IndeterminateIdentity("TCP listener table is unavailable")
            _, separator, local_port = fields[1].partition(":")
            if not separator:
                raise IndeterminateIdentity("TCP listener table is malformed")
            if int(fields[3], 16) == 10 and int(local_port, 16) == port:
                raise IndeterminateIdentity("service port already has an unowned listener")


def require_pidfd() -> None:
    if sys.version_info < (3, 11):
        raise RuntimeError('Python >=3.11 required')
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
        raise IndeterminateIdentity("service command is temporarily unavailable")
    cwd_stat = (root / "cwd").stat()
    return {"pid": pid, "start_time": int(fields[19]),
            "pgid": int(fields[2]), "sid": int(fields[3]),
            "cwd_identity": [cwd_stat.st_dev, cwd_stat.st_ino],
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


def committed_command_shape(command: list[str], service: str, port: int) -> bool:
    # The interpreter and cwd were verified before durable COMMIT. Resolving
    # their paths again would lose ownership after a symlink/release update.
    port_matches = any(command[index:index + 2] == ['--port', str(port)]
                       for index in range(len(command)))
    if service == 'panel':
        return port_matches and any(command[index:index + 3] == ['-m', 'uvicorn', 'app.main:app']
                                    for index in range(len(command)))
    return port_matches and 'main.py' in command


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
    primary_error = None
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                signal.pidfd_send_signal(descriptor, 0)
                snapshot = process_snapshot(args.pid)
                signal.pidfd_send_signal(descriptor, 0)
                gate_command = getattr(args, "_gate_command", None)
                if gate_command is None:
                    matches = expected_service(snapshot, args)
                else:
                    intended = dict(snapshot, argv=args._launch_command)
                    matches = (snapshot["command_sha256"] == command_digest(gate_command)
                               and snapshot["cwd"] == Path(args.panel_root if args.service == "panel" else args.comfy_root).resolve()
                               and expected_service(intended, args))
                if matches:
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
        record["identity"] = {key: snapshot[key] for key in ("pgid", "sid", "cwd_identity")}
        record["configuration"] = {"python": str(Path(args.python).resolve()),
                                   "cwd": str(snapshot["cwd"]), "port": args.port}
        if getattr(args, "_gate_command", None) is not None:
            record["launch_command"] = args._launch_command
            record["target_argv"] = args._target_argv
            record["gate_command_sha256"] = snapshot["command_sha256"]
            record["command_sha256"] = command_digest(args._target_argv)
        fd, temporary = tempfile.mkstemp(prefix=".service-pid-", dir=args.pid_file.parent)
        if artifacts is not None:
            artifacts.append(Path(temporary))
        handle = None
        try:
            handle = os.fdopen(fd, "w")
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException as primary:
            try:
                if handle is None:
                    os.close(fd)
                else:
                    handle.close()
            except BaseException as cleanup_error:
                primary.add_note(f"Registration stream close failed: {cleanup_error!r}")
            raise
        else:
            handle.close()
        os.replace(temporary, args.pid_file)
        directory = os.open(args.pid_file.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        except BaseException as primary:
            try:
                os.close(directory)
            except BaseException as cleanup_error:
                primary.add_note(f"Registration directory close failed: {cleanup_error!r}")
            raise
        else:
            os.close(directory)
    except BaseException as primary:
        primary_error = primary
        # After capture, every registration failure owns this pidfd cleanup
        # obligation, including /proc reads and identity validation.
        try:
            terminate_owned_process(descriptor)
        except BaseException as cleanup_error:
            primary.add_note(f"Owned pidfd termination failed: {cleanup_error!r}")
        try:
            args.pid_file.unlink(missing_ok=True)
        except BaseException as cleanup_error:
            primary.add_note(f"Registration pidfile cleanup failed: {cleanup_error!r}")
        raise
    finally:
        try:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        except BaseException as cleanup_error:
            if primary_error is None:
                # A cleanup failure after a successful registration must
                # remain visible and trigger the launcher's Popen fallback.
                primary_error = cleanup_error
                raise
            primary_error.add_note(f"Registration temporary cleanup failed: {cleanup_error!r}")
        finally:
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(f"Registration pidfd close failed: {cleanup_error!r}")


def checked_pidfd(args, *, allow_gate: bool = False) -> tuple[int, int]:
    try:
        content = args.pid_file.read_text()
    except FileNotFoundError:
        raise StaleIdentity("service PID record is absent") from None
    try:
        record = json.loads(content)
    except (ValueError, TypeError):
        raise StaleIdentity("invalid service PID record") from None
    if not isinstance(record, dict) or record.get("service") != args.service:
        raise StaleIdentity("stale service PID record")
    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        raise StaleIdentity("invalid service PID record")
    owns_group = (record.get('identity', {}).get('pgid') == pid
                  or ('launch_command' in record and 'gate_command_sha256' in record))
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        if (owns_group
                and record.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                and group_members(pid)):
            raise IndeterminateIdentity('Leader exited with surviving group; ownership record retained') from None
        raise StaleIdentity("recorded process is absent") from None
    try:
        try:
            snapshot = process_snapshot(pid)
        except ProcessLookupError:
            if owns_group and group_members(pid):
                raise IndeterminateIdentity('Leader exited with surviving group; ownership record retained') from None
            raise StaleIdentity("recorded process has exited") from None
        except FileNotFoundError:
            # Missing /proc data may be transient. Only pidfd-confirmed death
            # makes it safe to discard the ownership record.
            poll = select.poll()
            poll.register(descriptor, select.POLLIN)
            if poll.poll(0):
                raise StaleIdentity("recorded process has exited") from None
            raise IndeterminateIdentity("process identity is temporarily unavailable") from None
        if any(record.get(key) != snapshot[key] for key in ("start_time", "boot_id")):
            raise StaleIdentity("service process instance no longer matches")
        identity = record.get("identity")
        if identity is not None:
            if any(identity.get(key) != snapshot[key] for key in ("pgid", "sid", "cwd_identity")):
                raise StaleIdentity("committed service identity no longer matches")
            config = record.get("configuration", {})
            committed = SimpleNamespace(service=args.service, python=config.get("python", ""),
                                        port=config.get("port"), comfy_root=str(snapshot["cwd"]),
                                        panel_root=str(snapshot["cwd"]))
        elif isinstance(record.get('launch_command'), list) and record['launch_command']:
            # STEP 1 already pinned the exact target command and process
            # instance. Infer its old configuration, not the new environment.
            command = record['launch_command']
            try:
                port = int(command[command.index('--port') + 1])
            except (ValueError, IndexError):
                raise StaleIdentity('Invalid legacy service command') from None
            committed = SimpleNamespace(service=args.service, python=command[0], port=port,
                                        comfy_root=str(snapshot['cwd']), panel_root=str(snapshot['cwd']))
        else:
            committed = args
        if "launch_command" in record:
            command = record["launch_command"]
            target_argv = record.get("target_argv")
            if (not isinstance(command, list) or not command
                    or any(not isinstance(arg, str) for arg in command)
                    or not isinstance(target_argv, list) or not target_argv
                    or any(not isinstance(arg, str) for arg in target_argv)
                    or record.get("command_sha256") != command_digest(target_argv)
                    or not isinstance(record.get("gate_command_sha256"), str)
                    or len(record["gate_command_sha256"]) != 64):
                raise StaleIdentity("invalid approved service command")
            if snapshot["command_sha256"] == record.get("gate_command_sha256"):
                if not committed_command_shape(command, args.service, committed.port):
                    raise StaleIdentity("launch gate no longer matches expected service")
                if not allow_gate:
                    raise IndeterminateIdentity("service launch has not reached exec")
            elif (not committed_command_shape(command, args.service, committed.port)
                    or snapshot["argv"] != target_argv
                    or snapshot["command_sha256"] != record["command_sha256"]):
                raise StaleIdentity("service command no longer matches approved launch")
        elif not expected_service(snapshot, committed) or record.get("command_sha256") != snapshot["command_sha256"]:
            raise StaleIdentity("service process identity no longer matches")
    except BaseException as primary:
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            primary.add_note(f"Identity pidfd close failed: {cleanup_error!r}")
            if isinstance(primary, StaleIdentity):
                primary.control_error = True
        raise
    return pid, descriptor


def group_members(pid: int) -> list[Path]:
    """Inspect membership only; never use the resulting numeric PIDs to signal."""
    members = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            text = (entry / 'stat').read_text()
            fields = text[text.rindex(')') + 2:].split()
            if int(fields[2]) == pid and int(fields[3]) == pid and fields[0] not in {'Z', 'X'}:
                members.append(entry)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return members


@contextmanager
def stop_cancellation_window(*, keep_owner: bool):
    """TERM requests escalation; it cannot release the sole service pidfd."""
    cancelled = False
    previous = signal.getsignal(signal.SIGTERM)
    libc = None
    previous_parent_signal = ctypes.c_int()
    parent_signal_set = False
    def escalate(_signum, _frame):
        nonlocal cancelled
        cancelled = True
    signal.signal(signal.SIGTERM, escalate)
    try:
        if keep_owner:
            # CLI stop owns the record lock and original pidfd. Isolate it from
            # the outer shell's eventual group KILL, while parent death still
            # cooperatively requests immediate service escalation.
            parent = os.getppid()
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(2, ctypes.byref(previous_parent_signal), 0, 0, 0) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            parent_signal_set = True
            if os.getsid(0) != os.getpid():
                os.setsid()
            if os.getppid() != parent:
                cancelled = True
        yield lambda: cancelled
    finally:
        try:
            if parent_signal_set:
                if libc.prctl(1, previous_parent_signal.value, 0, 0, 0) != 0:
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error))
        finally:
            signal.signal(signal.SIGTERM, previous)


def stop_group(pid: int, descriptor: int, *, keep_owner: bool = False) -> None:
    """Keep the original pidfd through leader death and descendant cleanup."""
    with stop_cancellation_window(keep_owner=keep_owner) as cancelled:
        _stop_group(pid, descriptor, cancelled, keep_owner=keep_owner)


def _stop_group(pid: int, descriptor: int, cancelled, *, keep_owner: bool) -> None:
    require_group_control()
    snap = process_snapshot(pid)
    if snap['pgid'] != pid or snap['sid'] != pid:
        raise IndeterminateIdentity('Service does not own a dedicated group/session')
    # The panel needs time to finish cancellation-safe cleanup of its own CLI
    # sessions before escalation kills the controller itself.
    grace = float(os.environ.get('H3_SERVICE_TERM_TIMEOUT', '20'))
    if not 0 < grace <= 60:
        raise ValueError('Invalid service TERM timeout')
    errors = []
    for sig, seconds in ((signal.SIGTERM, grace), (signal.SIGKILL, 5), (signal.SIGKILL, 5)):
        try:
            signal.pidfd_send_signal(descriptor, sig, None, PIDFD_SIGNAL_PROCESS_GROUP)
        except ProcessLookupError:
            try:
                if not group_members(pid):
                    break
            except BaseException as error:
                errors.append(error)
                continue  # Unknown membership cannot release the capability.
            errors.append(IndeterminateIdentity('Owned group cannot be addressed'))
        except BaseException as error:
            errors.append(error)
            continue
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if sig == signal.SIGTERM and cancelled():
                break  # Escalate through this same descriptor, never a PGID.
            try:
                members = group_members(pid)
            except BaseException as error:
                errors.append(error)
                break  # Retain the fd and still perform safe KILL cleanup.
            if not members:
                if sig == signal.SIGTERM:
                    try:
                        # A /proc enumeration can miss a child forked while
                        # its leader exits. Only kernel-confirmed group absence
                        # permits skipping KILL; a zombie-only group is harmless
                        # to KILL through the same originally owned descriptor.
                        signal.pidfd_send_signal(descriptor, 0, None, PIDFD_SIGNAL_PROCESS_GROUP)
                    except ProcessLookupError:
                        pass
                    except BaseException as error:
                        errors.append(error)
                        break
                    else:
                        break
                if errors:
                    for error in errors[1:]:
                        errors[0].add_note(f'Group cleanup failed: {error!r}')
                    raise errors[0]
                return
            time.sleep(.02)
    if keep_owner:
        # A genuinely indeterminate group must not lose its only capability.
        # Retain this isolated owner and record lock until cleanup is confirmed.
        # Release inherited pipes so a cancelled controller can finish cleanup.
        while True:
            try:
                if not group_members(pid):
                    break
            except Exception:
                pass
            for fd in (1, 2):
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, PIDFD_SIGNAL_PROCESS_GROUP)
            except OSError:
                pass
            time.sleep(.1)
    if errors:
        for error in errors[1:]:
            errors[0].add_note(f'Group cleanup failed: {error!r}')
        raise errors[0]
    if not group_members(pid):
        return
    raise IndeterminateIdentity('Owned group has not exited; record retained')


def require_owned_listener(args, pid: int, descriptor: int) -> None:
    snapshot = process_snapshot(pid)
    if snapshot['pgid'] != pid or snapshot['sid'] != pid:
        raise IndeterminateIdentity('Service does not own a dedicated group/session')
    listeners = set()
    for table in (Path('/proc/net/tcp'), Path('/proc/net/tcp6')):
        for line in table.read_text().splitlines()[1:]:
            fields = line.split()
            if int(fields[3], 16) == 10 and int(fields[1].split(':')[1], 16) == args.port:
                listeners.add('socket:[' + fields[9] + ']')
    owned = set()
    for member in group_members(pid):
        try:
            for fd in (member / 'fd').iterdir():
                try:
                    owned.add(os.readlink(fd))
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
    signal.pidfd_send_signal(descriptor, 0)
    if not listeners or not listeners <= owned:
        raise IndeterminateIdentity('Listening socket is not owned by this service')


def require_desired_configuration(args, pid: int) -> None:
    record = json.loads(args.pid_file.read_text())
    config = record.get('configuration')
    if config is None:
        if expected_service(process_snapshot(pid), args):
            return
    else:
        desired = Path(args.panel_root if args.service == 'panel' else args.comfy_root).stat()
        if (config['python'] == str(Path(args.python).resolve()) and config['port'] == args.port
                and record['identity']['cwd_identity'] == [desired.st_dev, desired.st_ino]):
            return
    raise IndeterminateIdentity('Owned service uses different configuration; restart required')


def migrate_records(target: Path, legacy: Path) -> None:
    """Import STEP 1 ownership before any new service-control operation.

    Every caller passes through this lock before opening the control lock. The
    old records remain available if import is interrupted; a durable marker
    prevents reimporting old records after a service has been stopped.
    """
    if target.resolve() == legacy.resolve():
        return
    marker = target / '.legacy-imported'
    fd = os.open(target / '.migration.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    legacy_fd = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if marker.exists():
            return
        if legacy.exists():
            legacy_fd = os.open(legacy / 'service_ctl.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(legacy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise IndeterminateIdentity('Legacy service control still holds its lock') from None
                    time.sleep(.02)
            for service in ('render', 'prompt', 'panel'):
                old, new = legacy / (service + '.pid'), target / (service + '.pid')
                if old.exists() and not new.exists():
                    temporary = None
                    stream = None
                    out = None
                    primary = None
                    try:
                        payload = old.read_bytes()
                        out, temporary = tempfile.mkstemp(prefix='.import-', dir=target)
                        stream = os.fdopen(out, 'wb')
                        out = None
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                        stream.close()
                        stream = None
                        os.replace(temporary, new)
                    except BaseException as error:
                        primary = error
                        raise
                    finally:
                        for cleanup in (lambda: stream.close() if stream is not None else None,
                                        lambda: os.close(out) if out is not None else None,
                                        lambda: Path(temporary).unlink(missing_ok=True) if temporary is not None else None):
                            try:
                                cleanup()
                            except BaseException as error:
                                if primary is None:
                                    primary = error
                                else:
                                    primary.add_note(f'Ownership import cleanup failed: {error!r}')
                        if primary is not None and sys.exception() is None:
                            raise primary
        directory = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
            # Marker publication follows durable publication of every record.
            out = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
            try:
                os.fsync(out)
            finally:
                close_preserving(out)
            os.fsync(directory)
        finally:
            close_preserving(directory)
    finally:
        primary = sys.exception()
        for owned in (legacy_fd, fd):
            if owned is not None:
                try:
                    os.close(owned)
                except BaseException as error:
                    if primary is None:
                        primary = error
                    else:
                        primary.add_note(f'Ownership migration close failed: {error!r}')
        if primary is not None and sys.exception() is None:
            raise primary


def _terminate_launched_child(child: subprocess.Popen) -> None:
    """Reap this parent's child if registration could not clean it via pidfd."""
    errors = []
    if child.poll() is not None:
        child.wait(timeout=5)
        return

    def send(method) -> None:
        try:
            method()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)

    def wait(timeout, *, escalate=False) -> None:
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            if not escalate:
                errors.append(error)
        except BaseException as error:
            errors.append(error)

    send(child.terminate)
    wait(2, escalate=True)
    for _attempt in range(2):
        if child.returncode is not None:
            break
        send(child.kill)
        wait(5)
    if errors:
        for error in errors[1:]:
            errors[0].add_note(f"Spawned child cleanup failed: {error!r}")
        raise errors[0]


def launch_process(args, command: list[str]) -> None:
    """Keep the spawned child unreaped until registration opens its pidfd.

    Looking up a shell's old $! in a later helper could open a reused PID.
    This parent owns the Popen child and never polls/waits before pidfd_open,
    so even a child that exits immediately still pins its PID as a zombie.
    """
    if not command:
        raise ValueError("missing service command")
    cwd = args.panel_root if args.service == "panel" else args.comfy_root
    child = None
    descriptors = []
    log = None
    artifacts = [args.pid_file]
    try:
        gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
        descriptors.extend((gate_read, gate_write))
        ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
        descriptors.extend((ready_read, ready_write))
        args._launch_command = list(command)
        args._target_argv = expected_launch_argv(command)
        args._gate_command = [sys.executable, str(Path(__file__).resolve()), "_gate",
                              str(gate_read), str(ready_write), secrets.token_hex(24), "--", *command]
        log = args.log_file.open("wb")
        child = subprocess.Popen(args._gate_command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True, pass_fds=(gate_read, ready_write),
                                 env=getattr(args, 'environment', None))
        args.pid = child.pid
        log.close()
        log = None
        for descriptor in (gate_read, ready_write):
            close_preserving(descriptor)
            descriptors.remove(descriptor)
        ready = select.poll()
        ready.register(ready_read, select.POLLIN | select.POLLHUP)
        if not ready.poll(5000) or os.read(ready_read, 1) != b"R":
            raise RuntimeError("Service launch gate did not become ready")
        os.close(ready_read)
        descriptors.remove(ready_read)
        record_process(args, artifacts=artifacts)
        if os.write(gate_write, b"C") != 1:
            raise RuntimeError("Service launch gate did not accept authorization")
        # COMMIT is the last fallible operation that can abort launch. The
        # service may now create descendants; ownership remains durably
        # recorded even if closing the parent's pipe later reports an error.
    except BaseException as error:
        # record_process normally terminates through its original pidfd. The
        # parent still owns cleanup if capture itself failed or that cleanup
        # left the child alive. Never resolve a PID from the registration file.
        try:
            if child is not None:
                _terminate_launched_child(child)
        except BaseException as cleanup_error:
            error.add_note(f"Spawned child cleanup failed: {cleanup_error!r}")
            for note in getattr(cleanup_error, "__notes__", ()):
                error.add_note(note)
        finally:
            # Only this attempt's exact paths; other services may share the
            # directory, so a glob over registration tempfiles is unsafe.
            for artifact in artifacts:
                try:
                    artifact.unlink(missing_ok=True)
                except BaseException as cleanup_error:
                    error.add_note(f"Registration artifact cleanup failed: {cleanup_error!r}")
        raise
    finally:
        primary = sys.exception()
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                if primary is not None:
                    primary.add_note(f"Launch pipe close failed: {cleanup_error!r}")
                else:
                    try:
                        print("Launch pipe close failed after durable ownership commit", file=sys.stderr)
                    except BaseException:
                        pass
        if log is not None:
            try:
                log.close()
            except BaseException as cleanup_error:
                if primary is not None:
                    primary.add_note(f"Launch log close failed: {cleanup_error!r}")


def main() -> int:
    if len(sys.argv) >= 7 and sys.argv[1] == "_gate":
        launch_gate(int(sys.argv[2]), int(sys.argv[3]), sys.argv[6:])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("support", "migrate", "port-free", "launch", "record", "check", "signal", "stop", "listener", "desired"))
    parser.add_argument('--pid-dir', type=Path)
    parser.add_argument('--legacy-dir', type=Path)
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--controller-pid", type=int)
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
            require_group_control()
            return 0
        if args.command == 'migrate':
            require_group_control()
            migrate_records(args.pid_dir, args.legacy_dir)
            return 0
        if args.command == 'port-free' and args.pid_file is None:
            require_free_service_port(args.port)
            return 0
        with record_control(args.pid_file):
            if args.command == "port-free":
                require_free_service_port(args.port)
                return 0
            if args.command == "launch":
                if sys.version_info < (3, 11):
                    raise RuntimeError('Python >=3.11 required')
                require_group_control()
                require_live_launch_controller(args.controller_pid)
                launch_process(args, command)
                return 0
            if args.command == "record":
                record_process(args)
                return 0
            pid, descriptor = checked_pidfd(args, allow_gate=args.command in {"signal", "stop"})
            try:
                if args.command == "stop":
                    stop_group(pid, descriptor, keep_owner=True)
                    args.pid_file.unlink(missing_ok=True)
                elif args.command == "listener":
                    require_owned_listener(args, pid, descriptor)
                elif args.command == "desired":
                    require_desired_configuration(args, pid)
                elif args.command == "signal":
                    signal.pidfd_send_signal(descriptor, getattr(signal, "SIG" + args.signal))
                else:
                    print(pid)
            finally:
                close_preserving(descriptor)
            return 0
    except StaleIdentity as error:
        return 2 if getattr(error, "control_error", False) else 1
    except Exception:
        # No argv/environment dump: panel credentials live only in its env.
        return 2 if args.command in {"support", "check", "signal", "port-free", "stop", "listener", "desired"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
