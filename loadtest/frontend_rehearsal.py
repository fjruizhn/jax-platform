"""P100 private rehearsal coordinator.

This is deliberately an orchestration boundary: it invokes the published
frontend_release CLI and the existing browser/k6 harnesses.  It does not
reimplement release switching.  ``--plan`` is side-effect free; ``--execute``
is deliberately gated by the formal B approval SHA supplied in the private
configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

try:  # Support both ``python loadtest/...`` and package test discovery.
    from .frontend_fixture import FixtureServer
    from .frontend_metrics import GRACEFUL_STOP_SECONDS, MEASURED_SECONDS, WARMUP_SECONDS, build_matrix, summarize
except ImportError:  # pragma: no cover - exercised by direct CLI use.
    from frontend_fixture import FixtureServer
    from frontend_metrics import GRACEFUL_STOP_SECONDS, MEASURED_SECONDS, WARMUP_SECONDS, build_matrix, summarize

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
FORBIDDEN_PREFIXES = (Path('/srv'), Path('/etc'))
FORBIDDEN_PORTS = {8080, 5173}


def refuse(condition, message):
    if condition:
        raise ValueError(message)


def private_path(value, label, *, directory=False):
    supplied = Path(value)
    refuse(supplied.is_symlink(), f'{label} must not be a symlink')
    path = supplied.resolve(strict=True)
    refuse(any(path == root or root in path.parents for root in FORBIDDEN_PREFIXES), f'{label} targets a production path')
    if directory:
        refuse(not path.is_dir(), f'{label} must be an existing non-symlink directory')
    else:
        refuse(not path.is_file(), f'{label} must be an existing regular file')
    return path


def private_port(value, label):
    refuse(type(value) is not int or value < 1024 or value > 65535 or value in FORBIDDEN_PORTS, f'{label} must be an explicit private port')
    return value


def load_plan(raw):
    """Validate the explicit, private invocation contract before any process starts."""
    refuse(not isinstance(raw, dict), 'runner configuration must be an object')
    for key in ('phase_b_approved_sha', 'audited_checkout_sha', 'source_sha'):
        refuse(not isinstance(raw.get(key), str) or not SHA40.fullmatch(raw[key]), f'invalid {key}')
    refuse(len({raw['phase_b_approved_sha'], raw['audited_checkout_sha'], raw['source_sha']}) != 1,
           'formal B approval, audited checkout and release source SHA differ')
    for key in ('controller', 'settings', 'release_dir', 'staging_root', 'systemd_dir',
                'artifact_dir', 'source_checkout', 'k6_binary', 'browser_script',
                'browser_module', 'chromium_binary'):
        raw[key] = str(private_path(raw.get(key, ''), key, directory=key in {
            'release_dir', 'staging_root', 'systemd_dir', 'artifact_dir', 'source_checkout', 'browser_module'}))
    checkout = Path(raw['source_checkout'])
    expected_code = {'controller': checkout / 'ops' / 'frontend' / 'frontend_release.py',
                     'browser_script': checkout / 'loadtest' / 'frontend_browser.mjs'}
    for key, expected in expected_code.items():
        refuse(Path(raw[key]) != expected, f'{key} must come from the audited checkout')
    refuse(any(Path(raw['artifact_dir']).iterdir()), 'artifact_dir must be empty for a new rehearsal')
    release = Path(raw['release_dir'])
    refuse(release.name != raw['source_sha'], 'release directory must be named by its exact source SHA')
    refuse(not isinstance(raw.get('service'), str) or not re.fullmatch(r'p100-[A-Za-z0-9_-]+\.service', raw['service']),
           'private p100 service required')
    raw['listen_port'] = private_port(raw.get('listen_port'), 'listen_port')
    settings = json.loads(Path(raw['settings']).read_text())
    refuse(settings.get('listen_port') != raw['listen_port'], 'private settings listen_port differs from rehearsal port')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '')).resolve(strict=True)
    expected_systemd = runtime / 'systemd' / 'user'
    refuse(Path(raw['systemd_dir']) != expected_systemd or expected_systemd.stat().st_uid != os.getuid(),
           'systemd_dir must be the canonical current-user manager directory')
    required_settings = {'private_mode': True, 'owner_uid': os.getuid(), 'service': raw['service'],
                         'systemd_dir': str(expected_systemd), 'user_runtime_dir': str(runtime)}
    if any(settings.get(key) != value for key, value in required_settings.items()):
        raise ValueError('settings do not describe this private user rehearsal')
    for key in ('release_root', 'nginx_root'):
        private_path(settings.get(key, ''), 'settings.' + key, directory=True)
    manifest = release / 'manifest.json'
    refuse(not manifest.is_file() or manifest.is_symlink(), 'release manifest missing')
    try:
        release_identity = json.loads(manifest.read_text()).get('source_sha')
    except json.JSONDecodeError as error:
        raise ValueError('release manifest is unreadable') from error
    refuse(release_identity != raw['source_sha'], 'release manifest source SHA differs')
    for key in ('node_binary', 'vite_module'):
        raw[key] = str(private_path(raw.get(key, ''), key))
    expected_vite = Path(raw['source_checkout']) / 'frontend' / 'node_modules' / 'vite' / 'dist' / 'node' / 'index.js'
    refuse(Path(raw['vite_module']) != expected_vite.resolve(strict=True), 'vite_module must be the exact source checkout module')
    fixture = raw.get('fixture')
    refuse(not isinstance(fixture, dict), 'explicit synthetic fixture required')
    for key in ('email', 'password', 'user', 'project', 'appearance', 'state', 'max_body_bytes', 'event_limit'):
        refuse(key not in fixture, f'fixture missing {key}')
    invalid_hosts = raw.get('invalid_host_statuses')
    if not isinstance(invalid_hosts, dict) or set(invalid_hosts) != {'vite', 'nginx'} or any(
            type(status) is not int or status < 400 or status > 499 for status in invalid_hosts.values()):
        raise ValueError('explicit per-variant invalid-host denial statuses required')
    # The Vite base unit is generated by this runner but is never permitted to
    # point at the historical production development ports.
    unit = Path(raw['systemd_dir']) / raw['service']
    refuse(unit.exists(), 'private base unit already exists; refuse to overwrite')
    return raw


def command_for_controller(plan, action, fixture_url=None, staging=None, settings=None):
    command = [sys.executable, plan['controller'], action, '--settings', str(settings or plan['settings']), '--user']
    if action == 'rollback':
        return command
    stage = Path(staging or plan['staging_root']) / (action + '-' + os.urandom(8).hex())
    command += ['--release-dir', plan['release_dir'], '--staging-dir', str(stage)]
    if action in ('prepare', 'apply'):
        refuse(not fixture_url, 'apply requires the owned fixture URL')
        command += ['--override-backend', fixture_url]
    return command


def matrix_plan(plan):
    rows = build_matrix(['vite', 'nginx'], MEASURED_SECONDS, WARMUP_SECONDS, GRACEFUL_STOP_SECONDS)
    return {'schema': 'p100-rehearsal-plan-v1', 'source_sha': plan['source_sha'],
            'phase_b_approved_sha': plan['phase_b_approved_sha'], 'audited_checkout_sha': plan['audited_checkout_sha'], 'service': plan['service'],
            'rows': rows, 'controller': {'prepare_requires_fixture': True,
                                          'apply_requires_fixture': True,
                                          'rollback': command_for_controller(plan, 'rollback')}}


def _file_identity(path):
    info = path.stat()
    return {'device': info.st_dev, 'inode': info.st_ino, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _write_all(descriptor, data):
    offset = 0
    while offset < len(data):
        count = os.write(descriptor, data[offset:])
        if count <= 0:
            raise OSError('private rehearsal file write made no progress')
        offset += count


def write_unit(plan, entry_path, fixture_url):
    """Install only a private Vite base unit; release CLI owns its drop-in."""
    unit = Path(plan['systemd_dir']) / plan['service']
    workdir = Path(plan['source_checkout']) / 'frontend'
    lines = ['[Unit]', 'Description=P100 private Vite rehearsal', '', '[Service]', 'Type=simple',
             f'WorkingDirectory={workdir}', f'Environment=P100_FIXTURE_URL={fixture_url}',
             'ExecStart=' + ' '.join(_systemd_quote(item) for item in [plan['node_binary'], str(entry_path)]),
             'Restart=no', '', '[Install]', 'WantedBy=default.target', '']
    data = '\n'.join(lines).encode()
    descriptor = os.open(unit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        _write_all(descriptor, data); os.fsync(descriptor)
    except BaseException:
        unit.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return unit, _file_identity(unit)


def _systemd_quote(value):
    # systemd's ExecStart parser is not a shell; reject rather than attempting
    # to quote arbitrary command fragments.
    if not re.fullmatch(r'[A-Za-z0-9_./:=+,@%-]+', value):
        raise ValueError('Vite argv contains unsupported systemd characters')
    return value


def write_private_vite_entry(plan, fixture_url, directory):
    """Derive a private createServer entry from the exact source config."""
    source = Path(plan['source_checkout']) / 'frontend' / 'vite.config.js'
    refuse(not source.is_file() or source.is_symlink(), 'exact source Vite config missing')
    target = fixture_url.replace('http://', 'ws://', 1)
    entry = Path(directory) / 'vite.mjs'
    source_url = source.resolve().as_uri()
    vite_url = Path(plan['vite_module']).resolve().as_uri()
    code = f'''import {{createServer}} from {vite_url!r};
import sourceConfig from {source_url!r};
const fixture = process.env.P100_FIXTURE_URL;
if (fixture !== {fixture_url!r}) throw new Error('private fixture environment differs');
const sourceServer = sourceConfig.server || {{}};
const sourceProxy = sourceServer.proxy || {{}};
if (!sourceProxy['/api'] || !sourceProxy['/ws'] || !Array.isArray(sourceServer.allowedHosts)) throw new Error('source proxy/host contract missing');
const server = await createServer({{...sourceConfig, configFile: false, root: {str((Path(plan['source_checkout']) / 'frontend').resolve())!r}, server: {{...sourceServer, host: '127.0.0.1', port: {plan['listen_port']}, strictPort: true,
  allowedHosts: [...sourceServer.allowedHosts], proxy: {{...sourceProxy,
    '/api': {{...sourceProxy['/api'], target: {fixture_url!r}, changeOrigin: true}},
    '/ws': {{...sourceProxy['/ws'], target: {target!r}, ws: true, changeOrigin: true}}
  }}
}}}});
await server.listen();
'''
    descriptor = os.open(entry, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        _write_all(descriptor, code.encode('utf8')); os.fsync(descriptor)
    except BaseException:
        entry.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return entry


def write_private_settings(plan, entry_path, directory):
    """Copy only explicit private settings and bind the exact canary identity."""
    settings = json.loads(Path(plan['settings']).read_text())
    node, vite = Path(plan['node_binary']), Path(plan['vite_module'])
    settings['original_contract'] = {'kind': 'private-vite-canary', 'node_binary': str(node),
                                     'entry': str(entry_path), 'entry_sha256': hashlib.sha256(entry_path.read_bytes()).hexdigest(),
                                     'vite_module': str(vite), 'vite_sha256': hashlib.sha256(vite.read_bytes()).hexdigest()}
    output = Path(directory) / 'controller-settings.json'
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        data = json.dumps(settings, sort_keys=True).encode(); _write_all(descriptor, data); os.fsync(descriptor)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return output


def verify_audited_head(plan):
    result = run_checked(['git', '-C', plan['source_checkout'], 'rev-parse', 'HEAD'])
    if result.stdout.strip() != plan['audited_checkout_sha']:
        raise ValueError('checkout HEAD is not the exact audited B SHA')
    dirty = run_checked(['git', '-C', plan['source_checkout'], 'status', '--porcelain', '--untracked-files=all']).stdout
    refuse(bool(dirty), 'audited source checkout has tracked or untracked edits')


def run_checked(command, *, allowed=(0,), cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180)
    if result.returncode not in allowed:
        raise RuntimeError('command refused/failed: ' + Path(command[0]).name + ' rc=' + str(result.returncode))
    return result


def browser_config(plan, variant, port, fixture):
    return {'playwright_module': plan['browser_module'], 'chromium_binary': plan['chromium_binary'],
            'base_url': f'http://axioma-ia.io:{port}', 'allowed_ports': [port],
            'output_dir': str(Path(plan['artifact_dir']) / variant),
            'fixture_credentials': {'email': fixture['email'], 'password': fixture['password']},
            'marker': fixture['project']['nombre'], 'project_role_labels': {'es': 'Dueño', 'en': 'Owner'},
            'variant': variant, 'source_sha': plan['source_sha'], 'harness_sha': harness_sha(plan),
            'timeout_ms': 15000}


def harness_sha(plan):
    paths = [Path(plan['browser_script']), Path(__file__).with_name('frontend_http.js'),
             Path(__file__).with_name('frontend_http_flow.mjs'), Path(__file__).with_name('frontend_http_result.mjs')]
    # Both browser and k6 contracts deliberately use Git-SHA1-shaped,
    # 40-character provenance values.  This is a content digest of the exact
    # harness inputs, not a claim that they are a Git commit.
    digest = hashlib.sha1()
    for path in paths:
        digest.update(path.name.encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def k6_row(plan, row, browser_result, fixture):
    variant = row['variant']
    resources = Path(plan['artifact_dir']) / variant / 'resources.json'
    summary = Path(plan['artifact_dir']) / 'k6' / f"{variant}-{row['vus']}-{row['repetition']}.json"
    summary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = {**row, 'synthetic_counter_canary': False, 'source_sha': plan['source_sha'],
              'harness_sha': harness_sha(plan), 'resources_file': str(resources), 'summary_file': str(summary),
              'base_url': browser_result['base_url'], 'allowed_ports': [browser_result['port']],
              'fixture_credentials': {'email': fixture['email'], 'password': fixture['password']},
              'marker': fixture['project']['nombre']}
    return config, summary


def _http_probe(port, method, path, *, host='axioma-ia.io', origin=None, body=None):
    """Persist only status/cache metadata, never a response body or headers wholesale."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        headers = {'Host': host}
        if origin is not None:
            headers['Origin'] = origin
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        return {'method': method, 'path': path, 'status': response.status,
                'cache_control': response.getheader('Cache-Control', '')}
    finally:
        connection.close()


