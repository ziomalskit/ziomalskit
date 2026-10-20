"""Dependency drift regressions: local wheels only, no GPU or remote index."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from h3.scripts import runtime_dependencies as pins

ROOT = Path(__file__).resolve().parents[1]


class RuntimeDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aj dependency pins ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for source in (ROOT / "h3").glob("*constraints*.txt"):
            shutil.copy2(source, self.root / source.name)

    def wheel(self, name, version):
        info = f"{name}-{version}.dist-info"
        with zipfile.ZipFile(self.root / f"{name}-{version}-py3-none-any.whl", "w") as wheel:
            wheel.writestr(info + "/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
            wheel.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: AJ CPU fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            wheel.writestr(info + "/RECORD", "")

    def resolve(self, *requirements, cuda="13.0"):
        environment = dict(os.environ, PIP_CONSTRAINT=pins.constraint_file(self.root, cuda).as_uri(),
                           PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        return subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
                               "--no-deps", "--find-links", str(self.root), "--report", str(self.root / "report.json"),
                               *requirements], env=environment, text=True, capture_output=True, timeout=30)

    def test_future_versions_and_cpu_torch_do_not_change_a_fresh_resolution(self):
        self.wheel("numpy", "1.26.4")
        self.wheel("torch", "2.11.0+cu130")
        for _ in range(2):
            result = self.resolve("numpy", "torch")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((self.root / "report.json").read_text())
            self.assertEqual({item["metadata"]["name"]: item["metadata"]["version"] for item in report["install"]},
                             {"numpy": "1.26.4", "torch": "2.11.0+cu130"})
            # A later index snapshot also offers newer NumPy and CPU/other CUDA builds.
            self.wheel("numpy", "9.0.0")
            self.wheel("torch", "2.11.0+cpu")
            self.wheel("torch", "2.11.0+cu128")
        mismatch = self.resolve("numpy>=2")
        self.assertNotEqual(mismatch.returncode, 0, "an incompatible node must not silently upgrade NumPy")
        (self.root / "torch-2.11.0+cu130-py3-none-any.whl").unlink()
        missing = self.resolve("torch")
        self.assertNotEqual(missing.returncode, 0, "a missing CUDA wheel must not fall back to CPU")
        selected = self.resolve("torch", cuda="12.8")
        self.assertEqual(selected.returncode, 0, selected.stderr)

    def test_verification_rejects_missing_packages_and_wrong_versions(self):
        expected = pins.pinned_versions(pins.constraint_file(self.root, "13.0"))
        for value in ("unexpected", importlib.metadata.PackageNotFoundError("numpy")):
            with self.subTest(value=value), patch.object(pins.importlib.metadata, "version") as version:
                version.side_effect = value if isinstance(value, Exception) else lambda name: value
                with self.assertRaises((ValueError, importlib.metadata.PackageNotFoundError)):
                    pins.verify(self.root, self.root / "ComfyUI", "13.0")
        self.assertIn("transformers", expected)

    def test_preflight_source_verification_rejects_a_moved_or_missing_node(self):
        expected = pins.pinned_versions(pins.constraint_file(self.root, "13.0"))
        (self.root / "config").mkdir()
        shutil.copy2(ROOT / "h3/config/custom_nodes_manifest.json", self.root / "config/custom_nodes_manifest.json")
        manifest = json.loads((self.root / "config/custom_nodes_manifest.json").read_text())
        first = manifest["workflow_git"][0]
        with patch.object(pins.importlib.metadata, "version", side_effect=expected.__getitem__), \
             patch.object(pins.subprocess, "check_output", return_value="wrong revision\n"):
            with self.assertRaisesRegex(ValueError, first["folder"]):
                pins.verify(self.root, self.root / "ComfyUI", "13.0")
        with patch.object(pins.importlib.metadata, "version", side_effect=expected.__getitem__), \
             patch.object(pins.subprocess, "check_output", side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                pins.verify(self.root, self.root / "ComfyUI", "13.0")


if __name__ == "__main__":
    unittest.main()
