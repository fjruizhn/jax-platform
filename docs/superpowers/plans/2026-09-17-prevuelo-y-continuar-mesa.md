# Pre-vuelo y continuar — lado Mesa (jax-platform) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la Mesa no cree ni continúe un pipeline sin un pre-vuelo aprobado por Jacobs y sin el consentimiento humano del costo máximo, que un pipeline abortado o vencido se pueda continuar (con cambio de faceta) desde el panel, y que los rechazos de Jacobs lleguen al usuario con su código.

**Architecture:** `api/pipelines.py` gana un helper único que convierte cualquier respuesta de Jacobs en dict o en `HTTPException` con el mismo status; sobre él se montan `/preflight`, la creación con pre-vuelo + confirmación, y `/continue/preflight` + `/continue`. El umbral de confirmación es un ajuste nuevo (`pipeline_confirmar_usd`) de `ajustes.py`. En el frontend, `PipelineModal` y el nuevo `ContinuarPipelineModal` corren el pre-vuelo dentro del modal y confirman en `ConfirmarCostoDialogo` (sobre `Dialogo`). Los datos que el pre-vuelo de Jacobs necesita (columna `capability.min_output_tokens`, valor `preflight` del ENUM de salud, topes de salida medidos, `motor.max_tokens=0` para kimi/ada) van como migraciones de este repo.

**Tech Stack:** FastAPI + Python 3.14 (venv `/home/fruiz/jax-platform/backend/.venv`), aiomysql, MariaDB (`jax_memory_test` para tests), pytest; React 19 + Vite + vitest + Testing Library; Tailwind con tokens de `src/tema/tokens.css`.

**Spec:** `/home/fruiz/worktrees/jax-prevuelo/docs/superpowers/specs/2026-09-17-prevuelo-y-continuar-design.md` (repo `jax`, rama del plan J). El plan argumenta desde el spec: leer los dos.

---

## Desvíos del spec

Cada desvío con su evidencia. El implementador NO los reabre: son la resolución.

| # | Spec dice | Código real / brief | Resolución |
|---|---|---|---|
| DV-1 | §7 A: `PATCH /api/admin/motors/kimi` y `/ada` con `max_tokens=0` como acción operativa. | `backend/db/migrations.py:680,685` siembra `8000` para kimi y ada con `INSERT IGNORE`; `backend/tests/test_motor_migrations.py:66` fija `8000`. Un PATCH a mano no llega a `jax_memory_test`, a CI ni a una base nueva. El brief pide migración de datos versionada. | Migración de datos con marcador `motor_max_tokens_al_catalogo_v1` + `_MOTOR_SEED` a `0` (Task 3). El PATCH sólo gana la validación `max_tokens >= 0`. |
| DV-2 | §7 F: completar `model.max_output_tokens` "como migración de datos (patrón `_fix_*` idempotente con marcador)". | `backend/db/migrations.py:1883-1924` ya tiene `_MODEL_MAX_OUTPUT_TOKENS_SEED` con guarda `WHERE max_output_tokens IS NULL`: nunca pisa un valor puesto después, que es lo que el marcador protege. | Se agregan las filas medidas a esa lista (Task 2). Sin marcador nuevo: la guarda `IS NULL` ya es la propiedad. |
| DV-3 | §2 F habla de "max_output_tokens **y precios**" medidos. | Los precios los trae `model_catalog.enrich_from_models_dev` (`backend/model_catalog.py:253`); §4.6 declara que precio NULL no bloquea (`sin_precio`, no acotado → confirmación siempre). | Task 2 mide y REPORTA en el commit los modelos cobradores con precio NULL; no se siembran precios a mano. |
| DV-4 | §4.5: `request_type='preflight_probe'` "si axioma_usage lo restringe". | `backend/db/migrations.py:63`: `request_type VARCHAR(20)` sin CHECK ni ENUM; `'preflight_probe'` tiene 15 caracteres. | No hay DDL. Nada que migrar ni testear en este repo. |
| DV-5 | §4.5: `source='preflight'` es "DDL en jax-platform"; no menciona `facet_health.SOURCES`. | `backend/facet_health.py:38` + el patrón de `tests/test_facet_health_outcomes.py:189` (el ENUM y el frozenset se comparan). Jax-platform no escribe `preflight`, pero el conjunto tiene que espejar el ENUM. | `SOURCE_PREFLIGHT` en `facet_health.py` y test ENUM == SOURCES (Task 1). |
| DV-6 | §6.1: toda respuesta ≥ 400 de Jacobs → `{code:"jacobs_rechazo", status, motivo}`. | §6.2 exige que los 409/422 de la creación (`costo_supera_lo_aceptado`, `prevuelo_rechazado`, `reasignacion_invalida`) vuelvan al modal con su código y datos; con `jacobs_rechazo` genérico el modal no podría mostrarlos. | El helper deja pasar, con su status y SÓLO los campos declarados, los códigos propios de Jacobs `CODIGOS_DE_JACOBS`; todo lo demás es `jacobs_rechazo` (Task 5). |
| DV-7 | §6.1: "la Mesa lo manda [`costo_max_aceptado_usd`] siempre que haya confirmación". | Sin confirmación (costo ≤ umbral, todo acotado), si Jacobs recalcula un costo mayor entre el pre-vuelo y la creación, nadie consintió ese gasto. | La Mesa manda SIEMPRE `costo_max_aceptado_usd`: el confirmado si lo hubo, el umbral del ajuste si no (el umbral es el consentimiento previo del admin). |
| DV-8 | §2 y §4.7: el camino por objetivo (`_from_objective`) se pre-vuela en Jacobs después de planificar. | Desde la Mesa, sin `steps` no hay pre-vuelo previo ni costo que confirmar: sería un bypass del consentimiento. §2: "la Mesa siempre manda pasos explícitos (`buildSteps`)". | `POST /api/pipelines` sin `steps` → 422 `pasos_requeridos` (Task 6). Tests viejos que creaban con `{"objective": ...}` se actualizan. |
| DV-9 | §6.2: el panel muestra "el motivo" del aborto; no dice de dónde sale. | `jax/jacobs/executor.py:1129,1184,1276-1285`, `jacobs/routes.py:243`, `jacobs/reaper.py:158`: los eventos `STEP_FAILED`, `PIPELINE_ABORTED`, `PIPELINE_CANCELLED`, `KILL_SWITCH_ABORTED`, `REAPED` en `jacobs_events` (índice `idx_events_pipeline`, `jax/jacobs/store.py:75`). | `GET /api/pipelines` agrega `causa` a los `aborted`/`expired` con UNA consulta `IN` por PK de índice (Task 7), con EXPLAIN. |
| DV-10 | §5.1: 409 "estado no continuable" sin código. | La Mesa necesita un código estable (A-51, `tests/test_mesa_codigos.py:42`). | Código `estado_no_continuable` (contrato con J abajo). |
| DV-11 | §6.1 no menciona el cupo ni el WebSocket al continuar. | Continuar ocupa un cupo del tenant (`resource_manager`), y los eventos de WS salen de `engine_state` de la Mesa (`backend/jax_engine/state.py:99-107`), no de Jacobs. | `continue` verifica `max_pipelines`, admite el recurso y publica `pipeline_continued` desde `engine_state.continuar_pipeline` (Task 7). |
| DV-12 | §6.2: `ConfirmarCostoDialogo` sobre `Dialogo`, encima del modal. | `frontend/src/lib/useCerrarConEscape.js`: cada `Dialogo` escucha Escape en `document`; dos abiertos cerrarían los dos. | El modal padre pasa `cerrable={!confirmando}` mientras la confirmación está abierta (Tasks 9 y 10, con test). |
| DV-13 | §9: canarios de CI "rojos una vez a propósito". | Los límites de esta ronda prohíben `git push` a los agentes. | El canario es la Task 12 Step 5-8, marcada **CONTROLADOR** (requiere push). |
| DV-14 | §7 A: la falta de auditoría del PATCH de motores "va a `DEUDA.md` con fecha". | `DEUDA.md` vive en el repo `jax`. | Task 12 Step 9 entrega el texto exacto al controlador; este plan no toca el repo `jax`. |

---

## Global Constraints

- **Límites de la ronda** (`/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/limites-plan.md`): nada de `git push`, merge, PR, deploy, reinicio de servicios, `sudo`, escrituras en `/etc`; ninguna escritura a `jax_memory`; lecturas SELECT de producción SÓLO en la Task 2 (medición) y en sesión `READ ONLY`; nada de llamadas a proveedores LLM pagos ni a `:7777`/`:8080` (el GET de metadata de modelos de la Task 2 es gratuito y lo pide el brief); trabajar SOLO en `/home/fruiz/worktrees/jax-platform-prevuelo`; nunca `git stash` pelado.
- **Commits:** `git -C /home/fruiz/worktrees/jax-platform-prevuelo ...` con `pwd; git branch --show-current` en el MISMO comando antes de cada commit (rama `feat/prevuelo-y-continuar`). Cada mensaje termina con `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Backend tests** (siempre contra `jax_memory_test`, `JAX_DB_NAME` exportado DESPUÉS de sourcear el .env):
  `cd /home/fruiz/worktrees/jax-platform-prevuelo/backend && set -a && . /etc/jax/.env && set +a && export JAX_DB_NAME=jax_memory_test JAX_REPO_PATH=/home/fruiz/jax && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest <args>`
  Modo sin DB: mismo comando con `JAX_CI_NO_DB=1` agregado al `export`. En adelante `$PYTEST` = ese comando hasta `-m pytest`.
- **Frontend tests:** `cd /home/fruiz/worktrees/jax-platform-prevuelo/frontend && npx vitest run <archivos>` (en adelante `$VITEST`).
- **TDD de la casa:** cada test nuevo se ve FALLAR contra el código viejo con el Expected escrito en el paso; recién después se implementa.
- **P10:** todo `except` amplio (`Exception`, desnudo) sin `raise` lleva `# fail-soft: <razón específica>` en la misma línea. Los `except` estrechos que se escriben acá relanzan o están justificados en comentario.
- **i18n:** cero strings visibles en código; es/en con paridad (`src/i18n/paridad.test.js`). Nunca mostrar un código o texto del backend crudo: el `motivo`/`detalle` redactado se muestra como dato vía `t.respuestaDelServicio` / `t.detalleDelPrevuelo`.
- **Tema:** sólo clases de tokens existentes (`text-peligro`, `text-aviso`, `text-exito`, `text-texto*`, `bg-hundido`, `bg-superficie`, `bg-accion`, `text-sobre-color`, `border-borde*`). Ningún color crudo.
- **Diálogos:** ninguna `confirm`/`alert`/`prompt` (con o sin `window.`); todo en `components/Dialogo.jsx`. Sólo `Dialogo.jsx` dibuja `fixed inset-0` (`src/politica/modales.test.js`).
- **Montos:** en JSON viajan como string decimal (`"0.60"`); la Mesa acepta también número JSON de Jacobs y siempre responde string. En pantalla, `formatearUsd(monto, lang)` (`Intl.NumberFormat` con `localeFor(lang)`).
- **`detail` de HTTPException** en `api/pipelines.py`: literal snake_case, dict literal con `"code"`, o un nombre de variable (AST de `tests/test_mesa_codigos.py:42`). Nunca una llamada.
- **Ajuste nuevo:** clave `pipeline_confirmar_usd`, Decimal ≥ 0 con hasta 2 decimales, máximo `999999.99`, valor sembrado `0.50`, `0` = confirmar siempre (spec §6.1).
- **LAS CUATRO:** consultas nuevas con índice y EXPLAIN (Task 7); prueba de carga de `/api/pipelines/preflight` a c=25 con p95 y rps (Task 12).

### Contrato con Jacobs (lo implementa el plan J en el repo `jax`; los tests de este plan usan `backend/tests/jacobs_falso.py`, que lo respeta EXACTAMENTE)

Errores con el envoltorio de FastAPI `{"detail": {...}}`. Montos como string decimal.

- `POST {JACOBS_URL}/preflight` — cuerpo `{"invoked_by": "plataforma", "user_id", "tenant_id", "steps": [...]}`.
  - 200 `Veredicto`: `{"ok": bool, "violaciones": [{"paso": int, "faceta": str, "regla": str, "detalle": str}], "costo_max_usd": "<dec>", "pasos_costo": [{"paso": int, "faceta": str, "modelo": str, "llamadas_max": int, "tokens_in_max": int, "tokens_out_max": int, "usd_max": "<dec>" | null, "motivo": str | null}], "sondeadas": [str]}` (200 aunque `ok=false`).
  - 503 `{"detail": {"code": "prevuelo_no_disponible"}}`; 403 `invoked_by`.
- `POST {JACOBS_URL}/pipeline` — cuerpo de hoy + `"costo_max_aceptado_usd": "<dec>"`.
  - 200 `{"pipeline_id", ..., "costo_max_usd", "pasos_costo"}`.
  - 422 `{"detail": {"code": "prevuelo_rechazado", "violaciones", "costo_max_usd", "pasos_costo"}}`.
  - 409 `{"detail": {"code": "costo_supera_lo_aceptado", "costo_max_usd", "costo_max_aceptado_usd"}}`; 423/429 como hoy.
- `POST {JACOBS_URL}/pipeline/{id}/continue/preflight` — cuerpo `{"invoked_by", "user_id", "tenant_id", "reasignar"?: {"<step_index>": "<faceta>"}}`.
  - 200 `{"continuable": bool, "motivo": str | null, "pasos_a_correr": [int], "pasos_reusados": [int], "veredicto": Veredicto | null}`.
  - 422 `{"detail": {"code": "reasignacion_invalida", "violaciones"}}`; 403; 404.
- `POST {JACOBS_URL}/pipeline/{id}/continue` — mismo cuerpo + `"costo_max_aceptado_usd"`.
  - 200 `{"pipeline_id", "status": "running", "run_epoch", "pasos_a_correr", "pasos_reusados", "costo_max_usd", "pasos_costo"}`.
  - 409 `{"detail": {"code": "estado_no_continuable", "status_actual": str}}` o `costo_supera_lo_aceptado`; 422 `prevuelo_rechazado` | `reasignacion_invalida`; 423; 429.
- `GET {JACOBS_URL}/pipeline/{id}` (existe): `{"pipeline": {...}, "steps": [{"step_index", "facet", "capability", "depends_on": [int], "status", ...}]}`.

Si el plan J cambia una forma, se adapta en un solo lugar de cada lado: `api/pipelines.py` (`_evaluar_veredicto`, `_continuable`, `CODIGOS_DE_JACOBS`, `_CAMPOS_DE_CODIGO`) y `tests/jacobs_falso.py`.

---

## File Structure

Backend (`/home/fruiz/worktrees/jax-platform-prevuelo/backend`):
- Modify `db/migrations.py` — columna `capability.min_output_tokens`; ENUM `facet_health_event.source` + `preflight`; semilla medida de `min_output_tokens` (marcador); topes de salida medidos; `motor.max_tokens=0` kimi/ada (marcador); ajuste `pipeline_confirmar_usd` (marcador).
- Modify `facet_health.py` — `SOURCE_PREFLIGHT`.
- Modify `api/admin/motors.py` — `max_tokens >= 0` en POST y PATCH.
- Modify `ajustes.py` — clave `CONFIRMAR_USD`, intérprete de monto, límites.
- Modify `api/pipelines.py` — helper `_json_de_jacobs`/`_rechazo_de_jacobs`; `/preflight`; creación con pre-vuelo; `/continue/preflight`; `/continue`; `causa` en la lista.
- Modify `jax_engine/schemas.py` (`EventType` + `pipeline_continued`), `jax_engine/state.py` (`continuar_pipeline`).
- Create `tests/jacobs_falso.py` (no test: helper de contrato), `tests/test_prevuelo_esquema.py`, `tests/test_prevuelo_datos_medidos.py`, `tests/test_motor_tope_catalogo.py`, `tests/test_ajuste_confirmar_costo.py`, `tests/test_pipelines_propagacion.py`, `tests/test_pipelines_prevuelo.py`, `tests/test_pipelines_continuar.py`.
- Modify tests existentes: `tests/conftest.py` (fixture `ajustes_en_db`), `tests/test_ajustes.py`, `tests/test_migracion_ajustes.py`, `tests/test_motor_migrations.py`, `tests/test_mesa_codigos.py`, `tests/test_pipelines_identity_injection.py`, `tests/test_ajuste_max_pipelines.py`, `tests/test_pipeline_ownership.py`, `tests/test_pipelines_http_pooling.py`.

Frontend (`/home/fruiz/worktrees/jax-platform-prevuelo/frontend/src`):
- Create `lib/moneda.js` (+ test), `components/ConfirmarCostoDialogo.jsx`, `components/RightPanel/ContinuarPipelineModal.jsx` (+ test), `store/useJaxStore.pipelineContinued.test.js`.
- Modify `api/errores.js` (+ test), `i18n/es.js`, `i18n/en.js`, `store/useJaxStore.js`, `components/BottomBar/pipelineChain.js` (+ test), `components/BottomBar/PipelineModal.jsx` (+ test), `components/BottomBar/BottomBar.jsx` (+ `BottomBar.errores.test.jsx`), `components/RightPanel/RightPanel.jsx` (+ test), `pages/admin/AdminSettings.jsx` (+ test).

CI: Modify `/home/fruiz/worktrees/jax-platform-prevuelo/.github/workflows/policy.yml` (pisos).

---

### Task 0: Preparar y medir la línea de base

**Files:** ninguno.

**Interfaces:**
- Consumes: nada.
- Produces: los tres números de base que la Task 12 suma (esperados: con DB 1386 passed / 1 failed ambiental / 1 skipped; sin DB 787 passed; vitest 535 passed).

- [ ] **Step 1: Verificar rama y árbol limpio**

Run: `cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && git status --short && git log --oneline -1`
Expected: `feat/prevuelo-y-continuar`, sin cambios, HEAD `accc641` (o el commit de este plan encima).

- [ ] **Step 2: Instalar dependencias del frontend en el worktree**

Run: `cd /home/fruiz/worktrees/jax-platform-prevuelo/frontend && npm ci`
Expected: termina sin error; existe `node_modules/.bin/vitest`.

- [ ] **Step 3: Medir la base de las tres suites**

Run (tres comandos):
- `$PYTEST -q -p no:cacheprovider 2>&1 | tail -3`
- `$PYTEST -q -p no:cacheprovider 2>&1 | tail -3` con `JAX_CI_NO_DB=1`
- `$VITEST 2>&1 | tail -5`

Expected: aproximadamente `1386 passed, 1 failed, 1 skipped` / `787 passed` / `535 passed`. Anotar los tres números exactos: la Task 12 usa ESTOS, no los de este plan, si difieren.

---

### Task 1: Esquema — `capability.min_output_tokens` y `source='preflight'`

**Files:**
- Modify: `backend/db/migrations.py` (`CREATE_FACET_HEALTH_EVENT` ~línea 547, `_COLUMNS` final ~línea 1460, `_ENUM_EXTENSIONS` ~línea 1528)
- Modify: `backend/facet_health.py:28-38`
- Test: `backend/tests/test_prevuelo_esquema.py` (nuevo)

**Interfaces:**
- Consumes: nada.
- Produces: columna `capability.min_output_tokens INT NOT NULL DEFAULT 0`; `facet_health_event.source` acepta `'preflight'`; `facet_health.SOURCE_PREFLIGHT = "preflight"` ∈ `facet_health.SOURCES`.

- [ ] **Step 1: Escribir los tests que fallan**

Create `backend/tests/test_prevuelo_esquema.py`:

```python
"""Esquema que el pre-vuelo de Jacobs necesita (spec 2026-09-17 §4.4 y §4.5).
El DDL de `capability` y de `facet_health_event` vive en este repo; Jacobs
(plan J) lee `min_output_tokens` y escribe `source='preflight'`. Los puros
corren sin DB; los que piden `client` van contra jax_memory_test."""
import re

import facet_health
from db import migrations as m
from tests.identidades import sql


def test_el_escritor_conoce_el_source_preflight():
    assert facet_health.SOURCE_PREFLIGHT == "preflight"
    assert "preflight" in facet_health.SOURCES


def test_el_ENUM_de_source_coincide_exacto_con_SOURCES():
    """Mismo cierre mecánico que outcome (test_facet_health_outcomes.py): el
    ENUM de la DB y el frozenset no se derivan uno del otro sin una base."""
    valores = set(re.findall(r"'([a-z_]+)'",
                             re.search(r"source\s+ENUM\(([^)]+)\)", m.CREATE_FACET_HEALTH_EVENT).group(1)))
    for tabla, columna, valor, _ddl in m._ENUM_EXTENSIONS:
        if tabla == "facet_health_event" and columna == "source":
            valores.add(valor)
    assert valores == facet_health.SOURCES


def test_la_columna_min_output_tokens_esta_declarada():
    assert ("capability", "min_output_tokens",
            "ALTER TABLE capability ADD COLUMN min_output_tokens INT NOT NULL DEFAULT 0") in m._COLUMNS


def test_min_output_tokens_es_int_not_null_default_0(client):
    filas = client.portal.call(
        sql,
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' AND COLUMN_NAME = 'min_output_tokens'",
        (), True)
    assert filas == (("int", "NO", "0"),)


async def _insertar_y_leer_preflight():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO facet_health_event (facet, outcome, source, detail, ts) "
                    "VALUES ('test-prevuelo-esquema', 'ok', 'preflight', NULL, 1)")
            except Exception as exc:  # fail-soft: el test devuelve el error para afirmar AFUERA del portal (conftest._envolver_portal_call)
                return repr(exc)
            fila_id = cur.lastrowid
            await cur.execute("SELECT source FROM facet_health_event WHERE id = %s", (fila_id,))
            fila = await cur.fetchone()
            await cur.execute("DELETE FROM facet_health_event WHERE id = %s", (fila_id,))
    return fila


def test_facet_health_event_acepta_source_preflight(client):
    assert client.portal.call(_insertar_y_leer_preflight) == ("preflight",)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$PYTEST tests/test_prevuelo_esquema.py -v -p no:cacheprovider`
Expected: 4 FAILED y 1 PASSED —
- `test_el_escritor_conoce_el_source_preflight`: `AttributeError: module 'facet_health' has no attribute 'SOURCE_PREFLIGHT'`;
- `test_el_ENUM_de_source_coincide_exacto_con_SOURCES`: PASSED hoy (los dos lados sin `preflight`). Se ve rojo en el Step 3a, que cambia un solo lado;
- `test_la_columna_min_output_tokens_esta_declarada`: `AssertionError: assert ('capability', 'min_output_tokens', ...) in [...]`;
- `test_min_output_tokens_es_int_not_null_default_0`: `assert () == (('int', 'NO', '0'),)`;
- `test_facet_health_event_acepta_source_preflight`: `assert "DataError(1265, \"Data truncated for column 'source' at row 1\")" == ('preflight',)`.

- [ ] **Step 3a: Agregar el source al escritor y ver rojo el test del ENUM**

In `backend/facet_health.py`, replace:

```python
SOURCE_CANARY_REBIND = "canary_rebind"
```
with:
```python
SOURCE_CANARY_REBIND = "canary_rebind"
# Pre-vuelo (spec 2026-09-17 §4.5): la sonda de Jacobs registra su resultado
# con este source. La plataforma no lo escribe, pero el conjunto espeja el
# ENUM de la DB (test_prevuelo_esquema.py).
SOURCE_PREFLIGHT = "preflight"
```
and replace:
```python
SOURCES = frozenset({SOURCE_CHAT, SOURCE_CANARY_PERIODIC, SOURCE_CANARY_REBIND})
```
with:
```python
SOURCES = frozenset({SOURCE_CHAT, SOURCE_CANARY_PERIODIC, SOURCE_CANARY_REBIND, SOURCE_PREFLIGHT})
```

Run: `$PYTEST tests/test_prevuelo_esquema.py::test_el_ENUM_de_source_coincide_exacto_con_SOURCES -v -p no:cacheprovider`
Expected: FAIL `assert {'canary_periodic', 'canary_rebind', 'chat'} == frozenset({'canary_periodic', 'canary_rebind', 'chat', 'preflight'})`.

- [ ] **Step 3b: DDL**

In `backend/db/migrations.py`, in `CREATE_FACET_HEALTH_EVENT`, replace:
```python
    source  ENUM('chat','canary_periodic','canary_rebind') NOT NULL,
```
with:
```python
    source  ENUM('chat','canary_periodic','canary_rebind','preflight') NOT NULL,
```

At the end of `_ENUM_EXTENSIONS` (after the `provider`/`api_key_transport` tuple, before `]`), add:
```python
    # Pre-vuelo (spec 2026-09-17 §4.5): la sonda de Jacobs registra su
    # resultado con source='preflight', así el próximo pre-vuelo dentro de la
    # ventana de salud no vuelve a sondear. Lista COMPLETA de valores.
    (
        "facet_health_event", "source", "preflight",
        "ALTER TABLE facet_health_event MODIFY COLUMN source "
        "ENUM('chat','canary_periodic','canary_rebind','preflight') NOT NULL",
    ),
```

At the end of `_COLUMNS` (after the `("capability", "mode", ...)` tuple, before `]`), add:
```python
    # Pre-vuelo (spec 2026-09-17 §4.4): tokens de salida que una capability
    # necesita como mínimo. Jacobs compara el tope efectivo del paso contra
    # esto y rechaza con `tope_insuficiente`. 0 = sin mínimo declarado. La
    # semilla MEDIDA la pone _semilla_min_output_tokens_v1.
    ("capability", "min_output_tokens",
     "ALTER TABLE capability ADD COLUMN min_output_tokens INT NOT NULL DEFAULT 0"),
```

- [ ] **Step 4: Correr y ver verde**

Run: `$PYTEST tests/test_prevuelo_esquema.py tests/test_facet_health_outcomes.py tests/test_facet_health_writer.py -v -p no:cacheprovider`
Expected: todos PASS (la app de test corre `run_migrations` al arrancar `client` y aplica el ALTER a `jax_memory_test`).

- [ ] **Step 5: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/db/migrations.py backend/facet_health.py backend/tests/test_prevuelo_esquema.py && \
git commit -m "feat(prevuelo): capability.min_output_tokens y source preflight en la salud

Esquema que el pre-vuelo de Jacobs lee y escribe (spec 2026-09-17 §4.4, §4.5).
request_type 'preflight_probe' no necesita DDL: VARCHAR(20) sin restricción.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Datos medidos — semilla de `min_output_tokens` y topes de salida

