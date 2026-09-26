"""Credenciales para los scripts de carga (`loadtest/`) -- SIEMPRE
explícitas por variable de entorno, nunca leyendo `/etc/jax/.env` con sudo.

Retirado 2026-09-25 (mismo día, mismo motivo que `backend/tests/entorno_de_test.py`,
que reemplazó a `backend/tests/entorno_de_produccion.py`): cada script de
este directorio tenía su PROPIA copia de `_cargar_env_produccion()`
(`subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"])`) -- 9 copias del
mismo mecanismo, fuera del código de servicio, leyendo TODO el `.env` de
producción (contraseña de la base, JWT, Fernet, token de Telegram) en cada
corrida de un script de MEDICIÓN de carga.

Reemplazo: el operador arma el entorno del shell ANTES de correr un script
de `loadtest/`, con el MISMO archivo de credenciales de PRUEBA que usa la
suite de pytest del backend:

    set -a; . ~/.config/jax/test-db.env; set +a
    python3 loadtest/historial_seed.py loadtest/_seed_result.json

Sin esas variables, `credenciales_de_base_de_prueba()` falla con un mensaje
que dice exactamente qué falta y cómo cargarlo -- nunca una caída silenciosa
a un default de producción.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import os

#: Igual criterio que `backend/tests/entorno_de_test.py::CLAVES_PERMITIDAS`:
#: sólo credenciales de conexión a la base de PRUEBA. Ningún secreto de
#: sesión (JWT, Fernet, tokens de servicio) se lee así -- ver
#: `secreto_de_produccion_para_comparar` para el único caso legítimo
#: distinto, y por qué es distinto.
CLAVES_DB = ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD")


class CredencialDeCargaFaltante(RuntimeError):
    """Falta una variable de entorno y no hay default silencioso."""


def credenciales_de_base_de_prueba() -> dict:
    """Las credenciales de conexión a la base de PRUEBA, tomadas del
    entorno de ESTE proceso -- nunca leídas por este módulo desde ningún
    archivo ni con `sudo`. El operador las carga antes de invocar el script
    (ver el docstring del módulo). Sin todas, `CredencialDeCargaFaltante`
    con el nombre de cada una que falta y cómo resolverlo -- fail-closed,
    nunca una caída silenciosa a un valor de producción."""
    faltantes = [k for k in CLAVES_DB if not os.environ.get(k)]
    if faltantes:
        raise CredencialDeCargaFaltante(
            "faltan " + ", ".join(faltantes) + " en el entorno de este proceso. "
            "Cargalas con `set -a; . ~/.config/jax/test-db.env; set +a` "
            "(el mismo archivo que usa la suite de pytest del backend, ver "
            "backend/tests/entorno_de_test.py) antes de correr este script."
        )
    return {k: os.environ[k] for k in CLAVES_DB}


def secreto_de_produccion_para_comparar(variable: str) -> str | None:
    """Un secreto de PRODUCCIÓN, para una comparación de seguridad -- NUNCA
    para firmar ni para usar como credencial real (ver
    `memoria_levantar_entorno.py::abortar_si_el_secreto_de_carga_coincide_con_produccion`,
    que varios de estos scripts reusan para no correr un backend de carga
    que pudiera emitir tokens también válidos contra producción).

    Nunca se lee con `sudo` ni de `/etc/jax/.env`: el operador lo exporta a
    mano, una sola vez, sólo si quiere que la comparación se ejecute
    (`export JAX_JWT_SECRET_DE_PRODUCCION=...`, obtenido por su cuenta con
    las herramientas que ya tiene fuera de este repo). Ausente -> `None`:
    el llamador decide qué hacer -- hoy, avisar y seguir sin la comparación
    extra, nunca fallar cerrado sólo por esto. La barrera de fondo ya no
    depende de esta comparación: desde este mismo cambio, el backend de
    carga deja de copiar `/etc/jax/.env` entero a su propio entorno, así
    que ya no puede heredar el secreto de producción por accidente aunque
    esta comparación no se ejecute."""
    return os.environ.get(variable) or None
