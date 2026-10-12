"""Second Ultra F5 regressions: exact text, real panel requests, no workers."""
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

import httpx

from h3.app.prompt_modes import freeze_prompt_source, validate_manual_final
from tests.helpers import load_controller
from tests.test_final_blocker_fixes import request
from tests.test_manual_prompt import SHOTS


MALFORMED = (
    "abc00:05.000",
    "00:05.000abc",
    "At −00:10.000, change the camera angle.",
    "Range 00:20.000–00:01.000.",
    "Range 00:00.000-00:10.000-00:05.000.",
    "The clock reads 00:06.680.123.",
    "At ００:９０.０００, change the camera angle.",
    "-00:01.000",
    "+00:01.000",
    "00:60.000",
    "00:05.0000",
    "00:05..000",
    "00：05.000",
    "00:05．000",
    "00:05,000",
    "00:05.000-00:04.000",
    "00:05.000—00:06.000",
    "00:05.000–00:06.000",
    "00:05.000 - 00:06.000 - 00:07.000",
)
REVIEW_PROSE = (
    "Show the unquoted IPv6 address 2001:0db8:85a3:0000:0000:8a2e:0370:7334 on a terminal screen.",
    "Show a scoreboard reading 2:0 after the winning goal.",
    "Use a mixing ratio of 0:1 for the background color.",
)
ORDINARY = REVIEW_PROSE + tuple(
    f"Use an aspect ratio of {ratio}." for ratio in ("16:10", "16:9", "4:3", "3:2", "21:9", "9:16")
) + (
    "Version 2.10.4 is displayed on screen.",
    "Open https://example.com:8080/status in the browser.",
    "Connect to host.example:443.",
    "The file is named take_001_v2.3.mov.",
    "The file is named camera_00:05.000.png.",
    "Address 2001:db8::1 is visible.",
    "Address ::1 is visible.",
    "Address fe80::1 is visible.",
    "Address 2001:db8:0:1:1:1:1:1 is visible.",
    "Address ::ffff:0:192.0.2.1 is visible.",
    "Use ratio 2:1.",
)


