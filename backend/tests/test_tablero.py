"""Frente A (2026-09-16): GET /api/admin/dashboard afirmaba datos que no medía.
A-36: "JAX Engine" pingueaba /health (no existe) y `status_code < 500` daba
alive con un 404. A-35: "API Keys" contaba el .env, la verdad es `credential`.
A-49: pipelines_completed era 0 fijo. A-37: dos COUNT con DATE(created_at).
A-05/A-06: URL literal de LAS MANOS y recent_events constante."""
import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path

import aiomysql
import pytest

from api.admin import dashboard
from tests.identidades import cabeceras, sql

BACKEND = Path(__file__).resolve().parent.parent


def _fuente():
    return (BACKEND / "api/admin/dashboard.py").read_text(encoding="utf-8")


class _Resp:
    def __init__(self, codigo):
        self.status_code = codigo


def _cliente(codigo, urls):
    class _C:
        async def get(self, url, timeout=None):
            urls.append(url)
            return _Resp(codigo)

    async def fabrica():
        return _C()
    return fabrica


def test_un_404_no_es_alive(monkeypatch):
    monkeypatch.setattr(dashboard, "get_http_client", _cliente(404, []))
    assert asyncio.run(dashboard._check_http("http://127.0.0.1:1/api/health"))["status"] == "down"


def test_jax_engine_sondea_api_health_de_la_base_configurada(monkeypatch):
    urls = []
    monkeypatch.setattr(dashboard, "get_http_client", _cliente(200, urls))
    servicio = asyncio.run(dashboard._servicio("JAX Engine", "http://127.0.0.1:18080", "/api/health"))
    assert urls == ["http://127.0.0.1:18080/api/health"]
    assert servicio == {"name": "JAX Engine", "port": 18080, "status": "alive",
                        "latency_ms": servicio["latency_ms"]}


def test_sin_base_configurada_nunca_es_alive():
    assert asyncio.run(dashboard._servicio("JAX Engine", None, "/api/health")) == {
        "name": "JAX Engine", "port": None, "status": "sin_configurar", "latency_ms": None}


@pytest.mark.parametrize("base", ["http://127.0.0.1:abc", "http://127.0.0.1:99999", "http://[::1"])
def test_una_base_mal_formada_es_sin_configurar_y_no_tira_el_tablero(monkeypatch, base):
    """Ronda final M3 (2026-09-16): urlsplit(...).port afuera de todo try
    levantaba ValueError y el tablero entero devolvia 500."""
    urls = []
    monkeypatch.setattr(dashboard, "get_http_client", _cliente(200, urls))
    assert asyncio.run(dashboard._servicio("JAX Engine", base, "/api/health")) == {
        "name": "JAX Engine", "port": None, "status": "sin_configurar", "latency_ms": None}
    assert urls == []


def test_sin_literales_ni_restos():
    fuente = _fuente()
    for resto in ("127.0.0.1:7777", "127.0.0.1:8080", "recent_events", "_count_configured_keys",
                  "_PROVIDERS_KEYS", "/etc/jax/.env", "DATE(created_at)"):
        assert resto not in fuente, resto


def test_el_rango_del_dia_sale_de_la_misma_fecha():
    assert dashboard._rango_del_dia(date(2026, 9, 16)) == (
        datetime(2026, 9, 16, 0, 0), datetime(2026, 9, 17, 0, 0))


def test_el_where_del_uso_es_un_rango_sin_funcion():
    # EXPLAIN no distingue DATE(col) de un rango en MariaDB >= 11.1: se fija el texto.
    assert "created_at >= %s AND created_at < %s" in dashboard.SQL_USO_DEL_DIA
    assert "COALESCE(" in dashboard.SQL_USO_DEL_DIA


# --- con DB -------------------------------------------------------------------
async def _explain(consulta, args):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("EXPLAIN " + consulta, args)
            return await cur.fetchall()


def test_explain_del_uso_del_dia_usa_el_indice_cubriente(client):
    filas = client.portal.call(_explain, dashboard.SQL_USO_DEL_DIA, dashboard._rango_del_dia(date.today()))
    (fila,) = filas
    assert fila["key"] == "idx_axioma_usage_periodo", fila
    assert fila["type"] == "range", fila
    assert "filesort" not in (fila["Extra"] or "") and "temporary" not in (fila["Extra"] or ""), fila


