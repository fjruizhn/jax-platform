"""La suite no expone secretos de producción que no necesita (2026-09-27).

Historia del archivo -- dos hallazgos reales, en dos rondas de auditoría de la
MISMA sesión (`jax-platform-sync-config`):

1. `tests/conftest.py` cargaba TODO `/etc/jax/.env` con `os.environ.setdefault`
   salvo una LISTA NEGRA de 3 nombres (`CLAUDE_CODE_OAUTH_TOKEN`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Eso paró el primer defecto real
   (`model_catalog._read_anthropic_oauth_token()` usando el token OAuth REAL
   de Fernando en vez del fake que 3 tests monkeypatcheaban), pero una lista
   negra sólo cubre lo que a alguien se le ocurrió nombrar. `FERNET_KEY` y
   `JAX_JWT_SECRET` -- los dos secretos que la aplicación SÍ usa de verdad
   (cifrado de credenciales, firma de sesión) -- nunca estuvieron en esa
   lista, y la suite entera corría con la llave JWT y la Fernet key REALES de
   producción sin que nadie lo hubiera decidido así.

2. Arreglo de raíz (esta ronda): `tests/conftest.py` pasó de lista NEGRA a
   lista BLANCA -- sólo entran de `/etc/jax/.env` las variables que algún
   consumidor real exige sin default (`JAX_DB_HOST`, `JAX_DB_PORT`,
   `JAX_DB_USER`, `JAX_DB_PASSWORD`, `JAX_REPO_PATH`, `JAX_CONFIG_PATH`; ver
   el comentario de `VARIABLES_NECESARIAS_DE_PRODUCCION` en conftest.py para
   la evidencia por variable). `FERNET_KEY`/`JAX_JWT_SECRET` NUNCA se cargan
   de ahí: cada sesión de pruebas genera los suyos.

Mismo patrón que `test_conftest_sin_servicios_de_produccion.py`: corre el
conftest en un PROCESO APARTE -- con `tests.entorno_de_produccion.cargar`
reemplazada por una que devuelve valores fabricados cuando hace falta
determinismo (no depender de qué traiga hoy `/etc/jax/.env`, y correr igual
en CI, donde ese archivo no existe), y SIN reemplazar cuando hace falta el
archivo real (la comparación de hash contra el secreto de producción)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# NUNCA `import tests.conftest` (ni `from tests.conftest import ...`) al
# nivel de módulo de un archivo de test -- hallazgo real (2026-09-27, ronda
# anterior de esta misma sesión): pytest ya cargó `tests/conftest.py` por su
# cuenta (import automático de conftest, con SU propia resolución de nombre
# de módulo); un `import tests.conftest` explícito acá crea una SEGUNDA
# entrada en `sys.modules` bajo un nombre distinto y VUELVE A EJECUTAR TODO
# el código de nivel de módulo de conftest.py -- que reasigna `os.environ`
# con temp-dirs NUEVOS para `JAX_KILL_SWITCH_PATH`, `JAX_ADJUNTOS_DIR`, etc.
# (asignación directa, no `setdefault`). Efecto medido: 48 tests de
# test_kill_switch*.py/test_carril_mesa.py/test_facet_canary_freno.py
# empezaban a fallar SOLO cuando este archivo se colecionaba antes que
# ellos. Todo lo que necesite mirar `tests.conftest` en este archivo lo hace
# DENTRO de una sonda en subproceso (import aislado, proceso descartable) --
# nunca acá arriba. `tests.entorno_de_produccion` sí es seguro de importar
# acá: no tiene efectos de nivel de módulo (ver su propio docstring).
BACKEND = Path(__file__).resolve().parents[1]

# --- Categoría 1: nombres de la lista blanca, sin relación con la conexión
# a la base (para no arriesgar que la sonda no pueda conectar a la base de
# test real). Tienen que llegar al ambiente con el valor que trae
# `/etc/jax/.env`. Sólo `JAX_CONFIG_PATH` con un valor FABRICADO: nadie lo
# toca con sólo `import tests.conftest` (lo exige recién `api/chat.py`, que
# no se importa acá). `JAX_REPO_PATH` NO se fabrica -- `asegurar_base_de_test()`
# lo necesita YA, al nivel de módulo (`db/migrations.py::_jax_b9_migration_root`),
# apuntando a un checkout de jax de verdad; un valor inventado tira la sonda
# entera con `RuntimeError: JAX_REPO_PATH must be absolute` antes de llegar a
# imprimir nada (visto en el primer intento de este test). Se reusa el valor
# REAL ya vigente en este proceso -- sigue probando que el dato viene del
# `cargar()` mockeado (se lo saca de `entorno_limpio` más abajo) sin inventar
# una ruta que no exista.
NOMBRES_LISTA_BLANCA_SIN_DB = ("JAX_CONFIG_PATH",)
_VALOR_REAL_JAX_REPO_PATH = os.environ.get("JAX_REPO_PATH", "")

# --- Categoría 2: secretos ajenos (hallazgo de la ronda anterior) -- tienen
# que quedar en None: ni se cargan del archivo ni tienen generación propia.
NOMBRES_EXCLUIDOS_SIN_GENERACION = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)

# --- Categoría 3: los dos secretos que la app SÍ necesita (hallazgo de ESTA
# ronda) -- no pueden venir del archivo, pero tampoco pueden quedar vacíos:
# conftest.py genera un reemplazo propio de la sesión.
NOMBRES_EXCLUIDOS_CON_GENERACION = ("FERNET_KEY", "JAX_JWT_SECRET")

VALORES_FABRICADOS = {
    nombre: f"valor-de-prueba-{i}"
    for i, nombre in enumerate(
        NOMBRES_LISTA_BLANCA_SIN_DB
        + NOMBRES_EXCLUIDOS_SIN_GENERACION
        + NOMBRES_EXCLUIDOS_CON_GENERACION
    )
}
# JAX_REPO_PATH necesita el valor REAL (ver el comentario de arriba), no uno
# fabricado -- se agrega ACÁ, después del diccionario genérico, para no
# perder la generación automática de los demás nombres.
VALORES_FABRICADOS["JAX_REPO_PATH"] = _VALOR_REAL_JAX_REPO_PATH

_SONDA_LISTA_BLANCA = """
import json, os
import tests.entorno_de_produccion as ep
ep.cargar = lambda ruta=ep.RUTA, **kw: %r
import tests.conftest  # noqa: F401  (ejecuta el filtro real, como en la suite)
print(json.dumps({k: os.environ.get(k) for k in %r}))
""" % (VALORES_FABRICADOS, sorted(VALORES_FABRICADOS))


def test_solo_la_lista_blanca_llega_del_entorno_de_produccion():
    """Con `/etc/jax/.env` fabricado (determinístico, sin depender de qué
    traiga hoy el archivo real): las variables de la lista blanca llegan
    con ESE valor; los secretos ajenos quedan en None; los dos secretos con
    generación propia NO son el valor fabricado (se generó un reemplazo)."""
    # `env` explícito, SIN heredar estos nombres del proceso que corre
    # pytest -- si el hijo los heredara ya puestos, no podríamos distinguir
    # "vino del archivo fabricado" de "ya estaba" y el control dejaría de
    # probar lo que dice probar. Todo lo demás (credenciales reales de la
    # base de test, JAX_TEST_DB_SUFIJO, etc.) SÍ se hereda -- si no, la
    # sonda ni podría conectar a la base de la sesión.
    entorno_limpio = {k: v for k, v in os.environ.items() if k not in VALORES_FABRICADOS}
    salida = subprocess.run(
        [sys.executable, "-c", _SONDA_LISTA_BLANCA], cwd=BACKEND, env=entorno_limpio,
        capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    datos = json.loads(salida.stdout.strip().splitlines()[-1])

    for nombre in NOMBRES_LISTA_BLANCA_SIN_DB + ("JAX_REPO_PATH",):
        assert datos[nombre] == VALORES_FABRICADOS[nombre], (
            f"{nombre} no llegó del entorno de producción -- ¿se cayó de "
            "VARIABLES_NECESARIAS_DE_PRODUCCION en conftest.py?"
        )

    for nombre in NOMBRES_EXCLUIDOS_SIN_GENERACION:
        assert datos[nombre] is None, (
            f"{nombre} llegó al ambiente de la suite (valor={datos[nombre]!r}) -- "
            "conftest.py dejó de excluirlo"
        )

    for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION:
        assert datos[nombre] is not None, (
            f"{nombre} quedó vacío -- conftest.py dejó de generar un reemplazo propio de la sesión"
        )
        assert datos[nombre] != VALORES_FABRICADOS[nombre], (
            f"{nombre} llegó con el valor fabricado del archivo -- conftest.py volvió a "
            "cargarlo de /etc/jax/.env en vez de generar el suyo"
        )


_SONDA_HASH_GENERADOS = """
import hashlib, json, os
import tests.conftest  # noqa: F401  (SIN mock: hace lo mismo que la suite real, /etc/jax/.env real)
salida = {}
for nombre in %r:
    valor = os.environ.get(nombre)
    salida[nombre] = {
        "presente": valor is not None,
        "hash": hashlib.sha256(valor.encode()).hexdigest() if valor else None,
    }
