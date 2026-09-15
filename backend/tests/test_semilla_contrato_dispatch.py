"""Tripwire (PR-L ronda 1, 2026-09-14, hallazgo de PR-K): la semilla de base
VACÍA no puede vincular una faceta a un modelo que su dispatch rechazaría.

Hasta esta ronda _FACET_BINDING_SEED vinculaba ada a zhipu/glm-5.2, una fila
sin max_tokens_param ni max_output_tokens en las listas de semilla: en una
instalación nueva Ada nacía rota (el dispatch http_openai_compat falla
cerrado) y el guard de PR-J rechazaba ese mismo binding si alguien lo
re-guardaba. Producción usa glm-5.3, que sí tiene contrato.

Cómo nace una base vacía (db/migrations.py::run_migrations):
_seed_facets escribe _FACET_BINDING_SEED -> _seed_models_and_backfill crea
una fila de `model` por cada (provider_id, model_id) de esos bindings ->
_seed_model_max_tokens_param / _seed_model_max_output_tokens completan esas
filas desde _MODEL_MAX_TOKENS_PARAM_SEED / _MODEL_MAX_OUTPUT_TOKENS_SEED. Así
que el contrato con el que nace cada binding es exactamente lo que dicen esas
listas, y este test lo verifica con LOS MISMOS validadores del dispatch
(faltantes_del_contrato), sin DB: corre también en el job sin base.
"""
from contrato_dispatch import TRANSPORTS_CON_CONTRATO_DE_DISPATCH, faltantes_del_contrato
from db import migrations


def _bindings_que_leen_el_contrato():
    transporte = {key: t for key, _n, _i, _c, t, _a in migrations._FACET_SEED}
    return [
        (facet, provider_id, model_id, transporte[facet])
        for facet, provider_id, model_id in migrations._FACET_BINDING_SEED
        if transporte[facet] in TRANSPORTS_CON_CONTRATO_DE_DISPATCH
    ]


def test_toda_faceta_con_contrato_nace_con_un_modelo_que_lo_cumple():
    params = {(p, m): v for p, m, v in migrations._MODEL_MAX_TOKENS_PARAM_SEED}
    topes = {(p, m): v for p, m, v in migrations._MODEL_MAX_OUTPUT_TOKENS_SEED}
    bindings = _bindings_que_leen_el_contrato()
    assert bindings, "ninguna faceta de la semilla lee el contrato: el tripwire no mide nada"
    rotas = {}
    for facet, provider_id, model_id, transport in bindings:
        faltan = faltantes_del_contrato(
            transport, model_id, params.get((provider_id, model_id)), topes.get((provider_id, model_id)))
        if faltan:
            rotas[facet] = (f"{provider_id}/{model_id}", [campo for campo, _ in faltan])
    assert not rotas, f"facetas que nacerían rotas en una base vacía: {rotas}"


def test_ada_nace_vinculada_a_glm_5_3():
    """El caso concreto del hallazgo: el mismo modelo que usa producción."""
    semilla = {facet: (p, m) for facet, p, m in migrations._FACET_BINDING_SEED}
    assert semilla["ada"] == ("zhipu", "glm-5.3")
