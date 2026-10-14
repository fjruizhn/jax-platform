#!/usr/bin/env python3
"""Prepare, apply and reverse one Vite-to-nginx migration, with explicit settings.

render/prepare publish a new staging package only. apply/rollback share a
nonblocking lock and durable journal. An interrupted operation is restored to its
known original state before the caller may retry. Unknown drift fails closed.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import grp
import re
import shutil
import subprocess
import sys
import tempfile
import time
from build_release import publish_noreplace, sync_tree
from nginx_config import absolute_path, backend_url, render, runtime_path
from release_manifest import check_controlled_directory, check_object, checked_path, verify_release
from release_storage import (atomic_write, fingerprint, file_state, install_state, locked, mkdir,
                             read_json, restore_snapshot, snapshot_managed, sync_dir, update_asset_pool, write_json)
from release_systemd import Manager, STATIC, nginx_process, same_process

HELPERS=('launch_nginx.py','runtime_verify.py','release_manifest.py','nginx_config.py','build_release.py')
PHASES=('prepared','files_installed','daemon_reloaded','service_switched','verified','committed')


class RetryRequired(RuntimeError): pass


class Controller:
    def __init__(self,settings, *, user=False,manager=None,fault=None):
        self.s=dict(settings); self.user=user; self.fault=fault or (lambda boundary:None)
        self.uid=os.getuid() if user else 0
        if user:
            if not re.fullmatch(r'p100-[A-Za-z0-9_-]+\.service',str(self.s.get('service',''))):
                raise ValueError('--user requires private p100-*.service')
            if not self.s.get('private_mode') or self.s.get('owner_uid')!=self.uid:
                raise ValueError('--user requires explicit private_mode and current owner_uid')
            runtime=os.environ.get('XDG_RUNTIME_DIR')
            if not runtime or self.s.get('user_runtime_dir')!=runtime:
                raise ValueError('user_runtime_dir must equal manager XDG_RUNTIME_DIR')
            self.s['process_uid']=self.uid
            if 'runtime_path' in self.s and self.s['runtime_path']!=runtime+'/'+self.s['runtime_directory']:
                raise ValueError('user RuntimeDirectory path mismatch')
        else:
            if os.getuid()!=0 or self.s.get('private_mode') or self.s.get('owner_uid',0)!=0:
                raise ValueError('system mode requires UID0 and root-owned system settings')
            if self.s.get('service')!='jax-platform-frontend.service' or self.s.get('user')!='jaxsvc' or self.s.get('group')!='jaxsvc':
                raise ValueError('unsupported system unit contract')
            self.s['owner_uid']=0; self.s['process_uid']=pwd.getpwnam(self.s['user']).pw_uid
            grp.getgrnam(self.s['group'])
        for key in ('release_root','nginx_root','systemd_dir','nginx_binary'):
            if key not in self.s: raise ValueError('explicit '+key+' required')
            self.s[key]=absolute_path(self.s[key])
        if self.s.get('dropin_name')!='zz-nginx-estatico.conf': raise ValueError('unsupported managed dropin name')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',str(self.s.get('runtime_directory',''))): raise ValueError('RuntimeDirectory must be name')
        if user and not self.s['runtime_directory'].startswith('p100-'): raise ValueError('private runtime namespace required')
        runtime_path(self.s)
        if user and self.s.get('proof_connect_host','127.0.0.1') not in ('127.0.0.1','::1'):
            raise ValueError('private HTTP proof must connect loopback')
        if user and self.s['listen_port'] in (5173,8080): raise ValueError('private port cannot be production port')
        self.root=Path(self.s['release_root']); self.configroot=Path(self.s['nginx_root'])
        self.systemroot=Path(self.s['systemd_dir'])
        for directory in (self.root,self.configroot,self.systemroot):
            check_controlled_directory(directory,self.uid)
            if directory.stat().st_mode & 0o777!=0o755: raise ValueError('operational public directory must be0755: '+str(directory))
            if not user:
                for ancestor in (directory,*directory.parents):
                    if ancestor.stat().st_mode & 0o005!=0o005: raise ValueError('system service cannot traverse operational path')
        self.dropdir=self.systemroot/(self.s['service']+'.d')
        if self.dropdir.exists(): check_controlled_directory(self.dropdir,self.uid)
        self.config=self.configroot/'nginx.conf'; self.current=self.root/'current'; self.drop=self.dropdir/self.s['dropin_name']
        self.state=self.configroot/'state'
        self.manager=manager if manager is not None else Manager(self.s,user)

    def _version(self):
        result=subprocess.run([self.s['nginx_binary'],'-v'],capture_output=True,text=True,check=True)
        version=re.search(r'nginx/([0-9.]+)',result.stdout+result.stderr)
        if not version or version[1]!=self.s['nginx_version']: raise ValueError('nginx version mismatch')

    def _managed(self): return snapshot_managed([self.config,self.drop,self.current],uid=self.uid)

    def _capture(self,http=True):
        capture=self.manager.capture(http=http)
        capture['files'].pop(str(self.drop),None)
        return capture

    def _assert_static(self,actual,expected):
        if actual['files']!=expected['files']: raise ValueError('CAS base/dropins drift')
        for key in (*STATIC,'UnitFileState','ActiveState'):
            if actual['properties'][key]!=expected['properties'][key]: raise ValueError('CAS effective/state drift: '+key)
        if not same_process(actual['process'],expected['process']): raise ValueError('CAS process drift')
        if actual.get('http')!=expected.get('http'): raise ValueError('CAS original HTTP drift')

    def _known(self,receipt):
        """Accept only independently old/new fingerprints, including write/journal gaps."""
        self._check_helpers(receipt)
        actual=self._managed()
        for path,state in actual.items():
            if state not in (receipt['before'][path],receipt['after'][path]): raise ValueError('CAS unknown managed drift: '+path)
        capture=self._capture(http=False)
        if capture['files']!=receipt['origin']['files']: raise ValueError('CAS base/dropins drift')
        if capture['properties']['UnitFileState']!=receipt['origin']['properties']['UnitFileState']:
            raise ValueError('CAS enabled state drift')
        effective={key:capture['properties'][key] for key in STATIC}
        old={key:receipt['origin']['properties'][key] for key in STATIC}
        new=receipt.get('applied_effective')
        if effective!=old and (new is None or effective!=new):
            # daemon-reload can complete before its phase update: derive this
            # state from the explicit candidate invocation, never trust is-active.
            if not self._nginx_effective(capture['properties'],receipt): raise ValueError('CAS effective drift')
        original=receipt['origin']['process']; actualprocess=capture['process']
        launcher=['/usr/bin/python3','-I','-B',str(Path(receipt['helper_dir'])/'launch_nginx.py'),
                  '--binary',self.s['nginx_binary'],'--config',str(self.config)]
        deadline=time.monotonic()+5
        while (receipt.get('switch_intent') and actualprocess and actualprocess['argv']==launcher
               and actualprocess['exe']==os.path.realpath('/usr/bin/python3') and actualprocess['uid']==self.s['process_uid']):
            if time.monotonic()>=deadline: raise ValueError('nginx launcher did not exec; no signal sent')
            time.sleep(.02); capture=self._capture(http=False); actualprocess=capture['process']
        if capture['properties']['ActiveState']!=receipt['origin']['properties']['ActiveState']:
            # Only a controller stop/restart can leave an inactive transition.
            # Record the intended signal BEFORE sending it.
            if not (receipt.get('switch_intent') and capture['properties']['ActiveState'] in ('inactive','failed')):
                raise ValueError('CAS active state drift')
        if actualprocess is not None and not same_process(actualprocess,original) and not ((receipt.get('switch_intent') or receipt['operation']=='rollback') and nginx_process(actualprocess,self.s,self.config)):
            raise ValueError('CAS unknown process; no signal sent')
        return capture

    def _nginx_effective(self,properties,receipt):
        expected_user='' if self.user else self.s['user']; expected_group='' if self.user else self.s['group']
        commands=lambda text: re.findall(r'argv\[\]=(.*?) ; ignore_errors=',text)
        helper=Path(receipt['helper_dir'])
        launcher='/usr/bin/python3 -I -B '+str(helper/'launch_nginx.py')+' --binary '+self.s['nginx_binary']+' --config '+str(self.config)
        expected_start=launcher
        expected_pre=['/usr/bin/python3 -I -B '+str(helper/'runtime_verify.py')+' --settings '+str(helper/'settings.json')+
                      ' --release-dir '+receipt['release_dir']+' --config '+str(self.config)+' --metadata '+str(helper/'render.json'),
                      launcher+' --test']
        return (properties['Type']=='simple' and properties['KillMode']=='control-group'
                and properties['WorkingDirectory']=='/' and properties['User']==expected_user and properties['Group']==expected_group
                and commands(properties['ExecStart'])==[expected_start] and commands(properties['ExecStartPre'])==expected_pre)

    def _restore(self,receipt):
        self._known(receipt)
        # Persist intent before systemctl, covering signal-to-journal crash gaps.
        receipt['switch_intent']=True; self._journal(receipt,receipt['phase'])
        capture=self._capture(http=False)
        # Do not stop/restart an unknown PID. _known already proved its identity.
        if capture['properties']['ActiveState']=='active': self.manager.stop()
        self.fault('restore_stopped')
        for path,state in receipt['before'].items():
            install_state(path,state); self.fault('restored_'+Path(path).name)
        self.fault('restore_files')
        self.manager.reload(); self.fault('restore_reloaded')
        if receipt['origin']['properties']['ActiveState']=='active': self.manager.restart()
        self.fault('restore_service')
        self._verify_original(receipt)

    def _verify_original(self,receipt):
        actual=self._capture()
        self._assert_static(actual,receipt['origin'])
        if self._managed()!=receipt['before']: raise ValueError('restoration files mismatch')
        if actual['http']!=receipt['origin']['http']: raise ValueError('original HTTP proof mismatch')

    def _journal(self,receipt,phase):
        receipt['phase']=phase
        write_json(self.state/'journal.json',receipt)
        self.fault('journal_'+phase)

    def _recover(self):
        journal=self.state/'journal.json'
        if not journal.exists(): return
        receipt=read_json(journal,self.uid)
        if receipt['phase']=='committed': return
        if receipt['phase'] in ('recovered','rolled_back'):
            # A terminal journal is authoritative even if the pointer update
            # was interrupted; repair it without restarting a service.
            self._verify_original(receipt)
            write_json(self.state/'last.json',receipt)
            return
        self._restore(receipt)
        resolved='rolled_back' if receipt['operation']=='rollback' else 'recovered'
        write_json(self.state/'last.json',{**receipt,'phase':resolved})
        self._journal(receipt,resolved)
        raise RetryRequired('incomplete '+receipt['operation']+' recovered; retry explicitly')

    def _dropin(self,helper):
        quote=lambda value:'"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'
        usergroup='User=\nGroup=\n' if self.user else 'User='+self.s['user']+'\nGroup='+self.s['group']+'\n'
        return ('[Service]\nType=simple\nKillMode=control-group\n'+usergroup+'WorkingDirectory=/\nRuntimeDirectory='+self.s['runtime_directory']+
                '\nRuntimeDirectoryMode=0700\nExecStart=\nExecStartPre=\nExecStartPre=/usr/bin/python3 -I -B '+
                quote(helper/'runtime_verify.py')+' --settings '+quote(helper/'settings.json')+
                ' --release-dir '+quote(self.release)+' --config '+quote(self.config)+' --metadata '+quote(helper/'render.json')+
                '\nExecStartPre=/usr/bin/python3 -I -B '+quote(helper/'launch_nginx.py')+' --binary '+quote(self.s['nginx_binary'])+' --config '+quote(self.config)+' --test'+
                '\nExecStart=/usr/bin/python3 -I -B '+quote(helper/'launch_nginx.py')+' --binary '+quote(self.s['nginx_binary'])+' --config '+quote(self.config)+
                '\nStandardOutput=journal\nStandardError=journal\n')

    def prepare(self,release_dir,staging_dir,override=None):
        """No active filesystem/state mutations; publish one complete staging package."""
        release_dir=checked_path(Path(release_dir)); staging_dir=checked_path(Path(staging_dir))
        if release_dir.parent!=self.root/'releases': raise ValueError('release must belong to configured releases root')
        if staging_dir.is_relative_to(self.root) or staging_dir.is_relative_to(self.configroot) or staging_dir.is_relative_to(self.systemroot):
            raise ValueError('staging must be outside active roots')
        check_controlled_directory(staging_dir.parent,self.uid)
        if staging_dir.exists(): raise ValueError('staging destination must be new')
        self._version(); manifest=verify_release(release_dir,self.s)
        if override is not None and not self.user: raise ValueError('override only allowed with --user')
        config,metadata=render(settings=self.s,manifest=manifest,override_backend=override)
        self.release=release_dir
        helper_identity=hashlib.sha256(config.encode()+json.dumps(self.s,sort_keys=True).encode()+
                           b''.join(Path(__file__).with_name(name).read_bytes() for name in HELPERS)).hexdigest()
        helper=self.configroot/'helpers'/helper_identity
        dropin=self._dropin(helper)
        origin=self._capture()
        if origin['properties']['ActiveState'] not in ('active','inactive'): raise ValueError('original unit must be active or inactive')
        if origin['process'] is not None:
            process=origin['process']
            if (process['uid']!=self.s['process_uid'] or Path(process['exe']).name!='node'
                    or not any('vite' in arg.lower() for arg in process['argv'])):
                raise ValueError('original MainPID must be the expected Node/Vite process')
        if str(self.drop) in self.manager.unit_files(origin['properties']): raise ValueError('unowned managed dropin already installed')
        before=self._managed()
        if not before[str(self.drop)].get('absent'): raise ValueError('unowned managed dropin exists')
        with tempfile.TemporaryDirectory(prefix='.'+staging_dir.name+'-',dir=staging_dir.parent) as scratch:
            package=Path(scratch)/'package'; package.mkdir(mode=0o700)
            atomic_write(package/'nginx.conf',config)
            atomic_write(package/'render.json',json.dumps(metadata,sort_keys=True)+'\n')
            atomic_write(package/'dropin.conf',dropin)
            bundle=package/'helpers'; bundle.mkdir(mode=0o755)
            for name in HELPERS: atomic_write(bundle/name,Path(__file__).with_name(name).read_bytes(),mode=0o644)
            atomic_write(bundle/'settings.json',json.dumps(self.s,sort_keys=True)+'\n',mode=0o644)
            atomic_write(bundle/'render.json',json.dumps(metadata,sort_keys=True)+'\n',mode=0o644)
            preflight=Path(scratch)/'runtime'; preflight.mkdir(mode=0o700)
            testsettings={**self.s,'private_mode':True,'runtime_path':str(preflight),'owner_uid':self.uid}
            testconfig,_=render(settings=testsettings,manifest=manifest,override_backend=override)
            atomic_write(package/'preflight.conf',testconfig)
            self.fault('before_nginx_preflight')
            subprocess.run([self.s['nginx_binary'],'-t','-c',str(package/'preflight.conf')],check=True,capture_output=True,text=True)
            candidate=package/'unit'; candidate.mkdir(mode=0o700)
            fragment=Path(origin['properties']['FragmentPath'])
            atomic_write(candidate/self.s['service'],fragment.read_bytes(),mode=0o644)
            candidate_drop=candidate/(self.s['service']+'.d'); candidate_drop.mkdir(mode=0o755)
            for raw,state in origin['files'].items():
                if raw!=str(fragment):
                    target=candidate_drop/Path(raw).name
                    if target.exists():
                        if target.read_bytes()!=base64.b64decode(state['bytes']): raise ValueError('ambiguous original dropin precedence')
                    else: atomic_write(target,base64.b64decode(state['bytes']),mode=0o644)
            atomic_write(candidate_drop/self.s['dropin_name'],dropin,mode=0o644)
            self.fault('before_systemd_preflight'); self.manager.verify_unit(candidate/self.s['service'])
            plan={'settings':self.s,'metadata':metadata,'release_dir':str(release_dir),'helper_dir':str(helper),
                  'helpers':snapshot_managed(list(bundle.iterdir()),uid=self.uid),'origin':origin,'before':before,'config_digest':hashlib.sha256(config.encode()).hexdigest()}
            plan['helpers']={str(helper/Path(path).name):state for path,state in plan['helpers'].items()}
            atomic_write(package/'plan.json',json.dumps(plan,sort_keys=True)+'\n')
            # Runtime paths in this diagnostic config are scratch-only; do not
            # publish it as a reusable configuration.
            (package/'preflight.conf').unlink()
            sync_tree(package); publish_noreplace(package,staging_dir); sync_dir(staging_dir.parent)
        return {'staging_dir':str(staging_dir),'nginx_config':str(staging_dir/'nginx.conf'),
                'render_metadata':str(staging_dir/'render.json'),'source_sha':manifest['source_sha']}

    def _install_helpers(self,package,helper):
        mkdir(helper.parent,0o755,self.uid)
        if helper.exists():
            check_controlled_directory(helper,self.uid)
            if set(p.name for p in helper.iterdir())!=set(p.name for p in (package/'helpers').iterdir()): raise ValueError('helper collision')
            for source in (package/'helpers').iterdir():
                check_object(helper/source.name,self.uid,public=True)
                if source.read_bytes()!=(helper/source.name).read_bytes(): raise ValueError('helper collision')
            return
        temporary=helper.with_name('.'+helper.name+'.'+os.urandom(12).hex())
        temporary.mkdir(mode=0o755)
        try:
            for source in (package/'helpers').iterdir(): atomic_write(temporary/source.name,source.read_bytes(),mode=0o644)
            sync_tree(temporary); publish_noreplace(temporary,helper); sync_dir(helper.parent)
        finally:
            if temporary.exists(): shutil.rmtree(temporary)

    def _check_helpers(self,receipt):
        helper=Path(receipt['helper_dir'])
        check_controlled_directory(helper,self.uid)
        paths=list(helper.iterdir())
        if snapshot_managed(paths,uid=self.uid)!=receipt['helpers']: raise ValueError('CAS immutable helper drift')

    def _verify_applied(self,receipt):
        self._check_helpers(receipt)
        verify_release(Path(receipt['release_dir']),self.s,receipt['metadata']['source_sha'])
        if self._managed()!=receipt['after']: raise ValueError('CAS applied files drift')
        actual=self._capture()
        if actual['files']!=receipt['origin']['files']: raise ValueError('CAS base/dropins drift')
        if actual['properties']['UnitFileState']!=receipt['origin']['properties']['UnitFileState']: raise ValueError('CAS enabled state drift')
        if not self._nginx_effective(actual['properties'],receipt): raise ValueError('applied unit invocation mismatch')
        desired_active=receipt['origin']['properties']['ActiveState']
        if actual['properties']['ActiveState']!=desired_active: raise ValueError('CAS active state drift')
        if desired_active=='active':
            if not nginx_process(actual['process'],self.s,self.config): raise ValueError('applied MainPID/argv mismatch')
            http=actual['http']; index=receipt['manifest']['files']['index.html']
            if http!={'status':200,'mime':'text/html','sha':receipt['metadata']['source_sha'],'digest':index}:
                raise ValueError('applied HTTP release/index proof mismatch')
            if self.manager.http('/api')['sha'] is not None: raise ValueError('API leaked static SHA header')
        return actual

    def apply(self,release_dir,staging_dir,override=None):
        mkdir(self.state,0o700,self.uid)
        with locked(self.state/'lock',self.uid):
            self._recover()
            last=self.state/'last.json'
            if last.exists():
                previous=read_json(last,self.uid)
                if previous['phase']=='committed':
                    manifest=verify_release(Path(release_dir),self.s)
                    if manifest['source_sha']!=previous['metadata']['source_sha']:
                        raise ValueError('another SHA is applied; rollback required first')
                    if override!=previous['metadata']['override_backend']: raise ValueError('same SHA override differs; rollback required')
                    self._verify_applied(previous)
                    return {'applied':manifest['source_sha'],'idempotent':True}
            package=Path(staging_dir)
            self.prepare(release_dir,package,override)
            plan=read_json(package/'plan.json',self.uid)
            self._assert_static(self._capture(),plan['origin'])
            if self._managed()!=plan['before']: raise ValueError('CAS managed drift before mutation')
            self._version(); manifest=verify_release(Path(release_dir),self.s)
            config=(package/'nginx.conf').read_bytes(); dropin=(package/'dropin.conf').read_bytes()
            if hashlib.sha256(config).hexdigest()!=plan['metadata']['config_sha256']: raise ValueError('staging digest mismatch')
            # Prepare pool/helpers before journal: append-only objects never alter
            # live service and can safely survive a failed migration.
            update_asset_pool(Path(release_dir)/'dist',self.root/'pool',uid=self.uid,fault=self.fault)
            helper=Path(plan['helper_dir']); self._install_helpers(package,helper)
            if not self.dropdir.exists(): mkdir(self.dropdir,0o755,self.uid)
            self._assert_static(self._capture(),plan['origin'])
            if self._managed()!=plan['before']: raise ValueError('CAS managed drift before journal')
            after={str(self.config):file_state(config,uid=self.uid),str(self.drop):file_state(dropin,uid=self.uid),
                   str(self.current):{'symlink':'releases/'+manifest['source_sha'],'uid':self.uid,'gid':os.getgid(),'mode':0o777}}
            receipt={**plan,'manifest':manifest,'operation':'apply','after':after,'switch_intent':False}
            try:
                self._journal(receipt,'prepared')
                for path,state in after.items(): install_state(path,state); self.fault('installed_'+Path(path).name)
                self._journal(receipt,'files_installed')
                self._known(receipt); self.manager.reload(); self.fault('after_reload')
                loaded=self._capture(http=False)
                if not self._nginx_effective(loaded['properties'],receipt): raise ValueError('candidate effective invocation mismatch')
                receipt['applied_effective']={key:loaded['properties'][key] for key in STATIC}
                self._journal(receipt,'daemon_reloaded')
                self._known(receipt)
                if plan['origin']['properties']['ActiveState']=='active':
                    receipt['switch_intent']=True; self._journal(receipt,'daemon_reloaded'); self.manager.restart()
                self.fault('after_switch'); self._journal(receipt,'service_switched')
                self._verify_applied(receipt); self._journal(receipt,'verified')
                # last first: recovery still uses the incomplete journal if a
                # cut falls between last publication and journal committed.
                committed={**receipt,'phase':'committed'}; write_json(last,committed)
                self.fault('after_last'); self._journal(receipt,'committed')
                return {'applied':manifest['source_sha'],'idempotent':False}
            except Exception as error:
                try:
                    self._restore(receipt); self._journal(receipt,'recovered')
                    # A precommitted last pointer must not survive restoration.
                    write_json(last,{**receipt,'phase':'recovered'})
                except Exception as restore_error:
                    raise RuntimeError(str(error)+'; restoration ALSO FAILED: '+str(restore_error)) from error
                raise RuntimeError(str(error)+'; original service restored') from error

    def rollback(self):
        mkdir(self.state,0o700,self.uid)
        with locked(self.state/'lock',self.uid):
            self._recover()
            if not (self.state/'last.json').exists(): raise ValueError('no committed migration to roll back')
            receipt=read_json(self.state/'last.json',self.uid)
            if receipt['phase']=='rolled_back':
                self._verify_original(receipt); return {'rolled_back':True,'idempotent':True}
            if receipt['phase']!='committed': raise ValueError('no committed migration to roll back')
            self._verify_applied(receipt)
            receipt={**receipt,'operation':'rollback','switch_intent':False}
            try:
                self._journal(receipt,'prepared')
                self._restore(receipt); self._journal(receipt,'files_installed')
                self._journal(receipt,'daemon_reloaded'); self._journal(receipt,'service_switched')
                self._verify_original(receipt); self._journal(receipt,'verified')
                self._journal(receipt,'rolled_back'); write_json(self.state/'last.json',receipt)
                return {'rolled_back':True,'idempotent':False}
            except Exception as error:
                # Original restoration is itself retryable; preserve its journal.
                raise RuntimeError('rollback incomplete, recovery required: '+str(error)) from error


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('render','prepare','apply','rollback'))
    parser.add_argument('--settings',required=True,type=Path)
    parser.add_argument('--release-dir',type=Path)
    parser.add_argument('--staging-dir',type=Path)
    parser.add_argument('--user',action='store_true')
    parser.add_argument('--override-backend')
    args=parser.parse_args()
    try:
        check_controlled_directory(args.settings.absolute().parent,os.getuid() if args.user else 0)
        check_object(checked_path(args.settings),os.getuid() if args.user else 0)
        settings=json.loads(args.settings.read_text())
        controller=Controller(settings,user=args.user)
        override=backend_url(args.override_backend) if args.override_backend else None
        if override and not args.user: raise ValueError('backend override requires --user')
        if args.action=='rollback':
            if override or args.release_dir or args.staging_dir: raise ValueError('rollback only accepts settings and mode')
            result=controller.rollback()
        else:
            if not args.release_dir or not args.staging_dir: raise ValueError('--release-dir and --staging-dir required')
            result=getattr(controller,'prepare' if args.action=='render' else args.action)(args.release_dir,args.staging_dir,override)
        print(json.dumps(result,sort_keys=True)); return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,subprocess.CalledProcessError) as error:
        print('frontend release refused: '+str(error),file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
