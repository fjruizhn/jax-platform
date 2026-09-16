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
from pymysql import err as mysql_err

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
    monkeypatch.delenv(reintento.VARIABLE_MAX_INTENTOS, raising=False)
    cola.reset_estado()
    reintento.reset_intentos()
    usage_mod.reset_registros_perdidos()
    yield directorio
    cola.reset_estado()
    reintento.reset_intentos()
    usage_mod.reset_registros_perdidos()


# --- pool de mentira (para los tests que no necesitan la base) --------------
class _Cursor:
    def __init__(self, registro, rowcount, error):
        self.registro = registro
        self.rowcount = rowcount
        self.error = error

    async def execute(self, consulta, args=None):
        # `error` puede ser una excepción (falla todo) o un callable sobre los
        # args (falla SÓLO la fila que el test eligió): la fila venenosa
        # (Task 10) necesita distinguir "la base rechaza ESTA fila" de "la
        # base está caída", y con un error único para todo el lote los dos
        # casos se ven iguales.
        error = self.error(args) if callable(self.error) else self.error
        if error is not None:
            raise error
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


def test_el_lote_por_defecto_es_500(monkeypatch):
    # DECISIÓN de Fernando (2026-09-15) con números de la prueba de carga: 500
    # da 835 filas/s contra 747 con 200, y p99 del turno 1,60 ms contra 4,68
    # -- mejor en los dos ejes a la vez. Con 1000+ el p99 salta a 23 ms. El
    # número está fijado acá para que "más es mejor" no lo mueva sin medir.
    monkeypatch.delenv(reintento.VARIABLE_LOTE, raising=False)
    assert reintento.LOTE_POR_DEFECTO == 500
    assert reintento.lote() == 500


# --- Task 10 (2026-09-16): la fila venenosa ---------------------------------
# Una fila que el INSERT rechaza SIEMPRE (un `facet` de más de 30 chars, un
# `tokens_in` que no entra en el INT) no se quita del respaldo y se reintenta
# en cada ciclo para siempre: ocupa lugar contra el tope, gasta un INSERT por
# ciclo y nada lo dice. Pasados N intentos va a `rechazadas/`.
#
# El corazón de estos tests es la DISTINCIÓN: "falló el ciclo porque la base
# está caída" no es "falló esta fila porque la base la rechaza". Si se
# confunden, una caída larga manda la cola entera a cuarentena y el arreglo es
# peor que el defecto.

def _rechazo_de_la_base(codigo=1406, mensaje="Data too long for column 'facet' at row 1"):
    """Lo que tira MariaDB cuando el dato de ESTA fila no entra en la columna.
    1406 está mapeado a DataError por pymysql; 1292 (fecha imposible) NO está
    en el mapa y cae a OperationalError -- por eso la clasificación mira el
    código del servidor y no la clase de la excepción."""
    return mysql_err.DataError(codigo, mensaje)


def _caida_de_la_base():
    """Lo que tira el driver cuando la conexión se murió. Código de CLIENTE
    (2013), no del servidor: la base ni vio la fila."""
    return mysql_err.OperationalError(2013, "Lost connection to MySQL server during query")


def _rechazadas(directorio: Path):
    destino = directorio / reintento.SUBDIRECTORIO_RECHAZADAS
    if not destino.exists():
        return []
    return sorted(p.name for p in destino.glob(f"*{cola.SUFIJO}"))


def _envenenar(pool, spool_ids, error=None):
    """El INSERT de ESAS filas falla; el de las demás entra."""
    fallo = error if error is not None else _rechazo_de_la_base()
    pool.conexion.error = lambda args: fallo if args and args[0] in spool_ids else None


def test_el_maximo_de_intentos_sale_del_entorno_con_default(monkeypatch):
    # N = 3 y no 1: un lote puede fallar ENTERO por la base (un bloqueo, un
    # reinicio a mitad de ciclo) y no queremos cuarentena por eso. Tres
    # intentos consecutivos son tres ciclos -- tres minutos con el intervalo
    # por defecto -- y ninguna causa transitoria razonable dura eso sin
    # tocar el clasificador de abajo.
    monkeypatch.delenv(reintento.VARIABLE_MAX_INTENTOS, raising=False)
    assert reintento.MAX_INTENTOS_POR_DEFECTO == 3
    assert reintento.max_intentos() == 3

    monkeypatch.setenv(reintento.VARIABLE_MAX_INTENTOS, "5")
    assert reintento.max_intentos() == 5
    # Un valor mal escrito no puede apagar la cuarentena ni volverla inmediata.
    monkeypatch.setenv(reintento.VARIABLE_MAX_INTENTOS, "0")
    assert reintento.max_intentos() == 3


