"""Second-pass files use the existing checksum/enrollment gate, on CPU only."""
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

from tests import test_production_controller as fixture
from tests import test_render_feature_recovery as recovery
from h3.scripts.enroll_external_loras import enroll


class SecondPassLoraProvenanceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.ProductionControllerTests.setUp
    batch = fixture.ProductionControllerTests.batch
    job = recovery.RenderFeatureRecoveryTests.job

    def model_health(self):
        # Isolate LoRA-only cases from the full production model bundle.
        # Production metadata is untouched; LoRA verification/enrollment is real.
        artifacts = importlib.import_module(self.m.__package__ + '.artifacts')
        return patch.object(artifacts, 'validate_manifest', return_value={})

    def lora_file(self, identifier):
        path = self.m.COMFY_MODELS_DIR / 'loras' / self.m.LORA_REGISTRY[identifier]['filename']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'Synthetic CPU LoRA bytes, never loaded')
        return path

    async def selected(self, identifier, enabled=True):
        return await self.job({'second_pass_enabled': enabled,
            'second_pass_loras': [{'id': identifier, 'enabled': True, 'strength': .75}]}, profile='10eros_full')

    def enroll(self, identifier):
        enroll(self.m.COMFY_MODELS_DIR, self.m.ROOT / 'config/lora_registry.json', [identifier])

    async def test_off_second_pass_does_not_require_inactive_external_file(self):
        job = await self.selected('movement-v1', enabled=False)
        self.assertEqual(self.m.job_workflow_adapter(job).active_render_loras(job['context']), [])
        with self.model_health():
            await self.m.verify_production_files(job, 'render')

    async def test_on_external_second_lora_requires_explicit_enrollment(self):
        self.lora_file('movement-v1')
        job = await self.selected('movement-v1')
        with self.model_health(), self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')
        self.assertFalse((self.m.COMFY_MODELS_DIR / '.external_loras.json').exists())

    async def test_enrolled_second_lora_accepts_exact_bytes_and_rejects_replacement(self):
        path = self.lora_file('movement-v1')
        self.enroll('movement-v1')
        job = await self.selected('movement-v1')
        with self.model_health():
            await self.m.verify_production_files(job, 'render')
            path.write_bytes(path.read_bytes()[::-1])  # Same size, wrong hash.
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, 'render')

    async def test_second_lora_missing_file_wrong_receipt_or_symlink_fails_closed(self):
        path = self.lora_file('movement-v1')
        body = path.read_bytes()
        self.enroll('movement-v1')
        job = await self.selected('movement-v1')
        receipt_path = self.m.COMFY_MODELS_DIR / '.external_loras.json'
        receipt = json.loads(receipt_path.read_text())
        with self.model_health():
            path.unlink()
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, 'render')
            path.write_bytes(body)
            wrong = json.loads(receipt_path.read_text())
            wrong['artifacts']['movement-v1']['destination'] = 'loras/untrusted.safetensors'
            receipt_path.write_text(json.dumps(wrong))
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, 'render')
            receipt_path.write_text(json.dumps(receipt))
            other = self.m.COMFY_MODELS_DIR / 'other.bin'
            other.write_bytes(body)
            path.unlink(); path.symlink_to(other)
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, 'render')

    async def test_known_source_second_lora_uses_trusted_size_hash_without_external_override(self):
        path = self.lora_file('hm-breasts-v2')
        # Establish synthetic trusted metadata as a new fixture generation
        # before creating any jobs; runtime snapshots are deeply immutable.
        registry = self.m.registry_json(self.m.LORA_REGISTRY)
        metadata = registry['hm-breasts-v2']
        metadata.update(size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.m.LORA_REGISTRY = self.m.immutable_registry(registry)
        job = await self.selected('hm-breasts-v2')
        with self.model_health():
            await self.m.verify_production_files(job, 'render')
            path.write_bytes(path.read_bytes()[::-1])
            self.enroll('hm-breasts-v2')  # A local receipt cannot override a trusted source hash.
            with self.assertRaises(self.m.SubmissionRejected):
                await self.m.verify_production_files(job, 'render')

    async def test_same_registered_file_used_by_both_passes_is_verified_once(self):
        self.lora_file('movement-v1')
        self.enroll('movement-v1')
        self.req = self.req.model_copy(update={'loras': [self.m.LoraSetting(name='movement-v1', enabled=True, strength=.25)]})
        job = await self.selected('movement-v1')
        artifacts = importlib.import_module(self.m.__package__ + '.artifacts')
        with self.model_health(), patch.object(artifacts, 'verify_file', wraps=artifacts.verify_file) as verify:
            await self.m.verify_production_files(job, 'render')
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(verify.call_args.args[1], 'loras/' + self.m.LORA_REGISTRY['movement-v1']['filename'])

    async def test_unresolved_required_artifact_still_fences_second_lora_selection(self):
        manifest = self.m.CONFIG / 'models_manifest.json'
        value = json.loads(manifest.read_text())
        value['pending_artifacts'] = [dict(id='cpu-unresolved-required', required=True,
            profiles=['10eros_full'], provider='generated', destination='LLM/cpu-unresolved.gguf',
            status='blocked_source_metadata', reason='Synthetic unresolved CPU fixture')]
        manifest.write_text(json.dumps(value))
        self.lora_file('movement-v1')
        self.enroll('movement-v1')
        job = await self.selected('movement-v1')
        artifacts = importlib.import_module(self.m.__package__ + '.artifacts')
        health = importlib.import_module(self.m.__package__ + '.model_health')
        # Real manifest validation still rejects unresolved required provenance,
        # even if subsequent health checks would otherwise report healthy files.
        with patch.object(health, 'artifact_health', return_value={'status': 'verified_file'}), \
             patch.object(artifacts, 'verify_file', wraps=artifacts.verify_file) as verify, \
             self.assertRaises(self.m.SubmissionRejected):
            await self.m.verify_production_files(job, 'render')
        verify.assert_not_called()
