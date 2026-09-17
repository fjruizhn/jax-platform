# Frente A · Limpieza, defectos y reglas — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aplicar la sección A del spec de hallazgos (A-01..A-55, menos A-21, que va con el frente D): borrar lo muerto verificado, arreglar los defectos (`_safe_path`, tablero que miente, labels crudos, login por regex) y cerrar las violaciones de regla (i18n con códigos estables, rutas y semilla desde el entorno).

**Architecture:** Una rama y un PR en jax-platform (`fix/hallazgos-frente-a`) con 13 tareas por área, más una tarea pareada en jax (familia de espejos de las keywords del auto-ruteo). Cada cambio de conducta arranca con un test rojo contra el código viejo. El backend deja de mandar texto en español: manda `detail.code` (y, para el chat, `aviso.code` con `params`). El frontend lo traduce con `codigoDe()` y funciones nuevas en `api/errores.js`. Cierran la rama la carga (gate), los pisos de CI con canario, el deploy del spec y la Biblioteca.

**Tech Stack:** FastAPI (Starlette 1.3.1), aiomysql, MariaDB 12.3.3 (CI: mariadb 11.8), pytest con `asyncio_mode = auto`, Python 3.14 (`backend/.venv`); React 19, Vite 6, vitest 4, Testing Library; k6 v2.2.0 en `~/bin/k6`.

**Spec:** `docs/superpowers/specs/2026-09-16-hallazgos-auditoria-design.md` (sección A) y la evidencia en `docs/superpowers/specs/anexo-a/`. Se leen los dos antes de cada tarea.

**Base:** jax-platform `master` `26c9cd5`; jax `master` `bd95237`.

---

## Discrepancias con el spec

Verificadas contra el código en `26c9cd5` (jax `bd95237`). Ninguna cambia la intención de un ítem. Cada una dice qué hace el plan, y la última es una pregunta para Fernando.

1. **A-22: los nombres no coinciden y el checker compara por nombre.** `check_mirror_sync.py::_extract` compara el segmento de fuente de cada símbolo, con el nombre incluido (`ast.get_source_segment` de `NOMBRE = …`). En `api/chat.py` los sets se llaman `_KIMI_KW`…`_ADA_STRONG`, `_WEB_TIEBREAK` y `_WEB_KW_SETS`; en `jax/core/router.py`, `KIMI_KW`…`ADA_STRONG`, `_TIEBREAK` y `_KW_SETS`. Así como están, la familia daría "falta" en los 12 símbolos. **Medido:** al renombrar en chat.py, los 12 segmentos quedan idénticos byte a byte a los de router.py (script `ast` de solo lectura, 2026-09-16). El plan renombra en jax-platform (Task 8) y agrega la familia en jax (Task 14). **Orden de merge obligatorio:** jax-platform primero, porque el job `mirror-sync` de jax clona `master` de jax-platform. Ningún test del backend usa esos nombres (grep).
2. **A-24: un test lee `hub._lock`.** `tests/test_admin_usuarios_guardas.py:174` guarda `self._hub._lock.locked()`, y `test_close_user_cierra_fuera_del_lock_y_un_fallo_no_frena_a_las_demas` afirma que el close ocurre sin el lock tomado. Sin lock esa afirmación no tiene objeto. El plan la reescribe: la propiedad que queda es "`close_user` recorre una foto; un fallo o una desconexión durante el close no frena a las demás" (Task 2).
3. **A-48: `/api/state` hoy no toca la base, y sus tests puros corren sin DB.** El docstring de `api/state.py` dice "sin DB ni red" (camino caliente medido: p95 127 ms a c=25). `tests/test_state_por_duenio.py` llama al handler directo, sin DB, y cuenta en el piso del job `backend-tests-no-db`. Leer `facet` en cada pedido rompe esos tests y agrega una ida a la base al camino caliente. **Qué hace el plan:** `display_name` sale igual de la tabla `facet`, pero se lee UNA vez en el `lifespan`, después de `run_migrations()`, y se guarda en `engine_state`. **Invalidación declarada:** el reinicio del proceso. El único escritor de `facet.display_name` son las migraciones, que corren en el arranque; grep en jax-platform y en jax: no hay `UPDATE`/`INSERT` de `facet` en runtime. Un test de guarda lo fija (Task 7). Si Fernando prefiere la lectura por pedido, se cambia en la Task 7 y la carga del escenario A la mide.
4. **A-55: `JAX_REPO_PATH` y `JAX_CONFIG_PATH` hoy TIENEN default, y producción no las define.** "Mismo patrón que `JAX_REPO_PATH`" + "fallan cerradas si faltan" choca con el código: `chat.py:78,634`, `shadow_validation.py:84` y `governance_context.py:126` caen a `~/jax`. El inventario de variables de `docs/auditoria-api-keys-2026-08-09.md:145` no las lista, así que producción corre con esos defaults. Si solo se arregla `sys.path`, queda la misma ruta atada a `$HOME` en tres módulos más. **El plan vuelve obligatorias las seis** (`JAX_REPO_PATH`, `JAX_CONFIG_PATH`, `JAX_AUDIT_LOG_PATH`, `JAX_MISSIONS_DIR`, `JAX_BIN`, `JAX_REPO_BASE`) con un helper único, y el deploy las agrega a `/etc/jax/.env` ANTES del reinicio (Task 16). **Hallazgo de paso:** `tests/test_command_path_traversal.py` escribe hoy en el `~/jax/missions` REAL (`MISSIONS_DIR` se fija al importar). El conftest pasa a fijar directorios temporales, igual que ya hace con el sello y el respaldo de uso (Task 5). Quedan fuera, con motivo escrito en el test de guarda: `model_catalog.py:34` (`~/.claude/.credentials.json`, la ubicación que define Claude Code) y el `cwd=Path.home()` del subproceso `jax` en `command.py:112` (es el directorio de trabajo de la CLI, no una ruta de datos).
5. **A-51: `upload.py` también lo reescribe el frente D.** El plan convierte a código los dos `detail` que existen hoy (413 y 422 del PDF), y el frente D rebasa sobre esto. Además, "command" y "pipelines" tienen más textos que los del spec, y el plan los incluye por la misma regla: los 502 `detail=str(e)` de resume/cancel/results; el fallo de `_run_command` (`"Error ejecutando tarea: …"`, `"Error: …"`); el `"[DRY RUN] Tarea registrada"`; y el reenvío crudo del `detail` de Jacobs.
6. **A-51/A-53: un `detail` objeto rompe un slice.** En `chat.py:1075` hay `set_facet_status(facet, "error", …, detail[:100])`, y con `detail` dict eso lanza `TypeError`. El plan usa `motivo[:100]` (Task 8).
7. **A-36: no existe una variable para la base de jax-platform.** El plan crea `JAX_PLATFORM_URL`. Si falta, la tarjeta dice `sin_configurar` (nunca `alive`). Además, `_check_http` marca `alive` con `status_code < 500`: un 404 cuenta como vivo, en LAS MANOS también. El plan exige 200, igual que el poller de `jax_engine/state.py:162`.
8. **A-49: "pipelines completados" sin ventana.** Los demás stats son "de hoy", pero el spec dice `status='completed'` sin fecha. El plan cuenta el total, literal al spec. Índice: `idx_pipelines_status (status)`, que crea `jax/jacobs/store.py:82` (también en CI, por `init_tables()`).
9. **A-35: el registro histórico de P10 en jax nombra `_count_configured_keys`** (`jax/policy/rules/P10-fail-open-prohibido.yaml:33`, `policy/generated/CORPUS.md:41`). Es HISTORIA de un triage (2026-08-19) y no se edita.
10. **A-17 — pregunta para Fernando:** la fila `ws_notifications` ya existe en `axioma_config` de producción (`_ensure_defaults` hace `INSERT IGNORE`). Borrarla de `DEFAULT_CONFIG` no la borra de la base, y `GET /api/admin/config` la sigue listando. **Recomendación:** que la borre el frente C, que ya migra `axioma_config` con dump verificado fila por fila. El frente A no toca datos de producción.

---

## Global Constraints

Copiadas del spec (§0, reglas comunes) y de los planes vigentes del repo:

- **TDD:** test rojo contra el código viejo antes del arreglo (un control que no falla no valida). En los recortes sin cambio de conducta, el rojo es el test estructural que describe el estado nuevo, y la conducta la fija un test que pasa antes y después.
- **i18n:** ningún texto visible literal; es/en en paridad. Dark/light con tokens. Nada de `confirm/alert/prompt`: `components/Dialogo.jsx` y `ConfirmacionSuma` para lo destructivo.
- **Sin hardcoding:** config en `/etc/jax/.env` o en DB.
- **Fail-closed.** Todo caché declara su invalidación en el mismo commit.
- **Las cuatro del rendimiento:** índice verificado con EXPLAIN sobre la consulta real, nada bloqueante en `async def`, **prueba de carga con número registrado** para todo endpoint nuevo o modificado en camino de usuario.
- **Barrera de DB:** los tests corren solo por pytest; `conftest.py` fuerza `jax_memory_test`. `/etc/jax/.env` apunta a PRODUCCIÓN: nunca se carga en una shell para correr tests, scripts ni la carga. Los tests puros se corren con `JAX_CI_NO_DB=1`.
- **CI:** todo test nuevo lo corre un job; se verifica rompiéndolo (rojo sobre el sha real por API).
- **Mirror-sync:** si se toca un símbolo espejado (`jax/scripts/check_mirror_sync.py`), el cambio es de los dos repos en el mismo paso. En este frente eso aplica a A-22.
- **Deploy:** backend `sudo systemctl restart jax-platform.service` con 0 pipelines en vuelo; frontend build → rsync a `/tmp/axioma-deploy/` en la VM dev → `sudo rsync -a --delete --chown=www:www --exclude .user.ini` a `/www/wwwroot/axioma-ia.io/`, con backup previo.
- **Registro en la Biblioteca** (`jax/DEUDA.md` y `jax/CONTEXT.md`) antes de cerrar.
- **Git:** worktree `/home/fruiz/worktrees/jax-platform-frente-a`, rama `fix/hallazgos-frente-a` desde `origin/master`. Siempre `git -C <ruta>` (un `cd` fallido deja el shell en otro checkout). **Nunca `git stash`.** Los archivos se agregan uno por uno. No se hace push desde las tareas: el push va en la Task 16.
- **Venv y Node:** backend `/home/fruiz/jax-platform/backend/.venv/bin/python` (el worktree no tiene venv propio); Node `/home/fruiz/.nvm/versions/node/v24.16.0/bin` (`export PATH=/home/fruiz/.nvm/versions/node/v24.16.0/bin:$PATH` antes de `npm`).
- **Variables para correr tests en hall9000 hasta el deploy** (a partir de la Task 5 son obligatorias): se EXPORTAN en el comando, nunca se lee el `.env`: `JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml`.
- **Pisos de CI** (`.github/workflows/policy.yml`), en `26c9cd5`: vitest **exacto 448**; con DB **≥ 1175 passed y ≤ 1 skip**; sin DB **≥ 614** (`JAX_CI_MIN_PASSED`). Se ponen exactos en la Task 16, medidos dos veces, con un comentario fechado que diga qué suma.
- **Coordinación con los otros frentes (merge):** el frente B toca `jax_engine/schemas.py` (Literal de eventos) y `activateKillSwitch` del store; el C, `resource_manager.py` y `config_admin.py`; el D, `upload.py`, `BottomBar.jsx` (bloque de adjuntos) y `chat.py` (`ChatRequest`). El que mergea después rebasa. No se toca código de los otros frentes.
- **Placeholders de ejecución** (solo estos): `<N>` (número de PR), `<sha>`, `<fecha>`/`<hora>`, y los conteos y latencias medidos.

## Comandos de referencia

```bash
# Worktree (una vez, antes de la Task 1)
git -C /home/fruiz/jax-platform fetch origin
git -C /home/fruiz/jax-platform worktree add /home/fruiz/worktrees/jax-platform-frente-a -b fix/hallazgos-frente-a origin/master
export PATH=/home/fruiz/.nvm/versions/node/v24.16.0/bin:$PATH
npm --prefix /home/fruiz/worktrees/jax-platform-frente-a/frontend ci

# Test puro de backend (sin DB)
cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest <archivo>::<test> -q

# Suite backend con DB (jax_memory_test, forzado por conftest)
cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs

# Suite backend sin DB (el modo del job no-db)
cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs

# vitest
cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run <archivo>
```

## Mapa de archivos

| Archivo | Responsabilidad | Tareas |
|---|---|---|
| `backend/config_de_entorno.py` (nuevo) | `ruta_requerida(nombre) -> Path`: única lectura de rutas desde el entorno, fail-closed | 5 |
| `backend/tests/test_hallazgos_limpieza.py` (nuevo) | Guardas puras de los recortes de backend | 1 |
| `backend/tests/test_locks_sin_await.py` (nuevo) | Guardas de A-24 | 2 |
| `backend/tests/test_repositorio_rutas.py` (nuevo) | A-07/08/26/31/40 | 3 |
| `backend/tests/test_admin_recortes.py` (nuevo) | A-25/39/46/47 | 4 |
| `backend/tests/test_rutas_de_entorno.py` (nuevo) | A-41/54/55 | 5 |
| `backend/tests/test_tablero.py` (nuevo) | A-05/06/35/36/37/49 | 6 |
| `backend/tests/test_facetas_nombres.py` (nuevo) | A-42/48 | 7 |
| `backend/tests/test_chat_codigos.py` (nuevo) | A-13/16/22/51/53 en chat | 8 |
| `backend/tests/test_mesa_codigos.py` (nuevo) | A-14/30/51/53 en image/command/pipelines/upload | 9 |
| `backend/tests/test_login_bloqueo.py` (nuevo) | A-50 | 10 |
| `frontend/src/api/errores.js` | `textoDeErrorDeMesa`, `textoDeAviso` | 11 |
| `frontend/src/i18n/paridad.test.js` (nuevo) | es/en con las mismas claves, anidadas incluidas | 12 |
| `frontend/src/politica/modales.test.js` (nuevo) | Ningún overlay a mano fuera de `Dialogo.jsx` | 13 |
| `jax/scripts/check_mirror_sync.py` | Familia `router_keywords` | 14 |

## Interfaces (nombres que cruzan tareas)

- `config_de_entorno.ruta_requerida(nombre: str) -> pathlib.Path`: lanza `RuntimeError` si la variable falta, está vacía o no es absoluta (Task 5; la usan 5 y 8).
- `api.chat.AvisoDeChat(BaseModel)`: `code: Literal["faceta_sin_binding", "faceta_no_autorizada", "transporte_no_soportado", "identidad_del_modelo", "hyde_usa_modo_comando"]`, `params: dict[str, str]`, y `como_texto() -> str` (Task 8).
- `api.chat.ChatResponse.aviso: AvisoDeChat | None = None` (Task 8; lo lee la Task 11).
- Códigos de `detail` (Tasks 8-10; los traduce la Task 11). Forma: string `"<code>"` o `{"code": "<code>", ...datos}`.

  | Código | HTTP | Datos | Origen |
  |---|---|---|---|
  | `faceta_desconocida` | 400 | `facet` | chat |
  | `proveedor_error_http` | 502 | `facet`, `status`, `motivo` | chat |
  | `faceta_error` | 502 | `facet`, `motivo` | chat |
  | `credencial_no_disponible` | 503 | `provider` | image |
  | `imagen_error_http` | 502 | `status`, `motivo` | image |
  | `imagen_error` | 502 | `motivo` | image |
  | `task_id_invalido` | 400 | — | command |
  | `tarea_no_encontrada` | 404 | — | command |
  | `limite_de_pipelines` | 429 | `max` | pipelines |
  | `pipeline_id_invalido` | 400 | — | pipelines |
  | `pipeline_no_encontrado` | 404 | — | pipelines |
  | `jacobs_rechazo` | status de Jacobs | `status`, `motivo` | pipelines |
  | `jacobs_no_responde` | 502 | `motivo` | pipelines |
  | `archivo_demasiado_grande` | 413 | `max_bytes` | upload |
  | `pdf_ilegible` | 422 | `motivo` | upload |
  | `cuenta_bloqueada` | 423 | `retry_after_seconds` (+ cabecera `Retry-After`) | auth |
  | `ruta_invalida`, `archivo_no_encontrado` | 400, 404 | — | admin/repository |

- Evento `command_completed` y `GET /api/command/{id}`: `status` ∈ `completed|failed`, `code` ∈ `comando_sin_resultado|comando_fallo|comando_simulado` (opcional), `result: str`, `motivo: str` (solo en `comando_fallo`) (Task 9; lo lee la Task 11).
- Frontend: `textoDeErrorDeMesa(t, err, generico) -> string`, `textoDeAviso(t, aviso) -> string` (en `api/errores.js`); `contenidoDeComando(t, datos) -> string` (exportada de `store/useJaxStore.js`); `diccionarioActivo() -> object` (de `i18n/index.jsx`); `getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage, etiquetas)` con `etiquetas = {reposo, killSwitch, dalle, lasManosDown, gate, jacobs}`, todas requeridas.

---

### Task 1: Backend — código muerto y recortes sin cambio de conducta

Ítems: A-02, A-03, A-04, A-12, A-15, A-17 (backend), A-18, A-19, A-20, A-28, A-33, A-34.

**Files:**
- Modify: `backend/requirements.txt:10` (quitar `aiosmtplib>=3.0`)
- Modify: `ops/migration/MIGRATION.md:118`
- Modify: `backend/auth/models.py:5-9` (borrar `TokenPayload`)
- Modify: `backend/api/chat.py:81-93` (borrar `_load_jax_env` y su llamada)
- Modify: `backend/api/image.py:1-30` (borrar `_load_jax_env`, su llamada y el `import os` si queda sin uso)
- Modify: `backend/jax_engine/state.py:58,77,81,217`
- Modify: `backend/tests/test_sse_isolation.py:156,176`
- Modify: `backend/auth/jwt.py:18-39`
- Modify: `backend/jax_engine/websocket_hub.py:15-16`
- Modify: `backend/tests/test_websocket_isolation.py:30-32` (docstring del fake)
- Modify: `backend/db/transaccion.py:12-15`
- Modify: `backend/main.py:115-156`
- Delete: `backend/api/admin/facet_models.py`
- Modify: `backend/api/admin/__init__.py:13-28`, `backend/db/migrations.py:109-112` (comentarios que citan el módulo)
- Modify: `backend/api/admin/config_admin.py:18`
- Create: `backend/tests/test_hallazgos_limpieza.py`

**Interfaces:**
- Consumes: nada.
- Produces: `main.ROUTERS: tuple[APIRouter, ...]`; `auth.jwt._crear_token(tipo: str, segundos: int, user_id: str, tenant_id: str, role: str, token_version: int) -> str`; `db.transaccion.AISLAMIENTOS == frozenset({"READ COMMITTED"})`.

- [ ] **Step 1: Escribir los tests (rojos contra `26c9cd5`)**

```python
# backend/tests/test_hallazgos_limpieza.py
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
    assert "/etc/jax/.env" not in fuente


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
    import main
    rutas = {getattr(r, "path", None) for r in main.app.routes}
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
```

- [ ] **Step 2: Verlos en rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_hallazgos_limpieza.py -q`
Expected: FAIL en todos, salvo `test_los_tokens_conservan_su_payload` (pasa: fija conducta). Anotar en el reporte el motivo de cada rojo.

- [ ] **Step 3: Implementar**

`backend/requirements.txt`: borrar la línea `aiosmtplib>=3.0`. `ops/migration/MIGRATION.md:118`: `# (requirements ahora incluye psutil de la feature auth/admin)`.

`backend/auth/models.py`: borrar la clase `TokenPayload` (L5-9). Si `Optional` y `Field` siguen en uso (sí: `LoginRequest`, `AuthUser`), los imports quedan.

`backend/api/chat.py`: borrar el comentario `# Carga el .env de JAX una vez al importar el módulo`, la función `_load_jax_env` y la llamada `_load_jax_env()` (L81-93). `backend/api/image.py`: borrar L18-30. `os` en image.py queda sin uso: borrar `import os`.

`backend/jax_engine/state.py`: borrar `self._user_tenant_map: dict[str, str] = {}` (L58) y las líneas L77 y L81 que lo escriben. En `_poll_one_pipeline`, el payload del gate queda:

```python
                gate_event = JAXEvent(
                    event_type="human_gate_requested",
                    tenant_id=pipeline.tenant_id,
                    user_id=updated.user_id,
                    payload={"pipeline_id": pid},
                )
```

`backend/tests/test_sse_isolation.py`: en el docstring de L156, `stays stuck in connected_users/_user_tenant_map forever` → `stays stuck in connected_users forever`; borrar `assert user_id not in engine_state._user_tenant_map` (L176).

`backend/auth/jwt.py` (los dos wrappers públicos, intactos):

```python
def _crear_token(tipo: str, segundos: int, user_id: str, tenant_id: str, role: str, token_version: int) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
        "exp": int(time.time()) + segundos,
        "type": tipo,
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


# `tv` = jax_users.token_version al emitir (2026-09-12, admin usuarios etapa
# 2). El default 0 es el mismo valor con que se leen los tokens emitidos antes
# de que existiera `tv` (spec §3.2), y lo usan los tests que firman para
# user_id=1, cuya versión nunca cambia.
def create_access_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    return _crear_token("access", ACCESS_EXPIRE_SECONDS, user_id, tenant_id, role, token_version)


def create_refresh_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    return _crear_token("refresh", REFRESH_EXPIRE_SECONDS, user_id, tenant_id, role, token_version)
```

`backend/jax_engine/websocket_hub.py`, en `connect`: borrar las dos líneas del `if getattr(... "CONNECTED"): await websocket.accept()`. `tests/test_websocket_isolation.py:30-32`: el docstring del fake pasa a decir `main.py's websocket_endpoint accepts the socket before ws_hub.connect(); send_json just needs to be awaitable.`

`backend/db/transaccion.py`:

```python
# `aislamiento` (fix ronda 1 de la Task 2, 2026-09-15): nivel para ESTA
# transacción sola (`SET TRANSACTION`, sin SESSION: la conexión vuelve al pool
# con el nivel por defecto). Lista cerrada: el valor va interpolado en el SQL.
# Frente A (2026-09-16, A-28): solo READ COMMITTED -- ningún llamador pidió
# otro nivel (U33); uno nuevo se agrega acá cuando exista quien lo use.
AISLAMIENTOS = frozenset({"READ COMMITTED"})
```

`backend/main.py`:

```python
app = FastAPI(title="JAX Platform", version="0.1.0", lifespan=lifespan)

# Frente A (2026-09-16, A-18): el dev es mismo origen (proxy de Vite para /api
# y /ws) y producción también (nginx de la VM dev). Solo el origen declarado.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in [os.getenv("FRONTEND_ORIGIN", "")] if o],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

ROUTERS = (
    health_router,
    auth_router,
    state_router,
    facets_router,
    pipelines_router,
    events_router,
    chat_router,
    command_router,
    audit_router,
    image_router,
    upload_router,
    motors_router,
    dashboard_router,
    keys_router,
    credentials_router,
    users_router,
    repository_router,
    config_router,
    usage_router,
    # facet_models_router desregistrado (2026-08-10) y su módulo borrado
    # (2026-09-16, frente A): ver api/admin/__init__.py. La tabla queda.
    models_router,
    facet_bindings_router,
    admin_motors_router,
    smtp_router,
    apariencia_router,
)
for _router in ROUTERS:
    app.include_router(_router)
```

`git -C /home/fruiz/worktrees/jax-platform-frente-a rm backend/api/admin/facet_models.py`. En `api/admin/__init__.py`, reemplazar el bloque L13-28 por:

```python
# facet_models_router (legacy, tabla `facet_models`) DESREGISTRADO el
# 2026-08-10: desde Bloque C nadie invoca con facet_models (la fuente es
# facet_binding/resolve_facet()) y su panel (AdminApiKeys.jsx) se retiró.
# El módulo se borró el 2026-09-16 (frente A, A-34). La tabla `facet_models`
# y su semilla quedan como dato histórico.
```

En `db/migrations.py:109-112`, el comentario de `CREATE_FACET_MODELS` queda: `# NOTA: la exclusividad de is_active (un solo modelo activo por faceta) la aplicaba el router legado de facet_models, borrado el 2026-09-16 (frente A, A-34); la tabla queda como dato histórico sin escritor.` (sin el nombre de archivo, que el test rechaza).

`backend/api/admin/config_admin.py`: borrar `"ws_notifications": "true",`.

- [ ] **Step 4: Verlos en verde, y el scanner P10**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_hallazgos_limpieza.py tests/test_no_fail_open_except.py tests/test_sse_isolation.py tests/test_websocket_isolation.py -q`
Expected: PASS. `test_no_fail_open_except` sigue viendo ≥ 120 archivos (`PISO_ARCHIVOS_ESCANEADOS`) aunque se borre uno: anotar el número.

- [ ] **Step 5: Suite con DB de los archivos tocados**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_auth.py tests/test_sesiones_token_version.py tests/test_admin_usuarios_guardas.py tests/test_pipeline_user_id.py tests/test_sse_isolation.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/requirements.txt ops/migration/MIGRATION.md backend/auth/models.py backend/api/chat.py backend/api/image.py backend/jax_engine/state.py backend/tests/test_sse_isolation.py backend/auth/jwt.py backend/jax_engine/websocket_hub.py backend/tests/test_websocket_isolation.py backend/db/transaccion.py backend/main.py backend/api/admin/__init__.py backend/db/migrations.py backend/api/admin/config_admin.py backend/tests/test_hallazgos_limpieza.py
$G commit -m "chore(backend): frente A -- codigo muerto y recortes verificados (A-02..A-34)"
```
(El borrado de `facet_models.py` ya quedó en el índice con `git rm`. El mensaje termina con las líneas de atribución de la sesión.)

---

### Task 2: Locks sin `await` entre chequeo y acción (A-24)

**Files:**
- Modify: `backend/jax_engine/events.py`, `backend/jax_engine/websocket_hub.py`, `backend/jax_engine/resource_manager.py`
- Modify: `backend/tests/test_admin_usuarios_guardas.py:163-196`
- Create: `backend/tests/test_locks_sin_await.py`

