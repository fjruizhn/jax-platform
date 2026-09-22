"""Contrato de los endpoints de memoria. La pantalla es de superadmin: la
memoria es lo unico que el sistema acumula sobre nosotros.

`client_superadmin`: ver tests/conftest.py -- fixture CONSTRUIDO sobre
`client` + `tests.identidades.cabeceras`, que es el patron real de esta casa
(tests/test_config_admin_ajustes.py). El plan original daba por hecho un
`client_superadmin` que no existia (2026-09-20)."""
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest

from tests.identidades import sql


async def _crear_fact(fact_text, fact_type="technical", is_verified=False, expires_at=None):
    return await sql(
        "INSERT INTO facts (fact_uuid, fact_text, fact_type, is_verified, expires_at) "
        "VALUES (UUID(), %s, %s, %s, %s)",
        (fact_text, fact_type, is_verified, expires_at),
    )


async def _borrar_fact(fact_id):
    # Primero el hijo (bug corregido en esta misma ronda, 2026-09-20): un
    # /corregir corrido dentro del test deja `fact_id.superseded_by =
    # nuevo_id` -- el nuevo fact no queda "con superseded_by = fact_id" (ese
    # campo es de QUIEN fue reemplazado, no del reemplazo). Hay que leerlo y
    # borrar los dos ids, o el nuevo fact de cada corrección queda huérfano
    # en jax_memory_test para siempre (24 filas encontradas sueltas al medir).
    fila = await sql("SELECT superseded_by FROM facts WHERE id = %s", (fact_id,), True)
    nuevo_id = fila[0][0] if fila and fila[0][0] else None
    if nuevo_id:
        await sql("DELETE FROM facts WHERE id = %s", (nuevo_id,))
    await sql("DELETE FROM facts WHERE id = %s", (fact_id,))


@pytest.fixture(autouse=True)
def _hechos_de_prueba(request):
    """Hallazgo del RED (2026-09-20): jax_memory_test, compartida entre
    worktrees, no trae NINGUN fact sembrado -- sin esto, varios tests de esta
    suite (aprobar, corregir, vencidos) pasaban con listas VACIAS, sin
    ejercitar nada (un control que no falla no valida nada). Siembra un
    puñado de facts reales (3 sin verificar, 1 verificado, 1 vencido) y los
    borra al terminar, sin tocar lo que ya hubiera de otra sesión."""
    if "client" not in request.fixturenames:
        yield
        return
    cliente = request.getfixturevalue("client")
    ids = [
        cliente.portal.call(_crear_fact, "hecho de prueba memoria admin A"),
        cliente.portal.call(_crear_fact, "hecho de prueba memoria admin B"),
        cliente.portal.call(_crear_fact, "hecho de prueba memoria admin C"),
        cliente.portal.call(partial(_crear_fact, "hecho de prueba memoria admin verificado",
                                    is_verified=True)),
        cliente.portal.call(partial(_crear_fact, "hecho de prueba memoria admin vencido",
                                    expires_at="2020-01-01 00:00:00")),
    ]
    yield
    for fid in ids:
        cliente.portal.call(_borrar_fact, fid)


def test_listar_hechos_exige_superadmin(client):
    r = client.get("/api/admin/memoria/hechos")
    assert r.status_code == 401


def test_listar_hechos_devuelve_procedencia_siempre(client_superadmin):
    """«La memoria sin procedencia es otra forma de suposicion» -- spec §2.1.
    Un hecho sin de-donde-salio no se muestra: se muestra con la procedencia
    vacia y marcada, nunca sin el campo."""
    r = client_superadmin.get("/api/admin/memoria/hechos?limite=5")
    assert r.status_code == 200
    for hecho in r.json()["hechos"]:
        assert "procedencia" in hecho
        assert {"mensaje_id", "faceta"} <= set(hecho["procedencia"])


def test_los_vencidos_no_salen_salvo_que_se_pidan(client_superadmin):
    sin = client_superadmin.get("/api/admin/memoria/hechos?limite=200").json()["hechos"]
    con = client_superadmin.get("/api/admin/memoria/hechos?limite=200&incluir_vencidos=true").json()["hechos"]
    assert len(con) >= len(sin)
    assert all(not h["vencido"] for h in sin)


