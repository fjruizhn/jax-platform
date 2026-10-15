import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest


MODULE = Path(__file__).resolve().parents[1] / "switch.py"
spec = importlib.util.spec_from_file_location("frontend_switch", MODULE)
switch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(switch)


def command(*argv):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class ReleaseValidationTest(unittest.TestCase):
    def test_manifest_detects_mutated_index(self):
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / ("a" * 40)
            dist = release / "dist"
            dist.mkdir(parents=True)
            index = dist / "index.html"
            index.write_bytes(b"expected")
            (release / "manifest.json").write_text(json.dumps({
                "source_sha": release.name,
                "files": {"index.html": hashlib.sha256(b"expected").hexdigest()},
            }))
            self.assertEqual(switch.release_index(release), index)
            index.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                switch.release_index(release)


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("nginx") and shutil.which("systemctl"),
                     "isolated systemd integration requires root, nginx and systemctl")
class IsolatedSystemdTest(unittest.TestCase):
    """Uses a disposable unit under /run; never changes the production unit."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="p100-systemd-")
        cls.root = Path(cls.temp.name)
        cls.root.chmod(0o755)
        cls.unit = f"jax-p100-test-{os.getpid()}.service"
        cls.unit_path = Path("/run/systemd/system") / cls.unit
        cls.dropin_dir = Path("/run/systemd/system") / f"{cls.unit}.d"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}/"
        class Backend(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (f"{self.path}|{self.headers.get('Host')}|"
                        f"{self.headers.get('Origin')}|{self.headers.get('Upgrade')}").encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        cls.backend_host = f"127.0.0.1:{cls.backend.server_port}"
        cls.backend_thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.backend_thread.start()
        base = cls.root / "base"
        base.mkdir(mode=0o755)
        (base / "index.html").write_bytes(b"original-vite-mode")
        (base / "index.html").chmod(0o644)
        cls.release = cls.root / ("a" * 40)
        dist = cls.release / "dist"
        dist.mkdir(parents=True, mode=0o755)
        index = dist / "index.html"
        index.write_bytes(b"<html>static-release</html>")
        index.chmod(0o644)
        assets = dist / "assets"
        assets.mkdir(mode=0o755)
        (assets / "index-12345678.js").write_bytes(b"const release = 1;")
        (assets / "plain.js").write_bytes(b"const plain = 1;")
        (cls.release / "manifest.json").write_text(json.dumps({
            "source_sha": cls.release.name,
            "files": {path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in dist.rglob("*") if path.is_file()},
        }))
        for path in (cls.release, dist, assets):
            path.chmod(0o755)
        user = os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
        cls.unit_path.write_text(f"""[Unit]
Description=Disposable P100 test service
[Service]
Type=simple
User={user}
ExecStart=/usr/bin/python3 -m http.server {cls.port} --bind 127.0.0.1 --directory {base}
Restart=no
""")
        command("systemctl", "daemon-reload")
        command("systemctl", "start", cls.unit)
        cls.wait_for(b"original-vite-mode")

    @classmethod
    def tearDownClass(cls):
        command("systemctl", "stop", cls.unit)
        cls.unit_path.unlink(missing_ok=True)
        (cls.dropin_dir / "zz-static.conf").unlink(missing_ok=True)
        (cls.dropin_dir / "static-nginx.nginx").unlink(missing_ok=True)
        if cls.dropin_dir.exists():
            cls.dropin_dir.rmdir()
        command("systemctl", "daemon-reload")
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.backend_thread.join(timeout=2)
        cls.temp.cleanup()

    @classmethod
    def wait_for(cls, expected):
        for _ in range(30):
            result = subprocess.run(["curl", "-fsS", "--max-time", "1", cls.url],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if result.returncode == 0 and result.stdout == expected:
                return
            import time
            time.sleep(0.1)
        raise AssertionError(f"service did not return {expected!r}")

    def test_rollback_ignores_process_boot_and_checkout_state(self):
        switch.apply(self.release, self.dropin_dir, self.port, self.backend_host,
                     self.url, self.unit)
        self.wait_for(b"<html>static-release</html>")
        self.assertIn(b"Cache-Control: no-cache", command("curl", "-fsSI", self.url).stdout)
        self.assertIn(b"Cache-Control: no-cache", command("curl", "-fsSI", self.url + "index.html").stdout)
        self.assertEqual(command("curl", "-fsS", self.url + "client/route").stdout,
                         b"<html>static-release</html>")
        self.assertIn(b"immutable", command("curl", "-fsSI", self.url + "assets/index-12345678.js").stdout)
        self.assertNotIn(b"immutable", command("curl", "-fsSI", self.url + "assets/plain.js").stdout)
        self.assertEqual(subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                                         self.url + "assets/missing.js"], capture_output=True).stdout, b"404")
        self.assertEqual(command("curl", "-fsS", "-H", "Origin: https://axioma-ia.io",
                                 self.url + "api/ping?x=1").stdout,
                         f"/api/ping?x=1|{self.backend_host}|https://axioma-ia.io|None".encode())
        self.assertEqual(command("curl", "-fsS", "-H", "Upgrade: websocket",
                                 self.url + "ws?x=1").stdout,
                         f"/ws?x=1|{self.backend_host}|None|websocket".encode())
        self.assertNotEqual(subprocess.run(["curl", "-fsS", "-H", "Host: invalid.example",
                                           self.url], capture_output=True).returncode, 0)

        # A new nginx PID must not invalidate rollback.
        command("systemctl", "restart", self.unit)
        self.wait_for(b"<html>static-release</html>")

        # A changed checkout SHA and boot marker have no bearing on recovery.
        (self.root / "checkout-sha").write_text("changed-after-git-pull")
        (self.root / "boot-id").write_text("simulated-new-boot")
        command("systemctl", "stop", self.unit)
        switch.rollback(self.dropin_dir, self.unit)
        self.wait_for(b"original-vite-mode")

        # Failed units are reset before the same unconditional restart.
        switch.apply(self.release, self.dropin_dir, self.port, self.backend_host,
                     self.url, self.unit)
        command("systemctl", "kill", "--signal=SIGKILL", "--kill-who=main", self.unit)
        self.assertEqual(command("systemctl", "show", "--property=ActiveState", "--value",
                                 self.unit).stdout.strip(), b"failed")
        switch.rollback(self.dropin_dir, self.unit)
        self.wait_for(b"original-vite-mode")


if __name__ == "__main__":
    unittest.main()
