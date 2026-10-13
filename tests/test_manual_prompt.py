"""Plain-text manual validation never compiles or synthesizes a creative plan."""
import copy
import hashlib
import unittest

from h3.app.prompt_modes import (freeze_prompt_source, validate_prompt_source, validate_manual_final,
                                  validate_render_prompt)
from h3.app.temporal import duration_identity, final_timeline
from tests.test_temporal_contract import CANONICAL, FINAL

MANUAL = "  <Picture 1> walks along a quiet shore.\nKeep natural motion and all six references intact.  \n"
SHOTS = ("[Shot 1] Continuous movement.\n[Shot 2] At 00:06.680, change the camera angle.\n"
         "[Shot 3] At 00:13.360, finish naturally.")


class ManualFinalValidationTests(unittest.TestCase):
    def test_free_form_is_valid_without_six_sections(self):
        self.assertEqual(validate_manual_final(MANUAL, 20.04), MANUAL)

    def test_aspect_ratios_are_plain_prose_not_clock_timestamps(self):
        text = "<Picture 1> moves naturally in a 16:9 composition, keeping the 2:3 reference crop."
        self.assertEqual(validate_manual_final(text, 20.04), text)

    def test_six_section_final_prompt_is_also_valid_without_a_plan(self):
        self.assertEqual(validate_manual_final(FINAL, 20.04), FINAL)

    def test_canonical_explicit_cuts_are_preserved_exactly(self):
        self.assertEqual(validate_manual_final(SHOTS, 20.04), SHOTS)

    def test_empty_and_whitespace_only_fail(self):
        for text in (None, "", " \n\t", 42, True):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_shots_are_sequential_and_structural(self):
        for text in (SHOTS.replace("[Shot 2]", "[Shot 4]"), SHOTS.replace("[Shot 2]", "[Shot 1]"),
                     SHOTS.replace("[Shot 2]", "[Shot 02]"), SHOTS.replace("\n[Shot 2]", " inline [Shot 2]")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_first_shot_has_no_timestamp(self):
        with self.assertRaisesRegex(ValueError, "Shot 1"):
            validate_manual_final(SHOTS.replace("[Shot 1]", "[Shot 1] At 00:00.000,"), 20.04)

    def test_cut_must_be_strictly_inside_effective_duration(self):
        for stamp in ("00:00.000", "00:20.040", "00:20.041", "01:30.000"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_manual_final(SHOTS.replace("00:06.680", stamp), 20.04)

    def test_cut_order_is_strict_and_not_retimed(self):
        for stamp in ("00:06.680", "00:01.000"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_manual_final(SHOTS.replace("00:13.360", stamp), 20.04)

    def test_missing_malformed_and_extra_cut_fail_closed(self):
        for text in (SHOTS.replace(" At 00:06.680,", ""), SHOTS.replace("00:06.680", "0:06.680"),
                     SHOTS + "\nLater At 00:18.000, an extra cut.", SHOTS.replace("At 00:06.680,", "at 00:06.680,")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_unquoted_clock_timestamps_are_canonical_and_bounded(self):
        for stamp in ("00:20.041", "00:60.000", "00:06.68", "0:06.680", "-00:06.680", "+00:06.680",
                      "00:-06.680", "00:NaN", "00:Infinity", "00:00:30.000", "00:06.680:01", "00:06"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_manual_final("The clock reads " + stamp + ".", 20.04)
        self.assertEqual(validate_manual_final("The clock reads 00:20.040.", 20.04), "The clock reads 00:20.040.")

    def test_explicit_intervals_are_positive_bounded_and_preserved(self):
        text = "[Shot 1] Maintain movement.\nTimeline: 00:00.000-00:20.040\n"
        self.assertEqual(validate_manual_final(text, 20.04), text)
        self.assertEqual(validate_manual_final("Range 00:00.000-00:20.040.", 20.04), "Range 00:00.000-00:20.040.")
        crlf = text.replace("-", " - ").replace("\n", "\r\n")
        self.assertEqual(validate_manual_final(crlf, 20.04), crlf)
        for wrong in (text.replace("00:20.040", "00:20.041"), text.replace("00:20.040", "00:00.000"),
                      text.replace("00:00.000-", "00:21.000-"), text.replace("00:00.000-00:20.040", "0-20s"),
                      text + "Timeline: 00:19.000-00:20.000", "Range 00:20.000 - 00:01.000."):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                validate_manual_final(wrong, 20.04)

    def test_quoted_references_follow_existing_temporal_masking_rules(self):
        text = MANUAL + 'A sign reads "At 01:30.000, [Shot 8]".\n'
        self.assertEqual(validate_manual_final(text, 20.04), text)

    def test_control_characters_and_reasoning_boundaries_fail(self):
        for text in (MANUAL + "\0", MANUAL + "\x01", "<think>\nDraft.\n</think>\n" + MANUAL, "[Start thinking]\n" + MANUAL):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_manual_final(text, 20.04)

    def test_unicode_whitespace_crlf_and_template_like_text_remain_plain_text(self):
        text = "  żółć 🎬\r\n<Picture 1> {$value|other} $(never_execute) `literal` <script>alert(1)</script>  \r\n"
        self.assertEqual(validate_manual_final(text, 20.04), text)

    def test_frozen_manual_identity_is_utf8_exact_and_detects_drift(self):
        source = {**duration_identity(20), **freeze_prompt_source("manual_final", None, MANUAL, 20.04)}
        self.assertEqual(source["manual_final_prompt_sha256"], hashlib.sha256(MANUAL.encode()).hexdigest())
        validate_prompt_source(source)
        for change in ({"manual_final_prompt": MANUAL.strip()}, {"manual_final_prompt_sha256": "0" * 64},
                       {"prompt_mode": "autoprompt"}, {"prompt": "a scene"}, {"workflow_prompt_identity": "0" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_prompt_source(dict(source, **change))

    def test_duration_is_still_481_frames_and_20_point_040_seconds(self):
        identity = duration_identity(20)
        self.assertEqual(identity["legal_frame_count"], 481)
        self.assertEqual(identity["effective_duration_seconds"], 20.040)
        self.assertEqual(identity["effective_duration_ms"], 20040)

    def test_manual_validation_does_not_weaken_autoprompt(self):
        self.assertEqual(final_timeline(FINAL, CANONICAL, 20.04), FINAL)
        for text, plan in ((MANUAL, CANONICAL), (FINAL, None), (SHOTS, CANONICAL)):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_render_prompt({"context": duration_identity(20), "creative_plan": plan}, text)

    def test_manual_validation_never_creates_a_plan(self):
        context = {**duration_identity(20), **freeze_prompt_source("manual_final", None, MANUAL, 20.04)}
        before = copy.deepcopy(context)
        validate_prompt_source(context)
        self.assertEqual(context, before)
        self.assertNotIn("creative_plan", context)

    def test_invalid_effective_duration_fails(self):
        for value in (0, -1, float("nan"), float("inf"), "20.04", 20.0401, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_manual_final(MANUAL, value)
