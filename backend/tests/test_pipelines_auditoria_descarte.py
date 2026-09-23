"""GET /api/pipelines/{id}/auditoria-descarte (2026-09-22, cierre de los dos
huecos que dejó abiertos la revisión final de la rama Descartar Pipelines).

Por qué: PIPELINE_DISCARDED/RECOVERED/HIDDEN/RESTORED se escriben en
jacobs_events en la MISMA transacción que el CAS (jax, jacobs/routes.py::
transicion_descarte -> store.pipeline_transicion_descarte), con payload
{"user_id", "desde", "a"} -- pero ninguna pantalla los muestra: hoy se leen
con `mysql` a mano. Esto expone esos cuatro tipos de evento, y sólo esos
cuatro (nunca el resto de jacobs_events -- STEP_FAILED, PIPELINE_ABORTED,
etc.), ordenados del más nuevo al más viejo.

Permisos: el dueño (_require_pipeline_owner, MISMA regla que /results y
GET/{id} -- un hidden sigue dando 404 a un dueño no-superadmin) O el
superadmin (mismo patrón de rama que recover_pipeline: _require_pipeline_exists,
sin exigir dueño). Que la auditoría muestre el user_id del PROPIO
superadmin es intencional (decisión del coordinador, fix round 1): es el
punto de una auditoría, no una fuga -- ver el comentario en
api/pipelines.py::auditoria_descarte.

Índice: fix round 1 (MAJOR-1, revisión adversarial de PR 151) -- la
versión original forzaba `idx_events_pipeline (pipeline_id)`, que es
EXACTAMENTE el plan que el Ruling R20 (2026-09-17, comentario de
`sql_eventos_de_causa` en api/pipelines.py) midió como lento: examina TODOS
los eventos del pipeline (~221 medidos, 8,1 ms, p95 147 ms a c=25) para
devolver 0-4. Se corrigió al MISMO patrón que `sql_eventos_de_causa`: SIN
`FORCE INDEX` -- se deja que el optimizador elija.

Fix round 2 (MAJOR-A/B, misma revisión): la ronda 1 TAMBIÉN había quitado
el `ORDER BY`/`LIMIT` del SQL, razonando que un `LIMIT` sin `ORDER BY`
tomaría filas arbitrarias -- cierto, pero `ORDER BY id DESC LIMIT %s` SÍ
estaba disponible, y sin él `fetchall()` traía TODAS las filas de
auditoría del pipeline (con su `payload`) a Python en cada apertura del
detalle: un pipeline ciclado 5.000 veces traía 5.000 filas, no 4. Se
agregó `ORDER BY id DESC LIMIT %s` sobre UNA consulta con `event_type IN
(...)`.

Fix round 3 (MAJOR-2, misma revisión): ese `ORDER BY ... LIMIT` sobre un
`IN (...)` cambió el EJE del costo -- y el eje viejo (el que cubría R20)
se quedó sin cobertura otra vez, esta vez de un modo más traicionero:
INESTABLE. Con pocas filas de auditoría (bajo el límite) y mucho ruido
MÁS NUEVO (id más alto), el optimizador elige entre `idx_events_
pipeline_tipo` (bueno) e `idx_events_pipeline` (malo con este SQL --
escanea el rango de id completo saltando cada fila no-auditoría) según
las estadísticas del momento: el MISMO par (20 filas de auditoría, 2.000
de ruido) dio Handler_read=44 en una corrida y Handler_read=2.021 en
otra, sin cambiar el SQL ni los datos. Confiar en el optimizador para
este caso es el mismo error que MAJOR-1 ya corrigió una vez.

Se reemplazó la consulta única con `IN (...)` por CUATRO consultas, una
por `event_type` (`SQL_AUDITORIA_DESCARTE_POR_TIPO`), fusionadas y
ordenadas en Python.

Fix round 4 (MAJOR-2, misma revisión, decisión del coordinador):
CORRECCIÓN sobre lo que decía este párrafo desde el principio de la
ronda 3 -- ahí se aceptaba que la consulta por tipo, SIN `FORCE INDEX`,
no eligiera siempre `idx_events_pipeline_tipo`, razonando que el costo
quedaba acotado igual "con cualquiera de los dos planes". Eso era una
OBSERVACIÓN sobre los casos medidos hasta esa ronda, no una cota real: el
caso disperso la rompe -- 20 filas de auditoría (5 por tipo) + 2.000
`STEP_FAILED` MÁS NUEVOS. Si una consulta cae en `idx_events_pipeline`
(seguía siendo `possible_key` sin el `FORCE`), escanea hacia atrás
filtrando por tipo y NUNCA junta sus 51 -- tiene que recorrer el rango
COMPLETO, ~2.020 lecturas por consulta que cae mal, hasta ~8.080 si caen
mal las cuatro: CUATRO VECES peor que los 2.021 de la consulta única que
motivaron esta ronda.

Se agregó `FORCE INDEX (idx_events_pipeline_tipo)` a
`SQL_AUDITORIA_DESCARTE_POR_TIPO`. Esto NO es el mismo error que MAJOR-1
(forzar `idx_events_pipeline`, el índice MALO para esa consulta) -- acá
se fuerza el índice CORRECTO para esta forma exacta de consulta: con
`(pipeline_id, event_type)` más la PK que InnoDB agrega sola a todo
índice secundario, una igualdad de `event_type` con `ORDER BY id DESC
LIMIT 51` es un recorrido hacia atrás de exactamente 51 entradas del
índice -- sin filesort, sin otra forma de resolverlo que evaluar. El
índice está garantizado en producción: lo crea `init_tables()` del repo
`jax` (jacobs/store.py, Ruling R20, 2026-09-17 -- ANTERIOR a esta rama,
a diferencia de `idx_pipelines_visibles`, que sí es más nuevo que
producción) y `jax/tests/test_jacobs_events_indice_causa_db.py::
test_init_tables_crea_idx_events_pipeline_tipo` prueba que existe.

Con el `FORCE INDEX`, el tope de `Handler_read ≤ 4×(limite+1)` DEJA de
ser una observación sobre los casos que se sembraron y pasa a ser una
cota real, construida por el plan -- por eso los tests de abajo ya
afirman el nombre del índice en las cuatro consultas, no sólo el
Handler_read (MINOR de esta ronda: antes sólo se afirmaba
`table == "jacobs_events"`, cierto de cualquier plan).

Medido con EXPLAIN + Handler_read reales:
- Ruido de 221 eventos STEP_FAILED, MÁS VIEJOS que la auditoría (mismo
  que R20, `test_auditoria_descarte_no_escanea_todo_el_pipeline` más
  abajo): Handler_read TOTAL=8 para 4 filas devueltas.
- 5.000 eventos DE AUDITORÍA sembrados, sin ruido
  (`test_auditoria_descarte_acota_incluso_con_miles_de_eventos_de_auditoria`):
  Handler_read TOTAL acotado por 4×51, medido más abajo, SIEMPRE con
  `idx_events_pipeline_tipo` en las cuatro.
- Pocas filas de auditoría (20) + miles de ruido MÁS NUEVO (el caso
  disperso, antes inestable --
  `test_auditoria_descarte_con_ruido_mas_nuevo_no_escanea_el_pipeline`):
  con el `FORCE INDEX`, Handler_read=24 EXACTO (no un rango) en 5-7
  corridas repetidas, siempre `idx_events_pipeline_tipo` en las cuatro.

El test ya NO afirma el nombre del índice ni la ausencia de "Using
filesort" -- afirma el Handler_read real (rows examined), que es lo que
de verdad importa."""
import json
import time
import uuid
from functools import partial