**Interfaces:**
- Consumes: el `WebSocketHub.connect` de la Task 1 (sin accept).
- Produces: las mismas firmas `async` de siempre; los tres objetos sin atributo `_lock`. `jax_engine.lifecycle.lifecycle_lock` intacto.

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_locks_sin_await.py
"""Frente A, A-24 (2026-09-16): EventBus, WebSocketHub y ResourceManager
tomaban un asyncio.Lock alrededor de operaciones sobre dicts/sets SIN ningun
await adentro. En asyncio esas secciones ya corren sin interrupcion, asi que
el lock nunca se disputaba. Las firmas `async` se conservan (los llamadores
hacen await) y lifecycle_lock, que SI serializa secuencias con await, no se
toca. Puros."""
import asyncio
import inspect

from jax_engine.events import EventBus
from jax_engine.lifecycle import lifecycle_lock
from jax_engine.resource_manager import ResourceManager
from jax_engine.websocket_hub import WebSocketHub


def test_ninguno_de_los_tres_tiene_lock():
    for objeto in (EventBus(), WebSocketHub(), ResourceManager()):
        assert not hasattr(objeto, "_lock"), type(objeto).__name__


def test_las_firmas_siguen_siendo_async():
    metodos = (
        EventBus.subscribe, EventBus.unsubscribe, EventBus.publish,
        WebSocketHub.connect, WebSocketHub.disconnect, WebSocketHub.has_connections,
        WebSocketHub.send_to_user, WebSocketHub.close_user, WebSocketHub.connected_user_ids,
        ResourceManager.can_start_pipeline, ResourceManager.admit_pipeline,
        ResourceManager.release_pipeline, ResourceManager.active_count,
    )
    for metodo in metodos:
        assert inspect.iscoroutinefunction(metodo), metodo.__qualname__


def test_lifecycle_lock_sigue_siendo_un_lock():
    assert isinstance(lifecycle_lock, asyncio.Lock)


async def test_close_user_recorre_una_foto_aunque_el_close_desconecte():
    hub = WebSocketHub()

    class _Socket:
        def __init__(self):
            self.cerrado_con = None
            self.id = None

        async def close(self, code=1000):
            await hub.disconnect("u-a", self.id)   # muta el dict durante el recorrido
            self.cerrado_con = code

    a, b = _Socket(), _Socket()
    a.id = await hub.connect("u-a", a)
    b.id = await hub.connect("u-a", b)
    assert await hub.close_user("u-a") == 2
    assert (a.cerrado_con, b.cerrado_con) == (4001, 4001)
    assert not await hub.has_connections("u-a")
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_locks_sin_await.py -q`
Expected: FAIL `test_ninguno_de_los_tres_tiene_lock` (`EventBus`); los otros pasan.

- [ ] **Step 3: Implementar**

`events.py`:

```python
class EventBus:
    """Sin lock (2026-09-16, frente A, A-24): subscribe/unsubscribe y la
    lectura de publish no tienen await entre leer y escribir, así que en
    asyncio corren sin interrupción. El await del callback ya estaba fuera."""

    def __init__(self):
        # tenant_id -> {user_id -> callback}
        self._subscribers: dict[str, dict[str, Callback]] = defaultdict(dict)

    async def subscribe(self, tenant_id: str, user_id: str, callback: Callback):
        self._subscribers[tenant_id][user_id] = callback

    async def unsubscribe(self, user_id: str):
        for tenant_subscribers in self._subscribers.values():
            tenant_subscribers.pop(user_id, None)

    async def publish(self, event: JAXEvent):
        # WS canal por usuario — nunca por tenant: route only to the subscriber
        # that owns this event's user_id, not every user in the tenant.
        cb = self._subscribers.get(str(event.tenant_id), {}).get(str(event.user_id))
        if cb is None:
            return
        try:
            await cb(event)
        except Exception:  # fail-soft: aislar el fallo de un subscriber de WS del resto del event bus; los demas subscribers no deben verse afectados por uno roto
            pass
```

`import asyncio` queda sin uso en events.py: borrarlo.

`websocket_hub.py`: borrar `self._lock = asyncio.Lock()` y cada `async with self._lock:`, desindentando su cuerpo. `send_to_user` y `close_user` conservan la foto (`list(...)`) ANTES de cualquier await. En el docstring de `close_user`: "Las conexiones se leen bajo `_lock` y se cierran FUERA de él" → "Las conexiones se copian a una lista antes del primer await: un close que desconecta no altera el recorrido". `import asyncio` queda sin uso: borrarlo.

`resource_manager.py`:

```python
class ResourceManager:
    """Sin lock (frente A, A-24): cada método es una lectura o escritura de un
    set, sin await adentro. El hueco entre can_start_pipeline y admit_pipeline
    (api/pipelines.py) tiene awaits en el medio y el lock tampoco lo cubría."""

    def __init__(self):
        self._active: dict[str, set[str]] = defaultdict(set)

    async def can_start_pipeline(self, tenant_id: str) -> bool:
        return len(self._active[tenant_id]) < MAX_PIPELINES_PER_TENANT

    async def admit_pipeline(self, tenant_id: str, pipeline_id: str):
        self._active[tenant_id].add(pipeline_id)

    async def release_pipeline(self, tenant_id: str, pipeline_id: str):
        self._active[tenant_id].discard(pipeline_id)

    async def active_count(self, tenant_id: str) -> int:
        return len(self._active[tenant_id])
```

(borrar `import asyncio`). En `jax_engine/state.py:228-233`, el comentario "bajo un asyncio.Lock (resource_manager.py)" → "sobre un defaultdict(set) (resource_manager.py)".

`tests/test_admin_usuarios_guardas.py`: en `_SocketQueCierra`, borrar `self.lock_tomado_al_cerrar = None` y la línea que lee `self._hub._lock.locked()`. Borrar también el parámetro `hub` que solo existía para eso (`_SocketQueCierra(falla=False)`) y actualizar las 5 construcciones. El test `test_close_user_cierra_fuera_del_lock_y_un_fallo_no_frena_a_las_demas` pasa a llamarse `test_close_user_un_fallo_no_frena_a_las_demas`; se borra su último assert (`lock_tomado_al_cerrar`) y el docstring de la clase dice "Stand-in de WebSocket que registra el cierre".

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_locks_sin_await.py tests/test_admin_usuarios_guardas.py tests/test_websocket_isolation.py tests/test_sse_isolation.py tests/test_pipeline_resource_release.py -q`
Expected: PASS (los de DB se saltan: sin DB).

Run (con DB): `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_admin_usuarios_guardas.py tests/test_websocket_isolation.py tests/test_sse_isolation.py tests/test_sesion_unica.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/jax_engine/events.py backend/jax_engine/websocket_hub.py backend/jax_engine/resource_manager.py backend/jax_engine/state.py backend/tests/test_admin_usuarios_guardas.py backend/tests/test_locks_sin_await.py
$G commit -m "refactor(jax_engine): sin asyncio.Lock donde no hay await entre chequeo y accion (A-24)"
```

---

### Task 3: Repositorio — ruta segura, MIME real y recortes (A-07, A-08, A-26, A-31, A-40)

**Files:**
- Modify: `backend/api/admin/repository.py` (entero)
- Create: `backend/tests/test_repositorio_rutas.py`

**Interfaces:**
- Consumes: nada.
- Produces: `repository._resolve(path: str) -> Path` (lanza 400 `ruta_invalida` / 404 `archivo_no_encontrado`); `repository._file_info(path: Path) -> dict`. `REPO_BASE` sigue siendo un atributo del módulo (la Task 5 cambia de dónde sale).

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_repositorio_rutas.py
"""Frente A (2026-09-16): /api/admin/repo.

A-40 DEFECTO: _safe_path comparaba con `target.startswith(base)` sin
separador, asi que `documents/../../repo-x/secreto.txt` pasaba si existia una
carpeta hermana cuyo nombre empieza con "repo". Leia o BORRABA fuera del
repositorio (superadmin). Ahora Path.resolve() + is_relative_to(base).
A-08: el MIME salia `image/jpeg` para todo lo que no fuera .png.
A-07: POST /repo/save no tenia llamador ni tests (camino de escritura muerto).
A-26/A-31: una sola validacion de ruta, sin parametros por defecto muertos.
Puros: llaman a los handlers directo sobre un tmp_path."""
import asyncio
import base64
import inspect

import pytest
from fastapi import HTTPException

import api.admin.repository as repo
from auth.models import AuthUser

ADMIN = AuthUser(user_id="1", tenant_id="1", role="superadmin")


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for carpeta in repo.ALLOWED_FOLDERS:
        (base / carpeta).mkdir(parents=True)
    hermana = tmp_path / "repo-x"
    hermana.mkdir()
    (hermana / "secreto.txt").write_text("no deberia salir")
    monkeypatch.setattr(repo, "REPO_BASE", base)
    return base


def _error(corutina) -> HTTPException:
    try:
        asyncio.run(corutina)
    except HTTPException as e:
        return e
    raise AssertionError("se esperaba HTTPException")


def test_leer_una_carpeta_hermana_que_empieza_igual_es_400(raiz):
    e = _error(repo.get_file(path="documents/../../repo-x/secreto.txt", user=ADMIN))
    assert (e.status_code, e.detail) == (400, "ruta_invalida")


def test_borrar_en_una_carpeta_hermana_es_400_y_no_borra(raiz):
    e = _error(repo.delete_file(path="documents/../../repo-x/secreto.txt", user=ADMIN))
    assert (e.status_code, e.detail) == (400, "ruta_invalida")
    assert (raiz.parent / "repo-x" / "secreto.txt").exists()


def test_carpeta_no_permitida_es_400(raiz):
    e = _error(repo.get_file(path="otra/archivo.txt", user=ADMIN))
    assert (e.status_code, e.detail) == (400, "ruta_invalida")


def test_archivo_inexistente_es_404(raiz):
    e = _error(repo.get_file(path="documents/no-existe.md", user=ADMIN))
    assert (e.status_code, e.detail) == (404, "archivo_no_encontrado")


@pytest.mark.parametrize("nombre, mime", [
    ("a.png", "image/png"), ("b.JPG", "image/jpeg"), ("c.gif", "image/gif"),
    ("d.webp", "image/webp"), ("e.svg", "image/svg+xml"),
])
def test_el_mime_de_la_imagen_es_el_real(raiz, nombre, mime):
    (raiz / "images" / nombre).write_bytes(b"\x00\x01")
    datos = asyncio.run(repo.get_file(path=f"images/{nombre}", user=ADMIN))
    assert datos["type"] == "image"
    assert datos["base64"] == f"data:{mime};base64,{base64.b64encode(b'\x00\x01').decode()}"


def test_markdown_y_texto_se_leen_igual_que_antes(raiz):
    (raiz / "documents" / "nota.md").write_text("# hola")
    (raiz / "documents" / "nota.txt").write_text("hola")
    assert asyncio.run(repo.get_file(path="documents/nota.md", user=ADMIN)) == {
        "name": "nota.md", "type": "markdown", "content": "# hola"}
    assert asyncio.run(repo.get_file(path="documents/nota.txt", user=ADMIN))["type"] == "text"


def test_borrar_un_archivo_propio_funciona(raiz):
    (raiz / "missions" / "m.md").write_text("x")
    assert asyncio.run(repo.delete_file(path="missions/m.md", user=ADMIN)) == {"ok": True}
    assert not (raiz / "missions" / "m.md").exists()


def test_save_no_existe():
    assert not any(getattr(r, "path", "") == "/api/admin/repo/save" for r in repo.router.routes)
    assert not hasattr(repo, "SaveFileRequest")


def test_sin_parametros_por_defecto_muertos():
    assert list(inspect.signature(repo._file_info).parameters) == ["path"]
    assert not hasattr(repo, "_safe_path")
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_repositorio_rutas.py -q`
Expected: FAIL. El que importa: `test_leer_una_carpeta_hermana_que_empieza_igual_es_400` falla porque el código viejo DEVUELVE el archivo (`{"name": "secreto.txt", ...}`, sin excepción). Copiar esa salida al reporte: es la evidencia del defecto.

- [ ] **Step 3: Implementar**

```python
# backend/api/admin/repository.py
import asyncio
import base64
import mimetypes
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api/admin")

REPO_BASE = os.path.expanduser("~/jax/repo")
ALLOWED_FOLDERS = {"missions", "pipelines", "documents", "images"}
IMAGENES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _resolve(path: str) -> Path:
    """La única validación de rutas (A-26). A-40 (2026-09-16): antes era
    `realpath(...).startswith(base)` sin separador, y `documents/../../repo-x/…`
    salía del repositorio. is_relative_to compara por componentes."""
    partes = path.replace("\\", "/").split("/", 1)
    if len(partes) < 2 or partes[0] not in ALLOWED_FOLDERS:
        raise HTTPException(status_code=400, detail="ruta_invalida")
    base = Path(REPO_BASE).resolve()
    destino = (base / partes[0] / partes[1]).resolve()
    if not destino.is_relative_to(base):
        raise HTTPException(status_code=400, detail="ruta_invalida")
    if not destino.is_file():
        raise HTTPException(status_code=404, detail="archivo_no_encontrado")
    return destino


def _file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": os.path.relpath(path, Path(REPO_BASE).resolve()),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _listar() -> dict:
    base = Path(REPO_BASE)
    result = {}
    for folder in ALLOWED_FOLDERS:
        carpeta = base / folder
        carpeta.mkdir(parents=True, exist_ok=True)
        result[folder] = [_file_info(p.resolve()) for p in sorted(carpeta.iterdir()) if p.is_file()]
    return {"folders": result}


def _leer(destino: Path) -> dict:
    if destino.suffix.lower() in IMAGENES:
        mime = mimetypes.guess_type(destino.name)[0]
        b64 = base64.b64encode(destino.read_bytes()).decode()
        return {"name": destino.name, "type": "image", "base64": f"data:{mime};base64,{b64}"}
    contenido = destino.read_text(encoding="utf-8", errors="replace")
    tipo = "markdown" if destino.suffix.lower() == ".md" else "text"
    return {"name": destino.name, "type": tipo, "content": contenido}


# Disco en un hilo (LAS CUATRO, async): un archivo grande no congela el loop.
@router.get("/repo")
async def list_repo(user: AuthUser = Depends(require_superadmin)):
    return await asyncio.to_thread(_listar)


@router.get("/repo/file")
async def get_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    destino = await asyncio.to_thread(_resolve, path)
    return await asyncio.to_thread(_leer, destino)


@router.delete("/repo/file")
async def delete_file(path: str = Query(...), user: AuthUser = Depends(require_superadmin)):
    destino = await asyncio.to_thread(_resolve, path)
    await asyncio.to_thread(destino.unlink)
    return {"ok": True}
```

Nota: `_file_info` resuelve la base igual que `_resolve`, así que `path` relativo sale igual que antes (`documents/informe.pdf`). Los tests de `AdminRepository.test.jsx` no cambian.

- [ ] **Step 4: Verde**

Run: el comando del Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/api/admin/repository.py backend/tests/test_repositorio_rutas.py
$G commit -m "fix(repo): la ruta del repositorio no sale a carpetas hermanas; MIME real y sin /repo/save (A-07/08/26/31/40)"
```

---

### Task 4: Admin backend — SMTP, llaves, catálogo y un comentario (A-25, A-39, A-46, A-47)

**Files:**
- Modify: `backend/smtp_config.py` (agregar `http_de_fallo_de_envio`)
- Modify: `backend/api/admin/smtp.py:190-206`, `backend/api/admin/users.py:421-436`
- Modify: `backend/api/admin/keys.py:58,137-139,168,219,238`
- Modify: `backend/api/admin/models.py:44-61`
- Modify: `backend/api/admin/credentials.py:28-36`
- Create: `backend/tests/test_admin_recortes.py`

**Interfaces:**
- Produces: `smtp_config.http_de_fallo_de_envio(exc: UnicodeEncodeError | OSError | smtplib.SMTPException, registro: logging.Logger, contexto: str, destinatario: str) -> HTTPException`; `keys.USUARIO_LLAVES_LEGADO = 1`; `models._MODEL_FIELDS: tuple[str, ...]`.

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_admin_recortes.py
"""Frente A (2026-09-16): recortes de admin con remedio corregido por
terceros (spec A.2). Puros."""
import logging
import smtplib
from pathlib import Path

import pytest

import smtp_config
from api.admin import keys, models

BACKEND = Path(__file__).resolve().parent.parent
REGISTRO = logging.getLogger("prueba.smtp")


# A-39: solo las ramas comunes. ValueError queda en cada sitio (en smtp.py el
# ValueError de construir_mensaje va en otro try; en users.py, entre las dos).
def test_unicode_encode_error_es_502_y_nunca_loguea_la_excepcion(caplog):
    exc = UnicodeEncodeError("ascii", "clave-ñ-secreta", 6, 7, "ordinal not in range(128)")
    with caplog.at_level(logging.WARNING, logger="prueba.smtp"):
        http = smtp_config.http_de_fallo_de_envio(exc, REGISTRO, "Correo de prueba SMTP", "a@b.c")
    assert (http.status_code, http.detail) == (502, {"code": "smtp_password_no_ascii", "server": ""})
    assert "clave-ñ-secreta" not in caplog.text
    assert [r.getMessage() for r in caplog.records] == [
        "Correo de prueba SMTP a a@b.c: la contraseña SMTP guardada no es ASCII (AUTH)"]


@pytest.mark.parametrize("exc", [OSError("conexion rechazada"), smtplib.SMTPException("550 rechazado")])
def test_oserror_y_smtpexception_son_502_con_la_respuesta_del_servidor(caplog, exc):
    with caplog.at_level(logging.WARNING, logger="prueba.smtp"):
        http = smtp_config.http_de_fallo_de_envio(exc, REGISTRO, "Enlace de recuperación (admin)", "x@y.z")
    assert (http.status_code, http.detail) == (502, {"code": "smtp_envio_fallido", "server": str(exc)})
    assert [r.getMessage() for r in caplog.records] == [f"Enlace de recuperación (admin) a x@y.z falló: {exc}"]


def test_otra_excepcion_no_se_traduce_en_silencio():
    with pytest.raises(TypeError):
        smtp_config.http_de_fallo_de_envio(ValueError("x"), REGISTRO, "c", "d")


# A-46
def test_el_usuario_de_las_llaves_legadas_tiene_nombre():
    assert keys.USUARIO_LLAVES_LEGADO == 1
    fuente = (BACKEND / "api/admin/keys.py").read_text(encoding="utf-8")
    assert "user_id=1" not in fuente
    assert "user_id = 1" not in fuente
    assert "VALUES (1," not in fuente


# A-25
def test_las_columnas_del_catalogo_son_una_tupla_y_no_cambian():
    assert isinstance(models._MODEL_FIELDS, tuple)
    assert models._MODEL_FIELDS == (
        "id", "provider_id", "model_id", "is_alias", "context_window", "supports_tool_use",
        "supports_structured_output", "input_modalities", "price_input_per_1m_usd",
        "price_output_per_1m_usd", "price_cache_per_1m_usd", "release_date",
        "deprecation_date", "status", "source", "source_checked_at", "consecutive_misses",
        "max_tokens_param", "max_output_tokens",
    )
    assert models._MODEL_COLUMNS == ", ".join(models._MODEL_FIELDS)
    assert "incidente thot" in (BACKEND / "api/admin/models.py").read_text(encoding="utf-8")


# A-47: desde T6-2 la key de Gemini va en la cabecera x-goog-api-key.
def test_el_comentario_de_gemini_no_dice_query_string():
    assert "query string" not in (BACKEND / "api/admin/credentials.py").read_text(encoding="utf-8")
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_admin_recortes.py -q`
Expected: FAIL en todos.

- [ ] **Step 3: Implementar**

`smtp_config.py` (al final; agregar `from fastapi import HTTPException` en los imports):

```python
def http_de_fallo_de_envio(exc, registro, contexto: str, destinatario: str) -> HTTPException:
    """Las dos ramas de fallo de envío que comparten /smtp/test y "Enviar
    enlace" (frente A, A-39, 2026-09-16). El ValueError NO pasa por acá: en
    smtp.py sale de construir_mensaje en otro try, y en users.py va entre
    estas dos ramas. El orden de captura en el llamador: esta tupla ANTES de
    ValueError (UnicodeEncodeError es subclase de ValueError).

    NUNCA se interpola `exc` en el UnicodeEncodeError: smtplib codifica el
    AUTH en ascii y `exc.object` trae la contraseña entera (medido)."""
    if isinstance(exc, UnicodeEncodeError):
        registro.warning("%s a %s: la contraseña SMTP guardada no es ASCII (AUTH)", contexto, destinatario)
        return HTTPException(status_code=502, detail={"code": "smtp_password_no_ascii", "server": ""})
    if isinstance(exc, (OSError, smtplib.SMTPException)):
        registro.warning("%s a %s falló: %s", contexto, destinatario, exc)
        return HTTPException(status_code=502, detail={"code": "smtp_envio_fallido", "server": str(exc)})
    raise TypeError(f"http_de_fallo_de_envio no traduce {type(exc).__name__}")
```

`api/admin/smtp.py`, el try del envío:

```python
    try:
        # La prueba se ESPERA a propósito (quien la pide quiere el veredicto),
        # pero en un hilo: smtplib no toca el event loop.
        await asyncio.to_thread(smtp_config.enviar, settings, mensaje)
    except (UnicodeEncodeError, OSError, smtplib.SMTPException) as exc:
        raise smtp_config.http_de_fallo_de_envio(exc, logger, "Correo de prueba SMTP", destinatario) from exc
```

`api/admin/users.py`, dentro del try de `send_reset_link` (el `finally` con `enviado` no cambia):

```python
    try:
        await asyncio.to_thread(auth_api._send_reset_email, settings, email, enlace)
        enviado = True
    except (UnicodeEncodeError, OSError, smtplib.SMTPException) as exc:
        raise smtp_config.http_de_fallo_de_envio(exc, logger, "Enlace de recuperación (admin)", email) from exc
    except ValueError as exc:
        # construir_mensaje rechaza encabezados con caracteres de control:
        # misma red de estado corrupto que /smtp/test, mismo código.
        logger.warning("Enlace de recuperación (admin): no se pudo armar el mensaje: %s", exc)
        raise HTTPException(status_code=503, detail="smtp_config_corrupta") from exc
    finally:
```

El comentario de arriba del try ("Mismo juego de excepciones que /smtp/test…") queda y suma: "Las dos ramas comunes las traduce smtp_config.http_de_fallo_de_envio (A-39)". Verificar con grep que users.py ya importa `smtp_config`; si no, agregar `import smtp_config`.

`api/admin/keys.py`:

```python
# El almacén legado `user_api_keys` guarda las llaves de proveedor a nombre
# del superadmin sembrado (user_id=1 de db/seed.py), no del que hace el
# pedido: es la "red de seguridad" de credentials.py:4 (A-46, 2026-09-16).
USUARIO_LLAVES_LEGADO = 1
```

y reemplazar: `async def _seed_keys_from_env(pool, user_id: int = USUARIO_LLAVES_LEGADO)`; en `list_keys`, `user_id=USUARIO_LLAVES_LEGADO` (dos veces); en `test_key`, `user_id=USUARIO_LLAVES_LEGADO`; en el INSERT de `update_key`, `"VALUES (%s, %s, %s, %s) "` con `(USUARIO_LLAVES_LEGADO, provider_id, prov["env_key"], encrypted)`; en `delete_key`, `"DELETE FROM user_api_keys WHERE user_id = %s AND provider_id = %s"` con `(USUARIO_LLAVES_LEGADO, provider_id)`.

`api/admin/models.py`:

```python
_MODEL_FIELDS = (
    "id", "provider_id", "model_id", "is_alias", "context_window", "supports_tool_use",
    "supports_structured_output", "input_modalities", "price_input_per_1m_usd",
    "price_output_per_1m_usd", "price_cache_per_1m_usd", "release_date",
    "deprecation_date", "status", "source", "source_checked_at", "consecutive_misses",
    # max_tokens_param (2026-08-27, incidente thot): … (el comentario original, íntegro)
    # max_output_tokens (2026-08-27, segunda mitad del mismo incidente): … (íntegro)
    "max_tokens_param", "max_output_tokens",
)
_MODEL_COLUMNS = ", ".join(_MODEL_FIELDS)
```

Los dos comentarios del incidente se mueven sin cambiar una palabra.

`api/admin/credentials.py:35`: `"gemini":   None,  # la key va en la cabecera x-goog-api-key (T6-2), caso especial abajo`.

- [ ] **Step 4: Verde, puros y con DB**

Run: el comando del Step 2. Expected: PASS.
Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_smtp_endpoints.py tests/test_smtp_config.py tests/test_contrasenas.py tests/test_keys_http_pooling.py tests/test_t6_seguimiento.py tests/test_admin_keys_n1.py tests/test_admin_keys_model_source.py tests/test_admin_models_endpoints.py -q`
Expected: PASS (fijan los códigos SMTP, que nunca se loguee `exc`, y la firma de `_get_db_key`).

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/smtp_config.py backend/api/admin/smtp.py backend/api/admin/users.py backend/api/admin/keys.py backend/api/admin/models.py backend/api/admin/credentials.py backend/tests/test_admin_recortes.py
$G commit -m "refactor(admin): helper SMTP de ramas comunes, usuario legado con nombre, columnas en tupla (A-25/39/46/47)"
```

---

### Task 5: Rutas desde el entorno, lectura del audit y semilla (A-41, A-54, A-55)

**Files:**
- Create: `backend/config_de_entorno.py`
- Modify: `backend/api/audit.py`, `backend/api/command.py:17-18`, `backend/api/admin/repository.py` (`REPO_BASE`), `backend/api/chat.py:76-79,105,633-635`, `backend/shadow_validation.py:84`, `backend/governance_context.py:126`, `backend/jax_engine/owner_cleanup.py` (sin cambio de código: importa `MISSIONS_DIR`)
- Modify: `backend/db/seed.py`
- Modify: `backend/tests/conftest.py` (después del bloque de `JAX_USAGE_SPOOL_DIR`)
- Modify: `backend/tests/test_chat_contract_prompt.py:33`, `backend/tests/test_seed_admin_password.py:83`, `backend/tests/test_admin_usuarios_baja.py:306`, `backend/tests/test_repositorio_rutas.py` (sin cambio: usa monkeypatch)
- Modify: `.github/workflows/policy.yml` (env del job `backend-tests-no-db`: `JAX_CONFIG_PATH`)
- Create: `backend/tests/test_rutas_de_entorno.py`

