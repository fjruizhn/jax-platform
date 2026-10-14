#!/usr/bin/env python3
"""Transactional static frontend switcher. Defaults are never applied implicitly."""
from __future__ import annotations
import argparse, base64, fcntl, hashlib, json, os, shutil, stat, subprocess, sys
from pathlib import Path
from nginx_config import render as render_nginx_config

def atomic_write(path, value):
    if path.is_symlink(): raise ValueError("managed destination is symlink")
    path.parent.mkdir(parents=True, exist_ok=True); tmp=path.parent/("."+path.name+"."+os.urandom(8).hex())
    fd=os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0), 0o640)
    with os.fdopen(fd,"w") as f: f.write(value); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path); d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY); os.fsync(d); os.close(d)

def fingerprint(path):
    if not path.exists() and not path.is_symlink(): return {"absent":True}
    if path.is_symlink(): return {"symlink":os.readlink(path)}
    info=path.stat()
    if not stat.S_ISREG(info.st_mode): raise ValueError("managed object is not regular")
    return {"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"mode":stat.S_IMODE(info.st_mode),"uid":info.st_uid,"gid":info.st_gid,"bytes":base64.b64encode(path.read_bytes()).decode()}

def snapshot_managed(paths): return {str(p):fingerprint(p) for p in paths}
def restore_snapshot(snapshot):
    for raw,state in snapshot.items():
        p=Path(raw)
        if state.get("absent"):
            if p.exists() or p.is_symlink(): p.unlink()
        elif "symlink" in state:
            if p.exists() or p.is_symlink(): p.unlink()
            p.parent.mkdir(parents=True,exist_ok=True); p.symlink_to(state["symlink"])
        else:
            atomic_write(p,base64.b64decode(state["bytes"]).decode()); os.chmod(p,state["mode"])

def update_asset_pool(dist,pool):
    for source in (dist/"assets").rglob("*") if (dist/"assets").exists() else []:
        if not source.is_file(): continue
        target=pool/"assets"/source.relative_to(dist/"assets")
        if target.exists():
            if hashlib.sha256(source.read_bytes()).digest()!=hashlib.sha256(target.read_bytes()).digest(): raise ValueError("asset collision: "+str(source))
        else: target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)

def render_nginx(*,release_root,release_sha,runtime_directory,proxy_contract,listen=5173):
    """Compatibility adapter; all config text comes from the shared renderer.

    Legacy callers may pass an absolute runtime path. Phase B uses settings/manifest
    directly and must validate the associated on-disk release before calling render.
    """
    defaults=json.loads(Path(__file__).with_name("settings.json").read_text())
    settings={**defaults,"release_root":str(release_root),"listen_port":listen}
    if Path(runtime_directory).is_absolute():
        settings.update(private_mode=True,runtime_path=str(runtime_directory))
    else:
        settings["runtime_directory"]=runtime_directory
    contract={"api_prefix":proxy_contract["api_prefix"],"ws_prefix":proxy_contract["ws_prefix"],
              "scheme":proxy_contract.get("scheme","http"),"ws_scheme":proxy_contract.get("ws_scheme","ws"),
              "host":proxy_contract.get("host",proxy_contract.get("target_host")),
              "port":proxy_contract.get("port",proxy_contract.get("target_port")),
              "allowed_hosts":proxy_contract["allowed_hosts"]}
    empty=hashlib.sha256(b"").hexdigest()
    manifest={"schema_version":1,"source_sha":release_sha,
              **{key:settings[key] for key in ("node_version","npm_version","vite_version")},
              "lockfile_sha256":empty,"tree_digest":empty,"files":{"index.html":empty},
              "proxy_contract":contract}
    return render_nginx_config(settings=settings,manifest=manifest)[0]

def render_nginx_from_manifest(*, settings, manifest, override_backend=None):
    """Phase-A renderer entrypoint; no transaction or filesystem side effect."""
    return render_nginx_config(settings=settings, manifest=manifest, override_backend=override_backend)

def journal(path,phase,**extra): atomic_write(path,json.dumps({"phase":phase,**extra},sort_keys=True)+"\n")
def ctl(user,*args): subprocess.run(["systemctl",*(["--user"] if user else []),*args],check=True)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("action",choices=("render","prepare","apply","rollback")); p.add_argument("--settings",type=Path,default=Path(__file__).with_name("settings.json")); p.add_argument("--manifest",type=Path); p.add_argument("--user",action="store_true"); a=p.parse_args()
    try:
        s=json.loads(a.settings.read_text()); root=Path(s["nginx_root"]); root.mkdir(parents=True,exist_ok=True); state=root/"journal.json"
        with (root/".frontend-release.lock").open("w") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if state.exists() and json.loads(state.read_text()).get("phase")!="committed":
                old=json.loads(state.read_text()); restore_snapshot(old["snapshot"]); ctl(a.user,"daemon-reload"); ctl(a.user,"restart",s["service"]); raise RuntimeError("recovered incomplete transaction")
            if not a.manifest: p.error("--manifest required")
            m=json.loads(a.manifest.read_text()); nginx=root/"nginx.conf"; drop=Path(s.get("systemd_dir","/etc/systemd/system"))/(s["service"]+".d")/s["dropin_name"]; current=Path(s["release_root"])/"current"; snap=snapshot_managed([nginx,drop,current])
            if a.action=="rollback":
                if not state.exists(): raise ValueError("no durable transaction snapshot")
                previous=json.loads(state.read_text()).get("snapshot")
                if not previous: raise ValueError("journal has no rollback snapshot")
                restore_snapshot(previous); ctl(a.user,"daemon-reload"); ctl(a.user,"restart",s["service"]); journal(state,"committed",snapshot=previous); print(json.dumps({"rolled_back":True})); return 0
            config,render_metadata=render_nginx_from_manifest(settings=s,manifest=m); dropin="[Service]\nUser="+s.get("user","jaxsvc")+"\nGroup="+s.get("group","jaxsvc")+"\nWorkingDirectory="+s["release_root"]+"/current\nRuntimeDirectory="+s["runtime_directory"]+"\nExecStart=\nExecStartPre=\nExecStartPre=/usr/sbin/nginx -t -c "+str(nginx)+"\nExecStart=/usr/sbin/nginx -g 'daemon off;' -c "+str(nginx)+"\n"
            atomic_write(nginx,config); atomic_write(drop,dropin); journal(state,"prepared",snapshot=snap)
            if a.action in ("render","prepare"): print(json.dumps({"nginx_config":str(nginx),"dropin":str(drop)})); return 0
            release=Path(s["release_root"])/"releases"/m["source_sha"]
            if not (release/"dist").is_dir(): raise ValueError("release missing")
            update_asset_pool(release/"dist",Path(s["release_root"])/"pool")
            if current.exists() or current.is_symlink(): current.unlink()
            current.parent.mkdir(parents=True,exist_ok=True); current.symlink_to(release)
            journal(state,"files_installed",snapshot=snap); subprocess.run(["/usr/sbin/nginx","-t","-c",str(nginx)],check=True); ctl(a.user,"daemon-reload"); journal(state,"daemon_reloaded",snapshot=snap); ctl(a.user,"restart",s["service"]); journal(state,"service_switched",snapshot=snap); ctl(a.user,"is-active","--quiet",s["service"]); journal(state,"verified",snapshot=snap); journal(state,"committed",snapshot=snap); print(json.dumps({"applied":m["source_sha"]})); return 0
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as e: print("frontend release refused: "+str(e),file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
