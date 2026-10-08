"""Foreign processes and stale identities must survive real service_ctl calls."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from h3.scripts import process_identity
import tests.test_service_ctl as service_fixtures


class AdversarialProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = service_fixtures.ServiceControlTests()
        self.fixture.setUp()
        self.foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        deadline = time.monotonic() + 3
        while True:
            try:
                process_identity.process_snapshot(self.foreign.pid)
                break
            except (ProcessLookupError, process_identity.IndeterminateIdentity):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def tearDown(self):
        if self.foreign.poll() is None:
            self.foreign.terminate()
        self.foreign.wait(timeout=3)
        self.fixture.tearDown()

    def pidfile(self, service):
        directory = self.fixture.panel / "state/pids"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / (service + ".pid")

    def foreign_record(self, service, *, wrong_start=False):
        snapshot = process_identity.process_snapshot(self.foreign.pid)
        record = {key: snapshot[key] for key in ("pid", "start_time", "boot_id", "command_sha256")}
        record["service"] = service
        if wrong_start:
            record["start_time"] += 1
        return json.dumps(record)

    def foreign_survives_stop_and_start(self, record):
        for service in ("render", "prompt", "panel"):
            with self.subTest(service=service):
                path = self.pidfile(service)
                path.write_text(record(service))
                self.fixture.finished(self.fixture.command("stop", service))
                self.assertIsNone(self.foreign.poll())
                self.assertFalse(path.exists())
                path.write_text(record(service))
                self.fixture.finished(self.fixture.command("start", service))
                self.assertIsNone(self.foreign.poll())
                identity = json.loads(path.read_text())
                self.assertNotEqual(identity["pid"], self.foreign.pid)
                self.assertEqual(identity["service"], service)
                self.fixture.finished(self.fixture.command("stop", service))
                self.assertIsNone(self.foreign.poll())
                self.fixture.assert_lock_released()

    def test_legacy_numeric_foreign_pid_never_receives_a_signal_or_blocks_service_start(self):
        self.foreign_survives_stop_and_start(lambda _service: str(self.foreign.pid))

    def test_reused_foreign_pid_with_changed_start_time_is_stale(self):
        self.foreign_survives_stop_and_start(lambda service: self.foreign_record(service, wrong_start=True))

    def test_matching_start_time_does_not_make_a_foreign_command_an_owned_service(self):
        self.foreign_survives_stop_and_start(self.foreign_record)

    def test_start_time_boot_service_and_command_identity_are_all_required(self):
        self.fixture.finished(self.fixture.command("start", "render"))
        path = self.pidfile("render")
        original = path.read_text()
        identity = json.loads(original)
        for key, value in (("start_time", identity["start_time"] + 1), ("boot_id", "old-boot"),
                           ("service", "prompt"), ("command_sha256", "invalid")):
            with self.subTest(field=key):
                path.write_text(json.dumps(dict(identity, **{key: value})))
                self.fixture.finished(self.fixture.command("stop", "render"))
                os.kill(identity["pid"], 0)
                self.assertFalse(any(event["event"] == "stop" for event in self.fixture.events()))
                path.write_text(original)
        self.fixture.finished(self.fixture.command("stop", "render"))
        self.assertTrue(any(event["event"] == "stop" for event in self.fixture.events()))

    def test_signal_uses_open_pidfd_when_pid_is_replaced_after_identity_check(self):
        self.fixture.finished(self.fixture.command("start", "render"))
        path = self.pidfile("render")
        original = path.read_text()
        identity = json.loads(original)
        args = argparse.Namespace(pid_file=path, service="render", python=self.fixture.environment["COMFY_PYTHON"],
                                  comfy_root=str(self.fixture.comfy), panel_root=str(self.fixture.panel), port=8188)
        pid, descriptor = process_identity.checked_pidfd(args)
        try:
            # After validation the numeric PID lookup is irrelevant: the open
            # descriptor still owns the original process instance. Simulate
            # reuse without needing to force kernel PID allocation.
            with patch.object(os, "kill", side_effect=AssertionError("numeric PID signalling is unsafe")), \
                 patch.object(process_identity, "process_snapshot", side_effect=AssertionError("must not re-resolve PID")):
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)
        self.assertIsNone(self.foreign.poll())
        self.fixture.finished(self.fixture.command("stop", "render"))

    def test_pidfd_is_closed_without_signalling_if_identity_changed_after_open(self):
        path = self.pidfile("render")
        path.write_text(self.foreign_record("render", wrong_start=True))
        args = argparse.Namespace(pid_file=path, service="render", python=self.fixture.environment["COMFY_PYTHON"],
                                  comfy_root=str(self.fixture.comfy), panel_root=str(self.fixture.panel), port=8188)
        with patch.object(signal, "pidfd_send_signal") as send:
            with self.assertRaises(ValueError):
                process_identity.checked_pidfd(args)
        send.assert_not_called()
        self.assertIsNone(self.foreign.poll())
