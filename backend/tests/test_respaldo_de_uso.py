"""Cola durable para el registro de uso, Task 2 (plan
docs/superpowers/plans/2026-09-15-cola-durable-uso.md).

Dos cosas se prueban acá:

1. **La migración** — `axioma_usage` suma `spool_id CHAR(36) NULL` con índice
   UNIQUE. Es lo que hace idempotente al reintento: si el proceso se muere
   entre el INSERT y el borrado del archivo del respaldo, el `INSERT IGNORE`
   del ciclo siguiente no vuelve a cobrar la misma fila. Las filas del camino
   feliz quedan con `spool_id` NULL, y un UNIQUE admite varios NULL.
2. **`record_usage` encola en vez de perder** — el `except` sigue siendo
   fail-soft (el turno ya se pagó y ya se respondió), pero antes de contar la
   pérdida deja la fila en el respaldo de disco. Perdida es sólo la fila que
   tampoco pudo encolarse.

El respaldo de TODA la sesión de tests está aislado en un tempdir
(`JAX_USAGE_SPOOL_DIR` en conftest.py): ningún test toca
/srv/jax-data/usage-spool, que es de producción.
"""
import ast
import asyncio
import json
import logging
import uuid
from pathlib import Path

import aiomysql
import pytest

from api.admin import usage as usage_mod
from db import migrations
from uso import cola

COLUMNA = "spool_id"
INDICE = "uniq_axioma_usage_spool_id"


# --- la migración: el DDL ---------------------------------------------------
def test_el_ddl_de_la_columna_es_char36_nulo_y_en_linea():
    ddl = migrations.DDL_COLUMNA_SPOOL_ID
    assert "ALTER TABLE axioma_usage ADD COLUMN spool_id CHAR(36) NULL" in ddl
    assert "ALGORITHM=INPLACE" in ddl and "LOCK=NONE" in ddl


def test_el_ddl_del_indice_es_unico_y_en_linea():
    ddl = migrations.DDL_INDICE_SPOOL_ID
    assert f"ADD UNIQUE INDEX {INDICE} ({COLUMNA})" in ddl
    assert "ALGORITHM=INPLACE" in ddl and "LOCK=NONE" in ddl


class _CurDeMigracion:
    """Cursor de mentira: contesta a las dos preguntas de idempotencia
    (`information_schema.COLUMNS` y `.STATISTICS`) y a la lectura del
    lock_wait_timeout. Guarda cada SQL para poder afirmar el ORDEN."""

    def __init__(self, columna: bool, indice: bool, error=None):
        self.columna = columna
        self.indice = indice
        self.error = error
        self.sqls = []
        self._ultimo = ""

    async def execute(self, consulta, args=None):
        self.sqls.append((consulta, args))
        self._ultimo = consulta
        if consulta.startswith("ALTER TABLE") and self.error is not None:
            raise self.error

    async def fetchone(self):
        if "information_schema.COLUMNS" in self._ultimo:
            return (1 if self.columna else 0,)
        if "information_schema.STATISTICS" in self._ultimo:
            return (1 if self.indice else 0,)
        if "@@SESSION.lock_wait_timeout" in self._ultimo:
            return (86400,)
        raise AssertionError(f"fetchone inesperado tras {self._ultimo!r}")


def _migrar(cur):
    return asyncio.run(migrations._respaldo_de_uso(cur))


def _alters(cur):
    return [q for q, _ in cur.sqls if q.startswith("ALTER TABLE")]


def test_la_migracion_crea_columna_e_indice_si_faltan_en_ese_orden():
    cur = _CurDeMigracion(columna=False, indice=False)
    _migrar(cur)
    assert _alters(cur) == [migrations.DDL_COLUMNA_SPOOL_ID, migrations.DDL_INDICE_SPOOL_ID]


def test_la_migracion_acota_la_espera_alrededor_de_cada_ddl():
    cur = _CurDeMigracion(columna=False, indice=False)
    _migrar(cur)
    consultas = [q for q, _ in cur.sqls]
    for ddl in (migrations.DDL_COLUMNA_SPOOL_ID, migrations.DDL_INDICE_SPOOL_ID):
        i = consultas.index(ddl)
        assert cur.sqls[i - 1] == ("SET SESSION lock_wait_timeout=%s", (30,))
        assert cur.sqls[i + 1] == ("SET SESSION lock_wait_timeout=%s", (86400,))