def test_solo_un_rechazo_de_la_base_cuenta_contra_el_maximo():
    """El clasificador, solo. Fail-closed: lo que no está en la lista de
    códigos de RECHAZO DE DATO se trata como transitorio y NO cuenta. Es la
    dirección segura: una fila venenosa que sobrevive unos ciclos de más es
    barato; una fila sana en cuarentena es un cobro perdido."""
    assert reintento.la_base_rechaza_esta_fila(_rechazo_de_la_base(1406))
    assert reintento.la_base_rechaza_esta_fila(_rechazo_de_la_base(1264))  # out of range
    # 1292 no está en el mapa de pymysql y llega como OperationalError: la
    # clase de la excepción NO alcanza para clasificar, el código sí.
    assert reintento.la_base_rechaza_esta_fila(
        mysql_err.OperationalError(1292, "Incorrect datetime value"))

    assert not reintento.la_base_rechaza_esta_fila(_caida_de_la_base())
    assert not reintento.la_base_rechaza_esta_fila(
        mysql_err.OperationalError(1040, "Too many connections"))
    assert not reintento.la_base_rechaza_esta_fila(
        mysql_err.OperationalError(1213, "Deadlock found"))
    assert not reintento.la_base_rechaza_esta_fila(
        mysql_err.ProgrammingError(1146, "Table 'axioma_usage' doesn't exist"))
    assert not reintento.la_base_rechaza_esta_fila(RuntimeError("cualquier cosa"))
    assert not reintento.la_base_rechaza_esta_fila(OSError("Connection refused"))


async def test_una_fila_que_la_base_rechaza_siempre_termina_en_cuarentena(
        respaldo, monkeypatch, caplog):
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    await cola.encolar(_fila(_facet()))          # una sana, que sí entra
    _envenenar(pool, {venenosa})

    # los dos primeros ciclos: la venenosa falla y SIGUE pendiente
    primero = await reintento.drenar(10)
    assert primero["fallidas"] == 1 and primero["rechazadas"] == 0
    assert primero["insertadas"] == 1, "la sana entró en el primer ciclo"
    assert _archivos(respaldo) == [f"{venenosa}{cola.SUFIJO}"]

    segundo = await reintento.drenar(10)
    assert segundo["rechazadas"] == 0, "dos intentos no alcanzan"
    assert reintento.intentos()[venenosa] == 2
    assert _archivos(respaldo) == [f"{venenosa}{cola.SUFIJO}"]

    with caplog.at_level(logging.ERROR, logger=reintento.LOGGER):
        tercero = await reintento.drenar(10)

    assert tercero["rechazadas"] == 1
    assert _archivos(respaldo) == [], "la venenosa dejó de tapar el paso"
    assert _rechazadas(respaldo) == [f"{venenosa}{cola.SUFIJO}"]
    # el motivo REAL de la base, redactado, guardado al lado
    motivo = (respaldo / reintento.SUBDIRECTORIO_RECHAZADAS /
              f"{venenosa}{reintento.SUFIJO_MOTIVO}").read_text(encoding="utf-8")
    assert "Data too long" in motivo
    # un ERROR con el spool_id y el motivo, UNA vez
    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errores) == 1
    assert venenosa in errores[0].getMessage() and "Data too long" in errores[0].getMessage()
    # y se ve en el tablero, como PÉRDIDA
    assert usage_mod.registros_perdidos_stats()["rechazadas"] == 1

    # el ciclo siguiente ya no la ve
    cuarto = await reintento.drenar(10)
    assert cuarto["leidas"] == 0 and cuarto["rechazadas"] == 0


async def test_una_caida_larga_de_la_base_no_manda_nada_a_cuarentena(
        respaldo, monkeypatch):
    """EL test de esta tarea. Con la base caída fallan TODAS las filas: si el
    contador no distinguiera "falló el ciclo" de "falló esta fila", una caída
    de más de N ciclos vaciaría la cola entera a `rechazadas/` -- convertiría
    una demora recuperable en una pérdida definitiva, que es exactamente lo
    contrario de para qué existe el respaldo."""
    ids = [await cola.encolar(_fila(_facet())) for _ in range(3)]

    # (a) la caída dura: el pool ni se consigue. Seis ciclos, el doble de N.
    _pool_caido(monkeypatch)
    for _ in range(6):
        resumen = await reintento.drenar(10)
        assert resumen["rechazadas"] == 0

    # (b) la caída fea: el pool se consigue pero la conexión está muerta y
    # explota fila por fila -- en el resumen se ve igual que tres filas
    # venenosas, y NO lo es.
    pool = _pool_de_mentira(monkeypatch)
    pool.conexion.error = _caida_de_la_base()
    for _ in range(6):
        resumen = await reintento.drenar(10)
        assert resumen["fallidas"] == 3
        assert resumen["rechazadas"] == 0

    assert len(_archivos(respaldo)) == 3, "no se perdió ninguna fila sana"
    assert _rechazadas(respaldo) == []
    assert usage_mod.registros_perdidos_stats()["rechazadas"] == 0

    # y cuando la base vuelve, entran las tres
    pool.conexion.error = None
    final = await reintento.drenar(10)
    assert final["insertadas"] == 3 and final["quitadas"] == 3
    assert _archivos(respaldo) == [] and _rechazadas(respaldo) == []
    assert sorted(a[0] for _c, a in pool.registro) == sorted(ids)


