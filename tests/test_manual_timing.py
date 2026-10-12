"""Second Ultra F5 regressions: exact text, real panel requests, no workers."""
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

import httpx

from h3.app.manual_timing import manual_clocks
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
    "-\u200b 00:01.000",
    "+\u2060 00:01.000",
    "−\u0301 00:01.000",
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
INCOMPLETE_INTERVALS = (
    "Range 00:20.000-00:01.",
    "Range 00:20.000-00:",
    "Range 00:20.000-00:01",
    "Range 00:20.000-00:01..",
    "Range 00:20.000-00:01.0",
    "Range 00:20.000-00:01.0000",
    "Range 00:20.000-00:01.000abc",
    "Range abc00:20.000-00:21.000",
    "Range 00:20.000–00:21.000",
    "Range 00:20.000—00:21.000",
    "Range 00:20.000-00:21.000-00:22.000",
    "Range 00:05.000-00:04.000",
)
TIMESTAMP_FILES = (
    "camera_00:05.000.mp4", "camera_00:05.000.m4v",
    "audio_00:05.000.mp3", "archive_00:05.000.7z",
    "camera_00:05.000.h264", "camera_00:05.000.mov",
    "camera_00:05.000.mkv", "camera_00:05.000.webm", "camera_00:05.000.txt",
)
TIMESTAMP_PATHS = (
    "/path/camera_00:05.000.mp4",
    "https://example.com/video/camera_00:05.000.mp4",
    "https://example.com:8080/video_00:05.000.m4v",
    "https://example.com/camera_00:05.000.mp4",
    "https://example.com:8080/video/camera_00:05.000.m4v",
    r"C:\clips\camera_00:05.000.mp4",
    "/clips/camera_00:05.000.mp4",
    "https://example.com/at 00:05.000",
    "https://example.com/end- 00:05.000",
    "https://example.com/clock 2:0",
    "https://example.com/time 0:1",
)
NUMERIC_CLOCK_PROSE = (
    "The clock reads 00:05.000 - 4K video plays on the display.",
    "The clock reads 00:05.000 - 1920x1080 resolution is shown below.",
)
BACKUP_FILES = (
    "Display camera_00:05.000.mov~ in the file browser.",
    "Show camera_00:05.000.png-backup on the screen.",
    "/clips/camera_00:05.000.mov~",
    "https://example.com/video/camera_00:05.000.png-backup",
)
PLUS_BACKUP_FILES = (
    "Show camera_00:05.000+backup.mov on the screen.",
    "Display take_00:05.000+copy.m4v in the file browser.",
    "Show audio_00:05.000+backup.mp3 on the screen.",
    "Show archive_00:05.000+copy.7z on the screen.",
)
NONFINITE_INTERVALS = (
    "00:01.000-NaN",
    "00:01.000-Inf",
    "00:01.000-Infinity",
)
PATH_HIDDEN_RANGES = (
    "Range: /00:20.000-00:01.000",
    "Range /00:20.000-00:01.000",
    "Range ./00:20.000-00:01.000",
)
DECLARED_NON_CLOCK_VALUES = (
    "Clock:2026", "Clock: 4K", "Time:4K", "Timestamp:8bit",
    "Cut:123fps", "Shot:2026", "Range:2026", "Timeline:8bit",
)


