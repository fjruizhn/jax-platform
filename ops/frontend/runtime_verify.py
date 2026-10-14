#!/usr/bin/env python3
"""Immutable deployed ExecStartPre helper; never imports the editable checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import sys
import re
import subprocess

# -I excludes user-site, PYTHONPATH and the script directory. Add only this
# immutable installed bundle, never cwd or the editable repository.
sys.path.insert(0,str(Path(__file__).absolute().parent))
from release_manifest import check_controlled_directory, check_object, verify_release
from nginx_config import render


def verify(settings_path,release_dir,config,metadata_path):
    settings=json.loads(settings_path.read_text())
    uid=settings['owner_uid']
    for path in (settings_path,config,metadata_path):
        check_controlled_directory(path.parent,uid); check_object(path,uid,public=True)
    result=subprocess.run([settings['nginx_binary'],'-v'],capture_output=True,text=True,check=True)
    version=re.search(r'nginx/([0-9.]+)',result.stdout+result.stderr)
    if not version or version[1]!=settings['nginx_version']: raise ValueError('runtime nginx version mismatch')
    manifest=verify_release(release_dir,settings)
    metadata=json.loads(metadata_path.read_text())
    expected,rendered=render(settings=settings,manifest=manifest,override_backend=metadata['override_backend'])
    if metadata!=rendered or config.read_bytes()!=expected.encode(): raise ValueError('config/metadata/release mismatch')
    if hashlib.sha256(config.read_bytes()).hexdigest()!=metadata['config_sha256']: raise ValueError('config digest mismatch')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('settings','release-dir','config','metadata'): parser.add_argument('--'+flag,required=True,type=Path)
    args=parser.parse_args()
    verify(args.settings,args.release_dir,args.config,args.metadata)

if __name__=='__main__': main()
