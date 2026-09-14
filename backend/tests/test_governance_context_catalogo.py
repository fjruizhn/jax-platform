"""
governance_context con el catálogo de la DB (tanda A v2, spec 2026-09-14 §3.5).

Hasta hoy el contexto de gobernanza armaba el catálogo de capabilities desde
`las_manos/config.toml`, vacío desde el Bloque 3. Ahora `validation_context()`
es async, carga `await MotorCatalog.from_db()` y cachea con una clave que suma
el mtime del sello de facet_resolver (lo estampan las migraciones y el admin de
motores y capabilities) a los mtimes de los tres archivos de config.

Salvo el último, estos tests son PUROS: `MotorCatalog.from_db` se parchea, así
que corren también en el job sin DB. El sello es el archivo aislado por
función que pone conftest (`_sello_de_facets_aislado`), nunca el real.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import governance_context  # primero: pone policy/governance en sys.path
import claims as governance_claims  # noqa: E402
import facet_resolver  # noqa: E402
import grounding as governance_grounding  # noqa: E402
import validator as governance_validator  # noqa: E402

MotorCatalog = governance_validator.MotorCatalog


def _catalogo(**modos):
    return MotorCatalog({"capabilities": {n: {"allowed_motors": ["kimi"], "mode": m} for n, m in modos.items()}})


@pytest.fixture
def from_db(monkeypatch):
    """from_db falso que devuelve un catálogo NUEVO en cada llamada (así una
    recarga se distingue de un hit por identidad) y cuenta las llamadas."""
    fake = AsyncMock(side_effect=lambda: _catalogo(generate="read_only", file_write="mutating"))
    monkeypatch.setattr(MotorCatalog, "from_db", fake)
    governance_context.validation_context.cache_clear()
    yield fake
    governance_context.validation_context.cache_clear()


def _raw(nombre, modo, ptr):
    return {"predicate": "CAPABILITY_AVAILABLE", "args": {"name": nombre, "mode": modo}, "evidence_pointer": ptr}


def _validar(raw, snap, ctx, predicates):
    acc = governance_grounding.accredit(raw, snap)
    claim = governance_claims.Claim(
        predicate=raw["predicate"], args=governance_grounding.normalize_args(raw["args"]),
        authority=acc.authority, provenance_ref=acc.provenance_ref,
        evidence_pointer=raw["evidence_pointer"], scope="mesa_web")
    return acc, governance_validator.validate(claim, predicates, ctx, accreditation=acc)


def _puntero(snap, nombre):
    return next(e.pointer for e in snap.entries
                if e.pointer.startswith("/catalog_capabilities/") and e.args["name"] == nombre)


def test_el_contexto_trae_el_catalogo_de_la_db(from_db):
    ctx, _, _ = asyncio.run(governance_context.validation_context())
    assert ctx.catalog.get_capability("generate").mode == "read_only"
    assert from_db.await_count == 1


def test_sin_cambios_no_recarga_y_devuelve_el_mismo_objeto(from_db):
    async def dos_turnos():
        return (await governance_context.validation_context(),
                await governance_context.validation_context())

    primero, segundo = asyncio.run(dos_turnos())
    assert segundo is primero
    assert from_db.await_count == 1


def test_tocar_el_sello_fuerza_la_recarga(from_db):
    async def correr():
        antes = await governance_context.validation_context()
        assert facet_resolver._tocar_sello(), "no se pudo estampar el sello aislado del test"
        return antes, await governance_context.validation_context()

    antes, despues = asyncio.run(correr())
    assert despues is not antes
    assert from_db.await_count == 2


def test_db_caida_en_frio_falla_visible(monkeypatch):
    monkeypatch.setattr(
        MotorCatalog, "from_db", AsyncMock(side_effect=ConnectionRefusedError("DB caída, simulada")))
    governance_context.validation_context.cache_clear()
    try:
        with pytest.raises(ConnectionRefusedError):
            asyncio.run(governance_context.validation_context())
    finally:
        governance_context.validation_context.cache_clear()


def test_db_caida_en_caliente_no_sirve_el_catalogo_viejo(from_db):
    """Con el sello nuevo y la DB caída, servir el contexto cacheado sería un
    gate fail-open (podría seguir dando VALID a una capability revocada)."""
    async def correr():
        await governance_context.validation_context()
        from_db.side_effect = ConnectionRefusedError("DB caída, simulada")
        assert facet_resolver._tocar_sello()
        with pytest.raises(ConnectionRefusedError):
            await governance_context.validation_context()

    asyncio.run(correr())


def test_n_turnos_a_la_vez_disparan_una_sola_recarga(monkeypatch):
    async def lento():
        await asyncio.sleep(0.05)
        return _catalogo(generate="read_only")

    fake = AsyncMock(side_effect=lento)
    monkeypatch.setattr(MotorCatalog, "from_db", fake)
    governance_context.validation_context.cache_clear()

    async def veinte_turnos():
        return await asyncio.gather(*(governance_context.validation_context() for _ in range(20)))

    try:
        resultados = asyncio.run(veinte_turnos())
    finally:
        governance_context.validation_context.cache_clear()
    assert fake.await_count == 1
    assert all(r is resultados[0] for r in resultados)


def test_la_mesa_acredita_y_valida_una_capability_de_la_db(from_db):
    """El camino de la Mesa, sin nada armado a mano: el snapshot del
    contexto real lista `generate`, la citación se acredita OBSERVADO y el
    resolver la confirma con nombre y modo."""
    ctx, predicates, _ = asyncio.run(governance_context.validation_context())
    snap = governance_grounding.build_snapshot(ctx)
    acc, v = _validar(_raw("generate", "read_only", _puntero(snap, "generate")), snap, ctx, predicates)
    assert (acc.outcome, acc.authority) == ("ACCREDITED", "OBSERVADO")
    assert v.status == "VALID", v.detail


def test_control_una_capability_que_no_esta_en_la_db_no_tiene_linea_que_citar(from_db):
    """CONTROL del anterior: mismo camino, nombre ausente de la DB y de
    `ops` -> FACT_NOT_IN_SNAPSHOT. El VALID de arriba sale del catálogo."""
    ctx, predicates, _ = asyncio.run(governance_context.validation_context())
    snap = governance_grounding.build_snapshot(ctx)
    _, v = _validar(_raw("totalmente_inventado_xyz", "read_only", "/catalog_capabilities/0"), snap, ctx, predicates)
    assert v.status == "FACT_NOT_IN_SNAPSHOT"


def test_si_el_catalogo_cambia_entre_el_snapshot_y_la_validacion_el_resolver_da_fact_mismatch(monkeypatch):
    """El FACT_MISMATCH del resolver en la Mesa: la faceta citó bien el
    snapshot de SU turno, pero antes de la validación en sombra alguien
    cambió el modo en la DB y estampó el sello. El resolver ve el catálogo
    nuevo y lo dice (spec v2 §3.4, último punto)."""
    modos = iter(["read_only", "mutating"])
    monkeypatch.setattr(MotorCatalog, "from_db", AsyncMock(side_effect=lambda: _catalogo(generate=next(modos))))
    governance_context.validation_context.cache_clear()

    async def correr():
        ctx_turno, _, _ = await governance_context.validation_context()
        snap = governance_grounding.build_snapshot(ctx_turno)
        assert facet_resolver._tocar_sello()
        ctx_validacion, predicates, _ = await governance_context.validation_context()
        return snap, ctx_validacion, predicates

    try:
        snap, ctx, predicates = asyncio.run(correr())
    finally:
        governance_context.validation_context.cache_clear()
    acc, v = _validar(_raw("generate", "read_only", _puntero(snap, "generate")), snap, ctx, predicates)
    assert acc.outcome == "ACCREDITED"
    assert v.status == "FACT_MISMATCH"
    assert "mutating" in v.detail


def test_build_grounding_con_la_db_caida_da_SnapshotError(monkeypatch):
    import api.chat as chat

    monkeypatch.setattr(
        MotorCatalog, "from_db", AsyncMock(side_effect=ConnectionRefusedError("DB caída, simulada")))
    governance_context.validation_context.cache_clear()
    try:
        resultado = asyncio.run(chat._build_grounding())
    finally:
        governance_context.validation_context.cache_clear()
    assert isinstance(resultado, governance_grounding.SnapshotError)
    assert "ConnectionRefusedError" in resultado.reason


def test_con_la_db_real_el_contexto_trae_las_capabilities_sembradas_con_su_modo(client):
    """Sin parches: from_db contra jax_memory_test migrada por el fixture
    `client` (capability.mode incluida)."""
    governance_context.validation_context.cache_clear()
    try:
        ctx, _, _ = client.portal.call(governance_context.validation_context)
    finally:
        governance_context.validation_context.cache_clear()
    assert ctx.catalog.get_capability("generate").mode == "read_only"
    assert ctx.catalog.get_capability("file_write").mode == "mutating"


def test_una_recarga_colgada_vence_con_TimeoutError_y_no_retiene_nada(monkeypatch):
    """Ronda de arreglo 1 (Minor 1): una DB que acepta TCP y no responde
    colgaba la recarga para siempre (aiomysql.connect sin connect_timeout)
    y, con ella, a todo turno que necesitara recargar. Ahora la recarga
    tiene límite (GOVERNANCE_RELOAD_TIMEOUT_SECONDS) y, vencida, no deja
    nada retenido: el turno siguiente con la DB sana recarga y devuelve."""
    monkeypatch.setattr(governance_context, "GOVERNANCE_RELOAD_TIMEOUT_SECONDS", 0.05)

    async def colgado():
        await asyncio.Event().wait()  # nunca se setea: la DB que no contesta

    fake = AsyncMock(side_effect=colgado)
    monkeypatch.setattr(MotorCatalog, "from_db", fake)
    governance_context.validation_context.cache_clear()

    async def correr():
        # Guarda externa de 2 s para que el rojo no cuelgue la suite: si la
        # llamada no terminó sola, se cancela y el test lo dice.
        colgada = asyncio.ensure_future(governance_context.validation_context())
        hecho, _ = await asyncio.wait({colgada}, timeout=2.0)
        if colgada not in hecho:
            colgada.cancel()
            return "colgada", None
        primera = colgada.exception()
        fake.side_effect = lambda: _catalogo(generate="read_only")
        sana = asyncio.ensure_future(governance_context.validation_context())
        hecho, _ = await asyncio.wait({sana}, timeout=2.0)
        if sana not in hecho:
            sana.cancel()
            return primera, "retenida"
        return primera, sana.result()

    try:
        primera, segunda = asyncio.run(correr())
    finally:
        governance_context.validation_context.cache_clear()
    assert primera != "colgada", "la recarga no tiene límite de tiempo: sigue colgada a los 2 s"
    assert isinstance(primera, TimeoutError), repr(primera)
    assert segunda != "retenida", "tras el vencimiento, el turno siguiente quedó retenido"
    assert segunda[0].catalog.get_capability("generate").mode == "read_only"


def test_n_turnos_a_la_vez_con_la_db_caida_comparten_un_solo_intento(monkeypatch):
    """Ronda de arreglo 1 (Minor 2): con la DB caída, 20 turnos encolados
    detrás del lock reintentaban EN SERIE (20 conexiones). Ahora esperan la
    MISMA recarga en vuelo y reciben su MISMA excepción: un intento."""
    async def cae():
        await asyncio.sleep(0.05)
        raise ConnectionRefusedError("DB caída, simulada")

    fake = AsyncMock(side_effect=cae)
    monkeypatch.setattr(MotorCatalog, "from_db", fake)
    governance_context.validation_context.cache_clear()

    async def veinte_turnos():
        return await asyncio.gather(
            *(governance_context.validation_context() for _ in range(20)), return_exceptions=True)

    try:
        resultados = asyncio.run(veinte_turnos())
    finally:
        governance_context.validation_context.cache_clear()
    assert all(isinstance(r, ConnectionRefusedError) for r in resultados), resultados
    assert fake.await_count == 1, f"{fake.await_count} intentos de recarga para 20 turnos"