**Files:**
- Create (scratchpad, NO en el repo): `/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medir_prevuelo.py`, `.../scratchpad/medir_topes_proveedor.py`
- Modify: `backend/db/migrations.py` (`_MODEL_MAX_OUTPUT_TOKENS_SEED` ~línea 1883; nueva `_semilla_min_output_tokens_v1`; `run_migrations`)
- Test: `backend/tests/test_prevuelo_datos_medidos.py` (nuevo)

**Interfaces:**
- Consumes: columna `capability.min_output_tokens` (Task 1).
- Produces: `migrations.MIGRACION_MIN_OUTPUT_TOKENS_V1: str`, `migrations.MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17: dict[str, int]`, `async migrations._semilla_min_output_tokens_v1(cur) -> None`; filas nuevas en `_MODEL_MAX_OUTPUT_TOKENS_SEED`.

- [ ] **Step 1: Escribir el script de medición de `min_output_tokens` (sólo lectura)**

Create `/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medir_prevuelo.py`:

```python
"""Medición de SOLO LECTURA contra jax_memory (producción) para las semillas
del pre-vuelo (spec 2026-09-17 §4.4 y §7 F). Sesión READ ONLY + ROLLBACK:
no escribe nada. Imprime la evidencia y los literales para migrations.py."""
import asyncio
import json
import os
from collections import defaultdict

import aiomysql

LOG_MOTOR = "/home/fruiz/jax/las_manos/logs/motor_jobs.jsonl"
BLOQUE = 1024

# Pasos HTTP directos de Jacobs: su fila de uso (request_type='pipeline') no
# lleva job_id (jax/jacobs/usage_writer.py), así que se une por faceta y
# ventana [started_at, finished_at + 5 s]. Una fila que cae en pasos de
# capabilities DISTINTAS es ambigua y se excluye (se lista).
SQL_PASOS_HTTP = """
SELECT u.id, s.capability, u.tokens_out
FROM jacobs_steps s
JOIN axioma_usage u
  ON u.facet = s.facet
 AND u.request_type = 'pipeline'
 AND UNIX_TIMESTAMP(u.created_at) BETWEEN FLOOR(s.started_at) AND CEIL(s.finished_at) + 5
WHERE s.status = 'completed' AND s.started_at IS NOT NULL AND s.finished_at IS NOT NULL
"""

SQL_MODELOS_QUE_COBRAN = """
SELECT b.facet_key, f.transport, m.provider_id, m.model_id, m.max_output_tokens,
       m.price_input_per_1m_usd, m.price_output_per_1m_usd
FROM facet_binding b
JOIN facet f ON f.`key` = b.facet_key
JOIN model m ON m.provider_id = b.provider_id AND m.model_id = b.model_id
WHERE b.role = 'primary' AND f.transport IN ('http_openai_compat', 'http_gemini')
ORDER BY b.facet_key
"""


def redondear(n: int) -> int:
    return -(-n // BLOQUE) * BLOQUE


def corridas_de_motor() -> dict[str, list[int]]:
    ultimo = {}
    with open(LOG_MOTOR, encoding="utf-8") as f:
        for linea in f:
            try:
                d = json.loads(linea)
            except ValueError:
                continue  # línea cortada del log: no es una corrida medible
            ultimo[d["job_id"]] = d
    por_cap = defaultdict(list)
    for d in ultimo.values():
        uso = d.get("_usage") or {}
        if d.get("status") == "completed" and isinstance(uso.get("completion_tokens"), int):
            por_cap[d["capability"]].append(uso["completion_tokens"])
    return por_cap


async def main():
    assert os.environ.get("JAX_DB_NAME") == "jax_memory", "esta medición lee producción a propósito"
    conn = await aiomysql.connect(
        host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
        user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"],
        db="jax_memory", autocommit=False)
    try:
        async with conn.cursor() as cur:
            await cur.execute("SET SESSION TRANSACTION READ ONLY")
            await cur.execute("START TRANSACTION READ ONLY")
            await cur.execute(SQL_PASOS_HTTP)
            filas_http = await cur.fetchall()
            await cur.execute(SQL_MODELOS_QUE_COBRAN)
            modelos = await cur.fetchall()
            await cur.execute("SELECT `key` FROM capability ORDER BY `key`")
            capabilities = [r[0] for r in await cur.fetchall()]
            await cur.execute("ROLLBACK")
    finally:
        conn.close()

    caps_por_fila = defaultdict(set)
    tokens_por_fila = {}
    for fila_id, capability, tokens_out in filas_http:
        caps_por_fila[fila_id].add(capability)
        tokens_por_fila[fila_id] = tokens_out
    ambiguas = sorted(i for i, caps in caps_por_fila.items() if len(caps) > 1)
    medidas = defaultdict(list)
    for fila_id, caps in caps_por_fila.items():
        if len(caps) == 1 and tokens_por_fila[fila_id]:
            medidas[next(iter(caps))].append(tokens_por_fila[fila_id])
    for capability, valores in corridas_de_motor().items():
        medidas[capability].extend(valores)

    print("== A. min_output_tokens (max completion de corridas completadas) ==")
    print(f"filas de uso ambiguas excluidas: {ambiguas}")
    print("capability | corridas | max_crudo | redondeado")
    for capability in capabilities:
        valores = medidas.get(capability, [])
        crudo = max(valores) if valores else 0
        print(f"{capability} | {len(valores)} | {crudo} | {redondear(crudo) if crudo else 0}")
    print("\n# --- literal para MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 ---")
    for capability in capabilities:
        valores = medidas.get(capability, [])
        if valores:
            print(f'    "{capability}": {redondear(max(valores))},  # max={max(valores)}, corridas={len(valores)}')
    sin_corridas = [c for c in capabilities if not medidas.get(c)]
    print(f"# sin corridas medibles (quedan en 0): {sin_corridas}")

    print("\n== B. modelos vinculados que cobran ==")
    print("faceta | transporte | provider/model | max_output_tokens | precio_in | precio_out")
    for facet, transport, provider_id, model_id, tope, p_in, p_out in modelos:
        print(f"{facet} | {transport} | {provider_id}/{model_id} | {tope} | {p_in} | {p_out}")


asyncio.run(main())
```

- [ ] **Step 2: Correr la medición (lectura de producción)**

Run:
```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo/backend && set -a && . /etc/jax/.env && set +a && \
export JAX_DB_NAME=jax_memory && \
/home/fruiz/jax-platform/backend/.venv/bin/python /tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medir_prevuelo.py \
  | tee /tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medicion-prevuelo.txt
```
Expected: tabla A (una línea por capability), el bloque `# --- literal ...` y la tabla B. Si el bloque literal sale vacío (ninguna corrida medible), DETENERSE y reportarlo al controlador: el test de Step 5 exige al menos un valor.

- [ ] **Step 3: Medir los topes de salida faltantes contra el proveedor (GET gratuito de metadata)**

Create `/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medir_topes_proveedor.py`:

```python
"""GET de metadata/listado de modelos (gratuito, sin generar tokens) para los
modelos que cobran con max_output_tokens NULL. La key sale de la tabla
credential de producción (sólo lectura) y NUNCA se imprime.
Uso: python medir_topes_proveedor.py <provider_id>/<model_id> [...]"""
import asyncio
import json
import os
import sys

import aiomysql
import httpx

from credential_resolver import resolve_credential  # PYTHONPATH=backend (ver el comando)


async def base_url(provider_id: str) -> str | None:
    conn = await aiomysql.connect(
        host=os.environ["JAX_DB_HOST"], port=int(os.environ["JAX_DB_PORT"]),
        user=os.environ["JAX_DB_USER"], password=os.environ["JAX_DB_PASSWORD"], db="jax_memory")
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT base_url FROM provider WHERE id = %s", (provider_id,))
            fila = await cur.fetchone()
    finally:
        conn.close()
    return fila[0] if fila else None


async def medir(provider_id: str, model_id: str) -> None:
    key = await resolve_credential(provider_id)
    async with httpx.AsyncClient(timeout=20.0) as cliente:
        if provider_id == "gemini":
            r = await cliente.get(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}",
                headers={"x-goog-api-key": key})
            datos = r.json()
            print(f"{provider_id}/{model_id}: HTTP {r.status_code} outputTokenLimit={datos.get('outputTokenLimit')}")
            return
        url = (await base_url(provider_id) or "").rstrip("/")
        r = await cliente.get(f"{url}/models", headers={"Authorization": f"Bearer {key}"})
        entradas = r.json().get("data", [])
        entrada = next((e for e in entradas if e.get("id") == model_id), None)
        print(f"{provider_id}/{model_id}: HTTP {r.status_code} entrada={json.dumps(entrada, ensure_ascii=False)}")


async def main():
    assert os.environ.get("JAX_DB_NAME") == "jax_memory"
    for par in sys.argv[1:]:
        provider_id, _, model_id = par.partition("/")
        await medir(provider_id, model_id)


asyncio.run(main())
```

Run, pasando cada `provider/model` de la tabla B con `max_output_tokens` = `None` Y cada binding de `_FACET_BINDING_SEED` (`backend/db/migrations.py:635`) cuyo transporte cobra y que NO está en `_MODEL_MAX_OUTPUT_TOKENS_SEED` (hoy: `gemini/gemini-2.5-flash`):
```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo/backend && set -a && . /etc/jax/.env && set +a && \
export JAX_DB_NAME=jax_memory PYTHONPATH=/home/fruiz/worktrees/jax-platform-prevuelo/backend && \
/home/fruiz/jax-platform/backend/.venv/bin/python /tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medir_topes_proveedor.py gemini/gemini-2.5-flash <los NULL de la tabla B> \
  | tee -a /tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/medicion-prevuelo.txt
```
Expected: una línea por modelo. Gemini trae `outputTokenLimit=<int>`. Para OpenAI-compatibles: si la `entrada` trae un campo de tope de salida (p. ej. `max_output_tokens`, `max_completion_tokens`, `output_token_limit`), ese es el valor; si no lo trae, abrir la documentación oficial del proveedor y anotar URL + fecha de lectura + número. Un modelo sin valor medible ni documentado queda NULL (Jacobs lo rechazará con `sin_contrato_de_salida`) y se nombra en el commit.

- [ ] **Step 4: Escribir los tests que fallan**

Create `backend/tests/test_prevuelo_datos_medidos.py`:

```python
"""Datos que el pre-vuelo necesita, medidos y no inventados (spec 2026-09-17
§4.4 y §7 F). La medición (sólo lectura contra producción) y su evidencia
están en el comentario de MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 y en el commit."""
import pytest

from db import migrations
from db.migrations import (
    MIGRACION_MIN_OUTPUT_TOKENS_V1,
    MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17,
    _semilla_min_output_tokens_v1,
)
from tests.identidades import sql

TRANSPORTES_QUE_COBRAN = ("http_openai_compat", "http_gemini")


def test_lo_medido_son_multiplos_de_1024_positivos():
    assert MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17, "la medición no dejó ningún valor"
    malos = {k: v for k, v in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17.items()
             if not (isinstance(v, int) and v > 0 and v % 1024 == 0)}
    assert malos == {}


def test_todo_modelo_de_la_semilla_que_cobra_declara_su_tope_de_salida():
    """Sin tope no hay costo máximo: una base vacía no puede nacer con una
    faceta que el pre-vuelo rechaza con sin_contrato_de_salida."""
    transporte = {key: t for key, _n, _i, _c, t, _a in migrations._FACET_SEED}
    topes = {(p, mo) for p, mo, _ in migrations._MODEL_MAX_OUTPUT_TOKENS_SEED}
    faltan = [(f, p, mo) for f, p, mo in migrations._FACET_BINDING_SEED
              if transporte[f] in TRANSPORTES_QUE_COBRAN and (p, mo) not in topes]
    assert faltan == []


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _semilla_min_output_tokens_v1(cur)
        await conn.commit()


@pytest.fixture
def sin_marca_min(client):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_MIN_OUTPUT_TOKENS_V1,), True)
    antes = client.portal.call(sql, "SELECT `key`, min_output_tokens FROM capability", (), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
    client.portal.call(sql, "UPDATE capability SET min_output_tokens = 0", ())
    yield
    for clave, valor in antes:
        client.portal.call(sql, "UPDATE capability SET min_output_tokens = %s WHERE `key` = %s", (valor, clave))
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                           (MIGRACION_MIN_OUTPUT_TOKENS_V1,))


def _minimos(client):
    return dict(client.portal.call(sql, "SELECT `key`, min_output_tokens FROM capability", (), True))


def test_primera_corrida_siembra_lo_medido_y_deja_el_resto_en_cero(client, sin_marca_min):
    client.portal.call(_migrar)
    filas = _minimos(client)
    presentes = {k: v for k, v in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17.items() if k in filas}
    assert presentes, "ninguna capability medida existe en jax_memory_test"
    assert {k: filas[k] for k in presentes} == presentes
    assert {k: v for k, v in filas.items() if k not in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 and v != 0} == {}


def test_segunda_corrida_no_pisa_lo_que_el_admin_cambio(client, sin_marca_min):
    client.portal.call(_migrar)
    clave = next(k for k in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 if k in _minimos(client))
    client.portal.call(sql, "UPDATE capability SET min_output_tokens = 7 WHERE `key` = %s", (clave,))
    client.portal.call(_migrar)
    assert _minimos(client)[clave] == 7
```

- [ ] **Step 5: Correr y ver que fallan**

Run: `$PYTEST tests/test_prevuelo_datos_medidos.py -v -p no:cacheprovider`
Expected: ERROR de colección en los 4: `ImportError: cannot import name 'MIGRACION_MIN_OUTPUT_TOKENS_V1' from 'db.migrations'`. Además, para ver rojo el test de la semilla por sí solo, correrlo con el import comentado es innecesario: queda cubierto en Step 7 (sin la fila de gemini en la lista falla con `assert [('hipatia', 'gemini', 'gemini-2.5-flash')] == []`). Verificarlo agregando primero SÓLO la migración (Step 6a) y corriendo ese test antes de Step 6b.

- [ ] **Step 6a: Migración de `min_output_tokens` con los números medidos**

In `backend/db/migrations.py`, immediately after `_seed_model_max_output_tokens` (the function ending `(limit, provider_id, model_id),\n        )`), add:

```python
MIGRACION_MIN_OUTPUT_TOKENS_V1 = "capability_min_output_tokens_v1"
# Pre-vuelo (spec 2026-09-17 §4.4): tokens de SALIDA que cada capability
# necesitó como máximo en corridas COMPLETADAS, redondeado hacia arriba a
# múltiplo de 1024. MEDIDO 2026-09-17 contra jax_memory (producción), sesión
# READ ONLY: pasos HTTP de Jacobs = axioma_usage.tokens_out unido a
# jacobs_steps completados por faceta y ventana [started_at, finished_at+5 s]
# (filas ambiguas entre capabilities excluidas); pasos de Motor Registry =
# _usage.completion_tokens de las_manos/logs/motor_jobs.jsonl (último registro
# por job_id, status completed). Script y salida: ver el commit que agrega
# esta constante. Una capability sin corridas medibles no está acá y queda en
# 0 (sin mínimo), declarado en el mismo commit.
MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17: dict[str, int] = {
    # PEGAR AQUÍ, sin editar, las líneas del bloque
    # "# --- literal para MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17 ---" de
    # scratchpad/medicion-prevuelo.txt (Step 2). Cada línea ya trae su
    # comentario con el máximo crudo y la cantidad de corridas.
}


async def _semilla_min_output_tokens_v1(cur) -> None:
    """UNA vez (marcador): después, lo que el admin cambie no se pisa al
    arrancar. Sin marcador y a medias, la próxima corrida la completa: cada
    sentencia fija el mismo valor."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
    if await cur.fetchone() is not None:
        return
    for clave, minimo in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17.items():
        await cur.execute("UPDATE capability SET min_output_tokens = %s WHERE `key` = %s", (minimo, clave))
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
```

Reemplazar el comentario `# PEGAR AQUÍ ...` (las 4 líneas) por las líneas literales medidas. El dict resultante tiene la forma `"research": 16384,  # max=15210, corridas=37` por entrada (los números son los de la salida, no estos).

In `run_migrations`, replace:
```python
            await _seed_model_max_output_tokens(cur)
```
with:
```python
            await _seed_model_max_output_tokens(cur)
            # Después de _asegurar_forma_de_capability_mode y de la columna de
            # _COLUMNS: las filas de capability existen con su forma final.
            await _semilla_min_output_tokens_v1(cur)
```

Run: `$PYTEST tests/test_prevuelo_datos_medidos.py::test_todo_modelo_de_la_semilla_que_cobra_declara_su_tope_de_salida -v -p no:cacheprovider`
Expected: FAIL `assert [('hipatia', 'gemini', 'gemini-2.5-flash')] == []`.

- [ ] **Step 6b: Topes de salida medidos**

In `backend/db/migrations.py`, at the end of `_MODEL_MAX_OUTPUT_TOKENS_SEED` (after the `("ollama", "qwen3.6:35b-a3b-q4_K_M", 262144),` tuple, before `]`), add one tuple per model measured in Step 3, with its provenance comment. Forma exacta de cada entrada:

```python
    # Pre-vuelo (spec 2026-09-17 §7 F): medido 2026-09-17 con
    # GET https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash
    # (x-goog-api-key, metadata sin costo) -> outputTokenLimit=<N de la salida>.
    ("gemini",   "gemini-2.5-flash",   <N de la salida>),
```
Para un OpenAI-compatible: `# medido 2026-09-17 con GET <base_url>/models -> <campo>=<N>` o `# documentación oficial <URL>, leída 2026-09-17: <N>`. `<N de la salida>` es el entero impreso en `medicion-prevuelo.txt` para ese modelo; no hay otro valor válido.

- [ ] **Step 7: Correr y ver verde**

Run: `$PYTEST tests/test_prevuelo_datos_medidos.py tests/test_semilla_contrato_dispatch.py tests/test_model_max_output_tokens.py -v -p no:cacheprovider`
Expected: todos PASS.

- [ ] **Step 8: Commit (con la evidencia)**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/db/migrations.py backend/tests/test_prevuelo_datos_medidos.py && \
git commit -F - <<'MSG'
feat(prevuelo): semilla medida de min_output_tokens y topes de salida

Medido 2026-09-17 contra jax_memory (producción), sólo lectura, con
scratchpad/medir_prevuelo.py y medir_topes_proveedor.py (GET de metadata, sin
costo). Evidencia:

<pegar aquí las tablas A y B y las líneas de medir_topes_proveedor.py de
scratchpad/medicion-prevuelo.txt>

Capabilities sin corridas medibles (quedan en 0): <lista de la salida>.
Modelos que cobran con precio NULL (sin_precio -> confirmación siempre, no se
siembran precios a mano; los trae enrich_from_models_dev): <lista de la tabla B>.
Modelos sin tope medible (quedan NULL, pre-vuelo: sin_contrato_de_salida): <lista o "ninguno">.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
```

---

### Task 3: Motores al tope del catálogo y `max_tokens >= 0`

**Files:**
- Modify: `backend/db/migrations.py:680,685` (`_MOTOR_SEED`), nueva `_motor_max_tokens_al_catalogo_v1`, `run_migrations`
- Modify: `backend/api/admin/motors.py:104-120,228-237`
- Modify: `backend/tests/test_motor_migrations.py:66`
- Test: `backend/tests/test_motor_tope_catalogo.py` (nuevo)

**Interfaces:**
- Consumes: nada.
- Produces: `migrations.MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1 = "motor_max_tokens_al_catalogo_v1"`, `migrations.MOTORES_AL_TOPE_DEL_CATALOGO = ("kimi", "ada")`, `async migrations._motor_max_tokens_al_catalogo_v1(cur)`; POST/PATCH de motores responden 422 con `max_tokens < 0`.

- [ ] **Step 1: Escribir los tests que fallan**

Create `backend/tests/test_motor_tope_catalogo.py`:

```python
"""D1 de Fernando (spec 2026-09-17 §1, §7 A): kimi y ada con motor.max_tokens=0
(0 = sin tope propio: manda model.max_output_tokens). El pipeline ef9b2d6e se
cortó en 8000 con 7997 tokens de razonamiento. Y un max_tokens negativo llega
al proveedor como tope inválido: el endpoint lo rechaza."""
import pytest

from db import migrations
from db.migrations import MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1, _motor_max_tokens_al_catalogo_v1
from tests.identidades import cabeceras, sql

ADMIN = "motor-tope-catalogo"


def test_la_semilla_de_kimi_y_ada_nace_sin_tope_propio():
    topes = {fila[0]: fila[4] for fila in migrations._MOTOR_SEED}
    assert topes == {"kimi": 0, "ada": 0}


@pytest.fixture
def motores_restaurados(client):
    antes = client.portal.call(sql, "SELECT `key`, max_tokens FROM motor WHERE `key` IN ('kimi', 'ada')", (), True)
    yield
    for clave, valor in antes:
        client.portal.call(sql, "UPDATE motor SET max_tokens = %s WHERE `key` = %s", (valor, clave))


