#!/usr/bin/env python3
"""Build an exact clean Git SHA twice, verify bytes, and publish an immutable release."""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from release_manifest import (SHA40, check_object, checked_path, digest, expected_owner,
                              inventory, tree_digest, validate_contract, verify_release)


def run(command: list[str], cwd: Path, env: dict) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f'{command[0]} failed ({result.returncode}): {result.stderr[-4000:]}')
    return result.stdout.strip()


def check_source(repo: Path, sha: str) -> None:
    if not SHA40.fullmatch(sha):
        raise ValueError('--sha must be a lowercase SHA40')
    env = os.environ.copy()
    if run(['git', 'rev-parse', 'HEAD'], repo, env) != sha:
        raise ValueError('HEAD must equal --sha')
    if run(['git', 'status', '--porcelain', '--untracked-files=all'], repo, env):
        raise ValueError('repo must be clean')


def build_environment(work: Path) -> dict:
    home, temporary = work / 'home', work / 'tmp'
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    return {'PATH': os.environ['PATH'], 'HOME': str(home), 'TMPDIR': str(temporary),
            'TMP': str(temporary), 'TEMP': str(temporary), 'TZ': 'UTC', 'LC_ALL': 'C',
            'LANG': 'C', 'CI': 'true', 'npm_config_cache': str(work / 'npm-cache')}


def proxy_contract(source: Path, env: dict) -> dict:
    # Vite's config loader is authoritative. Functions cannot disappear via JSON serialization.
    probe = '''import {loadConfigFromFile} from 'vite';
const loaded=await loadConfigFromFile({command:'serve',mode:'development'},undefined,process.cwd());
if (!loaded) throw new Error('Vite config missing');
const server=loaded.config.server;
console.log(JSON.stringify(server, (key,value)=>{
 if (typeof value === 'function') throw new Error('unsupported server function: '+key);
 return value;
}));'''
    server = json.loads(run(['node', '--input-type=module', '-e', probe], source, env))
    proxy = server.get('proxy', {})
    if set(proxy) != {'/api', '/ws'}:
        raise ValueError('unsupported Vite proxy prefixes')
    expected = {'/api': {'target', 'changeOrigin'}, '/ws': {'target', 'changeOrigin', 'ws'}}
    for prefix, keys in expected.items():
        options = proxy[prefix]
        if not isinstance(options, dict) or set(options) != keys or options['changeOrigin'] is not True:
            raise ValueError('unsupported Vite proxy options')
    if proxy['/ws']['ws'] is not True:
        raise ValueError('WebSocket proxy must enable ws')
    api, ws = (urlsplit(proxy[prefix]['target']) for prefix in ('/api', '/ws'))
    for target in (api, ws):
        if target.path or target.query or target.fragment or target.username or target.password:
            raise ValueError('proxy target must be an origin')
    if (api.hostname, api.port) != (ws.hostname, ws.port):
        raise ValueError('Vite proxy targets disagree')
    hosts = server.get('allowedHosts', [])
    contract = {'api_prefix': '/api', 'ws_prefix': '/ws', 'scheme': api.scheme,
                'ws_scheme': ws.scheme, 'host': api.hostname, 'port': api.port,
                'allowed_hosts': hosts}
    validate_contract(contract)
    return contract


