import hashlib
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
from loadtest.frontend_rehearsal import command_for_controller, harness_sha, load_plan, matrix_plan, run_probes, write_private_vite_entry


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
            'listen_host': '127.0.0.1', 'proof_connect_host': '127.0.0.1',
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
                    'phase_b_controller_sha': 'a' * 40, 'harness_audited_sha': 'a' * 40,
                    'release_source_sha': 'a' * 40, 'git_repository': str(root / 'checkout'),
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

    def sealed(self, plan):
        return rehearsal.seal_inputs([Path(plan['browser_script'])], Path(plan['artifact_dir']) / 'sealed-test.json',
                                     trees=[Path(plan['source_checkout'])])

    @staticmethod
    def child_witness(digest):
        return type('Result', (), {'stdout': 'P100_DYNAMIC_INPUTS_SHA256=' + digest + '\n'})()

    @staticmethod
    def manager_stub():
        class Manager:
            def unit_paths(self): return []
            def capture(self, **kwargs): return {'properties': {}, 'files': {}, 'unit_paths': [], 'process': None}
            def coherent(self, capture): pass
            def stop(self): pass
            def reload(self): pass
        return Manager(), (lambda left, right: left == right)

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
        raw = dict(self.raw); raw['release_source_sha'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            load_plan(raw)

    def test_plan_records_independent_controller_harness_and_release_identities(self):
        raw = dict(self.raw)
        raw.pop('phase_b_approved_sha'); raw.pop('audited_checkout_sha'); raw.pop('source_sha')
        raw.update({'phase_b_controller_sha': 'b' * 40, 'harness_audited_sha': 'c' * 40,
                    'release_source_sha': 'a' * 40, 'git_repository': self.raw['source_checkout']})
        plan = load_plan(raw)
        self.assertEqual(plan['phase_b_controller_sha'], 'b' * 40)
        self.assertEqual(plan['harness_audited_sha'], 'c' * 40)
        self.assertEqual(plan['release_source_sha'], 'a' * 40)
        matrix = matrix_plan(plan)
        self.assertEqual(matrix['controller_sha'], 'b' * 40)
        self.assertEqual(matrix['harness_sha'], 'c' * 40)
        self.assertEqual(matrix['release_source_sha'], 'a' * 40)

    def test_explicit_identity_rejects_contradictory_legacy_alias_without_mutating_input(self):
        raw = dict(self.raw)
        raw['phase_b_approved_sha'] = 'b' * 40
        before = dict(raw)
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            load_plan(raw)
        self.assertEqual(raw, before)

    def test_execute_cli_requires_all_three_explicit_identities(self):
        config = Path(self.temp.name) / 'plan.json'; config.write_text(json.dumps(self.raw))
        base = ['frontend_rehearsal.py', '--config', str(config), '--execute',
                '--phase-b-approved-sha', 'a' * 40]
        with patch.object(sys, 'argv', base), self.assertRaises(SystemExit) as missing:
            rehearsal.main()
        self.assertEqual(missing.exception.code, 2)
        mismatched = base + ['--harness-audited-sha', 'a' * 40, '--release-source-sha', 'b' * 40]
        with patch.object(sys, 'argv', mismatched), self.assertRaises(SystemExit) as mismatch:
            rehearsal.main()
        self.assertEqual(mismatch.exception.code, 2)

    def test_sealed_input_manifest_detects_byte_drift(self):
        plan = load_plan(dict(self.raw))
        paths = rehearsal.harness_input_paths(plan)
        sealed = rehearsal.seal_inputs(paths, Path(plan['artifact_dir']) / 'inputs-manifest.json', trees=[Path(plan['source_checkout'])])
        rehearsal.verify_sealed_inputs(sealed)
        changed = paths[0]
        original = changed.read_bytes()
        try:
            changed.write_bytes(original + b'changed')
            with self.assertRaisesRegex(ValueError, 'sealed input drift'):
                rehearsal.verify_sealed_inputs(sealed)
        finally:
            changed.write_bytes(original)

    def test_browser_module_tree_drift_invalidates_sealed_input(self):
        plan = load_plan(dict(self.raw))
        module_file = Path(plan['browser_module']) / 'index.mjs'; module_file.write_text('export const browser = 1;')
        manifest = rehearsal.seal_inputs([Path(plan['browser_script'])], Path(plan['artifact_dir']) / 'browser-manifest.json',
                                         trees=[Path(plan['browser_module'])])
        rehearsal.verify_sealed_inputs(manifest)
        module_file.write_text('export const browser = 2;')
        with self.assertRaisesRegex(ValueError, 'sealed tree drift'):
            rehearsal.verify_sealed_inputs(manifest)

    def test_sealed_settings_and_executable_mutants_are_detected(self):
        plan = load_plan(dict(self.raw))
        settings = Path(plan['settings']); node = Path(plan['node_binary']); k6 = Path(plan['k6_binary']); chromium = Path(plan['chromium_binary'])
        manifest = rehearsal.seal_inputs([settings, node, k6, chromium], Path(plan['artifact_dir']) / 'tooling-manifest.json',
                                         trees=[Path(plan['browser_module'])])
        original = k6.read_bytes()
        try:
            k6.write_bytes(original + b'mutant')
            with self.assertRaisesRegex(ValueError, 'sealed input drift'):
                rehearsal.verify_sealed_inputs(manifest)
        finally:
            k6.write_bytes(original)

    def test_sealed_command_refuses_drift_before_invocation(self):
        plan = load_plan(dict(self.raw))
        paths = rehearsal.harness_input_paths(plan)
        sealed = rehearsal.seal_inputs(paths, Path(plan['artifact_dir']) / 'inputs-manifest.json', trees=[Path(plan['source_checkout'])])
        original = paths[0].read_bytes()
        try:
            paths[0].write_bytes(original + b'drift')
            with patch.object(rehearsal, 'run_checked') as checked, self.assertRaisesRegex(ValueError, 'sealed input drift'):
                rehearsal.run_sealed(sealed, ['false'])
            checked.assert_not_called()
        finally:
            paths[0].write_bytes(original)

    def test_manager_cas_refuses_foreign_dropin_or_process_drift_before_stop(self):
        baseline = {'properties': {'ActiveState': 'active'}, 'files': {'unit': {'sha256': 'a'}},
                    'unit_paths': ['/run/user/1000/systemd/user'], 'process': {'pid': 41, 'starttime': '7'}}
        class Manager:
            def __init__(self, captures): self.captures = iter(captures)
            def capture(self, **kwargs): return next(self.captures)
            def coherent(self, capture): pass
        same = lambda left, right: left == right
        rehearsal.manager_cas(Manager([baseline, baseline]), same, baseline)
        foreign = {**baseline, 'files': {**baseline['files'], 'foreign.conf': {'sha256': 'x'}}}
        with self.assertRaisesRegex(ValueError, 'CAS drift'):
            rehearsal.manager_cas(Manager([foreign, foreign]), same, baseline)
        restarted = {**baseline, 'process': {'pid': 42, 'starttime': '8'}}
        with self.assertRaisesRegex(ValueError, 'CAS drift'):
            rehearsal.manager_cas(Manager([restarted, restarted]), same, baseline)

    def test_manager_preflight_refuses_foreign_dropin_in_any_unit_path(self):
        root = Path(self.temp.name) / 'foreign-unit-path'; root.mkdir()
        drop = root / 'p100-rehearsal.service.d'; drop.mkdir(); (drop / '90-foreign.conf').write_text('[Service]\nExecStart=/bin/sleep infinity\n')
        class Manager:
            def unit_paths(self): return [str(root)]
        with self.assertRaisesRegex(ValueError, 'drop-in'):
            rehearsal.assert_private_unit_absent(Manager(), 'p100-rehearsal.service')

    def test_sealed_tree_allows_npm_style_internal_bin_link_and_rejects_external_link(self):
        root = Path(self.temp.name) / 'sealed-tree'; (root / 'node_modules' / 'vite' / 'bin').mkdir(parents=True)
        target = root / 'node_modules' / 'vite' / 'bin' / 'vite.js'; target.write_text('sealed vite')
        bin_dir = root / 'node_modules' / '.bin'; bin_dir.mkdir()
        (bin_dir / 'vite').symlink_to('../vite/bin/vite.js')
        inventory = rehearsal._tree_inventory(root)
        self.assertIn({'kind': 'symlink', 'path': str(bin_dir / 'vite'), 'target': '../vite/bin/vite.js',
                       'resolved': str(target), 'mode': (bin_dir / 'vite').lstat().st_mode & 0o777}, inventory)
        (bin_dir / 'outside').symlink_to('/bin/sh')
        with self.assertRaisesRegex(ValueError, 'escapes root'):
            rehearsal._tree_inventory(root)

    def test_bootstrap_seals_final_permission_modes_before_returning_manifest(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        def archive(repository, sha, paths, destination):
            if paths == ['ops/frontend']:
                output = destination / 'ops' / 'frontend'; output.mkdir(parents=True)
                (output / 'frontend_release.py').write_text('controller')
                (output / 'release_systemd.py').write_text('manager')
            elif paths == ['frontend']:
                output = destination / 'frontend'; output.mkdir()
                (output / 'vite.config.js').write_text('export default {}')
                (output / 'package-lock.json').write_text('{}')
            else:
                output = destination / 'loadtest'; output.mkdir()
                for source in paths:
                    (output / Path(source).name).write_text('harness')
        def checked(command, **kwargs):
            if command[:2] == ['npm', 'ci']:
                vite = Path(kwargs['cwd']) / 'node_modules' / 'vite' / 'dist' / 'node'; vite.mkdir(parents=True)
                (vite / 'index.js').write_text('vite')
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch.object(rehearsal, '_git_archive', side_effect=archive), patch.object(rehearsal, 'run_checked', side_effect=checked):
            _, manifest, _ = rehearsal.bootstrap_inputs(plan, artifacts)
        rehearsal.verify_sealed_inputs(manifest)

    def test_bootstrap_rejects_settings_changed_after_plan_validation(self):
        plan = load_plan(dict(self.raw))
        changed = json.loads(Path(plan['settings']).read_text()); changed['listen_host'] = '0.0.0.0'
        Path(plan['settings']).write_text(json.dumps(changed))
        with patch.object(rehearsal, '_git_archive'), patch.object(rehearsal, 'run_checked'):
            with self.assertRaisesRegex(ValueError, 'validated settings changed'):
                rehearsal.bootstrap_inputs(plan, Path(plan['artifact_dir']))

    def test_freeze_tree_preserves_execution_and_removes_only_writes(self):
        root = Path(self.temp.name) / 'freeze'; root.mkdir(mode=0o755)
        executable = root / 'esbuild'; executable.write_text('bin'); executable.chmod(0o755)
        ordinary = root / 'config.js'; ordinary.write_text('source'); ordinary.chmod(0o644)
        rehearsal.freeze_tree(root)
        self.assertEqual(executable.stat().st_mode & 0o777, 0o555)
        self.assertEqual(ordinary.stat().st_mode & 0o777, 0o444)
        manifest = rehearsal.seal_inputs([ordinary], Path(self.temp.name) / 'freeze-manifest.json', trees=[root])
        rehearsal.verify_sealed_inputs(manifest)

    def test_execute_launches_the_sealed_h_coordinator_not_the_live_file(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        h = artifacts / 'inputs' / 'harness' / 'loadtest'; h.mkdir(parents=True)
        coordinator = h / 'frontend_rehearsal.py'; coordinator.write_text('# sealed')
        browser = h / 'frontend_browser.mjs'; browser.write_text('// sealed')
        runtime = {**plan, 'browser_script': str(browser)}
        (artifacts / 'summary.json').write_text('{}')
        (artifacts / 'cleanup.json').write_text('{}')
        dynamic_entry = artifacts / 'vite.mjs'; dynamic_entry.write_text('entry')
        dynamic_settings = artifacts / 'controller-settings.json'; dynamic_settings.write_text('{}')
        dynamic = rehearsal.seal_inputs([dynamic_entry, dynamic_settings], artifacts / 'dynamic-inputs.json')
        (artifacts / 'terminal-receipt.json').write_text(json.dumps({
            'summary_sha256': hashlib.sha256((artifacts / 'summary.json').read_bytes()).hexdigest(),
            'cleanup_sha256': hashlib.sha256((artifacts / 'cleanup.json').read_bytes()).hexdigest(),
            'phase_b_controller_sha': runtime['phase_b_controller_sha'],
            'harness_audited_sha': runtime['harness_audited_sha'],
            'release_source_sha': runtime['release_source_sha'],
            'harness_inputs_sha256': rehearsal.harness_inputs_sha256(runtime),
            'dynamic_inputs_manifest_sha256': hashlib.sha256((artifacts / 'dynamic-inputs.json').read_bytes()).hexdigest()}))
        with patch.object(rehearsal, 'bootstrap_inputs', return_value=(runtime, self.sealed(runtime), artifacts / 'sealed-runtime.json')), \
             patch.object(rehearsal, 'run_sealed', return_value=self.child_witness(rehearsal.sealed_manifest_sha256(dynamic))) as run:
            rehearsal.execute(plan)
        self.assertEqual(Path(run.call_args.args[1][1]), coordinator)
        self.assertGreater(run.call_args.kwargs['timeout'], 40 * 60)
        self.assertEqual(run.call_args.kwargs['timeout'], rehearsal.sealed_coordinator_timeout(runtime))

    def test_individual_sealed_subprocess_keeps_default_180_second_timeout(self):
        plan = load_plan(dict(self.raw)); manifest = self.sealed(plan)
        with patch.object(rehearsal, 'run_checked') as checked:
            rehearsal.run_sealed(manifest, ['true'])
        self.assertNotIn('timeout', checked.call_args.kwargs)

    def test_terminal_invalidation_removes_claim_but_preserves_raw_measurement(self):
        artifacts = Path(self.temp.name) / 'invalidate'; artifacts.mkdir()
        (artifacts / 'cleanup.json').write_text('{}')
        (artifacts / 'summary.json').write_text('{"ok": true}')
        (artifacts / 'terminal-receipt.json').write_text('{}')
        raw = artifacts / 'k6-row.json'; raw.write_text('{"raw": true}')
        rehearsal.invalidate_terminal_receipt(artifacts, 'test drift')
        self.assertFalse((artifacts / 'summary.json').exists())
        self.assertFalse((artifacts / 'terminal-receipt.json').exists())
        self.assertEqual(raw.read_text(), '{"raw": true}')
        self.assertEqual(json.loads((artifacts / 'incomplete-receipt.json').read_text())['cause'], 'test drift')

    def test_execute_invalidates_terminal_claim_when_parent_hash_validation_fails(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        runtime = dict(plan); manifest = self.sealed(runtime)
        def complete(*args, **kwargs):
            (artifacts / 'cleanup.json').write_text('{}')
            (artifacts / 'summary.json').write_text('{"ok": true}')
            (artifacts / 'raw-k6.json').write_text('{"raw": true}')
            entry = artifacts / 'vite.mjs'; entry.write_text('entry')
            settings = artifacts / 'controller-settings.json'; settings.write_text('{}')
            dynamic = rehearsal.seal_inputs([entry, settings], artifacts / 'dynamic-inputs.json')
            (artifacts / 'terminal-receipt.json').write_text(json.dumps({'summary_sha256': 'wrong'}))
            return self.child_witness(rehearsal.sealed_manifest_sha256(dynamic))
        with patch.object(rehearsal, 'bootstrap_inputs', return_value=(runtime, manifest, artifacts / 'sealed-runtime.json')), \
             patch.object(rehearsal, 'run_sealed', side_effect=complete), \
             self.assertRaisesRegex(RuntimeError, 'terminal receipt'):
            rehearsal.execute(plan)
        self.assertFalse((artifacts / 'summary.json').exists())
        self.assertTrue((artifacts / 'raw-k6.json').exists())
        self.assertTrue((artifacts / 'incomplete-receipt.json').exists())

    def test_execute_rejects_substituted_dynamic_manifest_and_preserves_raw_measurement(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        runtime = dict(plan); manifest = self.sealed(runtime)
        def substituted_dynamic_manifest(*args, **kwargs):
            (artifacts / 'cleanup.json').write_text('{}')
            (artifacts / 'summary.json').write_text('{"ok": true}')
            (artifacts / 'raw-k6.json').write_text('{"raw": true}')
            entry = artifacts / 'vite.mjs'; entry.write_text('entry')
            settings = artifacts / 'controller-settings.json'; settings.write_text('{}')
            dynamic = rehearsal.seal_inputs([entry, settings], artifacts / 'dynamic-inputs.json')
            # Reproduce the attempted weakening: omit settings and bind the
            # receipt to the replacement file's own digest.
            weakened = {**dynamic, 'inputs': [dynamic['inputs'][0]]}
            dynamic_path = artifacts / 'dynamic-inputs.json'
            dynamic_path.write_bytes(rehearsal._canonical_json_bytes(weakened))
            (artifacts / 'terminal-receipt.json').write_text(json.dumps({
                'summary_sha256': hashlib.sha256((artifacts / 'summary.json').read_bytes()).hexdigest(),
                'cleanup_sha256': hashlib.sha256((artifacts / 'cleanup.json').read_bytes()).hexdigest(),
                'phase_b_controller_sha': runtime['phase_b_controller_sha'],
                'harness_audited_sha': runtime['harness_audited_sha'],
                'release_source_sha': runtime['release_source_sha'],
                'harness_inputs_sha256': rehearsal.harness_inputs_sha256(runtime),
                'dynamic_inputs_manifest_sha256': rehearsal.sealed_manifest_sha256(weakened)}))
            return self.child_witness(rehearsal.sealed_manifest_sha256(weakened))
        with patch.object(rehearsal, 'bootstrap_inputs', return_value=(runtime, manifest, artifacts / 'sealed-runtime.json')), \
             patch.object(rehearsal, 'run_sealed', side_effect=substituted_dynamic_manifest), \
             self.assertRaisesRegex(ValueError, 'dynamic sealed manifest input set differs'):
            rehearsal.execute(plan)
        self.assertFalse((artifacts / 'summary.json').exists())
        self.assertFalse((artifacts / 'terminal-receipt.json').exists())
        self.assertTrue((artifacts / 'raw-k6.json').exists())
        self.assertTrue((artifacts / 'incomplete-receipt.json').exists())

    def test_execute_rejects_dynamic_manifest_replaced_after_original_digest_bound(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        runtime = dict(plan); manifest = self.sealed(runtime)
        def replace_after_seal(*args, **kwargs):
            (artifacts / 'cleanup.json').write_text('{}')
            (artifacts / 'summary.json').write_text('{"ok": true}')
            (artifacts / 'raw-k6.json').write_text('{"raw": true}')
            entry = artifacts / 'vite.mjs'; entry.write_text('entry')
            settings = artifacts / 'controller-settings.json'; settings.write_text('{}')
            dynamic = rehearsal.seal_inputs([entry, settings], artifacts / 'dynamic-inputs.json')
            (artifacts / 'dynamic-inputs.json').write_bytes(rehearsal._canonical_json_bytes({**dynamic, 'inputs': [dynamic['inputs'][0]]}))
            (artifacts / 'terminal-receipt.json').write_text(json.dumps({
                'summary_sha256': hashlib.sha256((artifacts / 'summary.json').read_bytes()).hexdigest(),
                'cleanup_sha256': hashlib.sha256((artifacts / 'cleanup.json').read_bytes()).hexdigest(),
                'phase_b_controller_sha': runtime['phase_b_controller_sha'],
                'harness_audited_sha': runtime['harness_audited_sha'],
                'release_source_sha': runtime['release_source_sha'],
                'harness_inputs_sha256': rehearsal.harness_inputs_sha256(runtime),
                'dynamic_inputs_manifest_sha256': rehearsal.sealed_manifest_sha256(dynamic)}))
            return self.child_witness(rehearsal.sealed_manifest_sha256(dynamic))
        with patch.object(rehearsal, 'bootstrap_inputs', return_value=(runtime, manifest, artifacts / 'sealed-runtime.json')), \
             patch.object(rehearsal, 'run_sealed', side_effect=replace_after_seal), \
             self.assertRaisesRegex(RuntimeError, 'dynamic input provenance differs'):
            rehearsal.execute(plan)
        self.assertFalse((artifacts / 'summary.json').exists())
        self.assertTrue((artifacts / 'raw-k6.json').exists())
        self.assertTrue((artifacts / 'incomplete-receipt.json').exists())

    def test_execute_rejects_full_dynamic_and_terminal_forgery_against_child_witness(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        runtime = dict(plan); manifest = self.sealed(runtime)
        def forge_terminal_and_manifest(*args, **kwargs):
            (artifacts / 'cleanup.json').write_text('{}')
            (artifacts / 'summary.json').write_text('{"ok": true}')
            (artifacts / 'raw-k6.json').write_text('{"raw": true}')
            entry = artifacts / 'vite.mjs'; entry.write_text('entry')
            settings = artifacts / 'controller-settings.json'; settings.write_text('{"listen_host":"127.0.0.1"}')
            original = rehearsal.seal_inputs([entry, settings], artifacts / 'dynamic-inputs.json')
            settings.write_text('{"listen_host":"0.0.0.0"}')
            forged = {**original, 'inputs': [
                original['inputs'][0],
                {**original['inputs'][1], 'sha256': hashlib.sha256(settings.read_bytes()).hexdigest()}]}
            (artifacts / 'dynamic-inputs.json').write_bytes(rehearsal._canonical_json_bytes(forged))
            (artifacts / 'terminal-receipt.json').write_text(json.dumps({
                'summary_sha256': hashlib.sha256((artifacts / 'summary.json').read_bytes()).hexdigest(),
                'cleanup_sha256': hashlib.sha256((artifacts / 'cleanup.json').read_bytes()).hexdigest(),
                'phase_b_controller_sha': runtime['phase_b_controller_sha'],
                'harness_audited_sha': runtime['harness_audited_sha'],
                'release_source_sha': runtime['release_source_sha'],
                'harness_inputs_sha256': rehearsal.harness_inputs_sha256(runtime),
                'dynamic_inputs_manifest_sha256': rehearsal.sealed_manifest_sha256(forged)}))
            # This is the child pipe, captured before the attacker can alter
            # either artifact.  It must not agree with the forged receipt.
            return self.child_witness(rehearsal.sealed_manifest_sha256(original))
        with patch.object(rehearsal, 'bootstrap_inputs', return_value=(runtime, manifest, artifacts / 'sealed-runtime.json')), \
             patch.object(rehearsal, 'run_sealed', side_effect=forge_terminal_and_manifest), \
             self.assertRaisesRegex(RuntimeError, 'dynamic input provenance differs'):
            rehearsal.execute(plan)
        self.assertFalse((artifacts / 'summary.json').exists())
        self.assertFalse((artifacts / 'terminal-receipt.json').exists())
        self.assertTrue((artifacts / 'raw-k6.json').exists())
        self.assertTrue((artifacts / 'incomplete-receipt.json').exists())

    def test_derived_settings_drift_blocks_b_command_before_invocation(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        entry = artifacts / 'vite.mjs'; entry.write_text('entry')
        settings = artifacts / 'controller-settings.json'; settings.write_text('{"listen_host":"127.0.0.1"}')
        unit = Path(plan['systemd_dir']) / plan['service']; unit.write_text('owned')
        identity = rehearsal._file_identity(unit)
        class Fixture:
            base_url = 'http://127.0.0.1:49003'
            def __init__(self, address, config): self.config = config
            def start(self): return self
            def close(self): pass
        class Manager:
            def unit_paths(self): return []
            def capture(self, **kwargs): return {'properties': {}, 'files': {}, 'unit_paths': [], 'process': None}
            def coherent(self, capture): pass
        def manager_after_seal(*args):
            # Simula un escritor ajeno con permiso para alterar un archivo sellado.
            settings.chmod(0o600)
            settings.write_text('{"listen_host":"0.0.0.0"}')
            return Manager(), (lambda left, right: left == right)
        with patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=entry), \
             patch.object(rehearsal, 'write_private_settings', return_value=settings), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'bundled_manager', side_effect=manager_after_seal), \
             patch.object(rehearsal, 'assert_owned_vite_capture'), patch.object(rehearsal, 'validate_restored_vite'), \
             patch.object(rehearsal, 'run_sealed') as command, \
             self.assertRaisesRegex(ValueError, 'sealed input drift'):
            rehearsal._execute_sealed(plan, self.sealed(plan))
        command.assert_not_called()

    def test_dynamic_seal_detects_derived_vite_entry_drift(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        entry = artifacts / 'vite.mjs'; entry.write_text('export default {}')
        settings = artifacts / 'controller-settings.json'; settings.write_text('{"listen_host":"127.0.0.1"}')
        dynamic = rehearsal.seal_inputs([entry, settings], artifacts / 'dynamic-inputs.json')
        entry.write_text('export default { mutated: true }')
        with self.assertRaisesRegex(ValueError, 'sealed input drift'):
            rehearsal.verify_sealed_inputs(dynamic)

    def test_private_nginx_and_proof_must_bind_only_ipv4_loopback(self):
        settings_path = self.files['settings']
        baseline = json.loads(settings_path.read_text())
        for field, value in (('listen_host', '0.0.0.0'), ('listen_host', '::'),
                             ('proof_connect_host', '0.0.0.0')):
            settings_path.write_text(json.dumps({**baseline, field: value}))
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'private user rehearsal'):
                load_plan(dict(self.raw))
        settings_path.write_text(json.dumps(baseline))

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
        self.assertIn("cacheDir: '" + str(Path(plan['artifact_dir']) / 'vite-cache') + "'", text)
        self.assertNotIn("cacheDir: '" + str(Path(plan['source_checkout']) / 'frontend' / 'node_modules' / '.vite') + "'", text)

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

    def test_oversized_probe_sends_only_headers_and_reliably_receives_413(self):
        from loadtest.frontend_fixture import FixtureServer
        config = {**self.raw['fixture'], 'allowed_origins': ['http://axioma-ia.io:49001'],
                  'max_body_bytes': 2 * 1024 * 1024, 'event_limit': 60}
        fixture = FixtureServer(('127.0.0.1', 0), config).start()
        try:
            port = fixture.server_address[1]
            for _ in range(50):
                result = rehearsal._http_probe(port, 'POST', '/api/auth/login?via=ui',
                    host=f'localhost:{port}', origin='http://axioma-ia.io:49001',
                    declared_length=config['max_body_bytes'] + 1)
                self.assertEqual(result['status'], 413)
            self.assertEqual(len(fixture.observations()), 50)
            self.assertTrue(all(item['request_bytes'] == 0 for item in fixture.observations()))
        finally:
            fixture.close()

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
        entry = artifacts / 'vite.mjs'; entry.write_text('override')
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

        with patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=entry), \
             patch.object(rehearsal, 'write_private_settings', return_value=settings), \
             patch.object(rehearsal, 'run_checked', side_effect=fake_checked), \
             patch.object(rehearsal, 'run_probes', return_value=[]), \
             patch.object(rehearsal, 'bundled_manager', return_value=self.manager_stub()), \
             patch.object(rehearsal, 'assert_owned_vite_capture'), patch.object(rehearsal, 'validate_restored_vite'), \
             self.assertRaisesRegex(RuntimeError, 'apply failed'):
            rehearsal._execute_sealed(plan, self.sealed(plan))
        self.assertTrue(unit.exists())
        self.assertTrue(settings.exists())
        self.assertTrue(entry.exists())
        self.assertFalse(any(cmd[:3] == ['systemctl', '--user', 'stop'] for cmd in calls))
        self.assertIn('rollback: RuntimeError', json.loads((artifacts / 'cleanup.json').read_text())['cleanup_errors'])
        self.assertFalse((artifacts / 'summary.json').exists())

    def test_failure_before_apply_stops_owned_private_vite_without_rollback(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        entry = artifacts / 'vite.mjs'; entry.write_text('override')
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

        with patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=entry), \
             patch.object(rehearsal, 'write_private_settings', return_value=settings), \
             patch.object(rehearsal, 'run_checked', side_effect=fake_checked), \
             patch.object(rehearsal, 'run_probes', side_effect=RuntimeError('pre-switch probe failed')), \
             patch.object(rehearsal, 'bundled_manager', return_value=self.manager_stub()), \
             patch.object(rehearsal, 'assert_owned_vite_capture'), patch.object(rehearsal, 'validate_restored_vite'), \
             patch.object(rehearsal, 'manager_cas'), patch.object(rehearsal, 'stopped_matches', return_value=True), \
             self.assertRaisesRegex(RuntimeError, 'pre-switch probe failed'):
            rehearsal._execute_sealed(plan, self.sealed(plan))
        self.assertFalse(unit.exists())
        self.assertTrue(settings.exists())
        self.assertTrue(entry.exists())
        self.assertFalse(any(len(cmd) > 2 and cmd[2] == 'rollback' for cmd in calls))
        self.assertFalse(unit.exists())

    def test_foreign_post_start_dropin_rejection_preserves_recovery_without_stop(self):
        plan = load_plan(dict(self.raw)); artifacts = Path(plan['artifact_dir'])
        entry = artifacts / 'vite.mjs'; entry.write_text('override')
        settings = artifacts / 'controller-settings.json'; settings.write_text('{}')
        unit = Path(plan['systemd_dir']) / plan['service']; unit.write_text('owned')
        identity = rehearsal._file_identity(unit)
        class Fixture:
            base_url = 'http://127.0.0.1:49003'
            def __init__(self, address, config): self.config = config
            def start(self): return self
            def close(self): pass
        class Manager:
            stopped = False
            def unit_paths(self): return []
            def capture(self, **kwargs):
                return {'properties': {'FragmentPath': str(unit), 'DropInPaths': '/foreign/90-foreign.conf'},
                        'files': {'/foreign/90-foreign.conf': {'sha256': 'x'}}, 'unit_paths': [], 'process': None}
            def coherent(self, capture): pass
            def stop(self): self.stopped = True
            def reload(self): pass
        manager = Manager()
        def checked(command, **kwargs):
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        with patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=entry), \
             patch.object(rehearsal, 'write_private_settings', return_value=settings), \
             patch.object(rehearsal, 'bundled_manager', return_value=(manager, lambda left, right: left == right)), \
             patch.object(rehearsal, 'run_checked', side_effect=checked), \
             self.assertRaisesRegex(ValueError, 'foreign base or drop-in'):
            rehearsal._execute_sealed(plan, self.sealed(plan))
        self.assertFalse(manager.stopped)
        self.assertTrue(unit.exists())
        self.assertTrue(settings.exists())
        self.assertTrue(entry.exists())
        self.assertTrue((artifacts / 'incomplete-receipt.json').is_file())

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
        override = artifacts / 'vite.mjs'; override.write_text('override')
        (artifacts / 'controller-settings.json').write_text('{}')

        def fake_checked(command, **kwargs):
            calls.append(command)
            if command[0] == plan['k6_binary']:
                run_file = Path(next(item.split('=', 1)[1] for item in command if item.startswith('P100_RUN_CONFIG=')))
                summary = Path(json.loads(run_file.read_text())['summary_file'])
                summary.write_text('{}')
            return type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with patch.object(rehearsal, 'FixtureServer', Fixture), \
             patch.object(rehearsal, 'write_unit', return_value=(unit, identity)), \
             patch.object(rehearsal, 'write_private_vite_entry', return_value=override), \
             patch.object(rehearsal, 'write_private_settings', return_value=artifacts / 'controller-settings.json'), \
             patch.object(rehearsal, 'run_checked', side_effect=fake_checked), \
             patch.object(rehearsal, 'run_probes', return_value=[]), \
             patch.object(rehearsal, 'bundled_manager', return_value=self.manager_stub()), \
             patch.object(rehearsal, 'assert_owned_vite_capture'), \
             patch.object(rehearsal, 'manager_cas'), patch.object(rehearsal, 'stopped_matches', return_value=True), \
             patch.object(rehearsal, 'validate_restored_vite'), \
             patch.object(rehearsal, 'summarize', return_value={'ok': True}):
            rehearsal._execute_sealed(plan, self.sealed(plan))
        browser_positions = [i for i, call in enumerate(calls) if isinstance(call, list) and call[0] == plan['node_binary']]
        apply_position = next(i for i, call in enumerate(calls) if isinstance(call, list) and len(call) > 2 and call[2] == 'apply')
        k6_positions = [i for i, call in enumerate(calls) if isinstance(call, list) and call[0] == plan['k6_binary']]
        self.assertEqual(len(k6_positions), 40)
        self.assertLess(browser_positions[0], min(k6_positions[:20]))
        self.assertLess(max(k6_positions[:20]), apply_position)
        self.assertLess(apply_position, browser_positions[1])
        self.assertLess(browser_positions[1], min(k6_positions[20:]))
        rollback = next(i for i, call in enumerate(calls) if isinstance(call, list) and len(call) > 2 and call[2] == 'rollback')
        self.assertLess(max(k6_positions), rollback)
        summary = json.loads((artifacts / 'summary.json').read_text())
        receipt = json.loads((artifacts / 'terminal-receipt.json').read_text())
        self.assertEqual(receipt['summary_sha256'], hashlib.sha256((artifacts / 'summary.json').read_bytes()).hexdigest())
        self.assertEqual(receipt['cleanup_sha256'], hashlib.sha256((artifacts / 'cleanup.json').read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