@pytest.fixture
def sin_marca_motor(client, motores_restaurados):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
    yield
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                           (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _motor_max_tokens_al_catalogo_v1(cur)
        await conn.commit()


def _topes(client):
    return dict(client.portal.call(sql, "SELECT `key`, max_tokens FROM motor WHERE `key` IN ('kimi', 'ada')", (), True))


def test_primera_corrida_pone_kimi_y_ada_en_cero(client, sin_marca_motor):
    client.portal.call(sql, "UPDATE motor SET max_tokens = 8000 WHERE `key` IN ('kimi', 'ada')", ())
    client.portal.call(_migrar)
    assert _topes(client) == {"kimi": 0, "ada": 0}


def test_segunda_corrida_no_pisa_un_ajuste_posterior(client, sin_marca_motor):
    client.portal.call(_migrar)
    client.portal.call(sql, "UPDATE motor SET max_tokens = 4096 WHERE `key` = 'kimi'", ())
    client.portal.call(_migrar)
    assert _topes(client)["kimi"] == 4096


def test_patch_rechaza_max_tokens_negativo_sin_tocar_la_fila(client, motores_restaurados):
    antes = _topes(client)["kimi"]
    r = client.patch("/api/admin/motors/kimi", json={"max_tokens": -1},
                     headers=cabeceras(client, ADMIN, role="superadmin"))
    assert r.status_code == 422, r.text
    assert _topes(client)["kimi"] == antes


def test_post_rechaza_max_tokens_negativo(client):
    try:
        r = client.post("/api/admin/motors", json={
            "key": "test-tope-negativo", "provider_id": "deepseek", "model_id": "deepseek-v4-flash",
            "transport": "http_openai_compat", "max_tokens": -1,
        }, headers=cabeceras(client, ADMIN, role="superadmin"))
        assert r.status_code == 422, r.text
    finally:
        client.portal.call(sql, "DELETE FROM capability_motor WHERE motor_key = 'test-tope-negativo'", ())
        client.portal.call(sql, "DELETE FROM motor WHERE `key` = 'test-tope-negativo'", ())
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$PYTEST tests/test_motor_tope_catalogo.py -v -p no:cacheprovider`
Expected: ERROR de colección `ImportError: cannot import name 'MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1' from 'db.migrations'`. Para ver rojos los dos de endpoints contra el código viejo, antes de Step 3 comentar temporalmente la línea `from db.migrations import ...` y los tres tests que la usan, correr `-k "patch or post"` y ver `assert 200 == 422`; restaurar el archivo.

- [ ] **Step 3: Implementar**

In `backend/db/migrations.py`, in `_MOTOR_SEED`, replace:
```python
    ("kimi", "moonshot", "kimi-k3",   "http_openai_compat",  8000,       600,     True,      "audit_only",  True),
```
with:
```python
    # max_tokens 0 (D1 de Fernando, spec 2026-09-17 §1): sin tope propio, manda
    # model.max_output_tokens. Los 8000 cortaron el pipeline ef9b2d6e.
    ("kimi", "moonshot", "kimi-k3",   "http_openai_compat",  0,          600,     True,      "audit_only",  True),
```
and replace:
```python
    ("ada",  "zhipu",    "glm-5.3",   "http_openai_compat",  8000,       600,     True,      "audit_only",  True),
```
with:
```python
    ("ada",  "zhipu",    "glm-5.3",   "http_openai_compat",  0,          600,     True,      "audit_only",  True),
```

Immediately after `_raise_generate_execution_ceiling`, add:
```python
MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1 = "motor_max_tokens_al_catalogo_v1"
MOTORES_AL_TOPE_DEL_CATALOGO = ("kimi", "ada")


async def _motor_max_tokens_al_catalogo_v1(cur) -> None:
    """D1 de Fernando (spec 2026-09-17 §1 y §7 A): kimi y ada pasan a
    motor.max_tokens=0 -- el tope efectivo es model.max_output_tokens del
    catálogo (worker._limite_del_motor: 0 = sin tope propio). El seed usa
    INSERT IGNORE, así que la tupla nueva sólo alcanza a bases nuevas; esto
    corrige las existentes UNA vez (marcador): un ajuste posterior desde Admin
    no se pisa al arrancar."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s",
                      (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
    if await cur.fetchone() is not None:
        return
    marcas = ", ".join(["%s"] * len(MOTORES_AL_TOPE_DEL_CATALOGO))
    await cur.execute(f"UPDATE motor SET max_tokens = 0 WHERE `key` IN ({marcas})", MOTORES_AL_TOPE_DEL_CATALOGO)
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                      (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
```

In `run_migrations`, replace:
```python
            await _raise_generate_execution_ceiling(cur)
```
with:
```python
            await _raise_generate_execution_ceiling(cur)
            await _motor_max_tokens_al_catalogo_v1(cur)
```

In `backend/api/admin/motors.py`, in `CreateMotorRequest` replace:
```python
    max_tokens: int = 0
```
with:
```python
    # >= 0 (spec 2026-09-17 §7 A): un negativo llega al proveedor como tope
    # inválido. 0 = sin tope propio (manda model.max_output_tokens).
    max_tokens: int = Field(default=0, ge=0)
```
and in `UpdateMotorRequest` replace:
```python
    max_tokens: int | None = None
```
with:
```python
    max_tokens: int | None = Field(default=None, ge=0)
```

In `backend/tests/test_motor_migrations.py`, replace:
```python
    assert by_key["kimi"][2] == 8000
```
with:
```python
    # D1 de Fernando (spec 2026-09-17): sin tope propio, manda el catálogo.
    assert by_key["kimi"][2] == 0
    assert by_key["ada"][2] == 0
```

- [ ] **Step 4: Correr y ver verde**

Run: `$PYTEST tests/test_motor_tope_catalogo.py tests/test_motor_migrations.py tests/test_admin_motors_endpoints.py -v -p no:cacheprovider`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/db/migrations.py backend/api/admin/motors.py backend/tests/test_motor_tope_catalogo.py backend/tests/test_motor_migrations.py && \
git commit -m "feat(prevuelo): kimi y ada al tope del catálogo; max_tokens >= 0

D1 de Fernando (spec 2026-09-17 §1, §7 A) como migración de datos con marcador
(desvío DV-1 del plan: el PATCH a mano no llega a test ni a bases nuevas).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Ajuste `pipeline_confirmar_usd`

**Files:**
- Modify: `backend/ajustes.py:22-133`
- Modify: `backend/db/migrations.py` (nueva `_ajuste_confirmar_costo_v1`; `run_migrations`)
- Modify: `backend/tests/conftest.py:509-510`, `backend/tests/test_ajustes.py` (dos tests), `backend/tests/test_migracion_ajustes.py:37-41`
- Test: `backend/tests/test_ajuste_confirmar_costo.py` (nuevo)

**Interfaces:**
- Consumes: nada.
- Produces: `ajustes.CONFIRMAR_USD = "pipeline_confirmar_usd"` ∈ `ajustes.CLAVES`; `await ajustes.valor(ajustes.CONFIRMAR_USD) -> Decimal`; `ajustes.limites()["pipeline_confirmar_usd"] == {"min": "0", "max": "999999.99", "decimales": 2}`; `migrations.MIGRACION_AJUSTE_CONFIRMAR_USD_V1`, `migrations.VALOR_INICIAL_CONFIRMAR_USD = "0.50"`, `async migrations._ajuste_confirmar_costo_v1(cur)`.

- [ ] **Step 1: Escribir los tests que fallan**

Create `backend/tests/test_ajuste_confirmar_costo.py`:

```python
"""Umbral de confirmación de costo (spec 2026-09-17 §6.1): Decimal >= 0, 0 =
confirmar siempre, sembrado en 0.50 y editable en el panel de ajustes."""
from decimal import Decimal

import pytest

import ajustes
from db.migrations import MIGRACION_AJUSTE_CONFIRMAR_USD_V1, _ajuste_confirmar_costo_v1
from tests.identidades import cabeceras, sql


def test_acepta_montos_canonicos_con_hasta_dos_decimales():
    interpretar = ajustes.DEFINICIONES[ajustes.CONFIRMAR_USD].interpretar
    assert [interpretar(t) for t in ("0", "0.5", "0.50", "12", "999999.99")] == [
        Decimal("0"), Decimal("0.5"), Decimal("0.50"), Decimal("12"), Decimal("999999.99")]


@pytest.mark.parametrize("texto", ["", "-1", "0.505", "1e3", "00.5", ".5", " 0.5", "0,50", "1000000", "٠.٥", "NaN"])
def test_rechaza_lo_que_no_es_un_monto_canonico(texto):
    with pytest.raises(ajustes.ValorInvalido):
        ajustes.DEFINICIONES[ajustes.CONFIRMAR_USD].interpretar(texto)


def test_limites_publicados_del_umbral():
    assert ajustes.CONFIRMAR_USD in ajustes.CLAVES
    assert ajustes.limites()["pipeline_confirmar_usd"] == {"min": "0", "max": "999999.99", "decimales": 2}


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _ajuste_confirmar_costo_v1(cur)
        await conn.commit()


@pytest.fixture
def sin_marca_umbral(client, ajustes_en_db):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
    yield ajustes_en_db
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                           (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))


def test_sin_fila_la_migracion_siembra_0_50(client, sin_marca_umbral):
    sin_marca_umbral.quitar("pipeline_confirmar_usd")
    client.portal.call(_migrar)
    assert sin_marca_umbral.filas()["pipeline_confirmar_usd"] == "0.50"


def test_con_fila_guardada_la_migracion_no_la_pisa(client, sin_marca_umbral):
    sin_marca_umbral.poner(pipeline_confirmar_usd="2.00")
    client.portal.call(_migrar)
    assert sin_marca_umbral.filas()["pipeline_confirmar_usd"] == "2.00"


def test_el_put_de_admin_valida_y_guarda_el_umbral(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    encabezados = cabeceras(client, "ajuste-umbral", role="superadmin")
    malo = client.put("/api/admin/config", json=[{"key": "pipeline_confirmar_usd", "value": "abc"}], headers=encabezados)
    assert (malo.status_code, malo.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "pipeline_confirmar_usd"}})
    bueno = client.put("/api/admin/config", json=[{"key": "pipeline_confirmar_usd", "value": "1.25"}], headers=encabezados)
    assert bueno.status_code == 200
    assert client.portal.call(ajustes.valor, ajustes.CONFIRMAR_USD) == Decimal("1.25")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$PYTEST tests/test_ajuste_confirmar_costo.py -v -p no:cacheprovider`
Expected: ERROR de colección `ImportError: cannot import name 'MIGRACION_AJUSTE_CONFIRMAR_USD_V1' from 'db.migrations'` (16 tests: 1 + 11 parametrizados + 1 + 3; cuentan como 16 en los pisos).

- [ ] **Step 3: Implementar `ajustes.py`**

In `backend/ajustes.py`, replace:
```python
import asyncio
import logging
import math
import os
import sys
import time
import weakref
from dataclasses import dataclass
from typing import Awaitable, Callable
```
with:
```python
import asyncio
import logging
import math
import os
import re
import sys
import time
import weakref
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable
```

Replace:
```python
NOMBRE = "system_name"
CLAVES = (SESION, MAX_PIPELINES, RETENCION, IDIOMA, NOMBRE)
```
with:
```python
NOMBRE = "system_name"
# Umbral de confirmación de costo de un pipeline (spec 2026-09-17 §6.1): por
# encima de este costo máximo, o con un paso sin precio, la Mesa pide
# confirmación en ventana propia. 0 = confirmar siempre.
CONFIRMAR_USD = "pipeline_confirmar_usd"
CLAVES = (SESION, MAX_PIPELINES, RETENCION, IDIOMA, NOMBRE, CONFIRMAR_USD)
```

Replace:
```python
RETENCION_MAX = 365
```
with:
```python
RETENCION_MAX = 365
CONFIRMAR_USD_MAX = "999999.99"
CONFIRMAR_USD_DECIMALES = 2
# Canónico: sin ceros a la izquierda, punto decimal, hasta 2 decimales, ASCII.
_MONTO_USD = re.compile(r"(0|[1-9][0-9]{0,5})(\.[0-9]{1,2})?")
```

Replace:
```python
def _idioma(texto: str) -> str:
```
with:
```python
def _monto_usd(texto: str) -> Decimal:
    if not texto.isascii() or not _MONTO_USD.fullmatch(texto):
        raise ValorInvalido(texto)
    return Decimal(texto)


def _idioma(texto: str) -> str:
```

Replace:
```python
@dataclass(frozen=True)
class Definicion:
    interpretar: Callable[[str], int | str]
    limites: dict
```
with:
```python
@dataclass(frozen=True)
class Definicion:
    interpretar: Callable[[str], int | str | Decimal]
    limites: dict
```

Replace:
```python
    NOMBRE: Definicion(_nombre, {"max_largo": NOMBRE_MAX}),
}


def interpretar(clave: str, texto: str) -> int | str:
```
with:
```python
    NOMBRE: Definicion(_nombre, {"max_largo": NOMBRE_MAX}),
    # Los montos viajan como string: un float de JSON no es un monto exacto.
    CONFIRMAR_USD: Definicion(_monto_usd, {"min": "0", "max": CONFIRMAR_USD_MAX,
                                           "decimales": CONFIRMAR_USD_DECIMALES}),
}


def interpretar(clave: str, texto: str) -> int | str | Decimal:
```

Replace:
```python
async def valor(clave: str) -> int | str:
```
with:
```python
async def valor(clave: str) -> int | str | Decimal:
```

- [ ] **Step 4: Implementar la migración**

In `backend/db/migrations.py`, immediately after `_ajustes_que_mandan_v1`, add:
```python
MIGRACION_AJUSTE_CONFIRMAR_USD_V1 = "ajuste_pipeline_confirmar_usd_v1"
# Valor inicial decidido en el spec 2026-09-17 §6.1 (Fernando, GO autónomo).
VALOR_INICIAL_CONFIRMAR_USD = "0.50"


async def _ajuste_confirmar_costo_v1(cur) -> None:
    """Siembra el umbral de confirmación de costo UNA vez (marcador). INSERT
    IGNORE: una fila que ya exista (puesta a mano) se conserva."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s",
                      (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
    if await cur.fetchone() is not None:
        return
    await cur.execute(
        "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
        (ajustes.CONFIRMAR_USD, VALOR_INICIAL_CONFIRMAR_USD),
    )
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                      (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
```

In `run_migrations`, replace:
```python
            await _ajustes_que_mandan_v1(cur)
```
with:
```python
            await _ajustes_que_mandan_v1(cur)
            await _ajuste_confirmar_costo_v1(cur)
```

- [ ] **Step 5: Actualizar los tests existentes que fijan las cinco claves**

In `backend/tests/conftest.py`, replace:
```python
        validos={"session_timeout_min": "10080", "max_pipelines": "3",
                 "web_task_retention_days": "30", "lang_default": "es", "system_name": "Axioma"},
```
with:
```python
        validos={"session_timeout_min": "10080", "max_pipelines": "3",
                 "web_task_retention_days": "30", "lang_default": "es", "system_name": "Axioma",
                 "pipeline_confirmar_usd": "0.50"},
```

In `backend/tests/test_ajustes.py`, replace:
```python
        "lang_default": {"opciones": ["es", "en"]},
        "system_name": {"max_largo": 60},
    }
```
with:
```python
        "lang_default": {"opciones": ["es", "en"]},
        "system_name": {"max_largo": 60},
        "pipeline_confirmar_usd": {"min": "0", "max": "999999.99", "decimales": 2},
    }
```
and replace:
```python
        "session_timeout_min": 10080, "max_pipelines": 2, "web_task_retention_days": 30,
        "lang_default": "en", "system_name": "Axioma",
    }
```
with:
```python
        "session_timeout_min": 10080, "max_pipelines": 2, "web_task_retention_days": 30,
        "lang_default": "en", "system_name": "Axioma", "pipeline_confirmar_usd": Decimal("0.50"),
    }
```
and add `from decimal import Decimal` after `import asyncio` at the top of that file.

In `backend/tests/test_migracion_ajustes.py`, replace:
```python
    assert sin_marca.filas() == {
        "session_timeout_min": "10080", "max_pipelines": "3", "web_task_retention_days": "30",
        "lang_default": "es", "system_name": "Mi Sistema",
    }
```
with:
```python
    assert sin_marca.filas() == {
        "session_timeout_min": "10080", "max_pipelines": "3", "web_task_retention_days": "30",
        "lang_default": "es", "system_name": "Mi Sistema",
        # La fila del umbral la pone OTRA migración (_ajuste_confirmar_costo_v1)
        # y ésta no la toca: sigue con el valor que tenía.
        "pipeline_confirmar_usd": "0.50",
    }
```
and replace:
```python
    sin_marca.poner(session_timeout_min="60", max_pipelines="1", web_task_retention_days="7",
                    lang_default="en", system_name="Mi Sistema")
```
with:
```python
    sin_marca.poner(session_timeout_min="60", max_pipelines="1", web_task_retention_days="7",
                    lang_default="en", system_name="Mi Sistema", pipeline_confirmar_usd="0.50")
```

- [ ] **Step 6: Correr y ver verde**

Run: `$PYTEST tests/test_ajuste_confirmar_costo.py tests/test_ajustes.py tests/test_migracion_ajustes.py tests/test_config_admin_ajustes.py tests/test_apariencia.py tests/test_ajuste_sesion.py tests/test_ajuste_max_pipelines.py -v -p no:cacheprovider`
Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/ajustes.py backend/db/migrations.py backend/tests/conftest.py backend/tests/test_ajustes.py backend/tests/test_migracion_ajustes.py backend/tests/test_ajuste_confirmar_costo.py && \
git commit -m "feat(prevuelo): ajuste pipeline_confirmar_usd (umbral de confirmación de costo)

Spec 2026-09-17 §6.1: Decimal >= 0, sembrado 0.50 una vez, 0 = confirmar siempre.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Helper único de errores de Jacobs en get, results, resume y cancel

**Files:**
- Create: `backend/tests/jacobs_falso.py`
- Modify: `backend/api/pipelines.py` (helper nuevo; `get_pipeline_results`, `get_pipeline`, `resume_pipeline`, `cancel_pipeline`)
- Modify: `backend/tests/test_pipelines_http_pooling.py:69-91`
- Test: `backend/tests/test_pipelines_propagacion.py` (nuevo)

**Interfaces:**
- Consumes: `ajustes.CONFIRMAR_USD` (Task 4, lo usa `jacobs_falso.preparar`).
- Produces:
  - `api.pipelines.CODIGOS_DE_JACOBS: frozenset[str]` = `{"prevuelo_rechazado", "costo_supera_lo_aceptado", "reasignacion_invalida", "prevuelo_no_disponible", "estado_no_continuable"}`
  - `api.pipelines.MOTIVO_MAX = 200`, `api.pipelines.DETALLE_MAX = 300`
  - `api.pipelines._violaciones_redactadas(violaciones) -> list[dict]`
  - `api.pipelines._rechazo_de_jacobs(status_code: int, cuerpo, texto: str) -> HTTPException`
  - `api.pipelines._json_de_jacobs(r: httpx.Response) -> dict` (lanza HTTPException)
  - `tests.jacobs_falso`: `URL`, `respuesta(status, cuerpo=None, texto=None) -> httpx.Response`, `veredicto(...) -> dict`, `violacion(...) -> dict`, `paso_costo(...) -> dict`, `class JacobsFalso(rutas)` con `.llamadas`, `.cuerpos(metodo, ruta)`, `preparar(monkeypatch, falso, umbral="0.50", maximo=3, cupo_libre=True, nombre="nombre del pipeline") -> SimpleNamespace(admitidos, publicados, duenios)`

- [ ] **Step 1: Escribir el Jacobs falso**

Create `backend/tests/jacobs_falso.py`:

```python
"""Jacobs falso para los tests de api/pipelines.py (spec 2026-09-17).

Respeta EXACTAMENTE el contrato del plan (sección "Contrato con Jacobs", que
implementa el plan J en el repo jax): errores con el envoltorio de FastAPI
{"detail": {...}} y montos como string decimal. Si el contrato cambia, se
cambia acá y en api/pipelines.py, en ningún otro lado.

No es un test (no empieza con test_): pytest no lo colecta."""
from decimal import Decimal
from types import SimpleNamespace

import httpx

URL = "http://jacobs.test/jacobs"


def respuesta(status: int, cuerpo=None, texto: str | None = None) -> httpx.Response:
    if texto is not None:
        return httpx.Response(status, text=texto)
    return httpx.Response(status, json=cuerpo)


def paso_costo(paso: int = 0, faceta: str = "jekyll", usd: str | None = "0.10", motivo: str | None = None) -> dict:
    return {"paso": paso, "faceta": faceta, "modelo": "modelo-de-prueba", "llamadas_max": 1,
            "tokens_in_max": 100, "tokens_out_max": 1000, "usd_max": usd, "motivo": motivo}


def violacion(paso: int = 4, faceta: str = "kimi", regla: str = "tope_insuficiente",
              detalle: str = "tope 8000 < minimo 16384") -> dict:
    return {"paso": paso, "faceta": faceta, "regla": regla, "detalle": detalle}


def veredicto(ok: bool = True, costo: str = "0.10", violaciones=(), pasos_costo=None, sondeadas=()) -> dict:
    return {
        "ok": ok,
        "violaciones": list(violaciones),
        "costo_max_usd": costo,
        "pasos_costo": list(pasos_costo) if pasos_costo is not None else [paso_costo(usd=costo)],
        "sondeadas": list(sondeadas),
    }


class JacobsFalso:
    """rutas: {("POST", "/preflight"): httpx.Response | callable(cuerpo) -> httpx.Response}.
    Una ruta no declarada lanza AssertionError (la Mesa la convierte en 502:
    el test que llegó adonde no esperaba falla por status)."""

    def __init__(self, rutas=None):
        self.rutas = dict(rutas or {})
        self.llamadas: list[tuple[str, str, dict | None]] = []

    def _responder(self, metodo: str, url: str, cuerpo):
        assert url.startswith(URL), url
        ruta = url[len(URL):]
        self.llamadas.append((metodo, ruta, cuerpo))
        r = self.rutas.get((metodo, ruta))
        if r is None:
            raise AssertionError(f"Jacobs falso: ruta no declarada {metodo} {ruta}")
        return r(cuerpo) if callable(r) else r

    async def post(self, url, json=None, timeout=None):
        return self._responder("POST", url, json)

    async def get(self, url, timeout=None):
        return self._responder("GET", url, None)

    def cuerpos(self, metodo: str, ruta: str) -> list:
        return [c for m, r, c in self.llamadas if (m, r) == (metodo, ruta)]


def preparar(monkeypatch, falso: JacobsFalso, umbral: str = "0.50", maximo: int = 3,
             cupo_libre: bool = True, nombre: str = "nombre del pipeline") -> SimpleNamespace:
    """Instala el Jacobs falso y aísla todo lo que no es Jacobs: ajustes,
    cupo, dueño, recurso y eventos de WS quedan en memoria y se registran."""
    import api.pipelines as mod

    registro = SimpleNamespace(admitidos=[], publicados=[], duenios=[])
    valores = {mod.ajustes.MAX_PIPELINES: maximo, mod.ajustes.CONFIRMAR_USD: Decimal(umbral)}

    async def cliente():
        return falso

    async def valor(clave):
        return valores[clave]

    async def cupo(_tenant, _limite):
        return cupo_libre

    async def duenio(pipeline_id, _user):
        registro.duenios.append(pipeline_id)
        return nombre

    async def registrar_duenio(pipeline_id, _tenant_id, _user_id):
        registro.duenios.append(pipeline_id)

    async def admitir(tenant_id, pipeline_id):
        registro.admitidos.append((tenant_id, pipeline_id))

    async def upsert(pipeline, _tenant_id, _user_id):
        registro.publicados.append(("pipeline_step_changed", pipeline.pipeline_id, {}))

    async def continuar(pipeline, _tenant_id, _user_id, continuacion):
        registro.publicados.append(("pipeline_continued", pipeline.pipeline_id, continuacion))

    monkeypatch.setattr(mod, "get_http_client", cliente)
    monkeypatch.setattr(mod, "JACOBS_URL", URL)
    monkeypatch.setattr(mod.ajustes, "valor", valor)
    monkeypatch.setattr(mod.resource_manager, "can_start_pipeline", cupo)
    monkeypatch.setattr(mod.resource_manager, "admit_pipeline", admitir)
    monkeypatch.setattr(mod, "_require_pipeline_owner", duenio)
    monkeypatch.setattr(mod, "_record_pipeline_owner", registrar_duenio)
    monkeypatch.setattr(mod.engine_state, "upsert_pipeline", upsert)
    monkeypatch.setattr(mod.engine_state, "continuar_pipeline", continuar, raising=False)
    return registro
```

- [ ] **Step 2: Escribir los tests que fallan**

Create `backend/tests/test_pipelines_propagacion.py`:

```python
"""Propagación de los rechazos de Jacobs (spec 2026-09-17 §6.1): get, results,
resume y cancel devolvían 200 con el cuerpo de error de Jacobs, así que el
aviso de rechazo nunca aparecía; y un cuerpo no-JSON con status >= 400 se
reportaba como jacobs_no_responde. Puros: Jacobs falso y sin DB."""
import asyncio

import pytest
from fastapi import HTTPException

from api import pipelines as mod
from auth.models import AuthUser
from tests.jacobs_falso import JacobsFalso, preparar, respuesta, violacion

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
PID = "00000000-0000-0000-0000-00000000abcd"
ENDPOINTS = [
    ("get_pipeline", "GET", ""),
    ("get_pipeline_results", "GET", "/results"),
    ("resume_pipeline", "POST", "/resume"),
    ("cancel_pipeline", "POST", "/cancel"),
]


def _correr(corutina):
    try:
        return asyncio.run(corutina)
    except HTTPException as exc:
        return exc


