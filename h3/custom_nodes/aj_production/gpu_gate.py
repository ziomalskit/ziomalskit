"""Cooperative single-GPU phase leases; never suspend a resident CUDA process.

Render publishes a closed phase BEFORE waiting for the current LLM invocation
to reap. Every subsequent LLM stage waits outside its native invocation. The
renderer holds an exclusive lease during conditioning/decoding/unknown state;
sampling permits a measured, bounded LLM lease. OS locks survive neither a
process crash nor a worker restart; the phase owner and heartbeat must be live.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
import uuid


def process_start(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] in {"Z", "X"}:
        raise ValueError("GPU phase owner is no longer alive")
    return fields[19]


def measured_free_mb():
    try:
        value = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                        stderr=subprocess.DEVNULL, timeout=2, text=True).strip().splitlines()
        # Supported deployment is one GPU. Never sum different GPU memories.
        return int(value[0]) if len(value) == 1 and value[0].isdigit() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class GPUGate:
    def __init__(self, directory, *, check=lambda: None, measure=measured_free_mb):
        self.check, self.measure = check, measure
        path = Path(directory).absolute()
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in path.parts[1:]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            self.directory = fd
            fd = None
        finally:
            if fd is not None:
                os.close(fd)
        self.epoch = uuid.uuid4().hex
        self.owner = {"pid": os.getpid(), "start": process_start(os.getpid()), "epoch": self.epoch}
        self.render_lease = self.owner_lease = None
        self.phase = "unknown"
        self._mutex = threading.RLock()
        self._closed = threading.Event()
        self._heartbeat = None

    def _open(self, name, flags=os.O_RDWR | os.O_CREAT):
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=self.directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise ValueError("unsafe GPU coordination file")
        return fd

    @contextmanager
    def _state_lock(self):
        fd = self._open("state.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _write(self):
        name = ".state-" + uuid.uuid4().hex
        fd = self._open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({**self.owner, "phase": self.phase, "at": time.monotonic()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                target = os.stat("state.json", dir_fd=self.directory, follow_symlinks=False)
                if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1:
                    raise ValueError("unsafe GPU state target")
            except FileNotFoundError:
                pass
            os.replace(name, "state.json", src_dir_fd=self.directory, dst_dir_fd=self.directory)
        finally:
            try:
                os.unlink(name, dir_fd=self.directory)
            except FileNotFoundError:
                pass

    def _read(self):
        try:
            fd = self._open("state.json", os.O_RDONLY)
            with os.fdopen(fd) as stream:
                state = json.loads(stream.read(4097))
            if (not isinstance(state, dict) or type(state.get("pid")) is not int or state["pid"] <= 0
                    or type(state.get("at")) not in (int, float) or not math.isfinite(state["at"])
                    or not 0 <= time.monotonic() - state["at"] <= 3
                    or process_start(state["pid"]) != state.get("start")):
                return None
            owner = self._open("renderer.lock")
            try:
                try:
                    fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return None  # fresh file without live renderer ownership
                except BlockingIOError:
                    pass
            finally:
                os.close(owner)
            return state
        except (OSError, ValueError, KeyError, IndexError):
            return None

    def start_renderer(self):
        self.owner_lease = self._open("renderer.lock")
        try:
            fcntl.flock(self.owner_lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.transition("unknown")
        except BaseException:
            os.close(self.owner_lease)
            self.owner_lease = None
            raise
        def heartbeat():
            while not self._closed.wait(.5):
                try:
                    with self._mutex, self._state_lock():
                        self._write()
                except Exception:
                    # A stale heartbeat closes all prompt admission.
                    return
        self._heartbeat = threading.Thread(target=heartbeat, daemon=True)
        self._heartbeat.start()

    def transition(self, phase):
        if phase not in {"idle", "sampling", "conditioning", "decoding", "recovery", "unknown"}:
            phase = "unknown"
        with self._mutex:
            with self._state_lock():
                self.phase = phase
                self._write()
            if phase in {"idle", "sampling"}:
                if self.render_lease is not None:
                    os.close(self.render_lease)
                    self.render_lease = None
            elif self.render_lease is None:
                fd = self._open("inference.lock")
                try:
                    deadline = time.monotonic() + 900
                    while True:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            self.check()
                            if time.monotonic() >= deadline:
                                raise TimeoutError("render phase could not settle active LLM")
                            time.sleep(.05)
                    self.render_lease = fd
                    fd = None
                finally:
                    if fd is not None:
                        os.close(fd)

    @contextmanager
    def prompt(self, headroom_mb, allow_sampling, timeout):
        if type(headroom_mb) is not int or headroom_mb <= 0 or type(allow_sampling) is not bool:
            raise ValueError("invalid frozen GPU headroom policy")
        deadline, lease = time.monotonic() + timeout, None
        try:
            while lease is None:
                self.check()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Prompt Worker paused by render priority or unknown GPU state")
                with self._state_lock():
                    state = self._read()
                    phase = (state or {}).get("phase")
                    if phase == "idle" or phase == "sampling" and allow_sampling:
                        fd = self._open("inference.lock")
                        try:
                            # One Prompt Worker/LLM on this GPU, even if a second
                            # process was accidentally started. Render uses the
                            # same exclusive lock only outside sampling.
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            free = self.measure()
                            if type(free) is int and free >= headroom_mb:
                                lease, fd = fd, None
                        except BlockingIOError:
                            pass
                        finally:
                            if fd is not None:
                                os.close(fd)
                if lease is None:
                    time.sleep(.05)
            yield lease
        finally:
            if lease is not None:
                os.close(lease)

    def close(self):
        self._closed.set()
        if self._heartbeat:
            self._heartbeat.join(timeout=2)
        for fd in (self.render_lease, self.owner_lease, self.directory):
            if fd is not None:
                os.close(fd)
        self.render_lease = self.owner_lease = self.directory = None