def test_aprobar_en_lote_registra_quien(client_superadmin):
    """Spec §2.2: aprobar en lote, porque revisar 115 de a uno no lo hace
    nadie -- y una funcion que nadie usa es igual a no tenerla."""
    ids = [h["id"] for h in client_superadmin.get(
        "/api/admin/memoria/hechos?verificado=false&limite=3").json()["hechos"]]
    r = client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": ids})
    assert r.status_code == 200 and r.json()["aprobados"] == len(ids)
    for h in client_superadmin.get("/api/admin/memoria/hechos?limite=200").json()["hechos"]:
        if h["id"] in ids:
            assert h["verificado"] is True
            assert h["verificado_por"] is not None, "aprobado sin dueno"


def test_corregir_no_borra_marca_como_superado(client_superadmin):
    """Spec §2.3 y el Protocolo de la Memoria Viva: una memoria falsa no se
    borra en silencio, se marca como corregida, con version nueva."""
    viejo = client_superadmin.get("/api/admin/memoria/hechos?limite=1").json()["hechos"][0]
    r = client_superadmin.post(f"/api/admin/memoria/hechos/{viejo['id']}/corregir",
                               json={"texto": "version corregida de prueba"})
    assert r.status_code == 200
    nuevo_id = r.json()["nuevo_id"]
    todos = client_superadmin.get(
        "/api/admin/memoria/hechos?limite=500&incluir_superados=true").json()["hechos"]
    por_id = {h["id"]: h for h in todos}
    assert por_id[viejo["id"]]["superado_por"] == nuevo_id
    assert viejo["id"] in por_id, "el hecho viejo desaparecio: eso es borrar"


def test_no_existe_endpoint_de_borrado(client_superadmin):
    """Fuera de alcance por decision del spec §5: borrar es perder la historia
    de lo que creimos. Este test ata esa decision."""
    r = client_superadmin.delete("/api/admin/memoria/hechos/1")
    assert r.status_code in (404, 405)


def test_nadie_puede_aprobar_automaticamente(client_superadmin):
    """Spec §2.2: no hay aprobacion automatica. Si el sistema se aprueba a si
    mismo, is_verified deja de significar algo."""
    import inspect
    from api.admin import memoria
    fuente = inspect.getsource(memoria)
    assert "auto_aprobar" not in fuente and "aprobar_todo" not in fuente


# ---------------------------------------------------------------------------
# M1 (auditoria adversarial 2026-09-20 sobre feat/memoria-admin): el contrato
# de tres estados de verify_fact/expire_fact (jax/memory/db.py) -- True =
# existe, False = no existe, None = no se pudo -- se aplastaba en los
# consumidores de este archivo. `None` (la base no respondio a MITAD del
# pedido) se leia igual que `False` (el hecho no existe): un 404 sobre un
# hecho que SI existe (caducar), o un 200 {"aprobados": 0} silencioso sin que
# el superadmin supiera que el resto del lote NUNCA se intento (aprobar).
# ---------------------------------------------------------------------------

def test_aprobar_con_la_base_caida_no_es_500_generico(client_superadmin, monkeypatch):
    """m8 (cierre jax-platform#146, ronda 6, SEGURIDAD): MAJOR N2 (ronda 5)
    abandono `MemoryDB.verify_fact` -- y con eso el contrato de tres estados
    de arriba tambien se perdio para este endpoint: el viejo test que lo
    cubria (`test_aprobar_con_la_base_caida_a_mitad_del_lote_no_es_200_
    silencioso`, mockeaba `verify_fact`) quedo inalcanzable y se borro
    (ronda 5). Con la base caida, `transaccion()` (sobre
    `db.connection.get_pool()`) levanta una excepcion de conexion SIN
    GUARDA -- FastAPI la convertia en un 500 generico. Se simula la caida
    en el punto exacto donde pasaba de verdad: `get_pool()` DENTRO de
    `db.transaccion` (lo que usa `transaccion()` para adquirir la conexion),
    no un mock de alto nivel."""
    from db import transaccion as transaccion_mod

    async def _get_pool_caida():
        raise OSError("Connect call failed ('127.0.0.1', 18080)")

    monkeypatch.setattr(transaccion_mod, "get_pool", _get_pool_caida)
    r = client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": [1]})
    assert r.status_code == 503, (
        f"esperaba 503 (memoria_no_disponible), no {r.status_code}: un "
        "fallo al conectar/adquirir la conexion es 'la base no respondio', "
        "no un 500 generico sin traducir")
    assert r.json()["detail"] == "memoria_no_disponible"


