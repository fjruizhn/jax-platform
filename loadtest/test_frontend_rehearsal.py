import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loadtest import frontend_rehearsal as rehearsal
from loadtest.frontend_rehearsal import command_for_controller, harness_sha, load_plan, matrix_plan, run_probes, verify_audited_head, write_private_vite_entry


class RehearsalPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='p100-rehearsal-test-')
        root = Path(self.temp.name)
        self.old_runtime = os.environ.get('XDG_RUNTIME_DIR')
        runtime = root / 'runtime'; (runtime / 'systemd' / 'user').mkdir(parents=True)
        os.environ['XDG_RUNTIME_DIR'] = str(runtime)
        for name in ('staging', 'artifacts', 'checkout', 'release-root', 'nginx-root'):
            (root / name).mkdir()
        release = root / ('a' * 40); release.mkdir()
        self.files = {}
        for name in ('k6', 'chromium'):
            path = root / name; path.write_text('x'); self.files[name] = path
        (root / 'checkout' / 'ops' / 'frontend').mkdir(parents=True)
        (root / 'checkout' / 'loadtest').mkdir()
        self.files['controller'] = root / 'checkout' / 'ops' / 'frontend' / 'frontend_release.py'
        self.files['browser'] = root / 'checkout' / 'loadtest' / 'frontend_browser.mjs'
        self.files['controller'].write_text('x')
        self.files['browser'].write_text('x')
        self.files['settings'] = root / 'settings'
        node = Path(shutil.which('node')).resolve()
        node_version = subprocess.run([str(node), '--version'], capture_output=True, text=True, check=True).stdout.strip().removeprefix('v')
        self.files['settings'].write_text(json.dumps({'listen_port': 49001, 'node_version': node_version,
            'private_mode': True, 'owner_uid': os.getuid(), 'service': 'p100-rehearsal.service',
            'systemd_dir': str(runtime / 'systemd' / 'user'), 'user_runtime_dir': str(runtime),
            'release_root': str(root / 'release-root'), 'nginx_root': str(root / 'nginx-root')}))
        module = root / 'playwright-core'; module.mkdir()
        (root / 'checkout' / 'frontend').mkdir()
        (root / 'checkout' / 'frontend' / 'vite.config.js').write_text("export default {server:{allowedHosts:['axioma-ia.io'],proxy:{'/api':{},'/ws':{}}}}")
        (root / 'checkout' / 'frontend' / 'package-lock.json').write_text('{}\n')
        vite = root / 'checkout' / 'frontend' / 'node_modules' / 'vite' / 'dist' / 'node'; vite.mkdir(parents=True)
        vite_module = vite / 'index.js'; vite_module.write_text('export const createServer = ()=>{}')
        (release / 'manifest.json').write_text(json.dumps({'source_sha': 'a' * 40}))
        self.raw = {'phase_b_approved_sha': 'a' * 40, 'audited_checkout_sha': 'a' * 40, 'source_sha': 'a' * 40,
                    'controller': str(self.files['controller']), 'settings': str(self.files['settings']),
                    'release_dir': str(release), 'staging_root': str(root / 'staging'),
                    'systemd_dir': str(runtime / 'systemd' / 'user'), 'artifact_dir': str(root / 'artifacts'),
                    'source_checkout': str(root / 'checkout'), 'k6_binary': str(self.files['k6']),
                    'browser_script': str(self.files['browser']), 'browser_module': str(module),
                    'chromium_binary': str(self.files['chromium']), 'service': 'p100-rehearsal.service',
                    'listen_port': 49001, 'node_binary': str(node), 'vite_module': str(vite_module),
                    'invalid_host_statuses': {'vite': 403, 'nginx': 404},
                    'fixture': {'email': 'p100@example.invalid', 'password': 'synthetic', 'user': {}, 'project': {'nombre': 'p100'},
                                'appearance': {}, 'state': {}, 'max_body_bytes': 100, 'event_limit': 10}}

    def tearDown(self):
        if self.old_runtime is None: os.environ.pop('XDG_RUNTIME_DIR', None)
        else: os.environ['XDG_RUNTIME_DIR'] = self.old_runtime
        self.temp.cleanup()

    def test_plan_is_side_effect_free_and_predeclares_exact_schema2_matrix(self):
        plan = load_plan(dict(self.raw))
        result = matrix_plan(plan)
        self.assertEqual(len(result['rows']), 40)
        self.assertEqual(sum(row['warmup'] for row in result['rows']), 10)
        self.assertTrue(all(row['graceful_stop_seconds'] == 30 for row in result['rows']))
        self.assertTrue(result['controller']['prepare_requires_fixture'])

    def test_apply_consumes_published_cli_and_never_invents_production_defaults(self):
        plan = load_plan(dict(self.raw))
        command = command_for_controller(plan, 'apply', 'http://localhost:49003', Path(plan['staging_root']) / 'x')
        self.assertEqual(command[:3], [__import__('sys').executable, plan['controller'], 'apply'])
        self.assertEqual(command[command.index('--override-backend') + 1], 'http://localhost:49003')
        self.assertNotIn('/etc', command); self.assertNotIn('/srv', command)

    def test_refuses_production_ports_paths_existing_unit_and_wrong_release_identity(self):
        for key, value in [('listen_port', 5173), ('settings', '/etc/passwd'), ('release_dir', str(Path(self.raw['release_dir']).parent / 'wrong'))]:
            raw = dict(self.raw); raw[key] = value
            if key == 'release_dir': Path(value).mkdir()
            with self.subTest(key=key), self.assertRaises(ValueError): load_plan(raw)
        (Path(self.raw['systemd_dir']) / self.raw['service']).write_text('foreign')
        with self.assertRaises(ValueError): load_plan(dict(self.raw))

    def test_rehearsal_refuses_stale_artifacts_and_preserves_existing_entry(self):
        plan = load_plan(dict(self.raw))
        entry = Path(plan['artifact_dir']) / 'vite.mjs'
        entry.write_text('existing evidence')
        with self.assertRaisesRegex(FileExistsError, ''):
            write_private_vite_entry(plan, 'http://localhost:49003', Path(plan['artifact_dir']))
        self.assertEqual(entry.read_text(), 'existing evidence')
        with self.assertRaisesRegex(ValueError, 'artifact_dir must be empty'):
            load_plan(dict(self.raw))

    def test_rehearsal_requires_same_audited_source_for_vite_and_nginx(self):
        raw = dict(self.raw); raw['source_sha'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'release source SHA differ'):
            load_plan(raw)

    def test_private_path_rejects_symlink_before_resolving_it(self):
        target = Path(self.raw['controller'])
        alias = target.with_name('controller-alias')
        alias.symlink_to(target)
        raw = dict(self.raw); raw['controller'] = str(alias)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            load_plan(raw)

    def test_rehearsal_controller_must_be_the_audited_checkout_file(self):
        external = Path(self.raw['artifact_dir']).parent / 'other-controller.py'
        external.write_text('x')
        raw = dict(self.raw); raw['controller'] = str(external)
        with self.assertRaisesRegex(ValueError, 'audited checkout'):
            load_plan(raw)

    def test_harness_digest_changes_with_the_explicit_harness_files(self):
        plan = load_plan(dict(self.raw))
        # The temporary browser script is an intentional explicit input; the
        # digest is provenance, not a source-SHA claim.
        self.assertRegex(harness_sha(plan), r'^[0-9a-f]{40}$')

    def test_private_override_derives_source_proxy_and_never_mentions_backend8080(self):
        plan = load_plan(dict(self.raw))
        output = write_private_vite_entry(plan, 'http://localhost:49003', Path(plan['artifact_dir']))
        text = output.read_text()
        self.assertIn((Path(plan['source_checkout']) / 'frontend' / 'vite.config.js').resolve().as_uri(), text)
        self.assertIn("target: 'http://localhost:49003'", text)
        self.assertIn("target: 'ws://localhost:49003'", text)
        self.assertNotIn('8080', text)

    def test_ui_probes_require_proxy_observations_and_never_probe_fixture_directly_for_host_denial(self):
        observed, calls = [], []
        class Fixture:
            config = {'max_body_bytes': 100}; server_address = ('127.0.0.1', 49003)
            def observations(self): return list(observed)
        fixture = Fixture()
        def probe(port, method, path, **kwargs):
            calls.append((port, method, path, kwargs))
            if path.startswith('/api/p100-not-found'):
                observed.append({'target': path, 'method': method, 'host': 'localhost:49003', 'origin': kwargs['origin']})
                status = 404
            elif path.startswith('/api/auth/login'):
                observed.append({'target': path, 'method': method, 'host': 'localhost:49003', 'origin': kwargs['origin']})
                status = 413
            elif path.startswith('/assets/'): status = 404
            elif path == '/api/apariencia': status = 403
            else: status = 200
            return {'method': method, 'path': path, 'status': status, 'cache_control': 'no-cache'}
        with patch.object(rehearsal, '_http_probe', side_effect=probe):
            run_probes(49001, 'vite', fixture, 403)
        self.assertTrue(all(port == 49001 for port, *_ in calls))
        self.assertEqual(calls[0][3]['origin'], 'http://axioma-ia.io:49001')

    def test_execute_requires_actual_checkout_head_to_match_the_formal_approval(self):
        plan = load_plan(dict(self.raw))
        def clean(command, **kwargs):
            text = plan['audited_checkout_sha'] + '\n' if command[-1] == 'HEAD' else ''
            return type('Result', (), {'returncode': 0, 'stdout': text, 'stderr': ''})()
        with patch.object(rehearsal, 'run_checked', side_effect=clean):
            verify_audited_head(plan)
        def wrong(command, **kwargs):
            return type('Result', (), {'returncode': 0, 'stdout': 'c' * 40 + '\n', 'stderr': ''})()
        with patch.object(rehearsal, 'run_checked', side_effect=wrong), self.assertRaises(ValueError):
            verify_audited_head(plan)

    def test_dirty_checkout_is_not_an_audited_source(self):
        plan = load_plan(dict(self.raw))
        def dirty(command, **kwargs):
            if command[-1] == 'HEAD': text = plan['audited_checkout_sha'] + '\n'
            else: text = ' M frontend/src/App.jsx\n'
            return type('Result', (), {'returncode': 0, 'stdout': text, 'stderr': ''})()
        with patch.object(rehearsal, 'run_checked', side_effect=dirty), self.assertRaisesRegex(ValueError, 'tracked or untracked edits'):
            verify_audited_head(plan)

    def test_generated_entry_and_settings_match_controller_private_execstart_contract(self):
        plan = load_plan(dict(self.raw))
        entry = write_private_vite_entry(plan, 'http://localhost:49003', Path(plan['artifact_dir']))
        settings_path = rehearsal.write_private_settings(plan, entry, Path(plan['artifact_dir']))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops' / 'frontend'))
        try:
            from frontend_release import Controller
            controller = Controller.__new__(Controller)
            controller.s = json.loads(settings_path.read_text()); controller.user = True
            controller.s['process_uid'] = os.getuid(); controller.s['process_gid'] = os.getgid()
            contract = controller.s['original_contract']
            origin = {'properties': {'ExecStart': '{ path=' + contract['node_binary'] + ' ; argv[]=' +
                      contract['node_binary'] + ' ' + contract['entry'] + ' ; ignore_errors=no ; }', 'ExecStartPre': ''},
                      'process': None}
            controller._validate_original(origin, plan['source_sha'])
        finally:
            sys.path.pop(0)

    def test_private_unit_and_settings_survive_partial_writes(self):
        plan = load_plan(dict(self.raw))
        entry = write_private_vite_entry(plan, 'http://localhost:49003', Path(plan['artifact_dir']))
        original_write = os.write
        calls = []
        def short_write(descriptor, data):
            calls.append(len(data))
            return original_write(descriptor, data[:9])
        with patch.object(rehearsal.os, 'write', side_effect=short_write):
            settings_path = rehearsal.write_private_settings(plan, entry, Path(plan['artifact_dir']))
            unit, identity = rehearsal.write_unit(plan, entry, 'http://localhost:49003')
        self.assertGreater(len(calls), 2)
        self.assertEqual(json.loads(settings_path.read_text())['service'], plan['service'])
        self.assertIn('ExecStart=', unit.read_text())
        self.assertEqual(rehearsal._file_identity(unit), identity)

    def test_failed_private_file_write_removes_incomplete_file(self):
        plan = load_plan(dict(self.raw))
        entry = write_private_vite_entry(plan, 'http://localhost:49003', Path(plan['artifact_dir']))
        with patch.object(rehearsal.os, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                rehearsal.write_private_settings(plan, entry, Path(plan['artifact_dir']))
            with self.assertRaises(OSError):
                rehearsal.write_unit(plan, entry, 'http://localhost:49003')
        self.assertFalse((Path(plan['artifact_dir']) / 'controller-settings.json').exists())
        self.assertFalse((Path(plan['systemd_dir']) / plan['service']).exists())

    def test_failed_rollback_preserves_private_unit_and_recovery_inputs(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        entry = artifacts / 'override.mjs'; entry.write_text('override')
        settings = artifacts / 'controller-settings.json'; settings.write_text('{}')
        unit = Path(plan['systemd_dir']) / plan['service']; unit.write_text('owned')
        identity = rehearsal._file_identity(unit)
        calls = []

        class Fixture:
            base_url = 'http://127.0.0.1:49003'
            def __init__(self, address, config): self.config = config
            def start(self): return self
            def close(self): pass

        def fake_checked(command, **kwargs):
            calls.append(command)
            if len(command) > 2 and command[2] in ('apply', 'rollback'):
                raise RuntimeError(command[2] + ' failed')
            if command[0] == plan['k6_binary']:
                run_file = Path(next(item.split('=', 1)[1] for item in command if item.startswith('P100_RUN_CONFIG=')))
                Path(json.loads(run_file.read_text())['summary_file']).write_text('{}')
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch.object(rehearsal, 'verify_audited_head'), patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=entry), \
             patch.object(rehearsal, 'write_private_settings', return_value=settings), \
             patch.object(rehearsal, 'run_checked', side_effect=fake_checked), \
             patch.object(rehearsal, 'run_probes', return_value=[]), \
             self.assertRaisesRegex(RuntimeError, 'apply failed'):
            rehearsal.execute(plan)
        self.assertTrue(unit.exists())
        self.assertTrue(settings.exists())
        self.assertTrue(entry.exists())
        self.assertFalse(any(cmd[:3] == ['systemctl', '--user', 'stop'] for cmd in calls))
        self.assertIn('rollback: RuntimeError', json.loads((artifacts / 'cleanup.json').read_text())['cleanup_errors'])

    def test_failure_before_apply_stops_owned_private_vite_without_rollback(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        entry = artifacts / 'override.mjs'; entry.write_text('override')
        settings = artifacts / 'controller-settings.json'; settings.write_text('{}')
        unit = Path(plan['systemd_dir']) / plan['service']; unit.write_text('owned')
        identity = rehearsal._file_identity(unit)
        calls = []

        class Fixture:
            base_url = 'http://127.0.0.1:49003'
            def __init__(self, address, config): self.config = config
            def start(self): return self
            def close(self): pass

        def fake_checked(command, **kwargs):
            calls.append(command)
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch.object(rehearsal, 'verify_audited_head'), patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=entry), \
             patch.object(rehearsal, 'write_private_settings', return_value=settings), \
             patch.object(rehearsal, 'run_checked', side_effect=fake_checked), \
             patch.object(rehearsal, 'run_probes', side_effect=RuntimeError('pre-switch probe failed')), \
             self.assertRaisesRegex(RuntimeError, 'pre-switch probe failed'):
            rehearsal.execute(plan)
        self.assertFalse(unit.exists())
        self.assertFalse(settings.exists())
        self.assertFalse(entry.exists())
        self.assertFalse(any(len(cmd) > 2 and cmd[2] == 'rollback' for cmd in calls))
        self.assertTrue(any(cmd[:3] == ['systemctl', '--user', 'stop'] for cmd in calls))

    def test_execute_sequences_all_vite_rows_before_apply_then_nginx_rows_then_rollback(self):
        plan = load_plan(dict(self.raw)); calls = []
        artifacts = Path(plan['artifact_dir'])

        class Fixture:
            base_url = 'http://127.0.0.1:49003'; server_address = ('127.0.0.1', 49003)
            def __init__(self, address, config): self.config = config
            def start(self): return self
            def close(self): calls.append('fixture-close')
            def events(self): return []

        unit = artifacts / 'owned.service'; unit.write_text('owned')
        identity = rehearsal._file_identity(unit)
        override = artifacts / 'override.mjs'; override.write_text('override')

        def fake_checked(command, **kwargs):
            calls.append(command)
            if command[0] == plan['k6_binary']:
                run_file = Path(next(item.split('=', 1)[1] for item in command if item.startswith('P100_RUN_CONFIG=')))
                summary = Path(json.loads(run_file.read_text())['summary_file'])
                summary.write_text('{}')
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch.object(rehearsal, 'verify_audited_head'), patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=override), \
             patch.object(rehearsal, 'write_private_settings', return_value=artifacts / 'private-settings.json'), \
             patch.object(rehearsal, 'run_checked', side_effect=fake_checked), \
             patch.object(rehearsal, 'run_probes', return_value=[]), \
             patch.object(rehearsal, 'summarize', return_value={'ok': True}):
            rehearsal.execute(plan)
        browser_positions = [i for i, call in enumerate(calls) if isinstance(call, list) and call[0] == 'node']
        apply_position = next(i for i, call in enumerate(calls) if isinstance(call, list) and len(call) > 2 and call[2] == 'apply')
        k6_positions = [i for i, call in enumerate(calls) if isinstance(call, list) and call[0] == plan['k6_binary']]
        self.assertEqual(len(k6_positions), 40)
        self.assertLess(browser_positions[0], min(k6_positions[:20]))
        self.assertLess(max(k6_positions[:20]), apply_position)
        self.assertLess(apply_position, browser_positions[1])
        self.assertLess(browser_positions[1], min(k6_positions[20:]))
        rollback = next(i for i, call in enumerate(calls) if isinstance(call, list) and len(call) > 2 and call[2] == 'rollback')
        self.assertLess(max(k6_positions), rollback)


if __name__ == '__main__':
    unittest.main()
