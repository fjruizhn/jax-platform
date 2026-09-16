"""Task 3 del plan 2026-09-15-cola-durable-uso: el drenaje del respaldo.

`uso/reintento.py` es el ÚNICO que inserta lo que quedó en el respaldo: la
plataforma es la dueña de `axioma_usage` y la única con migraciones. Lo que se
prueba acá es lo que hace que el mecanismo sirva de verdad:

1. **Drena y deja el respaldo en cero** — si insertara y no borrara, el ciclo
   siguiente volvería a intentar la misma fila para siempre.
2. **Con la base caída no pierde nada** — no borra ni un archivo y devuelve el
   motivo; el ciclo siguiente recupera todo.
3. **El UNIQUE de verdad** (`uniq_axioma_usage_spool_id`, Task 2) contra la
   base de tests, no un mock: es el freno que evita cobrar dos veces y un freno
   sin prueba no es freno (Principio VII). Un duplicado CUENTA COMO ÉXITO y el
   archivo se borra igual -- si no, la fila queda dando vueltas para siempre.
4. **`created_at` es la hora del TURNO**, no la del reintento (Ruling C-4): una
   caída de dos horas no puede mover el costo al día siguiente.
5. **`status`/`job_id` llegan a la tabla** -- el motivo entero de la opción (b)
   que decidió Fernando: recuperar el cobro sin perder la trazabilidad contra
   `motor_jobs.jsonl`.

Las filas se construyen SIEMPRE llamando a `cola.encolar(...)`, nunca
escribiendo el JSON a mano: el contrato del archivo lo define `uso/cola.py` y
así un campo nuevo no rompe estos fixtures.

Los tests que tocan la base son SINCRÓNICOS y pasan por `client.portal.call`:
el pool de `db.connection` es un singleton de proceso atado al loop que lo
creó (el del portal del fixture `client`). Un test `async` normal correría en
el loop de pytest-asyncio y dejaría el pool atado al loop equivocado para toda
la sesión.
"""
import ast
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.admin import usage as usage_mod
from tests.identidades import sql
from uso import cola, reintento


FILA = {
    "tenant_id": "3",          # string a propósito: así los guarda record_usage
    "user_id": "7",            # y el drenaje es el que tiene que hacer int()
    "model": "reintento-modelo",
    "tokens_in": 10,
    "tokens_out": 5,
    "cost_usd": 0.25,
    "request_type": "chat",
    "origen": "platform",
}


def _facet() -> str:
    """Etiqueta única por test: `facet` es VARCHAR(30) y es por donde se
    limpian las filas que este test escribió (nunca por tenant ni por ventana
    de tiempo: la tabla es compartida)."""
    return f"reint-{uuid.uuid4().hex[:10]}"


def _fila(facet, **extra):
    return {**FILA, "facet": facet, **extra}


def _archivos(directorio: Path):
    return sorted(p.name for p in directorio.glob(f"*{cola.SUFIJO}"))


@pytest.fixture
def respaldo(tmp_path, monkeypatch):
    """Un respaldo propio por test y los contadores en cero. NUNCA
    /srv/jax-data/usage-spool: ahí drena la plataforma de producción y una fila
    de mentira se le cobraría a un tenant de verdad."""
    directorio = tmp_path / "respaldo"
    monkeypatch.setenv(cola.VARIABLE_DIRECTORIO, str(directorio))
    monkeypatch.delenv(reintento.VARIABLE_INTERVALO, raising=False)
    monkeypatch.delenv(reintento.VARIABLE_LOTE, raising=False)
    cola.reset_estado()
    usage_mod.reset_registros_perdidos()
    yield directorio
    cola.reset_estado()
    usage_mod.reset_registros_perdidos()


