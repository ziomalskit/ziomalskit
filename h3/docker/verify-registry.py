#!/usr/bin/env python3
"""Verify the published GHCR manifest/config and layer availability without a pull."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    image, tag, digest, revision, destination = sys.argv[1:]
    if not re.fullmatch(r"ghcr.io/[a-z0-9._/-]+", image):
        raise ValueError("Expected a GHCR image")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Missing build digest")
    repository = image.removeprefix("ghcr.io/")
    headers = {"Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"}
    # Only the ephemeral workflow token is used; it is never written to evidence.
    credentials = base64.b64encode(f"{os.environ['GITHUB_ACTOR']}:{os.environ['GHCR_TOKEN']}".encode()).decode()
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
    with urlopen(Request(f"https://ghcr.io/token?{query}", headers={"Authorization": f"Basic {credentials}"}), timeout=60) as response:
        token = json.load(response)["token"]
    headers["Authorization"] = f"Bearer {token}"

    def fetch(path, method="GET"):
        with urlopen(Request(f"https://ghcr.io/v2/{repository}/{path}", headers=headers, method=method), timeout=60) as response:
            return response.read(), response.headers

    def checked_json(path, expected):
        body, response_headers = fetch(path)
        actual = "sha256:" + hashlib.sha256(body).hexdigest()
        if actual != expected or response_headers.get("Docker-Content-Digest", expected) != expected:
            raise ValueError(f"Registry digest mismatch for {path}")
        return json.loads(body)

    manifest = checked_json(f"manifests/{digest}", digest)
    checked_json(f"manifests/{tag}", digest)
    config_descriptor = manifest["config"]
    config = checked_json(f"blobs/{config_descriptor['digest']}", config_descriptor["digest"])
    if (config["os"], config["architecture"]) != ("linux", "amd64"):
        raise ValueError("Published platform differs from linux/amd64")
    labels = config["config"]["Labels"]
    if labels["org.opencontainers.image.revision"] != revision:
        raise ValueError("Published source revision differs from checkout")
    if labels["org.opencontainers.image.source"] != "https://github.com/ziomalskit/ziomalskit":
        raise ValueError("Published source repository mismatch")
    for layer in manifest["layers"]:
        _, response_headers = fetch(f"blobs/{layer['digest']}", "HEAD")
        if int(response_headers["Content-Length"]) != layer["size"]:
            raise ValueError("Registry layer size mismatch")
    report = dict(image=f"{image}@{digest}", tag=tag, revision=revision,
                  platform="linux/amd64", config_digest=config_descriptor["digest"],
                  layers=len(manifest["layers"]),
                  compressed_layer_bytes=sum(layer["size"] for layer in manifest["layers"]),
                  manifest_digest_verified=True, tag_digest_verified=True,
                  config_digest_verified=True, layers_available=True, gpu_validation="PENDING")
    Path(destination).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
