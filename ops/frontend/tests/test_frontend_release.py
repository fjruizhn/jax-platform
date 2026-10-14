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



if __name__ == "__main__":
    unittest.main()
