"""Verify exact source import, all workflow JSONs, and shell/frontend syntax."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
import re

ROOT = Path(__file__).resolve().parents[1]
archive = ROOT / 'archive/AJ_PROJECT_MIGRATION_2026-10-06.zip'
expected = (ROOT / 'archive/AJ_PROJECT_MIGRATION_2026-10-06.sha256').read_text().split()[0]
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected, 'Migration checksum mismatch'
with zipfile.ZipFile(archive) as source:
    originals = 0
    for member in source.infolist():
        if member.is_dir():
            continue
        relative = Path(*Path(member.filename).parts[1:])
        assert (ROOT / 'migration' / relative).read_bytes() == source.read(member), relative
        originals += 1
jsons = list((ROOT / 'migration').rglob('*.json'))
for path in jsons:
    json.loads(path.read_text())
shells = list((ROOT / 'migration').rglob('*.sh')) + list((ROOT / 'scripts').glob('*.sh'))
for path in shells:
    subprocess.run(['bash', '-n', str(path)], check=True)
html = ROOT / 'migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5/static/index.html'
scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', html.read_text(), flags=re.S | re.I)
if shutil.which('node'):
    with tempfile.TemporaryDirectory() as temporary:
        for index, content in enumerate(scripts):
            path = Path(temporary) / f'inline-{index}.js'
            path.write_text(content)
            subprocess.run(['node', '--check', str(path)], check=True)
    print(f'Frontend JavaScript syntax: {len(scripts)} inline script(s) checked; browser behavior untested')
else:
    print('Frontend JavaScript syntax: SKIPPED (optional Node.js unavailable)')
print(f'Package integrity PASS: {originals} original files unchanged, {len(jsons)} JSON documents parsed, {len(shells)} shell scripts checked')
