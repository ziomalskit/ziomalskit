"""CPU-only artifact integrity, path fencing and credential boundary probes."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from h3.app.artifacts import (atomic_json, read_json, verify_file, validate_artifact, validate_manifest,
                              recipe_identity, validate_generated_provenance)
from h3.scripts.model_downloads import ensure_download, download_sdk
from h3.scripts.runtime_config import write_runtime
from tests.helpers import load_controller

SENTINEL = "FAKE_HF_SECRET_SENTINEL_FOR_CPU_TEST"
BODY = b"GGUF\x03\x00\x00\x00" + b"CPU synthetic data" * 10


def artifact():
    return {"id": "fixture-model", "role": "CPU test model", "provider": "huggingface", "repository": "fixture/model",
            "revision": "1" * 40, "repository_path": "model.gguf", "destination": "LLM/model.gguf",
            "size_bytes": len(BODY), "sha256": hashlib.sha256(BODY).hexdigest(), "required": True,
            "profiles": ["shared"], "gpu_validation": "pending"}


def generated():
    recipe = {"runtime": "llama.cpp", "format": "bf16", "source_repository": "fixture/canonical-bf16",
              "source_revision": "2" * 40, "llama_cpp_revision": "3" * 40, "converter_revision": "3" * 40,
              "converter_sha256": "4" * 64, "output_filename": "writer-BF16.gguf",
              "source_files": [{"path": "model.safetensors", "size_bytes": 200, "sha256": "5" * 64},
                               *[{"path": name, "size_bytes": 100, "sha256": "6" * 64}
                                 for name in ("config.json", "tokenizer.json", "tokenizer_config.json")]]}
    item = {"id": "generated-writer", "role": "Step 3/4", "provider": "generated", "destination": "LLM/writer-BF16.gguf",
            "required": True, "profiles": ["10eros_full"], "gpu_validation": "pending", "generation": recipe}
    receipt = {"schema_version": 1, "recipe_sha256": recipe_identity(recipe),
               **{key: value for key, value in recipe.items() if key != "runtime"},
               "conversion_command": ["python", "/build/llama/convert_hf_to_gguf.py", "/persistent/source", "--outfile",
                                      "/persistent/generated/writer-BF16.gguf", "--outtype", "bf16", "--no-lazy"],
               "size_bytes": len(BODY), "sha256": hashlib.sha256(BODY).hexdigest(),
               "cpu_load_verified": True, "gpu_validation": "pending"}
    return item, receipt


class ArtifactStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def install(self, body=BODY):
        path = self.root / "LLM/model.gguf"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(body)
        return path

    def test_manifest_requires_exact_immutable_provenance(self):
        item = artifact()
        manifest = {"schema_version": 2, "status": "production_pinned_gpu_pending", "artifacts": [item]}
        self.assertEqual(validate_manifest(manifest), {item["id"]: item})
        for field, value in (("revision", "main"), ("size_bytes", None), ("sha256", ""), ("destination", "../escape"),
                             ("repository", "https://provider/file?token=" + SENTINEL)):
            candidate = copy.deepcopy(item)
            candidate[field] = value
            with self.assertRaises(ValueError):
                validate_artifact(candidate)
        manifest["artifacts"].append(item)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_manifest(manifest)

    def test_exact_file_verification_and_signature(self):
        self.install()
        result = verify_file(self.root, "LLM/model.gguf", artifact())
        self.assertEqual(result["sha256"], artifact()["sha256"])
        self.assertEqual(result["signature"]["size"], len(BODY))

    def test_bad_size_and_hash_fail_closed(self):
        for body, message in ((BODY[:-1], "wrong size"), (b"x" + BODY[1:], "wrong SHA256")):
            self.install(body)
            with self.assertRaisesRegex(ValueError, message):
                verify_file(self.root, "LLM/model.gguf", artifact())

    def test_corrupt_existing_artifact_never_triggers_redownload_or_overwrite(self):
        self.install(b"x" + BODY[1:])
        downloader = Mock()
        with self.assertRaisesRegex(ValueError, "wrong SHA256"):
            ensure_download(self.root, artifact(), downloader=downloader)
        downloader.assert_not_called()
        self.assertEqual((self.root / "LLM/model.gguf").read_bytes(), b"x" + BODY[1:])

    def test_persistent_verified_reuse_requires_no_download_credentials(self):
        self.install()
        with patch.dict(os.environ, {}, clear=True):
            downloader = Mock(side_effect=AssertionError("network must not be called"))
            first = ensure_download(self.root, artifact(), downloader=downloader)
            second = ensure_download(self.root, artifact(), downloader=downloader)
        self.assertTrue(first["reused"] and second["reused"])
        downloader.assert_not_called()
        receipt = read_json(self.root, ".verification/fixture-model.json")
        self.assertEqual(receipt["sha256"], artifact()["sha256"])
        self.assertEqual(receipt["gpu_validation"], "pending")

    def test_resumable_staging_is_verified_before_publication(self):
        def download(item, staging):
            path = staging / item["repository_path"]
            path.write_bytes(BODY)
            return path
        result = ensure_download(self.root, artifact(), downloader=download)
        self.assertFalse(result["reused"])
        self.assertEqual((self.root / "LLM/model.gguf").read_bytes(), BODY)
        self.assertFalse(list((self.root / "LLM").glob("*.partial")))

    def test_bad_download_is_not_published(self):
        def download(item, staging):
            path = staging / item["repository_path"]
            path.write_bytes(b"x" + BODY[1:])
            return path
        with self.assertRaisesRegex(ValueError, "wrong SHA256"):
            ensure_download(self.root, artifact(), downloader=download)
        self.assertFalse((self.root / "LLM/model.gguf").exists())
        self.assertFalse((self.root / ".verification/fixture-model.json").exists())

    def test_sdk_cannot_return_external_path(self):
        external = self.root / "external.gguf"
        external.write_bytes(BODY)
        with self.assertRaises(ValueError):
            ensure_download(self.root, artifact(), downloader=lambda item, staging: external)
        self.assertFalse((self.root / "LLM/model.gguf").exists())

    def test_symlink_file_directory_root_and_fifo_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "model.gguf").write_bytes(BODY)
        link = self.root / "LLM"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            verify_file(self.root, "LLM/model.gguf", artifact())
        link.unlink()
        link.mkdir()
        (link / "model.gguf").symlink_to(outside / "model.gguf")
        with self.assertRaises(OSError):
            verify_file(self.root, "LLM/model.gguf", artifact())
        (link / "model.gguf").unlink()
        os.mkfifo(link / "model.gguf")
        with self.assertRaisesRegex(ValueError, "regular"):
            verify_file(self.root, "LLM/model.gguf", artifact())

    def test_metadata_symlink_cannot_disclose_or_overwrite_external_file(self):
        external = self.root / "external.json"
        external.write_text('{"preserved":true}')
        (self.root / "receipt.json").symlink_to(external)
        with self.assertRaises(OSError):
            read_json(self.root, "receipt.json")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            atomic_json(self.root, "receipt.json", {"changed": True})
        self.assertEqual(json.loads(external.read_text()), {"preserved": True})

    def test_generated_provenance_rejects_stale_source_converter_and_quantization(self):
        item, receipt = generated()
        self.assertEqual(validate_generated_provenance(item, receipt)["sha256"], artifact()["sha256"])
        for field, value in (("source_revision", "9" * 40), ("converter_sha256", "8" * 64),
                             ("source_files", []), ("recipe_sha256", "7" * 64), ("sha256", "")):
            candidate = copy.deepcopy(receipt)
            candidate[field] = value
            with self.assertRaises(ValueError):
                validate_generated_provenance(item, candidate)
        receipt["conversion_command"][6] = "q4_0"
        with self.assertRaisesRegex(ValueError, "BF16"):
            validate_generated_provenance(item, receipt)

    def test_download_sdk_suppresses_token_in_success_output_and_error(self):
        observed = []
        def fake_sdk(**kwargs):
            observed.append(kwargs)
            print(SENTINEL)
            print(SENTINEL, file=__import__("sys").stderr)
            return str(self.root / "model.gguf")
        module = types.ModuleType("huggingface_hub")
        module.hf_hub_download = fake_sdk
        output = io.StringIO()
        with patch.dict(os.environ, {"HF_TOKEN": SENTINEL, "HF_XET_HIGH_PERFORMANCE": "1"}), \
             patch.dict("sys.modules", {"huggingface_hub": module}), redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(download_sdk(artifact(), self.root), self.root / "model.gguf")
            module.hf_hub_download = Mock(side_effect=RuntimeError("download URL?token=" + SENTINEL))
            with self.assertRaises(ValueError) as raised:
                download_sdk(artifact(), self.root)
        self.assertEqual(observed[0]["token"], SENTINEL)
        self.assertEqual(observed[0]["revision"], "1" * 40)
        self.assertNotIn(SENTINEL, output.getvalue() + str(raised.exception))

    def test_fake_hf_token_never_persisted_in_runtime_env_receipts_provenance_or_snapshot(self):
        self.install()
        output = io.StringIO()
        with patch.dict(os.environ, {"HF_TOKEN": SENTINEL, "HF_XET_HIGH_PERFORMANCE": "1"}), redirect_stdout(output), redirect_stderr(output):
            write_runtime(self.root / "runtime.env", dict(os.environ))
            ensure_download(self.root, artifact(), downloader=Mock(side_effect=AssertionError("already present")))
            item, receipt = generated()
            validate_generated_provenance(item, receipt)
            atomic_json(self.root, ".provenance/writer.json", receipt)
            with load_controller() as controller:
                controller.save_state()
                self.assertNotIn(SENTINEL, controller.QUEUE_FILE.read_text())
                self.assertNotIn(SENTINEL, json.dumps(controller.state_diagnostics()))
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(SENTINEL.encode(), path.read_bytes())
        self.assertNotIn(SENTINEL, output.getvalue())
        self.assertNotIn("HF_TOKEN", (self.root / "runtime.env").read_text())

    def test_native_provider_output_cannot_disclose_ephemeral_token(self):
        code = '''
import os, sys, types
from pathlib import Path
from h3.scripts.model_downloads import download_sdk
module=types.ModuleType('huggingface_hub')
def download(**kwargs):
 for descriptor in (1,2): os.write(descriptor,os.environ['HF_TOKEN'].encode()+b'\\n')
 raise RuntimeError(os.environ['HF_TOKEN'])
module.hf_hub_download=download;sys.modules['huggingface_hub']=module
try: download_sdk({'repository':'fixture/model','revision':'1'*40,'repository_path':'model.gguf'},Path(sys.argv[1]))
except ValueError as error: print(str(error))
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root)], cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, "HF_TOKEN": SENTINEL}, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SENTINEL, result.stdout + result.stderr)
        self.assertIn("artifact download failed", result.stdout)