def run_probes(port, variant, fixture, invalid_host_status):
    """Exercise observable HTTP boundary contracts without recording credentials."""
    origin = f'http://axioma-ia.io:{port}'
    before = len(fixture.observations())
    results = [_http_probe(port, 'GET', '/api/p100-not-found?via=ui', origin=origin),
               _http_probe(port, 'POST', '/api/auth/login?via=ui', origin=origin,
                           body=b'x' * (fixture.config['max_body_bytes'] + 1)),
               _http_probe(port, 'GET', '/assets/p100-missing.js'),
               _http_probe(port, 'GET', '/api/apariencia', host='invalid.p100.example', origin=origin)]
    expected = [404, 413, 404, invalid_host_status]
    if [item['status'] for item in results] != expected:
        raise RuntimeError('HTTP 404/host/body contract failed')
    observed = fixture.observations()[before:]
    expected_host = 'localhost:' + str(fixture.server_address[1])
    expected_observations = [('/api/p100-not-found?via=ui', 'GET'), ('/api/auth/login?via=ui', 'POST')]
    if len(observed) != len(expected_observations) or any(
            item.get('target') != path or item.get('method') != method or item.get('origin') != origin or
            item.get('host') != expected_host for item, (path, method) in zip(observed, expected_observations)):
        raise RuntimeError('UI proxy did not preserve API path/query/Host/Origin contract')
    index = _http_probe(port, 'GET', '/index.html')
    results.append(index)
    if index['status'] != 200 or 'no-cache' not in index['cache_control'].lower():
        raise RuntimeError('index cache contract failed')
    if variant == 'nginx':
        # Browser discovery has already validated actual asset bytes.  Nginx's
        # immutable response is checked from its first discovered resource.
        resources = json.loads((Path(fixture.config['_artifact_dir']) / variant / 'resources.json').read_text())['resources']
        asset_path = next((item['path'] for item in resources if item['path'].startswith('/assets/')), None)
        if asset_path is None:
            raise RuntimeError('browser discovery found no immutable release asset')
        asset = _http_probe(port, 'GET', asset_path)
        results.append(asset)
        if asset['status'] != 200 or 'immutable' not in asset['cache_control'].lower():
            raise RuntimeError('hashed asset cache contract failed')
    return results


