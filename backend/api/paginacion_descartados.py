"""Paginación por CURSOR (keyset) de los dos listados de descartados
(2026-09-23, decisión de Fernando "hacerlo HOY"; medición en
docs/carga-descartados-offset-2026-09-23.md y
docs/carga-descartados-cursor-2026-09-23.md).

Por qué: `LIMIT n OFFSET m` hace que el motor lea y tire las `m` filas
anteriores -- medido, Handler_read = offset + 51 EXACTO, así que recorrer con
"ver más" un historial de N descartados cuesta ~N²/100 lecturas. Con el
cursor `(descartado_at, pipeline_id)` de la última fila entregada, la página
siguiente es un range scan que arranca justo después de esa fila y lee
~limite+1 filas en CUALQUIER profundidad.

Orden TOTAL, no sólo por fecha: `descartado_at DESC, pipeline_id DESC`.
`descartado_at` no es único (dos descartes en el mismo instante), y sin el
desempate por la clave primaria ni el cursor ni el propio OFFSET garantizan
un orden estable entre páginas. Los dos índices que sirven estas consultas
ya lo dan sin filesort: InnoDB agrega la PK (`pipeline_id`) al final de todo
índice secundario, así que `idx_pipelines_descartados (user_id, tenant_id,
status, descartado_at)` e `idx_pipelines_ocultos (status, descartado_at)`
ya están ordenados por `(descartado_at, pipeline_id)` dentro del prefijo
fijo -- probado con EXPLAIN en tests/test_pipelines_descartados_cursor.py.

NULL: `descartado_at` es `DOUBLE NULL` en el esquema de jax y ninguna
restricción impide un `discarded` sin fecha. MariaDB ordena los NULL al
FINAL en `DESC`, así que el predicado de "después del cursor" es:
  - cursor con fecha d:  descartado_at < d  OR  descartado_at IS NULL
                         OR (descartado_at = d AND pipeline_id < p)
  - cursor sin fecha:    descartado_at IS NULL AND pipeline_id < p

El cursor es OPACO para el cliente (base64url de un JSON `[d, p]`): el
cliente no lo arma ni lo interpreta, sólo devuelve el `cursor_siguiente`
que recibió. No va firmado a propósito: no da acceso a nada -- el filtro de
dueño (`user_id`/`tenant_id` del token) o el `require_superadmin` siguen en
la consulta, y un cursor fabricado sólo cambia DESDE dónde se lista lo que
el llamador ya puede ver.
"""
from __future__ import annotations

import base64
import binascii
import json
import math

from fastapi import HTTPException

# Tope del texto del cursor (Query(max_length=...)): un `[d, p]` real mide
# ~70 caracteres en base64url; 200 deja holgura sin aceptar basura grande.
CURSOR_MAX = 200
PIPELINE_ID_MAX = 36  # VARCHAR(36) en jacobs_pipelines (jax/jacobs/store.py)

ORDEN = "ORDER BY descartado_at DESC, pipeline_id DESC "
DESPUES_DEL_CURSOR = ("AND (descartado_at < %s OR descartado_at IS NULL "
                      "OR (descartado_at = %s AND pipeline_id < %s)) ")
DESPUES_DEL_CURSOR_SIN_FECHA = "AND descartado_at IS NULL AND pipeline_id < %s "


def codificar_cursor(descartado_at, pipeline_id: str) -> str:
    """`descartado_at` sale de MariaDB como float (DOUBLE) o None. `json`
    escribe el float con `repr`, que es de ida y vuelta EXACTA -- el mismo
    valor vuelve a la consulta sin redondeo (pymysql lo manda como literal
    DOUBLE), así que el `descartado_at = %s` del desempate compara bits
    iguales, no "casi iguales"."""
    d = None if descartado_at is None else float(descartado_at)
    crudo = json.dumps([d, pipeline_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(crudo).decode().rstrip("=")


def _rechazar_constante(_nombre):
    raise ValueError("NaN/Infinity no son una fecha")


def decodificar_cursor(cursor: str) -> tuple[float | None, str]:
    """Falla CERRADO: cualquier cursor que no sea exactamente `[número
    finito o null, texto de 1..36]` es un 422 `cursor_invalido` -- nunca se
    "adivina" una posición ni se cae a la primera página en silencio (eso
    repetiría filas en la interfaz sin que nadie lo note)."""
    try:
        relleno = "=" * (-len(cursor) % 4)
        crudo = base64.urlsafe_b64decode(cursor + relleno)
        valor = json.loads(crudo, parse_constant=_rechazar_constante)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="cursor_invalido") from exc
    if not (isinstance(valor, list) and len(valor) == 2):
        raise HTTPException(status_code=422, detail="cursor_invalido")
    d, p = valor
    if d is not None:
        if isinstance(d, bool) or not isinstance(d, (int, float)) or not math.isfinite(d):
            raise HTTPException(status_code=422, detail="cursor_invalido")
        d = float(d)
    if not isinstance(p, str) or not (1 <= len(p) <= PIPELINE_ID_MAX):
        raise HTTPException(status_code=422, detail="cursor_invalido")
    return d, p


def exigir_cursor_sin_offset(cursor: str | None, offset: int) -> None:
    """`cursor` y `offset` > 0 son dos formas de decir "desde dónde" -- las
    dos juntas no tienen un significado único, así que se rechazan en vez de
    elegir una en silencio."""
    if cursor is not None and offset:
        raise HTTPException(status_code=422, detail="cursor_y_offset")


def consulta_y_parametros(base: str, params_base: tuple, limite: int,
                          offset: int, cursor: str | None) -> tuple[str, tuple]:
    """`base` termina en el WHERE fijo de cada listado (con su espacio
    final). Devuelve el SQL y sus parámetros para OFFSET (compatibilidad:
    expandir, no contraer) o para cursor. Pide `limite + 1` filas para
    saber si hay página siguiente sin un COUNT(*)."""
    if cursor is None:
        return base + ORDEN + "LIMIT %s OFFSET %s", (*params_base, limite + 1, offset)
    d, p = decodificar_cursor(cursor)
    if d is None:
        return (base + DESPUES_DEL_CURSOR_SIN_FECHA + ORDEN + "LIMIT %s",
                (*params_base, p, limite + 1))
    return base + DESPUES_DEL_CURSOR + ORDEN + "LIMIT %s", (*params_base, d, d, p, limite + 1)


def cursor_siguiente(filas_de_la_pagina: list, hay_mas: bool, idx_fecha: int, idx_id: int = 0) -> str | None:
    """El cursor de la ÚLTIMA fila entregada, sólo si hay otra página."""
    if not hay_mas or not filas_de_la_pagina:
        return None
    ultima = filas_de_la_pagina[-1]
    return codificar_cursor(ultima[idx_fecha], ultima[idx_id])
