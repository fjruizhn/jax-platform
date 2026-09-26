"""B1.4 (2026-09-17) — el admin viejo de llaves escribia en dos lugares
muertos: la tabla `user_api_keys` y el archivo /etc/jax/.env. El resolvedor
vivo lee `credential`/`provider`, y la pantalla real rota y revoca por
/api/admin/credentials. Es decir: PUT y DELETE /api/admin/keys/{provider}
decian haber cambiado la llave sin cambiar nada, y ademas reintroducian las
env vars que B1.4 retira.

Este control fija el retiro: los dos verbos de escritura ya no existen y el
modulo no escribe /etc/jax/.env. La lectura (GET) y la sonda (POST .../test)
se conservan: la pantalla las usa.
"""
from pathlib import Path

from tests.identidades import cabeceras

BACKEND = Path(__file__).resolve().parent.parent


def _superadmin_headers(client):
    return cabeceras(client, "keys-sin-escritura", "superadmin", "test-keys-sin-escritura")


def test_put_de_llave_ya_no_existe(client):
    resp = client.put(
        "/api/admin/keys/openai",
        json={"api_key": "sk-nueva"},
        headers=_superadmin_headers(client),
    )
    assert resp.status_code in (404, 405), (
        f"PUT /api/admin/keys/openai sigue vivo ({resp.status_code}): "
        "escribe en user_api_keys y en /etc/jax/.env, que ya nadie lee"
    )


def test_delete_de_llave_ya_no_existe(client):
    resp = client.delete(
        "/api/admin/keys/openai",
        headers=_superadmin_headers(client),
    )
    assert resp.status_code in (404, 405), (
        f"DELETE /api/admin/keys/openai sigue vivo ({resp.status_code})"
    )


def test_el_modulo_no_escribe_el_archivo_de_entorno():
    fuente = (BACKEND / "api/admin/keys.py").read_text(encoding="utf-8")
    assert "_write_env_key" not in fuente, (
        "_write_env_key sigue en api/admin/keys.py: reintroduce las *_API_KEY "
        "en /etc/jax/.env despues de que B1.4 las retire"
    )
    assert "open(ENV_PATH, \"w\"" not in fuente and "open(ENV_PATH, 'w'" not in fuente, (
        "api/admin/keys.py sigue abriendo /etc/jax/.env para escritura"
    )


def test_la_barrera_de_escritura_en_produccion_muerde():
    """Una barrera que nunca se ejercita no es una barrera. Este control la
    hace fallar a propósito: si alguien la desarma, esto se pone rojo.

    PASO 0 (2026-09-25): la sección que seguía acá probando que la suite
    podía LEER `/etc/jax/.env` con `sudo -n cat` se retiró junto con
    `tests/entorno_de_produccion.py` -- la suite ya no lee ese archivo bajo
    ninguna circunstancia (ver `tests/entorno_de_test.py` y
    `test_conftest_sin_produccion.py`). Lo que queda acá es sólo la barrera
    de ESCRITURA, que protege un incidente distinto (B1.4) y sigue vigente."""
    import pytest

    from conftest import EscrituraEnProduccion

    with pytest.raises(EscrituraEnProduccion) as exc:
        open("/etc/jax/.env", "w")
    assert "/etc/jax/.env" in str(exc.value)


def test_el_sembrado_legado_de_llaves_no_revienta_sin_permiso(tmp_path, monkeypatch):
    """`/api/admin/keys` leía el .env en caliente: con el archivo del servicio devolvía 500.

    Movido acá desde `tests/test_entorno_de_produccion.py` (PASO 0,
    2026-09-25, al retirar ese módulo): prueba `api/admin/keys.py`, código
    de PRODUCCIÓN ajeno al incidente de carga de credenciales que cierra
    ese PASO -- nunca dependió de `entorno_de_produccion.py`."""
    from api.admin import keys as K

    cerrado = tmp_path / "env-ajeno"
    cerrado.write_text("OPENAI_API_KEY=sk-no-deberia-leerse\n")
    cerrado.chmod(0o000)
    monkeypatch.setattr(K, "ENV_PATH", str(cerrado))
    assert K._load_env() == {}
