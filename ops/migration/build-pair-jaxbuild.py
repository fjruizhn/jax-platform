#!/usr/bin/python3
"""Unprivileged, byte-for-byte Git builder for a paired release."""
import argparse, hashlib, json, os, shutil, stat, subprocess, sys
from pathlib import Path

def die(message): raise SystemExit(f"build-pair-jaxbuild: {message}")
def run(*args, cwd): return subprocess.check_output(args, cwd=cwd)
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def repo_tree(repo, commit):
 if os.geteuid()==0: die('must not run as root')
 if not (repo/'.git').is_dir() or (repo/'.git'/'objects'/'info'/'alternates').exists(): die('unsafe repository')
 if run('git','replace','-l',cwd=repo).strip(): die('replace refs forbidden')
 if run('git','status','--porcelain',cwd=repo).strip(): die('repository not fresh')
 actual=run('git','rev-parse',f'{commit}^{{commit}}',cwd=repo).decode().strip()
 if actual != commit or len(actual)!=40: die('commit mismatch')
 tree=run('git','rev-parse',f'{commit}^{{tree}}',cwd=repo).decode().strip()
 rows=run('git','ls-tree','-rz','-r',commit,cwd=repo).split(b'\0')[:-1]
 return tree, rows
def extract(repo, commit, dest):
 tree, rows=repo_tree(repo,commit)
 for row in rows:
  meta,name=row.split(b'\t',1); mode,kind,blob=meta.split()
  path=Path(name.decode('utf-8'))
  if path.is_absolute() or '..' in path.parts or kind != b'blob' or mode not in (b'100644',b'100755'): die('unsafe tree entry')
  out=dest/path; out.parent.mkdir(parents=True,exist_ok=True)
  out.write_bytes(run('git','cat-file','blob',blob.decode(),cwd=repo)); out.chmod(0o755 if mode==b'100755' else 0o644)
 return tree
def main():
 p=argparse.ArgumentParser(); p.add_argument('--platform',type=Path,required=True); p.add_argument('--platform-commit',required=True); p.add_argument('--jax',type=Path,required=True); p.add_argument('--jax-commit',required=True); p.add_argument('--out',type=Path,required=True); p.add_argument('--wheelhouse',type=Path,required=True); p.add_argument('--lock',type=Path,required=True); p.add_argument('--python',default=sys.executable); a=p.parse_args()
 if a.out.exists() or not a.wheelhouse.is_dir() or not a.lock.is_file() or '--hash=sha256:' not in a.lock.read_text(): die('invalid inputs')
 a.out.mkdir(mode=0o700); jxp=a.out/'jax-platform'; jax=a.out/'jax'; jxp.mkdir(); jax.mkdir()
 pt=extract(a.platform,a.platform_commit,jxp); jt=extract(a.jax,a.jax_commit,jax)
 subprocess.run([a.python,'-m','venv','--copies',str(jxp/'backend'/'.venv')],check=True)
 subprocess.run([str(jxp/'backend'/'.venv'/'bin'/'pip'),'install','--no-index',f'--find-links={a.wheelhouse}','--require-hashes','-r',str(a.lock)],check=True)
 subprocess.run([str(jxp/'backend'/'.venv'/'bin'/'pip'),'freeze','--all'],check=True,stdout=(a.out/'freeze.txt').open('wb'))
 (a.out/'build-attestation.json').write_text(json.dumps({'schema':'axioma.pair-build/v2','platform_commit':a.platform_commit,'platform_tree':pt,'jax_commit':a.jax_commit,'jax_tree':jt,'wheelhouse_sha256':digest(a.wheelhouse/'MANIFEST.sha256') if (a.wheelhouse/'MANIFEST.sha256').is_file() else None,'lock_sha256':digest(a.lock),'python':a.python,'freeze_sha256':digest(a.out/'freeze.txt')},sort_keys=True)+'\n')
if __name__=='__main__': main()