class ManualTimingTests(unittest.TestCase):
    def assert_exact(self, text):
        original = text.encode("utf-8")
        self.assertEqual(validate_manual_final(text, 20.04).encode("utf-8"), original)
        source = freeze_prompt_source("manual_final", None, text, 20.04)
        self.assertEqual(source["manual_final_prompt"].encode("utf-8"), original)
        self.assertEqual(source["manual_final_prompt_sha256"], hashlib.sha256(original).hexdigest())

    def test_review_prefixed_clock_rejects(self):
        with self.assertRaises(ValueError):
            validate_manual_final("abc00:05.000", 20.04)

    def test_review_false_positives_accept_unchanged(self):
        for text in REVIEW_PROSE:
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_all_required_malformed_timing_rejects(self):
        for text in MALFORMED:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_all_required_ordinary_prose_accepts_exactly(self):
        for text in ORDINARY:
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_integer_pairs_are_prose_without_temporal_context(self):
        for pair in ("0:0", "0:12", "12:0", "123:456", "00:05", "1:23456"):
            with self.subTest(pair=pair):
                self.assert_exact(f"Display {pair} beside the subject.")

    def test_filename_shape_does_not_override_temporal_directives(self):
        for name in ("camera_00:05.000.png", "take00:05.000.mov", "ref_00:05.000.tar.gz"):
            with self.subTest(name=name):
                self.assert_exact(f"File {name} is visible.")
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_manual_final("The clock reads " + name, 20.04)

    def test_named_clock_labels_and_timelines_preserve_canonical_values(self):
        for text in ("Clock:00:05.000", "The clock reads:00:05.000.",
                     "Timeline:00:00.000-00:10.000"):
            with self.subTest(text=text):
                self.assert_exact(text)
        for text in ("Clock:abc00:05.000", "Clock:00:05.000abc", "At:00:05.000",
                     "Clock:2001:db8::1", "Clock:camera_00:05.000.png",
                     "Timeline:00:10.000-00:05.000"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_ipv6_ipv4_tail_is_address_prose_not_fractional_time(self):
        for address in ("::ffff:192.0.2.1", "::ffff:0:192.0.2.1", "2001:db8::192.0.2.1",
                        "ffff::0:192.0.2.1", "ffff:0:0:0:0:0:192.0.2.1"):
            with self.subTest(address=address):
                self.assert_exact(f"Address {address} is visible.")
                self.assert_exact(f"Open https://[{address}]:8080/status.")
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_manual_final("The clock reads " + address, 20.04)

    def test_temporal_context_cannot_be_disguised_as_ratio_or_ipv6(self):
        for token in ("2:0", "0:1", "00:05", "2001:db8::1", "::1", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"):
            for directive in ("At ", "The clock reads ", "Timeline: "):
                with self.subTest(token=token, directive=directive), self.assertRaises(ValueError):
                    validate_manual_final(directive + token, 20.04)

    def test_clock_boundaries_reject_symmetrically(self):
        for prefix, suffix in (("abc", ""), ("é", ""), ("", "abc"), ("", "é"),
                               ("abc", "xyz"), ("e\u0301", ""), ("", "e\u0301"),
                               ("", ".123"), ("", "_extra")):
            with self.subTest(prefix=prefix, suffix=suffix), self.assertRaises(ValueError):
                validate_manual_final(prefix + "00:05.000" + suffix, 20.04)

    def test_invisible_unicode_cannot_hide_a_malformed_clock(self):
        for char in ("\u200b", "\u200d", "\u2060", "\u0301"):
            for offset in range(1, len("00:05.000")):
                text = "00:05.000"[:offset] + char + "00:05.000"[offset:]
                with self.subTest(text=text), self.assertRaises(ValueError):
                    validate_manual_final(text, 20.04)
            for text in ("abc" + char + "00:05.000", "00:05.000" + char + "abc"):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    validate_manual_final(text, 20.04)

    def test_valid_shots_timelines_and_scalar_bounds_remain_exact(self):
        for text in (SHOTS, "Timeline: 00:00.000-00:10.000", "Range 00:00.000 - 00:20.040.",
                     "The clock reads 00:00.000.", "The clock reads 00:20.040."):
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_structural_cuts_and_intervals_remain_authoritative(self):
        for text in ("At 00:05.000, cut.", "[Shot 1] At 00:00.000, move.",
                     SHOTS.replace("[Shot 2]", "[Shot 4]"), SHOTS.replace("00:13.360", "00:06.680"),
                     SHOTS.replace("00:13.360", "00:20.040"), "Timeline: 0:1-2:3",
                     "Timeline: 00:00.000-00:10.000\nTimeline: 00:09.000-00:20.040"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_unicode_crlf_and_whitespace_are_never_normalized(self):
        for text in ORDINARY:
            with self.subTest(text=text):
                self.assert_exact("  Cafe\u0301 żółć 🎬\r\n" + text + "\t \r\n")


class ManualTimingRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture = load_controller(production_probes=True)
        self.m = fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        self.body = request(self.m).model_dump(mode="json", exclude={"prompt"})
        self.body["prompt_mode"] = "manual_final"

    def body_for(self, text):
        return {**self.body, "request_id": str(uuid.uuid4()), "manual_final_prompt": text}

    async def test_actual_batches_rejects_prefixed_clock_before_any_worker_http(self):
        # Instantiate the local ASGI client before fencing every worker client.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                response = await client.post("/api/batches", json=self.body_for("abc00:05.000"))
                self.assertEqual(response.status_code, 400, response.text)
                worker.assert_not_called()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertEqual(self.m.request_results, {})
        self.assertFalse(self.m.QUEUE_FILE.exists())

    async def test_actual_batches_rejects_all_required_bad_timing_without_publication(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in MALFORMED:
                    with self.subTest(text=text):
                        response = await client.post("/api/batches", json=self.body_for(text))
                        self.assertEqual(response.status_code, 400, response.text)
                worker.assert_not_called()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])

    async def test_actual_batches_accepts_ordinary_prose_with_exact_durable_text(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in ORDINARY:
                    with self.subTest(text=text):
                        response = await client.post("/api/batches", json=self.body_for(text))
                        self.assertEqual(response.status_code, 200, response.text)
                        job = self.m.queue[-1]
                        self.assertEqual(job["status"], "render_queued_auto")
                        self.assertEqual(job["context"]["manual_final_prompt"].encode("utf-8"), text.encode("utf-8"))
                        self.assertEqual(job["final_h3_prompt"].encode("utf-8"), text.encode("utf-8"))
                        self.assertEqual(job["context"]["manual_final_prompt_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
                        self.assertEqual(job["context"]["legal_frame_count"], 481)
                        self.assertEqual(job["context"]["effective_duration_seconds"], 20.040)
                        persisted = json.loads(self.m.QUEUE_FILE.read_text())["queue"][-1]
                        self.assertEqual(persisted["context"]["manual_final_prompt"].encode("utf-8"), text.encode("utf-8"))
                        if text in REVIEW_PROSE:
                            path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=text)
                            graph = json.loads(path.read_text())
                            override = next(node for node in graph["nodes"] if node["id"] == 2632)
                            self.assertEqual(override["widgets_values_named"]["positive"].encode("utf-8"), text.encode("utf-8"))
                worker.assert_not_called()
        self.assertEqual(len(self.m.queue), len(ORDINARY))
        self.assertIsNone(self.m.pick_next_prompt_job())


if __name__ == "__main__":
    unittest.main()
