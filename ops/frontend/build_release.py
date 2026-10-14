#!/usr/bin/env python3
"""Create an immutable, two-build-verified static frontend release."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from urllib.parse import urlparse
from pathlib import Path

NODE, NPM = "24.16.0", "11.13.0"
def run(command, cwd, env): return subprocess.run(command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.strip()
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory(root): return {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}
def build(repo, sha, work):
    archive = subprocess.run(["git", "archive", sha, "frontend"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(work)], input=archive, check=True); source = work / "frontend"
    if (source / ".env").exists() or any(source.glob(".env.*")): raise ValueError("env files are forbidden")
    if any(key.startswith("VITE_") for key in os.environ): raise ValueError("implicit VITE_ environment is forbidden")
    env = {"PATH": os.environ["PATH"], "HOME": str(work / "home"), "CI": "true"}; (work / "home").mkdir()
    if run(["node", "--version"], source, env) != "v" + NODE or run(["npm", "--version"], source, env) != NPM: raise ValueError("Node/npm version mismatch")
    run(["npm", "ci"], source, env)
    # Vite's loader (not a regex) is the authority for proxy configuration; Vite v6 build/server-options docs.
    probe = "import {loadConfigFromFile} from 'vite';const x=await loadConfigFromFile({command:'build',mode:'production'},undefined,process.cwd());console.log(JSON.stringify(x.config.server));"
    server = json.loads(run(["node", "--input-type=module", "-e", probe], source, env)); proxy = server.get("proxy", {})
    allowed={"target","changeOrigin","ws"}
    if set(proxy) != {"/api", "/ws"} or any(set(proxy[x])-allowed or proxy[x].get("rewrite") or not proxy[x].get("changeOrigin") for x in proxy) or not proxy["/ws"].get("ws"): raise ValueError("unsupported Vite proxy contract")
    api, ws = urlparse(proxy["/api"]["target"]), urlparse(proxy["/ws"]["target"])
    if not api.hostname or api.port is None or (ws.hostname,ws.port)!=(api.hostname,api.port): raise ValueError("Vite proxy targets disagree")
    run(["npm", "run", "build"], source, env)
    contract = {"api_prefix":"/api", "ws_prefix":"/ws", "target_host":api.hostname, "target_port":api.port, "allowed_hosts":["localhost","127.0.0.1","::1",*server.get("allowedHosts",[])]}
    return source / "dist", {"lockfile_sha256":digest(source / "package-lock.json"), "proxy_contract":contract}
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--repo",type=Path,required=True); p.add_argument("--sha",required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    try:
        if len(a.sha)!=40 or run(["git","rev-parse","HEAD"],a.repo,os.environ.copy())!=a.sha or run(["git","status","--porcelain"],a.repo,os.environ.copy()): raise ValueError("repo must be clean and HEAD must equal --sha")
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            d, meta=build(a.repo,a.sha,Path(one)); other,_=build(a.repo,a.sha,Path(two)); files=inventory(d)
            if files!=inventory(other): raise ValueError("two builds differ")
            manifest={"source_sha":a.sha,"node_version":NODE,"npm_version":NPM,**meta,"files":files,"tree_digest":hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()}; dest=a.output/a.sha
            if dest.exists():
                if (dest/"manifest.json").is_file() and json.loads((dest/"manifest.json").read_text())==manifest: print(json.dumps({"release":str(dest),"manifest":str(dest/"manifest.json")})); return 0
                raise ValueError("existing release differs")
            stage=a.output/("."+a.sha+".staging"); stage.mkdir(parents=True); shutil.copytree(d,stage/"dist"); (stage/"manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n"); os.replace(stage,dest); print(json.dumps({"release":str(dest),"manifest":str(dest/"manifest.json")}))
        return 0
    except (OSError,ValueError,subprocess.CalledProcessError) as error: print("build release refused: "+str(error),file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
