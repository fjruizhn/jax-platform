"""State-machine tests for the sealed predecessor bootstrap recovery path."""
from __future__ import annotations
import importlib.util, json, os
from pathlib import Path
from types import SimpleNamespace
import pytest

REPO=Path(__file__).resolve().parents[2]
def module(name: str, relative: str):
 spec=importlib.util.spec_from_file_location(name,REPO/relative); assert spec and spec.loader
 result=importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result

def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
 boot=module('pair_bootstrap_recovery','ops/migration/bootstrap-pair-predecessor.py')
 root=tmp_path/'root'; releases=root/'releases'; releases.mkdir(parents=True); source=tmp_path/'source'; source.mkdir(); request=tmp_path/'request'; receipt=tmp_path/'receipt'; request.write_text('{}'); receipt.write_text('{}')
 adopter=tmp_path/'adopter'; adopter.write_text('trusted'); adopter.chmod(0o755)
 monkeypatch.setattr(boot,'ROOT',root); monkeypatch.setattr(boot,'SELECTION',tmp_path/'selection.json'); monkeypatch.setattr(boot,'ADOPTER',adopter)
 monkeypatch.setattr(boot,'secure_dir',lambda _:None); monkeypatch.setattr(boot,'lock',lambda _:os.open(tmp_path/'lock',os.O_CREAT|os.O_RDWR,0o600))
 def write(path: Path,data: bytes,mode: int=0o644): path.write_bytes(data); path.chmod(mode)
 monkeypatch.setattr(boot,'write_atomic',write); monkeypatch.setattr(boot,'read_json',lambda path,mode:json.loads(path.read_text()))
 args=SimpleNamespace(source=source,destination=releases/'release',expected_manifest_sha256='a'*64,request=request,receipt=receipt)
 manifest={'jax_platform_revision':'b'*40,'jax_revision':'c'*40}
 def adopt(*_,**__): args.destination.mkdir(); (args.destination/'pair-manifest.json').write_bytes(json.dumps(manifest).encode())
 monkeypatch.setattr(boot.hashlib,'sha256',lambda raw:SimpleNamespace(hexdigest=lambda:'a'*64))
 monkeypatch.setattr(boot.subprocess,'run',adopt)
 return boot,args,root

@pytest.mark.parametrize('cut',["after_adoption","after_selection","after_current"])
def test_bootstrap_resumes_each_durable_cut(tmp_path,monkeypatch,cut):
 boot,args,root=harness(tmp_path,monkeypatch); original_store=boot.store_journal; original_publish=boot.publish_current
 if cut=='after_adoption':
  def fail_adopt(*a,**kw): boot.subprocess.run=lambda *x,**y:None; (args.destination).mkdir(); (args.destination/'pair-manifest.json').write_bytes(b'{}'); raise KeyboardInterrupt
  # Simulate a killed adopter after a complete, externally verified release exists.
  def complete_then_cut(*a,**kw): args.destination.mkdir(); (args.destination/'pair-manifest.json').write_bytes(b'{}'); raise KeyboardInterrupt
  monkeypatch.setattr(boot.subprocess,'run',complete_then_cut)
 elif cut=='after_selection':
  def cut_store(path,journal,stage):
   value=original_store(path,journal,stage)
   if stage=='ADOPTED': return value
   if stage=='SELECTED': raise KeyboardInterrupt
   return value
  monkeypatch.setattr(boot,'store_journal',cut_store)
 else:
  def cut_current(*a,**kw): original_publish(*a,**kw); raise KeyboardInterrupt
  monkeypatch.setattr(boot,'publish_current',cut_current)
 with pytest.raises(KeyboardInterrupt): boot.bootstrap(args)
 # Repair the intentionally simplified manifest after the simulated abrupt adopter.
 if cut=='after_adoption': (args.destination/'pair-manifest.json').write_text(json.dumps({'jax_platform_revision':'b'*40,'jax_revision':'c'*40}))
 monkeypatch.setattr(boot,'store_journal',original_store); monkeypatch.setattr(boot,'publish_current',original_publish)
 boot.bootstrap(args); boot.bootstrap(args)
 assert json.loads((root/boot.JOURNAL).read_text())['stage']=='CURRENT'
 assert (root/'current').is_symlink()

def test_supervisor_refuses_existing_jaxbuild_process(monkeypatch):
 supervisor=module('pair_supervisor_recovery','ops/migration/pair-build-supervisor.py')
 monkeypatch.setattr(supervisor.pwd,'getpwnam',lambda _:SimpleNamespace(pw_uid=987,pw_shell='/usr/sbin/nologin'))
 monkeypatch.setattr(supervisor,'process_uids',lambda:{987})
 with pytest.raises(SystemExit,match='stopped nologin'): supervisor.require_quiescent_jaxbuild()
