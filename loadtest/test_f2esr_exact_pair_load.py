import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).with_name("f2esr_exact_pair_load.py")
    spec = importlib.util.spec_from_file_location("f2esr", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_harness_keeps_full_concurrency_and_warmup_contract():
    mod = _module()
    assert mod.NIVELES_DE_CONCURRENCIA == [1, 5, 10, 25, 50]
    assert mod.MAX_TURNS == mod.CALENTAMIENTO_TURNOS == 20
    assert mod.siembra.N_USUARIOS == 50 and mod.siembra.N_RELLENO == 5000
    assert mod.PETICIONES_POR_NIVEL == {1: 100, 5: 150, 10: 200, 25: 375, 50: 600}


def test_isolated_env_rejects_production_db(tmp_path):
    mod = _module()
    env = {"JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "3308", "JAX_DB_USER": "u", "JAX_DB_PASSWORD": "p", "JAX_DB_NAME": "jax_memory"}
    try:
        mod.construir_env(tmp_path, env, tmp_path, tmp_path)
    except RuntimeError:
        pass
    else:
        raise AssertionError("production database was accepted")


def test_closed_db_file_rejects_foreign_keys_and_accepts_only_fixed_target(tmp_path):
    mod = _module()
    path = tmp_path / "db.env"
    path.write_text("JAX_DB_HOST=127.0.0.1\nJAX_DB_PORT=13338\nJAX_DB_USER=u\n"
                    "JAX_DB_PASSWORD=p\nJAX_DB_NAME=jax_memory_test_f2esr\n")
    path.chmod(0o600)
    assert mod._read_env_file(path)["JAX_DB_NAME"] == mod.BASE_DE_PRUEBA
    path.write_text(path.read_text() + "FOREIGN=value\n")
    with pytest.raises(RuntimeError, match="exactly the five"):
        mod._read_env_file(path)


def test_exact_sha_guard_refuses_prefixes_and_dirty_worktree(tmp_path):
    mod = _module()
    assert not mod.SHA_COMPLETO.fullmatch("a" * 39)
    assert mod.SHA_COMPLETO.fullmatch("a" * 40)


def test_pair_and_payload_contract_are_closed():
    mod = _module()
    assert mod.PARES_GOBERNADOS == {
        "f2d": ("f2-c.renderer.2", "f2-c.domain.2"),
        "production-baseline": ("f2-c.renderer.2", "f2-c.domain.2"),
        "sr": ("f2-c.renderer.3", "f2-c.domain.5"),
    }
    env = mod.construir_env(Path("/tmp"), {
        "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "13338", "JAX_DB_USER": "u",
        "JAX_DB_PASSWORD": "p", "JAX_DB_NAME": "jax_memory_test_f2esr",
    }, Path("/tmp"), Path("/tmp"), respuesta_chars=8000)
    assert env["CARGA_RESPUESTA_CHARS"] == "8000"
