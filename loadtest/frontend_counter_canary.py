"""PREP-only cancellation canary: one VU, one owned slow loopback server, no app.

Synthetic a/b SHA labels deliberately cannot enter the real matrix summary.
The server blocks /login beyond duration1+grace1; it never authenticates anyone.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


def run_canary(k6_binary, script, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            requests.append({'method': 'GET', 'path': self.path.split('?')[0]})
            if not stop.wait(5):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<div id="root"></div><script type="module"></script>')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    if port in (8080, 5173):
        server.server_close()
        raise ValueError('ephemeral canary selected forbidden port')
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='p100-counter-canary-', dir='/tmp') as private:
            private = Path(private)
            resources = [{'path': '/synthetic-never-requested.js', 'type': 'script',
                          'mime': 'text/javascript', 'bytes': 20, 'sha256': 'c' * 64}]
            # JSON canonicalization here matches JS JSON.stringify(resources).
            import hashlib
            digest = hashlib.sha256(json.dumps(resources, separators=(',', ':')).encode()).hexdigest()
            discovery = {'variant': 'synthetic-counter-canary', 'source_sha': 'a' * 40,
                         'harness_sha': 'b' * 40, 'resource_digest': digest, 'resources': resources}
            resources_file = private / 'resources.json'
            resources_file.write_text(json.dumps(discovery))
            summary_file = output_dir / 'prep-counter-canary-v2-run.json'
            config = {'variant': 'synthetic-counter-canary', 'synthetic_counter_canary': True,
                      'vus': 1, 'duration_seconds': 1, 'graceful_stop_seconds': 1,
                      'repetition': 1, 'warmup': False, 'source_sha': 'a' * 40,
                      'harness_sha': 'b' * 40, 'resources_file': str(resources_file),
                      'summary_file': str(summary_file), 'base_url': f'http://axioma-ia.io:{port}',
                      'allowed_ports': [port], 'fixture_credentials': {
                          'email': 'canary@example.invalid', 'password': 'synthetic-never-submitted'},
                      'marker': 'synthetic-counter-canary'}
            run_file = private / 'run.json'
            run_file.write_text(json.dumps(config))
            empty_config = private / 'k6.json'
            empty_config.write_text('{}\n')
            # No K6_* config, proxy settings or credentials inherited from the human environment.
            child_env = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8'}
            command = [str(k6_binary), 'run', '--config', str(empty_config),
                       '--include-system-env-vars=false', '--no-usage-report',
                       '--new-machine-readable-summary=false', '--address', '',
                       '--env', f'P100_RUN_CONFIG={run_file}', str(script)]
            process = subprocess.run(command, env=child_env, capture_output=True, text=True, timeout=15)
            (output_dir / 'prep-counter-canary-v2-k6.txt').write_text(process.stdout + process.stderr)
            if process.returncode not in (0, 99):
                raise AssertionError(f'canary k6 failed before accounting: rc={process.returncode}')
            raw = json.loads(summary_file.read_text())
            summary_file.chmod(0o600)
            counts = raw['navigation_counts']
            assert raw['schema'] == 'p100-k6-run-v2' and raw['synthetic_counter_canary'] is True
            assert counts['started'] > 0 and counts['successful'] == 0 and counts['failed'] == 0
            assert counts['incomplete'] == counts['started']
            assert raw['navigation_p95_ms'] is None and raw['completed_only_navigation_p95_ms'] is None
            assert raw['incomplete_cause'] == 'scenario_deadline' and raw['latency_censored'] is True
            assert raw['latency_sample_count'] == 0
            assert len(requests) == 1 and requests[0]['path'] == '/login'
            result = {'schema': 'p100-counter-canary-proof-v2', 'ok': True,
                      'scope': 'synthetic slow owned loopback server; no frontend/controller/backend/unit/load matrix',
                      'synthetic_sha_labels_not_measurement': True, 'k6_returncode': process.returncode,
                      'navigation_counts': counts, 'navigation_p95_ms': None,
                      'incomplete_cause': raw['incomplete_cause'], 'requests': requests,
                      'duration_seconds': 1, 'graceful_stop_seconds': 1,
                      'observed_duration_ms': raw['observed_duration_ms'],
                      'wall_seconds': round(time.monotonic() - started, 3)}
            (output_dir / 'prep-counter-canary-v2.json').write_text(json.dumps(result, indent=2) + '\n')
            return result
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--k6-binary', type=Path, required=True)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_canary(args.k6_binary.resolve(), args.script.resolve(), args.output_dir.resolve())))


if __name__ == '__main__':
    main()