def test_explain_de_llaves_va_por_el_indice_de_credential(client):
    """provider es un catálogo que solo crece por migración (hoy 7 filas): se
    acepta recorrerlo. Lo que crece es credential, y va por idx_provider_state."""
    filas = client.portal.call(_explain, dashboard.SQL_LLAVES, ())
    (cred,) = [f for f in filas if f["table"] in ("c", "credential")]
    assert cred["key"] == "idx_provider_state", filas
    assert all("filesort" not in (f["Extra"] or "") and "temporary" not in (f["Extra"] or "") for f in filas), filas


def test_explain_de_pipelines_completados_va_por_idx_pipelines_status(client):
    (fila,) = client.portal.call(_explain, dashboard.SQL_PIPELINES_COMPLETADOS, ())
    assert fila["key"] == "idx_pipelines_status", fila


MARCA_BLOQUEO = "test-bloqueo-indice-"


async def _sembrar_cuentas(n):
    """n cuentas descartables (tenant 1 por la FK), 1 de cada 50 bloqueada:
    con las pocas filas de jax_memory_test el optimizador elige ALL aunque el
    indice exista, y en produccion la tabla crece."""
    from datetime import timezone
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    filas = [(f"{MARCA_BLOQUEO}{i}@example.invalid", ahora + timedelta(hours=1) if i % 50 == 0 else None)
             for i in range(n)]
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, locked_until) "
                "VALUES (1, %s, 'x', 'viewer', 'active', %s)", filas)
            await cur.execute("ANALYZE TABLE jax_users")
            await cur.fetchall()


async def _borrar_cuentas():
    return await sql("DELETE FROM jax_users WHERE email LIKE %s", (MARCA_BLOQUEO + "%",))


def test_existe_el_indice_de_locked_until(client):
    filas = client.portal.call(
        sql, "SELECT SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' "
        "AND INDEX_NAME = 'idx_jax_users_locked_until' ORDER BY SEQ_IN_INDEX", (), True)
    assert [tuple(f) for f in filas] == [(1, "locked_until")]


def test_explain_de_cuentas_bloqueadas_va_por_idx_jax_users_locked_until(client):
    """Task 15 R12(c): la consulta REAL del tablero, con 3.000 cuentas."""
    assert "FROM jax_users WHERE locked_until > %s" in dashboard.SQL_CUENTAS_BLOQUEADAS
    client.portal.call(_borrar_cuentas)
    try:
        client.portal.call(_sembrar_cuentas, 3000)
        (fila,) = client.portal.call(_explain, dashboard.SQL_CUENTAS_BLOQUEADAS, (datetime.now(),))
        assert fila["key"] == "idx_jax_users_locked_until", fila
        assert fila["type"] == "range", fila
    finally:
        client.portal.call(_borrar_cuentas)


def test_el_tablero_trae_los_numeros_reales(client, monkeypatch):
    monkeypatch.delenv("JAX_PLATFORM_URL", raising=False)
    hoy = date.today()
    medianoche = datetime.combine(hoy, time.min)
    ids = [
        client.portal.call(sql, "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, "
                           "cost_usd, request_type, created_at) VALUES (1, 1, 'tablero-t', 'm', 1, 1, 0, %s, %s)",
                           (tipo, cuando))
        for tipo, cuando in (("chat", medianoche), ("imagen", medianoche + timedelta(seconds=5)),
                             ("chat", medianoche - timedelta(seconds=1)))
    ]
    try:
        resp = client.get("/api/admin/dashboard", headers=cabeceras(client, "tablero-t", "superadmin"))
        ((mensajes, imagenes),) = client.portal.call(
            sql, "SELECT COUNT(*), SUM(request_type='imagen') FROM axioma_usage WHERE DATE(created_at) = %s",
            (hoy.isoformat(),), True)
        ((completados,),) = client.portal.call(
            sql, "SELECT COUNT(*) FROM jacobs_pipelines WHERE status = 'completed'", (), True)
        ((total, configuradas),) = client.portal.call(sql, dashboard.SQL_LLAVES, (), True)
    finally:
        for i in ids:
            client.portal.call(sql, "DELETE FROM axioma_usage WHERE id = %s", (i,))
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    assert set(datos) == {"services", "stats"}
    s = datos["stats"]
    assert (s["messages_today"], s["images_generated"]) == (mensajes, int(imagenes or 0))
    assert s["pipelines_completed"] == completados
    assert (s["api_keys_configured"], s["api_keys_total"]) == (int(configuradas), total)
    (motor,) = [sv for sv in datos["services"] if sv["name"] == "JAX Engine"]
    assert motor["status"] == "sin_configurar"


