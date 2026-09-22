"""POST /api/admin/memoria/hechos/fundir -- decision de Fernando 2026-09-20:
fundir casi-duplicados es SUPERSEDER, no caducar. "Esto fue reemplazado POR
AQUELLO" reconstruye la cadena (`superseded_by`); "esto dejo de valer"
(caducar) no dice nada de por que. Tres hechos que dicen lo mismo no son tres
hechos vencidos -- son uno con tres redacciones.

Todo o nada en UNA transaccion (mismo principio que corregir_hecho ya aplica,
jax-platform#107): fundir a medias deja la memoria peor que antes.

Ronda 2026-09-22 (hallazgo de Fernando en la pantalla de Memoria): "fundir en
el mas reciente" podia aprobar una SINTESIS (con partes inventadas por el
sintetizador) y con eso SUPERAR a un hecho ya verificado por Fernando. Dos
correcciones, las dos en el backend:
  - el superviviente ya NO es "el mas reciente" a secas: si hay algun
    verificado en el grupo, gana el verificado mas reciente (_elegir_
    superviviente, test_memoria_grupos.py).
  - `fundir_hechos` rechaza (409 `superviviente_no_verificado`) que un
    absorbido verificado quede superado por un superviviente sin verificar,
    y aprueba al superviviente EN LA MISMA transaccion si hacia falta --
    el frontend ya no llama a /hechos/aprobar aparte.
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


async def _verificado_de(fact_id):
    filas = await sql(
        "SELECT is_verified, verified_by FROM facts WHERE id = %s",
        (fact_id,), True,
    )
    if not filas:
        return None
    is_verified, verified_by = filas[0]
    return bool(is_verified), verified_by


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


# --- Ronda 2026-09-22: el superviviente se aprueba EN el fundir, y un
# verificado nunca puede quedar superado por uno sin verificar -----------

def test_fundir_aprueba_al_superviviente_no_verificado_en_la_misma_llamada(client_superadmin, trio):
    """El frontend ya NO llama a /hechos/aprobar antes de /hechos/fundir
    (una sola llamada, decision de esta ronda): fundir_hechos tiene que
    aprobar al superviviente el mismo, con el MISMO efecto que
    /hechos/aprobar (is_verified, verified_at, verified_by)."""
    superviviente, absorbido1, absorbido2 = trio
    verificado_antes, _ = client_superadmin.portal.call(_verificado_de, superviviente)
    assert verificado_antes is False, "la fixture trio tiene que arrancar sin verificar"

    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 200
    verificado, verificado_por = client_superadmin.portal.call(_verificado_de, superviviente)
    assert verificado is True
    assert verificado_por is not None, "verified_by no puede quedar implicito (Protocolo de la Memoria Viva)"


def test_fundir_rechaza_absorber_un_verificado_con_superviviente_sin_verificar(client_superadmin, trio):
    """El hallazgo real (2026-09-22): #161 (sintesis, sin verificar) no puede
    superar a #160 (fuente, YA verificada por Fernando). Todo o nada: ni el
    absorbido verificado cambia, ni el superviviente se autoaprueba."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(sql,
        "UPDATE facts SET is_verified = TRUE WHERE id = %s", (absorbido1,))
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "superviviente_no_verificado"
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by is None, "fundio a medias: el absorbido verificado SI cambio"
    superseded_by2, _, _ = client_superadmin.portal.call(_estado, absorbido2)
    assert superseded_by2 is None, "fundio a medias: el absorbido sano SI cambio"
    verificado, _ = client_superadmin.portal.call(_verificado_de, superviviente)
    assert verificado is False, "el superviviente se autoaprobo pese al rechazo"


def test_fundir_permite_absorber_hechos_verificados_si_el_superviviente_ya_lo_estaba(client_superadmin):
    """El caso sano: un superviviente YA verificado sí puede superar a
    absorbidos verificados (no hay perdida de verificacion)."""
    superviviente = client_superadmin.portal.call(_crear_fact, "fundir: superviviente ya verificado", True)
    absorbido = client_superadmin.portal.call(_crear_fact, "fundir: absorbido tambien verificado", True)
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                                   json={"superviviente_id": superviviente, "absorbidos": [absorbido]})
        assert r.status_code == 200
        superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido)
        assert superseded_by == superviviente
    finally:
        client_superadmin.portal.call(_borrar_facts, superviviente, absorbido)


def test_fundir_es_atomico_si_falla_a_mitad_del_lote_no_queda_nada_escrito(
        client_superadmin, trio, monkeypatch):
    """Si el UPDATE de `superseded_by` de un absorbido revienta a mitad del
    lote, la aprobacion del superviviente -- escrita ANTES, en la MISMA
    transaccion -- tiene que revertirse tambien. Todo o nada, jax-platform#107
    aplicado tambien a la aprobacion nueva de esta ronda."""
    from api.admin import memoria

    superviviente, absorbido1, absorbido2 = trio
    llamadas = []
    original = memoria._superar_en_cursor

    async def _revienta_en_la_segunda(cur, autor, absorbido_id, superviviente_id):
        llamadas.append(absorbido_id)
        if len(llamadas) == 2:
            raise RuntimeError("fallo simulado a mitad del lote de fundir")
        await original(cur, autor, absorbido_id, superviviente_id)

    monkeypatch.setattr(memoria, "_superar_en_cursor", _revienta_en_la_segunda)

    with pytest.raises(RuntimeError, match="fallo simulado"):
        client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})

    verificado, _ = client_superadmin.portal.call(_verificado_de, superviviente)
    assert verificado is False, "la aprobacion del superviviente NO se revirtio: fundio a medias"
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by is None, "el primer absorbido SI cambio antes de la falla: fundio a medias"
