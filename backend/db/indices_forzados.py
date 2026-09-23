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
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

RAIZ_BACKEND = Path(__file__).resolve().parent.parent

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


def indices_forzados(raiz: Path = RAIZ_BACKEND) -> dict[tuple[str, str], list[str]]:
    """{(tabla, índice): ["archivo.py:línea", ...]} de todo `FORCE INDEX`
    del código de `raiz`, sin `tests/` ni entornos virtuales."""
    encontrados: dict[tuple[str, str], list[str]] = {}
    for archivo in sorted(raiz.rglob("*.py")):
        relativo = archivo.relative_to(raiz)
        if relativo.parts[0] in ("tests", ".venv", "venv") or "__pycache__" in relativo.parts:
            continue
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
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


async def avisar_indices_forzados_ausentes(pool, forzados=None) -> list[tuple[str, str]]:
    """Un ERROR por cada índice forzado que no existe en la base activa.
    Devuelve la lista de (tabla, índice) ausentes. Si la comprobación misma
    no se puede hacer, también es un ERROR (no se da por buena en silencio),
    pero tampoco tumba el arranque."""
    if forzados is None:
        forzados = indices_forzados()
    if not forzados:
        return []
    tablas = sorted({t for t, _i in forzados})
    try:
        async with pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(SQL_INDICES_EXISTENTES.format(", ".join(["%s"] * len(tablas))), tablas)
            existentes = {(t, i) for t, i in await cur.fetchall()}
    except Exception as exc:  # fail-soft: chequeo de arranque que sólo avisa (docstring del módulo); el fallo se loguea en ERROR, no se traga
        logger.error("arranque: no se pudo comprobar los índices forzados %s (%s: %s)",
                     sorted(f"{t}.{i}" for t, i in forzados), type(exc).__name__, exc)
        return []
    ausentes = sorted(k for k in forzados if k not in existentes)
    for tabla, indice in ausentes:
        logger.error(
            "arranque: falta el índice %s en la tabla %s -- el código lo fuerza con FORCE INDEX "
            "en %s y esas consultas darán el error 1176 de MariaDB (500) hasta que exista; "
            "lo crea init_tables() de jax (jacobs/store.py)",
            indice, tabla, ", ".join(forzados[(tabla, indice)]))
    return ausentes
