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
            'ActiveState','UnitFileState','MainPID','Type','KillMode')
STATIC=('ExecStart','ExecStartPre','User','Group','WorkingDirectory','Type','KillMode')


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
    def unit_files(self,properties):
        paths={Path(properties['FragmentPath']),*[Path(p) for p in properties['DropInPaths'].split()]}
        # Detect not-yet-reloaded dropins too, including dash-prefix and type
        # directories in every manager search path. Only read regular .conf.
        unit=self.settings['service']
        names={unit+'.d','service.d'}
        stem=unit.removesuffix('.service')
        for index,char in enumerate(stem):
            if char=='-': names.add(stem[:index+1]+'.service.d')
        search=self.ctl('show','--property=UnitPath').strip().partition('=')[2].split()
        search.append(str(Path(properties['FragmentPath']).parent))
        for parent in search:
            for name in names:
                directory=Path(parent)/name
                if directory.is_symlink(): raise ValueError('symlink unit dropin directory')
                if directory.exists():
                    for path in directory.glob('*.conf'): paths.add(path)
        result={}
        for path in sorted(paths):
            uid=path.lstat().st_uid
            if uid not in (0,self.settings['owner_uid']): raise ValueError('foreign unit file owner')
            result[str(path)]=fingerprint(path,uid=uid)
        return result
    def process(self,properties):
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
        cgroup=(proc/'cgroup').read_text()
        groups=[line.split(':',2)[2] for line in cgroup.splitlines()]
        if not any(self.settings['service'] in Path(group).parts for group in groups):
            raise ValueError('MainPID belongs to a different unit')
        starttime=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
        return {'pid':pid,'uid':info.st_uid,'exe':executable,'argv':argv,'starttime':starttime,'cgroup':cgroup}
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
            properties=self.properties()
            try: process=self.process(properties)
            except (PermissionError,FileNotFoundError):
                if time.monotonic()>=deadline: raise
                time.sleep(.02); continue
            break
        result={'properties':properties,'files':self.unit_files(properties),'process':process}
        result['http']=None
        if http and properties['ActiveState']=='active':
            while True:
                try: result['http']=self.http(); break
                except (ConnectionRefusedError,ConnectionResetError):
                    if time.monotonic()>=deadline: raise
                    time.sleep(.02)
            # The listener proof may have waited for an ExecStart wrapper to
            # exec. Bind process identity to the observation after that wait.
            properties=self.properties(); result['properties']=properties
            result['process']=self.process(properties)
        return result
    def reload(self): self.ctl('daemon-reload')
    def restart(self): self.ctl('restart',self.settings['service'])
    def stop(self): self.ctl('stop',self.settings['service'])
    def verify_unit(self,path):
        subprocess.run(['systemd-analyze',*(['--user'] if self.user else []),'verify',str(path)],check=True,capture_output=True,text=True,
                       env={**os.environ,'SYSTEMD_UNIT_PATH':str(path.parent)+':'+('/usr/lib/systemd/user' if self.user else '/usr/lib/systemd/system')})


def same_process(actual,expected):
    if actual is None or expected is None: return actual is expected
    # PID/starttime may change on a legitimate restart; argv/executable/UID may not.
    return all(actual[key]==expected[key] for key in ('uid','exe','argv')) and (
        actual['pid']!=expected['pid'] or actual['starttime']==expected['starttime'])


def nginx_process(process,settings,config):
    if process is None or process['uid']!=settings['process_uid'] or process['exe']!=settings['nginx_binary']: return False
    args=process['argv']
    # nginx replaces its argv with its master-process title. The exact title
    # must match the invocation, rather than accepting any PID named nginx.
    invocation=[settings['nginx_binary'],'-g','daemon off;','-c',str(config)]
    return args==invocation or args==['nginx: master process '+' '.join(invocation)]
