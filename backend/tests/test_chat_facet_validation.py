"""Validación de req.facet contra la whitelist de config["personalities"]
(REFORMAS-v3 Fase 2 Sub-proyecto 2, finding 1 de la revisión final del
branch completo).

Antes de este fix, req.facet (input de usuario, sin validar) pasaba
directo a _invoke_facet, que caía en silencio al fallback de jax_local
cuando la faceta no existía en config["personalities"] — y el valor
crudo, sin importar su longitud, llegaba igual a
shadow_messages.facet VARCHAR(30) (db/migrations.py), donde un valor
más largo que 30 caracteres hacía que el INSERT de la garantía
fail-closed lanzara "Data too long" — cero fila, no una fila con
validated_at NULL. Ver también shadow_validation.py::_insert_shadow_message
(clamp facet[:30], defensa en profundidad para cualquier otro caller de
run_shadow_validation).
"""
import http_client
from auth.jwt import create_access_token
from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse


def test_chat_endpoint_rejects_overlong_unknown_facet(client):
    # Reproduce el bug original: una faceta inventada, más larga que la
    # columna shadow_messages.facet VARCHAR(30), nunca debería llegar a
    # _invoke_facet ni a la validación de shadow — se rechaza acá mismo.
    token = create_access_token(
        "test-facet-validation-overlong-user",
        "test-facet-validation-overlong-tenant",
        "operator",
    )
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "facet": "x" * 100},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "faceta desconocida" in resp.json()["detail"]


def test_chat_endpoint_rejects_short_but_unrecognized_facet(client):
    # Una faceta corta (cabría sin problema en VARCHAR(30)) pero que no
    # está en config["personalities"] también debe rechazarse — el bug no
    # es solo de longitud, es de "cualquier facet no reconocida se cuela".
    token = create_access_token(
        "test-facet-validation-unknown-user",
        "test-facet-validation-unknown-tenant",
        "operator",
    )
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "facet": "no_existe"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "faceta desconocida" in resp.json()["detail"]


def test_chat_endpoint_accepts_known_facet_and_round_trips_it(client):
    # Complemento: una faceta real (existe en config["personalities"])
    # sigue funcionando exactamente igual que antes del fix — el request
    # se resuelve normalmente y el campo facet vuelve intacto en la
    # respuesta.
    token = create_access_token(
        "test-facet-validation-known-user",
        "test-facet-validation-known-tenant",
        "operator",
    )
    fake = _FakePostClient(_FakeResponse({
        "choices": [{"message": {"content":
            '{"claim": [], "analysis": "faceta real, sin problema", "judgment": null}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola", "facet": "jekyll"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["facet"] == "jekyll"
    assert "faceta real, sin problema" in body["response"]


def test_chat_endpoint_accepts_none_facet_and_auto_routes(client):
    # req.facet=None (o ausente) sigue yendo por _auto_route sin tocar la
    # validación nueva — no debe romperse el camino de auto-ruteo. La
    # faceta que _auto_route elija siempre está en config["personalities"]
    # por construcción, así que esto nunca debería dar 400. La respuesta
    # falsa trae ambas formas (Ollama y OpenAI-compat) porque no sabemos
    # de antemano a qué faceta va a rutear _auto_route.
    token = create_access_token(
        "test-facet-validation-none-user",
        "test-facet-validation-none-tenant",
        "operator",
    )
    fake = _FakePostClient(_FakeResponse({
        "message": {"content":
            '{"claim": [], "analysis": "auto-ruteado", "judgment": null}'},
        "choices": [{"message": {"content":
            '{"claim": [], "analysis": "auto-ruteado", "judgment": null}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola, sin faceta explicita"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
