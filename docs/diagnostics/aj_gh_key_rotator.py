#!/usr/bin/env python3
"""Refresh a single restricted SSH key based on the trusted AJ GitHub workflow.

Does not touch existing user/Vast keys. Uses no private keys or credentials.
Refreshes current GitHub Actions key while this Vast instance is running.
"""
import base64
import hashlib
import json
import os
import pathlib
import re
import time
import urllib.request

ROOT=pathlib.Path("/root/.ssh")
AUTHORIZED=ROOT/"authorized_keys"
TAG="aj-gh-rotating-key-59453490"
GUARD="/workspace/.aj-diagnostics/aj_gh_ssh_guard.py"
URL=("https://api.github.com/repos/ziomalskit/ziomalskit/contents/"
     "docs/diagnostics/59453490-runner.pub?ref=diag/vast-init-59453490")

def get_key():
    request=urllib.request.Request(URL,headers={
        "Accept":"application/vnd.github+json","User-Agent":"AJ-SSH-rotator"})
    with urllib.request.urlopen(request,timeout=12) as response:
        data=json.load(response)
    content=base64.b64decode(data["content"]).decode().strip()
    parts=content.split()
    if (len(parts)!=3 or parts[0]!="ssh-ed25519" or
            parts[2]!="aj-gh-init-59453490"):
        raise ValueError("Unexpected published Actions SSH key")
    blob=base64.b64decode(parts[1],validate=True)
    if len(blob)!=51 or blob[:15]!=b"\x00\x00\x00\x0bssh-ed25519":
        raise ValueError("Invalid Ed25519 public key encoding")
    return parts[0]+" "+parts[1]

def refresh():
    key=get_key()
    desired=('restrict,command="'+GUARD+'" '+key+" "+TAG)
    if AUTHORIZED.is_symlink() or not AUTHORIZED.is_file():
        raise RuntimeError("Refusing unsafe authorized_keys path")
    content=AUTHORIZED.read_text()
    lines=[line for line in content.splitlines() if TAG not in line]
    body="\n".join([*lines,desired])+"\n"
    if body==content:
        return False
    temporary=AUTHORIZED.with_name(".authorized_keys.aj-tmp")
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"w") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        # Check that Vast has not concurrently changed user-managed keys.
        if AUTHORIZED.read_text()!=content:
            raise RuntimeError("Authorized keys changed concurrently; retrying")
        os.replace(temporary,AUTHORIZED)
        return True
    finally:
        temporary.unlink(missing_ok=True)

def main():
    while True:
        try:
            if refresh():
                print("ROTATED_GITHUB_ACTIONS_KEY",flush=True)
        except Exception as exc:
            print("ROTATION_DELAY",type(exc).__name__,flush=True)
        time.sleep(90)

if __name__=="__main__":
    main()
