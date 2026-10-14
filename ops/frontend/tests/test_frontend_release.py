"""Observable storage and transaction contracts, using private real files."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import frontend_release as release

class StorageContracts(unittest.TestCase):
    def setUp(self):
        previous=os.umask(0o022); self.addCleanup(os.umask,previous)
        self.tmp = tempfile.TemporaryDirectory(prefix='p100-storage-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_restore_preserves_mode_owner_and_absence(self):
        file = self.root/'old'; file.write_bytes(b'original\x00'); file.chmod(0o600)
        absent = self.root/'absent'
        saved = release.snapshot_managed([file, absent])
        file.write_bytes(b'changed'); file.chmod(0o644); absent.write_bytes(b'new')
        release.restore_snapshot(saved)
        self.assertEqual(file.read_bytes(), b'original\x00')
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(file.stat().st_uid, os.getuid())
        self.assertFalse(absent.exists())

    def test_current_replacement_is_atomic_without_unlink(self):
        releases=self.root/'releases'; releases.mkdir(mode=0o755)
        for sha in ('a'*40,'b'*40): (releases/sha).mkdir(mode=0o755)
        current = self.root/'current'; current.symlink_to('releases/'+'a'*40)
        saved = release.snapshot_managed([current]); current.unlink(); current.symlink_to('releases/'+'b'*40)
        original_unlink = Path.unlink
        def deny_current(path, *args, **kwargs):
            if path == current: raise AssertionError('current was unlinked')
            return original_unlink(path,*args,**kwargs)
        with patch.object(Path, 'unlink', deny_current): release.restore_snapshot(saved)
        self.assertEqual(os.readlink(current), 'releases/'+'a'*40)

    def test_pool_does_not_expose_partial_or_replace_collision(self):
        dist = self.root/'dist'; (dist/'assets').mkdir(parents=True,mode=0o755); dist.chmod(0o755)
        asset = dist/'assets/app-abcdefgh.js'; asset.write_bytes(b'first')
        pool = self.root/'pool'
        release.update_asset_pool(dist,pool)
        target=pool/'assets/app-abcdefgh.js'
        self.assertEqual(target.read_bytes(), b'first')
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        asset.write_bytes(b'second')
        with self.assertRaisesRegex(ValueError, 'collision'): release.update_asset_pool(dist,pool)
        self.assertEqual(target.read_bytes(), b'first')

    def test_pool_rejects_unhashed_or_symlink_sources(self):
        dist=self.root/'dist'; (dist/'assets').mkdir(parents=True,mode=0o755); dist.chmod(0o755)
        (dist/'assets/app.js').write_bytes(b'bad')
        with self.assertRaises(ValueError): release.update_asset_pool(dist,self.root/'pool')
        (dist/'assets/app.js').unlink(); (dist/'assets/app-abcdefgh.js').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): release.update_asset_pool(dist,self.root/'pool')

    def test_pool_existing_mode_drift_refused(self):
        dist=self.root/'dist'; (dist/'assets').mkdir(parents=True,mode=0o755); dist.chmod(0o755)
        (dist/'assets/app-abcdefgh.js').write_bytes(b'original')
        release.update_asset_pool(dist,self.root/'pool')
        target=self.root/'pool/assets/app-abcdefgh.js'; target.chmod(0o600)
        with self.assertRaises(ValueError): release.update_asset_pool(dist,self.root/'pool')
        self.assertEqual(target.stat().st_mode & 0o777,0o600)

    def test_unknown_current_target_is_refused_before_snapshot(self):
        current=self.root/'current'; current.symlink_to('/etc')
        with self.assertRaises(ValueError): release.snapshot_managed([current])

    def test_pool_cut_never_exposes_partial_and_retry_is_complete(self):
        dist=self.root/'dist'; (dist/'assets').mkdir(parents=True,mode=0o755); dist.chmod(0o755)
        source=dist/'assets/app-abcdefgh.js'; source.write_bytes(b'x'*1000000)
        pool=self.root/'pool'; target=pool/'assets/app-abcdefgh.js'
        class Cut(BaseException): pass
        def cut(at):
            if at=='pool_before_publish': raise Cut()
        with self.assertRaises(Cut): release.update_asset_pool(dist,pool,fault=cut)
        self.assertFalse(target.exists())
        release.update_asset_pool(dist,pool)
        self.assertEqual(target.read_bytes(),b'x'*1000000)
        source.write_bytes(b'collision')
        with self.assertRaises(ValueError): release.update_asset_pool(dist,pool)
        self.assertEqual(target.read_bytes(),b'x'*1000000)

    def test_atomic_write_fsync_failure_preserves_old_bytes(self):
        file=self.root/'config'; file.write_bytes(b'old')
        with patch('release_storage.os.fsync',side_effect=OSError('fsync cut')):
            with self.assertRaises(OSError): release.atomic_write(file,b'new',mode=0o644)
        self.assertEqual(file.read_bytes(),b'old')
        self.assertEqual(list(self.root.glob('.config.*')),[])

    def test_controlled_parent_symlink_and_writable_owner_refused(self):
        from release_storage import atomic_write
        foreign=self.root/'foreign'; foreign.mkdir(mode=0o755)
        link=self.root/'link'; link.symlink_to(foreign,target_is_directory=True)
        with self.assertRaises(ValueError): atomic_write(link/'config',b'bad')
        foreign.chmod(0o775)
        with self.assertRaises(ValueError): atomic_write(foreign/'config',b'bad')
        self.assertFalse((foreign/'config').exists())

    def test_launcher_exec_failure_closes_forwarders_and_returns(self):
        import subprocess
        helper=Path(release.__file__).with_name('launch_nginx.py')
        result=subprocess.run(['/usr/bin/python3','-I','-B',str(helper),'--binary',str(self.root/'missing-nginx'),
                               '--config',str(self.root/'config')],capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,127)
        self.assertIn('nginx exec failed',result.stderr)

    def test_pool_publication_race_never_overwrites_an_alien_asset(self):
        dist=self.root/'dist'; (dist/'assets').mkdir(parents=True,mode=0o755); dist.chmod(0o755)
        (dist/'assets/app-abcdefgh.js').write_bytes(b'new')
        pool=self.root/'pool'; target=pool/'assets/app-abcdefgh.js'
        def race(at):
            if at=='pool_before_publish': target.write_bytes(b'alien'); target.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'collision'): release.update_asset_pool(dist,pool,fault=race)
        self.assertEqual(target.read_bytes(),b'alien')

    def test_privileged_restore_preserves_uid_gid_and_mode_exactly(self):
        import subprocess
        script="""import os,sys,tempfile,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from release_storage import atomic_write,snapshot_managed,restore_snapshot
