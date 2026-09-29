"""F2-C platform bridge: raw provider candidates never become HTTP text."""
from pathlib import Path

from api.chat import _parse_contract_response
from api.governed_chat import _DEGRADED_NOTICE, project_provider_contract
from jax.memory.b9 import ScopeContext


def _scope():
    return ScopeContext(
        actor_principal="user:7", actor_type="USER", subject_user_id="7",
        tenant_id="1", project_id=None, calling_component="jax-platform-web-chat",
    )


def test_missing_f2c_core_fails_closed_without_provider_candidate(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", "/tmp/f2c-core-does-not-exist")
    raw = '{"claim": [], "analysis": "<b>CURRENT</b> server is healthy", "judgment": null}'
    result = project_provider_contract(_parse_contract_response(raw), memory_scope=_scope(), user_id="7")
    assert result.text == _DEGRADED_NOTICE
    assert "CURRENT" not in result.text
    assert result.contract_degraded is True
    assert result.governed_plain is True


def test_malformed_provider_contract_never_uses_raw_text(monkeypatch):
    monkeypatch.setenv("JAX_REPO_PATH", "/tmp/f2c-core-does-not-exist")
    raw = '{"claim": [{"predicate": "ENGINE_STATUS"'
    result = project_provider_contract(_parse_contract_response(raw), memory_scope=_scope(), user_id="7")
    assert result.text == _DEGRADED_NOTICE
    assert raw not in result.text
    assert result.contract_state == "UNAVAILABLE"


def test_web_chat_route_uses_single_governed_pre_display_bridge():
    source = Path(__file__).parents[1] / "api" / "chat.py"
    text = source.read_text(encoding="utf-8")
    route = text[text.index("async def chat("):text.index("async def _fire_completed(")]
    assert "project_provider_contract(" in route
    assert route.index("project_provider_contract(") < route.rindex("_update_history(")
    assert "_build_display_response(contract)" not in route
