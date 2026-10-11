"""Deterministic graphs compared with hashes captured before the refactor."""
import copy
import hashlib
import json

from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert
from tests.test_temporal_contract import CANONICAL, FINAL


def graph_digest(document):
    document = copy.deepcopy(document)
    metadata = document.get("extra", {}).get("aj_production")
    if metadata:
        document["extra"]["aj_production"] = {key: metadata[key] for key in
            ("profile", "baseline_sha256", "job_id", "phase") if key in metadata}
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parity_graphs(module):
    m = module
    from h3.app.workflow_adapters import compose
    m.COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    (m.COMFY_INPUT_DIR / "__h3_silence_1s.wav").write_bytes(b"CPU fixture")
    result = {}
    for profile, values in m.PRODUCTION["profiles"].items():
        result[profile + "/composition"] = compose(m.WORKFLOWS / values["render_template"], m.PRODUCTION, m.LORA_REGISTRY)
        for audio in ([], ["sound.wav"]):
            context = {"prompt": "CPU parity scene", "profile": profile, "model": profile,
                "profile_identity": m.profile_identity(profile), "duration_seconds": 20,
                **m.workflow_context(profile), **m.duration_identity(20),
                "pictures": [f"ref{i}.png" for i in range(6)], "audio": audio,
                "loras": m.resolve_loras(m.LORA_REGISTRY, profile, None),
                "conditioning_bridge": {"id": m.CONDITIONING_BRIDGE["id"], "filename": m.CONDITIONING_BRIDGE["filename"],
                    **m.CONDITIONING_BRIDGE["defaults"][profile]}}
            job = {"id": "cpu-parity", "profile": profile, "context": context,
                   "render_seed": 101, "analysis_seed": 201, "prompt_seed": 301, "creative_plan": CANONICAL}
            adapter = m.job_workflow_adapter(job)
            for phase in ("prompt", "render"):
                job["active_service"] = phase
                ui = json.loads(m.patch_workflow(job, m.production_workflow(job),
                    approved_prompt=FINAL if phase == "render" else None).read_text())
                prefix = profile + "/" + ("audio" if audio else "silent") + "/" + phase
                result[prefix + "/ui"] = ui
                catalog = production_catalog()
                graph = adapter.prepare_api_prompt(exact_convert(ui, catalog), catalog, phase=phase,
                                                  approved_prompt=FINAL if phase == "render" else None)
                guarded, _ = adapter.install_runtime_guards(graph, catalog, context, phase=phase)
                adapter.validate_final_graph(guarded, context, phase=phase)
                result[prefix + "/api"] = guarded
                if phase == "prompt":
                    texts = {key: key + " exact saved analysis" for key in ("visual_facts", "expanded_intent", "reference_map")}
                    result[prefix + "/cached-api"] = adapter.install_runtime_guards(graph, catalog, context,
                        phase=phase, analysis_texts=texts)[0]
    return result
