# Fase 2 — Consolidación faceta→modelo (Bloque C) + Catálogo (Bloque D, diseño)

**Fecha:** 2026-08-09 · **Rama:** `infra/facetas-consolidacion` (desde `infra/mariadb-12.3-migration`; Bloque D en sub-rama `infra/facetas-bloque-d`) · **Estado:** Bloque C COMPLETO y verificado (C1.7 en verde; commits `f7ebe64`/`18a5789` en jax, `447d3ec`/`130d426` en jax-platform). Bloque D — **D1 diseño en papel, sin implementar (PARADA 1)**.
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

**Bloque C — cierre real (corrige la nota "PARADA 2" que quedó escrita arriba y ya no es cierta):** C2 se aprobó y se ejecutó. Los 3 despachadores (REPL, Mesa web, Jacobs) resuelven la misma faceta al mismo modelo contra `facet_binding`, verificado con evidencia (`facet_resolver.py`, ver D0 abajo). C1.7 en verde. `facet_models` (legacy) sigue viva sin tocar, tal como decía C1.6.

---

## Bloque D — Catálogo de modelos y sincronización

**Estado: D1 — diseño en papel, nada implementado.** Base de esta sección: lectura directa de código real en `jax-platform` al momento de escribir esto (no supuesto), citada punto por punto abajo.

## D0 — Evidencia de partida (por qué esto hace falta, con archivo y línea)

- **`backend/api/admin/keys.py:18`** — `PROVIDERS` sigue hardcodeado en Python, con `"model": "gpt-4o"` para `thot`. La realidad operativa (`jacobs/executor.py`, confirmado en Bloque C) es `gpt-5.5`. Este es el bug concreto que P5e de la auditoría señaló — no una hipótesis; sigue vivo hoy porque el ítem #10 de la tabla C1.4 ("`admin/keys.py PROVIDERS` → ELIMINAR") quedó *planeado* en Bloque C pero no se ejecutó ahí (Bloque C solo tocó el despacho de Jacobs/REPL/Mesa web, no la pantalla de admin). D2 lo cierra de una vez.
- **`backend/facet_resolver.py:67-71`** — el JOIN real hoy: `facet_binding.model_id` es `VARCHAR(100)` de texto libre, sin FK. Confirma literalmente lo que C1.2 ya dejó dicho: la FK contra `model` se agrega recién aquí, en D1.1.
- **`docs/fase1-credenciales-diseno.md` (`CREATE TABLE provider`)** — ya trae `base_url`, `auth_type`, `is_local`, `status`. D1.1 reutiliza esa tabla tal cual, sin duplicarla.
- **`backend/credential_resolver.py`** — el patrón TTL 30s / stale 300s / fail-closed / dual-read instrumentado ya probado en producción (Fase 1). D1.3 lo espeja para sincronización, no lo reinventa.
- **`backend/api/admin/keys.py:158-169`** (`test_key`) — Gemini es la excepción real ya presente en el código: usa `?key=` en query string, no `Authorization: Bearer`. Los otros 4 proveedores son OpenAI-compatible. Esta divergencia real es la razón concreta de la columna `provider.api_key_transport` en D1.1 — sin ella, D1.3 reproduciría el mismo if/else disperso que se está eliminando.
- **`backend/api/facets.py`** — hoy es **solo lectura pública** (`GET /api/facets`, `POST /{facet}/status`). No existe ningún endpoint admin para editar `facet_binding`. D1.5/D2 tienen que crearlo desde cero, no es un refactor de algo existente.

## D1.1 — DDL de `model` (catálogo)

