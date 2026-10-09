"""Isolated CPU fixtures; no connection to ComfyUI or Vast.ai."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "h3"
BASELINE = ROOT / "migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5"


@contextmanager
def load_controller(source: Path = ACTIVE):
    """Fresh module, authentic workflow/config files, disposable runtime state."""
    with tempfile.TemporaryDirectory(prefix="aj-test-") as temporary:
        target = Path(temporary) / "h3"
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "runtime.env"))
        package_name = "_aj_fixture_" + uuid.uuid4().hex
        package = types.ModuleType(package_name)
        package.__path__ = [str(target / "app")]
        sys.modules[package_name] = package
        environment = {
            "H3_PANEL_USER": "h3", "H3_PANEL_PASSWORD": "test-only-password",
            "COMFY_ROOT": str(target / "comfy"),
            "COMFY_INPUT_DIR": str(target / "input"),
            "COMFY_OUTPUT_DIR": str(target / "output"),
            "COMFY_MODELS_DIR": str(target / "models"),
            "H3_PERSISTENT_ROOT": "", "H3_PERSISTENCE_MODE": "",
            "VAST_CLI": "/usr/bin/false", "SERVICE_CTL": "/usr/bin/false",
            "RENDER_RESTART_CMD": "/usr/bin/false", "PROMPT_RESTART_CMD": "/usr/bin/false",
            "H3_ALLOW_SUBMISSIONS": "1",
            "WORKSPACE": str(target / "workspace"),
            "H3_PROMPT_PREFETCH": "3", "H3_ENABLE_TERMINAL": "0",
        }
        try:
            with patch.dict(os.environ, environment):
                name = package_name + ".main"
                spec = importlib.util.spec_from_file_location(name, target / "app/main.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                # Pure controller tests replace service/task dependencies. Tests
                # of task death explicitly replace these with actual Tasks.
                for task_name in ("prompt_worker_task", "render_worker_task", "recovery_task", "vast_guard_task"):
                    setattr(module, task_name, Mock(done=Mock(return_value=False)))
                yield module
        finally:
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    del sys.modules[name]