async def test_el_contador_se_reinicia_cuando_la_fila_entra(respaldo, monkeypatch):
    """Los intentos son CONSECUTIVOS. Una fila que falla dos veces por algo
    transitorio y después entra no puede arrastrar ese crédito."""
    pool = _pool_de_mentira(monkeypatch)
    spool_id = await cola.encolar(_fila(_facet()))
    _envenenar(pool, {spool_id})

    for _ in range(2):
        await reintento.drenar(10)
    assert reintento.intentos()[spool_id] == 2

    pool.conexion.error = None
    await reintento.drenar(10)
    assert spool_id not in reintento.intentos(), "entró: el contador vuelve a cero"
    assert _archivos(respaldo) == []


async def test_un_error_ajeno_a_la_fila_tambien_reinicia_su_contador(
        respaldo, monkeypatch):
    """Dos rechazos de dato, un bloqueo de la base, y el contador vuelve a
    cero: "consecutivos" quiere decir consecutivos."""
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    sana = await cola.encolar(_fila(_facet()))
    _envenenar(pool, {venenosa})

    for _ in range(2):
        await reintento.drenar(10)
    assert reintento.intentos()[venenosa] == 2

    # un deadlock: no es culpa de la fila. La sana ya entró, así que el ciclo
    # NO se ve como una caída -- y aun así el contador de la venenosa se cae.
    _envenenar(pool, {venenosa}, error=mysql_err.OperationalError(1213, "Deadlock found"))
    resumen = await reintento.drenar(10)
    assert resumen["fallidas"] == 1 and resumen["rechazadas"] == 0
    assert venenosa not in reintento.intentos()

    _envenenar(pool, {venenosa})
    for _ in range(2):
        assert (await reintento.drenar(10))["rechazadas"] == 0
    assert _rechazadas(respaldo) == [], "el tercer intento del contador viejo no cuenta"
    assert (await reintento.drenar(10))["rechazadas"] == 1


async def test_una_fila_en_cuarentena_no_se_lee_ni_cuenta_para_el_tope(
        respaldo, monkeypatch):
    """`rechazadas/` es un subdirectorio, igual que `corruptos/`: `_listar` y
    `_contar_barato` sólo miran el primer nivel. Si contara para el tope, una
    fila muerta le quitaría lugar a una viva."""
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    _envenenar(pool, {venenosa})
    for _ in range(3):
        await reintento.drenar(10)
    assert _rechazadas(respaldo) == [f"{venenosa}{cola.SUFIJO}"]

    assert await cola.leer_pendientes(100) == []
    assert await cola.contar_pendientes() == 0
    assert cola.estadisticas()["en_cola"] == 0
    # y el subdirectorio no es `corruptos/`: el motivo y la acción del admin
    # son distintos y no se pueden mezclar en la misma bandeja.
    assert reintento.SUBDIRECTORIO_RECHAZADAS != cola.SUBDIRECTORIO_CORRUPTOS
    assert cola.estadisticas()["corruptos"] == 0


async def test_si_no_se_puede_mover_la_fila_sigue_pendiente_y_no_se_cuenta(
        respaldo, monkeypatch, caplog):
    """Fail-soft: un disco que no deja mover no puede tirar el ciclo ni
    inventar una pérdida en el tablero."""
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    _envenenar(pool, {venenosa})

    def no_se_puede(spool_id, motivo):
        raise OSError("Read-only file system")

    monkeypatch.setattr(reintento, "_rechazar", no_se_puede)
    with caplog.at_level(logging.WARNING, logger=reintento.LOGGER):
        for _ in range(3):
            resumen = await reintento.drenar(10)

    assert resumen["rechazadas"] == 0
    assert _archivos(respaldo) == [f"{venenosa}{cola.SUFIJO}"]
    assert usage_mod.registros_perdidos_stats()["rechazadas"] == 0
    assert "Read-only file system" in caplog.text


