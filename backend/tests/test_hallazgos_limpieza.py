"""Frente A, Task 1 (2026-09-16): recortes verificados por terceros (spec
2026-09-16-hallazgos-auditoria-design.md, A.1). Puros: sin DB ni red.

Cada test describe el estado NUEVO y falla contra 26c9cd5. La conducta que
el recorte no puede cambiar la fija el test que pasa antes y despues
(payload de los tokens, rutas registradas)."""
import asyncio
import os
from pathlib import Path

import pytest
from jose import jwt as jose_jwt

BACKEND = Path(__file__).resolve().parent.parent


def _fuente(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


# A-02
def test_requirements_no_declara_aiosmtplib():
    assert "aiosmtplib" not in _fuente("requirements.txt")
    assert "aiosmtplib" not in (BACKEND.parent / "ops/migration/MIGRATION.md").read_text(encoding="utf-8")


# A-03
def test_token_payload_ya_no_existe():
    import auth.models as modelos
    assert not hasattr(modelos, "TokenPayload")


# A-04: systemd carga el .env (EnvironmentFile=); un parser a mano re-inyectaba
# en corridas manuales valores CIFRADOS despues de decrypt_provider_keys_in_env.
@pytest.mark.parametrize("rel", ["api/chat.py", "api/image.py"])
def test_chat_e_image_no_parsean_el_env_a_mano(rel):
    fuente = _fuente(rel)
    assert "_load_jax_env" not in fuente
    assert 'open("/etc/jax/.env")' not in fuente


# A-12
def test_el_estado_no_guarda_un_mapa_usuario_tenant_que_nadie_lee():
    from jax_engine.state import JAXEngineState
    assert not hasattr(JAXEngineState(), "_user_tenant_map")


# A-15
async def test_human_gate_requested_solo_lleva_el_pipeline_id(monkeypatch):
    from jax_engine import state as state_mod
    from jax_engine.schemas import PipelineState

    publicados = []

    async def capturar(evento):
        publicados.append(evento)

    monkeypatch.setattr(state_mod.event_bus, "publish", capturar)

    class _Respuesta:
        status_code = 200

        def json(self):
            return {"pipeline": {"status": "interrupted"}, "steps": []}

    class _Cliente:
        async def get(self, url, timeout=None):
            return _Respuesta()

    estado = state_mod.JAXEngineState()
    pid = "33333333-cccc-4ccc-8ccc-000000000003"
    previo = PipelineState(pipeline_id=pid, tenant_id="T", user_id="U", name="p", status="running")
    await estado._poll_one_pipeline(_Cliente(), pid, previo)
    (gate,) = [e for e in publicados if e.event_type == "human_gate_requested"]
    assert gate.payload == {"pipeline_id": pid}


# A-17
def test_ws_notifications_no_es_un_ajuste():
    from api.admin import config_admin
    assert "ws_notifications" not in config_admin.DEFAULT_CONFIG


# A-18
def test_cors_solo_admite_frontend_origin():
    import main
    (cors,) = [m for m in main.app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    esperado = [o for o in [os.getenv("FRONTEND_ORIGIN", "")] if o]
    assert cors.kwargs["allow_origins"] == esperado


# A-19: la conducta (payload) no cambia; la estructura si.
def test_los_tokens_conservan_su_payload(monkeypatch):
    from auth import jwt as jwt_mod
    monkeypatch.setattr(jwt_mod.time, "time", lambda: 1_000_000)
    acceso = jose_jwt.get_unverified_claims(jwt_mod.create_access_token("7", "1", "operator", 3))
    refresco = jose_jwt.get_unverified_claims(jwt_mod.create_refresh_token("7", "1", "operator", 3))
    assert acceso == {"user_id": "7", "tenant_id": "1", "role": "operator", "tv": 3,
                      "exp": 1_000_000 + 15 * 60, "type": "access"}
    assert refresco == {"user_id": "7", "tenant_id": "1", "role": "operator", "tv": 3,
                        "exp": 1_000_000 + 7 * 24 * 3600, "type": "refresh"}


def test_los_dos_tokens_salen_de_un_solo_constructor():
    from auth import jwt as jwt_mod
    assert callable(getattr(jwt_mod, "_crear_token", None))
    assert _fuente("auth/jwt.py").count("jwt.encode(") == 1


# A-20: el endpoint acepta el socket antes de llamar al hub (main.py).
async def test_el_hub_no_acepta_sockets_por_su_cuenta():
    from jax_engine.websocket_hub import WebSocketHub

    class _SinAceptar:
        application_state = type("_E", (), {"name": "CONNECTING"})()
        aceptado = False

        async def accept(self):
            self.aceptado = True

    socket = _SinAceptar()
    await WebSocketHub().connect("u", socket)
    assert socket.aceptado is False


# A-28
async def test_solo_read_committed_es_un_aislamiento_valido():
    from db import transaccion as tx
    assert tx.AISLAMIENTOS == frozenset({"READ COMMITTED"})
    with pytest.raises(ValueError):
        async with tx.transaccion("REPEATABLE READ"):
            pass


# A-33: la lista de routers es datos; la conducta (rutas registradas) no cambia.
def test_main_registra_cada_router_de_la_tupla_una_vez():
    """FastAPI 0.139 ya no aplana los routers incluidos (ver
    test_fijar_password.py::test_solo_me_y_mi_cuenta_admiten_la_marca,
    medido 2026-09-15): `app.routes` trae `_IncludedRouter`, sin `.path`.
    `iter_route_contexts` es la API pública que da la ruta EFECTIVA."""
    from fastapi.routing import iter_route_contexts
    import main
    rutas = {rc.path for rc in iter_route_contexts(main.app.routes)}
    assert _fuente("main.py").count("app.include_router(") == 1
    for router in main.ROUTERS:
        for ruta in router.routes:
            assert ruta.path in rutas, ruta.path
    assert not any(p and p.startswith("/api/admin/facet-models") for p in rutas)


# A-34
def test_el_router_legado_de_facet_models_no_existe():
    assert not (BACKEND / "api/admin/facet_models.py").exists()
    for rel in ("main.py", "api/admin/__init__.py", "db/migrations.py"):
        assert "facet_models.py" not in _fuente(rel), rel