def build_once(repo: Path, sha: str, work: Path, settings: dict) -> tuple[Path, dict]:
    archive = subprocess.run(['git', 'archive', sha, 'frontend'], cwd=repo,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(work)], input=archive, check=True)
    source = work / 'frontend'
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('archived source symlinks are forbidden')
        if path.name == '.env' or path.name.startswith('.env.'):
            raise ValueError('archived env files are forbidden')
    env = build_environment(work)
    node = run(['node', '--version'], source, env).removeprefix('v')
    npm = run(['npm', '--version'], source, env)
    if node != settings['node_version'] or npm != settings['npm_version']:
        raise ValueError('Node/npm version mismatch')
    lockfile_digest = digest(source / 'package-lock.json')
    run(['npm', 'ci'], source, env)
    vite = run(['node', '-e', "console.log(require('./node_modules/vite/package.json').version)"], source, env)
    if vite != settings['vite_version']:
        raise ValueError('Vite version mismatch')
    contract = proxy_contract(source, env)
    run(['npm', 'run', 'build'], source, env)
    if digest(source / 'package-lock.json') != lockfile_digest:
        raise ValueError('build mutated archived lockfile')
    return source / 'dist', {'node_version': node, 'npm_version': npm, 'vite_version': vite,
                             'lockfile_sha256': lockfile_digest, 'proxy_contract': contract}


def sync_tree(root: Path) -> None:
    for directory, dirs, files in os.walk(root, topdown=False):
        for name in files:
            fd = os.open(Path(directory) / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish_noreplace(source: Path, destination: Path) -> None:
    """Linux atomic directory publication that cannot replace even an empty directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('renameat2 is required for no-replace publication')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def create_release(repo: Path, sha: str, output: Path, settings: dict) -> Path:
    check_source(repo, sha)
    if any(key.startswith('VITE_') for key in os.environ):
        raise ValueError('implicit VITE_ environment is forbidden')
    uid = expected_owner(settings)
    if uid != os.getuid():
        raise ValueError('build owner does not match artifact owner policy; use --private for a user rehearsal')
    output = checked_path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    check_object(output, uid)
    fd = os.open(output / '.build.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        destination = output / sha
        # Refuse corrupt reuse before doing a new build. Never overwrite an unknown release.
        if destination.exists() or destination.is_symlink():
            verify_release(destination, settings, sha)
        with tempfile.TemporaryDirectory(prefix='p100-build-one-') as one, tempfile.TemporaryDirectory(prefix='p100-build-two-') as two:
            dist, metadata = build_once(repo, sha, Path(one), settings)
            other_dist, other_metadata = build_once(repo, sha, Path(two), settings)
            files = inventory(dist, uid)
            if files != inventory(other_dist, uid) or metadata != other_metadata:
                raise ValueError('two builds differ in paths, bytes or proxy metadata')
            check_source(repo, sha)
            manifest = {'schema_version': 1, 'source_sha': sha, **metadata,
                        'files': files, 'tree_digest': tree_digest(files)}
            if destination.exists():
                if verify_release(destination, settings, sha) != manifest:
                    raise ValueError('existing release differs from rebuilt source')
                return destination
            with tempfile.TemporaryDirectory(prefix='.' + sha + '.staging-', dir=output) as staging:
                release = Path(staging) / sha
                release.mkdir(mode=0o755)
                shutil.copytree(dist, release / 'dist')
                shutil.copyfile(dist.parent / 'package-lock.json', release / 'package-lock.json')
                (release / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
                for path in [release, *release.rglob('*')]:
                    path.chmod(0o755 if path.is_dir() else 0o644)
                verify_release(release, settings, sha)
                sync_tree(release)
                check_source(repo, sha)
                if destination.exists() or destination.is_symlink():
                    raise ValueError('release destination appeared before publication')
                publish_noreplace(release, destination)
                directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return destination
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--settings', type=Path, default=Path(__file__).with_name('settings.json'))
    parser.add_argument('--private', action='store_true', help='Explicit rehearsal ownership by current UID')
    args = parser.parse_args()
    previous_umask = os.umask(0o022)
    try:
        settings = json.loads(args.settings.read_text())
        if args.private:
            settings.update(private_mode=True, owner_uid=os.getuid())
        release = create_release(args.repo, args.sha, args.output, settings)
        print(json.dumps({'release': str(release), 'manifest': str(release / 'manifest.json')}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print('build release refused: ' + str(error), file=sys.stderr)
        return 1
    finally:
        os.umask(previous_umask)


if __name__ == '__main__':
    raise SystemExit(main())
