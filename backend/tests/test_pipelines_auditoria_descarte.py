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
agregó `ORDER BY id DESC LIMIT %s` (limite+1, mismo idioma que
`list_pipelines`) -- el filesort que esto puede causar corre sobre las
filas de AUDITORÍA nada más (el mismo conjunto chico que ya se recortaba
en Python), no sobre el total de eventos del pipeline: el hallazgo de R20
es sobre escanear TODO el pipeline, no sobre ordenar unas pocas filas ya
filtradas, y no aplica acá.

Medido con EXPLAIN + Handler_read reales:
- Ruido de 221 eventos STEP_FAILED (mismo que R20,
  `test_auditoria_descarte_no_escanea_todo_el_pipeline` más abajo): SIN
  hint, el optimizador elige `idx_events_pipeline_tipo` -- Handler_read
  TOTAL=8 para 4 filas devueltas (antes: 204 con el índice forzado).
- 5.000 eventos DE AUDITORÍA sembrados (el escenario real de MAJOR-A/B,
  `test_auditoria_descarte_acota_incluso_con_miles_de_eventos_de_auditoria`
  más abajo): `ORDER BY id DESC LIMIT 51` -- Handler_read TOTAL=51 para 51
  filas devueltas (antes de este fix: 5.000 filas traídas a Python en
  cada pedido).

El test ya NO afirma el nombre del índice ni la ausencia de "Using
filesort" -- afirma el Handler_read real (rows examined), que es lo que
de verdad importa: un filesort sobre 51 filas es gratis, escanear 5.000
no lo es, tenga o no esa etiqueta el EXPLAIN."""
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
    from api.pipelines import LIMITE_AUDITORIA_DESCARTE, SQL_AUDITORIA_DESCARTE
    from tests.test_pipelines_descarte import _explain_y_handler_read

    pid = str(uuid.uuid4())
    ahora = time.time()
    try:
        # Mismo ruido que midió el Ruling R20 (comentario de
        # sql_eventos_de_causa, api/pipelines.py): 221 eventos STEP_FAILED
        # del MISMO pipeline -- sin esto, con 0-1 filas el optimizador
        # puede elegir cualquier índice y empatar.
        for i in range(221):
            client.portal.call(_insertar_evento, pid, "STEP_FAILED", {"step_index": i}, ahora + i)
        for i, tipo in enumerate(TIPOS_DE_DESCARTE):
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": "x", "desde": "a", "a": "b"}, ahora + 300 + i)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)

        args = (pid,) + TIPOS_DE_DESCARTE + (LIMITE_AUDITORIA_DESCARTE + 1,)
        explain, handler, filas = client.portal.call(_explain_y_handler_read, SQL_AUDITORIA_DESCARTE, args)
        assert explain["table"] == "jacobs_events"
        assert len(filas) == 4, filas
        total = sum(handler.values())
        # Medido: 8 (4 filas devueltas). El tope de 20 deja margen sin
        # dejar pasar un regreso al escaneo completo (225 filas: 221 de
        # ruido + 4 de auditoría).
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
    filtrar: son TODOS eventos de auditoría. `ORDER BY id DESC LIMIT %s`
    tiene que acotar el costo por el LIMIT, no por el total de filas de
    auditoría que existan.

    Comparación limpia medida a mano (no por mutación -- la ronda 1 ya
    aprendió esa lección, MAJOR-2: una mutación que rompe la cuenta de
    placeholders da un TypeError, no una prueba de comportamiento) contra
    la MISMA siembra de 5.000 filas de auditoría, la consulta vieja
    (`SELECT ... WHERE pipeline_id=%s AND event_type IN (...)`, sin
    `ORDER BY`/`LIMIT`, fix round 1) trae las 5.000 filas -- Handler_read
    TOTAL=5001. Con `ORDER BY id DESC LIMIT 51` (este fix): Handler_read
    TOTAL=51, abajo."""
    from api.pipelines import LIMITE_AUDITORIA_DESCARTE, SQL_AUDITORIA_DESCARTE
    from tests.test_pipelines_descarte import _explain_y_handler_read

    pid = str(uuid.uuid4())
    ahora = time.time()
    try:
        for i in range(5000):
            tipo = "PIPELINE_DISCARDED" if i % 2 == 0 else "PIPELINE_RECOVERED"
            client.portal.call(_insertar_evento, pid, tipo, {"user_id": "x", "desde": "a", "a": "b"}, ahora + i)
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)

        args = (pid,) + TIPOS_DE_DESCARTE + (LIMITE_AUDITORIA_DESCARTE + 1,)
        explain, handler, filas = client.portal.call(_explain_y_handler_read, SQL_AUDITORIA_DESCARTE, args)
        assert explain["table"] == "jacobs_events"
        assert len(filas) == LIMITE_AUDITORIA_DESCARTE + 1, filas
        total = sum(handler.values())
        # Medido: 51 (51 filas devueltas, exacto). Tope con margen; el
        # punto es que NO escale con las 5.000 sembradas.
        assert total <= 100, (
            f"{total} lecturas Handler_read para {LIMITE_AUDITORIA_DESCARTE + 1} filas pedidas -- "
            f"huele a que el motor está trayendo las 5.000 filas de auditoría "
            f"sembradas en vez de acotar por el LIMIT: {handler}"
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
