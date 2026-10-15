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
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
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
    # Never normalize aliases by mutating the caller's parsed configuration.
    raw = dict(raw)
    identities = ('phase_b_controller_sha', 'harness_audited_sha', 'release_source_sha')
    if all(key in raw for key in identities):
        for key in identities:
            refuse(not isinstance(raw.get(key), str) or not SHA40.fullmatch(raw[key]), f'invalid {key}')
        for legacy, declared in (('phase_b_approved_sha', raw['phase_b_controller_sha']),
                                 ('audited_checkout_sha', raw['harness_audited_sha']),
                                 ('source_sha', raw['release_source_sha'])):
            refuse(legacy in raw and raw[legacy] != declared, f'legacy {legacy} contradicts explicit B/H/S identity')
        # Compatibility for existing private configs.  These aliases are
        # provenance labels only; execution uses the three explicit identities.
        raw['phase_b_approved_sha'] = raw['phase_b_controller_sha']
        raw['audited_checkout_sha'] = raw['harness_audited_sha']
        raw['source_sha'] = raw['release_source_sha']
        refuse(not isinstance(raw.get('git_repository'), str) or not raw['git_repository'], 'explicit git_repository required')
        raw['git_repository'] = str(private_path(raw['git_repository'], 'git_repository', directory=True))
    else:
        raise ValueError('explicit B/H/S identities and git_repository are required')
    for key in ('settings', 'release_dir', 'staging_root', 'systemd_dir',
                'artifact_dir', 'k6_binary', 'browser_module', 'chromium_binary'):
        raw[key] = str(private_path(raw.get(key, ''), key, directory=key in {
            'release_dir', 'staging_root', 'systemd_dir', 'artifact_dir', 'browser_module'}))
    refuse(any(Path(raw['artifact_dir']).iterdir()), 'artifact_dir must be empty for a new rehearsal')
    release = Path(raw['release_dir'])
    refuse(release.name != raw['source_sha'], 'release directory must be named by its exact source SHA')
    refuse(not isinstance(raw.get('service'), str) or not re.fullmatch(r'p100-[A-Za-z0-9_-]+\.service', raw['service']),
           'private p100 service required')
    raw['listen_port'] = private_port(raw.get('listen_port'), 'listen_port')
    settings_bytes = Path(raw['settings']).read_bytes()
    settings = json.loads(settings_bytes)
    raw['settings_sha256'] = hashlib.sha256(settings_bytes).hexdigest()
    refuse(settings.get('listen_port') != raw['listen_port'], 'private settings listen_port differs from rehearsal port')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '')).resolve(strict=True)
    expected_systemd = runtime / 'systemd' / 'user'
    refuse(Path(raw['systemd_dir']) != expected_systemd or expected_systemd.stat().st_uid != os.getuid(),
           'systemd_dir must be the canonical current-user manager directory')
    required_settings = {'private_mode': True, 'owner_uid': os.getuid(), 'service': raw['service'],
                         'systemd_dir': str(expected_systemd), 'user_runtime_dir': str(runtime),
                         'listen_host': '127.0.0.1', 'proof_connect_host': '127.0.0.1'}
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
    for key in ('node_binary',):
        raw[key] = str(private_path(raw.get(key, ''), key))
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


def validate_private_settings(plan, settings):
    refuse(settings.get('listen_port') != plan['listen_port'], 'private settings listen_port differs from rehearsal port')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '')).resolve(strict=True)
    required = {'private_mode': True, 'owner_uid': os.getuid(), 'service': plan['service'],
                'systemd_dir': str(runtime / 'systemd' / 'user'), 'user_runtime_dir': str(runtime),
                'listen_host': '127.0.0.1', 'proof_connect_host': '127.0.0.1'}
    if any(settings.get(key) != value for key, value in required.items()):
        raise ValueError('private settings critical properties differ')


