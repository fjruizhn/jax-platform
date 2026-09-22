"""POST /api/admin/memoria/hechos/fundir -- decision de Fernando 2026-09-20:
fundir casi-duplicados es SUPERSEDER, no caducar. "Esto fue reemplazado POR
AQUELLO" reconstruye la cadena (`superseded_by`); "esto dejo de valer"
(caducar) no dice nada de por que. Tres hechos que dicen lo mismo no son tres
hechos vencidos -- son uno con tres redacciones.

Todo o nada en UNA transaccion (mismo principio que corregir_hecho ya aplica,
jax-platform#107): fundir a medias deja la memoria peor que antes.

Ronda 2026-09-22 (hallazgo de Fernando en la pantalla de Memoria): "fundir en
el mas reciente" podia aprobar una SINTESIS (con partes inventadas por el
sintetizador) y con eso SUPERAR a un hecho ya verificado por Fernando. El
superviviente ya NO es "el mas reciente" a secas: si hay algun verificado en
el grupo, gana el verificado mas reciente (_elegir_superviviente,
test_memoria_grupos.py), y `fundir_hechos` aprueba al superviviente EN LA
MISMA transaccion si hacia falta -- el frontend ya no llama a
/hechos/aprobar aparte.

Ronda 146 (revision adversarial de jax-platform PR 146, D3): el endpoint EXIGE
la regla, no solo la propone. Dos rechazos nuevos, los dos 409:
  - `fundir_sintesis_con_no_sintesis`: el lote (superviviente + absorbidos)
    tiene que ser compatible PAR A PAR -- mismo source_facet, sin citas
    cruzadas en source_fact_ids (_compatibles_para_fundir, la MISMA regla
    que usa el detector de casi-duplicados).
  - `superviviente_no_es_el_de_la_regla`: el `superviviente_id` que manda el
    cliente tiene que coincidir EXACTO con el que calcula
    `_elegir_superviviente` sobre ese mismo lote. Esto deja redundante (y
    por eso ELIMINADO, no dejado como codigo muerto) al viejo 409
    `superviviente_no_verificado` de la ronda anterior: si un absorbido
    esta verificado, la regla SIEMPRE elige un verificado como
    superviviente_correcto, asi que un superviviente sin verificar que
    coincida con la regla implica que NINGUN miembro del lote esta
    verificado.

Tercera vuelta (revision adversarial de jax-platform PR 146):
  - MAJOR 1: `fundir_sintesis_con_no_sintesis` ahora usa el CIERRE
    TRANSITIVO de citas (mismo que el detector) -- una cita indirecta (A
    cita a B, B cita a C) deja a A y C incompatibles igual.
  - MAJOR 2: un hecho vencido (`expires_at` en el pasado) no puede
    fundirse -- ni como superviviente ni como absorbido -- 409
    `hecho_vencido`, sin escribir nada.
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


async def _fijar_created_at(fact_id, valor):
    await sql("UPDATE facts SET created_at = %s WHERE id = %s", (valor, fact_id))


async def _fijar_expires_at(fact_id, valor):
    await sql("UPDATE facts SET expires_at = %s WHERE id = %s", (valor, fact_id))


async def _citar(fact_id, cita_a_id):
    await sql("UPDATE facts SET source_facet = 'synthesis', source_fact_ids = %s WHERE id = %s",
              (f"[{cita_a_id}]", fact_id))


@pytest.fixture
def trio(client):
    """Tres hechos frescos: un superviviente y dos absorbidos, ninguno
    superado todavia.

    Ronda 146 (D3): `fundir_hechos` ahora EXIGE que `superviviente_id` sea
    el que `_elegir_superviviente` calcularía para el lote -- sin esto, los
    tres se crean en la misma llamada y `created_at` (precisión de SEGUNDO)
    empataría, y la regla desempataría por el id MAYOR (D4), no por el rol
    semántico "superviviente" que le da nombre a esta fixture. Se fuerza su
    `created_at` bien por delante de los otros dos para que, en el caso sano
    (nadie verificado), la regla y el rol coincidan -- tal como coincidían
    antes de esta ronda, cuando el cliente podía elegir a mano."""
    superviviente = client.portal.call(_crear_fact, "fundir: hecho A (superviviente)")
    absorbido1 = client.portal.call(_crear_fact, "fundir: hecho B (absorbido)")
    absorbido2 = client.portal.call(_crear_fact, "fundir: hecho C (absorbido)")
    client.portal.call(_fijar_created_at, superviviente, "2026-09-22 12:00:00")
    client.portal.call(_fijar_created_at, absorbido1, "2020-01-01 00:00:00")
    client.portal.call(_fijar_created_at, absorbido2, "2020-01-02 00:00:00")
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
    superar a #160 (fuente, YA verificada por Fernando). Ronda 146 (D3): el
    codigo de error paso a `superviviente_no_es_el_de_la_regla` (el viejo
    `superviviente_no_verificado` quedo redundante y se elimino -- ver el
    docstring del modulo). Todo o nada: ni el absorbido verificado cambia,
    ni el superviviente se autoaprueba."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(sql,
        "UPDATE facts SET is_verified = TRUE WHERE id = %s", (absorbido1,))
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 409
    detalle = r.json()["detail"]
    assert detalle["code"] == "superviviente_no_es_el_de_la_regla"
    assert detalle["superviviente_correcto"] == absorbido1, (
        "el verificado (absorbido1) tenia que ser el que la regla calcula")
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido1)
    assert superseded_by is None, "fundio a medias: el absorbido verificado SI cambio"
    superseded_by2, _, _ = client_superadmin.portal.call(_estado, absorbido2)
    assert superseded_by2 is None, "fundio a medias: el absorbido sano SI cambio"
    verificado, _ = client_superadmin.portal.call(_verificado_de, superviviente)
    assert verificado is False, "el superviviente se autoaprobo pese al rechazo"


def test_fundir_permite_absorber_hechos_verificados_si_el_superviviente_ya_lo_estaba(client_superadmin):
    """El caso sano: un superviviente YA verificado sí puede superar a
    absorbidos verificados (no hay perdida de verificacion). `created_at`
    explicito (D3, la regla exige coincidencia exacta): los dos se crean en
    la misma llamada y podrian empatar de segundo -- se fuerza al
    superviviente a ser claramente el mas reciente para que la regla lo
    elija a EL, no al absorbido por el desempate de id (D4)."""
    superviviente = client_superadmin.portal.call(_crear_fact, "fundir: superviviente ya verificado", True)
    absorbido = client_superadmin.portal.call(_crear_fact, "fundir: absorbido tambien verificado", True)
    client_superadmin.portal.call(_fijar_created_at, superviviente, "2026-09-22 12:00:00")
    client_superadmin.portal.call(_fijar_created_at, absorbido, "2020-01-01 00:00:00")
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                                   json={"superviviente_id": superviviente, "absorbidos": [absorbido]})
        assert r.status_code == 200
        superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido)
        assert superseded_by == superviviente
    finally:
        client_superadmin.portal.call(_borrar_facts, superviviente, absorbido)


# --- D3 (revision adversarial de jax-platform PR 146): el endpoint EXIGE la
# regla, no solo la propone --------------------------------------------------

def test_fundir_rechaza_superviviente_que_no_es_el_de_la_regla_sin_verificados(client_superadmin, trio):
    """Caso general de la regla (D3), sin que medie verificacion: el cliente
    pide fundir con un absorbido (mas viejo) como superviviente, en vez del
    hecho que la regla elegiria (el mas reciente, `trio[0]`)."""
    superviviente, absorbido1, absorbido2 = trio
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": absorbido1,
                                     "absorbidos": [superviviente, absorbido2]})
    assert r.status_code == 409
    detalle = r.json()["detail"]
    assert detalle["code"] == "superviviente_no_es_el_de_la_regla"
    assert detalle["superviviente_correcto"] == superviviente
    # Nada se aplico -- ni siquiera el absorbido2, mas viejo que el
    # "superviviente" solicitado.
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido2)
    assert superseded_by is None


def test_fundir_rechaza_mezclar_sintesis_con_no_sintesis(client_superadmin, trio):
    """D1/D3: la API no permite a mano lo que el detector ya no propone. Un
    absorbido con `source_facet='synthesis'` no puede fundirse junto a un
    superviviente que no lo es, aunque el resto de las reglas (verificacion,
    fecha) darian un resultado valido."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_facet = 'synthesis' WHERE id = %s", (absorbido1,))
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 409
    assert r.json()["detail"] == "fundir_sintesis_con_no_sintesis"
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido2)
    assert superseded_by is None, "fundio a medias: el absorbido compatible SI cambio"


