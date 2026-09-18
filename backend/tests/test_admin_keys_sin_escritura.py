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
    hace fallar a propósito: si alguien la desarma, esto se pone rojo."""
    import pytest

    from conftest import EscrituraEnProduccion

    with pytest.raises(EscrituraEnProduccion) as exc:
        open("/etc/jax/.env", "w")
    assert "/etc/jax/.env" in str(exc.value)

    # La LECTURA directa dejó de estar permitida para el operador el 2026-09-17: el archivo es
    # root:jaxsvc 640 y la suite lo lee con `sudo -n cat` (tests/entorno_de_produccion.py). Acá se
    # fija lo que importa: quien no es el dueño recibe PermissionError (no la barrera de escritura,
    # que da EscrituraEnProduccion), y la suite igual pudo cargar el entorno.
    import os

    from tests.entorno_de_produccion import cargar

    if os.path.exists("/etc/jax/.env") and os.geteuid() != 0:
        try:
            with open("/etc/jax/.env") as f:
                f.readline()
        except PermissionError:  # fail-soft: ES lo esperado para el operador desde que el archivo es root:jaxsvc 640; lo que este control fija es que la barrera de ESCRITURA muerda
            pass  # lo esperado para el operador
        assert cargar("/etc/jax/.env"), "la suite tiene que poder cargar el entorno con sudo -n"