```sql
CREATE TABLE model (
  id                          INT AUTO_INCREMENT PRIMARY KEY,
  provider_id                 VARCHAR(50)   NOT NULL,
  model_id                    VARCHAR(100)  NOT NULL,   -- string exacto que el proveedor espera en el request
  is_alias                    BOOLEAN       NOT NULL DEFAULT FALSE,  -- TRUE = puntero móvil (ej. "deepseek-chat"); FALSE = versión fijada (ej. "deepseek-v4-flash", "gpt-5.5")
  context_window               INT          NULL,
  supports_tool_use            BOOLEAN      NOT NULL DEFAULT FALSE,
  supports_structured_output   BOOLEAN      NOT NULL DEFAULT FALSE,
  input_modalities             SET('text','image','audio','video') NOT NULL DEFAULT 'text',
  price_input_per_1m_usd       DECIMAL(10,4) NULL,
  price_output_per_1m_usd      DECIMAL(10,4) NULL,
  price_cache_per_1m_usd       DECIMAL(10,4) NULL,
  release_date                 DATE         NULL,
  deprecation_date             DATE         NULL,       -- aviso temprano de models.dev — ver D1.4
  status                       ENUM('available','degraded','deprecated','gone') NOT NULL DEFAULT 'available',
  source                       ENUM('provider_api','models_dev','manual') NOT NULL,  -- procedencia del dato — nunca implícita
  source_checked_at            DATETIME     NOT NULL,   -- última vez que ESTA fila se confirmó contra su fuente
  created_at                   DATETIME     DEFAULT NOW(),
  FOREIGN KEY (provider_id) REFERENCES provider(id),
  UNIQUE KEY uk_provider_model (provider_id, model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Decisión explícita — sin columna `alias_of`:** se consideró un puntero `alias_of INT` (alias → versión fijada que resuelve hoy). Se descarta: `facet_binding.resolved_version` (D1.2) más el historial de `model_binding_proposal` (D1.3) ya cubren "a qué apunta un alias ahora mismo" — una columna aparte sería una segunda fuente de lo mismo, exactamente el patrón que Bloque C eliminó 11 veces (C1.4). Un alias puede resolver distinto según la cuenta/región; lo único verificable es lo que YA se observó en una invocación real, no lo que se supone que apunta hoy.

**Extensión necesaria a `provider` (Fase 1), justificada por D0:**
```sql
ALTER TABLE provider
  ADD COLUMN api_key_transport ENUM('header_bearer','query_param') NOT NULL DEFAULT 'header_bearer',
  ADD COLUMN models_list_url   VARCHAR(255) NULL;   -- endpoint real de /v1/models o equivalente; NULL = sin sync automático de capa (a) para este proveedor todavía
```
`gemini` queda con `api_key_transport='query_param'`; los otros 4, default. Sin esto, D1.3 termina reproduciendo el `if provider_id == "gemini"` de `admin/keys.py:159` en un archivo nuevo.

**Migración de `facet_binding.model_id` (texto libre) a FK real** — 4 pasos, reversibles, mismo criterio de Fase 1 (parity check antes de cortar):
1. Poblar `model` desde los valores hoy hardcodeados (executor.py, `admin/keys.py PROVIDERS`, config.toml) — idempotente, mismo patrón que `_seed_providers`.
2. `ALTER TABLE facet_binding ADD COLUMN model_ref INT NULL, ADD FOREIGN KEY (model_ref) REFERENCES model(id);`
3. Backfill: `UPDATE facet_binding b JOIN model m ON m.provider_id=b.provider_id AND m.model_id=b.model_id SET b.model_ref=m.id;`
4. Verificar **cero filas con `model_ref IS NULL`** antes de tocar código de lectura. Solo entonces `facet_resolver.py` cambia su JOIN para leer `model_ref` en vez de comparar texto. La columna vieja `facet_binding.model_id` se conserva de solo-lectura un ciclo de cutover (mismo criterio que `user_api_keys` en B1.4) antes de dropearla en una corrida aparte — no en esta.

## D1.2 — Captura de `resolved_version` (detector de drift)

```sql
ALTER TABLE facet_binding
  ADD COLUMN resolved_version           VARCHAR(100) NULL,  -- último valor confirmado por el proveedor (campo `model` de la respuesta)
  ADD COLUMN resolved_version_checked_at DATETIME    NULL;
