"""
Spec §9.3: el camino de PRODUCCIÓN. Un POST /api/chat real produce UN solo
objeto snapshot y se verifica en sus dos consumidores:
  1. render(snapshot) está en el system prompt que salió al proveedor;
  2. ese mismo objeto es el que se encoló para run_shadow_validation.
La persistencia del sha256 de ese objeto la cubre
tests/test_shadow_validation_grounding.py (necesita conv_uuid, que acá es
None por los ids no numéricos -- mismo patrón que test_chat_contract_wrapper).
"""
from __future__ import annotations

import http_client
from unittest.mock import patch

from tests.test_chat_contract_wrapper import _FakeResponse


class _RecordingPostClient:
    def __init__(self):
        self.payloads = []

    async def post(self, url, **kwargs):
        if "/motor/authorize-facet" in url:
            return _FakeResponse({"allowed": True, "reason": "OK"})
        self.payloads.append(kwargs.get("json"))
        return _FakeResponse({
            "choices": [{"message": {"content": '{"claim": [], "analysis": "ok", "judgment": null}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })


def test_same_snapshot_object_reaches_prompt_and_background_task(client):
    import grounding as governance_grounding
    from auth.jwt import create_access_token
    token = create_access_token("test-grounding-user", "test-grounding-tenant", "operator")
    fake = _RecordingPostClient()
    captured = {}

    def spy_add_safe_task(background_tasks, fn, *args):
        captured["args"] = args

    original = http_client._client
    http_client._client = fake
    try:
        with patch("jax_engine.background.add_safe_task", side_effect=spy_add_safe_task):
            resp = client.post("/api/chat", json={"message": "hola", "facet": "jekyll"},
                               headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text

    # consumidor 2: el background task recibió el snapshot como 5º argumento
    conv_uuid, smid, facet, contract, grounding_obj = captured["args"]
    assert isinstance(grounding_obj, governance_grounding.Snapshot)
    assert len(grounding_obj.sha256) == 64

    # consumidor 1: el system prompt que salió contiene render() de ESE objeto
    assert len(fake.payloads) == 1
    system_prompt = fake.payloads[0]["messages"][0]["content"]
    assert governance_grounding.render(grounding_obj) in system_prompt
    # y el hash NO viajó (spec §5.1)
    assert grounding_obj.sha256 not in system_prompt


def test_snapshot_build_failure_is_marked_not_hidden(client, caplog):
    import grounding as governance_grounding
    from auth.jwt import create_access_token
    import api.chat as chat
    token = create_access_token("test-grounding-user-2", "test-grounding-tenant-2", "operator")
    fake = _RecordingPostClient()
    captured = {}

    def spy_add_safe_task(background_tasks, fn, *args):
        captured["args"] = args

    def boom():
        raise governance_grounding.GroundingBuildError("config ilegible")

    original = http_client._client
    http_client._client = fake
    try:
        with patch.object(chat, "_build_snapshot_or_raise", side_effect=boom), \
             patch("jax_engine.background.add_safe_task", side_effect=spy_add_safe_task), \
             caplog.at_level("ERROR", logger="api.chat"):
            resp = client.post("/api/chat", json={"message": "hola", "facet": "jekyll"},
                               headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original
    # el turno responde igual (el grounding es medición, no puede tumbar un chat)
    assert resp.status_code == 200, resp.text
    grounding_obj = captured["args"][4]
    assert isinstance(grounding_obj, governance_grounding.SnapshotError)
    assert "config ilegible" in grounding_obj.reason
    # y el prompt salió SIN bloque de hechos
    # El sufijo de contrato nombra el bloque ("la línea de HECHOS VERIFICADOS"),
    # así que el substring pelado siempre está. Lo que NO debe estar es el
    # bloque de render(): su primera línea, derivada del módulo, no hardcodeada.
    empty = governance_grounding.Snapshot(entries=(), canonical_json="{}", sha256="0" * 64)
    heading = governance_grounding.render(empty).splitlines()[0]
    assert heading not in fake.payloads[0]["messages"][0]["content"]
    # y la mitad "ruidosa" del contrato: el fallo quedó LOGUEADO con traceback,
    # no tragado en silencio (mismo criterio que el resto del módulo).
    records = [r for r in caplog.records if r.name == "api.chat"
               and "no se pudo construir el snapshot" in r.message]
    assert len(records) == 1
    assert records[0].exc_info is not None
