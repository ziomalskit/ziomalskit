"""Execute the actual panel script with trusted backend prompt/render capabilities."""
import json
from pathlib import Path
import subprocess
import unittest

from h3.app.prompt_modes import PROMPT_MODES
from h3.app.workflow_adapters import get_adapter
from h3.app.production import read_loras

ROOT = Path(__file__).resolve().parents[1]


class ManualPromptFrontendTests(unittest.TestCase):
    def run_js(self, scenario):
        registry = [{"id": key, **value} for key, value in read_loras(ROOT / "h3/config/lora_registry.json").items()]
        config = {"prompt_modes": PROMPT_MODES, "default_prompt_mode": "autoprompt", "default_profile": "h3_full",
                  "loras": registry, "profiles": [{"id": "h3_full", "label": "H3 Full", "workflow": get_adapter("h3_v20").render_capabilities()},
                                                   {"id": "10eros_full", "label": "10Eros Full", "workflow": get_adapter("h3_v20").render_capabilities()}]}
        result = subprocess.run(["node", str(ROOT / "tests/fixtures/manual_prompt_frontend.js"),
                                 str(ROOT / "h3/static/index.html"), json.dumps(config), scenario],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


for scenario in ("selector", "autoprompt", "manual_body", "retry", "pending_switch", "during_upload",
                 "empty", "unknown", "preview", "review", "escaping"):
    setattr(ManualPromptFrontendTests, "test_" + scenario, lambda self, scenario=scenario: self.run_js(scenario))
