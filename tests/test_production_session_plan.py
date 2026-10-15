"""CPU-only checks for the production runbook's real selection and copy/reuse."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from h3.app.artifacts import read_json, verify_file
from h3.scripts.model_acceptance import load_selection
from h3.scripts.model_downloads import ensure_download, install_verified, store_lock
from h3.scripts.provision_models import default_lora_destinations, disk_plan
from tests.test_artifact_storage import BODY, artifact


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'h3/config/models_manifest.json'
REGISTRY = ROOT / 'h3/config/lora_registry.json'


class ProductionSessionPlanTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text())
        self.optional = default_lora_destinations(REGISTRY)
        self.items = self.manifest['artifacts']
        self.selected = [item for item in self.items
                         if item['required'] or item['destination'] in self.optional]

    def test_actual_default_and_complete_inventory_accounting(self):
        required = [item for item in self.items if item['required']]
        optional = [item for item in self.selected if not item['required']]
        self.assertEqual((len(self.items), sum(i['size_bytes'] for i in self.items)),
                         (22, 284661829208))
        self.assertEqual((len(required), sum(i['size_bytes'] for i in required)),
                         (17, 277909390584))
        self.assertEqual({i['id']: i['size_bytes'] for i in optional},
                         {'hm-breasts-v2': 77580016, 'bunny-actionlogic-v1': 22045536})
        self.assertEqual((len(self.selected), sum(i['size_bytes'] for i in self.selected)),
                         (19, 278009016136))

    def test_all_eleven_acceptance_sources_match_selected_production_bytes(self):
        native, _ = load_selection()
        self.assertEqual((len(native), sum(i['size_bytes'] for i in native)),
                         (11, 113395969792))
        selected = {i['id']: i for i in self.selected}
        for item in native:
            with self.subTest(artifact=item['id']):
                self.assertEqual(item, selected[item['id']])
        remaining = sum(i['size_bytes'] for i in self.selected) - sum(i['size_bytes'] for i in native)
        self.assertEqual(remaining, 164613046344)
        self.assertEqual(remaining + sum(i['size_bytes'] for i in native), 278009016136)

    def test_cold_and_copied_store_plans_keep_staging_and_existing_space_separate(self):
        native, _ = load_selection()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cold = disk_plan(self.manifest, root, optional_destinations=self.optional)
            complete = disk_plan(self.manifest, root, optional_destinations=None)
            self.assertEqual(cold['known_stack_bytes'], 278009016136)
            self.assertEqual(cold['missing_download_bytes'], 278009016136)
            self.assertEqual(cold['download_staging_bytes'], 66280487368)
            self.assertEqual(cold['minimum_free_bytes'], 344289503504)
            self.assertEqual(complete['known_stack_bytes'], 284661829208)
            self.assertEqual(cold['minimum_free_bytes'] + sum(i['size_bytes'] for i in native),
                             457685473296)
            # A plan observes existence only. These placeholders are not verified weights.
            for item in native:
                path = root / item['destination']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            warm = disk_plan(self.manifest, root, optional_destinations=self.optional)
            self.assertEqual(warm['known_stack_bytes'], cold['known_stack_bytes'])
            self.assertEqual(warm['missing_download_bytes'], 164613046344)
            self.assertEqual(warm['minimum_free_bytes'], 230893533712)
            self.assertIn('10eros-writer', warm['pending_artifacts'])

    def test_real_cli_reports_selected_plan_then_blocks_retained_davidau_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'not-created'
            for flags, expected in (([], 278009016136), (['--include-optional'], 284661829208)):
                with self.subTest(flags=flags):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'h3/scripts/provision_models.py'),
                         '--manifest', str(MANIFEST), '--registry', str(REGISTRY),
                         '--models-root', str(root), '--plan', *flags],
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn('Model provisioning blocked', result.stderr)
                    self.assertEqual(json.loads(result.stdout)['known_stack_bytes'], expected)
                    self.assertFalse(root.exists())

    def test_atomic_copy_preserves_source_and_reuse_rehashes_without_network_or_receipt_copy(self):
        item = artifact()
        with tempfile.TemporaryDirectory() as temporary:
            source, target = (Path(temporary) / name for name in ('isolated', 'production'))
            path = source / item['destination']
            path.parent.mkdir(parents=True)
            path.write_bytes(BODY)
            forbidden = Mock(side_effect=AssertionError('local copy/reuse must not download'))
            ensure_download(source, item, downloader=forbidden)
            source_receipt = read_json(source, '.verification/fixture-model.json')
            with store_lock(target):
                installed = install_verified(target, item, source, path)
            self.assertEqual(installed['sha256'], item['sha256'])
            self.assertFalse((target / '.verification').exists())
            self.assertNotEqual(path.stat().st_ino, (target / item['destination']).stat().st_ino)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual((target / item['destination']).stat().st_nlink, 1)
            result = ensure_download(target, item, downloader=forbidden)
            self.assertTrue(result['reused'])
            target_receipt = read_json(target, '.verification/fixture-model.json')
            self.assertNotEqual(source_receipt['signature'], target_receipt['signature'])
            self.assertEqual(target_receipt['signature'], verify_file(target, item['destination'], item)['signature'])
            self.assertEqual(read_json(source, '.verification/fixture-model.json'), source_receipt)
            self.assertEqual(path.read_bytes(), BODY)
            forbidden.assert_not_called()


if __name__ == '__main__':
    unittest.main()
