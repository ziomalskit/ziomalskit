"""Local, monotonic installation timings. Never record argv, env or errors."""
from contextlib import AbstractContextManager
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

NAME = re.compile(r'[A-Za-z0-9_.-]{1,96}')


def emit(record):
    path = os.environ.get('H3_INSTALL_TIMING_FILE')
    if not path:
        return
    # A private local report, never a provider or telemetry endpoint.
    path = Path(path).absolute()
    for part in reversed((path.parent, *path.parent.parents)):
        if part.is_symlink() or not part.is_dir():
            raise ValueError('timing report parent must be a real directory')
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('timing report must be a private regular file')
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = (json.dumps(record, sort_keys=True) + '\n').encode()
        if os.write(fd, data) != len(data):
            raise OSError('incomplete timing record')
        os.fsync(fd)
    finally:
        os.close(fd)


class Stage(AbstractContextManager):
    def __init__(self, name, *, artifact=None, size_bytes=None, clock=None):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('invalid timing stage')
        if artifact is not None and (not isinstance(artifact, str) or not NAME.fullmatch(artifact)):
            raise ValueError('invalid timing artifact identifier')
        if size_bytes is not None and (type(size_bytes) is not int or size_bytes < 0):
            raise ValueError('invalid timing byte count')
        self.clock = clock or time.monotonic
        self.record = {'id': uuid.uuid4().hex, 'stage': name}
        if artifact is not None: self.record['artifact'] = artifact
        if size_bytes is not None: self.record['bytes'] = size_bytes

    def __enter__(self):
        self.record.update(start_monotonic=self.clock(), status='started')
        emit(self.record)
        return self

    def finish(self, status):
        end = self.clock()
        record = {**self.record, 'status': status, 'end_monotonic': end,
                  'elapsed_seconds': max(0, end - self.record['start_monotonic'])}
        emit(record)
        if os.environ.get('H3_INSTALL_TIMING_FILE'):
            print(json.dumps(record, sort_keys=True), flush=True)
        return record

    def __exit__(self, kind, value, traceback):
        self.finish('failed' if kind else 'completed')


def summary(path, *, clock=None):
    now = (clock or time.monotonic)()
    records = []
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 16 * 1024**2:
        os.close(fd)
        raise ValueError('invalid private timing report')
    with os.fdopen(fd, encoding='utf-8') as stream:
        for line in stream:
            if len(line) > 4096: raise ValueError('invalid timing record')
            records.append(json.loads(line))
    last, completed = {}, None
    for record in records:
        last[record['id']] = record
        if record['status'] == 'completed': completed = record['stage']
    unfinished = [{**r, 'status': 'interrupted',
                   'elapsed_seconds': max(0, now - r['start_monotonic'])}
                  if r['status'] == 'started' else r for r in last.values() if r['status'] in {'started', 'failed'}]
    end = now if any(r['status'] == 'started' for r in last.values()) else max((r['end_monotonic'] for r in last.values()), default=now)
    return {'last_completed_stage': completed, 'failed_or_interrupted': unfinished,
            'total_elapsed_seconds': max(0, end - min((r['start_monotonic'] for r in last.values()), default=end)),
            'stages': list(last.values())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'begin', 'end', 'summary'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        values = args.arguments
        if args.action == 'summary':
            print(json.dumps(summary(values[0] if values else os.environ['H3_INSTALL_TIMING_FILE']), sort_keys=True))
        elif args.action == 'begin':
            stage = Stage(values[0]).__enter__()
            print(json.dumps(stage.record, sort_keys=True))
        elif args.action == 'end':
            record = json.loads(values[0])
            if (set(record) != {'id', 'stage', 'start_monotonic', 'status'}
                    or not re.fullmatch('[0-9a-f]{32}', record['id']) or record['status'] != 'started'
                    or type(record['start_monotonic']) not in (int, float)
                    or not math.isfinite(record['start_monotonic']) or record['start_monotonic'] < 0):
                raise ValueError('invalid timing token')
            stage = Stage(record['stage']); stage.record = record
            if values[1] not in {'completed', 'failed'}: raise ValueError('invalid stage result')
            stage.finish(values[1])
        else:
            command = values[1:]
            if command[:1] == ['--']: command = command[1:]
            with Stage(values[0]) as stage:
                result = subprocess.run(command, check=False)
                if result.returncode:
                    # Finish once, keeping only the outcome, never the command.
                    raise SystemExit(result.returncode)
        return 0
    except SystemExit:
        raise
    except BaseException:
        print('Installation stage/report failed; inspect the private stage report.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
