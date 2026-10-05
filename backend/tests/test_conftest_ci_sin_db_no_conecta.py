"""Bajo `JAX_CI_NO_DB=1` la suite no lee `/etc/jax/.env` ni abre conexiones directas.

`JAX_CI_NO_DB=1` promete "este runner no tiene MariaDB". Pero en una maquina con
`/etc/jax/.env` (hall9000) el conftest cargaba credenciales reales, y `facet_resolver._db_conn`
y `credential_resolver._db_conn` llaman a `aiomysql.connect` directo (sin pasar por
`create_pool`, que es lo unico que el modo interceptaba): con el placeholder 127.0.0.1:3308 eso es
una conexion de verdad a la instancia de produccion, con credenciales reales.

Los dos controles corren en un PROCESO APARTE, igual que
`test_conftest_no_carga_secretos_no_necesarios.py`: lo que se prueba es el conftest en tiempo de
import y sus fixtures autouse, y el proceso de pytest que corre este archivo ya los paso. El
hijo nunca apunta a un puerto de produccion (`JAX_DB_PORT=1`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _entorno_hijo() -> dict:
    env = dict(os.environ)
    for k in ("JAX_DB_HOST", "JAX_DB_NAME", "JAX_TEST_DB_SUFIJO", "CI"):
        env.pop(k, None)
    env.update({"JAX_CI_NO_DB": "1", "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "1",
                "JAX_DB_USER": "nadie", "JAX_DB_PASSWORD": "nada"})
    env.setdefault("JAX_JWT_SECRET", "ci-dummy-not-a-real-secret")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND), env.get("PYTHONPATH", "")]))
    return env


_SONDA_NO_LEE_EL_ENTORNO = """
import tests.entorno_de_produccion as ep
leidas = []
def _cargar_espia(ruta=ep.RUTA, **kw):
    leidas.append(ruta)
    return {}
ep.cargar = _cargar_espia
import tests.conftest  # noqa: F401
print("LEIDAS=" + repr(leidas))
"""


def test_bajo_ci_sin_db_el_conftest_no_lee_el_entorno_de_produccion():
    r = subprocess.run([sys.executable, "-c", _SONDA_NO_LEE_EL_ENTORNO], cwd=BACKEND,
                       env=_entorno_hijo(), capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "LEIDAS=[]", (
        "bajo JAX_CI_NO_DB=1 el conftest leyo /etc/jax/.env: " + r.stdout)


def test_bajo_ci_sin_db_aiomysql_connect_directo_queda_interceptado():
    """`tests/_sonda_conexiones_sin_db.py` (no se colecta sola) llama a los dos `_db_conn`."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:warnings",
         "tests/_sonda_conexiones_sin_db.py"],
        cwd=BACKEND, env=_entorno_hijo(), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2500:] + r.stderr[-1000:]
    assert " 2 passed" in r.stdout, r.stdout[-800:]
