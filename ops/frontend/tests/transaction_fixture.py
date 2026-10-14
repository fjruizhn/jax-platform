"""Private test resources; nothing in this module operates a production unit."""
from __future__ import annotations
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from frontend_release import Controller
from release_storage import snapshot_managed
from release_systemd import Manager


class Fixture:
    def __init__(self, *, real=False,active=True):
        self.tmp=tempfile.TemporaryDirectory(prefix='p100-controller-'); self.root=Path(self.tmp.name)
        self.umask=os.umask(0o022)
        for name in ('frontend','nginx','units','staging'): (self.root/name).mkdir(mode=0o755)
        source=os.environ.get('P100_TEST_RELEASE_DIR')
        if real and not source: raise RuntimeError('P100_TEST_RELEASE_DIR must point to an actually built release')
        if source:
            self.release=self.root/'frontend/releases'/Path(source).name
            self.release.parent.mkdir(mode=0o755); shutil.copytree(source,self.release)
            self.manifest=json.loads((self.release/'manifest.json').read_text())
        else:
            # Explicit unit-test fixture, not a claimed build or source artifact.
            sha=hashlib.sha1(b'P100 deterministic unit test fixture').hexdigest()
            self.release=self.root/'frontend/releases'/sha
            (self.release/'dist/assets').mkdir(parents=True,mode=0o755)
            for directory in (self.release.parent,self.release,self.release/'dist'): directory.chmod(0o755)
            (self.release/'dist/index.html').write_bytes(b'<!doctype html><html>unit fixture</html>')
            (self.release/'dist/assets/app-abcdefgh.js').write_bytes(b'console.log("fixture");')
            (self.release/'package-lock.json').write_bytes(b'{}')
            files={'index.html':hashlib.sha256(b'<!doctype html><html>unit fixture</html>').hexdigest(),
                   'assets/app-abcdefgh.js':hashlib.sha256(b'console.log("fixture");').hexdigest()}
            tree=hashlib.sha256(''.join(k+' '+v+'\n' for k,v in sorted(files.items())).encode()).hexdigest()
            self.manifest={'schema_version':1,'source_sha':sha,'node_version':'24.16.0','npm_version':'11.13.0','vite_version':'6.4.3',
                           'lockfile_sha256':hashlib.sha256(b'{}').hexdigest(),'files':files,'tree_digest':tree,
                           'proxy_contract':{'api_prefix':'/api','ws_prefix':'/ws','scheme':'http','ws_scheme':'ws',
                                             'host':'localhost','port':8080,'allowed_hosts':['axioma-ia.io','www.axioma-ia.io']}}
            (self.release/'manifest.json').write_text(json.dumps(self.manifest))
        self.service='p100-contract-'+os.urandom(6).hex()+'.service'
        self.port=self.free_port()
        self.backend=http.server.ThreadingHTTPServer(('127.0.0.1',0),Backend)
        self.thread=threading.Thread(target=self.backend.serve_forever,daemon=True); self.thread.start()
        self.override={'scheme':'http','host':'localhost','port':self.backend.server_port}
        self.settings=json.loads((Path(__file__).resolve().parents[1]/'settings.json').read_text())
        self.settings.update(service=self.service,private_mode=True,owner_uid=os.getuid(),
            nginx_sha256=hashlib.sha256(Path(self.settings['nginx_binary']).read_bytes()).hexdigest(),
            release_root=str(self.root/'frontend'),nginx_root=str(self.root/'nginx'),systemd_dir=str(self.root/'units'),
            user_runtime_dir=os.environ['XDG_RUNTIME_DIR'],runtime_directory=self.service.removesuffix('.service'),
            listen_host='127.0.0.1',listen_port=self.port)
        self.settings_path=self.root/'settings.json'; self.settings_path.write_text(json.dumps(self.settings)); self.settings_path.chmod(0o600)
        self.unit=self.root/'units'/self.service
        self.original_dir=self.root/'original'; self.original_dir.mkdir(mode=0o755)
        (self.original_dir/'index.html').write_text('<!doctype html><html><body>VITE ORIGINAL CANARY</body></html>')
        vite=os.environ.get('P100_TEST_VITE_MODULE',str(Path(__file__).resolve().parents[3]/'frontend/node_modules/vite/dist/node/index.js'))
        self.script=self.root/'vite.mjs'
        self.script.write_text('import {createServer} from '+json.dumps(vite)+';\n'+
            'const s=await createServer({configFile:false,root:'+json.dumps(str(self.original_dir))+',server:{host:"127.0.0.1",port:'+str(self.port)+
            ',strictPort:true,allowedHosts:["localhost"],proxy:{"/api":{target:"http://localhost:'+str(self.backend.server_port)+'",changeOrigin:true},'+
            '"/ws":{target:"ws://localhost:'+str(self.backend.server_port)+'",changeOrigin:true,ws:true}}}}); await s.listen();\n')
        self.node=os.path.realpath(shutil.which('node'))
        vite_path=Path(vite)
        self.settings['original_contract']={'kind':'private-vite-canary','node_binary':self.node,'entry':str(self.script),
            'entry_sha256':hashlib.sha256(self.script.read_bytes()).hexdigest(),'vite_module':str(vite_path),
            'vite_sha256':hashlib.sha256(vite_path.read_bytes()).hexdigest()}
        self.settings_path.write_text(json.dumps(self.settings))
        self.unit.write_text('[Unit]\nDescription=P100 private transaction canary\n[Service]\nType=simple\nWorkingDirectory='+str(self.original_dir)+
                            '\nExecStart='+self.node+' '+str(self.script)+'\n[Install]\nWantedBy=default.target\n'); self.unit.chmod(0o644)
        dropdir=self.unit.with_name(self.service+'.d'); dropdir.mkdir(mode=0o755)
        (dropdir/'10-canary.conf').write_text('[Service]\nEnvironment=P100_CANARY_ONE=one\n'); (dropdir/'10-canary.conf').chmod(0o640)
        (dropdir/'20-canary.conf').write_text('[Service]\nEnvironment=P100_CANARY_TWO=two\nRestartSec=3\n'); (dropdir/'20-canary.conf').chmod(0o644)
        self.real=real
        if real:
            runtime_units=Path(os.environ['XDG_RUNTIME_DIR'])/'systemd/user'
            runtime_units.mkdir(mode=0o755,exist_ok=True)
            target=runtime_units/self.service
            shutil.copy2(self.unit,target)
            shutil.copytree(dropdir,target.with_name(self.service+'.d'))
            self.unit=target; dropdir=target.with_name(self.service+'.d')
            self.settings['systemd_dir']=str(runtime_units)
            self.settings_path.write_text(json.dumps(self.settings))
            self.manager=Manager({**self.settings,'process_uid':os.getuid()},True)
            self.manager.reload()
            if active:
                self.manager.restart()
                for attempt in range(100):
                    try: self.manager.http(); break
                    except OSError: time.sleep(.05)
                else: raise RuntimeError('private Vite did not start')
        else: self.manager=FakeManager(self.settings,self.unit,active)
        self.controller=Controller(self.settings,user=True,manager=self.manager)
        try: self.original=self.controller._capture()
        except BaseException:
            self.cleanup(); raise
        self.original_files=snapshot_managed([self.unit,*sorted(dropdir.glob('*.conf'))])
    @staticmethod
    def free_port():
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]
    def stage(self): return self.root/'staging'/os.urandom(6).hex()
    def apply(self): return self.controller.apply(self.release,self.stage(),self.override)
    def cleanup(self):
        if self.real:
            for args in (('stop',self.service),('disable','--runtime',self.service)):
                self.manager.ctl(*args)
            try:
                self.manager.ctl('reset-failed',self.service)
            except RuntimeError as error:
                # fail-soft: stop/disable may have unloaded this private unit
                # before reset-failed; any other manager failure is material.
                if 'not loaded' not in str(error): raise
            self.unit.unlink(missing_ok=True)
            shutil.rmtree(self.unit.with_name(self.service+'.d'),ignore_errors=True)
            self.manager.reload()
        self.backend.shutdown(); self.backend.server_close(); self.thread.join()
        if hasattr(self,'controller'): self.controller.lock_path.unlink(missing_ok=True)
        self.tmp.cleanup(); os.umask(self.umask)