from tests.identidades import cabeceras, sql, uid
from tests.test_pipelines_descarte import _insertar_pipeline, _borrar_pipelines

TENANT = "auditoria-descarte-t2"

TIPOS_DE_DESCARTE = ("PIPELINE_DISCARDED", "PIPELINE_RECOVERED", "PIPELINE_HIDDEN", "PIPELINE_RESTORED")


async def _insertar_evento(pipeline_id, event_type, payload, ts):
    await sql(
        "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
        "VALUES (%s, NULL, %s, %s, %s)",
        (pipeline_id, event_type, json.dumps(payload), ts),
    )


async def _borrar_eventos(pipeline_id):
    await sql("DELETE FROM jacobs_events WHERE pipeline_id=%s", (pipeline_id,))


async def _explain_y_handler_read_por_tipo(pid, limite_mas_uno):
    """Fix round 3 (MAJOR-2): mide las CUATRO consultas por tipo como una
    unidad -- FLUSH STATUS antes de las cuatro, Handler_read después de
    las cuatro, en la MISMA conexión (es un contador de sesión). Devuelve
    la lista de los cuatro EXPLAIN (uno por tipo) para que el test pueda
    afirmar que NINGUNO usa el índice malo -- MINOR (fix round 4): antes
    esta función devolvía los EXPLAIN pero ningún test leía `explain["key"]`,
    sólo `explain["table"]` (cierto de CUALQUIER plan, no prueba nada).
    Con `FORCE INDEX (idx_events_pipeline_tipo)` en
    `SQL_AUDITORIA_DESCARTE_POR_TIPO`, la clave es determinista -- los
    tests de abajo ahora sí la afirman."""
    from db.connection import get_pool
    from api.pipelines import SQL_AUDITORIA_DESCARTE_POR_TIPO

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            explains = []
            for tipo in TIPOS_DE_DESCARTE:
                await cur.execute("EXPLAIN " + SQL_AUDITORIA_DESCARTE_POR_TIPO, (pid, tipo, limite_mas_uno))
                cols = [d[0] for d in cur.description]
                explains.append(dict(zip(cols, await cur.fetchone())))
            await cur.execute("FLUSH STATUS")
            filas = []
            for tipo in TIPOS_DE_DESCARTE:
                await cur.execute(SQL_AUDITORIA_DESCARTE_POR_TIPO, (pid, tipo, limite_mas_uno))
                filas.extend(await cur.fetchall())
            await cur.execute("SHOW SESSION STATUS LIKE 'Handler_read%'")
            handler = {k: int(v) for k, v in await cur.fetchall()}
    return explains, handler, filas