@pytest.mark.parametrize("nombre, metodo, sufijo", ENDPOINTS)
def test_un_4xx_de_jacobs_sale_con_el_mismo_status_y_codigo(monkeypatch, nombre, metodo, sufijo):
    falso = JacobsFalso({(metodo, f"/pipeline/{PID}{sufijo}"): respuesta(409, {"detail": "ya finalizado"})})
    preparar(monkeypatch, falso)
    resultado = _correr(getattr(mod, nombre)(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert (resultado.status_code, resultado.detail) == (
        409, {"code": "jacobs_rechazo", "status": 409, "motivo": "ya finalizado"})


@pytest.mark.parametrize("nombre, metodo, sufijo", ENDPOINTS)
def test_un_error_no_json_de_jacobs_es_rechazo_con_texto_redactado(monkeypatch, nombre, metodo, sufijo):
    texto = "Internal Server Error api_key=sk-FAKE-propagacion fin"
    falso = JacobsFalso({(metodo, f"/pipeline/{PID}{sufijo}"): respuesta(500, texto=texto)})
    preparar(monkeypatch, falso)
    resultado = _correr(getattr(mod, nombre)(pipeline_id=PID, user=USUARIO))
    assert isinstance(resultado, HTTPException), resultado
    assert (resultado.status_code, resultado.detail["code"], resultado.detail["status"]) == (500, "jacobs_rechazo", 500)
    assert "sk-FAKE-propagacion" not in resultado.detail["motivo"]
    assert resultado.detail["motivo"].startswith("Internal Server Error")


def test_un_codigo_propio_de_jacobs_pasa_con_sus_datos_declarados_y_redactados():
    cuerpo = {"detail": {
        "code": "prevuelo_rechazado", "costo_max_usd": "1.00", "pasos_costo": [],
        "violaciones": [violacion(detalle="sonda: api_key=sk-FAKE-violacion fin")], "otro": "no pasa",
    }}
    exc = mod._rechazo_de_jacobs(422, cuerpo, "")
    assert exc.status_code == 422
    assert exc.detail == {
        "code": "prevuelo_rechazado", "costo_max_usd": "1.00", "pasos_costo": [],
        "violaciones": [{"paso": 4, "faceta": "kimi", "regla": "tope_insuficiente",
                         "detalle": "sonda: api_key=*** fin"}],
    }


def test_un_2xx_que_no_es_un_objeto_json_es_jacobs_no_responde():
    exc = _correr(_json_async(respuesta(200, texto="<html>proxy</html>")))
    assert isinstance(exc, HTTPException)
    assert (exc.status_code, exc.detail) == (502, {"code": "jacobs_no_responde", "motivo": "<html>proxy</html>"})


async def _json_async(r):
    return mod._json_de_jacobs(r)
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `$PYTEST tests/test_pipelines_propagacion.py -v -p no:cacheprovider`
Expected (10 tests):
- los 4 `test_un_4xx_...`: `AssertionError: {'ya finalizado'...}` — `assert isinstance({'detail': 'ya finalizado'}, HTTPException)` (hoy devuelve el cuerpo con 200);
- los 4 `test_un_error_no_json_...`: `AssertionError: assert (502, 'jacobs_no_responde', ...)` → KeyError `'status'` o `assert (502, ...) == (500, 'jacobs_rechazo', 500)`;
- `test_un_codigo_propio...` y `test_un_2xx...`: `AttributeError: module 'api.pipelines' has no attribute '_rechazo_de_jacobs'` / `'_json_de_jacobs'`.

- [ ] **Step 4: Implementar el helper y aplicarlo**

In `backend/api/pipelines.py`, after `INVOKED_BY_PLATAFORMA = "plataforma"`, add:

```python
# --- Respuestas de Jacobs (spec 2026-09-17 §6.1) ---------------------------
# Hasta hoy get/results/resume/cancel devolvían 200 con el cuerpo de error de
# Jacobs y el aviso de rechazo nunca aparecía. Un solo lugar decide qué hacer
# con cualquier respuesta: dict si salió bien; si no, la HTTPException con el
# MISMO status.
#
# Códigos propios que Jacobs devuelve con datos que la Mesa tiene que mostrar
# (desvío DV-6 del plan): pasan con su status y SÓLO los campos declarados.
# Cualquier otro rechazo es jacobs_rechazo con el motivo recortado y redactado.
CODIGOS_DE_JACOBS = frozenset({
    "prevuelo_rechazado", "costo_supera_lo_aceptado", "reasignacion_invalida",
    "prevuelo_no_disponible", "estado_no_continuable",
})
_CAMPOS_DE_CODIGO = ("violaciones", "costo_max_usd", "pasos_costo", "costo_max_aceptado_usd", "status_actual")
MOTIVO_MAX = 200
DETALLE_MAX = 300


def _violaciones_redactadas(violaciones) -> list[dict]:
    """Sólo los cuatro campos del contrato; el detalle puede traer el error de
    una sonda y se redacta antes de salir hacia el navegador."""
    if not isinstance(violaciones, list):
        return []
    return [
        {"paso": v.get("paso"), "faceta": v.get("faceta"), "regla": v.get("regla"),
         "detalle": recortar_redactado(str(v.get("detalle") or ""), DETALLE_MAX)}
        for v in violaciones if isinstance(v, dict)
    ]


def _rechazo_de_jacobs(status_code: int, cuerpo, texto: str) -> HTTPException:
    detalle_de_jacobs = cuerpo.get("detail") if isinstance(cuerpo, dict) else None
    if isinstance(detalle_de_jacobs, dict) and detalle_de_jacobs.get("code") in CODIGOS_DE_JACOBS:
        detalle = {"code": detalle_de_jacobs["code"]}
        for campo in _CAMPOS_DE_CODIGO:
            if campo in detalle_de_jacobs:
                detalle[campo] = detalle_de_jacobs[campo]
        if "violaciones" in detalle:
            detalle["violaciones"] = _violaciones_redactadas(detalle["violaciones"])
        return HTTPException(status_code=status_code, detail=detalle)
    if isinstance(cuerpo, dict):
        crudo = str(cuerpo.get("detail", ""))
    elif cuerpo is None:
        crudo = texto
    else:
        crudo = str(cuerpo)
    detalle = {"code": "jacobs_rechazo", "status": status_code, "motivo": recortar_redactado(crudo, MOTIVO_MAX)}
    return HTTPException(status_code=status_code, detail=detalle)


def _json_de_jacobs(r) -> dict:
    """La respuesta de Jacobs como dict. status >= 400 -> _rechazo_de_jacobs
    con el mismo status (también si el cuerpo no es JSON). Un 2xx/3xx que no
    es un objeto JSON -> 502 jacobs_no_responde con el texto redactado."""
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = None  # no-JSON: se decide abajo por status, nunca se traga
    if r.status_code >= 400:
        raise _rechazo_de_jacobs(r.status_code, cuerpo, r.text)
    if not isinstance(cuerpo, dict):
        detalle = {"code": "jacobs_no_responde", "motivo": recortar_redactado(r.text, MOTIVO_MAX)}
        raise HTTPException(status_code=502, detail=detalle)
    return cuerpo
```

Replace the body of `get_pipeline_results` try-block:
```python
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results", timeout=10.0)
        return r.json()
    except Exception as e:
```
with:
```python
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}/results", timeout=10.0)
        return _json_de_jacobs(r)
    except HTTPException:
        raise
    except Exception as e:
```

Replace in `get_pipeline`:
```python
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}", timeout=5.0)
        return r.json()
    except Exception as e:
```
with:
```python
    try:
        r = await client.get(f"{JACOBS_URL}/pipeline/{pipeline_id}", timeout=5.0)
        return _json_de_jacobs(r)
    except HTTPException:
        raise
    except Exception as e:
```

Replace in `resume_pipeline`:
```python
            timeout=10.0,
        )
        return r.json()
    except Exception as e:
```
with:
```python
            timeout=10.0,
        )
        return _json_de_jacobs(r)
    except HTTPException:
        raise
    except Exception as e:
```

Replace in `cancel_pipeline`:
```python
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel", timeout=10.0)
        if r.status_code == 200:
            engine_state.remove_pipeline(pipeline_id)
            await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
        return r.json()
    except Exception as e:
```
with:
```python
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/cancel", timeout=10.0)
        data = _json_de_jacobs(r)
        engine_state.remove_pipeline(pipeline_id)
        await resource_manager.release_pipeline(user.tenant_id, pipeline_id)
        return data
    except HTTPException:
        raise
    except Exception as e:
```

- [ ] **Step 5: El test de pooling deja de llamar al Jacobs de producción**

In `backend/tests/test_pipelines_http_pooling.py`, replace:
```python
def test_pipeline_endpoints_do_not_create_a_new_client_per_request(client, owned_pipeline_id):
```
with:
```python
def test_pipeline_endpoints_do_not_create_a_new_client_per_request(client, owned_pipeline_id, monkeypatch):
```
immediately after that function's docstring (before `with _ClientInstantiationCounter() as counter:`), add:
```python
    # 2026-09-17: con la propagación de errores, el 404 del Jacobs REAL (que
    # no conoce esta fila de jax_memory_test) ya no se devuelve como 200. Y un
    # test no debe llamar a :7777: puerto 9 (discard) rechaza la conexión.
    import api.pipelines as pipelines_mod
    monkeypatch.setattr(pipelines_mod, "JACOBS_URL", "http://127.0.0.1:9/jacobs")
```
and replace both:
```python
        assert resp.status_code in (200, 502)
```
with:
```python
        assert resp.status_code == 502
```

- [ ] **Step 6: Correr y ver verde**

Run: `$PYTEST tests/test_pipelines_propagacion.py tests/test_pipelines_http_pooling.py tests/test_pipeline_ownership.py tests/test_mesa_codigos.py tests/test_pipelines_identity_injection.py -v -p no:cacheprovider`
Expected: todos PASS. (`test_pipelines_identity_injection::test_resume_pipeline_inyecta_identidad_real` sigue verde: su `_R` no tiene `status_code`, el endpoint responde 502, pero el test sólo afirma el cuerpo capturado ANTES de la respuesta.)

- [ ] **Step 7: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/api/pipelines.py backend/tests/jacobs_falso.py backend/tests/test_pipelines_propagacion.py backend/tests/test_pipelines_http_pooling.py && \
git commit -m "fix(pipelines): los rechazos de Jacobs salen con su status en get, results, resume y cancel

Spec 2026-09-17 §6.1. Un helper único (_json_de_jacobs) y un Jacobs falso que
respeta el contrato del plan. El test de pooling deja de llamar a :7777.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `POST /api/pipelines/preflight` y creación con pre-vuelo y confirmación

**Files:**
- Modify: `backend/api/pipelines.py` (imports; `_monto`, `_prevuelo_no_disponible`, `_evaluar_veredicto`, `_prevuelo`, `_exigir_consentimiento`, `PedidoDePrevuelo`, `preflight_pipeline`; `create_pipeline`)
- Modify: `backend/tests/test_mesa_codigos.py:201-203`, `backend/tests/test_pipelines_identity_injection.py:20-58`, `backend/tests/test_ajuste_max_pipelines.py:33-35`, `backend/tests/test_pipeline_ownership.py:36-48,179-193`
- Test: `backend/tests/test_pipelines_prevuelo.py` (nuevo)

**Interfaces:**
- Consumes: `_json_de_jacobs`, `_violaciones_redactadas`, `MOTIVO_MAX` (Task 5); `ajustes.CONFIRMAR_USD` (Task 4); `tests.jacobs_falso` (Task 5).
- Produces:
  - `api.pipelines._monto(valor) -> Decimal` (ValueError si no es un monto ≥ 0 finito)
  - `api.pipelines._prevuelo_no_disponible() -> HTTPException` (502 `prevuelo_no_disponible`)
  - `api.pipelines._evaluar_veredicto(crudo, umbral: Decimal) -> dict` con claves `ok, violaciones, costo_max_usd (str), pasos_costo, sondeadas, umbral_usd (str), requiere_confirmacion (bool)`
  - `async api.pipelines._prevuelo(client, steps: list, user: AuthUser, umbral: Decimal) -> dict`
  - `api.pipelines._exigir_consentimiento(veredicto: dict, confirmado: Decimal | None) -> None` (422 `prevuelo_rechazado` / 409 `confirmacion_de_costo`)
  - `class api.pipelines.PedidoDePrevuelo(BaseModel): steps: list[dict]`
  - `async api.pipelines.preflight_pipeline(pedido: PedidoDePrevuelo, user) -> dict` en `POST /api/pipelines/preflight`
  - `POST /api/pipelines` exige `steps` (422 `pasos_requeridos`), acepta `costo_confirmado_usd` (422 `costo_confirmado_invalido`), manda `costo_max_aceptado_usd` a Jacobs.

- [ ] **Step 1: Escribir los tests que fallan**

Create `backend/tests/test_pipelines_prevuelo.py`:

```python
"""Pre-vuelo antes de gastar (spec 2026-09-17 §6.1): la Mesa pregunta a Jacobs
si se puede y cuánto costaría como máximo, pide consentimiento por encima del
umbral o con un paso sin precio, y la condición la hace cumplir quien gasta
(costo_max_aceptado_usd). Puros salvo el último (HTTP real con auth y ajuste)."""
import asyncio

import pytest
from fastapi import HTTPException

from api import pipelines as mod
from auth.models import AuthUser
from tests.identidades import cabeceras
from tests.jacobs_falso import JacobsFalso, paso_costo, preparar, respuesta, veredicto, violacion

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
PASOS = [{"facet": "kimi", "capability": "generate", "prompt": "x", "motor": "kimi"}]


def _correr(corutina):
    try:
        return asyncio.run(corutina)
    except HTTPException as exc:
        return exc


class _Pedido:
    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    async def json(self):
        return self._cuerpo


def _crear(cuerpo):
    return _correr(mod.create_pipeline(request=_Pedido(cuerpo), user=USUARIO))


def _prevuelo(falso_veredicto, status=200):
    return {("POST", "/preflight"): respuesta(status, falso_veredicto)}


# ---------------------------------------------------------------- /preflight

def test_preflight_reenvia_identidad_y_pasos_y_agrega_umbral(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.10")))
    preparar(monkeypatch, falso)
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert falso.cuerpos("POST", "/preflight") == [
        {"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1", "steps": PASOS}]
    assert (r["ok"], r["costo_max_usd"], r["umbral_usd"], r["requiere_confirmacion"]) == (True, "0.10", "0.50", False)


def test_preflight_pide_confirmacion_por_encima_del_umbral(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(costo="0.60"))))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is True


def test_preflight_pide_confirmacion_con_un_paso_sin_precio(monkeypatch):
    v = veredicto(costo="0.10", pasos_costo=[paso_costo(usd="0.10"), paso_costo(paso=1, usd=None, motivo="sin_precio")])
    preparar(monkeypatch, JacobsFalso(_prevuelo(v)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert r["requiere_confirmacion"] is True


def test_preflight_sin_ok_es_prevuelo_no_disponible(monkeypatch):
    malo = veredicto()
    del malo["ok"]
    preparar(monkeypatch, JacobsFalso(_prevuelo(malo)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert isinstance(r, HTTPException)
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_con_pasos_de_costo_ilegibles_es_prevuelo_no_disponible(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo(veredicto(pasos_costo=[paso_costo(usd="abc")]))))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (502, {"code": "prevuelo_no_disponible"})


def test_preflight_503_de_jacobs_pasa_con_su_codigo(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_prevuelo({"detail": {"code": "prevuelo_no_disponible"}}, status=503)))
    r = _correr(mod.preflight_pipeline(pedido=mod.PedidoDePrevuelo(steps=PASOS), user=USUARIO))
    assert (r.status_code, r.detail) == (503, {"code": "prevuelo_no_disponible"})


# ---------------------------------------------------------------- creación

def test_crear_sin_pasos_es_422_y_no_llama_a_jacobs(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "objective": "x"})
    assert (r.status_code, r.detail) == (422, {"code": "pasos_requeridos"})
    assert falso.llamadas == []


def test_crear_con_violaciones_es_422_y_no_crea(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(ok=False, violaciones=[violacion()])))
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert r.status_code == 422
    assert r.detail["code"] == "prevuelo_rechazado"
    assert r.detail["violaciones"][0]["regla"] == "tope_insuficiente"
    assert falso.cuerpos("POST", "/pipeline") == []


def test_crear_caro_sin_confirmar_es_409_con_el_costo_y_el_umbral(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.60")))
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert r.status_code == 409
    assert (r.detail["code"], r.detail["costo_max_usd"], r.detail["umbral_usd"]) == ("confirmacion_de_costo", "0.60", "0.50")
    assert falso.cuerpos("POST", "/pipeline") == []


def test_crear_con_una_confirmacion_menor_al_costo_es_409(monkeypatch):
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.60")))
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.59"})
    assert (r.status_code, r.detail["code"]) == (409, "confirmacion_de_costo")
    assert falso.cuerpos("POST", "/pipeline") == []


def test_crear_confirmado_manda_el_costo_aceptado_a_jacobs(monkeypatch):
    pid = "11111111-1111-1111-1111-111111111111"
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.60")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": pid, "costo_max_usd": "0.60"})})
    registro = preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.60"})
    (cuerpo,) = falso.cuerpos("POST", "/pipeline")
    assert cuerpo["costo_max_aceptado_usd"] == "0.60"
    assert "costo_confirmado_usd" not in cuerpo
    assert (cuerpo["invoked_by"], cuerpo["user_id"], cuerpo["tenant_id"]) == ("plataforma", "5", "1")
    assert r["pipeline_id"] == pid
    assert registro.admitidos == [("1", pid)]


def test_crear_sin_confirmacion_requerida_acepta_hasta_el_umbral(monkeypatch):
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.10")),
                         ("POST", "/pipeline"): respuesta(200, {"pipeline_id": None})})
    preparar(monkeypatch, falso)
    _crear({"name": "x", "steps": PASOS})
    (cuerpo,) = falso.cuerpos("POST", "/pipeline")
    assert cuerpo["costo_max_aceptado_usd"] == "0.50"


def test_crear_costo_supera_lo_aceptado_pasa_con_sus_datos(monkeypatch):
    rechazo = {"detail": {"code": "costo_supera_lo_aceptado", "costo_max_usd": "0.70", "costo_max_aceptado_usd": "0.60"}}
    falso = JacobsFalso({**_prevuelo(veredicto(costo="0.60")), ("POST", "/pipeline"): respuesta(409, rechazo)})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "0.60"})
    assert (r.status_code, r.detail) == (409, rechazo["detail"])


def test_crear_con_error_no_json_de_jacobs_es_rechazo_con_su_status(monkeypatch):
    falso = JacobsFalso({**_prevuelo(veredicto()), ("POST", "/pipeline"): respuesta(500, texto="boom")})
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS})
    assert (r.status_code, r.detail) == (500, {"code": "jacobs_rechazo", "status": 500, "motivo": "boom"})


def test_crear_con_costo_confirmado_invalido_es_422(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso)
    r = _crear({"name": "x", "steps": PASOS, "costo_confirmado_usd": "-1"})
    assert (r.status_code, r.detail) == (422, {"code": "costo_confirmado_invalido"})
    assert falso.llamadas == []


# ---------------------------------------------------------------- HTTP real

def test_preflight_por_http_usa_el_umbral_del_ajuste(client, ajustes_en_db, monkeypatch):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "pipeline_confirmar_usd": "0.05"})
    falso = JacobsFalso(_prevuelo(veredicto(costo="0.10")))

    async def cliente():
        return falso

    monkeypatch.setattr(mod, "get_http_client", cliente)
    monkeypatch.setattr(mod, "JACOBS_URL", "http://jacobs.test/jacobs")
    r = client.post("/api/pipelines/preflight", json={"steps": PASOS}, headers=cabeceras(client, "prevuelo-http"))
    assert r.status_code == 200, r.text
    assert (r.json()["umbral_usd"], r.json()["requiere_confirmacion"]) == ("0.05", True)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$PYTEST tests/test_pipelines_prevuelo.py -v -p no:cacheprovider`
Expected (16 tests): los de `/preflight` con `AttributeError: module 'api.pipelines' has no attribute 'PedidoDePrevuelo'`; `test_crear_sin_pasos...` con `assert (502, {...jacobs_no_responde...}) == (422, {'code': 'pasos_requeridos'})`; los demás de creación con `AttributeError: 'dict' object has no attribute 'status_code'` o `assert (502, ...) == ...`; el HTTP con `assert 405 == 200`.

- [ ] **Step 3: Implementar**

In `backend/api/pipelines.py`, replace:
```python
import os
import time
import uuid
import ajustes
from fastapi import APIRouter, Depends, HTTPException, Request, status
```
with:
```python
import os
import time
import uuid
from decimal import Decimal, InvalidOperation

import ajustes
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
```

After `_json_de_jacobs` (Task 5), add:

```python
# --- Pre-vuelo (spec 2026-09-17 §6.1) --------------------------------------
def _monto(valor) -> Decimal:
    """Monto de Jacobs o del cliente: string decimal o número JSON, finito y >= 0."""
    if isinstance(valor, bool) or not isinstance(valor, (str, int, float)):
        raise ValueError(valor)
    try:
        monto = Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(valor) from exc
    if not monto.is_finite() or monto < 0:
        raise ValueError(valor)
    return monto


def _prevuelo_no_disponible() -> HTTPException:
    detalle = {"code": "prevuelo_no_disponible"}
    return HTTPException(status_code=502, detail=detalle)


def _evaluar_veredicto(crudo, umbral: Decimal) -> dict:
    """Fail-closed: un veredicto sin `ok` booleano, sin costo legible o con
    pasos de costo mal formados NO es un pre-vuelo aprobado. Un paso con
    usd_max null es "no acotado" (sin precio): siempre pide confirmación."""
    try:
        ok = crudo["ok"]
        costo = _monto(crudo["costo_max_usd"])
        pasos = crudo["pasos_costo"]
        violaciones = crudo["violaciones"]
        if not isinstance(ok, bool) or not isinstance(pasos, list) or not isinstance(violaciones, list):
            raise ValueError("forma del veredicto")
        no_acotado = False
        for paso in pasos:
            if paso["usd_max"] is None:
                no_acotado = True
            else:
                _monto(paso["usd_max"])
    except (KeyError, TypeError, ValueError):
        raise _prevuelo_no_disponible() from None
    return {
        "ok": ok,
        "violaciones": _violaciones_redactadas(violaciones),
        "costo_max_usd": str(costo),
        "pasos_costo": pasos,
        "sondeadas": crudo.get("sondeadas", []),
        "umbral_usd": str(umbral),
        "requiere_confirmacion": costo > umbral or no_acotado,
    }


async def _prevuelo(client, steps: list, user: AuthUser, umbral: Decimal) -> dict:
    r = await client.post(
        f"{JACOBS_URL}/preflight",
        json={"invoked_by": INVOKED_BY_PLATAFORMA, "user_id": user.user_id,
              "tenant_id": user.tenant_id, "steps": steps},
        # Puede sondear facetas (en paralelo, con timeout propio en Jacobs).
        timeout=JACOBS_PIPELINE_TIMEOUT,
    )
    return _evaluar_veredicto(_json_de_jacobs(r), umbral)


def _exigir_consentimiento(veredicto: dict, confirmado: Decimal | None) -> None:
    """422 si el pre-vuelo rechazó; 409 si hace falta confirmar y no se
    confirmó al menos el costo máximo. Nada de esto crea ni gasta."""
    if not veredicto["ok"]:
        detalle = {"code": "prevuelo_rechazado", "violaciones": veredicto["violaciones"],
                   "costo_max_usd": veredicto["costo_max_usd"], "pasos_costo": veredicto["pasos_costo"]}
        raise HTTPException(status_code=422, detail=detalle)
    if veredicto["requiere_confirmacion"] and (
            confirmado is None or confirmado < Decimal(veredicto["costo_max_usd"])):
        detalle = {"code": "confirmacion_de_costo", "costo_max_usd": veredicto["costo_max_usd"],
                   "pasos_costo": veredicto["pasos_costo"], "umbral_usd": veredicto["umbral_usd"]}
        raise HTTPException(status_code=409, detail=detalle)


class PedidoDePrevuelo(BaseModel):
    steps: list[dict] = Field(min_length=1)
```

Immediately after the `list_pipelines` endpoint (before `@router.post("")`), add:

```python
@router.post("/preflight")
async def preflight_pipeline(pedido: PedidoDePrevuelo, user: AuthUser = Depends(get_current_user)):
    # El ajuste se lee FUERA del try: un ajuste ilegible es 503 ajuste_ilegible
    # (handler de main.py), no un 502 de Jacobs.
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    client = await get_http_client()
    try:
        return await _prevuelo(client, pedido.steps, user, umbral)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})
```

Replace the whole `create_pipeline` function (from `@router.post("")` through its final `raise HTTPException(status_code=502, ...)`) with:

```python
@router.post("")
async def create_pipeline(request: Request, user: AuthUser = Depends(get_current_user)):
    limite = await ajustes.valor(ajustes.MAX_PIPELINES)
    if not await resource_manager.can_start_pipeline(user.tenant_id, limite):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "limite_de_pipelines", "max": limite},
        )
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    body = await request.json()
    steps = body.get("steps") if isinstance(body, dict) else None
    # Sin pasos no hay pre-vuelo ni costo que confirmar (desvío DV-8 del plan).
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status_code=422, detail={"code": "pasos_requeridos"})
    try:
        crudo = body.pop("costo_confirmado_usd", None)
        confirmado = None if crudo is None else _monto(crudo)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "costo_confirmado_invalido"}) from None
    body["user_id"] = user.user_id
    body["tenant_id"] = user.tenant_id
    body["invoked_by"] = INVOKED_BY_PLATAFORMA
    client = await get_http_client()
    try:
        veredicto = await _prevuelo(client, steps, user, umbral)
        _exigir_consentimiento(veredicto, confirmado)
        # Siempre (desvío DV-7): el confirmado, o el umbral como consentimiento
        # previo del admin. Jacobs responde 409 costo_supera_lo_aceptado sin
        # crear si su pre-vuelo interno da más.
        body["costo_max_aceptado_usd"] = str(confirmado if confirmado is not None else umbral)
        r = await client.post(f"{JACOBS_URL}/pipeline", json=body, timeout=JACOBS_PIPELINE_TIMEOUT)
        data = _json_de_jacobs(r)
        pipeline_id = data.get("pipeline_id")
        if pipeline_id:
            # Antes de admitir el recurso o publicar el evento de WS
            # (que ya revela pipeline_id al dueño) — así un fallo acá
            # aborta limpio, sin slot de tenant huérfano ni owner file
            # faltante para un id que el cliente ya recibió.
            await _record_pipeline_owner(pipeline_id, user.tenant_id, user.user_id)
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})
```

- [ ] **Step 4: Actualizar los tests existentes que creaban sin pasos o con un Jacobs sin pre-vuelo**

In `backend/tests/test_mesa_codigos.py`, replace:
```python
class _Request:
    async def json(self):
        return {"objective": "x"}
```
with:
```python
class _Request:
    # Spec 2026-09-17: la creación exige pasos (pre-vuelo antes de gastar).
    async def json(self):
        return {"objective": "x", "steps": [{"facet": "thot", "capability": "critique", "prompt": "x"}]}
```

In `backend/tests/test_ajuste_max_pipelines.py`, replace:
```python
class _FakeRequest:
    async def json(self):
        return {"name": "carga-de-cupo"}
```
with:
```python
class _FakeRequest:
    async def json(self):
        return {"name": "carga-de-cupo", "steps": [{"facet": "thot", "capability": "critique", "prompt": "x"}]}
```
and replace:
```python
    def __init__(self, json_data, status_code):
        self._json_data = json_data
        self.status_code = status_code
```
with:
```python
    def __init__(self, json_data, status_code):
        self._json_data = json_data
        self.status_code = status_code
        # _json_de_jacobs lee r.text para un rechazo (httpx.Response lo tiene).
        self.text = str(json_data)
```

In `backend/tests/test_pipelines_identity_injection.py`, replace the whole `test_create_pipeline_inyecta_identidad_real` function with:
```python
def test_create_pipeline_inyecta_identidad_real(client, monkeypatch):
    from tests.jacobs_falso import JacobsFalso, respuesta, veredicto

    falso = JacobsFalso({
        ("POST", "/preflight"): respuesta(200, veredicto(costo="0.00")),
        ("POST", "/pipeline"): respuesta(200, {"pipeline_id": None}),
    })

    async def _fake_get_http_client():
        return falso

    import api.pipelines as pipelines_module
    monkeypatch.setattr(pipelines_module, "get_http_client", _fake_get_http_client)
    monkeypatch.setattr(pipelines_module, "JACOBS_URL", "http://jacobs.test/jacobs")

    client.post(
        "/api/pipelines",
        json={
            "name": "test",
            "objective": "x",
            "steps": [{"facet": "thot", "capability": "critique", "prompt": "x"}],
            "invoked_by": "cliente-mintiendo",
            "mode": "supervised",
            # Client-supplied identity must be overridden, not merely
            # filled in when absent -- this is the security-relevant case.
            "user_id": "spoofed-user",
            "tenant_id": "spoofed-tenant",
        },
        headers=_auth_headers(),
    )

    (enviado,) = falso.cuerpos("POST", "/pipeline")
    assert enviado["user_id"] == USER_ID
    assert enviado["tenant_id"] == TENANT_ID
    # tanda A (2026-09-14): invoked_by es un ROL que pone el backend, igual que
    # la identidad; lo que mande el cliente se pisa.
    assert enviado["invoked_by"] == "plataforma"
    (prevuelo,) = falso.cuerpos("POST", "/preflight")
    assert (prevuelo["user_id"], prevuelo["tenant_id"], prevuelo["invoked_by"]) == (USER_ID, TENANT_ID, "plataforma")
```

In `backend/tests/test_pipeline_ownership.py`, replace:
```python
class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, **kwargs):
        return self._response

    async def post(self, url, **kwargs):
        return self._response
```
with:
```python
class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, **kwargs):
        return self._response

    async def post(self, url, **kwargs):
        # Spec 2026-09-17: la creación pregunta el pre-vuelo antes de crear.
        if url.endswith("/preflight"):
            return _FakeResponse({"ok": True, "violaciones": [], "costo_max_usd": "0.00",
                                  "pasos_costo": [], "sondeadas": []})
        return self._response
```
and replace:
```python
        client.portal.call(_call_create_pipeline, _FakeRequest({"name": "test"}), user)
```
with:
```python
        client.portal.call(_call_create_pipeline, _FakeRequest(
            {"name": "test", "steps": [{"facet": "thot", "capability": "critique", "prompt": "x"}]}), user)
```

- [ ] **Step 5: Correr y ver verde**

Run: `$PYTEST tests/test_pipelines_prevuelo.py tests/test_pipelines_propagacion.py tests/test_mesa_codigos.py tests/test_ajuste_max_pipelines.py tests/test_pipelines_identity_injection.py tests/test_pipeline_ownership.py tests/test_no_fail_open_except.py -v -p no:cacheprovider`
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/api/pipelines.py backend/tests/test_pipelines_prevuelo.py backend/tests/test_mesa_codigos.py backend/tests/test_ajuste_max_pipelines.py backend/tests/test_pipelines_identity_injection.py backend/tests/test_pipeline_ownership.py && \
git commit -m "feat(pipelines): pre-vuelo y confirmación de costo antes de crear

POST /api/pipelines/preflight; la creación exige pasos, rechaza con 422
prevuelo_rechazado, pide 409 confirmacion_de_costo y manda
costo_max_aceptado_usd a Jacobs (spec 2026-09-17 §6.1, desvíos DV-6..DV-8).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Continuar — `/continue/preflight`, `/continue`, evento de WS y causa del aborto

**Files:**
- Modify: `backend/api/pipelines.py` (`_require_pipeline_owner` devuelve el nombre; `list_pipelines` con `causa`; `EVENTOS_DE_CAUSA`, `ESTADOS_CONTINUABLES`, `sql_eventos_de_causa`, `causa_de`, `_payload`; `PedidoDeContinuarPrevuelo`, `PedidoDeContinuar`, `_cuerpo_de_continuar`, `_continuable`, `continue_preflight`, `continue_pipeline`)
- Modify: `backend/jax_engine/schemas.py:6-15`, `backend/jax_engine/state.py:99-110`
- Test: `backend/tests/test_pipelines_continuar.py` (nuevo)

**Interfaces:**
- Consumes: `_json_de_jacobs`, `MOTIVO_MAX` (Task 5); `_evaluar_veredicto`, `_exigir_consentimiento` (Task 6); `tests.jacobs_falso.preparar` (registra `publicados` de `continuar_pipeline`).
- Produces:
  - `async api.pipelines._require_pipeline_owner(pipeline_id, user) -> str | None` (nombre del pipeline)
  - `GET /api/pipelines` → cada item suma `"causa": {"tipo": "fallo"|"cancelado"|"kill_switch"|"expirado"|"desconocida", "paso"?: int, "detalle"?: str} | None` (no-None sólo para `aborted`/`expired`)
  - `api.pipelines.EVENTOS_DE_CAUSA: dict[str, str]`, `api.pipelines.sql_eventos_de_causa(n_ids: int) -> str`, `api.pipelines.causa_de(eventos: list[tuple[int, str, str | None]]) -> dict`
  - `class PedidoDeContinuarPrevuelo(BaseModel): reasignar: dict[str, str] | None`; `class PedidoDeContinuar(PedidoDeContinuarPrevuelo): costo_confirmado_usd: Decimal | None (ge=0)`
  - `POST /api/pipelines/{id}/continue/preflight` → `{"continuable": bool, "motivo": str | None, "pasos_a_correr": [int], "pasos_reusados": [int], "veredicto": dict | None}` (veredicto = salida de `_evaluar_veredicto`)
  - `POST /api/pipelines/{id}/continue` → cuerpo de Jacobs; 409 `estado_no_continuable` `{code, motivo}`; 422/409 de consentimiento; 429 `limite_de_pipelines`
  - `EventType` incluye `"pipeline_continued"`; `async engine_state.continuar_pipeline(pipeline: PipelineState, tenant_id: str, user_id: str, continuacion: dict) -> None` publica `{**pipeline.model_dump(), **continuacion}`

- [ ] **Step 1: Escribir los tests que fallan**

Create `backend/tests/test_pipelines_continuar.py`:

```python
"""Continuar un pipeline abortado o vencido desde la Mesa (spec 2026-09-17
§5.1, §6.1, §6.2): dueño, cupo, pre-vuelo de los pasos pendientes, confirmación
de costo, y el panel sabe por qué se detuvo (causa)."""
import asyncio
import json
import time
import typing
import uuid

import pytest
from fastapi import HTTPException

from api import pipelines as mod
from auth.models import AuthUser
from jax_engine.schemas import EventType
from tests.identidades import cabeceras, sql, uid
from tests.jacobs_falso import JacobsFalso, preparar, respuesta, veredicto, violacion

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
PID = "22222222-2222-2222-2222-222222222222"
RUTA_PREVIA = f"/pipeline/{PID}/continue/preflight"
RUTA = f"/pipeline/{PID}/continue"


def _correr(corutina):
    try:
        return asyncio.run(corutina)
    except HTTPException as exc:
        return exc


def _continuable(v=None, continuable=True, motivo=None):
    return {"continuable": continuable, "motivo": motivo, "pasos_a_correr": [4, 5] if continuable else [],
            "pasos_reusados": [0, 1, 2, 3] if continuable else [], "veredicto": v if continuable else None}


def _previa(**kw):
    return {("POST", RUTA_PREVIA): respuesta(200, _continuable(**kw))}


# ---------------------------------------------------------------- /continue/preflight

def test_continue_preflight_reenvia_identidad_y_reasignacion(monkeypatch):
    falso = JacobsFalso(_previa(v=veredicto(costo="0.30")))
    preparar(monkeypatch, falso)
    r = _correr(mod.continue_preflight(pipeline_id=PID, pedido=mod.PedidoDeContinuarPrevuelo(reasignar={"4": "ada"}), user=USUARIO))
    assert falso.cuerpos("POST", RUTA_PREVIA) == [
        {"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1", "reasignar": {"4": "ada"}}]
    assert (r["continuable"], r["pasos_reusados"], r["veredicto"]["umbral_usd"], r["veredicto"]["requiere_confirmacion"]) == (
        True, [0, 1, 2, 3], "0.50", False)


def test_continue_preflight_no_continuable_trae_el_motivo_sin_veredicto(monkeypatch):
    preparar(monkeypatch, JacobsFalso(_previa(continuable=False, motivo="status completed")))
    r = _correr(mod.continue_preflight(pipeline_id=PID, pedido=mod.PedidoDeContinuarPrevuelo(), user=USUARIO))
    assert (r["continuable"], r["motivo"], r["veredicto"]) == (False, "status completed", None)


def test_continue_preflight_reasignacion_invalida_pasa_con_violaciones(monkeypatch):
    rechazo = {"detail": {"code": "reasignacion_invalida", "violaciones": [violacion(regla="cleanroom")]}}
    preparar(monkeypatch, JacobsFalso({("POST", RUTA_PREVIA): respuesta(422, rechazo)}))
    r = _correr(mod.continue_preflight(pipeline_id=PID, pedido=mod.PedidoDeContinuarPrevuelo(reasignar={"5": "ada"}), user=USUARIO))
    assert (r.status_code, r.detail["code"], r.detail["violaciones"][0]["regla"]) == (422, "reasignacion_invalida", "cleanroom")


# ---------------------------------------------------------------- /continue

def test_continuar_un_no_continuable_es_409_y_no_continua(monkeypatch):
    falso = JacobsFalso(_previa(continuable=False, motivo="status completed"))
    preparar(monkeypatch, falso)
    r = _correr(mod.continue_pipeline(pipeline_id=PID, pedido=mod.PedidoDeContinuar(), user=USUARIO))
    assert (r.status_code, r.detail) == (409, {"code": "estado_no_continuable", "motivo": "status completed"})
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_con_prevuelo_rechazado_es_422(monkeypatch):
    falso = JacobsFalso(_previa(v=veredicto(ok=False, violaciones=[violacion()])))
    preparar(monkeypatch, falso)
    r = _correr(mod.continue_pipeline(pipeline_id=PID, pedido=mod.PedidoDeContinuar(), user=USUARIO))
    assert (r.status_code, r.detail["code"]) == (422, "prevuelo_rechazado")
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_caro_sin_confirmar_es_409(monkeypatch):
    falso = JacobsFalso(_previa(v=veredicto(costo="0.60")))
    preparar(monkeypatch, falso)
    r = _correr(mod.continue_pipeline(pipeline_id=PID, pedido=mod.PedidoDeContinuar(), user=USUARIO))
    assert (r.status_code, r.detail["code"]) == (409, "confirmacion_de_costo")
    assert falso.cuerpos("POST", RUTA) == []


def test_continuar_confirmado_continua_admite_y_publica(monkeypatch):
    ok = {"pipeline_id": PID, "status": "running", "run_epoch": 2, "pasos_a_correr": [4, 5],
          "pasos_reusados": [0, 1, 2, 3], "costo_max_usd": "0.60", "pasos_costo": []}
    falso = JacobsFalso({**_previa(v=veredicto(costo="0.60")), ("POST", RUTA): respuesta(200, ok)})
    registro = preparar(monkeypatch, falso)
    pedido = mod.PedidoDeContinuar(reasignar={"4": "ada"}, costo_confirmado_usd="0.60")
    r = _correr(mod.continue_pipeline(pipeline_id=PID, pedido=pedido, user=USUARIO))
    assert r == ok
    assert falso.cuerpos("POST", RUTA) == [{"invoked_by": "plataforma", "user_id": "5", "tenant_id": "1",
                                            "reasignar": {"4": "ada"}, "costo_max_aceptado_usd": "0.60"}]
    assert registro.admitidos == [("1", PID)]
    assert registro.publicados == [("pipeline_continued", PID, {"run_epoch": 2, "pasos_reusados": [0, 1, 2, 3]})]


def test_continuar_rechazado_por_jacobs_sale_con_su_status_y_no_admite(monkeypatch):
    falso = JacobsFalso({**_previa(v=veredicto()), ("POST", RUTA): respuesta(423, {"detail": "kill switch activo"})})
    registro = preparar(monkeypatch, falso)
    r = _correr(mod.continue_pipeline(pipeline_id=PID, pedido=mod.PedidoDeContinuar(), user=USUARIO))
    assert (r.status_code, r.detail) == (423, {"code": "jacobs_rechazo", "status": 423, "motivo": "kill switch activo"})
    assert registro.admitidos == [] and registro.publicados == []


def test_continuar_con_el_cupo_lleno_es_429_sin_llamar_a_jacobs(monkeypatch):
    falso = JacobsFalso()
    preparar(monkeypatch, falso, cupo_libre=False, maximo=2)
    r = _correr(mod.continue_pipeline(pipeline_id=PID, pedido=mod.PedidoDeContinuar(), user=USUARIO))
    assert (r.status_code, r.detail) == (429, {"code": "limite_de_pipelines", "max": 2})
    assert falso.llamadas == []


def test_pipeline_continued_es_un_evento_del_ws():
    assert "pipeline_continued" in typing.get_args(EventType)


@pytest.mark.parametrize("eventos, esperado", [
    ([(1, "STEP_FAILED", json.dumps({"step_index": 4, "error": "api_key=sk-FAKE-causa cortado"})),
      (2, "PIPELINE_ABORTED", json.dumps({"at_wave": 3}))],
     {"tipo": "fallo", "paso": 4, "detalle": "api_key=*** cortado"}),
    ([(5, "PIPELINE_CANCELLED", json.dumps({"by": "API request"}))], {"tipo": "cancelado"}),
    ([(1, "STEP_FAILED", json.dumps({"step_index": 0, "error": "x"})), (9, "KILL_SWITCH_ABORTED", "{}")],
     {"tipo": "kill_switch"}),
    ([(3, "REAPED", json.dumps({"prev_status": "running", "reason": "sin avance"}))], {"tipo": "expirado"}),
])
def test_la_causa_es_la_del_ultimo_evento(eventos, esperado):
    assert mod.causa_de(eventos) == esperado


# ---------------------------------------------------------------- con DB

@pytest.mark.parametrize("nombre", ["continue_preflight", "continue_pipeline"])
def test_continuar_exige_ser_el_duenio(client, nombre):
    funcion = getattr(mod, nombre)

    async def llamar():
        try:
            await funcion(pipeline_id=str(uuid.uuid4()), pedido=mod.PedidoDeContinuar(),
                          user=AuthUser(user_id="intruso", tenant_id="1", role="operator"))
        except HTTPException as exc:
            return exc.status_code
        return None

    assert client.portal.call(llamar) == 404


@pytest.fixture
def abortado_con_eventos(client):
    duenio = uid(client, "continuar-causa", "operator")
    ahora = time.time()
    abortado, corriendo = str(uuid.uuid4()), str(uuid.uuid4())
    for pid, estado in ((abortado, "aborted"), (corriendo, "running")):
        client.portal.call(
            sql,
            "INSERT INTO jacobs_pipelines (pipeline_id, name, invoked_by, mode, status, created_at, updated_at, "
            "user_id, tenant_id, owner_ack_at) VALUES (%s, %s, 'plataforma', 'supervised', %s, %s, %s, %s, 'TENANT-CONT', %s)",
            (pid, f"causa {estado}", estado, ahora, ahora, duenio, ahora))
    client.portal.call(sql, "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                            "VALUES (%s, NULL, 'STEP_FAILED', %s, %s)",
                       (abortado, json.dumps({"step_index": 4, "error": "Salida cortada por max_tokens"}), ahora))
    client.portal.call(sql, "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                            "VALUES (%s, NULL, 'PIPELINE_ABORTED', %s, %s)",
                       (abortado, json.dumps({"at_wave": 3}), ahora + 1))
    yield abortado, corriendo
    for pid in (abortado, corriendo):
        client.portal.call(sql, "DELETE FROM jacobs_events WHERE pipeline_id = %s", (pid,))
        client.portal.call(sql, "DELETE FROM jacobs_pipelines WHERE pipeline_id = %s", (pid,))


def test_la_lista_trae_la_causa_de_los_abortados(client, abortado_con_eventos):
    abortado, corriendo = abortado_con_eventos
    r = client.get("/api/pipelines", headers=cabeceras(client, "continuar-causa", "operator", tenant_id="TENANT-CONT"))
    assert r.status_code == 200, r.text
    por_id = {p["pipeline_id"]: p for p in r.json()["pipelines"]}
    assert por_id[abortado]["causa"] == {"tipo": "fallo", "paso": 4, "detalle": "Salida cortada por max_tokens"}
    assert por_id[corriendo]["causa"] is None


def test_la_consulta_de_causa_usa_el_indice_de_eventos(client, abortado_con_eventos):
    relleno = [str(uuid.uuid4()) for _ in range(60)]
    for pid in relleno:
        client.portal.call(sql, "INSERT INTO jacobs_events (pipeline_id, step_id, event_type, payload, ts) "
                                "VALUES (%s, NULL, 'STEP_STARTED', '{}', 1)", (pid,))
    try:
        client.portal.call(sql, "ANALYZE TABLE jacobs_events", (), True)
        filas = client.portal.call(sql, "EXPLAIN " + mod.sql_eventos_de_causa(2),
                                   (*abortado_con_eventos, *mod.EVENTOS_DE_CAUSA), True)
        ((_id, _sel, tabla, _tipo, _posibles, clave, _largo, _ref, _filas, extra),) = [tuple(f) for f in filas]
        assert tabla == "jacobs_events"
        assert clave == "idx_events_pipeline", filas
        assert "filesort" not in (extra or "") and "temporary" not in (extra or ""), filas
    finally:
        for pid in relleno:
            client.portal.call(sql, "DELETE FROM jacobs_events WHERE pipeline_id = %s", (pid,))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$PYTEST tests/test_pipelines_continuar.py -v -p no:cacheprovider`
Expected (18 tests): los de continuar con `AttributeError: module 'api.pipelines' has no attribute 'PedidoDeContinuarPrevuelo'` / `'PedidoDeContinuar'` / `'continue_preflight'`; `test_pipeline_continued...` con `assert 'pipeline_continued' in (...)`; los 4 de causa con `AttributeError: ... 'causa_de'`; la lista con `KeyError: 'causa'`; el EXPLAIN con `AttributeError: ... 'sql_eventos_de_causa'`.

- [ ] **Step 3: Evento de WS**

In `backend/jax_engine/schemas.py`, replace:
```python
    "pipeline_step_changed",
```
with:
```python
    "pipeline_step_changed",
    # Spec 2026-09-17 §6.2: un pipeline abortado vuelve a correr; el panel lo
    # refresca igual que pipeline_step_changed (useJaxStore.js).
    "pipeline_continued",
```

In `backend/jax_engine/state.py`, replace:
```python
    def remove_pipeline(self, pipeline_id: str):
```
with:
```python
    async def continuar_pipeline(self, pipeline: PipelineState, tenant_id: str, user_id: str, continuacion: dict):
        """Spec 2026-09-17 §6.2: vuelve a la lista de activos (el poller lo
        sigue) y publica pipeline_continued con la época y los pasos reusados."""
        self._state.active_pipelines[pipeline.pipeline_id] = pipeline
        event = JAXEvent(
            event_type="pipeline_continued",
            tenant_id=tenant_id,
            user_id=user_id,
            payload={**pipeline.model_dump(), **continuacion},
        )
        await event_bus.publish(event)

    def remove_pipeline(self, pipeline_id: str):
```

- [ ] **Step 4: Dueño con nombre, causa en la lista**

In `backend/api/pipelines.py`, replace:
```python
import os
import time
import uuid
from decimal import Decimal, InvalidOperation
```
with:
```python
import json
import os
import time
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
```

In `_require_pipeline_owner`, replace:
```python
                "SELECT user_id, tenant_id, owner_ack_at FROM jacobs_pipelines WHERE pipeline_id=%s",
                (pipeline_id,),
            )
            row = await cur.fetchone()
    if row is None or row[2] is None or not es_del_usuario(row[0], row[1], user):
        raise HTTPException(status_code=404, detail="pipeline_no_encontrado")
```
with:
```python
                "SELECT user_id, tenant_id, owner_ack_at, name FROM jacobs_pipelines WHERE pipeline_id=%s",
                (pipeline_id,),
            )
            row = await cur.fetchone()
    if row is None or row[2] is None or not es_del_usuario(row[0], row[1], user):
        raise HTTPException(status_code=404, detail="pipeline_no_encontrado")
    # El nombre lo usa continue para el estado del panel (sin otra consulta).
    return row[3]
```

Replace the whole `list_pipelines` function with:
```python
# Causa de un pipeline detenido (desvío DV-9 del plan): el último de estos
# eventos de jacobs_events manda. paso/detalle salen del último STEP_FAILED.
EVENTOS_DE_CAUSA = {
    "STEP_FAILED": "fallo",
    "PIPELINE_ABORTED": "fallo",
    "PIPELINE_CANCELLED": "cancelado",
    "KILL_SWITCH_ABORTED": "kill_switch",
    "REAPED": "expirado",
}
ESTADOS_CONTINUABLES = ("aborted", "expired")


def sql_eventos_de_causa(n_ids: int) -> str:
    """Una consulta por lista, por idx_events_pipeline (jax/jacobs/store.py).
    Sin ORDER BY a propósito: el orden por id se hace en Python sobre pocas
    filas y así el plan no necesita filesort (EXPLAIN en
    tests/test_pipelines_continuar.py)."""
    ids = ", ".join(["%s"] * n_ids)
    tipos = ", ".join(["%s"] * len(EVENTOS_DE_CAUSA))
    return (f"SELECT pipeline_id, id, event_type, payload FROM jacobs_events "
            f"WHERE pipeline_id IN ({ids}) AND event_type IN ({tipos})")


def _payload(crudo) -> dict:
    if isinstance(crudo, dict):
        return crudo
    try:
        datos = json.loads(crudo) if crudo else {}
    except ValueError:
        return {}  # payload ilegible: la causa sale sin paso ni detalle; nunca se inventan
    return datos if isinstance(datos, dict) else {}


def causa_de(eventos: list[tuple[int, str, str | None]]) -> dict:
    """eventos: (id, event_type, payload) de UN pipeline."""
    if not eventos:
        return {"tipo": "desconocida"}
    ordenados = sorted(eventos, key=lambda e: e[0])
    causa = {"tipo": EVENTOS_DE_CAUSA[ordenados[-1][1]]}
    if causa["tipo"] == "fallo":
        fallos = [e for e in ordenados if e[1] == "STEP_FAILED"]
        if fallos:
            datos = _payload(fallos[-1][2])
            if isinstance(datos.get("step_index"), int):
                causa["paso"] = datos["step_index"]
            if datos.get("error"):
                causa["detalle"] = recortar_redactado(str(datos["error"]), MOTIVO_MAX)
    return causa


@router.get("")
async def list_pipelines(user: AuthUser = Depends(get_current_user)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_PIPELINES_DEL_USUARIO,
                              (user.user_id, user.tenant_id, LISTA_PIPELINES_MAX))
            filas = await cur.fetchall()
            detenidos = [pid for pid, _n, st, _c, _u in filas if st in ESTADOS_CONTINUABLES]
            eventos = defaultdict(list)
            if detenidos:
                await cur.execute(sql_eventos_de_causa(len(detenidos)), (*detenidos, *EVENTOS_DE_CAUSA))
                for pid, evento_id, tipo, payload in await cur.fetchall():
                    eventos[pid].append((evento_id, tipo, payload))
    return {"pipelines": [
        {"pipeline_id": pid, "name": name, "status": st, "created_at": c, "updated_at": u,
         "causa": causa_de(eventos[pid]) if st in ESTADOS_CONTINUABLES else None}
        for pid, name, st, c, u in filas
    ]}
```

- [ ] **Step 5: Endpoints de continuar**

In `backend/api/pipelines.py`, after `class PedidoDePrevuelo` (Task 6), add:
```python
class PedidoDeContinuarPrevuelo(BaseModel):
    reasignar: dict[str, str] | None = None


class PedidoDeContinuar(PedidoDeContinuarPrevuelo):
    costo_confirmado_usd: Decimal | None = Field(default=None, ge=0)


def _cuerpo_de_continuar(user: AuthUser, reasignar: dict[str, str] | None) -> dict:
    cuerpo = {"invoked_by": INVOKED_BY_PLATAFORMA, "user_id": user.user_id, "tenant_id": user.tenant_id}
    if reasignar:
        cuerpo["reasignar"] = reasignar
    return cuerpo


async def _continuable(client, pipeline_id: str, user: AuthUser,
                       reasignar: dict[str, str] | None, umbral: Decimal) -> dict:
    r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/continue/preflight",
                          json=_cuerpo_de_continuar(user, reasignar), timeout=JACOBS_PIPELINE_TIMEOUT)
    data = _json_de_jacobs(r)
    try:
        continuable = data["continuable"]
        a_correr = data["pasos_a_correr"]
        reusados = data["pasos_reusados"]
        if not isinstance(continuable, bool) or not isinstance(a_correr, list) or not isinstance(reusados, list):
            raise ValueError("forma de continue/preflight")
    except (KeyError, ValueError):
        raise _prevuelo_no_disponible() from None
    motivo = data.get("motivo")
    return {
        "continuable": continuable,
        "motivo": recortar_redactado(str(motivo), MOTIVO_MAX) if motivo else None,
        "pasos_a_correr": a_correr,
        "pasos_reusados": reusados,
        "veredicto": _evaluar_veredicto(data.get("veredicto"), umbral) if continuable else None,
    }
```

At the end of the file (after `cancel_pipeline`), add:
```python
@router.post("/{pipeline_id}/continue/preflight")
async def continue_preflight(pipeline_id: str, pedido: PedidoDeContinuarPrevuelo,
                             user: AuthUser = Depends(get_current_user)):
    await _require_pipeline_owner(pipeline_id, user)
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    client = await get_http_client()
    try:
        return await _continuable(client, pipeline_id, user, pedido.reasignar, umbral)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})


@router.post("/{pipeline_id}/continue")
async def continue_pipeline(pipeline_id: str, pedido: PedidoDeContinuar,
                            user: AuthUser = Depends(get_current_user)):
    nombre = await _require_pipeline_owner(pipeline_id, user)
    # Continuar ocupa un cupo del tenant, igual que crear (desvío DV-11).
    limite = await ajustes.valor(ajustes.MAX_PIPELINES)
    if not await resource_manager.can_start_pipeline(user.tenant_id, limite):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "limite_de_pipelines", "max": limite},
        )
    umbral = await ajustes.valor(ajustes.CONFIRMAR_USD)
    client = await get_http_client()
    try:
        estado = await _continuable(client, pipeline_id, user, pedido.reasignar, umbral)
        if not estado["continuable"]:
            detalle = {"code": "estado_no_continuable", "motivo": estado["motivo"]}
            raise HTTPException(status_code=409, detail=detalle)
        _exigir_consentimiento(estado["veredicto"], pedido.costo_confirmado_usd)
        cuerpo = _cuerpo_de_continuar(user, pedido.reasignar)
        aceptado = pedido.costo_confirmado_usd if pedido.costo_confirmado_usd is not None else umbral
        cuerpo["costo_max_aceptado_usd"] = str(aceptado)
        r = await client.post(f"{JACOBS_URL}/pipeline/{pipeline_id}/continue", json=cuerpo,
                              timeout=JACOBS_PIPELINE_TIMEOUT)
        data = _json_de_jacobs(r)
        await resource_manager.admit_pipeline(user.tenant_id, pipeline_id)
        await engine_state.continuar_pipeline(
            PipelineState(pipeline_id=pipeline_id, tenant_id=user.tenant_id, user_id=user.user_id,
                          name=nombre or "", status="running"),
            user.tenant_id, user.user_id,
            {"run_epoch": data.get("run_epoch"), "pasos_reusados": data.get("pasos_reusados", [])},
        )
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), MOTIVO_MAX)})
```

- [ ] **Step 6: Correr y ver verde**

Run: `$PYTEST tests/test_pipelines_continuar.py tests/test_pipelines_prevuelo.py tests/test_pipelines_propagacion.py tests/test_t6_seguimiento.py tests/test_pipeline_ownership.py tests/test_mesa_codigos.py tests/test_pipeline_user_id.py tests/test_no_fail_open_except.py -v -p no:cacheprovider`
Expected: todos PASS. Si el EXPLAIN muestra `key=None` (type `ALL`) con este volumen, NO cambiar la consulta: subir el relleno de 60 a 500 eventos, volver a correr, y anotar el número usado en el commit.

- [ ] **Step 7: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add backend/api/pipelines.py backend/jax_engine/schemas.py backend/jax_engine/state.py backend/tests/test_pipelines_continuar.py && \
git commit -m "feat(pipelines): continuar un pipeline abortado o vencido desde la Mesa

POST /api/pipelines/{id}/continue/preflight y /continue con dueño, cupo,
pre-vuelo y confirmación; evento pipeline_continued; GET /api/pipelines trae la
causa del aborto por idx_events_pipeline (spec 2026-09-17 §5.1, §6.1, §6.2).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend base — montos, códigos, reglas, causas, evento y clean-room por pasos

**Files:**
- Create: `frontend/src/lib/moneda.js`, `frontend/src/lib/moneda.test.js`, `frontend/src/store/useJaxStore.pipelineContinued.test.js`
- Modify: `frontend/src/api/errores.js`, `frontend/src/api/errores.test.js`, `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`, `frontend/src/store/useJaxStore.js:294`, `frontend/src/components/BottomBar/pipelineChain.js:79-93`, `frontend/src/components/BottomBar/pipelineChain.test.js`

**Interfaces:**
- Consumes: forma de `violaciones` y `causa` del backend (Tasks 6 y 7).
- Produces:
  - `formatearUsd(monto: string|number|null, lang: 'es'|'en') -> string|null`, `OPCIONES_USD` (`lib/moneda.js`)
  - `textoDeViolacion(t, v: {paso, faceta, regla, detalle}) -> string` (`api/errores.js`)
  - `cleanroomViolationsDePasos(pasos: [{facet, capability, depends_on}]) -> [{paso: number, facet: string, dependsOn: number}]` (`pipelineChain.js`)
  - i18n (es/en): `erroresMesa.{prevuelo_rechazado, confirmacion_de_costo, costo_supera_lo_aceptado, reasignacion_invalida, prevuelo_no_disponible, estado_no_continuable, pasos_requeridos, costo_confirmado_invalido}`, `reglasPrevuelo.{tope_insuficiente, sin_contrato_de_salida, credencial_ausente, faceta_caida, faceta_inexistente}`, `reglaPrevueloDesconocida(v)`, `detalleDelPrevuelo(texto)`, `causasDeAborto.{fallo, cancelado, kill_switch, expirado, desconocida}` (todas funciones `(c) => string`)
  - store: `handleEvent({event_type: 'pipeline_continued', payload})` pone el pipeline en `activePipelines`.

- [ ] **Step 1: Escribir los tests que fallan**

Create `frontend/src/lib/moneda.test.js`:
```js
import { describe, it, expect } from 'vitest'
import { formatearUsd, OPCIONES_USD } from './moneda'

// Montos del pre-vuelo (spec 2026-09-17 §6.2): con el locale activo, nunca un
// "$" pegado a mano. Llegan como string decimal desde el backend.
describe('formatearUsd', () => {
  it('formatea en inglés con símbolo y dos decimales', () => {
    expect(formatearUsd('0.5', 'en')).toBe('$0.50')
    expect(formatearUsd('1234.5', 'en')).toBe('$1,234.50')
  })

  it('conserva hasta cuatro decimales para montos chicos', () => {
    expect(formatearUsd('0.123456', 'en')).toBe('$0.1235')
  })

  it('usa el locale del idioma activo', () => {
    expect(formatearUsd('1234.5', 'es')).toBe(new Intl.NumberFormat('es-HN', OPCIONES_USD).format(1234.5))
  })

  it('un monto ausente o ilegible no se inventa', () => {
    for (const malo of [null, undefined, '', 'abc']) expect(formatearUsd(malo, 'es')).toBeNull()
  })
})
```

Create `frontend/src/store/useJaxStore.pipelineContinued.test.js`:
```js
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import { useJaxStore } from './useJaxStore'

const INICIAL = useJaxStore.getState()

describe('pipeline_continued (spec 2026-09-17 §6.2)', () => {
  beforeEach(() => useJaxStore.setState({ ...INICIAL, token: 't' }, true))

  it('pone el pipeline continuado en activePipelines igual que pipeline_step_changed', () => {
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_continued',
      payload: { pipeline_id: 'p-9', name: 'leyes', status: 'running', steps: [], run_epoch: 2, pasos_reusados: [0, 1] },
    })
    expect(useJaxStore.getState().activePipelines['p-9']).toMatchObject({ status: 'running', run_epoch: 2 })
  })
})
```

In `frontend/src/api/errores.test.js`, replace:
```js
import { textoDeErrorDeMesa, textoDeAviso } from './errores'
```
with:
```js
import { textoDeErrorDeMesa, textoDeAviso, textoDeViolacion } from './errores'
```
and append at the end of the file:
```js
describe('pre-vuelo y continuar (spec 2026-09-17)', () => {
  const CODIGOS = ['prevuelo_rechazado', 'confirmacion_de_costo', 'costo_supera_lo_aceptado', 'reasignacion_invalida',
    'prevuelo_no_disponible', 'estado_no_continuable', 'pasos_requeridos', 'costo_confirmado_invalido']

  it('cada código nuevo se traduce en los dos idiomas y no sale crudo', () => {
    for (const d of [es, en]) {
      for (const code of CODIGOS) {
        const texto = textoDeErrorDeMesa(d, err({ code, violaciones: [] }), 'GENERICO')
        expect(texto, code).not.toBe('GENERICO')
        expect(texto, code).not.toContain(code)
      }
    }
  })

  it('una violación se lee por su regla, con el paso humano y el detalle como dato', () => {
    const v = { paso: 4, faceta: 'kimi', regla: 'tope_insuficiente', detalle: 'tope 8000 < 16384' }
    expect(textoDeViolacion(es, v)).toBe(`${es.reglasPrevuelo.tope_insuficiente(v)} ${es.detalleDelPrevuelo('tope 8000 < 16384')}`)
    expect(es.reglasPrevuelo.tope_insuficiente(v)).toContain('5')
    for (const regla of ['tope_insuficiente', 'sin_contrato_de_salida', 'credencial_ausente', 'faceta_caida', 'faceta_inexistente']) {
      expect(en.reglasPrevuelo[regla](v), regla).toContain('kimi')
    }
  })

  it('una regla desconocida cae en el texto genérico de regla, nunca cruda', () => {
    const v = { paso: 0, faceta: 'x', regla: 'constructor' }
    expect(textoDeViolacion(es, v)).toBe(es.reglaPrevueloDesconocida(v))
    expect(textoDeViolacion(es, v)).not.toContain('constructor')
  })
})
```

In `frontend/src/components/BottomBar/pipelineChain.test.js`, add `cleanroomViolationsDePasos` to the existing import from `'./pipelineChain'` and append:
```js
describe('cleanroomViolationsDePasos (continuar, spec 2026-09-17 §6.2)', () => {
  it('marca un paso de auditoría con la misma faceta que una de sus dependencias', () => {
    const pasos = [
      { facet: 'hipatia', capability: 'research', depends_on: [] },
      { facet: 'ada', capability: 'generate', depends_on: [0] },
      { facet: 'ada', capability: 'validate_consistency', depends_on: [0, 1] },
    ]
    expect(cleanroomViolationsDePasos(pasos)).toEqual([{ paso: 2, facet: 'ada', dependsOn: 1 }])
  })

  it('sin capability de auditoría o sin coincidencia no marca nada', () => {
    expect(cleanroomViolationsDePasos([
      { facet: 'ada', capability: 'research', depends_on: [] },
      { facet: 'ada', capability: 'generate', depends_on: [0] },
      { facet: 'thot', capability: 'critique', depends_on: [0, 1] },
    ])).toEqual([])
  })
})
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$VITEST src/lib/moneda.test.js src/store/useJaxStore.pipelineContinued.test.js src/api/errores.test.js src/components/BottomBar/pipelineChain.test.js`
Expected: `moneda.test.js` falla la importación (`Failed to resolve import "./moneda"`), el del store `expected undefined to match object { status: 'running', run_epoch: 2 }`, errores `SyntaxError: The requested module './errores' does not provide an export named 'textoDeViolacion'`, pipelineChain `does not provide an export named 'cleanroomViolationsDePasos'`. (10 tests nuevos.)

- [ ] **Step 3: Implementar `moneda.js`, `errores.js`, store y clean-room**

Create `frontend/src/lib/moneda.js`:
```js
import { localeFor } from '../i18n/index.jsx'

// Montos en USD del pre-vuelo (spec 2026-09-17 §6.2): el backend los manda
// como string decimal; se muestran con el locale del idioma activo. Hasta 4
// decimales: un paso barato cuesta centavos de centavo.
export const OPCIONES_USD = { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 }

export function formatearUsd(monto, lang) {
  if (monto === null || monto === undefined || monto === '') return null
  const numero = Number(monto)
  if (!Number.isFinite(numero)) return null
  return new Intl.NumberFormat(localeFor(lang), OPCIONES_USD).format(numero)
}
```

In `frontend/src/api/errores.js`, append:
```js
// Violación del pre-vuelo (spec 2026-09-17 §4.1): se lee por su `regla`; una
// regla que esta versión no conoce cae en un texto genérico, nunca cruda. El
// `detalle` (redactado por el backend) va como dato.
export function textoDeViolacion(t, v) {
  const conocida = typeof v?.regla === 'string' && Object.hasOwn(t.reglasPrevuelo, v.regla)
  const base = (conocida ? t.reglasPrevuelo[v.regla] : t.reglaPrevueloDesconocida)(v || {})
  return v?.detalle ? `${base} ${t.detalleDelPrevuelo(v.detalle)}` : base
}
```

In `frontend/src/store/useJaxStore.js`, replace:
```js
    if (event_type === 'pipeline_step_changed') {
      set((s) => {
```
with:
```js
    // pipeline_continued (spec 2026-09-17 §6.2): mismo refresco del panel.
    if (event_type === 'pipeline_step_changed' || event_type === 'pipeline_continued') {
      set((s) => {
```

In `frontend/src/components/BottomBar/pipelineChain.js`, replace the whole `cleanroomViolations` function (from `// Espejo de jacobs/plan.py::_check_cleanroom` to the end of the file) with:
```js
// Espejo de jacobs/plan.py::_check_cleanroom: quien produce no aprueba. El
// servidor rechaza igual (422); esto avisa ANTES de enviar. Sobre pasos
// arbitrarios (continuar, spec 2026-09-17 §6.2): índices = posición.
export function cleanroomViolationsDePasos(pasos) {
  const violations = []
  pasos.forEach((paso, i) => {
    if (!AUDIT_CAPABILITIES.has(paso.capability)) return
    for (const dep of paso.depends_on || []) {
      if (pasos[dep] && pasos[dep].facet === paso.facet) {
        violations.push({ paso: i, facet: paso.facet, dependsOn: dep })
      }
    }
  })
  return violations
}

export function cleanroomViolations(facetsByRole) {
  const pasos = CHAIN_ROLES.map(role => ({
    facet: facetsByRole[role.id], capability: role.capability, depends_on: role.dependsOn,
  }))
  return cleanroomViolationsDePasos(pasos).map(v => ({
    role: CHAIN_ROLES[v.paso].id, facet: v.facet, dependsOnRole: CHAIN_ROLES[v.dependsOn].id,
  }))
}
```

- [ ] **Step 4: i18n es/en**

In `frontend/src/i18n/es.js`, replace:
```js
export default {
```
with:
```js
// Lugar de un paso en los textos del pre-vuelo: "Paso 5 (kimi)".
const lugarDelPaso = (v) => (Number.isInteger(v?.paso) ? `Paso ${v.paso + 1} (${v.faceta})` : `${v?.faceta ?? ''}`)

export default {
```
In `erroresMesa` of `es.js`, replace:
```js
    pdf_ilegible: () => 'No se pudo leer el PDF.',
  },
```
with:
```js
    pdf_ilegible: () => 'No se pudo leer el PDF.',
    // Pre-vuelo y continuar (spec 2026-09-17)
    prevuelo_rechazado: (d) => `El pre-vuelo rechazó el pipeline (${(d.violaciones || []).length} problema(s)). No se gastó nada.`,
    confirmacion_de_costo: () => 'Hace falta confirmar el costo máximo antes de correr.',
    costo_supera_lo_aceptado: () => 'El costo máximo subió por encima de lo que confirmaste. Revísalo y vuelve a confirmar.',
    reasignacion_invalida: () => 'La reasignación de facetas no es válida para este plan.',
    prevuelo_no_disponible: () => 'El pre-vuelo no está disponible: sin él no se corre nada.',
    estado_no_continuable: () => 'Este pipeline no se puede continuar en su estado actual.',
    pasos_requeridos: () => 'El pipeline no tiene pasos.',
    costo_confirmado_invalido: () => 'El costo confirmado no es válido.',
  },
  reglasPrevuelo: {
    tope_insuficiente: (v) => `${lugarDelPaso(v)}: el tope de salida del modelo no alcanza para esta tarea.`,
    sin_contrato_de_salida: (v) => `${lugarDelPaso(v)}: el modelo no declara su tope de salida, así que no se puede acotar el costo.`,
    credencial_ausente: (v) => `${lugarDelPaso(v)}: no hay una credencial activa para el proveedor.`,
    faceta_caida: (v) => `${lugarDelPaso(v)}: la faceta no respondió a la sonda.`,
    faceta_inexistente: (v) => `${lugarDelPaso(v)}: la faceta no existe o no está activa.`,
  },
  reglaPrevueloDesconocida: (v) => `${lugarDelPaso(v)}: el pre-vuelo lo rechazó por una regla que esta versión no conoce.`,
  detalleDelPrevuelo: (texto) => `Detalle: ${texto}`,
  causasDeAborto: {
    fallo: (c) => (Number.isInteger(c.paso) ? `Se detuvo: falló el paso ${c.paso + 1}.` : 'Se detuvo: falló un paso.'),
    cancelado: () => 'Se detuvo: lo cancelaron.',
    kill_switch: () => 'Se detuvo: se activó el kill switch.',
    expirado: () => 'Venció: estuvo demasiado tiempo sin avanzar.',
    desconocida: () => 'Se detuvo por una causa que no quedó registrada.',
  },
```

In `frontend/src/i18n/en.js`, replace:
```js
export default {
```
with:
```js
// Where a step is in the preflight texts: "Step 5 (kimi)".
const lugarDelPaso = (v) => (Number.isInteger(v?.paso) ? `Step ${v.paso + 1} (${v.faceta})` : `${v?.faceta ?? ''}`)

export default {
```
In `erroresMesa` of `en.js`, replace:
```js
    pdf_ilegible: () => 'The PDF could not be read.',
  },
```
with:
```js
    pdf_ilegible: () => 'The PDF could not be read.',
    // Preflight and continue (spec 2026-09-17)
    prevuelo_rechazado: (d) => `The preflight rejected the pipeline (${(d.violaciones || []).length} problem(s)). Nothing was spent.`,
    confirmacion_de_costo: () => 'The maximum cost must be confirmed before running.',
    costo_supera_lo_aceptado: () => 'The maximum cost rose above what you confirmed. Review it and confirm again.',
    reasignacion_invalida: () => 'The facet reassignment is not valid for this plan.',
    prevuelo_no_disponible: () => 'The preflight is not available: nothing runs without it.',
    estado_no_continuable: () => 'This pipeline cannot be continued in its current state.',
    pasos_requeridos: () => 'The pipeline has no steps.',
    costo_confirmado_invalido: () => 'The confirmed cost is not valid.',
  },
  reglasPrevuelo: {
    tope_insuficiente: (v) => `${lugarDelPaso(v)}: the model's output limit is not enough for this task.`,
    sin_contrato_de_salida: (v) => `${lugarDelPaso(v)}: the model does not declare its output limit, so the cost cannot be bounded.`,
    credencial_ausente: (v) => `${lugarDelPaso(v)}: there is no active credential for the provider.`,
    faceta_caida: (v) => `${lugarDelPaso(v)}: the facet did not answer the probe.`,
    faceta_inexistente: (v) => `${lugarDelPaso(v)}: the facet does not exist or is not active.`,
  },
  reglaPrevueloDesconocida: (v) => `${lugarDelPaso(v)}: the preflight rejected it by a rule this version does not know.`,
  detalleDelPrevuelo: (texto) => `Detail: ${texto}`,
  causasDeAborto: {
    fallo: (c) => (Number.isInteger(c.paso) ? `Stopped: step ${c.paso + 1} failed.` : 'Stopped: a step failed.'),
    cancelado: () => 'Stopped: it was cancelled.',
    kill_switch: () => 'Stopped: the kill switch was activated.',
    expirado: () => 'Expired: it went too long without progress.',
    desconocida: () => 'Stopped for a reason that was not recorded.',
  },
```

- [ ] **Step 5: Correr y ver verde**

Run: `$VITEST src/lib/moneda.test.js src/store/useJaxStore.pipelineContinued.test.js src/api/errores.test.js src/components/BottomBar/pipelineChain.test.js src/i18n/paridad.test.js src/components/BottomBar/PipelineModal.test.jsx`
Expected: todos PASS (paridad incluida; `PipelineModal` sigue verde con el clean-room refactorizado).

- [ ] **Step 6: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add frontend/src/lib/moneda.js frontend/src/lib/moneda.test.js frontend/src/store/useJaxStore.pipelineContinued.test.js frontend/src/store/useJaxStore.js frontend/src/api/errores.js frontend/src/api/errores.test.js frontend/src/i18n/es.js frontend/src/i18n/en.js frontend/src/components/BottomBar/pipelineChain.js frontend/src/components/BottomBar/pipelineChain.test.js && \
git commit -m "feat(mesa): montos, códigos y reglas del pre-vuelo, causas del aborto, pipeline_continued

Spec 2026-09-17 §6.2. Clean-room sobre pasos arbitrarios para continuar.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `PipelineModal` con pre-vuelo y `ConfirmarCostoDialogo`

**Files:**
- Create: `frontend/src/components/ConfirmarCostoDialogo.jsx`
- Modify: `frontend/src/components/BottomBar/PipelineModal.jsx`, `frontend/src/components/BottomBar/PipelineModal.test.jsx`
- Modify: `frontend/src/components/BottomBar/BottomBar.jsx:211-230`, `frontend/src/components/BottomBar/BottomBar.errores.test.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`

**Interfaces:**
- Consumes: `formatearUsd` (Task 8), `textoDeViolacion`, `textoDeErrorDeMesa` (Task 8), `POST /pipelines/preflight` (Task 6).
- Produces:
  - `ConfirmarCostoDialogo({ veredicto, enviando, onConfirmar, onCancelar })` — veredicto con `costo_max_usd, umbral_usd, pasos_costo[{paso, faceta, usd_max}]`
  - `PipelineModal({ objective, onClose, onSubmit })`: `onSubmit(body)` DEBE rechazar la promesa si la creación falla; el modal muestra el error y no se cierra. `body.costo_confirmado_usd` sólo si hubo confirmación.
  - i18n: `prevueloTitulo`, `confirmarCostoTitulo`, `confirmarCostoMensaje(monto, umbral)`, `confirmarCostoPaso(n, faceta, monto)`, `confirmarCostoPasoNoAcotado(n, faceta)`, `confirmarCostoNoAcotado`, `confirmarCostoBoton`.

- [ ] **Step 1: Escribir los tests que fallan**

In `frontend/src/components/BottomBar/PipelineModal.test.jsx`, replace:
```js
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
```
with:
```js
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
```
replace:
```js
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(),
  },
}))
```
with:
```js
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))
```
replace:
```js
import en from '../../i18n/en.js'
```
(the first occurrence, in the import block) with:
```js
import en from '../../i18n/en.js'
import { formatearUsd } from '../../lib/moneda'
import { textoDeViolacion } from '../../api/errores'

