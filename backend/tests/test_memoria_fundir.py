"""POST /api/admin/memoria/hechos/fundir -- decision de Fernando 2026-09-20:
fundir casi-duplicados es SUPERSEDER, no caducar. "Esto fue reemplazado POR
AQUELLO" reconstruye la cadena (`superseded_by`); "esto dejo de valer"
(caducar) no dice nada de por que. Tres hechos que dicen lo mismo no son tres
hechos vencidos -- son uno con tres redacciones.

Todo o nada en UNA transaccion (mismo principio que corregir_hecho ya aplica,
jax-platform#107): fundir a medias deja la memoria peor que antes.
"""
import pytest

from tests.identidades import sql


async def _crear_fact(fact_text, is_verified=False):
    return await sql(
        "INSERT INTO facts (fact_uuid, fact_text, fact_type, is_verified) "
        "VALUES (UUID(), %s, 'technical', %s)",
        (fact_text, is_verified),
    )


async def _superar_a_mano(fact_id, nuevo_id):
    await sql(
        "UPDATE facts SET superseded_by = %s, superseded_at = NOW() WHERE id = %s",
        (nuevo_id, fact_id),
    )


async def _estado(fact_id):
    filas = await sql(
        "SELECT superseded_by, superseded_by_user, expires_at FROM facts WHERE id = %s",
        (fact_id,), True,
    )
    return filas[0] if filas else None


async def _borrar_facts(*ids):
    for fact_id in ids:
        await sql("DELETE FROM facts WHERE id = %s", (fact_id,))


@pytest.fixture
def trio(client):
    """Tres hechos frescos: un superviviente y dos absorbidos, ninguno
    superado todavia."""
    superviviente = client.portal.call(_crear_fact, "fundir: hecho A (superviviente)")
    absorbido1 = client.portal.call(_crear_fact, "fundir: hecho B (absorbido)")
    absorbido2 = client.portal.call(_crear_fact, "fundir: hecho C (absorbido)")
    yield superviviente, absorbido1, absorbido2
    client.portal.call(_borrar_facts, superviviente, absorbido1, absorbido2)


def test_fundir_exige_superadmin(client, trio):
    superviviente, absorbido1, absorbido2 = trio
    r = client.post("/api/admin/memoria/hechos/fundir",
                    json={"superviviente_id": superviviente, "absorbidos": [absorbido1]})
    assert r.status_code == 401


def test_fundir_marca_los_absorbidos_como_superados_por_el_superviviente(client_superadmin, trio):
    superviviente, absorbido1, absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 200
    assert r.json()["superados"] == 2
    for absorbido in (absorbido1, absorbido2):
        superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido)
        assert superseded_by == superviviente


def test_fundir_registra_quien_decidio(client_superadmin, trio):
    """El autor es el user_id de require_superadmin -- no un valor implicito
    (Protocolo de la Memoria Viva: toda decision, quien y cuando)."""
    superviviente, absorbido1, _absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente, "absorbidos": [absorbido1]})
    assert r.status_code == 200
    _superseded_by, superseded_by_user, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by_user is not None


def test_fundir_no_toca_expires_at(client_superadmin, trio):
    """Fundir no es caducar: no le pone `expires_at` a nadie (eso lo hace
    /caducar, un endpoint aparte)."""
    superviviente, absorbido1, _absorbido2 = trio
    client_superadmin.post("/api/admin/memoria/hechos/fundir",
                           json={"superviviente_id": superviviente, "absorbidos": [absorbido1]})
    _superseded_by, _superseded_by_user, expires_at = client_superadmin.portal.call(_estado, absorbido1)
    assert expires_at is None


def test_fundir_sin_absorbidos_no_hace_nada(client_superadmin, trio):
    superviviente, _absorbido1, _absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente, "absorbidos": []})
    assert r.status_code == 200
    assert r.json()["superados"] == 0


def test_fundir_ignora_absorbidos_repetidos(client_superadmin, trio):
    superviviente, absorbido1, _absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido1]})
    assert r.status_code == 200
    assert r.json()["superados"] == 1


def test_fundir_rechaza_al_superviviente_entre_los_absorbidos(client_superadmin, trio):
    """Un hecho superado por si mismo es un ciclo -- rechazarlo es mas barato
    que explicarlo despues."""
    superviviente, absorbido1, _absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, superviviente]})
    assert r.status_code == 400
    # Nada se aplico: ni siquiera el absorbido valido del lote.
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by is None


def test_fundir_con_un_id_inexistente_no_aplica_nada_del_lote(client_superadmin, trio):
    """Todo o nada: un id que no existe en el lote tiene que impedir que el
    resto -- valido -- se funda igual."""
    superviviente, absorbido1, _absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, 999999999]})
    assert r.status_code == 404
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by is None, "fundio a medias: el absorbido valido SI cambio"


def test_fundir_rechaza_superviviente_ya_superado(client_superadmin, trio):
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(_superar_a_mano, superviviente, absorbido2)
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente, "absorbidos": [absorbido1]})
    assert r.status_code == 409


def test_fundir_rechaza_absorbido_ya_superado_y_no_toca_al_otro(client_superadmin, trio):
    """Encadenar sobre una cadena rota confunde la historia -- y todo o nada
    aplica tambien acá: el absorbido sano del mismo lote no se funde."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(_superar_a_mano, absorbido2, superviviente)
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 409
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by is None, "fundio a medias: el absorbido sano SI cambio"
