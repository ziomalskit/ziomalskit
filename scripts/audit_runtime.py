"""Prove the old probes still detect RC5 defects, then test the active fixes."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = ROOT / "migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5"
EXPECTED = {
    "missing_subprocess_import", "dispatch_during_shutdown",
    "recovery_accepts_failed_render", "recovery_requeues_on_unknown_remote_state",
    "dispatch_before_recovery_reconciliation", "guard_dies_after_failed_lifecycle_action",
    "non_ascii_basic_auth_raises",
}


def main() -> None:
    output = ROOT / ".local/audit"
    output.mkdir(parents=True, exist_ok=True)
    baseline_result = output / "baseline.json"
    completed = subprocess.run([
        sys.executable, str(ROOT / "scripts/panel_regression_probe.py"), str(BASELINE),
        "--output", str(baseline_result),
    ], cwd=ROOT, capture_output=True, text=True, check=True)
    baseline = json.loads(baseline_result.read_text())
    detected = {item["id"] for item in baseline["findings"] if item["status"] == "confirmed"}
    if detected != EXPECTED:
        raise RuntimeError("Baseline probes stopped detecting the original seven defects")
    print("BASELINE: all 7 original defects still reproduced; baseline files untouched", flush=True)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {
        "baseline_defects_reproduced": sorted(detected),
        "active_cpu_regression_tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped),
        "gpu_acceptance": "not_run",
        "real_comfyui_submissions": 0, "vast_actions": 0,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if not result.wasSuccessful() or result.testsRun == 0 or result.skipped:
        raise SystemExit(1)
    print(f"ACTIVE CPU AUDIT PASS: {result.testsRun} regression tests; no skipped tests. GPU acceptance remains unverified.")


if __name__ == "__main__":
    main()
