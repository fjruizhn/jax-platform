"""Validate the exact immutable bytes selected by nginx's live roots."""
import hashlib
import os
from pathlib import Path
import stat
from release_manifest import check_controlled_directory, checked_path, check_object, expected_owner


def verify_current(settings,release_dir):
    root=checked_path(Path(settings['release_root']))
    uid=expected_owner(settings)
    check_controlled_directory(root,uid); check_object(root,uid,public=True)
    release=checked_path(Path(release_dir))
    current=root/'current'
    info=current.lstat()
    if not stat.S_ISLNK(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=0o777:
        raise ValueError('current must be an owned release symlink')
    target=os.readlink(current)
    selected=Path(os.path.abspath(root/target))
    if selected!=release or release.parent!=root/'releases':
        raise ValueError('current does not select the attested release')
    check_object(checked_path(selected),uid,public=True)
    return (info.st_dev,info.st_ino,info.st_uid,info.st_gid,target)


def verify_pool(settings,manifest):
    """Check the current manifest subset; valid historical extra assets remain."""
    uid=expected_owner(settings)
    pool=checked_path(Path(settings['release_root'])/'pool')
    expected={path:digest for path,digest in manifest['files'].items() if path.startswith('assets/')}
    if not expected: return {}
    check_controlled_directory(pool,uid); check_object(pool,uid,public=True)
    inventory={}
    for relative,wanted in expected.items():
        target=checked_path(pool/relative)
        for parent in (target.parent,*target.parent.parents):
            if not parent.is_relative_to(pool): break
            check_controlled_directory(parent,uid); check_object(parent,uid,public=True)
        info=check_object(target,uid,public=True)
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        if digest!=wanted: raise ValueError('served pool asset digest mismatch: '+relative)
        inventory[relative]={'sha256':digest,'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode)}
    return inventory


def verify_served(settings,release_dir,manifest):
    current=verify_current(settings,release_dir)
    pool=verify_pool(settings,manifest)
    if verify_current(settings,release_dir)!=current: raise ValueError('current changed while verifying served bytes')
    return pool
