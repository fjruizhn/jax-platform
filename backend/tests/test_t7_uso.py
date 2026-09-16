"""Task 7 (2026-09-15): registro de uso -- perdidas visibles y validacion de
ids ANTES del LLM (hallazgos-laterales.md §4, opcion 1 decidida por Fernando).

1. `record_usage` sigue fail-soft (el turno ya se pago), pero cada fila que no
   se puede escribir sube `registros_perdidos` (por proceso, desde el arranque,
   patron de facet_health.write_failure_stats()) y GET /api/admin/usage lo
   expone: el total ya no se disfraza de completo.
2. `tenant_id`/`user_id` no numericos se rechazan en la entrada de /api/chat y
   de /api/image/generate, ANTES de resolver credencial o llamar al proveedor
   -- no recien en el INSERT, cuando el costo ya se pago.

Los tests puros usan un pool falso y corren sin DB; los que piden `client`
escriben en jax_memory_test (conftest) y se saltean sin DB.
"""
import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

from api import chat as chat_mod
from api import image as image_mod
from api.admin import usage as usage_mod
from auth.models import AuthUser
from uso import cola

SECRETO = "sk-FAKE-task7-0123456789abcdef"


@pytest.fixture(autouse=True)
def _contador_limpio():
    usage_mod.reset_registros_perdidos()
    cola.reset_estado()
    yield
    usage_mod.reset_registros_perdidos()
    cola.reset_estado()


def _respaldo_que_no_acepta(monkeypatch):
    """Desde la cola durable (2026-09-15, Task 2), `registros_perdidos` cuenta
    las filas que ADEMAS no pudieron encolarse: una fila en el respaldo no esta
    perdida, esta pendiente. Estos tests son sobre el contador de PERDIDAS, asi
    que le cierran la puerta al respaldo a proposito. El camino en que si entra
    esta en tests/test_respaldo_de_uso.py."""
    async def no_encola(fila):
        return None
    monkeypatch.setattr(usage_mod.cola, "encolar", no_encola)


def _pool_que_falla(monkeypatch, exc):
    async def get_pool():
        raise exc
    monkeypatch.setattr(usage_mod, "get_pool", get_pool)


def _record(user_id="1", tenant_id="1"):
    return asyncio.run(usage_mod.record_usage(
        user_id, tenant_id, "jekyll", "deepseek", "m", 10, 5, "chat", 0.01))


# --- 1. contador ---------------------------------------------------------------
def test_contador_parte_de_cero():
    stats = usage_mod.registros_perdidos_stats()
    assert stats["registros_perdidos"] == 0 and stats["ultimo_error"] is None


def test_fallo_del_insert_sube_el_contador_y_no_lanza(monkeypatch, caplog):
    _pool_que_falla(monkeypatch, RuntimeError("pool caido"))
    _respaldo_que_no_acepta(monkeypatch)
    with caplog.at_level("WARNING", logger="admin.usage"):
        assert _record() is None
        assert _record() is None
    stats = usage_mod.registros_perdidos_stats()
    assert stats["registros_perdidos"] == 2
    assert "RuntimeError" in stats["ultimo_error"]
    assert "record_usage failed" in caplog.text and "total=2" in caplog.text


def test_el_ultimo_error_y_el_log_van_redactados(monkeypatch, caplog):
    _pool_que_falla(monkeypatch, RuntimeError(f"fallo conectando api_key={SECRETO} al pool"))
    _respaldo_que_no_acepta(monkeypatch)
    with caplog.at_level("WARNING", logger="admin.usage"):
        _record()
    assert SECRETO not in usage_mod.registros_perdidos_stats()["ultimo_error"]
    assert SECRETO not in caplog.text


