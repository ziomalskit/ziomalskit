#!/usr/bin/env bash
# Archive tracked source only: untracked tokens/weights never enter build context.
set -euo pipefail
root="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$root"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Commit tracked edits before building.' >&2; exit 1; }
revision="$(git rev-parse HEAD)"
context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
git archive HEAD h3 | tar -x -C "$context"
args=()
if [[ -n "${CODEX_PROXY_CERT:-}" ]]; then
  args+=(--secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt)
fi
image="aj-vast:${revision:0:12}"
docker build --platform linux/amd64 "${args[@]}" --build-arg "AJ_REVISION=$revision" \
  -t "$image" -f "$context/h3/docker/Dockerfile" "$context"
docker image inspect "$image" --format 'Image={{.Id}} UncompressedBytes={{.Size}}'
echo 'Local image only. Nothing published. Record the registry digest after separately approved publication.'