**Interfaces:**
- Consumes: `repository.REPO_BASE` (Task 3).
- Produces: `config_de_entorno.ruta_requerida(nombre) -> Path`; `db.seed.email_de_semilla() -> str`; `db.seed.tenant_de_semilla() -> str`; los módulos exponen `AUDIT_LOG: Path`, `MISSIONS_DIR: Path`, `JAX_BIN: Path`, `REPO_BASE: Path`, y `chat.JAX_REPO: Path`.

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_rutas_de_entorno.py
"""Frente A (2026-09-16).

A-55: AUDIT_LOG, MISSIONS_DIR, JAX_BIN, REPO_BASE y el sys.path de ~/jax
estaban atados a $HOME sin variable. JAX_REPO_PATH y JAX_CONFIG_PATH tenian
default ~/jax (produccion corria con el default: no estan en /etc/jax/.env,
inventario 2026-08-09). Ahora las seis son obligatorias: sin la variable el
modulo no se importa y el servicio no arranca (fail-closed).
A-41: el audit se lee con deque(maxlen=20), filtrando vacias ANTES, en un hilo.
A-54: email y tenant de la semilla desde el entorno; si faltan y hay que
sembrar, error explicito. Puros salvo el ultimo (pide client)."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import api.audit as audit_mod
from auth.models import AuthUser
from config_de_entorno import ruta_requerida

BACKEND = Path(__file__).resolve().parent.parent
SUPER = AuthUser(user_id="1", tenant_id="1", role="superadmin")


def test_ruta_requerida_sin_variable_es_error_con_el_nombre(monkeypatch):
    monkeypatch.delenv("JAX_PRUEBA_RUTA", raising=False)
    with pytest.raises(RuntimeError, match="JAX_PRUEBA_RUTA"):
        ruta_requerida("JAX_PRUEBA_RUTA")


def test_ruta_requerida_relativa_es_error(monkeypatch):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", "jax/repo")
    with pytest.raises(RuntimeError, match="absoluta"):
        ruta_requerida("JAX_PRUEBA_RUTA")


def test_ruta_requerida_absoluta_se_devuelve(monkeypatch, tmp_path):
    monkeypatch.setenv("JAX_PRUEBA_RUTA", str(tmp_path))
    assert ruta_requerida("JAX_PRUEBA_RUTA") == tmp_path


@pytest.mark.parametrize("modulo, variable", [
    ("api.audit", "JAX_AUDIT_LOG_PATH"),
    ("api.command", "JAX_MISSIONS_DIR"),
    ("api.command", "JAX_BIN"),
    ("api.admin.repository", "JAX_REPO_BASE"),
    ("governance_context", "JAX_REPO_PATH"),
    ("api.chat", "JAX_CONFIG_PATH"),
])
def test_sin_la_variable_el_modulo_no_se_importa(modulo, variable):
    entorno = {k: v for k, v in os.environ.items() if k != variable}
    r = subprocess.run([sys.executable, "-c", f"import {modulo}"], cwd=BACKEND, env=entorno,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert variable in r.stderr, r.stderr[-2000:]


# Fuera, con motivo: model_catalog.py (~/.claude/.credentials.json, ubicacion
# que define Claude Code) y el cwd del subproceso jax en api/command.py (el
# directorio de trabajo de la CLI, no una ruta de datos).
PERMITIDOS = {("model_catalog.py", "expanduser"), ("api/command.py", "Path.home()")}


def test_ningun_modulo_de_produccion_arma_rutas_desde_home():
    hallazgos = []
    for ruta in BACKEND.rglob("*.py"):
        rel = ruta.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/")):
            continue
        texto = ruta.read_text(encoding="utf-8")
        for patron in ("expanduser", "Path.home()"):
            if patron in texto and (rel, patron) not in PERMITIDOS:
                hallazgos.append(f"{rel}: {patron}")
    assert hallazgos == []


def _leer(monkeypatch, ruta):
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", ruta)
    return asyncio.run(audit_mod.get_audit(user=SUPER))


def test_audit_con_lineas_vacias_devuelve_las_ultimas_20_con_contenido(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("".join(json.dumps({"event": f"E{i}"}) + "\n\n   \n" for i in range(30)))
    eventos = _leer(monkeypatch, ruta)["events"]
    assert [e["event"] for e in eventos] == [f"E{i}" for i in range(29, 9, -1)]


def test_audit_no_carga_el_archivo_entero_y_lee_en_un_hilo(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text(json.dumps({"event": "E"}) + "\n")

    def entero(*a, **k):
        raise AssertionError("read_text carga el log entero en memoria")

    en_hilo = []
    real = asyncio.to_thread

    async def espia(funcion, *a, **k):
        en_hilo.append(funcion)
        return await real(funcion, *a, **k)

    monkeypatch.setattr(Path, "read_text", entero)
    monkeypatch.setattr(asyncio, "to_thread", espia)
    assert _leer(monkeypatch, ruta) == {"events": [{"event": "E"}]}
    assert en_hilo, "la lectura del audit no pasó por asyncio.to_thread"


def test_la_semilla_sin_email_es_error_explicito(monkeypatch):
    from db import seed
    monkeypatch.delenv("JAX_SEED_SUPERADMIN_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="JAX_SEED_SUPERADMIN_EMAIL"):
        seed.email_de_semilla()


def test_la_semilla_sin_tenant_es_error_explicito(monkeypatch):
    from db import seed
    monkeypatch.delenv("JAX_SEED_TENANT_NAME", raising=False)
    with pytest.raises(RuntimeError, match="JAX_SEED_TENANT_NAME"):
        seed.tenant_de_semilla()


def test_la_semilla_no_trae_datos_de_personas_en_el_codigo():
    fuente = (BACKEND / "db/seed.py").read_text(encoding="utf-8")
    assert "rich-hn" not in fuente
    assert "Diamante" not in fuente


def test_sin_variables_la_semilla_no_falla_si_no_hay_que_sembrar(client, monkeypatch):
    """user_id=1 y tenant 1 ya existen en jax_memory_test: no hace falta sembrar."""
    from db.seed import run_seed
    monkeypatch.delenv("JAX_SEED_SUPERADMIN_EMAIL", raising=False)
    monkeypatch.delenv("JAX_SEED_TENANT_NAME", raising=False)
    client.portal.call(run_seed)
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_rutas_de_entorno.py -q`
Expected: la colección falla con `ModuleNotFoundError: config_de_entorno`. Anotar ese rojo. Para ver el rojo de cada test, crear primero SOLO `config_de_entorno.py` (Step 3a) y volver a correr: FAIL en los parametrizados (los módulos se importan igual), en el scan de `$HOME`, en `test_audit_no_carga…` y en los tres de la semilla. `test_audit_con_lineas_vacias…` pasa también con el código viejo: fija conducta.

- [ ] **Step 3a: El helper**

```python
# backend/config_de_entorno.py
"""Rutas de datos desde el entorno (frente A, A-55, 2026-09-16).

Una sola forma de leerlas, fail-closed: sin la variable, o con una ruta
relativa, el módulo que la pide no se importa y el servicio no arranca. Un
default relativo a $HOME ataba el backend a la máquina de Fernando y, en los
tests, hacía escribir en ~/jax/missions REAL (test_command_path_traversal)."""
import os
from pathlib import Path


def ruta_requerida(nombre: str) -> Path:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"{nombre} no configurada en /etc/jax/.env (ruta absoluta obligatoria)")
    ruta = Path(valor)
    if not ruta.is_absolute():
        raise RuntimeError(f"{nombre} tiene que ser una ruta absoluta, no {valor!r}")
    return ruta
```

- [ ] **Step 3b: Los lectores**

`api/audit.py`:

```python
import asyncio
import json
from collections import deque

from fastapi import APIRouter, Depends, HTTPException

from auth.middleware import require_superadmin
from auth.models import AuthUser
from config_de_entorno import ruta_requerida

router = APIRouter(prefix="/api")

AUDIT_LOG = ruta_requerida("JAX_AUDIT_LOG_PATH")


def _ultimas_20_lineas(ruta) -> list[str]:
    """A-41 (2026-09-16): recorre el archivo una vez guardando solo 20 líneas.
    Las vacías se filtran ANTES de entrar al deque; si no, contarían entre las
    20 y saldrían menos eventos."""
    with open(ruta, encoding="utf-8") as archivo:
        return list(deque((linea for linea in archivo if linea.strip()), maxlen=20))


# Task 6 S3 (2026-09-15): … (comentario existente, íntegro)
@router.get("/audit")
async def get_audit(user: AuthUser = Depends(require_superadmin)):
    if not AUDIT_LOG.exists():
        return {"events": []}
    try:
        lineas = await asyncio.to_thread(_ultimas_20_lineas, AUDIT_LOG)
    except (OSError, UnicodeDecodeError) as exc:
        # Task 3 (2026-09-15, clase b): … (comentario existente, íntegro)
        raise HTTPException(status_code=503, detail="auditoria_ilegible") from exc
    events = []
    for line in reversed(lineas):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:  # fail-soft: descarta una linea JSONL corrupta entre las ultimas 20 mostradas; el resto del log se muestra igual, no es un fallo total silencioso
            pass
    return {"events": events}
```

`api/command.py`: `from config_de_entorno import ruta_requerida`; `MISSIONS_DIR = ruta_requerida("JAX_MISSIONS_DIR")`; `JAX_BIN = ruta_requerida("JAX_BIN")`.
`api/admin/repository.py`: `from config_de_entorno import ruta_requerida`; `REPO_BASE = ruta_requerida("JAX_REPO_BASE")`; borrar `import os` solo si queda sin uso (`os.path.relpath` lo sigue usando: queda).

`api/chat.py`:

```python
# Rutas al repo `jax` (repo vecino), OBLIGATORIAS (frente A, A-55): antes
# caían a ~/jax. Sin ellas el módulo no se importa. CI las fija en el job.
CONFIG_PATH = str(ruta_requerida("JAX_CONFIG_PATH"))
JAX_REPO = ruta_requerida("JAX_REPO_PATH")
```

(el bloque de comentario viejo de `CONFIG_PATH` queda, con la última frase "El default se conserva…" reemplazada por "Desde el 2026-09-16 no hay default: ver config_de_entorno.py"). L105: `sys.path.insert(0, str(JAX_REPO))`. L633-635: `_GOVERNANCE_DIR = os.path.join(str(JAX_REPO), "policy", "governance")`, y en el comentario de arriba, "no el `~/jax` hardcodeado del import de MemoryDB de más arriba" → "la misma JAX_REPO del import de MemoryDB". Agregar el import `from config_de_entorno import ruta_requerida` junto a los demás.

`shadow_validation.py:84`: `JAX_REPO = ruta_requerida("JAX_REPO_PATH")`. `governance_context.py:126`: `JAX_REPO = ruta_requerida("JAX_REPO_PATH")`. Cada uno con su import. Verificar con grep que `os.path.expanduser` queda sin uso en los dos y borrar el import si corresponde.
`tests/test_chat_contract_prompt.py:33`: `str(Path(os.environ["JAX_REPO_PATH"]) / "policy" / "governance"),`.

- [ ] **Step 3c: La semilla**

`db/seed.py`:

```python
def email_de_semilla() -> str:
    """A-54 (2026-09-16): el superadmin sembrado sale del entorno. Solo se
    pide cuando hay que sembrar (user_id=1 no existe)."""
    valor = os.environ.get("JAX_SEED_SUPERADMIN_EMAIL", "").strip()
    if not valor:
        raise RuntimeError("JAX_SEED_SUPERADMIN_EMAIL no configurada: hace falta para sembrar user_id=1")
    return valor


def tenant_de_semilla() -> str:
    valor = os.environ.get("JAX_SEED_TENANT_NAME", "").strip()
    if not valor:
        raise RuntimeError("JAX_SEED_TENANT_NAME no configurada: hace falta para sembrar tenant_id=1")
    return valor
```

En `_resolve_seed_admin_password`, el warning pasa a `"aleatoria para user_id=1: {generated} "` (sin correo). En `run_seed`:

```python
            if count == 0:
                await cur.execute(
                    "INSERT INTO jax_tenants (tenant_id, name, plan, status) "
                    "VALUES (1, %s, 'superadmin', 'active')",
                    (tenant_de_semilla(),),
                )
```

```python
            if count == 0:
                hashed = _hash(_resolve_seed_admin_password())
                await cur.execute(
                    "INSERT INTO jax_users "
                    "(user_id, tenant_id, email, password_hash, role, status) "
                    "VALUES (1, 1, %s, %s, 'superadmin', 'active')",
                    (email_de_semilla(), hashed),
                )
```

- [ ] **Step 3d: conftest y tests que fijaban el correo**

En `tests/conftest.py`, después de `os.environ["JAX_USAGE_SPOOL_DIR"] = …`:

```python
# Rutas de datos aisladas (2026-09-16, frente A, A-55), por la misma razón
# que el sello y el respaldo de uso: api/command.py, api/audit.py y
# api/admin/repository.py las leen AL IMPORTARSE. Antes eran ~/jax/... REALES
# y test_command_path_traversal escribía en ~/jax/missions de producción.
# Forzadas (no setdefault): un /etc/jax/.env con las rutas reales no puede
# ganarles. JAX_REPO_PATH y JAX_CONFIG_PATH NO se fijan acá: apuntan al repo
# `jax` de verdad (vocabulario, config) y las pone el job de CI o quien corre.
_RUTAS_DE_PRUEBA = tempfile.mkdtemp(prefix="jax-test-rutas-")
os.environ["JAX_MISSIONS_DIR"] = os.path.join(_RUTAS_DE_PRUEBA, "missions")
os.environ["JAX_REPO_BASE"] = os.path.join(_RUTAS_DE_PRUEBA, "repo")
os.environ["JAX_AUDIT_LOG_PATH"] = os.path.join(_RUTAS_DE_PRUEBA, "audit.jsonl")
os.environ["JAX_BIN"] = os.path.join(_RUTAS_DE_PRUEBA, "bin", "jax")
# Semilla (A-54): en una base vacía (CI) hay que sembrar user_id=1. Valores de
# prueba salvo que el .env traiga los reales.
os.environ.setdefault("JAX_SEED_SUPERADMIN_EMAIL", "superadmin-semilla@example.invalid")
os.environ.setdefault("JAX_SEED_TENANT_NAME", "Tenant de la semilla de prueba")
```

`tests/test_seed_admin_password.py:83`: `assert email == os.environ["JAX_SEED_SUPERADMIN_EMAIL"]`. `tests/test_admin_usuarios_baja.py:306`: `assert fila["deleted_by_email"] == os.environ["JAX_SEED_SUPERADMIN_EMAIL"]  # el superadmin sembrado (user_id=1)`, con `import os` si falta.

**Ojo con la base de tests local:** `jax_memory_test` ya tiene user_id=1 con el correo viejo. El test de la baja compara contra el correo de la variable, así que sin reseed fallaría. `test_run_seed_reseeds_admin_from_env_var_on_empty_db` borra y resiembra user_id=1 con el correo de la variable. Correr la suite completa una vez (Step 4) y confirmar que los dos pasan en la misma corrida. Si el orden de colección pone la baja antes, anotarlo y resolverlo en el test de la baja, leyendo el correo de user_id=1 desde la base en vez de la variable (el test fija "quién hizo la baja", no qué correo tiene).

`.github/workflows/policy.yml`, job `backend-tests-no-db`, bloque `env`:

```yaml
      JAX_REPO_PATH: /tmp/jax
      # Obligatoria desde el 2026-09-16 (frente A, A-55): api/chat.py no se
      # importa sin ella. Apunta al clon del paso "Traer el repo jax".
      JAX_CONFIG_PATH: /tmp/jax/config/config.toml
```

- [ ] **Step 4: Verde, sin DB y con DB**

Run: el comando del Step 2. Expected: PASS (el test con `client` se saltea).
Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs`
Expected: 0 failed, 0 errors. Después: `ls ~/jax/missions | grep -c web-task-pivot` tiene que dar 0 archivos nuevos respecto de antes de correr (anotar el antes y el después).
Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs`
Expected: 0 failed, 0 errors.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/config_de_entorno.py backend/api/audit.py backend/api/command.py backend/api/admin/repository.py backend/api/chat.py backend/shadow_validation.py backend/governance_context.py backend/db/seed.py backend/tests/conftest.py backend/tests/test_chat_contract_prompt.py backend/tests/test_seed_admin_password.py backend/tests/test_admin_usuarios_baja.py backend/tests/test_rutas_de_entorno.py .github/workflows/policy.yml
$G commit -m "fix(config): rutas y semilla desde el entorno, fail-closed; audit leido en un hilo (A-41/54/55)"
```

---

### Task 6: Tablero que mide (A-05, A-06, A-35, A-36, A-37, A-38, A-49, A-52 RAM)

**Files:**
- Modify: `backend/api/admin/dashboard.py` (entero)
- Modify: `backend/tests/test_dashboard_http_pooling.py:33-35` (docstring)
- Modify: `frontend/src/pages/admin/AdminDashboard.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js` (`statRam`, `serviceNotConfigured`)
- Create: `backend/tests/test_tablero.py`, `frontend/src/pages/admin/AdminDashboard.test.jsx`

**Interfaces:**
- Consumes: `jax_engine.state.LAS_MANOS_URL`.
- Produces: `dashboard.SQL_USO_DEL_DIA`, `dashboard.SQL_LLAVES`, `dashboard.SQL_PIPELINES_COMPLETADOS`, `dashboard._rango_del_dia(dia: date) -> tuple[datetime, datetime]`, `dashboard._servicio(nombre: str, base_url: str | None) -> dict`. Respuesta: `{"services": [...], "stats": {...}}`; `status` de un servicio ∈ `alive|down|connected|error|sin_configurar`.

- [ ] **Step 1: Tests rojos (backend)**

```python
# backend/tests/test_tablero.py
"""Frente A (2026-09-16): GET /api/admin/dashboard afirmaba datos que no medía.
A-36: "JAX Engine" pingueaba /health (no existe) y `status_code < 500` daba
alive con un 404. A-35: "API Keys" contaba el .env, la verdad es `credential`.
A-49: pipelines_completed era 0 fijo. A-37: dos COUNT con DATE(created_at).
A-05/A-06: URL literal de LAS MANOS y recent_events constante."""
import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path

import aiomysql
import pytest

from api.admin import dashboard
from tests.identidades import cabeceras, sql

BACKEND = Path(__file__).resolve().parent.parent


def _fuente():
    return (BACKEND / "api/admin/dashboard.py").read_text(encoding="utf-8")


class _Resp:
    def __init__(self, codigo):
        self.status_code = codigo


def _cliente(codigo, urls):
    class _C:
        async def get(self, url, timeout=None):
            urls.append(url)
            return _Resp(codigo)

    async def fabrica():
        return _C()
    return fabrica


def test_un_404_no_es_alive(monkeypatch):
    monkeypatch.setattr(dashboard, "get_http_client", _cliente(404, []))
    assert asyncio.run(dashboard._check_http("http://127.0.0.1:1/api/health"))["status"] == "down"


def test_jax_engine_sondea_api_health_de_la_base_configurada(monkeypatch):
    urls = []
    monkeypatch.setattr(dashboard, "get_http_client", _cliente(200, urls))
    servicio = asyncio.run(dashboard._servicio("JAX Engine", "http://127.0.0.1:18080", "/api/health"))
    assert urls == ["http://127.0.0.1:18080/api/health"]
    assert servicio == {"name": "JAX Engine", "port": 18080, "status": "alive",
                        "latency_ms": servicio["latency_ms"]}


def test_sin_base_configurada_nunca_es_alive():
    assert asyncio.run(dashboard._servicio("JAX Engine", None, "/api/health")) == {
        "name": "JAX Engine", "port": None, "status": "sin_configurar", "latency_ms": None}


def test_sin_literales_ni_restos():
    fuente = _fuente()
    for resto in ("127.0.0.1:7777", "127.0.0.1:8080", "recent_events", "_count_configured_keys",
                  "_PROVIDERS_KEYS", "/etc/jax/.env", "DATE(created_at)"):
        assert resto not in fuente, resto


def test_el_rango_del_dia_sale_de_la_misma_fecha():
    assert dashboard._rango_del_dia(date(2026, 9, 16)) == (
        datetime(2026, 9, 16, 0, 0), datetime(2026, 9, 17, 0, 0))


def test_el_where_del_uso_es_un_rango_sin_funcion():
    # EXPLAIN no distingue DATE(col) de un rango en MariaDB >= 11.1: se fija el texto.
    assert "created_at >= %s AND created_at < %s" in dashboard.SQL_USO_DEL_DIA
    assert "COALESCE(" in dashboard.SQL_USO_DEL_DIA


# --- con DB -------------------------------------------------------------------
async def _explain(consulta, args):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("EXPLAIN " + consulta, args)
            return await cur.fetchall()


def test_explain_del_uso_del_dia_usa_el_indice_cubriente(client):
    filas = client.portal.call(_explain, dashboard.SQL_USO_DEL_DIA, dashboard._rango_del_dia(date.today()))
    (fila,) = filas
    assert fila["key"] == "idx_axioma_usage_periodo", fila
    assert fila["type"] == "range", fila
    assert "filesort" not in (fila["Extra"] or "") and "temporary" not in (fila["Extra"] or ""), fila


def test_explain_de_llaves_va_por_el_indice_de_credential(client):
    """provider es un catálogo que solo crece por migración (hoy 7 filas): se
    acepta recorrerlo. Lo que crece es credential, y va por idx_provider_state."""
    filas = client.portal.call(_explain, dashboard.SQL_LLAVES, ())
    (cred,) = [f for f in filas if f["table"] in ("c", "credential")]
    assert cred["key"] == "idx_provider_state", filas
    assert all("filesort" not in (f["Extra"] or "") and "temporary" not in (f["Extra"] or "") for f in filas), filas


def test_explain_de_pipelines_completados_va_por_idx_pipelines_status(client):
    (fila,) = client.portal.call(_explain, dashboard.SQL_PIPELINES_COMPLETADOS, ())
    assert fila["key"] == "idx_pipelines_status", fila


def test_el_tablero_trae_los_numeros_reales(client, monkeypatch):
    monkeypatch.delenv("JAX_PLATFORM_URL", raising=False)
    hoy = date.today()
    medianoche = datetime.combine(hoy, time.min)
    ids = [
        client.portal.call(sql, "INSERT INTO axioma_usage (tenant_id, user_id, facet, model, tokens_in, tokens_out, "
                           "cost_usd, request_type, created_at) VALUES (1, 1, 'tablero-t', 'm', 1, 1, 0, %s, %s)",
                           (tipo, cuando))
        for tipo, cuando in (("chat", medianoche), ("imagen", medianoche + timedelta(seconds=5)),
                             ("chat", medianoche - timedelta(seconds=1)))
    ]
    try:
        resp = client.get("/api/admin/dashboard", headers=cabeceras(client, "tablero-t", "superadmin"))
        ((mensajes, imagenes),) = client.portal.call(
            sql, "SELECT COUNT(*), SUM(request_type='imagen') FROM axioma_usage WHERE DATE(created_at) = %s",
            (hoy.isoformat(),), True)
        ((completados,),) = client.portal.call(
            sql, "SELECT COUNT(*) FROM jacobs_pipelines WHERE status = 'completed'", (), True)
        ((total, configuradas),) = client.portal.call(sql, dashboard.SQL_LLAVES, (), True)
    finally:
        for i in ids:
            client.portal.call(sql, "DELETE FROM axioma_usage WHERE id = %s", (i,))
    assert resp.status_code == 200, resp.text
    datos = resp.json()
    assert set(datos) == {"services", "stats"}
    s = datos["stats"]
    assert (s["messages_today"], s["images_generated"]) == (mensajes, int(imagenes or 0))
    assert s["pipelines_completed"] == completados
    assert (s["api_keys_configured"], s["api_keys_total"]) == (int(configuradas), total)
    (motor,) = [sv for sv in datos["services"] if sv["name"] == "JAX Engine"]
    assert motor["status"] == "sin_configurar"
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_tablero.py -q`
Expected: FAIL en todos (atributos que no existen; `test_un_404_no_es_alive` da `alive`).

- [ ] **Step 3: Implementar backend**

```python
# backend/api/admin/dashboard.py
import os
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit

import psutil
from fastapi import APIRouter, Depends

from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.state import LAS_MANOS_URL
from tiempo import utc_ahora

router = APIRouter(prefix="/api/admin")

# A-37 (2026-09-16): una consulta, rango sargable sobre idx_axioma_usage_periodo.
# SUM sin filas es NULL: COALESCE. EXPLAIN no distingue DATE(col) de un rango
# en MariaDB >= 11.1; tests/test_tablero.py fija el texto del WHERE.
SQL_USO_DEL_DIA = (
    "SELECT COUNT(*), COALESCE(SUM(request_type = 'imagen'), 0) FROM axioma_usage "
    "WHERE created_at >= %s AND created_at < %s"
)
# A-35: la verdad de las llaves es `credential` (credential_resolver), no el
# .env. Total = proveedores activos que usan api_key; configurados = los que
# tienen al menos una credencial activa (idx_provider_state).
SQL_LLAVES = (
    "SELECT COUNT(*), COALESCE(SUM(EXISTS (SELECT 1 FROM credential c "
    "WHERE c.provider_id = p.id AND c.state = 'active')), 0) "
    "FROM provider p WHERE p.auth_type = 'api_key' AND p.status = 'active'"
)
# A-49: total de completados (spec: status='completed', sin ventana). Índice
# idx_pipelines_status, creado por jax/jacobs/store.py::init_tables().
SQL_PIPELINES_COMPLETADOS = "SELECT COUNT(*) FROM jacobs_pipelines WHERE status = 'completed'"


def _rango_del_dia(dia: date) -> tuple[datetime, datetime]:
    """Los dos límites salen de la MISMA fecha: [00:00 del día, 00:00 del siguiente)."""
    inicio = datetime.combine(dia, time.min)
    return inicio, inicio + timedelta(days=1)


async def _check_http(url: str) -> dict:
    try:
        client = await get_http_client()
        t0 = utc_ahora()
        r = await client.get(url, timeout=3.0)
        ms = int((utc_ahora() - t0).total_seconds() * 1000)
        # A-36: solo un 200 es vivo (igual que el poller de jax_engine/state.py).
        return {"status": "alive" if r.status_code == 200 else "down", "latency_ms": ms}
    except Exception:  # fail-soft: cualquier error del ping ES 'down' en el tablero
        return {"status": "down", "latency_ms": None}


async def _servicio(nombre: str, base_url: str | None, ruta: str) -> dict:
    """Sin base configurada no se inventa un estado: `sin_configurar`."""
    if not base_url:
        return {"name": nombre, "port": None, "status": "sin_configurar", "latency_ms": None}
    return {"name": nombre, "port": urlsplit(base_url).port, **await _check_http(f"{base_url}{ruta}")}


async def _check_db() -> dict:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        return {"status": "connected"}
    except Exception:  # fail-soft: cualquier error ES status 'error' en el tablero
        return {"status": "error"}


@router.get("/dashboard")
async def get_dashboard(user: AuthUser = Depends(require_superadmin)):
    services = [
        await _servicio("LAS MANOS", LAS_MANOS_URL, "/health"),
        await _servicio("JAX Engine", os.environ.get("JAX_PLATFORM_URL"), "/api/health"),
        # Sin default: este panel MUESTRA el puerto, no conecta. … (comentario existente, íntegro)
        {"name": "MariaDB", "port": os.environ.get("JAX_DB_PORT"), **await _check_db()},
    ]

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_USO_DEL_DIA, _rango_del_dia(date.today()))
            messages_today, images_today = await cur.fetchone()
            await cur.execute("SELECT COUNT(*) FROM jax_users WHERE status = 'active'")
            (users_active,) = await cur.fetchone()
            await cur.execute("SELECT COUNT(*) FROM jax_users WHERE locked_until > %s", (utc_ahora(),))
            (users_locked,) = await cur.fetchone()
            await cur.execute(SQL_LLAVES)
            keys_total, keys_configured = await cur.fetchone()
            await cur.execute(SQL_PIPELINES_COMPLETADOS)
            (pipelines_completed,) = await cur.fetchone()

    mem = psutil.virtual_memory()
    return {
        "services": services,
        "stats": {
            "messages_today": messages_today,
            "images_generated": int(images_today),
            "pipelines_completed": pipelines_completed,
            "users_active": users_active,
            "users_locked": users_locked,
            "api_keys_configured": int(keys_configured),
            "api_keys_total": keys_total,
            "ram": {
                "total_mb": round(mem.total / 1024 / 1024),
                "used_mb": round(mem.used / 1024 / 1024),
                "percent": mem.percent,
            },
        },
    }
```

`tests/test_dashboard_http_pooling.py:33-35`, docstring: `get_dashboard() probes LAS MANOS (/health) and, if JAX_PLATFORM_URL is set, JAX Engine (/api/health). Both are unreachable in the test env, so this only pins zero new httpx.AsyncClient() instantiations.`

- [ ] **Step 4: Test rojo (frontend)**

```jsx
// frontend/src/pages/admin/AdminDashboard.test.jsx
import { render, screen, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { readFileSync } from 'node:fs'

vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import AdminDashboard from './AdminDashboard'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const DATOS = {
  services: [
    { name: 'LAS MANOS', port: 7777, status: 'alive', latency_ms: 3 },
    { name: 'JAX Engine', port: null, status: 'sin_configurar', latency_ms: null },
  ],
  stats: {
    messages_today: 12, images_generated: 2, pipelines_completed: 5, users_active: 3,
    users_locked: 0, api_keys_configured: 4, api_keys_total: 5,
    ram: { total_mb: 1000, used_mb: 900, percent: 90 },
  },
}

beforeEach(() => {
  localStorage.clear()
  api.get.mockResolvedValue({ data: DATOS })
})

describe('AdminDashboard (frente A)', () => {
  it('un servicio sin configurar no se pinta como vivo y dice por qué', async () => {
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const tarjeta = (await screen.findByText('JAX Engine')).closest('div.rounded-lg')
    expect(within(tarjeta).getByText(es.serviceNotConfigured)).toBeInTheDocument()
    expect(tarjeta.className).not.toContain('border-exito-borde')
  })

  it('el tono llega como clase completa, sin armar clases en runtime', () => {
    const fuente = readFileSync(new URL('./AdminDashboard.jsx', import.meta.url), 'utf8')
    expect(fuente).not.toMatch(/const colors\s*=/)
    expect(fuente).not.toContain('label="RAM"')
    expect(fuente).toMatch(/tono="text-info"/)
  })

  it('la etiqueta de RAM sale de i18n', async () => {
    localStorage.setItem('jax_lang', 'en')
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    expect(await screen.findByText(en.statRam)).toBeInTheDocument()
    expect(typeof es.statRam).toBe('string')
  })

  it('pipelines completados muestra el número del backend', async () => {
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const etiqueta = await screen.findByText(es.statPipelines)
    expect(within(etiqueta.parentElement).getByText('5')).toBeInTheDocument()
  })
})
```

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/pages/admin/AdminDashboard.test.jsx`
Expected: FAIL en los tres primeros (no existe `serviceNotConfigured`; `const colors`; `statRam` es función).

- [ ] **Step 5: Implementar frontend**

`AdminDashboard.jsx`:

```jsx
function ServiceCard({ service, t }) {
  const isOk = service.status === 'alive' || service.status === 'connected'
  const texto = isOk
    ? (service.status === 'connected' ? t.serviceConnected : t.serviceAlive)
    : service.status === 'sin_configurar' ? t.serviceNotConfigured
    : service.status === 'error' ? t.serviceError : t.serviceDown
  return (
    <div className={`rounded-lg p-4 border ${isOk ? 'border-exito-borde bg-exito-fondo' : 'border-peligro-borde bg-peligro-fondo'}`}>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-texto">{service.name}</div>
          {service.port && <div className="text-xs text-texto">:{service.port}</div>}
        </div>
        <div className={`flex items-center gap-1.5 text-xs font-semibold ${isOk ? 'text-exito' : 'text-peligro'}`}>
          <span className={`w-2 h-2 rounded-full ${isOk ? 'bg-exito' : 'bg-peligro'}`} />
          {texto}
        </div>
      </div>
      {service.latency_ms != null && (
        <div className="text-xs text-texto mt-1">{service.latency_ms}ms</div>
      )}
    </div>
  )
}

// A-38 (2026-09-16): el tono llega como clase COMPLETA. Armarla en runtime
// (`text-${tono}`) la esconde de Tailwind y de src/tema/contraste.test.js.
function StatCard({ label, value, tono = 'text-texto', sub }) {
  return (
    <div className="rounded-lg p-4 bg-superficie border border-borde text-center">
      <div className={`text-2xl font-bold ${tono} mb-1`}>{value}</div>
      <div className="text-xs text-texto-tenue">{label}</div>
      {sub && <div className="text-xs text-texto-tenue mt-0.5">{sub}</div>}
    </div>
  )
}
```

y en el grid:

```jsx
              <StatCard label={t.statMessages}     value={s.messages_today}      tono="text-info" />
              <StatCard label={t.statPipelines}    value={s.pipelines_completed} tono="text-texto-fuerte" />
              <StatCard label={t.statImages}       value={s.images_generated}    tono="text-acento-texto" />
              <StatCard label={t.statUsersActive}  value={s.users_active}        tono="text-exito" />
              {s.users_locked > 0 && (
                <StatCard label={t.statUsersLocked} value={s.users_locked} tono="text-aviso" />
              )}
              <StatCard
                label={t.statApiKeysLabel}
                value={`${s.api_keys_configured}/${s.api_keys_total}`}
                tono={s.api_keys_configured === s.api_keys_total ? 'text-exito' : 'text-aviso'}
              />
              {s.ram && (
                <StatCard
                  label={t.statRam}
                  value={`${s.ram.percent}%`}
                  tono={s.ram.percent > 85 ? 'text-aviso' : 'text-texto'}
                  sub={`${s.ram.used_mb} / ${s.ram.total_mb} MB`}
                />
              )}
```

i18n: en `es.js`, reemplazar `statRam: (pct) => \`RAM: ${pct}%\`,` por `statRam: 'RAM',` y agregar junto a `serviceError`: `serviceNotConfigured: 'Sin configurar',`. En `en.js`: `statRam: 'RAM',` y `serviceNotConfigured: 'Not configured',`. Antes de cambiar la forma de `statRam`, grep: `statRam` no tiene lectores (verificado en `26c9cd5`).

- [ ] **Step 6: Verde**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/pages/admin/AdminDashboard.test.jsx src/tema/contraste.test.js`
Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_tablero.py tests/test_dashboard_http_pooling.py -q`
Expected: PASS. Copiar al reporte las tres salidas de EXPLAIN (tal cual).

- [ ] **Step 7: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/api/admin/dashboard.py backend/tests/test_dashboard_http_pooling.py backend/tests/test_tablero.py frontend/src/pages/admin/AdminDashboard.jsx frontend/src/pages/admin/AdminDashboard.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js
$G commit -m "fix(tablero): mide de verdad -- llaves en credential, pipelines reales, JAX Engine en /api/health, un rango sargable (A-05/06/35/36/37/38/49/52)"
```

---

### Task 7: Facetas — sin hex en el backend y `display_name` en `/api/state` (A-42, A-48)

**Files:**
- Modify: `backend/jax_engine/schemas.py:33-38`, `backend/jax_engine/state.py:16-27,62-68,83-87`, `backend/api/facets.py:23-27`, `backend/main.py` (lifespan)
- Modify: `frontend/src/components/BottomBar/BottomBar.jsx:12-13`, `frontend/src/components/BottomBar/PipelineModal.jsx:16-17` (comentarios)
- Modify: `frontend/src/store/useJaxStore.facetTokens.test.js` (un test más)
- Create: `backend/tests/test_facetas_nombres.py`

**Interfaces:**
- Produces: `FacetState.display_name: str | None = None` (sin `color`); `JAXEngineState.cargar_nombres_de_facetas() -> None` (async; lee `SELECT \`key\`, display_name FROM facet`); `jax_engine.state.DEFAULT_FACETS: list[str]`.

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_facetas_nombres.py
"""Frente A (2026-09-16).
A-42: el backend no guarda hex de facetas; /api/facets sigue leyendo
facet.color_hex (fuente de verdad, Bloque C).
A-48: el store solo carga /api/state y ahi no habia display_name: los labels
caian al nombre crudo. Ahora /api/state lo trae de la tabla `facet`, leida UNA
vez al arrancar. INVALIDACION: reinicio del proceso -- el unico escritor de
facet.display_name son las migraciones, que corren en el arranque (lo fija
test_nadie_escribe_la_tabla_facet_en_runtime)."""
import re
from pathlib import Path

from jax_engine import state as state_mod
from jax_engine.schemas import FacetState
from tests.identidades import cabeceras, sql

BACKEND = Path(__file__).resolve().parent.parent


def test_facet_state_no_tiene_color():
    assert "color" not in FacetState(name="x").model_dump()


def test_no_hay_hex_de_facetas_en_jax_engine():
    for rel in ("jax_engine/state.py", "jax_engine/schemas.py"):
        assert not re.search(r"#[0-9a-fA-F]{6}\b", (BACKEND / rel).read_text(encoding="utf-8")), rel


class _Cursor:
    def __init__(self, filas):
        self.filas, self.consultas = filas, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, consulta, args=()):
        self.consultas.append(consulta)

    async def fetchall(self):
        return self.filas


class _Conexion:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self):
        return self._cursor


class _Pool:
    def __init__(self, cursor):
        self._cursor = cursor

    def acquire(self):
        return _Conexion(self._cursor)


async def test_cargar_nombres_pone_el_display_name_de_la_tabla(monkeypatch):
    cursor = _Cursor([("hipatia", "Hipatia"), ("jekyll", "Dr. Jekyll")])

    async def pool():
        return _Pool(cursor)

    monkeypatch.setattr(state_mod, "get_pool", pool)
    estado = state_mod.JAXEngineState()
    await estado.cargar_nombres_de_facetas()
    facetas = estado.get_state().facets
    assert (facetas["hipatia"].display_name, facetas["jekyll"].display_name) == ("Hipatia", "Dr. Jekyll")
    assert facetas["jacobs"].display_name is None
    assert cursor.consultas == ["SELECT `key`, display_name FROM facet"]


def test_nadie_escribe_la_tabla_facet_en_runtime():
    escritura = re.compile(r"(?i)\b(UPDATE|INSERT\s+(IGNORE\s+)?INTO|DELETE\s+FROM|REPLACE\s+INTO)\s+`?facet`?[\s(]")
    hallazgos = []
    for ruta in BACKEND.rglob("*.py"):
        rel = ruta.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/")) or rel == "db/migrations.py":
            continue
        if escritura.search(ruta.read_text(encoding="utf-8")):
            hallazgos.append(rel)
    assert hallazgos == []


def test_state_y_facets_sirven_los_datos_de_la_tabla(client):
    filas = client.portal.call(sql, "SELECT `key`, display_name, color_hex FROM facet", (), True)
    h = cabeceras(client, "facetas-nombres", "operator")
    estado = client.get("/api/state", headers=h).json()["facets"]
    facetas = client.get("/api/facets", headers=h).json()["facets"]
    for clave, nombre, color in filas:
        if clave in estado:
            assert estado[clave]["display_name"] == nombre
            assert "color" not in estado[clave]
        if clave in facetas:
            assert facetas[clave]["color"] == color
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_facetas_nombres.py -q`
Expected: FAIL en todos salvo `test_nadie_escribe_la_tabla_facet_en_runtime`, que ya pasa: es la guarda de la invalidación y pasa desde antes. Anotar su conducta con una mutación: agregar temporalmente `# UPDATE facet SET x` a `api/facets.py`, verlo rojo y revertir.

- [ ] **Step 3: Implementar**

`schemas.py`:

```python
class FacetState(BaseModel):
    name: str
    status: FacetStatus = "idle"
    last_message: str = ""
    last_update: str = Field(default_factory=lambda: utc_ahora().isoformat() + "Z")
    # Tabla `facet` (Bloque C), cargado al arrancar (A-48). El color no vive
    # acá: el frontend deriva el token de la clave (tema-tokens §7.3, A-42).
    display_name: str | None = None
```

`state.py`: borrar `FACET_COLORS` y reemplazar por

```python
# Solo el orden y el conjunto de facetas conocidas; identidad y color viven en
# la tabla `facet` (display_name) y en el tema del frontend (token).
DEFAULT_FACETS = ["jax_local", "jekyll", "hyde", "hipatia", "thot", "kimi", "ada", "jacobs"]
```

Agregar `from db.connection import get_pool`. `_init_facets`: `FacetState(name=name, status="idle")`. En `set_facet_status`: `self._state.facets[facet] = FacetState(name=facet)`. Método nuevo:

```python
    async def cargar_nombres_de_facetas(self):
        """A-48. Una lectura al arrancar (main.py lifespan, después de
        run_migrations). Invalidación: reinicio -- el único escritor de
        facet.display_name son las migraciones del arranque
        (tests/test_facetas_nombres.py lo fija). Sin base, el lifespan ya
        falló antes: no hay estado sin nombres que servir."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT `key`, display_name FROM facet")
                filas = await cur.fetchall()
        for clave, nombre in filas:
            faceta = self._state.facets.get(clave)
            if faceta is not None:
                faceta.display_name = nombre
