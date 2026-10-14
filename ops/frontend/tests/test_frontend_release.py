"""Behavioral contract tests for the isolated frontend release tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
import http.server
import socket
import threading
import urllib.request


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "ops/frontend"))


class FrontendReleaseContractTests(unittest.TestCase):
    def test_settings_hold_only_declared_deployment_defaults(self) -> None:
        settings = json.loads((REPO / "ops/frontend/settings.json").read_text())
        self.assertEqual(settings["service"], "jax-platform-frontend.service")
        self.assertEqual(settings["release_root"], "/srv/jax-platform-frontend")
        self.assertEqual(settings["node_version"], "24.16.0")
        self.assertEqual(settings["npm_version"], "11.13.0")

    def test_renderer_preserves_vite_prefix_proxy_and_static_cache_contract(self) -> None:
        from frontend_release import render_nginx

        config = render_nginx(
            release_root="/private/releases",
            release_sha="a" * 40,
            runtime_directory="/private/runtime",
            proxy_contract={
                "api_prefix": "/api",
                "ws_prefix": "/ws",
                "target_host": "localhost",
                "target_port": 8080,
                "allowed_hosts": ["axioma-ia.io", "www.axioma-ia.io"],
            },
        )
        self.assertIn("location ^~ /api {", config)
        self.assertIn("location ^~ /ws {", config)
        self.assertIn("proxy_pass http://127.0.0.1:8080;", config)
        self.assertIn("proxy_set_header Host localhost:8080;", config)
        self.assertIn("proxy_set_header Origin $http_origin;", config)
        self.assertIn("location ^~ /assets/ {", config)
        self.assertIn("Cache-Control \"public, max-age=31536000, immutable\"", config)
        self.assertIn("location = /index.html {", config)
        self.assertIn("Cache-Control \"no-cache\"", config)
        self.assertIn("X-JAX-Frontend-SHA", config)
        self.assertNotIn("X-Forwarded-For", config)
        self.assertIn("return 404", config)

    def test_build_rejects_dirty_checkout_before_publishing(self) -> None:
        tool = REPO / "ops/frontend/build_release.py"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            result = subprocess.run(
                ["python3", str(tool), "--repo", str(REPO), "--sha", "0" * 40, "--output", str(output)],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_render_writes_only_managed_files_in_private_prepare_mode(self) -> None:
        tool = REPO / "ops/frontend/frontend_release.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = root / "settings.json"
            settings.write_text(json.dumps({
                "service": "private-frontend.service",
                "release_root": str(root / "releases"),
                "nginx_root": str(root / "nginx"),
                "systemd_dir": str(root / "systemd"),
                "dropin_name": "zz-nginx-estatico.conf",
                "runtime_directory": str(root / "runtime"),
            }))
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "source_sha": "b" * 40,
                "proxy_contract": {
                    "api_prefix": "/api", "ws_prefix": "/ws", "target_host": "localhost",
                    "target_port": 8080, "allowed_hosts": ["localhost"],
                },
            }))
            result = subprocess.run(
                ["python3", str(tool), "render", "--settings", str(settings), "--manifest", str(manifest)],
                text=True,
                capture_output=True,
                check=True,
            )
            rendered = json.loads(result.stdout)
            nginx = Path(rendered["nginx_config"])
            dropin = Path(rendered["dropin"])
            self.assertTrue(nginx.is_file())
            self.assertTrue(dropin.is_file())
            self.assertIn("ExecStart=", dropin.read_text())
            self.assertTrue(dropin.is_relative_to(root / "systemd"))

    def test_pool_rejects_same_asset_name_with_different_bytes(self) -> None:
        from frontend_release import update_asset_pool

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"; second = root / "second"
            (first / "assets").mkdir(parents=True); (second / "assets").mkdir(parents=True)
            (first / "assets" / "app-abc.js").write_bytes(b"first")
            (second / "assets" / "app-abc.js").write_bytes(b"second")
            update_asset_pool(first, root / "pool")
            with self.assertRaisesRegex(ValueError, "collision"):
                update_asset_pool(second, root / "pool")

    def test_snapshot_and_restore_recover_managed_files_exactly(self) -> None:
        from frontend_release import restore_snapshot, snapshot_managed

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); managed = root / "managed.conf"
            managed.write_text("vite")
            snapshot = snapshot_managed([managed])
            managed.write_text("nginx")
            restore_snapshot(snapshot)
            self.assertEqual(managed.read_text(), "vite")

    def test_nginx_serves_release_and_proxies_prefixes_in_private_processes(self) -> None:
        from frontend_release import render_nginx
        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
                self.wfile.write((self.path + "|" + self.headers["Host"]).encode())
            def log_message(self, *args): pass
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); release = root / "release"; (release / "current/dist").mkdir(parents=True); (release / "pool/assets").mkdir(parents=True); (root / "run").mkdir()
            (release / "current/dist/index.html").write_text("SPA")
            (release / "pool/assets/app-12345678.js").write_text("console.log(1)")
            with socket.socket() as listener: listener.bind(("127.0.0.1", 0)); upstream_port = listener.getsockname()[1]
            server = http.server.ThreadingHTTPServer(("127.0.0.1", upstream_port), Upstream); thread = threading.Thread(target=server.serve_forever); thread.start()
            with socket.socket() as listener: listener.bind(("127.0.0.1", 0)); nginx_port = listener.getsockname()[1]
            config = root / "nginx.conf"
            config.write_text(render_nginx(release_root=str(release), release_sha="c" * 40, runtime_directory=str(root / "run"), listen=nginx_port, proxy_contract={"api_prefix":"/api","ws_prefix":"/ws","target_host":"localhost","target_port":upstream_port,"allowed_hosts":["localhost"]}))
            process = subprocess.Popen(["nginx", "-c", str(config), "-p", str(root)])
            try:
                for _ in range(20):
                    try: response = urllib.request.urlopen(f"http://localhost:{nginx_port}/api/path?q=1"); break
                    except OSError: import time; time.sleep(.05)
                self.assertEqual(response.read().decode(), f"/api/path?q=1|localhost:{upstream_port}")
                asset = urllib.request.urlopen(f"http://localhost:{nginx_port}/assets/app-12345678.js")
                self.assertEqual(asset.headers["Cache-Control"], "public, max-age=31536000, immutable")
                self.assertEqual(asset.headers["Content-Type"].split(";")[0], "application/javascript")
                with self.assertRaises(urllib.error.HTTPError) as missing: urllib.request.urlopen(f"http://localhost:{nginx_port}/assets/missing-12345678.js")
                self.assertEqual(missing.exception.code, 404)
                missing.exception.close()
            finally:
                process.terminate(); process.wait(); server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
