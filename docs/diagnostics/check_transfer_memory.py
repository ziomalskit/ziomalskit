import pathlib,json,subprocess
state=pathlib.Path('/workspace/.aj-diagnostics/models-transfer.json')
print('TRANSFER',state.read_text() if state.exists() else 'missing')
for name in ['memory.stat','memory.usage_in_bytes','memory.limit_in_bytes','memory.soft_limit_in_bytes']:
 print(name,(pathlib.Path('/sys/fs/cgroup/memory')/name).read_text())
print('HOST_AVAILABLE',[line for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')])
log=pathlib.Path('/workspace/.aj-diagnostics/models-transfer.log')
if log.exists():
 for line in log.read_text(errors='replace').splitlines()[-6:]:
  if 'https:' not in line and 'token' not in line.lower():print('TRANSFER_LOG',line[:1500])
