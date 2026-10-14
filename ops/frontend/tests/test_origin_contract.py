"""Portable integration tests for source-derived origin contracts."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from origin_contract import _manager, canonical, generate, install_contract, load, load_activation, settings_digest, validate_activation


class ProductionManagerContract(unittest.TestCase):
    def test_activation_cli_normalizes_root_owner_for_manager_unit_files(self):
        settings={'service':'jax-platform-frontend.service'}
        with patch('release_systemd.Manager') as manager:
            _manager(settings)
        manager.assert_called_once_with({'service':'jax-platform-frontend.service','owner_uid':0},False)
        with self.assertRaisesRegex(ValueError,'production frontend'):
            _manager({'service':'p100-test.service','private_mode':True})


class OriginContractIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='p100-origin-contract-test-')
        self.root=Path(self.temporary.name); self.root.chmod(0o700)
        self.checkout=self.root/'checkout'; self.checkout.mkdir()
        self.frontend=self.checkout/'frontend'; self.frontend.mkdir()
        self.node=self.root/'node'; self.npm=self.root/'npm-cli'
        self.node.write_text('#!/bin/sh\nexec "$@"\n'); self.node.chmod(0o755)
        self.npm.write_text('''#!/bin/sh
if [ "$1" = --version ]; then echo 11.13.0; exit 0; fi
if [ "$1" != ci ]; then exit 64; fi
set -eu
value=$(cat package-lock.json)
mkdir -p node_modules/vite/bin node_modules/.bin
printf 'vite:%s' "$value" > node_modules/vite/bin/vite.js
printf '{"name":"vite","source":"%s"}' "$value" > node_modules/vite/package.json
ln -s ../vite/bin/vite.js node_modules/.bin/vite
'''); self.npm.chmod(0o755)
        self._write_source('S1'); self._git('init','--quiet'); self._git('add','.'); self._git('config','user.name','test')
        self._git('config','user.email','test@example.invalid'); self._git('commit','--quiet','-m','S1')
        self.s1=self._git('rev-parse','HEAD').strip()
        self.settings={'npm_version':'11.13.0','original_contract':self._contract()}

    def tearDown(self): self.temporary.cleanup()

    def _git(self,*args):
        return subprocess.run(['git','-C',str(self.checkout),*args],check=True,capture_output=True,text=True).stdout

    def _write_source(self,value):
        (self.frontend/'package-lock.json').write_text(value+'\n')
        (self.frontend/'package.json').write_text('{"name":"fixture"}\n')

    def _pin(self,path,prefix):
        info=path.stat()
        return {prefix+'_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),prefix+'_owner_uid':info.st_uid,
                prefix+'_owner_gid':info.st_gid,prefix+'_mode':format(info.st_mode & 0o777,'04o')}

    def _contract(self):
        return {'node_binary':str(self.node),'npm_cli':str(self.npm),**self._pin(self.node,'node'),**self._pin(self.npm,'npm_cli')}

    def test_archive_source_pins_replay_and_tool_mutation(self):
        # Dirty bytes must not enter the archive for S1.
        self._write_source('DIRTY')
        first=self.root/'s1.json'; payload=generate(self.checkout,self.s1,first,self.settings)
        self.assertEqual(payload['source_sha'],self.s1)
        self.assertEqual(payload['entry_sha256'],hashlib.sha256(b'vite:S1').hexdigest())
        self.assertEqual(first.read_text(),canonical(payload))
        loaded,digest=load(first,self.s1,owner_uid=os.getuid())
        self.assertEqual(loaded,payload); self.assertEqual(digest,hashlib.sha256(first.read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError): generate(self.checkout,self.s1,first,self.settings)

        # S2 is a different audited object: S1's contract cannot be replayed.
        self._write_source('S2'); self._git('add','.'); self._git('commit','--quiet','-m','S2')
        s2=self._git('rev-parse','HEAD').strip(); second=self.root/'s2.json'
        self.assertNotEqual(s2,self.s1); second_payload=generate(self.checkout,s2,second,self.settings)
        self.assertEqual(second_payload['source_sha'],s2)
        with self.assertRaisesRegex(ValueError,'audited source'): load(first,s2,owner_uid=os.getuid())

        # Both executable pins are checked before Git/npm can become authority.
        self.node.write_text('#!/bin/sh\nexit 0\n'); self.node.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'node pin differs'):
            generate(self.checkout,s2,self.root/'node-mutated.json',self.settings)
        self.node.write_text('#!/bin/sh\nexec "$@"\n'); self.node.chmod(0o755)
        self.npm.write_text('#!/bin/sh\nexit 0\n'); self.npm.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'npm_cli pin differs'):
            generate(self.checkout,s2,self.root/'npm-mutated.json',self.settings)

    def test_generated_contract_survives_partial_writes(self):
        original_write=os.write
        calls=[]
        def short_write(descriptor,data):
            calls.append(len(data))
            return original_write(descriptor,data[:7])
        output=self.root/'short-write.json'
        with patch('origin_contract.os.write',side_effect=short_write):
            payload=generate(self.checkout,self.s1,output,self.settings)
        self.assertGreater(len(calls),1)
        self.assertEqual(output.read_text(),canonical(payload))
        self.assertEqual(load(output,self.s1,owner_uid=os.getuid())[0],payload)

    def test_install_contract_validates_operator_source_before_root_publication(self):
        source=self.root/'source.json'
        payload=generate(self.checkout,self.s1,source,self.settings)
        with self.assertRaisesRegex(ValueError,'requires UID0'):
            install_contract(source,self.root/'installed.json',self.s1)
        with patch('origin_contract.os.geteuid',return_value=0), patch('origin_contract._root_publish',return_value=payload) as publish:
            self.assertEqual(install_contract(source,self.root/'installed.json',self.s1),payload)
        publish.assert_called_once_with(self.root/'installed.json',payload)
        with patch('origin_contract.os.geteuid',return_value=0), patch('origin_contract._root_publish') as publish:
            with self.assertRaisesRegex(ValueError,'audited source'):
                install_contract(source,self.root/'wrong.json','b'*40)
        publish.assert_not_called()


class ActivationReceiptContract(unittest.TestCase):
    """I0/I1 must be immutable evidence, not a vague restart assertion."""
    source='a'*40

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='p100-activation-contract-')
        self.root=Path(self.temporary.name)
        self.settings={'service':'jax-platform-frontend.service','node_version':'24.16.0'}
        self.contract_sha='1'*64
        self.i0={'schema_version':1,'kind':'vite-i0','source_sha':self.source,
                 'contract_sha256':self.contract_sha,'settings_sha256':settings_digest(self.settings),
                 'checkout_sha':self.source,'capture':self.capture('i0',100,'10')}
        self.i1={'schema_version':1,'kind':'vite-activation','source_sha':self.source,
                 'contract_sha256':self.contract_sha,'settings_sha256':settings_digest(self.settings),
                 'i0_sha256':hashlib.sha256(canonical(self.i0).encode()).hexdigest(),'i0':self.i0,
                 'i1':self.capture('i1',101,'11')}
        self.path=self.root/'activation.json'

    def tearDown(self): self.temporary.cleanup()

    def capture(self,invocation,pid,starttime):
        process={'pid':pid,'uid':123,'gid':123,'exe':'/node','exe_dev':1,'exe_ino':2,
                 'exe_sha256':'2'*64,'argv':['/node','/vite'],'starttime':starttime,
                 'cgroup':'/service','boot_id':'boot-A','invocation_id':invocation}
        return {'properties':{'InvocationID':invocation,'ActiveState':'active'},'files':{'/unit':{'sha256':'3'*64}},
                'process':process,'unit_paths':['/units'],'http':{'status':200,'mime':'text/html','sha':None,'digest':'4'*64}}

    def write(self,value):
        self.path.write_text(canonical(value)); self.path.chmod(0o600)

    def test_activation_requires_i0_i1_and_current_exactly_matches_i1(self):
        self.write(self.i1)
        # Old receipts had only one post-restart process and accepted this
        # incomplete object.  The activation contract requires both captures.
        loaded,_=load_activation(self.path,self.source,owner_uid=os.getuid(),settings=self.settings)
        self.assertEqual(loaded,self.i1)
        self.assertEqual(validate_activation(self.path,self.source,self.settings,self.i1['i1'],owner_uid=os.getuid()),self.i1)
        live=self.capture('H',99,'9')
        with self.assertRaisesRegex(ValueError,'current Vite capture differs'):
            validate_activation(self.path,self.source,self.settings,live,owner_uid=os.getuid())

    def test_activation_rejects_tampered_digest_and_same_restart_identity(self):
        damaged={**self.i1,'contract_sha256':'f'*64}; self.write(damaged)
        with self.assertRaisesRegex(ValueError,'I0|activation receipt'):
            load_activation(self.path,self.source,owner_uid=os.getuid(),settings=self.settings)
        unchanged={**self.i1,'i1':self.capture('i0',101,'10')}; self.write(unchanged)
        with self.assertRaisesRegex(ValueError,'restart identity'):
            load_activation(self.path,self.source,owner_uid=os.getuid(),settings=self.settings)

    def test_activation_requires_the_published_0600_mode(self):
        self.write(self.i1)
        self.path.chmod(0o640)
        with self.assertRaisesRegex(ValueError,'owner or mode'):
            load_activation(self.path,self.source,owner_uid=os.getuid(),settings=self.settings)


class _PrivateActivationReceiptMoved:
    """Exercise root receipt publication around a live, disposable user Vite."""
    def setUp(self):
        from transaction_fixture import Fixture
        self.fixture=Fixture(real=True); self.addCleanup(self.fixture.cleanup)
        self.root=self.fixture.root
        self.checkout=self.root/'published'; frontend=self.checkout/'frontend'
        (frontend/'node_modules/vite/bin').mkdir(parents=True)
        (frontend/'node_modules/.bin').mkdir()
        (frontend/'package-lock.json').write_text('{"lock":1}\n')
        (frontend/'node_modules/vite/bin/vite.js').write_text('console.log("published S")\n')
        (frontend/'node_modules/vite/package.json').write_text('{"name":"vite"}\n')
        (frontend/'node_modules/.bin/vite').symlink_to('../vite/bin/vite.js')
        subprocess.run(['git','init','--quiet',str(self.checkout)],check=True)
        subprocess.run(['git','-C',str(self.checkout),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.checkout),'-c','user.name=p100-test','-c','user.email=p100@example.invalid',
                        'commit','--quiet','-m','published-S'],check=True)
        self.source=subprocess.run(['git','-C',str(self.checkout),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
        entry=frontend/'node_modules/vite/bin/vite.js'; package=frontend/'node_modules/vite/package.json'; lock=frontend/'package-lock.json'
        self.contract=self.root/'origin.json'
        payload={'schema_version':1,'source_sha':self.source,'entry_target':'../vite/bin/vite.js',
                 'entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),
                 'vite_package_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
                 'lockfile_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}
        source_contract=self.root/'origin-source.json'; source_contract.write_text(canonical(payload)); source_contract.chmod(0o600)
        self.receipts=self.root/'receipts'; self.receipts.mkdir(mode=0o700)
        subprocess.run(['sudo','-n','install','-o','root','-g','root','-m','0600',str(source_contract),str(self.contract)],check=True)
        subprocess.run(['sudo','-n','chown','root:root',str(self.receipts)],check=True)
        self.i0=self.receipts/'i0.json'; self.i1=self.receipts/'i1.json'

    def tearDown(self):
        if hasattr(self,'receipts'):
            subprocess.run(['sudo','-n','rm','-f',str(self.i0),str(self.i1),str(self.receipts/'altered.json')],check=False)
            subprocess.run(['sudo','-n','rmdir',str(self.receipts)],check=False)

    def root_receipt(self,action,output,seal=None):
        # The receipt process is root. Capture deliberately crosses back to the
        # caller's user manager, so the only service it can observe is the
        # disposable p100-contract-* canary made by Fixture.
        module=str(Path(__file__).resolve().parents[1])
        capture='''import json,sys
sys.path.insert(0,sys.argv[1])
from release_systemd import Manager
print(json.dumps(Manager(json.loads(sys.argv[2]),True).capture(http=True),sort_keys=True))
'''
        code='''import json,os,subprocess,sys
sys.path.insert(0,sys.argv[1])
from origin_contract import seal,activate,load,validate_activation
settings=json.loads(sys.argv[2])
class UserManager:
 def capture(self,http=True):
  result=subprocess.run(["sudo","-n","-u",os.environ["P100_USER"],"env","XDG_RUNTIME_DIR="+os.environ["P100_USER_RUNTIME"],"DBUS_SESSION_BUS_ADDRESS="+os.environ["P100_USER_BUS"],sys.executable,"-c",sys.argv[-1],sys.argv[1],sys.argv[2]],capture_output=True,text=True)
  if result.returncode: raise RuntimeError(result.stderr.strip())
  return json.loads(result.stdout)
manager=UserManager()
if sys.argv[3]=="seal": value=seal(settings,sys.argv[4],sys.argv[5],sys.argv[6],sys.argv[7],manager=manager)
elif sys.argv[3]=="activate": value=activate(settings,sys.argv[4],sys.argv[5],sys.argv[6],sys.argv[7],sys.argv[8],manager=manager)
else:
 contract,digest=load(sys.argv[6],sys.argv[5],owner_uid=0)
 value=validate_activation(sys.argv[7],sys.argv[5],settings,manager.capture(),contract_digest=digest)
print(json.dumps(value,sort_keys=True))
'''
        env={**os.environ,'P100_USER':pwd.getpwuid(os.getuid()).pw_name,'P100_USER_RUNTIME':os.environ['XDG_RUNTIME_DIR'],
             'P100_USER_BUS':os.environ['DBUS_SESSION_BUS_ADDRESS']}
        args=['sudo','-n','-E',sys.executable,'-c',code,module,json.dumps(self.fixture.settings),action,
              str(self.checkout),self.source,str(self.contract)]
        if action=='seal': args.append(str(output))
        elif action=='activate': args.extend([str(seal),str(output)])
        else: args.append(str(output))
        args.append(capture)
        return subprocess.run(args,capture_output=True,text=True,env=env)

    def test_i0_i1_root_receipts_survive_private_restart_and_tamper_refuses(self):
        first=self.root_receipt('seal',self.i0)
        self.assertEqual(first.returncode,0,first.stderr)
        self.fixture.manager.restart()
        for _ in range(100):
            try:
                self.fixture.manager.http(); break
            except OSError: time.sleep(.05)
        else: self.fail('private Vite did not restart')
        second=self.root_receipt('activate',self.i1,self.i0)
        self.assertEqual(second.returncode,0,second.stderr)
        metadata=subprocess.run(['sudo','-n','stat','-c','%u:%g:%a',str(self.i0),str(self.i1)],capture_output=True,text=True,check=True).stdout.splitlines()
        self.assertEqual(metadata,['0:0:600','0:0:600'])
        payload=json.loads(subprocess.run(['sudo','-n','cat',str(self.i1)],capture_output=True,text=True,check=True).stdout)
        self.assertNotEqual(payload['i0']['capture']['process']['invocation_id'],payload['i1']['process']['invocation_id'])
        self.assertNotEqual(payload['i0']['capture']['process']['starttime'],payload['i1']['process']['starttime'])
        # Disk remains S, but a later Vite H process is a different I1 and
        # prepare's exact-current check refuses it before publication.
        self.fixture.manager.restart()
        for _ in range(100):
            try:
                self.fixture.manager.http(); break
            except OSError: time.sleep(.05)
        else: self.fail('private Vite H did not restart')
        live_h=self.root_receipt('validate',self.i1)
        self.assertNotEqual(live_h.returncode,0)
        self.assertIn('current Vite capture differs',live_h.stderr)
        # Altering I1 cannot be replayed: its canonical content and the I0
        # digest/identity validation are read again before prepare can use it.
        payload['settings_sha256']='f'*64
        altered=self.root/'altered.json'; altered.write_text(canonical(payload)); altered.chmod(0o600)
        subprocess.run(['sudo','-n','install','-o','root','-g','root','-m','0600',str(altered),str(self.receipts/'altered.json')],check=True)
        checker='''import sys; sys.path.insert(0,sys.argv[1]); from origin_contract import load_activation
try: load_activation(sys.argv[2],sys.argv[3],settings={})
except ValueError: raise SystemExit(0)
raise SystemExit(1)
'''
        check=subprocess.run(['sudo','-n',sys.executable,'-c',checker,str(Path(__file__).resolve().parents[1]),str(self.receipts/'altered.json'),self.source],capture_output=True,text=True)
        self.assertEqual(check.returncode,0,check.stderr)
        subprocess.run(['sudo','-n','rm','-f',str(self.receipts/'altered.json')],check=True)


if __name__=='__main__': unittest.main(verbosity=2)
