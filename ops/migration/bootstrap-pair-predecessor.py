#!/usr/bin/python3 -I
"""One-time root bootstrap of an already-built, sealed pair predecessor.

The builder (as jaxbuild) creates the candidate with --bootstrap-predecessor.
This program never reads Git, pip metadata, or candidate code. It calls only the
separately installed root-owned adopter, then publishes the first selection.
"""
from __future__ import annotations
import argparse, hashlib, json, os, stat, subprocess
from pathlib import Path

ROOT = Path('/opt/jax-platform-pair')
ADOPTER = Path('/usr/local/libexec/adopt-pair-root.py')

def die(message: str) -> None: raise SystemExit(f'bootstrap-pair-predecessor: {message}')
def atomic(path: Path, value: bytes) -> None:
    fd=os.open(path.with_name('.'+path.name+'.tmp'), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o644)
    try: os.write(fd,value); os.fsync(fd); os.fchown(fd,0,0); os.fchmod(fd,0o644)
    finally: os.close(fd)
    os.replace(path.with_name('.'+path.name+'.tmp'),path); d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True); p.add_argument('--destination',type=Path,required=True); p.add_argument('--expected-manifest-sha256',required=True); a=p.parse_args()
    if os.geteuid()!=0 or len(a.expected_manifest_sha256)!=64 or any(c not in '0123456789abcdef' for c in a.expected_manifest_sha256): die('root and external manifest digest required')
    root=ROOT; releases=root/'releases'; selection=Path('/etc/jax/platform-pair-selection.json')
    if any(x.exists() or x.is_symlink() for x in (root/'current',selection,a.destination)) or a.destination.parent != releases: die('bootstrap requires an empty canonical pair root')
    if not ADOPTER.is_file() or ADOPTER.is_symlink(): die('trusted adopter unavailable')
    subprocess.run(['/usr/bin/python3','-I','-S',str(ADOPTER),'--source',str(a.source),'--destination',str(a.destination),'--expected-manifest-sha256',a.expected_manifest_sha256],check=True)
    raw=(a.destination/'pair-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=a.expected_manifest_sha256: die('sealed predecessor digest mismatch')
    try: manifest=json.loads(raw)
    except json.JSONDecodeError: die('sealed predecessor manifest invalid')
    jxp,jax=manifest.get('jax_platform_revision'),manifest.get('jax_revision')
    if not all(isinstance(x,str) and len(x)==40 and all(c in '0123456789abcdef' for c in x) for x in (jxp,jax)): die('sealed predecessor identity invalid')
    atomic(selection,json.dumps({'release':str(a.destination),'jax_platform_revision':jxp,'jax_revision':jax,'manifest_sha256':a.expected_manifest_sha256},separators=(',',':')).encode()+b'\n')
    link=root/'.current.bootstrap.tmp'; os.symlink('releases/'+a.destination.name,link); os.replace(link,root/'current'); d=os.open(root,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)
if __name__=='__main__': main()
