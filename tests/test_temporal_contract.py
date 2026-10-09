"""Real v20 timecodes and deterministic guards; no inference or provider I/O."""
import copy
import math
from pathlib import Path
import unittest

from h3.app.temporal import (duration_identity, validate_duration, normalize_plan, canonical_plan,
                             final_timeline, FRAME_EXPRESSION, LENGTH_EXPRESSION)
from h3.app.v20 import all_nodes, verified_baseline, BASELINE_FILE
from h3.app.production_api import frozen_duration, temporal_guards, validate_temporal_graph
from h3.app.writer_policy import stage_routes, validate_writer_graph, validate_stages
from tests.test_production_api import production_catalog
from tests import test_production_api as api_fixture


RAW_NINETY = "SHOT PLAN\n[Shot 1]\nTimeline: 00:00.000-00:30.000\nAction: Maintain the movement.\n\n[Shot 2]\nTimeline: 00:30.000-01:00.000\nAction: Preserve detail.\n\n[Shot 3]\nTimeline: 01:00.000-01:30.000\nAction: Finish naturally.\n"
CANONICAL = RAW_NINETY.replace("00:30.000", "00:06.680").replace("01:00.000", "00:13.360").replace("01:30.000", "00:20.040")
FINAL = ("subject_definitions: Picture 1 retained.\n\nsummary: Continuous progression.\n\n"
         "retention_analysis: Preserve all references.\n\ndetailed_description:\n"
         "[Shot 1] Maintain the movement.\n[Shot 2] At 00:06.680, preserve detail.\n"
         "[Shot 3] At 00:13.360, finish naturally through the final frame.\n\n"
         "overall_soundscape: Natural sounds.\n\nnon_diegetic_music: None.")


