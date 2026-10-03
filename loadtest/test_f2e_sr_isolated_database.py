import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).with_name("f2e_sr_isolated_database.py")
    spec = importlib.util.spec_from_file_location("f2e_sr_isolated_database", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_schema_loader_removes_only_the_jax_database_selector(tmp_path):
    mod = _module()
    schema = tmp_path / "jax_memory_schema.sql"
    schema.write_text("CREATE DATABASE IF NOT EXISTS jax_memory CHARACTER SET utf8mb4;\nUSE jax_memory;\nCREATE TABLE `x` (`id` INT);\n")
    assert mod._jax_schema_sql(tmp_path) == "CREATE TABLE `x` (`id` INT);\n"


def test_pristine_guard_refuses_existing_credential_file(tmp_path, monkeypatch):
    mod = _module()
    credentials = tmp_path / "already-there"
    credentials.write_text("not a credential")
    monkeypatch.setattr(mod, "_port_is_free", lambda _port: True)
    monkeypatch.setattr(mod, "_docker_exists", lambda _kind, _name: False)
    monkeypatch.setattr(mod, "_run", lambda *_args, **_kwargs: type("Result", (), {"returncode": 0, "stdout": mod.IMAGE_ID})())
    with pytest.raises(mod.BootstrapRefused, match="credential file"):
        mod.assert_pristine(env_file=credentials)


def test_constants_pin_an_isolated_loopback_database_contract():
    mod = _module()
    assert mod.PORT == 13338
    assert mod.DATABASE != "jax_memory"
    assert mod.CONTAINER == "axioma-f2esr-load-20261003"