# --- pool de mentira (para los tests que no necesitan la base) --------------
class _Cursor:
    def __init__(self, registro, rowcount, error):
        self.registro = registro
        self.rowcount = rowcount
        self.error = error

    async def execute(self, consulta, args=None):
        if self.error is not None:
            raise self.error
        self.registro.append((consulta, args))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conexion:
    def __init__(self, registro, rowcount, error):
        self.registro = registro
        self.rowcount = rowcount
        self.error = error
        self.commits = 0

    def cursor(self):
        return _Cursor(self.registro, self.rowcount, self.error)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, rowcount=1, error=None):
        self.registro = []
        self.conexion = _Conexion(self.registro, rowcount, error)

    def acquire(self):
        return self.conexion


def _pool_de_mentira(monkeypatch, **kwargs):
    pool = _Pool(**kwargs)

    async def get_pool():
        return pool

    monkeypatch.setattr(reintento, "get_pool", get_pool)
    return pool


def _pool_caido(monkeypatch, error=None):
    async def get_pool():
        raise error or RuntimeError("pool caido")

    monkeypatch.setattr(reintento, "get_pool", get_pool)


# --- configuración ---------------------------------------------------------

def test_el_intervalo_y_el_lote_salen_del_entorno_con_default(monkeypatch):
    monkeypatch.delenv(reintento.VARIABLE_INTERVALO, raising=False)
    monkeypatch.delenv(reintento.VARIABLE_LOTE, raising=False)
    assert reintento.intervalo() == reintento.INTERVALO_POR_DEFECTO == 60
    assert reintento.lote() == reintento.LOTE_POR_DEFECTO

    monkeypatch.setenv(reintento.VARIABLE_INTERVALO, "5")
    monkeypatch.setenv(reintento.VARIABLE_LOTE, "7")
    assert reintento.intervalo() == 5
    assert reintento.lote() == 7

    # Un valor mal escrito no puede apagar el drenaje en silencio.
    monkeypatch.setenv(reintento.VARIABLE_INTERVALO, "no-es-un-numero")
    monkeypatch.setenv(reintento.VARIABLE_LOTE, "0")
    assert reintento.intervalo() == reintento.INTERVALO_POR_DEFECTO
    assert reintento.lote() == reintento.LOTE_POR_DEFECTO


# --- el drenaje contra la base de tests ------------------------------------
async def _encolar(filas):
    return [await cola.encolar(fila) for fila in filas]


async def _encolar_y_drenar(filas, limite=100):
    await _encolar(filas)
    return await reintento.drenar(limite)


def _borrar(client, facet):
    client.portal.call(sql, "DELETE FROM axioma_usage WHERE facet = %s", (facet,))


def test_drena_lo_pendiente_y_el_respaldo_queda_en_cero(client, respaldo):
    facet = _facet()
    try:
        resumen = client.portal.call(_encolar_y_drenar, [_fila(facet)])

        assert (resumen["leidas"], resumen["insertadas"], resumen["quitadas"]) == (1, 1, 1)
        assert resumen["motivo"] is None
        assert _archivos(respaldo) == []

        filas = client.portal.call(
            sql,
            "SELECT tenant_id, user_id, model, tokens_in, tokens_out, cost_usd, "
            "request_type, spool_id FROM axioma_usage WHERE facet = %s",
            (facet,), True)
        assert len(filas) == 1
        tenant, usuario, modelo, entrada, salida, costo, tipo, spool_id = filas[0]
        assert (tenant, usuario) == (3, 7), "tenant_id/user_id entran como enteros"
        assert (modelo, entrada, salida) == ("reintento-modelo", 10, 5)
        assert float(costo) == 0.25 and tipo == "chat"
        assert spool_id and len(spool_id) == 36
    finally:
        _borrar(client, facet)