def _git_archive(repository, sha, paths, destination):
    """Materialize only named Git-object bytes, never a mutable checkout."""
    verified = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--verify', sha + '^{commit}'],
                              capture_output=True, text=True)
    if verified.returncode or verified.stdout.strip() != sha:
        raise ValueError('Git object is not the declared commit: ' + sha)
    result = subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar', sha, '--', *paths],
                            capture_output=True)
    if result.returncode:
        raise RuntimeError('git archive failed for sealed input')
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode='r:') as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            if relative.is_absolute() or '..' in relative.parts or member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ValueError('unsafe Git archive member')
        archive.extractall(destination, filter='data')


def bootstrap_inputs(plan, artifact_dir):
    """Construct B/H/S execution roots from immutable Git objects before service start."""
    if 'git_repository' not in plan:
        raise ValueError('--execute requires git_repository and explicit B/H/S identities')
    root = Path(artifact_dir) / 'inputs'
    root.mkdir(mode=0o700)
    controller_root, harness_root, source_root = (root / name for name in ('controller', 'harness', 'source'))
    for directory in (controller_root, harness_root, source_root):
        directory.mkdir(mode=0o700)
    repository = plan['git_repository']
    _git_archive(repository, plan['phase_b_controller_sha'], ['ops/frontend'], controller_root)
    _git_archive(repository, plan['harness_audited_sha'], [
        'loadtest/frontend_rehearsal.py', 'loadtest/frontend_fixture.py', 'loadtest/frontend_metrics.py',
        'loadtest/frontend_browser.mjs', 'loadtest/frontend_http.js', 'loadtest/frontend_http_flow.mjs',
        'loadtest/frontend_http_result.mjs'], harness_root)
    _git_archive(repository, plan['release_source_sha'], ['frontend'], source_root)
    source_frontend = source_root / 'frontend'
    run_checked(['npm', 'ci', '--ignore-scripts'], cwd=source_frontend)
    settings_copy = root / 'settings.json'
    settings_bytes = Path(plan['settings']).read_bytes()
    if hashlib.sha256(settings_bytes).hexdigest() != plan['settings_sha256']:
        raise ValueError('validated settings changed before sealed copy')
    descriptor = os.open(settings_copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        _write_all(descriptor, settings_bytes)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    copied = dict(plan)
    copied.update({'controller': str(controller_root / 'ops/frontend/frontend_release.py'),
                   'browser_script': str(harness_root / 'loadtest/frontend_browser.mjs'),
                   'harness_dir': str(harness_root / 'loadtest'),
                   'source_checkout': str(source_root),
                   'settings': str(settings_copy),
                   'vite_module': str(source_frontend / 'node_modules/vite/dist/node/index.js')})
    validate_private_settings(copied, json.loads(settings_copy.read_text()))
    runtime_config = root / 'sealed-runtime.json'
    _atomic_json(runtime_config, {**copied, '_sealed_runtime': True,
                                  '_sealed_manifest': str(root / 'manifest.json')})
    # The inventory includes modes.  Freeze the three executable trees before
    # sealing, otherwise the post-bootstrap permission hardening would itself
    # look like drift on the first command.
    for tree in (controller_root, harness_root, source_root):
        freeze_tree(tree)
    # Playwright's external module is executable browser code too.  It stays
    # in place (third-party ownership is not altered), but its full tree is
    # an audited input and every action verifies it for drift.
    manifest = seal_inputs([runtime_config, settings_copy, Path(plan['node_binary']),
                            Path(plan['k6_binary']), Path(plan['chromium_binary'])], root / 'manifest.json',
                           trees=[controller_root, harness_root, source_root, Path(plan['browser_module'])])
    os.chmod(runtime_config, 0o400)
    os.chmod(settings_copy, 0o400)
    os.chmod(root / 'manifest.json', 0o400)
    os.chmod(root, 0o500)
    return copied, manifest, runtime_config


def freeze_tree(root):
    """Remove write permission while retaining executable dependency binaries."""
    for directory, _, files in os.walk(root):
        directory_path = Path(directory)
        directory_mode = directory_path.stat().st_mode & 0o777
        refuse(not directory_mode & 0o400 or not directory_mode & 0o100,
               'sealed directory lacks owner read/traverse permission: ' + str(directory_path))
        for filename in files:
            path = directory_path / filename
            if path.is_symlink():
                continue
            mode = path.stat().st_mode & 0o777
            refuse(not mode & 0o400, 'sealed file lacks owner read permission: ' + str(path))
            os.chmod(path, mode & ~0o222)
        os.chmod(directory_path, directory_mode & ~0o222)


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
    return {'schema': 'p100-rehearsal-plan-v2', 'source_sha': plan['source_sha'],
            'phase_b_approved_sha': plan['phase_b_approved_sha'], 'audited_checkout_sha': plan['audited_checkout_sha'], 'service': plan['service'],
            'controller_sha': plan['phase_b_controller_sha'], 'harness_sha': plan['harness_audited_sha'],
            'release_source_sha': plan['release_source_sha'],
            'rows': rows, 'controller': {'prepare_requires_fixture': True,
                                          'apply_requires_fixture': True,
                                          'rollback': command_for_controller(plan, 'rollback')}}


def sealed_coordinator_timeout(plan):
    """Bound the one H coordinator process; its child commands retain 180 s.

    The matrix owns every k6 duration plus graceful stop. Fifteen minutes
    covers the two browser passes, build/apply/rollback, probes and startup.
    """
    rows = matrix_plan(plan)['rows']
    return sum(row['duration_seconds'] + row['graceful_stop_seconds'] for row in rows) + 15 * 60


def _file_identity(path):
    info = path.stat()
    return {'device': info.st_dev, 'inode': info.st_ino, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def harness_input_paths(plan):
    """The complete H executable surface; keep this list aligned with R2's contract."""
    here = Path(plan.get('harness_dir', Path(__file__).resolve().parent))
    return [Path(plan['browser_script']), here / 'frontend_rehearsal.py', here / 'frontend_fixture.py',
            here / 'frontend_metrics.py', here / 'frontend_http.js',
            here / 'frontend_http_flow.mjs', here / 'frontend_http_result.mjs']


def _tree_inventory(root):
    root = Path(root).resolve(strict=True)
    entries = []
    for directory, dirs, files in os.walk(root):
        dirs.sort(); files.sort()
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                dirs.remove(name)
                entries.append(_internal_link_inventory(root, path))
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                entries.append(_internal_link_inventory(root, path))
            else:
                refuse(not path.is_file(), 'sealed tree contains non-regular file: ' + str(path))
                entries.append({'kind': 'file', 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'mode': path.stat().st_mode & 0o777})
    return sorted(entries, key=lambda item: item['path'])


def _internal_link_inventory(root, path):
    """Permit only a literal link whose resolved target is inside this tree."""
    literal = os.readlink(path)
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError('sealed tree contains dangling or cyclic symlink: ' + str(path)) from error
    if target != root and root not in target.parents:
        raise ValueError('sealed tree symlink escapes root: ' + str(path))
    if not (target.is_file() or target.is_dir()):
        raise ValueError('sealed tree symlink target is unsupported: ' + str(path))
    return {'kind': 'symlink', 'path': str(path), 'target': literal,
            'resolved': str(target), 'mode': path.lstat().st_mode & 0o777}


def seal_inputs(paths, manifest_path, *, trees=()):
    """Write an immutable provenance manifest before starting a private service."""
    entries = []
    for path in paths:
        path = Path(path)
        refuse(path.is_symlink() or not path.is_file(), 'sealed input must be a regular file: ' + str(path))
        entries.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    roots = [str(Path(root).resolve(strict=True)) for root in trees]
    manifest = {'schema': 'p100-rehearsal-inputs-v2', 'inputs': entries,
                'trees': {root: _tree_inventory(root) for root in roots}}
    manifest_path = Path(manifest_path)
    descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        _write_all(descriptor, _canonical_json_bytes(manifest))
        os.fsync(descriptor)
    except BaseException:
        manifest_path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return manifest


def _canonical_json_bytes(value):
    """Stable manifest bytes, so a receipt can bind the object we sealed."""
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('utf8')


def sealed_manifest_sha256(manifest):
    return hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()


def verify_dynamic_inputs(manifest, artifacts):
    """Accept only the exact two derived files, never a manifest-selected subset."""
    artifacts = Path(artifacts).resolve(strict=True)
    expected = {str((artifacts / name).resolve()) for name in ('vite.mjs', 'controller-settings.json')}
    if manifest.get('schema') != 'p100-rehearsal-inputs-v2' or manifest.get('trees') != {}:
        raise ValueError('dynamic sealed manifest schema differs')
    entries = manifest.get('inputs')
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError('dynamic sealed manifest input set differs')
    paths = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
            raise ValueError('dynamic sealed manifest entry differs')
        if not isinstance(entry['path'], str) or not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise ValueError('dynamic sealed manifest entry differs')
        paths.append(entry['path'])
    if set(paths) != expected or len(set(paths)) != len(paths):
        raise ValueError('dynamic sealed manifest input set differs')
    verify_sealed_inputs(manifest)


def verify_sealed_inputs(manifest):
    if not manifest.get('inputs'):
        raise ValueError('sealed manifest is incomplete')
    for entry in manifest.get('inputs', []):
        path = Path(entry['path'])
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('sealed input drift: ' + str(path))
    for root, expected in manifest.get('trees', {}).items():
        if _tree_inventory(root) != expected:
            raise ValueError('sealed tree drift: ' + root)


def run_sealed(manifest, command, **kwargs):
    """Guard executable invocation at both sides of the subprocess boundary."""
    verify_sealed_inputs(manifest)
    try:
        return run_checked(command, **kwargs)
    finally:
        verify_sealed_inputs(manifest)


def bundled_manager(plan, settings):
    """Load Manager only from B's extracted controller bundle."""
    directory = str(Path(plan['controller']).resolve().parent)
    module_path = Path(directory) / 'release_systemd.py'
    if not module_path.is_file() or module_path.is_symlink():
        raise ValueError('sealed B Manager is missing')
    sys.path.insert(0, directory)
    try:
        spec = importlib.util.spec_from_file_location('_p100_sealed_release_systemd', module_path)
        if spec is None or spec.loader is None:
            raise ValueError('sealed B Manager cannot be loaded')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    manager = module.Manager(settings, True)
    return manager, module.same_process


def validate_restored_vite(plan, settings, manager, capture):
    """Use B's Controller contract before accepting rollback's new Vite PID."""
    directory = str(Path(plan['controller']).resolve().parent)
    module_path = Path(directory) / 'frontend_release.py'
    spec = importlib.util.spec_from_file_location('_p100_sealed_frontend_release', module_path)
    if spec is None or spec.loader is None:
        raise ValueError('sealed B Controller cannot be loaded')
    sys.path.insert(0, directory)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    controller = module.Controller(settings, user=True, manager=manager)
    controller._validate_original(capture, plan['release_source_sha'])


def stopped_matches(before, stopped):
    if stopped['properties']['ActiveState'] != 'inactive' or stopped['process'] is not None:
        return False
    stable = ('FragmentPath', 'DropInPaths', 'ExecStart', 'ExecStartPre', 'User', 'Group',
              'WorkingDirectory', 'UnitFileState', 'Type', 'KillMode', 'RuntimeDirectory',
              'RuntimeDirectoryMode', 'StandardOutput', 'StandardError')
    return (before['unit_paths'] == stopped['unit_paths'] and before['files'] == stopped['files'] and
            all(before['properties'][key] == stopped['properties'][key] for key in stable))


def manager_cas(manager, same_process, expected):
    """Two complete coherent observations immediately before an owned stop."""
    first = manager.capture(http=False); manager.coherent(first)
    second = manager.capture(http=False); manager.coherent(second)
    if (first['properties'] != expected['properties'] or first['files'] != expected['files'] or
            first['unit_paths'] != expected['unit_paths'] or not same_process(first['process'], expected['process']) or
            second['properties'] != first['properties'] or second['files'] != first['files'] or
            second['unit_paths'] != first['unit_paths'] or not same_process(second['process'], first['process'])):
        raise ValueError('private unit Manager CAS drift; no stop or unlink')
    return second


def assert_private_unit_absent(manager, service):
    """Refuse any base unit or drop-in in every user-manager search path."""
    stem = service.removesuffix('.service')
    names = {service + '.d', 'service.d'}
    names.update(stem[:index + 1] + '.service.d' for index, char in enumerate(stem) if char == '-')
    for parent in manager.unit_paths():
        root = Path(parent)
        if (root / service).exists() or (root / service).is_symlink():
            raise ValueError('private base unit already exists in Manager UnitPath')
        for name in names:
            directory = root / name
            if directory.is_symlink():
                raise ValueError('private unit drop-in directory is a symlink')
            if directory.exists() and any(directory.glob('*.conf')):
                raise ValueError('private unit drop-in already exists in Manager UnitPath')


def assert_owned_vite_capture(capture, unit, identity):
    """Do not adopt a coherent unit if any foreign drop-in raced creation."""
    properties = capture['properties']
    if properties['FragmentPath'] != str(Path(unit)) or properties['DropInPaths'].split():
        raise ValueError('private ownership witness has foreign base or drop-in')
    unit = Path(unit)
    base = capture['files'].get(str(unit))
    # Manager fingerprints carry content/owner/mode; bind device+inode from
    # the live no-follow identity as well, because its API omits them.
    if _file_identity(unit) != identity or not base or base.get('sha256') != identity['sha256']:
        raise ValueError('private ownership witness base bytes differ')
    for path, state in capture['files'].items():
        candidate = Path(path)
        if candidate.name == unit.name and candidate != unit and not state.get('absent'):
            raise ValueError('private ownership witness has another service base')
    if any(path.endswith('.conf') for path, state in capture['files'].items() if not state.get('absent')):
        raise ValueError('private ownership witness includes a drop-in')


def _atomic_json(path, value):
    """Publish a private receipt only after its preceding operation completed."""
    path = Path(path)
    encoded = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def invalidate_terminal_receipt(artifacts, cause):
    """Remove a terminal claim after post-run drift; retain raw measurements."""
    artifacts = Path(artifacts)
    for name in ('summary.json', 'terminal-receipt.json'):
        (artifacts / name).unlink(missing_ok=True)
    receipt = artifacts / 'incomplete-receipt.json'
    if not receipt.exists():
        _atomic_json(receipt, {'schema': 'p100-rehearsal-incomplete-v2', 'cause': cause,
                               'cleanup_sha256': _sha256_file(artifacts / 'cleanup.json') if (artifacts / 'cleanup.json').is_file() else None})


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
    cache_dir = (Path(directory) / 'vite-cache').resolve()
    code = f'''import {{createServer}} from {vite_url!r};
import sourceConfig from {source_url!r};
const fixture = process.env.P100_FIXTURE_URL;
if (fixture !== {fixture_url!r}) throw new Error('private fixture environment differs');
const sourceServer = sourceConfig.server || {{}};
const sourceProxy = sourceServer.proxy || {{}};
if (!sourceProxy['/api'] || !sourceProxy['/ws'] || !Array.isArray(sourceServer.allowedHosts)) throw new Error('source proxy/host contract missing');
const server = await createServer({{...sourceConfig, configFile: false, root: {str((Path(plan['source_checkout']) / 'frontend').resolve())!r}, cacheDir: {str(cache_dir)!r}, server: {{...sourceServer, host: '127.0.0.1', port: {plan['listen_port']}, strictPort: true,
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
    paths = harness_input_paths(plan)
    # Both browser and k6 contracts deliberately use Git-SHA1-shaped,
    # 40-character provenance values.  This is a content digest of the exact
    # harness inputs, not a claim that they are a Git commit.
    digest = hashlib.sha1()
    for path in paths:
        digest.update(path.name.encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def harness_inputs_sha256(plan):
    """SHA-256 provenance of the seven sealed H sources, distinct from legacy SHA-1."""
    digest = hashlib.sha256()
    for path in harness_input_paths(plan):
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


def _http_probe(port, method, path, *, host='axioma-ia.io', origin=None, body=None, declared_length=None):
    """Persist only status/cache metadata, never a response body or headers wholesale."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        headers = {'Host': host}
        if origin is not None:
            headers['Origin'] = origin
        if body is not None and declared_length is not None:
            raise ValueError('probe cannot provide both a body and a declared-only length')
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(body))
        if declared_length is not None:
            if type(declared_length) is not int or declared_length < 0:
                raise ValueError('probe declared length must be a non-negative integer')
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(declared_length)
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
                           declared_length=fixture.config['max_body_bytes'] + 1),
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


def _execute_sealed(plan, sealed_inputs):
    """Run the private Vite -> nginx -> Vite rehearsal after formal B approval."""
    artifacts = Path(plan['artifact_dir']); artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    verify_sealed_inputs(sealed_inputs)
    def run(command, **kwargs):
        return run_sealed(sealed_inputs, command, **kwargs)
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
    report = None
    manager = same_process = ownership = None
    dynamic_inputs = None
    dynamic_inputs_sha256 = None
    try:
        entry = write_private_vite_entry(plan, fixture_url, artifacts)
        private_settings = write_private_settings(plan, entry, artifacts)
        for path in (entry, private_settings):
            os.chmod(path, path.stat().st_mode & ~0o222)
        dynamic_inputs = seal_inputs([entry, private_settings], artifacts / 'dynamic-inputs.json')
        dynamic_inputs_sha256 = sealed_manifest_sha256(dynamic_inputs)
        verify_dynamic_inputs(dynamic_inputs, artifacts)
        def run(command, **kwargs):
            verify_dynamic_inputs(dynamic_inputs, artifacts)
            try:
                return run_sealed(sealed_inputs, command, **kwargs)
            finally:
                verify_dynamic_inputs(dynamic_inputs, artifacts)
        manager, same_process = bundled_manager(plan, json.loads(private_settings.read_text()))
        assert_private_unit_absent(manager, plan['service'])
        unit, identity = write_unit(plan, entry, fixture_url)
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'start', plan['service']])
        candidate = manager.capture(http=False)
        manager.coherent(candidate)
        assert_owned_vite_capture(candidate, unit, identity)
        validate_restored_vite(plan, json.loads(private_settings.read_text()), manager, candidate)
        ownership = candidate
        browser_runs = {}
        # Both implementations use the same port.  The controller replaces
        # Vite only after every Vite browser/probe/k6 row is durable.
        for variant, port in (('vite', plan['listen_port']),):
            config = browser_config(plan, variant, port, fixture_config)
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
                json.dump(config, handle); config_path = Path(handle.name)
            try:
                config_path.chmod(0o600)
                run([plan['node_binary'], plan['browser_script'], '--config', str(config_path)])
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
                result = run([plan['k6_binary'], 'run', '--config', str(empty_k6), '--include-system-env-vars=false',
                                      '--no-usage-report', '--new-machine-readable-summary=false', '--env',
                                      'P100_RUN_CONFIG=' + str(run_input), str(Path(plan.get('harness_dir', Path(__file__).resolve().parent)) / 'frontend_http.js')], allowed=(0, 99))
                (summary.with_suffix('.k6.txt')).write_text(result.stdout + result.stderr, encoding='utf8')
                raw_runs.append(json.loads(summary.read_text()))
            finally:
                run_input.unlink(missing_ok=True)
        # B's apply deliberately prepares a fresh package itself, but this
        # explicit prepare validates the accredited Vite identity before any
        # release switch and leaves no active mutation.
        run(command_for_controller(plan, 'prepare', fixture_url, settings=private_settings))
        apply_attempted = True
        run(command_for_controller(plan, 'apply', fixture_url, settings=private_settings))
        config = browser_config(plan, 'nginx', plan['listen_port'], fixture_config)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
            json.dump(config, handle); config_path = Path(handle.name)
        try:
            config_path.chmod(0o600)
            run([plan['node_binary'], plan['browser_script'], '--config', str(config_path)])
        finally:
            config_path.unlink(missing_ok=True)
        browser_runs['nginx'] = {'base_url': config['base_url'], 'port': plan['listen_port']}
        (artifacts / 'probes-nginx.json').write_text(json.dumps(run_probes(plan['listen_port'], 'nginx', fixture, plan['invalid_host_statuses']['nginx']), indent=2) + '\n', encoding='utf8')
        for row in (row for row in matrix_plan(plan)['rows'] if row['variant'] == 'nginx'):
            config, summary = k6_row(plan, row, browser_runs[row['variant']], fixture_config)
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, dir=artifacts) as handle:
                json.dump(config, handle); run_input = Path(handle.name)
            try:
                result = run([plan['k6_binary'], 'run', '--config', str(empty_k6), '--include-system-env-vars=false',
                                      '--no-usage-report', '--new-machine-readable-summary=false', '--env',
                                      'P100_RUN_CONFIG=' + str(run_input), str(Path(plan.get('harness_dir', Path(__file__).resolve().parent)) / 'frontend_http.js')], allowed=(0, 99))
                (summary.with_suffix('.k6.txt')).write_text(result.stdout + result.stderr, encoding='utf8')
                raw_runs.append(json.loads(summary.read_text()))
            finally:
                run_input.unlink(missing_ok=True)
        report = summarize(raw_runs)
        report['fixture_events'] = fixture.events()
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
                        run(command_for_controller(plan, 'rollback', settings=private_settings))
                        restored = True
                    except Exception as error:  # fail-soft: preserve the canary unit and recovery inputs; record failure alongside the primary error
                        cleanup_errors.append('rollback: ' + type(error).__name__)
                if restored:
                    try:
                        if manager is None or same_process is None or ownership is None:
                            raise ValueError('sealed Manager ownership witness missing')
                        else:
                            # Rollback legitimately replaces nginx with Vite,
                            # so obtain the Vite witness only after rollback.
                            if apply_attempted:
                                ownership = manager.capture(http=False)
                                manager.coherent(ownership)
                                validate_restored_vite(plan, json.loads(private_settings.read_text()), manager, ownership)
                            manager_cas(manager, same_process, ownership)
                            verify_sealed_inputs(sealed_inputs)
                            manager.stop()
                            verify_sealed_inputs(sealed_inputs)
                            stopped = manager.capture(http=False)
                            manager.coherent(stopped)
                            if not stopped_matches(ownership, stopped):
                                raise ValueError('private unit stop changed effective ownership witness')
                            unit.unlink()
                            verify_sealed_inputs(sealed_inputs)
                            manager.reload()
                            verify_sealed_inputs(sealed_inputs)
                            assert_private_unit_absent(manager, plan['service'])
                    except Exception as error:  # fail-soft: preserve recovery inputs and record incomplete cleanup without masking the primary error
                        cleanup_errors.append('unit cleanup: ' + type(error).__name__)
        fixture.close()
        cleanup = {'primary_error': None if primary is None else type(primary).__name__, 'cleanup_errors': cleanup_errors}
        artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_json(artifacts / 'cleanup.json', cleanup)
        if cleanup_errors and primary is None:
            raise RuntimeError('rehearsal cleanup incomplete: ' + '; '.join(cleanup_errors))
        if primary is None and not cleanup_errors and report is not None:
            # The measurement is not a result until rollback and private-unit
            # teardown both completed.  The two receipts bind that fact.
            try:
                verify_sealed_inputs(sealed_inputs)
                verify_dynamic_inputs(dynamic_inputs, artifacts)
                if _sha256_file(artifacts / 'dynamic-inputs.json') != dynamic_inputs_sha256:
                    raise ValueError('dynamic sealed manifest drift')
            except Exception as error:
                invalidate_terminal_receipt(artifacts, 'pre-terminal sealed input drift: ' + type(error).__name__)
                raise
            _atomic_json(artifacts / 'summary.json', report)
            receipt = {'schema': 'p100-rehearsal-terminal-v2',
                       'cleanup_sha256': _sha256_file(artifacts / 'cleanup.json'),
                       'summary_sha256': _sha256_file(artifacts / 'summary.json'),
                       'phase_b_controller_sha': plan['phase_b_controller_sha'],
                       'harness_audited_sha': plan['harness_audited_sha'],
                       'release_source_sha': plan['release_source_sha'],
                       'harness_inputs_sha256': harness_inputs_sha256(plan),
                       'dynamic_inputs_manifest_sha256': dynamic_inputs_sha256}
            _atomic_json(artifacts / 'terminal-receipt.json', receipt)
        elif primary is not None or cleanup_errors:
            _atomic_json(artifacts / 'incomplete-receipt.json', {
                'schema': 'p100-rehearsal-incomplete-v1',
                'cleanup_sha256': _sha256_file(artifacts / 'cleanup.json'),
                'primary_error': None if primary is None else type(primary).__name__})
    return report


def execute(plan):
    """Bootstrap immutable B/H/S inputs, then execute only H's coordinator bytes."""
    artifacts = Path(plan['artifact_dir']); artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_plan, manifest, runtime_config = bootstrap_inputs(plan, artifacts)
    # This process has no authority to operate the rehearsal after bootstrap:
    # invoke the coordinator that was extracted from H's Git object.
    try:
        run_sealed(manifest, [sys.executable, runtime_plan['browser_script'].replace('frontend_browser.mjs', 'frontend_rehearsal.py'),
                              '--sealed-runtime', str(runtime_config)], timeout=sealed_coordinator_timeout(runtime_plan))
    except Exception as error:
        invalidate_terminal_receipt(artifacts, 'post-H sealed input drift or failure: ' + type(error).__name__)
        raise
    summary = artifacts / 'summary.json'; receipt = artifacts / 'terminal-receipt.json'
    try:
        if not summary.is_file() or not receipt.is_file():
            raise RuntimeError('sealed coordinator returned without terminal summary')
        terminal = json.loads(receipt.read_text())
        if terminal.get('summary_sha256') != _sha256_file(summary) or terminal.get('cleanup_sha256') != _sha256_file(artifacts / 'cleanup.json'):
            raise RuntimeError('terminal receipt does not bind completed cleanup and summary')
        expected = {key: runtime_plan[key] for key in ('phase_b_controller_sha', 'harness_audited_sha', 'release_source_sha')}
        if any(terminal.get(key) != value for key, value in expected.items()) or terminal.get('harness_inputs_sha256') != harness_inputs_sha256(runtime_plan):
            raise RuntimeError('terminal receipt provenance differs from sealed B/H/S inputs')
        dynamic_path = artifacts / 'dynamic-inputs.json'
        expected_dynamic_digest = terminal.get('dynamic_inputs_manifest_sha256')
        dynamic_bytes = dynamic_path.read_bytes()
        if not isinstance(expected_dynamic_digest, str) or hashlib.sha256(dynamic_bytes).hexdigest() != expected_dynamic_digest:
            raise RuntimeError('terminal receipt dynamic input provenance differs')
        dynamic = json.loads(dynamic_bytes)
        verify_dynamic_inputs(dynamic, artifacts)
    except Exception as error:
        invalidate_terminal_receipt(artifacts, 'post-H terminal validation: ' + type(error).__name__)
        raise
    return json.loads(summary.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--phase-b-approved-sha')
    parser.add_argument('--harness-audited-sha')
    parser.add_argument('--release-source-sha')
    parser.add_argument('--sealed-runtime', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sealed_runtime is not None:
        if args.plan or args.execute or args.phase_b_approved_sha or args.harness_audited_sha or args.release_source_sha:
            parser.error('--sealed-runtime is internal and exclusive')
        raw = json.loads(args.sealed_runtime.read_text())
        if raw.pop('_sealed_runtime', False) is not True:
            parser.error('sealed runtime marker required')
        manifest_path = Path(raw.pop('_sealed_manifest', ''))
        if not manifest_path.is_file():
            parser.error('sealed input manifest missing')
        manifest = json.loads(manifest_path.read_text())
        verify_sealed_inputs(manifest)
        _execute_sealed(raw, manifest)
        return 0
    if args.plan == args.execute:
        parser.error('choose exactly one of --plan or --execute')
    if args.config is None:
        parser.error('--config is required outside sealed runtime')
    plan = load_plan(json.loads(args.config.read_text()))
    if args.plan:
        print(json.dumps(matrix_plan(plan), indent=2)); return 0
    if (args.phase_b_approved_sha != plan['phase_b_controller_sha'] or
            args.harness_audited_sha != plan['harness_audited_sha'] or
            args.release_source_sha != plan['release_source_sha']):
        parser.error('--execute requires exact B/H/S identities from the private config')
    execute(plan)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
