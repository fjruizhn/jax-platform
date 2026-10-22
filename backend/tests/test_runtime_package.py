import hashlib, importlib.util, json, os
from pathlib import Path
import pytest
M=Path(__file__).resolve().parents[2]/'ops/migration/runtime_package.py'; s=importlib.util.spec_from_file_location('rp',M); rp=importlib.util.module_from_spec(s); s.loader.exec_module(rp)
def test_rejects_bad_digest_and_extra(tmp_path):
 p=tmp_path/'p'; p.mkdir(); f=p/'helper'; f.write_text('x'); f.chmod(0o644)
 data={'schema':rp.SCHEMA,'payload':[{'path':'helper','role':'helper','sha256':hashlib.sha256(b'x').hexdigest(),'size':1,'mode':'0644','owner':'root:root'}]}
 raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()+b'\n'; (p/'manifest.json').write_bytes(raw)
 with pytest.raises(rp.PackageError): rp.validate(p,'0'*64)
 (p/'extra').write_text('x')
 with pytest.raises(rp.PackageError): rp.validate(p,hashlib.sha256(raw).hexdigest())

def test_snapshot_restore_preserves_present_and_absent(tmp_path):
 root=tmp_path/'root'; old=root/'helper'; old.parent.mkdir(); old.write_text('old'); old.chmod(0o600)
 paths={'helper':old,'unit':root/'unit'}; backup=tmp_path/'backup'; rp.snapshot(paths,backup)
 old.write_text('new'); old.chmod(0o644); paths['unit'].write_text('newunit')
 rp.restore(paths,backup)
 assert old.read_text()=='old' and (old.stat().st_mode&0o777)==0o600 and not paths['unit'].exists()

def test_import_rolls_back_after_swap(tmp_path, monkeypatch):
 bundle=tmp_path/'bundle'; bundle.mkdir(); (bundle/'h').write_text('new')
 monkeypatch.setattr(rp,'validate',lambda *_:{'payload':[{'path':'h'}]})
 dest=tmp_path/'dest'; dest.write_text('old'); paths={'h':dest}
 with pytest.raises(rp.PackageError): rp.import_package(bundle,'x',paths,tmp_path/'backup',fail_after=1)
 assert dest.read_text()=='old'