def test_escritura_exitosa_no_sube_el_contador(monkeypatch):
    class _Cur:
        # Como el cursor real de aiomysql: record_usage devuelve el id de la
        # fila escrita (fix wave final, 2026-09-15).
        lastrowid = 1
        async def execute(self, sql, params=None): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        async def commit(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Pool:
        def acquire(self): return _Conn()

    async def get_pool():
        return _Pool()
    monkeypatch.setattr(usage_mod, "get_pool", get_pool)
    _record()
    assert usage_mod.registros_perdidos_stats()["registros_perdidos"] == 0


# --- 2. validacion pura --------------------------------------------------------
@pytest.mark.parametrize("user_id,tenant_id", [("1", "1"), ("42", "7"), ("123456789", "0")])
def test_ids_numericos_pasan(user_id, tenant_id):
    assert usage_mod.validar_ids_de_uso(user_id, tenant_id) is None


@pytest.mark.parametrize("user_id,tenant_id", [
    ("1", ""), ("1", "TENANT-A"), ("abc", "1"), ("1", "1.5"), ("1", "-3"),
    ("1", " 1"), ("1", "١"), ("1", None), (None, "1"), ("1", "9" * 30),
])
def test_ids_no_numericos_se_rechazan_con_codigo_estable(user_id, tenant_id):
    with pytest.raises(HTTPException) as e:
        usage_mod.validar_ids_de_uso(user_id, tenant_id)
    assert e.value.status_code == 400
    assert e.value.detail == {"code": "ids_de_uso_invalidos"}


# --- 3. el chat corta ANTES del proveedor ---------------------------------------
def _usuario(tenant_id):
    return AuthUser(user_id="5", tenant_id=tenant_id, role="operator")


def test_chat_con_tenant_no_numerico_no_llama_al_proveedor(monkeypatch):
    llamadas = []

    async def invoke_espia(*a, **kw):
        llamadas.append(a)
        return "no deberia", None

    monkeypatch.setattr(chat_mod, "_load_config",
                        lambda: {"personalities": {"jekyll": {"system_prompt": "x"}}})
    monkeypatch.setattr(chat_mod, "_invoke_facet", invoke_espia)
    req = chat_mod.ChatRequest(message="hola", facet="jekyll")
    with pytest.raises(HTTPException) as e:
        asyncio.run(chat_mod.chat(req, BackgroundTasks(), user=_usuario("TENANT-A")))
    assert e.value.status_code == 400
    assert e.value.detail == {"code": "ids_de_uso_invalidos"}
    assert llamadas == [], "el proveedor no se llama con ids invalidos"


# --- 4. la imagen corta ANTES de la credencial y del proveedor -------------------
def test_imagen_con_tenant_vacio_no_resuelve_credencial_ni_llama(monkeypatch):
    llamadas = []

    async def credencial(*a):
        llamadas.append("credencial")
        return "k"

    async def cliente():
        llamadas.append("cliente")
        raise AssertionError("no se llega al proveedor")

    monkeypatch.setattr(image_mod, "resolve_credential_instrumented", credencial)
    monkeypatch.setattr(image_mod, "get_http_client", cliente)
    with pytest.raises(HTTPException) as e:
        asyncio.run(image_mod.generate_image(image_mod.ImageRequest(prompt="gato"), user=_usuario("")))
    assert e.value.detail == {"code": "ids_de_uso_invalidos"}
    assert llamadas == []


# --- 5. la vista de uso lo expone (con DB) --------------------------------------
def test_get_usage_expone_registros_perdidos(client, monkeypatch):
    from tests.identidades import cabeceras

    h = cabeceras(client, "t7-uso-superadmin", "superadmin")
    r0 = client.get("/api/admin/usage?period=day", headers=h)
    assert r0.status_code == 200, r0.text
    assert r0.json()["registros_perdidos"] == 0
    assert set(r0.json()) >= {"by_facet", "chart_data", "period"}, "los campos existentes siguen"

    async def get_pool_caido():
        raise RuntimeError("pool caido")
    monkeypatch.setattr(usage_mod, "get_pool", get_pool_caido)
    _respaldo_que_no_acepta(monkeypatch)
    client.portal.call(usage_mod.record_usage, "1", "1", "jekyll", "p", "m", 1, 1, "chat", 0.01)
    monkeypatch.undo()

    r1 = client.get("/api/admin/usage?period=day", headers=h)
    assert r1.json()["registros_perdidos"] == 1


# --- 4b (2026-09-15, cola durable): la vista distingue pendientes de perdidos ---
def test_get_usage_expone_pendientes_y_perdidas_por_desborde(client, monkeypatch, tmp_path):
    """La pantalla ya sabe mostrar tres estados (Task 4a), pero el handler no le
    mandaba los campos: `registros_perdidos_stats()` existía desde la Task 2 y
    sus únicos llamadores eran los tests. Sin este cableado, AdminCosts.jsx cae
    siempre al `|| 0` y el aviso de "hay N esperando reintento" no aparece nunca.

    Se listan los tres campos uno por uno en vez de desparramar el dict entero:
    `registros_perdidos_stats()` también trae `ultimo_error`, que es texto de
    error de la base y no tiene por qué viajar a una pantalla. Un `**stats`
    publicaría además cualquier campo que alguien le agregue mañana."""
    from tests.identidades import cabeceras
    from uso import cola

    h = cabeceras(client, "t4b-uso-superadmin", "superadmin")
    monkeypatch.setenv(cola.VARIABLE_DIRECTORIO, str(tmp_path / "respaldo"))
    cola.reset_estado()

    cuerpo = client.get("/api/admin/usage?period=day", headers=h).json()
    assert cuerpo["en_cola"] == 0
    assert cuerpo["perdidas_por_desborde"] == 0
    assert cuerpo["ultimo_reintento"] is None
    assert "ultimo_error" not in cuerpo, "el texto de error de la base no viaja a la pantalla"

    client.portal.call(cola.encolar, {
        "created_at": "2026-09-15T19:00:00+00:00", "tenant_id": 1, "user_id": 1,
        "facet": "jekyll", "model": "m", "tokens_in": 1, "tokens_out": 1,
        "cost_usd": 0.01, "request_type": "chat", "origen": "platform",
        "status": None, "job_id": None,
    })
    assert client.get("/api/admin/usage?period=day", headers=h).json()["en_cola"] == 1

    usage_mod.marcar_reintento("2026-09-15T20:00:00+00:00")
    assert client.get("/api/admin/usage?period=day", headers=h).json()["ultimo_reintento"] == (
        "2026-09-15T20:00:00+00:00"
    )