def test_aprobar_con_un_error_real_dentro_de_la_transaccion_no_se_disfraza_de_503(
        client_superadmin, monkeypatch):
    """Control del arreglo de arriba: el `except` de m8 atrapa SOLO el fallo
    de conectar/adquirir (`enter_async_context`), nunca lo que pase DENTRO
    de la transaccion ya abierta -- un error real ahi tiene que seguir
    siendo un 500 (o lo que sea), no disfrazarse de 503."""
    from api.admin import memoria

    id1 = client_superadmin.portal.call(_crear_fact, "aprobar control m8: hecho 1")
    try:
        async def _revienta(cur, autor, fact_id):
            raise RuntimeError("fallo real dentro de la transaccion, no de conexion")

        monkeypatch.setattr(memoria, "_aprobar_en_cursor", _revienta)
        with pytest.raises(RuntimeError, match="fallo real dentro de la transaccion"):
            client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": [id1]})
    finally:
        client_superadmin.portal.call(_borrar_fact, id1)


def test_aprobar_es_atomico_si_falla_a_mitad_del_lote_no_queda_nada_escrito(
        client_superadmin, monkeypatch):
    """MAJOR N2 (revision adversarial de jax-platform PR 146, ronda 5):
    `aprobar_hechos` dejo de llamar a `MemoryDB.verify_fact` (jax/memory/
    db.py, que no mira `superseded_by`/`expires_at` -- ver el docstring del
    endpoint en memoria.py) y pasa a leer con `FOR UPDATE` + escribir con
    `_aprobar_en_cursor`, TODO en la MISMA transaccion (mismo patron que
    `fundir_hechos`, jax-platform#107). Este test reemplaza al viejo (que
    mockeaba `verify_fact`, ya inalcanzable desde este endpoint) con el
    mismo principio de `test_fundir_es_atomico_si_falla_a_mitad_del_lote_
    no_queda_nada_escrito`: si algo revienta a mitad del lote, NADA de lo
    escrito antes en esa misma transaccion queda -- nunca un 200 con
    'aprobados' de MENOS que no avise que el resto del lote nunca se
    intento."""
    from api.admin import memoria

    id1 = client_superadmin.portal.call(_crear_fact, "aprobar atomico: hecho 1")
    id2 = client_superadmin.portal.call(_crear_fact, "aprobar atomico: hecho 2")
    llamadas = []
    original = memoria._aprobar_en_cursor

    async def _revienta_en_la_segunda(cur, autor, fact_id):
        llamadas.append(fact_id)
        if len(llamadas) == 2:
            raise RuntimeError("fallo simulado a mitad del lote de aprobar")
        await original(cur, autor, fact_id)

    monkeypatch.setattr(memoria, "_aprobar_en_cursor", _revienta_en_la_segunda)
    try:
        with pytest.raises(RuntimeError, match="fallo simulado"):
            client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": [id1, id2]})
        hechos = client_superadmin.get(
            "/api/admin/memoria/hechos?limite=500").json()["hechos"]
        por_id = {h["id"]: h for h in hechos}
        assert por_id[id1]["verificado"] is False, (
            "el primero del lote SI quedo aprobado: aprobo a medias")
    finally:
        client_superadmin.portal.call(_borrar_fact, id1)
        client_superadmin.portal.call(_borrar_fact, id2)


# ---------------------------------------------------------------------------
# MAJOR N2 (revision adversarial de jax-platform PR 146, ronda 5):
# `MemoryDB.verify_fact` no mira `superseded_by` ni `expires_at` -- desde una
# pantalla de Memoria vieja (otra pestana, u otro superadmin que no recargo)
# se podia aprobar un hecho ya vencido o ya superado por otro. El chequeo
# vive en este endpoint (el repo jax no se toca), lee el lote entero con
# `FOR UPDATE` y rechaza TODO el lote (todo o nada) si un solo id falla.
# ---------------------------------------------------------------------------

