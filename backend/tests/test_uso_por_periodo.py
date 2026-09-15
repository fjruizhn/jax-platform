"""Fix wave final, item 8 (2026-09-15): GET /api/admin/usage bajo carga.

La prueba de carga del gate U29 (carga-state-usage.md) dio NO-GO: 102k filas
en axioma_usage, ~11 rps planos y p95 ~2,7 s con c=25. EXPLAIN: `ALL`,
`Using where; Using temporary; Using filesort` -- `WHERE DATE(created_at) >=`
no es sargable (la funcion envuelve la columna) y no habia indice.

  (a) Rango sargable `created_at >= <inicio del dia>`: MISMO resultado que
      DATE(created_at) >= dia, probado en una foto consistente con filas de
      borde (00:00:00 del dia entra, un segundo antes no).
  (b) Indice cubriente idx_axioma_usage_periodo, creado por la plataforma
      (dueña de axioma_usage: CREATE_AXIOMA_USAGE en db/migrations.py) con
      ALGORITHM=INPLACE, LOCK=NONE y lock_wait_timeout acotado.
  (c) `ORDER BY SUM(cost_usd)`: antes ordenaba por el cost_usd de una fila
      cualquiera del grupo.
  (d) EXPLAIN de las consultas REALES (las constantes de usage.py).

Los tests de DB piden `client` (se saltean sin DB). Las filas que siembran
llevan una faceta unica por corrida y se borran por id o por esa faceta: no
tocan filas de otra sesion (la carga usa tenants >= 900000).
"""
import asyncio
import json
import logging
import uuid
from datetime import date, datetime, time, timedelta

import aiomysql
import pytest

from api.admin import usage as usage_mod
from db import migrations
from tests.identidades import sql, token_de

IDX = "idx_axioma_usage_periodo"
COLUMNAS = ["created_at", "facet", "model", "request_type", "tokens_in", "tokens_out", "cost_usd"]

# Historia: las consultas de 618d83b, tal cual. Solo para comparar resultados.
SQL_VIEJA_POR_FACETA = """
    SELECT facet, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*), request_type,
           SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced_requests
    FROM axioma_usage
    WHERE DATE(created_at) >= %s
    GROUP BY facet, model, request_type
"""
SQL_VIEJA_GRAFICO = """
    SELECT facet, DATE(created_at) as day, COUNT(*) as cnt
    FROM axioma_usage
    WHERE DATE(created_at) >= %s
    GROUP BY facet, day
"""


def _faceta(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:10]}"          # VARCHAR(30)


async def _insertar(filas) -> list[int]:
    """filas: (facet, cost_usd, created_at | None). Devuelve los ids."""
    ids = []
    for facet, costo, creada in filas:
        if creada is None:
            ids.append(await sql(
                "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, "
                "cost_usd, request_type) VALUES (1, 1, %s, 't8-modelo', 1, 1, %s, 'chat')",
                (facet, costo)))
        else:
            ids.append(await sql(
                "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, "
                "cost_usd, request_type, created_at) VALUES (1, 1, %s, 't8-modelo', 1, 1, %s, 'chat', %s)",
                (facet, costo, creada)))
    return ids


async def _borrar_ids(ids):
    if ids:
        marcas = ", ".join(["%s"] * len(ids))
        await sql(f"DELETE FROM axioma_usage WHERE id IN ({marcas})", tuple(ids))


async def _en_una_foto(consultas):
    """Todas las consultas sobre la MISMA foto: una escritura concurrente
    entre la vieja y la nueva no puede hacerlas diferir."""
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            out = []
            for consulta, args in consultas:
                await cur.execute(consulta, args)
                out.append([tuple(f) for f in await cur.fetchall()])
            await conn.rollback()
            return out


