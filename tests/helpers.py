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
from unittest.mock import AsyncMock, Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "h3"
BASELINE = ROOT / "migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5"


def production_fixture_context(module, request):
    """Valid durable production identity for scheduler fault fixtures."""
    context=request.model_dump(mode="json")
    profile=request.profile or module.PRODUCTION["default_profile"]
    context.update(profile=profile,model=profile,profile_identity=module.profile_identity(profile),
                   **module.duration_identity(request.duration_seconds))
    return context


def production_fixture_texts(context,label):
    from h3.app.temporal import timecode,milliseconds
    end=timecode(milliseconds(context["effective_duration_seconds"]))
    plan=f"[Shot 1]\nTimeline: 00:00.000-{end}\nAction: {label}."
    final=(f"subject_definitions: Reference 1 retained.\n\nsummary: {label}.\n\n"
           "retention_analysis: All references retained.\n\ndetailed_description:\n"
           f"[Shot 1] {label}, progressing continuously through the final frame.\n\n"
           "overall_soundscape: Natural sounds.\n\nnon_diegetic_music: None.")
    return plan,final


def production_fixture_history(module,prompt_id,label):
    """Actual pinned converter/guard graph with synthetic model output only."""
    import json
    from h3.app.production_api import frozen_duration,temporal_guards
    from h3.app.workflow_conversion import prepare_api_prompt
    from tests.test_production_api import production_catalog
    from tests.test_workflows import exact_convert
    job=next(job for job in module.queue if job.get("prompt_prompt_id")==prompt_id)
    path=module.patch_workflow(job,module.production_workflow(job))
    catalog=production_catalog()
    graph=prepare_api_prompt(exact_convert(json.loads(path.read_text()),catalog),catalog,phase="prompt")
    graph=temporal_guards(frozen_duration(graph,job["context"],catalog),catalog)
    plan,final=production_fixture_texts(job["context"],label)
    titles={"STEP 0 — JoyCaption Visual Facts / Output":"CPU facts", "STEP 1 — Expanded Intent / Output":"CPU intent",
            "STEP 2 — Reference Map / Output":"CPU reference map", "STEP 3 — Creative Director / Output":plan,
            "STEP 4 — Final H3 Prompt / Output":final}
    outputs={key:{"text":[titles[node["_meta"]["title"]]]} for key,node in graph.items() if node.get("_meta",{}).get("title") in titles}
    outputs["5732"]={"text":[final]}
    return {"status":{"status_str":"success","completed":True},"prompt":graph,"outputs":outputs}


@contextmanager
def load_controller(source: Path = ACTIVE, *, production_probes: bool = False, terminal_opt_in: bool = False):
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
            # Existing controller tests explicitly exercise the diagnostic
            # graphs. Normal deployment never enables legacy submissions.
            "H3_ALLOW_DIAGNOSTIC_SUBMISSIONS": "1",
            "WORKSPACE": str(target / "workspace"),
            "H3_PROMPT_PREFETCH": "3", "H3_ENABLE_TERMINAL": "1" if terminal_opt_in else "0",
        }
        try:
            with patch.dict(os.environ, environment):
                name = package_name + ".main"
                spec = importlib.util.spec_from_file_location(name, target / "app/main.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                if not production_probes and source == ACTIVE:
                    # New process-epoch/cache-release I/O is separate from the
                    # accepted durability tests. Production integration tests
                    # opt in and exercise those requests with MockTransport.
                    module.refresh_prompt_epoch = AsyncMock()
                    module.prepare_render_model_boundary = AsyncMock()
                    module.verify_production_files = AsyncMock()
                    module.arm_execution_protocol = AsyncMock()
                    module.quiesce_service = AsyncMock()
                    module.fetch_durable_result = AsyncMock(return_value={})
                # Pure controller tests replace service/task dependencies. Tests
                # of task death explicitly replace these with actual Tasks.
                for task_name in ("prompt_worker_task", "render_worker_task", "recovery_task", "vast_guard_task"):
                    setattr(module, task_name, Mock(done=Mock(return_value=False)))
                yield module
        finally:
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + "."):
                    del sys.modules[name]
