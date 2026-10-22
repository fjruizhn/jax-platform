"""Root MariaDB execution contract for the invitation delivery routines."""
from __future__ import annotations
import os, subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(os.getenv("JAX_DB_USER") != "root", reason="requires disposable root MariaDB")

def _install_delivery_sql() -> None:
    db=os.environ["JAX_DB_NAME"]; env={**os.environ, "MYSQL_PWD": os.environ["JAX_DB_PASSWORD"]}
    sql=Path(__file__).parents[2]/"ops/virtualoffice/office-invitation-delivery-v1.sql"
    payload=(f"CREATE USER IF NOT EXISTS `jaxoffice_invitation_owner`@`localhost` ACCOUNT LOCK;\nUSE `{db}`;\n"+sql.read_text()).encode()
    subprocess.run(["mysql","--protocol=TCP","-h",os.environ["JAX_DB_HOST"],"-P",os.environ["JAX_DB_PORT"],"-u","root"],input=payload,env=env,check=True,capture_output=True)

def test_delivery_routines_install_on_disposable_root_db(client):
    _install_delivery_sql()
    assert client is not None
