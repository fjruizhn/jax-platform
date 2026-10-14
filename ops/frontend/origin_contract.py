#!/usr/bin/env python3
"""Build and verify a source-derived Vite origin contract.

The generator reads a named Git object, never a running service.  Its output is
canonical JSON, installed outside the checkout by the deployment procedure.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))+'\n'


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_all(descriptor, data):
    offset=0
    while offset<len(data):
        count=os.write(descriptor,data[offset:])
        if count<=0: raise OSError('contract write made no progress')
        offset+=count


def settings_digest(settings):
    """The receipt binds semantics, not whitespace in its settings file."""
    if not isinstance(settings,dict): raise ValueError('settings must be an object')
    # Controller derives these three values from the effective UID/group; they
    # are not bytes in the root-owned settings input and must not make a
    # receipt unverifiable merely because the controller normalized them.
    stable={key:value for key,value in settings.items() if key not in ('owner_uid','process_uid','process_gid')}
    return hashlib.sha256(canonical(stable).encode()).hexdigest()


def _pinned_regular(path, prefix, contract):
    path=Path(path)
    required=(prefix+'_sha256',prefix+'_owner_uid',prefix+'_owner_gid',prefix+'_mode')
    if any(key not in contract for key in required) or path.is_symlink() or not path.is_file():
        raise ValueError(prefix+' pin is incomplete')
    info=path.stat()
    if (hashlib.sha256(path.read_bytes()).hexdigest()!=contract[prefix+'_sha256'] or
        info.st_uid!=contract[prefix+'_owner_uid'] or info.st_gid!=contract[prefix+'_owner_gid'] or
        format(info.st_mode & 0o777,'04o')!=contract[prefix+'_mode']):
        raise ValueError(prefix+' pin differs')
    return path


def generate(checkout, source_sha, output, settings):
    if os.geteuid()==0: raise ValueError('origin contract generator refuses UID0')
    checkout=Path(checkout); output=Path(output)
    parent=output.parent
    info=parent.stat()
    if parent.is_symlink() or not parent.is_dir() or info.st_uid!=os.geteuid() or info.st_mode & 0o077:
        raise ValueError('origin contract output parent must be operator-owned private directory')
    if output.exists() or output.is_symlink(): raise FileExistsError('origin contract output already exists')
    contract=settings.get('original_contract',{})
    node=_pinned_regular(contract.get('node_binary',''),'node',contract)
    npm=_pinned_regular(contract.get('npm_cli',''),'npm_cli',contract)
    version=subprocess.run([str(node),str(npm),'--version'],check=True,capture_output=True,text=True).stdout.strip()
    if version!=settings.get('npm_version'): raise ValueError('pinned npm version differs')
    if not re.fullmatch(r'[0-9a-f]{40}',source_sha): raise ValueError('source SHA must be full lowercase SHA1')
    actual=subprocess.run(['git','-C',str(checkout),'rev-parse',source_sha+'^{commit}'],check=True,capture_output=True,text=True).stdout.strip()
    if actual!=source_sha: raise ValueError('source SHA is not the requested commit')
    with tempfile.TemporaryDirectory(prefix='p100-origin-contract-') as temporary:
        archive=Path(temporary)/'source.tar'
        with archive.open('wb') as out: subprocess.run(['git','-C',str(checkout),'archive','--format=tar',source_sha],check=True,stdout=out)
        source=Path(temporary)/'source'; source.mkdir()
        with tarfile.open(archive) as tar: tar.extractall(source,filter='data')
        frontend=source/'frontend'
        # npm ci makes Vite's entry/package bytes a property of this source S.
        subprocess.run([str(node),str(npm),'ci','--ignore-scripts','--audit=false','--fund=false'],cwd=frontend,check=True,capture_output=True,text=True)
        entry=frontend/'node_modules/.bin/vite'; target=(entry.parent/os.readlink(entry)).resolve(strict=True)
        payload={'schema_version':1,'source_sha':source_sha,'entry_target':os.readlink(entry),
                 'entry_sha256':sha(target),'vite_package_sha256':sha(frontend/'node_modules/vite/package.json'),
                 'lockfile_sha256':sha(frontend/'package-lock.json')}
    # A generated contract is immutable evidence.  Never replace an existing
    # path: installation is a distinct root-owned no-replace publication.
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o644)
    try:
        data=canonical(payload).encode()
        _write_all(descriptor,data); os.fsync(descriptor)
    finally: os.close(descriptor)
    directory=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try: os.fsync(directory)
    finally: os.close(directory)
    return payload


def load(path, source_sha, *, owner_uid=0):
    path=Path(path)
    info=path.lstat()
    if not path.is_file() or path.is_symlink() or info.st_uid!=owner_uid or info.st_mode & 0o022:
        raise ValueError('origin contract owner or mode is unsafe')
    try: payload=json.loads(path.read_text())
    except (OSError,json.JSONDecodeError) as error: raise ValueError('origin contract is unreadable') from error
    required={'schema_version','source_sha','entry_target','entry_sha256','vite_package_sha256','lockfile_sha256'}
    if (not isinstance(payload,dict) or set(payload)!=required or canonical(payload)!=path.read_text() or
        payload.get('schema_version')!=1 or payload.get('source_sha')!=source_sha or
        payload.get('entry_target')!='../vite/bin/vite.js' or
        any(not isinstance(payload.get(key),str) or not re.fullmatch(r'[0-9a-f]{64}',payload[key])
            for key in ('entry_sha256','vite_package_sha256','lockfile_sha256'))):
        raise ValueError('origin contract does not match audited source')
    return payload,hashlib.sha256(path.read_bytes()).hexdigest()


def install_contract(source,output,source_sha):
    """Publish a validated operator contract once in a root-owned namespace."""
    if os.geteuid()!=0: raise ValueError('origin contract installation requires UID0')
    source=Path(source)
    info=source.lstat()
    if info.st_uid==0: raise ValueError('source must be the operator-generated contract')
    payload,_=load(source,source_sha,owner_uid=info.st_uid)
    return _root_publish(output,payload)



def _root_publish(output,payload):
    if os.geteuid()!=0: raise ValueError('activation receipt creation requires UID0')
    output=Path(output); parent=output.parent; info=parent.stat()
    if parent.is_symlink() or not parent.is_dir() or info.st_uid!=0 or info.st_mode & 0o022:
        raise ValueError('activation receipt parent is unsafe')
    # The root-owned, non-writable final directory is the confinement point:
    # after this check an untrusted writable ancestor cannot replace a child
    # or create the O_EXCL target inside it.  Production passes a directory
    # below root-owned /opt; the private canary intentionally uses an equally
    # controlled root-owned leaf below its disposable test tree.
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        _write_all(fd,canonical(payload).encode())
        os.fsync(fd)
    finally: os.close(fd)
    fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)
    return payload


def _clean_checkout(checkout,source):
    checkout=Path(checkout).resolve(strict=True)
    if checkout.is_symlink() or not checkout.is_dir(): raise ValueError('checkout is unsafe')
    # This runs as root against the jaxsvc-owned published checkout.  Pin the
    # sole allowed safe.directory rather than weakening Git globally.
    git=['git','-c','safe.directory='+str(checkout),'-C',str(checkout)]
    head=subprocess.run([*git,'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    dirty=subprocess.run([*git,'status','--porcelain'],check=True,capture_output=True,text=True).stdout
    if head!=source or dirty: raise ValueError('checkout is not clean audited source')


def _revalidate_checkout(checkout,source,contract):
    """Re-read the selected checkout's Vite bytes after publication of S."""
    checkout=Path(checkout); _clean_checkout(checkout,source)
    frontend=checkout/'frontend'; entry=frontend/'node_modules/.bin/vite'
    if entry.is_symlink() is False or os.readlink(entry)!=contract['entry_target']:
        raise ValueError('published Vite entry differs from origin contract')
    target=(entry.parent/contract['entry_target']).resolve(strict=True)
    package=frontend/'node_modules/vite/package.json'; lockfile=frontend/'package-lock.json'
    if target.is_symlink() or package.is_symlink() or lockfile.is_symlink() or any(not path.is_file() for path in (target,package,lockfile)):
        raise ValueError('published Vite files are unsafe')
    if (sha(target)!=contract['entry_sha256'] or sha(package)!=contract['vite_package_sha256'] or
        sha(lockfile)!=contract['lockfile_sha256']):
        raise ValueError('published Vite bytes differ from origin contract')