```

`main.py`, lifespan: después de `await run_seed()`, `await engine_state.cargar_nombres_de_facetas()`.

`api/facets.py`:

```python
    for key, display_name, icon, color_hex in rows:
        if key in facets:
            facets[key]["display_name"] = display_name
            facets[key]["icon"] = icon
            facets[key]["color"] = color_hex
```

Comentarios del frontend: `BottomBar.jsx:12-13`: `// Solo orden de despliegue — label viene de /api/state (display_name de la tabla \`facet\`, Bloque C) y el token de color del store; no se duplican aca.` `PipelineModal.jsx:17`: `// capability_motor -- R4) -- label y token vienen de facetsState (/api/state).`

`useJaxStore.facetTokens.test.js`, un test más dentro del `describe` existente (contrato con el backend nuevo; ya pasa: el store fusiona los campos que llegan):

```js
  it('loadState: el display_name del servidor queda en la faceta (A-48)', async () => {
    api.get.mockResolvedValueOnce({
      data: { facets: { hipatia: { name: 'hipatia', status: 'idle', display_name: 'Hipatia' } }, active_pipelines: {}, las_manos_alive: true },
    })
    await useJaxStore.getState().loadState()
    expect(useJaxStore.getState().facets.hipatia.display_name).toBe('Hipatia')
  })
```

- [ ] **Step 4: Verde**

Run: el comando del Step 2 (con DB), y `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_state_por_duenio.py tests/test_facetas_nombres.py -q`
Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/store/useJaxStore.facetTokens.test.js src/components/BottomBar`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/jax_engine/schemas.py backend/jax_engine/state.py backend/api/facets.py backend/main.py backend/tests/test_facetas_nombres.py frontend/src/components/BottomBar/BottomBar.jsx frontend/src/components/BottomBar/PipelineModal.jsx frontend/src/store/useJaxStore.facetTokens.test.js
$G commit -m "fix(facetas): display_name de la tabla en /api/state y sin hex en el backend (A-42/48)"
```

---

### Task 8: `chat.py` — avisos y errores con código, espejo del auto-ruteo (A-13, A-16, A-22, A-51 chat, A-53 chat)

**Files:**
- Modify: `backend/api/chat.py` (sets L337-414 y `_auto_route`; `_MODEL_IDENTITY_*` L799-815; `_invoke_facet_dispatch` L831-936; `_detalle_502_http` L990-996; `ChatResponse`; `chat()` L1013-1140; `_fire_completed` L1142-1153)
- Modify: `backend/tests/test_chat_facet_validation.py:38,52,135,181,333`, `backend/tests/test_facet_model_wiring.py:214`, `backend/tests/test_redaccion_caminos.py:147-160`, `backend/tests/test_chat_contract_wrapper.py:286-293`
- Create: `backend/tests/test_chat_codigos.py`

