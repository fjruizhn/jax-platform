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