def test_fundir_rechaza_dos_sintesis_que_se_citan_entre_si(client_superadmin):
    """D1, el caso fino: dos sintesis SI son compatibles entre si por tipo,
    pero si UNA CITA A LA OTRA en source_fact_ids siguen sin poder
    fundirse -- mismo criterio que el detector.

    MINOR 3 (revision adversarial de jax-platform PR 146, ronda 4): el
    codigo de error tiene que decir la verdad -- este rechazo NO es por tipo
    cruzado (los dos son 'synthesis'), es por la cita. `fundir_sintesis_
    con_no_sintesis` mentiria sobre el motivo; el codigo correcto es
    `fundir_hechos_relacionados_por_cita`."""
    s1 = client_superadmin.portal.call(_crear_fact, "fundir: sintesis 1")
    s2 = client_superadmin.portal.call(_crear_fact, "fundir: sintesis 2 (cita a s1)")
    client_superadmin.portal.call(_fijar_created_at, s1, "2020-01-01 00:00:00")
    client_superadmin.portal.call(_fijar_created_at, s2, "2026-09-22 12:00:00")
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_facet = 'synthesis' WHERE id IN (%s, %s)", (s1, s2))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_fact_ids = %s WHERE id = %s", (f"[{s1}]", s2))
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                                   json={"superviviente_id": s2, "absorbidos": [s1]})
        assert r.status_code == 409
        assert r.json()["detail"] == "fundir_hechos_relacionados_por_cita"
    finally:
        client_superadmin.portal.call(_borrar_facts, s1, s2)


