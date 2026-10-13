"""Deterministic graphs compared with hashes captured before the refactor."""
import copy
import hashlib
import json

from tests.test_production_api import production_catalog
from tests.test_workflows import exact_convert
from tests.test_temporal_contract import CANONICAL, FINAL


def graph_digest(document, *, previous_llm_routes=False):
    document = copy.deepcopy(document)
    if previous_llm_routes:
        # Keep the received PR #5 hashes intact. Only the explicitly requested
        # model/projector substitutions and new per-LLM gates are normalized.
        # Every other graph byte/edge/setting still has to match that baseline.
        # Exact new routes and gate controls are independently asserted in
        # test_dual_llm_profiles and test_gpu_phase_gate.
        replacements = {
            "Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2.Q4_K_M.gguf": "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.f16.gguf",
            "Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2.Q6_K.gguf": "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.f16.gguf",
            "Mistral-Small-3.2-24B-Instruct-2506-Heretic-v1.2-2.mmproj-f16.gguf": "Gemma-4-E4B-IT-ABLITERATED-UNCENSORED-PHILADELPHIA-CLASS.mmproj-f16.gguf",
            "Qwen3-14B-heretic.Q4_K_M.gguf": "Qwen3.5-4B-Heretic-Q8_0.gguf",
            "Qwen3.5-27B-heretic.Q5_K_M.gguf": "Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic-BF16.gguf",
        }
        def normalize(value):
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                value = {key: normalize(item) for key, item in value.items()}
                if value.get("class_type") in {"AJPromptTextProcessor", "AJGatedCompilerTextProcessor"}:
                    value["class_type"] = "LLMTextProcessor" if value["class_type"] == "AJPromptTextProcessor" else "AJCompilerTextProcessor"
                    value["inputs"].pop("aj_headroom_mb")
                    value["inputs"].pop("aj_allow_sampling")
                return value
            return value
        document = normalize(document)
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
                                                  approved_prompt=FINAL if phase == "render" else None, context=context)
                guarded, _ = adapter.install_runtime_guards(graph, catalog, context, phase=phase)
                adapter.validate_final_graph(guarded, context, phase=phase)
                result[prefix + "/api"] = guarded
                if phase == "prompt":
                    texts = {key: key + " exact saved analysis" for key in ("visual_facts", "expanded_intent", "reference_map")}
                    result[prefix + "/cached-api"] = adapter.install_runtime_guards(graph, catalog, context,
                        phase=phase, analysis_texts=texts)[0]
    return result