// Pre-vuelo aprobado y barato: el camino de todos los tests viejos.
const VEREDICTO_OK = {
  ok: true, violaciones: [], costo_max_usd: '0.10', umbral_usd: '0.50', requiere_confirmacion: false,
  pasos_costo: [{ paso: 0, faceta: 'hipatia', usd_max: '0.10' }],
}
```
replace:
```js
beforeEach(() => {
  vi.clearAllMocks()
  mockCatalog()
})
```
with:
```js
beforeEach(() => {
  vi.clearAllMocks()
  mockCatalog()
  api.post.mockResolvedValue({ data: VEREDICTO_OK })
})
```
and append at the end of the file:
```js
// Spec 2026-09-17 §6.2: nada se crea sin pre-vuelo; violaciones y rechazos se
// quedan DENTRO del modal; la confirmación de costo va en ventana propia.
describe('PipelineModal -- pre-vuelo y confirmación de costo', () => {
  const CARO = {
    ...VEREDICTO_OK, costo_max_usd: '0.60', requiere_confirmacion: true,
    pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.60' }],
  }

  async function listo(props = {}) {
    renderModal(props, { layout: 'chain' })
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
  }

  it('pregunta el pre-vuelo con los mismos pasos y, sin confirmación, crea sin costo_confirmado_usd', async () => {
    const onSubmit = vi.fn(() => Promise.resolve())
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const [url, cuerpo] = api.post.mock.calls[0]
    expect(url).toBe('/pipelines/preflight')
    expect(cuerpo.steps).toEqual(onSubmit.mock.calls[0][0].steps)
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('costo_confirmado_usd')
  })

  it('con violaciones las muestra en el modal, no crea y no cierra', async () => {
    const v = { paso: 4, faceta: 'kimi', regla: 'tope_insuficiente', detalle: 'tope 8000 < 16384' }
    api.post.mockResolvedValue({ data: { ...VEREDICTO_OK, ok: false, violaciones: [v] } })
    const onSubmit = vi.fn()
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled()
  })

  it('por encima del umbral pide confirmación en ventana propia y crea con el costo confirmado', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn(() => Promise.resolve())
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(formatearUsd('0.60', 'es'))
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0].costo_confirmado_usd).toBe('0.60')
  })

  it('cancelar la confirmación no crea y deja el modal abierto', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn()
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.cancel }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
    expect(screen.getByRole('dialog', { name: es.newPipelineTitle })).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('Escape con la confirmación abierta cierra sólo la confirmación', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onClose = vi.fn()
    await listo({ onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
    expect(onClose).not.toHaveBeenCalled()
  })

  it('un paso sin precio se avisa en la confirmación', async () => {
    api.post.mockResolvedValue({ data: { ...CARO, pasos_costo: [{ paso: 1, faceta: 'ada', usd_max: null }] } })
    await listo()
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(es.confirmarCostoNoAcotado)
    expect(dialogo).toHaveTextContent(es.confirmarCostoPasoNoAcotado(2, 'ada'))
  })

  it('si la creación falla, el error se ve en el modal y se puede reintentar', async () => {
    const rechazo = { response: { status: 409, data: { detail: { code: 'costo_supera_lo_aceptado' } } } }
    const onSubmit = vi.fn(() => Promise.reject(rechazo))
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled()
  })

  it('si el pre-vuelo no responde, el error se ve en el modal y el botón vuelve', async () => {
    api.post.mockRejectedValue(new Error('network'))
    await listo()
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.errorPipeline)
    expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled()
  })
})
```

In `frontend/src/components/BottomBar/BottomBar.errores.test.jsx`, replace:
```js
import api from '../../api/client'
```
with:
```js
// El modal real no importa acá: se capturan sus props para llamar a onSubmit.
vi.mock('./PipelineModal', () => ({
  default: (props) => { globalThis.__propsDelModalDePipeline = props; return null },
}))

