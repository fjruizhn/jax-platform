"""Filesystem and HTTP regressions for Phase A (no live service mutations)."""
import hashlib
import base64
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from release_manifest import verify_release
from nginx_config import render

SHA = 'ab' * 20

def sha(data):
    return hashlib.sha256(data).hexdigest()

def settings(root):
    return {**json.loads((TOOLS / 'settings.json').read_text()),
            'private_mode': True, 'owner_uid': os.getuid(),
            'release_root': str(root), 'runtime_path': str(root / 'run'),
            'listen_host': '127.0.0.1', 'listen_port': 18099}

def fixture(root):
    release = root / 'releases' / SHA
    (release / 'dist/assets').mkdir(parents=True)
    files = {'index.html': b'<html>SPA</html>', 'favicon.svg': b'<svg/>',
             'assets/app-12345678.js': b'console.log(1)'}
    for name, data in files.items():
        (release / 'dist' / name).write_bytes(data)
    lock = b'{"lockfileVersion":3}\n'
    (release / 'package-lock.json').write_bytes(lock)
    inv = {name: sha(data) for name, data in files.items()}
    manifest = {'schema_version': 1, 'source_sha': SHA,
                'node_version': '24.16.0', 'npm_version': '11.13.0', 'vite_version': '6.4.3',
                'lockfile_sha256': sha(lock), 'files': inv,
                'tree_digest': sha(''.join(f'{k} {v}\n' for k,v in sorted(inv.items())).encode()),
                'proxy_contract': {'api_prefix': '/api', 'ws_prefix': '/ws', 'scheme': 'http',
                                   'ws_scheme': 'ws', 'host': 'localhost', 'port': 8080,
                                   'allowed_hosts': ['axioma-ia.io', 'www.axioma-ia.io']}}
    (release / 'manifest.json').write_text(json.dumps(manifest))
    release.parent.chmod(0o755)
    for path in [release, *release.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    return release, manifest

class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release, self.manifest = fixture(self.root)
        self.settings = settings(self.root)
    def tearDown(self): self.tmp.cleanup()
    def write(self): (self.release / 'manifest.json').write_text(json.dumps(self.manifest))
    def test_valid_inventory_with_directories(self):
        self.assertEqual(verify_release(self.release, self.settings, SHA), self.manifest)
    def test_schema_required_and_exact(self):
        for value in (None, 2, True):
            with self.subTest(value=value):
                self.manifest['schema_version'] = value; self.write()
                with self.assertRaises(ValueError): verify_release(self.release, self.settings)
    def test_corrupt_missing_extra_lock_and_version(self):
        for mutation in ('corrupt', 'missing', 'extra', 'emptydir', 'lock', 'version', 'digest', 'asset', 'manifestmissing'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                r,m = fixture(Path(temp)); s = settings(Path(temp))
                if mutation == 'corrupt': (r/'dist/index.html').write_bytes(b'changed')
                if mutation == 'missing': (r/'dist/index.html').unlink()
                if mutation == 'extra': (r/'dist/extra.txt').write_bytes(b'new')
                if mutation == 'emptydir': (r/'dist/unexpected').mkdir(mode=0o755)
                if mutation == 'asset': (r/'dist/assets/unhashed.js').write_bytes(b'new')
                if mutation == 'manifestmissing': (r/'manifest.json').unlink()
                if mutation == 'lock': (r/'package-lock.json').write_bytes(b'changed')
                if mutation == 'version': m['vite_version']='0'; (r/'manifest.json').write_text(json.dumps(m))
                if mutation == 'digest': m['tree_digest']='0'*64; (r/'manifest.json').write_text(json.dumps(m))
                with self.assertRaises(ValueError): verify_release(r,s)
    def test_symlinks_fifo_modes_owner_and_identity(self):
        for mutation in ('filelink','parentlink','fifo','filemode','dirmode','parentmode','owner','identity','malformed'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); r,m=fixture(root); s=settings(root)
                if mutation == 'filelink': (r/'dist/index.html').unlink(); (r/'dist/index.html').symlink_to(r/'package-lock.json')
                if mutation == 'parentlink':
                    link=root/'link'; link.symlink_to(r.parent); r=link/SHA
                if mutation == 'fifo': os.mkfifo(r/'dist/fifo')
                if mutation == 'filemode': (r/'manifest.json').chmod(0o666)
                if mutation == 'dirmode': (r/'dist').chmod(0o777)
                if mutation == 'parentmode': r.parent.chmod(0o777)
                if mutation == 'owner': s['owner_uid']=os.getuid()+1
                if mutation == 'identity': m['source_sha']='c'*40; (r/'manifest.json').write_text(json.dumps(m))
                if mutation == 'malformed': m['files']['index.html']='bad'; (r/'manifest.json').write_text(json.dumps(m))
                with self.assertRaises(ValueError): verify_release(r,s)
    def test_cli_verifies_release_and_renders_staging(self):
        conf=self.root/'staging/nginx.conf'; meta=self.root/'staging/render.json'; sf=self.root/'settings.json'
        sf.write_text(json.dumps(self.settings))
        result=subprocess.run([sys.executable,str(TOOLS/'nginx_config.py'),'--release-dir',str(self.release),
                               '--settings',str(sf),'--staging-dir',str(conf.parent),'--private',
                               '--override-backend','http://localhost:18098'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(meta.read_text())['override_backend']['port'],18098)
        self.assertFalse((self.root/'current').exists())
        (self.release/'dist/index.html').write_bytes(b'tamper')
        result=subprocess.run(result.args,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)

    def test_render_cli_rejects_nginx_version_mismatch_before_output(self):
        sf=self.root/'settings.json'; bad={**self.settings,'nginx_version':'0.0.0'}
        sf.write_text(json.dumps(bad)); output=self.root/'nginx.conf'
        result=subprocess.run([sys.executable,str(TOOLS/'nginx_config.py'),'--release-dir',str(self.release),
                               '--settings',str(sf),'--staging-dir',str(output),'--private'],
                              capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0); self.assertIn('nginx version',result.stderr)
        self.assertFalse(output.exists())

    def test_public_modes_reject_restrictive_objects(self):
        for name,mode in [('dist/index.html',0o600),('dist',0o700),('',0o700),('../',0o700),('manifest.json',0o600)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp); release,_=fixture(root); path=release/name
                path.chmod(mode)
                with self.assertRaises(ValueError): verify_release(release,settings(root))

    def test_release_under_writable_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); ancestor=root/'unsafe'; ancestor.mkdir(mode=0o775); ancestor.chmod(0o775)
            private=ancestor/'private'; private.mkdir(mode=0o700)
            release,_=fixture(private)
            with self.assertRaises(ValueError): verify_release(release,settings(private))

class RenderInputTests(unittest.TestCase):
    def test_rejects_injection_bad_ports_paths_sha_and_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); _,m=fixture(root); s=settings(root)
            for key,value in [('runtime_path','/tmp/x;evil'),('release_root','relative'),('listen_host','evil;'),('listen_port',0),('runtime_directory','/absolute')]:
                with self.subTest(key=key), self.assertRaises(ValueError): render(settings={**s,key:value},manifest=m)
            with self.assertRaises(ValueError): render(settings=s,manifest={**m,'source_sha':'RENDERED'})
            with self.assertRaises(ValueError): render(settings={**s,'private_mode':False},manifest=m,override_backend={'scheme':'http','host':'localhost','port':12345})

class BuildTests(unittest.TestCase):
    """Real git archive with fixture npm process; real frontend build recorded separately."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.repo=self.root/'repo'; self.repo.mkdir(); (self.repo/'frontend').mkdir()
        for command in [['git','init','-q'],['git','config','user.email','fixture@example.invalid'],['git','config','user.name','Fixture']]:
            subprocess.run(command,cwd=self.repo,check=True)
        (self.repo/'frontend/package-lock.json').write_text('{}')
        (self.repo/'frontend/vite.config.js').write_text('fixture')
        subprocess.run(['git','add','.'],cwd=self.repo,check=True)
        subprocess.run(['git','commit','-qm','fixture'],cwd=self.repo,check=True)
        self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo,text=True).strip()
        self.bin=self.root/'bin'; self.bin.mkdir()
        node='''#!/usr/bin/env python3
import json,sys
if '--version' in sys.argv: print('v24.16.0')
elif 'vite/package.json' in sys.argv[-1]: print('6.4.3')
else: print(json.dumps({'port':5173,'allowedHosts':['axioma-ia.io'],'proxy':{'/api':{'target':'http://localhost:8080','changeOrigin':True},'/ws':{'target':'ws://localhost:8080','changeOrigin':True,'ws':True}}}))
'''
        npm='''#!/usr/bin/env python3
import os,sys,pathlib
if '--version' in sys.argv: print('11.13.0')
elif sys.argv[1] == 'ci':
 if pathlib.Path('FAIL').exists(): sys.exit(7)
else:
 p=pathlib.Path('dist/assets'); p.mkdir(parents=True)
 pathlib.Path('dist/index.html').write_text('SPA')
 (p/'app-12345678.js').write_text(os.getcwd() if pathlib.Path('NONDET').exists() else 'same')
 if pathlib.Path('MUTATE').exists(): pathlib.Path(pathlib.Path('MUTATE').read_text()).write_text('dirty during build')
'''
        (self.bin/'node').write_text(node); (self.bin/'npm').write_text(npm)
        for p in self.bin.iterdir(): p.chmod(0o755)
        self.env={**os.environ,'PATH':str(self.bin)+':'+os.environ['PATH']}
        self.out=self.root/'out'
    def tearDown(self): self.tmp.cleanup()
    def run_build(self):
        return subprocess.run([sys.executable,str(TOOLS/'build_release.py'),'--repo',str(self.repo),'--sha',self.head,'--output',str(self.out),'--private'],env=self.env,capture_output=True,text=True)
    def commit_marker(self,name):
        (self.repo/'frontend'/name).write_text('marker')
        subprocess.run(['git','add','.'],cwd=self.repo,check=True)
        subprocess.run(['git','commit','-qm',name],cwd=self.repo,check=True)
        self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo,text=True).strip()
    def test_success_and_validated_reuse_then_corruption_refused(self):
        result=self.run_build(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.run_build().returncode,0)
        (self.out/self.head/'dist/index.html').write_text('tamper')
        self.assertNotEqual(self.run_build().returncode,0)
        self.assertEqual((self.out/self.head/'dist/index.html').read_text(),'tamper')

    def test_root_can_build_from_checkout_owned_by_service_user(self):
        # Production runs as root while its clean checkout belongs to jaxsvc.
        # Remove sudo's UID hint so Git exercises its actual dubious-owner check.
        script = '''import json, pathlib, sys, tempfile
from build_release import check_source, build_once
repo=pathlib.Path(sys.argv[1]); sha=sys.argv[2]
check_source(repo, sha)
with tempfile.TemporaryDirectory() as temp:
    work=pathlib.Path(temp)
    dist, metadata=build_once(repo, sha, work, {'node_version':'24.16.0','npm_version':'11.13.0','vite_version':'6.4.3'})
    assert (dist/'index.html').read_text() == 'SPA'
    assert metadata['vite_version'] == '6.4.3'
'''
        result=subprocess.run(['sudo','-n','env','-u','SUDO_UID','-u','SUDO_GID',
                               'PYTHONPATH='+str(TOOLS), 'PATH='+self.env['PATH'],
                               sys.executable,'-c',script,str(self.repo),self.head],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_dirty_correct_sha_rejected(self):
        (self.repo/'frontend/vite.config.js').write_text('dirty')
        result=self.run_build(); self.assertNotEqual(result.returncode,0)
        self.assertIn('clean',result.stderr); self.assertFalse(self.out.exists())
    def test_ci_failure_nondeterminism_archived_env_and_ambient_env(self):
        for marker in ('FAIL','NONDET','.env.production'):
            with self.subTest(marker=marker):
                self.commit_marker(marker)
                result=self.run_build(); self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertFalse((self.out/self.head).exists())
                (self.repo/'frontend'/marker).unlink()
                subprocess.run(['git','add','.'],cwd=self.repo,check=True)
                subprocess.run(['git','commit','-qm','remove marker'],cwd=self.repo,check=True)
                self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo,text=True).strip()
        self.env['VITE_INJECTED']='x'
        result=self.run_build(); self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.out/self.head).exists())

    def test_source_mutation_during_build_does_not_publish(self):
        (self.repo/'frontend/MUTATE').write_text(str(self.repo/'frontend/vite.config.js'))
        subprocess.run(['git','add','.'],cwd=self.repo,check=True)
        subprocess.run(['git','commit','-qm','mutation fixture'],cwd=self.repo,check=True)
        self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo,text=True).strip()
        result=self.run_build()
        self.assertNotEqual(result.returncode,0); self.assertIn('clean',result.stderr)
        self.assertFalse((self.out/self.head).exists())
        self.assertFalse(list(self.out.glob('.*.staging-*')))
    def test_runtime_mismatch_does_not_publish(self):
        node=self.bin/'node'; original=node.read_text()
        for before,after in [('v24.16.0','v0.1.0'),('6.4.3','0.1.0')]:
            with self.subTest(version=before):
                node.write_text(original.replace(before,after))
                result=self.run_build()
                self.assertNotEqual(result.returncode,0); self.assertIn('version mismatch',result.stderr)
                self.assertFalse((self.out/self.head).exists())
        node.write_text(original)

    def test_parent_mode_is_public_and_existing_private_parent_is_not_changed(self):
        result=self.run_build(); self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.out.stat().st_mode&0o7777,0o755)
        self.out.chmod(0o700)
        result=self.run_build(); self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.out.stat().st_mode&0o7777,0o700)

    def test_distinct_uid_can_read_built_release(self):
        # A disposable public ancestor contains fixture bytes only, never credentials.
        result=self.run_build(); self.assertEqual(result.returncode,0,result.stderr)
        self.root.chmod(0o755)
        try:
            reader="import os,pathlib,sys; assert (os.getuid(),os.getgid(),os.getgroups())==(65534,65534,[]); print(pathlib.Path(sys.argv[1]).read_text())"
            result=subprocess.run(['sudo','-n','setpriv','--reuid=65534','--regid=65534','--clear-groups',
                                   sys.executable,'-c',reader,str(self.out/self.head/'dist/index.html')],
                                  capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(result.stdout.strip(),'SPA')
        finally: self.root.chmod(0o700)

    def test_publication_rejects_an_unsafe_ancestor(self):
        ancestor=self.root/'unsafe'; ancestor.mkdir(mode=0o775); ancestor.chmod(0o775)
        self.out=ancestor/'releases'
        result=self.run_build(); self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.out/self.head).exists())
        self.assertEqual(ancestor.stat().st_mode&0o7777,0o775)

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.release,self.manifest=fixture(self.root)
        (self.root/'current').symlink_to(self.release)
        (self.root/'pool/assets').mkdir(parents=True)
        (self.root/'pool/assets/app-12345678.js').write_bytes(b'new asset')
        (self.root/'pool/assets/old-ABCDEFGH.css').write_bytes(b'old asset')
        (self.root/'pool/assets/unhashed.js').write_bytes(b'no immutable')
        (self.root/'pool/assets/link-12345678.js').symlink_to(self.release/'dist/index.html')
        (self.root/'run').mkdir()
        self.calls=[]; calls=self.calls; self.ws_calls=[]; ws_calls=self.ws_calls
        class Upstream(http.server.BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def respond(self):
                data=self.rfile.read(int(self.headers.get('Content-Length',0)))
                calls.append(self.path)
                if self.headers.get('Upgrade') == 'websocket':
                    ws_calls.append({'path':self.path,'host':self.headers.get('Host'),'origin':self.headers.get('Origin')})
                    accept=base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                    self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade')
                    self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
                    header=self.rfile.read(2)
                    if len(header)==2:
                        length=header[1]&127; mask=self.rfile.read(4); payload=self.rfile.read(length)
                        decoded=bytes(value^mask[i%4] for i,value in enumerate(payload))
                        self.wfile.write(bytes([0x81,length])+decoded); self.wfile.flush()
                    self.close_connection=True; return
                body=json.dumps({'path':self.path,'host':self.headers.get('Host'),'origin':self.headers.get('Origin'),
                                 'bytes':len(data),'sha256':sha(data),'xff':self.headers.get('X-Forwarded-For')}).encode()
                self.send_response(418 if self.path.startswith('/api/status') else 404 if self.path.startswith('/api/missing') else 200)
                self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','private'); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); self.port=sock.getsockname()[1]
        self.s=settings(self.root); self.s['listen_port']=self.port
        config,meta=render(settings=self.s,manifest=self.manifest,
                           override_backend={'scheme':'http','host':'localhost','port':self.server.server_port})
        self.meta=meta; self.config=self.root/'nginx.conf'; self.config.write_text(config)
        self.log=(self.root/'nginx.log').open('w')
        test=subprocess.run(['nginx','-t','-p',str(self.root),'-c',str(self.config)],capture_output=True,text=True)
        if test.returncode:
            self.server.shutdown(); self.server.server_close(); self.thread.join(); self.log.close(); self.tmp.cleanup()
            self.fail(test.stderr)
        self.process=subprocess.Popen(['nginx','-p',str(self.root),'-c',str(self.config),'-g','daemon off;'],stdout=self.log,stderr=self.log)
        for attempt in range(100):
            try:
                with socket.create_connection(('127.0.0.1',self.port),timeout=.1): break
            except OSError:
                if self.process.poll() is not None: self.fail('nginx failed to start')
                time.sleep(.02)
    def tearDown(self):
        self.process.terminate(); self.process.wait(timeout=5)
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.log.close(); self.tmp.cleanup()
    def request(self,path,method='GET',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request(method,path,body=body,headers={'Host':'axioma-ia.io',**(headers or {})})
        response=connection.getresponse(); result=(response.status,dict(response.getheaders()),response.read())
        connection.close(); return result
    def test_get_head_spa_static_mime_cache_and_old_pool(self):
        for path,mime,cache,content in [('/', 'text/html','no-cache',b'<html>SPA</html>'),
                ('/dashboard/deep','text/html','no-cache',b'<html>SPA</html>'),
                ('/index.html','text/html','no-cache',b'<html>SPA</html>'),
                ('/favicon.svg','image/svg+xml','no-cache',b'<svg/>'),
                ('/assets/app-12345678.js','application/javascript','public, max-age=31536000, immutable',b'new asset'),
                ('/assets/old-ABCDEFGH.css','text/css','public, max-age=31536000, immutable',b'old asset')]:
            for method in ('GET','HEAD'):
                with self.subTest(path=path,method=method):
                    status,headers,body=self.request(path,method)
                    self.assertEqual(status,200); self.assertEqual(headers['Content-Type'],mime)
                    self.assertEqual(headers['Cache-Control'],cache); self.assertEqual(headers['X-JAX-Frontend-SHA'],SHA)
                    self.assertEqual(body,b'' if method=='HEAD' else content)
    def test_static_missing_private_dot_source_and_pool_symlink(self):
        for path in ['/missing.js','/missing.pdf','/assets/missing-12345678.js','/assets/unhashed.js',
                     '/src/secret','/node_modules/x','/package.json','/package-lock.json','/.env',
                     '/deep/.secret','/assets/.secret-12345678.js','/assets/link-12345678.js']:
            for method in ('GET','HEAD'):
                with self.subTest(path=path,method=method):
                    status,headers,body=self.request(path,method)
                    self.assertEqual(status,404); self.assertNotEqual(body,b'<html>SPA</html>')
                    self.assertNotIn('immutable',headers.get('Cache-Control',''))
    def test_api_prefix_query_origin_host_status_and_large_body(self):
        for path in ['/api/path?q=a%20b','/apix/prefix?x=1','/wsx/prefix?q=2','/api/status','/api/missing']:
            with self.subTest(path=path):
                status,headers,body=self.request(path,headers={'Origin':'https://origin.example'})
                self.assertEqual(status,418 if path=='/api/status' else 404 if path=='/api/missing' else 200)
                data=json.loads(body); self.assertEqual(data['path'],path)
                self.assertEqual(data['host'],f'localhost:{self.server.server_port}')
                self.assertEqual(data['origin'],'https://origin.example'); self.assertIsNone(data['xff'])
                self.assertNotIn('X-JAX-Frontend-SHA',headers); self.assertEqual(headers['Cache-Control'],'private')
        payload=b'x'*(2*1024*1024)
        status,headers,body=self.request('/api/upload','POST',payload,{'Origin':'https://origin.example'})
        self.assertEqual(status,200); data=json.loads(body)
        self.assertEqual(data['bytes'],len(payload)); self.assertEqual(data['sha256'],sha(payload))
    def test_host_guard_prevents_upstream_and_allows_vite_defaults(self):
        before=len(self.calls)
        for host in ['evil.example','axioma-ia.io.evil.example','[f:]','999.1.1.1']:
            self.assertEqual(self.request('/apix/no',headers={'Host':host})[0],403)
        self.assertEqual(len(self.calls),before)
        for host in ['localhost','sub.localhost','127.0.0.1','10.20.30.40','[::1]','[2001:db8::1]','[::ffff:127.0.0.1]','www.axioma-ia.io']:
            with self.subTest(host=host): self.assertEqual(self.request('/api/yes',headers={'Host':host})[0],200)
    def raw_request(self,port,target,host):
        with socket.create_connection(('127.0.0.1',port),timeout=5) as sock:
            sock.sendall((f'GET {target} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n').encode())
            response=b''
            while chunk:=sock.recv(4096): response+=chunk
        return int(response.split(b' ',2)[1])

    def test_absolute_target_always_rejected_before_upstream(self):
        for target,host in [('http://axioma-ia.io/apix/absolute?x=1','evil.example'),
                            ('http://evil.example/apix/absolute?x=1','axioma-ia.io'),
                            ('http://axioma-ia.io/apix/absolute?x=1','axioma-ia.io')]:
            with self.subTest(target=target,host=host):
                before=len(self.calls)
                self.assertEqual(self.raw_request(self.port,target,host),403)
                self.assertEqual(len(self.calls),before)

    def test_header_case_dot_port_matches_actual_vite(self):
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        probe="""import {createServer} from 'vite';
const server=await createServer({configFile:false,root:process.argv[1],
server:{host:'127.0.0.1',port:Number(process.argv[2]),strictPort:true,
allowedHosts:['axioma-ia.io','www.axioma-ia.io'],
proxy:{'/api':{target:'http://localhost:'+process.argv[3],changeOrigin:true}}}});
await server.listen();"""
        log=(self.root/'vite.log').open('w')
        env={key:value for key,value in os.environ.items() if not key.startswith(('VITE_','__VITE_'))}
        process=subprocess.Popen(['node','--input-type=module','-e',probe,str(self.root),str(port),str(self.server.server_port)],
                                 cwd=TOOLS.parents[1]/'frontend',env=env,stdout=log,stderr=log)
        try:
            for attempt in range(100):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                except OSError:
                    if process.poll() is not None: self.fail('Vite failed: '+(self.root/'vite.log').read_text())
                    time.sleep(.02)
            for host in ['axioma-ia.io','www.axioma-ia.io','localhost','sub.localhost','UPPER.localhost',
                         '127.0.0.1','[::1]','[2001:DB8::1]','axioma-ia.io:12345',
                         'AXIOMA-IA.IO','axioma-ia.io.','evil.example']:
                with self.subTest(host=host):
                    vite=self.raw_request(port,'/apix/parity?x=1',host)
                    before=len(self.calls); nginx=self.raw_request(self.port,'/apix/parity?x=1',host)
                    self.assertEqual(nginx,vite)
                    if vite==403: self.assertEqual(len(self.calls),before)
            for host in ['axioma-ia.io:0','axioma-ia.io:65536','axioma-ia.io:abc','axioma-ia.io\r\nHost: evil.example']:
                before=len(self.calls)
                self.assertIn(self.raw_request(self.port,'/apix/no',host),(400,403))
                self.assertEqual(len(self.calls),before)
            # Vite checks the allowed header but its origin-form proxy does not handle the absolute URL (404).
            # nginx deliberately denies every absolute target (403).
            self.assertEqual(self.raw_request(port,'http://axioma-ia.io/apix/absolute','axioma-ia.io'),404)
            self.assertEqual(self.raw_request(port,'http://axioma-ia.io/apix/absolute','evil.example'),403)
        finally:
            process.terminate(); process.wait(timeout=5); log.close()

    def test_websocket_upgrade_and_override_record(self):
        key='dGhlIHNhbXBsZSBub25jZQ=='
        with socket.create_connection(('127.0.0.1',self.port),timeout=5) as sock:
            request=(f'GET /ws/echo?token=fixture HTTP/1.1\r\nHost: axioma-ia.io\r\n'
                     f'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: {key}\r\n'
                     'Sec-WebSocket-Version: 13\r\nOrigin: https://origin.example\r\n\r\n')
            sock.sendall(request.encode()); response=b''
            while b'\r\n\r\n' not in response: response+=sock.recv(4096)
            self.assertIn(b' 101 ',response)
            self.assertIn(b's3pPLMBiTxaQ9kYGzzhZRbK+xOo=',response)
            self.assertNotIn(b'X-JAX-Frontend-SHA',response)
            payload=b'hello through nginx'; mask=b'abcd'
            masked=bytes(value^mask[i%4] for i,value in enumerate(payload))
            sock.sendall(bytes([0x81,0x80|len(payload)])+mask+masked)
            reply=b''
            while len(reply)<len(payload)+2: reply+=sock.recv(4096)
            self.assertEqual(reply,bytes([0x81,len(payload)])+payload)
        self.assertEqual(self.ws_calls,[{'path':'/ws/echo?token=fixture',
                         'host':f'localhost:{self.server.server_port}','origin':'https://origin.example'}])
        self.assertEqual(self.meta['upstream_original']['port'],8080)
        self.assertEqual(self.meta['override_backend']['port'],self.server.server_port)

class PublicationTests(unittest.TestCase):
    def test_never_replaces_unknown_empty_destination(self):
        from build_release import publish_noreplace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'stage'; destination=root/'release'
            source.mkdir(); (source/'proof').write_text('staging')
            destination.mkdir()
            with self.assertRaises(OSError): publish_noreplace(source,destination)
            self.assertTrue((source/'proof').exists()); self.assertEqual(list(destination.iterdir()),[])

class ViteLoaderTests(unittest.TestCase):
    def test_real_loader_rejects_functions_options_remote_scheme_and_ws_mismatch(self):
        from build_release import proxy_contract
        deps=TOOLS.parents[1]/'frontend/node_modules'
        if not (deps/'vite/package.json').exists():
            self.skipTest('npm ci required for real Vite loader tests')
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp); (source/'node_modules').symlink_to(deps)
            (source/'package.json').write_text('{"type":"module"}')
            valid="export default {server:{allowedHosts:['axioma-ia.io'],proxy:{'/api':{target:'http://localhost:8080',changeOrigin:true},'/ws':{target:'ws://localhost:8080',changeOrigin:true,ws:true}}}}"
            (source/'vite.config.js').write_text(valid)
            contract=proxy_contract(source,os.environ.copy())
            self.assertEqual(contract['port'],8080)
            mutations=[valid.replace("changeOrigin:true},'/ws'","changeOrigin:true,rewrite:p=>p},'/ws'"),
                       valid.replace("changeOrigin:true},'/ws'","changeOrigin:true,configure:()=>{}},'/ws'"),
                       valid.replace('http://localhost','https://localhost'),
                       valid.replace('http://localhost','http://remote.example'),
                       valid.replace('ws://localhost:8080','ws://localhost:8081'),
                       valid.replace('changeOrigin:true','changeOrigin:false'),
                       valid.replace("allowedHosts:['axioma-ia.io']","allowedHosts:true")]
            for altered in mutations:
                with self.subTest(config=altered):
                    (source/'vite.config.js').write_text(altered)
                    with self.assertRaises(ValueError): proxy_contract(source,os.environ.copy())

class StagingPackageTests(unittest.TestCase):
    def test_digest_correlates_pure_render_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); _,manifest=fixture(root)
            config,meta=render(settings=settings(root),manifest=manifest)
            self.assertEqual(meta.get('config_sha256'),sha(config.encode()))

    def test_second_write_and_publication_failure_leave_no_package(self):
        import nginx_config
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); _,manifest=fixture(root)
            config,meta=render(settings=settings(root),manifest=manifest)
            write=nginx_config.staging_write
            def fail_second(path,data):
                if path.name=='render.json': raise OSError('second write failure')
                write(path,data)
            for boundary in ('write','publish'):
                with self.subTest(boundary=boundary):
                    target=root/'package'
                    patcher=patch.object(nginx_config,'staging_write',side_effect=fail_second) if boundary=='write' else patch.object(nginx_config,'publish_noreplace',side_effect=OSError('before publish'))
                    with patcher,self.assertRaises(OSError): nginx_config.publish_staging(target,config,meta)
                    self.assertFalse(target.exists()); self.assertEqual(list(root.glob('.package.prepare-*')),[])

    def test_existing_package_and_publication_race_preserve_previous_bytes(self):
        import nginx_config
        from build_release import publish_noreplace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); _,manifest=fixture(root)
            config,meta=render(settings=settings(root),manifest=manifest)
            known=root/'known'; nginx_config.publish_staging(known,config,meta)
            previous={path.name:path.read_bytes() for path in known.iterdir()}
            changed=config+'# changed config\n'; changed_meta={**meta,'config_sha256':sha(changed.encode())}
            with self.assertRaises((OSError,ValueError)): nginx_config.publish_staging(known,changed,changed_meta)
            self.assertEqual({path.name:path.read_bytes() for path in known.iterdir()},previous)
            target=root/'package'; target.mkdir(); (target/'nginx.conf').write_text('previous')
            with self.assertRaises((OSError,ValueError)): nginx_config.publish_staging(target,config,meta)
            self.assertEqual((target/'nginx.conf').read_text(),'previous')
            target=root/'race'
            def collide(source,destination):
                destination.mkdir(); (destination/'sentinel').write_text('unknown')
                publish_noreplace(source,destination)
            with patch.object(nginx_config,'publish_noreplace',side_effect=collide),self.assertRaises(OSError):
                nginx_config.publish_staging(target,config,meta)
            self.assertEqual(list(p.name for p in target.iterdir()),['sentinel'])
            self.assertEqual(list(root.glob('.race.prepare-*')),[])

    def test_cli_second_write_failure_has_no_stdout_or_published_half(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); release,_=fixture(root); sf=root/'settings.json'; sf.write_text(json.dumps(settings(root)))
            script="""import sys
sys.path.insert(0,sys.argv.pop(1))
import nginx_config
original=nginx_config.staging_write
def fail_second(path,data):
 if path.name=='render.json': raise OSError('injected second write')
 original(path,data)
nginx_config.staging_write=fail_second
raise SystemExit(nginx_config.main())
"""
            target=root/'package'
            result=subprocess.run([sys.executable,'-c',script,str(TOOLS),'--release-dir',str(release),
                                   '--settings',str(sf),'--staging-dir',str(target),'--private'],capture_output=True,text=True)
            self.assertEqual(result.returncode,1); self.assertEqual(result.stdout,'')
            self.assertIn('injected second write',result.stderr)
            self.assertFalse(target.exists()); self.assertEqual(list(root.glob('.package.prepare-*')),[])

    def test_kill_at_publication_boundaries_never_exposes_a_partial_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); _,manifest=fixture(root)
            config,meta=render(settings=settings(root),manifest=manifest)
            data=root/'input.json'; data.write_text(json.dumps({'config':config,'meta':meta}))
            script="""import json,os,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]); import nginx_config
