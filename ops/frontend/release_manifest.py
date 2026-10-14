"""Shared fail-closed validation of immutable release bytes and identity.

DAC checks reject unsafe owners/modes, but do not protect against root/sudo.
Private callers must declare private_mode and owner_uid explicitly.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat

SHA40 = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
ASSET = re.compile(r'assets/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+')
HOST = re.compile(r'(?:\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?')


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def checked_path(path: Path) -> Path:
    """Reject symlinks in every existing ancestor without resolving them away."""
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError('symlink in managed path: ' + str(part))
    return path


def expected_owner(settings: dict) -> int:
    if settings.get('private_mode'):
        uid = settings.get('owner_uid')
        if type(uid) is not int or uid < 0:
            raise ValueError('private_mode requires explicit owner_uid')
        return uid
    if settings.get('owner_uid', 0) != 0:
        raise ValueError('system artifacts require root ownership')
    return 0


def check_object(path: Path, owner_uid: int | None = None) -> os.stat_result:
    info = path.lstat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError('non-regular release object: ' + str(path))
    if info.st_mode & 0o022:
        raise ValueError('group/world writable release object: ' + str(path))
    if owner_uid is not None and info.st_uid != owner_uid:
        raise ValueError('release owner mismatch: ' + str(path))
    return info


def inventory(root: Path, owner_uid: int | None = None) -> dict[str, str]:
    root = checked_path(root)
    if not stat.S_ISDIR(check_object(root, owner_uid).st_mode):
        raise ValueError('dist must be a directory')
    files = {}
    directories = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            info = check_object(path, owner_uid)
            if stat.S_ISDIR(info.st_mode):
                directories.add(path.relative_to(root).as_posix())
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith('assets/') and not ASSET.fullmatch(relative):
                raise ValueError('asset lacks Vite hash: ' + relative)
            files[relative] = digest(path)
    expected_directories = {str(parent) for name in files for parent in Path(name).parents if str(parent) != '.'}
    if directories != expected_directories:
        raise ValueError('unexpected empty directories in release inventory')
    return dict(sorted(files.items()))


def tree_digest(files: dict[str, str]) -> str:
    encoded = ''.join(f'{key} {value}\n' for key, value in sorted(files.items())).encode()
    return hashlib.sha256(encoded).hexdigest()


def valid_host(host: object, *, allowed: bool = False) -> str:
    if not isinstance(host, str):
        raise ValueError('host must be a string')
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        if not HOST.fullmatch(host) or (host.startswith('.') and not allowed):
            raise ValueError('unsafe host')
        return host.lower()


def valid_port(port: object) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid port')
    return port


def validate_contract(contract: dict) -> None:
    required = {'api_prefix', 'ws_prefix', 'scheme', 'ws_scheme', 'host', 'port', 'allowed_hosts'}
    if not isinstance(contract, dict) or set(contract) != required:
        raise ValueError('unsupported proxy contract schema')
    if contract['api_prefix'] != '/api' or contract['ws_prefix'] != '/ws':
        raise ValueError('unsupported proxy prefixes')
    if contract['scheme'] != 'http' or contract['ws_scheme'] != 'ws':
        raise ValueError('unsupported upstream schemes')
    if contract['host'] not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('upstream must be loopback')
    valid_port(contract['port'])
    hosts = contract['allowed_hosts']
    if not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts):
        raise ValueError('allowedHosts must be a list of hosts')
    for host in hosts:
        valid_host(host, allowed=True)


def validate_manifest(manifest: dict, settings: dict, expected_sha: str | None = None) -> None:
    required = {'schema_version', 'source_sha', 'node_version', 'npm_version', 'vite_version',
                'lockfile_sha256', 'proxy_contract', 'files', 'tree_digest'}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError('manifest schema incomplete or unknown fields')
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('unsupported manifest schema_version')
    source = manifest['source_sha']
    if not isinstance(source, str) or not SHA40.fullmatch(source) or (expected_sha and source != expected_sha):
        raise ValueError('release SHA identity mismatch')
    for key in ('node_version', 'npm_version', 'vite_version'):
        if manifest[key] != settings[key]:
            raise ValueError('runtime version mismatch: ' + key)
    for key in ('lockfile_sha256', 'tree_digest'):
        if not isinstance(manifest[key], str) or not SHA256.fullmatch(manifest[key]):
            raise ValueError('invalid digest: ' + key)
    files = manifest['files']
    if not isinstance(files, dict) or 'index.html' not in files:
        raise ValueError('release inventory requires index.html')
    for path, value in files.items():
        if not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', path):
            raise ValueError('unsafe inventory path')
        if path.startswith('/') or any(part in ('', '.', '..') for part in path.split('/')):
            raise ValueError('unsafe inventory path')
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ValueError('invalid inventory digest')
    validate_contract(manifest['proxy_contract'])


def verify_release(release_dir: Path, settings: dict, expected_sha: str | None = None) -> dict:
    release_dir = checked_path(release_dir)
    uid = expected_owner(settings)
    check_object(release_dir.parent, uid)
    if not stat.S_ISDIR(check_object(release_dir, uid).st_mode):
        raise ValueError('release must be a directory')
    if set(p.name for p in release_dir.iterdir()) != {'manifest.json', 'dist', 'package-lock.json'}:
        raise ValueError('unexpected or missing release files')
    for name in ('manifest.json', 'package-lock.json'):
        if not stat.S_ISREG(check_object(release_dir / name, uid).st_mode):
            raise ValueError('release metadata must be regular files')
    manifest = json.loads((release_dir / 'manifest.json').read_text())
    validate_manifest(manifest, settings, expected_sha)
    if release_dir.name != manifest['source_sha']:
        raise ValueError('release directory SHA identity mismatch')
    if digest(release_dir / 'package-lock.json') != manifest['lockfile_sha256']:
        raise ValueError('archived lockfile digest mismatch')
    files = inventory(release_dir / 'dist', uid)
    if files != manifest['files'] or tree_digest(files) != manifest['tree_digest']:
        raise ValueError('release inventory mismatch')
    return manifest
