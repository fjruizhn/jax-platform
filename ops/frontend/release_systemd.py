"""The sole systemd/process/HTTP boundary, injectable for deterministic faults."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
import re
from pathlib import Path
import subprocess
import time
from release_storage import snapshot_managed, fingerprint

PROPERTIES=('FragmentPath','DropInPaths','ExecStart','ExecStartPre','User','Group','WorkingDirectory',
            'ActiveState','SubState','Result','UnitFileState','MainPID','ControlPID','ControlGroup','InvocationID',
            'Type','KillMode','RuntimeDirectory','RuntimeDirectoryMode','StandardOutput','StandardError','NeedDaemonReload')
STATIC=('ExecStart','ExecStartPre','User','Group','WorkingDirectory','Type','KillMode','RuntimeDirectory','RuntimeDirectoryMode','StandardOutput','StandardError')


class Manager:
    def __init__(self,settings,user): self.settings=settings; self.user=user
    def ctl(self,*args):
        result=subprocess.run(['systemctl',*(['--user'] if self.user else []),*args],capture_output=True,text=True)
        if result.returncode: raise RuntimeError('systemctl '+args[0]+': '+result.stderr.strip())
        return result.stdout
    def properties(self):
        data=self.ctl('show',self.settings['service'],*['--property='+key for key in PROPERTIES])
        values={}
        for line in data.splitlines():
            if '=' not in line: continue
            key,value=line.split('=',1)
            if key in ('ExecStart','ExecStartPre') and key in values: values[key]+=' '+value
            else: values[key]=value
        values.setdefault('ExecStartPre','')
        values.setdefault('RuntimeDirectory','')
        if any(key not in values for key in PROPERTIES) or not values['FragmentPath']:
            raise ValueError('unit is missing or incomplete: '+repr(values))
        if values['ActiveState'] not in ('active','inactive','failed'):
            raise ValueError('unit must be stable active or inactive')
        for key in ('ExecStart','ExecStartPre'):
            raw=values[key]
            commands=re.findall(r'\{ path=(.*?) ; argv\[\]=(.*?) ; ignore_errors=(.*?) ;',raw)
            if raw and not commands: raise ValueError('unrecognized systemd execution properties')
            values[key]=' '.join('{ path='+path+' ; argv[]='+argv+' ; ignore_errors='+ignore+' ; }' for path,argv,ignore in commands)
        return values
    def unit_paths(self):
        return self.ctl('show','--property=UnitPath').strip().partition('=')[2].split()

    def unit_files(self,properties, *, unit_paths=None):
        paths={Path(properties['FragmentPath']),*[Path(p) for p in properties['DropInPaths'].split()]}
        # Detect not-yet-reloaded dropins too, including dash-prefix and type
        # directories in every manager search path. Only read regular .conf.
        unit=self.settings['service']
        names={unit+'.d','service.d'}
        stem=unit.removesuffix('.service')
        for index,char in enumerate(stem):
            if char=='-': names.add(stem[:index+1]+'.service.d')
        search=list(self.unit_paths() if unit_paths is None else unit_paths)
        search.append(str(Path(properties['FragmentPath']).parent))
        for parent in search:
            for name in names:
                directory=Path(parent)/name
                if directory.is_symlink(): raise ValueError('symlink unit dropin directory')
                if directory.exists():
                    for path in directory.glob('*.conf'): paths.add(path)
        result={str(Path(parent)/unit):{'absent':True} for parent in search}
        paths.update(Path(parent)/unit for parent in search if (Path(parent)/unit).exists() or (Path(parent)/unit).is_symlink())
        for path in sorted(paths):
            if not path.exists() and not path.is_symlink():
                # A loaded drop-in can disappear from disk between show and
                # capture. Preserve that fact for coherence instead of
                # crashing before the controller can refuse the drift.
                result[str(path)]={'absent':True}
                continue
            uid=path.lstat().st_uid
            if uid not in (0,self.settings['owner_uid']): raise ValueError('foreign unit file owner')
            result[str(path)]=fingerprint(path,uid=uid)
        return result
    def coherent(self,capture, *, allowed_drop=None):
        properties=capture['properties']
        search=capture['unit_paths']
        candidates=[str(Path(parent)/self.settings['service']) for parent in search]
        selected=next((path for path in candidates if not capture['files'][path].get('absent')),None)
        if selected!=properties['FragmentPath']: raise ValueError('loaded fragment differs from disk selection')
        names=['service.d']
        stem=self.settings['service'].removesuffix('.service')
        names.extend(stem[:index+1]+'.service.d' for index,char in enumerate(stem) if char=='-')
        names.append(self.settings['service']+'.d')
        dropins={}
        for name in names:
            for parent in reversed(search):
                directory=Path(parent)/name
                for path in capture['files']:
                    if Path(path).parent==directory and path.endswith('.conf'):
                        dropins[Path(path).name]=path
        selected_drop=[dropins[name] for name in sorted(dropins)]
        loaded=properties['DropInPaths'].split()
        if allowed_drop:
            selected_drop=[path for path in selected_drop if path!=allowed_drop]
            loaded=[path for path in loaded if path!=allowed_drop]
        if loaded!=selected_drop: raise ValueError('loaded dropins differ from disk selection')
        if properties['NeedDaemonReload']!='no' and not allowed_drop:
            raise ValueError('foreign NeedDaemonReload; refuse without reload')

    def process(self,properties,boot_id=None):
        if boot_id is None: boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        pid=int(properties['MainPID'])
        if properties['ActiveState'] in ('inactive','failed'):
            if pid: raise ValueError('inactive unit has a PID')
            return None
        if not pid: raise ValueError('active unit has no MainPID')
        proc=Path('/proc')/str(pid)
        info=proc.stat()
        cmdline=(proc/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        argv=[a.decode('utf8') for a in cmdline]
        executable=os.readlink(proc/'exe')
        descriptor=os.open(proc/'exe',os.O_RDONLY|os.O_CLOEXEC)
        try:
            executable_stat=os.fstat(descriptor)
            digest=hashlib.sha256()
            while block:=os.read(descriptor,1024*1024): digest.update(block)
        finally: os.close(descriptor)
        cgroup=(proc/'cgroup').read_text()
        groups=[line.split(':',2)[2] for line in cgroup.splitlines()]
        if not any(self.settings['service'] in Path(group).parts for group in groups):
            raise ValueError('MainPID belongs to a different unit')
        starttime=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
        return {'pid':pid,'uid':info.st_uid,'gid':info.st_gid,'exe':executable,'exe_dev':executable_stat.st_dev,
                'exe_ino':executable_stat.st_ino,'exe_sha256':digest.hexdigest(),'argv':argv,'starttime':starttime,
                'cgroup':cgroup,'boot_id':boot_id,'invocation_id':properties['InvocationID']}
    def http(self,path='/index.html'):
        connection=http.client.HTTPConnection(self.settings.get('proof_connect_host','127.0.0.1'),self.settings['listen_port'],timeout=2)
        try:
            connection.request('GET',path,headers={'Host':self.settings.get('proof_host','localhost')})
            response=connection.getresponse(); body=response.read()
            return {'status':response.status,'mime':response.getheader('Content-Type','').split(';')[0],
                    'sha':response.getheader('X-JAX-Frontend-SHA'), 'digest':hashlib.sha256(body).hexdigest()}
        finally: connection.close()
    def capture(self, *, http=True):
        deadline=time.monotonic()+5
        while True:
            boot_a=Path('/proc/sys/kernel/random/boot_id').read_text().strip(); properties=self.properties()
            try: process=self.process(properties,boot_a)
            except (PermissionError,FileNotFoundError):
                if time.monotonic()>=deadline: raise
                time.sleep(.02); continue
            break
        paths=self.unit_paths(); files=self.unit_files(properties,unit_paths=paths)
        boot_b=Path('/proc/sys/kernel/random/boot_id').read_text().strip(); after=self.properties()
        stable=('MainPID','ControlPID','InvocationID','ControlGroup','ActiveState','SubState','Result','FragmentPath','DropInPaths','NeedDaemonReload')
        if boot_a!=boot_b or any(properties[key]!=after[key] for key in stable): raise ValueError('torn systemd/process observation')
        result={'properties':after,'files':files,'process':process,'unit_paths':paths}
        result['http']=None
        if http and properties['ActiveState']=='active':
            while True:
                try: proof=self.http()
                except (ConnectionRefusedError,ConnectionResetError):
                    if time.monotonic()>=deadline: raise
                    time.sleep(.02)
                    continue
                # A listener response is evidence only if a complete later
                # capture still names the same unit/process/files state.
                later=self.capture(http=False)
                if (later['properties']==result['properties'] and later['files']==result['files'] and
                    later['unit_paths']==result['unit_paths'] and same_process(later['process'],result['process'])):
                    result=later; result['http']=proof; break
                if time.monotonic()>=deadline: raise ValueError('HTTP proof raced unit observation')
                time.sleep(.02)
        return result
    def reload(self): self.ctl('daemon-reload')
    def restart(self): self.ctl('restart',self.settings['service'])
    def stop(self): self.ctl('stop',self.settings['service'])
    def verify_unit(self,path):
        subprocess.run(['systemd-analyze',*(['--user'] if self.user else []),'verify',str(path)],check=True,capture_output=True,text=True,
                       env={**os.environ,'SYSTEMD_UNIT_PATH':str(path.parent)+':'+('/usr/lib/systemd/user' if self.user else '/usr/lib/systemd/system')})


def same_process(actual,expected):
    if actual is None or expected is None: return actual is expected
    # This predicate is used only for a pre-mutation observation of the
    # original service.  A restart performed by this controller is proved by
    # the nginx-specific branch in Controller._known; accepting a new Vite
    # PID here would let an external restart race a transaction.
    return all(actual.get(key)==expected.get(key) for key in ('pid','starttime','uid','gid','exe','exe_dev','exe_ino','exe_sha256','argv','cgroup','boot_id','invocation_id'))


def nginx_process(process,settings,config):
    if process is None or process['uid']!=settings['process_uid'] or process['exe']!=settings['nginx_binary']: return False
    args=process['argv']
    # nginx replaces its argv with its master-process title. The exact title
    # must match the invocation, rather than accepting any PID named nginx.
    invocation=[settings['nginx_binary'],'-g','daemon off;','-c',str(config)]
    return args==invocation or args==['nginx: master process '+' '.join(invocation)]


def execution_commands(text):
    if not text: return []
    pattern=r'\{ path=(.*?) ; argv\[\]=(.*?) ; ignore_errors=(yes|no) ; \}'
    matches=list(re.finditer(pattern,text))
    if not matches or ' '.join(match.group(0) for match in matches)!=text:
        raise ValueError('unsupported effective execution properties')
    result=[]
    for match in matches:
        path,argv,ignore=match.groups()
        result.append({'path':path,'argv':argv.split(' '),'ignore_errors':ignore=='yes'})
    return result