def test_la_migracion_no_toca_nada_si_la_columna_y_el_indice_ya_estan():
    cur = _CurDeMigracion(columna=True, indice=True)
    _migrar(cur)
    assert _alters(cur) == []


def test_la_migracion_no_crea_el_indice_si_la_columna_no_se_pudo_crear():
    """Un UNIQUE sobre una columna que no existe es un error duro que tiraría
    el arranque. Si el ALTER de la columna se quedó sin su lock (1205), el
    índice espera al próximo arranque, igual que la columna."""
    cur = _CurDeMigracion(
        columna=False, indice=False,
        error=aiomysql.OperationalError(1205, "Lock wait timeout exceeded"))
    _migrar(cur)
    assert _alters(cur) == [migrations.DDL_COLUMNA_SPOOL_ID]


def test_run_migrations_llama_a_la_migracion_del_respaldo():
    """En una base que ya tiene la columna, sacar la llamada no rompe nada
    visible: los tests de information_schema siguen verdes. Este escaneo no."""
    arbol = ast.parse(Path(migrations.__file__).read_text())
    (run,) = [n for n in ast.walk(arbol)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_migrations"]
    llamadas = [n for n in ast.walk(run)
                if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)
                and n.value.func.id == "_respaldo_de_uso"]
    assert len(llamadas) == 1


# --- la migración: contra la base de tests ----------------------------------
def test_la_columna_existe_con_su_forma(client):
    from tests.identidades import sql

    filas = client.portal.call(
        sql,
        "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'axioma_usage' AND COLUMN_NAME = %s",
        (COLUMNA,), True)
    assert filas, "run_migrations no creó la columna spool_id"
    tipo, largo, nulable = filas[0]
    assert (tipo, int(largo), nulable) == ("char", 36, "YES")


def test_el_indice_existe_y_es_unico(client):
    from tests.identidades import sql

    filas = client.portal.call(
        sql,
        "SELECT COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'axioma_usage' "
        "AND INDEX_NAME = %s ORDER BY SEQ_IN_INDEX",
        (INDICE,), True)
    assert [(f[0], int(f[1])) for f in filas] == [(COLUMNA, 0)]


def _insertar_con_spool(spool_id, facet):
    from tests.identidades import sql

    return sql(
        "INSERT IGNORE INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, "
        "tokens_out, cost_usd, request_type, spool_id) "
        "VALUES (1, 1, %s, 'respaldo-modelo', 1, 1, 0.01, 'chat', %s)",
        (facet, spool_id))


def test_el_unique_impide_cobrar_dos_veces_la_misma_fila_del_respaldo(client):
    """El caso real: el proceso muere entre el INSERT y el borrado del archivo.
    El ciclo siguiente reinserta el MISMO spool_id y no tiene que duplicar."""
    from tests.identidades import sql

    facet = f"respaldo-{uuid.uuid4().hex[:10]}"           # VARCHAR(30)
    spool_id = str(uuid.uuid4())
    try:
        client.portal.call(_insertar_con_spool, spool_id, facet)
        client.portal.call(_insertar_con_spool, spool_id, facet)
        filas = client.portal.call(
            sql, "SELECT COUNT(*) FROM axioma_usage WHERE facet = %s", (facet,), True)
        assert int(filas[0][0]) == 1
    finally:
        client.portal.call(sql, "DELETE FROM axioma_usage WHERE facet = %s", (facet,))


def test_el_camino_feliz_admite_muchas_filas_con_spool_id_nulo(client):
    """Un UNIQUE con varios NULL es legal en MariaDB, y el camino feliz no
    escribe spool_id: si no lo fuera, la segunda fila de chat fallaría."""
    from tests.identidades import sql

    facet = f"respaldo-{uuid.uuid4().hex[:10]}"
    try:
        for _ in range(3):
            client.portal.call(_insertar_con_spool, None, facet)
        filas = client.portal.call(
            sql, "SELECT COUNT(*) FROM axioma_usage WHERE facet = %s", (facet,), True)
        assert int(filas[0][0]) == 3
    finally:
        client.portal.call(sql, "DELETE FROM axioma_usage WHERE facet = %s", (facet,))


