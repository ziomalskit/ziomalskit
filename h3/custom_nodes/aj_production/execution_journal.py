"""Durable terminal records and a queue-mutex restart barrier for pinned ComfyUI.

The worker fsyncs results BEFORE removing running ownership. Quiesce closes
admission and cannot acknowledge readiness while any running prompt remains.
An unavailable/stuck worker therefore cannot authorize a destructive restart.
"""
import copy
import heapq
import json
import os
from pathlib import Path
import stat
import uuid

PROTOCOL = "aj-terminal-v1"


class ExecutionJournal:
    def __init__(self, queue, directory):
        self.queue = queue
        self.directory = Path(os.path.abspath(directory))
        # Create/walk without following a retained ancestor link, even before
        # the first metadata write. No outside directory is created via a link.
        descriptor = os.open(self.directory.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.directory.parts[1:]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        finally:
            os.close(descriptor)
        self.published_ids = set()
        self.quiesced = False
        with queue.mutex:
            for prompt_id, entry in queue.history.items():
                self.write(prompt_id, entry)
            original_done, original_put = queue.task_done, queue.put

            def task_done(item_id, history_result, status, process_item=None):
                with queue.mutex:
                    prompt = copy.deepcopy(queue.currently_running[item_id])
                    if process_item is not None:
                        prompt = process_item(prompt)
                    entry = {"prompt": prompt, "outputs": {}, "status": copy.deepcopy(status._asdict()) if status else None}
                    entry.update(history_result)
                    self.write(prompt[1], entry)
                    original_done(item_id, history_result, status, lambda _prompt: prompt)

            def put(item):
                with queue.mutex:
                    if self.quiesced:
                        raise RuntimeError("AJ worker quiesced; reconcile before submitting")
                    return original_put(item)

            def get(timeout=None):
                with queue.not_empty:
                    while not queue.queue or self.quiesced:
                        queue.not_empty.wait(timeout=timeout)
                        if timeout is not None and (not queue.queue or self.quiesced):
                            return None
                    item = heapq.heappop(queue.queue)
                    index = queue.task_counter
                    queue.currently_running[index] = copy.deepcopy(item)
                    queue.task_counter += 1
                    queue.server.queue_updated()
                    return item, index
            queue.task_done, queue.put, queue.get = task_done, put, get

    def path(self, prompt_id):
        if str(uuid.UUID(prompt_id)) != prompt_id:
            raise ValueError("invalid execution identity")
        return self.directory / (prompt_id + ".json")

    def write(self, prompt_id, entry):
        target = self.path(prompt_id)
        body = json.dumps({"protocol": PROTOCOL, "prompt_id": prompt_id, "entry": entry}, allow_nan=False).encode()
        if len(body) > 32 * 1024 * 1024:
            raise ValueError("oversized execution result")
        temporary = self.directory / ("." + uuid.uuid4().hex + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists() or target.is_symlink():
                if not stat.S_ISREG(target.lstat().st_mode):
                    raise ValueError("unsafe execution result")
            os.replace(temporary, target)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.published_ids.add(prompt_id)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, prompt_id):
        try:
            fd = os.open(self.path(prompt_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
                raise ValueError("unsafe execution result")
            receipt = json.load(stream)
        if receipt.get("protocol") != PROTOCOL or receipt.get("prompt_id") != prompt_id or not isinstance(receipt.get("entry"), dict):
            raise ValueError("invalid execution result receipt")
        return receipt["entry"]

    def cancel(self, prompt_id):
        with self.queue.mutex:
            for item in self.queue.queue:
                if item[1] == prompt_id:
                    # Publish a durable tombstone before deletion, while get()
                    # cannot move this prompt into running ownership.
                    entry = {"prompt": item, "outputs": {}, "status": {"status_str": "error", "completed": True,
                             "messages": [["execution_interrupted", {"prompt_id": prompt_id}]]},
                             "aj_cancel": {"protocol": PROTOCOL, "prompt_id": prompt_id, "state": "pending_deleted"}}
                    self.write(prompt_id, entry)
                    self.queue.delete_queue_item(lambda candidate: candidate[1] == prompt_id)
                    return entry["aj_cancel"]
            if self.queue.interrupt_if_running(prompt_id):
                return {"protocol": PROTOCOL, "prompt_id": prompt_id, "state": "running_signalled"}
            entry = self.read(prompt_id)
            return entry.get("aj_cancel") or {"protocol": PROTOCOL, "prompt_id": prompt_id, "state": "unknown"}

    def quiesce(self, owned_ids):
        with self.queue.mutex:
            items = list(self.queue.currently_running.values()) + list(self.queue.queue)
            if any(item[1] not in owned_ids for item in items):
                raise ValueError("quiesce refused: unowned execution present")
            self.quiesced = True
            for item in list(self.queue.queue):
                self.cancel(item[1])
            for item in list(self.queue.currently_running.values()):
                self.queue.interrupt_if_running(item[1])
            self.queue.not_empty.notify_all()
            if not self.queue.currently_running and not self.queue.queue:
                # A bound original task_done obtained before hook installation
                # may have published directly to native history. Persist that
                # terminal record while the restart barrier still owns mutex.
                for prompt_id, entry in self.queue.history.items():
                    if prompt_id not in self.published_ids:
                        self.write(prompt_id, entry)
            # task_done must durably settle every running identity before ready.
            return {"protocol": PROTOCOL, "ready": not self.queue.currently_running and not self.queue.queue}
