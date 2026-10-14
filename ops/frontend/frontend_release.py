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
                             read_json, restore_snapshot, snapshot_managed, sync_dir, update_asset_pool, write_json,directory_state)
from release_systemd import Manager, STATIC, nginx_process, same_process, execution_commands
from runtime_state import verify_served, verify_pool
from origin_contract import (load as load_origin_contract, canonical as canonical_origin_contract,
                             validate_activation)

HELPERS=('runtime_state.py','launch_nginx.py','runtime_verify.py','release_manifest.py','nginx_config.py','build_release.py')
PHASES=('prepared','files_installed','daemon_reloaded','service_switched','verified','committed')

# The journal state is an authority boundary, not a progress hint.  A durable
# intent admits only the filesystem prefix immediately before or after that
# one write.  Recovery never accepts a bag of independently old/new paths.
ORIGIN='ORIGIN'; FILES_INSTALLED='FILES_INSTALLED'; RELOAD_INTENT='RELOAD_INTENT'
READY_TO_SWITCH='READY_TO_SWITCH'; SWITCH_INTENT='SWITCH_INTENT'
CANDIDATE_UNWITNESSED='CANDIDATE_UNWITNESSED'; CANDIDATE_WITNESSED='CANDIDATE_WITNESSED'
VERIFIED='VERIFIED'; COMMITTED='COMMITTED'
STOP_INTENT='STOP_INTENT'; STOPPED='STOPPED'; RESTORE_FILES='RESTORE_FILES'
RESTORE_RELOAD_INTENT='RESTORE_RELOAD_INTENT'; RESTORE_READY='RESTORE_READY'
RESTART_INTENT='RESTART_INTENT'; RESTORED_UNWITNESSED='RESTORED_UNWITNESSED'
RESTORED_WITNESSED='RESTORED_WITNESSED'; ROLLED_BACK='ROLLED_BACK'


class RetryRequired(RuntimeError): pass