import api from '../../api/client'
```
and append at the end of the file:
```js
describe('BottomBar -- crear pipeline (spec 2026-09-17 §6.2)', () => {
  it('si crear falla, relanza el error para el modal y no ensucia el chat', async () => {
    globalThis.__propsDelModalDePipeline = undefined
    render(<I18nProvider><BottomBar /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: es.modePipeline }))
    const caja = screen.getByRole('textbox')
    fireEvent.change(caja, { target: { value: 'investigar leyes' } })
    fireEvent.keyDown(caja, { key: 'Enter' })
    await waitFor(() => expect(globalThis.__propsDelModalDePipeline).toBeTruthy())
    const rechazo = { response: { status: 409, data: { detail: { code: 'confirmacion_de_costo' } } } }
    api.post.mockRejectedValue(rechazo)
    await expect(globalThis.__propsDelModalDePipeline.onSubmit({ steps: [], mode: 'autonomous' })).rejects.toBe(rechazo)
    expect(useJaxStore.getState().messages).toEqual([])
  })
})
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$VITEST src/components/BottomBar/PipelineModal.test.jsx src/components/BottomBar/BottomBar.errores.test.jsx`
Expected (9 tests nuevos): `TypeError: Cannot read properties of undefined (reading '0')` en el primero (no hay llamada a `api.post`); los de violaciones/confirmación con `Unable to find role="dialog" and name "Confirmar costo máximo"` o `expected "spy" to not be called`; BottomBar con `promise resolved "undefined" instead of rejecting` y mensajes en el chat. Los tests viejos del modal siguen verdes.

- [ ] **Step 3: i18n**

In `frontend/src/i18n/es.js`, replace:
```js
  planAndExecute: 'Planificar y ejecutar',
```
with:
```js
  planAndExecute: 'Planificar y ejecutar',
  // Pre-vuelo y confirmación de costo (spec 2026-09-17 §6.2)
  prevueloTitulo: 'El pre-vuelo encontró problemas. No se gastó nada:',
  confirmarCostoTitulo: 'Confirmar costo máximo',
  confirmarCostoMensaje: (monto, umbral) =>
    `Esto puede costar hasta ${monto}. Se pide confirmación por encima de ${umbral} o cuando un paso no tiene precio.`,
  confirmarCostoPaso: (n, faceta, monto) => `Paso ${n} · ${faceta}: hasta ${monto}`,
  confirmarCostoPasoNoAcotado: (n, faceta) => `Paso ${n} · ${faceta}: sin precio en el catálogo`,
  confirmarCostoNoAcotado: 'Hay pasos sin precio: el costo real puede superar el máximo mostrado.',
  confirmarCostoBoton: 'Confirmar y correr',
```
In `frontend/src/i18n/en.js`, replace the line `  planAndExecute: '<valor en inglés>',` (find with `grep -n "planAndExecute" frontend/src/i18n/en.js`) keeping it, and add right after it:
```js
  // Preflight and cost confirmation (spec 2026-09-17 §6.2)
  prevueloTitulo: 'The preflight found problems. Nothing was spent:',
  confirmarCostoTitulo: 'Confirm maximum cost',
  confirmarCostoMensaje: (monto, umbral) =>
    `This can cost up to ${monto}. Confirmation is required above ${umbral} or when a step has no price.`,
  confirmarCostoPaso: (n, faceta, monto) => `Step ${n} · ${faceta}: up to ${monto}`,
  confirmarCostoPasoNoAcotado: (n, faceta) => `Step ${n} · ${faceta}: no price in the catalog`,
  confirmarCostoNoAcotado: 'Some steps have no price: the real cost can exceed the maximum shown.',
  confirmarCostoBoton: 'Confirm and run',
```

- [ ] **Step 4: `ConfirmarCostoDialogo`**

Create `frontend/src/components/ConfirmarCostoDialogo.jsx`:
```jsx
import { useI18n } from '../i18n/index.jsx'
import Dialogo from './Dialogo'
import { formatearUsd } from '../lib/moneda'