def test_una_fila_ya_insertada_no_se_cobra_dos_veces(client, respaldo):
    """El caso real: el proceso muere DESPUÉS del INSERT y ANTES de borrar el
    archivo. El ciclo siguiente reinserta el MISMO spool_id contra el UNIQUE
    real de la base, no contra un mock. El duplicado cuenta como ÉXITO y el
    archivo se borra igual: si no, la fila se queda dando vueltas para siempre.
    """
    facet = _facet()
    try:
        (spool_id,) = client.portal.call(_encolar, [_fila(facet)])
        client.portal.call(reintento.drenar, 100)

        # el archivo "sobrevive" a la caída: se vuelve a encolar con su id
        resumen = client.portal.call(
            _encolar_y_drenar, [_fila(facet, spool_id=spool_id)])

        assert resumen["leidas"] == 1
        assert resumen["quitadas"] == 1, "un duplicado también se saca del respaldo"
        assert _archivos(respaldo) == []
        filas = client.portal.call(
            sql, "SELECT COUNT(*) FROM axioma_usage WHERE facet = %s", (facet,), True)
        assert int(filas[0][0]) == 1, "el UNIQUE evitó el doble cobro"
    finally:
        _borrar(client, facet)


def test_la_fila_llega_con_status_y_job_id(client, respaldo):
    """Sin esto se recupera el cobro y se pierde la trazabilidad: la
    reconciliación contra `motor_jobs.jsonl` empareja por `job_id`."""
    facet = _facet()
    trabajo = str(uuid.uuid4())
    try:
        client.portal.call(
            _encolar_y_drenar,
            [_fila(facet, origen="motor_registry", status="ok", job_id=trabajo)])

        filas = client.portal.call(
            sql, "SELECT status, job_id FROM axioma_usage WHERE facet = %s",
            (facet,), True)
        assert filas[0] == ("ok", trabajo)
    finally:
        _borrar(client, facet)


def test_created_at_es_la_hora_del_turno_no_la_del_reintento(client, respaldo):
    """Ruling C-4: la columna existe para esto. Se compara en epoch
    (`UNIX_TIMESTAMP`) para no depender de la zona de la sesión de MariaDB."""
    facet = _facet()
    turno = datetime.now(timezone.utc) - timedelta(hours=3)
    try:
        client.portal.call(
            _encolar_y_drenar, [_fila(facet, created_at=turno.isoformat())])

        filas = client.portal.call(
            sql, "SELECT UNIX_TIMESTAMP(created_at) FROM axioma_usage "
                 "WHERE facet = %s", (facet,), True)
        guardado = float(filas[0][0])
        assert abs(guardado - turno.timestamp()) < 2, (
            "se insertó la hora del reintento y no la del turno")
    finally:
        _borrar(client, facet)


def test_un_id_no_numerico_entra_como_null_y_no_rompe_el_drenaje(client, respaldo):
    """El contrato con jax dice que el drenaje convierte con int(). Un valor no
    numérico no puede hacer fallar el lote entero: entra en NULL, que es
    exactamente "no se sabe", y el resto de las filas pasa."""
    facet = _facet()
    try:
        resumen = client.portal.call(
            _encolar_y_drenar, [_fila(facet, tenant_id="no-es-un-numero")])

        assert resumen["insertadas"] == 1 and resumen["quitadas"] == 1
        filas = client.portal.call(
            sql, "SELECT tenant_id, user_id FROM axioma_usage WHERE facet = %s",
            (facet,), True)
        assert filas[0] == (None, 7)
    finally:
        _borrar(client, facet)


def test_el_drenaje_deja_su_marca_de_vida(client, respaldo):
    """`ultimo_reintento` es lo que distingue "hay 5 pendientes y el drenaje
    corre" de "hay 5 pendientes y el drenaje está muerto"."""
    facet = _facet()
    try:
        assert usage_mod.registros_perdidos_stats()["ultimo_reintento"] is None
        client.portal.call(_encolar_y_drenar, [_fila(facet)])
        marca = usage_mod.registros_perdidos_stats()["ultimo_reintento"]
        assert marca and datetime.fromisoformat(marca).tzinfo is not None
    finally:
        _borrar(client, facet)


# --- la base caída ---------------------------------------------------------

