"""La suite no expone secretos de producción que no necesita (2026-09-27).

Defecto real encontrado en hall9000: `tests/conftest.py` cargaba TODO
`/etc/jax/.env` con `os.environ.setdefault` -- incluido
`CLAUDE_CODE_OAUTH_TOKEN`, la credencial OAuth real de la cuenta de
Fernando. `model_catalog._read_anthropic_oauth_token()` prueba la variable
de entorno ANTES que el archivo local, así que 3 tests que monkeypatcheaban
`_ANTHROPIC_CREDENTIALS_PATH` para simular "archivo ausente/vencido/con
token fake" en realidad terminaban usando el token REAL sin que nadie lo
pidiera -- fallaban con el token real en el `Authorization`, no con el fake
esperado.

Mismo patrón que `test_conftest_sin_servicios_de_produccion.py`: corre el
conftest en un PROCESO APARTE con `tests.entorno_de_produccion.cargar`
reemplazada por una que devuelve los secretos EXCLUIDOS con valores
fabricados -- así el control es determinístico (no depende de que
`/etc/jax/.env` exista ni de qué traiga hoy) y corre igual en CI, donde ese
archivo no existe."""
import json
import os
import subprocess
import sys
from pathlib import Path

# NUNCA `import tests.conftest` (ni `from tests.conftest import ...`) al
# nivel de módulo de un archivo de test -- hallazgo real (2026-09-27, esta
# misma ronda): pytest ya cargó `tests/conftest.py` por su cuenta (import
# automático de conftest, con SU propia resolución de nombre de módulo);
# un `import tests.conftest` explícito acá crea una SEGUNDA entrada en
# `sys.modules` bajo un nombre distinto y VUELVE A EJECUTAR TODO el código
# de nivel de módulo de conftest.py -- que reasigna `os.environ` con
# temp-dirs NUEVOS para `JAX_KILL_SWITCH_PATH`, `JAX_ADJUNTOS_DIR`, etc.
# (asignación directa, no `setdefault`). Efecto medido: 48 tests de
# test_kill_switch*.py/test_carril_mesa.py/test_facet_canary_freno.py
# empezaban a fallar SOLO cuando este archivo se colecionaba antes que
# ellos -- el freno de pruebas quedaba "ya activado" de una corrida
# anterior porque la ruta real del archivo cambió a mitad de sesión. Todo
# lo que necesite mirar `tests.conftest` en este archivo lo hace DENTRO de
# un sonda en subproceso (import aislado, proceso descartable) -- nunca acá
# arriba.
BACKEND = Path(__file__).resolve().parents[1]

# Lista FIJA, propia de este test -- NO se deriva de
# `conftest_mod.SECRETOS_DE_PRODUCCION_NO_NECESARIOS`. Si se derivara, un
# `frozenset()` vaciado por accidente en producción haría que el `for` de
# abajo no iterara nada y el test pasara VACÍO -- exactamente el defecto que
# esto tiene que atrapar. `test_la_lista_de_exclusion_no_esta_vacia` cubre
# el caso "la lista de producción está vacía"; este test cubre "estos
# nombres puntuales, sea cual sea el estado de la lista de producción".
NOMBRES_QUE_TIENEN_QUE_QUEDAR_EXCLUIDOS = ("CLAUDE_CODE_OAUTH_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")

VALORES_FABRICADOS = {nombre: f"secreto-de-prueba-{i}" for i, nombre in enumerate(
    NOMBRES_QUE_TIENEN_QUE_QUEDAR_EXCLUIDOS)}
# Un control positivo: una variable inofensiva que SÍ tiene que pasar, para
# que este test no pase "de arriba" por accidente (p. ej. si `cargar()`
# terminara devolviendo `{}` por algún motivo, todo lo demás igual pasaría
# de "no está" sin que la exclusión hubiera hecho nada).
VALORES_FABRICADOS["JAX_UNA_VARIABLE_DE_CONTROL_INOFENSIVA"] = "deberia-pasar"

_SONDA = """
import json, os
import tests.entorno_de_produccion as ep
ep.cargar = lambda ruta=ep.RUTA, **kw: %r
import tests.conftest  # noqa: F401  (ejecuta el filtro real, como en la suite)
print(json.dumps({k: os.environ.get(k) for k in %r}))
""" % (VALORES_FABRICADOS, sorted(VALORES_FABRICADOS))


def test_los_secretos_no_necesarios_declarados_nunca_llegan_al_ambiente():
    # `env` explícito, SIN heredar estos 3 nombres del proceso que corre
    # pytest (que ya los excluyó él mismo al cargar SU PROPIO conftest.py,
    # pero esto no puede depender de eso): si el hijo los heredara ya
    # puestos, `os.environ.setdefault()` (adentro del conftest real, más
    # abajo) no haría nada -- ni para bien ni para mal -- y el control
    # dejaría de probar lo que dice probar.
    entorno_limpio = {k: v for k, v in os.environ.items() if k not in NOMBRES_QUE_TIENEN_QUE_QUEDAR_EXCLUIDOS}
    salida = subprocess.run(
        [sys.executable, "-c", _SONDA], cwd=BACKEND, env=entorno_limpio,
        capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    datos = json.loads(salida.stdout.strip().splitlines()[-1])

    for nombre in NOMBRES_QUE_TIENEN_QUE_QUEDAR_EXCLUIDOS:
        assert datos[nombre] is None, (
            f"{nombre} llegó al ambiente de la suite (valor={datos[nombre]!r}) -- "
            "conftest.py dejó de excluirlo de SECRETOS_DE_PRODUCCION_NO_NECESARIOS"
        )
    # Control positivo: sin la exclusión, TODO lo que `cargar()` devuelve
    # llega al ambiente -- si esto también diera None, el problema sería el
    # arnés del test (la sonda), no la exclusión real.
    assert datos["JAX_UNA_VARIABLE_DE_CONTROL_INOFENSIVA"] == "deberia-pasar"


_SONDA_LISTA_NO_VACIA = """
import json
import tests.conftest as conftest_mod
print(json.dumps(sorted(conftest_mod.SECRETOS_DE_PRODUCCION_NO_NECESARIOS)))
"""


def test_la_lista_de_exclusion_no_esta_vacia():
    """Un `frozenset()` vacío haría pasar el test de arriba sin exclusión
    real -- éste falla si alguien "arregla" el test vaciando la lista en vez
    de mantenerla. Corre en un PROCESO APARTE -- ver el comentario grande al
    principio del archivo sobre por qué `tests.conftest` nunca se importa al
    nivel de módulo de un test."""
    salida = subprocess.run(
        [sys.executable, "-c", _SONDA_LISTA_NO_VACIA], cwd=BACKEND,
        capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    lista = json.loads(salida.stdout.strip().splitlines()[-1])
    assert lista
    assert "CLAUDE_CODE_OAUTH_TOKEN" in lista
