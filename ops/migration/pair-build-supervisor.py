#!/usr/bin/python3 -I
"""Root supervisor for a jaxbuild pair build. Root never opens Git objects."""
from __future__ import annotations
import argparse, hashlib, json, os, stat, subprocess
from pathlib import Path

def die(message:str)->None: raise SystemExit('pair-build-supervisor: '+message)
def regular(path:Path, mode:int|None=None)->bytes:
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=0 or st.st_gid!=0 or st.st_mode&0o022 or (mode is not None and stat.S_IMODE(st.st_mode)!=mode): die('unsafe trusted dependency')
  return b''.join(iter(lambda:os.read(fd,1048576),b''))
 finally: os.close(fd)
def load_request(path:Path)->dict:
 raw=regular(path,0o600)
 try: x=json.loads(raw)
 except (UnicodeDecodeError, json.JSONDecodeError) as exc:
  raise SystemExit('pair-build-supervisor: invalid request') from exc
 required={'schema','nonce','platform','platform_commit','jax','jax_commit','platform_predecessor','jax_predecessor','inputs','wheelhouse_sha256','lock_sha256','out','bootstrap_predecessor','builder_sha256','python'}
 if set(x)!=required or x.get('schema')!='axioma.pair-build-request/v1' or not isinstance(x.get('bootstrap_predecessor'),bool): die('invalid request')
 for k,n in [('nonce',64),('platform_commit',40),('jax_commit',40),('wheelhouse_sha256',64),('lock_sha256',64),('builder_sha256',64)]:
  if not isinstance(x[k],str) or len(x[k])!=n or any(c not in '0123456789abcdef' for c in x[k]): die('invalid request')
 if not isinstance(x['python'],str) or not x['python'].startswith('/'): die('invalid Python request')
 if x['bootstrap_predecessor'] != (x['platform_predecessor'] is None and x['jax_predecessor'] is None): die('invalid predecessor request')
 return x
def main()->None:
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--builder',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True);a=p.parse_args()
 if os.geteuid()!=0: die('root required')
 r=load_request(a.request); code=regular(a.builder,0o755)
 if hashlib.sha256(code).hexdigest()!=r['builder_sha256']: die('builder digest mismatch')
 read_fd,write_fd=os.pipe(); os.set_inheritable(write_fd,True)
 args=['/usr/sbin/runuser','-u','jaxbuild','--','/usr/bin/python3','-I','-S',str(a.builder),'--platform',r['platform'],'--platform-commit',r['platform_commit'],'--jax',r['jax'],'--jax-commit',r['jax_commit'],'--out',r['out'],'--inputs',r['inputs'],'--wheelhouse-sha256',r['wheelhouse_sha256'],'--lock-sha256',r['lock_sha256'],'--request-nonce',r['nonce'],'--receipt-fd',str(write_fd),'--python',r['python']]
 if r['bootstrap_predecessor']: args.append('--bootstrap-predecessor')
 else: args += ['--platform-predecessor',r['platform_predecessor'],'--jax-predecessor',r['jax_predecessor']]
 child=subprocess.Popen(args,pass_fds=(write_fd,),stdin=subprocess.DEVNULL);os.close(write_fd); raw=os.read(read_fd,65536);os.close(read_fd)
 if child.wait()!=0: die('builder failed')
 try: receipt=json.loads(raw)
 except (UnicodeDecodeError, json.JSONDecodeError) as exc:
  raise SystemExit('pair-build-supervisor: invalid builder receipt') from exc
 wanted={'schema':'axioma.pair-build-receipt/v1','nonce':r['nonce'],'platform_commit':r['platform_commit'],'jax_commit':r['jax_commit'],'builder_sha256':r['builder_sha256'],'python':r['python']}
 if any(receipt.get(k)!=v for k,v in wanted.items()): die('receipt binding mismatch')
 out=Path(r['out']);st=out.stat()
 if receipt.get('output_dev')!=st.st_dev or receipt.get('output_ino')!=st.st_ino: die('output identity mismatch')
 
 fd=os.open(out/'pair-manifest.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1: die('unsafe output manifest')
  manifest=b''.join(iter(lambda:os.read(fd,1048576),b''))
 finally: os.close(fd)
 if hashlib.sha256(manifest).hexdigest()!=receipt.get('manifest_sha256'): die('receipt manifest mismatch')
 fd=os.open(a.receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try: os.write(fd,raw);os.fsync(fd);os.fchown(fd,0,0)
 finally: os.close(fd)
if __name__=='__main__':main()