async def test_con_la_base_caida_no_pierde_nada_y_devuelve_el_motivo(
        respaldo, monkeypatch, caplog):
    _pool_caido(monkeypatch)
    facet = _facet()
    await cola.encolar(_fila(facet))

    with caplog.at_level(logging.WARNING, logger=reintento.LOGGER):
        resumen = await reintento.drenar(10)

    assert (resumen["insertadas"], resumen["quitadas"]) == (0, 0)
    assert resumen["motivo"] and "pool caido" in resumen["motivo"]
    assert len(_archivos(respaldo)) == 1, "no se borró nada con la base caída"
    assert "drenaje" in caplog.text


async def test_el_ciclo_siguiente_recupera_lo_que_la_caida_dejo(
        respaldo, monkeypatch):
    _pool_caido(monkeypatch)
    facet = _facet()
    await cola.encolar(_fila(facet))
    await reintento.drenar(10)
    assert len(_archivos(respaldo)) == 1

    pool = _pool_de_mentira(monkeypatch)
    resumen = await reintento.drenar(10)

    assert (resumen["insertadas"], resumen["quitadas"]) == (1, 1)
    assert _archivos(respaldo) == []
    (consulta, args), = pool.registro
    assert consulta.startswith("INSERT IGNORE INTO axioma_usage")
    assert pool.conexion.commits == 1


async def test_el_insert_nombra_las_columnas_del_contrato(respaldo, monkeypatch):
    """Las trece del archivo menos `origen`, que NO es una columna de
    `axioma_usage` (identifica al proceso que depositó, no al gasto): doce
    columnas, `created_at` explícito -- nunca el DEFAULT -- y `status`/`job_id`
    incluidas."""
    pool = _pool_de_mentira(monkeypatch)
    await cola.encolar(_fila(_facet()))

    await reintento.drenar(10)

    (consulta, args), = pool.registro
    assert consulta.startswith("INSERT IGNORE INTO axioma_usage")
    nombradas = consulta[consulta.index("(") + 1:consulta.index(")")]
    columnas = [c.strip() for c in nombradas.split(",")]
    assert columnas == list(reintento.COLUMNAS)
    assert set(reintento.COLUMNAS) == set(cola.CAMPOS) - {"origen"}
    assert "created_at" in columnas and len(args) == len(columnas)


async def test_una_fila_que_el_insert_rechaza_no_frena_a_las_demas(
        respaldo, monkeypatch, caplog):
    """Un error de DATO en una fila (no de conexión) no puede dejar sin drenar
    al resto del lote."""
    pool = _pool_de_mentira(monkeypatch)
    buenas = []
    original = pool.conexion.cursor
    llamadas = {"n": 0}

    def cursor():
        llamadas["n"] += 1
        cur = original()
        if llamadas["n"] == 1:
            cur.error = ValueError("dato imposible")
        return cur

    monkeypatch.setattr(pool.conexion, "cursor", cursor)
    for _ in range(3):
        buenas.append(await cola.encolar(_fila(_facet())))

    with caplog.at_level(logging.WARNING, logger=reintento.LOGGER):
        resumen = await reintento.drenar(10)

    assert resumen["insertadas"] == 2 and resumen["quitadas"] == 2
    assert resumen["fallidas"] == 1
    assert len(_archivos(respaldo)) == 1, "la fila que falló SIGUE en el respaldo"


async def test_un_respaldo_ilegible_no_tumba_el_drenaje_pero_queda_en_error(
        respaldo, monkeypatch, caplog):
    """`cola.leer_pendientes` propaga OSError a propósito -- una cola ilegible
    no puede quedar muda. El que envuelve es el drenaje, y lo hace RUIDOSO:
    sin un ERROR en el log, una cola que no se puede leer crece invisible."""
    async def ilegible(limite):
        raise OSError("Permission denied")

    monkeypatch.setattr(reintento.cola, "leer_pendientes", ilegible)

    with caplog.at_level(logging.ERROR, logger=reintento.LOGGER):
        resumen = await reintento.drenar(10)

    assert resumen["leidas"] == 0 and resumen["motivo"]
    assert "Permission denied" in resumen["motivo"]
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


