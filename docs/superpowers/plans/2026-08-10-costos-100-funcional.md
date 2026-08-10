# Costos 100% Funcional — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `axioma_usage` tenga tokens y costos reales para todas las facetas invocables (Mesa web + Kimi vía Jacobs), en vez de ceros hardcodeados.

**Architecture:** Parte A — dejar de descartar los campos `usage`/`prompt_eval_count` que cada transporte de `chat.py` ya recibe, y calcular costo contra la tabla `model` real en vez de un dict hardcodeado. Parte B — Kimi solo es invocable vía Jacobs (`las_manos`), que hoy no lleva identidad real de usuario; se corrige en origen (JWT autenticado) y se propaga hasta que `motor_registry/worker.py` pueda escribir `axioma_usage` directamente (misma DB `jax_memory`, conexión propia, mismo patrón que `credential_resolver.py`).

**Tech Stack:** FastAPI + aiomysql (ambos repos), pytest (jax-platform), unittest.IsolatedAsyncioTestCase stdlib (las_manos, sin pytest instalado).

## Global Constraints

- Spec de referencia: `docs/superpowers/specs/2026-08-10-costos-100-funcional-design.md` — cualquier duda de alcance/decisión se resuelve ahí, no se reinterpreta acá.
- Hyde queda sin tracking de costos — no tocar `chat.py`'s early-return de hyde.
- Costo `NULL`/fail-soft si el modelo no está en el catálogo `model` — nunca inventar un número.
- TDD en cada task: test rojo, confirmar que falla por la razón correcta, implementar, confirmar verde, commit.
- jax-platform: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/ -q` para la suite completa.
- las_manos: sin pytest — cada test nuevo es un archivo `_<nombre>_test.py` ejecutable directo con `PYTHONPATH=/home/fruiz/jax/las_manos .venv/bin/python _<nombre>_test.py` (mismo patrón que `_worker_max_tokens_test.py`, ya en el repo).
- Nunca commitear sin `py_compile` limpio en los archivos tocados.
- Restart de servicio (`jax-platform`/`jax-las-manos`) solo con confirmación explícita antes de cada uno — mismo criterio de todo el día.

---

## Parte A — Transportes directos de la Mesa (jax-platform)

### Task 1: `record_usage()` calcula costo contra la tabla `model`, no `MODEL_PRICES`

**Files:**
- Modify: `backend/api/admin/usage.py`
- Test: `backend/tests/test_usage_pricing.py` (nuevo)

**Interfaces:**
- Produces: `record_usage(user_id: str, tenant_id: str, facet: str, provider_id: str, model: str, tokens_in: int, tokens_out: int, request_type: str = "chat", cost_usd_override: float | None = None) -> None`

- [ ] **Step 1: Escribir el test que falla**

```python
# backend/tests/test_usage_pricing.py
"""Costo real desde `model` (Bloque D), no desde un dict hardcodeado.
Mismo patron de client.portal.call que test_model_catalog_sync.py."""
from api.admin.usage import record_usage


async def _fetch_last_usage_row():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tokens_in, tokens_out, cost_usd, model, facet "
                "FROM axioma_usage ORDER BY id DESC LIMIT 1"
            )
            return await cur.fetchone()


async def _seed_priced_model(provider_id, model_id, price_in, price_out):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, "
                "price_input_per_1m_usd, price_output_per_1m_usd) "
                "VALUES (%s, %s, 'available', 'manual', NOW(), %s, %s) "
                "ON DUPLICATE KEY UPDATE price_input_per_1m_usd=%s, price_output_per_1m_usd=%s",
                (provider_id, model_id, price_in, price_out, price_in, price_out),
            )
        await conn.commit()


def test_record_usage_calcula_costo_desde_tabla_model(client):
    client.portal.call(_seed_priced_model, "deepseek", "deepseek-v4-flash", 0.14, 0.28)
    client.portal.call(
        record_usage,
        "1", "test-tenant", "jekyll", "deepseek", "deepseek-v4-flash", 1000, 500, "chat", None,
    )
    row = client.portal.call(_fetch_last_usage_row)
    tokens_in, tokens_out, cost_usd, model, facet = row
    assert tokens_in == 1000
    assert tokens_out == 500
    expected = (1000 * 0.14 + 500 * 0.28) / 1_000_000
    assert abs(float(cost_usd) - expected) < 1e-9


def test_record_usage_modelo_sin_catalogo_da_costo_null(client):
    client.portal.call(
        record_usage,
        "1", "test-tenant", "jekyll", "deepseek", "modelo-que-no-existe-nunca", 100, 50, "chat", None,
    )
    row = client.portal.call(_fetch_last_usage_row)
    _tokens_in, _tokens_out, cost_usd, _model, _facet = row
    assert cost_usd is None


def test_record_usage_cost_usd_override_ignora_tabla_model(client):
    client.portal.call(
        record_usage,
        "1", "test-tenant", "thot_image", "openai", "gpt-image-1", 0, 0, "imagen", 0.04,
    )
    row = client.portal.call(_fetch_last_usage_row)
    _tokens_in, _tokens_out, cost_usd, _model, _facet = row
    assert abs(float(cost_usd) - 0.04) < 1e-9
```

- [ ] **Step 2: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_usage_pricing.py -v`
Expected: FAIL — `TypeError: record_usage() takes ... positional arguments` (firma vieja no acepta `provider_id`/`cost_usd_override`).

- [ ] **Step 3: Reescribir `record_usage`**

