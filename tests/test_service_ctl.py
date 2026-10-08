"""Real concurrent service_ctl commands with inert, CPU-only worker processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from tests.test_deployment import executable


ROOT = Path(__file__).resolve().parents[1]


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aj-service-lock-")
        self.base = Path(self.temporary.name)
        self.panel = self.base / "panel with spaces"
        (self.panel / "scripts").mkdir(parents=True)
        for name in ("service_ctl.sh", "python_env.sh", "process_identity.py"):
            shutil.copy2(ROOT / "h3/scripts" / name, self.panel / "scripts" / name)
        self.comfy = self.base / "ComfyUI"
        self.comfy.mkdir()
        prefix = self.base / "env"
        self.log = self.base / "workers.jsonl"
        self.commands = []
        worker = f'''#!{sys.executable}
import json,os,signal,sys,time
from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
a=sys.argv[1:]
if a and a[0].endswith('/process_identity.py'):
 os.execv({sys.executable!r},[{sys.executable!r},*a])
if a and a[0]=='-c':
 print({str(prefix)!r});sys.exit(0)
service='panel' if a[:2]==['-m','uvicorn'] else ('render' if a[a.index('--port')+1]=='8188' else 'prompt')
def record(event):
 with open({str(self.log)!r},'a') as out:out.write(json.dumps({{'event':event,'service':service,'pid':os.getpid(),'lock_inherited':os.path.exists('/proc/self/fd/9')}})+'\\n')
def stop(*args):
 record('stop');sys.exit(0)
signal.signal(signal.SIGTERM,stop)
record('start')
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(401 if service=='panel' else 200);self.end_headers()
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',int(a[a.index('--port')+1])),Handler).serve_forever()
'''
        python = prefix / "bin/python"
        executable(python, worker)
        tools = self.base / "bin"
        tools.mkdir()
        self.environment = dict(os.environ, PANEL_ROOT=str(self.panel), COMFY_ROOT=str(self.comfy),
                                WORKSPACE=str(self.base), PYTHON_BIN=str(python), COMFY_PYTHON=str(python),
                                PID_DIR=str(self.panel / 'state/pids'),
                                RUNTIME_ENV=str(self.base / "absent.env"), H3_PANEL_PASSWORD="test-only",
                                PATH=str(tools) + ":" + os.environ["PATH"], H3_READINESS_TIMEOUT="3")

    def tearDown(self):
        for process in self.commands:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        # Stop only inert workers launched by this test, even if a command failed.
        pids = {row["pid"] for row in self.events() if row["event"] == "start"}
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            stopped = {row["pid"] for row in self.events() if row["event"] == "stop"}
            if pids <= stopped:
                break
            time.sleep(.01)
        self.temporary.cleanup()

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def command(self, action, service):
        process = subprocess.Popen(["bash", str(self.panel / "scripts/service_ctl.sh"), action, service],
                                   env=self.environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.commands.append(process)
        return process

    def finished(self, process):
        out, err = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, out + err)

    def wait_start(self, service):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(row["event"] == "start" and row["service"] == service for row in self.events()):
                return
            time.sleep(.01)
        self.fail(f"worker {service} did not start")

    def assert_lock_released(self):
        lock = self.panel / "state/pids/service_ctl.lock"
        result = subprocess.run(["flock", "-n", str(lock), "true"], capture_output=True)
        self.assertEqual(result.returncode, 0, "a worker retained the command lock")
        self.assertFalse(any(row["lock_inherited"] for row in self.events()))

    def test_two_parallel_starts_create_one_worker_and_release_lock(self):
        for service in ("render", "prompt", "panel"):
            with self.subTest(service=service):
                first = self.command("start", service)
                second = self.command("start", service)
                self.finished(first)
                self.finished(second)
                starts = [row for row in self.events() if row["event"] == "start" and row["service"] == service]
                self.assertEqual(len(starts), 1)
                pidfile = self.panel / "state/pids" / f"{service}.pid"
                identity = json.loads(pidfile.read_text())
                self.assertEqual(identity["pid"], starts[0]["pid"])
                self.assertEqual(identity["service"], service)
                self.assertGreater(identity["start_time"], 0)
                self.assert_lock_released()
                self.finished(self.command("stop", service))

    def test_two_parallel_start_all_commands_do_not_duplicate_any_service(self):
        first = self.command("start", "all")
        second = self.command("start", "all")
        self.finished(first)
        self.finished(second)
        starts = [row for row in self.events() if row["event"] == "start"]
        self.assertEqual([row["service"] for row in starts], ["render", "prompt", "panel"])
        self.assert_lock_released()

    def test_start_stop_restart_all_are_serialized_as_complete_operations(self):
        start = self.command("start", "all")
        self.wait_start("render")
        stop = self.command("stop", "all")
        restart = self.command("restart", "all")
        for process in (start, stop, restart):
            self.finished(process)
        rows = self.events()
        events = [(row["event"], row["service"]) for row in rows]
        starts = [("start", service) for service in ("render", "prompt", "panel")]
        stops = [("stop", service) for service in ("render", "prompt", "panel")]
        restarting = [(event, service) for service in ("render", "prompt", "panel") for event in ("stop", "start")]
        self.assertIn(events, [starts + stops + starts, starts + restarting + stops])
        alive = {service: 0 for service in ("render", "prompt", "panel")}
        for event, service in events:
            alive[service] += 1 if event == "start" else -1
            self.assertIn(alive[service], (0, 1), "duplicate or untracked worker")
        for service, count in alive.items():
            self.assertEqual((self.panel / "state/pids" / f"{service}.pid").exists(), bool(count))
        self.assert_lock_released()