with tempfile.TemporaryDirectory(prefix='p100-root-metadata-') as temporary:
    path=Path(temporary)/'config'
    atomic_write(path,b'original',mode=0o640,uid=0,gid=65534)
    saved=snapshot_managed([path],uid=0)
    atomic_write(path,b'changed',mode=0o644,uid=0,gid=0)
    os.chown(path,65534,0)
    restore_snapshot(saved)
    info=path.stat()
    print(json.dumps([path.read_text(),info.st_uid,info.st_gid,info.st_mode & 0o777]))
"""
        result=subprocess.run(['sudo','-n','/usr/bin/python3','-I','-c',script,str(Path(release.__file__).parent)],
                              capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),['original',0,65534,0o640])

    def test_cli_rejects_writable_or_symlink_settings_before_roots(self):
        import subprocess
        settings=self.root/'settings.json'; settings.write_text('{}'); settings.chmod(0o666)
        command=[sys.executable,release.__file__,'rollback','--user','--settings']
        result=subprocess.run([*command,str(settings)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1); self.assertIn('writable',result.stderr)
        settings.chmod(0o600); link=self.root/'settings-link.json'; link.symlink_to(settings)
        result=subprocess.run([*command,str(link)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1); self.assertIn('symlink',result.stderr)

    def test_private_mode_cannot_target_production_service(self):
        s=json.loads((Path(release.__file__).parent/'settings.json').read_text())
        s.update(private_mode=True,owner_uid=os.getuid())
        with self.assertRaises(ValueError): release.Controller(s,user=True)

    def test_cli_requires_explicit_settings(self):
        import subprocess
        result=subprocess.run([sys.executable,release.__file__,'rollback'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('--settings',result.stderr)

if __name__=='__main__': unittest.main()
