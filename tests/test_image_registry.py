"""Registry evidence must reject a moved tag, corrupt config or missing layer."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_registry", ROOT / "h3/docker/verify-registry.py")
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


class RegistryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.revision = "a" * 40
        self.config = json.dumps(dict(os="linux", architecture="amd64", config=dict(Labels={
            "org.opencontainers.image.revision": self.revision,
            "org.opencontainers.image.source": "https://github.com/ziomalskit/ziomalskit"}))).encode()
        self.config_digest = self.digest(self.config)
        self.layer_digest = "sha256:" + "b" * 64
        self.manifest = json.dumps(dict(config=dict(digest=self.config_digest),
                                       layers=[dict(digest=self.layer_digest, size=123)])).encode()
        self.manifest_digest = self.digest(self.manifest)
        self.calls = []
        self.responses = {
            "manifests/sha-fixture": self.manifest,
            f"manifests/{self.manifest_digest}": self.manifest,
            f"blobs/{self.config_digest}": self.config,
            f"blobs/{self.layer_digest}": b"",
        }

    @staticmethod
    def digest(body):
        return "sha256:" + hashlib.sha256(body).hexdigest()

    def fetch(self, request, timeout):
        self.calls.append((request.full_url, request.get_method()))
        if request.full_url.startswith("https://ghcr.io/token?"):
            body = b'{"token":"FAKE_TEST_TOKEN"}'
        else:
            key = request.full_url.split("/v2/ziomalskit/aj-vast/")[1]
            body = self.responses[key]
            if body is None:
                raise HTTPError(request.full_url, 404, "missing layer", {}, None)
        response = io.BytesIO(body)
        response.headers = {"Content-Length": "123"}
        return response

    def verify(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "registry.json"
            args = ["verify", "ghcr.io/ziomalskit/aj-vast", "sha-fixture", self.manifest_digest, self.revision, str(report)]
            with patch.object(registry.sys, "argv", args), patch.object(registry, "urlopen", self.fetch), \
                 patch.dict(os.environ, {"GITHUB_ACTOR": "test", "GHCR_TOKEN": "FAKE_TEST_CREDENTIAL"}), \
                 patch("sys.stdout", io.StringIO()):
                registry.main()
            return json.loads(report.read_text())

    def test_verifies_registry_bytes_tag_revision_and_layer_availability(self):
        report = self.verify()
        self.assertTrue(report["manifest_digest_verified"])
        self.assertTrue(report["tag_digest_verified"])
        self.assertEqual(report["compressed_layer_bytes"], 123)
        self.assertEqual(report["gpu_validation"], "PENDING")
        self.assertTrue(any(method == "HEAD" for _, method in self.calls))
        self.assertNotIn("FAKE_TEST", json.dumps(report))

    def test_moved_tag_rejects_publication_evidence(self):
        self.responses["manifests/sha-fixture"] = b'{"changed":true}'
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.verify()

    def test_corrupt_config_rejects_publication_evidence(self):
        self.responses[f"blobs/{self.config_digest}"] = b'{"corrupt":true}'
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.verify()

    def test_missing_layer_rejects_publication_evidence(self):
        self.responses[f"blobs/{self.layer_digest}"] = None
        with self.assertRaises(HTTPError):
            self.verify()
