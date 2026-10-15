"""Current writer installation is independent of unperformed GPU acceptance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from h3.app.artifacts import validate_manifest
from h3.app.temporal import duration_identity
from h3.app.workflow_adapters import get_adapter
from h3.scripts import provision_models, model_downloads, model_acceptance
from tests import test_production_api as api
from tests.test_artifact_storage import BODY, artifact

ROOT = Path(__file__).resolve().parents[1] / 'h3'


class CurrentWriterManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT/'config/models_manifest.json').read_text())

    def test_active_27b_source_is_exactly_pinned_while_gpu_is_pending(self):
        entries = validate_manifest(self.manifest)
        self.assertNotIn('10eros-writer', entries)
        self.assertEqual(self.manifest['pending_artifacts'], [])
        self.assertEqual(len(entries), 22)
        current = entries['10eros-writer-replacement']
        self.assertEqual((current['repository'], current['revision'], current['repository_path'],
                          current['destination'], current['size_bytes'], current['sha256']), (
            'mradermacher/Qwen3.5-27B-heretic-GGUF', '81ceaf141911d6ad2e0fbd008e5c043ebdd33fc5',
            'Qwen3.5-27B-heretic.Q5_K_M.gguf', 'LLM/Qwen3.5-27B-heretic.Q5_K_M.gguf',
            19399608384, '66787dfbf5462ac355457efaf24e503d244f1083945243ba029080a8e6276322'))
        self.assertEqual(current['gpu_validation'], 'pending')
        selected, _ = model_acceptance.load_selection()
        self.assertIn(current, selected)

    def test_real_provisioner_prepares_all_default_files_without_gpu_evidence(self):
        manifest = copy.deepcopy(self.manifest)
        for item in manifest['artifacts']:
            item.update(size_bytes=len(BODY), sha256=artifact()['sha256'])
        def fixture_download(root, item):
            def receive(item, staging):
                path = staging/'cpu-payload'; path.write_bytes(BODY); return path
            return model_downloads.ensure_download(root, item, downloader=receive)
        optional = provision_models.default_lora_destinations(ROOT/'config/lora_registry.json')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'models'
            with patch.object(provision_models, 'ensure_download', side_effect=fixture_download) as downloads:
                result = provision_models.provision(manifest, root, Path(temporary)/'unused-toolchain',
                                                     optional_destinations=optional)
            self.assertEqual(len(result), 19)
            self.assertEqual(downloads.call_count, 19)
            self.assertTrue(all(row['gpu_validation'] == 'pending' for row in result))
            self.assertTrue((root/'LLM/Qwen3.5-27B-heretic.Q5_K_M.gguf').is_file())
            self.assertFalse(any('DavidAU' in str(p) or 'F451' in str(p) for p in root.rglob('*')))

    def test_unknown_pending_still_blocks_before_any_download(self):
        self.manifest['pending_artifacts'] = [{'id': 'unknown-required', 'required': True}]
        with tempfile.TemporaryDirectory() as temporary, patch.object(provision_models, 'ensure_download') as downloads:
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                provision_models.provision(self.manifest, Path(temporary), Path(temporary))
            downloads.assert_not_called()

    def test_both_real_prepared_writer_stages_bind_27b_and_reject_old_9b(self):
        api.ProductionAPITests.setUpClass()
        fixture = api.ProductionAPITests()
        adapter = get_adapter('h3_v20')
        policy = fixture.profiles['profiles']['10eros_full']
        context = {**duration_identity(20), 'profile_identity': policy}
        graph, _ = adapter.install_runtime_guards(fixture.convert('10eros_full', 'prompt'),
            fixture.catalog, context, phase='prompt')
        adapter.validate_final_graph(graph, context, phase='prompt')
        for stage, identifier in adapter.descriptor['api']['writer_stages'].items():
            self.assertEqual(graph[identifier]['inputs']['model'], 'Qwen3.5-27B-heretic.Q5_K_M.gguf')
            damaged = copy.deepcopy(graph)
            damaged[identifier]['inputs']['model'] = 'Qwen3.5-9B-F451-AND-TRI-Polar-Ultra-Pro-Writer-Uncensored-Heretic-BF16.gguf'
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                adapter.validate_final_graph(damaged, context, phase='prompt')