# --- record_usage encola --------------------------------------------------------
@pytest.fixture
def respaldo(tmp_path, monkeypatch):
    """Un respaldo vacío por test y los contadores en cero."""
    directorio = tmp_path / "respaldo"
    monkeypatch.setenv(cola.VARIABLE_DIRECTORIO, str(directorio))
    cola.reset_estado()
    usage_mod.reset_registros_perdidos()
    yield directorio
    cola.reset_estado()
    usage_mod.reset_registros_perdidos()


def _pool_que_falla(monkeypatch, exc=None):
    async def get_pool():
        raise exc or RuntimeError("pool caido")
    monkeypatch.setattr(usage_mod, "get_pool", get_pool)


def _record(**kwargs):
    argumentos = dict(
        user_id="7", tenant_id="3", facet="jekyll", provider_id="deepseek",
        model="m", tokens_in=10, tokens_out=5, request_type="chat",
        cost_usd_override=0.25)
    argumentos.update(kwargs)
    return asyncio.run(usage_mod.record_usage(**argumentos))


def _filas_del_respaldo(directorio: Path):
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(directorio.glob(f"*{cola.SUFIJO}"))]


def test_el_fallo_del_insert_deja_la_fila_en_el_respaldo_y_no_cuenta_perdida(
        respaldo, monkeypatch, caplog):
    _pool_que_falla(monkeypatch)
    with caplog.at_level(logging.INFO, logger="admin.usage"):
        assert _record() is None
    assert len(_filas_del_respaldo(respaldo)) == 1
    stats = usage_mod.registros_perdidos_stats()
    assert stats["registros_perdidos"] == 0, "está pendiente, no perdida"
    assert stats["en_cola"] == 1
    assert "record_usage encolada" in caplog.text


def test_la_fila_encolada_lleva_el_contrato_completo(respaldo, monkeypatch):
    _pool_que_falla(monkeypatch)
    _record()
    (fila,) = _filas_del_respaldo(respaldo)
    assert set(fila) == set(cola.CAMPOS)
    assert fila["origen"] == "platform"
    assert (fila["facet"], fila["model"]) == ("jekyll", "m")
    assert (fila["tokens_in"], fila["tokens_out"]) == (10, 5)
    assert fila["request_type"] == "chat"
    assert str(fila["tenant_id"]) == "3" and str(fila["user_id"]) == "7"
    assert fila["cost_usd"] == 0.25
    assert fila["created_at"] and fila["spool_id"]


def test_la_fila_encolada_guarda_la_hora_del_turno_no_la_del_reintento(
        respaldo, monkeypatch):
    """El archivo lleva su created_at y el reintento lo escribe explícito: una
    caída de dos horas no puede mover el costo al día siguiente."""
    from datetime import datetime

    _pool_que_falla(monkeypatch)
    antes = datetime.now().astimezone()
    _record()
    (fila,) = _filas_del_respaldo(respaldo)
    cuando = datetime.fromisoformat(fila["created_at"])
    assert cuando.tzinfo is not None, "sin zona, la hora del turno es ambigua"
    assert abs((cuando - antes).total_seconds()) < 60


def test_sin_precio_en_el_catalogo_la_fila_se_encola_con_costo_nulo(
        respaldo, monkeypatch):
    """El fallo puede ser ANTES de calcular el costo (la consulta de precio
    también va a la base). La fila se encola igual: el gasto ocurrió."""
    _pool_que_falla(monkeypatch)
    _record(cost_usd_override=None)
    (fila,) = _filas_del_respaldo(respaldo)
    assert fila["cost_usd"] is None


