#!/usr/bin/env python3
"""Render validated releases to explicit staging files; never apply a service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import subprocess
from urllib.parse import urlsplit

from release_manifest import checked_path, valid_host, valid_port, validate_manifest, verify_release


def absolute_path(value: object) -> str:
    if not isinstance(value, str) or not value.startswith('/') or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
        raise ValueError('unsafe absolute nginx path')
    if any(part in ('.', '..', '') for part in value.split('/')[1:]):
        raise ValueError('non-canonical nginx path')
    return str(checked_path(Path(value)))


def runtime_path(settings: dict) -> str:
    name = settings['runtime_directory']
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('RuntimeDirectory must be a name, not a path')
    if 'runtime_path' in settings:
        if not settings.get('private_mode') and settings['runtime_path'] != '/run/' + name:
            raise ValueError('system runtime path must correspond to RuntimeDirectory')
        return absolute_path(settings['runtime_path'])
    if settings.get('private_mode'):
        return absolute_path(settings['user_runtime_dir']) + '/' + name
    return '/run/' + name


def backend_url(value: str) -> dict:
    target = urlsplit(value)
    if target.scheme != 'http' or target.hostname not in ('localhost', '127.0.0.1', '::1') or target.path or target.query or target.fragment or target.username or target.password:
        raise ValueError('override must be an explicit loopback HTTP origin')
    return {'scheme': target.scheme, 'host': target.hostname, 'port': valid_port(target.port)}


def render(*, settings: dict, manifest: dict, override_backend: dict | None = None) -> tuple[str, dict]:
    """Public Phase A/B interface: deterministic config and override audit metadata."""
    validate_manifest(manifest, settings)
    contract = manifest['proxy_contract']
    original = {key: contract[key] for key in ('scheme', 'host', 'port')}
    backend = original if override_backend is None else override_backend
    if override_backend is not None and not settings.get('private_mode'):
        raise ValueError('backend override is private-mode only')
    if set(backend) != {'scheme', 'host', 'port'} or backend['scheme'] != 'http' or backend['host'] not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('unsupported backend origin')
    port = valid_port(backend['port'])
    upstream_ip = '[::1]' if backend['host'] == '::1' else '127.0.0.1'
    target_host = '[::1]' if backend['host'] == '::1' else backend['host']
    listen_host = valid_host(settings['listen_host'])
    if listen_host not in ('0.0.0.0', '127.0.0.1', '::1', '::'):
        raise ValueError('unsupported listen address')
    listen_host = '[' + listen_host + ']' if ':' in listen_host else listen_host
    listen_port = valid_port(settings['listen_port'])
    runtime = runtime_path(settings)
    root = absolute_path(settings['release_root'])
    workers = settings['worker_connections']
    if type(workers) is not int or not 16 <= workers <= 65536:
        raise ValueError('invalid worker_connections')
    hosts = [r'localhost', r'(?:[a-z0-9-]+\.)+localhost']
    for host in contract['allowed_hosts']:
        host = valid_host(host, allowed=True)
        hosts.append(r'(?:[a-z0-9-]+\.)*' + re.escape(host[1:]) if host.startswith('.') else re.escape(host))
    octet = r'(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
    ipv4 = rf'{octet}(?:\.{octet}){{3}}'
    # All valid compressed/full IPv6 forms, including an embedded IPv4 suffix.
    hextet = '[0-9a-f]{1,4}'
    ipv6 = [rf'(?:{hextet}:){{7}}{hextet}', rf'(?:{hextet}:){{1,7}}:',
            rf':(?:(?::{hextet}){{1,7}}|:)', rf'(?:{hextet}:){{6}}{ipv4}',
            rf'::(?:{hextet}:){{0,5}}{ipv4}']
    for right in range(1, 7):
        ipv6.append(rf'(?:{hextet}:){{1,{7-right}}}(?::{hextet}){{1,{right}}}')
    for right in range(5):
        ipv6.append(rf'(?:{hextet}:){{1,{5-right}}}:(?:{hextet}:){{{right}}}{ipv4}')
    hosts.extend([ipv4, r'\[(?:' + '|'.join(ipv6) + r')\]'])
    host_pattern = '|'.join(hosts)
    processes = settings['worker_processes']
    timeout = settings['websocket_read_timeout_seconds']
    if type(processes) is not int or not 1 <= processes <= 64:
        raise ValueError('invalid worker_processes')
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('invalid websocket timeout')
    source = manifest['source_sha']
    static_headers = f'add_header Cache-Control "no-cache"; add_header X-JAX-Frontend-SHA "{source}";'
    proxy = f'''proxy_pass http://{upstream_ip}:{port};
      proxy_http_version 1.1;
      proxy_set_header Host {target_host}:{port};
      proxy_set_header Origin $http_origin;
      proxy_request_buffering off;
      proxy_buffering off;'''
    text = f'''worker_processes {processes};
pid {runtime}/nginx.pid;
error_log /dev/stderr notice;
events {{ worker_connections {workers}; }}
http {{
  types {{
    text/html html htm;
    text/css css;
    application/javascript js mjs;
    application/json json map;
    image/svg+xml svg;
    image/png png; image/jpeg jpg jpeg; image/gif gif;
    image/webp webp; image/x-icon ico; image/avif avif;
    font/woff woff; font/woff2 woff2; font/ttf ttf;
    text/plain txt; application/pdf pdf; application/wasm wasm;
  }}
  default_type application/octet-stream;
  access_log /dev/stdout;
  client_body_temp_path {runtime}/client-body;
  proxy_temp_path {runtime}/proxy;
  fastcgi_temp_path {runtime}/fastcgi;
  uwsgi_temp_path {runtime}/uwsgi;
  scgi_temp_path {runtime}/scgi;
  map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
  map $host $allowed_host {{
    default 0;
    "~^(?:{host_pattern})$" 1;
  }}
  map $uri $hashed_asset {{
    default 0;
    "~^/assets/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{{8,}}\\.[A-Za-z0-9]+$" 1;
  }}
  server {{
    listen {listen_host}:{listen_port};
    server_name _;
    if ($allowed_host = 0) {{ return 403; }}
    if ($uri ~ "(?:^|/)\\.|^/(?:src|node_modules)(?:/|$)|^/(?:package(?:-lock)?\\.json|vite\\.config\\.[^/]+)$") {{ return 404; }}
    root {root}/current/dist;
    disable_symlinks on from={root}/current/dist;
    client_max_body_size 0;
    location ^~ /api {{
      {proxy}
    }}
    location ^~ /ws {{
      {proxy}
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_read_timeout {timeout}s;
    }}
    location ^~ /assets/ {{
      if ($hashed_asset = 0) {{ return 404; }}
      root {root}/pool;
      disable_symlinks on from={root};
      try_files $uri =404;
      add_header Cache-Control "public, max-age=31536000, immutable";
      add_header X-JAX-Frontend-SHA "{source}";
    }}
    location ~* \\.[a-z0-9]+$ {{ try_files $uri =404; {static_headers} }}
    location = /index.html {{ try_files /index.html =404; {static_headers} }}
    location / {{ try_files $uri /index.html; {static_headers} }}
  }}
}}
'''
    metadata = {'source_sha': source, 'upstream_original': original,
                'override_backend': override_backend, 'runtime_directory': settings['runtime_directory'],
                'runtime_path': runtime, 'nginx_version_required': settings['nginx_version']}
    return text, metadata


def staging_write(path: Path, data: str) -> None:
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + os.urandom(8).hex())
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', required=True, type=Path)
    parser.add_argument('--settings', type=Path, default=Path(__file__).with_name('settings.json'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--metadata', required=True, type=Path)
    parser.add_argument('--private', action='store_true')
    parser.add_argument('--override-backend')
    args = parser.parse_args()
    try:
        settings = json.loads(args.settings.read_text())
        if args.private:
            settings.update(private_mode=True, owner_uid=os.getuid())
        if args.override_backend and not args.private:
            raise ValueError('--override-backend requires --private')
        manifest = verify_release(args.release_dir, settings)
        binary = absolute_path(settings['nginx_binary'])
        version = subprocess.run([binary, '-v'], capture_output=True, text=True, check=True)
        actual_version = re.search(r'nginx/([0-9.]+)', version.stderr + version.stdout)
        if not actual_version or actual_version[1] != settings['nginx_version']:
            raise ValueError('nginx version mismatch')
        override = backend_url(args.override_backend) if args.override_backend else None
        config, metadata = render(settings=settings, manifest=manifest, override_backend=override)
        outputs = [checked_path(args.output), checked_path(args.metadata)]
        release = checked_path(args.release_dir)
        if outputs[0] == outputs[1] or any(path.is_relative_to(release) for path in outputs):
            raise ValueError('render outputs must be distinct staging files outside the immutable release')
        staging_write(outputs[0], config)
        staging_write(outputs[1], json.dumps(metadata, sort_keys=True, indent=2) + '\n')
        print(json.dumps({'nginx_config': str(outputs[0]), 'render_metadata': str(outputs[1])}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print('nginx render refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
