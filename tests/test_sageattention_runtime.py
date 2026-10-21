"""Offline installation transactions; no CUDA build, GPU or model download."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from h3.scripts import sageattention_runtime as sage


class SageInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aj-sage-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.receipt = self.root / "receipt.json"
        self.versions = dict(torch="2.11.0+cu130", torchvision="0.26.0+cu130",
                             torchaudio="2.11.0+cu130", **{"nvidia-cuda-runtime-cu13": "13.0.96"},
                             setuptools="80.0", wheel="0.45", packaging="25.0")
        self.identity = dict(revision=sage.REVISION, torch=self.versions["torch"], arch="12.0")
        self.commands = []
        self.failure = None
        for target, replacement in (("packages", lambda: dict(self.versions)),
                                     ("runtime_identity", lambda cuda: dict(self.identity)),
                                     ("receipt_path", lambda: self.receipt),
                                     ("installed_files", lambda: {"_fused.so": "digest"}),
                                     ("checked_source", lambda path: None),
                                     ("smoke_command", lambda comfy: None),
                                     ("run", self.command)):
            mock = patch.object(sage, target, side_effect=replacement).start()
            setattr(self, target + "_mock", mock)
            self.addCleanup(patch.stopall)

    def command(self, args, **kwargs):
        self.commands.append((args, kwargs))
        if args[:2] == ["git", "init"]:
            Path(args[-1]).mkdir()
        if "wheel" in args and "--wheel-dir" in args:
            if self.failure:
                raise self.failure
            wheels = Path(args[args.index("--wheel-dir") + 1])
            wheels.mkdir()
            (wheels / "sageattention-2.2.0-cp312-cp312-linux_x86_64.whl").write_bytes(b"fixture")
        if "install" in args and args[-1].endswith(".whl"):
            self.versions["sageattention"] = "2.2.0"
        elif "install" in args:
            name, version = args[-1].split("==")
            self.versions[name] = version

    def install(self):
        sage.install(self.root / "ComfyUI", self.root / "build", "13.0")

    def test_fresh_pinned_source_build_preserves_existing_dependencies_and_reuses(self):
        original = dict(self.versions)
        with patch.dict(os.environ, {"SAGEATTN_SKIP_CUDA_BUILD": "1", "EXT_PARALLEL": "99",
                                     "NVCC_APPEND_FLAGS": "bad", "TORCH_CUDA_ARCH_LIST": "8.0"}):
            self.install()
        wheel, options = next((a, kw) for a, kw in self.commands if "--wheel-dir" in a)
        self.assertIn("--no-deps", wheel)
        self.assertIn("--no-build-isolation", wheel)
        self.assertIn("--no-cache-dir", wheel)
        self.assertEqual(options["env"]["TORCH_CUDA_ARCH_LIST"], "12.0")
        self.assertEqual(options["env"]["EXT_PARALLEL"], "1")
        self.assertEqual(options["env"]["SAGEATTN_SKIP_CUDA_BUILD"], "0")
        self.assertLessEqual(int(options["env"]["MAX_JOBS"]), 2)
        self.assertNotIn("NVCC_APPEND_FLAGS", options["env"])
        fetch = next(a for a, _ in self.commands if "fetch" in a)
        self.assertEqual(fetch[-1], sage.REVISION)
        self.assertIn(sage.SOURCE, next(a for a, _ in self.commands if "remote" in a))
        self.checked_source_mock.assert_called_once()
        self.assertTrue(self.receipt.is_file())
        self.assertTrue(all(self.versions[name] == version for name, version in original.items()))
        self.assertFalse(any((self.root / "build").iterdir()))
        self.commands.clear()
        self.install()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.smoke_command_mock.call_count, 2)

    def test_only_missing_build_helpers_are_installed_without_dependencies(self):
        del self.versions["wheel"]
        self.install()
        helpers = [a for a, _ in self.commands if "install" in a and not a[-1].endswith(".whl")]
        self.assertEqual(len(helpers), 1)
        self.assertEqual(helpers[0][-1], "wheel==0.43.0")
        self.assertIn("--no-deps", helpers[0])

    def test_unowned_or_interrupted_install_cannot_be_reused(self):
        self.versions["sageattention"] = "2.2.0"
        with self.assertRaisesRegex(ValueError, "Unowned/incomplete"):
            self.install()
        self.assertEqual(self.commands, [])

    def test_changed_source_abi_or_installed_files_rejected_without_reinstall(self):
        self.install()
        self.commands.clear()
        for key in ("revision", "torch", "arch"):
            with self.subTest(key=key), patch.dict(self.identity, {key: "unexpected"}):
                with self.assertRaisesRegex(ValueError, "differs"):
                    self.install()
        self.installed_files_mock.side_effect = lambda: {"_fused.so": "changed"}
        with self.assertRaisesRegex(ValueError, "differs"):
            self.install()
        self.assertEqual(self.commands, [])

    def test_build_failure_and_interruption_leave_no_receipt_and_allow_retry(self):
        for error in (subprocess.CalledProcessError(1, ["build"]), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.failure = error
                with self.assertRaises(type(error)):
                    self.install()
                self.assertFalse(self.receipt.exists())
                self.assertFalse(any((self.root / "build").iterdir()))
        self.failure = None
        self.install()
        self.assertTrue(self.receipt.exists())

    def test_gpu_failure_after_install_leaves_unverified_install_blocked(self):
        self.smoke_command_mock.side_effect = ValueError("CUDA kernel mismatch")
        with self.assertRaisesRegex(ValueError, "kernel mismatch"):
            self.install()
        self.assertFalse(self.receipt.exists())
        with self.assertRaisesRegex(ValueError, "Unowned/incomplete"):
            self.install()

    def test_unavailable_pinned_source_stops_without_build_or_receipt(self):
        original = self.run_mock.side_effect
        def missing_pin(args, **kwargs):
            if "fetch" in args:
                raise subprocess.CalledProcessError(128, args)
            return original(args, **kwargs)
        self.run_mock.side_effect = missing_pin
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assertFalse(self.receipt.exists())
        self.assertNotIn("sageattention", self.versions)
        self.assertFalse(any((self.root / "build").iterdir()))

    def test_existing_torch_or_cuda_package_change_is_a_hard_error(self):
        before = dict(self.versions)
        for name in ("torch", "torchvision", "torchaudio", "nvidia-cuda-runtime-cu13", "packaging"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, name):
                    sage.unchanged(before, {**before, name: "newer"})


class SageSourceAndToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aj-sage-source-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def test_source_requires_exact_revision_origin_and_pristine_tree(self):
        self.assertEqual(sage.REVISION, "eb615cf6cf4d221338033340ee2de1c37fbdba4a")
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / "setup.py").write_text("# source fixture\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("remote", "add", "origin", sage.SOURCE)
        with self.assertRaisesRegex(ValueError, "revision mismatch"):
            sage.checked_source(self.root)
        with patch.object(sage, "REVISION", self.git("rev-parse", "HEAD")):
            sage.checked_source(self.root)
            self.git("remote", "set-url", "origin", "https://example.invalid/unapproved")
            with self.assertRaisesRegex(ValueError, "origin mismatch"):
                sage.checked_source(self.root)
            self.git("remote", "set-url", "origin", sage.SOURCE)
            (self.root / "setup.py").write_text("# changed\n")
            with self.assertRaisesRegex(ValueError, "not pristine"):
                sage.checked_source(self.root)

    def test_installed_distribution_checks_compiled_files_version_and_hash(self):
        files = ["sageattention/_qattn_sm89.fixture.so", "sageattention/_fused.fixture.so",
                 "sageattention/core.py", "sageattention-2.2.0.dist-info/METADATA"]
        for name in files:
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"fixture")
        dist = SimpleNamespace(version="2.2.0", files=files, locate_file=lambda name: self.root / name)
        with patch.object(sage.metadata, "distribution", return_value=dist), patch.object(sys, "prefix", str(self.root)):
            before = sage.installed_files()
            (self.root / files[0]).write_bytes(b"changed")
            self.assertNotEqual(before, sage.installed_files())
            dist.version = "2.3.0"
            with self.assertRaisesRegex(ValueError, "version differs"):
                sage.installed_files()
            dist.version = "2.2.0"
            dist.files = files[1:]
            with self.assertRaisesRegex(ValueError, "extensions are missing"):
                sage.installed_files()

    def test_kj_probe_loads_only_the_actual_checked_functions(self):
        path = self.root / "custom_nodes/ComfyUI-KJNodes/nodes/ltxv_nodes.py"
        path.parent.mkdir(parents=True)
        body = b'''raise AssertionError("unrelated module initialization must not run")
_QATTN_PROBE = {"sm89": "symbol"}
def _resolve_qattn(arch): return _QATTN_PROBE[arch]
def _sageattn_int8_fp8_nhd(qkv, dtype): return qkv
def minimax_sageattn_forward(self, x): return _sageattn_int8_fp8_nhd(x, None)
'''
        path.write_bytes(body)
        with patch.object(sage, "output", return_value=sage.KJ_REVISION), patch.object(
                sage, "KJ_SOURCE_SHA256", hashlib.sha256(body).hexdigest()):
            ns = sage.kj_functions(self.root, {})
            self.assertEqual(ns["minimax_sageattn_forward"](None, [1, 2]), [1, 2])
            path.write_bytes(body + b"# upstream/local drift\n")
            with self.assertRaisesRegex(ValueError, "differs"):
                sage.kj_functions(self.root, {})

    def test_runtime_abi_gpu_and_devel_toolchain_are_required(self):
        nvcc = self.root / "bin/nvcc"
        nvcc.parent.mkdir()
        nvcc.touch()
        torch = SimpleNamespace(__version__="2.11.0+cu130", version=SimpleNamespace(cuda="13.0"),
            _C=SimpleNamespace(_GLIBCXX_USE_CXX11_ABI=True),
            cuda=SimpleNamespace(is_available=lambda: True, get_device_capability=lambda n: (12, 0)))
        extension = SimpleNamespace(CUDA_HOME=str(self.root))
        with patch.dict(sys.modules, {"torch": torch, "triton": SimpleNamespace(__version__="3.6"),
                                     "torch.utils.cpp_extension": extension}), \
             patch.object(sage.shutil, "which", return_value="/usr/bin/c++"), \
             patch.object(sage, "output", return_value="release 13.0, compiler fixture"):
            self.assertEqual(sage.runtime_identity("13.0")["torch"], "2.11.0+cu130")
            with patch.object(sage.shutil, "which", return_value=None):
                with self.assertRaisesRegex(ValueError, "C\\+\\+ compiler"):
                    sage.runtime_identity("13.0")
            with patch.object(sage, "output", return_value="release 12.8"):
                with self.assertRaisesRegex(ValueError, "must match"):
                    sage.runtime_identity("13.0")
            torch.__version__ = "2.12.0+cu130"
            with self.assertRaisesRegex(ValueError, "will not replace"):
                sage.runtime_identity("13.0")
            torch.__version__ = "2.11.0+cu130"
            torch.cuda.get_device_capability = lambda n: (9, 0)
            with self.assertRaisesRegex(ValueError, "sm_120"):
                sage.runtime_identity("13.0")
            torch.cuda.get_device_capability = lambda n: (12, 0)
            nvcc.unlink()
            with self.assertRaisesRegex(ValueError, "nvcc"):
                sage.runtime_identity("13.0")


if __name__ == "__main__":
    unittest.main()