def test_si_el_respaldo_tambien_falla_ahi_si_cuenta_la_perdida(
        respaldo, monkeypatch, caplog):
    _pool_que_falla(monkeypatch, RuntimeError("pool caido"))

    async def no_encola(fila):
        return None
    monkeypatch.setattr(usage_mod.cola, "encolar", no_encola)

    with caplog.at_level(logging.WARNING, logger="admin.usage"):
        assert _record() is None
    stats = usage_mod.registros_perdidos_stats()
    assert stats["registros_perdidos"] == 1
    assert "RuntimeError" in stats["ultimo_error"]
    assert "record_usage failed" in caplog.text and "total=1" in caplog.text


def test_una_cola_que_explota_no_le_quita_la_respuesta_al_usuario(
        respaldo, monkeypatch, caplog):
    """`encolar` promete no propagar, pero record_usage no puede depender de
    esa promesa: si la rompiera, la excepción saldría del `except` y el turno
    ya cobrado terminaría en 500."""
    _pool_que_falla(monkeypatch)

    async def explota(fila):
        raise OSError("disco lleno")
    monkeypatch.setattr(usage_mod.cola, "encolar", explota)

    with caplog.at_level(logging.WARNING, logger="admin.usage"):
        assert _record() is None
    assert usage_mod.registros_perdidos_stats()["registros_perdidos"] == 1


def test_el_log_del_encolado_tambien_va_redactado(respaldo, monkeypatch, caplog):
    """El motivo viaja al log INFO del camino nuevo, no solo al WARNING del
    viejo: la redacción tiene que cubrirlo igual."""
    secreto = "sk-FAKE-respaldo-0123456789abcdef"
    _pool_que_falla(monkeypatch, RuntimeError(f"fallo con api_key={secreto}"))
    with caplog.at_level(logging.INFO, logger="admin.usage"):
        _record()
    assert "record_usage encolada" in caplog.text
    assert secreto not in caplog.text


def test_el_camino_feliz_no_deja_nada_en_el_respaldo(respaldo, monkeypatch):
    class _Cur:
        lastrowid = 1
        async def execute(self, sql, params=None): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        async def commit(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Pool:
        def acquire(self): return _Conn()

    async def get_pool():
        return _Pool()
    monkeypatch.setattr(usage_mod, "get_pool", get_pool)

    assert _record() == 1
    assert not respaldo.exists() or _filas_del_respaldo(respaldo) == []
    assert usage_mod.registros_perdidos_stats()["registros_perdidos"] == 0


# --- las estadísticas ------------------------------------------------------------
def test_las_estadisticas_publican_la_cola(respaldo):
    # La igualdad es EXACTA a propósito: un campo nuevo tiene que pasar por
    # acá, porque el handler de /api/admin/usage elige los campos que publica
    # uno por uno y agregar uno sin cablearlo lo dejaría invisible.
    # `rechazadas`/`ultimo_rechazo` son de la Task 10 (2026-09-16).
    assert usage_mod.registros_perdidos_stats() == {
        "registros_perdidos": 0,
        "ultimo_error": None,
        "en_cola": 0,
        "perdidas_por_desborde": 0,
        "rechazadas": 0,
        "ultimo_rechazo": None,
        "ultimo_reintento": None,
    }


def test_el_desborde_del_respaldo_se_ve_en_las_estadisticas(respaldo, monkeypatch):
    """Descartar la fila más vieja por el tope SÍ es una pérdida, y se cuenta
    aparte de la que no se pudo escribir."""
    monkeypatch.setenv(cola.VARIABLE_MAX_FILAS, "1")
    _pool_que_falla(monkeypatch)
    _record()
    _record()
    stats = usage_mod.registros_perdidos_stats()
    assert stats["perdidas_por_desborde"] == 1
    assert stats["en_cola"] == 1


def test_el_ultimo_reintento_queda_en_las_estadisticas(respaldo):
    """Lo marca el drenaje (Task 3). Sin un valor propio, el tablero no puede
    distinguir 'hay 5 pendientes y el reintento corre' de 'hay 5 pendientes y
    el reintento está muerto'."""
    usage_mod.marcar_reintento("2026-09-15T20:00:00+00:00")
    assert usage_mod.registros_perdidos_stats()["ultimo_reintento"] == "2026-09-15T20:00:00+00:00"
    usage_mod.reset_registros_perdidos()
    assert usage_mod.registros_perdidos_stats()["ultimo_reintento"] is None