print(json.dumps(salida))
""" % (list(NOMBRES_EXCLUIDOS_CON_GENERACION),)


def test_fernet_key_y_jwt_secret_generados_nunca_coinciden_con_produccion():
    """Comparación por HASH, nunca en texto plano -- ni acá ni en la salida
    de la sonda se imprime el valor real de ningún secreto, sólo su
    sha256. Corre el conftest REAL (sin mock de `cargar`) para probar
    exactamente lo que corre la suite: si `/etc/jax/.env` no existe (CI),
    la comparación se saltea para ese nombre -- no hay nada de producción
    contra qué comparar, y eso no es un defecto de este control."""
    from tests import entorno_de_produccion

    valores_reales = entorno_de_produccion.cargar()

    # Sólo se quitan los dos generados: todo lo demás (credenciales de la
    # base de test, JAX_REPO_PATH, etc.) se hereda igual que en la suite
    # real, para que el conftest real corra de punta a punta sin tropezar
    # con una base de datos inalcanzable.
    entorno_sin_generados = {
        k: v for k, v in os.environ.items() if k not in NOMBRES_EXCLUIDOS_CON_GENERACION
    }
    salida = subprocess.run(
        [sys.executable, "-c", _SONDA_HASH_GENERADOS], cwd=BACKEND, env=entorno_sin_generados,
        capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    datos = json.loads(salida.stdout.strip().splitlines()[-1])

    for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION:
        assert datos[nombre]["presente"], (
            f"{nombre} no se generó -- `import tests.conftest` no lo fijó"
        )
        if nombre in valores_reales and valores_reales[nombre]:
            hash_real = hashlib.sha256(valores_reales[nombre].encode()).hexdigest()
            assert datos[nombre]["hash"] != hash_real, (
                f"{nombre} generado por conftest.py COINCIDE con el de PRODUCCIÓN -- "
                "la suite volvió a firmar/cifrar con el secreto real. (Este mensaje nunca "
                "imprime el valor: sólo compara hashes.)"
            )


def test_fernet_key_y_jwt_secret_del_propio_proceso_de_pytest_no_son_los_de_produccion():
    """MINOR-5 (cuarta ronda de la auditoría adversarial, 2026-09-27): el
    `os.environ` del PROPIO proceso que está corriendo esta prueba -- la
    suite real, no un subproceso aparte -- comparado por HASH contra los de
    producción. Si esto alguna vez diera falso (el hash coincide), sería
    porque la propia corrida de pytest está firmando/cifrando con las
    llaves reales AHORA MISMO."""
    from tests import entorno_de_produccion

    valores_reales = entorno_de_produccion.cargar()
    for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION:
        valor_de_esta_sesion = os.environ.get(nombre)
        assert valor_de_esta_sesion, f"{nombre} no está puesto en esta sesión de pytest"
        if nombre in valores_reales and valores_reales[nombre]:
            hash_real = hashlib.sha256(valores_reales[nombre].encode()).hexdigest()
            hash_de_esta_sesion = hashlib.sha256(valor_de_esta_sesion.encode()).hexdigest()
            assert hash_de_esta_sesion != hash_real, (
                f"{nombre} de ESTA sesión de pytest coincide con el de producción -- "
                "toda la suite está corriendo con el secreto real ahora mismo."
            )


_SONDA_FORZADO_AUNQUE_YA_VENGA_PUESTO = """
import hashlib, json, os
import tests.conftest  # noqa: F401  (YA con los reales puestos en el ambiente -- simula `set -a; . /etc/jax/.env`)
salida = {}
for nombre in %r:
    valor = os.environ.get(nombre)
    salida[nombre] = {
        "presente": valor is not None,
        "hash": hashlib.sha256(valor.encode()).hexdigest() if valor else None,
    }
