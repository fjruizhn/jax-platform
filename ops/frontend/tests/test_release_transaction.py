"""Transactions with actual files/preflights and injected manager/fault boundaries."""
import copy
import json
import os
from pathlib import Path
import signal
import sys
import unittest
from unittest.mock import patch
from transaction_fixture import Fixture
from frontend_release import Controller, RetryRequired
from release_storage import fingerprint, locked, snapshot_managed


class Transactions(unittest.TestCase):
    def fixture(self,**kwargs):
        value=Fixture(**kwargs); self.addCleanup(value.cleanup); return value
    def test_prepare_is_pure_repeatable_and_failures_keep_original(self):
        f=self.fixture(); original=f.controller._managed()
        for iteration in range(2): f.controller.prepare(f.release,f.stage(),f.override)
        self.assertEqual(f.controller._managed(),original); self.assertFalse(f.controller.state.exists())
        for boundary in ('before_nginx_preflight','before_systemd_preflight'):
            def fail(at):
                if at==boundary: raise RuntimeError('preflight rejected')
            c=Controller(f.settings,user=True,manager=f.manager,fault=fail)
            with self.assertRaisesRegex(RuntimeError,'preflight rejected'): c.prepare(f.release,f.stage(),f.override)
            self.assertEqual(c._managed(),original); self.assertEqual(c._capture(),f.original)
            self.assertFalse(c.state.exists())
    def test_apply_rollback_idempotency_preserves_original_snapshot(self):
        f=self.fixture(); f.apply(); last=f.controller.state/'last.json'; origin=json.loads(last.read_text())['origin']
        self.assertTrue(f.apply()['idempotent']); self.assertEqual(json.loads(last.read_text())['origin'],origin)
        self.assertTrue(f.controller.rollback()['rolled_back']); self.assertTrue(f.controller.rollback()['idempotent'])
        self.assertEqual(f.controller._capture()['http'],f.original['http'])
        self.assertEqual(snapshot_managed([Path(p) for p in f.original_files]),f.original_files)
        self.assertFalse(f.controller.current.exists()); self.assertFalse(f.controller.drop.exists())
    def test_different_sha_is_rejected_while_applied(self):
        f=self.fixture(); f.apply(); second=f.release.with_name('a'*40)
        import shutil,hashlib
        shutil.copytree(f.release,second)
        manifest=json.loads((second/'manifest.json').read_text()); manifest['source_sha']=second.name
        (second/'manifest.json').write_text(json.dumps(manifest)); (second/'manifest.json').chmod(0o644)
        before=f.controller._managed()
        with self.assertRaisesRegex(ValueError,'rollback required'): f.controller.apply(second,f.stage(),f.override)
        self.assertEqual(f.controller._managed(),before)
    def test_enabled_state_is_preserved_for_active_and_inactive_origins(self):
        for active in (True,False):
            with self.subTest(active=active):
                f=self.fixture(active=active); f.manager.enabled='enabled'
                f.apply(); self.assertEqual(f.manager.enabled,'enabled')
                f.controller.rollback(); self.assertEqual(f.manager.enabled,'enabled')
                self.assertEqual(f.manager.active,'active' if active else 'inactive')

    def test_cut_after_commit_is_idempotent_without_losing_vite_origin(self):
        f=self.fixture()
        class Cut(BaseException): pass
        def cut(at):
            if at=='journal_committed': raise Cut()
        f.controller.fault=cut
        with self.assertRaises(Cut): f.apply()
        f.controller.fault=lambda at:None
        self.assertTrue(f.apply()['idempotent'])
        f.controller.rollback()
        self.assertEqual(f.controller._capture()['http'],f.original['http'])

    def test_foreign_process_uid_is_refused_before_any_staging(self):
        f=self.fixture(); original=f.manager.process
        def wrong(properties):
            process=original(properties)
            if process: process['uid']=65534
            return process
        f.manager.process=wrong
        with self.assertRaisesRegex(ValueError,'original MainPID'): f.controller.prepare(f.release,f.stage(),f.override)
        self.assertFalse(f.controller.state.exists())

    def test_other_process_cannot_acquire_cli_lock(self):
        import subprocess
        f=self.fixture(); f.apply()
        with locked(f.controller.lock_path,os.getuid()):
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'frontend_release.py'),
                                   'rollback','--settings',str(f.settings_path),'--user'],capture_output=True,text=True)
        self.assertEqual(result.returncode,1); self.assertIn('already locked',result.stderr)
        self.assertTrue(f.controller.drop.exists())

    def test_inactive_unit_is_not_started_by_apply_or_rollback(self):
        f=self.fixture(active=False); f.apply(); self.assertEqual(f.manager.active,'inactive')
        f.controller.rollback(); self.assertEqual(f.manager.active,'inactive'); self.assertEqual(f.manager.pid,400)
    def test_postcheck_wrong_http_is_active_but_restored(self):
        f=self.fixture()
        def wrong(boundary):
            if boundary=='after_switch': f.manager.bad_http=True
        f.controller.fault=wrong
        with self.assertRaisesRegex(RuntimeError,'HTTP.*restored'): f.apply()
        self.assertEqual(f.controller._capture()['http'],f.original['http']); self.assertFalse(f.controller.drop.exists())
    def test_observed_vite_process_with_active_nginx_unit_is_restored(self):
        f=self.fixture()
        def old_process(boundary):
            if boundary=='after_switch': f.manager.process_kind='vite'
        f.controller.fault=old_process; stops=f.manager.stop_calls
        with self.assertRaisesRegex(RuntimeError,'MainPID/argv mismatch; restoration ALSO FAILED.*unknown process'): f.apply()
        self.assertTrue(f.controller.drop.exists())
        self.assertEqual(f.manager.active,'active')
        self.assertEqual(f.manager.stop_calls,stops)
        journal=json.loads((f.controller.state/'journal.json').read_text())
        self.assertEqual(journal['phase'],'service_switched')
        self.assertTrue(journal['switch_intent'])

    def test_postcheck_wrong_mainpid_is_active_but_no_unknown_signal(self):
        f=self.fixture()
        def wrong(boundary):
            if boundary=='after_switch': f.manager.bad_process=True
        f.controller.fault=wrong
        with self.assertRaisesRegex(RuntimeError,'ALSO FAILED.*unknown process'): f.apply()
        self.assertTrue(f.controller.drop.exists()); self.assertEqual(f.manager.active,'active')
        # A human resolving unknown process is necessary; do not signal it.
    def test_dist_tamper_and_version_mismatch_are_preflight_errors(self):
        f=self.fixture(); (f.release/'dist/index.html').write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError,'inventory mismatch'): f.apply()
        self.assertFalse(f.controller.drop.exists()); self.assertEqual(f.controller._capture(),f.original)
        f.settings['nginx_version']='0.0.0'; c=Controller(f.settings,user=True,manager=f.manager)
        with self.assertRaisesRegex(ValueError,'version mismatch'): c.prepare(f.release,f.stage(),f.override)
    def test_rollback_cas_refuses_file_dropin_base_effective_active_enabled_drift(self):
        for kind in ('config','base','newdrop','drop','effective','active','enabled'):
            with self.subTest(kind=kind):
                f=self.fixture(); f.apply()
                if kind=='config': f.controller.config.write_text('alien')
                if kind=='base': f.unit.write_text('alien')
                if kind=='newdrop': (f.controller.dropdir/'30-alien.conf').write_text('alien')
                if kind=='drop': f.controller.drop.write_text('alien')
                if kind=='effective': f.manager.effective['WorkingDirectory']='/alien'
                if kind=='active': f.manager.active='inactive'
                if kind=='enabled': f.manager.enabled='enabled'
                before=f.controller._managed(); pid=f.manager.pid
                with self.assertRaises(ValueError): f.controller.rollback()
                self.assertEqual(f.controller._managed(),before); self.assertEqual(f.manager.pid,pid)
    def test_lock_is_shared_by_apply_and_rollback(self):
        f=self.fixture(); f.apply()
        with locked(f.controller.lock_path,os.getuid()):
            for operation in (f.apply,f.controller.rollback):
                with self.assertRaisesRegex(ValueError,'locked'): operation()
    def test_all_apply_write_and_phase_crashes_recover_once_and_retry(self):
        boundaries=['journal_prepared','installed_config','installed_zz-nginx-estatico.conf','installed_current',
                    'journal_files_installed','after_reload','journal_daemon_reloaded','after_switch',
                    'journal_service_switched','journal_verified','after_last']
        class Cut(BaseException): pass
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                f=self.fixture()
                def cut(at):
                    if at==boundary or (boundary=='installed_config' and at.startswith('installed_nginx-')): raise Cut()
                f.controller.fault=cut
                with self.assertRaises(Cut): f.apply()
                f.controller.fault=lambda at:None
                with self.assertRaisesRegex(RetryRequired,'retry explicitly'): f.apply()
                self.assertEqual(f.controller._capture()['http'],f.original['http'])
                self.assertFalse(f.controller.drop.exists())
                f.apply(); f.controller.rollback()
    def test_all_rollback_crashes_recover_once_without_restart_loop(self):
        class Cut(BaseException): pass
        for boundary in ('journal_prepared','restore_stopped','restored_config','restored_zz-nginx-estatico.conf','restored_current','restore_files','restore_reload_intent_persisted','restore_reloaded','restore_service',
                         'journal_files_installed','journal_daemon_reloaded','journal_service_switched','journal_verified','journal_rolled_back'):
            with self.subTest(boundary=boundary):
                f=self.fixture(); f.apply()
                def cut(at):
                    if at==boundary or (boundary=='restored_config' and at.startswith('restored_nginx-')): raise Cut()
                f.controller.fault=cut
                with self.assertRaises(Cut): f.controller.rollback()
                f.controller.fault=lambda at:None
                if boundary=='journal_rolled_back':
                    f.controller.rollback()
                else:
                    with self.assertRaises(RetryRequired): f.controller.rollback()
                self.assertTrue(f.controller.rollback()['idempotent'])
                self.assertEqual(f.controller._capture()['http'],f.original['http'])
    def test_base_drift_between_prepare_and_apply_is_not_overwritten(self):
        f=self.fixture()
        original=f.controller.prepare
        def prepare_then_drift(*args,**kwargs):
            result=original(*args,**kwargs)
            f.unit.write_text('human replacement')
            return result
        f.controller.prepare=prepare_then_drift
        with self.assertRaisesRegex(ValueError,'NeedDaemonReload|base/dropins drift'): f.apply()
        self.assertEqual(f.unit.read_text(),'human replacement')
        self.assertFalse(f.controller.drop.exists())
        self.assertFalse((f.controller.state/'journal.json').exists())

    def test_applied_helper_drift_is_refused_before_signals(self):
        f=self.fixture(); f.apply()
        receipt=json.loads((f.controller.state/'last.json').read_text())
        helper=Path(receipt['helper_dir'])/'runtime_verify.py'; helper.write_text('human replacement')
        pid=f.manager.pid
        with self.assertRaisesRegex(ValueError,'helper drift'): f.controller.rollback()
        self.assertEqual(f.manager.pid,pid); self.assertTrue(f.controller.drop.exists())

    def test_backend_override_metadata_preserves_original_and_digest(self):
        f=self.fixture(); package=f.stage(); f.controller.prepare(f.release,package,f.override)
        metadata=json.loads((package/'render.json').read_text())
        self.assertEqual(metadata['upstream_original'],{'scheme':'http','host':'localhost','port':8080})
        self.assertEqual(metadata['override_backend'],f.override)
        import hashlib
        self.assertEqual(metadata['config_sha256'],hashlib.sha256((package/'nginx.conf').read_bytes()).hexdigest())

    def test_rollback_restart_failure_remains_recoverable(self):
        f=self.fixture(); f.apply(); restart=f.manager.restart
        def fail(): raise RuntimeError('restart failed')
        f.manager.restart=fail
        with self.assertRaisesRegex(RuntimeError,'rollback incomplete'): f.controller.rollback()
        f.manager.restart=restart
        with self.assertRaises(RetryRequired): f.controller.rollback()
        self.assertTrue(f.controller.rollback()['idempotent'])
        self.assertEqual(f.controller._capture()['http'],f.original['http'])

    def test_recovery_unknown_drift_does_not_signal(self):
        class Cut(BaseException): pass
        f=self.fixture()
        def cut(at):
            if at=='journal_files_installed': raise Cut()
        f.controller.fault=cut
        with self.assertRaises(Cut): f.apply()
        f.controller.fault=lambda at:None; f.controller.config.write_text('human change'); oldpid=f.manager.pid
        with self.assertRaisesRegex(ValueError,'unknown managed drift'): f.apply()
        self.assertEqual(f.controller.config.read_text(),'human change'); self.assertEqual(f.manager.pid,oldpid)

if __name__=='__main__': unittest.main()
