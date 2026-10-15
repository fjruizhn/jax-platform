"""Regression reproductions of formal B R1 findings; old code must fail them."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch
from transaction_fixture import Fixture
from frontend_release import Controller, RetryRequired
from release_storage import locked
from release_systemd import Manager, same_process

class RoundTwo(unittest.TestCase):
    def fixture(self,**kwargs):
        fixture=Fixture(**kwargs); self.addCleanup(fixture.cleanup); return fixture
    def installed_verify(self,f):
        receipt=json.loads((f.controller.state/'last.json').read_text()); helper=Path(receipt['helper_dir'])
        return subprocess.run(['/usr/bin/python3','-I','-B',str(helper/'runtime_verify.py'),'--settings',str(helper/'settings.json'),
                               '--release-dir',str(f.release),'--config',str(f.controller.config),'--metadata',str(helper/'render.json')],capture_output=True,text=True)
    def test_installed_helper_rejects_current_pointing_at_another_release(self):
        f=self.fixture(); f.apply(); other=f.release.with_name('b'*40); shutil.copytree(f.release,other)
        (other/'dist/index.html').write_bytes(b'ALIEN INDEX')
        f.controller.current.unlink(); f.controller.current.symlink_to('releases/'+other.name)
        self.assertNotEqual(self.installed_verify(f).returncode,0)
    def test_pool_tamper_is_rejected_by_installed_helper_and_idempotency(self):
        for damage in ('bytes','mode','delete'):
            with self.subTest(damage=damage):
                f=self.fixture(); f.apply(); asset=next((f.controller.root/'pool/assets').glob('*'))
                if damage=='bytes': asset.write_bytes(b'CORRUPTED POOL ASSET')
                if damage=='mode': asset.chmod(0o600)
                if damage=='delete': asset.unlink()
                self.assertNotEqual(self.installed_verify(f).returncode,0)
                with self.assertRaises((ValueError,OSError)): f.apply()
    def test_same_sha_new_workers_or_timeout_or_release_path_is_not_idempotent(self):
        for setting,value in (('worker_connections',256),('websocket_read_timeout_seconds',7200)):
            with self.subTest(setting=setting):
                f=self.fixture(); f.apply(); settings={**f.settings,setting:value}
                controller=Controller(settings,user=True,manager=f.manager)
                with self.assertRaisesRegex(ValueError,'rollback'): controller.apply(f.release,f.stage(),f.override)
    def test_effective_execution_path_and_ignore_errors_cannot_be_spoofed(self):
        f=self.fixture(); f.apply(); receipt=json.loads((f.controller.state/'last.json').read_text())
        for field in ('ExecStart','ExecStartPre'):
            for before,after in (('path=/usr/bin/python3','path=/tmp/evil'),('ignore_errors=no','ignore_errors=yes')):
                with self.subTest(field=field,damage=after):
                    properties=f.manager.properties(); properties[field]=properties[field].replace(before,after)
                    self.assertFalse(f.controller._nginx_effective(properties,receipt))
    def test_unloaded_disk_dropin_is_not_accepted_as_origin(self):
        f=self.fixture(); (f.controller.dropdir/'30-unloaded.conf').write_text('[Service]\nEnvironment=ALIEN=1\n')
        original=f.manager.properties
        def dirty(): return {**original(),'NeedDaemonReload':'yes'}
        f.manager.properties=dirty
        with self.assertRaisesRegex(ValueError,'loaded|reload|coher'): f.controller.prepare(f.release,f.stage(),f.override)
        self.assertFalse(f.controller.current.exists()); self.assertFalse(f.controller.state.exists())
    def test_higher_precedence_absent_base_is_in_snapshot(self):
        f=self.fixture(); upper=f.root/'upper'; upper.mkdir(mode=0o755)
        manager=Manager({**f.settings,'process_uid':os.getuid()},True)
        manager.ctl=lambda *args:'UnitPath='+str(upper)+' '+str(f.unit.parent)+'\n'
        before=manager.unit_files(f.manager.properties())
        (upper/f.service).write_text('[Service]\nExecStart=/bin/false\n')
        after=manager.unit_files(f.manager.properties())
        self.assertNotEqual(before,after)
    def test_missing_dropdir_is_not_created_before_prepared_journal(self):
        f=self.fixture()
        for path in f.controller.dropdir.iterdir(): path.unlink()
        f.controller.dropdir.rmdir(); f.manager.reload()
        original=f.controller._assert_static; calls=0
        def reject_second(*args):
            nonlocal calls
            calls+=1
            if calls==2: raise ValueError('foreign second CAS')
            return original(*args)
        f.controller._assert_static=reject_second
        with self.assertRaisesRegex(ValueError,'second CAS'): f.apply()
        self.assertFalse(f.controller.dropdir.exists())
        self.assertFalse((f.controller.state/'journal.json').exists())
    def test_missing_dropdir_is_absent_again_after_rollback(self):
        f=self.fixture()
        for path in f.controller.dropdir.iterdir(): path.unlink()
        f.controller.dropdir.rmdir(); f.manager.reload()
        f.apply(); f.controller.rollback()
        self.assertFalse(f.controller.dropdir.exists())
    def test_alternative_config_roots_contend_for_same_unit_lock(self):
        f=self.fixture(); f.apply(); alternative=f.root/'nginx-other'; alternative.mkdir(mode=0o755)
        settings={**f.settings,'nginx_root':str(alternative)}; settings_path=f.root/'other-settings.json'
        settings_path.write_text(json.dumps(settings)); settings_path.chmod(0o600)
        path=getattr(f.controller,'lock_path',f.controller.state/'lock')
        with locked(path,os.getuid()):
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'frontend_release.py'),'rollback',
                                   '--settings',str(settings_path),'--user'],capture_output=True,text=True)
        self.assertEqual(result.returncode,1); self.assertIn('already locked',result.stderr)

    def test_prepare_refuses_an_unaccredited_original_execstart(self):
        f=self.fixture()
        f.manager.effective['ExecStart']='{ path=/usr/bin/node ; argv[]=/usr/bin/node /tmp/not-vite-malware.js ; ignore_errors=no ; }'
        with self.assertRaisesRegex(ValueError,'original ExecStart'):
            f.controller.prepare(f.release,f.stage(),f.override)

    def test_external_vite_restart_between_prepare_and_cas_is_not_signalled(self):
        f=self.fixture(); original=f.controller.prepare
        def prepare_then_restart(*args,**kwargs):
            result=original(*args,**kwargs)
            f.manager.pid+=1
            return result
        f.controller.prepare=prepare_then_restart
        with self.assertRaisesRegex(ValueError,'CAS process drift'):
            f.apply()
        self.assertFalse(f.controller.drop.exists())

    def test_system_vite_contract_accepts_the_accredited_bin_symlink_chain(self):
        f=self.fixture(); frontend=f.root/'production-frontend'; vite=frontend/'node_modules/vite'
        (vite/'bin').mkdir(parents=True); (frontend/'node_modules/.bin').mkdir()
        entry=vite/'bin/vite.js'; entry.write_text('console.log("vite fixture")\n')
        package=vite/'package.json'; package.write_text('{"version":"6.4.3"}\n')
        lock=frontend/'package-lock.json'; lock.write_text('{"lockfileVersion":3}\n')
        link=frontend/'node_modules/.bin/vite'; link.symlink_to('../vite/bin/vite.js')
        guard=f.root/'checkout-guard'; guard.write_text('#!/bin/sh\nexit 0\n'); guard.chmod(0o755)
        marker=f.root/'README'; marker.write_text('clean\n')
        node=Path(f.node)
        subprocess.run(['git','init','--quiet',str(f.root)],check=True)
        subprocess.run(['git','-C',str(f.root),'add','.'],check=True)
        subprocess.run(['git','-C',str(f.root),'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','fixture'],check=True)
        checkout_sha=subprocess.run(['git','-C',str(f.root),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
        contract={'kind':'system-vite','node_binary':str(node),'checkout':str(f.root),
                  'checkout_branch':'master','working_directory':str(frontend),
                  'entry':str(link),'entry_target':'../vite/bin/vite.js',
                  'entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),
                  'vite_package':str(package),'vite_package_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
                  'node_sha256':hashlib.sha256(node.read_bytes()).hexdigest(),
                  'node_owner_uid':os.getuid(),'node_owner_gid':os.getgid(),'node_mode':'0755',
                  'lockfile':str(lock),'lockfile_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),'argv':['dev','--host','0.0.0.0','--port','5173'],
                  'guard':str(guard),'guard_sha256':hashlib.sha256(guard.read_bytes()).hexdigest(),
                  'guard_owner_uid':os.getuid(),'guard_owner_gid':os.getgid(),'guard_mode':'0755',
                  'exec_start_pre':[{'path':str(guard),'argv':[str(guard),str(f.root)],'ignore_errors':False}],
                  'user':'jaxsvc','group':'jaxsvc'}
        controller=Controller.__new__(Controller); controller.s={**f.settings,'original_contract':contract}; controller.user=False; controller.s['process_uid']=os.getuid()
        start={'path':str(node),'argv':[str(node),str(link),'dev','--host','0.0.0.0','--port','5173'],'ignore_errors':False}
        origin={'properties':{'ExecStart':'{ path='+start['path']+' ; argv[]='+' '.join(start['argv'])+' ; ignore_errors=no ; }',
                              'ExecStartPre':'{ path='+str(guard)+' ; argv[]='+str(guard)+' '+str(f.root)+' ; ignore_errors=no ; }',
                              'WorkingDirectory':str(frontend),'User':'jaxsvc','Group':'jaxsvc'},'process':None}
        controller._validate_original(origin,checkout_sha)
        with self.assertRaisesRegex(ValueError,'checkout source'):
            controller._validate_original(origin,'f'*40)
        for key,value in (('node_sha256','0'*64),('guard_sha256','0'*64),('node_mode','0700'),('guard_owner_uid',1)):
            with self.subTest(key=key):
                controller.s['original_contract']={**contract,key:value}
                with self.assertRaisesRegex(ValueError,'bytes differ'):
                    controller._validate_original(origin,checkout_sha)
        controller.s['original_contract']=contract
        marker.write_text('dirty\n')
        with self.assertRaisesRegex(ValueError,'checkout dirty'):
            controller._validate_original(origin,checkout_sha)

    def test_strict_process_identity_rejects_gid_or_executable_fingerprint_drift(self):
        original={'pid':71,'starttime':'99','uid':1001,'gid':1001,'exe':'/node','exe_dev':1,'exe_ino':2,
                  'exe_sha256':'a'*64,'argv':['/node','vite'],'cgroup':'/unit','boot_id':'boot','invocation_id':'invoke'}
        for key,value in (('gid',1002),('exe_dev',3),('exe_ino',4),('exe_sha256','b'*64),('boot_id','other'),('invocation_id','other')):
            with self.subTest(key=key):
                changed={**original,key:value}
                self.assertFalse(same_process(changed,original))

    def test_each_prepare_allocates_an_immutable_transaction_config(self):
        f=self.fixture(); first=f.stage(); second=f.stage()
        f.controller.prepare(f.release,first,f.override); f.controller.prepare(f.release,second,f.override)
        plans=[json.loads((path/'plan.json').read_text()) for path in (first,second)]
        self.assertNotEqual(plans[0]['txid'],plans[1]['txid'])
        for plan,path in zip(plans,(first,second)):
            self.assertRegex(plan['txid'],r'^[0-9a-f]{32}$')
            self.assertEqual(plan['config_path'],str(f.root/'nginx'/('nginx-'+plan['txid']+'.conf')))
            self.assertIn(plan['config_path'],(path/'dropin.conf').read_text())

    def test_candidate_is_witnessed_across_restart_crash_gaps(self):
        class Cut(BaseException): pass
        for boundary in ('after_restart_before_candidate_capture','after_candidate_capture_before_journal'):
            with self.subTest(boundary=boundary):
                f=self.fixture()
                f.controller.fault=lambda at: (_ for _ in ()).throw(Cut()) if at==boundary else None
                with self.assertRaises(Cut): f.apply()
                f.controller.fault=lambda at: None
                with self.assertRaises(RetryRequired): f.apply()
                receipt=json.loads((f.controller.state/'last.json').read_text())
                self.assertEqual(receipt['phase'],'recovered')
                self.assertIn('candidate_identity',receipt)
                self.assertIn('restored_identity',receipt)
                self.assertEqual(f.controller._capture()['http'],f.original['http'])

    def test_unrelated_nginx_after_durable_candidate_is_never_stopped(self):
        class Cut(BaseException): pass
        f=self.fixture(); f.controller.fault=lambda at: (_ for _ in ()).throw(Cut()) if at=='after_switch' else None
        with self.assertRaises(Cut): f.apply()
        f.controller.fault=lambda at: None
        f.manager.pid+=100  # different InvocationID, PID/starttime and process identity
        stops=f.manager.stop_calls
        with self.assertRaises(ValueError): f.apply()
        self.assertEqual(f.manager.stop_calls,stops)

    def test_pre_restart_barrier_rejects_external_vite_without_signal(self):
        f=self.fixture()
        def drift(boundary):
            if boundary=='switch_intent_persisted':
                f.manager.pid+=100  # new InvocationID/starttime after durable intent
        f.controller.fault=drift; restarts=f.manager.restart_calls; stops=f.manager.stop_calls
        with self.assertRaisesRegex(RuntimeError,'original process differs'):
            f.apply()
        self.assertEqual(f.manager.restart_calls,restarts)
        self.assertEqual(f.manager.stop_calls,stops)

    def test_candidate_witness_precedes_http_postcheck(self):
        f=self.fixture()
        def bad_http(boundary):
            if boundary=='after_restart_before_candidate_capture': f.manager.bad_http=True
        f.controller.fault=bad_http
        with self.assertRaisesRegex(RuntimeError,'HTTP.*restored'):
            f.apply()
        receipt=json.loads((f.controller.state/'last.json').read_text())
        self.assertIn('candidate_identity',receipt)
        self.assertEqual(f.controller._capture()['http'],f.original['http'])

    def test_legacy_config_is_snapshotted_but_never_reinstalled(self):
        f=self.fixture(); legacy=f.controller.configroot/'nginx.conf'; legacy.write_text('human legacy')
        before=legacy.stat(); payload=legacy.read_bytes()
        f.apply(); after=legacy.stat()
        self.assertEqual(legacy.read_bytes(),payload)
        self.assertEqual((after.st_dev,after.st_ino,after.st_mtime_ns),(before.st_dev,before.st_ino,before.st_mtime_ns))

    def test_candidate_gid_or_executable_hash_refuses_before_restore_signal(self):
        for field in ('bad_gid','bad_hash'):
            with self.subTest(field=field):
                f=self.fixture()
                def drift(boundary,field=field):
                    if boundary=='after_restart_before_candidate_capture': setattr(f.manager,field,True)
                f.controller.fault=drift; stops=f.manager.stop_calls
                with self.assertRaisesRegex(RuntimeError,'candidate .* differs'):
                    f.apply()
                self.assertEqual(f.manager.stop_calls,stops)

    def test_candidate_from_another_boot_cannot_authorize_restore_signal(self):
        f=self.fixture(); process=f.manager.process; stops=f.manager.stop_calls
        def other_boot(properties):
            value=process(properties)
            if value is not None and f.manager.process_kind=='nginx': value={**value,'boot_id':'other-boot'}
            return value
        f.manager.process=other_boot
        with self.assertRaisesRegex(RuntimeError,'candidate boot identity differs'):
            f.apply()
        self.assertEqual(f.manager.stop_calls,stops)

    def test_rollback_rechecks_loaded_dropins_after_reload_before_restart(self):
        f=self.fixture(); f.apply(); restarts=f.manager.restart_calls
        def drift(boundary):
            if boundary=='restore_reloaded':
                (f.controller.dropdir/'40-external.conf').write_text('[Service]\nEnvironment=ALIEN=1\n')
                f.manager.reload()
        f.controller.fault=drift
        with self.assertRaisesRegex(RuntimeError,'base/dropins drift before restart'):
            f.controller.rollback()
        self.assertEqual(f.manager.restart_calls,restarts)

    def test_rollback_rechecks_process_immediately_before_restart(self):
        f=self.fixture(); f.apply(); restarts=f.manager.restart_calls
        def drift(boundary):
            if boundary=='restart_intent_persisted':
                f.manager.active='active'; f.manager.process_kind='vite'; f.manager.pid+=100
        f.controller.fault=drift
        with self.assertRaisesRegex(RuntimeError,'external process before restart'):
            f.controller.rollback()
        self.assertEqual(f.manager.restart_calls,restarts)

    def test_rollback_witnesses_exact_original_already_active_before_restart(self):
        f=self.fixture(); f.apply(); restarts=f.manager.restart_calls; original=copy.deepcopy(f.original['process'])
        def drift(boundary):
            if boundary=='restart_intent_persisted':
                f.manager.active='active'; f.manager.process_kind='vite'; f.manager.pid=original['pid']
        f.controller.fault=drift
        self.assertEqual(f.controller.rollback(),{'rolled_back':True,'idempotent':False})
        self.assertEqual(f.manager.restart_calls,restarts)
        receipt=json.loads((f.controller.state/'last.json').read_text())
        self.assertTrue(receipt['restore_without_signal'])
        self.assertEqual(receipt['restored_identity'],original)
        self.assertEqual(f.controller.rollback(),{'rolled_back':True,'idempotent':True})

    def test_restart_intent_exact_original_witness_survives_restore_service_cut(self):
        class Cut(BaseException): pass
        f=self.fixture(); f.apply(); original=copy.deepcopy(f.original['process'])
        def cut(boundary):
            if boundary=='restart_intent_persisted':
                f.manager.active='active'; f.manager.process_kind='vite'; f.manager.pid=original['pid']
            if boundary=='restore_service': raise Cut()
        f.controller.fault=cut
        with self.assertRaises(Cut): f.controller.rollback()
        journal=json.loads((f.controller.state/'journal.json').read_text())
        self.assertEqual(journal['state'],'RESTORED_WITNESSED')
        self.assertTrue(journal['restore_without_signal'])
        self.assertEqual(journal['restored_identity'],original)
        stops,restarts=f.manager.stop_calls,f.manager.restart_calls
        f.controller.fault=lambda boundary:None
        with self.assertRaises(RetryRequired): f.controller.rollback()
        self.assertEqual((f.manager.stop_calls,f.manager.restart_calls),(stops,restarts))
        self.assertTrue(f.controller.rollback()['idempotent'])

    def test_restart_intent_exact_original_before_witness_never_signals_on_retry(self):
        class Cut(BaseException): pass
        f=self.fixture(); f.apply(); original=copy.deepcopy(f.original['process'])
        def cut(boundary):
            if boundary=='restart_intent_persisted':
                f.manager.active='active'; f.manager.process_kind='vite'; f.manager.pid=original['pid']
            if boundary=='exact_original_before_witness': raise Cut()
        f.controller.fault=cut
        with self.assertRaises(Cut): f.controller.rollback()
        journal=json.loads((f.controller.state/'journal.json').read_text())
        self.assertEqual(journal['state'],'RESTART_INTENT')
        self.assertFalse(journal['restore_without_signal'])
        stops,restarts=f.manager.stop_calls,f.manager.restart_calls
        f.controller.fault=lambda boundary:None
        with self.assertRaises(RetryRequired): f.controller.rollback()
        self.assertEqual((f.manager.stop_calls,f.manager.restart_calls),(stops,restarts))
        persisted=json.loads((f.controller.state/'journal.json').read_text())
        self.assertTrue(persisted['restore_without_signal'])
        self.assertTrue(f.controller.rollback()['idempotent'])

    def test_candidate_capture_waits_for_nginx_stable_argv(self):
        f=self.fixture(); f.manager.process_kind='nginx'; f.manager.config=f.controller.config
        capture=f.controller._capture; calls=0
        def transitional(*args,**kwargs):
            nonlocal calls
            value=capture(*args,**kwargs); calls+=1
            if calls==1:
                value={**value,'process':{**value['process'],'argv':[f.settings['nginx_binary'],'-c',str(f.controller.config)]}}
            return value
        f.controller._capture=transitional
        observed=f.controller._candidate_capture()
        self.assertGreaterEqual(calls,2)
        self.assertEqual(observed['process']['argv'],[f.settings['nginx_binary'],'-g','daemon off;','-c',str(f.controller.config)])

    def test_candidate_process_identity_is_durable_before_commit(self):
        f=self.fixture(); f.apply()
        receipt=json.loads((f.controller.state/'last.json').read_text())
        self.assertEqual(receipt['candidate_identity'],f.controller._capture()['process'])

    def test_external_vite_restart_after_switch_intent_is_not_stopped_in_recovery(self):
        class Cut(BaseException): pass
        f=self.fixture()
        f.controller.fault=lambda boundary: (_ for _ in ()).throw(Cut()) if boundary=='after_switch' else None
        with self.assertRaises(Cut): f.apply()
        f.controller.fault=lambda boundary: None
        f.manager.process_kind='vite'; f.manager.pid+=100
        stops=f.manager.stop_calls
        with self.assertRaises((ValueError,RetryRequired)): f.apply()
        self.assertEqual(f.manager.stop_calls,stops)

    def test_ready_to_switch_refuses_each_live_drift_before_restart(self):
        """The durable SWITCH_INTENT is not a licence to restart after drift."""
        for kind in ('current','pool','drop','need-reload'):
            with self.subTest(kind=kind):
                f=self.fixture(); restarts=f.manager.restart_calls; stops=f.manager.stop_calls
                original_properties=f.manager.properties
                def drift(boundary,kind=kind):
                    if boundary!='switch_intent_persisted': return
                    if kind=='current':
                        f.controller.current.unlink(); f.controller.current.symlink_to('releases/'+'f'*40)
                    elif kind=='pool':
                        next(path for path in (f.controller.root/'pool/assets').rglob('*') if path.is_file()).write_bytes(b'drift')
                    elif kind=='drop': f.controller.drop.write_text('human drift')
                    else: f.manager.properties=lambda: {**original_properties(),'NeedDaemonReload':'yes'}
                f.controller.fault=drift
                with self.assertRaisesRegex(RuntimeError,'drift|mismatch|reload|restoration ALSO FAILED'):
                    f.apply()
                self.assertEqual(f.manager.restart_calls,restarts)
                self.assertEqual(f.manager.stop_calls,stops)

    def test_recovery_refuses_non_prefix_mixture_without_signal(self):
        class Cut(BaseException): pass
        f=self.fixture()
        f.controller.fault=lambda boundary: (_ for _ in ()).throw(Cut()) if boundary=='journal_files_installed' else None
        with self.assertRaises(Cut): f.apply()
        receipt=json.loads((f.controller.state/'journal.json').read_text())
        # Keep later writes but remove an earlier one: every individual file
        # is old or new, yet the global prefix is impossible.
        f.controller.config.unlink(); f.controller.fault=lambda boundary: None
        stops=f.manager.stop_calls; restarts=f.manager.restart_calls
        with self.assertRaisesRegex(ValueError,'unknown managed drift'):
            f.apply()
        self.assertEqual(f.manager.stop_calls,stops)
        self.assertEqual(f.manager.restart_calls,restarts)
        self.assertEqual(receipt['state'],'FILES_INSTALLED')

    def test_stop_intent_binds_one_prefix_and_refuses_later_file_drift(self):
        f=self.fixture(); stops=f.manager.stop_calls
        def fault(boundary):
            if boundary=='after_switch': f.manager.bad_http=True
            if boundary=='stop_intent_persisted': f.controller.config.unlink()
        f.controller.fault=fault
        with self.assertRaisesRegex(RuntimeError,'unknown managed drift|restoration ALSO FAILED'):
            f.apply()
        self.assertEqual(f.manager.stop_calls,stops)

    def test_stop_intent_refuses_unloaded_base_touch_before_signal(self):
        f=self.fixture(); stops=f.manager.stop_calls
        original_properties=f.manager.properties
        def fault(boundary):
            if boundary=='after_switch': f.manager.bad_http=True
            if boundary=='stop_intent_persisted':
                f.manager.properties=lambda: {**original_properties(),'NeedDaemonReload':'yes'}
        f.controller.fault=fault
        with self.assertRaisesRegex(RuntimeError,'reload|restoration ALSO FAILED'):
            f.apply()
        self.assertEqual(f.manager.stop_calls,stops)

    def test_switch_intent_cut_with_original_vite_recovers_without_signal(self):
        class Cut(BaseException): pass
        f=self.fixture(); stops=f.manager.stop_calls; restarts=f.manager.restart_calls
        f.controller.fault=lambda boundary: (_ for _ in ()).throw(Cut()) if boundary=='switch_intent_persisted' else None
        with self.assertRaises(Cut): f.apply()
        f.controller.fault=lambda boundary: None
        with self.assertRaises(RetryRequired): f.apply()
        self.assertEqual(f.manager.stop_calls,stops)
        self.assertEqual(f.manager.restart_calls,restarts)
        self.assertEqual(f.controller._capture()['http'],f.original['http'])

if __name__=='__main__': unittest.main()

class OriginContractTests(unittest.TestCase):
    def test_source_contract_is_bound_to_sha_and_immutable_file(self):
        from origin_contract import load
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)/'origin.json'; source='a'*40
            payload={'schema_version':1,'source_sha':source,'entry_target':'../vite/bin/vite.js',
                     'entry_sha256':'1'*64,'vite_package_sha256':'2'*64,'lockfile_sha256':'3'*64}
            path.write_text(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n'); path.chmod(0o644)
            loaded,digest=load(path,source,owner_uid=os.getuid())
            self.assertEqual(loaded,payload); self.assertEqual(len(digest),64)
            with self.assertRaisesRegex(ValueError,'audited source'):
                load(path,'b'*40,owner_uid=os.getuid())
            path.chmod(0o666)
            with self.assertRaisesRegex(ValueError,'owner or mode'):
                load(path,source,owner_uid=os.getuid())

    def test_generator_never_overwrites_existing_contract(self):
        from origin_contract import generate
        with tempfile.TemporaryDirectory() as raw:
            output=Path(raw)/'origin.json'; output.write_text('old')
            with self.assertRaises(FileExistsError):
                generate(Path(raw),'a'*40,output,{'original_contract':{}})
            self.assertEqual(output.read_text(),'old')


if __name__=='__main__': unittest.main()
