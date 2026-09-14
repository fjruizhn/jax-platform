"""
`datetime.utcnow()` está deprecado desde Python 3.12 (DEUDA.md, anotados de
la etapa 1 de admin usuarios, 2026-09-13): ruido de DeprecationWarning en
toda corrida de pytest.

El reemplazo NO es `datetime.now(timezone.utc)` a secas: eso devuelve una
hora CON zona, y el backend la compara contra columnas DATETIME que aiomysql
entrega SIN zona (`password_reset_tokens.expires_at`, `jax_users.locked_until`).
Comparar una con zona contra una sin zona lanza TypeError: el reset de
contraseña y el bloqueo por intentos se romperían. `utc_ahora()` conserva la
semántica exacta de `utcnow()` -- UTC, sin zona -- sin la llamada deprecada.

El tercer test es el guard: ningún módulo del backend (tests incluidos)
vuelve a llamar `utcnow`. Mira el AST, no el texto, para no contar
comentarios ni este docstring.
"""
from __future__ import annotations

import ast
import datetime
from pathlib import Path

from tiempo import utc_ahora

BACKEND = Path(__file__).resolve().parents[1]


def test_utc_ahora_es_utc_sin_zona():
    ahora = utc_ahora()
    assert ahora.tzinfo is None
    referencia = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    assert abs((referencia - ahora).total_seconds()) < 2


def test_se_compara_contra_un_datetime_sin_zona_como_los_de_la_base():
    # Una hora con zona lanzaría TypeError acá: es el caso de expires_at.
    columna = datetime.datetime(2020, 1, 1, 0, 0, 0)
    assert utc_ahora() > columna


def test_ningun_modulo_del_backend_llama_utcnow():
    ofensores = []
    for ruta in BACKEND.rglob("*.py"):
        if {".venv", "__pycache__"} & set(ruta.parts):
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Attribute) and nodo.attr == "utcnow":
                ofensores.append(f"{ruta.relative_to(BACKEND)}:{nodo.lineno}")
    assert ofensores == []
