"""Contrato de los endpoints de memoria. La pantalla es de superadmin: la
memoria es lo unico que el sistema acumula sobre nosotros.

`client_superadmin`: ver tests/conftest.py -- fixture CONSTRUIDO sobre
`client` + `tests.identidades.cabeceras`, que es el patron real de esta casa
(tests/test_config_admin_ajustes.py). El plan original daba por hecho un
`client_superadmin` que no existia (2026-09-20)."""
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

def test_aprobar_con_la_base_caida_a_mitad_del_lote_no_es_200_silencioso(
        client_superadmin, monkeypatch):
    from api.admin import memoria as memoria_mod
    from unittest.mock import AsyncMock

    # Forzar que _chat_mod._memory ya este conectado, sin depender del orden
    # de la suite.
    client_superadmin.get("/api/admin/memoria/hechos?limite=1")
    memoria = memoria_mod._chat_mod._memory
    assert memoria is not None, "la memoria no se conecto -- el test no probaria nada"

    monkeypatch.setattr(memoria, "verify_fact", AsyncMock(return_value=None))
    r = client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": [1]})
    assert r.status_code == 503, (
        f"esperaba 503 (memoria_no_disponible), no {r.status_code}: un None "
        "de verify_fact es 'la base no respondio', no 'el id no existe', y "
        "un 200 con aprobados de menos no avisa que el resto del lote nunca "
        "se intento")


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