def _capture(value):
    if not isinstance(value,dict) or set(value)!={'properties','files','process','unit_paths','http'}:
        raise ValueError('activation capture is incomplete')
    if not isinstance(value['properties'],dict) or not isinstance(value['files'],dict) or not isinstance(value['unit_paths'],list):
        raise ValueError('activation capture has invalid systemd state')
    process=value['process']
    required={'pid','uid','gid','exe','exe_dev','exe_ino','exe_sha256','argv','starttime','cgroup','boot_id','invocation_id'}
    if (not isinstance(process,dict) or set(process)!=required or not isinstance(value['http'],dict) or
        not isinstance(process['boot_id'],str) or not isinstance(process['invocation_id'],str) or
        not isinstance(process['starttime'],str) or value['properties'].get('InvocationID')!=process['invocation_id']):
        raise ValueError('activation capture has invalid process proof')
    return value


def _i0(value,source,contract_digest,config_digest):
    required={'schema_version','kind','source_sha','contract_sha256','settings_sha256','checkout_sha','capture'}
    if (not isinstance(value,dict) or set(value)!=required or value.get('schema_version')!=1 or value.get('kind')!='vite-i0' or
        value.get('source_sha')!=source or value.get('checkout_sha')!=source or
        value.get('contract_sha256')!=contract_digest or value.get('settings_sha256')!=config_digest):
        raise ValueError('I0 does not match audited activation inputs')
    _capture(value['capture']); return value