class Controller:
    def __init__(self,settings, *, user=False,manager=None,fault=None,origin_contract=None,activation_receipt=None):
        self.s=dict(settings); self.user=user; self.fault=fault or (lambda boundary:None)
        self.origin_contract_path=Path(origin_contract) if origin_contract else None
        self.activation_receipt_path=Path(activation_receipt) if activation_receipt else None
        self.origin_contract=None
        self.uid=os.getuid() if user else 0
        if user:
            if not re.fullmatch(r'p100-[A-Za-z0-9_-]+\.service',str(self.s.get('service',''))):
                raise ValueError('--user requires private p100-*.service')
            if not self.s.get('private_mode') or self.s.get('owner_uid')!=self.uid:
                raise ValueError('--user requires explicit private_mode and current owner_uid')
            runtime=os.environ.get('XDG_RUNTIME_DIR')
            if not runtime or self.s.get('user_runtime_dir')!=runtime:
                raise ValueError('user_runtime_dir must equal manager XDG_RUNTIME_DIR')
            self.s['process_uid']=self.uid; self.s['process_gid']=os.getgid()
            if 'runtime_path' in self.s and self.s['runtime_path']!=runtime+'/'+self.s['runtime_directory']:
                raise ValueError('user RuntimeDirectory path mismatch')
        else:
            if os.getuid()!=0 or self.s.get('private_mode') or self.s.get('owner_uid',0)!=0:
                raise ValueError('system mode requires UID0 and root-owned system settings')
            if self.s.get('service')!='jax-platform-frontend.service' or self.s.get('user')!='jaxsvc' or self.s.get('group')!='jaxsvc':
                raise ValueError('unsupported system unit contract')
            self.s['owner_uid']=0; self.s['process_uid']=pwd.getpwnam(self.s['user']).pw_uid
            self.s['process_gid']=grp.getgrnam(self.s['group']).gr_gid
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
        # A candidate configuration is named by a transaction identifier.  A
        # controller created for recovery rebinds this only from its signed
        # durable receipt; it never guesses a live nginx.conf.
        self.config=None; self.current=self.root/'current'; self.drop=self.dropdir/self.s['dropin_name']
        self.state=self.configroot/'state'
        runtime=Path('/run/user')/str(self.uid) if user else Path('/run')
        if user and (str(runtime)!=self.s['user_runtime_dir'] or runtime.stat().st_uid!=self.uid):
            raise ValueError('user manager runtime must be canonical /run/user/UID')
        self.lock_directory=runtime/('p100-frontend-release-locks' if user else 'jax-frontend-release-locks')
        self.lock_path=self.lock_directory/(self.s['service']+'.lock')
        self.manager=manager if manager is not None else Manager(self.s,user)

    def _version(self):
        result=subprocess.run([self.s['nginx_binary'],'-v'],capture_output=True,text=True,check=True)
        version=re.search(r'nginx/([0-9.]+)',result.stdout+result.stderr)
        if not version or version[1]!=self.s['nginx_version']: raise ValueError('nginx version mismatch')

    def _config_for_txid(self,txid):
        if not isinstance(txid,str) or not re.fullmatch(r'[0-9a-f]{32}',txid):
            raise ValueError('invalid transaction identifier')
        return self.configroot/('nginx-'+txid+'.conf')

    def _bind_receipt(self,receipt):
        txid=receipt.get('txid')
        config=receipt.get('config_path')
        expected=self._config_for_txid(txid)
        if config!=str(expected): raise ValueError('receipt candidate config path differs')
        self.config=expected
        if receipt.get('origin_contract') is not None:
            contract=receipt['origin_contract']
            if not isinstance(contract,dict) or not isinstance(contract.get('sha256'),str):
                raise ValueError('receipt origin contract digest missing')
            payload={key:value for key,value in contract.items() if key!='sha256'}
            if hashlib.sha256(canonical_origin_contract(payload).encode()).hexdigest()!=contract['sha256']:
                raise ValueError('receipt origin contract digest differs')
            source=receipt.get('manifest',receipt).get('source_sha')
            if payload.get('source_sha')!=source:
                raise ValueError('receipt origin contract source differs')
            self.origin_contract=contract

    def _managed(self):
        legacy=self.configroot/'nginx.conf'; config=self.config or legacy
        return {**snapshot_managed([legacy,config,self.drop,self.current],uid=self.uid),
                str(self.dropdir):directory_state(self.dropdir,self.uid)}

    def _capture(self,http=True, *, exclude_managed_drop=True):
        capture=self.manager.capture(http=http)
        if exclude_managed_drop:
            capture['files'].pop(str(self.drop),None)
        return capture

    def _assert_static(self,actual,expected):
        self.manager.coherent(actual)
        if actual.get('unit_paths')!=expected.get('unit_paths'): raise ValueError('CAS UnitPath drift')
        if actual['files']!=expected['files']: raise ValueError('CAS base/dropins drift')
        for key in (*STATIC,'UnitFileState','ActiveState'):
            if actual['properties'][key]!=expected['properties'][key]: raise ValueError('CAS effective/state drift: '+key)
        if not same_process(actual['process'],expected['process']): raise ValueError('CAS process drift')
        if actual.get('http')!=expected.get('http'): raise ValueError('CAS original HTTP drift')

    def _validate_original(self,origin, expected_checkout_sha=None):
        """Accredit the Vite definition, even when the unit is inactive."""
        contract=dict(self.s.get('original_contract') or {})
        if not contract: raise ValueError('explicit original ExecStart contract required')
        kind=contract.get('kind')
        if kind=='system-vite' and getattr(self,'origin_contract',None) is not None:
            if self.origin_contract.get('source_sha')!=expected_checkout_sha:
                raise ValueError('durable origin contract source differs')
            for key in ('entry_target','entry_sha256','vite_package_sha256','lockfile_sha256'):
                contract[key]=self.origin_contract[key]
        if kind not in ('private-vite-canary','system-vite') or (kind=='private-vite-canary')!=self.user:
            raise ValueError('original ExecStart contract mode is not permitted')
        if kind=='system-vite':
            required=('node_binary','node_sha256','node_owner_uid','node_owner_gid','node_mode','checkout','checkout_branch','working_directory','entry','entry_target','entry_sha256',
                      'vite_package','vite_package_sha256','lockfile','lockfile_sha256','argv','guard','guard_sha256',
                      'guard_owner_uid','guard_owner_gid','guard_mode','exec_start_pre','user','group')
            if any(key not in contract for key in required): raise ValueError('system Vite contract is incomplete')
            if not isinstance(expected_checkout_sha,str) or not re.fullmatch(r'[0-9a-f]{40}',expected_checkout_sha):
                raise ValueError('system Vite requires an audited release source SHA')
            numeric=('node_owner_uid','node_owner_gid','guard_owner_uid','guard_owner_gid')
            if not all(isinstance(contract[key],str) for key in required if key not in ('argv','exec_start_pre',*numeric)) or not all(isinstance(contract[key],int) for key in numeric):
                raise ValueError('system Vite contract has invalid paths')
            if not isinstance(contract['argv'],list) or not all(isinstance(value,str) for value in contract['argv']):
                raise ValueError('system Vite contract has invalid argv')
            if not isinstance(contract['exec_start_pre'],list) or not all(isinstance(value,dict) for value in contract['exec_start_pre']):
                raise ValueError('system Vite contract has invalid ExecStartPre')
            node=Path(contract['node_binary']); checkout=Path(contract['checkout']); working=Path(contract['working_directory'])
            entry=Path(contract['entry']); package=Path(contract['vite_package']); lockfile=Path(contract['lockfile']); guard=Path(contract['guard'])
            try:
                if any(path.is_symlink() or not path.is_file() for path in (node,package,lockfile,guard)) or not checkout.is_dir() or not working.is_dir():
                    raise ValueError('system Vite contract has unsafe path')
                for path,prefix in ((node,'node'),(guard,'guard')):
                    stat=path.stat()
                    if (stat.st_uid!=contract[prefix+'_owner_uid'] or stat.st_gid!=contract[prefix+'_owner_gid'] or
                        format(stat.st_mode & 0o777,'04o')!=contract[prefix+'_mode']):
                        raise ValueError('system Vite contract bytes differ')
                if not entry.is_symlink() or os.readlink(entry)!=contract['entry_target']:
                    raise ValueError('system Vite entry symlink differs')
                target=(entry.parent/contract['entry_target']).resolve(strict=True)
                if target.is_symlink() or not target.is_file() or target!=working/'node_modules/vite/bin/vite.js':
                    raise ValueError('system Vite entry target is unsafe')
                if (hashlib.sha256(node.read_bytes()).hexdigest()!=contract['node_sha256'] or
                    hashlib.sha256(target.read_bytes()).hexdigest()!=contract['entry_sha256'] or
                    hashlib.sha256(package.read_bytes()).hexdigest()!=contract['vite_package_sha256'] or
                    hashlib.sha256(lockfile.read_bytes()).hexdigest()!=contract['lockfile_sha256'] or
                    hashlib.sha256(guard.read_bytes()).hexdigest()!=contract['guard_sha256']):
                    raise ValueError('system Vite contract bytes differ')
                checkout_real=checkout.resolve(strict=True)
                if checkout_real.is_symlink() or checkout_real!=checkout:
                    raise ValueError('system Vite checkout is unsafe')
                # Root validates the jaxsvc-owned published checkout.  Limit
                # Git's dubious-ownership exception to this checked path.
                git=['git','-c','safe.directory='+str(checkout_real),'-C',str(checkout_real)]
                source=subprocess.run([*git,'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
                if source!=expected_checkout_sha: raise ValueError('system Vite checkout source differs')
                if subprocess.run([*git,'branch','--show-current'],capture_output=True,text=True,check=True).stdout.strip()!=contract['checkout_branch']:
                    raise ValueError('system Vite checkout branch differs')
                if subprocess.run([*git,'status','--porcelain'],capture_output=True,text=True,check=True).stdout:
                    raise ValueError('system Vite checkout dirty')
                if subprocess.run([str(node),'--version'],capture_output=True,text=True,check=True).stdout.strip()!='v'+self.s['node_version']:
                    raise ValueError('system Vite Node version differs')
            except OSError as error:
                raise ValueError('system Vite contract cannot be accredited') from error
            expected={'path':str(node),'argv':[str(node),str(entry),*contract['argv']],'ignore_errors':False}
            if execution_commands(origin['properties']['ExecStart'])!=[expected]:
                raise ValueError('original ExecStart differs from system Vite contract')
            if execution_commands(origin['properties']['ExecStartPre'])!=contract['exec_start_pre']:
                raise ValueError('original ExecStartPre differs from system Vite contract')
            if any(origin['properties'][key]!=contract[key.lower() if key!='WorkingDirectory' else 'working_directory']
                   for key in ('User','Group','WorkingDirectory')):
                raise ValueError('original system Vite identity differs')
            process=origin['process']
            if process is not None and (process['uid']!=self.s['process_uid'] or process['gid']!=self.s['process_gid'] or process['exe']!=os.path.realpath(node)
                                        or process['exe_sha256']!=contract['node_sha256'] or process['argv']!=expected['argv'] or
                                        origin['properties']['ControlGroup'] not in process['cgroup']):
                raise ValueError('original MainPID differs from system Vite contract')
            return
        required=('node_binary','entry','entry_sha256','vite_module','vite_sha256')
        if any(not isinstance(contract.get(key),str) for key in required):
            raise ValueError('original ExecStart contract is incomplete')
        node=Path(contract['node_binary']); entry=Path(contract['entry']); vite=Path(contract['vite_module'])
        try:
            if any(path.is_symlink() or not path.is_file() for path in (node,entry,vite)):
                raise ValueError('original ExecStart contract has unsafe path')
            if hashlib.sha256(entry.read_bytes()).hexdigest()!=contract['entry_sha256'] or hashlib.sha256(vite.read_bytes()).hexdigest()!=contract['vite_sha256']:
                raise ValueError('original ExecStart contract bytes differ')
            if subprocess.run([str(node),'--version'],capture_output=True,text=True,check=True).stdout.strip()!='v'+self.s['node_version']:
                raise ValueError('original ExecStart Node version differs')
        except OSError as error:
            raise ValueError('original ExecStart contract cannot be accredited') from error
        expected={'path':str(node),'argv':[str(node),str(entry)],'ignore_errors':False}
        if execution_commands(origin['properties']['ExecStart'])!=[expected]:
            raise ValueError('original ExecStart differs from accredited Vite contract')
        if origin['properties']['ExecStartPre'] and any(command['ignore_errors'] for command in execution_commands(origin['properties']['ExecStartPre'])):
            raise ValueError('original ExecStartPre ignores failures')
        process=origin['process']
        if process is not None and (process['uid']!=self.s['process_uid'] or process['gid']!=self.s['process_gid'] or process['exe']!=os.path.realpath(node)
                                    or process['argv']!=expected['argv'] or origin['properties']['ControlGroup'] not in process['cgroup']):
            raise ValueError('original MainPID differs from accredited Vite contract')

    def _ready_to_switch(self,receipt):
        """One complete, no-reload candidate state immediately before restart."""
        self._check_helpers(receipt)
        manifest=verify_release(Path(receipt['release_dir']),self.s,receipt['metadata']['source_sha'])
        verify_served(self.s,Path(receipt['release_dir']),manifest)
        if self._managed()!=receipt['after']: raise ValueError('ready state managed files differ')
        capture=self._capture(http=False,exclude_managed_drop=False)
        self.manager.coherent(capture)
        if {path:state for path,state in capture['files'].items() if path!=str(self.drop)}!=receipt['origin']['files']:
            raise ValueError('ready state base/dropins differ')
        if capture['properties']['UnitFileState']!=receipt['origin']['properties']['UnitFileState']:
            raise ValueError('ready state enabled differs')
        if not self._nginx_effective(capture['properties'],receipt): raise ValueError('ready state effective differs')
        if capture['properties']['ActiveState']!=receipt['origin']['properties']['ActiveState']:
            raise ValueError('ready state active differs')
        if not same_process(capture['process'],receipt['origin']['process']):
            raise ValueError('ready state original process differs; no signal sent')
        return capture

    def _candidate_signal_witness(self,receipt,capture):
        self._exact_candidate(receipt,capture)
        if self._managed()!=receipt['after']: raise ValueError('candidate managed files differ')
        self.manager.coherent(capture)
        if {path:state for path,state in capture['files'].items() if path!=str(self.drop)}!=receipt['origin']['files']:
            raise ValueError('candidate base/dropins differ')
        return capture

    def _managed_prefixes(self,receipt):
        """Return every equivalent durable prefix represented by live files."""
        prefixes=receipt.get('managed_prefixes')
        if not isinstance(prefixes,list) or not prefixes:
            raise ValueError('journal lacks durable managed-prefix contract')
        actual=self._managed()
        matches=[index for index,expected in enumerate(prefixes) if actual==expected]
        if matches: return matches
        raise ValueError('CAS unknown managed drift')

    def _managed_prefix(self,receipt):
        # A no-op directory install can make adjacent exact prefixes equal.
        # Recovery chooses the furthest proven prefix; authorization still
        # checks that its durable state admits at least one exact match.
        return max(self._managed_prefixes(receipt))

    def _state_allows_prefix(self,receipt,index):
        state=receipt.get('state')
        total=len(receipt.get('managed_prefixes',()))-1
        if state==ORIGIN: allowed={0}
        elif isinstance(state,str) and re.fullmatch(r'INSTALLING_[0-9]+',state):
            k=int(state.rsplit('_',1)[1]); allowed={k,k+1}
        elif state in (FILES_INSTALLED,RELOAD_INTENT,READY_TO_SWITCH,SWITCH_INTENT,
                       CANDIDATE_UNWITNESSED,CANDIDATE_WITNESSED,VERIFIED,COMMITTED,
                       ): allowed={total}
        elif state in (STOP_INTENT,STOPPED):
            start=receipt.get('restore_start_prefix')
            if not isinstance(start,int): raise ValueError('restore intent lacks exact start prefix')
            allowed={start}
        elif isinstance(state,str) and re.fullmatch(r'RESTORE_INSTALLING_[0-9]+',state):
            k=int(state.rsplit('_',1)[1]); allowed={k,k-1}
        elif state in (RESTORE_FILES,RESTORE_RELOAD_INTENT,RESTORE_READY,RESTART_INTENT,
                       RESTORED_UNWITNESSED,RESTORED_WITNESSED,ROLLED_BACK): allowed={0}
        else: raise ValueError('journal state is unknown')
        if index not in allowed or not 0<=index<=total:
            raise ValueError('journal state/prefix mismatch: '+str(state))
        return state

    def _known(self,receipt):
        """Accept only the exact durable prefix permitted by journal state."""
        self._check_helpers(receipt)
        matches=self._managed_prefixes(receipt)
        failures=[]
        for index in matches:
            try: self._state_allows_prefix(receipt,index)
            except ValueError as error: failures.append(error)
            else: break
        else: raise failures[0]
        state=receipt.get('state')
        # Before daemon-reload the just-written managed drop is intentionally
        # on disk but absent from loaded DropInPaths.  Every state that can
        # signal a process requires a fully coherent no-reload observation.
        pre_reload=((isinstance(state,str) and state.startswith(('INSTALLING_','RESTORE_INSTALLING_'))) or state in (FILES_INSTALLED,RELOAD_INTENT,RESTORE_FILES) or
                    state in (STOP_INTENT,STOPPED) and receipt.get('restore_allows_unloaded_drop',False))
        # A reload intent is the one manager transition whose disk PRE has
        # already been restored while systemd still has the candidate loaded.
        # Persist that complete PRE observation before daemon-reload.  On
        # recovery, accept only those bytes or the fully coherent POST.
        if state==RESTORE_RELOAD_INTENT:
            capture=self._capture(http=False,exclude_managed_drop=False)
            reload_pre=receipt.get('restore_reload_pre')
            if capture!=reload_pre:
                self.manager.coherent(capture)
        else:
            capture=self._capture(http=False,exclude_managed_drop=pre_reload)
            self.manager.coherent(capture,allowed_drop=str(self.drop) if pre_reload else None)
        if {path:value for path,value in capture['files'].items() if path!=str(self.drop)}!=receipt['origin']['files']:
            raise ValueError('CAS base/dropins drift')
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
        transition=receipt.get('switch_intent') or receipt['operation']=='rollback'
        if actualprocess is not None and not same_process(actualprocess,original):
            candidate=receipt.get('candidate_identity')
            if not transition or candidate is None or not same_process(actualprocess,candidate):
                raise ValueError('CAS unknown process; no signal sent')
        return capture

    def _candidate_capture(self):
        deadline=time.monotonic()+5
        while True:
            capture=self._capture(http=False,exclude_managed_drop=False); process=capture['process']
            launcher=['/usr/bin/python3','-I','-B',str(Path(self.configroot/'helpers'))]
            # The helper path has a content address, so match the full stable
            # suffix instead of treating a Python MainPID as nginx.
            bootstrap=(process and process['exe']==os.path.realpath('/usr/bin/python3') and
                       process['argv'][:3]==launcher[:3] and any(Path(arg).name=='launch_nginx.py' for arg in process['argv']))
            # systemd can expose nginx's MainPID after exec but before nginx
            # gives itself its stable master-process argv.  That PID is not
            # an authority to stop yet; wait for the exact invocation.
            settling=(process and process['exe']==self.s['nginx_binary'] and
                      not nginx_process(process,self.s,self.config))
            if not bootstrap and not settling:
                return capture
            if time.monotonic()>=deadline: raise ValueError('nginx launcher did not exec')
            time.sleep(.02)

    def _exact_candidate(self,receipt,capture):
        """Accredit the nginx activation that this transaction may later stop."""
        properties=capture['properties']; process=capture['process']; origin=receipt['origin']
        if properties['ActiveState']!='active' or properties['SubState']!='running' or properties['Result']!='success':
            raise ValueError('candidate unit is not a successful active service')
        if properties['ControlPID']!='0' or not properties['InvocationID']:
            raise ValueError('candidate control/invocation identity is incomplete')
        if origin['process'] is not None and properties['InvocationID']==origin['properties']['InvocationID']:
            raise ValueError('candidate did not receive a new InvocationID')
        if origin['process'] is not None and process['boot_id']!=origin['process']['boot_id']:
            raise ValueError('candidate boot identity differs from origin')
        if not self._nginx_effective(properties,receipt): raise ValueError('candidate effective invocation mismatch')
        if not nginx_process(process,self.s,self.config): raise ValueError('candidate MainPID/argv mismatch')
        if process['gid']!=self.s['process_gid'] or properties['ControlGroup'] not in process['cgroup']:
            raise ValueError('candidate process group/cgroup differs')
        wanted=self.s.get('nginx_sha256')
        if not isinstance(wanted,str) or not re.fullmatch(r'[0-9a-f]{64}',wanted) or process['exe_sha256']!=wanted:
            raise ValueError('candidate nginx executable fingerprint differs')
        if process['invocation_id']!=properties['InvocationID']:
            raise ValueError('candidate process invocation differs')
        return capture

    def _nginx_effective(self,properties,receipt):
        expected_user='' if self.user else self.s['user']; expected_group='' if self.user else self.s['group']
        commands=execution_commands
        helper=Path(receipt['helper_dir'])
        launcher='/usr/bin/python3 -I -B '+str(helper/'launch_nginx.py')+' --binary '+self.s['nginx_binary']+' --config '+str(self.config)
        expected_start=launcher
        expected_pre=['/usr/bin/python3 -I -B '+str(helper/'runtime_verify.py')+' --settings '+str(helper/'settings.json')+
                      ' --release-dir '+receipt['release_dir']+' --config '+str(self.config)+' --metadata '+str(helper/'render.json'),
                      launcher+' --test']
        return (properties['Type']=='simple' and properties['KillMode']=='control-group'
                and properties['WorkingDirectory']=='/' and properties['User']==expected_user and properties['Group']==expected_group
                and commands(properties['ExecStart'])==[{'path':'/usr/bin/python3','argv':expected_start.split(' '),'ignore_errors':False}]
                and commands(properties['ExecStartPre'])==[{'path':'/usr/bin/python3','argv':command.split(' '),'ignore_errors':False} for command in expected_pre]
                and properties['RuntimeDirectory']==self.s['runtime_directory'] and properties['RuntimeDirectoryMode']=='0700'
                and properties['StandardOutput']=='journal' and properties['StandardError']=='journal')

    def _restore(self,receipt):
        initial=self._known(receipt)
        pre_restore_state=receipt.get('state')
        receipt['restore_start_prefix']=self._managed_prefix(receipt)
        pre_reload=(isinstance(pre_restore_state,str) and pre_restore_state.startswith(('INSTALLING_','RESTORE_INSTALLING_'))) or pre_restore_state in (FILES_INSTALLED,RELOAD_INTENT,RESTORE_FILES,RESTORE_RELOAD_INTENT)
        untouched_original=(pre_reload or pre_restore_state in (READY_TO_SWITCH,SWITCH_INTENT)) and same_process(initial['process'],receipt['origin']['process'])
        receipt['restore_without_signal']=untouched_original
        receipt['restore_allows_unloaded_drop']=pre_reload
        # Persist intent before systemctl, covering signal-to-journal crash gaps.
        receipt['switch_intent']=True; self._journal(receipt,receipt['phase'],STOP_INTENT)
        self.fault('stop_intent_persisted')
        capture=self._known(receipt)
        # A failure before the candidate restart still has the exact original
        # Vite process.  Restore its files/reload without disrupting it; only
        # an actually switched (or inactive) service needs stop/restart.
        # Do not stop/restart an unknown PID. _known already proved its identity.
        if capture['properties']['ActiveState']=='active' and not untouched_original: self.manager.stop()
        self._journal(receipt,receipt['phase'],STOPPED)
        self.fault('restore_stopped')
        # Recovery can start from any durable apply prefix.  Reverse only the
        # writes that are actually present; a prefix is exact, never a union.
        present=self._managed_prefix(receipt)
        order=list(reversed(receipt['mutation_order'][:present]))
        for index,path in enumerate(order):
            # Before each inverse write, durable state permits only this exact
            # prefix or the next one.  It is therefore safe after SIGKILL.
            current=present-index
            self._journal(receipt,receipt['phase'],'RESTORE_INSTALLING_'+str(current))
            state=receipt['before'][path]
            install_state(path,state); self.fault('restored_'+Path(path).name)
            self._journal(receipt,receipt['phase'],'RESTORE_INSTALLING_'+str(current-1))
        self.fault('restore_files')
        self._journal(receipt,receipt['phase'],RESTORE_FILES)
        receipt['restore_reload_pre']=self._capture(http=False,exclude_managed_drop=False)
        self._journal(receipt,receipt['phase'],RESTORE_RELOAD_INTENT)
        self.fault('restore_reload_intent_persisted')
        self.manager.reload(); self.fault('restore_reloaded')
        self._journal(receipt,receipt['phase'],RESTORE_READY)
        self.fault('restore_ready')
        self._restore_ready(receipt,untouched_original=untouched_original)
        if receipt['origin']['properties']['ActiveState']=='active' and not untouched_original:
            self._journal(receipt,receipt['phase'],RESTART_INTENT)
            self.fault('restart_intent_persisted')
            # Journal intent is durable before the signal; recapture after it
            # so an external Vite cannot be restarted by this controller.  A
            # process that is byte-for-byte the original is already the
            # desired service, though: witness it durably and do not signal.
            ready=self._restore_ready(receipt,untouched_original=False,allow_exact_original=True)
            if same_process(ready['process'],receipt['origin']['process']):
                receipt['restore_without_signal']=True
            else:
                self.manager.restart()
                self._journal(receipt,receipt['phase'],RESTORED_UNWITNESSED)
        self.fault('restore_service')
        restored=self._verify_original(receipt,restarted=receipt['origin']['properties']['ActiveState']=='active' and not receipt.get('restore_without_signal',False))
        receipt['restored_identity']=restored['process']
        self._journal(receipt,'restored_identified',RESTORED_WITNESSED)
        self.fault('restored_identity_persisted')

    def _verify_original(self,receipt, *, restarted=False):
        actual=self._capture()
        self.manager.coherent(actual)
        if actual['unit_paths']!=receipt['origin']['unit_paths']:
            raise ValueError('original UnitPath drift')
        if actual['files']!=receipt['origin']['files']:
            raise ValueError('original base/dropins drift')
        for key in (*STATIC,'UnitFileState','ActiveState'):
            if actual['properties'][key]!=receipt['origin']['properties'][key]: raise ValueError('original effective/state drift: '+key)
        old=receipt['origin']['process']; current=actual['process']
        if old is None:
            if current is not None: raise ValueError('inactive original acquired a process')
        elif current is None:
            raise ValueError('original process identity drift')
        elif not restarted and not same_process(current,old):
            raise ValueError('original process identity drift')
        elif restarted:
            if (actual['properties']['ActiveState']!='active' or actual['properties']['SubState']!='running' or
                actual['properties']['Result']!='success' or actual['properties']['ControlPID']!='0' or
                current['boot_id']!=old['boot_id'] or current['invocation_id']==old['invocation_id']):
                raise ValueError('restored service state is not exact')
            candidate=receipt.get('candidate_identity')
            if candidate is not None and current['invocation_id']==candidate['invocation_id']:
                raise ValueError('restored service retained candidate invocation')
        self._validate_original(actual,receipt['manifest']['source_sha'])
        if self._managed()!=receipt['before']: raise ValueError('restoration files mismatch')
        if actual['http']!=receipt['origin']['http']: raise ValueError('original HTTP proof mismatch')
        return actual

    def _restore_ready(self,receipt, *, untouched_original,allow_exact_original=False):
        """CAS restored disk and unit selection immediately before restart."""
        actual=self._capture(http=False,exclude_managed_drop=False)
        self.manager.coherent(actual)
        if actual['unit_paths']!=receipt['origin']['unit_paths']:
            raise ValueError('restored UnitPath drift before restart')
        if actual['files']!=receipt['origin']['files']:
            raise ValueError('restored base/dropins drift before restart')
        for key in (*STATIC,'UnitFileState'):
            if actual['properties'][key]!=receipt['origin']['properties'][key]:
                raise ValueError('restored effective/state drift before restart: '+key)
        original=receipt['origin']['process']; process=actual['process']
        if untouched_original:
            if not same_process(process,original):
                raise ValueError('restored original process drift before restart')
        elif allow_exact_original and same_process(process,original):
            # The controller did not start this process.  Its complete
            # original identity is the only active state safe to witness
            # between RESTART_INTENT and the restart signal.
            pass
        elif actual['properties']['ActiveState']!='inactive' or process is not None:
            raise ValueError('restored unit acquired external process before restart')
        return actual

    def _journal(self,receipt,phase,state=None):
        receipt['phase']=phase
        if state is not None: receipt['state']=state
        write_json(self.state/'journal.json',receipt)
        self.fault('journal_'+phase)

    def _recover(self):
        journal=self.state/'journal.json'
        if not journal.exists(): return
        receipt=read_json(journal,self.uid)
        self._bind_receipt(receipt)
        if receipt.get('state')==COMMITTED and receipt['phase']=='committed':
            return
        if not isinstance(receipt.get('state'),str):
            raise ValueError('legacy journal lacks fail-closed state machine')
        if receipt['phase'] in ('recovered','rolled_back'):
            # A terminal journal is authoritative even if the pointer update
            # was interrupted; repair it without restarting a service.
            self._verify_original(receipt,restarted=receipt.get('restored_identity') is not None and not receipt.get('restore_without_signal',False))
            write_json(self.state/'last.json',receipt)
            return
        if receipt.get('restored_identity') is not None:
            restored=self._verify_original(receipt,restarted=not receipt.get('restore_without_signal',False))
            if same_process(restored['process'],receipt['restored_identity']):
                resolved='rolled_back' if receipt['operation']=='rollback' else 'recovered'
                write_json(self.state/'last.json',{**receipt,'phase':resolved})
                self._journal(receipt,resolved,RESTORED_WITNESSED if resolved=='recovered' else ROLLED_BACK)
                raise RetryRequired('incomplete '+receipt['operation']+' already restored; retry explicitly')
        # If restoration had already restarted the original but the process
        # died before its witness journal, recognize that complete restored
        # state before considering any further signal.
        if receipt.get('candidate_identity') is not None:
            try:
                restored=self._verify_original(receipt,restarted=True)
            except ValueError:  # fail-soft: not restored; exact candidate or unknown-state checks follow before any signal
                restored=None
            if restored is not None:
                receipt['restored_identity']=restored['process']
                self._journal(receipt,'restored_identified',RESTORED_WITNESSED)
        # A cut after restart can leave nginx exact but without a durable
        # candidate witness.  Capture it afresh and persist its identity before
        # _restore is allowed to issue a stop.  An exact original is already
        # safe; record that witness and finish without a signal.
        if receipt.get('switch_intent') and receipt.get('candidate_identity') is None:
            observed=self._candidate_capture()
            try:
                candidate=self._exact_candidate(receipt,observed)
            except ValueError:
                # A SIGKILL at SWITCH_INTENT can still be the exact original
                # Vite with candidate files already loaded.  It is ready to
                # switch, not restored; _restore will revert that delta
                # without a stop/restart.
                self._ready_to_switch(receipt)
            else:
                # This is the authority-to-stop witness.  Persist it before
                # HTTP/API verification, whose failure must still permit a
                # proven candidate to be stopped on the next invocation.
                receipt['candidate_identity']=candidate['process']
                self._journal(receipt,'candidate_identified',CANDIDATE_WITNESSED)
                verified=self._verify_applied(receipt)
                if not same_process(verified['process'],candidate['process']):
                    raise ValueError('candidate changed during complete verification')
        if receipt.get('restored_identity') is not None:
            resolved='rolled_back' if receipt['operation']=='rollback' else 'recovered'
            write_json(self.state/'last.json',{**receipt,'phase':resolved})
            self._journal(receipt,resolved,RESTORED_WITNESSED if resolved=='recovered' else ROLLED_BACK)
            raise RetryRequired('incomplete '+receipt['operation']+' already restored; retry explicitly')
        self._restore(receipt)
        resolved='rolled_back' if receipt['operation']=='rollback' else 'recovered'
        write_json(self.state/'last.json',{**receipt,'phase':resolved})
        self._journal(receipt,resolved,RESTORED_WITNESSED if resolved=='recovered' else ROLLED_BACK)
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

    def prepare(self,release_dir,staging_dir,override=None,txid=None):
        """No active filesystem/state mutations; publish one complete staging package."""
        release_dir=checked_path(Path(release_dir)); staging_dir=checked_path(Path(staging_dir))
        if release_dir.parent!=self.root/'releases': raise ValueError('release must belong to configured releases root')
        if staging_dir.is_relative_to(self.root) or staging_dir.is_relative_to(self.configroot) or staging_dir.is_relative_to(self.systemroot):
            raise ValueError('staging must be outside active roots')
        check_controlled_directory(staging_dir.parent,self.uid)
        if staging_dir.exists(): raise ValueError('staging destination must be new')
        self._version(); manifest=verify_release(release_dir,self.s)
        previous_config=self.config
        activation=None
        if not self.user:
            if self.origin_contract_path is None: raise ValueError('system migration requires --origin-contract')
            contract,digest=load_origin_contract(self.origin_contract_path,manifest['source_sha'])
            self.origin_contract={**contract,'sha256':digest}
            if self.activation_receipt_path is None: raise ValueError('system migration requires --activation-receipt')
            # I1 is proof of the audited Vite S after its controlled restart.
            # Capture it first and require byte-for-byte identity before any
            # staging package exists; an old H still alive on disk S refuses.
            activation_origin=self._capture()
            activation=validate_activation(self.activation_receipt_path,manifest['source_sha'],self.s,
                                           activation_origin,contract_digest=digest)
        txid=txid or os.urandom(16).hex(); self.config=self._config_for_txid(txid)
        if override is not None and not self.user: raise ValueError('override only allowed with --user')
        config,metadata=render(settings=self.s,manifest=manifest,override_backend=override)
        self.release=release_dir
        helper_identity=hashlib.sha256(config.encode()+json.dumps(self.s,sort_keys=True).encode()+
                           b''.join(Path(__file__).with_name(name).read_bytes() for name in HELPERS)).hexdigest()
        helper=self.configroot/'helpers'/helper_identity
        dropin=self._dropin(helper)
        origin=activation_origin if activation is not None else self._capture()
        self.manager.coherent(origin)
        if origin['properties']['ActiveState'] not in ('active','inactive'): raise ValueError('original unit must be active or inactive')
        self._validate_original(origin,manifest['source_sha'])
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
            try:
                self.fault('before_nginx_preflight')
                subprocess.run([self.s['nginx_binary'],'-t','-c',str(package/'preflight.conf')],check=True,capture_output=True,text=True)
            except BaseException:
                self.config=previous_config
                raise
            candidate=package/'unit'; candidate.mkdir(mode=0o700)
            fragment=Path(origin['properties']['FragmentPath'])
            atomic_write(candidate/self.s['service'],fragment.read_bytes(),mode=0o644)
            candidate_drop=candidate/(self.s['service']+'.d'); candidate_drop.mkdir(mode=0o755)
            for raw in origin['properties']['DropInPaths'].split():
                state=origin['files'][raw]
                if raw!=str(fragment):
                    target=candidate_drop/Path(raw).name
                    if target.exists():
                        if target.read_bytes()!=base64.b64decode(state['bytes']): raise ValueError('ambiguous original dropin precedence')
                    else: atomic_write(target,base64.b64decode(state['bytes']),mode=0o644)
            atomic_write(candidate_drop/self.s['dropin_name'],dropin,mode=0o644)
            try:
                self.fault('before_systemd_preflight'); self.manager.verify_unit(candidate/self.s['service'])
            except BaseException:
                self.config=previous_config
                raise
            plan={'settings':self.s,'metadata':metadata,'release_dir':str(release_dir),'helper_dir':str(helper),
                  'helpers':snapshot_managed(list(bundle.iterdir()),uid=self.uid),'origin':origin,'before':before,
                  'config_digest':hashlib.sha256(config.encode()).hexdigest(),'txid':txid,'config_path':str(self.config),
                  'origin_contract':self.origin_contract}
            plan['helpers']={str(helper/Path(path).name):state for path,state in plan['helpers'].items()}
            atomic_write(package/'plan.json',json.dumps(plan,sort_keys=True)+'\n')
            # Runtime paths in this diagnostic config are scratch-only; do not
            # publish it as a reusable configuration.
            (package/'preflight.conf').unlink()
            sync_tree(package); publish_noreplace(package,staging_dir); sync_dir(staging_dir.parent)
        self.config=previous_config
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
        manifest=verify_release(Path(receipt['release_dir']),self.s,receipt['metadata']['source_sha'])
        verify_served(self.s,Path(receipt['release_dir']),manifest)
        if self._managed()!=receipt['after']: raise ValueError('CAS applied files drift')
        actual=self._capture(exclude_managed_drop=False)
        self.manager.coherent(actual)
        unowned_files={path:state for path,state in actual['files'].items() if path!=str(self.drop)}
        if unowned_files!=receipt['origin']['files']: raise ValueError('CAS base/dropins drift')
        if actual['properties']['UnitFileState']!=receipt['origin']['properties']['UnitFileState']: raise ValueError('CAS enabled state drift')
        if not self._nginx_effective(actual['properties'],receipt): raise ValueError('applied unit invocation mismatch')
        desired_active=receipt['origin']['properties']['ActiveState']
        if actual['properties']['ActiveState']!=desired_active: raise ValueError('CAS active state drift')
        if desired_active=='active':
            self._exact_candidate(receipt,actual)
            identity=receipt.get('candidate_identity')
            if identity is not None and not same_process(actual['process'],identity):
                raise ValueError('candidate process identity drift')
            http=actual['http']; index=receipt['manifest']['files']['index.html']
            if http!={'status':200,'mime':'text/html','sha':receipt['metadata']['source_sha'],'digest':index}:
                raise ValueError('applied HTTP release/index proof mismatch')
            if self.manager.http('/api')['sha'] is not None: raise ValueError('API leaked static SHA header')
        return actual

    def apply(self,release_dir,staging_dir,override=None):
        mkdir(self.state,0o700,self.uid)
        mkdir(self.lock_directory,0o700,self.uid)
        with locked(self.lock_path,self.uid):
            self._recover()
            last=self.state/'last.json'
            if last.exists():
                previous=read_json(last,self.uid)
                if previous['phase']=='committed':
                    manifest=verify_release(Path(release_dir),self.s)
                    if self.s!=previous['settings']: raise ValueError('normalized settings differ; rollback required')
                    if str(checked_path(Path(release_dir)))!=previous['release_dir']: raise ValueError('release path differs; rollback required')
                    requested,metadata=render(settings=self.s,manifest=manifest,override_backend=override)
                    if metadata!=previous['metadata']: raise ValueError('render request differs; rollback required')
                    for name in HELPERS:
                        if Path(__file__).with_name(name).read_bytes()!=(Path(previous['helper_dir'])/name).read_bytes():
                            raise ValueError('helper version differs; rollback required')
                    if manifest['source_sha']!=previous['metadata']['source_sha']:
                        raise ValueError('another SHA is applied; rollback required first')
                    if override!=previous['metadata']['override_backend']: raise ValueError('same SHA override differs; rollback required')
                    self._verify_applied(previous)
                    return {'applied':manifest['source_sha'],'idempotent':True}
            package=Path(staging_dir)
            self.prepare(release_dir,package,override)
            plan=read_json(package/'plan.json',self.uid); self._bind_receipt(plan)
            self._assert_static(self._capture(),plan['origin'])
            if self._managed()!=plan['before']: raise ValueError('CAS managed drift before mutation')
            self._version(); manifest=verify_release(Path(release_dir),self.s)
            config=(package/'nginx.conf').read_bytes(); dropin=(package/'dropin.conf').read_bytes()
            if hashlib.sha256(config).hexdigest()!=plan['metadata']['config_sha256']: raise ValueError('staging digest mismatch')
            # Prepare pool/helpers before journal: append-only objects never alter
            # live service and can safely survive a failed migration.
            update_asset_pool(Path(release_dir)/'dist',self.root/'pool',uid=self.uid,fault=self.fault)
            helper=Path(plan['helper_dir']); self._install_helpers(package,helper)
            self._assert_static(self._capture(),plan['origin'])
            if self._managed()!=plan['before']: raise ValueError('CAS managed drift before journal')
            before_dir=plan['before'][str(self.dropdir)]
            created_dir={'directory':True,'uid':self.uid,'gid':os.getgid(),'mode':0o755,'entries':[]}
            after_dir={**(created_dir if before_dir.get('absent') else before_dir),
                       'entries':sorted([*before_dir.get('entries',[]),self.drop.name])}
            after={**plan['before'],str(self.dropdir):after_dir,str(self.config):file_state(config,uid=self.uid),str(self.drop):file_state(dropin,uid=self.uid),
                   str(self.current):{'symlink':'releases/'+manifest['source_sha'],'uid':self.uid,'gid':os.getgid(),'mode':0o777}}
            # Creating the drop directory cannot claim its future drop-in as
            # an entry.  The exact prefix changes that directory only when
            # the drop file itself is installed.
            mutation_after={str(self.dropdir):(created_dir if before_dir.get('absent') else before_dir),str(self.config):file_state(config,uid=self.uid),str(self.drop):file_state(dropin,uid=self.uid),
                            str(self.current):{'symlink':'releases/'+manifest['source_sha'],'uid':self.uid,'gid':os.getgid(),'mode':0o777}}
            mutation_order=list(mutation_after)
            prefixes=[dict(plan['before'])]
            for path in mutation_order:
                next_prefix={**prefixes[-1],path:mutation_after[path]}
                if path==str(self.drop): next_prefix[str(self.dropdir)]=after_dir
                prefixes.append(next_prefix)
            if prefixes[-1]!=after: raise ValueError('internal managed-prefix construction differs')
            receipt={**plan,'manifest':manifest,'operation':'apply','after':after,'switch_intent':False,
                     'mutation_order':mutation_order,'managed_prefixes':prefixes,'state':ORIGIN}
            try:
                self._journal(receipt,'prepared',ORIGIN)
                for index,(path,state) in enumerate(mutation_after.items()):
                    self._journal(receipt,'prepared','INSTALLING_'+str(index))
                    install_state(path,state); self.fault('installed_'+Path(path).name)
                    self._journal(receipt,'prepared','INSTALLING_'+str(index+1))
                self._journal(receipt,'files_installed',FILES_INSTALLED)
                self._known(receipt); self._journal(receipt,'files_installed',RELOAD_INTENT); self.manager.reload(); self.fault('after_reload')
                loaded=self._capture(http=False)
                if not self._nginx_effective(loaded['properties'],receipt): raise ValueError('candidate effective invocation mismatch')
                receipt['applied_effective']={key:loaded['properties'][key] for key in STATIC}
                self._journal(receipt,'daemon_reloaded',READY_TO_SWITCH)
                self._known(receipt)
                if plan['origin']['properties']['ActiveState']=='active':
                    receipt['switch_intent']=True; self._journal(receipt,'daemon_reloaded',SWITCH_INTENT)
                    self.fault('switch_intent_persisted')
                    self._ready_to_switch(receipt)
                    self.manager.restart()
                    self._journal(receipt,'daemon_reloaded',CANDIDATE_UNWITNESSED)
                    self.fault('after_restart_before_candidate_capture')
                    observed=self._candidate_signal_witness(receipt,self._candidate_capture())
                    receipt['candidate_identity']=observed['process']
                    self.fault('after_candidate_capture_before_journal')
                    # Persist the authority-to-stop witness before HTTP/API
                    # postchecks, which can fail for unrelated served bytes.
                    self._journal(receipt,'candidate_identified',CANDIDATE_WITNESSED)
                    self.fault('candidate_identity_persisted')
                    candidate=self._verify_applied(receipt)
                    if not same_process(candidate['process'],observed['process']):
                        raise ValueError('candidate changed during complete verification')
                self.fault('after_switch'); self._journal(receipt,'service_switched',CANDIDATE_WITNESSED)
                self._verify_applied(receipt)
                self._journal(receipt,'verified',VERIFIED)
                # last first: recovery still uses the incomplete journal if a
                # cut falls between last publication and journal committed.
                committed={**receipt,'phase':'committed','state':COMMITTED}; write_json(last,committed)
                self.fault('after_last'); self._journal(receipt,'committed',COMMITTED)
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
        mkdir(self.lock_directory,0o700,self.uid)
        with locked(self.lock_path,self.uid):
            self._recover()
            if not (self.state/'last.json').exists(): raise ValueError('no committed migration to roll back')
            receipt=read_json(self.state/'last.json',self.uid)
            self._bind_receipt(receipt)
            if receipt['phase']=='rolled_back':
                self._verify_original(receipt,restarted=not receipt.get('restore_without_signal',False)); return {'rolled_back':True,'idempotent':True}
            if receipt['phase']!='committed': raise ValueError('no committed migration to roll back')
            self._verify_applied(receipt)
            receipt={**receipt,'operation':'rollback','switch_intent':False}
            try:
                self._journal(receipt,'prepared',COMMITTED)
                self._restore(receipt); self._journal(receipt,'files_installed',RESTORED_WITNESSED)
                self._journal(receipt,'daemon_reloaded',RESTORED_WITNESSED); self._journal(receipt,'service_switched',RESTORED_WITNESSED)
                self._verify_original(receipt,restarted=not receipt.get('restore_without_signal',False)); self._journal(receipt,'verified',RESTORED_WITNESSED)
                self._journal(receipt,'rolled_back',ROLLED_BACK); write_json(self.state/'last.json',receipt)
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
    parser.add_argument('--origin-contract',type=Path)
    parser.add_argument('--activation-receipt',type=Path)
    args=parser.parse_args()
    try:
        check_controlled_directory(args.settings.absolute().parent,os.getuid() if args.user else 0)
        check_object(checked_path(args.settings),os.getuid() if args.user else 0)
        settings=json.loads(args.settings.read_text())
        controller=Controller(settings,user=args.user,origin_contract=args.origin_contract,activation_receipt=args.activation_receipt)
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