def test_auditoria_de_un_pipeline_ajeno_es_404(client):
    duenio = uid(client, "auditoria-c1-duenio", "operator")
    uid(client, "auditoria-c1-otro", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "aborted"))
    try:
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c1-otro", "operator", tenant_id=TENANT))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "pipeline_no_encontrado"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_dueno_trae_solo_los_cuatro_tipos_ordenados_del_mas_nuevo(client):
    duenio = uid(client, "auditoria-c2-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": 0}, ahora - 10)
        client.portal.call(_insertar_evento, pid, "PIPELINE_DISCARDED",
                           {"user_id": duenio, "desde": "aborted", "a": "discarded"}, ahora - 5)
        client.portal.call(_insertar_evento, pid, "PIPELINE_RECOVERED",
                           {"user_id": duenio, "desde": "discarded", "a": "aborted"}, ahora - 3)
        client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": 1}, ahora - 1)
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c2-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        eventos = resp.json()["eventos"]
        assert [e["event_type"] for e in eventos] == ["PIPELINE_RECOVERED", "PIPELINE_DISCARDED"]
        assert eventos[0]["user_id"] == duenio
        assert eventos[0]["desde"] == "discarded"
        assert eventos[0]["a"] == "aborted"
        assert eventos[0]["ts"] == ahora - 3
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_dueno_trae_los_cuatro_tipos_los_cuatro(client):
    """Fix round 3 (MAJOR-2): ningún otro test sembraba los CUATRO tipos a
    la vez -- una mutación real (saltear PIPELINE_RESTORED del loop de
    `auditoria_descarte`, ver el reporte de la tarea) pasaba TODA la
    suite en verde sin este test. Con las cuatro consultas por tipo
    (fix round 3), cada tipo se pide y se fusiona por separado -- este
    test es el único que prueba que los CUATRO, no sólo dos o tres,
    efectivamente vuelven."""
    duenio = uid(client, "auditoria-c2b-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        client.portal.call(_insertar_evento, pid, "PIPELINE_DISCARDED",
                           {"user_id": duenio, "desde": "aborted", "a": "discarded"}, ahora - 30)
        client.portal.call(_insertar_evento, pid, "PIPELINE_HIDDEN",
                           {"user_id": duenio, "desde": "discarded", "a": "hidden"}, ahora - 20)
        client.portal.call(_insertar_evento, pid, "PIPELINE_RESTORED",
                           {"user_id": duenio, "desde": "hidden", "a": "discarded"}, ahora - 10)
        client.portal.call(_insertar_evento, pid, "PIPELINE_RECOVERED",
                           {"user_id": duenio, "desde": "discarded", "a": "aborted"}, ahora)
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c2b-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        eventos = resp.json()["eventos"]
        assert [e["event_type"] for e in eventos] == [
            "PIPELINE_RECOVERED", "PIPELINE_RESTORED", "PIPELINE_HIDDEN", "PIPELINE_DISCARDED"]
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_de_un_pipeline_sin_eventos_de_descarte_es_lista_vacia(client):
    duenio = uid(client, "auditoria-c3-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "completed"))
    try:
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c3-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        assert resp.json()["eventos"] == []
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_de_un_hidden_para_su_dueno_no_superadmin_es_404(client):
    duenio = uid(client, "auditoria-c4-duenio", "operator")
    pid = str(uuid.uuid4())
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "hidden",
                       status_previo="aborted", descartado_por=duenio, descartado_at=time.time()))
    try:
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-c4-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "pipeline_no_encontrado"
    finally:
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_superadmin_sobre_el_pipeline_de_otro_es_200(client, client_superadmin):
    duenio = uid(client, "auditoria-c5-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "hidden",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        client.portal.call(_insertar_evento, pid, "PIPELINE_HIDDEN",
                           {"user_id": "superadmin-1", "desde": "discarded", "a": "hidden"}, ahora)
        resp = client_superadmin.get(f"/api/pipelines/{pid}/auditoria-descarte")
        assert resp.status_code == 200, resp.text
        eventos = resp.json()["eventos"]
        assert [e["event_type"] for e in eventos] == ["PIPELINE_HIDDEN"]
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_del_superadmin_de_un_pipeline_inexistente_es_404(client_superadmin):
    resp = client_superadmin.get(f"/api/pipelines/{uuid.uuid4()}/auditoria-descarte")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "pipeline_no_encontrado"