def seal(settings,checkout,source,contract,output,*,manager=None):
    """Publish I0 after S is on disk and immediately before the Vite restart."""
    payload,digest=load(contract,source,owner_uid=0); _revalidate_checkout(checkout,source,payload)
    if os.geteuid()!=0: raise ValueError('activation receipt creation requires UID0')
    capture=(manager or _manager(settings)).capture(http=True)
    _capture(capture)
    return _root_publish(output,{'schema_version':1,'kind':'vite-i0','source_sha':source,'contract_sha256':digest,
                                 'settings_sha256':settings_digest(settings),'checkout_sha':source,'capture':capture})


def _manager(settings):
    from release_systemd import Manager
    if settings.get('service')!='jax-platform-frontend.service' or settings.get('private_mode'):
        raise ValueError('activation CLI requires the production frontend service')
    return Manager({**settings,'owner_uid':0},False)


def activate(settings,checkout,source,contract,seal_path,output,*,manager=None):
    if os.geteuid()!=0: raise ValueError('activation receipt creation requires UID0')
    payload,digest=load(contract,source,owner_uid=0); _revalidate_checkout(checkout,source,payload)
    config_digest=settings_digest(settings)
    seal_value,seal_digest=load_activation(seal_path,source,owner_uid=0,kind='vite-i0',settings=settings,contract_digest=digest)
    _i0(seal_value,source,digest,config_digest)
    capture=(manager or _manager(settings)).capture(http=True); _capture(capture)
    before=seal_value['capture']['process']; after=capture['process']
    if (before['boot_id']!=after['boot_id'] or before['invocation_id']==after['invocation_id'] or
        before['starttime']==after['starttime']):
        raise ValueError('post-start process does not prove a same-boot restart identity')
    return _root_publish(output,{'schema_version':1,'kind':'vite-activation','source_sha':source,'contract_sha256':digest,
                                 'settings_sha256':config_digest,'i0_sha256':seal_digest,'i0':seal_value,'i1':capture})


