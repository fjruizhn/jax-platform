"""Fail-closed validator for a root-owned runtime package; never executes it."""
from __future__ import annotations
import hashlib, json, os, stat
import shutil
from pathlib import Path

SCHEMA="axioma.platform-runtime-package/v1"
class PackageError(Exception): pass
def _sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def validate(root: Path, expected_digest: str):
 root=root.resolve(strict=True); manifest=root/'manifest.json'
 raw=manifest.read_bytes()
 if not raw.endswith(b'\n') or hashlib.sha256(raw).hexdigest()!=expected_digest: raise PackageError('manifest digest')
 try: data=json.loads(raw)
 except json.JSONDecodeError as e: raise PackageError('manifest json') from e
 if json.dumps(data,sort_keys=True,separators=(',',':')).encode()+b'\n'!=raw or data.get('schema')!=SCHEMA: raise PackageError('noncanonical manifest')
 payload=data.get('payload')
 if not isinstance(payload,list) or not payload: raise PackageError('payload')
 seen=set()
 for item in payload:
  if not isinstance(item,dict) or set(item)!={'path','role','sha256','size','mode','owner'}: raise PackageError('entry')
  rel=item['path']
  if not isinstance(rel,str) or not rel or rel.startswith('/') or '..' in Path(rel).parts or rel in seen: raise PackageError('path')
  seen.add(rel); p=root/rel; s=p.lstat()
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid!=0 or s.st_gid!=0: raise PackageError('unsafe payload')
  if item['role'] not in {'helper','unit','manifest'} or item['owner']!='root:root' or item['size']!=s.st_size or item['mode']!=f'{stat.S_IMODE(s.st_mode):04o}' or item['sha256']!=_sha(p): raise PackageError('payload mismatch')
 actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
 if actual != seen|{'manifest.json'}: raise PackageError('extra payload')
 return data

def snapshot(paths, backup):
 backup.mkdir()
 for n,p in paths.items():
  q=backup/n
  if not p.exists(): (backup/(n+'.ABSENT')).write_text(''); continue
  q.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,q)
def restore(paths, backup):
 for n,p in paths.items():
  q=backup/n
  if (backup/(n+'.ABSENT')).exists():
   if p.exists(): p.unlink()
  else:
   p.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(q,p)
