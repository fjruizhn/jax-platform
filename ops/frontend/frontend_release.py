#!/usr/bin/env python3
"""Transactional static frontend switcher. Defaults are never applied implicitly."""
from __future__ import annotations
import argparse, base64, fcntl, hashlib, json, os, shutil, stat, subprocess, sys
from pathlib import Path

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
    hosts=" ".join(proxy_contract["allowed_hosts"]); api=proxy_contract["api_prefix"]; ws=proxy_contract["ws_prefix"]; port=proxy_contract["target_port"]; host=str(proxy_contract["target_host"])+":"+str(port)
    # nginx.org/r/try_files and nginx.org/r/proxy_pass: no URI suffix preserves URI/query.
    return "\n".join(["worker_processes 1;",f"pid {runtime_directory}/nginx.pid;","error_log /dev/stderr notice;","events { worker_connections 128; }","http {"," include /etc/nginx/mime.types;"," default_type application/octet-stream;"," access_log /dev/stdout;"," map $http_upgrade $connection_upgrade { default upgrade; '' close; }"," server {",f"  listen {listen}; server_name {hosts}; root {release_root}/current/dist; client_max_body_size 0;",f"  location ^~ {api} {{ proxy_pass http://127.0.0.1:{port}; proxy_http_version 1.1; proxy_set_header Host {host}; proxy_set_header Origin $http_origin; proxy_request_buffering off; proxy_buffering off; }}",f"  location ^~ {ws} {{ proxy_pass http://127.0.0.1:{port}; proxy_http_version 1.1; proxy_set_header Host {host}; proxy_set_header Origin $http_origin; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade; proxy_read_timeout 3600s; proxy_request_buffering off; proxy_buffering off; }}",f"  location ^~ /assets/ {{ root {release_root}/pool; try_files $uri =404; add_header Cache-Control \"public, max-age=31536000, immutable\" always; add_header X-JAX-Frontend-SHA \"{release_sha}\" always; }}",f"  location = /index.html {{ try_files /index.html =404; add_header Cache-Control \"no-cache\" always; add_header X-JAX-Frontend-SHA \"{release_sha}\" always; }}","  location ~ /\\. { return 404; }","  location / { try_files $uri $uri/ /index.html; add_header Cache-Control \"no-cache\" always; add_header X-JAX-Frontend-SHA \""+release_sha+"\" always; }"," }","}",""])

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
            config=render_nginx(release_root=s["release_root"],release_sha=m["source_sha"],runtime_directory=s["runtime_directory"],proxy_contract=m["proxy_contract"],listen=int(s.get("listen",5173))); dropin="[Service]\nUser="+s.get("user","jaxsvc")+"\nGroup="+s.get("group","jaxsvc")+"\nWorkingDirectory="+s["release_root"]+"/current\nRuntimeDirectory="+s["runtime_directory"]+"\nExecStart=\nExecStartPre=\nExecStartPre=/usr/sbin/nginx -t -c "+str(nginx)+"\nExecStart=/usr/sbin/nginx -g 'daemon off;' -c "+str(nginx)+"\n"
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