```python
# backend/api/admin/usage.py — reemplaza record_usage() completo, borra MODEL_PRICES
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")


async def _lookup_model_price(provider_id: str, model: str) -> tuple[float | None, float | None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT price_input_per_1m_usd, price_output_per_1m_usd "
                "FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, model),
            )
            row = await cur.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


async def record_usage(
    user_id: str,
    tenant_id: str,
    facet: str,
    provider_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    request_type: str = "chat",
    cost_usd_override: float | None = None,
):
    """Llamar desde chat.py e image.py para registrar uso. Costo real desde
    `model` (Bloque D) — nunca un dict hardcodeado. Si el modelo no esta en
    el catalogo (nunca corrio un sync), cost_usd queda NULL con el motivo
    visible en el propio dato (nunca un numero inventado). cost_usd_override
    es para pricing plano-por-request que no encaja en precio-por-token
    (ej. generacion de imagenes)."""
    if cost_usd_override is not None:
        cost = cost_usd_override
    else:
        price_in, price_out = await _lookup_model_price(provider_id, model)
        if price_in is None or price_out is None:
            cost = None
        else:
            cost = (tokens_in * float(price_in) + tokens_out * float(price_out)) / 1_000_000

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost, request_type),
                )
            await conn.commit()
    except Exception:
        pass  # usage tracking is best-effort


@router.get("/usage")
async def get_usage(
    period: str = Query("day", pattern="^(day|week|month)$"),
    user: AuthUser = Depends(require_superadmin),
):
    days = {"day": 1, "week": 7, "month": 30}[period]
    since = date.today() - timedelta(days=days)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT facet, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*), request_type
                FROM axioma_usage
                WHERE DATE(created_at) >= %s
                GROUP BY facet, model, request_type
                ORDER BY cost_usd DESC
                """,
                (since.isoformat(),),
            )
            rows = await cur.fetchall()

            labels = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
            await cur.execute(
                """
                SELECT facet, DATE(created_at) as day, COUNT(*) as cnt
                FROM axioma_usage
                WHERE DATE(created_at) >= %s
                GROUP BY facet, day
                """,
                ((date.today() - timedelta(days=7)).isoformat(),),
            )
            chart_rows = await cur.fetchall()

    by_facet = [
        {
            "facet": r[0],
            "model": r[1],
            "tokens_in": int(r[2] or 0),
            "tokens_out": int(r[3] or 0),
            "cost_usd": float(r[4]) if r[4] is not None else None,
            "requests": int(r[5] or 0),
            "request_type": r[6],
        }
        for r in rows
    ]

    chart_map: dict = {}
    for facet, day, cnt in chart_rows:
        if facet not in chart_map:
            chart_map[facet] = {l: 0 for l in labels}
        if day.isoformat() in chart_map[facet]:
            chart_map[facet][day.isoformat()] = int(cnt)

    datasets = {f: [chart_map.get(f, {}).get(l, 0) for l in labels] for f in chart_map}

    return {
        "by_facet": by_facet,
        "chart_data": {"labels": labels, "datasets": datasets},
        "period": period,
    }
```

- [ ] **Step 4: Confirmar verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_usage_pricing.py -v`
Expected: 3 passed.

- [ ] **Step 5: `py_compile` + commit**

```bash
cd /home/fruiz/jax-platform/backend && .venv/bin/python -m py_compile api/admin/usage.py
cd /home/fruiz/jax-platform && git add backend/api/admin/usage.py backend/tests/test_usage_pricing.py
git commit -m "fix(usage): record_usage calcula costo desde la tabla model, no un dict hardcodeado"
```

---

### Task 2: `chat.py` captura tokens reales por transporte

**Files:**
- Modify: `backend/api/chat.py`
- Test: `backend/tests/test_chat_usage_capture.py` (nuevo)

**Interfaces:**
- Consumes: `record_usage(...)` de Task 1 (firma nueva con `provider_id`).
- Produces: `_invoke_facet(...) -> tuple[str, UsageInfo | None]` (antes devolvía solo `str` — TODOS los call-sites de `_invoke_facet` deben actualizarse en este mismo task).
- Produces: `UsageInfo` — `NamedTuple` con `provider_id: str`, `model: str`, `tokens_in: int`, `tokens_out: int`.

- [ ] **Step 1: Escribir el test que falla**

```python
# backend/tests/test_chat_usage_capture.py
"""Cada transporte de chat.py ya recibe la respuesta completa de la API —
el fix es dejar de descartar los campos de tokens que ya estan ahi, no
agregar requests nuevos. Shapes reales verificadas con evidencia el
2026-08-10 (curl real contra las 3 APIs)."""
import http_client
from api.chat import _call_openai_compat, _call_gemini, _call_ollama


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        pass


class _FakePostClient:
    def __init__(self, response):
        self._response = response

    async def post(self, url, **kwargs):
        return self._response