def test_aprobar_rechaza_un_hecho_vencido_con_409(client_superadmin):
    fid = client_superadmin.portal.call(
        partial(_crear_fact, "aprobar vencido", expires_at="2020-01-01 00:00:00"))
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": [fid]})
        assert r.status_code == 409 and r.json()["detail"] == "hecho_vencido"
        hechos = client_superadmin.get(
            "/api/admin/memoria/hechos?limite=500&incluir_vencidos=true").json()["hechos"]
        assert next(h for h in hechos if h["id"] == fid)["verificado"] is False, (
            "un hecho vencido NO puede quedar aprobado")
    finally:
        client_superadmin.portal.call(_borrar_fact, fid)


def test_aprobar_rechaza_un_hecho_ya_superado_con_409(client_superadmin):
    viejo = client_superadmin.portal.call(_crear_fact, "aprobar superado: viejo")
    nuevo = client_superadmin.portal.call(_crear_fact, "aprobar superado: nuevo")
    client_superadmin.portal.call(
        sql, "UPDATE facts SET superseded_by = %s, superseded_at = NOW() WHERE id = %s",
        (nuevo, viejo))
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": [viejo]})
        assert r.status_code == 409 and r.json()["detail"] == "hecho_superado"
        hechos = client_superadmin.get(
            "/api/admin/memoria/hechos?limite=500&incluir_superados=true").json()["hechos"]
        assert next(h for h in hechos if h["id"] == viejo)["verificado"] is False, (
            "un hecho ya superado NO puede quedar aprobado")
    finally:
        client_superadmin.portal.call(_borrar_fact, viejo)
        client_superadmin.portal.call(_borrar_fact, nuevo)


def test_aprobar_un_vencido_en_el_lote_tira_el_lote_entero_todo_o_nada(client_superadmin):
    """Un solo id vencido en el lote no puede tirar solo a ese id -- el lote
    ENTERO se rechaza (mismo principio que fundir): dos hechos sanos que
    viajaban en el mismo POST porque Fernando marco varios juntos no pueden
    quedar aprobados "por accidente" mientras el tercero explica el 409."""
    sano1 = client_superadmin.portal.call(_crear_fact, "aprobar lote: sano 1")
    sano2 = client_superadmin.portal.call(_crear_fact, "aprobar lote: sano 2")
    vencido = client_superadmin.portal.call(
        partial(_crear_fact, "aprobar lote: vencido", expires_at="2020-01-01 00:00:00"))
    try:
        r = client_superadmin.post(
            "/api/admin/memoria/hechos/aprobar", json={"ids": [sano1, sano2, vencido]})
        assert r.status_code == 409 and r.json()["detail"] == "hecho_vencido"
        hechos = client_superadmin.get(
            "/api/admin/memoria/hechos?limite=500").json()["hechos"]
        por_id = {h["id"]: h for h in hechos}
        assert por_id[sano1]["verificado"] is False, "todo o nada: sano1 no debia aprobarse"
        assert por_id[sano2]["verificado"] is False, "todo o nada: sano2 no debia aprobarse"
    finally:
        client_superadmin.portal.call(_borrar_fact, sano1)
        client_superadmin.portal.call(_borrar_fact, sano2)
        client_superadmin.portal.call(_borrar_fact, vencido)


def test_caducar_con_la_base_caida_no_es_404_sobre_un_hecho_que_existe(
        client_superadmin, monkeypatch):
    from api.admin import memoria as memoria_mod
    from unittest.mock import AsyncMock

    client_superadmin.get("/api/admin/memoria/hechos?limite=1")
    memoria = memoria_mod._chat_mod._memory
    assert memoria is not None, "la memoria no se conecto -- el test no probaria nada"

    monkeypatch.setattr(memoria, "expire_fact", AsyncMock(return_value=None))
    r = client_superadmin.post("/api/admin/memoria/hechos/1/caducar", json={})
    assert r.status_code == 503, (
        f"esperaba 503 (memoria_no_disponible), no {r.status_code}: un None "
        "de expire_fact es 'la base no respondio', y ANTES de este arreglo "
        "se leia como 404 'hecho_no_encontrado' sobre un hecho que SI existe")


