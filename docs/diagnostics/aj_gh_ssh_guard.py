#!/usr/bin/env python3
"""Restricted GitHub Actions SSH entrypoint for the user's existing Vast container.

Only accepts the operator's existing 'true' connectivity probe and exactly the
Python script whose hash is currently pinned in the trusted GitHub control.
It does not permit SSH shell access or forwarding.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO="ziomalskit/ziomalskit"
REF="diag/vast-init-59453490"
CONTROL="docs/diagnostics/59453490-control.json"
BASE="https://api.github.com/repos/"+REPO+"/contents/"

def main():
    original=os.environ.get("SSH_ORIGINAL_COMMAND","")
    if original=="true":
        return 0
    if original!="python3 -":
        return 126
    script=sys.stdin.buffer.read(2_000_001)
    if not script or len(script)>2_000_000:
        return 126
    request=urllib.request.Request(
        BASE+CONTROL+"?ref="+REF,
        headers={"User-Agent":"AJ-SSH-restricted-operator","Accept":"application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request,timeout=12) as response:
            doc=json.load(response)
        control=json.loads(base64.b64decode(doc["content"]))
    except Exception:
        return 126
    path=control.get("script_path")
    if (control.get("action")=="close"
            or not isinstance(path,str)
            or re.fullmatch(r"docs/diagnostics/[A-Za-z0-9_-]+\.py",path) is None
            or control.get("sha256")!=hashlib.sha256(script).hexdigest()):
        return 126
    try:
        return subprocess.run([sys.executable,"-"],input=script,
                              timeout=min(7200,max(30,int(control.get("timeout_seconds",600)))),
                              check=False).returncode
    except Exception:
        return 125

if __name__=="__main__":
    sys.exit(main())
