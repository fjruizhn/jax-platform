"""Las migraciones y los ajustes no dependen del secreto de firma (2026-09-17).

Defecto: el frente C (#91) hizo `db/migrations.py -> import ajustes ->
from auth.jwt import ACCESS_EXPIRE_SECONDS`, y `auth/jwt.py` exige
JAX_JWT_SECRET AL IMPORTARSE. El job `jacobs-gobernanza-db` de jax corre
`from db.migrations import run_migrations` sin ese secreto (no firma nada) y
quedo en rojo (run 35195896499).

Principio: el secreto se exige donde se firma y verifica. La vida de los
tokens vive en `auth/constantes.py`, sin efectos al importarse. El fail-closed
de la app se conserva: `uvicorn main:app` importa `main`, que importa
`auth.jwt`, asi que sin secreto el servicio NO arranca (no falla recien en la
primera request). Los tests corren en subproceso: un modulo ya importado en
este proceso no volveria a ejecutar su chequeo."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
MENSAJE = "JAX_JWT_SECRET no configurada"

# El env del job de jax: solo la DB y PYTHONPATH (policy.yml de jax,
# job jacobs-gobernanza-db). Sin JAX_JWT_SECRET y sin /etc/jax/.env.
ENTORNO_DEL_JOB_DE_JAX = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "JAX_DB_HOST": "127.0.0.1",
    "JAX_DB_PORT": "3306",
    "JAX_DB_USER": "root",
    "JAX_DB_PASSWORD": "ci",
    "JAX_DB_NAME": "jax_memory_test",
    "PYTHONPATH": ".:las_manos",
}


def _importar(modulo: str, entorno: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", f"import {modulo}"], cwd=BACKEND, env=entorno,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("modulo", ["db.migrations", "ajustes", "auth.constantes"])
def test_se_importa_sin_el_secreto_con_el_entorno_del_job_de_jax(modulo):
    r = _importar(modulo, ENTORNO_DEL_JOB_DE_JAX)
    assert r.returncode == 0, r.stderr[-2000:]


def _entorno_sin_secreto() -> dict:
    return {k: v for k, v in os.environ.items() if k != "JAX_JWT_SECRET"}


def test_auth_jwt_sin_el_secreto_sigue_sin_importarse():
    r = _importar("auth.jwt", _entorno_sin_secreto())
    assert r.returncode != 0
    assert MENSAJE in r.stderr, r.stderr[-2000:]


def test_la_app_sin_el_secreto_no_arranca():
    # `main` es lo que carga `uvicorn main:app` (ExecStart de jax-platform):
    # si el import falla, el servicio no arranca.
    r = _importar("main", _entorno_sin_secreto())
    assert r.returncode != 0
    assert MENSAJE in r.stderr, r.stderr[-2000:]


def test_la_vida_de_los_tokens_es_una_sola():
    import auth.constantes as constantes
    entorno = {**os.environ, "JAX_JWT_SECRET": os.environ.get("JAX_JWT_SECRET") or "prueba"}
    r = subprocess.run(
        [sys.executable, "-c",
         "import auth.jwt, ajustes, auth.constantes as c; "
         "assert auth.jwt.ACCESS_EXPIRE_SECONDS is c.ACCESS_EXPIRE_SECONDS; "
         "assert ajustes.SESION_MIN == c.ACCESS_EXPIRE_SECONDS // 60"],
        cwd=BACKEND, env=entorno, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert constantes.ACCESS_EXPIRE_SECONDS == 15 * 60