def test_auditoria_de_un_id_con_forma_invalida_es_400(client):
    resp = client.get("/api/pipelines/abc%3Fx=1/auditoria-descarte",
                      headers=cabeceras(client, "auditoria-c6", "operator"))
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "pipeline_id_invalido"


def test_auditoria_descarte_no_escanea_todo_el_pipeline(client):
    """MAJOR-1 (fix round 1, revisión adversarial de PR 151): la propiedad
    REAL -- Handler_read acotado por las filas de AUDITORÍA, no por el
    total de eventos del pipeline -- no sólo la clave del plan (mismo
    criterio que jax y que el resto de los EXPLAIN de esta tarea: un
    EXPLAIN que sólo mira el nombre del índice se puede volver a romper sin
    que ningún test lo note, como pasó acá con el FORCE INDEX original)."""
    from api.pipelines import LIMITE_AUDITORIA_DESCARTE

    pid = str(uuid.uuid4())
    ahora = time.time()
    try:
        # Mismo ruido que midió el Ruling R20 (comentario de
        # sql_eventos_de_causa, api/pipelines.py): 221 eventos STEP_FAILED
        # del MISMO pipeline, MÁS VIEJOS que la auditoría -- sin esto, con
        # 0-1 filas el optimizador puede elegir cualquier índice y empatar.
        for i in range(221):
            client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": i}, ahora + i)
        for i, tipo in enumerate(TIPOS_DE_DESCARTE):
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": "x", "desde": "a", "a": "b"}, ahora + 300 + i)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)

        explains, handler, filas = client.portal.call(
            _explain_y_handler_read_por_tipo, pid, LIMITE_AUDITORIA_DESCARTE + 1)
        for explain in explains:
            assert explain["table"] == "jacobs_events"
            # Fix round 4 (MINOR): con FORCE INDEX (decisión del
            # coordinador), la clave es DETERMINISTA -- se afirma, no sólo
            # el Handler_read.
            assert explain["key"] == "idx_events_pipeline_tipo", explain
        assert len(filas) == 4, filas
        total = sum(handler.values())
        # Medido: 8 exacto (4 filas de auditoría). Margen chico, ya no un
        # canario: con FORCE INDEX el plan no puede caer en
        # idx_events_pipeline.
        assert total <= 20, (
            f"{total} lecturas Handler_read para 4 filas de auditoría -- "
            f"huele a que el motor está tocando los 221 eventos NO-auditoría "
            f"del pipeline: {handler}"
        )
    finally:
        client.portal.call(_borrar_eventos, pid)


