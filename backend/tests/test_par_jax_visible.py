"""El par plataforma/jax roto tiene que VERSE (2026-10-05).

Incidente: plataforma 0e582da6 + jax f96941b1 -> ``_core()`` lanzo
``GovernedChatUnavailable`` (f2-c.domain.7 vs .6); el ``except Exception`` de
``governed_chat`` lo trago sin log, ``chat.py`` devolvio 503 sin log y el usuario vio
«No se pudo conectar con la faceta». Estos tests usan un jax FALSO y autocontenido
(sin depender de ningun checkout): lo unico que importa es la version que expone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types

import pytest

# Literales a proposito: el test no importa las constantes del codigo que prueba.
RENDERER, DOMAIN, SCHEMAS = "f2-c.renderer.3", "f2-c.domain.7", ("f2-c.1",)
F2D_LIFECYCLE_API_VERSION = "f2-d.lifecycle.2"
RUNTIME_STATUS_API_VERSION_ESPERADA = "f2-e.runtime-status.4"


def _escribir(raiz, ruta, contenido):
    destino = raiz / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido, encoding="utf-8")


def _jax_falso(raiz, *, domain=DOMAIN, lifecycle=F2D_LIFECYCLE_API_VERSION,
               runtime_status=RUNTIME_STATUS_API_VERSION_ESPERADA,
               structured_projection="f2-c.structured-projection.1",
               structured_bytes="f2-d.structured-bytes.1"):
    _escribir(raiz, "policy/__init__.py", "")
    _escribir(raiz, "policy/governance/__init__.py", "")
    _escribir(raiz, "policy/governance/governed_renderer.py", (
        "class GovernedDomainRegistry: pass\n"
        "class GovernedRenderer: pass\n"
        "class RenderContext: pass\n"
        "class WebChatGovernanceAdapter: pass\n"))
    _escribir(raiz, "policy/governance/response.py", (
        "class ContractState: pass\n"
        "class GovernanceReceipt: pass\n"
        "class ResponseScope: pass\n"))
    _escribir(raiz, "policy/governance/governed_domain.py", (
        f"GOVERNED_RENDERER_API_VERSION = {RENDERER!r}\n"
        f"GOVERNED_DOMAIN_SPEC_VERSION = {domain!r}\n"
        f"GOVERNED_ENVELOPE_SCHEMA_VERSIONS = frozenset({set(SCHEMAS)!r})\n"
        "class GovernedDomainSpecification: pass\n"))
    _escribir(raiz, "policy/governance/output_lifecycle.py", (
        f"OUTPUT_LIFECYCLE_API_VERSION = {lifecycle!r}\n"
        "def validate_lifecycle_version(version): return version\n"))
    _escribir(raiz, "policy/governance/runtime_status.py",
              f"RUNTIME_STATUS_API_VERSION = {runtime_status!r}\n")
    _escribir(raiz, "policy/governance/resolution.py", "")
    _escribir(raiz, "policy/governance/structured_projection.py",
              f"STRUCTURED_PROJECTION_API_VERSION = {structured_projection!r}\n")
    _escribir(raiz, "policy/governance/structured_lifecycle.py",
              f"STRUCTURED_BYTES_LIFECYCLE_API_VERSION = {structured_bytes!r}\n")
    return raiz


@pytest.fixture
def jax_en(monkeypatch, tmp_path):
    """Apunta JAX_REPO_PATH a un jax falso y aisla ``policy.*`` del resto de la suite."""
    previos = {n: m for n, m in sys.modules.items() if n == "policy" or n.startswith("policy.")}
    for n in previos:
        del sys.modules[n]
    ruta_previa = list(sys.path)

    def apuntar(**versiones):
        raiz = _jax_falso(tmp_path / "jax", **versiones)
        monkeypatch.setenv("JAX_REPO_PATH", str(raiz))
        for n in [n for n in sys.modules if n == "policy" or n.startswith("policy.")]:
            del sys.modules[n]
        return raiz

    yield apuntar
    for n in [n for n in sys.modules if n == "policy" or n.startswith("policy.")]:
        del sys.modules[n]
    sys.modules.update(previos)
    sys.path[:] = ruta_previa


def _ambito():
    return types.SimpleNamespace(tenant_id="t1", project_id=None,
                                 subject_user_id="u1", actor_principal="user:u1")


# --- (a) par incompatible -> log error + fail-closed ---------------------------------------

def test_par_incompatible_deja_log_de_error_y_no_filtra_texto(jax_en, caplog):
    from api.governed_chat import project_provider_contract

    jax_en(domain="f2-c.domain.6")
    contrato = types.SimpleNamespace(contract_parsed=True, claims=[],
                                     analysis="TEXTO-SECRETO-DEL-PROVEEDOR", judgment="")
    with caplog.at_level(logging.ERROR, logger="api.governed_chat"):
        proy = project_provider_contract(contrato, memory_scope=_ambito(), user_id="u1",
                                         request_id="req-123", trace_id="tr-9")
    # fail-closed hacia el cliente: sin unidad de transporte, sin texto del proveedor.
    assert proy.transport_unit is None
    assert proy.contract_state == "UNAVAILABLE"
    assert "TEXTO-SECRETO-DEL-PROVEEDOR" not in proy.text
    registros = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(registros) == 1
    r = registros[0]
    assert r.exc_info and r.exc_info[0].__name__ == "GovernedChatUnavailable"
    assert "req-123" in r.getMessage()
    assert "TEXTO-SECRETO-DEL-PROVEEDOR" not in r.getMessage() + str(r.exc_info[1])
    # el motivo dice las dos versiones: la esperada y la encontrada.
    assert DOMAIN in str(r.exc_info[1]) and "f2-c.domain.6" in str(r.exc_info[1])


def test_project_sealed_envelope_tambien_deja_log(jax_en, caplog):
    from api.governed_chat import project_sealed_envelope

    jax_en(domain="f2-c.domain.6")
    with caplog.at_level(logging.ERROR, logger="api.governed_chat"):
        proy = project_sealed_envelope(object(), object())
    assert proy.transport_unit is None
    assert any(r.levelno >= logging.ERROR and r.exc_info for r in caplog.records)


def test_503_de_ciclo_de_vida_deja_log_con_faceta_request_y_motivo(caplog):
    from api.chat import _lifecycle_unavailable_response

    with caplog.at_level(logging.ERROR, logger="api.chat"):
        resp = _lifecycle_unavailable_response(facet="thot", request_id="req-77",
                                               motivo="transport_unit ausente")
    assert resp.status_code == 503
    assert json.loads(resp.body) == {"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}
    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "thot" in msg and "req-77" in msg and "transport_unit ausente" in msg


def test_chat_py_no_devuelve_el_503_de_transport_unit_sin_log():
    """Los dos sitios `transport_unit is None` pasan por el helper que registra."""
    from pathlib import Path
    import api.chat as chat

    fuente = Path(chat.__file__).read_text(encoding="utf-8")
    trozos = fuente.split("if governed.transport_unit is None:")
    assert len(trozos) == 3, "se esperaban exactamente dos sitios transport_unit is None"
    for resto in trozos[1:]:
        assert resto.lstrip().startswith("return _lifecycle_unavailable_response(")


# --- (b)/(c) arranque ---------------------------------------------------------------------

@pytest.mark.parametrize("versiones, texto", [
    ({"domain": "f2-c.domain.6"}, "f2-c.domain.6"),
    ({"lifecycle": "f2-d.lifecycle.1"}, "f2-d.lifecycle.1"),
    ({"runtime_status": "f2-e.runtime-status.3"}, "f2-e.runtime-status.3"),
    ({"structured_projection": "f2-c.structured-projection.0"}, "f2-c.structured-projection.0"),
    ({"structured_bytes": "f2-d.structured-bytes.9"}, "f2-d.structured-bytes.9"),
])
def test_verificar_par_incompatible_falla_y_dice_esperada_y_encontrada(jax_en, caplog, versiones, texto):
    from par_jax import ParJaxIncompatible, verificar_par_jax

    jax_en(**versiones)
    with caplog.at_level(logging.CRITICAL, logger="par_jax"):
        with pytest.raises(ParJaxIncompatible) as e:
            verificar_par_jax()
    assert texto in str(e.value)
    assert "expects" in str(e.value)
    assert any(r.levelno == logging.CRITICAL and texto in r.getMessage() for r in caplog.records)


def test_verificar_par_sin_jax_repo_path_falla(monkeypatch):
    from par_jax import ParJaxIncompatible, verificar_par_jax

    monkeypatch.delenv("JAX_REPO_PATH", raising=False)
    with pytest.raises(ParJaxIncompatible):
        verificar_par_jax()


@pytest.fixture
def sin_humo(monkeypatch):
    """El jax falso solo tiene versiones: la prueba de humo se prueba aparte, con el jax real."""
    import par_jax

    monkeypatch.setattr(par_jax, "_prueba_de_humo", lambda: None)


def test_verificar_par_compatible_pasa(jax_en, sin_humo):
    from par_jax import verificar_par_jax

    jax_en()
    verificar_par_jax()


class _Testigo(Exception):
    """Lanzada por el primer paso del lifespan DESPUES del chequeo del par."""


def _arrancar_con_testigo(monkeypatch):
    """Corre el lifespan real con el primer paso posterior al chequeo sustituido por un
    testigo: si el chequeo no corre, se propaga _Testigo (la prueba FALLA); jamas sigue
    hasta la base ni se salta por el conftest sin DB."""
    import main

    def testigo():
        raise _Testigo

    monkeypatch.setattr(main.limites_de_adjuntos, "cargar_limites", testigo)

    async def correr():
        async with main.lifespan(main.app):
            pass
    asyncio.run(correr())


def test_lifespan_con_jax_incompatible_no_arranca(jax_en, monkeypatch):
    from par_jax import ParJaxIncompatible

    jax_en(domain="f2-c.domain.6")
    with pytest.raises(ParJaxIncompatible):
        _arrancar_con_testigo(monkeypatch)


def test_lifespan_con_jax_compatible_pasa_el_chequeo_y_sigue(jax_en, sin_humo, monkeypatch):
    jax_en()
    with pytest.raises(_Testigo):
        _arrancar_con_testigo(monkeypatch)


def test_lifespan_invoca_el_chequeo_aunque_el_par_fuera_compatible(monkeypatch):
    """Mutacion: si main deja de llamar a verificar_par_jax, esta prueba falla."""
    import main

    llamadas = []
    monkeypatch.setattr(main, "verificar_par_jax", lambda: llamadas.append(1))
    with pytest.raises(_Testigo):
        _arrancar_con_testigo(monkeypatch)
    assert llamadas == [1]


# --- MINOR 4: prueba de humo real ------------------------------------------------------------

def test_prueba_de_humo_con_el_jax_real_mintea_la_unidad():
    """El jax de la suite (JAX_REPO_PATH, sin base ni red) pasa la prueba completa."""
    import os
    from par_jax import verificar_par_jax

    assert os.environ.get("JAX_REPO_PATH")
    verificar_par_jax()


def test_prueba_de_humo_sin_transport_unit_falla(jax_en, monkeypatch, caplog):
    import api.governed_chat as gc
    import par_jax

    jax_en()
    proy = gc.GovernedChatProjection(
        text="x", response_id=None, envelope_digest=None, source_envelope_digest=None,
        contract_state="UNAVAILABLE", contract_degraded=True, governed_plain=True)
    monkeypatch.setattr(gc, "project_provider_contract", lambda *a, **k: proy)
    with pytest.raises(par_jax.ParJaxIncompatible, match="transport_unit"):
        par_jax.verificar_par_jax()


# --- MAJOR 3: pipeline-list con el mismo dueño de versiones ------------------------------------

def test_pipeline_list_no_fija_sus_propias_versiones_f2c_ni_runtime_status():
    from pathlib import Path
    import api.governed_pipeline_list as gpl

    fuente = Path(gpl.__file__).read_text(encoding="utf-8")
    assert "f2-c.domain" not in fuente and "f2-c.renderer" not in fuente
    assert "f2-e.runtime-status" not in fuente
    assert not hasattr(gpl, "_F2C_TUPLE") and not hasattr(gpl, "_RUNTIME_STATUS_VERSION")


@pytest.mark.parametrize("versiones, texto", [
    ({"domain": "f2-c.domain.6"}, "f2-c.domain.6"),
    ({"runtime_status": "f2-e.runtime-status.3"}, "f2-e.runtime-status.3"),
    ({"structured_projection": "f2-c.structured-projection.0"}, "f2-c.structured-projection.0"),
    ({"structured_bytes": "f2-d.structured-bytes.9"}, "f2-d.structured-bytes.9"),
])
def test_pipeline_list_warning_dice_esperada_encontrada_y_correlacion(jax_en, caplog, versiones, texto):
    import re
    import api.governed_pipeline_list as gpl

    jax_en(**versiones)
    with caplog.at_level(logging.WARNING, logger="api.governed_pipeline_list"):
        resp = asyncio.run(gpl.govern_pipeline_list({}, None))
    assert resp.status_code == 503
    registros = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(registros) == 1
    msg = registros[0].getMessage()
    assert texto in msg and "expects" in msg
    assert re.search(r"correlation_id=[0-9a-f-]{36}", msg)


# --- MINOR 5: los cuatro 503 de chat.py -----------------------------------------------------

def test_chat_py_todos_los_503_de_ciclo_de_vida_pasan_por_el_helper():
    from pathlib import Path
    import api.chat as chat

    fuente = Path(chat.__file__).read_text(encoding="utf-8")
    assert fuente.count('{"detail": {"code": "OUTPUT_LIFECYCLE_UNAVAILABLE"}}') == 1  # solo el helper
    assert fuente.count("return _lifecycle_unavailable_response(") == 4


def test_503_por_fallo_de_preparacion_registra_exc_info(caplog):
    from api.chat import _lifecycle_unavailable_response

    with caplog.at_level(logging.ERROR, logger="api.chat"):
        try:
            raise RuntimeError("prepare failure")
        except RuntimeError:
            _lifecycle_unavailable_response(facet="thot", request_id="req-5",
                                            motivo="preparacion F2-D fallo", con_traza=True)
    r = [r for r in caplog.records if r.levelno >= logging.ERROR][0]
    assert r.exc_info and r.exc_info[0] is RuntimeError
    assert "req-5" in r.getMessage() and "thot" in r.getMessage()


# --- MINOR 6: visibles en journalctl -p err ---------------------------------------------------

@pytest.mark.parametrize("nombre", ["api.governed_chat", "api.chat", "par_jax",
                                    "api.governed_pipeline_list"])
def test_loggers_del_par_tienen_handler_y_nivel_visibles(nombre):
    import main  # noqa: F401  (configura los loggers al importarse)

    lg = logging.getLogger(nombre)
    assert lg.handlers, f"{nombre} sin handler propio: uvicorn --log-level warning lo pierde"
    assert lg.getEffectiveLevel() <= logging.ERROR
    assert all(h.level <= logging.ERROR for h in lg.handlers)


# --- MINOR 7: el texto del proveedor no llega al log ---------------------------------------------

SECRETO = "TEXTO-SECRETO-DEL-PROVEEDOR-7"


def _nucleo_falso(falla):
    class Atributos:
        scope_digest = "d"

        def __init__(self, *a, **kw):
            self.__dict__.update(kw)

    class Adaptador:
        def __init__(self, scope, receipt):
            pass

        def seal_non_governed_candidate(self, *, response_id, candidate_text):
            if falla == "seal":
                raise RuntimeError(f"seal fallo con {candidate_text}")
            return "envelope"

    class Renderizador:
        def render_text(self, envelope, context):
            if falla == "render":
                raise RuntimeError(f"render fallo con {SECRETO}")
            return types.SimpleNamespace(
                text="t", response_id="r", envelope_digest="d", source_envelope_digest="s",
                contract_state=types.SimpleNamespace(value="VALID"))

    estados = types.SimpleNamespace(DEGRADED_STRUCTURED=1, BLOCKED_SYSTEM_CLAIM=2)
    nucleo = (Atributos, Renderizador, Atributos, Adaptador, estados, Atributos, Atributos, Atributos)

    def mint(*a, **kw):
        raise RuntimeError(f"mint fallo con {SECRETO}")
    ciclo = types.SimpleNamespace(mint_governed_transport_unit=mint)
    return nucleo, ciclo


@pytest.mark.parametrize("etapa", ["seal", "render", "mint"])
def test_el_texto_del_proveedor_no_llega_al_log_si_falla_despues_de_core(monkeypatch, caplog, etapa):
    import api.governed_chat as gc

    nucleo, ciclo = _nucleo_falso(etapa)
    monkeypatch.setattr(gc, "_core", lambda: nucleo)
    monkeypatch.setattr(gc, "_lifecycle_core", lambda: ciclo)
    contrato = types.SimpleNamespace(contract_parsed=True, claims=[], analysis=SECRETO, judgment="")
    with caplog.at_level(logging.DEBUG):
        proy = gc.project_provider_contract(contrato, memory_scope=_ambito(), user_id="u1",
                                            request_id="req-1", trace_id="tr-1")
    assert proy.transport_unit is None and SECRETO not in proy.text
    errores = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errores) == 1 and "RuntimeError" in errores[0].getMessage()
    assert SECRETO not in caplog.text
    assert all(SECRETO not in str(a) for r in caplog.records for a in (r.args or ()))