def test_fundir_no_reaprueba_ni_cambia_verified_by_de_un_superviviente_ya_verificado(client_superadmin):
    """D6 (MENOR 7, 'un control que no falla no valida nada'): si el
    superviviente YA estaba verificado por OTRA persona, fundir no puede
    pisar `verified_by`/`verified_at` con el usuario y el momento actuales
    -- eso reescribiria quien y cuando lo verifico de verdad."""
    from datetime import datetime, timedelta

    superviviente = client_superadmin.portal.call(_crear_fact, "fundir: ya verificado por otro", True)
    absorbido = client_superadmin.portal.call(_crear_fact, "fundir: absorbido")
    client_superadmin.portal.call(_fijar_created_at, superviviente, "2026-09-22 12:00:00")
    client_superadmin.portal.call(_fijar_created_at, absorbido, "2020-01-01 00:00:00")
    hace_un_mes = (datetime(2026, 9, 22) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    client_superadmin.portal.call(
        sql, "UPDATE facts SET verified_by = 999999, verified_at = %s WHERE id = %s",
        (hace_un_mes, superviviente))
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                                   json={"superviviente_id": superviviente, "absorbidos": [absorbido]})
        assert r.status_code == 200
        verificado, verificado_por = client_superadmin.portal.call(_verificado_de, superviviente)
        assert verificado is True
        assert verificado_por == 999999, "fundir REAPROBO a un superviviente ya verificado (piso verified_by)"
        verified_at = client_superadmin.portal.call(
            sql, "SELECT verified_at FROM facts WHERE id = %s", (superviviente,), True)[0][0]
        assert verified_at.strftime("%Y-%m-%d") == hace_un_mes[:10], (
            "fundir REAPROBO a un superviviente ya verificado (piso verified_at)")
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


# --- MAJOR 2 (revision adversarial de jax-platform PR 146, tercera vuelta):
# un hecho vencido no puede fundirse ------------------------------------------

def test_fundir_rechaza_superviviente_vencido(client_superadmin, trio):
    """El escenario del revisor: caducar el superviviente (el que la regla
    elegiria por fecha) y despues intentar fundir con el -- 409
    `hecho_vencido`, nada se escribe."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(_fijar_expires_at, superviviente, "2020-01-01 00:00:00")
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 409
    assert r.json()["detail"] == "hecho_vencido"
    for absorbido in (absorbido1, absorbido2):
        superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido)
        assert superseded_by is None, f"fundio a medias: {absorbido} SI cambio"
    verificado, _ = client_superadmin.portal.call(_verificado_de, superviviente)
    assert verificado is False, "el superviviente vencido se autoaprobo pese al rechazo"


def test_fundir_rechaza_absorbido_vencido(client_superadmin, trio):
    """No sólo el superviviente: un absorbido vencido tampoco puede
    fundirse -- perderia su propia fecha de vencimiento en silencio,
    superado por un hecho que no la tiene."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(_fijar_expires_at, absorbido1, "2020-01-01 00:00:00")
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 409
    assert r.json()["detail"] == "hecho_vencido"
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido2)
    assert superseded_by is None, "fundio a medias: el absorbido sano SI cambio"


def test_fundir_permite_un_vencimiento_futuro(client_superadmin, trio):
    """`expires_at` en el FUTURO no es "vencido" -- sigue vigente. Sólo
    `expires_at <= NOW()` bloquea."""
    superviviente, absorbido1, absorbido2 = trio
    client_superadmin.portal.call(_fijar_expires_at, superviviente, "2099-01-01 00:00:00")
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido1, absorbido2]})
    assert r.status_code == 200
    assert r.json()["superados"] == 2