async def test_un_ciclo_donde_no_entro_nada_y_hubo_error_ajeno_no_manda_a_cuarentena(
        respaldo, monkeypatch):
    """La SEGUNDA baranda. El caso mixto y feo: la venenosa ya tenía dos
    intentos y la conexión se muere a mitad del lote justo cuando saca el
    tercero. Vista fila por fila, la tercera falla es un rechazo de dato de
    verdad; visto el ciclo entero, no entró NADA y hubo errores que no son de
    dato -- tiene la forma de una caída. Ante la duda no se manda a
    cuarentena, que es la decisión que no se puede deshacer."""
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    sana = await cola.encolar(_fila(_facet()))
    _envenenar(pool, {venenosa})

    # dos ciclos sanos: la sana entra, la venenosa suma dos intentos
    await reintento.drenar(10)
    await cola.encolar(_fila(_facet(), spool_id=sana))
    await reintento.drenar(10)
    assert reintento.intentos()[venenosa] == 2

    # el ciclo feo: la venenosa saca su 1406 y el resto se muere
    otra = await cola.encolar(_fila(_facet()))
    caida = _caida_de_la_base()
    rechazo = _rechazo_de_la_base()
    pool.conexion.error = lambda args: rechazo if args and args[0] == venenosa else caida

    resumen = await reintento.drenar(10)

    assert (resumen["insertadas"], resumen["duplicadas"]) == (0, 0)
    assert resumen["fallidas"] == 2
    assert resumen["rechazadas"] == 0, "no entró nada: el ciclo no es evidencia"
    assert _rechazadas(respaldo) == []
    assert reintento.intentos() == {}, "un ciclo con forma de caída borra los contadores"
    assert sorted(_archivos(respaldo)) == sorted(
        [f"{venenosa}{cola.SUFIJO}", f"{otra}{cola.SUFIJO}"])

    # y ya con la base sana hacen falta los tres intentos de nuevo, desde cero
    _envenenar(pool, {venenosa})
    for esperado in (0, 0):
        assert (await reintento.drenar(10))["rechazadas"] == esperado
    assert (await reintento.drenar(10))["rechazadas"] == 1


async def test_un_ciclo_que_falla_entero_borra_los_contadores(respaldo, monkeypatch):
    """La TERCERA baranda, la del `except` de `drenar`: si el ciclo se cayó
    entero (el pool no se consigue, el respaldo no se puede leer) ninguna fila
    tuvo su oportunidad y ningún intento cuenta."""
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    await cola.encolar(_fila(_facet()))
    _envenenar(pool, {venenosa})
    await reintento.drenar(10)
    await cola.encolar(_fila(_facet()))
    await reintento.drenar(10)
    assert reintento.intentos()[venenosa] == 2

    _pool_caido(monkeypatch)
    assert (await reintento.drenar(10))["rechazadas"] == 0
    assert reintento.intentos() == {}, "el ciclo no corrió: los intentos se borran"

    # el tercer intento del contador viejo ya no existe
    _pool_de_mentira(monkeypatch)
    pool2 = reintento.get_pool
    monkeypatch.setattr(reintento, "get_pool", pool2)
    await cola.encolar(_fila(_facet()))
    pool = await reintento.get_pool()
    _envenenar(pool, {venenosa})
    assert (await reintento.drenar(10))["rechazadas"] == 0
    assert reintento.intentos()[venenosa] == 1
    assert _rechazadas(respaldo) == []


async def test_el_contador_no_queda_colgado_si_la_fila_ya_no_esta(respaldo, monkeypatch):
    """El contador sólo existe para una fila que falló, y una fila que falló
    sigue pendiente y es de las más viejas: vuelve al lote del ciclo siguiente.
    La que NO vuelve -- porque el tope la descartó por vieja (`_hacer_lugar`)
    o porque otro proceso la drenó -- dejaría su entrada colgada para siempre.
    Sin la poda, `_intentos` es un dict que sólo crece en un servicio que no se
    reinicia nunca."""
    pool = _pool_de_mentira(monkeypatch)
    venenosa = await cola.encolar(_fila(_facet()))
    _envenenar(pool, {venenosa})
    await reintento.drenar(10)
    assert reintento.intentos()[venenosa] == 1

    # el tope la descarta por vieja: el archivo ya no está
    (respaldo / f"{venenosa}{cola.SUFIJO}").unlink()
    await reintento.drenar(10)
    assert reintento.intentos() == {}, "el contador de una fila que ya no existe se poda"