```

Captura **best-effort, fire-and-forget** — mismo patrón que la escritura de `messages` en `jax_memory` (CONTEXT.md §5): nunca bloquea ni hace fallar la respuesta al usuario. En cada invocación exitosa, el despachador por transporte (`_invoke_http_openai_compat`/`_invoke_http_gemini`, ya colapsados en Bloque C1.5) compara el `model` devuelto contra el `resolved_version` cacheado en proceso (mismo `_cache` de `facet_resolver.py`, TTL 30s — no una query nueva por mensaje). Si cambió: (a) escribe la nueva fila en `facet_binding`, (b) crea un `model_binding_proposal` con `reason='drift_detected'` (D1.3) — la alerta ES la proposal pendiente, no una tabla de log aparte. Sin esto habría dos estructuras para lo mismo (ver decisión de D1.1).

## D1.3 — Sincronización en tres capas + regla de oro

**a) `/v1/models` del proveedor** (`provider.models_list_url`, credencial vía `resolve_credential_instrumented` — Fase 1, sin reimplementar). Única verdad de disponibilidad para esta cuenta. Upsert en `model` con `source='provider_api'`.

**b) `models.dev`** (`https://models.dev/api.json`) — enriquecimiento: precio, contexto, tool_use, modalidades, `deprecation_date`. Match por `provider_id`+`model_id` normalizado (minúsculas, sin espacios); sin match → los campos de metadata quedan `NULL`, **nunca bloquea el upsert de (a)**. Todo campo que llegue de aquí se guarda con `source='models_dev'` — si (a) ya trajo el mismo dato con `source='provider_api'`, (a) gana siempre. `axioma_usage`/costo real quedan **fuera de alcance** (ver abajo): esta tabla no alimenta ninguna decisión de costo todavía, solo se muestra como referencia con su procedencia visible.

**c) Verificación empírica** — el health check de Fase 1 (`POST /api/admin/keys/{provider}/test`), reutilizado tal cual como tercera señal de vida, no reimplementado.

**REGLA DE ORO (mecanismo concreto, no solo principio):**
```sql
CREATE TABLE model_binding_proposal (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  facet_key           VARCHAR(50) NOT NULL,
  current_model_ref   INT NULL,      -- lo que hoy tiene facet_binding.model_ref (NULL si el binding no existe aún)
  proposed_model_ref  INT NOT NULL,
  reason              ENUM('new_model_available','drift_detected','deprecation_warning') NOT NULL,
  detail              TEXT NULL,
  status              ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  decided_by           INT NULL,
  decided_at           DATETIME NULL,
  created_at           DATETIME DEFAULT NOW(),
  FOREIGN KEY (facet_key) REFERENCES facet(`key`),
  FOREIGN KEY (proposed_model_ref) REFERENCES model(id),
  FOREIGN KEY (decided_by) REFERENCES jax_users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```
- Escritura automática sobre `model` (catálogo): **permitida**, sin gate.
- Escritura sobre `facet_binding` (producción): **solo** vía `UPDATE facet_binding SET model_ref=proposed_model_ref ...` disparado por el endpoint de aprobación, nunca por el job de sync. Rechazar = marcar `status='rejected'`, cero escritura a `facet_binding`.

## D1.4 — Deprecación y recall

- Un `model_id` que **desaparece** de `/v1/models` (capa a) en N sincronizaciones consecutivas (configurable, default 3 — evita marcar por un solo fallo de red del proveedor) pasa `model.status` de `available` → `degraded` → `deprecated`. **Nunca** se borra la fila ni se pasa a `gone` automáticamente; `gone` es un estado de confirmación manual (el operador certifica que el proveedor lo retiró de verdad).
- Un `403`/`404` **súbito** en una invocación real (no en el sync) sobre un `model_ref` que veía funcionando: el despachador marca `model.status='degraded'` de inmediato con nota "posible revocación/recall — reintentable, no es bug de código" — mismo criterio que ya usa LAS MANOS para distinguir kill switch de error real (CONTEXT.md §9, 14-jun).
- `deprecation_date` de `models.dev` (capa b) se muestra como aviso temprano en la UI (D1.5, tab 2) — nunca dispara una transición de `status` por sí sola, es solo la fuente de enriquecimiento, no la fuente de disponibilidad real (esa es siempre la capa a).

## D1.5 — Rediseño de la pantalla en tres pestañas

