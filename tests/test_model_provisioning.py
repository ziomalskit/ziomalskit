"""Tiny synthetic conversion, enrollment and read-only health contracts."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from h3.app.artifacts import atomic_json, read_json, verify_file
from h3.app.model_health import artifact_health, manifest_health, verify_bridge_files
from h3.scripts import build_generated_models as build
from h3.scripts.enroll_external_loras import enroll
from h3.scripts.install_owned_nodes import install
from h3.scripts.model_downloads import ensure_download
from h3.scripts.provision_models import disk_plan, provision
from tests.test_artifact_storage import artifact, generated, BODY, SENTINEL

ROOT = Path(__file__).resolve().parents[1] / "h3"


class ModelProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.item, _ = generated()
        self.sources = {
            "model.safetensors": b"synthetic BF16 shard; never loaded",
            "config.json": json.dumps({"architectures": ["Qwen3_5ForConditionalGeneration"], "dtype": "bfloat16"}).encode(),
            "tokenizer.json": b'{"model": {}}', "tokenizer_config.json": b'{"tokenizer_class": "CPUFixture"}',
        }
        self.freeze_sources()
        self.toolchain = self.root / "toolchain"
        self.commands = []

    def freeze_sources(self):
        self.item["generation"]["source_files"] = [{"path": name, "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                                                 for name, body in self.sources.items()]

    def download_source(self, root, item):
        path = root / item["destination"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.sources[item["repository_path"]])
        return verify_file(root, item["destination"], item)

    def runner(self, command):
        self.commands.append(command)
        if "--outfile" in command:
            Path(command[command.index("--outfile") + 1]).write_bytes(BODY)
        else:
            self.assertIn("--gpu-layers", command)
            self.assertEqual(command[command.index("--gpu-layers") + 1], "0")
            self.assertEqual(command[command.index("--n-predict") + 1], "0")

    def build(self, **options):
        return build.ensure_generated(self.root, self.item, self.toolchain, downloader=options.pop("downloader", self.download_source),
                                      runner=options.pop("runner", self.runner), toolchain_check=options.pop("toolchain_check", Mock()), **options)

    def test_bf16_conversion_records_all_source_tool_command_and_output_provenance(self):
        result = self.build()
        self.assertFalse(result["reused"])
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(self.commands[0][-3:], ["--outtype", "bf16", "--no-lazy"])
        receipt = read_json(self.root, ".provenance/generated-writer.json")
        self.assertEqual(receipt["source_files"], self.item["generation"]["source_files"])
        self.assertEqual(receipt["llama_cpp_revision"], self.item["generation"]["llama_cpp_revision"])
        self.assertEqual(receipt["converter_revision"], receipt["llama_cpp_revision"])
        self.assertTrue(receipt["cpu_load_verified"])
        self.assertEqual(receipt["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(receipt["gpu_validation"], "pending")
        self.assertFalse((self.root / ".build/generated-writer/writer-BF16.gguf").exists())
        self.assertEqual(artifact_health(self.root, self.item)["status"], "verified_file")

    def test_verified_generated_reuse_needs_no_source_toolchain_or_token(self):
        self.build()
        source_root = self.root / ".sources"
        __import__("shutil").rmtree(source_root)
        forbidden = Mock(side_effect=AssertionError("verified reuse must not build/download"))
        with patch.dict(os.environ, {}, clear=True):
            result = self.build(downloader=forbidden, runner=forbidden, toolchain_check=forbidden)
        self.assertTrue(result["reused"])
        forbidden.assert_not_called()

    def test_conversion_publication_crashes_resume_both_sides_without_rebuilding(self):
        for boundary in ("before_output", "before_receipt"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                real_json = build.atomic_json
                def write(root, path, receipt):
                    if path.startswith(".provenance/"):
                        raise OSError("synthetic power loss")
                    real_json(root, path, receipt)
                fault = patch.object(build, "install_verified", side_effect=OSError("synthetic power loss")) if boundary == "before_output" else patch.object(build, "atomic_json", side_effect=write)
                with fault, self.assertRaises(OSError):
                    self.build()
                self.assertFalse((self.root / ".provenance/generated-writer.json").exists())
                forbidden = Mock(side_effect=AssertionError("completed journal must resume"))
                result = self.build(downloader=forbidden, runner=forbidden, toolchain_check=forbidden)
                self.assertTrue(result["reused"])
                forbidden.assert_not_called()
                self.assertEqual((self.root / self.item["destination"]).read_bytes(), BODY)

    def test_stale_recipe_wrong_architecture_and_non_bf16_cannot_publish(self):
        for config in ({"architectures": ["OtherArchitecture"], "dtype": "bfloat16"},
                       {"architectures": ["Qwen3_5ForCausalLM"], "dtype": "float16"}):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.sources["config.json"] = json.dumps(config).encode()
                self.freeze_sources()
                runner = Mock()
                with self.assertRaises(ValueError):
                    self.build(runner=runner)
                runner.assert_not_called()
                self.assertFalse((self.root / self.item["destination"]).exists())

    def test_generated_bad_hash_and_stale_source_fail_closed_without_rebuild(self):
        self.build()
        output = self.root / self.item["destination"]
        output.write_bytes(b"x" + BODY[1:])
        forbidden = Mock(side_effect=AssertionError("corruption must not silently rebuild"))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.build(runner=forbidden, downloader=forbidden)
        forbidden.assert_not_called()
        output.write_bytes(BODY)
        self.item["generation"]["source_revision"] = "9" * 40
        with self.assertRaisesRegex(ValueError, "stale"):
            self.build(runner=forbidden, downloader=forbidden)

    def test_failed_conversion_partial_file_is_never_a_model(self):
        def fail(command):
            Path(command[command.index("--outfile") + 1]).write_bytes(b"partial")
            raise ValueError("synthetic failure")
        with self.assertRaises(ValueError):
            self.build(runner=fail)
        self.assertFalse((self.root / self.item["destination"]).exists())
        self.assertFalse((self.root / ".provenance/generated-writer.json").exists())
        with self.assertRaisesRegex(ValueError, "partial/stale"):
            self.build()

    def test_conversion_children_never_inherit_download_or_panel_secrets(self):
        with patch.dict(os.environ, {"HF_TOKEN": SENTINEL, "HF_HUB_TOKEN": SENTINEL, "H3_PANEL_PASSWORD": SENTINEL, "CONTAINER_API_KEY": SENTINEL}), \
             patch.object(build.subprocess, "run") as run:
            build.run_private(["python", "converter"])
            environment = run.call_args.kwargs["env"]
            self.assertNotIn(SENTINEL, json.dumps(environment))
            self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
            self.assertEqual(run.call_args.kwargs["stdout"], build.subprocess.DEVNULL)
        with patch.object(build.subprocess, "run", side_effect=RuntimeError(SENTINEL)), self.assertRaises(ValueError) as error:
            build.run_private(["python", "converter"])
        self.assertNotIn(SENTINEL, str(error.exception))

    def test_read_only_health_distinguishes_missing_size_hash_receipt_and_verified(self):
        item = artifact()
        self.assertEqual(artifact_health(self.root, item)["status"], "missing")
        path = self.root / item["destination"]
        path.parent.mkdir()
        path.write_bytes(BODY[:-1])
        self.assertEqual(artifact_health(self.root, item)["status"], "wrong_size")
        path.write_bytes(BODY)
        self.assertEqual(artifact_health(self.root, item)["status"], "hash_verification_required")
        ensure_download(self.root, item)
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        manifest = {"schema_version": 2, "status": "production_pinned_gpu_pending", "artifacts": [item]}
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        result = manifest_health(manifest_path, self.root)
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["files"][0]["status"], "verified_file")
        self.assertEqual(result["gpu_validation"], "pending")
        self.assertTrue(all(p.read_bytes() == body for p, body in before.items()))
        path.write_bytes(b"x" + BODY[1:])
        self.assertEqual(artifact_health(self.root, item)["status"], "hash_verification_required")
        self.assertEqual(artifact_health(self.root, item, full_hash=True)["status"], "wrong_hash_or_provenance")

    def test_disk_plan_accounts_for_source_output_and_download_staging(self):
        manifest = {"schema_version": 2, "status": "production_pinned_gpu_pending", "artifacts": [artifact(), self.item]}
        plan = disk_plan(manifest, self.root)
        self.assertEqual(plan["generated_source_bytes"], sum(len(body) for body in self.sources.values()))
        self.assertGreater(plan["minimum_free_bytes"], len(BODY) * 2 + plan["generated_source_bytes"])
        with patch("h3.scripts.provision_models.shutil.disk_usage", return_value=Mock(free=1)), \
             patch("h3.scripts.provision_models.ensure_download") as download:
            with self.assertRaisesRegex(ValueError, "cannot hold"):
                provision(manifest, self.root, self.toolchain)
            download.assert_not_called()

    def test_external_enrollment_accepts_only_registered_ids_and_rejects_changed_bytes(self):
        path = self.root / "loras/movement_h3_lora_v1_500.safetensors"
        path.parent.mkdir()
        path.write_bytes(BODY)
        registry = ROOT / "config/lora_registry.json"
        for identifiers in (["../file"], ["unknown"], ["movement-v1", "movement-v1"]):
            with self.assertRaises(ValueError):
                enroll(self.root, registry, identifiers)
        result = enroll(self.root, registry, ["movement-v1"])
        self.assertEqual(result["provenance_status"], "external/user-provided")
        metadata = read_json(self.root, ".external_loras.json")["artifacts"]["movement-v1"]
        verify_file(self.root, metadata["destination"], metadata)
        path.write_bytes(b"x" + BODY[1:])
        with self.assertRaisesRegex(ValueError, "SHA256"):
            verify_file(self.root, metadata["destination"], metadata)
        with self.assertRaisesRegex(ValueError, "already enrolled"):
            enroll(self.root, registry, ["movement-v1"])

    def test_external_enrollment_rejects_symlinks_and_invalid_registry_bridge(self):
        (self.root / "loras").mkdir()
        external = self.root / "outside.safetensors"
        external.write_bytes(BODY)
        (self.root / "loras/movement_h3_lora_v1_500.safetensors").symlink_to(external)
        with self.assertRaises(OSError):
            enroll(self.root, ROOT / "config/lora_registry.json", ["movement-v1"])
        registry = json.loads((ROOT / "config/lora_registry.json").read_text())
        registry["conditioning_bridge"]["filename"] = "../outside.safetensors"
        bad = self.root / "registry.json"
        bad.write_text(json.dumps(registry))
        with self.assertRaises(ValueError):
            enroll(self.root, bad, ["bunny-actionlogic-v1"])

    def test_owned_node_install_reuses_updates_and_preserves_unrelated_local_edits(self):
        source, nodes = self.root / "source", self.root / "nodes"
        source.mkdir()
        module = source / "__init__.py"
        module.write_text("VALUE = 1\n")
        install(source, nodes)
        installed = nodes / "aj_production/__init__.py"
        inode = installed.stat().st_ino
        install(source, nodes)
        self.assertEqual(installed.stat().st_ino, inode)
        module.write_text("VALUE = 2\n")
        install(source, nodes)
        self.assertEqual(installed.read_text(), module.read_text())
        installed.write_text("LOCAL = 3\n")
        with self.assertRaisesRegex(ValueError, "local edits"):
            install(source, nodes)
        self.assertEqual(installed.read_text(), "LOCAL = 3\n")

    def test_owned_node_install_rejects_symlink_destination(self):
        source, nodes = self.root / "source", self.root / "nodes"
        source.mkdir()
        (source / "__init__.py").write_text("VALUE = 1\n")
        nodes.symlink_to(source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            install(source, nodes)
        self.assertFalse((source / "aj_production").exists())

    def test_bunny_bundled_copy_cannot_shadow_verified_persistent_bytes(self):
        entry = {"filename": "Bridge.safetensors", "size_bytes": len(BODY), "sha256": hashlib.sha256(BODY).hexdigest()}
        persistent = self.root / "models/semantic_bridge/Bridge.safetensors"
        persistent.parent.mkdir(parents=True)
        persistent.write_bytes(BODY)
        verify_bridge_files(self.root / "models", self.root, entry, entry)
        bundled = self.root / "custom_nodes/BUNNY_H3_Conditioning_Bridge/models/Bridge.safetensors"
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"x" + BODY[1:])
        with self.assertRaisesRegex(ValueError, "SHA256"):
            verify_bridge_files(self.root / "models", self.root, entry, entry)
        bundled.write_bytes(BODY)
        verify_bridge_files(self.root / "models", self.root, entry, entry)
