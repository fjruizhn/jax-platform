# Backend httpx Connection Pooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 15 per-call `httpx.AsyncClient(...)` instantiations scattered across jax-platform's backend with a single shared, pooled client reused for the lifetime of the process — eliminating a fresh TCP/TLS handshake on every outbound request to Jacobs/LAS MANOS, Ollama, and provider LLM APIs.

**Architecture:** A module-level singleton (`backend/http_client.py`) mirroring the existing `db/connection.py` pooling pattern exactly: lazy `get_http_client()` creates one `httpx.AsyncClient()` on first use, `close_http_client()` tears it down. Wired into `main.py`'s `lifespan` the same way `get_pool()`/`close_pool()` already are — called by direct import everywhere, never via `app.state` or `Depends`, matching this codebase's established convention (confirmed: `app.state` has zero existing usages in this repo). No client-level default timeout is set; every call site keeps its own per-request `timeout=` kwarg exactly as today, so external behavior (which endpoints wait how long) does not change.

**Tech Stack:** Python 3.14, FastAPI, httpx 0.28.1 (`>=0.27` in requirements.txt, unpinned), pytest + pytest-asyncio (`asyncio_mode = auto` in pytest.ini), aiomysql (existing pattern being mirrored).

## Global Constraints

- Every outbound `client.get()`/`client.post()` call must keep its exact current `timeout=` value (ranges from 3.0s to 180.0s across sites) — pass it as a per-call kwarg since the shared client has no client-level default.
- No route handler signature may be changed to add `request: Request` — the shared client is reached by direct import (`from http_client import get_http_client`), matching how `db/connection.get_pool()` is already used everywhere in this codebase.
- Do not change any existing error-handling/except behavior, response shapes, or status codes — this is a pooling refactor only, not a behavior change.
- `backend/jax_engine/state.py`'s `engine_state = JAXEngineState()` singleton is instantiated at module import time, before the FastAPI `app` object exists — the fix for its two call sites must not depend on `app` being constructed first.
- Run `python -m py_compile` on every modified `.py` file before considering a task done (repo convention, see global CLAUDE.md "Regla del carpintero").
- All tests run via `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/<file> -v`.

---

### Task 1: Shared httpx client singleton

**Files:**
- Create: `backend/http_client.py`
- Test: `backend/tests/test_http_client.py`

**Interfaces:**
- Produces: `async def get_http_client() -> httpx.AsyncClient` (returns the same instance across calls until closed), `async def close_http_client() -> None` (closes and resets the singleton so a later `get_http_client()` call creates a fresh instance).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_http_client.py
import httpx

import http_client


async def test_get_http_client_returns_same_instance_across_calls():
    try:
        c1 = await http_client.get_http_client()
        c2 = await http_client.get_http_client()
        assert c1 is c2
        assert isinstance(c1, httpx.AsyncClient)
    finally:
        await http_client.close_http_client()


async def test_close_http_client_resets_the_singleton():
    c1 = await http_client.get_http_client()
    await http_client.close_http_client()
    assert c1.is_closed

    c2 = await http_client.get_http_client()
    try:
        assert c2 is not c1
        assert not c2.is_closed
    finally:
        await http_client.close_http_client()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_http_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'http_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/http_client.py
import httpx

_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_http_client():
    global _client
    if _client:
        await _client.aclose()
        _client = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_http_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify it compiles and commit**

Run: `cd /home/fruiz/jax-platform/backend && python -m py_compile http_client.py`

```bash
cd /home/fruiz/jax-platform
git add backend/http_client.py backend/tests/test_http_client.py
git commit -m "feat(backend): add shared httpx client singleton"
```

---

### Task 2: Wire the shared client into app lifespan

**Files:**
- Modify: `backend/main.py:1-64`
- Test: `backend/tests/test_http_client.py` (append)