print(json.dumps(salida))
""" % (list(NOMBRES_EXCLUIDOS_CON_GENERACION),)


def test_fernet_key_y_jwt_secret_se_fuerzan_aunque_ya_vengan_puestos_en_el_ambiente():
    """MINOR-5: no alcanza con no CARGARLOS de `/etc/jax/.env` -- si alguien
    corriera `set -a; . /etc/jax/.env; set +a; pytest` (exportando el
    archivo A MANO, en el shell, ANTES de arrancar pytest), un `setdefault`
    los habría encontrado YA puestos y los habría dejado pasar tal cual. Se
    simula EXACTAMENTE eso: el entorno del subproceso trae los valores
    REALES de producción puestos ANTES de que `tests.conftest` se importe
    -- y se verifica que el conftest los REEMPLAZA de todas formas (fuerza,
    no `setdefault`)."""
    from tests import entorno_de_produccion
    valores_reales = entorno_de_produccion.cargar()

    if not any(valores_reales.get(nombre) for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION):
        pytest.skip("no hay /etc/jax/.env con estos secretos en esta máquina -- nada que simular")

    con_los_reales_ya_puestos = dict(os.environ)
    for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION:
        if valores_reales.get(nombre):
            con_los_reales_ya_puestos[nombre] = valores_reales[nombre]
        else:
            con_los_reales_ya_puestos.pop(nombre, None)

    salida = subprocess.run(
        [sys.executable, "-c", _SONDA_FORZADO_AUNQUE_YA_VENGA_PUESTO], cwd=BACKEND,
        env=con_los_reales_ya_puestos, capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    datos = json.loads(salida.stdout.strip().splitlines()[-1])

    for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION:
        if not valores_reales.get(nombre):
            continue
        hash_real = hashlib.sha256(valores_reales[nombre].encode()).hexdigest()
        assert datos[nombre]["hash"] != hash_real, (
            f"{nombre} SIGUE siendo el de producción después de `import tests.conftest` -- "
            "no se está forzando: un `set -a; . /etc/jax/.env; pytest` filtraría el secreto real "
            "a toda la suite. (Este mensaje nunca imprime el valor: sólo compara hashes.)"
        )


_SONDA_LISTA_BLANCA_NO_VACIA = """
import json
import tests.conftest as conftest_mod
print(json.dumps(sorted(conftest_mod.VARIABLES_NECESARIAS_DE_PRODUCCION)))
"""


def test_la_lista_blanca_no_esta_vacia_y_no_incluye_los_generados():
    """Un `frozenset()` vaciado por accidente haría que NADA llegue de
    `/etc/jax/.env` -- eso rompería la suite entera de forma ruidosa (falla
    la conexión a la base), pero igual hay que cubrir el caso, y sobre todo
    cubrir que nadie vuelva a agregar FERNET_KEY/JAX_JWT_SECRET a la lista
    blanca "para simplificar". Corre en un PROCESO APARTE -- ver el
    comentario grande al principio del archivo."""
    salida = subprocess.run(
        [sys.executable, "-c", _SONDA_LISTA_BLANCA_NO_VACIA], cwd=BACKEND,
        capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    lista = json.loads(salida.stdout.strip().splitlines()[-1])
    assert lista
    assert "JAX_DB_HOST" in lista
    for nombre in NOMBRES_EXCLUIDOS_CON_GENERACION:
        assert nombre not in lista, (
            f"{nombre} está en la lista blanca -- volvería a cargarse de /etc/jax/.env"
        )