**Interfaces:**
- Consumes: `JAX_REPO` (Task 5).
- Produces: `AvisoDeChat`, `ChatResponse.aviso`; `_invoke_facet_dispatch` devuelve `(str | AvisoDeChat, UsageInfo | None, outcome)`; `_detalle_502_http(facet, e) -> dict`; `_detalle_502_generico(facet, e) -> dict`; `_fire_completed(facet, tenant_id, user_id)`; nombres del espejo `KIMI_KW, KIMI_STRONG, HIPATIA_KW, HIPATIA_STRONG, JEKYLL_KW, JEKYLL_STRONG, THOT_KW, THOT_STRONG, ADA_KW, ADA_STRONG, _TIEBREAK, _KW_SETS` (la Task 14 los compara).

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_chat_codigos.py
"""Frente A (2026-09-16), api/chat.py.
A-53: las respuestas enlatadas eran texto en español; ahora `aviso` con codigo
y params (i18n en el frontend). `response` lleva una marca sin idioma para el
historial y la memoria. A-51: los 400/502 llevan detail.code. A-13: el evento
facet_response_completed ya no repite la respuesta entera. A-16: una sola
llamada a _call_ollama. A-22: los sets llevan el nombre de jax/core/router.py
para entrar a check_mirror_sync. Puros salvo el ultimo."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import http_client
from api import chat as chat_mod
from facet_resolver import FacetUnavailableError
from tests.identidades import cabeceras
from tests.test_chat_contract_wrapper import _FakePostClient, _FakeResponse

BACKEND = Path(__file__).resolve().parent.parent
CONFIG = {"personalities": {"jax_local": {"system_prompt": "x"}, "thot": {"system_prompt": "x"},
                            "otra": {"system_prompt": "x"}}}


def _despachar(monkeypatch, faceta, resuelta, mensaje="hola"):
    async def resolver(_clave):
        if resuelta is None:
            raise FacetUnavailableError(faceta)
        return resuelta
    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    return asyncio.run(chat_mod._invoke_facet_dispatch(faceta, CONFIG, "u-codigos", mensaje))


def test_sin_binding_es_un_aviso_con_codigo(monkeypatch):
    texto, usage, _ = _despachar(monkeypatch, "thot", None)
    assert usage is None
    assert texto == chat_mod.AvisoDeChat(code="faceta_sin_binding", params={"facet": "thot"})


def test_transporte_no_soportado_es_un_aviso(monkeypatch):
    f = SimpleNamespace(transport="motor_registry", model="m", provider_id="p")
    texto, _, _ = _despachar(monkeypatch, "thot", f)
    assert texto == chat_mod.AvisoDeChat(code="transporte_no_soportado",
                                        params={"facet": "thot", "transport": "motor_registry"})


def test_gate_denegado_es_un_aviso(monkeypatch):
    f = SimpleNamespace(transport="http_gemini", model="m", provider_id="gemini")
    original = http_client._client
    http_client._client = _FakePostClient(_FakeResponse({"allowed": False, "reason": "no"}))
    try:
        texto, _, _ = _despachar(monkeypatch, "thot", f)
    finally:
        http_client._client = original
    assert texto == chat_mod.AvisoDeChat(code="faceta_no_autorizada", params={"facet": "thot"})


def test_identidad_del_modelo_es_un_aviso_con_el_dato_real(monkeypatch):
    f = SimpleNamespace(transport="ollama", model="modelo-centinela", provider_id="ollama")
    texto, _, _ = _despachar(monkeypatch, "jax_local", f, "que modelo sos")
    assert texto == chat_mod.AvisoDeChat(
        code="identidad_del_modelo",
        params={"facet": "jax_local", "model": "modelo-centinela", "provider": "ollama"})


def test_la_marca_de_un_aviso_no_tiene_idioma():
    aviso = chat_mod.AvisoDeChat(code="faceta_sin_binding", params={"facet": "thot"})
    assert aviso.como_texto() == "[faceta_sin_binding facet=thot]"
    assert chat_mod.AvisoDeChat(code="hyde_usa_modo_comando").como_texto() == "[hyde_usa_modo_comando]"


def test_no_quedan_textos_enlatados_en_español():
    fuente = (BACKEND / "api/chat.py").read_text(encoding="utf-8")
    for resto in ("no está disponible", "Hyde opera", "Corro con", "_MODEL_IDENTITY_HOSTING",
                  "Error en ", "Error HTTP ", "faceta desconocida"):
        assert resto not in fuente, resto


def test_el_502_http_es_un_codigo_con_motivo_redactado():
    req = httpx.Request("POST", "https://x.test/v1")
    exc = httpx.HTTPStatusError("x", request=req, response=httpx.Response(400, text="malo", request=req))
    assert chat_mod._detalle_502_http("hipatia", exc) == {
        "code": "proveedor_error_http", "facet": "hipatia", "status": 400, "motivo": "malo"}


def test_el_502_generico_es_un_codigo():
    assert chat_mod._detalle_502_generico("thot", RuntimeError("se cayó")) == {
        "code": "faceta_error", "facet": "thot", "motivo": "se cayó"}


def test_facet_response_completed_solo_lleva_la_faceta(monkeypatch):
    publicados = []

    async def capturar(evento):
        publicados.append(evento)

    monkeypatch.setattr(chat_mod.event_bus, "publish", capturar)
    asyncio.run(chat_mod._fire_completed("thot", "1", "5"))
    (evento,) = publicados
    assert (evento.event_type, evento.payload) == ("facet_response_completed", {"facet": "thot"})


async def _capturar_prompts(monkeypatch, faceta):
    prompts = []

    async def ollama(system_prompt, history, message, config, model):
        prompts.append(system_prompt)
        return "ok", 1, 1

    async def resolver(_clave):
        return SimpleNamespace(transport="ollama", model="qwen-x", provider_id="ollama")

    monkeypatch.setattr(chat_mod, "_call_ollama", ollama)
    monkeypatch.setattr(chat_mod, "resolve_facet", resolver)
    await chat_mod._invoke_facet_dispatch(faceta, CONFIG, "u-ollama", "hola")
    return prompts[0]


async def test_solo_jax_local_recibe_el_dato_de_su_modelo(monkeypatch):
    assert "qwen-x" in await _capturar_prompts(monkeypatch, "jax_local")
    assert "qwen-x" not in await _capturar_prompts(monkeypatch, "otra")


def test_una_sola_llamada_a_call_ollama_en_el_dispatch():
    arbol = ast.parse((BACKEND / "api/chat.py").read_text(encoding="utf-8"))
    (funcion,) = [n for n in arbol.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_invoke_facet_dispatch"]
    llamadas = [n for n in ast.walk(funcion)
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_call_ollama"]
    assert len(llamadas) == 1


def test_los_sets_llevan_los_nombres_del_espejo():
    for nombre in ("KIMI_KW", "KIMI_STRONG", "HIPATIA_KW", "HIPATIA_STRONG", "JEKYLL_KW",
                   "JEKYLL_STRONG", "THOT_KW", "THOT_STRONG", "ADA_KW", "ADA_STRONG"):
        assert isinstance(getattr(chat_mod, nombre), frozenset), nombre
        assert not hasattr(chat_mod, "_" + nombre)
    assert chat_mod._TIEBREAK == ("hipatia", "thot", "ada", "kimi", "jekyll")
    assert set(chat_mod._KW_SETS) == {"kimi", "hipatia", "jekyll", "thot", "ada"}


def test_hyde_responde_un_aviso(client, chat_sin_memoria):
    resp = client.post("/api/chat", json={"message": "hola", "facet": "hyde"},
                       headers=cabeceras(client, "chat-codigos-hyde", "operator"))
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["aviso"] == {"code": "hyde_usa_modo_comando", "params": {}}
    assert cuerpo["response"] == "[hyde_usa_modo_comando]"
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_chat_codigos.py -q`
Expected: FAIL en todos salvo `test_solo_jax_local_recibe_el_dato_de_su_modelo`, que fija conducta y pasa.

- [ ] **Step 3: Implementar**

**A-22.** Renombrar `_KIMI_KW`→`KIMI_KW` … `_ADA_STRONG`→`ADA_STRONG`, `_WEB_KW_SETS`→`_KW_SETS` y `_WEB_TIEBREAK`→`_TIEBREAK`, con todas sus referencias (`_auto_route` y su docstring). El formato de cada `frozenset((...))` NO se toca: tiene que quedar byte a byte igual a `jax/core/router.py` (Discrepancia 1). El comentario de arriba de `KIMI_KW` queda:

```python
# Keywords por faceta — ESPEJO de jax/core/router.py (A-22, 2026-09-16).
# Copia a propósito: importar jax.core.router arrastra contrato_dispatch ->
# facet_resolver y rompe CI (verificado por terceros). La vigila
# jax/scripts/check_mirror_sync.py, familia `router_keywords`: un cambio acá
# se hace también allá, en el mismo paso.
# Hyde NO es destino del auto-routing: es ejecutor, no conversador.
```

**A-53.** Después de `_is_model_identity_question`, reemplazar `_MODEL_IDENTITY_HOSTING` y `_model_identity_reply` por:

```python
class AvisoDeChat(BaseModel):
    """Respuesta enlatada (sin LLM) como CÓDIGO + datos (A-53, 2026-09-16). El
    texto visible lo arma el frontend con i18n (t.avisosChat[code]).
    `como_texto()` es la marca sin idioma que va al historial del hilo y a la
    memoria: registra QUÉ pasó sin fijar un idioma en la base."""
    code: Literal["faceta_sin_binding", "faceta_no_autorizada", "transporte_no_soportado",
                  "identidad_del_modelo", "hyde_usa_modo_comando"]
    params: dict[str, str] = {}

    def como_texto(self) -> str:
        datos = " ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"[{self.code}{' ' + datos if datos else ''}]"
```

En `_invoke_facet_dispatch`: el retorno sin binding pasa a `return AvisoDeChat(code="faceta_sin_binding", params={"facet": facet}), None, OUTCOME_UNBOUND`; el denegado, a `return AvisoDeChat(code="faceta_no_autorizada", params={"facet": facet}), None, gate_outcome`; la identidad, a `return AvisoDeChat(code="identidad_del_modelo", params={"facet": facet, "model": f.model, "provider": f.provider_id}), None, OUTCOME_OK`; el transporte, a `return AvisoDeChat(code="transporte_no_soportado", params={"facet": facet, "transport": f.transport}), None, OUTCOME_UNSUPPORTED_TRANSPORT`. La anotación de retorno: `-> tuple["str | AvisoDeChat", UsageInfo | None, str]`, y la de `_invoke_facet`: `-> tuple["str | AvisoDeChat", UsageInfo | None]` (el cuerpo de `_invoke_facet` NO se toca: `test_policy_invoke_facet_envoltorio.py` fija su estructura).

**A-16:**

```python
    if f.transport == "ollama":
        # Bug 3: jax_local no sabia con que modelo corre y confabulaba su
        # identidad. Le damos el dato real como contexto informativo.
        ident = (
            f"\n\nDato tecnico (para tu propia referencia, no lo repitas sin que "
            f"te pregunten): el modelo que te ejecuta en este momento es "
            f"'{f.model}', via Ollama local en hall9000."
        ) if facet == "jax_local" else ""
        text, tin, tout = await _call_ollama(system_prompt + ident, history, message, config, f.model)
        return text, UsageInfo(f.provider_id, f.model, tin, tout), OUTCOME_OK
```

(El texto de `ident` va al modelo, no al usuario: no es i18n de UI.)

**A-51, detalles:**

```python
def _detalle_502_http(facet: str, e: httpx.HTTPStatusError) -> dict:
    """detail del 502 cuando el proveedor responde con error. Código estable
    (A-51); `motivo` es lo que dijo el proveedor, REDACTADO y DESPUÉS recortado
    (fix round 1 de 3bed155: al revés, una key que cruza el corte sale en claro)."""
    return {"code": "proveedor_error_http", "facet": facet, "status": e.response.status_code,
            "motivo": recortar_redactado(e.response.text, 200)}


def _detalle_502_generico(facet: str, e: Exception) -> dict:
    return {"code": "faceta_error", "facet": facet, "motivo": recortar_redactado(str(e), 200)}
```

`ChatResponse`: agregar `aviso: AvisoDeChat | None = None`, con el comentario `# A-53: presente en las respuestas enlatadas; el frontend muestra t.avisosChat[aviso.code].` y actualizar el de `contract_degraded` ("respuestas enlatadas (aviso)").

En `chat()`:

```python
    if req.facet is not None and req.facet not in config["personalities"]:
        raise HTTPException(status_code=400, detail={"code": "faceta_desconocida", "facet": req.facet[:50]})
```

```python
    if facet == "hyde":
        aviso = AvisoDeChat(code="hyde_usa_modo_comando")
        await _fire_completed(facet, tenant_id, user_id)
        return ChatResponse(facet=facet, response=aviso.como_texto(), timestamp=timestamp,
                            contract_degraded=False, aviso=aviso)
```

```python
    except httpx.HTTPStatusError as e:
        detail = _detalle_502_http(facet, e)
        await engine_state.set_facet_status(facet, "error", tenant_id, user_id, detail["motivo"][:100])
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        detail = _detalle_502_generico(facet, e)
        await engine_state.set_facet_status(facet, "error", tenant_id, user_id, detail["motivo"][:100])
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=502, detail=detail)
```

Después del try:

```python
    aviso = response_text if isinstance(response_text, AvisoDeChat) else None
    if aviso is not None:
        response_text = aviso.como_texto()
```

y el `return ChatResponse(...)` final suma `aviso=aviso`. `redactar_secretos` queda sin uso en chat.py si nada más lo usa: verificar con grep y ajustar el import.

**A-13:**

```python
async def _fire_completed(facet: str, tenant_id: str, user_id: str):
    # A-13 (2026-09-16): el único lector (useJaxStore) mira el event_type; la
    # respuesta ya viaja por HTTP. No se repite por el bus.
    event = JAXEvent(event_type="facet_response_completed", tenant_id=tenant_id,
                     user_id=user_id, payload={"facet": facet})
    await event_bus.publish(event)
```

y la llamada al final de `chat()`: `await _fire_completed(facet, tenant_id, user_id)`.

**Tests existentes:**
- `test_chat_facet_validation.py:38` y `:52`: `assert resp.json()["detail"]["code"] == "faceta_desconocida"`.
- `:135` y `:181`: `assert resp.json()["aviso"]["code"] == "faceta_no_autorizada"`.
- `:333`: `assert texto == chat_mod.AvisoDeChat(code="faceta_no_autorizada", params={"facet": "facet_http_nuevo"})`.
- `test_facet_model_wiring.py:214`: `assert resp.json()["aviso"]["params"]["model"] == SENTINEL_MODEL, (...)`.
- `test_redaccion_caminos.py:147-160`: `detail = chat_mod._detalle_502_http("hipatia", exc)`; `assert detail["code"] == "proveedor_error_http" and detail["status"] == 400`; `assert "AIza" not in detail["motivo"]`; `assert len(detail["motivo"]) <= 200`.
- `test_chat_contract_wrapper.py:286-293`: la regla del endpoint es "parsear solo si no es enlatada"; agregar `assert isinstance(text, chat_mod.AvisoDeChat)` antes del `is_canned`, con `from api import chat as chat_mod` dentro del test.

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_chat_codigos.py tests/test_chat_facet_validation.py tests/test_facet_model_wiring.py tests/test_redaccion_caminos.py tests/test_chat_contract_wrapper.py tests/test_policy_invoke_facet_envoltorio.py tests/test_kimi_chat_transport.py tests/test_facet_canary.py tests/test_facet_health_outcomes.py -q`
Expected: PASS.

Espejo (lectura, sin escribir en jax):

```bash
python3 - <<'EOF'
import ast
def seg(p):
    s = open(p).read(); t = ast.parse(s)
    return {n.targets[0].id: ast.get_source_segment(s, n) for n in t.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
a = seg('/home/fruiz/worktrees/jax-platform-frente-a/backend/api/chat.py'); b = seg('/home/fruiz/jax/jax/core/router.py')
nombres = ['KIMI_KW','KIMI_STRONG','HIPATIA_KW','HIPATIA_STRONG','JEKYLL_KW','JEKYLL_STRONG','THOT_KW','THOT_STRONG','ADA_KW','ADA_STRONG','_TIEBREAK','_KW_SETS']
print({n: a.get(n) == b.get(n) for n in nombres})
EOF
```
Expected: los 12 en `True`.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/api/chat.py backend/tests/test_chat_codigos.py backend/tests/test_chat_facet_validation.py backend/tests/test_facet_model_wiring.py backend/tests/test_redaccion_caminos.py backend/tests/test_chat_contract_wrapper.py
$G commit -m "fix(chat): avisos y errores con codigo estable, evento sin la respuesta, keywords con nombre de espejo (A-13/16/22/51/53)"
```

---

### Task 9: image, command, pipelines y upload con códigos (A-14, A-30, A-51, A-53 command)

**Files:**
- Modify: `backend/api/image.py`, `backend/api/command.py`, `backend/api/pipelines.py`, `backend/api/upload.py`, `backend/jax_engine/schemas.py:6-17`
- Modify: `backend/tests/test_fix_wave_final.py:150-176`
- Create: `backend/tests/test_mesa_codigos.py`

**Interfaces:**
- Consumes: `MISSIONS_DIR`, `JAX_BIN` (Task 5).
- Produces: los códigos de la tabla de Interfaces; el payload de `command_completed`; `command._escribir_fallo(task_id, motivo) -> None`.

- [ ] **Step 1: Test rojo**

```python
# backend/tests/test_mesa_codigos.py
"""Frente A (2026-09-16): la Mesa no recibe texto en español del backend.
A-51: detail con codigo estable en image/command/pipelines/upload. A-53: el
resultado de un comando sin output, fallido o simulado es un codigo. A-14:
image_generated y command_started no tienen consumidor. A-30: locales de un
solo uso. Puros."""
import ast
import asyncio
import io
import json
import os
import stat
import typing
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, UploadFile

from api import command as command_mod
from api import image as image_mod
from api import pipelines as pipelines_mod
from api import upload as upload_mod
from auth.models import AuthUser
from credential_resolver import CredentialUnavailableError
from jax_engine.schemas import EventType

BACKEND = Path(__file__).resolve().parent.parent
USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
MODULOS = ("api/chat.py", "api/image.py", "api/command.py", "api/pipelines.py", "api/upload.py")


def _error(corutina) -> HTTPException:
    try:
        asyncio.run(corutina)
    except HTTPException as e:
        return e
    raise AssertionError("se esperaba HTTPException")


@pytest.mark.parametrize("rel", MODULOS)
def test_ningun_detail_es_texto_libre(rel):
    """detail: un codigo snake_case, un dict con 'code' o un nombre (variable)."""
    malos = []
    for nodo in ast.walk(ast.parse((BACKEND / rel).read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", None) == "HTTPException":
            for kw in nodo.keywords:
                if kw.arg != "detail":
                    continue
                v = kw.value
                ok = (isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.replace("_", "").isalpha()
                      and v.value == v.value.lower()) \
                    or (isinstance(v, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "code" for k in v.keys)) \
                    or isinstance(v, ast.Name)
                if not ok:
                    malos.append(f"{rel}:{nodo.lineno}")
    assert malos == []


def test_los_eventos_sin_consumidor_no_existen():
    tipos = typing.get_args(EventType)
    assert "image_generated" not in tipos and "command_started" not in tipos


def test_imagen_sin_credencial_es_503_con_codigo(monkeypatch):
    async def sin_credencial(_p):
        raise CredentialUnavailableError("openai")
    monkeypatch.setattr(image_mod, "resolve_credential_instrumented", sin_credencial)
    e = _error(image_mod.generate_image(image_mod.ImageRequest(prompt="x"), user=USUARIO))
    assert (e.status_code, e.detail) == (503, {"code": "credencial_no_disponible", "provider": "openai"})


def test_la_imagen_no_publica_un_evento(monkeypatch):
    async def credencial(_p):
        return "sk-x"

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": "QQ=="}]}

    class _Cliente:
        async def post(self, *a, **k):
            return _Resp()

    async def cliente():
        return _Cliente()

    publicados = []

    async def publicar(evento):
        publicados.append(evento)

    async def uso(*a, **k):
        return None

    monkeypatch.setattr(image_mod, "resolve_credential_instrumented", credencial)
    monkeypatch.setattr(image_mod, "get_http_client", cliente)
    monkeypatch.setattr(image_mod, "record_usage", uso)
    monkeypatch.setattr(image_mod.event_bus, "publish", publicar)
    r = asyncio.run(image_mod.generate_image(image_mod.ImageRequest(prompt="un gato"), user=USUARIO))
    assert (r.url, r.revised_prompt) == ("data:image/png;base64,QQ==", "un gato")
    assert publicados == []


@pytest.fixture
def misiones(tmp_path, monkeypatch):
    monkeypatch.setattr(command_mod, "MISSIONS_DIR", tmp_path)
    eventos = []

    async def publicar(evento):
        eventos.append(evento)

    async def estado(*a, **k):
        return None

    monkeypatch.setattr(command_mod.event_bus, "publish", publicar)
    monkeypatch.setattr(command_mod.engine_state, "set_facet_status", estado)
    return tmp_path, eventos


def _binario(tmp_path, cuerpo):
    ruta = tmp_path / "jax-falso"
    ruta.write_text(f"#!/bin/sh\n{cuerpo}\n")
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
    return ruta


def _correr(tmp_path, eventos, modo="execute"):
    tid = "44444444-dddd-4ddd-8ddd-000000000004"
    mision = tmp_path / f"web-task-{tid}.md"
    mision.write_text("---\nfaceta: hyde\n---\n\nlistar\n")
    (tmp_path / f"web-task-{tid}_owner.json").write_text(json.dumps({"tenant_id": "1", "user_id": "5"}))
    resultado = tmp_path / f"web-task-{tid}_result.md"
    asyncio.run(command_mod._run_command(tid, mision, resultado, "1", "5", modo))
    (evento,) = [e for e in eventos if e.event_type == "command_completed"]
    return tid, evento.payload, resultado


def test_comando_sin_output_es_un_codigo(misiones, monkeypatch):
    tmp, eventos = misiones
    monkeypatch.setattr(command_mod, "JAX_BIN", _binario(tmp, "exit 0"))
    tid, payload, resultado = _correr(tmp, eventos)
    assert payload == {"task_id": tid, "status": "completed", "code": "comando_sin_resultado", "result": ""}
    assert resultado.read_text() == ""
    assert asyncio.run(command_mod.get_command_result(tid, user=USUARIO)) == {
        "status": "completed", "result": "", "code": "comando_sin_resultado"}


def test_comando_que_falla_es_un_codigo_con_motivo(misiones, monkeypatch):
    tmp, eventos = misiones
    monkeypatch.setattr(command_mod, "JAX_BIN", tmp / "no-existe")
    tid, payload, _ = _correr(tmp, eventos)
    assert (payload["status"], payload["code"], payload["result"]) == ("failed", "comando_fallo", "")
    assert "no-existe" in payload["motivo"]
    consulta = asyncio.run(command_mod.get_command_result(tid, user=USUARIO))
    assert (consulta["status"], consulta["code"]) == ("failed", "comando_fallo")


def test_dry_run_es_un_codigo_con_la_mision(misiones):
    tmp, eventos = misiones
    tid, payload, _ = _correr(tmp, eventos, "dry_run")
    assert (payload["status"], payload["code"]) == ("completed", "comando_simulado")
    assert payload["result"] == "---\nfaceta: hyde\n---\n\nlistar\n"


def test_crear_comando_no_publica_command_started(misiones, monkeypatch):
    tmp, eventos = misiones

    async def sin_correr(*a, **k):
        return None

    monkeypatch.setattr(command_mod, "_run_command", sin_correr)
    asyncio.run(command_mod.create_command(command_mod.CommandRequest(command="x"), user=USUARIO))
    assert [e.event_type for e in eventos] == []


def test_task_id_invalido_es_un_codigo():
    e = _error(command_mod.get_command_result("no-es-uuid", user=USUARIO))
    assert (e.status_code, e.detail) == (400, "task_id_invalido")


def test_el_limite_de_pipelines_es_un_codigo_con_el_maximo(monkeypatch):
    async def lleno(_t):
        return False
    monkeypatch.setattr(pipelines_mod.resource_manager, "can_start_pipeline", lleno)
    e = _error(pipelines_mod.create_pipeline(request=None, user=USUARIO))
    assert (e.status_code, e.detail) == (429, {"code": "limite_de_pipelines",
                                               "max": pipelines_mod.MAX_PIPELINES_PER_TENANT})


class _Request:
    async def json(self):
        return {"objective": "x"}


def _cliente_jacobs(monkeypatch, respuesta=None, excepcion=None):
    class _C:
        async def post(self, *a, **k):
            if excepcion:
                raise excepcion
            return respuesta

    async def cliente():
        return _C()

    async def libre(_t):
        return True

    monkeypatch.setattr(pipelines_mod, "get_http_client", cliente)
    monkeypatch.setattr(pipelines_mod.resource_manager, "can_start_pipeline", libre)


def test_un_rechazo_de_jacobs_es_un_codigo(monkeypatch):
    req = httpx.Request("POST", "http://j.test/pipeline")
    _cliente_jacobs(monkeypatch, respuesta=httpx.Response(423, json={"detail": "kill switch"}, request=req))
    e = _error(pipelines_mod.create_pipeline(request=_Request(), user=USUARIO))
    assert (e.status_code, e.detail) == (423, {"code": "jacobs_rechazo", "status": 423, "motivo": "kill switch"})


def test_jacobs_caido_es_un_codigo(monkeypatch):
    _cliente_jacobs(monkeypatch, excepcion=httpx.ConnectError("refused"))
    e = _error(pipelines_mod.create_pipeline(request=_Request(), user=USUARIO))
    assert (e.status_code, e.detail["code"]) == (502, "jacobs_no_responde")


def test_pipeline_id_invalido_es_un_codigo():
    e = _error(pipelines_mod._require_pipeline_owner("no-es-uuid", USUARIO))
    assert (e.status_code, e.detail) == (400, "pipeline_id_invalido")


def test_archivo_demasiado_grande_es_un_codigo():
    grande = UploadFile(file=io.BytesIO(b"x" * (upload_mod.MAX_FILE_SIZE + 1)), filename="a.txt")
    e = _error(upload_mod.upload_file(file=grande, user=USUARIO))
    assert (e.status_code, e.detail) == (413, {"code": "archivo_demasiado_grande",
                                               "max_bytes": upload_mod.MAX_FILE_SIZE})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_mesa_codigos.py -q`
Expected: FAIL en todos salvo `test_la_imagen_no_publica_un_evento`, que falla por la aserción `publicados == []`. `test_ningun_detail_es_texto_libre[api/chat.py]` pasa (Task 8).

- [ ] **Step 3: Implementar**

`schemas.py`, `EventType`: borrar `"command_started",` y `"image_generated",`.

`image.py`:

```python
    try:
        api_key = await resolve_credential_instrumented("openai")
    except CredentialUnavailableError:
        raise HTTPException(status_code=503, detail={"code": "credencial_no_disponible", "provider": "openai"})
```

```python
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail={
            "code": "imagen_error_http", "status": e.response.status_code,
            "motivo": recortar_redactado(e.response.text, 200, (api_key,))})
    except Exception as e:
        raise HTTPException(status_code=502, detail={
            "code": "imagen_error", "motivo": recortar_redactado(str(e), 200, (api_key,))})
```

(el comentario "Fix wave final…" de arriba queda). Final (A-14 + A-30):

```python
    await record_usage(
        user.user_id, user.tenant_id, "thot_image", "openai", "gpt-image-1",
        0, 0, "imagen", cost_usd_override=0.04,
    )
    # gpt-image-1 no incluye revised_prompt
    return ImageResponse(url=url, revised_prompt=req.prompt)
```

Borrar los imports sin uso: `JAXEvent`, `event_bus`.

`command.py`:

```python
def _result_file(task_id: str) -> Path:
    return MISSIONS_DIR / f"web-task-{task_id}_result.md"


def _escribir_fallo(task_id: str, motivo: str) -> None:
    """El fallo de la tarea queda en el archivo de dueño que ya lee GET (A-51),
    escrito atómico: un GET concurrente nunca lee JSON a medias."""
    duenio = _owner_file(task_id)
    datos = json.loads(duenio.read_text())
    datos["fallo"] = {"code": "comando_fallo", "motivo": motivo}
    temporal = duenio.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(datos))
    os.replace(temporal, duenio)
```

(`import os`). En `create_command`, borrar el bloque `start_event` y su `publish` (A-14).

En `get_command_result`: `detail="task_id_invalido"` y los dos 404 `detail="tarea_no_encontrada"`. Después del chequeo de dueño:

```python
    fallo = owner.get("fallo")
    if isinstance(fallo, dict):
        return {"status": "failed", "code": fallo.get("code"), "motivo": fallo.get("motivo", "")}
    result_file = _result_file(task_id)
    if result_file.exists():
        texto = result_file.read_text()
        return {"status": "completed", "result": texto} if texto else {
            "status": "completed", "result": "", "code": "comando_sin_resultado"}
    return {"status": "running"}
```

`_run_command`:

```python
    try:
        codigo = None
        if mode == "dry_run":
            texto = mission_file.read_text()
            result_file.write_text(texto)
            codigo = "comando_simulado"
        else:
            proc = await asyncio.create_subprocess_exec(
                str(JAX_BIN), "--task", str(mission_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(Path.home()),
            )
            await proc.wait()
            # JAX escribe el resultado en result_file internamente.
            # No sobreescribir — solo leer.
            if result_file.exists():
                texto = result_file.read_text()
            else:
                texto = ""
                result_file.write_text("")
            if not texto:
                codigo = "comando_sin_resultado"
        payload = {"task_id": task_id, "status": "completed", "result": texto}
        if codigo:
            payload["code"] = codigo
        await event_bus.publish(JAXEvent(event_type="command_completed", tenant_id=tenant_id,
                                         user_id=user_id, payload=payload))
        await engine_state.set_facet_status("hyde", "idle", tenant_id, user_id)

    except Exception as e:  # fail-soft: tarea de fondo: el fallo se publica como command_completed status='failed' con código y queda en el archivo de dueño; no hay falso éxito
        motivo = recortar_redactado(str(e), 400)
        _escribir_fallo(task_id, motivo)
        await event_bus.publish(JAXEvent(
            event_type="command_completed", tenant_id=tenant_id, user_id=user_id,
            payload={"task_id": task_id, "status": "failed", "code": "comando_fallo",
                     "result": "", "motivo": motivo}))
        await engine_state.set_facet_status("hyde", "idle", tenant_id, user_id)
```

(`from redaccion import recortar_redactado`). Para el payload del dry_run, el test espera `result` = texto de la misión y `code` = `comando_simulado`: el `payload` de arriba lo cumple.

`pipelines.py`: `from jax_engine.resource_manager import MAX_PIPELINES_PER_TENANT, resource_manager`. Reemplazos:
- L73: `raise HTTPException(status_code=400, detail="pipeline_id_invalido")`
- L83: `raise HTTPException(status_code=404, detail="pipeline_no_encontrado")`
- L118-121: `raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail={"code": "limite_de_pipelines", "max": MAX_PIPELINES_PER_TENANT})`
- L134: `raise HTTPException(status_code=r.status_code, detail={"code": "jacobs_rechazo", "status": r.status_code, "motivo": recortar_redactado(str(data.get("detail", "")), 200)})`
- L155, 166, 177, 195, 212: `raise HTTPException(status_code=502, detail={"code": "jacobs_no_responde", "motivo": recortar_redactado(str(e), 200)})`
(`from redaccion import recortar_redactado`.) Antes de editar, verificar con `grep -n "detail" backend/api/pipelines.py` que no queda otro `detail`. Si aparece uno más, se trata igual y se lista en el reporte.

`upload.py`:
- L34: `raise HTTPException(status_code=413, detail={"code": "archivo_demasiado_grande", "max_bytes": MAX_FILE_SIZE})`
- L70: `raise HTTPException(status_code=422, detail={"code": "pdf_ilegible", "motivo": recortar_redactado(str(e), 200)})` (import). El frente D reescribe este archivo (Discrepancia 5).

`test_fix_wave_final.py:150-176`, en los tres tests de imagen:
- `test_el_502_de_imagen_no_deja_un_pedazo_de_key_AIza_que_cruza_el_corte`: `detail = …detail`; `assert (detail["code"], detail["status"]) == ("imagen_error_http", 400)`; `assert "AIza" not in detail["motivo"]`; `assert len(detail["motivo"]) <= 200`.
- `…tapa_la_credencial_conocida…`: `motivo = …detail["motivo"]`; mismas dos aserciones sobre `motivo`.
- `test_el_error_generico_de_imagen_redacta_antes_de_recortar`: `assert detail["code"] == "imagen_error"`; `assert "sk-FAKE" not in detail["motivo"]`; `assert len(detail["motivo"]) <= 200`.

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_mesa_codigos.py tests/test_fix_wave_final.py tests/test_command_ownership.py tests/test_command_path_traversal.py tests/test_image_http_pooling.py tests/test_pipelines_http_pooling.py tests/test_owner_cleanup.py -q`
Run (con DB): `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_pipeline_ownership.py tests/test_pipelines_identity_injection.py tests/test_t6_seguimiento.py tests/test_pipeline_user_id.py -q`
Expected: PASS. Si un test de propiedad de pipeline compara `detail == "Pipeline no encontrado"`, pasa a `"pipeline_no_encontrado"` en este mismo commit (grep previo: ninguno en `26c9cd5`).

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/api/image.py backend/api/command.py backend/api/pipelines.py backend/api/upload.py backend/jax_engine/schemas.py backend/tests/test_fix_wave_final.py backend/tests/test_mesa_codigos.py
$G commit -m "fix(mesa): image, command, pipelines y upload responden codigos estables; sin eventos sin consumidor (A-14/30/51/53)"
```

---

### Task 10: Login bloqueado con código y `retry_after_seconds` (A-50)

**Files:**
- Modify: `backend/api/auth.py:114-119` y un helper nuevo arriba de `login`
- Modify: `backend/tests/test_login_sin_enumeracion.py:100-122`
- Modify: `frontend/src/pages/Login.jsx:45-49`, `frontend/src/pages/Login.test.jsx`
- Create: `backend/tests/test_login_bloqueo.py`

**Interfaces:**
- Produces: `api.auth._cuenta_bloqueada(locked_until: datetime, ahora: datetime) -> HTTPException` → 423, `detail={"code": "cuenta_bloqueada", "retry_after_seconds": int}`, cabecera `Retry-After`.

- [ ] **Step 1: Tests rojos**

```python
# backend/tests/test_login_bloqueo.py
"""Frente A, A-50 (2026-09-16): Login.jsx sacaba los minutos con una regex del
texto español "Cuenta bloqueada. Intenta de nuevo en N minuto(s)." Ahora el 423
lleva codigo estable, segundos y Retry-After (como el 429). Puro."""
from datetime import datetime, timedelta

from api.auth import _cuenta_bloqueada

AHORA = datetime(2026, 9, 16, 12, 0, 0)


def test_el_423_trae_codigo_segundos_y_retry_after():
    e = _cuenta_bloqueada(AHORA + timedelta(minutes=10), AHORA)
    assert (e.status_code, e.detail) == (423, {"code": "cuenta_bloqueada", "retry_after_seconds": 600})
    assert e.headers == {"Retry-After": "600"}


def test_una_fraccion_de_segundo_redondea_hacia_arriba_y_nunca_es_cero():
    assert _cuenta_bloqueada(AHORA + timedelta(milliseconds=200), AHORA).detail["retry_after_seconds"] == 1
    assert _cuenta_bloqueada(AHORA, AHORA).detail["retry_after_seconds"] == 1
```

`test_login_sin_enumeracion.py`, en `test_cuenta_bloqueada_sin_contrasena_correcta_no_se_revela`:

```python
        status, detail = _login(client, email, CLAVE)
        assert status == 423 and detail["code"] == "cuenta_bloqueada", (status, detail)
        assert 0 < detail["retry_after_seconds"] <= 600, detail
```

Frontend, en `Login.test.jsx`, un `describe` nuevo:

```jsx
describe('Login -- cuenta bloqueada (A-50)', () => {
  it('un 423 con código usa los segundos del backend, no una regex del texto', async () => {
    loginMock.mockRejectedValue({ response: { status: 423, headers: {}, data: { detail: { code: 'cuenta_bloqueada', retry_after_seconds: 540 } } } })
    renderLogin()
    enviar()
    await waitFor(() => expect(screen.getByText('Cuenta bloqueada. Intenta de nuevo en 9 minuto(s).')).toBeInTheDocument())
  })

  it('en inglés también, sin texto del backend', async () => {
    localStorage.setItem('jax_lang', 'en')
    loginMock.mockRejectedValue({ response: { status: 423, headers: {}, data: { detail: { code: 'cuenta_bloqueada', retry_after_seconds: 61 } } } })
    renderLogin()
    fireEvent.change(screen.getByPlaceholderText(en.emailPlaceholder), { target: { value: 'a@b.c' } })
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: en.loginButton }))
    await waitFor(() => expect(screen.getByText('Account locked. Try again in 2 minute(s).')).toBeInTheDocument())
  })

  it('sin segundos, el mensaje genérico de bloqueo', async () => {
    loginMock.mockRejectedValue({ response: { status: 423, headers: {}, data: { detail: { code: 'cuenta_bloqueada' } } } })
    renderLogin()
    enviar()
    await waitFor(() => expect(screen.getByText('Cuenta bloqueada. Revisa tu correo.')).toBeInTheDocument())
  })

  it('Login.jsx no interpreta texto del backend', () => {
    const fuente = readFileSync(new URL('./Login.jsx', import.meta.url), 'utf8')
    expect(fuente).not.toMatch(/minuto/)
  })
})
```

(agregar `import { readFileSync } from 'node:fs'`, e `import en from '../i18n/en.js'` si el archivo no lo importa ya.)

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_login_bloqueo.py -q` → FAIL (ImportError).
Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/pages/Login.test.jsx` → FAIL en los 4 nuevos (el primero con `detail.match is not a function`).

- [ ] **Step 3: Implementar**

`api/auth.py` (import `math`):

```python
def _cuenta_bloqueada(locked_until, ahora) -> HTTPException:
    """423 con código y segundos (A-50, 2026-09-16), igual que el 429 del
    limitador: el frontend no interpreta texto."""
    segundos = max(1, math.ceil((locked_until - ahora).total_seconds()))
    return HTTPException(
        status_code=status.HTTP_423_LOCKED,
        detail={"code": "cuenta_bloqueada", "retry_after_seconds": segundos},
        headers={"Retry-After": str(segundos)},
    )
```

y en `login`: `if bloqueada: raise _cuenta_bloqueada(locked_until, now)`.

`Login.jsx`:

```jsx
      if (status === 423) {
        // A-50: segundos del backend (detail.retry_after_seconds), nunca texto.
        const segundos = err.response?.data?.detail?.retry_after_seconds
        setError(Number.isFinite(segundos) ? t.accountLockedMinutes(Math.ceil(segundos / 60)) : t.accountLocked)
      } else if (status === 429) {
```

- [ ] **Step 4: Verde**

Los dos comandos del Step 2, y con DB: `cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_login_sin_enumeracion.py tests/test_login_rate_limit.py tests/test_auth.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add backend/api/auth.py backend/tests/test_login_bloqueo.py backend/tests/test_login_sin_enumeracion.py frontend/src/pages/Login.jsx frontend/src/pages/Login.test.jsx
$G commit -m "fix(login): el bloqueo responde codigo y segundos; el frontend no parsea texto (A-50)"
```

---

### Task 11: Frontend de la Mesa — traducir códigos, avisos y comandos (A-27, A-43, A-44, A-51 FE, A-53 FE)

**Files:**
- Modify: `frontend/src/api/errores.js`
- Modify: `frontend/src/components/BottomBar/BottomBar.jsx` (L67-75, L140-244)
- Modify: `frontend/src/store/useJaxStore.js` (L313-349, L455-505)
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`
- Create: `frontend/src/api/errores.test.js`, `frontend/src/components/BottomBar/BottomBar.errores.test.jsx`, `frontend/src/store/useJaxStore.comandos.test.js`

**Interfaces:**
- Consumes: los códigos y el payload de comandos (Tasks 8-9).
- Produces: `textoDeErrorDeMesa(t, err, generico)`, `textoDeAviso(t, aviso)`, `contenidoDeComando(t, datos)`; claves i18n `errorPrefix`, `respuestaDelServicio`, `erroresMesa`, `avisosChat`, `hostingDeProveedor`, `hostingGenerico`, `avisoDesconocido`, `commandFailed`, `commandDryRun`.

- [ ] **Step 1: Tests rojos**

```js
// frontend/src/api/errores.test.js
import { describe, it, expect } from 'vitest'
import { textoDeErrorDeMesa, textoDeAviso } from './errores'
import es from '../i18n/es.js'
import en from '../i18n/en.js'

const err = (detail) => ({ response: { data: { detail } } })

describe('textoDeErrorDeMesa (A-51)', () => {
  it('traduce un código con datos y agrega lo que respondió el servicio', () => {
    const texto = textoDeErrorDeMesa(es, err({ code: 'proveedor_error_http', facet: 'thot', status: 400, motivo: 'bad request' }), es.errorFacet)
    expect(texto).toBe(`${es.erroresMesa.proveedor_error_http({ facet: 'thot', status: 400 })} ${es.respuestaDelServicio('bad request')}`)
    expect(texto).not.toContain('proveedor_error_http')
  })

  it('un código como string también se traduce', () => {
    expect(textoDeErrorDeMesa(en, err('task_id_invalido'), en.errorTask)).toBe(en.erroresMesa.task_id_invalido({}))
  })

  it('el límite de pipelines usa el máximo del backend', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'limite_de_pipelines', max: 4 }), es.errorPipeline)).toContain('4')
  })

  it('un código desconocido o un texto libre caen al genérico, nunca crudo', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'otro_codigo' }), es.errorFacet)).toBe(es.errorFacet)
    expect(textoDeErrorDeMesa(es, err('Límite de 3 pipelines concurrentes alcanzado'), es.errorPipeline)).toBe(es.errorPipeline)
    expect(textoDeErrorDeMesa(es, {}, es.errorImagen)).toBe(es.errorImagen)
  })
})

describe('textoDeAviso (A-53)', () => {
  it('identidad del modelo arma el hosting por proveedor, en cada idioma', () => {
    const aviso = { code: 'identidad_del_modelo', params: { facet: 'jax_local', model: 'qwen', provider: 'ollama' } }
    expect(textoDeAviso(es, aviso)).toBe(es.avisosChat.identidad_del_modelo(aviso.params, es.hostingDeProveedor.ollama))
    expect(textoDeAviso(en, aviso)).toContain('qwen')
    expect(textoDeAviso(en, aviso)).not.toBe(textoDeAviso(es, aviso))
  })

  it('un proveedor sin texto usa el hosting genérico; un código desconocido, el aviso genérico', () => {
    expect(textoDeAviso(es, { code: 'identidad_del_modelo', params: { model: 'm', provider: 'nuevo' } })).toContain(es.hostingGenerico)
    expect(textoDeAviso(es, { code: 'algo_nuevo', params: {} })).toBe(es.avisoDesconocido)
  })
})
```

```jsx
// frontend/src/components/BottomBar/BottomBar.errores.test.jsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { readFileSync } from 'node:fs'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))

import api from '../../api/client'
import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()

function enviarChat(texto) {
  render(<I18nProvider><BottomBar /></I18nProvider>)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: texto } })
  fireEvent.click(screen.getByRole('button', { name: es.send }))
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, activeFacet: 'thot', messages: [] }, true)
  api.post.mockReset()
})

describe('BottomBar -- errores y avisos con código', () => {
  it('un 502 con código se traduce; el código no aparece crudo', async () => {
    api.post.mockRejectedValue({ response: { status: 502, data: { detail: { code: 'faceta_error', facet: 'thot', motivo: 'timeout' } } } })
    enviarChat('hola')
    await waitFor(() => expect(useJaxStore.getState().messages).toHaveLength(2))
    const { content } = useJaxStore.getState().messages[1]
    expect(content).toBe(`**${es.errorPrefix}:** ${es.erroresMesa.faceta_error({ facet: 'thot' })} ${es.respuestaDelServicio('timeout')}`)
  })

  it('una respuesta enlatada muestra el texto del aviso, no la marca', async () => {
    api.post.mockResolvedValue({ data: {
      facet: 'thot', response: '[faceta_sin_binding facet=thot]', timestamp: 't',
      aviso: { code: 'faceta_sin_binding', params: { facet: 'thot' } },
    } })
    enviarChat('hola')
    await waitFor(() => expect(useJaxStore.getState().messages).toHaveLength(2))
    expect(useJaxStore.getState().messages[1].content).toBe(es.avisosChat.faceta_sin_binding({ facet: 'thot' }))
  })

  it('sin prefijos literales ni mapa de lambdas', () => {
    const fuente = readFileSync(new URL('./BottomBar.jsx', import.meta.url), 'utf8')
    expect(fuente).not.toContain('**Error:**')
    expect(fuente).not.toContain('PLACEHOLDERS')
  })
})
```

(El literal `[Archivo adjunto: …]` de `BottomBar.jsx:137` es del bloque de adjuntos: lo cubre el frente D.)

```js
// frontend/src/store/useJaxStore.comandos.test.js
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore, contenidoDeComando } from './useJaxStore'
import es from '../i18n/es.js'

const INICIAL = useJaxStore.getState()

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1 } }, true)
  vi.clearAllMocks()
})

describe('contenidoDeComando (A-53)', () => {
  it('cada código tiene su texto', () => {
    expect(contenidoDeComando(es, { code: 'comando_fallo', motivo: 'sin binario' })).toBe(es.commandFailed('sin binario'))
    expect(contenidoDeComando(es, { code: 'comando_sin_resultado', result: '' })).toBe(es.commandNoResult)
    expect(contenidoDeComando(es, { code: 'comando_simulado', result: 'mision' })).toBe(es.commandDryRun('mision'))
    expect(contenidoDeComando(es, { result: 'listo' })).toBe('listo')
  })
})

describe('command_completed y checkPendingTasks con códigos (A-44)', () => {
  it('un fallo por WS queda failed con el texto traducido y sale de pendientes', () => {
    useJaxStore.setState({ messages: [{ id: 'cmd-t1', facet: 'hyde', content: '…', status: 'running', timestamp: 't' }] })
    localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: ['t1'] }))
    useJaxStore.getState().handleEvent({ event_type: 'command_completed',
      payload: { task_id: 't1', status: 'failed', code: 'comando_fallo', result: '', motivo: 'x' } })
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['failed', es.commandFailed('x')])
    expect(JSON.parse(localStorage.getItem('jax_pending_cmds')).ids).toEqual([])
  })

  it('un completado SIN resultado deja de consultarse (antes quedaba running para siempre)', async () => {
    useJaxStore.setState({ messages: [{ id: 'cmd-t2', facet: 'hyde', content: '…', status: 'running', timestamp: 't' }] })
    localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: ['t2'] }))
    api.get.mockResolvedValue({ data: { status: 'completed', result: '', code: 'comando_sin_resultado' } })
    await useJaxStore.getState().checkPendingTasks()
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['completed', es.commandNoResult])
    expect(JSON.parse(localStorage.getItem('jax_pending_cmds')).ids).toEqual([])
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/api/errores.test.js src/components/BottomBar/BottomBar.errores.test.jsx src/store/useJaxStore.comandos.test.js`
Expected: FAIL (funciones y claves inexistentes; el segundo test de checkPendingTasks queda `running`).

- [ ] **Step 3: Implementar**

`api/errores.js` (agregar):

```js
// Errores de la Mesa (frente A, A-51, 2026-09-16): chat, comando, imagen,
// pipelines y subida responden un código estable. Nunca se muestra el código
// crudo ni un texto del backend; `motivo` (lo que dijo un servicio externo,
// ya redactado por el backend) se agrega como dato, igual que smtpServerSaid.
export function textoDeErrorDeMesa(t, err, generico) {
  const code = codigoDe(err)
  const traducir = code && t.erroresMesa[code]
  if (!traducir) return generico
  const detail = err?.response?.data?.detail
  const datos = detail && typeof detail === 'object' ? detail : {}
  const base = traducir(datos)
  return datos.motivo ? `${base} ${t.respuestaDelServicio(datos.motivo)}` : base
}

// Respuestas enlatadas del chat (A-53): `aviso` con código y params.
export function textoDeAviso(t, aviso) {
  const traducir = t.avisosChat[aviso?.code]
  if (!traducir) return t.avisoDesconocido
  const params = aviso.params || {}
  if (aviso.code === 'identidad_del_modelo') {
    return traducir(params, t.hostingDeProveedor[params.provider] || t.hostingGenerico)
  }
  return traducir(params)
}
```

`es.js`, junto a `errorFacet`:

```js
  // Frente A (2026-09-16): errores y avisos de la Mesa con código estable.
  errorPrefix: 'Error',
  respuestaDelServicio: (texto) => `Respuesta del servicio: ${texto}`,
  erroresMesa: {
    faceta_desconocida: (d) => `La faceta «${d.facet}» no existe.`,
    proveedor_error_http: (d) => `El proveedor de ${d.facet} respondió con error ${d.status}.`,
    faceta_error: (d) => `${d.facet} no pudo responder.`,
    credencial_no_disponible: (d) => `No hay una credencial válida configurada para ${d.provider}.`,
    imagen_error_http: (d) => `El servicio de imágenes respondió con error ${d.status}.`,
    imagen_error: () => 'No se pudo generar la imagen.',
    task_id_invalido: () => 'El identificador de la tarea no es válido.',
    tarea_no_encontrada: () => 'La tarea no existe.',
    limite_de_pipelines: (d) => `Ya hay ${d.max} pipelines en curso: espera a que termine uno.`,
    pipeline_id_invalido: () => 'El identificador del pipeline no es válido.',
    pipeline_no_encontrado: () => 'El pipeline no existe.',
    jacobs_rechazo: (d) => `Jacobs rechazó el pipeline (${d.status}).`,
    jacobs_no_responde: () => 'Jacobs no respondió.',
    archivo_demasiado_grande: (d) => `El archivo supera el máximo de ${Math.round(d.max_bytes / 1048576)} MB.`,
    pdf_ilegible: () => 'No se pudo leer el PDF.',
  },
  avisosChat: {
    faceta_sin_binding: (p) => `⚠️ ${p.facet} no está disponible: sin binding activo configurado.`,
    faceta_no_autorizada: (p) => `⚠️ ${p.facet} no está disponible: acceso no autorizado.`,
    transporte_no_soportado: (p) => `⚠️ ${p.facet} no está disponible: transporte '${p.transport}' no soportado en la Mesa web.`,
    identidad_del_modelo: (p, hosting) => `Corro con '${p.model}' ${hosting} — dato leído en vivo del selector de modelos activo, no de memoria.`,
    hyde_usa_modo_comando: () => 'Hyde opera en modo tarea autónoma — usa el modo Comando para ejecutar tareas técnicas.',
  },
  hostingDeProveedor: {
    ollama: 'vía Ollama local en hall9000',
    deepseek: 'vía la API de DeepSeek',
    gemini: 'vía la API de Gemini (Google)',
    openai: 'vía la API de OpenAI',
    moonshot: 'vía la API de Moonshot',
    zhipu: 'vía la API de Zhipu (GLM)',
  },
  hostingGenerico: 'vía la API configurada para esta faceta',
  avisoDesconocido: 'La faceta respondió con un aviso que esta versión no conoce.',
  commandFailed: (motivo) => `Error ejecutando la tarea: ${motivo}`,
  commandDryRun: (mision) => `[Simulación] Tarea registrada:\n\n${mision}`,
```

`en.js`, con las mismas claves:

```js
  // Front A (2026-09-16): Mesa errors and notices with stable codes.
  errorPrefix: 'Error',
  respuestaDelServicio: (texto) => `Service response: ${texto}`,
  erroresMesa: {
    faceta_desconocida: (d) => `The facet "${d.facet}" does not exist.`,
    proveedor_error_http: (d) => `The provider for ${d.facet} returned error ${d.status}.`,
    faceta_error: (d) => `${d.facet} could not respond.`,
    credencial_no_disponible: (d) => `There is no valid credential configured for ${d.provider}.`,
    imagen_error_http: (d) => `The image service returned error ${d.status}.`,
    imagen_error: () => 'The image could not be generated.',
    task_id_invalido: () => 'The task id is not valid.',
    tarea_no_encontrada: () => 'The task does not exist.',
    limite_de_pipelines: (d) => `${d.max} pipelines are already running: wait for one to finish.`,
    pipeline_id_invalido: () => 'The pipeline id is not valid.',
    pipeline_no_encontrado: () => 'The pipeline does not exist.',
    jacobs_rechazo: (d) => `Jacobs rejected the pipeline (${d.status}).`,
    jacobs_no_responde: () => 'Jacobs did not respond.',
    archivo_demasiado_grande: (d) => `The file exceeds the ${Math.round(d.max_bytes / 1048576)} MB maximum.`,
    pdf_ilegible: () => 'The PDF could not be read.',
  },
  avisosChat: {
    faceta_sin_binding: (p) => `⚠️ ${p.facet} is not available: no active binding configured.`,
    faceta_no_autorizada: (p) => `⚠️ ${p.facet} is not available: access not authorized.`,
    transporte_no_soportado: (p) => `⚠️ ${p.facet} is not available: transport '${p.transport}' is not supported in the web Mesa.`,
    identidad_del_modelo: (p, hosting) => `I run on '${p.model}' ${hosting} — read live from the active model selector, not from memory.`,
    hyde_usa_modo_comando: () => 'Hyde works as an autonomous task runner — use Command mode for technical tasks.',
  },
  hostingDeProveedor: {
    ollama: 'via local Ollama on hall9000',
    deepseek: 'via the DeepSeek API',
    gemini: 'via the Gemini API (Google)',
    openai: 'via the OpenAI API',
    moonshot: 'via the Moonshot API',
    zhipu: 'via the Zhipu API (GLM)',
  },
  hostingGenerico: 'via the API configured for this facet',
  avisoDesconocido: 'The facet replied with a notice this version does not know.',
  commandFailed: (motivo) => `Error running the task: ${motivo}`,
  commandDryRun: (mision) => `[Dry run] Task registered:\n\n${mision}`,
```

**`BottomBar.jsx`:** import `import { textoDeErrorDeMesa, textoDeAviso } from '../../api/errores'`.

A-27:

```jsx
  const activeFacetObj = FACETS.find((f) => f.id === activeFacet) || FACETS[0]
  const placeholder = mode === 'chat' ? t.placeholderChat(activeFacetObj.label)
    : mode === 'comando' ? t.placeholderComando()
    : mode === 'pipeline' ? t.placeholderPipeline()
    : mode === 'imagen' ? t.placeholderImagen()
    : ''
```

A-43, dentro del componente:

```jsx
  // A-43 (2026-09-16): los tres errores de la Mesa se arman igual. El prefijo
  // es una clave de i18n; el detalle ya viene traducido (textoDeErrorDeMesa).
  function agregarError(facet, id, prefijoClave, detalle) {
    addMessage({ id, facet, content: `**${t[prefijoClave]}:** ${detalle}`, timestamp: new Date().toISOString() })
  }
```

El chat, al responder: `content: data.aviso ? textoDeAviso(t, data.aviso) : data.response,`. El catch del chat: `agregarError(activeFacet, Date.now().toString() + '_err', 'errorPrefix', textoDeErrorDeMesa(t, err, t.errorFacet))`. El comando: `updateMessage(msgId, { content: \`**${t.errorPrefix}:** ${textoDeErrorDeMesa(t, err, t.errorTask)}\`, status: 'completed' })`. La imagen: `agregarError('dalle', Date.now().toString() + '_img_err', 'errorPrefix', textoDeErrorDeMesa(t, err, t.errorImagen))`. El pipeline: `agregarError('jacobs', \`pipeline-err-${Date.now()}\`, 'errorPipelinePrefix', textoDeErrorDeMesa(t, err, t.errorPipeline))`. El bloque de adjuntos (L82-84 y L131-139) NO se toca: es del frente D.

**`useJaxStore.js`:** agregar `import { textoDeErrorDeMesa } from '../api/errores'` solo si se usa (acá no hace falta). Exportar, arriba del store:

```js
// A-53 (2026-09-16): el resultado de un comando llega con código cuando no hay
// texto que mostrar (sin output, fallo o simulación). Lo usan el evento de WS
// y la consulta de pendientes: un solo lugar decide el texto.
export function contenidoDeComando(t, datos) {
  if (datos?.code === 'comando_fallo') return t.commandFailed(datos.motivo || '')
  if (datos?.code === 'comando_simulado') return t.commandDryRun(datos.result || '')
  return datos?.result || t.commandNoResult
}
```

Dentro del `create`, junto a `_savePendingIds`:

```js
  // A-44 (2026-09-16): "resolver un comando" (contenido, estado, sacarlo de
  // pendientes) en un solo lugar. Quien llama conserva sus chequeos (sesión
  // vigente, mensaje que todavía existe) ANTES de llamarlo.
  const _resolverComando = (msgId, taskId, content, status) => {
    set((s) => ({ messages: s.messages.map((m) => (m.id === msgId ? { ...m, content, status } : m)) }))
    _savePendingIds(_loadPendingIds().filter((id) => id !== taskId))
  }
```

`command_completed`:

```js
    if (event_type === 'command_completed') {
      const { task_id, status } = payload
      const msgId = `cmd-${task_id}`
      const msgStatus = status === 'failed' ? 'failed' : 'completed'
      const sessionEpoch = get()._sessionEpoch

      const applyResult = (content) => {
        if (!isSameSession(sessionEpoch)) return
        // El placeholder pudo ser evictado por _capMessages … (comentario existente)
        if (!get().messages.some((m) => m.id === msgId)) return
        _resolverComando(msgId, task_id, content, msgStatus)
      }

      if (payload.result || payload.code) {
        applyResult(contenidoDeComando(_t(), payload))
      } else if (task_id) {
        // resultado completo en archivo — pedir al backend. … (comentario existente)
        api.get(`/command/${task_id}`).then(
          ({ data }) => applyResult(contenidoDeComando(_t(), data)),
          () => applyResult(_t().commandNoResult)
        ).catch((err) => console.error('command result render failed', err))
      } else {
        applyResult(_t().commandNoResult)
      }
    }
```

`checkPendingTasks`, dentro del `try`:

```js
        if (data.status === 'completed' || data.status === 'failed') {
          _resolverComando(msg.id, taskId, contenidoDeComando(_t(), data), data.status)
        } else {
          stillRunning++
        }
```

y en el `catch` (404/400): `_resolverComando(msg.id, taskId, _t().commandNoResult, 'completed')`.

(`_t` sigue existiendo hasta la Task 12.)

- [ ] **Step 4: Verde y suite del frontend**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run`
Expected: 0 fallidos. Si `useJaxStore.commandPolling.test.js` asumía `data.result` no vacío como única salida, se ajusta en este commit.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add frontend/src/api/errores.js frontend/src/api/errores.test.js frontend/src/components/BottomBar/BottomBar.jsx frontend/src/components/BottomBar/BottomBar.errores.test.jsx frontend/src/store/useJaxStore.js frontend/src/store/useJaxStore.comandos.test.js frontend/src/i18n/es.js frontend/src/i18n/en.js
$G commit -m "feat(mesa): la Mesa traduce codigos y avisos del backend; resolver comando en un lugar (A-27/43/44/51/53)"
```

---

### Task 12: Frontend — dependencia, WebSocket, i18n y store (A-01, A-09, A-10, A-11, A-17 i18n, A-29, A-45)

**Files:**
- Modify: `frontend/package.json:15`, `frontend/package-lock.json` (regenerado)
- Modify: `frontend/src/api/websocket.js:46-48,78`, `frontend/src/store/useWebSocket.js:48`
- Modify: `frontend/src/pages/Admin.jsx:2,14`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js` (25 claves), `frontend/src/i18n/index.jsx`
- Modify: `frontend/src/store/useJaxStore.js` (L1-13, L593-626), `frontend/src/components/HalEye/HalEye.jsx:21-29`
- Modify: `frontend/src/store/useJaxStore.eyeState.test.js`, `frontend/src/store/useJaxStore.facetTokens.test.js:42,67`, `frontend/src/api/websocket.test.js`
- Create: `frontend/src/i18n/paridad.test.js`, `frontend/src/limpieza.test.js`

**Interfaces:**
- Produces: `diccionarioActivo()` en `i18n/index.jsx`; `getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage, etiquetas)`.

- [ ] **Step 1: Tests rojos**

```js
// frontend/src/i18n/paridad.test.js
import { describe, it, expect } from 'vitest'
import es from './es.js'
import en from './en.js'

// Paridad es/en (frente A, 2026-09-16): hasta hoy cada test miraba "su" clave;
// ninguno impedía que un idioma ganara o perdiera una. Incluye los objetos
// anidados (erroresMesa, avisosChat, smtpErrors...). Medido en 26c9cd5: 470/470.
function claves(obj, prefijo = '') {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === 'object' && !Array.isArray(v) ? claves(v, `${prefijo}${k}.`) : [`${prefijo}${k}`])
}

describe('i18n', () => {
  it('es y en tienen exactamente las mismas claves', () => {
    expect(claves(en).sort()).toEqual(claves(es).sort())
  })

  it('cada clave es del mismo tipo en los dos idiomas', () => {
    const tipo = (d, ruta) => typeof ruta.split('.').reduce((o, k) => o[k], d)
    for (const ruta of claves(es)) expect([ruta, tipo(en, ruta)]).toEqual([ruta, tipo(es, ruta)])
  })
})
```

```jsx
// frontend/src/limpieza.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { renderHook } from '@testing-library/react'
import es from './i18n/es.js'
import en from './i18n/en.js'
import { diccionarioActivo } from './i18n/index.jsx'
import { createWebSocket } from './api/websocket'
import { useWebSocket } from './store/useWebSocket'
import { getEyeState } from './store/useJaxStore'

const fuente = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8')

// Las 25 claves sin lector (anexo A, ficha 16; verificadas una por una).
const MUERTAS = ['adminCostsPeriod', 'adminEventsTitle', 'adminKeyAddModel', 'adminKeyFacet', 'adminKeyModel',
  'adminKeyModelAdd', 'adminKeyModelDelete', 'adminKeyModelDeleteActive', 'adminKeyModelDeleteConfirmButton',
  'adminKeyModelDeleteConfirmPlaceholder', 'adminKeyModelDeleteConfirmTitle', 'adminKeyModelDeleteConfirmWrong',
  'adminKeyModelDeleteConfirmSum', 'adminKeyModelName', 'adminKeyModelProvider', 'adminNav', 'adminProposalsCurrent',
  'adminSettingsWsNotif', 'adminUserChangeRole', 'adminUserResetPwd', 'attachedFile', 'attachFile', 'attachTooLarge',
  'attachTypes', 'statApiKeys']

const ETIQUETAS = { reposo: 'r', killSwitch: 'k', dalle: 'd', lasManosDown: 'l', gate: 'g', jacobs: 'j' }

beforeEach(() => localStorage.clear())

describe('limpieza del frontend (frente A)', () => {
  it('A-01: @heroicons/react no es dependencia', () => {
    expect(JSON.parse(fuente('../package.json')).dependencies).not.toHaveProperty('@heroicons/react')
  })

  it('A-09: createWebSocket solo devuelve close y no instala un onerror vacío', () => {
    class FakeWS { constructor() { FakeWS.ultimo = this } close() {} }
    vi.stubGlobal('WebSocket', FakeWS)
    const ws = createWebSocket('5', 'tok', () => {}, () => {})
    expect(Object.keys(ws)).toEqual(['close'])
    expect(FakeWS.ultimo.onerror).toBeUndefined()
    ws.close()
    vi.unstubAllGlobals()
  })

  it('A-09: useWebSocket no devuelve nada', () => {
    const { result } = renderHook(() => useWebSocket())
    expect(result.current).toBeUndefined()
  })

  it('A-10: Admin no pide i18n que no usa', () => {
    expect(fuente('./pages/Admin.jsx')).not.toContain('useI18n')
  })

  it('A-11/A-17: las claves sin lector no existen en ningún idioma', () => {
    for (const clave of MUERTAS) {
      expect(es).not.toHaveProperty(clave)
      expect(en).not.toHaveProperty(clave)
    }
  })

  it('A-29: el store usa el diccionario activo del proveedor de i18n', () => {
    localStorage.setItem('jax_lang', 'en')
    expect(diccionarioActivo()).toBe(en)
    localStorage.setItem('jax_lang', 'xx')
    expect(diccionarioActivo()).toBe(es)
    expect(fuente('./store/useJaxStore.js')).not.toMatch(/function _t\(/)
  })

  it('A-45: getEyeState exige todas las etiquetas', () => {
    expect(() => getEyeState({}, {}, true, false, false)).toThrow(/etiqueta/)
    const { killSwitch, ...sinUna } = ETIQUETAS
    expect(() => getEyeState({}, {}, true, true, false, sinUna)).toThrow(/killSwitch/)
    expect(getEyeState({}, {}, true, true, false, ETIQUETAS).label).toBe('k')
    expect(fuente('./store/useJaxStore.js')).not.toContain("'KILL SWITCH'")
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/limpieza.test.js src/i18n/paridad.test.js`
Expected: FAIL en todo `limpieza.test.js`; `paridad.test.js` PASA (470/470 medido). Control de la paridad: borrar a mano `serviceNotConfigured` de `en.js`, verlo rojo y restaurar. Anotarlo.

- [ ] **Step 3: Implementar**

A-01: `export PATH=/home/fruiz/.nvm/versions/node/v24.16.0/bin:$PATH && npm --prefix /home/fruiz/worktrees/jax-platform-frente-a/frontend uninstall @heroicons/react`. Verificar después: `grep -c heroicons /home/fruiz/worktrees/jax-platform-frente-a/frontend/package-lock.json` → 0.

A-09, `websocket.js`: borrar el bloque `ws.onerror = () => { … }` (L46-48) y la propiedad `send` del objeto que se devuelve. `useWebSocket.js`: borrar `return wsRef.current`.

A-10, `Admin.jsx`: borrar el import de `useI18n` y `const { t } = useI18n()`.

A-11/A-17: borrar las 25 claves (una línea cada una) de `es.js` y de `en.js`. Si alguna tiene un comentario pegado que solo la explica a ella, el comentario se va con la clave.

A-29, `i18n/index.jsx`:

```jsx
// Idioma guardado y su diccionario (A-29, 2026-09-16): una sola regla para el
// proveedor y para el store, que no es un componente y no puede usar el hook.
export function idiomaGuardado() {
  return LANGS[localStorage.getItem('jax_lang')] ? localStorage.getItem('jax_lang') : 'es'
}

export function diccionarioActivo() {
  return LANGS[idiomaGuardado()]
}
```

y en `I18nProvider`: `useState(() => idiomaGuardado())`. `useJaxStore.js`: borrar los imports de `es`/`en`, la función `_t` y su comentario; agregar `import { diccionarioActivo } from '../i18n/index.jsx'` y reemplazar cada `_t()` por `diccionarioActivo()` (grep: 8 usos, contando los de la Task 11).

A-45, `useJaxStore.js`:

```js
// A-45 (2026-09-16): las etiquetas del ojo son TODAS requeridas y salen de i18n
// (HalEye las pasa desde t.eye*). Antes caían a un texto fijo en el código.
const ETIQUETAS_DEL_OJO = ['reposo', 'killSwitch', 'dalle', 'lasManosDown', 'gate', 'jacobs']

export function getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage, etiquetas) {
  for (const clave of ETIQUETAS_DEL_OJO) {
    if (typeof etiquetas?.[clave] !== 'string') throw new Error(`getEyeState: falta la etiqueta ${clave}`)
  }
  if (killSwitchActive) return { token: 'peligro', animation: 'none', label: etiquetas.killSwitch }
  if (generatingImage) return { token: 'faceta-imagen', animation: 'pulse-fast', label: etiquetas.dalle }
  // Thinking toma prioridad sobre todo — incluso si lasManos está abajo
  const thinking = Object.entries(facets).find(([, f]) => f.status === 'thinking')
  if (thinking) {
    const [name, f] = thinking
    return { token: f.token, animation: name === 'hyde' ? 'pulse-fast' : 'pulse-slow', label: name }
  }
  if (!lasManos) return { token: 'texto-tenue', animation: 'none', label: etiquetas.lasManosDown }
  if (Object.values(activePipelines).some((p) => p.status === 'waiting_gate')) {
    return { token: 'aviso', animation: 'blink', label: etiquetas.gate }
  }
  if (Object.values(activePipelines).some((p) => p.status === 'running')) {
    return { token: 'faceta-jacobs', animation: 'pulse-slow', label: etiquetas.jacobs }
  }
  return { ...EYE_ESTADO_REPOSO, label: etiquetas.reposo }
}
```

(el comentario M5 de arriba se reemplaza por este). `HalEye.jsx`:

```jsx
  const eye = reposo
    ? { ...EYE_ESTADO_REPOSO, label: t.eyeIdle }
    : getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage, {
        reposo: t.eyeIdle,
        killSwitch: t.eyeKillSwitch,
        dalle: t.eyeDallE3,
        lasManosDown: t.eyeLasManosDown,
        gate: t.eyeGate,
        jacobs: t.eyeJacobs,
      })
```

Tests viejos. `useJaxStore.eyeState.test.js` se reescribe entero:

```js
import { describe, expect, it } from 'vitest'
import { getEyeState } from './useJaxStore'
import { TOKENS } from '../tema/tokens'

const ETIQUETAS = {
  reposo: 'idle', killSwitch: 'INTERRUPTOR', dalle: 'PINTOR-3', lasManosDown: 'MANOS CAÍDAS', gate: 'PORTÓN', jacobs: 'Jacobo',
}

// A-45 (2026-09-16): sin defaults. Las etiquetas vienen siempre de quien llama.
describe('getEyeState usa exactamente las etiquetas que recibe', () => {
  it('cada estado devuelve su etiqueta', () => {
    expect(getEyeState({}, {}, true, false, false, ETIQUETAS).label).toBe('idle')
    expect(getEyeState({}, {}, true, true, false, ETIQUETAS).label).toBe('INTERRUPTOR')
    expect(getEyeState({}, {}, true, false, true, ETIQUETAS).label).toBe('PINTOR-3')
    expect(getEyeState({}, {}, false, false, false, ETIQUETAS).label).toBe('MANOS CAÍDAS')
    expect(getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false, ETIQUETAS).label).toBe('PORTÓN')
    expect(getEyeState({}, { p: { status: 'running' } }, true, false, false, ETIQUETAS).label).toBe('Jacobo')
  })

  it('el kill switch tiene prioridad sobre el reposo', () => {
    expect(getEyeState({}, {}, true, true, false, ETIQUETAS).label).toBe('INTERRUPTOR')
  })
})

// Task 20 (spec 2026-09-14-tema-tokens §7.3): el ojo devuelve el NOMBRE de un token del tema, no un hex.
describe('getEyeState devuelve tokens', () => {
  it('cada estado del ojo devuelve un token del tema, no un hex', () => {
    const pensando = { hyde: { status: 'thinking', token: 'faceta-hyde' } }
    const casos = [
      getEyeState({}, {}, true, true, false, ETIQUETAS),
      getEyeState({}, {}, true, false, true, ETIQUETAS),
      getEyeState(pensando, {}, true, false, false, ETIQUETAS),
      getEyeState({}, {}, false, false, false, ETIQUETAS),
      getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false, ETIQUETAS),
      getEyeState({}, { p: { status: 'running' } }, true, false, false, ETIQUETAS),
      getEyeState({}, {}, true, false, false, ETIQUETAS),
    ]
    expect(casos.map((e) => e.token)).toEqual([
      'peligro', 'faceta-imagen', 'faceta-hyde', 'texto-tenue', 'aviso', 'faceta-jacobs', 'faceta-jax-local',
    ])
    for (const e of casos) {
      expect(TOKENS).toContain(e.token)
      expect(e).not.toHaveProperty('color')
    }
  })
})
```

`useJaxStore.facetTokens.test.js`: arriba, `const ETIQUETAS = { reposo: 'r', killSwitch: 'k', dalle: 'd', lasManosDown: 'l', gate: 'g', jacobs: 'j' }`. L42: `getEyeState(facets, activePipelines, lasManos, killSwitchActive, false, ETIQUETAS)`. L67: `getEyeState(facets, {}, true, false, false, ETIQUETAS)`.

`api/websocket.test.js`: si algún test usa el valor devuelto más allá de `close`, se ajusta (grep previo: no).

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run && npm run build`
Expected: 0 fallidos; build OK.

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add frontend/package.json frontend/package-lock.json frontend/src/api/websocket.js frontend/src/api/websocket.test.js frontend/src/store/useWebSocket.js frontend/src/pages/Admin.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js frontend/src/i18n/index.jsx frontend/src/store/useJaxStore.js frontend/src/components/HalEye/HalEye.jsx frontend/src/store/useJaxStore.eyeState.test.js frontend/src/store/useJaxStore.facetTokens.test.js frontend/src/i18n/paridad.test.js frontend/src/limpieza.test.js
$G commit -m "chore(frontend): sin heroicons, ws sin send, 25 claves muertas fuera, diccionarioActivo, etiquetas del ojo requeridas, paridad es/en (A-01/09/10/11/17/29/45)"
```

---

### Task 13: Modales a mano → `Dialogo`; revocar con `ConfirmacionSuma` (A-23, A-32, A-52 'Error')

**Files:**
- Modify: `frontend/src/pages/admin/AdminMotors.jsx:155-285`
- Modify: `frontend/src/pages/admin/AdminFacetsModels.jsx` (L42-87, L202-249)
- Modify: `frontend/src/pages/admin/AdminSmtp.jsx:224-260`
- Modify: `frontend/src/components/BottomBar/PipelineModal.jsx:181-190,201-215,393-395`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js` (`adminKeyTestError`, `adminKeyRotateError`, `adminKeyRevokeError`)
- Create: `frontend/src/politica/modales.test.js`, `frontend/src/pages/admin/AdminMotors.test.jsx`, `frontend/src/pages/admin/AdminFacetsModels.test.jsx`
- Modify: `frontend/src/components/BottomBar/PipelineModal.test.jsx`, `frontend/src/pages/admin/AdminSmtp.test.jsx` (un test cada uno)

**Interfaces:**
- Consumes: `Dialogo({idTitulo, titulo, claseTitulo, onCerrar, className, children})`, `ConfirmacionSuma({titulo, mensaje, textoConfirmar, onConfirmar, onCancelar})`.

- [ ] **Step 1: Tests rojos**

```js
// frontend/src/politica/modales.test.js
// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'

// A-23 (2026-09-16): un modal hecho a mano (velo `fixed inset-0` + panel) no
// tiene inert, foco, Escape ni ARIA (Ruling U27). Todo modal va sobre
// components/Dialogo.jsx, el único que puede dibujar el velo.
const raiz = new URL('../', import.meta.url)
const archivos = readdirSync(raiz, { recursive: true })
  .filter((r) => /\.(jsx|js)$/.test(r) && !/\.test\./.test(r))

describe('modales', () => {
  it('solo Dialogo.jsx dibuja el velo de un modal', () => {
    expect(archivos.length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const conVelo = archivos.filter((r) => readFileSync(new URL(r, raiz), 'utf8').includes('fixed inset-0'))
    expect(conVelo).toEqual(['components/Dialogo.jsx'])
  })
})
```

```jsx
// frontend/src/pages/admin/AdminMotors.test.jsx
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminMotors from './AdminMotors'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

beforeEach(() => {
  localStorage.clear()
  api.get.mockImplementation((url) => Promise.resolve({ data: url === '/admin/motors'
    ? { motors: [], transport_values: ['ollama'], dispatchable_transports: ['ollama'] }
    : url === '/admin/models' ? { models: [] } : { capabilities: [] } }))
})

describe('AdminMotors -- crear motor en un Dialogo (A-23)', () => {
  it('abre un diálogo modal con nombre; Escape lo cierra; un clic en el fondo no', async () => {
    render(<I18nProvider><AdminMotors /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminMotorsCreate }))
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveAttribute('aria-modal', 'true')
    expect(dialogo).toHaveAccessibleName(es.adminMotorsCreateTitle)
    fireEvent.click(dialogo.parentElement)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
```

```jsx
// frontend/src/pages/admin/AdminFacetsModels.test.jsx
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('./AdminModelCatalog', () => ({ default: () => null }))
vi.mock('./AdminFacetBindings', () => ({ default: () => null }))
vi.mock('./AdminMotors', () => ({ default: () => null }))

import api from '../../api/client'
import AdminFacetsModels from './AdminFacetsModels'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

function resolverSuma(dialogo) {
  const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
  fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
}

beforeEach(() => {
  localStorage.clear()
  api.post.mockReset()
  api.get.mockImplementation((url) => Promise.resolve({ data: url === '/admin/keys'
    ? { providers: [{ id: 'openai', name: 'OpenAI', has_key: true, key_last4: '1234' }] }
    : { providers: [{ id: 'openai', credentials: [{ state: 'active', last_health_status: 'ok' }] }] } }))
})

describe('AdminFacetsModels -- credenciales (A-23)', () => {
  it('revocar pide la suma antes de llamar a la API', async () => {
    api.post.mockResolvedValue({ data: {} })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRevoke }))
    const dialogo = screen.getByRole('dialog')
    const confirmar = within(dialogo).getByRole('button', { name: es.adminKeyRevoke })
    expect(confirmar).toBeDisabled()
    expect(api.post).not.toHaveBeenCalled()
    resolverSuma(dialogo)
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/credentials/openai/revoke'))
  })

  it('un fallo al revocar deja el diálogo abierto', async () => {
    api.post.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRevoke }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.adminKeyRevoke }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('rotar abre un Dialogo con nombre', async () => {
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRotate }))
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
  })

  it('un fallo al probar la llave dice un texto de i18n, no "Error" literal', async () => {
    api.post.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyTest }))
    expect(await screen.findByText(`${es.adminKeyFail}: ${es.adminKeyTestError}`)).toBeInTheDocument()
  })
})
```

`PipelineModal.test.jsx`, un `describe` nuevo al final:

```jsx
describe('PipelineModal -- es un Dialogo (A-23)', () => {
  it('diálogo modal con nombre; el clic en el fondo no cierra y Escape sí', async () => {
    api.get.mockResolvedValue({ data: { capabilities: [], motors: [] } })
    const onClose = vi.fn()
    render(<I18nProvider><PipelineModal objective="x" onClose={onClose} onSubmit={() => Promise.resolve()} /></I18nProvider>)
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveAccessibleName(es.newPipelineTitle)
    fireEvent.click(dialogo.parentElement)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
```

`AdminSmtp.test.jsx`, dentro del `describe` del diálogo, después de `'Cancelar y Escape cierran el diálogo sin enviar'`. Reusar la carga del test de L181 ("el botón abre un diálogo prellenado…"): copiar sus mocks y su apertura al escribirlo.

```jsx
  it('el diálogo de prueba es el Dialogo común: portal fuera del formulario y el fondo no cierra (A-23)', async () => {
    // (mismos mocks y apertura que el test "el botón abre un diálogo prellenado…")
    const dialogo = await screen.findByRole('dialog')
    expect(dialogo.closest('form[class]')).toBeNull()
    fireEvent.click(dialogo.parentElement)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run src/politica/modales.test.js src/pages/admin/AdminMotors.test.jsx src/pages/admin/AdminFacetsModels.test.jsx src/components/BottomBar/PipelineModal.test.jsx src/pages/admin/AdminSmtp.test.jsx`
Expected: FAIL en los nuevos (5 archivos con velo; sin `role="dialog"` en Motors, FacetsModels y PipelineModal; el clic en el fondo cierra Smtp y PipelineModal).

- [ ] **Step 3: Implementar**

`AdminMotors.jsx` (import `Dialogo from '../../components/Dialogo'`): reemplazar las dos `div` de velo y panel, y el `h2`, por

```jsx
      {creating && (
        <Dialogo idTitulo="motor-crear-titulo" titulo={t.adminMotorsCreateTitle}
          onCerrar={() => setCreating(false)} className="max-w-lg max-h-[90vh] overflow-y-auto">
          {/* …el contenido del formulario, desde el primer <label> hasta los botones, sin cambios… */}
        </Dialogo>
      )}
```

`AdminFacetsModels.jsx` (imports `Dialogo` y `ConfirmacionSuma`; `const addToast = useJaxStore((s) => s.addToast)` con import del store):

```jsx
  async function handleTest(id) {
    setTesting(p => ({ ...p, [id]: true }))
    setTestResult(p => ({ ...p, [id]: null }))
    try {
      const { data } = await api.post(`/admin/credentials/${id}/test`)
      setTestResult(p => ({ ...p, [id]: data }))
      loadCredentials()  // salud persistida — refleja lo que quedó en DB
    } catch {
      setTestResult(p => ({ ...p, [id]: { ok: false, error: t.adminKeyTestError } }))
    } finally {
      setTesting(p => ({ ...p, [id]: false }))
    }
  }
```

En `handleRotate` y `handleRevoke`, el `catch {}` vacío pasa a `catch { addToast({ type: 'error', message: t.adminKeyRotateError }) }` (y `t.adminKeyRevokeError`). En `handleRevoke`, `setRevokeConfirm(null)` queda solo en el camino de éxito, así el diálogo sigue abierto ante un fallo. Los modales:

```jsx
      {rotating && (
        <Dialogo idTitulo="llave-rotar-titulo" titulo={`${t.adminKeyEnter} ${providers.find(p => p.id === rotating)?.name ?? ''}`}
          onCerrar={() => setRotating(null)}>
          <PasswordInput
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
            placeholder={t.adminKeyNewValue}
            wrapperClassName="mb-4"
            className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco font-mono"
          />
          <div className="flex gap-2 justify-end">
            <button onClick={() => setRotating(null)} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
            <button
              onClick={() => handleRotate(rotating)}
              disabled={saving || !newKey.trim()}
              className="px-4 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors"
            >
              {saving ? t.adminBindingsSaving : t.adminKeySave}
            </button>
          </div>
        </Dialogo>
      )}

      {/* Revocación: corte inmediato, sin gracia -- destructivo: ConfirmacionSuma */}
      {revokeConfirm && (
        <ConfirmacionSuma
          titulo={t.adminKeyRevokeConfirmTitle}
          mensaje={t.adminKeyRevokeConfirmBody}
          textoConfirmar={t.adminKeyRevoke}
          onConfirmar={() => handleRevoke(revokeConfirm)}
          onCancelar={() => setRevokeConfirm(null)}
        />
      )}
```

(Antes decía `t.attachUploading`, una clave de adjuntos del frente D. `adminBindingsSaving` es la que `AdminMotors` ya usa para "guardando".)

i18n, `es.js`: `adminKeyTestError: 'No se pudo probar la credencial.'`, `adminKeyRotateError: 'No se pudo rotar la credencial.'`, `adminKeyRevokeError: 'No se pudo revocar la credencial.'`. `en.js`: `adminKeyTestError: 'The credential could not be tested.'`, `adminKeyRotateError: 'The credential could not be rotated.'`, `adminKeyRevokeError: 'The credential could not be revoked.'`.

`AdminSmtp.jsx` (import `Dialogo`):

```jsx
      {dialogo.abierto && (
        <Dialogo idTitulo="smtp-test-titulo" titulo={t.smtpTestModalTitle}
          claseTitulo="text-base font-bold text-texto-fuerte mb-4" onCerrar={cerrarDialogo}>
          <form onSubmit={enviarPrueba} className="space-y-3">
            {/* el <div> del campo sin `autoFocus` (Dialogo enfoca el primer campo), el alert y los botones, sin cambios */}
          </form>
        </Dialogo>
      )}
```

Si `cerrarDialogo` devolvía el foco a `disparadorPrueba` a mano, ese código se borra: `Dialogo` ya devuelve el foco al elemento que lo tenía. El test existente "el foco vuelve al botón al cerrar" lo verifica.

`PipelineModal.jsx` (import `Dialogo from '../Dialogo'`):

```jsx
  return (
    <Dialogo idTitulo="pipeline-modal-titulo" titulo={t.newPipelineTitle}
      claseTitulo="text-sm font-bold text-texto uppercase tracking-widest" onCerrar={onClose}>
      <p className="text-xs text-texto-tenue -mt-3 mb-4 truncate">
        {t.objectiveLabel}: {objective}
      </p>
      {/* …Modo, Forma, cadena/paralelo, avisos del catálogo y Botones, sin cambios… */}
    </Dialogo>
  )
```

A-32, `handleSubmit` en paralelo:

```jsx
    const steps = buildSteps(selected, objective, FACET_OPTIONS, motorChoices, motorsByKey)
    await onSubmit({
      name: t.pipelineName(objective),
      objective,
      mode,
      max_steps: steps.length,
      steps,
    })
```

- [ ] **Step 4: Verde, suite y escaneos**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run && npm run build`
Expected: 0 fallidos (incluye `dialogosDelNavegador.test.js` y `contraste.test.js`).

- [ ] **Step 5: Commit**

```bash
G="git -C /home/fruiz/worktrees/jax-platform-frente-a"
$G add frontend/src/pages/admin/AdminMotors.jsx frontend/src/pages/admin/AdminFacetsModels.jsx frontend/src/pages/admin/AdminSmtp.jsx frontend/src/components/BottomBar/PipelineModal.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js frontend/src/politica/modales.test.js frontend/src/pages/admin/AdminMotors.test.jsx frontend/src/pages/admin/AdminFacetsModels.test.jsx frontend/src/components/BottomBar/PipelineModal.test.jsx frontend/src/pages/admin/AdminSmtp.test.jsx
$G commit -m "fix(ui): los cinco modales a mano pasan a Dialogo y revocar a ConfirmacionSuma; sin fallbacks muertos (A-23/32/52)"
```

---

### Task 14: jax — familia de espejos `router_keywords` (A-22, lado jax)

**Files (repo jax):**
- Modify: `/home/fruiz/worktrees/jax-frente-a/scripts/check_mirror_sync.py` (tupla `FAMILIAS`)
- Modify: `/home/fruiz/worktrees/jax-frente-a/jax/core/router.py` (comentario arriba de `KIMI_KW`)

**Interfaces:**
- Consumes: los 12 nombres de la Task 8 en `jax-platform/backend/api/chat.py`.
- Produces: la familia `router_keywords` (canónico `jax/core/router.py`).

- [ ] **Step 1: Worktree de jax**

```bash
git -C /home/fruiz/jax fetch origin
git -C /home/fruiz/jax worktree add /home/fruiz/worktrees/jax-frente-a -b fix/hallazgos-frente-a origin/master
```

- [ ] **Step 2: Rojo (contra la plataforma SIN el renombre)**

Run: `cd /home/fruiz/worktrees/jax-frente-a && JAX_PLATFORM_REPO_ROOT=/home/fruiz/jax-platform python3 scripts/check_mirror_sync.py; echo "exit=$?"`
Expected, ANTES de agregar la familia: `exit=0` (hoy nadie compara las keywords: ese es el punto ciego). Anotarlo.

- [ ] **Step 3: Agregar la familia**

Al final de `FAMILIAS`, antes del `)` de cierre:

```python
    Familia(
        nombre="router_keywords",
        canonico=JAX_ROOT / "jax" / "core" / "router.py",
        espejos=(
            # La Mesa web copia las keywords del auto-ruteo (A-22 de la auditoria
            # de sobre-ingenieria, 2026-09-16). NO puede importarlas: jax.core.router
            # arrastra contrato_dispatch -> `from facet_resolver import _db_conn`, que
            # dentro del backend resuelve al facet_resolver de la plataforma, y ~/jax
            # no existe en el runner (verificado por terceros). Hasta hoy la copia
            # podia divergir sin que nada avisara: los 10 sets y el desempate estaban
            # identicos por AST y ningun checker los miraba.
            ("jax-platform", JAX_PLATFORM_ROOT / "backend" / "api" / "chat.py"),
        ),
        # Los 10 sets, el orden de desempate y el mapa faceta -> (keywords, fuertes).
        # _sin_tildes y el scoring quedan afuera: el de la Mesa tiene logs y
        # docstring propios. Si un dia divergen por diseno, se declara con el marcador.
        compartidos=(
            "KIMI_KW", "KIMI_STRONG",
            "HIPATIA_KW", "HIPATIA_STRONG",
            "JEKYLL_KW", "JEKYLL_STRONG",
            "THOT_KW", "THOT_STRONG",
            "ADA_KW", "ADA_STRONG",
            "_TIEBREAK",
            "_KW_SETS",
        ),
        nota="Copia en jax-platform backend/api/chat.py (nombres alineados 2026-09-16). "
             "ORDEN DE MERGE: la plataforma primero -- contra un jax-platform con los "
             "nombres viejos (_KIMI_KW...) esta familia da 'falta' en los 12 simbolos.",
    ),
```

`jax/core/router.py`, arriba de `KIMI_KW`, agregar al comentario existente: `# ESPEJO en jax-platform backend/api/chat.py, familia router_keywords de scripts/check_mirror_sync.py: un cambio aca se hace alla en el mismo paso.`

- [ ] **Step 4: Rojo y verde de la familia**

Run: `cd /home/fruiz/worktrees/jax-frente-a && JAX_PLATFORM_REPO_ROOT=/home/fruiz/jax-platform python3 scripts/check_mirror_sync.py; echo "exit=$?"`
Expected: `exit=1` con `DRIFT: 'KIMI_KW (jax-platform)' falta…` (12 líneas): master de la plataforma todavía no renombró.

Run: `cd /home/fruiz/worktrees/jax-frente-a && JAX_PLATFORM_REPO_ROOT=/home/fruiz/worktrees/jax-platform-frente-a python3 scripts/check_mirror_sync.py; echo "exit=$?"`
Expected: `exit=0`, `[router_keywords] sincronizado (0 divergencia(s) declarada(s))`.

Mutación (en una copia de scratch, nunca en el worktree):

```bash
S=/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/mutacion-router
rm -rf "$S" && mkdir -p "$S" && cp -r /home/fruiz/worktrees/jax-platform-frente-a/backend "$S/backend"
sed -i 's/"codigo", "programar"/"codigo", "programa_mutado"/' "$S/backend/api/chat.py"
cd /home/fruiz/worktrees/jax-frente-a && JAX_PLATFORM_REPO_ROOT="$S" python3 scripts/check_mirror_sync.py; echo "exit=$?"
rm -rf "$S"
```
Expected: `exit=1` con `DRIFT: 'KIMI_KW (jax-platform)' difiere del canonico SIN declararlo`.

Run: `cd /home/fruiz/worktrees/jax-frente-a && python3 -m pytest scripts/_check_mirror_sync_test.py -q`
Expected: `14 passed` (el piso del job no cambia).

- [ ] **Step 5: Commit (en jax; el PR se abre en la Task 16, después del merge de la plataforma)**

```bash
J="git -C /home/fruiz/worktrees/jax-frente-a"
$J add scripts/check_mirror_sync.py jax/core/router.py
$J commit -m "ci(mirror): las keywords del auto-ruteo de la Mesa entran a check_mirror_sync (A-22)"
```

---

### Task 15: Prueba de carga — gate de merge

**Files (scratchpad, NUNCA en la rama):**
- Create: `/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/carga_frente_a_test.py`, `…/carga-frente-a.md`, `…/carga-frente-a.json`

**Cómo:** el mismo arnés que `carga_etapa5_test.py` y el de fijar contraseña.
- pytest en DOS worktrees de scratch: la base (`26c9cd5`) y el HEAD de la Task 13: `git -C /home/fruiz/jax-platform worktree add --detach /home/fruiz/worktrees/carga-frente-a-base 26c9cd5` y `git -C /home/fruiz/jax-platform worktree add --detach /home/fruiz/worktrees/carga-frente-a-head fix/hallazgos-frente-a`.
- El archivo de carga se copia al `backend/tests/` de cada worktree de scratch.
- `conftest.py` fuerza `jax_memory_test` y aísla el sello, el respaldo de uso y (en HEAD) las rutas. En la base, el test fija `api.command.MISSIONS_DIR`, `JAX_BIN`, `api.audit.AUDIT_LOG` y `REPO_BASE` a `tmp_path` con monkeypatch ANTES de medir, y verifica que `~/jax/missions` no ganó archivos.
- `httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app))` en el loop del portal del fixture `client`.
- El sello se verifica intacto por mtime antes y después.
- Tenants ≥ 900000 para toda fila sembrada, que se borra al final.
- Nunca `/etc/jax/.env`.
- Cada escenario, a c = 1/10/25/50: rps, p50/p95/p99, 5xx y errores.

**Escenarios (peor caso):**
- **A · `GET /api/state`** (camino caliente, A-42/A-48), con 50 pipelines activos en memoria y 200 usuarios conectados. Base vs HEAD. **Criterio: p95 HEAD ≤ p95 base + 10 %.**
- **B · `POST /api/chat` enlatado** (`facet=hyde`, sin memoria: `chat_sin_memoria`) y **error 502** (`_invoke_facet` parcheado para lanzar `RuntimeError`), para ver el costo de armar el `detail` y el camino de `set_facet_status` sin LLM. Base vs HEAD, mismo criterio.
- **C · `POST /api/image/generate` con 400 del proveedor** (cliente HTTP falso con 200 ms de latencia): p95 y 0 5xx fuera del 502 esperado.
- **D · `POST /api/command` + `GET /api/command/{id}`** con `JAX_BIN` = script `#!/bin/sh\nexit 0` en `tmp_path`: 500 tareas a c=25. Invariantes: cada GET termina en `completed`+`comando_sin_resultado`; ninguna queda `running`; ningún owner file queda con JSON a medias (los 500 parsean). Repetir con un `JAX_BIN` inexistente: todos terminan `failed`+`comando_fallo`.
- **E · `POST /api/pipelines` con el cupo lleno** (429 con código), c=50.
- **F · `POST /api/auth/login` de una cuenta bloqueada con la contraseña correcta** (423 con código), c=10: lo domina el bcrypt. Base vs HEAD.
- **G · `GET /api/admin/dashboard`** con 100.000 filas en `axioma_usage` (tenants ≥ 900000, 5.000 de hoy), 2.000 filas en `jacobs_pipelines` (tenant ≥ 900000, mitad `completed`) y `LAS_MANOS_URL`/`JAX_PLATFORM_URL` apuntando a `http://127.0.0.1:1` (rechazo inmediato). Base vs HEAD a c=1/10/25.
- **H · `GET /api/audit`** con un log de 50 MB. Mientras 10 lectores lo piden en paralelo, un bucle mide la latencia de `GET /api/health` en el mismo loop. Base vs HEAD. **Criterio:** en HEAD el p95 de `/api/health` durante la lectura no supera 5× su p95 en reposo (en la base se espera que el loop se congele); memoria pico (tracemalloc) de HEAD < 5 MB.
- **I · `GET /api/admin/repo/file`**, un .md de 1 MB y una imagen de 2 MB, c=25: mismo control de `/api/health` que en H.
- **J · `POST /api/chat/upload` de 11 MB** (413 con código), c=10.
- **K · EXPLAIN sobre las consultas REALES** en `jax_memory_test`, con los datos de G: `SQL_USO_DEL_DIA` → `range` sobre `idx_axioma_usage_periodo`; `SQL_LLAVES` → `credential` por `idx_provider_state`; `SQL_PIPELINES_COMPLETADOS` → `idx_pipelines_status`; `SELECT \`key\`, display_name FROM facet` (arranque, una vez). Buscar `Using filesort` y `Using temporary`.

- [ ] **Step 1: Escribir el arnés y correrlo en los dos worktrees**

```bash
S=/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad
for W in base head; do
  cp "$S/carga_frente_a_test.py" /home/fruiz/worktrees/carga-frente-a-$W/backend/tests/
  (cd /home/fruiz/worktrees/carga-frente-a-$W/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml CARGA_SALIDA="$S/carga-frente-a-$W.json" /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/carga_frente_a_test.py -q -s -p no:cacheprovider)
done
```

- [ ] **Step 2: Informe** en `carga-frente-a.md`: las tablas base/HEAD por escenario, el punto de saturación ("con cuántos usuarios simultáneos empieza a degradarse") y los EXPLAIN tal cual. Veredicto por criterio.
- [ ] **Step 3: Gate.** Cualquier 5xx inesperado, invariante roto, criterio A/B/H fuera o EXPLAIN con scan de tabla que crece → **no hay merge**: ronda de arreglos y la carga se repite sobre el HEAD nuevo. Después, borrar los dos worktrees de scratch (`git -C /home/fruiz/jax-platform worktree remove …`).

---

### Task 16: Pisos de CI, canario, PR, deploy y Biblioteca

- [ ] **Step 1: Suites completas, dos veces cada una**

```bash
cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs --junitxml=/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/junit-db.xml
cd /home/fruiz/worktrees/jax-platform-frente-a/backend && JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs --junitxml=/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/junit-nodb.xml
cd /home/fruiz/worktrees/jax-platform-frente-a/frontend && npx vitest run --reporter=json --outputFile=/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/vitest.json
```
Expected: 0 failed, 0 errors. Con DB, 1 skip.

- [ ] **Step 2: Pisos exactos** en `.github/workflows/policy.yml`: `numPassedTests !== <N vitest>` (hoy 448), `PISO_PASSED = <N con DB>` (hoy 1175), `JAX_CI_MIN_PASSED: "<N sin DB>"` (hoy 614). Cada uno con un comentario fechado, igual que los anteriores: "448 -> N el 2026-09-16 (frente A de la auditoría): +x … por archivo; vistos en rojo antes; medido dos veces". Si algún número BAJA respecto del piso (tests borrados: `test_sse_isolation` pierde un assert, no un test), explicar el porqué en el comentario. Commit: `ci: pisos del frente A medidos dos veces`.

- [ ] **Step 3: Review final** (opus) de `origin/master..HEAD` con el spec y las Discrepancias. Sus hallazgos se arreglan antes del PR, no se difieren.

- [ ] **Step 4: Push y PR**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-a push -u origin fix/hallazgos-frente-a
gh pr create --repo fjruizhn/jax-platform --base master --head fix/hallazgos-frente-a \
  --title "Frente A: limpieza, defectos y reglas de la auditoría (A-01..A-55)" \
  --body-file /tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/pr-frente-a.md
```
El cuerpo lleva: la lista A-xx → commit, las Discrepancias 1-10, los números de la Task 15, las variables nuevas del `.env` y el orden de merge con jax. Termina con las líneas de atribución de la sesión.

- [ ] **Step 5: Canario de CI (rojo sobre el sha real, por API)**

1. Romper un test nuevo que corre en el job sin DB (`test_repositorio_rutas.py::test_leer_una_carpeta_hermana_que_empieza_igual_es_400`: cambiar `(400, "ruta_invalida")` por `(400, "canario")`), commit `test: canario (se revierte)` y push.
2. `SHA=$(git -C /home/fruiz/worktrees/jax-platform-frente-a rev-parse HEAD)`; esperar la corrida y `gh api repos/fjruizhn/jax-platform/commits/$SHA/check-runs --jq '.check_runs[] | [.name, .conclusion] | @tsv'` → `backend-tests-no-db  failure`.
3. Igual con un test nuevo de vitest (`limpieza.test.js`, A-10: `not.toContain('useI18n')` → `toContain('useI18n')`) en el mismo commit → `frontend-tests  failure`.
4. Revertir con `git revert --no-edit HEAD`, push; `gh api …/commits/<sha nuevo>/check-runs` → todos `success`.
Anotar los dos shas y las conclusiones en el reporte.

- [ ] **Step 6: Gate por headSha y merge de la plataforma**

```bash
gh pr checks <N> --repo fjruizhn/jax-platform
gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid
git -C /home/fruiz/jax-platform ls-remote origin refs/heads/fix/hallazgos-frente-a
git -C /home/fruiz/worktrees/jax-platform-frente-a rev-parse HEAD
```
Los tres shas iguales y ningún check fuera de `SUCCESS`. Recién ahí: `gh pr merge <N> --repo fjruizhn/jax-platform --merge --match-head-commit <sha>`.

- [ ] **Step 7: PR de jax (familia de espejos) y su gate**

```bash
git -C /home/fruiz/worktrees/jax-frente-a push -u origin fix/hallazgos-frente-a
gh pr create --repo fjruizhn/Jax --base master --head fix/hallazgos-frente-a --title "mirror-sync: keywords del auto-ruteo de la Mesa (A-22)" --body-file /tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad/pr-jax-frente-a.md
```
El job `mirror-sync` clona el master de la plataforma YA mergeado: tiene que dar `sincronizado` en `router_keywords`. Mismo gate por headSha. No se mergea todavía: la Biblioteca (Step 11) va en esta misma rama.

- [ ] **Step 8: Línea base de producción antes del deploy (solo lectura)**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/health
curl -s http://127.0.0.1:8080/api/health | python3 -c "import json,sys; print(json.load(sys.stdin)['active_pipelines'])"
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
sudo grep -oE '^(JAX_REPO_PATH|JAX_CONFIG_PATH|JAX_AUDIT_LOG_PATH|JAX_MISSIONS_DIR|JAX_BIN|JAX_REPO_BASE|JAX_PLATFORM_URL|JAX_SEED_SUPERADMIN_EMAIL|JAX_SEED_TENANT_NAME)=' /etc/jax/.env
for p in /home/fruiz/jax /home/fruiz/jax/config/config.toml /home/fruiz/jax/las_manos/logs/audit.jsonl /home/fruiz/jax/missions /home/fruiz/.local/bin/jax /home/fruiz/jax/repo; do test -e "$p" && echo "ok $p" || echo "FALTA $p"; done
```
Anotar todo. `grep -o` imprime solo los NOMBRES, nunca valores. Si una ruta dice `FALTA`, se para y se pregunta a Fernando.
0 pipelines en vuelo en la base (lectura): `set -a; . /etc/jax/.env; set +a; mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "SELECT COUNT(*) FROM jacobs_pipelines WHERE status IN ('pending','running','interrupted')"` en una subshell que se cierra enseguida. Tienen que ser 0 (y `active_pipelines` = 0); si no, se espera.

- [ ] **Step 9: Deploy (con GO de Fernando)**

Backend:

```bash
sudo cp -p /etc/jax/.env /etc/jax/.env.backup-pre-frente-a-$(date +%Y%m%d-%H%M%S)
sudo cmp /etc/jax/.env /etc/jax/.env.backup-pre-frente-a-* && echo "backup idéntico"
sudo tee -a /etc/jax/.env >/dev/null <<'EOF'
# Frente A (2026-09-16): rutas obligatorias (A-55), base de jax-platform para el tablero (A-36) y semilla (A-54)
JAX_REPO_PATH=/home/fruiz/jax
JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml
JAX_AUDIT_LOG_PATH=/home/fruiz/jax/las_manos/logs/audit.jsonl
JAX_MISSIONS_DIR=/home/fruiz/jax/missions
JAX_BIN=/home/fruiz/.local/bin/jax
JAX_REPO_BASE=/home/fruiz/jax/repo
JAX_PLATFORM_URL=http://127.0.0.1:8080
JAX_SEED_SUPERADMIN_EMAIL=fernando@rich-hn.com
JAX_SEED_TENANT_NAME=Inversiones Diamante Negro
EOF
git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only && git -C /home/fruiz/jax-platform log --oneline -1
/home/fruiz/jax-platform/backend/.venv/bin/pip install -r /home/fruiz/jax-platform/backend/requirements.txt
sudo systemctl restart jax-platform.service
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/health
sudo journalctl -u jax-platform.service --since "-2 min" --no-pager | grep -iE "traceback|error|RuntimeError" || echo "journal limpio"
```
El `sudo cmp` se corre ANTES del `tee` (con un solo backup del día; si hay más, se nombra el archivo exacto). El valor de `JAX_SEED_SUPERADMIN_EMAIL` lo confirma Fernando al dar el GO. **Rollback:** restaurar el backup del `.env`, `git -C /home/fruiz/jax-platform checkout 26c9cd5` y reiniciar.

Frontend:

```bash
export PATH=/home/fruiz/.nvm/versions/node/v24.16.0/bin:$PATH
cd /home/fruiz/jax-platform/frontend && npm ci && npm run build && md5sum dist/index.html dist/assets/index-*.js
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" 'B=/www/wwwroot/axioma-ia.io.backup-pre-frente-a-$(date +%Y%m%d-%H%M%S); sudo cp -a /www/wwwroot/axioma-ia.io "$B" && sudo diff -r /www/wwwroot/axioma-ia.io "$B" && echo "backup idéntico: $B"'
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

- [ ] **Step 10: Smoke y verificación en vivo**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8080/api/admin/repo/save   # ruta borrada: 404 o 405 sin token, anotar
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/admin/dashboard          # 401 sin token
/home/fruiz/bin/k6 run -e VUS=10 /home/fruiz/jax/loadtest/health.js                          # p95 anotado (tráfico de producción, 25 s)
```
Con un token que Fernando genere en su sesión (U23: sin credenciales scriptadas), opcional: `k6 run -e TOKEN=<token> -e VUS=10 /home/fruiz/jax/loadtest/api-autenticada.js` desde una copia en scratch con `RUTAS = ['/api/state', '/api/facets', '/api/pipelines']`. Anotar p95 y tasa de error, o por qué no se corrió.
**En vivo con Fernando, en claro/oscuro y es/en:** el tablero (JAX Engine vivo, llaves N/M reales, pipelines completados con número, RAM); los labels de facetas con nombre ("Dr. Jekyll", no `jekyll`); en el chat, "que modelo sos" a JAX y un mensaje a Hyde traducidos al cambiar de idioma; Admin → Modelos: probar, rotar (Escape cierra) y revocar (pide la suma); Admin → SMTP: el diálogo de prueba; Pipeline: el modal cierra con Escape y no con clic afuera.

- [ ] **Step 11: Biblioteca (en la rama de jax del Step 7)**

`/home/fruiz/worktrees/jax-frente-a/DEUDA.md`, sección nueva antes de "Cerrado — tanda A": `## Cerrado — hallazgos de la auditoría de sobre-ingeniería, frente A (2026-09-16)`. Contenido:
- PR y sha de la plataforma; `index-*.js` servido; hora del reinicio.
- Qué se borró y qué se arregló, por id (A-01..A-55; A-21 va con D), con el defecto de `_safe_path` como HISTORIA ("leía y borraba en carpetas hermanas; superadmin; sin carpetas hermanas en producción").
- Las Discrepancias 1-10, con la decisión de Fernando sobre la 10.
- Los números de la carga (Task 15) y los EXPLAIN tal cual.
- Las 9 variables agregadas al `.env` (nombres), el backup y el rollback.
- Los pisos nuevos y el canario (shas).
- La lección: *"un default a `$HOME` en un import hace que los tests escriban en producción"* (`test_command_path_traversal`).
- Pendientes con fecha, si los hay.

`/home/fruiz/worktrees/jax-frente-a/CONTEXT.md` §9 (Historial de hitos): una entrada fechada con un párrafo y el puntero a DEUDA.md.

```bash
J="git -C /home/fruiz/worktrees/jax-frente-a"
$J add DEUDA.md CONTEXT.md
$J commit -m "docs(biblioteca): frente A de la auditoria cerrado y desplegado"
$J push
```
Gate por headSha (como el Step 6) y `gh pr merge <N jax> --repo fjruizhn/Jax --merge --match-head-commit <sha>`. Después: `git -C /home/fruiz/jax pull --ff-only`.

- [ ] **Step 12: Limpieza.** Borrar los worktrees `/home/fruiz/worktrees/jax-platform-frente-a` y `/home/fruiz/worktrees/jax-frente-a` y las ramas locales mergeadas; mover los informes del scratchpad al ledger o borrarlos. El backup del `.env` queda hasta que Fernando cierre la verificación en vivo; después se borra con fecha.

---

## Autorrevisión (hecha al escribir el plan)

**Cobertura del spec (sección A):**

| Ítem | Tarea |
|---|---|
| A-01 | 12 |
| A-02, A-03, A-04 | 1 |
| A-05, A-06 | 6 |
| A-07, A-08 | 3 |
| A-09, A-10, A-11 | 12 |
| A-12 | 1 |
| A-13 | 8 |
| A-14 | 9 |
| A-15 | 1 |
| A-16 | 8 |
| A-17 | 1 (backend) + 12 (i18n) |
| A-18, A-19, A-20 | 1 |
| A-21 | frente D (fuera de este plan, como dice el spec) |
| A-22 | 8 + 14 |
| A-23 | 13 |
| A-24 | 2 |
| A-25 | 4 |
| A-26 | 3 |
| A-27 | 11 |
| A-28 | 1 |
| A-29 | 12 |
| A-30 | 9 |
| A-31 | 3 |
| A-32 | 13 |
| A-33, A-34 | 1 |
| A-35, A-36, A-37, A-38 | 6 |
| A-39 | 4 |
| A-40 | 3 |
| A-41 | 5 |
| A-42 | 7 |
| A-43, A-44 | 11 |
| A-45 | 12 |
| A-46, A-47 | 4 |
| A-48 | 7 |
| A-49 | 6 |
| A-50 | 10 |
| A-51 | 8, 9 y 11 |
| A-52 | 6 (RAM) + 13 ('Error') |
| A-53 | 8, 9 y 11 |
| A-54, A-55 | 5 |

Reglas comunes: la carga es la Task 15; el canario, los pisos, el deploy y la Biblioteca, la Task 16; mirror-sync, las Tasks 8 y 14 con orden de merge.

**Placeholders:** solo los de ejecución declarados en Global Constraints. Dos pasos piden reutilizar código ya existente en el mismo archivo de test (los mocks del test de AdminSmtp L181) y dicen de dónde copiarlo.

**Consistencia de nombres:** los que se usan en tests, implementación e Interfaces son los mismos: `ruta_requerida`, `AvisoDeChat.como_texto`, `_detalle_502_http`/`_detalle_502_generico`, `_fire_completed(facet, tenant_id, user_id)`, `http_de_fallo_de_envio(exc, registro, contexto, destinatario)`, `USUARIO_LLAVES_LEGADO`, `_resolve`/`_file_info(path)`, `_servicio(nombre, base_url, ruta)`, `SQL_USO_DEL_DIA`/`SQL_LLAVES`/`SQL_PIPELINES_COMPLETADOS`, `cargar_nombres_de_facetas`, `textoDeErrorDeMesa`, `textoDeAviso`, `contenidoDeComando`, `_resolverComando`, `diccionarioActivo`, y `getEyeState(..., etiquetas)` con la clave `reposo`.

**Riesgos:**
1. Las variables obligatorias pueden dejar el servicio sin arrancar si falta una. Mitigación: Step 8 verifica nombres y rutas, el `.env` tiene backup y el rollback está escrito.
2. `jax_memory_test` local cambia el correo de user_id=1 al resembrar (Task 5, Step 3d).
3. Conflictos de merge con los frentes B/C/D (listados en Global Constraints).
4. `/api/state` no ve un cambio de `display_name` hasta reiniciar (Discrepancia 3: hoy no hay escritor en runtime y la guarda lo fija).