def load_activation(path,source,*,owner_uid=0,kind='vite-activation',settings=None,contract_digest=None):
    path=Path(path); info=path.lstat()
    # Receipts are published 0600.  Accepting a broader read mode would make
    # the deployed authority contract differ from the one we actually seal.
    if (not path.is_file() or path.is_symlink() or info.st_uid!=owner_uid or
            (info.st_mode & 0o777)!=0o600):
        raise ValueError('activation receipt owner or mode is unsafe')
    value=json.loads(path.read_text())
    if not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('kind')!=kind or value.get('source_sha')!=source or canonical(value)!=path.read_text():
        raise ValueError('activation receipt does not match audited source')
    if kind=='vite-i0':
        if settings is not None and contract_digest is not None:
            _i0(value,source,contract_digest,settings_digest(settings))
        return value,hashlib.sha256(path.read_bytes()).hexdigest()
    required={'schema_version','kind','source_sha','contract_sha256','settings_sha256','i0_sha256','i0','i1'}
    if set(value)!=required or not isinstance(value.get('contract_sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',value['contract_sha256']):
        raise ValueError('activation receipt does not match audited source')
    if settings is not None and value['settings_sha256']!=settings_digest(settings):
        raise ValueError('activation receipt settings digest differs')
    if contract_digest is not None and value['contract_sha256']!=contract_digest:
        raise ValueError('activation receipt contract digest differs')
    i0=_i0(value['i0'],source,value['contract_sha256'],value['settings_sha256'])
    if value['i0_sha256']!=hashlib.sha256(canonical(i0).encode()).hexdigest():
        raise ValueError('activation receipt I0 digest differs')
    _capture(value['i1'])
    before=i0['capture']['process']; after=value['i1']['process']
    if (before['boot_id']!=after['boot_id'] or before['invocation_id']==after['invocation_id'] or before['starttime']==after['starttime']):
        raise ValueError('activation receipt restart identity differs')
    return value,hashlib.sha256(path.read_bytes()).hexdigest()


def validate_activation(path,source,settings,current,*,owner_uid=0,contract_digest=None):
    """Reaccredit I1 against the exact live capture just before prepare publishes."""
    value,_=load_activation(path,source,owner_uid=owner_uid,settings=settings,contract_digest=contract_digest)
    _capture(current)
    if current!=value['i1']: raise ValueError('current Vite capture differs from activation receipt I1')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('generate','install-contract','seal','activate'),nargs='?',default='generate')
    parser.add_argument('--checkout'); parser.add_argument('--source-sha',required=True)
    parser.add_argument('--output',required=True,type=Path); parser.add_argument('--settings',type=Path)
    parser.add_argument('--contract',type=Path); parser.add_argument('--seal',type=Path)
    parser.add_argument('--source',type=Path)
    args=parser.parse_args(); settings=json.loads(args.settings.read_text()) if args.settings else None
    if args.action=='generate':
        if not args.checkout or settings is None: parser.error('generate requires --checkout --settings')
        result=generate(args.checkout,args.source_sha,args.output,settings)
    elif args.action=='install-contract':
        if not args.source: parser.error('install-contract requires --source')
        result=install_contract(args.source,args.output,args.source_sha)
    elif args.action=='seal':
        if not args.checkout or not args.contract or settings is None: parser.error('seal requires --checkout --contract --settings')
        result=seal(settings,args.checkout,args.source_sha,args.contract,args.output)
    else:
        if settings is None or not args.checkout or not args.contract or not args.seal: parser.error('activate requires --settings --checkout --contract --seal')
        result=activate(settings,args.checkout,args.source_sha,args.contract,args.seal,args.output)
    print(json.dumps(result,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
