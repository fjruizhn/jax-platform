"""The first Office owner must come from another physical MariaDB."""
import asyncio
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "ops/virtualoffice/import-owner.py"
spec = importlib.util.spec_from_file_location("office_owner_import_test", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IdentityConnection:
    def __init__(self, identity):
        self.identity = identity

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql):
        assert sql == "SELECT @@server_id,@@hostname,DATABASE()"

    async def fetchone(self):
        return self.identity


def test_owner_import_rejects_reused_server_id_even_with_different_hostname():
    source = IdentityConnection((17, "jax-main", "jax_memory"))
    office = IdentityConnection((17, "axioma-office-db", "axioma_office"))
    with pytest.raises(RuntimeError, match="distinct physical MariaDB"):
        asyncio.run(module._assert_distinct_servers(source, office, {
            "OFFICE_DB_SERVER_ID": "17", "OFFICE_DB_HOSTNAME": "axioma-office-db",
            "JAX_DB_NAME": "axioma_office",
        }))
