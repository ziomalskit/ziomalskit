"""Run the actual panel JavaScript for result links and Advanced request races."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductFrontendTests(unittest.TestCase):
    def run_js(self, scenario):
        result = subprocess.run(["node", str(ROOT / "tests/fixtures/product_frontend.js"),
                                 str(ROOT / "h3/static/index.html"), scenario],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


for scenario in ("results", "config", "closed", "diagnostics", "diagnostics_race", "logs", "logs_race", "close_pending",
                 "terminal_disabled", "terminal_connect", "terminal_handshake_race", "terminal_stale_socket", "terminal_failure"):
    setattr(ProductFrontendTests, "test_" + scenario, lambda self, scenario=scenario: self.run_js(scenario))
