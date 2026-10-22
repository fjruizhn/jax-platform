#!/usr/bin/python3 -I
"""Crash-recoverable one-time root bootstrap of a sealed pair predecessor."""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, stat, subprocess, uuid
from pathlib import Path

ROOT = Path('/opt/jax-platform-pair')
ADOPTER = Path('/usr/local/libexec/adopt-pair-root.py')
SELECTION = Path('/etc/jax/platform-pair-selection.json')
JOURNAL = '.bootstrap-predecessor-v1.json'
LOCK = '.bootstrap-predecessor-v1.lock'

def die(message: str) -> None: raise SystemExit(f'bootstrap-pair-predecessor: {message}')

def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def secure_dir(path: Path) -> None:
    value = path.stat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != 0 or value.st_gid != 0 or value.st_mode & 0o022: die('unsafe canonical pair root')

def write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        view = memoryview(data)
        while view: view = view[os.write(fd, view):]
        os.fsync(fd); os.fchown(fd, 0, 0); os.fchmod(fd, mode)
    finally: os.close(fd)
    os.replace(temporary, path); fsync_dir(path.parent)

def read_json(path: Path, mode: int) -> dict:
    try: fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc: die(f'cannot read bootstrap journal: {exc}')
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != 0 or before.st_gid != 0 or stat.S_IMODE(before.st_mode) != mode): die('unsafe bootstrap journal')
        data = b''.join(iter(lambda: os.read(fd, 1048576), b'')); after = os.fstat(fd)
    finally: os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size): die('bootstrap journal changed during read')
    try: return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError): die('invalid bootstrap journal')

def lock(root: Path) -> int:
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600); value = os.fstat(fd)
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_uid != 0 or value.st_gid != 0 or stat.S_IMODE(value.st_mode) != 0o600): os.close(fd); die('unsafe bootstrap lock')
    fcntl.flock(fd, fcntl.LOCK_EX); return fd

def journal_binding(args: argparse.Namespace) -> dict:
    return {'schema':'axioma.bootstrap-predecessor/v1','stage':'PRE_ADOPTION','source':str(args.source.resolve(strict=True)),'destination':str(args.destination),'manifest_sha256':args.expected_manifest_sha256,'request':str(args.request.resolve(strict=True)),'receipt':str(args.receipt.resolve(strict=True))}

def store_journal(path: Path, journal: dict, stage: str) -> dict:
    result = journal | {'stage':stage}; write_atomic(path, json.dumps(result, sort_keys=True, separators=(',',':')).encode()+b'\n', 0o600); return result

def sealed_manifest(destination: Path, digest: str) -> dict:
    try: raw = (destination / 'pair-manifest.json').read_bytes()
    except OSError: die('sealed predecessor missing')
    if hashlib.sha256(raw).hexdigest() != digest: die('sealed predecessor digest mismatch')
    try: manifest = json.loads(raw)
    except json.JSONDecodeError: die('sealed predecessor manifest invalid')
    if not all(isinstance(x,str) and len(x)==40 and all(c in '0123456789abcdef' for c in x) for x in (manifest.get('jax_platform_revision'), manifest.get('jax_revision'))): die('sealed predecessor identity invalid')
    return manifest

def selection_bytes(destination: Path, manifest: dict, digest: str) -> bytes:
    return json.dumps({'release':str(destination),'jax_platform_revision':manifest['jax_platform_revision'],'jax_revision':manifest['jax_revision'],'manifest_sha256':digest}, sort_keys=True, separators=(',',':')).encode()+b'\n'

def same_selection(path: Path, wanted: bytes) -> bool:
    if not path.exists() or path.is_symlink(): return False
    try: return json.dumps(read_json(path, 0o644), sort_keys=True, separators=(',',':')).encode()+b'\n' == wanted
    except SystemExit: return False

def same_current(root: Path, destination: Path) -> bool:
    current = root/'current'
    return current.is_symlink() and os.readlink(current)==f'releases/{destination.name}' and current.resolve(strict=False)==destination

def publish_current(root: Path, destination: Path) -> None:
    current=root/'current'
    if same_current(root,destination): return
    if current.exists() or current.is_symlink(): die('bootstrap current conflicts with journal')
    temporary=root/f'.current.bootstrap.{uuid.uuid4().hex}.tmp'; os.symlink(f'releases/{destination.name}',temporary); os.replace(temporary,current); fsync_dir(root)

def bootstrap(args: argparse.Namespace) -> None:
    root=ROOT; releases=root/'releases'; selection=SELECTION
    secure_dir(root); secure_dir(releases)
    if args.destination.parent != releases: die('bootstrap destination must be under canonical releases')
    fd=lock(root)
    try:
        path=root/JOURNAL; expected=journal_binding(args)
        if path.exists() or path.is_symlink():
            journal=read_json(path,0o600)
            if journal.get('stage') not in {'PRE_ADOPTION','ADOPTED','SELECTED','CURRENT'} or any(journal.get(k)!=v for k,v in expected.items() if k!='stage'): die('bootstrap journal conflicts with request')
        else:
            if args.destination.exists() or args.destination.is_symlink() or selection.exists() or selection.is_symlink() or (root/'current').exists() or (root/'current').is_symlink(): die('bootstrap requires an empty canonical pair root')
            journal=store_journal(path,expected,'PRE_ADOPTION')
        if not args.destination.exists():
            if not ADOPTER.is_file() or ADOPTER.is_symlink(): die('trusted adopter unavailable')
            subprocess.run(['/usr/bin/python3','-I','-S',str(ADOPTER),'--source',str(args.source),'--destination',str(args.destination),'--expected-manifest-sha256',args.expected_manifest_sha256,'--request',str(args.request),'--receipt',str(args.receipt)],check=True)
        manifest=sealed_manifest(args.destination,args.expected_manifest_sha256)
        if journal['stage']=='PRE_ADOPTION': journal=store_journal(path,journal,'ADOPTED')
        wanted=selection_bytes(args.destination,manifest,args.expected_manifest_sha256)
        if selection.exists() or selection.is_symlink():
            if not same_selection(selection,wanted): die('bootstrap selection conflicts with journal')
        else: write_atomic(selection,wanted)
        if journal['stage']=='ADOPTED': journal=store_journal(path,journal,'SELECTED')
        publish_current(root,args.destination)
        if journal['stage']=='SELECTED': store_journal(path,journal,'CURRENT')
    finally: os.close(fd)

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True); p.add_argument('--destination',type=Path,required=True); p.add_argument('--expected-manifest-sha256',required=True); p.add_argument('--request',type=Path,required=True); p.add_argument('--receipt',type=Path,required=True); a=p.parse_args()
    if os.geteuid()!=0 or len(a.expected_manifest_sha256)!=64 or any(c not in '0123456789abcdef' for c in a.expected_manifest_sha256): die('root and external manifest digest required')
    bootstrap(a)
if __name__=='__main__': main()
