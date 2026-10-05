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
               runtime_status=RUNTIME_STATUS_API_VERSION_ESPERADA):
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


def test_verificar_par_compatible_pasa(jax_en):
    from par_jax import verificar_par_jax

    jax_en()
    verificar_par_jax()


def _arrancar():
    import main

    async def correr():
        async with main.lifespan(main.app):
            pass
    return correr


def test_lifespan_con_jax_incompatible_no_arranca_ni_abre_la_base(jax_en, monkeypatch):
    import main
    from par_jax import ParJaxIncompatible

    jax_en(domain="f2-c.domain.6")
    llamadas = []

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.setattr(main, "get_pool", pool_espia)
    with pytest.raises(ParJaxIncompatible):
        asyncio.run(_arrancar()())
    assert llamadas == []


def test_lifespan_con_jax_compatible_pasa_el_chequeo_y_sigue(jax_en, monkeypatch):
    import main

    jax_en()

    class SiguioAdelante(Exception):
        pass

    def siguiente_paso():
        raise SiguioAdelante

    monkeypatch.setattr(main.limites_de_adjuntos, "cargar_limites", siguiente_paso)
    with pytest.raises(SiguioAdelante):
        asyncio.run(_arrancar()())