def test_caducar_un_id_que_de_verdad_no_existe_sigue_dando_404(client_superadmin):
    """Control del arreglo de arriba: el chequeo de `ok is None` va ANTES del
    de `not ok`, pero el 404 real (hecho que de verdad no existe, `expire_fact`
    devolviendo `False`) tiene que seguir intacto."""
    r = client_superadmin.post(
        "/api/admin/memoria/hechos/999999999/caducar", json={})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Hora local vs UTC (2026-09-22). El frontend manda `new Date().toISOString()`
# -- SIEMPRE con `Z` (UTC) -- pero `@@session.time_zone` de la base es SYSTEM
# (Honduras, UTC-6): antes de este arreglo `datetime.fromisoformat(vence_at)`
# guardaba la hora UTC tal cual, como si fuera hora local, y el hecho
# quedaba "caducado" recien ~6 horas despues (visto en produccion con los
# hechos #140 y #15: `expires_at` 354 y 359 minutos DESPUES de `updated_at`).
# ---------------------------------------------------------------------------

# Margen de la aserción de cercanía (MAJOR 2, revisión adversarial
# jax-platform#147): un `expires_at <= NOW()` de un solo lado deja pasar
# una mutación que reste el desfase DOS veces (o cualquier otra que empuje
# `expires_at` bien atrás en el pasado) -- sigue siendo `<= NOW()`, así que
# ese control por si solo no prueba que la conversión fue CORRECTA, sólo que
# no quedó en el futuro. `ABS(TIMESTAMPDIFF(SECOND, expires_at, NOW())) <= 5`
# exige que el valor converitdo caiga CERCA de "ahora", no en cualquier
# punto del pasado. 5 segundos: margen generoso sobre la duración real de
# una request HTTP + un roundtrip a MariaDB local (medido: bajo 100 ms).
_MARGEN_CERCANIA_SEGUNDOS = 5