class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'{"fixture":"p100"}'; self.send_response(404); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass


class FakeManager:
    """Simulates ONLY the external manager; controller and every file remain real."""
    def __init__(self,settings,unit,active):
        self.s=settings; self.settings=settings; self.unit=unit; self.active='active' if active else 'inactive'; self.enabled='disabled'; self.pid=400
        contract=settings['original_contract']; start='{ path='+contract['node_binary']+' ; argv[]='+contract['node_binary']+' '+contract['entry']+' ; ignore_errors=no ; }'
        self.old={'ExecStart':start,'ExecStartPre':'', 'User':'','Group':'','WorkingDirectory':str(unit.parent.parent/'original'),'Type':'simple','KillMode':'control-group','RuntimeDirectory':'','RuntimeDirectoryMode':'0755','StandardOutput':'journal','StandardError':'inherit'}
        self.effective=dict(self.old); self.loaded_drops=[str(p) for p in sorted(unit.with_name(unit.name+'.d').glob('*.conf'))]
        self.loaded_files=self.unit_files({'FragmentPath':str(unit),'DropInPaths':' '.join(self.loaded_drops)})
        self.process_kind='vite'; self.bad_http=False; self.bad_process=False; self.bad_gid=False; self.bad_hash=False; self.fail_verify=False; self.stop_calls=0; self.restart_calls=0; self.config=Path(self.s['nginx_root'])/'nginx.conf'
    def properties(self):
        return {**self.effective,'FragmentPath':str(self.unit),'DropInPaths':' '.join(self.loaded_drops),'NeedDaemonReload':'yes' if self.unit_files({'FragmentPath':str(self.unit),'DropInPaths':''})!=self.loaded_files else 'no',
                'ActiveState':self.active,'SubState':'running' if self.active=='active' else 'dead','Result':'success','UnitFileState':self.enabled,
                'MainPID':str(self.pid) if self.active=='active' else '0','ControlPID':'0','ControlGroup':'/private/'+self.s['service'],'InvocationID':'fixture-'+str(self.pid)}
    def unit_paths(self): return [str(self.unit.parent)]
    def unit_files(self,properties): return Manager.unit_files(self,properties)
    def coherent(self,capture,**kwargs): return Manager.coherent(self,capture,**kwargs)
    def process(self,properties):
        if self.active=='inactive': return None
        config=self.config
        nginx=self.s['nginx_binary']
        executable=nginx if self.process_kind=='nginx' else self.s['original_contract']['node_binary']
        stat=os.stat(executable)
        return {'pid':self.pid,'uid':os.getuid(),'gid':os.getgid()+int(self.bad_gid),'exe':executable,'exe_dev':stat.st_dev,'exe_ino':stat.st_ino,
                'exe_sha256':'0'*64 if self.bad_hash else hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
                'argv':['wrong'] if self.bad_process else ([nginx,'-g','daemon off;','-c',str(config)] if self.process_kind=='nginx' else [self.s['original_contract']['node_binary'],self.s['original_contract']['entry']]),
                'starttime':str(self.pid),'cgroup':'/private/'+self.s['service'],'boot_id':'fixture-boot','invocation_id':'fixture-'+str(self.pid)}
    def http(self,path='/index.html'):
        if self.process_kind=='vite' or self.bad_http:
            return {'status':200,'mime':'text/html','sha':None,'digest':hashlib.sha256(b'Vite original').hexdigest()}
        if path=='/api': return {'status':404,'mime':'application/json','sha':None,'digest':'unused'}
        current=Path(self.s['release_root'])/'current'
        return {'status':200,'mime':'text/html','sha':current.resolve().name,'digest':hashlib.sha256((current/'dist/index.html').read_bytes()).hexdigest()}
    def capture(self, *, http=True):
        properties=self.properties()
        return {'properties':properties,'files':self.unit_files(properties),'process':self.process(properties),'unit_paths':self.unit_paths(),
                'http':self.http() if http and self.active=='active' else None}
    def reload(self):
        self.loaded_files=self.unit_files({'FragmentPath':str(self.unit),'DropInPaths':''})
        self.loaded_drops=[str(p) for p in sorted(self.unit.with_name(self.unit.name+'.d').glob('*.conf'))]
        drop=self.unit.with_name(self.unit.name+'.d')/'zz-nginx-estatico.conf'
        if not drop.exists(): self.effective=dict(self.old); return
        lines=drop.read_text().splitlines()
        starts=[line.partition('=')[2] for line in lines if line.startswith('ExecStart=') and line!='ExecStart=']
        pres=[line.partition('=')[2] for line in lines if line.startswith('ExecStartPre=') and line!='ExecStartPre=']
        show=lambda value:'{ path='+shlex.split(value)[0]+' ; argv[]='+' '.join(shlex.split(value))+' ; ignore_errors=no ; }'
        argv=shlex.split(starts[0]); self.config=Path(argv[argv.index('--config')+1])
        self.effective={'ExecStart':show(starts[0]),'ExecStartPre':' '.join(show(value) for value in pres),'User':'','Group':'','WorkingDirectory':'/','Type':'simple','KillMode':'control-group','RuntimeDirectory':self.s['runtime_directory'],'RuntimeDirectoryMode':'0700','StandardOutput':'journal','StandardError':'journal'}
    def stop(self): self.stop_calls+=1; self.active='inactive'
    def restart(self):
        self.restart_calls+=1; self.active='active'; self.pid+=1; self.process_kind='nginx' if self.effective['WorkingDirectory']=='/' else 'vite'
        self.bad_process=False; self.bad_http=False
    def verify_unit(self,path):
        if self.fail_verify: raise RuntimeError('injected systemd verify failure')
        # Exercise the actual analyzer even though service lifecycle is simulated.
        subprocess.run(['systemd-analyze','--user','verify',str(path)],check=True,capture_output=True,text=True,
            env={**os.environ,'SYSTEMD_UNIT_PATH':str(path.parent)+':/usr/lib/systemd/user'})