class TemporalContractTests(unittest.TestCase):
    def test_python_uses_exact_canonical_v20_expressions_for_representative_durations(self):
        root = Path(__file__).resolve().parents[1] / "h3"
        nodes = {n["id"]: n for n in all_nodes(verified_baseline(root / "workflows" / BASELINE_FILE))}
        frame = nodes[134]["widgets_values_named"]["expression"]
        length = nodes[137]["widgets_values_named"]["expression"]
        self.assertEqual((FRAME_EXPRESSION, LENGTH_EXPRESSION), (frame, length))
        for requested, count, effective in ((1,39,1.62),(5,124,5.16),(8,192,8.0),(15,362,15.08),(20,481,20.04),(30,736,30.66)):
            with self.subTest(requested=requested):
                result = duration_identity(requested)
                self.assertEqual((result["legal_frame_count"], result["effective_duration_seconds"]), (count,effective))
                self.assertEqual(count % 17, 5)
                self.assertEqual(eval(frame, {"max":max,"round":round}, {"a":requested}), count)
                self.assertEqual(eval(length, {"floor":math.floor,"round":round}, {"a":count}), effective)

    def test_reported_ninety_second_plan_is_retimed_to_exact_twenty_point_zero_four(self):
        self.assertEqual(normalize_plan(RAW_NINETY, 20.04), CANONICAL)
        intervals = [(a,b) for a,b,_ in canonical_plan(CANONICAL,20.04)]
        self.assertEqual(intervals, [(0,6680),(6680,13360),(13360,20040)])
        self.assertEqual(normalize_plan(CANONICAL,20.04), CANONICAL)

    def test_normalization_preserves_all_action_prose_and_does_not_add_speed_words(self):
        result = normalize_plan(RAW_NINETY,20.04)
        self.assertEqual([line for line in result.splitlines() if not line.startswith("Timeline:")],
                         [line for line in RAW_NINETY.splitlines() if not line.startswith("Timeline:")])

    def test_short_long_and_small_rounding_mismatch_are_normalized(self):
        for finish in ("00:01.000", "00:20.039", "00:20.041", "01:30.000"):
            plan = f"[Shot 1]\nTimeline: 00:00.000-{finish}\nAction remains exact."
            self.assertEqual(normalize_plan(plan,20.04), "[Shot 1]\nTimeline: 00:00.000-00:20.040\nAction remains exact.")

    def test_gaps_and_overlaps_become_contiguous_relative_allocations(self):
        for begin in ("00:20.000", "00:40.000"):
            plan = RAW_NINETY.replace("Timeline: 00:30.000-01:00.000", f"Timeline: {begin}-01:00.000")
            result = normalize_plan(plan,20.04)
            schedule = canonical_plan(result,20.04)
            self.assertEqual(schedule[-1][1],20040)
            self.assertTrue(all(a[1]==b[0] for a,b in zip(schedule,schedule[1:])))

    def test_integer_remainder_and_minimum_positive_intervals_are_deterministic(self):
        plan = "[Shot 1]\nTimeline: 00:00.000-00:00.001\n[Shot 2]\nTimeline: 00:00.001-00:00.002\n[Shot 3]\nTimeline: 00:00.002-01:30.000"
        for duration in (.003,.004,.007,20.04):
            actual = normalize_plan(plan,duration)
            schedule = canonical_plan(actual,duration)
            self.assertTrue(all(end>start for start,end,_ in schedule))
            self.assertEqual(schedule[-1][1], round(duration*1000))
            self.assertEqual(normalize_plan(plan,duration), actual)
        with self.assertRaises(ValueError):
            normalize_plan(plan,.002)

    def test_malformed_and_impossible_timecodes_fail_closed(self):
        for value in ("00:60.000", "-00:01.000", "00:01.00", "NaN", "Infinity", "0:01.000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_plan(RAW_NINETY.replace("00:30.000",value),20.04)

    def test_zero_and_negative_intervals_fail_closed(self):
        for value in ("00:00.000",):
            with self.assertRaises(ValueError):
                normalize_plan(RAW_NINETY.replace("00:30.000",value),20.04)
        with self.assertRaises(ValueError):
            normalize_plan(RAW_NINETY.replace("Timeline: 00:30.000-01:00.000", "Timeline: 01:00.000-00:30.000"),20.04)

    def test_duplicate_missing_and_nonsequential_shots_fail_closed(self):
        for mutation in (RAW_NINETY.replace("[Shot 2]","[Shot 1]"),RAW_NINETY.replace("[Shot 2]","[Shot 4]"),
                         RAW_NINETY.replace("Timeline: 00:30.000-01:00.000", ""),
                         RAW_NINETY.replace("[Shot 2]","[Shot 02]"),RAW_NINETY.replace("[Shot 2]","[Shot -2]")):
            with self.assertRaises(ValueError):normalize_plan(mutation,20.04)

    def test_duplicate_timeline_and_nonzero_first_start_fail_closed(self):
        for mutation in (RAW_NINETY.replace("Action: Maintain", "Timeline: 00:00.000-00:01.000\nAction: Maintain"),
                         RAW_NINETY.replace("Timeline: 00:00.000", "Timeline: 00:01.000")):
            with self.assertRaises(ValueError):normalize_plan(mutation,20.04)

    def test_one_shot_and_minute_boundary(self):
        plan = "[Shot 1]\nTimeline: 00:00.000-00:01.000\nUnchanged action."
        self.assertIn("00:00.000-01:00.010",normalize_plan(plan,60.01))
        self.assertEqual(final_timeline(FINAL.split("[Shot 2]")[0]+"\noverall_soundscape: Quiet.\nnon_diegetic_music: None.",
                                       normalize_plan(plan,60.01),60.01),
                         FINAL.split("[Shot 2]")[0]+"\noverall_soundscape: Quiet.\nnon_diegetic_music: None.")

    def test_final_correct_timestamps_are_byte_preserved(self):
        self.assertEqual(final_timeline(FINAL,CANONICAL,20.04,canonicalize=True),FINAL)
        self.assertEqual(final_timeline(FINAL,CANONICAL,20.04),FINAL)

    def test_final_wrong_and_over_target_parseable_cuts_are_bound_to_plan(self):
        wrong = FINAL.replace("00:06.680","01:30.000").replace("00:13.360","00:00.001")
        self.assertEqual(final_timeline(wrong,CANONICAL,20.04,canonicalize=True),FINAL)
        with self.assertRaises(ValueError):final_timeline(wrong,CANONICAL,20.04)

    def test_final_extra_missing_duplicate_and_out_of_order_shots_fail_closed(self):
        for wrong in (FINAL.replace("[Shot 3]","[Shot 4]"), FINAL.replace("[Shot 3]","[Shot 2]"),
                      FINAL.replace("[Shot 3] At 00:13.360, finish naturally through the final frame.\n", ""),
                      FINAL.replace("overall_soundscape:","[Shot 4] At 00:19.000, invented action.\noverall_soundscape:")):
            with self.assertRaises(ValueError):final_timeline(wrong,CANONICAL,20.04,canonicalize=True)

    def test_final_first_timestamp_and_malformed_later_timestamp_fail_closed(self):
        for wrong in (FINAL.replace("[Shot 1]","[Shot 1] At 00:00.000,"),FINAL.replace("00:06.680","00:60.000")):
            with self.assertRaises(ValueError):final_timeline(wrong,CANONICAL,20.04,canonicalize=True)

    def test_invalid_effective_durations_and_persisted_frame_mismatch_fail_closed(self):
        for value in (float("nan"),float("inf"),0,-1,20.0401,"20.04"):
            with self.assertRaises(ValueError):normalize_plan(RAW_NINETY,value)
        value = duration_identity(20)
        for change in ({"legal_frame_count":480},{"effective_duration_seconds":20},{"effective_duration_ms":20041}):
            with self.assertRaises(ValueError):validate_duration(dict(value,**change))


class WriterAndTemporalGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):api_fixture.ProductionAPITests.setUpClass()
    def graph(self,profile="h3_full"):
        fixture=api_fixture.ProductionAPITests()
        return temporal_guards(frozen_duration(fixture.convert(profile,"prompt"),duration_identity(20),fixture.catalog),fixture.catalog)

    def test_guarded_plan_is_the_only_step_four_path_and_both_captures_are_guarded(self):
        for profile in ("h3_full","10eros_full"):
            graph=self.graph(profile)
            validate_temporal_graph(graph,duration_identity(20))
            validate_writer_graph(graph,stage_routes(api_fixture.ProductionAPITests.profiles["profiles"][profile]))
            self.assertNotIn("1731:184:134",graph)
            self.assertEqual(graph["7453"]["inputs"]["values.b"],["aj_frozen_duration",1])

    def test_reasoning_or_performance_port_cannot_feed_a_final_capture(self):
        for port in (1,2):
            graph=self.graph()
            graph["aj_final_timeline"]["inputs"]["final_h3_prompt"]=["2551:2640:4275",port]
            with self.assertRaisesRegex(ValueError,"reasoning/performance"):
                validate_writer_graph(graph,stage_routes(api_fixture.ProductionAPITests.profiles["profiles"]["h3_full"]))

    def test_missing_guard_or_frozen_duration_drift_fails_closed(self):
        graph=self.graph();graph["aj_frozen_duration"]["inputs"]["effective_duration_seconds"]=90.0
        with self.assertRaises(ValueError):validate_temporal_graph(graph,duration_identity(20))
        graph=self.graph();graph.pop("aj_creative_timeline")
        with self.assertRaises(ValueError):validate_temporal_graph(graph,duration_identity(20))

    def test_stage_specific_native_budget_reserves_final_answer_and_input_allowance(self):
        for profile in api_fixture.ProductionAPITests.profiles["profiles"].values():
            routes=stage_routes(profile)
            self.assertEqual(routes["step3"]["reasoning"],"off")
            self.assertEqual(routes["step4"]["reasoning"],"on")
            self.assertEqual(routes["step3"]["model"],routes["step4"]["model"])
            self.assertEqual(routes["step4"]["max_tokens"]-routes["step4"]["reasoning_budget"]-routes["step4"]["framing_tokens"],8192)
            self.assertGreaterEqual(routes["step4"]["ctx_size"]-routes["step4"]["max_tokens"],16384)

    def test_starved_or_unbounded_reasoning_configuration_is_rejected(self):
        stages=api_fixture.ProductionAPITests.profiles["profiles"]["h3_full"]["writer_stages"]
        for change in ({"reasoning_budget":-1},{"max_tokens":8192},{"ctx_size":24576},{"reasoning":"off"}):
            candidate=copy.deepcopy(stages);candidate["step4"].update(change)
            with self.assertRaises(ValueError):validate_stages(candidate)