# --- Task 15 R12c, ronda 2: el indice va por el DDL ACOTADO -----------------
# jax_users la lee cada request autenticado (auth/middleware.py); un ALTER con
# el lock_wait_timeout por defecto (86400 s) esperando su MDL encola TODAS las
# lecturas nuevas detras. Tiene que ir por _crear_indice_acotado (30 s).
import ast  # noqa: E402

from db import migrations  # noqa: E402


class _CurDeMigracion:
    def __init__(self, existe: bool):
        self.existe = existe
        self.sqls = []
        self._ultimo = ""

    async def execute(self, consulta, args=None):
        self.sqls.append((consulta, args))
        self._ultimo = consulta

    async def fetchone(self):
        if "information_schema.STATISTICS" in self._ultimo:
            return (1 if self.existe else 0,)
        if "@@SESSION.lock_wait_timeout" in self._ultimo:
            return (86400,)
        raise AssertionError(f"fetchone inesperado tras {self._ultimo!r}")


def test_indice_locked_until_se_crea_con_el_ddl_acotado():
    cur = _CurDeMigracion(existe=False)
    asyncio.run(migrations._indice_de_cuentas_bloqueadas(cur))
    ddl = [q for q, _ in cur.sqls if q.startswith("ALTER TABLE")]
    assert ddl == [migrations.DDL_INDICE_CUENTAS_BLOQUEADAS]
    assert "idx_jax_users_locked_until (locked_until)" in ddl[0]
    assert "ALGORITHM=INPLACE" in ddl[0] and "LOCK=NONE" in ddl[0]
    i = [q for q, _ in cur.sqls].index(ddl[0])
    assert cur.sqls[i - 1] == ("SET SESSION lock_wait_timeout=%s", (30,))
    assert cur.sqls[i + 1] == ("SET SESSION lock_wait_timeout=%s", (86400,))


def test_indice_locked_until_ya_creado_no_toca_nada():
    cur = _CurDeMigracion(existe=True)
    asyncio.run(migrations._indice_de_cuentas_bloqueadas(cur))
    assert len(cur.sqls) == 1 and "information_schema.STATISTICS" in cur.sqls[0][0]


def test_indice_locked_until_no_esta_en_la_lista_sin_cota():
    assert all(indice != "idx_jax_users_locked_until" for _, indice, _ in migrations._INDEXES)
    arbol = ast.parse(Path(migrations.__file__).read_text(encoding="utf-8"))
    (run,) = [n for n in ast.walk(arbol) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_migrations"]
    llamadas = [n for n in ast.walk(run)
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name) and n.value.func.id == "_indice_de_cuentas_bloqueadas"]
    assert len(llamadas) == 1


def test_run_migrations_dos_veces_deja_el_indice_locked_until(client):
    """Idempotente sobre la DB real de tests: el client ya corrio run_migrations
    al arrancar; una segunda corrida no falla y el indice sigue."""
    import warnings
    with warnings.catch_warnings():
        # Los seeds son INSERT IGNORE: en la segunda corrida MariaDB avisa
        # "Duplicate entry" por cada fila ya sembrada. Es lo esperado.
        warnings.simplefilter("ignore")
        client.portal.call(migrations.run_migrations)
    ((n,),) = client.portal.call(
        sql, "SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'jax_users' AND INDEX_NAME = 'idx_jax_users_locked_until'", (), True)
    assert n == 1