async def test_drenar_nunca_propaga_hacia_afuera(respaldo, monkeypatch):
    """Corre en una tarea de fondo: una excepción que salga mata el loop y el
    respaldo deja de drenarse hasta el próximo reinicio."""
    async def explota(limite):
        raise RuntimeError("cualquier cosa")

    monkeypatch.setattr(reintento.cola, "leer_pendientes", explota)
    resumen = await reintento.drenar(10)
    assert resumen["motivo"]


# --- el loop ---------------------------------------------------------------

async def test_no_arranca_bajo_pytest(respaldo, monkeypatch, caplog):
    ciclos = []

    async def falso(limite=None):
        ciclos.append(limite)
        return {}

    monkeypatch.setattr(reintento, "drenar", falso)
    with caplog.at_level(logging.WARNING, logger=reintento.LOGGER):
        await reintento.start_reintento_de_uso()

    assert ciclos == []
    assert "pytest" in caplog.text


async def test_al_arrancar_drena_una_vez_antes_del_primer_sleep(
        respaldo, monkeypatch):
    """Un reinicio después de una caída tiene que recuperar enseguida, no al
    minuto: con el intervalo en una hora igual tiene que haber drenado."""
    ciclos = []

    async def falso(limite=None):
        ciclos.append(limite)
        return {}

    monkeypatch.setattr(reintento, "drenar", falso)
    monkeypatch.setenv(reintento.VARIABLE_INTERVALO, "3600")

    tarea = asyncio.create_task(reintento.start_reintento_de_uso(forzado=True))
    await asyncio.sleep(0.05)
    try:
        assert len(ciclos) == 1
    finally:
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea


async def test_el_loop_repite_y_se_cancela_limpio(respaldo, monkeypatch):
    ciclos = []

    async def falso(limite=None):
        ciclos.append(limite)
        return {}

    monkeypatch.setattr(reintento, "drenar", falso)
    monkeypatch.setenv(reintento.VARIABLE_INTERVALO, "0.01")

    tarea = asyncio.create_task(reintento.start_reintento_de_uso(forzado=True))
    await asyncio.sleep(0.1)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert len(ciclos) >= 2
    assert tarea.cancelled()


async def test_un_ciclo_que_falla_no_mata_el_loop(respaldo, monkeypatch):
    """Ni siquiera si `drenar` rompiera su promesa de no propagar."""
    ciclos = []

    async def a_veces_explota(limite=None):
        ciclos.append(limite)
        if len(ciclos) == 1:
            raise RuntimeError("el primer ciclo se rompió")
        return {}

    monkeypatch.setattr(reintento, "drenar", a_veces_explota)
    monkeypatch.setenv(reintento.VARIABLE_INTERVALO, "0.01")

    tarea = asyncio.create_task(reintento.start_reintento_de_uso(forzado=True))
    await asyncio.sleep(0.1)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert len(ciclos) >= 2, "el loop murió en el primer ciclo fallido"


# --- el cableado -----------------------------------------------------------

def test_el_lifespan_arranca_el_drenaje():
    """Sin la llamada, el respaldo se llena y nadie lo vacía: ningún otro test
    lo notaría, porque todos ejercitan `drenar()` a mano."""
    import main

    arbol = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    (lifespan,) = [n for n in ast.walk(arbol)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    nombres = [n.func.id for n in ast.walk(lifespan)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "start_reintento_de_uso" in nombres


def test_el_drenaje_no_reimplementa_el_formato_del_respaldo():
    """El contrato del archivo lo define `uso/cola.py` (la copia que compara
    jax por AST). Si el drenaje leyera los archivos por su cuenta, un cambio de
    formato dejaría de verse acá."""
    fuente = Path(reintento.__file__).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    modulos = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos.update(a.name.split(".")[0] for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            modulos.add(nodo.module.split(".")[0])
    assert "json" not in modulos and "pathlib" not in modulos
    assert "uso" in modulos