// Confirmación de costo máximo (spec 2026-09-17 §6.2). Consentimiento humano,
// no una acción destructiva: sin suma (no es ConfirmacionSuma). Va sobre
// Dialogo (portal, inert, foco, Escape = cancelar). Quien la abre encima de
// otro Dialogo le pasa cerrable={false} mientras está abierta (desvío DV-12).
export default function ConfirmarCostoDialogo({ veredicto, enviando, onConfirmar, onCancelar }) {
  const { t, lang } = useI18n()
  const pasos = veredicto.pasos_costo || []
  const sinPrecio = (p) => p.usd_max === null || p.usd_max === undefined
  return (
    <Dialogo idTitulo="confirmar-costo-titulo" titulo={t.confirmarCostoTitulo}
      claseTitulo="text-sm font-semibold text-texto mb-2" onCerrar={onCancelar}>
      <p className="text-sm text-texto-suave mb-3">
        {t.confirmarCostoMensaje(formatearUsd(veredicto.costo_max_usd, lang), formatearUsd(veredicto.umbral_usd, lang))}
      </p>
      <ul className="space-y-1 mb-3">
        {pasos.map((p) => (
          <li key={`${p.paso}-${p.faceta}`} className="text-xs text-texto">
            {sinPrecio(p)
              ? t.confirmarCostoPasoNoAcotado(p.paso + 1, p.faceta)
              : t.confirmarCostoPaso(p.paso + 1, p.faceta, formatearUsd(p.usd_max, lang))}
          </li>
        ))}
      </ul>
      {pasos.some(sinPrecio) && <p className="text-xs text-aviso mb-3">{t.confirmarCostoNoAcotado}</p>}
      <div className="flex gap-2 justify-end pt-2">
        <button type="button" onClick={onCancelar}
          className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">
          {t.cancel}
        </button>
        <button type="button" onClick={onConfirmar} disabled={enviando}
          className="px-4 py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
          {t.confirmarCostoBoton}
        </button>
      </div>
    </Dialogo>
  )
}
```

- [ ] **Step 5: `PipelineModal`**

In `frontend/src/components/BottomBar/PipelineModal.jsx`, replace:
```js
import Dialogo from '../Dialogo'
```
with:
```js
import Dialogo from '../Dialogo'
import AlertaError from '../AlertaError'
import ConfirmarCostoDialogo from '../ConfirmarCostoDialogo'
import { textoDeErrorDeMesa, textoDeViolacion } from '../../api/errores'
```

Replace:
```js
  const [motorChoices, setMotorChoices] = useState({})  // {facet_id: motor_key | ''}
```
with:
```js
  const [motorChoices, setMotorChoices] = useState({})  // {facet_id: motor_key | ''}
  // Pre-vuelo (spec 2026-09-17 §6.2): lo que devolvió y lo que falta confirmar.
  const [violaciones, setViolaciones] = useState([])
  const [errorEnvio, setErrorEnvio] = useState(null)
  const [pendiente, setPendiente] = useState(null)  // {body, veredicto} esperando confirmación
```

Replace the whole `handleSubmit` function (from `  async function handleSubmit() {` through its closing `  }` right before `  const PIPELINE_MODES = [`) with:
```js
  function armarCuerpo() {
    const steps = layout === 'chain'
      ? buildChainSteps(objective, chainFacets, t.chainInstructions)
      : buildSteps(selected, objective, FACET_OPTIONS, motorChoices, motorsByKey)
    return { name: t.pipelineName(objective), objective, mode, max_steps: steps.length, steps }
  }

  // Los rechazos se quedan DENTRO del modal: cerrarlo perdería lo elegido.
  function mostrarError(err) {
    const detail = err?.response?.data?.detail
    if (detail?.code === 'prevuelo_rechazado' && Array.isArray(detail.violaciones)) {
      setViolaciones(detail.violaciones)
      return
    }
    setErrorEnvio(textoDeErrorDeMesa(t, err, t.errorPipeline))
  }

  // onSubmit rechaza si la creación falla (BottomBar.handlePipelineSubmit).
  async function crear(body, costo) {
    await onSubmit(costo == null ? body : { ...body, costo_confirmado_usd: costo })
    onClose()
  }

  async function handleSubmit() {
    if (!catalogReady || submitting) return
    if (layout === 'chain' ? chainBlocked : selected.length === 0) return
    const body = armarCuerpo()
    setSubmitting(true)
    setViolaciones([])
    setErrorEnvio(null)
    try {
      const { data } = await api.post('/pipelines/preflight', { steps: body.steps })
      if (!data.ok) {
        setViolaciones(data.violaciones || [])
        return
      }
      if (data.requiere_confirmacion) {
        setPendiente({ body, veredicto: data })
        return
      }
      await crear(body, null)
    } catch (err) {
      mostrarError(err)
    } finally {
      setSubmitting(false)
    }
  }

  async function confirmarCosto() {
    const { body, veredicto } = pendiente
    setSubmitting(true)
    try {
      await crear(body, veredicto.costo_max_usd)
    } catch (err) {
      setPendiente(null)
      mostrarError(err)
    } finally {
      setSubmitting(false)
    }
  }
```

Replace:
```jsx
    <Dialogo idTitulo="pipeline-modal-titulo" titulo={t.newPipelineTitle}
      claseTitulo="text-sm font-bold text-texto uppercase tracking-widest" onCerrar={onClose}>
```
with:
```jsx
    <Dialogo idTitulo="pipeline-modal-titulo" titulo={t.newPipelineTitle}
      claseTitulo="text-sm font-bold text-texto uppercase tracking-widest" onCerrar={onClose}
      cerrable={!pendiente}>
```

Replace:
```jsx
        {/* Botones */}
        <div className="flex gap-2">
```
with:
```jsx
        {violaciones.length > 0 && (
          <div role="alert" className="mb-3">
            <p className="text-[11px] font-semibold text-peligro mb-1">{t.prevueloTitulo}</p>
            {violaciones.map((v, i) => (
              <p key={`${v.paso}-${v.regla}-${i}`} className="text-[11px] text-peligro">{textoDeViolacion(t, v)}</p>
            ))}
          </div>
        )}
        {errorEnvio && <AlertaError className="mb-2 text-[11px]">{errorEnvio}</AlertaError>}

        {/* Botones */}
        <div className="flex gap-2">
```

Replace the closing of the component:
```jsx
            {submitting ? t.starting : t.planAndExecute}
          </button>
        </div>
    </Dialogo>
```
with:
```jsx
            {submitting ? t.starting : t.planAndExecute}
          </button>
        </div>
        {pendiente && (
          <ConfirmarCostoDialogo veredicto={pendiente.veredicto} enviando={submitting}
            onConfirmar={confirmarCosto} onCancelar={() => setPendiente(null)} />
        )}
    </Dialogo>
```

- [ ] **Step 6: `BottomBar.handlePipelineSubmit` relanza**

In `frontend/src/components/BottomBar/BottomBar.jsx`, replace the whole `handlePipelineSubmit` function with:
```js
  // PipelineModal muestra los errores de la creación dentro de sí mismo
  // (spec 2026-09-17 §6.2): acá sólo se crea y se anuncia. Un fallo se
  // relanza para que el modal no se cierre ni el chat se ensucie.
  async function handlePipelineSubmit(pipelineBody) {
    const { data } = await api.post('/pipelines', pipelineBody)
    addMessage({
      id: Date.now().toString(),
      facet: 'user',
      content: pipelineObjective,
      timestamp: new Date().toISOString(),
    })
    const pid = (data.pipeline_id || '').slice(0, 12)
    addMessage({
      id: `pipeline-${data.pipeline_id || Date.now()}`,
      facet: 'jacobs',
      content: t.pipelineStarted(pid, pipelineBody.mode, pipelineBody.steps?.length || 'auto'),
      timestamp: new Date().toISOString(),
    })
  }
```
Si después de esto `agregarError` o `textoDeErrorDeMesa` quedan sin usar en `BottomBar.jsx`, verificarlo con `grep -n "agregarError\|textoDeErrorDeMesa" frontend/src/components/BottomBar/BottomBar.jsx` (hoy los usa también el chat: quedan).

- [ ] **Step 7: Correr y ver verde**

Run: `$VITEST src/components/BottomBar src/components/Dialogo.test.jsx src/politica src/i18n src/tema`
Expected: todos PASS (incluye escaneo de diálogos del navegador, `modales.test.js` y paridad).

- [ ] **Step 8: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add frontend/src/components/ConfirmarCostoDialogo.jsx frontend/src/components/BottomBar/PipelineModal.jsx frontend/src/components/BottomBar/PipelineModal.test.jsx frontend/src/components/BottomBar/BottomBar.jsx frontend/src/components/BottomBar/BottomBar.errores.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js && \
git commit -m "feat(mesa): pre-vuelo dentro del modal de pipeline y confirmación de costo en ventana propia

Spec 2026-09-17 §6.2: violaciones y rechazos quedan en el modal; Escape con la
confirmación abierta cierra sólo la confirmación (desvío DV-12).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `RightPanel` con detenidos y `ContinuarPipelineModal`

**Files:**
- Create: `frontend/src/components/RightPanel/ContinuarPipelineModal.jsx`, `frontend/src/components/RightPanel/ContinuarPipelineModal.test.jsx`
- Modify: `frontend/src/components/RightPanel/RightPanel.jsx`, `frontend/src/components/RightPanel/RightPanel.test.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`

**Interfaces:**
- Consumes: `GET /pipelines` con `causa` y `GET /pipelines/{id}`, `POST /pipelines/{id}/continue/preflight` y `/continue` (Task 7); `ConfirmarCostoDialogo` (Task 9); `facetOptionsFor`, `cleanroomViolationsDePasos` (Task 8); `formatearUsd`, `textoDeViolacion`, `textoDeErrorDeMesa`.
- Produces:
  - `ContinuarPipelineModal({ pipeline: {pipeline_id, name}, onClose, onContinuado })` — manda `{reasignar}` (sólo facetas cambiadas, claves string del índice) y `costo_confirmado_usd` si confirmó.
  - i18n: `continuablesTitulo`, `continuablesError`, `continuarPipeline`, `continuarTitulo`, `continuarCargando`, `continuarErrorCarga`, `continuarPasoReusado(n, faceta)`, `continuarPasoACorrer(n, capability)`, `continuarCostoMax(monto)`, `continuarCleanroom(n, faceta, dep)`, `continuarNoContinuable`, `continuarBoton`.

- [ ] **Step 1: Escribir los tests que fallan**

Create `frontend/src/components/RightPanel/ContinuarPipelineModal.test.jsx`:
```jsx
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Continuar un pipeline detenido (spec 2026-09-17 §6.2): pasos reusados como
// listos, pasos a correr con selector de faceta (mismas opciones y clean-room
// que PipelineModal), costo máximo del pre-vuelo de continue y la misma
// confirmación en ventana propia.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import ContinuarPipelineModal from './ContinuarPipelineModal'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import { formatearUsd } from '../../lib/moneda'

const PASOS = [
  { step_index: 0, facet: 'hipatia', capability: 'research', depends_on: [], status: 'completed' },
  { step_index: 1, facet: 'ada', capability: 'design', depends_on: [0], status: 'completed' },
  { step_index: 2, facet: 'jekyll', capability: 'critique', depends_on: [0, 1], status: 'completed' },
  { step_index: 3, facet: 'ada', capability: 'reconcile', depends_on: [1, 2], status: 'completed' },
  { step_index: 4, facet: 'kimi', capability: 'generate', depends_on: [3], status: 'failed' },
  { step_index: 5, facet: 'thot', capability: 'validate_consistency', depends_on: [0, 2, 3, 4], status: 'pending' },
]
const VEREDICTO = {
  ok: true, violaciones: [], costo_max_usd: '0.30', umbral_usd: '0.50', requiere_confirmacion: false,
  pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.25' }, { paso: 5, faceta: 'thot', usd_max: '0.05' }],
}
const CONTINUABLE = { continuable: true, motivo: null, pasos_a_correr: [4, 5], pasos_reusados: [0, 1, 2, 3], veredicto: VEREDICTO }
const PIPELINE = { pipeline_id: 'p-1', name: 'leyes de energía', status: 'aborted' }

function mockPrevio(previo) {
  api.post.mockImplementation((url) => Promise.resolve({
    data: url.endsWith('/continue/preflight') ? previo : { pipeline_id: 'p-1', status: 'running' },
  }))
}

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  api.get.mockImplementation((url) => Promise.resolve({
    data: url === '/motors/capabilities'
      ? { capabilities: [{ key: 'generate', allowed_motors: ['kimi', 'jax_local'] }], motors: [] }
      : { pipeline: { pipeline_id: 'p-1' }, steps: PASOS },
  }))
  mockPrevio(CONTINUABLE)
})

function abrir() {
  const onClose = vi.fn()
  const onContinuado = vi.fn()
  render(<I18nProvider><ContinuarPipelineModal pipeline={PIPELINE} onClose={onClose} onContinuado={onContinuado} /></I18nProvider>)
  return { onClose, onContinuado }
}

const selectDe = (n, cap) => screen.getByLabelText(es.continuarPasoACorrer(n, cap))

describe('ContinuarPipelineModal', () => {
  it('muestra los pasos reusados como listos, los que faltan con selector y el costo máximo', async () => {
    abrir()
    expect(await screen.findByText(es.continuarPasoReusado(1, 'hipatia'))).toBeInTheDocument()
    expect(selectDe(5, 'generate')).toHaveValue('kimi')
    expect(screen.getByText(es.continuarCostoMax(formatearUsd('0.30', 'es')))).toBeInTheDocument()
  })

  it('cambiar la faceta de un paso vuelve a pedir el pre-vuelo con la reasignación', async () => {
    abrir()
    fireEvent.change(await waitFor(() => selectDe(5, 'generate')), { target: { value: 'ada' } })
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/p-1/continue/preflight', { reasignar: { 4: 'ada' } }))
  })

  it('un auditor igual a quien produjo avisa y bloquea Continuar', async () => {
    abrir()
    fireEvent.change(await waitFor(() => selectDe(6, 'validate_consistency')), { target: { value: 'ada' } })
    expect(await screen.findByText(es.continuarCleanroom(6, 'ada', 4))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.continuarBoton })).toBeDisabled()
  })

  it('por encima del umbral confirma en ventana propia y continúa con el costo confirmado', async () => {
    mockPrevio({ ...CONTINUABLE, veredicto: { ...VEREDICTO, requiere_confirmacion: true } })
    const { onClose, onContinuado } = abrir()
    await screen.findByText(es.continuarPasoReusado(1, 'hipatia'))
    fireEvent.click(screen.getByRole('button', { name: es.continuarBoton }))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/p-1/continue', { reasignar: {}, costo_confirmado_usd: '0.30' }))
    await waitFor(() => expect(onContinuado).toHaveBeenCalled())
    expect(onClose).toHaveBeenCalled()
  })

  it('un pipeline que no se puede continuar lo dice y no deja continuar', async () => {
    mockPrevio({ continuable: false, motivo: 'status completed', pasos_a_correr: [], pasos_reusados: [], veredicto: null })
    abrir()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.continuarNoContinuable)
    expect(screen.getByRole('button', { name: es.continuarBoton })).toBeDisabled()
  })

  it('si continuar falla, el error se ve en la ventana y no se cierra', async () => {
    api.post.mockImplementation((url) => (url.endsWith('/continue/preflight')
      ? Promise.resolve({ data: CONTINUABLE })
      : Promise.reject({ response: { status: 409, data: { detail: { code: 'costo_supera_lo_aceptado' } } } })))
    const { onClose } = abrir()
    await screen.findByText(es.continuarPasoReusado(1, 'hipatia'))
    fireEvent.click(screen.getByRole('button', { name: es.continuarBoton }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
    expect(onClose).not.toHaveBeenCalled()
  })
})
```

In `frontend/src/components/RightPanel/RightPanel.test.jsx`, replace:
```js
      expect(api.get).not.toHaveBeenCalled()
```
with:
```js
      // 2026-09-17: el panel pide GET /pipelines (detenidos); /audit sigue sin pedirse.
      expect(api.get).not.toHaveBeenCalledWith('/audit')
```
and append at the end of the file:
```js
describe('RightPanel -- pipelines detenidos que se pueden continuar (spec 2026-09-17 §6.2)', () => {
  const CAUSA_FALLO = { tipo: 'fallo', paso: 4, detalle: 'Salida cortada' }
  const LISTA = { pipelines: [
    { pipeline_id: 'p-ab', name: 'leyes', status: 'aborted', causa: CAUSA_FALLO },
    { pipeline_id: 'p-ex', name: 'vencido', status: 'expired', causa: { tipo: 'expirado' } },
    { pipeline_id: 'p-ok', name: 'terminado', status: 'completed', causa: null },
  ] }

  beforeEach(() => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url === '/pipelines' ? LISTA : { events: [] } }))
  })

  it('un abortado muestra su causa y el botón Continuar; un completado no aparece', async () => {
    renderPanel()
    expect(await screen.findByText(`${es.causasDeAborto.fallo(CAUSA_FALLO)} ${es.respuestaDelServicio('Salida cortada')}`)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: es.continuarPipeline })).toHaveLength(2)
    expect(screen.queryByText('terminado')).not.toBeInTheDocument()
  })

  it('un vencido muestra la causa expirado', async () => {
    renderPanel()
    expect(await screen.findByText(es.causasDeAborto.expirado({ tipo: 'expirado' }))).toBeInTheDocument()
  })

  it('Continuar abre la ventana de continuar', async () => {
    api.post.mockReturnValue(new Promise(() => {}))
    renderPanel()
    const [boton] = await screen.findAllByRole('button', { name: es.continuarPipeline })
    fireEvent.click(boton)
    expect(await screen.findByRole('dialog', { name: es.continuarTitulo })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$VITEST src/components/RightPanel`
Expected (9 tests nuevos): `ContinuarPipelineModal.test.jsx` falla la importación (`Failed to resolve import "./ContinuarPipelineModal"`); los 3 de RightPanel con `Unable to find an element with the text: Se detuvo: falló el paso 5. ...` / `Unable to find role="button" and name "Continuar"`. Los tests viejos del panel siguen verdes.

- [ ] **Step 3: i18n**

In `frontend/src/i18n/es.js`, replace:
```js
  cancelError: 'No se pudo cancelar el pipeline. Probá de nuevo.',
```
with:
```js
  cancelError: 'No se pudo cancelar el pipeline. Probá de nuevo.',
  // Continuar un pipeline detenido (spec 2026-09-17 §6.2)
  continuablesTitulo: 'Detenidos',
  continuablesError: 'No se pudo cargar la lista de pipelines detenidos.',
  continuarPipeline: 'Continuar',
  continuarTitulo: 'Continuar pipeline',
  continuarCargando: 'Cargando pasos y costo…',
  continuarErrorCarga: 'No se pudo preparar la continuación.',
  continuarPasoReusado: (n, faceta) => `✓ Paso ${n} · ${faceta}: listo, se reutiliza`,
  continuarPasoACorrer: (n, capability) => `Paso ${n} · ${capability}`,
  continuarCostoMax: (monto) => `Costo máximo de lo que falta: ${monto}`,
  continuarCleanroom: (n, faceta, dep) => `Paso ${n}: ${faceta} no puede auditar lo que produjo en el paso ${dep}. Elige otra faceta.`,
  continuarNoContinuable: 'Este pipeline no se puede continuar en su estado actual.',
  continuarBoton: 'Continuar',
```
In `frontend/src/i18n/en.js`, find `  cancelError:` (`grep -n "cancelError" frontend/src/i18n/en.js`) and add right after that line:
```js
  // Continue a stopped pipeline (spec 2026-09-17 §6.2)
  continuablesTitulo: 'Stopped',
  continuablesError: 'The list of stopped pipelines could not be loaded.',
  continuarPipeline: 'Continue',
  continuarTitulo: 'Continue pipeline',
  continuarCargando: 'Loading steps and cost…',
  continuarErrorCarga: 'The continuation could not be prepared.',
  continuarPasoReusado: (n, faceta) => `✓ Step ${n} · ${faceta}: ready, reused`,
  continuarPasoACorrer: (n, capability) => `Step ${n} · ${capability}`,
  continuarCostoMax: (monto) => `Maximum cost of what is left: ${monto}`,
  continuarCleanroom: (n, faceta, dep) => `Step ${n}: ${faceta} cannot audit what it produced in step ${dep}. Choose another facet.`,
  continuarNoContinuable: 'This pipeline cannot be continued in its current state.',
  continuarBoton: 'Continue',
```

- [ ] **Step 4: `ContinuarPipelineModal`**

Create `frontend/src/components/RightPanel/ContinuarPipelineModal.jsx`:
```jsx
import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import Dialogo from '../Dialogo'
import AlertaError from '../AlertaError'
import ConfirmarCostoDialogo from '../ConfirmarCostoDialogo'
import { facetOptionsFor, cleanroomViolationsDePasos } from '../BottomBar/pipelineChain'
import { textoDeErrorDeMesa, textoDeViolacion } from '../../api/errores'
import { formatearUsd } from '../../lib/moneda'

// Continuar un pipeline abortado o vencido (spec 2026-09-17 §6.2). Los pasos
// con resultado se reutilizan; los que faltan se pueden cambiar de faceta con
// las mismas opciones y el mismo clean-room que PipelineModal. Cada cambio
// vuelve a pedir el pre-vuelo de continue: Jacobs decide qué se reusa y cuánto
// cuesta lo que falta.
const CLASE_SELECT = 'text-xs bg-hundido border border-borde-control rounded px-2 py-1 text-texto focus:outline-none focus:border-foco'

export default function ContinuarPipelineModal({ pipeline, onClose, onContinuado }) {
  const { t, lang } = useI18n()
  const id = pipeline.pipeline_id
  const [pasos, setPasos] = useState(null)
  const [capabilities, setCapabilities] = useState(null)
  const [errorCarga, setErrorCarga] = useState(false)
  const [reasignar, setReasignar] = useState({})
  const [estado, setEstado] = useState(null)
  const [error, setError] = useState(null)
  const [enviando, setEnviando] = useState(false)
  const [confirmando, setConfirmando] = useState(false)
  // Mientras se recalcula tras una reasignación, la lista queda a la vista
  // (no parpadea) pero Continuar se bloquea: el costo mostrado es el viejo.
  const [calculando, setCalculando] = useState(false)
  const pedido = useRef(0)

  useEffect(() => {
    Promise.all([api.get(`/pipelines/${id}`), api.get('/motors/capabilities')])
      .then(([p, c]) => {
        const ordenados = [...(p.data.steps || [])].sort((a, b) => a.step_index - b.step_index)
        setPasos(ordenados)
        const porCapability = {}
        for (const cap of c.data.capabilities || []) porCapability[cap.key] = cap.allowed_motors
        setCapabilities(porCapability)
      })
      .catch(() => setErrorCarga(true))
  }, [id])

  // Una respuesta vieja (de una reasignación anterior) no pisa la vigente.
  const claveReasignar = JSON.stringify(reasignar)
  useEffect(() => {
    const numero = ++pedido.current
    setCalculando(true)
    setError(null)
    api.post(`/pipelines/${id}/continue/preflight`, { reasignar })
      .then(({ data }) => {
        if (numero !== pedido.current) return
        setEstado(data)
        setCalculando(false)
      })
      .catch((err) => {
        if (numero !== pedido.current) return
        setError(textoDeErrorDeMesa(t, err, t.continuarErrorCarga))
        setCalculando(false)
      })
  }, [id, claveReasignar])

  const facetaDe = (p) => reasignar[String(p.step_index)] ?? p.facet
  const vigentes = (pasos || []).map((p) => ({ facet: facetaDe(p), capability: p.capability, depends_on: Array.isArray(p.depends_on) ? p.depends_on : [] }))
  const cleanroom = cleanroomViolationsDePasos(vigentes)
  const reusados = new Set(estado?.pasos_reusados || [])
  const veredicto = estado?.veredicto
  const violaciones = veredicto && !veredicto.ok ? veredicto.violaciones || [] : []
  const bloqueado = enviando || calculando || !estado?.continuable || !veredicto?.ok || cleanroom.length > 0
  const cargando = !errorCarga && (pasos === null || capabilities === null || (estado === null && !error))

  function elegir(paso, faceta) {
    setReasignar((r) => {
      const siguiente = { ...r }
      if (faceta === paso.facet) delete siguiente[String(paso.step_index)]
      else siguiente[String(paso.step_index)] = faceta
      return siguiente
    })
  }

  function opciones(paso) {
    return [...new Set([paso.facet, ...facetOptionsFor({ capability: paso.capability }, capabilities || {})])]
  }

  async function continuar(costo) {
    setEnviando(true)
    setError(null)
    try {
      const cuerpo = costo == null ? { reasignar } : { reasignar, costo_confirmado_usd: costo }
      const { data } = await api.post(`/pipelines/${id}/continue`, cuerpo)
      onContinuado(data)
      onClose()
    } catch (err) {
      setConfirmando(false)
      setError(textoDeErrorDeMesa(t, err, t.continuarErrorCarga))
    } finally {
      setEnviando(false)
    }
  }

  function alPulsarContinuar() {
    if (veredicto?.requiere_confirmacion) setConfirmando(true)
    else continuar(null)
  }

  return (
    <Dialogo idTitulo="continuar-pipeline-titulo" titulo={t.continuarTitulo} onCerrar={onClose}
      cerrable={!confirmando} className="max-w-lg">
      <p className="text-xs text-texto-tenue -mt-3 mb-4 truncate">{pipeline.name}</p>

      {errorCarga && <AlertaError className="mb-2 text-xs">{t.continuarErrorCarga}</AlertaError>}
      {cargando && <p className="mb-2 text-xs text-texto-tenue">{t.continuarCargando}</p>}

      {pasos && capabilities && estado && (
        <ol className="space-y-1.5 mb-3">
          {pasos.map((p) => (
            <li key={p.step_index} className="flex items-center gap-2 p-2 rounded-lg border border-borde bg-hundido">
              {reusados.has(p.step_index) ? (
                <span className="text-xs text-exito">{t.continuarPasoReusado(p.step_index + 1, p.facet)}</span>
              ) : (
                <>
                  <span className="text-xs text-texto flex-1">{t.continuarPasoACorrer(p.step_index + 1, p.capability)}</span>
                  <select aria-label={t.continuarPasoACorrer(p.step_index + 1, p.capability)} className={CLASE_SELECT}
                    value={facetaDe(p)} onChange={(e) => elegir(p, e.target.value)}>
                    {opciones(p).map((f) => <option key={f} value={f}>{f}</option>)}
                  </select>
                </>
              )}
            </li>
          ))}
        </ol>
      )}

      {cleanroom.map((v) => (
        <p key={`${v.paso}-${v.dependsOn}`} className="mb-1 text-[11px] text-peligro">
          {t.continuarCleanroom(v.paso + 1, v.facet, v.dependsOn + 1)}
        </p>
      ))}

      {estado && !estado.continuable && (
        <AlertaError className="mb-2 text-xs">
          {estado.motivo ? `${t.continuarNoContinuable} ${t.respuestaDelServicio(estado.motivo)}` : t.continuarNoContinuable}
        </AlertaError>
      )}

      {veredicto?.ok && !calculando && (
        <p className="mb-2 text-xs text-texto">{t.continuarCostoMax(formatearUsd(veredicto.costo_max_usd, lang))}</p>
      )}

      {violaciones.length > 0 && (
        <div role="alert" className="mb-2">
          <p className="text-[11px] font-semibold text-peligro mb-1">{t.prevueloTitulo}</p>
          {violaciones.map((v, i) => (
            <p key={`${v.paso}-${v.regla}-${i}`} className="text-[11px] text-peligro">{textoDeViolacion(t, v)}</p>
          ))}
        </div>
      )}

      {error && <AlertaError className="mb-2 text-xs">{error}</AlertaError>}

      <div className="flex gap-2">
        <button onClick={onClose}
          className="flex-1 py-2 rounded-lg text-xs font-semibold bg-hundido text-texto-suave hover:text-texto border border-borde transition-colors">
          {t.cancel}
        </button>
        <button onClick={alPulsarContinuar} disabled={bloqueado}
          className="flex-1 py-2 rounded-lg text-xs font-bold bg-accion hover:bg-accion-hover text-sobre-color transition-colors disabled:opacity-40">
          {t.continuarBoton}
        </button>
      </div>

      {confirmando && veredicto && (
        <ConfirmarCostoDialogo veredicto={veredicto} enviando={enviando}
          onConfirmar={() => continuar(veredicto.costo_max_usd)} onCancelar={() => setConfirmando(false)} />
      )}
    </Dialogo>
  )
}
```

- [ ] **Step 5: `RightPanel`**

In `frontend/src/components/RightPanel/RightPanel.jsx`, replace:
```js
import { memo, useState } from 'react'
```
with:
```js
import { memo, useEffect, useState } from 'react'
```
replace:
```js
import AlertaError from '../AlertaError'
```
with:
```js
import AlertaError from '../AlertaError'
import ContinuarPipelineModal from './ContinuarPipelineModal'

// Estados que se pueden continuar (spec 2026-09-17 §5.2 regla 2).
const CONTINUABLES = ['aborted', 'expired']

// Causa del aborto (GET /api/pipelines, `causa`): texto por tipo; un tipo que
// esta versión no conoce cae en `desconocida`. El detalle va como dato.
function textoDeCausa(t, causa) {
  const tipo = causa?.tipo
  const traducir = typeof tipo === 'string' && Object.hasOwn(t.causasDeAborto, tipo)
    ? t.causasDeAborto[tipo] : t.causasDeAborto.desconocida
  const base = traducir(causa || {})
  return causa?.detalle ? `${base} ${t.respuestaDelServicio(causa.detalle)}` : base
}
```
replace:
```js
  const [aviso, setAviso] = useState(null)

  const pipelines = Object.values(activePipelines)
```
with:
```js
  const [aviso, setAviso] = useState(null)
  const [continuables, setContinuables] = useState([])
  const [errorContinuables, setErrorContinuables] = useState(false)
  const [aContinuar, setAContinuar] = useState(null)
  const [recarga, setRecarga] = useState(0)

  const pipelines = Object.values(activePipelines)
  // La lista de detenidos se vuelve a pedir cuando cambia el estado de algún
  // pipeline del store (termina, se aborta, se continúa) o tras continuar.
  const huella = pipelines.map((p) => `${p.pipeline_id}:${p.status}`).join('|')

  useEffect(() => {
    let vigente = true
    api.get('/pipelines')
      .then(({ data }) => {
        if (!vigente) return
        setErrorContinuables(false)
        setContinuables((data?.pipelines || []).filter((p) => CONTINUABLES.includes(p.status)))
      })
      .catch(() => { if (vigente) setErrorContinuables(true) })
    return () => { vigente = false }
  }, [huella, recarga])
```
replace:
```jsx
              {pipelines.length > 1 && (
                <div className="mt-3 text-xs text-texto-tenue">
                  {t.pipelinesAdditional(pipelines.length - 1)}
                </div>
              )}
            </div>
          )}
        </div>
```
with:
```jsx
              {pipelines.length > 1 && (
                <div className="mt-3 text-xs text-texto-tenue">
                  {t.pipelinesAdditional(pipelines.length - 1)}
                </div>
              )}
            </div>
          )}

          {(continuables.length > 0 || errorContinuables) && (
            <div className="p-3 border-t border-borde">
              <p className="text-xs font-semibold text-texto-suave uppercase tracking-wider mb-2">{t.continuablesTitulo}</p>
              {errorContinuables && <AlertaError className="text-xs">{t.continuablesError}</AlertaError>}
              {continuables.map((p) => (
                <div key={p.pipeline_id} className="mb-2 p-2 rounded-lg border border-borde bg-superficie">
                  <div className="text-xs font-semibold text-texto truncate">{p.name}</div>
                  <div className="text-xs text-peligro mt-0.5">{textoDeCausa(t, p.causa)}</div>
                  <button onClick={() => setAContinuar(p)}
                    className="mt-2 w-full py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-xs font-semibold transition-colors">
                    {t.continuarPipeline}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
```
replace:
```jsx
          <AuditLog />
        </div>
      )}
    </div>
  )
}
```
with:
```jsx
          <AuditLog />
        </div>
      )}
      {aContinuar && (
        <ContinuarPipelineModal pipeline={aContinuar} onClose={() => setAContinuar(null)}
          onContinuado={() => setRecarga((n) => n + 1)} />
      )}
    </div>
  )
}
```

- [ ] **Step 6: Correr y ver verde**

Run: `$VITEST src/components/RightPanel src/politica src/i18n src/tema src/components/Dialogo.test.jsx`
Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add frontend/src/components/RightPanel/ContinuarPipelineModal.jsx frontend/src/components/RightPanel/ContinuarPipelineModal.test.jsx frontend/src/components/RightPanel/RightPanel.jsx frontend/src/components/RightPanel/RightPanel.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js && \
git commit -m "feat(mesa): continuar pipelines detenidos desde el panel, con cambio de faceta

Spec 2026-09-17 §6.2: causa del aborto, pasos reusados, selector con clean-room,
costo máximo del pre-vuelo de continue y confirmación en ventana propia.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Panel de ajustes — umbral de confirmación

**Files:**
- Modify: `frontend/src/pages/admin/AdminSettings.jsx`, `frontend/src/pages/admin/AdminSettings.test.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`

**Interfaces:**
- Consumes: `GET /api/admin/config` con `limites.pipeline_confirmar_usd = {min, max, decimales}` (Task 4).
- Produces: campo `#ajuste-confirmar-usd`; i18n `adminSettingsConfirmarUsd`, `adminSettingsConfirmarUsdAyuda`.

