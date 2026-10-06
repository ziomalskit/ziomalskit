"""Execute upstream checks sequentially in a disposable copy, preserving state."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / 'migration/01_CURRENT_TRUTH/H3_VAST_MOBILE_PRE_RENTAL_FINAL_RC5',
    ROOT / 'h3',
)
for source in SOURCES:
    print(f'Unmodified offline suites: {source.relative_to(ROOT)}', flush=True)
    with tempfile.TemporaryDirectory(prefix='aj-offline-') as temporary:
        target = Path(temporary) / 'panel'
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', 'runtime.env'))
        for name in ('offline_selftest.py', 'integration_matrix_test.py'):
            subprocess.run([sys.executable, str(target / 'scripts' / name)], cwd=target, check=True)
