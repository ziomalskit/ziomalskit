#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077
unset HF_TOKEN HF_HUB_TOKEN HUGGING_FACE_HUB_TOKEN VAST_API_KEY VASTAI_API_KEY
mkdir -p /run/sshd /root/.ssh
# PUBLIC_KEY is an SSH PUBLIC key; never supply a password or private key.
if [[ -n "${PUBLIC_KEY:-}" ]]; then
  [[ "$PUBLIC_KEY" == ssh-* || "$PUBLIC_KEY" == ecdsa-* ]] || { echo 'Invalid SSH public key' >&2; exit 1; }
  printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
fi
unset PUBLIC_KEY
[[ -s /root/.ssh/authorized_keys ]] || { echo 'Configure the Vast SSH public key (PUBLIC_KEY or authorized_keys); no password login.' >&2; exit 1; }
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A >/dev/null
python3 -c 'import pathlib,uuid; pathlib.Path("/run/aj-boot-id").write_text(str(uuid.uuid4()))'
# Docker/Vast only starts private SSH. Health, transfer and services are explicit.
echo 'AJ image started; run aj-docker init over SSH. No weights/services/renders started.'
exec /usr/sbin/sshd -D -e -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -o PermitRootLogin=prohibit-password -o AllowTcpForwarding=local -o GatewayPorts=no