- [ ] **Step 1: Escribir los tests que fallan**

In `frontend/src/pages/admin/AdminSettings.test.jsx`, append at the end:
```js
describe('AdminSettings -- umbral de confirmación de costo (spec 2026-09-17 §6.1)', () => {
  const CON_UMBRAL = { data: {
    config: [{ key: 'system_name', value: 'Axioma' }, { key: 'pipeline_confirmar_usd', value: '0.50' }],
    limites: { ...LIMITES, pipeline_confirmar_usd: { min: '0', max: '999999.99', decimales: 2 } },
  } }

  it('el campo toma mínimo, máximo y paso del servidor', async () => {
    api.get.mockResolvedValue(CON_UMBRAL)
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsConfirmarUsd)
    expect(campo).toHaveValue(0.5)
    expect(campo).toHaveAttribute('min', '0')
    expect(campo).toHaveAttribute('max', '999999.99')
    expect(campo).toHaveAttribute('step', '0.01')
  })

  it('un umbral fuera de rango se nombra con la etiqueta del campo', async () => {
    api.get.mockResolvedValue(CON_UMBRAL)
    api.put.mockRejectedValue(rechazo(400, { code: 'config_valor_invalido', clave: 'pipeline_confirmar_usd' }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_valor_invalido(es.adminSettingsConfirmarUsd))
  })

  it('la etiqueta y la ayuda existen en los dos idiomas y difieren', () => {
    for (const clave of ['adminSettingsConfirmarUsd', 'adminSettingsConfirmarUsdAyuda']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(es[clave]).not.toBe(en[clave])
    }
  })
})
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `$VITEST src/pages/admin/AdminSettings.test.jsx`
Expected (3 nuevos): `Unable to find a label with the text of: undefined`/`es.adminSettingsConfirmarUsd` indefinido → `expected undefined to be truthy`; el de error con `expected element to have text content ... "undefined"`.

- [ ] **Step 3: Implementar**

In `frontend/src/i18n/es.js`, replace:
```js
  adminSettingsSystemName: 'Nombre del sistema',
```
with:
```js
  adminSettingsSystemName: 'Nombre del sistema',
  adminSettingsConfirmarUsd: 'Confirmar pipelines desde (USD)',
  adminSettingsConfirmarUsdAyuda: 'Por encima de este costo máximo, o con un paso sin precio, se pide confirmación antes de correr. 0 = confirmar siempre.',
```
In `frontend/src/i18n/en.js`, replace:
```js
  adminSettingsSystemName: 'System name',
```
with:
```js
  adminSettingsSystemName: 'System name',
  adminSettingsConfirmarUsd: 'Confirm pipelines from (USD)',
  adminSettingsConfirmarUsdAyuda: 'Above this maximum cost, or with a step without price, confirmation is required before running. 0 = always confirm.',
```

In `frontend/src/pages/admin/AdminSettings.jsx`, replace:
```js
  system_name: 'adminSettingsSystemName',
}
```
with:
```js
  system_name: 'adminSettingsSystemName',
  pipeline_confirmar_usd: 'adminSettingsConfirmarUsd',
}
```
replace:
```jsx
function CampoNumero({ id, etiqueta, ayuda, valor, limite, onChange }) {
  return (
    <div>
      <label htmlFor={id} className={CLASE_ETIQUETA}>{etiqueta}</label>
      <input id={id} type="number" min={limite?.min} max={limite?.max} value={valor ?? ''}
        onChange={e => onChange(e.target.value)} className={CLASE_CAMPO} />
```
with:
```jsx
function CampoNumero({ id, etiqueta, ayuda, valor, limite, onChange }) {
  // Montos (spec 2026-09-17): el servidor dice cuántos decimales admite.
  const paso = Number.isInteger(limite?.decimales) ? String(10 ** -limite.decimales) : undefined
  return (
    <div>
      <label htmlFor={id} className={CLASE_ETIQUETA}>{etiqueta}</label>
      <input id={id} type="number" min={limite?.min} max={limite?.max} step={paso} value={valor ?? ''}
        onChange={e => onChange(e.target.value)} className={CLASE_CAMPO} />
```
replace:
```jsx
          onChange={v => set('web_task_retention_days', v)} />
```
with:
```jsx
          onChange={v => set('web_task_retention_days', v)} />
        <CampoNumero id="ajuste-confirmar-usd" etiqueta={t.adminSettingsConfirmarUsd} ayuda={t.adminSettingsConfirmarUsdAyuda}
          valor={config.pipeline_confirmar_usd} limite={limites.pipeline_confirmar_usd}
          onChange={v => set('pipeline_confirmar_usd', v)} />
```

- [ ] **Step 4: Correr y ver verde**

Run: `$VITEST src/pages/admin/AdminSettings.test.jsx src/i18n`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add frontend/src/pages/admin/AdminSettings.jsx frontend/src/pages/admin/AdminSettings.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js && \
git commit -m "feat(admin): umbral de confirmación de costo en Configuración

Spec 2026-09-17 §6.1: límites y decimales del servidor.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Pisos de CI, prueba de carga, canario y entrega

**Files:**
- Modify: `.github/workflows/policy.yml` (piso vitest ~línea 567; `PISO_PASSED` ~línea 1456; `JAX_CI_MIN_PASSED` ~línea 1972)
- Create (scratchpad, NO en el repo): `.../scratchpad/jacobs_falso_servidor.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: pisos nuevos medidos; número de carga registrado; texto para `DEUDA.md` (repo jax).

Cuentas esperadas de tests nuevos (sobre la base de la Task 0):
- Backend con DB: Task 1 (5) + Task 2 (4) + Task 3 (5) + Task 4 (16) + Task 5 (10) + Task 6 (16) + Task 7 (18) = **74** → `1386 + 74 = 1460`.
- Backend sin DB (sólo los que no piden `client`): Task 1 (3) + Task 2 (2) + Task 3 (1) + Task 4 (13) + Task 5 (10) + Task 6 (15) + Task 7 (14) = **58** → `787 + 58 = 845`.
- Vitest: Task 8 (10) + Task 9 (9) + Task 10 (9) + Task 11 (3) = **31** → `535 + 31 = 566`.

- [ ] **Step 1: Medir las tres suites dos veces**

Run dos veces cada una:
- `$PYTEST -q -p no:cacheprovider --junitxml=/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/junit-db.xml 2>&1 | tail -3`
- mismo con `JAX_CI_NO_DB=1` y `junit-nodb.xml`
- `$VITEST 2>&1 | tail -5`

Expected: con DB `1460 passed, 1 failed (grounding ambiental, el mismo de la base), 1 skipped`; sin DB `845 passed`; vitest `566 passed, 0 failed`. Si un número difiere de base + delta, NO ajustar el piso al número: buscar qué test sobra o falta (`--collect-only -q | grep <archivo>`) y explicarlo en el commit.

- [ ] **Step 2: Subir los pisos**

In `.github/workflows/policy.yml`, replace:
```
            if (r.numFailedTests !== 0 || r.numPassedTests !== 535) {
              console.error("PISO ROTO: se esperaban 535 tests de vitest CORRIDOS y 0 fallidos.");
```
with:
```
            // 535 -> 566 el 2026-09-17 (pre-vuelo y continuar, spec
            // 2026-09-17): moneda (+4), errores (+3), store pipeline_continued
            // (+1), pipelineChain clean-room por pasos (+2), PipelineModal
            // pre-vuelo y confirmación (+8), BottomBar relanza (+1),
            // ContinuarPipelineModal (+6), RightPanel detenidos (+3),
            // AdminSettings umbral (+3). Vistos en rojo contra accc641.
            // Medido dos veces: 566 passed, 0 fallidos.
            if (r.numFailedTests !== 0 || r.numPassedTests !== 566) {
              console.error("PISO ROTO: se esperaban 566 tests de vitest CORRIDOS y 0 fallidos.");
```
replace:
```
          PISO_PASSED = 1386
```
with:
```
          # 1386 -> 1460 el 2026-09-17 (pre-vuelo y continuar): esquema (+5),
          # datos medidos (+4), tope de motores (+5), ajuste de umbral (+16),
          # propagación de errores de Jacobs (+10), pre-vuelo y confirmación
          # (+16), continuar y causa (+18). Vistos en rojo contra accc641.
          # Mismo hueco ambiental (1 failed de grounding), 1 skip. Medido dos
          # veces: 1460 passed / 1 failed / 1 skipped.
          PISO_PASSED = 1460
```
replace:
```
      JAX_CI_MIN_PASSED: "787"
```
with:
```
      # 787 -> 845 el 2026-09-17 (pre-vuelo y continuar): +58 puros (esquema 3,
      # datos 2, motores 1, umbral 13, propagación 10, pre-vuelo 15,
      # continuar 14); los que piden `client` se saltean. Medido dos veces con
      # JAX_CI_NO_DB=1: 845 passed.
      JAX_CI_MIN_PASSED: "845"
```
(Si el Step 1 midió otros números con explicación, usar los medidos y ajustar los comentarios.)

Commit:
```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo && pwd && git branch --show-current && \
git add .github/workflows/policy.yml && \
git commit -m "ci: pisos del pre-vuelo y continuar (vitest 566, con DB 1460, sin DB 845)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Prueba de carga de `POST /api/pipelines/preflight` (instancia aislada)**

Create `/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/jacobs_falso_servidor.py`:
```python
"""Jacobs falso para la prueba de carga: contesta el contrato de /preflight
al instante (el costo medido es el de la Mesa: auth, ajuste, reenvío y
validación del veredicto). Nunca llama a nada."""
from fastapi import FastAPI

app = FastAPI()
VEREDICTO = {"ok": True, "violaciones": [], "costo_max_usd": "0.30", "sondeadas": [],
             "pasos_costo": [{"paso": 0, "faceta": "kimi", "modelo": "kimi-k3", "llamadas_max": 1,
                              "tokens_in_max": 4000, "tokens_out_max": 131072, "usd_max": "0.30", "motivo": None}]}


@app.post("/jacobs/preflight")
async def preflight(cuerpo: dict):
    return VEREDICTO


@app.get("/health")
async def health():
    return {"ok": True}
```

Arrancar el Jacobs falso (en background):
```bash
cd /tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad && \
/home/fruiz/jax-platform/backend/.venv/bin/python -m uvicorn jacobs_falso_servidor:app --host 127.0.0.1 --port 17777
```

Arrancar el backend aislado sobre `jax_memory_test` (en background):
```bash
S=/tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/carga-prevuelo && mkdir -p $S/bin && \
cd /home/fruiz/worktrees/jax-platform-prevuelo/backend && set -a && . /etc/jax/.env && set +a && \
export JAX_DB_NAME=jax_memory_test JACOBS_URL=http://127.0.0.1:17777/jacobs LAS_MANOS_URL=http://127.0.0.1:17777 \
  CANARY_INTERVAL_SECONDS=0 JAX_FACET_SEAL_PATH=$S/sello JAX_USAGE_SPOOL_DIR=$S/spool JAX_MISSIONS_DIR=$S/missions \
  JAX_REPO_BASE=$S/repo JAX_AUDIT_LOG_PATH=$S/audit.jsonl JAX_BIN=$S/bin/jax JAX_REPO_PATH=/home/fruiz/jax && \
/home/fruiz/jax-platform/backend/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 18080
```

Verificar el aislamiento ANTES de cargar (el `Monitor`/espera hasta que `curl -s http://127.0.0.1:18080/api/health` responda):
```bash
PID=$(pgrep -f "uvicorn main:app --host 127.0.0.1 --port 18080") && \
tr '\0' '\n' < /proc/$PID/environ | grep -E '^(JAX_DB_NAME|JACOBS_URL|CANARY_INTERVAL_SECONDS)='
```
Expected: `JAX_DB_NAME=jax_memory_test`, `JACOBS_URL=http://127.0.0.1:17777/jacobs`, `CANARY_INTERVAL_SECONDS=0`. Si no coincide, DETENER y matar la instancia.

Cargar (c=25; y c=1 y c=50 para ubicar la degradación):
```bash
cd /home/fruiz/worktrees/jax-platform-prevuelo/backend && set -a && . /etc/jax/.env && set +a && export JAX_DB_NAME=jax_memory_test && \
TOKEN=$(/home/fruiz/jax-platform/backend/.venv/bin/python -c "from auth.jwt import create_access_token; print(create_access_token('1', '1', 'superadmin'))") && \
CUERPO='{"steps":[{"facet":"kimi","capability":"generate","prompt":"carga","motor":"kimi"}]}' && \
for c_n in "1 200" "25 1000" "50 1000"; do set -- $c_n; \
  python3 /home/fruiz/jax/scripts/load_test.py --url http://127.0.0.1:18080/api/pipelines/preflight -X POST \
    --cuerpo "$CUERPO" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -c $1 -n $2; done \
  | tee /tmp/claude-1000/-home-fruiz/4792bd29-6fa4-48da-980c-3b4c7526688f/scratchpad/carga-prevuelo.json
```
Expected: `"errores": 0` en las tres; anotar rps, p50, p95, p99 por concurrencia. Criterio: 0 errores a c=25 es condición de GO; un error → diagnosticar (no subir el timeout).

Apagar las dos instancias (`pkill -f "port 18080"`, `pkill -f "port 17777"`) y borrar `$S`.

- [ ] **Step 4: Registrar la carga**

Agregar al final de la descripción del commit de pisos (con `git commit --amend` sólo si ese commit no salió todavía de este worktree; si no, un commit vacío `--allow-empty`) el bloque:
```
Carga POST /api/pipelines/preflight — VERDAD OPERACIONAL 2026-09-17 <hora CST>
Instancia aislada (jax_memory_test, Jacobs falso instantáneo, uvicorn 1 worker, hall9000).
c=1  n=200 : rps <x> p50 <x> ms p95 <x> ms p99 <x> ms errores 0
c=25 n=1000: rps <x> p50 <x> ms p95 <x> ms p99 <x> ms errores 0
c=50 n=1000: rps <x> p50 <x> ms p95 <x> ms p99 <x> ms errores <x>
```
con los números de `carga-prevuelo.json` (no otros).

- [ ] **Step 5 (CONTROLADOR, requiere push): Canario rojo**

Con la rama empujada y verde, crear un commit que rompe a propósito los tres jobs:
- `backend/api/pipelines.py`: en `CODIGOS_DE_JACOBS`, quitar `"prevuelo_rechazado",` (rompe `test_un_codigo_propio_de_jacobs...`, puro: rojo en con-DB y sin-DB).
- `frontend/src/components/BottomBar/PipelineModal.jsx`: en `handleSubmit`, cambiar `setViolaciones(data.violaciones || [])` por `setViolaciones([])` (rompe el test de violaciones).

```bash
git -C /home/fruiz/worktrees/jax-platform-prevuelo commit -am "test(ci): canario rojo a propósito (se revierte)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>" && \
git -C /home/fruiz/worktrees/jax-platform-prevuelo push
```

- [ ] **Step 6 (CONTROLADOR): Ver rojo por API sobre el headSha real**

```bash
SHA=$(git -C /home/fruiz/worktrees/jax-platform-prevuelo rev-parse HEAD) && \
gh run list --repo fjruizhn/jax-platform --branch feat/prevuelo-y-continuar --json databaseId,headSha,status,conclusion --limit 5 \
  | python3 -c "import json,sys; [print(r) for r in json.load(sys.stdin) if r['headSha']=='$SHA']"
```
Esperar a `status=completed` y luego `gh run view <databaseId> --repo fjruizhn/jax-platform --json jobs --jq '.jobs[] | {name, conclusion}'`.
Expected: `backend-tests-con-db`, `backend-tests-no-db` y `frontend-tests` con `conclusion: failure` sobre ese `headSha`.

- [ ] **Step 7 (CONTROLADOR): Revertir y ver verde**

```bash
git -C /home/fruiz/worktrees/jax-platform-prevuelo revert --no-edit HEAD && git -C /home/fruiz/worktrees/jax-platform-prevuelo push
```
Repetir Step 6 con el nuevo `HEAD`. Expected: los tres jobs `success`, con los pisos nuevos impresos (`passed=1460`, `passed=845`, `passed=566`).

- [ ] **Step 8 (CONTROLADOR): Rebase sobre master antes de mergear**

Spec §10: `git fetch origin && git rebase origin/master`, resolver conflictos preservando lo de los frentes B/E/F, volver a correr las tres suites y ajustar los pisos si master sumó tests (base nueva + deltas de este plan).

- [ ] **Step 9: Entregar al controlador el texto para `jax/DEUDA.md` (desvío DV-14)**

Texto exacto:
```
- **2026-09-17 · PATCH /api/admin/motors sin auditoría (jax-platform).** El
  endpoint cambia `motor` (transporte, tope, timeout, estado) sin registrar
  quién cambió qué ni el valor anterior; `user_admin_audit` y
  `model_catalog_audit` no lo cubren. Visto al llevar kimi/ada a
  `max_tokens=0` (spec 2026-09-17-prevuelo-y-continuar §7 A), hecho como
  migración de datos con marcador. Fecha: 2026-10-01.
```

---

## Self-review (hecho al escribir; no es tarea)

**1. Cobertura del spec (lado Mesa):**
- §4.4 columna `min_output_tokens` → Task 1; semilla medida → Task 2.
- §4.5 ENUM `preflight` → Task 1; `request_type` → DV-4 (sin DDL).
- §1 D1 / §7 A kimi y ada a 0 + validación → Task 3.
- §7 F topes medidos → Task 2 (precios: DV-3).
- §6.1 `/preflight`, creación con 422/409, `costo_max_aceptado_usd` → Task 6; `continue/preflight` y `continue` → Task 7; propagación en resume/cancel/get/results → Task 5, en create → Task 6, en continue → Task 7; ajuste `pipeline_confirmar_usd` → Task 4 (+ panel Task 11).
- §6.2 PipelineModal + ConfirmarCostoDialogo → Task 9; RightPanel + ContinuarPipelineModal + motivo → Tasks 7 y 10; `pipeline_continued` → Tasks 7 y 8; i18n, tokens, Intl, sin diálogos del navegador → Tasks 8-11.
- §8 `prevuelo_no_disponible` (fail-closed en la Mesa) → Task 6.
- §9 Mesa backend (409, 422, propagación en los 5, ajuste), frontend (modal no se cierra, confirmación propia, continuar con reasignación, paridad, escaneo), canarios, carga → Tasks 5-12.
- Lado Jacobs (§4.1-4.3, §4.6, §5.2-5.4, época, sonda, relanzador) → plan J; contrato declarado en Global Constraints.

**2. Placeholders:** los únicos valores que no están en el plan son los números medidos (Task 2 Step 6a/6b) y los de carga (Task 12 Step 4); los dos tienen el comando exacto que los produce, el archivo de salida y el lugar exacto donde van (brief: medir, no inventar).

**3. Consistencia de nombres:** `CONFIRMAR_USD`, `_json_de_jacobs`, `_rechazo_de_jacobs`, `_violaciones_redactadas`, `MOTIVO_MAX`, `_evaluar_veredicto`, `_prevuelo`, `_exigir_consentimiento`, `_prevuelo_no_disponible`, `PedidoDePrevuelo`, `PedidoDeContinuarPrevuelo`, `PedidoDeContinuar`, `_continuable`, `continue_preflight`, `continue_pipeline`, `causa_de`, `sql_eventos_de_causa`, `EVENTOS_DE_CAUSA`, `continuar_pipeline`, `formatearUsd`, `textoDeViolacion`, `cleanroomViolationsDePasos`, `ConfirmarCostoDialogo`, `ContinuarPipelineModal` y las claves i18n se usan con el mismo nombre en cada tarea que las consume.