def execute(plan):
    """Run the private Vite -> nginx -> Vite rehearsal after formal B approval."""
    verify_audited_head(plan)
    artifacts = Path(plan['artifact_dir']); artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    fixture_config = dict(plan['fixture'])
    fixture_config['allowed_origins'] = [f"http://axioma-ia.io:{plan['listen_port']}"]
    fixture_config['_artifact_dir'] = plan['artifact_dir']
    fixture = FixtureServer(('127.0.0.1', 0), fixture_config).start()
    fixture_url = fixture.base_url.replace('127.0.0.1', 'localhost')
    unit = identity = entry = private_settings = None
    raw_runs = []
    primary = None
    cleanup_errors = []
    apply_attempted = False
    try:
        entry = write_private_vite_entry(plan, fixture_url, artifacts)
        private_settings = write_private_settings(plan, entry, artifacts)
        unit, identity = write_unit(plan, entry, fixture_url)
        run_checked(['systemctl', '--user', 'daemon-reload'])
        run_checked(['systemctl', '--user', 'start', plan['service']])
        browser_runs = {}
        # Both implementations use the same port.  The controller replaces
        # Vite only after every Vite browser/probe/k6 row is durable.
        for variant, port in (('vite', plan['listen_port']),):
            config = browser_config(plan, variant, port, fixture_config)
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
                json.dump(config, handle); config_path = Path(handle.name)
            try:
                config_path.chmod(0o600)
                run_checked(['node', plan['browser_script'], '--config', str(config_path)])
            finally:
                config_path.unlink(missing_ok=True)
            browser_runs[variant] = {'base_url': config['base_url'], 'port': port}
            (artifacts / 'probes-vite.json').write_text(json.dumps(run_probes(port, variant, fixture, plan['invalid_host_statuses'][variant]), indent=2) + '\n', encoding='utf8')
        empty_k6 = artifacts / 'k6-empty-config.json'
        empty_k6.write_text('{}\n', encoding='utf8'); empty_k6.chmod(0o600)
        for row in (row for row in matrix_plan(plan)['rows'] if row['variant'] == 'vite'):
            config, summary = k6_row(plan, row, browser_runs['vite'], fixture_config)
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
                json.dump(config, handle); run_input = Path(handle.name)
            try:
                result = run_checked([plan['k6_binary'], 'run', '--config', str(empty_k6), '--include-system-env-vars=false',
                                      '--no-usage-report', '--new-machine-readable-summary=false', '--env',
                                      'P100_RUN_CONFIG=' + str(run_input), str(Path(__file__).with_name('frontend_http.js'))], allowed=(0, 99))
                (summary.with_suffix('.k6.txt')).write_text(result.stdout + result.stderr, encoding='utf8')
                raw_runs.append(json.loads(summary.read_text()))
            finally:
                run_input.unlink(missing_ok=True)
        # B's apply deliberately prepares a fresh package itself, but this
        # explicit prepare validates the accredited Vite identity before any
        # release switch and leaves no active mutation.
        run_checked(command_for_controller(plan, 'prepare', fixture_url, settings=private_settings))
        apply_attempted = True
        run_checked(command_for_controller(plan, 'apply', fixture_url, settings=private_settings))
        config = browser_config(plan, 'nginx', plan['listen_port'], fixture_config)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
            json.dump(config, handle); config_path = Path(handle.name)
        try:
            config_path.chmod(0o600)
            run_checked(['node', plan['browser_script'], '--config', str(config_path)])
        finally:
            config_path.unlink(missing_ok=True)
        browser_runs['nginx'] = {'base_url': config['base_url'], 'port': plan['listen_port']}
        (artifacts / 'probes-nginx.json').write_text(json.dumps(run_probes(plan['listen_port'], 'nginx', fixture, plan['invalid_host_statuses']['nginx']), indent=2) + '\n', encoding='utf8')
        for row in (row for row in matrix_plan(plan)['rows'] if row['variant'] == 'nginx'):
            config, summary = k6_row(plan, row, browser_runs[row['variant']], fixture_config)
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
                json.dump(config, handle); run_input = Path(handle.name)
            try:
                result = run_checked([plan['k6_binary'], 'run', '--config', str(empty_k6), '--include-system-env-vars=false',
                                      '--no-usage-report', '--new-machine-readable-summary=false', '--env',
                                      'P100_RUN_CONFIG=' + str(run_input), str(Path(__file__).with_name('frontend_http.js'))], allowed=(0, 99))
                (summary.with_suffix('.k6.txt')).write_text(result.stdout + result.stderr, encoding='utf8')
                raw_runs.append(json.loads(summary.read_text()))
            finally:
                run_input.unlink(missing_ok=True)
        report = summarize(raw_runs)
        report['fixture_events'] = fixture.events()
        (artifacts / 'summary.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf8')
        return report
    except BaseException as error:
        primary = error
        raise
    finally:
        # Refuse to stop/remove a unit if another session changed its identity.
        # Record cleanup failures without masking the operation's primary error.
        if unit is not None:
            owned = unit.exists() and _file_identity(unit) == identity
            if not owned:
                cleanup_errors.append('private base unit identity drifted; left untouched')
            else:
                restored = not apply_attempted
                if apply_attempted:
                    try:
                        run_checked(command_for_controller(plan, 'rollback', settings=private_settings))
                        restored = True
                    except Exception as error:  # fail-soft: preserve the canary unit and recovery inputs; record failure alongside the primary error
                        cleanup_errors.append('rollback: ' + type(error).__name__)
                if restored:
                    try:
                        run_checked(['systemctl', '--user', 'stop', plan['service']])
                        unit.unlink()
                        run_checked(['systemctl', '--user', 'daemon-reload'])
                    except Exception as error:  # fail-soft: preserve recovery inputs and record incomplete cleanup without masking the primary error
                        cleanup_errors.append('unit cleanup: ' + type(error).__name__)
        # A failed rollback or uncertain base-unit identity needs the canary
        # settings and entry intact for a later, evidence-based recovery.
        if not cleanup_errors:
            for path in (private_settings, entry):
                if path is not None:
                    path.unlink(missing_ok=True)
        fixture.close()
        cleanup = {'primary_error': None if primary is None else type(primary).__name__, 'cleanup_errors': cleanup_errors}
        artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
        (artifacts / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n', encoding='utf8')
        if cleanup_errors and primary is None:
            raise RuntimeError('rehearsal cleanup incomplete: ' + '; '.join(cleanup_errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--phase-b-approved-sha')
    args = parser.parse_args()
    if args.plan == args.execute:
        parser.error('choose exactly one of --plan or --execute')
    plan = load_plan(json.loads(args.config.read_text()))
    if args.plan:
        print(json.dumps(matrix_plan(plan), indent=2)); return 0
    if args.phase_b_approved_sha != plan['phase_b_approved_sha']:
        parser.error('--execute requires the exact formally approved B SHA from the private config')
    execute(plan)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
