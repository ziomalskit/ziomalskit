"""Operator boundaries with synthetic quotes/devices; no GPU/provider/inference."""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from h3.scripts import gpu_acceptance_operator as operator, model_acceptance as models
from h3.scripts import process_identity
from tests.helpers import load_controller

UTC = timezone.utc
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
BODY = b'GGUF CPU SYNTHETIC OPERATOR FIXTURE; NOT A REAL MODEL'


class OperatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name); self.root = self.base/'acceptance'
        self.session_path = self.base/'operator-session.json'
        self.session = json.loads(operator.TEMPLATE.read_text())
        self.session.update(checkout_sha='a'*40, root=str(self.root))
        self.session['approvals'] = dict.fromkeys(self.session['approvals'], True)
        self.session['storage'].update(mount=str(self.base), volume_id='123', machine_id='456', device=42, inode=99,
            source='/dev/fixture', fstype='ext4', fsroot='/', build_reserve_bytes=10**9, evidence_reserve_bytes=10**9)
        self.session['hardware'].update(gpu_name='NVIDIA RTX PRO 6000 Blackwell Workstation Edition',
            min_vram_mib=97000, min_ram_bytes=32*1024**3, min_cpu_cores=4)
        # Prices and limits are test data, never a recommendation or live quote.
        self.session['quote'].update(reference='SYNTHETIC_NO_PROVIDER_REQUEST',
            valid_until_utc='2026-10-16T00:00:00Z', gpu_usd_per_hour=1, instance_disk_usd_per_hour=.2,
            volume_usd_per_hour=.1, stopped_disk_usd_per_hour=.2, ingress_usd_per_gb=.05,
            egress_usd_per_gb=.1, external_preparation_usd=2, download_bytes_cap=200*10**9,
            upload_bytes_cap=10**9, retention_hours=24)
        self.session['limits'].update(max_instance_usd_per_hour=2, max_total_usd=100, max_session_seconds=7200,
            max_preflight_seconds=600, max_review_seconds=600,
            max_download_seconds=3600, max_build_seconds=1800, max_probe_seconds=600, min_free_bytes=2*10**9,
            poll_seconds=.25, shutdown_grace_seconds=10, billing_max_age_seconds=300)
        self.session['billing'].update(session_started_utc='2026-10-15T11:59:00Z',
            observed_at_utc='2026-10-15T11:59:30Z', observed_total_usd=3)
        self.observed = {'mount':{'target':str(self.base), 'source':'/dev/fixture', 'fstype':'ext4', 'fsroot':'/', 'options':'rw'},
            'device':42, 'inode':99, 'container_root_device':1, 'gpu':{'uuid':'GPU-SYNTHETIC',
                'name':self.session['hardware']['gpu_name'], 'driver':'580.65.06', 'total_mib':97920,
                'used_mib':1000, 'free_mib':96920, 'compute_cap':'12.0'}, 'gpu_pids':[], 'cuda':'13.0',
            'kernel':'6.9.0-test', 'python':[3,12,0], 'ram_available_bytes':64*1024**3, 'cpu_cores':8,
            'free_bytes':10**12, 'production_ports':[]}
        disk = patch.object(models.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**12))
        disk.start();self.addCleanup(disk.stop)
        self.items,self.approval = models.load_selection()

    def write_session(self, session=None):
        self.session_path.write_text(json.dumps(session or self.session)); self.session_path.chmod(0o600)

    def prepare(self):
        models.require_store(self.root,self.approval,create=True,environment={})
        evidence=self.root/'evidence';evidence.mkdir(mode=0o700)
        self.write_session();return evidence/'telemetry.jsonl'

    def inspect(self, phase='A', **kwargs):
        return operator.inspect(self.session,phase,observer=lambda _:copy.deepcopy(self.observed),now=NOW,**kwargs)

    def synthetic_models(self):
        # This source lock exists only inside a private CPU fixture.
        manifest=json.loads((models.PACKAGE/'config/models_manifest.json').read_text())
        approval=copy.deepcopy(self.approval)
        for row in manifest['artifacts']+approval['approved_artifacts']:
            if row['id'] in models.APPROVED:
                row.update(size_bytes=len(BODY),sha256=hashlib.sha256(BODY).hexdigest())
        profiles=json.loads((models.PACKAGE/'config/production_profiles.json').read_text())
        items=models.select_artifacts(manifest,approval,profiles)
        self.items,self.approval=items,approval
        selected=patch.object(models,'load_selection',return_value=(items,approval));selected.start();self.addCleanup(selected.stop)
        output=self.prepare()
        for item in items:
            path=self.root/'models'/item['destination'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(BODY)
        def verify(root, selected_items, selected_approval, **_):
            self.assertEqual(selected_approval,approval)
            result=[]
            for item in selected_items:
                checked=models.verify_file(root/'models',item['destination'],item)
                result.append({'id':item['id'],'signature':checked['signature'],'sha256':checked['sha256']})
            return {'model_files':result,'gpu_acceptance':'not_run','production_gate_retired':False}
        verified=patch.object(models,'verify_models',side_effect=verify);verified.start();self.addCleanup(verified.stop)
        return output

    def watch(self, output, **kwargs):
        return operator.watch(self.session_path,self.session,kwargs.pop('phase','B'),output,
            observer=kwargs.pop('observer',lambda _:copy.deepcopy(self.observed)),clock=lambda:0,
            wall_clock=lambda:NOW,pause=kwargs.pop('pause',Mock(side_effect=KeyboardInterrupt)),**kwargs)

    def test_unfilled_default_plan_has_no_gpu_network_write_or_guessed_limits(self):
        template=json.loads(operator.TEMPLATE.read_text())
        with patch.object(operator,'command',side_effect=AssertionError('readonly source plan only')):
            result=operator.plan(template,now=NOW)
        self.assertEqual(result['status'],'BLOCKED');self.assertFalse(self.root.exists())
        self.assertIn('limits.max_total_usd',result['blocked']);self.assertIn('approval.first_cost',result['blocked'])
        self.assertEqual(len(result['approved_artifacts']),11);self.assertFalse(result['submissions'])
        self.assertEqual(template,json.loads(operator.TEMPLATE.read_text()))

    def test_phase_approvals_remain_separate_and_never_grant_production(self):
        self.session['approvals']['prepare_models']=False;self.session['approvals']['model_probes']=False
        self.assertEqual(operator.validate_session(self.session,'A',NOW),[])
        self.assertIn('approval.prepare_models',operator.validate_session(self.session,'B',NOW))
        self.assertIn('approval.model_probes',operator.validate_session(self.session,'C',NOW))
        self.assertIs(operator.plan(self.session,'A',NOW)['submissions'],False)

    def test_cost_quote_can_be_reviewed_before_rental_without_inventing_mount_identity(self):
        self.session['storage']['device']=None;self.session['storage']['inode']=None
        self.session['approvals']['first_cost']=False
        result=operator.plan(self.session,now=NOW)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(Decimal(result['cost_ceiling_usd']),Decimal('21.90'))
        self.assertIn('storage.device',result['blocked']);self.assertIn('approval.first_cost',result['blocked'])
        self.assertIsNone(self.session['storage']['device'])

    def test_missing_negative_boolean_nan_and_infinite_limits_are_rejected(self):
        for key in self.session['limits']:
            for value in (None,False,-1,float('nan'),float('inf'),0):
                session=copy.deepcopy(self.session);session['limits'][key]=value
                with self.subTest(key=key,value=value):
                    if value is None:self.assertIn('limits.'+key,operator.validate_session(session,'A',NOW))
                    else:
                        with self.assertRaises(ValueError):operator.validate_session(session,'A',NOW)

    def test_quote_zero_requires_explicit_value_and_unknown_secret_fields_fail(self):
        for key in ('gpu_usd_per_hour','volume_usd_per_hour','ingress_usd_per_gb'):
            session=copy.deepcopy(self.session);session['quote'][key]=0
            self.assertEqual(operator.validate_session(session,'A',NOW),[])
            session['quote'][key]=None
            self.assertIn('quote.'+key,operator.validate_session(session,'A',NOW))
        self.session['HF_TOKEN']='PRIVATE_SENTINEL'
        with self.assertRaises(ValueError):operator.validate_session(self.session,'A',NOW)

    def test_cost_ceiling_accounts_for_retention_transfer_build_session_and_external_prepare(self):
        self.assertEqual(operator.cost_ceiling(self.session),Decimal('21.90'))
        self.session['limits']['max_total_usd']=20
        self.assertIn('BUDGET_EXCEEDS_LIMIT',operator.plan(self.session,now=NOW)['blocked'])
        # Compute alone fits, but the combined active compute/storage rate does not.
        self.session['limits']['max_instance_usd_per_hour']=1.1
        self.assertIn('INSTANCE_RATE_EXCEEDS_LIMIT',operator.plan(self.session,now=NOW)['blocked'])

    def test_quote_expiry_time_inconsistency_and_unapproved_toolkit_are_blocked(self):
        cases=[('quote','valid_until_utc','2026-10-14T00:00:00Z'),('quote','valid_until_utc','2026-10-16'),
               ('limits','max_probe_seconds',8000),('limits','max_build_seconds',8000),
               ('limits','max_preflight_seconds',8000),('limits','max_review_seconds',8000),('hardware','cuda','12.8')]
        for section,key,value in cases:
            session=copy.deepcopy(self.session);session[section][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):operator.validate_session(session,'A',NOW)

    def test_operator_disk_floor_cannot_override_model_staging_or_reserves(self):
        self.session['limits']['min_free_bytes']=1
        with patch.object(models.shutil,'disk_usage',return_value=SimpleNamespace(free=100*10**9)):
            result=operator.plan(self.session,now=NOW)
        self.assertIn('INSUFFICIENT_DISK',result['blocked'])
        self.assertEqual(result['minimum_free_bytes'],113395969792+2*10**9)
        self.session['quote']['download_bytes_cap']=1
        self.assertIn('TRANSFER_CAP_TOO_SMALL',operator.plan(self.session,now=NOW)['blocked'])

    def test_live_disk_floor_tracks_operational_reserve_without_reallocating_downloaded_bytes(self):
        with patch.object(models.shutil,'disk_usage',return_value=SimpleNamespace(free=40*10**9)):
            self.assertEqual(operator.plan(self.session,now=NOW)['status'],'BLOCKED')
            self.assertEqual(operator.plan(self.session,now=NOW,live=True)['status'],'PLAN_READY')
        with patch.object(models.shutil,'disk_usage',return_value=SimpleNamespace(free=10**9)):
            self.assertEqual(operator.plan(self.session,now=NOW,live=True)['status'],'BLOCKED')
        self.observed['free_bytes']=100*10**9
        self.assertIn('INSUFFICIENT_DISK',self.inspect()['blocked'])

    def test_symlink_production_and_root_mounts_never_become_accepted_storage(self):
        for path in ('/','/workspace/ComfyUI','/workspace/H3_VAST_MOBILE'):
            session=copy.deepcopy(self.session);session['root']=path
            with self.subTest(path=path),self.assertRaises(ValueError):operator.validate_session(session,'A',NOW)
        linked=self.base/'linked';linked.symlink_to(self.base,target_is_directory=True)
        self.session['storage']['mount']=str(linked)
        with self.assertRaises(ValueError):operator.validate_session(self.session,'A',NOW)

    def test_mount_identity_fsroot_device_inode_and_retention_are_fail_closed(self):
        changes=[('device',43),('inode',100),('container_root_device',42)]
        for key,value in changes:
            old=self.observed[key];self.observed[key]=value
            self.assertIn('PERSISTENT_MOUNT_CHANGED_OR_UNPROVEN',self.inspect()['blocked']);self.observed[key]=old
        for key,value in [('source','other'),('target','/'),('fstype','overlay'),('fsroot','other'),('options','ro')]:
            old=self.observed['mount'][key];self.observed['mount'][key]=value
            self.assertIn('PERSISTENT_MOUNT_CHANGED_OR_UNPROVEN',self.inspect()['blocked']);self.observed['mount'][key]=old

    def test_linux_68_passes_only_isolated_preflight_with_all_other_requirements_met(self):
        self.synthetic_models()
        self.observed['kernel']='6.8.0-142-generic'
        self.observed['gpu']['driver']='595.91.07'
        with (patch.object(operator,'command',side_effect=AssertionError('no live diagnostic command')),
              patch.object(os,'pidfd_open',side_effect=AssertionError('no production group control'))):
            for phase in ('A','B','C','D'):
                with self.subTest(phase=phase):
                    result=self.inspect(phase,verify_models=phase=='C')
                    self.assertEqual(result['status'],'PREFLIGHT_READY')
                    self.assertFalse(result['submissions'])
                    self.assertFalse(result['submissions'])
                    self.assertEqual(result['gpu_acceptance'],'NOT RUN')
                    if phase=='C':self.assertEqual(result['model_sha256_verification'],'VERIFIED')

    def test_linux_67_is_blocked_before_model_runtime_verification_in_every_phase(self):
        self.synthetic_models()
        self.observed['kernel']='6.7.12-generic'
        with patch.object(models,'verify_models',side_effect=AssertionError('blocked kernel cannot verify runtime')) as verify:
            for phase in ('A','B','C','D'):
                with self.subTest(phase=phase):
                    result=self.inspect(phase,verify_models=True)
                    self.assertEqual(result['status'],'BLOCKED')
                    self.assertIn('KERNEL_PYTHON_MISMATCH',result['blocked'])
            verify.assert_not_called()

    def test_linux_69_still_passes_every_isolated_phase(self):
        self.synthetic_models()
        self.observed['kernel']='6.9.0-generic'
        for phase in ('A','B','C','D'):
            with self.subTest(phase=phase):
                self.assertEqual(self.inspect(phase,verify_models=phase=='C')['status'],'PREFLIGHT_READY')

    def test_linux_68_watch_keeps_real_billing_deadlines_and_observation_only(self):
        output=self.synthetic_models()
        self.observed['kernel']='6.8.0-142-generic'
        with (patch.object(subprocess,'Popen',side_effect=AssertionError('no inference or lifecycle')),
              patch.object(os,'pidfd_open',side_effect=AssertionError('no production group control')),
              patch('sys.stdout',new=io.StringIO())):
            result=self.watch(output,phase='C')
        self.assertEqual(result['status'],'INTERRUPTED')
        self.assertTrue(result['billing_continues'])
        self.assertFalse(result['submissions'])
        self.session['billing']['observed_at_utc']='2026-10-15T11:40:00Z'
        with self.assertRaisesRegex(ValueError,'stale/invalid actual billing'):
            self.watch(output.with_name('stale.jsonl'),phase='C')
        self.assertFalse(output.with_name('stale.jsonl').exists())
        self.session['billing']['observed_at_utc']='2026-10-15T12:00:00Z'
        for phase,elapsed in (('B',5400),('C',600)):
            with self.subTest(phase=phase),self.assertRaisesRegex(ValueError,'time/cost ceiling'):
                operator.live_budget(self.session,phase,NOW,elapsed)

    def test_wrong_gpu_driver_cuda_kernel_ram_cpu_and_services_never_authorize_probes(self):
        self.observed['kernel']='6.8.0-142-generic'
        gpu_cases=[('name','NVIDIA RTX 6000 Ada'),('compute_cap','8.9'),('driver','579.99.99'),('total_mib',90000)]
        for key,value in gpu_cases:
            old=self.observed['gpu'][key];self.observed['gpu'][key]=value
            self.assertEqual(self.inspect()['status'],'BLOCKED');self.observed['gpu'][key]=old
        for key,value in [('cuda','12.8'),('kernel','6.7.0'),('python',[3,10,0]),('cpu_cores',1),
                          ('ram_available_bytes',1),('production_ports',[8188]),('free_bytes',1)]:
            old=self.observed[key];self.observed[key]=value
            self.assertEqual(self.inspect()['status'],'BLOCKED');self.observed[key]=old

    def test_missing_gpu_is_a_failure_and_never_installs_or_downloads(self):
        with self.assertRaises(FileNotFoundError):
            operator.inspect(self.session,observer=Mock(side_effect=FileNotFoundError('no GPU')),now=NOW)
        self.assertFalse(self.root.exists())

    def test_unexpected_gpu_pid_is_blocked_and_watch_allowlist_is_only_observation(self):
        self.observed['gpu_pids']=[123]
        self.assertIn('UNEXPECTED_GPU_PROCESS',self.inspect()['blocked'])
        self.assertEqual(self.inspect(allowed_pids=[123])['status'],'PREFLIGHT_READY')
        self.assertFalse(self.inspect(allowed_pids=[123])['submissions'])

    def test_incomplete_models_block_C_without_calling_file_runtime_verifier(self):
        self.prepare()
        with patch.object(models,'verify_models',side_effect=AssertionError('no incomplete verification')):
            self.assertIn('MODELS_INCOMPLETE',self.inspect('C',verify_models=True)['blocked'])

    def test_phase_C_uses_existing_full_verifier_and_wrong_same_size_bytes_fail(self):
        self.synthetic_models()
        result=self.inspect('C',verify_models=True)
        self.assertEqual(result['model_sha256_verification'],'VERIFIED')
        self.assertEqual(result['gpu_acceptance'],'NOT RUN')
        path=self.root/'models'/self.items[0]['destination'];path.write_bytes(b'X'*len(BODY))
        with self.assertRaises(ValueError):self.inspect('C',verify_models=True)

    def test_actual_billing_staleness_budget_session_and_per_phase_deadline_stop(self):
        good=operator.live_budget(self.session,'C',NOW,0)
        self.assertEqual(good['observed_bill_usd'],'3')
        session=copy.deepcopy(self.session);session['limits']['max_total_usd']=21
        # Provider billing alone omits separately paid preparation. It must be
        # reserved even when the current invoice plus transfer/retention fits.
        with self.assertRaises(ValueError):operator.live_budget(session,'C',NOW,0)
        for change in ('stale','budget','session','phase'):
            session=copy.deepcopy(self.session);elapsed=0
            if change=='stale':session['billing']['observed_at_utc']='2026-10-15T11:40:00Z'
            if change=='budget':session['billing']['observed_total_usd']=100
            if change=='session':session['billing']['session_started_utc']='2026-10-15T09:00:00Z'
            if change=='phase':elapsed=600
            with self.subTest(change=change),self.assertRaises(ValueError):operator.live_budget(session,'C',NOW,elapsed)

    def test_interrupted_watch_preserves_private_evidence_and_never_claims_PASS_or_stops_provider(self):
        output=self.prepare()
        with patch.object(subprocess,'Popen',side_effect=AssertionError('no inference or lifecycle')),patch('sys.stdout',new=io.StringIO()):
            result=self.watch(output)
        self.assertEqual(result['status'],'INTERRUPTED');self.assertTrue(result['billing_continues'])
        records=[json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual([record['event'] for record in records],['START','SAMPLE','END'])
        self.assertEqual(records[-1]['status'],'INTERRUPTED')
        self.assertEqual(output.stat().st_mode&0o777,0o600)
        self.assertFalse(any(row.get('status')=='PASS' or row.get('submissions') for row in records))

    def test_mount_loss_after_one_sample_stops_without_writing_an_acceptance(self):
        output=self.prepare();observer=Mock(side_effect=[copy.deepcopy(self.observed),copy.deepcopy(self.observed),
            {**copy.deepcopy(self.observed),'device':100}])
        with patch('sys.stdout',new=io.StringIO()):result=self.watch(output,observer=observer,pause=lambda _:None)
        self.assertEqual(result['status'],'STOP')
        self.assertIn('PERSISTENT_MOUNT_CHANGED_OR_UNPROVEN',result['stop_reasons'])
        self.assertEqual(json.loads(output.read_text().splitlines()[-1])['samples'],1)

    def test_changed_approval_or_model_after_sha_verification_is_STOP(self):
        for mode in ('approval','model','billing_start','billing_time','billing_total'):
            with self.subTest(mode=mode):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                output=self.synthetic_models()
                changes=0
                def mutate(_):
                    nonlocal changes
                    changes+=1
                    if changes>1:raise KeyboardInterrupt
                    if mode=='approval':
                        new=copy.deepcopy(self.session);new['limits']['max_total_usd']=101;self.write_session(new)
                    elif mode=='model':
                        (self.root/'models'/self.items[0]['destination']).write_bytes(b'X'*len(BODY))
                    else:
                        new=copy.deepcopy(self.session)
                        key,value={'billing_start':('session_started_utc','2026-10-15T11:59:10Z'),
                                   'billing_time':('observed_at_utc','2026-10-15T11:59:10Z'),
                                   'billing_total':('observed_total_usd',2)}[mode]
                        new['billing'][key]=value;self.write_session(new)
                with patch('sys.stdout',new=io.StringIO()):result=self.watch(output,phase='C',pause=mutate)
                self.assertEqual(result['status'],'STOP')
                if mode.startswith('billing_'):
                    self.assertIn('BILLING_COUNTER_CHANGED_OR_MOVED_BACKWARD',result['stop_reasons'])

    def test_atomic_forward_billing_refresh_remains_observational_and_preserves_session_start(self):
        output=self.prepare();refreshes=0
        def refresh(_):
            nonlocal refreshes
            refreshes+=1
            if refreshes>1:raise KeyboardInterrupt
            new=copy.deepcopy(self.session)
            new['billing'].update(observed_at_utc='2026-10-15T12:00:00Z',observed_total_usd=4)
            temporary=self.session_path.with_suffix('.tmp');temporary.write_text(json.dumps(new));temporary.chmod(0o600)
            temporary.replace(self.session_path)
        with patch('sys.stdout',new=io.StringIO()):result=self.watch(output,pause=refresh)
        self.assertEqual(result['status'],'INTERRUPTED')
        records=[json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual([row['observed_bill_usd'] for row in records if row['event']=='SAMPLE'],['3','4'])
        self.assertEqual(result['gpu_acceptance'],'NOT RUN')

    def test_no_overwrite_symlink_or_public_evidence_is_accepted(self):
        output=self.prepare();foreign=self.base/'foreign';foreign.write_text('preserve')
        for kind in ('file','symlink','hardlink'):
            if output.exists() or output.is_symlink():output.unlink()
            if kind=='file':output.write_text('preserve')
            elif kind=='symlink':output.symlink_to(foreign)
            else:os.link(foreign,output)
            with self.subTest(kind=kind),self.assertRaises(OSError):self.watch(output)
            self.assertEqual(foreign.read_text(),'preserve')
        output.unlink();output.parent.chmod(0o755)
        with self.assertRaises(ValueError):self.watch(output)

    def test_stale_bill_prevents_creation_and_collectors_accept_only_real_diagnostic_flags(self):
        output=self.prepare();self.session['billing']['observed_at_utc']='2026-10-15T11:40:00Z';self.write_session()
        with self.assertRaises(ValueError):self.watch(output)
        self.assertFalse(output.exists())
        for argument in ('--run','--allow-pending','--rent'):
            result=subprocess.run([sys.executable,str(operator.PACKAGE/'scripts/gpu_acceptance_operator.py'),
                '--session',str(self.session_path),argument],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse(output.exists())

    def test_readonly_diagnostic_child_receives_no_credentials_or_model_command(self):
        with (patch.object(subprocess,'check_output',return_value='readonly') as call,
              patch.dict(os.environ,{'HF_TOKEN':'CPU_SECRET','VAST_API_KEY':'CPU_SECRET'})):
            self.assertEqual(operator.command(['nvidia-smi','--version']),'readonly')
        argv,kwargs=call.call_args
        self.assertEqual(argv[0],['nvidia-smi','--version'])
        self.assertNotIn('HF_TOKEN',kwargs['env']);self.assertNotIn('VAST_API_KEY',kwargs['env'])

    def test_real_default_cli_leaves_source_root_and_flags_unchanged_without_echoing_secrets(self):
        template=json.loads(operator.TEMPLATE.read_text());sentinel='CPU_FAKE_SECRET_DO_NOT_PRINT'
        template['quote']['reference']=sentinel;self.write_session(template)
        before=self.session_path.read_bytes()
        result=subprocess.run([sys.executable,str(operator.PACKAGE/'scripts/gpu_acceptance_operator.py'),
            '--session',str(self.session_path)],capture_output=True,text=True,
            env={**os.environ,'HF_TOKEN':sentinel,'VAST_API_KEY':sentinel,'H3_ALLOW_SUBMISSIONS':'1'})
        self.assertNotEqual(result.returncode,0);self.assertNotIn(sentinel,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'BLOCKED')
        self.assertEqual(self.session_path.read_bytes(),before);self.assertFalse(self.root.exists())

    def test_real_cli_rejects_secret_fields_and_linked_session_files(self):
        template=json.loads(operator.TEMPLATE.read_text());sentinel='CPU_FAKE_SECRET_DO_NOT_PRINT'
        template['HF_TOKEN']=sentinel;self.write_session(template)
        for linked in (False,True):
            path=self.session_path
            if linked:
                path=self.base/'linked-session.json';path.symlink_to(self.session_path)
            result=subprocess.run([sys.executable,str(operator.PACKAGE/'scripts/gpu_acceptance_operator.py'),
                '--session',str(path)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('BLOCKED',result.stderr)
            self.assertNotIn(sentinel,result.stdout+result.stderr);self.assertFalse(self.root.exists())

    def test_device_queries_parse_real_csv_shape_and_reject_unknown_or_multiple_devices(self):
        mount=json.dumps({'filesystems':[self.observed['mount']]})
        row='GPU-SYNTHETIC, NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 580.65.06, 97920, 1000, 96920, 12.0\n'
        for rows in (row,'',row+row):
            with patch.object(operator,'command',side_effect=[mount,rows,'','Cuda compilation tools, release 13.0']) as queried:
                if rows!=row:
                    with self.assertRaises(ValueError):operator.observe(self.session)
                else:
                    result=operator.observe(self.session)
                    self.assertEqual(result['gpu']['total_mib'],97920)
                    self.assertEqual(result['gpu_pids'],[])
                    self.assertEqual(result['cuda'],'13.0')
                self.assertTrue(all(call.args[0][0] in {'findmnt','nvidia-smi','nvcc'} for call in queried.call_args_list))


class ProductionKernelBoundaryTests(unittest.TestCase):
    def test_production_group_feature_probe_still_rejects_unsupported_kernel(self):
        with load_controller() as controller:
            for probe in (controller._require_restart_group_control,process_identity.require_group_control):
                for error in (errno.EINVAL,errno.ENOSYS):
                    with (self.subTest(probe=probe.__name__,error=error),
                          patch.object(os,'pidfd_open',return_value=77),patch.object(os,'close'),
                          patch.object(controller.signal,'pidfd_send_signal',side_effect=OSError(error,'unsupported')) as send):
                        with self.assertRaisesRegex(RuntimeError,'6.9'):probe()
                        send.assert_called_once_with(77,0,None,4)

    def test_production_group_feature_probe_remains_required_even_when_supported(self):
        with load_controller() as controller:
            for probe in (controller._require_restart_group_control,process_identity.require_group_control):
                for result in (None,OSError(errno.ESRCH,'not a group leader')):
                    with (self.subTest(probe=probe.__name__,result=result),
                          patch.object(os,'pidfd_open',return_value=77),patch.object(os,'close'),
                          patch.object(controller.signal,'pidfd_send_signal',side_effect=result) as send):
                        probe()
                        send.assert_called_once_with(77,0,None,4)


if __name__=='__main__':unittest.main()
