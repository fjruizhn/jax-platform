"""Task 6, follow-ups (2026-09-15, fallos del controller T6-1, T6-2, T6-5a, T6-5b).

- T6-1: el filtro que redacta los loggers de httpx vale para un pedido REAL.
- T6-2: la key de Gemini viaja en la cabecera `x-goog-api-key`, nunca en la
  URL, en los 4 lugares (chat, sync del catalogo, test de /keys y test de
  /credentials). La redaccion de S1 queda como defensa en profundidad.
- T6-5a: GET /api/pipelines lee de jacobs_pipelines con la regla de dueño
  (antes hacia proxy de un GET de Jacobs que no existe y no filtraba nada).
- T6-5b: POST /api/facets/{facet}/status solo superadmin (sin llamador en el
  frontend ni en el repo jax).

Todo el HTTP pasa por httpx.MockTransport: no se llama a Google. La key es
FALSA. Los tests con `client` usan jax_memory_test y se saltean sin DB.
"""
import asyncio
import logging
import time
import uuid

import httpx
import pytest

import http_client  # noqa: F401 -- instala el filtro de los loggers de httpx
import model_catalog
from api import chat as chat_mod
from api import pipelines as pipelines_mod
from api.admin import credentials as credentials_mod
from auth.models import AuthUser
from tests.identidades import cabeceras, sql, uid

KEY = "AIzaFAKE-t6seg-0123456789abcdef"
SUPERADMIN = AuthUser(user_id="1", tenant_id="1", role="superadmin")


class _Captura:
    """MockTransport que guarda cada pedido y responde 200 con `json`."""

    def __init__(self, json):
        self.pedidos = []
        self._json = json

    def cliente(self):
        def handler(request):
            self.pedidos.append(request)
            return httpx.Response(200, json=self._json)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sin_key_en_la_url(request):
    assert "key=" not in str(request.url)
    assert KEY not in str(request.url)
    assert request.headers.get("x-goog-api-key") == KEY