# --- MAJOR 1 (revision adversarial de jax-platform PR 146, tercera vuelta):
# el endpoint usa el MISMO cierre transitivo que el detector ------------------

def test_fundir_rechaza_lote_con_cita_transitiva_no_directa(client_superadmin, trio):
    """A cita a B, B cita a C -- A y C nunca se citan DIRECTO, pero estan
    relacionados transitivamente (MAJOR 1a). El endpoint tiene que
    rechazarlos igual, con el MISMO codigo que una cita directa.

    Los TRES quedan marcados `source_facet='synthesis'` a proposito: si sólo
    A y B lo estuvieran, el rechazo saldria por el chequeo de TIPO (A/B
    síntesis vs C no-síntesis), no por la transitividad de la cita -- que es
    justo lo que este test tiene que ejercitar.

    MINOR 3 (revision adversarial de jax-platform PR 146, ronda 4): con los
    TRES del mismo tipo, el unico motivo de rechazo es la cita transitiva --
    el codigo tiene que ser `fundir_hechos_relacionados_por_cita`, no
    `fundir_sintesis_con_no_sintesis` (que mentiria: no hay tipo cruzado
    aca)."""
    superviviente, absorbido1, absorbido2 = trio
    # superviviente cita a absorbido1, absorbido1 cita a absorbido2 -- asi
    # superviviente y absorbido2 quedan relacionados solo por transitividad.
    client_superadmin.portal.call(_citar, superviviente, absorbido1)
    client_superadmin.portal.call(_citar, absorbido1, absorbido2)
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_facet = 'synthesis' WHERE id = %s", (absorbido2,))
    r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                               json={"superviviente_id": superviviente,
                                     "absorbidos": [absorbido2]})
    assert r.status_code == 409
    assert r.json()["detail"] == "fundir_hechos_relacionados_por_cita"
    superseded_by, _, _ = client_superadmin.portal.call(_estado, absorbido2)
    assert superseded_by is None


# --- MINOR 2 (revision adversarial de jax-platform PR 146, ronda 4): ningun
# test fijaba el ALCANCE de SQL_CITAS ------------------------------------

def test_sql_citas_ve_la_cadena_a_traves_de_un_superado_y_un_vencido(client_superadmin, trio):
    """SQL_CITAS (memoria.py) trae TODOS los facts con `source_fact_ids` no
    nulo, a proposito SIN filtrar por `superseded_by`/`expires_at` -- una
    sintesis puede citar a un hecho que despues se supera o vence, y la
    cadena tiene que seguir cerrada igual (comentario junto a SQL_CITAS).
    Si alguien le agrega `AND superseded_by IS NULL` (o el equivalente para
    `expires_at`), este test tiene que caer -- fijado como mutacion, no
    solo como intencion en un comentario.

    Cadena: superviviente cita a P (que despues se marca SUPERADO); P cita
    a Q (que despues se marca VENCIDO); Q cita a absorbido2. Ninguno de los
    dos eslabones (P, Q) participa del lote que se intenta fundir -- sólo
    del grafo de citas."""
    superviviente, _absorbido1, absorbido2 = trio
    p = client_superadmin.portal.call(_crear_fact, "fundir: eslabon P (se superara)")
    q = client_superadmin.portal.call(_crear_fact, "fundir: eslabon Q (vencera)")
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_facet = 'synthesis' WHERE id IN (%s, %s)",
        (superviviente, absorbido2))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_fact_ids = %s WHERE id = %s", (f"[{p}]", superviviente))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_fact_ids = %s WHERE id = %s", (f"[{q}]", p))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_fact_ids = %s WHERE id = %s", (f"[{absorbido2}]", q))
    client_superadmin.portal.call(_superar_a_mano, p, absorbido2)
    client_superadmin.portal.call(_fijar_expires_at, q, "2020-01-01 00:00:00")
    try:
        r = client_superadmin.post("/api/admin/memoria/hechos/fundir",
                                   json={"superviviente_id": superviviente,
                                         "absorbidos": [absorbido2]})
        assert r.status_code == 409, (
            f"la cadena de citas a traves de P (superado) y Q (vencido) "
            f"tenia que rechazar el fundir igual: {r.status_code} {r.text}")
        assert r.json()["detail"] == "fundir_hechos_relacionados_por_cita"
    finally:
        client_superadmin.portal.call(_borrar_facts, p, q)
