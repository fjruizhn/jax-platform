"""Chequeo del ARRANQUE: los índices que el código fuerza con `FORCE INDEX`
existen en la base (2026-09-23).

Por qué: `FORCE INDEX (nombre)` con un nombre que no existe NO es un hint
que MariaDB ignora -- es el error 1176 ("Key ... doesn't exist in table") y
el endpoint que lo usa da 500. Esos índices no los crea este repo: los crea
`init_tables()` de `jax` (jacobs/store.py), con espera acotada, así que un
despliegue puede quedar sin alguno sin que nada lo diga hasta que un usuario
abre la vista (p. ej. Descartados sin `idx_pipelines_descartados`).

La lista NO se escribe a mano: se saca del propio código de `backend/`
(fuera de `tests/`), leyendo con `ast` cada literal de texto que no sea un
docstring y buscando `FROM <tabla> [alias] FORCE INDEX (<índices>)`. Los
comentarios no son literales y los docstrings se saltan, así que lo que se
menciona en prosa no cuenta: sólo el SQL que de verdad se ejecuta. Un
`FORCE INDEX` nuevo queda vigilado sin tocar este archivo.

No tumba el arranque (mismo criterio que `ajustes.avisar_claves_ilegibles`):
el resto de la plataforma funciona sin esos índices, sólo fallan las
consultas que los fuerzan. Deja un `logger.error` por índice ausente, con
tabla, índice y dónde se fuerza, para que se vea en el journal ANTES de que
lo vea un usuario.

Tolerante por diseño (auditoría de jax-platform#160): un `.py` ilegible
(no UTF-8, sintaxis rota, sin permiso) es un ERROR con su nombre y el
escaneo sigue con el resto; y `chequeo_de_arranque()` envuelve TODO el
chequeo, así que ningún fallo propio de este aviso tumba el arranque.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import math
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

RAIZ_BACKEND = Path(__file__).resolve().parent.parent

# Directorios que no se recorren NUNCA (se podan antes de entrar): tests, y
# lo que no es código de este repo (entornos virtuales, dependencias del
# frontend, cachés de bytecode). En el checkout de producción `backend/`
# tiene su `.venv` adentro: recorrerlo serían miles de archivos ajenos.
EXCLUIDOS = frozenset({".venv", "venv", "node_modules", "__pycache__", ".git"})
# `tests` se poda SOLO en la raíz (backend/tests): un paquete `x/tests/` con SQL de
# producción adentro tiene que seguir escaneado (auditoría de #160, ronda 2).
EXCLUIDOS_EN_LA_RAIZ = frozenset({"tests"})
# Tope del chequeo de arranque: una base que acepta la conexión y no contesta no
# puede dejar colgado el lifespan (auditoría de #160, ronda 2). Configurable,
# con default 15 s -- a diferencia de adjuntos/limites.py (sin default: ahí un
# límite ausente es un error de negocio), este tope SÍ tiene uno porque no es
# un límite de negocio, es sólo el freno de este aviso de arranque.
VARIABLE_TOPE_CHEQUEO_S = "JAX_CHEQUEO_INDICES_FORZADOS_TOPE_S"
_TOPE_CHEQUEO_S_DEFAULT = "15"


class IndicesForzadosConfigInvalida(RuntimeError):
    """`JAX_CHEQUEO_INDICES_FORZADOS_TOPE_S` no es un número finito > 0. Fail-
    closed, mismo criterio que adjuntos/limites.py (auditoría de #160, MINOR
    1): antes de esto, `abc` tumbaba el import con un ValueError crudo,
    `nan` colgaba el event loop (asyncio.wait_for hace math.ceil(timeout) en
    los selectors, y math.ceil(nan) revienta con ValueError DENTRO del
    loop), `inf` dejaba el chequeo sin tope de verdad, y `0`/negativo hacía
    que el chequeo nunca llegara a correr."""


def cargar_tope_chequeo_s() -> float:
    """JAX_CHEQUEO_INDICES_FORZADOS_TOPE_S, con default 15 s si falta. Si
    está puesta, tiene que parsear como número finito > 0 -- si no, error de
    configuración claro (variable y valor crudo) en vez de una traza cruda o
    un loop caído. Se lee en cada llamada (mismo criterio que el resto de
    `adjuntos/*`: el entorno de un proceso no cambia en caliente, no hay
    nada que cachear); el valor validado se fija una única vez al importar
    este módulo, más abajo, para que `chequeo_de_arranque` lo lea de un
    atributo simple del módulo (y los tests lo puedan sustituir con
    monkeypatch sin tocar el entorno)."""
    crudo = os.environ.get(VARIABLE_TOPE_CHEQUEO_S, _TOPE_CHEQUEO_S_DEFAULT)
    try:
        valor = float(crudo)
    except (TypeError, ValueError):
        valor = None
    if valor is None or not math.isfinite(valor) or valor <= 0:
        raise IndicesForzadosConfigInvalida(
            "tope del chequeo de índices forzados inválido (tiene que ser un "
            f"número finito > 0 en /etc/jax/.env): {VARIABLE_TOPE_CHEQUEO_S}={crudo!r}")
    return valor


TOPE_CHEQUEO_S = cargar_tope_chequeo_s()

_FORCE_INDEX = re.compile(
    r"\bFROM\s+`?(?P<tabla>\w+)`?(?:\s+(?:AS\s+)?(?!FORCE\b)\w+)?\s+FORCE\s+INDEX\s*\((?P<indices>[^)]*)\)",
    re.IGNORECASE,
)


def _literales_de_codigo(arbol: ast.AST):
    """Constantes de texto del módulo que NO son docstrings (una expresión
    suelta que sólo es un texto: docstring de módulo, clase o función)."""
    docstrings = {id(n.value) for n in ast.walk(arbol)
                  if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)
                and id(nodo) not in docstrings):
            yield nodo


def _archivos_python(raiz: Path):
    """Los `.py` de `raiz`, podando EXCLUIDOS antes de entrar (os.walk con
    `dirnames` recortado in situ), en orden estable."""
    for actual, dirnames, filenames in os.walk(raiz):
        fuera = EXCLUIDOS | (EXCLUIDOS_EN_LA_RAIZ if Path(actual) == Path(raiz) else frozenset())
        dirnames[:] = sorted(d for d in dirnames if d not in fuera)
        for nombre in sorted(filenames):
            if nombre.endswith(".py"):
                yield Path(actual) / nombre


def indices_forzados(raiz: Path = RAIZ_BACKEND) -> dict[tuple[str, str], list[str]]:
    """{(tabla, índice): ["archivo.py:línea", ...]} de todo `FORCE INDEX`
    del código de `raiz`. Un archivo que no se puede leer o parsear es un
    ERROR con su nombre, y se sigue con los demás."""
    encontrados: dict[tuple[str, str], list[str]] = {}
    for archivo in _archivos_python(raiz):
        relativo = archivo.relative_to(raiz)
        try:
            arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            logger.error("arranque: no se pudo leer %s para buscar FORCE INDEX (%s: %s); "
                         "los índices que fuerce ese archivo NO se comprueban",
                         relativo, type(exc).__name__, exc)
            continue
        for nodo in _literales_de_codigo(arbol):
            for m in _FORCE_INDEX.finditer(nodo.value):
                for indice in (i.strip().strip("`") for i in m.group("indices").split(",")):
                    if indice:
                        encontrados.setdefault((m.group("tabla"), indice), []).append(
                            f"{relativo}:{nodo.lineno}")
    return encontrados


SQL_INDICES_EXISTENTES = (
    "SELECT TABLE_NAME, INDEX_NAME FROM information_schema.STATISTICS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({})"
)
SQL_TABLAS_EXISTENTES = (
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({})"
)


async def avisar_indices_forzados_ausentes(pool, forzados=None) -> list[tuple[str, str]]:
    """Un ERROR por cada índice forzado que no existe en la base activa (o
    por su tabla, si lo que falta es la tabla entera). Devuelve la lista de
    (tabla, índice) ausentes. Los nombres de índice se comparan sin
    distinguir mayúsculas: MariaDB no las distingue en `FORCE INDEX`. Si la
    comprobación misma no se puede hacer, también es un ERROR (no se da por
    buena en silencio), pero tampoco tumba el arranque."""
    if forzados is None:
        forzados = indices_forzados()
    if not forzados:
        return []
    tablas = sorted({t for t, _i in forzados})
    marcas = ", ".join(["%s"] * len(tablas))
    try:
        async with pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(SQL_TABLAS_EXISTENTES.format(marcas), tablas)
            tablas_existentes = {t for (t,) in await cur.fetchall()}
            await cur.execute(SQL_INDICES_EXISTENTES.format(marcas), tablas)
            existentes = {(t, i.lower()) for t, i in await cur.fetchall()}
    except Exception as exc:  # fail-soft: chequeo de arranque que sólo avisa (docstring del módulo); el fallo se loguea en ERROR, no se traga
        logger.error("arranque: no se pudo comprobar los índices forzados %s (%s: %s)",
                     sorted(f"{t}.{i}" for t, i in forzados), type(exc).__name__, exc)
        return []
    ausentes = sorted(k for k in forzados if (k[0], k[1].lower()) not in existentes)
    for tabla, indice in ausentes:
        donde = ", ".join(forzados[(tabla, indice)])
        if tabla not in tablas_existentes:
            logger.error(
                "arranque: falta la TABLA %s (y con ella el índice %s) -- el código la usa con "
                "FORCE INDEX en %s y esas consultas van a fallar hasta que exista; la crea "
                "init_tables() de jax (jacobs/store.py)", tabla, indice, donde)
            continue
        logger.error(
            "arranque: falta el índice %s en la tabla %s -- el código lo fuerza con FORCE INDEX "
            "en %s y esas consultas darán el error 1176 de MariaDB (500) hasta que exista; "
            "lo crea init_tables() de jax (jacobs/store.py)", indice, tabla, donde)
    return ausentes


async def chequeo_de_arranque(obtener_pool) -> None:
    """Lo que llama el lifespan de main.py. TODO el chequeo va dentro de un
    fail-soft: ni el escaneo del código ni la consulta pueden tumbar el
    arranque -- este aviso existe para que se vea un problema, no para
    crear otro."""
    fase = "escanear el código en busca de FORCE INDEX"

    async def _chequear():
        nonlocal fase
        forzados = await asyncio.to_thread(indices_forzados)
        fase = "obtener el pool de la base"
        pool = await obtener_pool()
        fase = "consultar information_schema por los índices existentes"
        await avisar_indices_forzados_ausentes(pool, forzados)
    try:
        await asyncio.wait_for(_chequear(), TOPE_CHEQUEO_S)
    except TimeoutError:  # el timeout de asyncio.wait_for no trae mensaje propio (MINOR 2: salía "(TimeoutError: )" vacío)
        logger.error(
            "arranque: el chequeo de índices forzados no terminó en %ss (colgado en: %s); "
            "los FORCE INDEX NO quedaron comprobados", TOPE_CHEQUEO_S, fase)
    except Exception as exc:  # fail-soft: aviso de arranque (docstring); el fallo se loguea en ERROR con su tipo, no se traga
        logger.error("arranque: el chequeo de índices forzados falló (%s: %s); "
                     "los FORCE INDEX NO quedaron comprobados", type(exc).__name__, exc)