**Interfaces:**
- Consumes: `get_http_client`, `close_http_client` from Task 1 (`http_client.py`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_http_client.py`:

```python
def test_shared_http_client_is_created_at_app_startup(client):
    assert http_client._client is not None
    assert isinstance(http_client._client, httpx.AsyncClient)
    assert not http_client._client.is_closed
```

(`client` is the session-scoped `TestClient` fixture from `tests/conftest.py`, which enters the app's real `lifespan` via `with TestClient(app) as c:` — its mere presence as a fixture argument triggers startup.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_http_client.py::test_shared_http_client_is_created_at_app_startup -v`
Expected: FAIL — `http_client._client` is `None` because nothing calls `get_http_client()` at startup yet.

- [ ] **Step 3: Wire it into `lifespan`**

In `backend/main.py`, change the import line:

```python
from db.connection import get_pool, close_pool
```
to:
```python
from db.connection import get_pool, close_pool
from http_client import get_http_client, close_http_client
```

Change the `lifespan` function body from:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    await run_migrations()
    await run_seed()
    engine_state.start_background_tasks()
    yield
    # Cerrar conversaciones web abiertas -> el worker de facts las destila.
    try:
        from api.chat import flush_open_conversations
        n = await flush_open_conversations()
        if n:
            # flush=True: sin esto el print se pierde por buffering al salir el proceso.
            print(f"[memoria] {n} conversación(es) web cerradas en shutdown", flush=True)
    except Exception:
        pass
    await close_pool()
```
to:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    await get_http_client()
    await run_migrations()
    await run_seed()
    engine_state.start_background_tasks()
    yield
    # Cerrar conversaciones web abiertas -> el worker de facts las destila.
    try:
        from api.chat import flush_open_conversations
        n = await flush_open_conversations()
        if n:
            # flush=True: sin esto el print se pierde por buffering al salir el proceso.
            print(f"[memoria] {n} conversación(es) web cerradas en shutdown", flush=True)
    except Exception:
        pass
    await close_http_client()
    await close_pool()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_http_client.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full existing suite to confirm no regression, then commit**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest -v`
Expected: all tests that passed before still pass.

```bash
cd /home/fruiz/jax-platform
git add backend/main.py backend/tests/test_http_client.py
git commit -m "feat(backend): create shared httpx client at app startup, close at shutdown"
```

---

### Task 3: Migrate `api/pipelines.py` (6 call sites) to the shared client

**Files:**
- Modify: `backend/api/pipelines.py` (full file, 6 functions)
- Test: `backend/tests/test_pipelines_http_pooling.py`

**Interfaces:**
- Consumes: `get_http_client` from Task 1.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pipelines_http_pooling.py
import httpx

from auth.jwt import create_access_token

USER_ID = "test-pipelines-pooling-user"
TENANT_ID = "test-pipelines-pooling-tenant"


def _headers():
    token = create_access_token(USER_ID, TENANT_ID, "operator")
    return {"Authorization": f"Bearer {token}"}


class _ClientInstantiationCounter:
    """Wraps httpx.AsyncClient.__init__ to count NEW instantiations, without
    touching the already-running shared client created at app startup."""

    def __init__(self):
        self.count = 0
        self._original = httpx.AsyncClient.__init__

    def __enter__(self):
        original = self._original
        counter = self

        def wrapped(self_client, *args, **kwargs):
            counter.count += 1
            return original(self_client, *args, **kwargs)

        httpx.AsyncClient.__init__ = wrapped
        return self

    def __exit__(self, *exc):
        httpx.AsyncClient.__init__ = self._original


def test_pipeline_endpoints_do_not_create_a_new_client_per_request(client):
    """LAS MANOS is not running in the test environment, so every call
    degrades gracefully (connection refused -> caught Exception branch) —
    this only pins that no NEW httpx.AsyncClient() is instantiated per
    request now that all 6 sites share the app-startup client."""
    with _ClientInstantiationCounter() as counter:
        resp = client.get("/api/pipelines", headers=_headers())
        assert resp.status_code == 200

        resp = client.get("/api/pipelines/fake-id", headers=_headers())
        assert resp.status_code in (200, 502)

        resp = client.get("/api/pipelines/fake-id/results", headers=_headers())
        assert resp.status_code in (200, 502)

    assert counter.count == 0, (
        f"expected 0 new httpx.AsyncClient() instantiations across 3 pipeline "
        f"requests (shared client created once at app startup), got {counter.count}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_pipelines_http_pooling.py -v`
Expected: FAIL — `counter.count` is 3 (one new client per request), not 0.

- [ ] **Step 3: Migrate all 6 sites**

Replace the full contents of `backend/api/pipelines.py`:

```python
import os
from fastapi import APIRouter, Depends, HTTPException, Request, status
from auth.middleware import get_current_user
from auth.models import AuthUser
from http_client import get_http_client
from jax_engine.resource_manager import resource_manager
from jax_engine.state import engine_state
from jax_engine.schemas import PipelineState

router = APIRouter(prefix="/api/pipelines")

JACOBS_URL = os.getenv("JACOBS_URL", "http://127.0.0.1:7777/jacobs")


@router.get("")
async def list_pipelines(user: AuthUser = Depends(get_current_user)):
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline", timeout=5.0)
        return r.json()
    except Exception:
        return {"pipelines": [], "error": "LAS MANOS no disponible"}


@router.post("")
async def create_pipeline(request: Request, user: AuthUser = Depends(get_current_user)):
    if not await resource_manager.can_start_pipeline(user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Límite de 3 pipelines concurrentes alcanzado",
        )
    body = await request.json()
    client = await get_http_client()
    try:
        r = await client.post(f"{JACOBS_URL}/pipeline", json=body, timeout=10.0)
        data = r.json()
        if r.status_code == 200:
            pipeline_id = data.get("pipeline_id")
            if pipeline_id:
                await resource_manager.admit_pipeline(user.tenant_id, pipeline_id)
                initial = PipelineState(
                    pipeline_id=pipeline_id,
                    tenant_id=user.tenant_id,
                    user_id=user.user_id,
                    name=body.get("name", "Pipeline"),
                    status="running",
                )
                await engine_state.upsert_pipeline(initial, user.tenant_id, user.user_id)
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}/results")
async def get_pipeline_results(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results", timeout=10.0)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, user: AuthUser = Depends(get_current_user)):
    client = await get_http_client()
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}", timeout=5.0)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{pipeline_id}/resume")
async def resume_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    client = await get_http_client()
    try:
        r = await client.post(
            f"{JACOBS_URL}/pipeline/{pipeline_id}/resume",
            json={"invoked_by": "Fernando"},
            timeout=10.0,
        )
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{pipeline_id}/cancel")
async def cancel_pipeline(
    pipeline_id: str,
    user: AuthUser = Depends(get_current_user),
):
    client = await get_http_client()
    try:
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel", timeout=10.0)
        if r.status_code == 200:
            engine_state.remove_pipeline(pipeline_id)
            await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_pipelines_http_pooling.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Verify it compiles, run full suite, commit**

```bash
cd /home/fruiz/jax-platform/backend
python -m py_compile api/pipelines.py
.venv/bin/pytest -v
```
Expected: all tests pass (no regressions in `test_pipeline_user_id.py` or others touching this file).

```bash
cd /home/fruiz/jax-platform
git add backend/api/pipelines.py backend/tests/test_pipelines_http_pooling.py
git commit -m "perf(backend): reuse shared httpx client in api/pipelines.py"
```

---

### Task 4: Migrate `api/admin/dashboard.py` (1 call site, invoked twice per request)

**Files:**
- Modify: `backend/api/admin/dashboard.py:1-30`
- Test: `backend/tests/test_dashboard_http_pooling.py`

**Interfaces:**
- Consumes: `get_http_client` from Task 1.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_dashboard_http_pooling.py
import httpx

from auth.jwt import create_access_token

USER_ID = "test-dashboard-pooling-user"
TENANT_ID = "test-dashboard-pooling-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


class _ClientInstantiationCounter:
    def __init__(self):
        self.count = 0
        self._original = httpx.AsyncClient.__init__

    def __enter__(self):
        original = self._original
        counter = self

        def wrapped(self_client, *args, **kwargs):
            counter.count += 1
            return original(self_client, *args, **kwargs)

        httpx.AsyncClient.__init__ = wrapped
        return self

    def __exit__(self, *exc):
        httpx.AsyncClient.__init__ = self._original


def test_dashboard_health_checks_do_not_create_new_clients(client):
    """get_dashboard() calls _check_http() twice per request (LAS MANOS +
    JAX Engine health). Both services are unreachable in the test env, so
    this only pins zero new httpx.AsyncClient() instantiations."""
    with _ClientInstantiationCounter() as counter:
        resp = client.get("/api/admin/dashboard", headers=_superadmin_headers())
        assert resp.status_code == 200

    assert counter.count == 0, (
        f"expected 0 new httpx.AsyncClient() instantiations for 2 internal "
        f"health checks per dashboard request, got {counter.count}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_dashboard_http_pooling.py -v`
Expected: FAIL — `counter.count` is 2, not 0.

- [ ] **Step 3: Migrate the site**

In `backend/api/admin/dashboard.py`, change the import line:
```python
import os
import httpx
import psutil
```
to:
```python
import os
import psutil
```
and add, alongside the other local imports:
```python
from http_client import get_http_client
```

Change `_check_http`:
```python
async def _check_http(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            t0 = datetime.utcnow()
            r = await client.get(url)
            ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
            return {"status": "alive" if r.status_code < 500 else "down", "latency_ms": ms}
    except Exception:
        return {"status": "down", "latency_ms": None}
```
to:
```python
async def _check_http(url: str) -> dict:
    try:
        client = await get_http_client()
        t0 = datetime.utcnow()
        r = await client.get(url, timeout=3.0)
        ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
        return {"status": "alive" if r.status_code < 500 else "down", "latency_ms": ms}
    except Exception:
        return {"status": "down", "latency_ms": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_dashboard_http_pooling.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Verify it compiles, run full suite, commit**

```bash
cd /home/fruiz/jax-platform/backend
python -m py_compile api/admin/dashboard.py
.venv/bin/pytest -v
```

```bash
cd /home/fruiz/jax-platform
git add backend/api/admin/dashboard.py backend/tests/test_dashboard_http_pooling.py
git commit -m "perf(backend): reuse shared httpx client in api/admin/dashboard.py"
```

---

### Task 5: Migrate `api/image.py` (1 call site, external OpenAI API)

**Files:**
- Modify: `backend/api/image.py:1-70`
- Test: `backend/tests/test_image_http_pooling.py`

**Interfaces:**
- Consumes: `get_http_client` from Task 1.

Since this site calls the real `api.openai.com`, the test injects a fake client directly into the `http_client` module singleton instead of hitting the network, so it stays fast and deterministic.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_image_http_pooling.py
import os

import http_client
from auth.jwt import create_access_token

USER_ID = "test-image-pooling-user"
TENANT_ID = "test-image-pooling-tenant"


def _headers():
    token = create_access_token(USER_ID, TENANT_ID, "operator")
    return {"Authorization": f"Bearer {token}"}


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_generate_image_uses_the_shared_client(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
    fake = _FakeClient(_FakeResponse({
        "data": [{"url": "https://example.com/img.png", "revised_prompt": "a cat"}]
    }))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post(
            "/api/image/generate",
            headers=_headers(),
            json={"prompt": "a cat"},
        )
    finally:
        http_client._client = original

    assert resp.status_code == 200
    assert resp.json() == {"url": "https://example.com/img.png", "revised_prompt": "a cat"}
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://api.openai.com/v1/images/generations"
    assert kwargs["timeout"] == 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_image_http_pooling.py -v`
Expected: FAIL — `generate_image` still opens its own `httpx.AsyncClient()`, ignoring the fake injected into `http_client._client`, so `fake.calls` stays empty and the response body doesn't match (real call attempted against the fake API key, raising inside the existing except-block, yielding a 502).

- [ ] **Step 3: Migrate the site**

In `backend/api/image.py`, change:
```python
import base64
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from auth.middleware import get_current_user
```
to:
```python
import base64
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from auth.middleware import get_current_user
from http_client import get_http_client
```
(`httpx` import stays — `httpx.HTTPStatusError` is still referenced in the `except` clause below.)

Change:
```python
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-image-1",
                    "prompt": req.prompt,
                    "size": "1024x1024",
                    "quality": "medium",
                    "n": 1,
                },
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Image API error {e.response.status_code}: {e.response.text[:200]}",
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error generando imagen: {str(e)[:200]}")
```
to:
```python
    client = await get_http_client()
    try:
        r = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-image-1",
                "prompt": req.prompt,
                "size": "1024x1024",
                "quality": "medium",
                "n": 1,
            },
            timeout=120.0,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Image API error {e.response.status_code}: {e.response.text[:200]}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error generando imagen: {str(e)[:200]}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_image_http_pooling.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Verify it compiles, run full suite, commit**

```bash
cd /home/fruiz/jax-platform/backend
python -m py_compile api/image.py
.venv/bin/pytest -v
```

```bash
cd /home/fruiz/jax-platform
git add backend/api/image.py backend/tests/test_image_http_pooling.py
git commit -m "perf(backend): reuse shared httpx client in api/image.py"
```

---

### Task 6: Migrate `api/chat.py` (3 call sites: Ollama, OpenAI-compatible, Gemini)

**Files:**
- Modify: `backend/api/chat.py:330-374`
- Test: `backend/tests/test_chat_http_pooling.py`

**Interfaces:**
- Consumes: `get_http_client` from Task 1.
- Produces: no change to `_call_ollama(system_prompt, history, message, config, model) -> str`, `_call_openai_compat(base_url, api_key, model, system_prompt, history, message) -> str`, `_call_gemini(api_key, model, system_prompt, history, message) -> str` signatures — callers elsewhere in `chat.py` are unaffected.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_chat_http_pooling.py
import http_client
from api.chat import _call_ollama, _call_openai_compat, _call_gemini


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


async def test_call_ollama_uses_the_shared_client():
    fake = _FakeClient(_FakeResponse({"message": {"content": "hola"}}))
    original = http_client._client
    http_client._client = fake
    try:
        result = await _call_ollama(
            "system prompt", [], "hola",
            {"personalities": {"jax_local": {"api_url": "http://127.0.0.1:11434/api/chat"}}},
            "qwen3-coder:30b",
        )
    finally:
        http_client._client = original

    assert result == "hola"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert kwargs["timeout"] == 180.0
    assert kwargs["json"]["keep_alive"] == -1


async def test_call_openai_compat_uses_the_shared_client():
    fake = _FakeClient(_FakeResponse({"choices": [{"message": {"content": "respuesta"}}]}))
    original = http_client._client
    http_client._client = fake
    try:
        result = await _call_openai_compat(
            "https://api.deepseek.com/v1", "sk-test", "deepseek-v4-flash",
            "system prompt", [], "hola",
        )
    finally:
        http_client._client = original

    assert result == "respuesta"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert kwargs["timeout"] == 120.0


async def test_call_gemini_uses_the_shared_client():
    fake = _FakeClient(_FakeResponse({
        "candidates": [{"content": {"parts": [{"text": "respuesta gemini"}]}}]
    }))
    original = http_client._client
    http_client._client = fake
    try:
        result = await _call_gemini("test-key", "gemini-2.5-flash", "system prompt", [], "hola")
    finally:
        http_client._client = original

    assert result == "respuesta gemini"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url.startswith("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
    assert kwargs["timeout"] == 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_chat_http_pooling.py -v`
Expected: FAIL for all 3 tests — each `_call_*` helper still opens its own `httpx.AsyncClient()` and tries a real network call, ignoring the injected fake.

- [ ] **Step 3: Migrate all 3 sites**

In `backend/api/chat.py`, add the import alongside the existing ones near the top of the file (wherever `import httpx` currently sits):
```python
from http_client import get_http_client
```

Change:
```python
async def _call_ollama(system_prompt: str, history: list[dict], message: str, config: dict, model: str) -> str:
    url = config["personalities"]["jax_local"]["api_url"]
    messages = _build_messages(system_prompt, history, message)
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(url, json={"model": model, "messages": messages, "stream": False, "keep_alive": -1})
        r.raise_for_status()
        return r.json()["message"]["content"]
```
to:
```python
async def _call_ollama(system_prompt: str, history: list[dict], message: str, config: dict, model: str) -> str:
    url = config["personalities"]["jax_local"]["api_url"]
    messages = _build_messages(system_prompt, history, message)
    client = await get_http_client()
    r = await client.post(
        url,
        json={"model": model, "messages": messages, "stream": False, "keep_alive": -1},
        timeout=180.0,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]
```

Change:
```python
async def _call_openai_compat(
    base_url: str, api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
) -> str:
    messages = _build_messages(system_prompt, history, message)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
```
to:
```python
async def _call_openai_compat(
    base_url: str, api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
) -> str:
    messages = _build_messages(system_prompt, history, message)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    client = await get_http_client()
    r = await client.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages},
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
```

Change:
```python
async def _call_gemini(
    api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [{"googleSearch": {}}],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
```
to:
```python
async def _call_gemini(
    api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [{"googleSearch": {}}],
    }
    client = await get_http_client()
    r = await client.post(url, json=body, timeout=120.0)
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_chat_http_pooling.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Verify it compiles, run full suite, commit**

```bash
cd /home/fruiz/jax-platform/backend
python -m py_compile api/chat.py
.venv/bin/pytest -v
```
Expected: all tests pass, including `test_facet_model_wiring.py` which also touches `chat.py`.

```bash
cd /home/fruiz/jax-platform
git add backend/api/chat.py backend/tests/test_chat_http_pooling.py
git commit -m "perf(backend): reuse shared httpx client in api/chat.py"
```

---

### Task 7: Migrate `api/admin/keys.py` (2 call sites)

**Files:**
- Modify: `backend/api/admin/keys.py:147-179`
- Test: `backend/tests/test_keys_http_pooling.py`

**Interfaces:**
- Consumes: `get_http_client` from Task 1.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_keys_http_pooling.py
import api.admin.keys as keys_module
import http_client
from auth.jwt import create_access_token

USER_ID = "test-keys-pooling-user"
TENANT_ID = "test-keys-pooling-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_test_key_generic_branch_uses_the_shared_client(client, monkeypatch):
    """Exercises the generic (non-Gemini) branch of POST /keys/{id}/test,
    which reads test_url from PROVIDERS and hits it with a Bearer header."""
    async def fake_get_db_key(pool, user_id, provider_id):
        return "sk-fake-key"

    monkeypatch.setattr(keys_module, "_get_db_key", fake_get_db_key)

    fake = _FakeClient(_FakeResponse(status_code=200))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post("/api/admin/keys/openai/test", headers=_superadmin_headers())
    finally:
        http_client._client = original

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://api.openai.com/v1/models"
    assert kwargs["headers"] == {"Authorization": "Bearer sk-fake-key"}
    assert kwargs["timeout"] == 10.0


def test_test_key_gemini_branch_uses_the_shared_client(client, monkeypatch):
    async def fake_get_db_key(pool, user_id, provider_id):
        return "gk-fake-key"

    monkeypatch.setattr(keys_module, "_get_db_key", fake_get_db_key)

    fake = _FakeClient(_FakeResponse(status_code=200))
    original = http_client._client
    http_client._client = fake
    try:
        resp = client.post("/api/admin/keys/gemini/test", headers=_superadmin_headers())
    finally:
        http_client._client = original

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models?key=gk-fake-key"
    assert kwargs["timeout"] == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_keys_http_pooling.py -v`
Expected: FAIL — both branches still open their own `httpx.AsyncClient()`, ignoring the injected fake, so `fake.calls` stays empty and the real network call raises inside the except-block (returning `ok: False`).

- [ ] **Step 3: Migrate both sites**

In `backend/api/admin/keys.py`, change:
```python
import os
import time
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser
from crypto_secrets import encrypt_secret, decrypt_secret, decrypt_db_secret
from db.connection import get_pool
```
to:
```python
import os
import time
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser
from crypto_secrets import encrypt_secret, decrypt_secret, decrypt_db_secret
from db.connection import get_pool
from http_client import get_http_client
```
(`httpx` import removed — after this change nothing in the file references the `httpx` module directly.)

Change:
```python
    if not prov["test_url"]:
        if provider_id == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            try:
                t0 = time.time()
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(url)
                ms = int((time.time() - t0) * 1000)
                return {"ok": r.status_code == 200, "latency_ms": ms, "error": None if r.status_code == 200 else r.text[:100]}
            except Exception as e:
                return {"ok": False, "latency_ms": None, "error": str(e)[:100]}
        return {"ok": False, "latency_ms": None, "error": "Test no disponible para este provider"}

    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(prov["test_url"], headers={"Authorization": f"Bearer {api_key}"})
        ms = int((time.time() - t0) * 1000)
        return {"ok": r.status_code < 400, "latency_ms": ms, "error": None if r.status_code < 400 else r.text[:100]}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "error": str(e)[:100]}
```
to:
```python
    if not prov["test_url"]:
        if provider_id == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            try:
                t0 = time.time()
                client = await get_http_client()
                r = await client.get(url, timeout=10.0)
                ms = int((time.time() - t0) * 1000)
                return {"ok": r.status_code == 200, "latency_ms": ms, "error": None if r.status_code == 200 else r.text[:100]}
            except Exception as e:
                return {"ok": False, "latency_ms": None, "error": str(e)[:100]}
        return {"ok": False, "latency_ms": None, "error": "Test no disponible para este provider"}

    try:
        t0 = time.time()
        client = await get_http_client()
        r = await client.get(prov["test_url"], headers={"Authorization": f"Bearer {api_key}"}, timeout=10.0)
        ms = int((time.time() - t0) * 1000)
        return {"ok": r.status_code < 400, "latency_ms": ms, "error": None if r.status_code < 400 else r.text[:100]}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "error": str(e)[:100]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_keys_http_pooling.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify it compiles, run full suite, commit**

```bash
cd /home/fruiz/jax-platform/backend
python -m py_compile api/admin/keys.py
.venv/bin/pytest -v
```
Expected: all tests pass, including `test_admin_keys_n1.py`.

```bash
cd /home/fruiz/jax-platform
git add backend/api/admin/keys.py backend/tests/test_keys_http_pooling.py
git commit -m "perf(backend): reuse shared httpx client in api/admin/keys.py"
```

---

### Task 8: Fold `jax_engine/state.py`'s two background pollers into the shared client

**Files:**
- Modify: `backend/jax_engine/state.py:173-187`
- Test: `backend/tests/test_state_http_pooling.py`

**Interfaces:**
- Consumes: `get_http_client` from Task 1.
- Produces: `_check_las_manos_health(self, client)` and `_poll_one_pipeline(self, client, pid, pipeline)` keep their existing signatures unchanged (both already accept `client` as a parameter — this is exactly why `test_las_manos_health_broadcast.py`'s `_FakeClient` injection pattern keeps working unmodified).

These two sites are background-task loops that already hold one `httpx.AsyncClient` open for their entire lifetime (not one per iteration) — this migration removes the *second*, duplicate connection pool they maintain alongside the one from Task 1-2, folding everything into a single pool for the whole process.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_state_http_pooling.py
import asyncio

import http_client
from jax_engine.state import JAXEngineState


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _FakeClient:
    def __init__(self):
        self.get_calls = []

    async def get(self, url, *args, **kwargs):
        self.get_calls.append(url)
        return _FakeResponse(200)


async def test_poll_las_manos_uses_the_shared_client():
    fake = _FakeClient()
    original = http_client._client
    http_client._client = fake
    state = JAXEngineState()
    try:
        task = asyncio.get_event_loop().create_task(state._poll_las_manos())
        await asyncio.sleep(0)  # let the first loop iteration run
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        http_client._client = original

    assert len(fake.get_calls) >= 1
    assert fake.get_calls[0].endswith("/health")


async def test_poll_pipelines_uses_the_shared_client():
    fake = _FakeClient()
    original = http_client._client
    http_client._client = fake
    state = JAXEngineState()
    from jax_engine.schemas import PipelineState
    state._state.active_pipelines["pid-1"] = PipelineState(
        pipeline_id="pid-1", tenant_id="t1", user_id="u1", name="p", status="running",
    )
    try:
        task = asyncio.get_event_loop().create_task(state._poll_pipelines())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        http_client._client = original

    assert any("/jacobs/pipeline/pid-1" in url for url in fake.get_calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_state_http_pooling.py -v`
Expected: FAIL — both pollers still open their own dedicated `httpx.AsyncClient()` inside an `async with`, never touching the fake injected into `http_client._client`, so `fake.get_calls` stays empty.

- [ ] **Step 3: Migrate both sites**

In `backend/jax_engine/state.py`, add the import alongside the existing ones:
```python
import asyncio
import json
import os
from datetime import datetime
import httpx
from .schemas import (
    EcosystemState, FacetState, PipelineState, PipelineStep, UserSession, JAXEvent
)
from .events import event_bus
```
to:
```python
import asyncio
import json
import os
from datetime import datetime
import httpx
from http_client import get_http_client
from .schemas import (
    EcosystemState, FacetState, PipelineState, PipelineStep, UserSession, JAXEvent
)
from .events import event_bus
```
(`httpx` import stays — it's still used for the `client: httpx.AsyncClient` type hints on `_check_las_manos_health` and `_poll_one_pipeline`.)

Change:
```python
    async def _poll_las_manos(self):
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                await self._check_las_manos_health(client)
                await asyncio.sleep(30)

    async def _poll_pipelines(self):
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                for pid, pipeline in list(self._state.active_pipelines.items()):
                    if pipeline.status not in ("running", "waiting_gate"):
                        continue
                    await self._poll_one_pipeline(client, pid, pipeline)

                await asyncio.sleep(5)
```
to:
```python
    async def _poll_las_manos(self):
        client = await get_http_client()
        while True:
            await self._check_las_manos_health(client)
            await asyncio.sleep(30)

    async def _poll_pipelines(self):
        client = await get_http_client()
        while True:
            for pid, pipeline in list(self._state.active_pipelines.items()):
                if pipeline.status not in ("running", "waiting_gate"):
                    continue
                await self._poll_one_pipeline(client, pid, pipeline)

            await asyncio.sleep(5)
```

`_check_las_manos_health` calls `client.get(f"{LAS_MANOS_URL}/health")` with no per-call timeout today (it relied on the client-level `timeout=5.0`); since the shared client has no default, add it explicitly:
```python
    async def _check_las_manos_health(self, client: httpx.AsyncClient):
        try:
            r = await client.get(f"{LAS_MANOS_URL}/health")
            alive = r.status_code == 200
        except Exception:
            alive = False
```
to:
```python
    async def _check_las_manos_health(self, client: httpx.AsyncClient):
        try:
            r = await client.get(f"{LAS_MANOS_URL}/health", timeout=5.0)
            alive = r.status_code == 200
        except Exception:
            alive = False
```

Same for `_poll_one_pipeline`:
```python
    async def _poll_one_pipeline(self, client: httpx.AsyncClient, pid: str, pipeline: PipelineState):
        try:
            r = await client.get(f"{LAS_MANOS_URL}/jacobs/pipeline/{pid}")
```
to:
```python
    async def _poll_one_pipeline(self, client: httpx.AsyncClient, pid: str, pipeline: PipelineState):
        try:
            r = await client.get(f"{LAS_MANOS_URL}/jacobs/pipeline/{pid}", timeout=5.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest tests/test_state_http_pooling.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Verify it compiles, run full suite including the pre-existing health-broadcast test, commit**

```bash
cd /home/fruiz/jax-platform/backend
python -m py_compile jax_engine/state.py
.venv/bin/pytest -v
```
Expected: all tests pass — in particular `test_las_manos_health_broadcast.py`, which calls `_check_las_manos_health(client)` directly with its own `_FakeClient` and must keep passing unmodified, since this task didn't change that method's signature.

```bash
cd /home/fruiz/jax-platform
git add backend/jax_engine/state.py backend/tests/test_state_http_pooling.py
git commit -m "perf(backend): reuse shared httpx client in jax_engine/state.py background pollers"
```

---

### Task 9: Full-suite regression pass and final verification

**Files:** none (verification-only task).

- [ ] **Step 1: Confirm zero remaining raw `httpx.AsyncClient(` instantiations outside `http_client.py`**

Run:
```bash
cd /home/fruiz/jax-platform/backend
grep -rn "httpx.AsyncClient(" --include="*.py" . | grep -v "^./http_client.py" | grep -v /tests/ | grep -v .venv
```
Expected: no output (every remaining call site was migrated).

- [ ] **Step 2: Run the full backend test suite one more time**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/pytest -v`
Expected: all tests pass, zero failures, zero errors.

- [ ] **Step 3: Commit if anything is outstanding (should be a no-op)**

```bash
cd /home/fruiz/jax-platform
git status
```
Expected: clean tree — every prior task already committed its own changes.
