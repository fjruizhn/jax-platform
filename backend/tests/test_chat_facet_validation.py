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
import httpx
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
    fake = _FakePostClient(
        _FakeResponse({
            "choices": [{"message": {"content":
                '{"claim": [], "analysis": "faceta real, sin problema", "judgment": null}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }),
        authorize_response=_FakeResponse({"allowed": True, "reason": "OK"}),
    )
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


class _FakeFailingPostClient:
    """Simula las_manos caido de verdad -- conexion rechazada, no un
    mock que devuelve un error prolijo. Es el test que prueba que el
    gate gatea cuando mas importa (Requisito 3 del spec)."""
    async def post(self, url, **kwargs):
        raise httpx.ConnectError("Connection refused", request=None)


def test_chat_endpoint_denies_hipatia_when_authorize_facet_returns_false(client):
    token = create_access_token("test-authz-denied-user", "test-authz-denied-tenant", "operator")
    fake = _FakePostClient(_FakeResponse({"allowed": False, "reason": "caller no autorizado"}))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola", "facet": "hipatia"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200
    assert "no autorizado" in resp.json()["response"] or "no disponible" in resp.json()["response"]


def test_chat_endpoint_denies_hipatia_logs_the_reason_from_authorize_facet(client, caplog):
    # Review finding (Important): el except fail-closed no logueaba nada, y
    # el 'reason' que trae /motor/authorize-facet se leia en ningun lado --
    # justo el dato que existe para que la denegacion sea explicable. Este
    # test prueba que una denegacion LIMPIA (las_manos respondio, dijo que
    # no) deja ese reason en los logs, no solo el mensaje generico al
    # usuario. Ver tambien test_chat_endpoint_denies_hipatia_when_las_manos_is_down
    # para el caso "las_manos no respondio en absoluto" -- son casos
    # distintos y el fix los loguea distinto a proposito.
    token = create_access_token("test-authz-logs-user", "test-authz-logs-tenant", "operator")
    fake = _FakePostClient(_FakeResponse({"allowed": False, "reason": "caller no autorizado"}))
    original = http_client._client
    http_client._client = fake
    try:
        with caplog.at_level("WARNING", logger="api.chat"):
            resp = client.post(
                "/api/chat",
                json={"message": "hola", "facet": "hipatia"},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        http_client._client = original
    assert resp.status_code == 200
    assert any("caller no autorizado" in record.message for record in caplog.records)


def test_chat_endpoint_denies_hipatia_when_las_manos_is_down(client):
    """El caso critico: las_manos no responde en absoluto (ConnectError,
    no un 4xx/5xx prolijo). Fail-closed exige que esto tambien deniegue,
    no que se despache igual porque "no se pudo verificar"."""
    token = create_access_token("test-authz-down-user", "test-authz-down-tenant", "operator")
    original = http_client._client
    http_client._client = _FakeFailingPostClient()
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola", "facet": "hipatia"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200
    body = resp.json()["response"]
    assert "no autorizado" in body or "no disponible" in body


def test_chat_endpoint_allows_hipatia_when_authorize_facet_returns_true(client):
    token = create_access_token("test-authz-allowed-user", "test-authz-allowed-tenant", "operator")

    class _SequencedFakeClient:
        """Primera llamada = /motor/authorize-facet (allowed=True), segunda
        = la llamada real al proveedor del facet -- shape confirmado
        contra _call_gemini() (chat.py:552-579)."""
        def __init__(self):
            self._calls = 0

        async def post(self, url, **kwargs):
            self._calls += 1
            if "/motor/authorize-facet" in url:
                return _FakeResponse({"allowed": True, "reason": "OK"})
            return _FakeResponse({
                "candidates": [{"content": {"parts": [{"text": "hola desde hipatia"}]}}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
            })

    fake = _SequencedFakeClient()
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hola", "facet": "hipatia"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        http_client._client = original
    assert resp.status_code == 200
    assert "hola desde hipatia" in resp.json()["response"]
    # Exactamente dos llamadas: authorize-facet, despues el proveedor real.
    # Sin este assert, el test pasaria igual aunque la respuesta del
    # proveedor nunca llegara a dispararse.
    assert fake._calls == 2