# --- (a) mismo resultado ------------------------------------------------------------
def test_el_rango_sargable_devuelve_lo_mismo_que_DATE(client):
    hoy = date.today()
    desde_dia = hoy - timedelta(days=1)                   # period=day
    desde_grafico = hoy - timedelta(days=7)
    facet = _faceta("t8e")
    medianoche = datetime.combine(desde_dia, time.min)
    borde_grafico = datetime.combine(desde_grafico, time.min)
    ids = client.portal.call(_insertar, [
        (facet, 0.1, medianoche),                          # entra (dia y grafico)
        (facet, 0.2, medianoche - timedelta(seconds=1)),   # fuera del dia, dentro del grafico
        (facet, 0.3, datetime.combine(hoy, time(0, 0, 5))),  # entra
        (facet, 0.4, borde_grafico),                       # borde del grafico: entra
        (facet, 0.5, borde_grafico - timedelta(seconds=1)),  # fuera de todo
    ])
    try:
        viejo_f, nuevo_f, viejo_g, nuevo_g = client.portal.call(_en_una_foto, [
            (SQL_VIEJA_POR_FACETA, (desde_dia.isoformat(),)),
            (usage_mod.SQL_USO_POR_FACETA, (usage_mod._inicio_del_dia(desde_dia),)),
            (SQL_VIEJA_GRAFICO, (desde_grafico.isoformat(),)),
            (usage_mod.SQL_USO_GRAFICO, (usage_mod._inicio_del_dia(desde_grafico),)),
        ])
    finally:
        client.portal.call(_borrar_ids, ids)
    # (c) cambia el ORDEN a proposito: se comparan como conjuntos.
    assert sorted(nuevo_f, key=repr) == sorted(viejo_f, key=repr)
    assert sorted(nuevo_g, key=repr) == sorted(viejo_g, key=repr)
    # Control: las filas de borde estan en la foto y cuentan lo esperado.
    (mia,) = [f for f in nuevo_f if f[0] == facet]
    assert mia[5] == 2 and float(mia[4]) == pytest.approx(0.4)
    assert sum(f[2] for f in nuevo_g if f[0] == facet) == 4


# --- (c) orden por la suma --------------------------------------------------------------
def test_by_facet_ordena_por_la_suma_del_costo_no_por_una_fila(client):
    base = uuid.uuid4().hex[:8]
    una_cara = f"t8o-{base}-una"                         # 1 fila de 0,9 -> suma 0,9
    muchas_baratas = f"t8o-{base}-diez"                  # 10 filas de 0,2 -> suma 2,0
    ids = client.portal.call(_insertar, [(una_cara, 0.9, None)] + [(muchas_baratas, 0.2, None)] * 10)
    try:
        token = token_de(client, "t8-uso-orden", "superadmin", "1")
        resp = client.get("/api/admin/usage?period=day", headers={"Authorization": f"Bearer {token}"})
    finally:
        client.portal.call(_borrar_ids, ids)
    assert resp.status_code == 200, resp.text
    by_facet = resp.json()["by_facet"]
    costos = {f["facet"]: f["cost_usd"] for f in by_facet}
    assert costos[muchas_baratas] == pytest.approx(2.0) and costos[una_cara] == pytest.approx(0.9)
    facetas = [f["facet"] for f in by_facet]
    assert facetas.index(muchas_baratas) < facetas.index(una_cara), (
        "ORDER BY cost_usd ordenaba por una fila cualquiera del grupo (0,2 < 0,9)")


# --- (b) el indice ----------------------------------------------------------------------
def test_el_indice_de_periodo_existe_con_sus_columnas(client):
    filas = client.portal.call(
        sql,
        "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'axioma_usage' AND INDEX_NAME = %s ORDER BY SEQ_IN_INDEX",
        (IDX,), True)
    assert [f[0] for f in filas] == COLUMNAS


def test_el_ddl_del_indice_es_en_linea_y_con_sus_columnas():
    ddl = migrations.DDL_INDICE_USO_POR_PERIODO
    assert f"ADD INDEX {IDX} ({', '.join(COLUMNAS)})" in ddl
    assert "ALGORITHM=INPLACE" in ddl and "LOCK=NONE" in ddl


class _CurFalso:
    def __init__(self, error=None):
        self.sqls = []
        self.error = error

    async def execute(self, consulta, args=None):
        self.sqls.append((consulta, args))
        if consulta.startswith("ALTER TABLE") and self.error is not None:
            raise self.error

    async def fetchone(self):
        return (86400,)


def _acotado(cur):
    return asyncio.run(migrations._crear_indice_acotado(
        cur, "axioma_usage", IDX, migrations.DDL_INDICE_USO_POR_PERIODO))