def test_caducar_con_hora_utc_deja_el_hecho_vencido_de_inmediato(client_superadmin):
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba caducar-hora-utc")
    try:
        vence_at = datetime.now(timezone.utc).isoformat()
        r = client_superadmin.post(
            f"/api/admin/memoria/hechos/{fact_id}/caducar",
            json={"vence_at": vence_at})
        assert r.status_code == 200, r.text

        fila = client_superadmin.portal.call(
            sql,
            "SELECT expires_at <= NOW(), "
            f"ABS(TIMESTAMPDIFF(SECOND, expires_at, NOW())) <= {_MARGEN_CERCANIA_SEGUNDOS}, "
            "expires_at, NOW() FROM facts WHERE id = %s",
            (fact_id,), True)
        vencido_ya, cerca_de_ahora, expires_at, ahora = fila[0]
        assert vencido_ya, (
            f"expires_at ({expires_at}) quedo DESPUES de NOW() de la base "
            f"({ahora}): la hora UTC con 'Z' se escribio sin convertir a la "
            "hora local de la base -- el defecto de las ~6 horas de retraso."
        )
        assert cerca_de_ahora, (
            f"expires_at ({expires_at}) quedo a mas de "
            f"{_MARGEN_CERCANIA_SEGUNDOS}s de NOW() de la base ({ahora}): "
            "'vencido ya' no alcanza, la conversion tiene que caer CERCA de "
            "ahora, no en cualquier punto del pasado (mutacion: restar el "
            "desfase dos veces seguiria dando <= NOW())."
        )
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_caducar_con_zona_no_utc_ni_local_deja_el_hecho_vencido_de_inmediato(
        client_superadmin):
    """MAJOR 1 (revision adversarial jax-platform#147): el test de arriba
    manda SIEMPRE offset +00:00 ('Z'). Si el runner de CI corre su MariaDB
    en UTC (la imagen oficial de Docker, por default), el bug ORIGINAL de
    esta pantalla -- pymysql/aiomysql ignoran el `tzinfo` de un `datetime` al
    escaparlo (`pymysql/converters.py::escape_datetime` sólo mira
    year/month/day/hour/minute/second, nunca `tzinfo`) y graban las cifras
    de reloj literales -- TAMBIEN deja `expires_at <= NOW()` ahi: si
    local=UTC, "grabar la hora UTC como si fuera local" da, por casualidad,
    el valor correcto. El control de arriba NO fallaria en ESE runner aunque
    el bug sea real (razonado por escrito acá; probado de verdad en
    `test_el_bug_original_fallaria_tambien_con_la_base_en_utc`, abajo, que
    fuerza una sesion en UTC de verdad).

    Mandar una zona que no sea ni la de la base (Honduras, UTC-6) ni UTC
    (+00:00) rompe esa casualidad: las cifras de reloj de +05:00 no
    coinciden con la hora de NINGUNA de las dos zonas posibles del runner
    (Honduras o UTC), asi que el bug queda expuesto sin importar en que zona
    corra el contenedor de MariaDB del job."""
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba caducar-zona-no-utc-ni-local")
    try:
        vence_at = datetime.now(timezone(timedelta(hours=5))).isoformat()
        r = client_superadmin.post(
            f"/api/admin/memoria/hechos/{fact_id}/caducar",
            json={"vence_at": vence_at})
        assert r.status_code == 200, r.text

        fila = client_superadmin.portal.call(
            sql,
            "SELECT expires_at <= NOW(), "
            f"ABS(TIMESTAMPDIFF(SECOND, expires_at, NOW())) <= {_MARGEN_CERCANIA_SEGUNDOS}, "
            "expires_at, NOW() FROM facts WHERE id = %s",
            (fact_id,), True)
        vencido_ya, cerca_de_ahora, expires_at, ahora = fila[0]
        assert vencido_ya and cerca_de_ahora, (
            f"expires_at ({expires_at}) no quedo vencido y cerca de NOW() de "
            f"la base ({ahora}): con offset +05:00 (ni la zona de la base ni "
            "UTC) el bug original queda expuesto sin importar en que zona "
            "corra el runner."
        )
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_el_bug_original_fallaria_tambien_con_la_base_en_utc(client_superadmin):
    """Prueba directa del razonamiento de arriba: si la CONEXION (no la base
    entera -- `SET time_zone` sin `GLOBAL` es de sesion, y se repone antes de
    soltar la conexion al pool) estuviera en UTC, el patron VIEJO de esta
    pantalla (un `datetime` CON offset pasado directo a un `UPDATE`, sin
    `CONVERT_TZ` -- exactamente lo que hacia `datetime.fromisoformat(vence_at)`
    antes de este arreglo) sigue grabando mal un vence_at en +05:00. No pasa
    por el endpoint (que ya esta arreglado): ejercita el driver directo,
    sobre un `fact` real, para no depender de logica que ya no existe en el
    codigo."""
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba bug-original-sesion-utc")
    try:
        async def _con_sesion_utc():
            from db.connection import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SET time_zone = '+00:00'")
                    try:
                        vence_at_5 = datetime.now(timezone(timedelta(hours=5)))
                        # El patron VIEJO: el datetime CON offset va directo
                        # al UPDATE, sin CONVERT_TZ.
                        await cur.execute(
                            "UPDATE facts SET expires_at = %s WHERE id = %s",
                            (vence_at_5, fact_id))
                        await cur.execute(
                            "SELECT expires_at <= NOW(), "
                            f"ABS(TIMESTAMPDIFF(SECOND, expires_at, NOW())) <= {_MARGEN_CERCANIA_SEGUNDOS}, "
                            "expires_at, NOW() FROM facts WHERE id = %s",
                            (fact_id,))
                        return await cur.fetchone()
                    finally:
                        # Nunca se devuelve al pool una conexion con la zona
                        # de sesion cambiada: otro test la reusaria en UTC.
                        await cur.execute("SET time_zone = 'SYSTEM'")

        vencido_ya, cerca_de_ahora, expires_at, ahora = client_superadmin.portal.call(
            _con_sesion_utc)
        assert not (vencido_ya and cerca_de_ahora), (
            f"con la SESION en UTC, expires_at ({expires_at}) quedo CERCA y "
            f"vencido respecto de NOW() ({ahora}) aun con el patron viejo "
            "(datetime con offset pasado directo, sin CONVERT_TZ) -- si esto "
            "pasa es porque el driver empezo a respetar tzinfo al escapar "
            "fechas, y el razonamiento de mas arriba (por que hace falta un "
            "offset que no sea ni Honduras ni UTC) dejo de aplicar."
        )
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_caducar_sin_zona_se_rechaza_por_ambigua(client_superadmin):
    """Una fecha sin zona no dice si es UTC, hora local, u otra cosa -- el
    frontend siempre manda 'Z' (ver arriba), asi que una llegada sin zona es
    un llamador distinto al esperado, no un caso a adivinar."""
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba caducar-sin-zona")
    try:
        r = client_superadmin.post(
            f"/api/admin/memoria/hechos/{fact_id}/caducar",
            json={"vence_at": "2026-09-22T08:18:00"})
        assert r.status_code == 400
        assert r.json()["detail"] == "vence_at_sin_zona"
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_caducar_si_no_se_puede_resolver_la_zona_horaria_es_503_y_no_toca_expire_fact(
        client_superadmin, monkeypatch):
    """MINOR 3+4 (revision adversarial jax-platform#147): un `CONVERT_TZ` que
    da NULL (o cualquier otro error de `_hora_local_de_base` -- driver, red)
    es un fallo de infraestructura, no "sin caducidad": `expire_fact(None)`
    QUITARIA la caducidad en vez de ponerla. Fail-closed: 503
    memoria_no_disponible, mismo contrato de tres estados (M1) que el resto
    del endpoint -- y `expire_fact` ni se llega a invocar."""
    from unittest.mock import AsyncMock

    from api.admin import memoria as memoria_mod

    client_superadmin.get("/api/admin/memoria/hechos?limite=1")
    memoria = memoria_mod._chat_mod._memory
    assert memoria is not None, "la memoria no se conecto -- el test no probaria nada"

    monkeypatch.setattr(
        memoria_mod, "_hora_local_de_base",
        AsyncMock(side_effect=memoria_mod._ZonaHorariaNoResuelta("CONVERT_TZ devolvio NULL")))
    expire_fact_llamado = AsyncMock(return_value=True)
    monkeypatch.setattr(memoria, "expire_fact", expire_fact_llamado)

    r = client_superadmin.post(
        "/api/admin/memoria/hechos/1/caducar",
        json={"vence_at": datetime.now(timezone.utc).isoformat()})
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "memoria_no_disponible"
    expire_fact_llamado.assert_not_called()


