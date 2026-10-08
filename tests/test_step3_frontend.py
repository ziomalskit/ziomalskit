"""Run actual inline JS with reversed responses and editable DOM state."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class Step3FrontendTests(unittest.TestCase):
    def run_js(self, scenario):
        result = subprocess.run(['node', str(ROOT / 'tests/fixtures/step3_frontend.js'),
                                 str(ROOT / 'h3/static/index.html'), scenario], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

for scenario in ('approval','approval_uncertain','approval_replacement','selection','jobs','vast','guards',
                 'focused','edit_during_save','partial_save','guard_conflict','restart','action_snapshot','jobs_failure'):
    setattr(Step3FrontendTests, 'test_' + scenario, lambda self, scenario=scenario: self.run_js(scenario))