def test_auditoria_descarte_acota_incluso_con_miles_de_eventos_de_auditoria(client):
    """MAJOR-A/B (fix round 2, revisión adversarial de PR 151): el
    escenario REAL que el revisor describió -- un pipeline ciclado
    discard/recover miles de veces -- no tiene ruido STEP_FAILED que
    filtrar: son TODOS eventos de auditoría. Las consultas por tipo tienen
    que acotar el costo por el LIMIT, no por el total de filas de
    auditoría que existan."""
    from api.pipelines import LIMITE_AUDITORIA_DESCARTE

    pid = str(uuid.uuid4())
    ahora = time.time()
    try:
        for i in range(5000):
            tipo = "PIPELINE_DISCARDED" if i % 2 == 0 else "PIPELINE_RECOVERED"
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": "x", "desde": "a", "a": "b"}, ahora + i)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)

        explains, handler, filas = client.portal.call(
            _explain_y_handler_read_por_tipo, pid, LIMITE_AUDITORIA_DESCARTE + 1)
        for explain in explains:
            assert explain["table"] == "jacobs_events"
            assert explain["key"] == "idx_events_pipeline_tipo", explain
        # Cada tipo trae hasta limite+1 -- PIPELINE_DISCARDED y
        # PIPELINE_RECOVERED llegan al tope (51 cada uno, 2500 sembradas de
        # cada uno), HIDDEN/RESTORED traen 0.
        assert len(filas) == 2 * (LIMITE_AUDITORIA_DESCARTE + 1), filas
        total = sum(handler.values())
        # Medido: 204 exacto (2×51 de DISCARDED/RECOVERED + 2×0 de
        # HIDDEN/RESTORED, que confirman "0 filas" sin escanear las 5.000).
        # Margen chico, ya no un canario.
        assert total <= 220, (
            f"{total} lecturas Handler_read -- huele a que el motor está "
            f"trayendo las 5.000 filas de auditoría sembradas en vez de "
            f"acotar por el LIMIT: {handler}"
        )
    finally:
        client.portal.call(_borrar_eventos, pid)