# --- T6-1 ------------------------------------------------------------------------
def test_T6_1_un_pedido_real_de_httpx_no_deja_la_key_en_el_log(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    cap = _Captura({})

    async def pedir():
        async with cap.cliente() as c:
            await c.get(f"https://g.example/v1beta/models?key={KEY}")

    asyncio.run(pedir())
    assert "HTTP Request: GET" in caplog.text
    assert KEY not in caplog.text


# --- T6-2 ------------------------------------------------------------------------
def test_T6_2_chat_manda_la_key_en_la_cabecera(monkeypatch):
    cap = _Captura({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    cliente = cap.cliente()

    async def get_client():
        return cliente

    monkeypatch.setattr(chat_mod, "get_http_client", get_client)
    texto, _, _ = asyncio.run(chat_mod._call_gemini(KEY, "gemini-2.5-pro", "s", [], "hola"))
    assert texto == "ok"
    (req,) = cap.pedidos
    _sin_key_en_la_url(req)
    assert req.url.path == "/v1beta/models/gemini-2.5-pro:generateContent"


class _Cur:
    def __init__(self, filas): self.filas = list(filas); self.sql = []
    async def execute(self, q, p=None): self.sql.append((q, p))
    async def fetchone(self): return self.filas.pop(0) if self.filas else None
    async def fetchall(self): return []
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Conn:
    def __init__(self, cur): self.cur = cur
    def cursor(self): return self.cur
    async def commit(self): pass
    async def rollback(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Pool:
    def __init__(self, cur): self.cur = cur
    def acquire(self): return _Conn(self.cur)


def _pool_con(monkeypatch, modulo, filas):
    cur = _Cur(filas)

    async def get_pool():
        return _Pool(cur)

    monkeypatch.setattr(modulo, "get_pool", get_pool)
    return cur


def test_T6_2_sync_del_catalogo_manda_la_key_en_la_cabecera(monkeypatch):
    _pool_con(monkeypatch, model_catalog, [
        ("header_goog_api_key", "https://generativelanguage.googleapis.com/v1beta/models")])
    cap = _Captura({"models": []})
    cliente = cap.cliente()

    async def get_client():
        return cliente

    async def cred(provider_id):
        return KEY

    monkeypatch.setattr(model_catalog, "get_http_client", get_client)
    monkeypatch.setattr(model_catalog, "resolve_credential", cred)
    asyncio.run(model_catalog.sync_provider_models("gemini"))
    (req,) = cap.pedidos
    _sin_key_en_la_url(req)
    assert "authorization" not in req.headers


def test_T6_2_el_transporte_query_param_ya_no_se_usa(monkeypatch):
    """Fail-closed: una fila que quedara con el valor viejo no vuelve a poner
    la key en la URL; falla, y el sync lo reporta como error del provider."""
    _pool_con(monkeypatch, model_catalog, [
        ("query_param", "https://generativelanguage.googleapis.com/v1beta/models")])
    cap = _Captura({"models": []})
    cliente = cap.cliente()

    async def get_client():
        return cliente

    async def cred(provider_id):
        return KEY

    monkeypatch.setattr(model_catalog, "get_http_client", get_client)
    monkeypatch.setattr(model_catalog, "resolve_credential", cred)
    with pytest.raises(ValueError) as exc:
        asyncio.run(model_catalog.sync_provider_models("gemini"))
    assert KEY not in str(exc.value)
    assert cap.pedidos == []


def test_T6_2_test_de_credencial_manda_la_key_en_la_cabecera(monkeypatch):
    import crypto_secrets
    _pool_con(monkeypatch, credentials_mod, [(7, b"cifrado")])
    monkeypatch.setattr(crypto_secrets, "decrypt_db_secret", lambda enc: KEY)
    cap = _Captura({"models": []})
    cliente = cap.cliente()

    async def get_client():
        return cliente

    monkeypatch.setattr(credentials_mod, "get_http_client", get_client)

    class _Req:
        client = None

    body = asyncio.run(credentials_mod.test_credential("gemini", request=_Req(), user=SUPERADMIN))
    assert body["ok"] is True
    (req,) = cap.pedidos
    _sin_key_en_la_url(req)


def test_T6_2_test_de_keys_manda_la_key_en_la_cabecera(client, monkeypatch):
    from api.admin import keys as keys_mod
    cap = _Captura({"models": []})
    cliente = cap.cliente()

    async def get_client():
        return cliente

    async def db_key(pool, user_id, provider_id):
        return KEY

    monkeypatch.setattr(keys_mod, "get_http_client", get_client)
    monkeypatch.setattr(keys_mod, "_get_db_key", db_key)
    resp = client.post("/api/admin/keys/gemini/test", headers=cabeceras(client, "t6seg-admin", "superadmin"))
    assert resp.status_code == 200, resp.text
    (req,) = cap.pedidos
    _sin_key_en_la_url(req)


def test_T6_2_la_migracion_deja_a_gemini_con_la_cabecera(client):
    filas = client.portal.call(sql, "SELECT api_key_transport FROM provider WHERE id='gemini'", (), True)
    assert [f[0] for f in filas] == ["header_goog_api_key"]
    viejas = client.portal.call(sql, "SELECT COUNT(*) FROM provider WHERE api_key_transport='query_param'", (), True)
    assert viejas[0][0] == 0


# --- T6-5a -------------------------------------------------------------------------
async def _insertar(pipeline_id, user_id, tenant_id, owner_ack_at, nombre, creado):
    await sql(
        "INSERT INTO jacobs_pipelines "
        "(pipeline_id, name, invoked_by, mode, status, created_at, updated_at, user_id, tenant_id, owner_ack_at) "
        "VALUES (%s, %s, 'plataforma', 'supervised', 'running', %s, %s, %s, %s, %s)",
        (pipeline_id, nombre, creado, creado, user_id, tenant_id, owner_ack_at))


async def _borrar(ids):
    for pid in ids:
        await sql("DELETE FROM jacobs_pipelines WHERE pipeline_id=%s", (pid,))


@pytest.fixture
def pipelines_de_dos_tenants(client):
    duenio = uid(client, "t6seg-lista-duenio", "operator")
    ahora = time.time()
    ids = {k: str(uuid.uuid4()) for k in ("viejo", "nuevo", "sin_ack", "ajeno")}
    client.portal.call(_insertar, ids["viejo"], duenio, "TENANT-A", ahora, "mio viejo", ahora - 10)
    client.portal.call(_insertar, ids["nuevo"], duenio, "TENANT-A", ahora, "mio nuevo", ahora)
    client.portal.call(_insertar, ids["sin_ack"], duenio, "TENANT-A", None, "sin ack", ahora)
    client.portal.call(_insertar, ids["ajeno"], "otro-user", "TENANT-B", ahora, "secreto de B", ahora)
    yield ids
    client.portal.call(_borrar, list(ids.values()))


def test_T6_5a_el_duenio_ve_solo_los_suyos_mas_nuevos_primero(client, pipelines_de_dos_tenants):
    ids = pipelines_de_dos_tenants
    resp = client.get("/api/pipelines",
                      headers=cabeceras(client, "t6seg-lista-duenio", "operator", tenant_id="TENANT-A"))
    assert resp.status_code == 200, resp.text
    lista = resp.json()["pipelines"]
    assert [p["pipeline_id"] for p in lista] == [ids["nuevo"], ids["viejo"]]
    assert lista[0]["name"] == "mio nuevo"
    assert "secreto de B" not in resp.text


def test_T6_5a_un_usuario_de_otro_tenant_no_ve_el_pipeline(client, pipelines_de_dos_tenants):
    resp = client.get("/api/pipelines",
                      headers=cabeceras(client, "t6seg-lista-ajeno", "viewer", tenant_id="TENANT-B"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["pipelines"] == []
    assert "mio nuevo" not in resp.text


def test_T6_5a_mismo_usuario_en_otro_tenant_no_es_el_duenio(client, pipelines_de_dos_tenants):
    resp = client.get("/api/pipelines",
                      headers=cabeceras(client, "t6seg-lista-duenio", "operator", tenant_id="TENANT-B"))
    assert resp.json()["pipelines"] == []


def test_T6_6_el_indice_de_duenio_existe_con_sus_columnas(client):
    """Ruling T6-6 (2026-09-15): el indice es de `jacobs_pipelines`, tabla del
    repo jax. DUEÑO Y FUENTE DE VERDAD: jax/jacobs/store.py::init_tables().
    La plataforma NO corre DDL sobre esa tabla. En CI la crea el paso "Crear
    el esquema de Jacobs con SU propio init_tables()" (policy.yml, clona jax
    master). Si falta, este test FALLA con el motivo -- no se saltea: una
    consulta de camino caliente sin indice no es un estado aceptable."""
    filas = client.portal.call(
        sql,
        "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jacobs_pipelines' "
        "AND INDEX_NAME = 'idx_jacobs_pipelines_duenio' ORDER BY SEQ_IN_INDEX", (), True)
    assert [f[0] for f in filas] == ["user_id", "tenant_id", "created_at"], (
        "jacobs_pipelines no tiene idx_jacobs_pipelines_duenio (user_id, tenant_id, "
        "created_at). Lo crea jax/jacobs/store.py::init_tables(): correr el init_tables() "
        "de jax (con el PR del indice) contra esta base.")


def test_T6_5a_la_consulta_usa_el_indice_de_duenio(client, pipelines_de_dos_tenants):
    """LAS CUATRO (indexing): EXPLAIN sobre la consulta REAL. El indice lo crea
    jax/jacobs/store.py (ver el test de arriba), no la plataforma.

    `idx_pipelines_visibles`, no `idx_jacobs_pipelines_duenio` (Task 4,
    descartar-pipelines, fix round 4, Ruling 18/19, 2026-09-22) -- ver la
    nota igual en test_historial_pipelines.py::test_la_consulta_paginada_usa_el_indice_de_dueño_sin_filesort_ni_temporary.
    El nombre del test se queda ("de dueño"): la fila sigue filtrada por
    dueño (user_id+tenant_id), el índice sólo agrega `visible` para acotar
    el costo por el LIMIT."""
    filas = client.portal.call(sql, "EXPLAIN " + pipelines_mod.SQL_PIPELINES_DEL_USUARIO,
                               ("x", "TENANT-A", pipelines_mod.LISTA_PIPELINES_MAX, 0), True)
    ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
    assert tabla == "jacobs_pipelines"
    assert clave == "idx_pipelines_visibles", filas
    assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas


# --- T6-5b -------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_T6_5b_cambiar_el_estado_de_una_faceta_es_403_si_no_es_superadmin(client, role):
    resp = client.post("/api/facets/thot/status", json={"status": "offline"},
                       headers=cabeceras(client, "t6seg-facet", role))
    assert resp.status_code == 403, resp.text


def test_T6_5b_el_superadmin_puede_cambiar_el_estado(client):
    resp = client.post("/api/facets/thot/status", json={"status": "idle"},
                       headers=cabeceras(client, "t6seg-facet", "superadmin"))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "facet": "thot", "status": "idle"}
