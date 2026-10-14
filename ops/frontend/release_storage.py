"""Durable filesystem primitives for the frontend's small, fixed transaction.

fsync covers file bytes/metadata and parent directory entries. This is a durability
protocol, not a claim that hardware power loss has been exercised. DAC excludes
root and other writers with the same UID.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from release_manifest import ASSET, SHA40, check_controlled_directory, checked_path, check_object


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def mkdir(path, mode, uid):
    path=checked_path(Path(path))
    if not path.exists():
        check_controlled_directory(path.parent,uid)
        path.mkdir(mode=mode); path.chmod(mode); sync_dir(path.parent)
    check_controlled_directory(path,uid)
    if stat.S_IMODE(path.stat().st_mode)!=mode: raise ValueError('directory mode mismatch: '+str(path))
    return path


def fingerprint(path, *, uid=None):
    path=Path(path)
    checked_path(path.parent)
    if not path.exists() and not path.is_symlink(): return {'absent':True}
    info=path.lstat()
    if uid is not None and info.st_uid!=uid: raise ValueError('managed owner mismatch: '+str(path))
    if uid is not None: check_controlled_directory(path.parent,uid)
    metadata={'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode)}
    if stat.S_ISLNK(info.st_mode):
        target=os.readlink(path)
        # Only current is a supported managed link, always to releases/<SHA>.
        absolute=Path(os.path.abspath(path.parent/target))
        if path.name!='current' or absolute.parent!=path.parent/'releases' or not SHA40.fullmatch(absolute.name):
            raise ValueError('unknown current target')
        checked_path(absolute)
        if not absolute.is_dir(): raise ValueError('current target must be an existing release directory')
        check_object(absolute,info.st_uid,public=True)
        return {**metadata,'symlink':target}
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022: raise ValueError('unsafe managed file: '+str(path))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream: data=stream.read()
    return {**metadata,'sha256':hashlib.sha256(data).hexdigest(),'bytes':base64.b64encode(data).decode()}


def snapshot_managed(paths, *, uid=None):
    return {str(path):fingerprint(path,uid=uid) for path in paths}


def file_state(data,mode=0o644,uid=None,gid=None):
    return {'uid':os.getuid() if uid is None else uid,'gid':os.getgid() if gid is None else gid,
            'mode':mode,'sha256':hashlib.sha256(data).hexdigest(),'bytes':base64.b64encode(data).decode()}


def atomic_write(path,data, *, mode=0o600,uid=None,gid=None):
    path=Path(path); checked_path(path)
    owner=os.getuid() if uid is None else uid
    check_controlled_directory(path.parent,owner)
    if path.exists() and not stat.S_ISREG(path.lstat().st_mode): raise ValueError('managed destination not regular')
    temporary=path.with_name('.'+path.name+'.'+os.urandom(12).hex())
    try:
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data.encode() if isinstance(data,str) else data)
            os.fchmod(stream.fileno(),mode)
            os.fchown(stream.fileno(),owner,os.getgid() if gid is None else gid)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path); sync_dir(path.parent)
    finally: temporary.unlink(missing_ok=True)


def install_state(path,state):
    path=Path(path); checked_path(path.parent)
    if state.get('absent'):
        if path.exists() or path.is_symlink(): path.unlink(); sync_dir(path.parent)
    elif 'symlink' in state:
        temporary=path.with_name('.'+path.name+'.'+os.urandom(12).hex())
        try:
            os.symlink(state['symlink'],temporary)
            os.lchown(temporary,state['uid'],state['gid'])
            os.replace(temporary,path); sync_dir(path.parent)
        finally: temporary.unlink(missing_ok=True)
    else:
        atomic_write(path,base64.b64decode(state['bytes']),mode=state['mode'],uid=state['uid'],gid=state['gid'])


def restore_snapshot(snapshot):
    for path,state in snapshot.items(): install_state(path,state)


def write_json(path,value):
    atomic_write(path,json.dumps(value,sort_keys=True,indent=2)+'\n')


def read_json(path,uid):
    info=check_object(checked_path(path),uid)
    if stat.S_IMODE(info.st_mode)!=0o600: raise ValueError('receipt mode must be0600')
    return json.loads(path.read_text())


@contextmanager
def locked(path,uid):
    checked_path(path); check_controlled_directory(path.parent,uid)
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o600:
            raise ValueError('unsafe lock')
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error: raise ValueError('frontend transaction already locked') from error
        yield
    finally: os.close(fd)


def update_asset_pool(dist,pool, *, uid=None, fault=None):
    """Append complete files with link(2) no-overwrite publication; never copy live."""
    uid=os.getuid() if uid is None else uid
    dist=checked_path(Path(dist)); pool=Path(pool)
    check_controlled_directory(dist,uid)
    sources=[]
    if (dist/'assets').exists():
        for path in (dist/'assets').rglob('*'):
            info=check_object(path,uid)
            if stat.S_ISDIR(info.st_mode): continue
            relative=path.relative_to(dist).as_posix()
            if not ASSET.fullmatch(relative): raise ValueError('asset lacks Vite hash')
            sources.append((path,relative,path.read_bytes()))
    # Validate all collisions before publishing any member.
    for source,relative,data in sources:
        target=pool/relative; checked_path(target)
        if target.exists():
            check_object(target,uid,public=True)
            if target.read_bytes()!=data: raise ValueError('asset collision: '+relative)
    mkdir(pool,0o755,uid)
    for source,relative,data in sources:
        target=pool/relative
        for directory in reversed(list(target.parent.parents)):
            if directory.is_relative_to(pool) and directory!=pool: mkdir(directory,0o755,uid)
        mkdir(target.parent,0o755,uid)
        if target.exists(): continue
        temporary=target.with_name('.'+target.name+'.'+os.urandom(12).hex())
        try:
            atomic_write(temporary,data,mode=0o644,uid=uid)
            if fault: fault('pool_before_publish')
            try: os.link(temporary,target,follow_symlinks=False)
            except FileExistsError:
                check_object(target,uid,public=True)
                if target.read_bytes()!=data: raise ValueError('asset collision: '+relative)
            sync_dir(target.parent)
            if fault: fault('pool_after_publish')
        finally: temporary.unlink(missing_ok=True); sync_dir(target.parent)
