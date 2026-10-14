"""Explicit real-systemd canary suite: requires an actual built release and Vite.

Invoke separately: P100_TEST_RELEASE_DIR=/.../releases/SHA python3
ops/frontend/tests/real_unit_contract.py. No skip or mock manager fallback.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from transaction_fixture import Fixture
from frontend_release import Controller,RetryRequired
from release_storage import snapshot_managed


class RealUnitContracts(unittest.TestCase):
    def fixture(self,**kwargs):
        value=Fixture(real=True,**kwargs); self.addCleanup(value.cleanup); return value
    def test_real_vite_nginx_vite_with_two_dropins_and_preflight_rejection(self):
        f=self.fixture()
        before=f.controller._managed(); pid=f.original['process']['pid']
        broken={**f.settings,'worker_connections':1}
        bad=Controller(broken,user=True)
        with self.assertRaises(ValueError): bad.apply(f.release,f.stage(),f.override)
        self.assertEqual(f.manager.process(f.manager.properties())['pid'],pid)
        self.assertEqual(f.controller._managed(),before)
        # Exercise nginx -t failure (occupied permission-invalid runtime config)
        # by fault boundary changing only the private candidate file.
        def reject(boundary):
            if boundary=='before_nginx_preflight':
                for candidate in (f.root/'staging').glob('.*/package/preflight.conf'):
                    with candidate.open('a') as out: out.write('\ninvalid_directive on;\n')
        f.controller.fault=reject
        with self.assertRaises(subprocess.CalledProcessError): f.apply()
        self.assertEqual(f.manager.process(f.manager.properties())['pid'],pid)
        self.assertEqual(f.controller._managed(),before)
        f.controller.fault=lambda boundary:None
        f.apply(); applied=f.controller._capture()
        self.assertEqual(applied['http']['sha'],f.manifest['source_sha'])
        self.assertEqual(applied['http']['digest'],f.manifest['files']['index.html'])
        self.assertNotEqual(applied['process']['exe'],f.original['process']['exe'])
        self.assertTrue(f.apply()['idempotent'])
        f.controller.rollback(); restored=f.controller._capture()
        self.assertEqual(restored['http'],f.original['http'])
        self.assertEqual(restored['process']['argv'],f.original['process']['argv'])
        self.assertEqual(snapshot_managed([Path(p) for p in f.original_files]),f.original_files)
        self.assertEqual(restored['properties']['UnitFileState'],f.original['properties']['UnitFileState'])
        self.assertTrue(f.controller.rollback()['idempotent'])
        output=os.environ.get('P100_REAL_EVIDENCE_DIR')
        if output:
            destination=Path(output); destination.mkdir(parents=True,exist_ok=True)
            (destination/'real-roundtrip.json').write_text(json.dumps({'original':f.original,'applied':applied,'restored':restored},indent=2,sort_keys=True))
    def test_helpers_ignore_pythonpath_and_user_site_and_leave_no_children(self):
        f=self.fixture()
        poison=f.root/'poison'; poison.mkdir(mode=0o755)
        marker=f.root/'poison-executed'
        code='from pathlib import Path; Path('+repr(str(marker))+').write_text("unsafe")\nraise RuntimeError("poison import")\n'
        (poison/'sitecustomize.py').write_text(code); (poison/'release_manifest.py').write_text(code)
        inherited=f.controller.dropdir/'30-python-env.conf'
        inherited.write_text('[Service]\nEnvironment=PYTHONPATH='+str(poison)+'\nEnvironment=PYTHONUSERBASE='+str(poison)+'\n')
        inherited.chmod(0o644); f.manager.reload()
        f.apply(); capture=f.controller._capture(); pid=capture['process']['pid']
        cgroup=capture['process']['cgroup'].partition('::')[2].strip()
        pids=Path('/sys/fs/cgroup'+cgroup+'/cgroup.procs')
        children=[int(value) for value in pids.read_text().split()]
        self.assertIn(pid,children); self.assertGreaterEqual(len(children),4)
        self.assertFalse(marker.exists())
        f.controller.rollback()
        for child in children:
            self.assertFalse(Path('/proc',str(child)).exists(),'shim/nginx child leaked after rollback')
        self.assertFalse(marker.exists())

    def test_real_inactive_stays_inactive(self):
        f=self.fixture(active=False); f.apply()
        self.assertEqual(f.manager.properties()['ActiveState'],'inactive')
        f.controller.rollback(); self.assertEqual(f.manager.properties()['ActiveState'],'inactive')
        self.assertFalse(f.controller.drop.exists())
    def cut_cli(self,f,action,boundary):
        # Call the production CLI, injecting only the explicitly exposed fault
        # callback. No fake lifecycle, file or HTTP observer is involved.
        harness='''import os,signal,sys
sys.path.insert(0,sys.argv.pop(1))
import frontend_release
original=frontend_release.Controller
def factory(settings,**kwargs):
    def fault(at):
        if at==os.environ['P100_CUT']: os.kill(os.getpid(),signal.SIGKILL)
    return original(settings,fault=fault,**kwargs)
frontend_release.Controller=factory
raise SystemExit(frontend_release.main())
'''
        arguments=[sys.executable,'-c',harness,str(Path(__file__).resolve().parents[1]),action,'--settings',str(f.settings_path),'--user']
        if action=='apply': arguments+=['--release-dir',str(f.release),'--staging-dir',str(f.stage()),'--override-backend','http://localhost:'+str(f.backend.server_port)]
        result=subprocess.run(arguments,capture_output=True,text=True,env={**os.environ,'P100_CUT':boundary})
        self.assertEqual(result.returncode,-signal.SIGKILL,result.stderr)
    def test_real_sigkill_apply_and_rollback_recover_and_retry(self):
        for action,boundary in (('apply','installed_current'),('apply','after_switch'),('rollback','restore_files')):
            with self.subTest(action=action,boundary=boundary):
                f=self.fixture()
                if action=='rollback': f.apply()
                self.cut_cli(f,action,boundary)
                with self.assertRaises(RetryRequired):
                    f.controller.rollback() if action=='rollback' else f.apply()
                self.assertEqual(f.controller._capture()['http'],f.original['http'])
                self.assertFalse(f.controller.drop.exists())
                if action=='rollback': self.assertTrue(f.controller.rollback()['idempotent'])
                else: f.apply(); f.controller.rollback()

if __name__=='__main__': unittest.main(verbosity=2)
