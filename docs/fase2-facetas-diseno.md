# Fase 2 — Consolidación faceta→modelo (Bloque C) + Catálogo (Bloque D, diseño)

**Fecha:** 2026-08-09 · **Rama:** `infra/facetas-consolidacion` (desde `infra/mariadb-12.3-migration`) · **Estado:** C1 — diseño en papel.
**Base:** `docs/auditoria-api-keys-2026-08-09.md`, `docs/fase1-credenciales-diseno.md`, C0 de esta corrida.
**Fuera de alcance** (anotado, no implementado): `axioma_usage`/costos (roto, `tokens_in/out`=0, `cost_usd`=0.00 en 100% de las filas — el precio de models.dev no sirve hasta que se capturen tokens reales), retención R2, `_director_patch/`, capa de credenciales de Fase 1 (solo se consume).

---

## C1.1 — `facet`

```sql
CREATE TABLE facet (
  `key`                 VARCHAR(50)  NOT NULL PRIMARY KEY,  -- INMUTABLE: lo referencian los DAGs de Jacobs y el código
  display_name          VARCHAR(100) NOT NULL,
  icon                  VARCHAR(10)  NULL,                   -- emoji/glifo — reemplaza router.py ICONS
  color_hex             VARCHAR(7)   NULL,                   -- reemplaza FACET_COLORS + los 3 arrays de frontend
  persona               TEXT         NULL,                   -- system prompt — reemplaza config.toml system_prompt
  transport             ENUM('http_openai_compat','http_gemini','motor_registry','ollama','subprocess') NOT NULL,
  requires_tool_use     BOOLEAN      NOT NULL DEFAULT FALSE,
  requires_structured_output BOOLEAN NOT NULL DEFAULT FALSE,
  min_context_tokens    INT          NOT NULL DEFAULT 0,
  max_latency_ms        INT          NULL,
  max_cost_per_1k_usd   DECIMAL(10,6) NULL,
  auto_selectable       BOOLEAN      NOT NULL DEFAULT TRUE,   -- reemplaza AUTO_FACETAS
  status                ENUM('active','degraded','disabled') NOT NULL DEFAULT 'active',
  created_at            TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`key` nunca se actualiza tras el insert (se aplica a nivel de código en el resolver/admin, no hay forma nativa de MariaDB de declarar una columna inmutable — se documenta y se verifica en el endpoint de edición: `UPDATE facet SET ... WHERE key=...` nunca incluye `key` en el SET).

## C1.2 — `facet_binding`

```sql
CREATE TABLE facet_binding (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  facet_key    VARCHAR(50)  NOT NULL,
  provider_id  VARCHAR(50)  NOT NULL,
  model_id     VARCHAR(100) NOT NULL,  -- texto libre en Bloque C; FK a `model` recien en D1.1 (el catalogo no existe todavia)
  role         ENUM('primary','fallback_1','fallback_2','disabled') NOT NULL DEFAULT 'primary',
  params       JSON         NULL,      -- temperatura, etc., si el transporte lo soporta
  approved_by  INT          NULL,
  approved_at  DATETIME     NULL,
  created_at   DATETIME     DEFAULT NOW(),
  FOREIGN KEY (facet_key) REFERENCES facet(`key`),
  FOREIGN KEY (provider_id) REFERENCES provider(id),
  FOREIGN KEY (approved_by) REFERENCES jax_users(user_id),
  UNIQUE KEY uk_facet_role (facet_key, role)  -- un solo primary, un solo fallback_1, etc. por faceta
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Validación fail-closed al guardar — declarado con honestidad, no inflado:** en Bloque C, sin el catálogo de `model` (Bloque D), solo son verificables estructuralmente: (a) `facet_key`/`provider_id` existen, (b) `role='primary'` único por faceta (constraint DB), (c) compatibilidad `facet.transport`↔`provider.auth_type` (ej. un `provider.auth_type='subprocess'` — anthropic/hyde — no puede bindearse con role http). Las validaciones de **contrato de capabilities real** (`requires_tool_use`, `min_context_tokens` contra lo que el modelo realmente soporta) requieren la metadata de `model` que Bloque D todavía no crea — quedan como **check pendiente, explícitamente marcado `unknown` en la UI** (no se simula una validación que no puede ser cierta), y se activan solas en cuanto D1.1 aterrice (mismo endpoint, misma función, sin refactor adicional — el hook ya queda escrito).

## C1.3 — Resolver único

```python
# facet_resolver.py — espejado en jax-platform, jax/core, las_manos (mismo
# patron que credential_resolver.py). Consume resolve_credential_instrumented,
# no lo reimplementa.

FACET_CACHE_TTL_SECONDS = 30      # mismo criterio que credenciales
FACET_STALE_MAX_SECONDS = 300

class FacetUnavailableError(Exception):
    """FAIL-CLOSED: sin binding role='primary' activo. El llamador declara
    estado degradado — nunca cae a un default hardcodeado."""

@dataclass
class ResolvedFacet:
    key: str; provider_id: str; model: str; credential: str
    transport: str; persona: str; params: dict

async def resolve_facet(facet_key: str) -> ResolvedFacet:
    # SELECT facet.*, facet_binding.provider_id, facet_binding.model_id, facet_binding.params
    # FROM facet JOIN facet_binding ON facet_binding.facet_key = facet.key
    # WHERE facet.key=%s AND facet.status='active' AND facet_binding.role='primary'
    # credential = await resolve_credential_instrumented(provider_id)  -- Fase 1, sin reimplementar
    # cache TTL 30s / stale 300s, identico a B1.2
```

Importable por los 3 despachadores — un solo módulo, no tres espejos de *lógica* (siguen siendo 3 *archivos* por límite de repos/venvs independientes, mismo trade-off documentado en Fase 1).

## C1.4 — Plan de eliminación (11 backend + 3 frontend = 14 fuentes originales)

| # | Fuente | Destino |
|---|---|---|
| 1 | router.py `LABELS` | **ELIMINAR** — deriva de `facet.display_name` |
| 2 | router.py `ICONS` | **ELIMINAR** — deriva de `facet.icon` |
| 3 | router.py `ALIASES` | **CONSERVAR** — parsing de input del REPL (typos fonéticos), no es dato de identidad ni comportamiento; migrarlo sería sobre-ingeniería para una lista de tolerancia a errores de tipeo |
| 4 | router.py `VALID_FACETAS` | **ELIMINAR** — deriva de `SELECT key FROM facet WHERE status='active'` |
| 5 | router.py `AUTO_FACETAS` | **ELIMINAR** — deriva de `facet.auto_selectable` |
| 6 | `jacobs/models.py VALID_FACETS` | **ELIMINAR** — deriva de facet (vía `facet_resolver`) |
| 7 | `jacobs/plan.py VALID_FACETS` | **ELIMINAR** — duplicado exacto del #6 sin import compartido; se borra y se importa de `models.py`, sin necesitar ni siquiera una query nueva |
| 8 | `chat.py _WEB_KW_SETS`/`_WEB_TIEBREAK` | **CONSERVAR** — heurística de ruteo automático por palabra clave, no es una lista de identidad/existencia; es política de negocio genuinamente distinta |
| 9 | `jax_engine/state.py FACET_COLORS` | **ELIMINAR** — deriva de `facet.color_hex` |
| 10 | `admin/keys.py PROVIDERS` | **ELIMINAR** — ya reemplazable por `provider` ⋈ `facet_binding` (Fase 1 ya sentó la tabla `provider`) |
| 11 | `config.toml [personalities.*]` | **REDUCIDO, no eliminado completo** — `system_prompt`/selección de modelo migran a `facet`/`facet_binding`; sobrevive solo para parámetros de transporte específicos del REPL (`voice_id`, `voice_speed`, `grounding_policy` default) que no son identidad ni modelo |
| 12 | frontend `LeftPanel.jsx FACET_ORDER` | **ELIMINAR** — deriva de un endpoint que expone `facet.*` ordenado |
| 13 | frontend `BottomBar.jsx` array | **ELIMINAR** — mismo endpoint |
| 14 | frontend `PipelineModal.jsx` array | **ELIMINAR** — mismo endpoint (el campo `capability` que trae hoy es de otro sistema ya existente, `las_manos/config.toml [capabilities.*]` — fuera de alcance, no se toca, solo se deja de duplicar el nombre/color/label) |

**Resultado: 11 de 14 eliminadas. 3 sobreviven, las 3 justificadas por ser conceptos genuinamente distintos** (parsing de input, heurística de ruteo, parámetros de transporte) — ninguna es una fuente paralela de "qué facetas existen" o "qué modelo usan". Fuente nueva agregada: **una** (`facet`), que es exactamente la fuente única que se buscaba. Objetivo cumplido: menos fuentes al final que al principio.

## C1.5 — Refactor de Jacobs: antes/después (2 funciones, para validar el patrón)

**`_invoke_jekyll` — ANTES** (`executor.py:331-359`, transporte openai-compatible):
```python
async def _invoke_jekyll(prompt: str, timeout: int) -> dict:
    api_key = await resolve_credential_instrumented("deepseek")
    model   = "deepseek-v4-flash"
    url     = "https://api.deepseek.com/chat/completions"
    ...
    return {"success": True, "facet": "jekyll", "model": model, "result": texto}
```

**DESPUÉS** — colapsa en un invocador genérico por transporte, no por faceta:
```python
async def _invoke_http_openai_compat(f: ResolvedFacet, prompt: str, timeout: int) -> dict:
    url = f"{f.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {f.credential}"}
    payload = {"model": f.model, "messages": [{"role": "system", "content": f.persona}, {"role": "user", "content": prompt}]}
    ...
    return {"success": True, "facet": f.key, "model": f.model, "result": texto}
```
`_invoke_thot`/`_invoke_ada` desaparecen — eran copias casi idénticas de `_invoke_jekyll` con URL/modelo distintos, ahora es la misma función parametrizada por `f`.

**`_invoke_hipatia` — ANTES** (`executor.py:260-326`, formato Gemini + grounding, no colapsa con el openai-compat por el formato de request/response distinto):
```python
async def _invoke_hipatia(prompt: str, timeout: int) -> dict:
    api_key = await resolve_credential_instrumented("gemini")
    model   = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    ...
```
**DESPUÉS**:
```python
async def _invoke_http_gemini(f: ResolvedFacet, prompt: str, timeout: int) -> dict:
    url = f"{f.base_url}/models/{f.model}:generateContent?key={f.credential}"
    # resto de la lógica de grounding/retry es de Gemini como transporte, no de Hipatia como faceta — se conserva intacta
    ...
```

**`_dispatch_step` — ANTES** (`executor.py:754-772`, if/elif por nombre de faceta):
```python
if step.facet == "jekyll": return await _invoke_jekyll(prompt, timeout)
if step.facet == "hipatia": return await _invoke_hipatia(prompt, timeout)
if step.facet == "thot": return await _invoke_thot(prompt, timeout)
if step.facet == "ada": return await _invoke_ada(prompt, timeout)
if step.facet == "kimi": return await _invoke_motor(prompt, timeout)
if step.facet == "jax_local": return await _invoke_jax_local(prompt, timeout)
if step.facet == "hyde": return await _invoke_hyde(prompt, timeout)
```

**DESPUÉS** — despacho por TRANSPORTE (legítimo, es una propiedad real y distinta), la faceta ya no aparece como literal:
```python
f = await resolve_facet(step.facet)
if f.transport == "http_openai_compat": return await _invoke_http_openai_compat(f, prompt, timeout)
if f.transport == "http_gemini":        return await _invoke_http_gemini(f, prompt, timeout)
if f.transport == "motor_registry":     return await _invoke_motor(f, prompt, timeout)
if f.transport == "ollama":             return await _invoke_ollama(f, prompt, timeout)
if f.transport == "subprocess":         return await _invoke_hyde(f, prompt, timeout)  # hyde conserva su gate humano especial, no es genérico
```
Si mañana se agrega una 8ª faceta que use DeepSeek, **cero líneas de código nuevas** — solo una fila en `facet`+`facet_binding`.

## C1.6 — Corte y rollback

Secuencia: (1) migrar `facet`+`facet_binding` desde los valores hardcodeados actuales (idempotente, mismo patrón que `_seed_providers`/`_migrate_user_api_keys_to_credential` de Fase 1); (2) verificar que `resolve_facet()` devuelve, para las 7 facetas, exactamente lo mismo que hoy están hardcodeadas (query de paridad, no se toca código de despacho todavía); (3) recién entonces se cambia `_dispatch_step`/`_invoke_*`. `facet_models` (legacy) **no se toca** — sigue siendo la fuente de la Mesa web (`_resolve_active_model`) hasta que se decida deprecarla, fuera de esta corrida. Rollback: `git revert` de los commits de C2 — las tablas nuevas quedan como dato huérfano inofensivo, nada las referencia si el código vuelve atrás.

## C1.7 — Plan de pruebas (diseño; se ejecuta en C2)

- [ ] Cambiar el modelo de `jekyll` desde el admin → Jacobs lo refleja sin restart (mismo mecanismo de TTL 30s ya probado en Fase 1).
- [ ] Repetir para las 5 facetas hardcodeadas (hipatia, jekyll, thot, ada, jax_local).
- [ ] `resolve_facet("jekyll")` da el mismo `(provider, model)` en REPL, Mesa web y Jacobs — mismo query, misma tabla, comparación directa.
- [ ] Binding con `provider_id` inexistente → rechazado al guardar (FK constraint + validación de capa aplicación, motivo visible en la respuesta del endpoint).
- [ ] Faceta sin `role='primary'` activo → `FacetUnavailableError`, estado degradado explícito en el step de Jacobs (no una llamada con modelo vacío).
- [ ] Hyde (`subprocess`) y jax_local (`ollama`) siguen funcionando — su transporte no cambia, solo dejan de tener el modelo/URL hardcodeado en Python.

---

## Nota para Bloque D (no se ejecuta todavía)

`model.model_id` distingue **alias** (puntero móvil, ej. `deepseek-chat`) de **versión fijada** (ej. `deepseek-v4-flash`) con un campo `is_alias BOOLEAN`. `facet_binding.model_id` pasa a FK contra `model` recién en D1.1 — hasta entonces sigue siendo texto libre en Bloque C, documentado arriba. `resolved_version` (lo que el proveedor confirma haber ejecutado, capturado del campo `model` de la respuesta) es el detector de drift que la auditoría (P5e) señaló como ausente. `models.dev` es enriquecimiento, nunca verdad de producción para costo — eso se verifica contra el proveedor real.

---

**PARADA 2.** Nada ejecutado — sin `CREATE TABLE`, sin tocar `executor.py`. Espero aprobación explícita antes de C2.