def test_call_openai_compat_devuelve_tokens_reales(client):
    fake = _FakePostClient(_FakeResponse({
        "choices": [{"message": {"content": "hola"}}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 17},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        text, tokens_in, tokens_out = client.portal.call(
            _call_openai_compat, "https://api.example.com/v1", "sk-fake", "modelo-x",
            "system", [], "hola", None,
        )
    finally:
        http_client._client = original
    assert text == "hola"
    assert tokens_in == 42
    assert tokens_out == 17


def test_call_gemini_devuelve_tokens_reales(client):
    fake = _FakePostClient(_FakeResponse({
        "candidates": [{"content": {"parts": [{"text": "hola"}]}}],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8},
    }))
    original = http_client._client
    http_client._client = fake
    try:
        text, tokens_in, tokens_out = client.portal.call(
            _call_gemini, "fake-key", "gemini-2.5-flash", "system", [], "hola", None,
        )
    finally:
        http_client._client = original
    assert text == "hola"
    assert tokens_in == 20
    assert tokens_out == 8


def test_call_ollama_devuelve_tokens_reales(client):
    fake = _FakePostClient(_FakeResponse({
        "message": {"content": "hola"},
        "prompt_eval_count": 31,
        "eval_count": 36,
    }))
    original = http_client._client
    http_client._client = fake
    try:
        text, tokens_in, tokens_out = client.portal.call(
            _call_ollama, "system", [], "hola",
            {"personalities": {"jax_local": {"api_url": "http://localhost:11434/api/chat"}}},
            "qwen3-coder:30b",
        )
    finally:
        http_client._client = original
    assert text == "hola"
    assert tokens_in == 31
    assert tokens_out == 36
```

- [ ] **Step 2: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_chat_usage_capture.py -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got ...)` (las 3 funciones hoy devuelven solo `str`).

- [ ] **Step 3: Implementar — cambiar las 3 funciones de transporte + `_invoke_facet` + `chat()`**

```python
# backend/api/chat.py — reemplazos puntuales, mismo archivo

# Agregar cerca de los imports existentes:
from typing import NamedTuple


class UsageInfo(NamedTuple):
    provider_id: str
    model: str
    tokens_in: int
    tokens_out: int


# Reemplaza _call_ollama completo:
async def _call_ollama(system_prompt: str, history: list[dict], message: str, config: dict, model: str) -> tuple[str, int, int]:
    url = config["personalities"]["jax_local"]["api_url"]
    messages = _build_messages(system_prompt, history, message)
    client = await get_http_client()
    r = await client.post(
        url,
        json={"model": model, "messages": messages, "stream": False, "keep_alive": -1},
        timeout=180.0,
    )
    r.raise_for_status()
    data = r.json()
    return data["message"]["content"], data.get("prompt_eval_count", 0), data.get("eval_count", 0)


# Reemplaza _call_openai_compat completo:
async def _call_openai_compat(
    base_url: str, api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
    on_response=None,
) -> tuple[str, int, int]:
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
    data = r.json()
    if on_response:
        await on_response(data)
    usage = data.get("usage") or {}
    return data["choices"][0]["message"]["content"], usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


# Reemplaza _call_gemini completo:
async def _call_gemini(
    api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
    on_response=None,
) -> tuple[str, int, int]:
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
    if on_response:
        await on_response(data)
    usage = data.get("usageMetadata") or {}
    return (
        data["candidates"][0]["content"]["parts"][0]["text"],
        usage.get("promptTokenCount", 0),
        usage.get("candidatesTokenCount", 0),
    )


# Dentro de _invoke_facet: reemplaza el bloque final (desde "if _is_model_identity_question"
# hasta el final de la funcion) por:
    if _is_model_identity_question(message):
        return _model_identity_reply(f.model, facet), None

    if f.transport == "ollama":
        if facet == "jax_local":
            ident = (
                f"\n\nDato tecnico (para tu propia referencia, no lo repitas sin que "
                f"te pregunten): el modelo que te ejecuta en este momento es "
                f"'{f.model}', via Ollama local en hall9000."
            )
            text, tin, tout = await _call_ollama(system_prompt + ident, history, message, config, f.model)
        else:
            text, tin, tout = await _call_ollama(system_prompt, history, message, config, f.model)
        return text, UsageInfo(f.provider_id, f.model, tin, tout)

    async def _on_response(data: dict) -> None:
        await _record_resolved_version_from_response(facet, data)

    if f.transport == "http_gemini":
        text, tin, tout = await _call_gemini(f.credential, f.model, system_prompt, history, message, on_response=_on_response)
        return text, UsageInfo(f.provider_id, f.model, tin, tout)

    if f.transport == "http_openai_compat":
        text, tin, tout = await _call_openai_compat(f.base_url, f.credential, f.model, system_prompt, history, message, on_response=_on_response)
        return text, UsageInfo(f.provider_id, f.model, tin, tout)

    return f"⚠️ {facet} no está disponible: transporte '{f.transport}' no soportado en la Mesa web.", None


# En chat(): reemplaza
#   response_text = await _invoke_facet(facet, config, user_id, req.message, semantic_context)
# por:
    response_text, usage = await _invoke_facet(facet, config, user_id, req.message, semantic_context)

# Reemplaza el bloque "Registrar uso" completo:
    if usage is not None:
        await record_usage(user_id, tenant_id, facet, usage.provider_id, usage.model, usage.tokens_in, usage.tokens_out, "chat")
```

También hay que ajustar el `except` block: hoy `_invoke_facet` puede lanzar excepciones que el `try/except` de `chat()` ya maneja — el cambio de tipo de retorno no afecta eso, las excepciones siguen propagándose igual.

- [ ] **Step 4: Confirmar verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_chat_usage_capture.py -v && .venv/bin/python -m pytest tests/ -q`
Expected: 3 passed en el archivo nuevo, suite completa sin regresiones (buscar específicamente tests que llamen `_invoke_facet`/`_call_openai_compat`/`_call_gemini`/`_call_ollama` directamente — si alguno asume el retorno viejo tipo `str`, actualizarlo a desempaquetar la tupla).

- [ ] **Step 5: `py_compile` + commit**

```bash
cd /home/fruiz/jax-platform/backend && .venv/bin/python -m py_compile api/chat.py
cd /home/fruiz/jax-platform && git add backend/api/chat.py backend/tests/test_chat_usage_capture.py
git commit -m "fix(chat): capturar tokens reales por transporte en vez de hardcodear 0,0"
```

---

### Task 3: `image.py` registra costo de generación de imágenes

**Files:**
- Modify: `backend/api/image.py`
- Test: `backend/tests/test_image_usage.py` (nuevo — revisar primero si ya existe un test file de image.py para no duplicar fixtures; si existe, agregar el test ahí en vez de crear uno nuevo)

**Interfaces:**
- Consumes: `record_usage(...)` de Task 1, con `cost_usd_override=0.04` para `gpt-image-1`.

- [ ] **Step 1: Escribir el test que falla**

```python
# backend/tests/test_image_usage.py
"""image.py nunca llamaba record_usage — costo de imagenes sin trackear.
gpt-image-1 es costo plano por imagen, no por token: cost_usd_override."""
import http_client
from auth.jwt import create_access_token


class _FakeImageResponse:
    def json(self):
        return {"data": [{"b64_json": "ZmFrZQ==", "revised_prompt": "un gato"}]}

    def raise_for_status(self):
        pass


class _FakeImagePostClient:
    async def post(self, url, **kwargs):
        return _FakeImageResponse()


async def _fetch_last_usage_row():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT facet, model, cost_usd, request_type FROM axioma_usage "
                "ORDER BY id DESC LIMIT 1"
            )
            return await cur.fetchone()


def test_generate_image_registra_uso_con_costo_plano(client, monkeypatch):
    import credential_resolver

    async def fake_credential(provider_id):
        return "sk-fake"

    monkeypatch.setattr(credential_resolver, "resolve_credential_instrumented", fake_credential)
    original = http_client._client
    http_client._client = _FakeImagePostClient()
    try:
        token = create_access_token("1", "test-tenant", "user")
        resp = client.post("/api/image/generate", json={"prompt": "un gato"}, headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original

    assert resp.status_code == 200
    row = client.portal.call(_fetch_last_usage_row)
    facet, model, cost_usd, request_type = row
    assert model == "gpt-image-1"
    assert request_type == "imagen"
    assert abs(float(cost_usd) - 0.04) < 1e-9
```

- [ ] **Step 2: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_image_usage.py -v`
Expected: FAIL — la última fila de `axioma_usage` no corresponde a esta request (o la tabla queda sin cambios), porque `record_usage` nunca se llama desde `image.py`.

- [ ] **Step 3: Implementar**

Agregar a `backend/api/image.py`, después de construir `url`/`revised_prompt` (justo antes del `return ImageResponse(...)`):

```python
from api.admin.usage import record_usage

# ... dentro de generate_image, antes del return final:
    await record_usage(
        user.user_id, user.tenant_id, "thot_image", "openai", "gpt-image-1",
        0, 0, "imagen", cost_usd_override=0.04,
    )
```

- [ ] **Step 4: Confirmar verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_image_usage.py -v`
Expected: 1 passed.

- [ ] **Step 5: `py_compile` + commit**

```bash
cd /home/fruiz/jax-platform/backend && .venv/bin/python -m py_compile api/image.py
cd /home/fruiz/jax-platform && git add backend/api/image.py backend/tests/test_image_usage.py
git commit -m "fix(image): registrar costo de generacion de imagenes en axioma_usage"
```

---

## Parte B — Kimi vía Jacobs (jax + jax-platform)

### Task 4: Identidad real en `jax-platform/api/pipelines.py`

**Files:**
- Modify: `backend/api/pipelines.py`
- Test: `backend/tests/test_pipelines_identity_injection.py` (nuevo)

**Interfaces:**
- Produces: el `body`/payload que `create_pipeline`/`resume_pipeline` reenvían a Jacobs siempre lleva `user_id`/`tenant_id` reales del `AuthUser` autenticado, sobrescribiendo cualquier valor que mande el cliente.

- [ ] **Step 1: Escribir el test que falla**

```python
# backend/tests/test_pipelines_identity_injection.py
"""api/pipelines.py reenviaba el body del cliente sin inyectar identidad
real -- create_pipeline dependia de lo que mandara el front (hoy "Fernando"
fijo), resume_pipeline lo hardcodeaba en Python directamente. Ninguno de
los dos debe confiar en identidad que venga del cliente para algo que se
usa para atribuir costo."""
from auth.jwt import create_access_token

USER_ID = "1"
TENANT_ID = "test-pipelines-identity-tenant"


def _auth_headers():
    token = create_access_token(USER_ID, TENANT_ID, "user")
    return {"Authorization": f"Bearer {token}"}


def test_create_pipeline_inyecta_identidad_real(client, monkeypatch):
    captured = {}

    class _FakeClient:
        async def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            class _R:
                status_code = 200
                def json(self):
                    return {"pipeline_id": None}
            return _R()
        async def get(self, url, timeout=None):
            class _R:
                def json(self):
                    return {}
            return _R()

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", lambda: _FakeClient())

    client.post(
        "/api/pipelines",
        json={"name": "test", "objective": "x", "invoked_by": "cliente-mintiendo", "mode": "supervised"},
        headers=_auth_headers(),
    )

    assert captured["json"]["user_id"] == USER_ID
    assert captured["json"]["tenant_id"] == TENANT_ID


def test_resume_pipeline_inyecta_identidad_real(client, monkeypatch):
    captured = {}

    class _FakeClient:
        async def post(self, url, json=None, timeout=None):
            captured["json"] = json
            class _R:
                def json(self):
                    return {"ok": True}
            return _R()

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", lambda: _FakeClient())
    monkeypatch.setattr(pipelines_module, "_require_pipeline_owner", lambda pid, user: None)

    client.post(
        "/api/pipelines/00000000-0000-0000-0000-000000000000/resume",
        headers=_auth_headers(),
    )

    assert captured["json"]["user_id"] == USER_ID
    assert captured["json"]["tenant_id"] == TENANT_ID
    assert "Fernando" not in str(captured["json"])
```

- [ ] **Step 2: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_pipelines_identity_injection.py -v`
Expected: FAIL — `KeyError: 'user_id'` (el body reenviado hoy no tiene esas claves).

- [ ] **Step 3: Implementar**

En `backend/api/pipelines.py`, dentro de `create_pipeline`, reemplazar:
```python
    body = await request.json()
```
por:
```python
    body = await request.json()
    body["user_id"] = user.user_id
    body["tenant_id"] = user.tenant_id
```

Y en `resume_pipeline`, reemplazar:
```python
            json={"invoked_by": "Fernando"},
```
por:
```python
            json={"invoked_by": "Fernando", "user_id": user.user_id, "tenant_id": user.tenant_id},
```

- [ ] **Step 4: Confirmar verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_pipelines_identity_injection.py -v`
Expected: 2 passed.

- [ ] **Step 5: `py_compile` + commit**

```bash
cd /home/fruiz/jax-platform/backend && .venv/bin/python -m py_compile api/pipelines.py
cd /home/fruiz/jax-platform && git add backend/api/pipelines.py backend/tests/test_pipelines_identity_injection.py
git commit -m "fix(pipelines): inyectar user_id/tenant_id real del JWT, nunca confiar en el del cliente"
```

---

### Task 5: `jax/jacobs` — `Pipeline`/`PipelineCreateRequest` cargan identidad real

**Files:**
- Modify: `jacobs/models.py`, `jacobs/store.py`, `jacobs/routes.py`
- Test: `jacobs/_pipeline_identity_test.py` (nuevo — unittest stdlib, mismo patrón que `_worker_max_tokens_test.py`)

**Interfaces:**
- Produces: `Pipeline.user_id: str | None`, `Pipeline.tenant_id: str | None`; `PipelineCreateRequest.user_id: str | None`, `.tenant_id: str | None`; `store.pipeline_create`/`store.pipeline_get` (o el nombre real de la función de lectura — confirmar en `store.py` antes de tocar) persisten y devuelven ambos campos.

- [ ] **Step 1: Escribir el test que falla**

Primero confirmar el nombre real de la función que lee una fila de `jacobs_pipelines` y la reconstruye en un objeto `Pipeline` (se vio `invoked_by=row["invoked_by"]` cerca de la línea 192 de `store.py` — leer esa función completa antes de escribir el test, para usar su nombre real).

```python
#!/usr/bin/env python3
# jax/jacobs/_pipeline_identity_test.py
"""Pipeline.user_id/tenant_id: identidad real para atribuir costo de Kimi
(via motor_registry) — antes jacobs_pipelines solo tenia invoked_by (label
humano, no una FK a un usuario real). Corre con:
  PYTHONPATH=/home/fruiz/jax .venv/bin/python jacobs/_pipeline_identity_test.py
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
import uuid

os.environ.setdefault("JAX_DB_NAME", "jax_memory_test")

from jacobs import store
from jacobs.models import Pipeline, PipelineStatus


class PipelineIdentityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await store.init_tables()

    async def test_pipeline_create_y_lectura_conservan_user_id_tenant_id(self):
        pid = str(uuid.uuid4())
        p = Pipeline(
            pipeline_id=pid, name="test", invoked_by="Fernando", mode="supervised",
            status=PipelineStatus.pending, user_id="1", tenant_id="test-tenant",
            created_at=time.time(), updated_at=time.time(),
        )
        await store.pipeline_create(p)
        loaded = await store.pipeline_get(pid)  # confirmar nombre real antes de implementar
        self.assertEqual(loaded.user_id, "1")
        self.assertEqual(loaded.tenant_id, "test-tenant")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax && PYTHONPATH=/home/fruiz/jax .venv/bin/python jacobs/_pipeline_identity_test.py`
Expected: FAIL — `TypeError: Pipeline() got an unexpected keyword argument 'user_id'`.

- [ ] **Step 3: Implementar**

`jacobs/models.py` — agregar a `Pipeline` (después de `invoked_by`):
```python
    user_id:            str | None = None
    tenant_id:           str | None = None
```
Y a `PipelineCreateRequest` (después de `invoked_by`):
```python
    user_id:            str | None = None
    tenant_id:           str | None = None
```

`jacobs/store.py`:
- En `init_tables()`, después del `CREATE TABLE IF NOT EXISTS jacobs_pipelines (...)`, agregar chequeo idempotente de columnas nuevas (mismo espíritu que el patrón `_column_exists` de `jax-platform/backend/db/migrations.py`, pero inline aquí porque `store.py` no tiene ese helper todavía):
```python
            for col, ddl in [
                ("user_id", "ALTER TABLE jacobs_pipelines ADD COLUMN user_id VARCHAR(50) NULL"),
                ("tenant_id", "ALTER TABLE jacobs_pipelines ADD COLUMN tenant_id VARCHAR(50) NULL"),
            ]:
                await cur.execute(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='jacobs_pipelines' AND COLUMN_NAME=%s",
                    (col,),
                )
                (exists,) = await cur.fetchone()
                if not exists:
                    await cur.execute(ddl)
```
- En `pipeline_create()`, agregar `user_id`/`tenant_id` al INSERT (columnas y placeholders) y a la tupla de valores (`p.user_id, p.tenant_id`).
- En la función que reconstruye `Pipeline` desde una fila (la que tiene `invoked_by=row["invoked_by"]`), agregar `user_id=row["user_id"], tenant_id=row["tenant_id"]`, y asegurarse de que el `SELECT` de esa función incluya esas columnas (si usa `SELECT *` ya las trae solo).

`jacobs/routes.py` — en `create_pipeline`, donde se construye el objeto `Pipeline` (buscar el `Pipeline(...)` que usa `req.invoked_by` — está después del fragmento ya leído, agregar en la misma llamada):
```python
        user_id=req.user_id,
        tenant_id=req.tenant_id,
```

- [ ] **Step 4: Confirmar verde**

Run: `cd /home/fruiz/jax && PYTHONPATH=/home/fruiz/jax .venv/bin/python jacobs/_pipeline_identity_test.py`
Expected: OK (1 test).

- [ ] **Step 5: `py_compile` + commit**

```bash
cd /home/fruiz/jax && .venv/bin/python -m py_compile jacobs/models.py jacobs/store.py jacobs/routes.py 2>&1 || python3 -m py_compile jacobs/models.py jacobs/store.py jacobs/routes.py
git add jacobs/models.py jacobs/store.py jacobs/routes.py jacobs/_pipeline_identity_test.py
git commit -m "fix(jacobs): Pipeline carga user_id/tenant_id real, no solo invoked_by de display"
```

---

### Task 6: `executor.py` propaga identidad al dispatch de motor_registry

**Files:**
- Modify: `jacobs/executor.py`
- Test: extender `jacobs/_pipeline_identity_test.py` (mismo archivo de Task 5) o crear `jacobs/_executor_motor_payload_test.py` si `_invoke_motor` es difícil de aislar sin red real — decidir al leer `_invoke_motor` completo primero.

**Interfaces:**
- Consumes: `Pipeline.user_id`/`.tenant_id` de Task 5.
- Modifies: `_invoke_motor(step: Step, timeout: int)` → `_invoke_motor(step: Step, pipeline: Pipeline, timeout: int)`. Confirmar todos los call-sites de `_invoke_motor` (al menos `_dispatch_step`, que ya recibe `pipeline`) y actualizarlos.

- [ ] **Step 1: Leer `_invoke_motor` completo y su(s) call-site(s)**

Antes de escribir el test: `sed -n '475,545p' jacobs/executor.py` para ver el cuerpo real de `_invoke_motor` y confirmar dónde arma el `payload` (visto parcialmente: incluye `caller`, `capability`, `motor`, `trace_id`, `prompt`) y `grep -n "_invoke_motor(" jacobs/executor.py` para los call-sites.

- [ ] **Step 2: Escribir el test que falla**

```python
# Agregar a jacobs/_pipeline_identity_test.py (mismo archivo de Task 5)
from unittest.mock import AsyncMock, patch
from jacobs.models import Step, StepStatus


class ExecutorMotorPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_motor_incluye_identidad_del_pipeline(self):
        from jacobs import executor

        pipeline = Pipeline(
            pipeline_id="p1", name="t", invoked_by="Fernando", mode="supervised",
            status=PipelineStatus.pending, user_id="1", tenant_id="test-tenant",
            created_at=time.time(), updated_at=time.time(),
        )
        step = Step(
            step_id="s1", pipeline_id="p1", step_index=0, facet="kimi",
            capability="implementation", status=StepStatus.pending,
            trace_id="t1", input={"prompt": "hola"},
        )

        captured = {}

        class _FakeResp:
            status_code = 202
            def json(self):
                return {"job_id": "j1", "status": "running"}
            def raise_for_status(self):
                pass

        async def fake_post(self, url, json=None, **kwargs):
            captured["json"] = json
            return _FakeResp()

        with patch("httpx.AsyncClient.post", fake_post):
            try:
                await executor._invoke_motor(step, pipeline, timeout=30)
            except Exception:
                pass  # el resto del polling puede fallar en este test acotado; solo interesa el payload inicial

        self.assertEqual(captured["json"].get("user_id"), "1")
        self.assertEqual(captured["json"].get("tenant_id"), "test-tenant")
```

- [ ] **Step 3: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax && PYTHONPATH=/home/fruiz/jax .venv/bin/python jacobs/_pipeline_identity_test.py`
Expected: FAIL — `TypeError: _invoke_motor() takes 2 positional arguments but 3 were given` (la firma vieja no acepta `pipeline`).

- [ ] **Step 4: Implementar**

En `jacobs/executor.py`:
- Cambiar la firma: `async def _invoke_motor(step: Step, pipeline: Pipeline, timeout: int) -> dict:`.
- En el diccionario `payload` (visto: `{"caller": "jacobs", "capability": ..., "motor": ..., "trace_id": ..., "prompt": ...}`), agregar:
```python
        "user_id":    pipeline.user_id,
        "tenant_id":  pipeline.tenant_id,
```
- Actualizar el/los call-site(s) de `_invoke_motor` (encontrados en Step 1) para pasar `pipeline` — casi seguro dentro de `_dispatch_step(step, pipeline)`, que ya tiene `pipeline` en scope.

- [ ] **Step 5: Confirmar verde**

Run: `cd /home/fruiz/jax && PYTHONPATH=/home/fruiz/jax .venv/bin/python jacobs/_pipeline_identity_test.py`
Expected: OK (2 tests).

- [ ] **Step 6: `py_compile` + commit**

```bash
cd /home/fruiz/jax && python3 -m py_compile jacobs/executor.py
git add jacobs/executor.py jacobs/_pipeline_identity_test.py
git commit -m "fix(jacobs): propagar user_id/tenant_id del pipeline al dispatch de motor_registry"
```

---

### Task 7: `motor_registry` acepta identidad y escribe `axioma_usage`

**Files:**
- Modify: `las_manos/motor_registry/models.py`, `las_manos/motor_registry/routes.py`, `las_manos/motor_registry/worker.py`
- Create: `las_manos/motor_registry/usage_writer.py`
- Test: `las_manos/_motor_usage_writer_test.py` (nuevo)

**Interfaces:**
- Produces: `usage_writer.record_motor_usage(user_id: str, tenant_id: str, facet: str, provider_id: str, model: str, tokens_in: int, tokens_out: int) -> None` — mismo cálculo de costo que `jax-platform/backend/api/admin/usage.py::_lookup_model_price` (mismo criterio "3 espejos" ya usado en `credential_resolver.py`/`model_catalog.py`, no un paquete compartido).
- Consumes en `worker.py`: `motor_entry.provider` (ya existe en `MotorEntry`, ver `catalog.py`) para saber `provider_id`; `usage`/`finish_reason` ya capturados (fix de hoy, `_finish_reason`/`_usage` en el job).

- [ ] **Step 1: Escribir el test que falla**

```python
#!/usr/bin/env python3
# jax/las_manos/_motor_usage_writer_test.py
"""motor_registry escribe axioma_usage directo (misma DB jax_memory, las_manos
ya se conecta ahi via credential_resolver). Costo: mismo lookup contra
`model` que usa jax-platform, espejado aca (mismo criterio que
credential_resolver.py/model_catalog.py -- repos independientes, sin
paquete compartido). Corre con:
  PYTHONPATH=/home/fruiz/jax/las_manos .venv/bin/python _motor_usage_writer_test.py
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("JAX_DB_NAME", "jax_memory_test")

from motor_registry import usage_writer


async def _seed_priced_model(provider_id, model_id, price_in, price_out):
    import aiomysql
    conn = await aiomysql.connect(
        host=os.getenv("JAX_DB_HOST", "localhost"), port=int(os.getenv("JAX_DB_PORT", "3306")),
        user=os.getenv("JAX_DB_USER", ""), password=os.getenv("JAX_DB_PASSWORD", ""),
        db=os.getenv("JAX_DB_NAME", "jax_memory_test"), autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, "
                "price_input_per_1m_usd, price_output_per_1m_usd) "
                "VALUES (%s, %s, 'available', 'manual', NOW(), %s, %s) "
                "ON DUPLICATE KEY UPDATE price_input_per_1m_usd=%s, price_output_per_1m_usd=%s",
                (provider_id, model_id, price_in, price_out, price_in, price_out),
            )
    finally:
        conn.close()


async def _fetch_last_usage_row():
    import aiomysql
    conn = await aiomysql.connect(
        host=os.getenv("JAX_DB_HOST", "localhost"), port=int(os.getenv("JAX_DB_PORT", "3306")),
        user=os.getenv("JAX_DB_USER", ""), password=os.getenv("JAX_DB_PASSWORD", ""),
        db=os.getenv("JAX_DB_NAME", "jax_memory_test"), autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tokens_in, tokens_out, cost_usd, model, facet FROM axioma_usage ORDER BY id DESC LIMIT 1"
            )
            return await cur.fetchone()
    finally:
        conn.close()


class MotorUsageWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_record_motor_usage_calcula_costo_real(self):
        await _seed_priced_model("moonshot", "kimi-k2.7-code", 0.95, 4.00)
        await usage_writer.record_motor_usage(
            "1", "test-tenant", "kimi", "moonshot", "kimi-k2.7-code", 1000, 500,
        )
        row = await _fetch_last_usage_row()
        tokens_in, tokens_out, cost_usd, model, facet = row
        self.assertEqual(tokens_in, 1000)
        self.assertEqual(tokens_out, 500)
        expected = (1000 * 0.95 + 500 * 4.00) / 1_000_000
        self.assertAlmostEqual(float(cost_usd), expected, places=9)

    async def test_record_motor_usage_sin_identidad_no_escribe(self):
        row_before = await _fetch_last_usage_row()
        await usage_writer.record_motor_usage(None, None, "kimi", "moonshot", "kimi-k2.7-code", 100, 50)
        row_after = await _fetch_last_usage_row()
        self.assertEqual(row_before, row_after)  # fail-soft: sin identidad, no escribe nada


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Confirmar que falla por la razón correcta**

Run: `cd /home/fruiz/jax/las_manos && PYTHONPATH=/home/fruiz/jax/las_manos .venv/bin/python _motor_usage_writer_test.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'motor_registry.usage_writer'`.

- [ ] **Step 3: Implementar `usage_writer.py`**

```python
# jax/las_manos/motor_registry/usage_writer.py
"""Escritura directa a axioma_usage (jax-platform) desde motor_registry.
Mismo patron de conexion que credential_resolver.py/jacobs/store.py: cada
repo se conecta a la misma DB jax_memory con su propio conector minimo,
sin paquete compartido (repos/venvs independientes, mismo trade-off
documentado desde Fase 1).
"""
from __future__ import annotations

import logging
import os

import aiomysql

logger = logging.getLogger("motor_registry.usage_writer")


def _db_cfg() -> dict:
    return {
        "host": os.getenv("JAX_DB_HOST", "localhost"),
        "port": int(os.getenv("JAX_DB_PORT", "3306")),
        "user": os.getenv("JAX_DB_USER", ""),
        "password": os.getenv("JAX_DB_PASSWORD", ""),
        "db": os.getenv("JAX_DB_NAME", "jax_memory"),
        "charset": "utf8mb4",
        "autocommit": True,
    }


async def _lookup_model_price(conn, provider_id: str, model: str) -> tuple[float | None, float | None]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT price_input_per_1m_usd, price_output_per_1m_usd "
            "FROM model WHERE provider_id=%s AND model_id=%s",
            (provider_id, model),
        )
        row = await cur.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


async def record_motor_usage(
    user_id: str | None,
    tenant_id: str | None,
    facet: str,
    provider_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Fail-soft en dos sentidos: sin user_id/tenant_id no escribe nada (job
    disparado sin identidad real todavia -- ver Task 6), y cualquier error
    de DB se loguea sin romper el flujo del worker (usage tracking best-effort,
    mismo criterio que record_usage en jax-platform)."""
    if not user_id or not tenant_id:
        return
    try:
        conn = await aiomysql.connect(**_db_cfg())
        try:
            price_in, price_out = await _lookup_model_price(conn, provider_id, model)
            cost = None
            if price_in is not None and price_out is not None:
                cost = (tokens_in * float(price_in) + tokens_out * float(price_out)) / 1_000_000
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost_usd, request_type) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 'motor')",
                    (tenant_id, user_id, facet, model, tokens_in, tokens_out, cost),
                )
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"record_motor_usage failed facet={facet} reason={type(e).__name__}: {e}")
```

- [ ] **Step 4: Confirmar verde**

Run: `cd /home/fruiz/jax/las_manos && PYTHONPATH=/home/fruiz/jax/las_manos .venv/bin/python _motor_usage_writer_test.py`
Expected: OK (2 tests).

- [ ] **Step 5: Conectar `worker.py`/`models.py`/`routes.py`**

`motor_registry/models.py` — agregar a `MotorDispatchRequest` (tiene `model_config = {"extra": "forbid"}`, así que estos campos son obligatorios de declarar):
```python
    user_id:    str | None = None
    tenant_id:  str | None = None
```

`motor_registry/routes.py` — en la llamada a `motor_worker.run(...)` dentro de `dispatch()`, agregar:
```python
            user_id=req.user_id,
            tenant_id=req.tenant_id,
```

`motor_registry/worker.py` — agregar `user_id: str | None = None, tenant_id: str | None = None` a la firma de `run()`. Al final, justo antes (o después) del `store.update(...)` que marca `COMPLETED`, agregar:
```python
    from motor_registry.usage_writer import record_motor_usage
    provider_id = _MOTOR_PROVIDER_MAP.get(motor)
    if provider_id and usage:
        await record_motor_usage(
            user_id, tenant_id, motor, provider_id, motor_entry.model,
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
        )
```
(el import inline evita un ciclo si `usage_writer` llegara a importar algo de `worker` en el futuro — no es el caso hoy, pero mantiene el mismo estilo cauteloso que el resto del módulo).

- [ ] **Step 6: Extender `_worker_max_tokens_test.py` para cubrir la escritura de usage**

Agregar un test nuevo a `las_manos/_worker_max_tokens_test.py` (ya existe, mismo patrón de mocks) que pase `user_id`/`tenant_id` a `worker.run(...)` y verifique (con `usage_writer.record_motor_usage` parcheado via `unittest.mock.patch`) que se llama con los argumentos correctos — no hace falta DB real en este test, ya está cubierta la lógica de costo en Step 1-4.

- [ ] **Step 7: `py_compile` + suite completa + commit**

```bash
cd /home/fruiz/jax/las_manos && .venv/bin/python -m py_compile motor_registry/models.py motor_registry/routes.py motor_registry/worker.py motor_registry/usage_writer.py
PYTHONPATH=/home/fruiz/jax/las_manos .venv/bin/python _worker_max_tokens_test.py
PYTHONPATH=/home/fruiz/jax/las_manos .venv/bin/python _motor_usage_writer_test.py
cd /home/fruiz/jax && git add las_manos/motor_registry/models.py las_manos/motor_registry/routes.py las_manos/motor_registry/worker.py las_manos/motor_registry/usage_writer.py las_manos/_motor_usage_writer_test.py las_manos/_worker_max_tokens_test.py
git commit -m "feat(motor_registry): escribir axioma_usage real cuando hay identidad de usuario"
```

---

## Cierre — deploy y verificación en producción

### Task 8: Desplegar y verificar con evidencia real

**Files:** ninguno nuevo — solo restart + verificación.

- [ ] **Step 1:** Confirmar con el usuario antes de reiniciar servicios (mismo criterio de todo el día).
- [ ] **Step 2:** `sudo systemctl restart jax-platform.service` y `sudo systemctl restart jax-las-manos.service` — verificar `NRestarts=0` y `/api/health`/`/health` en 200.
- [ ] **Step 3:** Disparar un chat real por cada transporte que tenga credencial activa (jekyll/hipatia/thot/ada/jax_local) y confirmar en `axioma_usage` que `tokens_in`/`tokens_out`/`cost_usd` ya no son cero.
- [ ] **Step 4:** Disparar un pipeline real con un step de Kimi y confirmar en `axioma_usage` una fila con `request_type='motor'`, `facet='kimi'`, tokens y costo reales.
- [ ] **Step 5:** Abrir la pestaña Costos en el navegador (`AdminCosts.jsx`) y confirmar visualmente que la tabla y el gráfico muestran datos reales — sin necesidad de rebuild de frontend (no se tocó ningún `.jsx`).
- [ ] **Step 6:** Actualizar `CONTEXT.md` (jax) con el resultado, mismo formato que las entradas de hoy.