**Tab 1 — Proveedores y Credenciales.** Ya existe (`AdminApiKeys.jsx` + `backend/api/admin/keys.py`), se reubica sin romper Fase 1: rotar/revocar, audit log, salud persistida (`credential.last_health_status`). Cambio real de alcance de D2 (no repetición de C2): `admin/keys.py` deja de leer `PROVIDERS` hardcodeado y lee `provider` — esto es lo que cierra el bug `gpt-4o` citado en D0.

**Tab 2 — Catálogo de modelos** (nueva). Endpoints nuevos:
- `GET /api/admin/models?provider=&status=` — lista con `is_alias`, precio, `deprecation_date`, `source`/`source_checked_at` visibles (procedencia siempre a la vista, nunca implícita — mismo principio de "origen de autoridad" del §7 de CONTEXT.md).
- `POST /api/admin/models/sync` — dispara D1.3, solo escribe `model`.
- `GET /api/admin/models/proposals?status=pending` / `POST /api/admin/models/proposals/{id}/approve|reject` — la UI de la regla de oro.

**Tab 3 — Facetas y Bindings** (nueva, sobre superficie que hoy no existe — ver D0 sobre `facets.py`). CRUD de `facet_binding` (hoy inexistente en admin), validación de contrato de capabilities **recién real** (`facet.requires_tool_use`/`min_context_tokens` contra `model.supports_tool_use`/`context_window` — el check que C1.2 dejó explícitamente marcado `unknown` porque el catálogo no existía todavía), cadena de fallback (`role='fallback_1'`/`'fallback_2'`), botón "probar faceta end-to-end" (reusa `resolve_facet` + un prompt corto de smoke test, mismo principio que `test_key` pero pasando por el resolver real en vez del array `PROVIDERS`).

Aplica la paleta y componentes ya existentes del proyecto (dark/light por CSS variables, i18n en `es.js`/`en.js` — ninguna etiqueta nueva de UI se hardcodea, política de CLAUDE.md).

## D1.6 — Plan de pruebas (diseño; se ejecuta en D2 con evidencia real, no resumen)

- [ ] El botón de sincronizar trae modelos nuevos al catálogo y **no** cambia ningún `facet_binding` por su cuenta.
- [ ] Un modelo retirado del proveedor se marca `degraded`→`deprecated` tras 3 syncs consecutivos sin aparecer — no se borra la fila.
- [ ] `resolved_version` se captura y difiere visiblemente del `model_id` solicitado cuando el binding es un alias (`is_alias=TRUE`).
- [ ] Un `model_binding_proposal` se puede aprobar (escribe `facet_binding.model_ref`) o rechazar (no escribe nada) desde la UI.
- [ ] Un binding contra un `model_ref` que no existe en el catálogo es rechazado por la FK, con motivo visible en la respuesta del endpoint.
- [ ] Gemini sincroniza vía `api_key_transport='query_param'` sin ningún `if provider_id == "gemini"` nuevo en el código de sync.
- [ ] `admin/keys.py` deja de mostrar `gpt-4o` para thot — el bug de D0 queda cerrado con evidencia (captura de pantalla o response JSON del endpoint).

---

## FUERA DE ALCANCE (anotado, no implementado en D1/D2)

- **`axioma_usage`/costos** — roto: `tokens_in`/`tokens_out` siempre en 0, `cost_usd` 0.00 en el 100% de las filas. El precio de `models.dev` (D1.3-b) no sirve para decisiones de costo hasta que se capturen tokens reales. Se muestra en el catálogo solo como referencia con su `source` visible, nunca como base de cálculo.
- **Retención de backups en R2** (forget+prune fallando en R2) — sin relación con este bloque, no se toca.
- **Código muerto en `jax/_director_patch/`** — no se toca.
- **Capa de credenciales de Fase 1** — D1.3 solo la **consume** (`resolve_credential_instrumented`), no se modifica `credential_resolver.py` ni las tablas `provider`/`credential` salvo la extensión aditiva de D1.1 (`api_key_transport`, `models_list_url`), que no cambia semántica existente ni rompe consumidores actuales.

---

**PARADA 1.** Nada ejecutado — sin `CREATE TABLE`, sin `ALTER TABLE`, sin tocar `executor.py`/`facet_resolver.py`/frontend. Espero aprobación explícita antes de D2.
