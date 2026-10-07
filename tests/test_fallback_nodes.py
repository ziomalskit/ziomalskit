"""Pinned installs against real disposable Git repositories, with no network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.test_deployment import executable


ROOT = Path(__file__).resolve().parents[1]


class FallbackNodeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aj-fallback-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.panel = self.base / "panel"
        (self.panel / "scripts").mkdir(parents=True)
        (self.panel / "config").mkdir()
        for name in ("install_fallback_nodes.sh", "python_env.sh", "runtime_config.py", "deployment.py"):
            shutil.copy2(ROOT / "h3/scripts" / name, self.panel / "scripts" / name)
        self.git_env = dict(os.environ, GIT_AUTHOR_NAME="AJ test", GIT_COMMITTER_NAME="AJ test",
                            GIT_AUTHOR_EMAIL="test@example.invalid", GIT_COMMITTER_EMAIL="test@example.invalid")
        manifest = json.loads((ROOT / "h3/config/custom_nodes_manifest.json").read_text())
        self.specs = manifest["explicit_fallback_git"]
        for index, spec in enumerate(self.specs):
            source = self.base / f"source-{index}"
            source.mkdir()
            self.git(source, "init", "-q")
            (source / "marker").write_text("pinned\n")
            self.git(source, "add", "marker")
            self.git(source, "commit", "-qm", "pinned fixture")
            spec["repo"] = str(source)
            spec["revision"] = self.git(source, "rev-parse", "HEAD").strip()
            (source / "marker").write_text("moving HEAD\n")
            self.git(source, "commit", "-qam", "later fixture")
        self.manifest = self.panel / "config/custom_nodes_manifest.json"
        self.manifest.write_text(json.dumps(manifest))
        # The unchanged LLM portion also uses an isolated local repository.
        llm = self.base / "llm-source"
        llm.mkdir()
        self.git(llm, "init", "-q")
        (llm / "marker").write_text("LLM fixture")
        self.git(llm, "add", "marker")
        self.git(llm, "commit", "-qm", "LLM fixture")
        llm_commit = self.git(llm, "rev-parse", "HEAD").strip()
        config = self.panel / "scripts/runtime_config.py"
        config.write_text(config.read_text().replace("65983de33681816f856db7e16767da906084c688", llm_commit))
        prefix = self.base / "env"
        python = prefix / "bin/python"
        executable(python, f'''#!{sys.executable}
import os,sys
a=sys.argv[1:]
if a and a[0]=='-c' and 'print(sys.prefix)' in a[1]:print({str(prefix)!r});sys.exit(0)
if a[:2]==['-m','pip']:sys.exit(0)
os.execv({sys.executable!r},[{sys.executable!r},*a])
''')
        self.comfy = self.base / "ComfyUI"
        self.environment = dict(self.git_env, PANEL_ROOT=str(self.panel), COMFY_ROOT=str(self.comfy),
                                PYTHON_BIN=str(python), COMFY_PYTHON=str(python))
        count = int(self.environment.get("GIT_CONFIG_COUNT", "0"))
        self.environment.update({"GIT_CONFIG_COUNT": str(count + 1),
                                 f"GIT_CONFIG_KEY_{count}": f"url.{llm}.insteadOf",
                                 f"GIT_CONFIG_VALUE_{count}": "https://github.com/KingManiya/ComfyUI-LLM-text-processor.git"})

    def git(self, path, *args):
        return subprocess.check_output(["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
                                       text=True, stderr=subprocess.PIPE, env=self.git_env)

    def install(self):
        return subprocess.run(["bash", str(self.panel / "scripts/install_fallback_nodes.sh")],
                              text=True, capture_output=True, env=self.environment, timeout=15)

    def node(self, spec):
        return self.comfy / "custom_nodes" / spec["folder"]

    def test_manifest_has_all_three_full_pins(self):
        specs = json.loads((ROOT / "h3/config/custom_nodes_manifest.json").read_text())["explicit_fallback_git"]
        self.assertEqual(len(specs), 3)
        for spec in specs:
            self.assertRegex(spec["revision"], r"^[0-9a-f]{40}$")
            self.assertTrue(spec["repo"].startswith("https://github.com/"))
            self.assertTrue(spec["folder"])

    def test_fresh_install_and_rerun_enforce_pins_instead_of_remote_head(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            for spec in self.specs:
                self.assertEqual(self.git(self.node(spec), "rev-parse", "HEAD").strip(), spec["revision"])
                self.assertEqual((self.node(spec) / "marker").read_text(), "pinned\n")

    def test_existing_revision_mismatch_is_checked_out_and_original_branch_preserved(self):
        for spec in self.specs:
            target = self.node(spec)
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", spec["repo"], str(target)], env=self.git_env, check=True)
            self.assertNotEqual(self.git(target, "rev-parse", "HEAD").strip(), spec["revision"])
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for spec in self.specs:
            target = self.node(spec)
            self.assertEqual(self.git(target, "rev-parse", "HEAD").strip(), spec["revision"])
            self.assertTrue(self.git(target, "branch", "--list").strip(), "existing branch was removed")

    def test_local_tracked_staged_and_untracked_changes_are_preserved(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.node(self.specs[0])
        for kind in ("tracked", "staged", "untracked"):
            with self.subTest(kind=kind):
                path = target / ("local-file" if kind == "untracked" else "marker")
                path.write_text("local changes must survive\n")
                if kind == "staged":
                    self.git(target, "add", "marker")
                before = self.git(target, "status", "--porcelain")
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Local changes preserved", result.stderr)
                self.assertEqual(path.read_text(), "local changes must survive\n")
                self.assertEqual(self.git(target, "status", "--porcelain"), before)
                self.git(target, "restore", "--staged", "--worktree", "marker")
                if kind == "untracked":
                    path.unlink()

    def test_wrong_origin_is_rejected_without_replacing_checkout(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.node(self.specs[0])
        self.git(target, "remote", "set-url", "origin", str(self.base / "unrelated"))
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected node origin", result.stderr)
        self.assertEqual(self.git(target, "rev-parse", "HEAD").strip(), self.specs[0]["revision"])