class ManualTimingTests(unittest.TestCase):
    def assert_exact(self, text):
        original = text.encode("utf-8")
        self.assertEqual(validate_manual_final(text, 20.04).encode("utf-8"), original)
        source = freeze_prompt_source("manual_final", None, text, 20.04)
        self.assertEqual(source["manual_final_prompt"].encode("utf-8"), original)
        self.assertEqual(source["manual_final_prompt_sha256"], hashlib.sha256(original).hexdigest())

    def test_numeric_prose_is_not_an_interval_operand(self):
        for text in NUMERIC_CLOCK_PROSE:
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_timestamped_backup_files_accept_exactly(self):
        for text in BACKUP_FILES:
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_timestamped_plus_backup_files_accept_exactly(self):
        for text in PLUS_BACKUP_FILES:
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_plus_backup_suffix_cannot_hide_malformed_milliseconds(self):
        for name in ("camera_00:05.000abc+backup.mov", "camera_00:05.0000+backup.mov",
                     "camera_00:05..000+backup.mov"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_manual_final(name, 20.04)

    def test_nonfinite_interval_operands_require_canonical_clocks(self):
        for endpoint in ("NaN", "Inf", "Infinity", "nan", "INF", "infinity"):
            for text in ("00:01.000-" + endpoint, endpoint + "-00:05.000",
                         "Range 00:01.000 - " + endpoint):
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, "canonical ASCII"):
                    validate_manual_final(text, 20.04)

    def test_standalone_nonfinite_words_are_not_scalar_clocks(self):
        for word in ("NaN", "Inf", "Infinity"):
            with self.subTest(word=word):
                self.assert_exact(word)
                self.assertEqual(manual_clocks(word, 20040), [])
                with self.assertRaisesRegex(ValueError, "canonical ASCII"):
                    validate_manual_final("Clock:" + word, 20.04)

    def test_explicit_range_precedes_path_opacity(self):
        for text in PATH_HIDDEN_RANGES:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 30.04)

    def test_digit_leading_words_remain_prose_after_scalar_clocks(self):
        for word in ("4K", "8K", "1920x1080", "123fps", "2026", "8bit"):
            with self.subTest(word=word):
                self.assert_exact("The clock reads 00:05.000 - " + word + " is displayed.")

    def test_directive_operands_cannot_become_opaque_files_or_addresses(self):
        for directive in ("Range: ", "Timeline: ", "At ", "Clock reads ", "Time is ", "Timestamp: ", "Cut ", "Shot 2 "):
            for operand in ("/00:01.000-00:05.000", "camera_00:05.000.mov~", "[2001:db8::1]"):
                with self.subTest(directive=directive, operand=operand), self.assertRaises(ValueError):
                    validate_manual_final(directive + operand, 20.04)

    def test_filename_suffixes_do_not_exempt_malformed_fractional_groups(self):
        for text in ("camera_00:05.000abc.mp4", "camera_00:05.0000.mp4", "camera_00:05..000.mp4"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_declared_temporal_values_require_canonical_clocks(self):
        for text in DECLARED_NON_CLOCK_VALUES:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_review_prefixed_clock_rejects(self):
        with self.assertRaises(ValueError):
            validate_manual_final("abc00:05.000", 20.04)

    def test_review_incomplete_interval_endpoint_rejects(self):
        with self.assertRaises(ValueError):
            validate_manual_final("Range 00:20.000-00:01.", 20.04)

    def test_review_numeric_extension_filenames_accept_exactly(self):
        for name in TIMESTAMP_FILES + ("archive_00:05.7z", "camera_00:05.3gp"):
            with self.subTest(name=name):
                self.assert_exact("The file is named " + name + ".")

    def test_whole_interval_syntax_cannot_drop_incomplete_endpoints(self):
        # Every complete timestamp in this matrix is within duration, so syntax
        # failures cannot be accidentally satisfied by an unrelated bounds error.
        for text in INCOMPLETE_INTERVALS:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 30.04)

    def test_whole_intervals_preserve_canonical_text(self):
        for text in ("Range 00:01.000-00:05.000.", "Range 00:01.000 - 00:05.000.",
                     "Timeline: 00:00.000-00:10.000"):
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_partial_left_endpoints_and_noncanonical_operators_reject(self):
        for text in ("Range 00:01-00:05.000", "Range 00:-00:05.000",
                     "Range 00:01.0-00:05.000", "Range 00:01.000--00:05",
                     "Range 00:01.000+00:05", "Range 00:01.000- -00:05",
                     "Range 00:01.000-\u200b 00:05", "Range 00:01.000-Clock:00:05.000"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_urls_and_paths_are_opaque_without_english_context(self):
        for text in TIMESTAMP_PATHS:
            with self.subTest(text=text):
                self.assert_exact(text)
        for text in ("[camera_00:05.000.mp4]", "Show(camera_00:05.000.m4v)",
                     "IPv6:::ffff:0:192.0.2.1", "Address ::ffff:192.0.2.1%eth0 is visible."):
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_filename_shape_never_exempts_a_millisecond_tail(self):
        for text in ("abc00:05.000", "00:05.000abc", "abc00:05.000abc", "00:05.000mp4"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)
        self.assert_exact("00:05.000")

    def test_hyphenated_ordinary_prose_does_not_become_an_interval(self):
        for text in ("Version 2.10.4-beta is shown.", "Use a 16:10-compatible display.",
                     "The score changed from 2:0 to 3:0.",
                     "File camera_00:05.000.mp4-final.txt is displayed.",
                     "The clock reads 00:05.000 - keep the camera steady."):
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_opaque_filename_clocks_do_not_use_temporal_duration_bounds(self):
        for text in ("camera_05:00.000.mp4", "/clips/camera_99:99.999.m4v",
                     "https://example.com/camera_00:60.000.mp4"):
            with self.subTest(text=text):
                self.assert_exact(text)

    def test_explicit_clock_context_has_priority_over_opaque_spans(self):
        for text in TIMESTAMP_FILES + TIMESTAMP_PATHS:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final("Clock:" + text, 20.04)
        for text in ("Clock\u200b:camera_00:05.000.mp4", "The clo\u0301ck reads camera_00:05.000.mp4"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_invisible_characters_cannot_hide_extra_cut_directives(self):
        for text in ("At\u200b 00:07.123, cut.", "A\u2060t 00:07.123, cut.",
                     "At\u0301 00:07.123, cut.", SHOTS + "\nLater At\u200b 00:18.000, cut."):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_opaque_spans_cannot_supply_context_to_neighboring_values(self):
        for text in ("https://example.com/at 00:05.000", "https://example.com/end- 00:05.000",
                     "https://example.com/clock 2:0", "https://example.com/time 0:1"):
            with self.subTest(text=text):
                self.assert_exact(text)
        for text in ("https://example.com/at At 00:05.000, cut.",
                     "https://example.com/end- -00:05.000",
                     "https://example.com/clock Clock:00:60.000",
                     "https://example.com/at 00:20.000-00:01"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

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

    async def assert_accepted_exact_requests(self, texts):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in texts:
                    with self.subTest(text=text):
                        original = text.encode("utf-8")
                        digest = hashlib.sha256(original).hexdigest()
                        response = await client.post("/api/batches", json=self.body_for(text))
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(validate_manual_final(text, 20.04).encode("utf-8"), original)
                        frozen = freeze_prompt_source("manual_final", None, text, 20.04)
                        self.assertEqual(frozen["manual_final_prompt"].encode("utf-8"), original)
                        self.assertEqual(frozen["manual_final_prompt_sha256"], digest)
                        job = self.m.queue[-1]
                        self.assertEqual(job["status"], "render_queued_auto")
                        self.assertEqual(job["context"]["manual_final_prompt"].encode("utf-8"), original)
                        self.assertEqual(job["final_h3_prompt"].encode("utf-8"), original)
                        self.assertEqual(job["context"]["manual_final_prompt_sha256"], digest)
                        persisted = json.loads(self.m.QUEUE_FILE.read_text())["queue"][-1]
                        self.assertEqual(persisted["context"]["manual_final_prompt"].encode("utf-8"), original)
                        self.assertEqual(persisted["final_h3_prompt"].encode("utf-8"), original)
                        self.assertEqual(persisted["context"]["manual_final_prompt_sha256"], digest)
                        path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=text)
                        override = next(node for node in json.loads(path.read_text())["nodes"] if node["id"] == 2632)
                        self.assertEqual(override["widgets_values_named"]["positive"].encode("utf-8"), original)
                worker.assert_not_called()
        self.assertEqual(len(self.m.queue), len(texts))
        self.assertEqual(len(self.m.batches), len(texts))
        self.assertEqual(len(self.m.request_results), len(texts))
        self.assertIsNone(self.m.pick_next_prompt_job())

    async def test_actual_batches_accepts_numeric_clock_prose_exactly(self):
        await self.assert_accepted_exact_requests(NUMERIC_CLOCK_PROSE)

    async def test_actual_batches_accepts_timestamped_backups_exactly(self):
        await self.assert_accepted_exact_requests(BACKUP_FILES)

    async def test_actual_batches_accepts_plus_timestamped_backups_exactly(self):
        await self.assert_accepted_exact_requests(PLUS_BACKUP_FILES)

    async def test_actual_batches_rejects_nonfinite_endpoints_without_publication(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in NONFINITE_INTERVALS:
                    with self.subTest(text=text):
                        response = await client.post("/api/batches", json=self.body_for(text))
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertEqual(self.m.queue, [])
                        self.assertEqual(self.m.batches, [])
                        self.assertEqual(self.m.request_results, {})
                        self.assertFalse(self.m.QUEUE_FILE.exists())
                        worker.assert_not_called()  # Neither Prompt nor Render Worker can POST.

    async def test_actual_batches_rejects_path_hidden_ranges_without_publication(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in PATH_HIDDEN_RANGES:
                    with self.subTest(text=text):
                        response = await client.post("/api/batches", json={**self.body_for(text), "duration_seconds": 30})
                        self.assertEqual(response.status_code, 400, response.text)
                worker.assert_not_called()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertEqual(self.m.request_results, {})
        self.assertFalse(self.m.QUEUE_FILE.exists())

    async def test_actual_batches_rejects_declared_non_clock_values_without_publication(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in DECLARED_NON_CLOCK_VALUES:
                    with self.subTest(text=text):
                        response = await client.post("/api/batches", json=self.body_for(text))
                        self.assertEqual(response.status_code, 400, response.text)
                worker.assert_not_called()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertEqual(self.m.request_results, {})
        self.assertFalse(self.m.QUEUE_FILE.exists())

    async def test_actual_batches_rejects_incomplete_range_before_worker_http(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                response = await client.post("/api/batches", json=self.body_for("Range 00:20.000-00:01."))
                self.assertEqual(response.status_code, 400, response.text)
                worker.assert_not_called()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertEqual(self.m.request_results, {})
        self.assertFalse(self.m.QUEUE_FILE.exists())

    async def test_actual_batches_rejects_complete_incomplete_endpoint_matrix(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in INCOMPLETE_INTERVALS:
                    with self.subTest(text=text):
                        body = {**self.body_for(text), "duration_seconds": 30}
                        response = await client.post("/api/batches", json=body)
                        self.assertEqual(response.status_code, 400, response.text)
                worker.assert_not_called()
        self.assertEqual(self.m.queue, [])
        self.assertEqual(self.m.batches, [])
        self.assertEqual(self.m.request_results, {})
        self.assertFalse(self.m.QUEUE_FILE.exists())

    async def test_actual_batches_preserves_timestamped_files_and_paths_through_render(self):
        texts = tuple("The file is named " + name + "." for name in TIMESTAMP_FILES) + TIMESTAMP_PATHS
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.m.app), base_url="http://panel",
                                    auth=("h3", "test-only-password")) as client:
            with patch.object(self.m.httpx, "AsyncClient", side_effect=AssertionError("Worker HTTP invoked")) as worker:
                for text in texts:
                    with self.subTest(text=text):
                        original = text.encode("utf-8")
                        response = await client.post("/api/batches", json=self.body_for(text))
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(validate_manual_final(text, 20.04).encode("utf-8"), original)
                        frozen = freeze_prompt_source("manual_final", None, text, 20.04)
                        self.assertEqual(frozen["manual_final_prompt"].encode("utf-8"), original)
                        digest = hashlib.sha256(original).hexdigest()
                        self.assertEqual(frozen["manual_final_prompt_sha256"], digest)
                        job = self.m.queue[-1]
                        self.assertEqual(job["status"], "render_queued_auto")
                        self.assertEqual(job["context"]["manual_final_prompt"].encode("utf-8"), original)
                        self.assertEqual(job["final_h3_prompt"].encode("utf-8"), original)
                        self.assertEqual(job["context"]["manual_final_prompt_sha256"], digest)
                        persisted = json.loads(self.m.QUEUE_FILE.read_text())["queue"][-1]
                        self.assertEqual(persisted["context"]["manual_final_prompt"].encode("utf-8"), original)
                        path = self.m.patch_workflow(job, self.m.production_workflow(job), approved_prompt=text)
                        override = next(node for node in json.loads(path.read_text())["nodes"] if node["id"] == 2632)
                        self.assertEqual(override["widgets_values_named"]["positive"].encode("utf-8"), original)
                worker.assert_not_called()
        self.assertEqual(len(self.m.queue), len(texts))
        self.assertIsNone(self.m.pick_next_prompt_job())

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
        self.assertEqual(self.m.request_results, {})
        self.assertFalse(self.m.QUEUE_FILE.exists())

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