value=json.loads(Path(sys.argv[2]).read_text()); after=sys.argv[4]=='after'
original=nginx_config.publish_noreplace
def cut(source,destination):
 if after: original(source,destination)
 os.kill(os.getpid(),signal.SIGKILL)
nginx_config.publish_noreplace=cut
nginx_config.publish_staging(Path(sys.argv[3]),value['config'],value['meta'])
"""
            for boundary in ['before','after']:
                with self.subTest(boundary=boundary):
                    target=root/boundary
                    result=subprocess.run([sys.executable,'-c',script,str(TOOLS),str(data),str(target),boundary],capture_output=True,text=True)
                    self.assertEqual(result.returncode,-9)
                    if boundary=='before': self.assertFalse(target.exists())
                    else:
                        self.assertEqual(set(path.name for path in target.iterdir()),{'nginx.conf','render.json'})
                        persisted=json.loads((target/'render.json').read_text())
                        self.assertEqual(persisted['config_sha256'],sha((target/'nginx.conf').read_bytes()))

    def test_unsafe_parent_and_ancestor_modes_are_refused_without_chmod(self):
        import nginx_config
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); _,manifest=fixture(root)
            config,meta=render(settings=settings(root),manifest=manifest)
            for mode in [0o775,0o777]:
                with self.subTest(mode=oct(mode)):
                    parent=root/str(mode); parent.mkdir(mode=mode); parent.chmod(mode)
                    with self.assertRaises(ValueError): nginx_config.publish_staging(parent/'package',config,meta)
                    self.assertEqual(parent.stat().st_mode&0o7777,mode); self.assertEqual(list(parent.iterdir()),[])
            ancestor=root/'unsafe'; ancestor.mkdir(mode=0o775); ancestor.chmod(0o775)
            parent=ancestor/'private'; parent.mkdir(mode=0o700)
            with self.assertRaises(ValueError): nginx_config.publish_staging(parent/'package',config,meta)
            self.assertFalse((parent/'package').exists()); self.assertEqual(ancestor.stat().st_mode&0o7777,0o775)
            with self.assertRaises(ValueError): nginx_config.check_package_parent(root,owner_uid=os.getuid()+1)