async def test_hora_local_de_base_si_convert_tz_da_null_lanza_zona_no_resuelta(monkeypatch):
    """MINOR 3, la mitad INTERNA del arreglo: el test de arriba monkeypatchea
    `_hora_local_de_base` ENTERA y sólo prueba que el llamador reacciona bien
    a `_ZonaHorariaNoResuelta` -- no que esta función la levante de verdad
    cuando `CONVERT_TZ` da `NULL`. Acá se falsea sólo el pool/cursor (nunca
    la base real -- ni siquiera la de test) para forzar ese `NULL`
    directamente."""
    from api.admin import memoria as memoria_mod

    class _CursorFalso:
        async def execute(self, *a, **k):
            return None

        async def fetchone(self):
            return (None,)  # el mismo shape que CONVERT_TZ(...) NULL

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _ConexionFalsa:
        def cursor(self):
            return _CursorFalso()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _PoolFalso:
        def acquire(self):
            return _ConexionFalsa()

    async def _pool_falso():
        return _PoolFalso()

    monkeypatch.setattr(memoria_mod, "get_pool", _pool_falso)

    with pytest.raises(memoria_mod._ZonaHorariaNoResuelta):
        await memoria_mod._hora_local_de_base(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Rango de TIMESTAMP (MINOR 5, revision adversarial jax-platform#147).
# `expires_at` es TIMESTAMP: rango real 1970-01-01 00:00:01 a
# 2038-01-19 03:14:07, los dos en UTC. Fuera de ese rango se rechaza con 400
# vence_at_invalido ANTES de tocar la base -- no se le pide a MariaDB que
# decida que hacer con un valor que no puede representar.
# ---------------------------------------------------------------------------

def test_caducar_antes_del_minimo_de_timestamp_es_400_vence_at_invalido(client_superadmin):
    """Tambien cubre el otro motivo del hallazgo: un año < 1000 (acá 500) no
    puede llegar a `_hora_local_de_base` -- si llegara, `strftime('%Y', ...)`
    no garantiza el relleno a 4 digitos en todas las libc. Con la validacion
    de rango ANTES de la conversion, este caso ni se acerca a esa función."""
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba caducar-antes-de-1970")
    try:
        r = client_superadmin.post(
            f"/api/admin/memoria/hechos/{fact_id}/caducar",
            json={"vence_at": "0500-01-01T00:00:00+00:00"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "vence_at_invalido"
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_caducar_despues_del_maximo_de_timestamp_es_400_vence_at_invalido(client_superadmin):
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba caducar-despues-de-2038")
    try:
        r = client_superadmin.post(
            f"/api/admin/memoria/hechos/{fact_id}/caducar",
            json={"vence_at": "2040-01-01T00:00:00+00:00"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "vence_at_invalido"
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_caducar_en_el_borde_valido_del_rango_no_es_400(client_superadmin):
    """Control del arreglo de arriba: el borde EXACTO valido (el minimo real
    de TIMESTAMP) no se rechaza -- la validacion es `>=`/`<=`, no `>`/`<`."""
    fact_id = client_superadmin.portal.call(
        _crear_fact, "hecho de prueba caducar-borde-valido")
    try:
        r = client_superadmin.post(
            f"/api/admin/memoria/hechos/{fact_id}/caducar",
            json={"vence_at": "1970-01-01T00:00:01+00:00"})
        assert r.status_code == 200, r.text
    finally:
        client_superadmin.portal.call(_borrar_fact, fact_id)


def test_los_dos_pools_ven_la_misma_zona_horaria(client_superadmin):
    """Baranda del acoplamiento (HECHO del revisor, jax-platform#147): el
    arreglo depende de que el pool de jax-platform
    (`db.connection.get_pool()`, el que usa `_hora_local_de_base` para
    `CONVERT_TZ`) y el pool de `MemoryDB` (`jax/memory/db.py`, el que usa
    `expire_fact` para el `UPDATE` real) vean la MISMA
    `@@session.time_zone`. Hoy los dos confían en el default del servidor
    (SYSTEM) sin que nada lo verifique -- si algún día uno de los dos fija
    un `time_zone` de sesión propio (via `init_command`, o un `SET` en
    cualquier punto de su ciclo de vida), la caducidad volvería a
    desincronizarse en silencio, exactamente como el defecto original.

    `memoria` es la instancia REAL de `MemoryDB` que usa el endpoint
    (`_chat_mod._memory`, la misma que `_memoria_conectada()` devuelve) --
    no una instanciada aparte para el test: es, literalmente, "como lo hace
    el endpoint"."""
    from api.admin import memoria as memoria_mod

    client_superadmin.get("/api/admin/memoria/hechos?limite=1")
    memoria = memoria_mod._chat_mod._memory
    assert memoria is not None and memoria.pool is not None, (
        "la memoria no se conecto -- el test no probaria nada")

    async def _ambas_zonas():
        from db.connection import get_pool

        async def _tz(pool):
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT @@session.time_zone")
                    return (await cur.fetchone())[0]

        pool_jax_platform = await get_pool()
        return await _tz(pool_jax_platform), await _tz(memoria.pool)

    tz_jax_platform, tz_memory_db = client_superadmin.portal.call(_ambas_zonas)
    assert tz_jax_platform == tz_memory_db, (
        f"db.connection.get_pool() (usado por _hora_local_de_base) ve "
        f"{tz_jax_platform!r}, pero el pool de MemoryDB.expire_fact() ve "
        f"{tz_memory_db!r}: la conversion de _hora_local_de_base apuntaria a "
        "una zona distinta de la que realmente usa el UPDATE."
    )
