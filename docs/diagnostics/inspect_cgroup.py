import json, pathlib, os, shutil, hashlib
print('CGROUP_MEMBERSHIP',pathlib.Path('/proc/self/cgroup').read_text())
print('CGROUP_MOUNTS')
mounts=[]
for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
 if ' - cgroup ' in line or ' - cgroup2 ' in line:
  fields=line.split();print(line);mounts.append(fields[4])
print('AFFINITY_CPUS',len(os.sched_getaffinity(0)))
for mount in mounts:
 root=pathlib.Path(mount)
 for name in ['memory.limit_in_bytes','memory.soft_limit_in_bytes','memory.usage_in_bytes','memory.use_hierarchy','cpu.cfs_quota_us','cpu.cfs_period_us','cpuset.cpus','cgroup.controllers']:
  f=root/name
  if f.is_file(): print('LIMIT',str(f),f.read_text().strip())
print('DISK',dict(zip(['total','used','free'],shutil.disk_usage('/workspace'))))
f=pathlib.Path('/opt/aj/ziomalskit/h3/docker/instance.py');print('INSTANCE_PY_SHA256',hashlib.sha256(f.read_bytes()).hexdigest())
print('IMAGE',pathlib.Path('/etc/aj-image.json').read_text())