def test_indice_acotado_crea_con_espera_corta_y_restaura():
    cur = _CurFalso()
    assert _acotado(cur) is True
    assert cur.sqls == [
        ("SELECT @@SESSION.lock_wait_timeout", None),
        ("SET SESSION lock_wait_timeout=%s", (migrations._LOCK_WAIT_DDL_SEGUNDOS,)),
        (migrations.DDL_INDICE_USO_POR_PERIODO, None),
        ("SET SESSION lock_wait_timeout=%s", (86400,)),
    ]
    assert migrations._LOCK_WAIT_DDL_SEGUNDOS == 30


def test_indice_acotado_1205_loguea_ERROR_y_el_arranque_sigue(caplog):
    cur = _CurFalso(aiomysql.OperationalError(1205, "Lock wait timeout exceeded"))
    with caplog.at_level(logging.ERROR, logger="db.migrations"):
        assert _acotado(cur) is False
    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errores) == 1 and IDX in errores[0].getMessage()
    assert cur.sqls[-1] == ("SET SESSION lock_wait_timeout=%s", (86400,))


def test_indice_acotado_otro_error_sube_y_restaura():
    cur = _CurFalso(aiomysql.OperationalError(1846, "LOCK=NONE is not supported"))
    with pytest.raises(aiomysql.OperationalError):
        _acotado(cur)
    assert cur.sqls[-1] == ("SET SESSION lock_wait_timeout=%s", (86400,))


# --- (d) EXPLAIN de las consultas reales ---------------------------------------------
async def _explain_completo(consulta, args):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("EXPLAIN " + consulta, args)
            filas = await cur.fetchall()
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN FORMAT=JSON " + consulta, args)
            (plan,) = await cur.fetchone()
    return filas, json.loads(plan)


def _camino_a_la_tabla(nodo, camino=()):
    """Claves JSON desde la raiz hasta el nodo `table` de axioma_usage."""
    if isinstance(nodo, dict):
        if nodo.get("table_name") == "axioma_usage":
            return camino, nodo
        for clave, valor in nodo.items():
            hallado = _camino_a_la_tabla(valor, camino + (clave,))
            if hallado:
                return hallado
    elif isinstance(nodo, list):
        for valor in nodo:
            hallado = _camino_a_la_tabla(valor, camino)
            if hallado:
                return hallado
    return None


async def _sembrar_para_explain(facet):
    """400 filas viejas (400 dias) y 20 de hoy: el rango de `day` es chico
    frente a la tabla, como en produccion."""
    viejas = datetime.now() - timedelta(days=400)
    valores = [(facet, 0.01, viejas)] * 400 + [(facet, 0.01, datetime.now())] * 20
    marcas = ", ".join(["(1, 1, %s, 't8-modelo', 1, 1, %s, 'chat', %s)"] * len(valores))
    args = tuple(x for fila in valores for x in fila)
    await sql("INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, "
              f"cost_usd, request_type, created_at) VALUES {marcas}", args)
    await sql("ANALYZE TABLE axioma_usage", (), True)


@pytest.mark.parametrize("nombre", ["SQL_USO_POR_FACETA", "SQL_USO_GRAFICO"])
def test_explain_de_la_consulta_real_usa_el_indice_cubriente(client, nombre):
    consulta = getattr(usage_mod, nombre)
    facet = _faceta("t8x")
    client.portal.call(_sembrar_para_explain, facet)
    try:
        filas, plan = client.portal.call(
            _explain_completo, consulta, (usage_mod._inicio_del_dia(date.today() - timedelta(days=1)),))
    finally:
        client.portal.call(sql, "DELETE FROM axioma_usage WHERE facet = %s", (facet,))
    (fila,) = filas
    assert fila["table"] == "axioma_usage"
    assert fila["key"] == IDX, fila
    assert fila["type"] == "range", fila                  # no `ALL`: no es un scan de la tabla
    assert "Using index" in (fila["Extra"] or ""), fila   # cubriente: no toca la fila base
    # Lo que queda (`Using temporary; Using filesort`) es el GROUP BY y el
    # ORDER BY sobre los GRUPOS (faceta x modelo x tipo, o faceta x dia), no
    # sobre las filas del rango: en el plan JSON la tabla cuelga DEBAJO de
    # temporary_table, y el filesort, si esta, encima de ella.
    camino, tabla = _camino_a_la_tabla(plan)
    assert tabla["key"] == IDX and tabla.get("using_index") is True, tabla
    assert "temporary_table" in camino, camino
    if "filesort" in camino:
        assert camino.index("filesort") < camino.index("temporary_table"), camino
