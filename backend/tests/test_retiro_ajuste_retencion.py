"""T16 (2026-10-02, auditoria MAJOR-7): web_task_retention_days se retira.

El ajuste gobernaba solo el reaper `owner_cleanup` de los archivos de /command
(`web-task-*`); ambos se borraron. Queda: ni en CLAVES/DEFINICIONES/limites, ni
sembrado por la migracion de 2026-09-16, y una migracion idempotente borra la fila
vieja de `axioma_config` en cada arranque (la clave ya no existe en el codigo: nada
puede recrearla). Puros, sin DB: el cursor es un doble que registra las sentencias.
"""
import ast
import asyncio
from pathlib import Path

import ajustes
from db import migrations

CLAVE = "web_task_retention_days"


class _Cursor:
    def __init__(self):
        self.sentencias = []

    async def execute(self, sql, params=()):
        self.sentencias.append((" ".join(sql.split()), tuple(params)))


def test_ajustes_ya_no_define_la_retencion():
    assert not hasattr(ajustes, "RETENCION") and not hasattr(ajustes, "RETENCION_MAX")
    assert CLAVE not in ajustes.CLAVES
    assert CLAVE not in ajustes.DEFINICIONES
    assert CLAVE not in ajustes.limites()


def test_la_migracion_de_2026_09_16_ya_no_siembra_la_retencion():
    assert CLAVE not in migrations.VALORES_QUE_RIGEN_2026_09_16


def test_la_migracion_de_retiro_borra_solo_esa_fila_y_es_idempotente():
    cur = _Cursor()
    asyncio.run(migrations._retirar_ajuste_retencion_v1(cur))
    asyncio.run(migrations._retirar_ajuste_retencion_v1(cur))  # dos arranques seguidos
    assert cur.sentencias == [
        ("DELETE FROM axioma_config WHERE config_key = %s", (CLAVE,)),
    ] * 2


def test_el_arranque_corre_la_migracion_de_retiro_despues_de_la_que_mandaba():
    fuente = (Path(migrations.__file__)).read_text(encoding="utf-8")
    llamadas = [n.value.func.id for n in ast.walk(ast.parse(fuente))
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)]
    assert "_retirar_ajuste_retencion_v1" in llamadas
    assert llamadas.index("_ajustes_que_mandan_v1") < llamadas.index("_retirar_ajuste_retencion_v1")