def test_auditoria_descarte_con_ruido_mas_nuevo_no_escanea_el_pipeline(client):
    """MAJOR-2 (fix round 3, revisión adversarial de PR 151; FORCE INDEX
    en fix round 4, decisión del coordinador): el escenario que rompía la
    consulta única (`ORDER BY id DESC LIMIT %s` sobre `event_type IN
    (...)`) -- pocas filas de auditoría (bajo el límite) y miles de
    eventos NO-auditoría MÁS NUEVOS (id más alto). Con la consulta única,
    el optimizador podía elegir `idx_events_pipeline` (malo: escanea el
    rango de id completo saltando el ruido) de forma INESTABLE -- medido
    a mano, Handler_read pasaba de 44 a 2.021 entre corridas idénticas,
    sin cambiar SQL ni datos. La ronda 3 cambió a consultas por tipo pero
    SIN forzar el índice -- ese mismo caso disperso seguía pudiendo caer
    en `idx_events_pipeline` (era `possible_key` igual), y ahí es CUATRO
    VECES peor que la consulta única (hasta ~8.080, una por cada de las
    cuatro consultas que cayera mal). Con `FORCE INDEX
    (idx_events_pipeline_tipo)` (round 4) no hay esa alternativa: el plan
    es determinista, y este test ya no es un canario -- es la cota real."""
    from api.pipelines import LIMITE_AUDITORIA_DESCARTE

    pid = str(uuid.uuid4())
    ahora = time.time()
    try:
        # 20 filas de auditoría (bajo el límite de 50) -- el caso medido
        # como inestable con la consulta única.
        for i in range(20):
            tipo = TIPOS_DE_DESCARTE[i % 4]
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": "x", "desde": "a", "a": "b"}, ahora + i)
        # 2.000 eventos NO-auditoría MÁS NUEVOS (ids más altos) -- el
        # ruido que el índice malo tendría que saltar uno por uno.
        for i in range(2000):
            client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": i}, ahora + 20 + i)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)

        explains, handler, filas = client.portal.call(
            _explain_y_handler_read_por_tipo, pid, LIMITE_AUDITORIA_DESCARTE + 1)
        for explain in explains:
            assert explain["table"] == "jacobs_events"
            assert explain["key"] == "idx_events_pipeline_tipo", explain
        assert len(filas) == 20, filas
        total = sum(handler.values())
        # Medido: 24 EXACTO (4 tipos × 6 lecturas cada uno, 5 filas + 1 de
        # confirmación de fin de rango por tipo), en 5-7 corridas repetidas
        # -- ya no un rango ("de 44 a 2.021" como sin el FORCE). El tope de
        # 220 deja margen sin dejar pasar un regreso al escaneo completo
        # (~2.020 por consulta que cayera mal, hasta ~8.080 las cuatro --
        # lo que este mismo escenario media SIN el FORCE INDEX).
        assert total <= 220, (
            f"{total} lecturas Handler_read para 20 filas de auditoría con "
            f"2.000 eventos más nuevos -- huele a que el motor está "
            f"escaneando el ruido en vez de acotar por tipo: {handler}"
        )
    finally:
        client.portal.call(_borrar_eventos, pid)


def test_auditoria_descarte_topa_en_el_limite_muestra_los_mas_nuevos_y_marca_truncado(client):
    """Fix round 1 + fix round 2 (MAJOR-A/B): discard/recover es repetible
    sin tope -- un pipeline ciclado muchas veces no puede devolver una
    respuesta sin cota, el corte tiene que quedarse con los MÁS NUEVOS (no
    una porción arbitraria del orden del índice), y la respuesta tiene que
    DECIR que hay más -- una auditoría que se calla en 50 sin avisar
    informa MENOS de lo que pasó."""
    from api.pipelines import LIMITE_AUDITORIA_DESCARTE

    duenio = uid(client, "auditoria-limite-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        n = LIMITE_AUDITORIA_DESCARTE + 5
        for i in range(n):
            tipo = "PIPELINE_DISCARDED" if i % 2 == 0 else "PIPELINE_RECOVERED"
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": duenio, "desde": "x", "a": "y"}, ahora + i)
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-limite-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        eventos = cuerpo["eventos"]
        assert len(eventos) == LIMITE_AUDITORIA_DESCARTE
        assert eventos[0]["ts"] == ahora + (n - 1)
        assert eventos[-1]["ts"] == ahora + (n - LIMITE_AUDITORIA_DESCARTE)
        assert cuerpo["truncado"] is True
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])


def test_auditoria_descarte_sin_llegar_al_limite_truncado_es_false(client):
    """Complemento del test de arriba: con MENOS eventos que el límite,
    `truncado` tiene que decir que no falta nada -- sin este test, un
    `truncado` que siempre da `True` (o que nunca se calculó bien)
    pasaría igual el test del límite."""
    duenio = uid(client, "auditoria-sintrunc-duenio", "operator")
    pid = str(uuid.uuid4())
    ahora = time.time()
    client.portal.call(partial(_insertar_pipeline, pid, duenio, TENANT, "discarded",
                       status_previo="aborted", descartado_por=duenio, descartado_at=ahora))
    try:
        client.portal.call(_insertar_evento, pid, "PIPELINE_DISCARDED",
                           {"user_id": duenio, "desde": "aborted", "a": "discarded"}, ahora)
        resp = client.get(f"/api/pipelines/{pid}/auditoria-descarte",
                          headers=cabeceras(client, "auditoria-sintrunc-duenio", "operator", tenant_id=TENANT))
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert len(cuerpo["eventos"]) == 1
        assert cuerpo["truncado"] is False
    finally:
        client.portal.call(_borrar_eventos, pid)
        client.portal.call(_borrar_pipelines, [pid])
