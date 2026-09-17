# Frente B · Kill switch real — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el botón KILL de la Mesa detenga de verdad a JAX: un superadmin lo activa desde la Mesa, el archivo del freno aparece en `JAX_KILL_SWITCH_PATH`, la Mesa responde 423 `kill_switch_activo`, y lo que está corriendo se aborta. Eso incluye LAS MANOS, los steps de Jacobs en vuelo y `jax --task`. Además, cada activación y cada reanudación quedan auditadas.

**Architecture:**
1. **Una sola definición del freno**: `jax/core/interruptor.py` (canónico), con un symlink en `las_manos/interruptor.py` y una copia espejada en `jax-platform/backend/interruptor.py`. Una familia nueva de `check_mirror_sync.py` vigila que las copias no diverjan.
   - La ruta sale de `JAX_KILL_SWITCH_PATH`. Si falta, lanza error: fail-closed.
   - La lectura usa `os.stat`. Sólo ENOENT cuenta como freno suelto; cualquier otro error cuenta como freno puesto.
   - La escritura es atómica: archivo temporal + `os.link`, que además es exclusiva.
2. **Los lectores de jax** usan ese módulo:
   - LAS MANOS: `server.py`, `ssh_worker`, `motor_registry/routes.py` y `worker.py`.
   - Jacobs: `policy.py`, y además un freno en vuelo por step con `correr_con_interruptor`.
   - El REPL y `jax --task`.
3. **jax-platform escribe el freno** desde `kill_switch.py`: `estado`, `activar`, `reanudar` y `exigir_mesa_libre`.
   - Cada cambio queda en la tabla `kill_switch_audit`.
   - Se difunde por el event bus a todos los suscriptores, WS y SSE.
   - Tres endpoints de superadmin.
   - `/api/state.kill_switch_active` para todos los usuarios.
   - 423 en las cinco rutas que ejecutan.
4. **El frontend** muestra el estado real:
   - activar con `Dialogo`;
   - reanudar con `ConfirmacionSuma`;
   - errores visibles, sin `catch {}`.
5. **Deploy**: primero la infra (directorio + variable), después los lectores (jax), y por último el escritor (jax-platform). Así nunca hay un lector mirando la ruta vieja mientras el escritor usa la nueva.

**Tech Stack:**
- Python 3.14.4, en los tres venvs (`~/jax/.venv`, `~/jax/las_manos/.venv`, `jax-platform/backend/.venv`; verificado con `--version`).
- FastAPI 0.139, aiomysql, MariaDB 12.3 (CI 11.8), pytest + pytest-asyncio (`asyncio_mode = auto` en jax-platform).
- React 19, vitest, k6 v2.2.0 (`/home/fruiz/bin/k6`).

**Spec:** `/home/fruiz/worktrees/jax-platform-hallazgos-docs/docs/superpowers/specs/2026-09-16-hallazgos-auditoria-design.md`, sección **B. Kill switch real** (y las reglas comunes de §0).

**Base medida al escribir:** jax-platform `26c9cd5`, jax `bd95237`, 2026-09-16.

---

## Discrepancias con el spec (con evidencia)

Nada de esto cambia en silencio: cada punto dice qué afirma el spec, qué se midió y qué hace el plan.

1. **Hay lectores que el spec no nombra.** El spec lista `jacobs/policy.py`, `las_manos/server.py` (+`config.toml`), `motor_registry/routes.py`, `worker.py`, `ssh_worker.py` y `jax/core/main.py`. `grep -rn "PAUSE\|KILL_SWITCH\|kill_switch" --include=*.py --include=*.toml --include=*.sh` en `bd95237` encuentra además:
   - `jacobs/routes.py:111,272,326` (423 al crear, reanudar y aprobar) y `jacobs/executor.py:1122` (entre olas). Leen a través de `jacobs.policy.check_kill_switch`, así que quedan cubiertos al cambiar `policy.py`.
   - `tools/jacobs_relaunch.py:43`: importa `check_kill_switch` y su mensaje nombra la ruta literal.
   - **`config/config.toml:14` `[jax] kill_switch_path`**. Es el archivo que lee el REPL (`jax/core/main.py:382,523`), no `las_manos/config.toml`: el spec nombra el `config.toml` equivocado para ese lector.
   - `scripts/manual_motor_v02_integration.py:38,227-279`: la ruta literal y `sudo touch`/`sudo rm` sobre el freno de producción.
   - `CONTEXT.md:19` (§2, "Kill Switch: `/etc/jax/PAUSE`").
   - `_director_patch/executor_block.py:161`: es un fragmento de parche sin importar (`test_no_blocking_in_async.py` lo declara "no parsea"). No se toca.

   **Plan:** Tasks 3, 4 y 5 cubren todos. Un test de clase (Task 5) prohíbe el literal en cualquier archivo de código versionado.

2. **Hoy no existe ningún escritor.**
   - El frontend hace `api.post('/kill-switch')` (`frontend/src/store/useJaxStore.js:558-564`).
   - `grep -rn "kill-switch\|kill_switch" backend --include=*.py` sin tests da una sola coincidencia: `jax_engine/schemas.py:10`, el literal del evento. No hay ninguna ruta.
   - El store pone `killSwitchActive: true` ANTES del POST, y `catch {}` se traga el 404.

   Resultado: el botón muestra "KILL SWITCH ACTIVO" sin que nada se detenga. El spec dice "se quita el `catch {}`", pero no dice que el endpoint no existe. **Plan:** Tasks 6-9 construyen el escritor completo.

3. **Los lectores actuales fallan abiertos si no pueden leer el directorio.** Todos miran con `Path(...).exists()`. Medido el 2026-09-16 con el Python 3.14.4 de `las_manos/.venv`, con el archivo presente y el directorio en modo `000`:
   - `Path.exists()` → `False`;
   - `os.stat()` → `PermissionError`.

   Con el directorio nuevo (`2770 root:fruiz`), un proceso que no esté en el grupo `fruiz` leería el freno SUELTO. El spec no dice cómo se lee. **Plan:** `interruptor_activo()` con `os.stat`: sólo ENOENT es suelto (Task 1), y los tests lo prueban con `chmod 0`.

4. **`ssh_worker` no vigila si nadie le configura la ruta.** `las_manos/workers/ssh_worker.py:34` tiene `KILL_SWITCH_PATH: str | None = None` y `:100` pone el watcher sólo `if kill_switch_path:`. Hoy se lo configura `server.py:84`. Cualquier otro importador corre sin freno. **Plan:** la ruta se resuelve dentro de `ssh_exec`, y sin variable lanza error (Task 3).

5. **El estado no llega a quien no es superadmin.** El spec dice "GET al cargar" y declara el GET sólo para superadmin, pero `<KillSwitch />` se dibuja para TODOS los usuarios (`BottomBar.jsx:386`, sin chequeo de rol). Un operator nunca podría saber que la Mesa está frenada (el ojo HAL también lee `killSwitchActive`). **Plan:**
   - `/api/state` (autenticado, todos los roles) lleva `kill_switch_active`;
   - `GET /api/admin/kill-switch` (superadmin) agrega el último registro de auditoría;
   - el botón KILL y Reanudar sólo aparecen para superadmin;
   - el operator ve el aviso cuando el freno está puesto.

6. **Reanudar o aprobar un pipeline también ejecuta.**
   - El spec pone el 423 sólo en "creación de pipelines".
   - `POST /api/pipelines/{id}/resume` reenvía a Jacobs, que ya da 423 con el texto literal "Kill switch activo — no se puede reanudar" (`jacobs/routes.py:272`). La plataforma lo pasa tal cual (`api/pipelines.py:180-195`), así que es un texto sin i18n.

   **Plan:** `resume` también exige la mesa libre y responde con el código. `cancel` NO se frena: detener no se impide.

7. **"Evento WS a todos los canales": no hay difusión.**
   - `EventBus.publish` enruta a UN usuario (`jax_engine/events.py:24-36`).
   - El patrón de `las_manos_health_changed` recorre `engine_state.connected_users`, que es sólo WS (`state.py:166`; SSE nunca llama `register_user`).

   **Plan:** `EventBus.publicar_a_todos(event_type, payload)` recorre los suscriptores del bus, WS y SSE (Task 6).

8. **"El worker del REPL si corre": no existe como servicio.** Las unidades de `/etc/systemd/system/jax-*` son `las-manos`, `platform`, `platform-frontend`, `memory-worker` (+timer) y `memory-synthesis` (+timer). El REPL es interactivo (`~/.local/bin/jax`) y `/api/command` lanza `jax --task` por pedido (`api/command.py:108`). **Plan:** el deploy verifica `pgrep -af jax.core.main` vacío (Task 13).

9. **"Lo que ya corre aborta por sus watchers; Jacobs aborta en la ola siguiente" se queda corto.**
   - `jacobs/executor.py:1165-1168` corre la ola con `asyncio.gather` sin vigilancia. Un step de Hyde corre `run_sandboxed_claude` dentro de Jacobs (`executor.py:472-519`) y un step HTTP llama al proveedor directo: los dos siguen hasta terminar la ola, aunque el freno esté puesto.
   - `jax --task` mira el freno una sola vez, al empezar (`main.py:425`), y después corre Hyde entero (`main.py:468`).
   - Además hay una carrera. Cuando Jacobs corta un step de motor, cancela el job en LAS MANOS (`executor.py` `_invoke_motor` → `_cancel_motor_job`). El worker marca ese job `cancelled` "Job cancelado externamente" (`worker.py:690-700`), no `killed_by_switch`. El peor caso del spec ("el job queda `killed_by_switch`") dependería de quién llega primero.

   **Plan:**
   - `correr_con_interruptor` (sondeo cada 250 ms, cancela, y `run_sandboxed_claude` mata el proceso al cancelar: `hyde_sandbox.py:342-345`) envuelve cada step de Jacobs y cada `invoke` del REPL y de `--task` (Tasks 4 y 5).
   - El worker marca `killed_by_switch` también cuando lo cancelan con el freno puesto (Task 3).

   **Límite declarado, no diferido:** un turno de chat o de imagen de la Mesa que ya salió al proveedor antes del freno termina. Es texto o una imagen, sin efecto en el mundo: el chat intercepta a Hyde con una respuesta fija (`api/chat.py:1048-1051`) y las acciones van por Comando o Pipeline, que sí se cortan. Todo pedido nuevo recibe 423. Ver P2.

10. **Qué pasa si la auditoría falla: el spec no lo dice.** El plan decide así (ver P1):
    - Activar pone el freno PRIMERO. Si la auditoría falla, el freno queda puesto y la respuesta es 500 `kill_switch_auditoria_fallida`, con el caso en el journal.
    - Reanudar audita y borra en la misma transacción. Si la confirmación falla después de borrar, vuelve a poner el freno. Ante la duda, frenado.

## Preguntas abiertas (el plan trae la recomendación implementada)

- **P1 · Auditoría caída al activar.** Recomendación: el freno se pone igual. Una base caída es justo un momento en que se puede querer frenar, y bloquear el freno por la auditoría invertiría la prioridad. Queda 500 visible más el journal. Si Fernando prefiere "sin auditoría no hay freno", el cambio es reordenar `activar()` y un test.
- **P2 · Turno de chat o imagen en vuelo.** Recomendación: no se aborta (discrepancia 9). Si Fernando lo quiere, se envuelve `_invoke_facet` con el mismo patrón de sondeo en la plataforma: una tarea más.

---

## Global Constraints

- **Repos y ramas:** `feat/kill-switch-real` en los dos.
  - jax: `git -C /home/fruiz/jax fetch origin && git -C /home/fruiz/jax worktree add /home/fruiz/worktrees/jax-frente-b -b feat/kill-switch-real origin/master`.
  - jax-platform: `git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform worktree add /home/fruiz/worktrees/jax-platform-frente-b -b feat/kill-switch-real origin/master`.
  - Siempre `git -C <ruta>`, y `pwd` en el mismo comando si hay `cd`.
  - **Nada de `git stash`**: si hay que apartar algo, commit WIP.
- **Coordinación con los frentes A, C y D.** Tocan los mismos archivos:
  - `main.py` (A-18, A-33), `jax_engine/events.py` (A-24 quita el lock de EventBus), `jax_engine/schemas.py` (A-14);
  - `BottomBar.jsx` (A-51, D), `useJaxStore.js` (A-43, A-44), `api/errores.js` (A-51);
  - `api/chat.py`, `api/command.py` (A-55), `i18n/es.js` y `en.js`.

  Antes de abrir cada PR: `git -C <worktree> fetch origin && git -C <worktree> rebase origin/master`. Los conflictos se resuelven conservando los dos cambios. Si A-24 ya quitó el lock, `publicar_a_todos` toma la foto de `_subscribers` sin lock (no hay `await` entre la lectura y la copia).
- **Barrera de producción en los tests:**
  - jax-platform: `backend/tests/conftest.py` fuerza `JAX_DB_NAME=jax_memory_test`, y desde la Task 2 también **`JAX_KILL_SWITCH_PATH` a un temporal** (asignación, no `setdefault`: el `.env` de producción la va a tener).
  - jax: `conftest.py` de la raíz fuerza lo mismo (Task 1).
  - Nunca se cargan `/etc/jax/.env` ni `JAX_DB_*` en una shell para correr tests o la carga. La única lectura de producción es la del deploy, con GO de Fernando.
- **Fail-closed:**
  - Sin `JAX_KILL_SWITCH_PATH` (vacía o relativa) nada arranca: LAS MANOS al importar `server.py`, la plataforma al importar `main.py`, el REPL y `--task` con `sys.exit(1)`.
  - Un error al mirar el freno que no sea ENOENT cuenta como freno PUESTO.
- **Sin hardcoding:** la ruta vive sólo en `/etc/jax/.env`. Ningún archivo de código versionado contiene el literal de la ruta vieja (test de clase, Task 5).
- **Códigos estables, nunca texto de usuario en el backend:**

  | Código | HTTP | Cuándo |
  |---|---|---|
  | `kill_switch_activo` | 423 | chat, image, command, crear pipeline o reanudarlo con el freno puesto |
  | `kill_switch_no_escribible` | 503 | la plataforma no puede escribir ni borrar el archivo; nada cambió |
  | `kill_switch_auditoria_fallida` | 500 | el cambio de archivo no quedó auditado; el freno quedó PUESTO |
  | `Solo superadmin` | 403 | heredado de `require_superadmin`, no se toca |

- **i18n es/en** para cada texto nuevo; las claves viejas sin uso se borran. **Tokens del tema** (`src/tema/tokens.css`), nunca colores crudos. **Nada de `confirm/alert/prompt`**: activar va con `components/Dialogo.jsx`, reanudar con `components/ConfirmacionSuma.jsx`.
- **P10:** todo `except` amplio sin `raise`, o con cuerpo `pass`, lleva `# fail-soft: <razón>` en la misma línea. Los dos escáneres siguen verdes: `jax/policy/tests/test_no_fail_open_except.py` y `jax-platform/backend/tests/test_no_fail_open_except.py`.
- **LAS CUATRO DEL RENDIMIENTO:**
  - *Indexing:* `SQL_ULTIMO` va por `idx_kill_switch_audit_at`, sin filesort, con EXPLAIN en test (Task 7) y en el deploy (Task 13).
  - *Cache:* **ningún caché del freno, a propósito.** Un caché retrasa el freno por su TTL. Se lee con un `stat` por pedido y se mide (Task 10).
  - *Async:* las escrituras (fsync) van en `asyncio.to_thread`. El `stat` del camino caliente es síncrono, igual que los lectores actuales; su costo se mide en la Task 10 (criterio p95 ≤ base + 10 %).
  - *Load:* Task 10 (arnés, gate de merge) y Task 14 (k6 en producción). Sin número no hay GO.
- **TDD:** cada test se ve ROJO contra el código viejo por el motivo que dice el paso, y después verde. Los controles que no pueden ser rojos (el camino ya funcionaba) se validan por la mutación que indica el paso.
- **CI:** todo test nuevo lo corre un job. Los pisos se suben con el número MEDIDO dos veces y un comentario. Canario rojo sobre el sha real (Task 11).
- **Mirror-sync:** la familia `interruptor` se declara en jax y la copia se crea en jax-platform en la MISMA tarea (Task 2).
- **Commits** sin `--no-verify`, terminados con:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## Mapa de archivos

| Repo | Archivo | Acción | Responsabilidad |
|---|---|---|---|
| jax | `jax/core/interruptor.py` | Crear | canónico: ruta, lectura fail-closed, escritura atómica, `correr_con_interruptor` |
| jax | `las_manos/interruptor.py` | Crear (symlink → `../jax/core/interruptor.py`) | import pelado para LAS MANOS y Jacobs |
| jax | `conftest.py` | Modificar | fuerza `JAX_KILL_SWITCH_PATH` temporal |
| jax | `scripts/check_mirror_sync.py` | Modificar | familia `interruptor` |
| jax | `las_manos/server.py` | Modificar | ruta al importar, `_kill_switch_active` con `interruptor_activo`, 423 sin ruta literal |
| jax | `las_manos/workers/ssh_worker.py` | Modificar | sin global `KILL_SWITCH_PATH`; resuelve la ruta; watcher fail-closed |
| jax | `las_manos/motor_registry/routes.py` | Modificar | ruta por dispatch, sin default silencioso |
| jax | `las_manos/motor_registry/worker.py` | Modificar | pre-chequeo y watcher con `interruptor_activo`; cancelado con freno = `killed_by_switch` |
| jax | `las_manos/config.toml`, `config/config.toml` | Modificar | sin `kill_switch_path` |
| jax | `las_manos/motor_registry/catalog.py:189` | Modificar | comentario |
| jax | `jacobs/policy.py` | Modificar | `check_kill_switch` = `interruptor_activo()` |
| jax | `jacobs/executor.py` | Modificar | cada step dentro de `correr_con_interruptor` |
| jax | `tools/jacobs_relaunch.py`, `scripts/manual_motor_v02_integration.py` | Modificar | sin ruta literal ni sudo |
| jax | `jax/core/main.py` | Modificar | ruta desde la variable (fail-closed), invokes bajo el interruptor |
| jax | `tests/test_interruptor.py`, `tests/test_interruptor_lectores.py`, `tests/test_interruptor_escritor_de_la_plataforma.py`, `tests/test_jacobs_interruptor.py`, `tests/test_repl_interruptor.py`, `tests/test_interruptor_sin_rutas_fijas.py` | Crear | tests |
| jax | `loadtest/kill-switch.js` | Crear | k6 del 423 y admin |
| jax | `.github/workflows/policy.yml` | Modificar | `tests-puros` + `mirror-sync` + pisos |
| jax-platform | `backend/interruptor.py` | Crear | copia espejada (sin la parte async del REPL) |
| jax-platform | `backend/tests/conftest.py` | Modificar | ruta temporal forzada, limpieza por test, barrera de sesión |
| jax-platform | `backend/main.py` | Modificar | fail-closed al importar; router nuevo |
| jax-platform | `backend/kill_switch.py` | Crear | `activo`, `estado`, `activar`, `reanudar`, `exigir_mesa_libre`, `RUTAS_FRENADAS` |
| jax-platform | `backend/db/migrations.py` | Modificar | tabla `kill_switch_audit` |
| jax-platform | `backend/jax_engine/events.py`, `schemas.py` | Modificar | `publicar_a_todos`, evento `kill_switch_released` |
| jax-platform | `backend/api/admin/kill_switch.py`, `api/admin/__init__.py` | Crear/Modificar | endpoints |
| jax-platform | `backend/api/state.py` | Modificar | `kill_switch_active` |
| jax-platform | `backend/api/chat.py`, `image.py`, `command.py`, `pipelines.py` | Modificar | `Depends(exigir_mesa_libre)` |
| jax-platform | `backend/tests/identidades.py` | Modificar | `borrar_usuario` limpia `kill_switch_audit` |
| jax-platform | `backend/tests/test_interruptor_arranque.py`, `test_kill_switch.py`, `test_kill_switch_endpoints.py`, `test_kill_switch_mesa.py` | Crear | tests |
| jax-platform | `frontend/src/components/BottomBar/KillSwitch.jsx` (+test) | Modificar | estado real, Dialogo, ConfirmacionSuma, avisos |
| jax-platform | `frontend/src/store/useJaxStore.js` (+ `useJaxStore.killSwitch.test.js`) | Modificar | `loadState`, eventos, `activarKillSwitch`, `reanudarKillSwitch` |
| jax-platform | `frontend/src/api/client.js`, `api/errores.js` (+ `client.killSwitch.test.js`) | Modificar | 423 → store; `textoDeKillSwitch` |
| jax-platform | `frontend/src/components/BottomBar/BottomBar.jsx` (+test), `RightPanel/RightPanel.jsx` | Modificar | el 423 se muestra traducido |
| jax-platform | `frontend/src/i18n/es.js`, `en.js` | Modificar | claves |
| jax-platform | `.github/workflows/policy.yml` | Modificar | pisos |

## Interfaces

**Produce el canónico `jax/core/interruptor.py`.** Los símbolos marcados como compartidos son idénticos en `jax-platform/backend/interruptor.py`:

```python
VARIABLE_RUTA = "JAX_KILL_SWITCH_PATH"                          # compartido
class InterruptorSinConfigurar(RuntimeError)                     # compartido
def ruta_del_interruptor() -> Path                               # compartido; lanza InterruptorSinConfigurar
def interruptor_activo(ruta: Path | str | None = None) -> bool   # compartido; None -> ruta_del_interruptor()
def _sincronizar_directorio(directorio: Path) -> None            # compartido
def escribir_pausa(ruta: Path, contenido: str) -> bool           # compartido; True = lo puso esta llamada
def borrar_pausa(ruta: Path) -> bool                             # compartido; True = lo quitó esta llamada
class InterruptorActivado(RuntimeError)                          # sólo jax
INTERVALO_DE_SONDEO = 0.25                                       # sólo jax
async def correr_con_interruptor(corrutina, *, intervalo: float = INTERVALO_DE_SONDEO)  # sólo jax
```

**Produce `jax-platform/backend/kill_switch.py`:**

```python
ACCIONES = frozenset({"activar", "reanudar"})
KILL_SWITCH_ACTIVO = "kill_switch_activo"
NO_ESCRIBIBLE = "kill_switch_no_escribible"
AUDITORIA_FALLIDA = "kill_switch_auditoria_fallida"
EVENTO_ACTIVADO = "kill_switch_activated"
EVENTO_LIBERADO = "kill_switch_released"
SQL_ULTIMO: str
RUTAS_FRENADAS = frozenset({("POST", "/api/chat"), ("POST", "/api/image/generate"), ("POST", "/api/command"),
                            ("POST", "/api/pipelines"), ("POST", "/api/pipelines/{pipeline_id}/resume")})
class InterruptorNoEscribible(RuntimeError)
class AuditoriaDelInterruptorFallida(RuntimeError)
def activo() -> bool
async def estado() -> dict        # {"activo": bool, "ultimo": {"accion", "user_id", "email", "at"} | None}
async def activar(usuario: AuthUser) -> dict   # {"activo": True, "cambio": bool}
async def reanudar(usuario: AuthUser) -> dict  # {"activo": False, "cambio": bool}
async def exigir_mesa_libre(user: AuthUser = Depends(get_current_user)) -> AuthUser
```

**Produce `EventBus`:** `async def publicar_a_todos(self, event_type: str, payload: dict) -> int`, que devuelve cuántos suscriptores lo recibieron.

**Rutas nuevas:**
- `GET /api/admin/kill-switch` → `estado()`;
- `POST /api/admin/kill-switch/activar` → `activar()`;
- `POST /api/admin/kill-switch/reanudar` → `reanudar()`.

`GET /api/state` suma `kill_switch_active: bool`.

**Frontend:**
- `useJaxStore`: `killSwitchActive` (ya existe), `activarKillSwitch(): Promise<void>` y `reanudarKillSwitch(): Promise<void>`, que lanzan si falla. `activateKillSwitch` se borra.
- `api/errores.js`: `textoDeKillSwitch(t, err) -> string | null`.
- Claves i18n nuevas: `killConfirmTitle`, `killConfirmMessage`, `killResumeButton`, `killResumeTitle`, `killResumeMessage`, `killResumeConfirm`, `killSwitchReleasedToast`, `killSwitchErrorActivar`, `killSwitchErrorReanudar`, `killSwitchErrorNoEscribible`, `killSwitchErrorAuditoria`, `kill_switch_activo`.
- Claves borradas: `killConfirm`, `killSwitchStoppedToast`.

---

### Task 1: jax · el interruptor canónico y la suite aislada

**Files:**
- Create: `/home/fruiz/worktrees/jax-frente-b/jax/core/interruptor.py`
- Create: `/home/fruiz/worktrees/jax-frente-b/las_manos/interruptor.py` (symlink)
- Modify: `/home/fruiz/worktrees/jax-frente-b/conftest.py` (después de la línea `os.environ["JAX_USAGE_SPOOL_DIR"] = ...`)
- Test: `/home/fruiz/worktrees/jax-frente-b/tests/test_interruptor.py`
- Modify: `/home/fruiz/worktrees/jax-frente-b/.github/workflows/policy.yml` (job `tests-puros`)

**Interfaces:**
- Consumes: nada.
- Produces: todo `jax/core/interruptor.py` (ver Interfaces).

- [ ] **Step 1: Escribir el test que falla** — `tests/test_interruptor.py`:

```python
"""El interruptor de JAX: dónde está el freno y si está puesto
(plan 2026-09-16-frente-b-kill-switch, Task 1).

Antes, cada lector tenía su ruta escrita a mano y miraba con `Path.exists()`,
que devuelve False ante un PermissionError: con el archivo puesto y el
directorio ilegible, el freno se leía SUELTO (medido, Python 3.14.4).

Corre con:
  PYTHONPATH=.:las_manos python -m pytest tests/test_interruptor.py -v

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from jax.core import interruptor

VARIABLE = "JAX_KILL_SWITCH_PATH"
ES_ROOT = os.geteuid() == 0


def test_sin_variable_no_hay_ruta(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with pytest.raises(interruptor.InterruptorSinConfigurar, match=VARIABLE):
        interruptor.ruta_del_interruptor()


def test_variable_vacia_es_lo_mismo_que_ausente(monkeypatch):
    monkeypatch.setenv(VARIABLE, "   ")
    with pytest.raises(interruptor.InterruptorSinConfigurar):
        interruptor.ruta_del_interruptor()


def test_ruta_relativa_se_rechaza(monkeypatch):
    monkeypatch.setenv(VARIABLE, "PAUSE")
    with pytest.raises(interruptor.InterruptorSinConfigurar, match="absoluta"):
        interruptor.ruta_del_interruptor()


def test_sin_variable_mirar_el_freno_tambien_lanza(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    with pytest.raises(interruptor.InterruptorSinConfigurar):
        interruptor.interruptor_activo()


def test_la_ruta_se_lee_en_cada_llamada(monkeypatch, tmp_path):
    monkeypatch.setenv(VARIABLE, str(tmp_path / "a" / "PAUSE"))
    assert interruptor.ruta_del_interruptor() == tmp_path / "a" / "PAUSE"
    monkeypatch.setenv(VARIABLE, str(tmp_path / "b" / "PAUSE"))
    assert interruptor.ruta_del_interruptor() == tmp_path / "b" / "PAUSE"


def test_suelto_si_el_archivo_no_existe(monkeypatch, tmp_path):
    monkeypatch.setenv(VARIABLE, str(tmp_path / "PAUSE"))
    assert interruptor.interruptor_activo() is False


def test_puesto_si_el_archivo_existe(monkeypatch, tmp_path):
    (tmp_path / "PAUSE").write_text("")
    monkeypatch.setenv(VARIABLE, str(tmp_path / "PAUSE"))
    assert interruptor.interruptor_activo() is True


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_directorio_ilegible_cuenta_como_puesto(monkeypatch, tmp_path):
    carpeta = tmp_path / "interruptor"
    carpeta.mkdir()
    (carpeta / "PAUSE").write_text("")
    monkeypatch.setenv(VARIABLE, str(carpeta / "PAUSE"))
    carpeta.chmod(0)
    try:
        # El defecto que se cierra: pathlib lo lee SUELTO.
        assert (carpeta / "PAUSE").exists() is False
        assert interruptor.interruptor_activo() is True
    finally:
        carpeta.chmod(0o700)


def test_un_componente_que_no_es_directorio_cuenta_como_puesto(monkeypatch, tmp_path):
    archivo = tmp_path / "archivo"
    archivo.write_text("")
    monkeypatch.setenv(VARIABLE, str(archivo / "PAUSE"))
    assert interruptor.interruptor_activo() is True


def test_escribir_pausa_publica_contenido_completo_y_no_pisa(tmp_path):
    ruta = tmp_path / "PAUSE"
    assert interruptor.escribir_pausa(ruta, '{"user_id": "7"}') is True
    assert ruta.read_text() == '{"user_id": "7"}'
    assert stat.S_IMODE(ruta.stat().st_mode) == 0o660
    assert interruptor.escribir_pausa(ruta, '{"user_id": "8"}') is False
    assert ruta.read_text() == '{"user_id": "7"}'
    assert [p.name for p in tmp_path.iterdir()] == ["PAUSE"]  # sin temporales


def test_borrar_pausa_es_idempotente(tmp_path):
    ruta = tmp_path / "PAUSE"
    interruptor.escribir_pausa(ruta, "{}")
    assert interruptor.borrar_pausa(ruta) is True
    assert not ruta.exists()
    assert interruptor.borrar_pausa(ruta) is False


def test_escribir_en_un_directorio_que_no_existe_lanza(tmp_path):
    with pytest.raises(FileNotFoundError):
        interruptor.escribir_pausa(tmp_path / "no-existe" / "PAUSE", "{}")


def test_correr_devuelve_el_resultado(monkeypatch, tmp_path):
    monkeypatch.setenv(VARIABLE, str(tmp_path / "PAUSE"))

    async def rapida():
        return 42

    assert asyncio.run(interruptor.correr_con_interruptor(rapida(), intervalo=0.01)) == 42


def test_correr_con_el_freno_puesto_no_arranca(monkeypatch, tmp_path):
    (tmp_path / "PAUSE").write_text("")
    monkeypatch.setenv(VARIABLE, str(tmp_path / "PAUSE"))
    arranco = []

    async def tarea():
        arranco.append(True)

    with pytest.raises(interruptor.InterruptorActivado, match="killed_by_switch"):
        asyncio.run(interruptor.correr_con_interruptor(tarea(), intervalo=0.01))
    assert arranco == []


def test_correr_cancela_lo_que_corre_cuando_aparece_el_freno(monkeypatch, tmp_path):
    ruta = tmp_path / "PAUSE"
    monkeypatch.setenv(VARIABLE, str(ruta))
    limpieza = []

    async def larga():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            # Lo que hace hyde_sandbox.run_sandboxed_claude: proc.kill().
            limpieza.append("cancelada")
            raise

    async def escenario():
        loop = asyncio.get_running_loop()

        async def poner():
            await asyncio.sleep(0.05)
            interruptor.escribir_pausa(ruta, "{}")

        loop.create_task(poner())
        inicio = loop.time()
        try:
            await interruptor.correr_con_interruptor(larga(), intervalo=0.01)
        except interruptor.InterruptorActivado as exc:
            return str(exc), loop.time() - inicio
        return None, loop.time() - inicio

    mensaje, duracion = asyncio.run(escenario())
    assert mensaje is not None and "killed_by_switch" in mensaje
    assert limpieza == ["cancelada"]
    assert duracion < 1.0


def test_el_conftest_aisla_la_ruta_de_produccion():
    assert not str(interruptor.ruta_del_interruptor()).startswith("/etc/jax")
```

- [ ] **Step 2: Correrlo y ver el rojo**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_interruptor.py -q`
Expected: ERROR de colección, `ImportError: cannot import name 'interruptor' from 'jax.core'`.

- [ ] **Step 3: Implementar el canónico** — `jax/core/interruptor.py`:

```python
"""El interruptor de JAX (kill switch): dónde está el freno y si está puesto.

FUENTE ÚNICA para los procesos que lo LEEN (LAS MANOS con Jacobs adentro, el
REPL y `jax --task`) y para el único que lo ESCRIBE (jax-platform, que tiene
una copia en `backend/interruptor.py`: familia `interruptor` de
scripts/check_mirror_sync.py).

Hasta el 2026-09-16 la ruta estaba escrita a mano en seis lugares (uno con un
default silencioso) y cada lector miraba con `Path.exists()`, que devuelve
False ante un PermissionError: con el archivo puesto y el directorio
ilegible, el freno se leía SUELTO (medido en Python 3.14.4).

Reglas:
- La ruta sale de JAX_KILL_SWITCH_PATH y se lee en CADA llamada. Sin la
  variable, vacía o relativa: `InterruptorSinConfigurar`. Sin saber dónde
  está el freno no se ejecuta nada.
- `interruptor_activo` usa os.stat: sólo "no existe" es SUELTO; cualquier
  otro error (permiso, ENOTDIR, E/S) es PUESTO.
- `escribir_pausa` publica el archivo completo de una vez (temporal +
  os.link, que falla si ya existe) y `borrar_pausa` lo quita; los dos
  sincronizan el directorio.
- `correr_con_interruptor` (sólo jax) corre una corrutina y la cancela si el
  freno aparece; `hyde_sandbox.run_sandboxed_claude` mata su proceso al
  recibir la cancelación.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

VARIABLE_RUTA = "JAX_KILL_SWITCH_PATH"


class InterruptorSinConfigurar(RuntimeError):
    """JAX_KILL_SWITCH_PATH falta, está vacía o no es una ruta absoluta."""


def ruta_del_interruptor() -> Path:
    valor = os.environ.get(VARIABLE_RUTA, "").strip()
    if not valor:
        raise InterruptorSinConfigurar(
            f"{VARIABLE_RUTA} no está definida: sin saber dónde está el freno "
            "no se ejecuta nada (se define en /etc/jax/.env)"
        )
    ruta = Path(valor)
    if not ruta.is_absolute():
        raise InterruptorSinConfigurar(
            f"{VARIABLE_RUTA} tiene que ser una ruta absoluta, no {valor!r}"
        )
    return ruta


def interruptor_activo(ruta: Path | str | None = None) -> bool:
    objetivo = Path(ruta) if ruta is not None else ruta_del_interruptor()
    try:
        os.stat(objetivo)
    except FileNotFoundError:
        return False
    except OSError:  # fail-closed: sin poder mirar el freno (permiso, ENOTDIR, E/S) se lo da por PUESTO
        return True
    return True


def _sincronizar_directorio(directorio: Path) -> None:
    descriptor = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def escribir_pausa(ruta: Path, contenido: str) -> bool:
    """Pone el freno. True si lo puso esta llamada; False si ya estaba."""
    directorio = ruta.parent
    descriptor, temporal = tempfile.mkstemp(prefix=".pausa-", dir=directorio)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.chmod(temporal, 0o660)
        try:
            os.link(temporal, ruta)
        except FileExistsError:
            return False
        _sincronizar_directorio(directorio)
        return True
    finally:
        os.unlink(temporal)


def borrar_pausa(ruta: Path) -> bool:
    """Quita el freno. True si lo quitó esta llamada; False si no estaba."""
    try:
        os.unlink(ruta)
    except FileNotFoundError:
        return False
    _sincronizar_directorio(ruta.parent)
    return True


class InterruptorActivado(RuntimeError):
    """El freno estaba puesto al empezar o apareció mientras la operación corría."""


INTERVALO_DE_SONDEO = 0.25  # segundos: el mismo del ssh_worker (CONTEXT.md §9, 2026-06-14)


async def correr_con_interruptor(corrutina, *, intervalo: float = INTERVALO_DE_SONDEO):
    ruta = ruta_del_interruptor()
    if interruptor_activo(ruta):
        corrutina.close()
        raise InterruptorActivado(f"killed_by_switch — {ruta} estaba puesto antes de empezar")
    tarea = asyncio.ensure_future(corrutina)
    try:
        while True:
            hechas, _ = await asyncio.wait({tarea}, timeout=intervalo)
            if hechas:
                return tarea.result()
            if interruptor_activo(ruta):
                tarea.cancel()
                await asyncio.gather(tarea, return_exceptions=True)
                raise InterruptorActivado(f"killed_by_switch — {ruta} apareció durante la ejecución")
    finally:
        if not tarea.done():
            tarea.cancel()
```

- [ ] **Step 4: Symlink y conftest**

Run: `cd /home/fruiz/worktrees/jax-frente-b/las_manos && pwd && ln -s ../jax/core/interruptor.py interruptor.py && ls -la interruptor.py`
Expected: `interruptor.py -> ../jax/core/interruptor.py`.

En `/home/fruiz/worktrees/jax-frente-b/conftest.py`, justo debajo de `os.environ["JAX_USAGE_SPOOL_DIR"] = tempfile.mkdtemp(prefix="jax-test-respaldo-uso-")`:

```python
# El kill switch, aislado por la misma razón (2026-09-16, frente B). Desde
# ese día la ruta del freno sale de JAX_KILL_SWITCH_PATH y /etc/jax/.env la
# define: un test que pusiera el freno sin esto detendría a JAX en producción.
# Asignación y no setdefault, a propósito.
os.environ["JAX_KILL_SWITCH_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="jax-test-interruptor-"), "PAUSE")
```

- [ ] **Step 5: Verde**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_interruptor.py -q`
Expected: `16 passed`.

Mutaciones (cada una tiene que poner algo en rojo; después se revierte):
- `except OSError: return True` → `return False`: cae `test_directorio_ilegible_cuenta_como_puesto` y `test_un_componente_que_no_es_directorio_cuenta_como_puesto`.
- Quitar el `if interruptor_activo(ruta)` del bucle: cae `test_correr_cancela_lo_que_corre_cuando_aparece_el_freno`.

- [ ] **Step 6: CI.** En `.github/workflows/policy.yml`, job `tests-puros`, agregar `tests/test_interruptor.py` en las DOS listas (la de `-v` y la del piso). Medir dos veces:

`cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest -q <la lista completa del job> | tail -1`

Reemplazar `494 passed, 1 skipped` por el número medido (se espera `510 passed, 1 skipped`). Comentario: `# 494 -> <N> el 2026-09-16 (frente B, Task 1): tests/test_interruptor.py (+16).`

- [ ] **Step 7: P10 de jax**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && /home/fruiz/jax/.venv/bin/python -m pytest policy/tests/test_no_fail_open_except.py -q`
Expected: verde.

- [ ] **Step 8: Commit**

```bash
git -C /home/fruiz/worktrees/jax-frente-b add jax/core/interruptor.py las_manos/interruptor.py conftest.py tests/test_interruptor.py .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-frente-b commit -m "feat(interruptor): ruta del freno desde JAX_KILL_SWITCH_PATH, lectura fail-closed y escritura atómica

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: jax-platform · copia espejada, suite aislada y arranque fail-closed (+ familia en jax)

**Files:**
- Create: `/home/fruiz/worktrees/jax-platform-frente-b/backend/interruptor.py`
- Modify: `/home/fruiz/worktrees/jax-platform-frente-b/backend/tests/conftest.py`
- Modify: `/home/fruiz/worktrees/jax-platform-frente-b/backend/main.py:9-15`
- Test: `/home/fruiz/worktrees/jax-platform-frente-b/backend/tests/test_interruptor_arranque.py`
- Modify: `/home/fruiz/worktrees/jax-frente-b/scripts/check_mirror_sync.py` (tupla `FAMILIAS`, después de la familia `cola_uso`)

**Interfaces:**
- Consumes: los símbolos compartidos de la Task 1.
- Produces: `backend/interruptor.py` (compartidos), la ruta temporal forzada en la suite de la plataforma, y la familia `interruptor`.

- [ ] **Step 1: Test que falla** — `backend/tests/test_interruptor_arranque.py`:

```python
"""La plataforma no arranca sin saber dónde está el freno (plan
2026-09-16-frente-b-kill-switch, Task 2). Puro: importa en un subproceso, no
arranca la app ni toca la base."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _importar_main(entorno):
    return subprocess.run(
        [sys.executable, "-c", "import main"], cwd=BACKEND, env=entorno,
        capture_output=True, text=True, timeout=120)


def test_la_suite_no_mira_el_freno_de_produccion():
    import interruptor
    assert not str(interruptor.ruta_del_interruptor()).startswith("/etc/jax")


def test_sin_la_variable_la_plataforma_no_arranca():
    entorno = {k: v for k, v in os.environ.items() if k != "JAX_KILL_SWITCH_PATH"}
    r = _importar_main(entorno)
    assert r.returncode != 0
    assert "JAX_KILL_SWITCH_PATH" in r.stderr


def test_con_la_variable_el_import_pasa():
    r = _importar_main(os.environ.copy())
    assert r.returncode == 0, r.stderr[-2000:]
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && .venv/bin/python -m pytest tests/test_interruptor_arranque.py -q` (si el worktree no tiene `.venv`, usar `/home/fruiz/jax-platform/backend/.venv/bin/python`).
Expected: FAIL. `test_la_suite_no_mira...` cae por `ModuleNotFoundError: interruptor`, y `test_sin_la_variable...` cae porque `import main` devuelve 0.

- [ ] **Step 3: La copia** — `backend/interruptor.py`, con los compartidos IDÉNTICOS a la Task 1:

```python
"""El interruptor de JAX (kill switch), copia de jax-platform.

Espejo de `jax/core/interruptor.py` (familia `interruptor` de
scripts/check_mirror_sync.py en el repo jax). La plataforma es el único
ESCRITOR del freno; LAS MANOS, Jacobs y el REPL lo leen con el canónico. Los
dos lados tienen que hablar del MISMO archivo con la MISMA semántica: sólo
"no existe" es SUELTO. `correr_con_interruptor` no está acá: es del REPL y
de Jacobs.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

VARIABLE_RUTA = "JAX_KILL_SWITCH_PATH"


class InterruptorSinConfigurar(RuntimeError):
    """JAX_KILL_SWITCH_PATH falta, está vacía o no es una ruta absoluta."""


def ruta_del_interruptor() -> Path:
    valor = os.environ.get(VARIABLE_RUTA, "").strip()
    if not valor:
        raise InterruptorSinConfigurar(
            f"{VARIABLE_RUTA} no está definida: sin saber dónde está el freno "
            "no se ejecuta nada (se define en /etc/jax/.env)"
        )
    ruta = Path(valor)
    if not ruta.is_absolute():
        raise InterruptorSinConfigurar(
            f"{VARIABLE_RUTA} tiene que ser una ruta absoluta, no {valor!r}"
        )
    return ruta


def interruptor_activo(ruta: Path | str | None = None) -> bool:
    objetivo = Path(ruta) if ruta is not None else ruta_del_interruptor()
    try:
        os.stat(objetivo)
    except FileNotFoundError:
        return False
    except OSError:  # fail-closed: sin poder mirar el freno (permiso, ENOTDIR, E/S) se lo da por PUESTO
        return True
    return True


def _sincronizar_directorio(directorio: Path) -> None:
    descriptor = os.open(directorio, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def escribir_pausa(ruta: Path, contenido: str) -> bool:
    """Pone el freno. True si lo puso esta llamada; False si ya estaba."""
    directorio = ruta.parent
    descriptor, temporal = tempfile.mkstemp(prefix=".pausa-", dir=directorio)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.chmod(temporal, 0o660)
        try:
            os.link(temporal, ruta)
        except FileExistsError:
            return False
        _sincronizar_directorio(directorio)
        return True
    finally:
        os.unlink(temporal)


def borrar_pausa(ruta: Path) -> bool:
    """Quita el freno. True si lo quitó esta llamada; False si no estaba."""
    try:
        os.unlink(ruta)
    except FileNotFoundError:
        return False
    _sincronizar_directorio(ruta.parent)
    return True
```

- [ ] **Step 4: Arranque fail-closed.** En `backend/main.py`, ANTES del comentario `# Debe correr antes de importar cualquier router: systemd carga` (línea 9):

```python
# El freno primero (2026-09-16, frente B): sin JAX_KILL_SWITCH_PATH la
# plataforma no sabe dónde escribir ni dónde mirar el kill switch, y una Mesa
# sin freno no arranca. Lanza InterruptorSinConfigurar y uvicorn sale con
# error: systemd lo muestra en el journal.
import interruptor
interruptor.ruta_del_interruptor()

```

- [ ] **Step 5: Conftest.** En `backend/tests/conftest.py`:

(a) Debajo de `os.environ["JAX_USAGE_SPOOL_DIR"] = tempfile.mkdtemp(prefix="jax-test-respaldo-uso-")`:

```python
# Kill switch aislado para TODA la sesión (2026-09-16, frente B), por la misma
# razón que el sello y el respaldo: /etc/jax/.env define JAX_KILL_SWITCH_PATH
# y el setdefault de arriba la cargaría. Un test que active el freno contra
# esa ruta detendría LAS MANOS, Jacobs y el REPL de producción. Asignación,
# no setdefault. main.py exige la variable al importarse.
RUTA_DEL_FRENO_DE_PRUEBA = os.path.join(
    tempfile.mkdtemp(prefix="jax-test-interruptor-"), "PAUSE")
os.environ["JAX_KILL_SWITCH_PATH"] = RUTA_DEL_FRENO_DE_PRUEBA
FRENO_DE_PRODUCCION = "/etc/jax/interruptor"
import time as _time  # noqa: E402
INICIO_DE_SESION = _time.time()
```

(b) Al final del archivo:

```python
@pytest.fixture(autouse=True)
def _freno_suelto_entre_tests():
    """Ningún test hereda el freno puesto por otro. Estructural, como el
    aislamiento del sello."""
    yield
    try:
        os.unlink(RUTA_DEL_FRENO_DE_PRUEBA)
    except FileNotFoundError:  # fail-soft: el test no puso el freno; no hay nada que quitar
        pass


def pytest_sessionfinish(session, exitstatus):
    """Barrera verificable: si durante la sesión apareció o cambió algo en el
    directorio del freno de producción, la corrida falla. En el runner no
    existe (0 escrituras, no un error)."""
    try:
        cambios = [p.name for p in os.scandir(FRENO_DE_PRODUCCION)
                   if p.stat(follow_symlinks=False).st_mtime >= INICIO_DE_SESION]
    except FileNotFoundError:
        return
    if cambios:
        print(f"\nBARRERA DEL KILL SWITCH: la suite tocó {FRENO_DE_PRODUCCION}: {cambios}",
              file=__import__("sys").stderr)
        session.exitstatus = 1
```

- [ ] **Step 6: Verde, y también con el entorno del runner**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_interruptor_arranque.py -q`
Expected: `3 passed`.

Run (reproduce `backend-tests-no-db`, sin `/etc/jax/.env` en el proceso): `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && env -i PATH=/usr/bin:/bin HOME=/tmp/ci-home JAX_JWT_SECRET=ci-dummy-not-a-real-secret JAX_CI_NO_DB=1 JAX_REPO_PATH=/tmp/jax /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_interruptor_arranque.py -q`
Expected: `3 passed`. El conftest igual lee `/etc/jax/.env` con `setdefault`, pero lo pisa con la ruta temporal. Si `test_con_la_variable_el_import_pasa` cae por otra variable, PARAR y reportar el stderr: no se ajusta el test a ciegas.

Mutación: sacar las dos líneas de `main.py` → cae `test_sin_la_variable_la_plataforma_no_arranca`.

- [ ] **Step 7: La familia de espejos (jax, en esta misma tarea).** En `/home/fruiz/worktrees/jax-frente-b/scripts/check_mirror_sync.py`, dentro de `FAMILIAS` y después de la `Familia(nombre="cola_uso", ...)`:

```python
    Familia(
        nombre="interruptor",
        canonico=JAX_ROOT / "jax" / "core" / "interruptor.py",
        espejos=(
            # Symlink a jax/core (frente B, 2026-09-16), como cola_uso: comparar
            # hoy es un no-op y se incluye igual.
            ("las_manos", JAX_ROOT / "las_manos" / "interruptor.py"),
            ("jax-platform", JAX_PLATFORM_ROOT / "backend" / "interruptor.py"),
        ),
        # El que ESCRIBE el freno (la plataforma) y los que lo LEEN (LAS MANOS,
        # Jacobs, el REPL) tienen que hablar del mismo archivo con la misma
        # semantica: solo ENOENT es suelto. Un drift aca no se ve como error: se
        # ve como un boton que dice "detenido" mientras las manos siguen.
        # `InterruptorActivado`, `INTERVALO_DE_SONDEO` y `correr_con_interruptor`
        # quedan afuera: son solo de jax (asyncio del REPL y de Jacobs).
        compartidos=(
            "VARIABLE_RUTA",
            "InterruptorSinConfigurar",
            "ruta_del_interruptor",
            "interruptor_activo",
            "_sincronizar_directorio",
            "escribir_pausa",
            "borrar_pausa",
        ),
    ),
```

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && JAX_PLATFORM_REPO_ROOT=/home/fruiz/worktrees/jax-platform-frente-b /home/fruiz/jax/.venv/bin/python scripts/check_mirror_sync.py; echo rc=$?`
Expected: `rc=0`.

Mutación: cambiar `return True` por `return False` en el `except OSError` de la COPIA de la plataforma → `rc=1` y el reporte nombra `interruptor_activo`. Revertir.

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && /home/fruiz/jax/.venv/bin/python -m pytest scripts/_check_mirror_sync_test.py -q | tail -1`
Expected: `14 passed` (el piso del job no cambia).

- [ ] **Step 8: Commits (uno por repo)**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-b add backend/interruptor.py backend/main.py backend/tests/conftest.py backend/tests/test_interruptor_arranque.py
git -C /home/fruiz/worktrees/jax-platform-frente-b commit -m "feat(interruptor): copia espejada, suite aislada y arranque fail-closed sin JAX_KILL_SWITCH_PATH

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git -C /home/fruiz/worktrees/jax-frente-b add scripts/check_mirror_sync.py
git -C /home/fruiz/worktrees/jax-frente-b commit -m "feat(mirror-sync): familia interruptor

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: jax · LAS MANOS lee el interruptor (+ peor caso del motor en vuelo)

**Files:**
- Modify: `las_manos/server.py` (docstring línea 8; líneas 76-84; 208-209; 399), `las_manos/workers/ssh_worker.py:9,34-127`, `las_manos/motor_registry/routes.py:54,205`, `las_manos/motor_registry/worker.py:6,16,95-101,440-446,690-700`, `las_manos/config.toml:17`, `las_manos/motor_registry/catalog.py:189`
- Test: `tests/test_interruptor_lectores.py`, `tests/test_interruptor_escritor_de_la_plataforma.py`
- Modify: `.github/workflows/policy.yml` (`tests-puros`, `mirror-sync`)

(Todas las rutas son relativas a `/home/fruiz/worktrees/jax-frente-b`.)

**Interfaces:**
- Consumes: `interruptor.ruta_del_interruptor`, `interruptor.interruptor_activo` y `interruptor.escribir_pausa` (import pelado, vía el symlink de la Task 1).
- Produces:
  - `ssh_worker.ssh_exec(host, command, dry_run=False, timeout=120.0, kill_switch_path: str | Path | None = None)`: `None` = la ruta de la variable.
  - `worker.run(..., kill_switch_path: str, ...)` con la firma sin cambios.
  - El job cancelado con el freno puesto queda `failed` con `killed_by_switch`.

- [ ] **Step 1: Tests que fallan** — `tests/test_interruptor_lectores.py`:

```python
"""LAS MANOS lee el freno desde JAX_KILL_SWITCH_PATH y falla cerrado (plan
2026-09-16-frente-b-kill-switch, Task 3). Sólo se mockea el borde de red (el
transporte), la credencial y la escritura de costo; JobStore y MotorCatalog
corren de verdad contra un JSONL temporal.

Corre con:
  PYTHONPATH=.:las_manos python -m pytest tests/test_interruptor_lectores.py -v

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import ast
import asyncio
import os
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import interruptor
from motor_registry import worker
from motor_registry.catalog import MotorCatalog
from motor_registry.job_store import JobStore
from motor_registry.models import JobStatus
from workers import ssh_worker

RAIZ = Path(__file__).resolve().parents[1]
ES_ROOT = os.geteuid() == 0

_CAP = {
    "allowed_motors": ["kimi"], "allowed_callers": ["jacobs"], "risk_level": "low",
    "sandbox_only": True, "requires_human_gate": False, "max_execution_minutes": 15,
    "max_recursion_depth": 0, "output_schema": "",
}
_CFG = {
    "motors": {
        "kimi": {
            "enabled": True, "provider": "kimi", "transport": "http_openai_compat",
            "provider_id": "moonshot", "api_key_env": "KIMI_API_KEY",
            "api_url": "https://api.moonshot.ai/v1/chat/completions", "model": "kimi-k3",
            "max_context_tokens": 256000, "sandbox_only": True, "default_timeout_seconds": 600,
            "supports_reasoning": True, "reasoning_default_visibility": "audit_only",
            "max_tokens": 8000,
        },
    },
    "capabilities": {"implementation": _CAP},
}


async def _job(tmp_path, ruta_freno, transporte, durante=None):
    """Corre worker.run con `transporte`; `durante(tarea)` actúa mientras corre."""
    store = JobStore(str(tmp_path / "jobs.jsonl"))
    job_id = store.create(caller="jacobs", capability="implementation", motor="kimi",
                          trace_id="t", prompt="p", recursion_depth=0)
    parches = [
        patch.object(worker, "resolve_credential_instrumented", AsyncMock(return_value="sk-fake")),
        patch("contrato_dispatch._leer_contrato", AsyncMock(return_value=("max_tokens", 131072))),
        patch("motor_registry.usage_writer.record_motor_usage", AsyncMock()),
        patch("httpx.AsyncClient.post", AsyncMock(side_effect=AssertionError("llamada de red real en un test"))),
        patch.dict(worker._TRANSPORT_DISPATCH, {"http_openai_compat": transporte}),
    ]
    for p in parches:
        p.start()
    try:
        tarea = asyncio.create_task(worker.run(
            job_id=job_id, motor="kimi", capability="implementation", prompt="p",
            context={}, store=store, catalog=MotorCatalog(_CFG), kill_switch_path=str(ruta_freno)))
        if durante is not None:
            await durante(tarea)
        await asyncio.wait_for(asyncio.gather(tarea, return_exceptions=True), timeout=3.0)
    finally:
        for p in reversed(parches):
            p.stop()
    return store._index[job_id]


def _transporte_lento(en_vuelo, cancelado):
    async def transporte(**kwargs):
        en_vuelo.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelado.append(True)
            raise
    return transporte


def test_motor_en_vuelo_queda_killed_by_switch(tmp_path, monkeypatch):
    """EL PEOR CASO del spec, del lado de LAS MANOS: el modelo está
    respondiendo, aparece el freno y el job termina killed_by_switch."""
    ruta = tmp_path / "interruptor" / "PAUSE"
    ruta.parent.mkdir()
    monkeypatch.setattr(worker, "_KILL_SWITCH_INTERVAL", 0.05)
    cancelado = []

    async def escenario():
        en_vuelo = asyncio.Event()

        async def durante(tarea):
            await asyncio.wait_for(en_vuelo.wait(), 2.0)
            interruptor.escribir_pausa(ruta, '{"user_id": "peor-caso"}')

        return await _job(tmp_path, ruta, _transporte_lento(en_vuelo, cancelado), durante)

    estado = asyncio.run(escenario())
    assert estado["status"] == JobStatus.FAILED.value
    assert "killed_by_switch" in estado["error"]
    assert cancelado == [True]


def test_motor_cancelado_con_el_freno_puesto_queda_killed_by_switch(tmp_path, monkeypatch):
    """Jacobs corta el step a los 250 ms y cancela el job ANTES del watcher de
    5 s: sin esto el job quedaba 'cancelado externamente' y el freno no
    aparecía como la causa."""
    ruta = tmp_path / "PAUSE"
    monkeypatch.setattr(worker, "_KILL_SWITCH_INTERVAL", 30.0)
    cancelado = []

    async def escenario():
        en_vuelo = asyncio.Event()

        async def durante(tarea):
            await asyncio.wait_for(en_vuelo.wait(), 2.0)
            interruptor.escribir_pausa(ruta, "{}")
            tarea.cancel()

        return await _job(tmp_path, ruta, _transporte_lento(en_vuelo, cancelado), durante)

    estado = asyncio.run(escenario())
    assert estado["status"] == JobStatus.FAILED.value
    assert "killed_by_switch" in estado["error"]


def test_motor_cancelado_sin_freno_sigue_siendo_cancelado(tmp_path, monkeypatch):
    ruta = tmp_path / "PAUSE"
    monkeypatch.setattr(worker, "_KILL_SWITCH_INTERVAL", 30.0)

    async def escenario():
        en_vuelo = asyncio.Event()

        async def durante(tarea):
            await asyncio.wait_for(en_vuelo.wait(), 2.0)
            tarea.cancel()

        return await _job(tmp_path, ruta, _transporte_lento(en_vuelo, []), durante)

    estado = asyncio.run(escenario())
    assert estado["status"] == JobStatus.CANCELLED.value
    assert estado["error"] == "Job cancelado externamente"


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_motor_con_directorio_ilegible_no_llama_al_modelo(tmp_path):
    carpeta = tmp_path / "interruptor"
    carpeta.mkdir()
    (carpeta / "PAUSE").write_text("")
    llamadas = []

    async def transporte(**kwargs):
        llamadas.append(kwargs)
        return {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}], "usage": {}}

    carpeta.chmod(0)
    try:
        estado = asyncio.run(_job(tmp_path, carpeta / "PAUSE", transporte))
    finally:
        carpeta.chmod(0o700)
    assert estado["status"] == JobStatus.FAILED.value
    assert "killed_by_switch" in estado["error"]
    assert llamadas == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_watcher_ssh_con_directorio_ilegible_mata_el_proceso(tmp_path, monkeypatch):
    carpeta = tmp_path / "interruptor"
    carpeta.mkdir()
    (carpeta / "PAUSE").write_text("")
    monkeypatch.setattr(ssh_worker, "POLL_INTERVAL", 0.01)

    async def escenario():
        proc = await asyncio.create_subprocess_exec("sleep", "30")
        abortado = {"flag": False}
        carpeta.chmod(0)
        try:
            await asyncio.wait_for(
                ssh_worker._kill_switch_watcher(proc, str(carpeta / "PAUSE"), abortado), 2.0)
        finally:
            carpeta.chmod(0o700)
        await asyncio.wait_for(proc.wait(), 2.0)
        return abortado["flag"], proc.returncode

    flag, codigo = asyncio.run(escenario())
    assert flag is True
    assert codigo is not None


def test_ssh_exec_sin_variable_no_ejecuta(monkeypatch):
    monkeypatch.delenv("JAX_KILL_SWITCH_PATH", raising=False)
    lanzar = AsyncMock(side_effect=AssertionError("no debía lanzar ssh"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", lanzar)
    with pytest.raises(interruptor.InterruptorSinConfigurar):
        asyncio.run(ssh_worker.ssh_exec("127.0.0.1", "true"))
    lanzar.assert_not_called()


def test_ssh_worker_no_guarda_una_ruta_global():
    assert not hasattr(ssh_worker, "KILL_SWITCH_PATH")


def _fuente(relativa):
    return (RAIZ / relativa).read_text(encoding="utf-8")


def test_server_resuelve_la_ruta_al_importarse_antes_de_leer_config():
    fuente = _fuente("las_manos/server.py")
    arbol = ast.parse(fuente)
    asignacion = next(
        n for n in arbol.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "KILL_SWITCH" for t in n.targets))
    assert ast.unparse(asignacion.value) == "ruta_del_interruptor()"
    lectura_config = next(n for n in arbol.body if isinstance(n, ast.With))
    assert asignacion.lineno < lectura_config.lineno
    assert "kill_switch_path" not in fuente
    assert "ssh_worker.KILL_SWITCH_PATH" not in fuente


def test_routes_resuelve_la_ruta_en_cada_dispatch():
    fuente = _fuente("las_manos/motor_registry/routes.py")
    assert "_KILL_SWITCH_PATH" not in fuente
    valores = [ast.unparse(k.value) for k in ast.walk(ast.parse(fuente))
               if isinstance(k, ast.keyword) and k.arg == "kill_switch_path"]
    assert valores == ["str(ruta_del_interruptor())"]


def test_config_toml_de_las_manos_sin_ruta():
    with open(RAIZ / "las_manos" / "config.toml", "rb") as f:
        assert "kill_switch_path" not in tomllib.load(f)["server"]
```

Y `tests/test_interruptor_escritor_de_la_plataforma.py` (lo corre el job `mirror-sync`, que tiene el checkout de la plataforma):

```python
"""El freno que ESCRIBE jax-platform es el que LEE LAS MANOS (plan
2026-09-16-frente-b-kill-switch, Task 3): el peor caso del motor en vuelo con
el escritor real de la plataforma, cargado desde su checkout. En CI lo corre
`mirror-sync` con JAX_PLATFORM_REPO_ROOT y piso de 0 skipped.

Corre con:
  JAX_PLATFORM_REPO_ROOT=<checkout> PYTHONPATH=.:las_manos python -m pytest tests/test_interruptor_escritor_de_la_plataforma.py -v
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from motor_registry import worker
from motor_registry.models import JobStatus
from tests.test_interruptor_lectores import _job, _transporte_lento

RAIZ = Path(__file__).resolve().parents[1]
RAIZ_PLATAFORMA = Path(os.environ.get("JAX_PLATFORM_REPO_ROOT", Path.home() / "jax-platform"))
COPIA = RAIZ_PLATAFORMA / "backend" / "interruptor.py"

pytestmark = pytest.mark.skipif(
    not COPIA.is_file(),
    reason=f"sin {COPIA} (seteá JAX_PLATFORM_REPO_ROOT); en CI lo cubre el job mirror-sync")


def _escritor_de_la_plataforma():
    spec = importlib.util.spec_from_file_location("interruptor_de_la_plataforma", COPIA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_copia_es_identica_al_canonico():
    sys.path.insert(0, str(RAIZ / "scripts"))
    from check_mirror_sync import FAMILIAS, revisar  # noqa: E402

    familia = next(f for f in FAMILIAS if f.nombre == "interruptor")
    drift, _declaradas, faltantes = revisar(familia)
    assert (drift, faltantes) == ([], [])


def test_el_freno_de_la_plataforma_mata_el_motor_en_vuelo(tmp_path, monkeypatch):
    plataforma = _escritor_de_la_plataforma()
    ruta = tmp_path / "PAUSE"
    monkeypatch.setattr(worker, "_KILL_SWITCH_INTERVAL", 0.05)
    cancelado = []

    async def escenario():
        en_vuelo = asyncio.Event()

        async def durante(tarea):
            await asyncio.wait_for(en_vuelo.wait(), 2.0)
            assert plataforma.escribir_pausa(ruta, '{"user_id": "plataforma"}') is True

        return await _job(tmp_path, ruta, _transporte_lento(en_vuelo, cancelado), durante)

    estado = asyncio.run(escenario())
    assert estado["status"] == JobStatus.FAILED.value
    assert "killed_by_switch" in estado["error"]
    assert cancelado == [True]
```

(Si `tests/` no es importable como paquete, es decir `from tests.test_interruptor_lectores import ...` da `ModuleNotFoundError`, verificarlo primero con `ls tests/__init__.py`. En ese caso mover `_job` y `_transporte_lento` a `tests/_motor_en_vuelo.py` e importarlos de ahí en los dos archivos, con `sys.path.insert(0, str(RAIZ / "tests"))`.)

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_interruptor_lectores.py -q`
Expected, cayendo contra el código viejo:
- `test_motor_cancelado_con_el_freno_puesto...`: status `cancelled`;
- `test_motor_con_directorio_ilegible...` y `test_watcher_ssh_con_directorio_ilegible...`: `Path.exists` lee suelto;
- `test_ssh_exec_sin_variable...`: no lanza;
- `test_ssh_worker_no_guarda...`;
- los tres de fuente.

`test_motor_en_vuelo_queda_killed_by_switch` y `test_motor_cancelado_sin_freno...` PASAN: son controles, y se validan por mutación en el Step 5.

- [ ] **Step 3: Implementar**

`las_manos/server.py`:
- Línea 8 del docstring: `    1. Kill switch  — ¿existe el archivo de JAX_KILL_SWITCH_PATH? Si sí, nada se ejecuta.`
- Debajo de `from workers import ssh_worker, file_worker, rsync_worker`:

```python
from interruptor import interruptor_activo, ruta_del_interruptor

# El freno ANTES de cualquier otra configuración (2026-09-16, frente B): sin
# JAX_KILL_SWITCH_PATH, LAS MANOS no arrancan (InterruptorSinConfigurar).
KILL_SWITCH = ruta_del_interruptor()
```

- Borrar `KILL_SWITCH = Path(SERVER_CFG["kill_switch_path"])` y el bloque:

```python
# El freno en la carretera: el ssh_worker vigila este path durante CADA
# ejecución y aborta en vuelo si aparece. Lo configuramos al arrancar.
ssh_worker.KILL_SWITCH_PATH = str(KILL_SWITCH)
```

- `_kill_switch_active` queda así (el nombre no cambia: lo fija `tests/test_health_de_las_manos_puede_fallar.py:141`):

```python
def _kill_switch_active() -> bool:
    return interruptor_activo(KILL_SWITCH)
```

- Línea 399: `detail="KILL SWITCH ACTIVO — LAS MANOS están detenidas",`

`las_manos/workers/ssh_worker.py`:
- Docstring línea 9: `cada POLL_INTERVAL segundos. Si el archivo de JAX_KILL_SWITCH_PATH aparece a mitad de la`.
- Borrar `KILL_SWITCH_PATH: str | None = None`, su comentario, `_UNSET` y su comentario.
- Import: `from interruptor import interruptor_activo, ruta_del_interruptor`.
- Watcher:

```python
async def _kill_switch_watcher(proc, kill_switch_path: str, aborted: dict) -> None:
    """Sondea el kill switch mientras el proceso corre. Si aparece (o no se lo
    puede mirar: interruptor_activo falla cerrado), mata."""
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        if proc.returncode is not None:
            return  # el proceso ya terminó solo
        if interruptor_activo(kill_switch_path):
            aborted["flag"] = True
            try:
                proc.kill()  # SIGKILL al cliente ssh → SIGHUP al remoto (pty)
            except ProcessLookupError:  # fail-soft: kill() sobre un proceso que ya pudo haber terminado solo entre el chequeo y el kill (TOCTOU benigno)
                pass
            return
```

- Firma y comienzo de `ssh_exec`:

```python
async def ssh_exec(
    host: str,
    command: str,
    dry_run: bool = False,
    timeout: float = 120.0,
    kill_switch_path: str | Path | None = None,
) -> dict:
    """Ejecuta un comando vía SSH. Devuelve dict con resultado.

    Un watcher concurrente vigila el freno (por defecto, el de
    JAX_KILL_SWITCH_PATH) y aborta la operación en vuelo si aparece. Sin la
    variable lanza InterruptorSinConfigurar antes de ejecutar nada.
    """
    if kill_switch_path is None:
        kill_switch_path = ruta_del_interruptor()
```

- El watcher va SIEMPRE: reemplazar `watcher = None` / `if kill_switch_path:` / `watcher = asyncio.create_task(...)` por `watcher = asyncio.create_task(_kill_switch_watcher(proc, str(kill_switch_path), aborted))`. En el `finally`, `watcher.cancel()` sin el `if`.

`las_manos/motor_registry/routes.py`:
- Borrar la línea 54 `_KILL_SWITCH_PATH: str = ...`.
- Agregar `from interruptor import ruta_del_interruptor` junto a los demás imports.
- En el `motor_worker.run(...)`: `kill_switch_path=str(ruta_del_interruptor()),`

`las_manos/motor_registry/worker.py`:
- Docstring: línea 6 `  2. Verifica el kill switch (archivo de JAX_KILL_SWITCH_PATH) antes de llamar`; línea 16 `Kill switch: si el archivo existe (o no se lo puede mirar) antes o durante → FAILED con error "killed_by_switch".`
- Import `from interruptor import interruptor_activo`.
- `_watch_kill_switch`: `if interruptor_activo(path):`.
- Pre-chequeo: `if interruptor_activo(kill_switch_path):`.
- Correr `grep -n "CANCELLED" las_manos/motor_registry/worker.py`. En CADA bloque `except asyncio.CancelledError:` que marca `JobStatus.CANCELLED` (hoy `:690-700`), reemplazar el `store.update(...)` y el `_report_usage("cancelled")` por:

```python
            if interruptor_activo(kill_switch_path):
                # Jacobs corta el step con el freno puesto y cancela el job
                # antes del watcher de 5 s: la causa real es el freno.
                store.update(
                    job_id,
                    status=JobStatus.FAILED.value,
                    finished_at=time.time(),
                    error="killed_by_switch — PAUSE detectado al cancelar el job",
                )
                await _report_usage("failed")
            else:
                store.update(
                    job_id,
                    status=JobStatus.CANCELLED.value,
                    finished_at=time.time(),
                    error="Job cancelado externamente",
                )
                await _report_usage("cancelled")
```

(Si el bloque escribe `_files_written` o notifica escrituras, se conserva igual en las dos ramas.)

`las_manos/config.toml`: borrar la línea `kill_switch_path = "/etc/jax/PAUSE"`. `las_manos/motor_registry/catalog.py:189`: `(TOML queda solo para [server] y lo que routes.py` (sin `kill_switch_path`).

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_interruptor_lectores.py tests/test_health_de_las_manos_puede_fallar.py tests/test_motor_job_cancel_and_length.py tests/test_motor_contexto_y_salida.py tests/test_motor_contrato_dispatch.py las_manos/_worker_tool_loop_test.py las_manos/_worker_max_tokens_test.py -q | tail -2`
Expected: todo verde (`test_interruptor_lectores.py`: 10 passed).

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && JAX_PLATFORM_REPO_ROOT=/home/fruiz/worktrees/jax-platform-frente-b PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_interruptor_escritor_de_la_plataforma.py -q -rs`
Expected: `2 passed`, 0 skipped.

- [ ] **Step 5: Mutaciones de los controles** (revertir cada una):
- En `_watch_kill_switch`, `if interruptor_activo(path): return` → `if False: return`: cae `test_motor_en_vuelo_queda_killed_by_switch` (timeout de 3 s) y el de la plataforma.
- En el `CancelledError`, forzar la rama `else`: cae `test_motor_cancelado_con_el_freno_puesto...`.

- [ ] **Step 6: CI.**
- `tests-puros`: agregar `tests/test_interruptor_lectores.py` en las dos listas y subir el piso con el número medido dos veces (+10).
- `mirror-sync`: después del paso `Piso exacto de tests CORRIDOS (0 skipped, con el checkout presente)`, agregar:

```yaml
      - name: El freno que escribe la plataforma mata el motor en vuelo
        env:
          JAX_PLATFORM_REPO_ROOT: /tmp/jax-platform
          PYTHONPATH: .:las_manos
        run: |
          pip install pytest-asyncio httpx cryptography pydantic pyyaml aiofiles fastapi==0.139.0
          python -m pytest tests/test_interruptor_escritor_de_la_plataforma.py -q -rs 2>&1 | tee /tmp/ks
          # 2026-09-16 (frente B, Task 3): la copia identica + el peor caso con el
          # escritor real de la plataforma. Sin el checkout se saltan: se exige 0 skipped.
          grep -qE "^2 passed in" /tmp/ks || {
            echo "PISO ROTO: se esperaban 2 tests CORRIDOS y 0 skipped."; exit 1; }
```

- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/worktrees/jax-frente-b add las_manos/server.py las_manos/workers/ssh_worker.py las_manos/motor_registry/routes.py las_manos/motor_registry/worker.py las_manos/config.toml las_manos/motor_registry/catalog.py tests/test_interruptor_lectores.py tests/test_interruptor_escritor_de_la_plataforma.py .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-frente-b commit -m "feat(las-manos): el freno sale de JAX_KILL_SWITCH_PATH, falla cerrado y el job cancelado con freno queda killed_by_switch

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: jax · Jacobs corta cada step en vuelo; relanzador y script manual

**Files:**
- Modify: `jacobs/policy.py:10-29`, `jacobs/executor.py` (import cerca de la línea 38 y `_run_one_step`), `tools/jacobs_relaunch.py:42-44`, `scripts/manual_motor_v02_integration.py:4,38,223-279`
- Test: `tests/test_jacobs_interruptor.py`
- Modify: `.github/workflows/policy.yml` (`tests-puros`)

**Interfaces:**
- Consumes: `interruptor.interruptor_activo`, `interruptor.correr_con_interruptor`, `interruptor.InterruptorSinConfigurar` y `interruptor.escribir_pausa`.
- Produces: `jacobs.policy.check_kill_switch() -> bool`, con la misma firma pero sin constante de ruta y lanzando error si falta la variable.

- [ ] **Step 1: Test que falla** — `tests/test_jacobs_interruptor.py`:

```python
"""Jacobs y el freno (plan 2026-09-16-frente-b-kill-switch, Task 4).

Antes: la ruta era una constante de policy.py, se miraba con Path.exists()
(suelto si el directorio es ilegible) y un step ya lanzado seguía hasta
terminar la ola: un Hyde en vuelo no se cortaba. Nada sale a la red ni a la
DB: store y el despacho se parchean.

Corre con:
  PYTHONPATH=.:las_manos python -m pytest tests/test_jacobs_interruptor.py -v

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["JAX_DB_NAME"] = "jax_memory_test"

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import interruptor  # noqa: E402
from jacobs import executor, policy, routes  # noqa: E402

ES_ROOT = os.geteuid() == 0
_NO_PLANIFICAR = AsyncMock(side_effect=AssertionError("el test no debe llegar a planificar"))


def test_policy_no_guarda_una_ruta_propia():
    assert not hasattr(policy, "KILL_SWITCH_PATH")


def test_check_kill_switch_sigue_la_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("JAX_KILL_SWITCH_PATH", str(tmp_path / "PAUSE"))
    assert policy.check_kill_switch() is False
    (tmp_path / "PAUSE").write_text("")
    assert policy.check_kill_switch() is True


def test_check_kill_switch_sin_variable_falla_cerrado(monkeypatch):
    monkeypatch.delenv("JAX_KILL_SWITCH_PATH", raising=False)
    with pytest.raises(interruptor.InterruptorSinConfigurar):
        policy.check_kill_switch()


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_check_kill_switch_con_directorio_ilegible_esta_puesto(tmp_path, monkeypatch):
    carpeta = tmp_path / "interruptor"
    carpeta.mkdir()
    (carpeta / "PAUSE").write_text("")
    monkeypatch.setenv("JAX_KILL_SWITCH_PATH", str(carpeta / "PAUSE"))
    carpeta.chmod(0)
    try:
        assert policy.check_kill_switch() is True
    finally:
        carpeta.chmod(0o700)


def test_validate_create_con_freno_puesto_rechaza(tmp_path, monkeypatch):
    (tmp_path / "PAUSE").write_text("")
    monkeypatch.setenv("JAX_KILL_SWITCH_PATH", str(tmp_path / "PAUSE"))
    r = policy.validate_create("plataforma", "supervised", 3, 0)
    assert not r.ok
    assert "Kill switch" in r.reason


def test_plan_con_freno_puesto_da_423(tmp_path, monkeypatch):
    (tmp_path / "PAUSE").write_text("")
    monkeypatch.setenv("JAX_KILL_SWITCH_PATH", str(tmp_path / "PAUSE"))
    with patch.object(routes, "_build_plan_or_reject", _NO_PLANIFICAR), \
         pytest.raises(HTTPException) as frenado:
        asyncio.run(routes.plan_only(routes.PlanRequest(
            name="t", objective="o", invoked_by="plataforma", mode="dry_run")))
    assert frenado.value.status_code == 423


def test_step_en_vuelo_se_corta_al_poner_el_freno(tmp_path, monkeypatch):
    ruta = tmp_path / "PAUSE"
    monkeypatch.setenv("JAX_KILL_SWITCH_PATH", str(ruta))
    cancelado = []
    fallas = []

    async def escenario():
        loop = asyncio.get_running_loop()
        en_vuelo = asyncio.Event()

        async def despacho_largo(step, pipeline):
            en_vuelo.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelado.append(True)  # run_sandboxed_claude mata el proceso acá
                raise

        async def fail_step(pipeline, step, i, error):
            fallas.append(error)

        step = SimpleNamespace(status=None, started_at=None, finished_at=None, facet="hyde",
                               capability="implementation", timeout_seconds=30, step_id="s0",
                               output_ref=None)
        pipeline = SimpleNamespace(pipeline_id="p-freno", context={}, name="t")
        with patch.object(executor.store, "step_upsert", AsyncMock()), \
             patch.object(executor.store, "event_append", AsyncMock()), \
             patch.object(executor, "_dispatch_step", despacho_largo), \
             patch.object(executor, "_fail_step", fail_step):
            tarea = asyncio.create_task(executor._run_one_step(step, 0, pipeline))
            await asyncio.wait_for(en_vuelo.wait(), 2.0)
            interruptor.escribir_pausa(ruta, "{}")
            inicio = loop.time()
            completo = await asyncio.wait_for(tarea, 2.0)
            return completo, loop.time() - inicio

    completo, duracion = asyncio.run(escenario())
    assert completo is False
    assert cancelado == [True]
    assert len(fallas) == 1 and "killed_by_switch" in fallas[0]
    assert duracion < 1.0
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_jacobs_interruptor.py -q`
Expected: caen todos menos los de 423 y `validate_create` (miran una ruta fija, no la del test), y cae `test_step_en_vuelo...` por el timeout de 2 s.

- [ ] **Step 3: Implementar**

`jacobs/policy.py`: borrar `from pathlib import Path` (si queda sin uso) y `KILL_SWITCH_PATH = Path("/etc/jax/PAUSE")`. Agregar `from interruptor import interruptor_activo` y reemplazar:

```python
def check_kill_switch() -> bool:
    """True si el kill switch está puesto (archivo de JAX_KILL_SWITCH_PATH).

    Sin la variable lanza InterruptorSinConfigurar: sin saber dónde está el
    freno no se ejecuta nada. Un error al mirarlo que no sea "no existe"
    cuenta como PUESTO (interruptor.py)."""
    return interruptor_activo()
```

`jacobs/executor.py`: import `from interruptor import correr_con_interruptor` junto a `from jacobs.policy import check_kill_switch`. En `_run_one_step`:

```python
        raw_output = await asyncio.wait_for(
            # El freno en vuelo (2026-09-16, frente B): antes un step ya lanzado
            # seguía hasta terminar la ola aunque el kill switch estuviera
            # puesto. Si aparece, se cancela en <= 250 ms: run_sandboxed_claude
            # mata a Hyde y _invoke_motor cancela el job en LAS MANOS.
            # InterruptorActivado cae en el except general de abajo, así que
            # queda _fail_step con "killed_by_switch".
            correr_con_interruptor(_dispatch_step(step, pipeline)),
            timeout=step.timeout_seconds,
        )
```

`tools/jacobs_relaunch.py:43`: `print("✗ Kill switch activo (archivo de JAX_KILL_SWITCH_PATH). Abortando.")`

`scripts/manual_motor_v02_integration.py`:
- docstring línea 4: `(dispatch real a Kimi, activa el kill switch de PRODUCCIÓN: el archivo de JAX_KILL_SWITCH_PATH).`
- borrar la constante `PAUSE_PATH`.
- reemplazar el bloque "PRUEBA 3" desde `print("PRUEBA 3 — Kill switch (/etc/jax/PAUSE)")` hasta `print(f"PAUSE eliminado. Existe: {os.path.exists(PAUSE_PATH)}")` por:

```python
print("PRUEBA 3 — Kill switch (archivo de JAX_KILL_SWITCH_PATH)")
print("=" * 60)

from pathlib import Path  # noqa: E402
from interruptor import borrar_pausa, escribir_pausa, interruptor_activo  # noqa: E402

# Sin la variable en /etc/jax/.env esto lanza KeyError: sin freno no hay prueba.
PAUSE_PATH = Path(load_env(ENV_FILE)["JAX_KILL_SWITCH_PATH"])

if borrar_pausa(PAUSE_PATH):
    print("PAUSE previo eliminado")

ks_ok = False
try:
    escribir_pausa(PAUSE_PATH, '{"accion": "prueba-manual-motor-v02"}')
    print(f"PAUSE creado. Puesto: {interruptor_activo(PAUSE_PATH)}")
except OSError as e:
    print(f"{FAIL} No se pudo crear PAUSE: {e}")
    stop_server()
    sys.exit(1)

try:
    resp3 = http_post("/motor/dispatch", DISPATCH_PAYLOAD)
    print(f"\nDispatch:\n{json.dumps(resp3, indent=2, ensure_ascii=False)}")
    job_id3 = resp3.get("job_id")
except Exception as e:
    print(f"{FAIL} dispatch con PAUSE activo falló: {e}")
    borrar_pausa(PAUSE_PATH)
    stop_server()
    sys.exit(1)
```

Todo lo que sigue hasta `ks_ok = all(...)` y el `print(... PRUEBA 3)` queda igual. Al final:

```python
borrar_pausa(PAUSE_PATH)
print(f"PAUSE eliminado. Puesto: {interruptor_activo(PAUSE_PATH)}")
```

(`subprocess` se sigue usando más arriba para arrancar el servidor: verificar con `grep -n subprocess scripts/manual_motor_v02_integration.py` antes de tocar el import.)

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_jacobs_interruptor.py tests/test_jacobs_invoked_by_rol.py tests/test_jacobs_timeout_by_capability.py tests/test_jacobs_director.py -q | tail -1`
Expected: todo verde (`test_jacobs_interruptor.py`: 7 passed).

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m py_compile tools/jacobs_relaunch.py scripts/manual_motor_v02_integration.py && echo ok`
Expected: `ok`. El script manual NO se ejecuta: toca producción.

Mutación: quitar `correr_con_interruptor(` del executor → cae `test_step_en_vuelo_se_corta_al_poner_el_freno`.

- [ ] **Step 5: CI.** `tests-puros`: agregar `tests/test_jacobs_interruptor.py` en las dos listas; piso +7, medido dos veces.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/worktrees/jax-frente-b add jacobs/policy.py jacobs/executor.py tools/jacobs_relaunch.py scripts/manual_motor_v02_integration.py tests/test_jacobs_interruptor.py .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-frente-b commit -m "feat(jacobs): check_kill_switch desde el interruptor y cada step corre bajo el freno en vuelo

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: jax · el REPL y `jax --task` bajo el freno; ninguna ruta fija

**Files:**
- Modify: `jax/core/main.py` (docstring línea 8; borrar `kill_switch_active` en :133-135; `run_task` :382, :425-428, :468; `main` :523, :794-797, :861; imports)
- Modify: `config/config.toml:14`
- Test: `tests/test_repl_interruptor.py`, `tests/test_interruptor_sin_rutas_fijas.py`
- Modify: `.github/workflows/policy.yml` (`tests-puros`)

**Interfaces:**
- Consumes: `jax.core.interruptor.{ruta_del_interruptor, interruptor_activo, correr_con_interruptor, InterruptorSinConfigurar}`.
- Produces: nada nuevo.

- [ ] **Step 1: Tests que fallan** — `tests/test_repl_interruptor.py`:

```python
"""El REPL y `jax --task` bajo el freno (plan 2026-09-16-frente-b-kill-switch,
Task 5). Se lee el código fuente de jax/core/main.py, igual que
test_degradaciones_declaradas.py: importar main arrastra voz y oído.

Antes: la ruta salía de config/config.toml y se miraba una sola vez antes
de invocar; `jax --task` corría a Hyde entero con el freno puesto después.

En memoria de Jairo Urbina.
"""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
FUENTE = (RAIZ / "jax" / "core" / "main.py").read_text(encoding="utf-8")
ARBOL = ast.parse(FUENTE)


def _funcion(nombre):
    return next(n for n in ast.walk(ARBOL)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nombre)


def _es_llamada_a(nodo, nombre):
    return isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id == nombre


@pytest.mark.parametrize("nombre", ["run_task", "main"])
def test_cada_invoke_corre_bajo_el_interruptor(nombre):
    fn = _funcion(nombre)
    invokes = [n for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "invoke"]
    assert invokes, "no hay invoke: el test no probaría nada"
    envueltas = {id(n.args[0]) for n in ast.walk(fn) if _es_llamada_a(n, "correr_con_interruptor") and n.args}
    assert [n.lineno for n in invokes if id(n) not in envueltas] == []


@pytest.mark.parametrize("nombre", ["run_task", "main"])
def test_la_ruta_sale_de_la_variable_y_sin_ella_se_sale_con_1(nombre):
    fn = _funcion(nombre)
    assert len([n for n in ast.walk(fn) if _es_llamada_a(n, "ruta_del_interruptor")]) == 1
    manejadores = [h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers
                   if isinstance(h.type, ast.Name) and h.type.id == "InterruptorSinConfigurar"]
    assert manejadores, f"{nombre} no falla cerrado sin JAX_KILL_SWITCH_PATH"
    assert any(ast.unparse(s) == "sys.exit(1)" for h in manejadores for s in h.body)


def test_main_ya_no_lee_la_ruta_de_la_config():
    assert "kill_switch_path" not in FUENTE
    assert "def kill_switch_active" not in FUENTE


def test_la_config_del_repl_no_declara_la_ruta():
    with open(RAIZ / "config" / "config.toml", "rb") as f:
        assert "kill_switch_path" not in tomllib.load(f)["jax"]
```

`tests/test_interruptor_sin_rutas_fijas.py`:

```python
"""Ninguna ruta del freno escrita a mano (plan 2026-09-16-frente-b-kill-switch,
Task 5). Test de CLASE sobre los archivos de código versionados: la ruta vive
sólo en /etc/jax/.env (JAX_KILL_SWITCH_PATH). La documentación (.md) queda
afuera a propósito: CONTEXT.md §9 y DEUDA.md cuentan la historia con la
ruta vieja, y eso es historia, no configuración."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LITERAL = "/etc/jax/" + "PAUSE"
SUFIJOS = {".py", ".toml", ".sh", ".js", ".service", ".yml", ".yaml"}
EXCLUIDOS = ("_director_patch/",)


def _archivos():
    salida = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True).stdout
    for relativa in salida.splitlines():
        ruta = RAIZ / relativa
        if ruta.suffix in SUFIJOS and not relativa.startswith(EXCLUIDOS) and ".backup-" not in relativa and ruta.is_file():
            yield relativa, ruta


def test_el_escaneo_mira_algo():
    assert len(list(_archivos())) > 100


def test_ningun_archivo_de_codigo_nombra_la_ruta_vieja():
    hallazgos = [r for r, ruta in _archivos() if LITERAL in ruta.read_text(encoding="utf-8", errors="replace")]
    assert hallazgos == []


def test_ninguna_config_declara_la_ruta_del_freno():
    patron = re.compile(r"^\s*kill_switch_path\s*=", re.MULTILINE)
    hallazgos = [r for r, ruta in _archivos()
                 if ruta.suffix == ".toml" and patron.search(ruta.read_text(encoding="utf-8"))]
    assert hallazgos == []
```

(`tools/jacobs_relaunch.py` y el script manual ya quedaron limpios en la Task 4. Si el escaneo encuentra otro archivo, se limpia en esta tarea: no se agrega a `EXCLUIDOS` sin decisión de Fernando.)

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_repl_interruptor.py tests/test_interruptor_sin_rutas_fijas.py -q`
Expected:
- FAIL los 6 de `test_repl_interruptor.py`;
- FAIL `test_ningun_archivo...`: `jax/core/main.py`, `config/config.toml` y `las_manos/motor_registry/worker.py` si quedó algún docstring;
- FAIL `test_ninguna_config...`: `config/config.toml`.

- [ ] **Step 3: Implementar**

`jax/core/main.py`:
- Import: `from jax.core.interruptor import InterruptorSinConfigurar, correr_con_interruptor, interruptor_activo, ruta_del_interruptor`.
- Línea 8 del docstring: `(archivo de JAX_KILL_SWITCH_PATH).`
- Borrar `def kill_switch_active(path: str) -> bool:` con su docstring y su return.
- En `run_task`, reemplazar `    kill_path = cfg["jax"]["kill_switch_path"]` por:

```python
    try:
        kill_path = ruta_del_interruptor()
    except InterruptorSinConfigurar as exc:
        print(f"[tarea] {exc}")
        sys.exit(1)
```

- `if kill_switch_active(kill_path):` → `if interruptor_activo(kill_path):` (los dos sitios, `run_task` y `main`).
- `respuesta = await muscle.invoke(contenido, history=None)` → `respuesta = await correr_con_interruptor(muscle.invoke(contenido, history=None))`. `InterruptorActivado` cae en el `except (MuscleError, Exception)` existente, que escribe "# Error en tarea" y sale con 1.
- En `main`, el mismo reemplazo de `kill_path`, con `print(f"[JAX] {exc}")`.
- `respuesta = await muscle.invoke(user_text, history=history_for_invocation, model=model_override)` → `respuesta = await correr_con_interruptor(muscle.invoke(user_text, history=history_for_invocation, model=model_override))`. El `except Exception` del bucle lo muestra con `humanizar_error` y el turno no entra al historial.

`config/config.toml`: borrar `kill_switch_path = "/etc/jax/PAUSE"` de `[jax]`.

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=.:las_manos /home/fruiz/jax/.venv/bin/python -m pytest tests/test_repl_interruptor.py tests/test_interruptor_sin_rutas_fijas.py tests/test_degradaciones_declaradas.py tests/test_repl_fuentes.py tests/test_repl_modelos_permitidos.py -q | tail -1`
Expected: verde (9 nuevos).

Run: `cd /home/fruiz/worktrees/jax-frente-b && pwd && PYTHONPATH=. /home/fruiz/jax/.venv/bin/python -m py_compile jax/core/main.py && echo ok`
Expected: `ok`.

Mutación: volver a poner `/etc/jax/PAUSE` literal en un comentario de `las_manos/server.py` → cae `test_ningun_archivo...`. Revertir.

- [ ] **Step 5: Suite completa de `tests-puros` y P10**

Agregar `tests/test_repl_interruptor.py tests/test_interruptor_sin_rutas_fijas.py` a las dos listas de `tests-puros`. Correr la lista completa dos veces y fijar el piso medido. Se espera `510 + 10 + 7 + 9 = 536 passed, 1 skipped`; vale el número medido. Correr `policy/tests/test_no_fail_open_except.py` y `tests/test_no_blocking_in_async.py`: verdes.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/worktrees/jax-frente-b add jax/core/main.py config/config.toml tests/test_repl_interruptor.py tests/test_interruptor_sin_rutas_fijas.py .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-frente-b commit -m "feat(repl): ruta del freno desde la variable, invoke bajo el interruptor y ninguna ruta fija en el repo

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: jax-platform · `kill_switch.py`, auditoría y difusión a todos

**Files:**
- Create: `backend/kill_switch.py`
- Modify: `backend/db/migrations.py` (DDL después de `CREATE_USER_ADMIN_AUDIT`; `_TABLES` después de `("user_admin_audit", ...)`)
- Modify: `backend/jax_engine/events.py`, `backend/jax_engine/schemas.py:6-17`
- Test: `backend/tests/test_kill_switch.py`

(Rutas relativas a `/home/fruiz/worktrees/jax-platform-frente-b`.)

**Interfaces:**
- Consumes: `interruptor.*` (Task 2); `db.transaccion.transaccion` y `AISLAMIENTO_ADMIN`; `db.connection.get_pool`; `tiempo.iso_utc`, `tiempo.utc_ahora`; `auth.middleware.get_current_user`; `auth.models.AuthUser`.
- Produces: todo `kill_switch.py` y `EventBus.publicar_a_todos` (ver Interfaces).

- [ ] **Step 1: Test que falla** — `backend/tests/test_kill_switch.py`:

```python
"""El kill switch de la Mesa (plan 2026-09-16-frente-b-kill-switch, Task 6).
Puro: la transacción y la difusión se sustituyen; el archivo es el REAL de
la ruta temporal que fija conftest.py. Corre en los dos jobs de CI."""
import asyncio
import json
import os
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

import interruptor
import kill_switch
from auth.models import AuthUser
from jax_engine.events import EventBus
from jax_engine.schemas import JAXEvent

ADMIN = AuthUser(user_id="7", tenant_id="1", role="superadmin")
OPERADOR = AuthUser(user_id="8", tenant_id="1", role="operator")
ES_ROOT = os.geteuid() == 0


class _Base:
    """Transacción de mentira con el mismo contrato que db.transaccion:
    confirma al salir, y ante una excepción no confirma."""

    def __init__(self):
        self.filas = []
        self.commits = 0
        self.falla_insert = None
        self.falla_commit = None

    @asynccontextmanager
    async def transaccion(self, aislamiento=None):
        assert aislamiento == "READ COMMITTED"
        base = self
        pendientes = []

        class _Cursor:
            async def execute(self, sql, args=()):
                if base.falla_insert is not None:
                    raise base.falla_insert
                pendientes.append(args)

        yield _Cursor()
        if self.falla_commit is not None:
            raise self.falla_commit
        self.filas.extend(pendientes)
        self.commits += 1


@pytest.fixture
def entorno(monkeypatch):
    ruta = interruptor.ruta_del_interruptor()
    eventos = []

    async def publicar(tipo, payload):
        eventos.append((tipo, payload))
        return 0

    base = _Base()
    monkeypatch.setattr(kill_switch.event_bus, "publicar_a_todos", publicar)
    monkeypatch.setattr(kill_switch, "transaccion", base.transaccion)
    return ruta, eventos, base


async def test_activar_pone_el_freno_avisa_y_audita(entorno):
    ruta, eventos, base = entorno
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True}
    assert json.loads(ruta.read_text())["user_id"] == "7"
    assert eventos == [("kill_switch_activated", {"activo": True})]
    assert base.filas == [("activar", 7)]


async def test_activar_dos_veces_no_duplica_nada(entorno):
    _, eventos, base = entorno
    await kill_switch.activar(ADMIN)
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": False}
    assert len(eventos) == 1 and len(base.filas) == 1


async def test_activar_con_la_auditoria_caida_deja_el_freno_puesto(entorno):
    ruta, eventos, base = entorno
    base.falla_insert = RuntimeError("base caída")
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.activar(ADMIN)
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [("kill_switch_activated", {"activo": True})]


async def test_reanudar_sin_freno_no_audita(entorno):
    _, eventos, base = entorno
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": False}
    assert eventos == [] and base.filas == []


async def test_reanudar_audita_quita_y_avisa(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": True}
    assert not interruptor.interruptor_activo(ruta)
    assert base.filas == [("reanudar", 7)]
    assert eventos == [("kill_switch_released", {"activo": False})]


async def test_reanudar_si_la_confirmacion_falla_vuelve_a_poner_el_freno(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    base.falla_commit = RuntimeError("commit perdido")
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.reanudar(ADMIN)
    assert interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


async def test_reanudar_si_el_insert_falla_no_toca_el_freno(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, '{"original": true}')
    base.falla_insert = RuntimeError("base caída")
    with pytest.raises(kill_switch.AuditoriaDelInterruptorFallida):
        await kill_switch.reanudar(ADMIN)
    assert json.loads(ruta.read_text()) == {"original": True}
    assert eventos == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_reanudar_sin_permiso_de_escritura_no_cambia_nada(entorno):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.reanudar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert interruptor.interruptor_activo(ruta)
    assert base.commits == 0 and eventos == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_activar_sin_permiso_de_escritura_no_avisa_ni_audita(entorno):
    ruta, eventos, base = entorno
    ruta.parent.chmod(0o500)
    try:
        with pytest.raises(kill_switch.InterruptorNoEscribible):
            await kill_switch.activar(ADMIN)
    finally:
        ruta.parent.chmod(0o700)
    assert not interruptor.interruptor_activo(ruta)
    assert eventos == [] and base.filas == []


async def test_veinte_activaciones_simultaneas_ponen_el_freno_una_vez(entorno):
    _, eventos, base = entorno
    respuestas = await asyncio.gather(*(kill_switch.activar(ADMIN) for _ in range(20)))
    assert sum(r["cambio"] for r in respuestas) == 1
    assert len(base.filas) == 1 and len(eventos) == 1


async def test_la_mesa_libre_deja_pasar_y_frenada_responde_423(entorno):
    ruta, _, _ = entorno
    assert await kill_switch.exigir_mesa_libre(OPERADOR) is OPERADOR
    interruptor.escribir_pausa(ruta, "{}")
    with pytest.raises(HTTPException) as frenada:
        await kill_switch.exigir_mesa_libre(OPERADOR)
    assert (frenada.value.status_code, frenada.value.detail) == (423, "kill_switch_activo")


async def test_publicar_a_todos_llega_a_cada_suscriptor_y_uno_roto_no_corta():
    bus = EventBus()
    recibidos = []

    async def bien(evento):
        recibidos.append((evento.tenant_id, evento.user_id, evento.event_type, evento.payload))

    async def roto(evento):
        raise RuntimeError("socket muerto")

    await bus.subscribe("t1", "u1", bien)
    await bus.subscribe("t1", "u2", roto)
    await bus.subscribe("t2", "u3", bien)
    assert await bus.publicar_a_todos("kill_switch_released", {"activo": False}) == 2
    assert sorted(recibidos) == [
        ("t1", "u1", "kill_switch_released", {"activo": False}),
        ("t2", "u3", "kill_switch_released", {"activo": False}),
    ]


def test_el_evento_liberado_es_un_tipo_valido():
    JAXEvent(event_type="kill_switch_released", tenant_id="1", user_id="1")
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch.py -q`
Expected: error de colección, `ModuleNotFoundError: No module named 'kill_switch'`.

- [ ] **Step 3: Evento, bus y tabla**

`jax_engine/schemas.py`: en `EventType`, después de `"kill_switch_activated",` agregar `"kill_switch_released",`.

`jax_engine/events.py`, dentro de `EventBus`, después de `publish`:

```python
    async def publicar_a_todos(self, event_type: str, payload: dict) -> int:
        """Un evento para CADA suscriptor del bus, WS y SSE (kill switch,
        2026-09-16). `publish` enruta a un solo usuario a propósito; esto es
        solo para estado global. Devuelve cuántos lo recibieron."""
        async with self._lock:
            destinos = [
                (tenant_id, user_id, cb)
                for tenant_id, suscriptores in self._subscribers.items()
                for user_id, cb in suscriptores.items()
            ]
        recibidos = 0
        for tenant_id, user_id, cb in destinos:
            evento = JAXEvent(event_type=event_type, tenant_id=tenant_id, user_id=user_id, payload=payload)
            try:
                await cb(evento)
                recibidos += 1
            except Exception:  # fail-soft: un suscriptor roto (socket muerto) no impide que el resto se entere del freno; el estado real igual llega por /api/state
                continue
        return recibidos
```

`db/migrations.py`, después de `CREATE_USER_ADMIN_AUDIT`:

```python
# Auditoría del kill switch (2026-09-16, frente B). Una fila por CAMBIO real
# del freno (poner o quitar), no por pedido. Sin FK a jax_users, como
# user_admin_audit: la historia sobrevive a la baja del usuario. `at` en UTC
# explícito (UTC_TIMESTAMP(6)); el último cambio sale por idx_kill_switch_audit_at.
CREATE_KILL_SWITCH_AUDIT = """
CREATE TABLE IF NOT EXISTS kill_switch_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  accion VARCHAR(10) NOT NULL,
  user_id INT NOT NULL,
  at DATETIME(6) NOT NULL,
  CONSTRAINT chk_kill_switch_audit_accion CHECK (accion IN ('activar', 'reanudar')),
  INDEX idx_kill_switch_audit_at (at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
```

y en `_TABLES`, después de `("user_admin_audit", CREATE_USER_ADMIN_AUDIT),`: `    ("kill_switch_audit", CREATE_KILL_SWITCH_AUDIT),  # sin FK a propósito`.

- [ ] **Step 4: El módulo** — `backend/kill_switch.py`:

```python
"""Kill switch de la Mesa (2026-09-16, frente B).

La plataforma es el único ESCRITOR del freno (interruptor.py, espejo de
jax/core/interruptor.py). LAS MANOS, Jacobs y el REPL lo leen del mismo
archivo. Reglas:

- activar: el freno PRIMERO (escritura atómica), después el aviso a todos y
  al final la auditoría. Si la auditoría falla, el freno queda PUESTO y se
  lanza AuditoriaDelInterruptorFallida (500 + journal): una base caída no
  impide frenar.
- reanudar: auditoría y borrado en la MISMA transacción. Si la confirmación
  falla después de borrar, se vuelve a poner el freno. Ante la duda, frenado.
- una fila de auditoría por CAMBIO real; pedir lo que ya está no escribe.
- sin caché: el freno se mira con un stat por pedido (medido en la carga del
  frente B). Un caché lo retrasaría su TTL.
- exigir_mesa_libre: dependencia de las rutas que ejecutan (RUTAS_FRENADAS);
  423 `kill_switch_activo`.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Depends, HTTPException, status

import interruptor
from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from jax_engine.events import event_bus
from tiempo import iso_utc, utc_ahora

logger = logging.getLogger(__name__)

ACCIONES = frozenset({"activar", "reanudar"})
KILL_SWITCH_ACTIVO = "kill_switch_activo"
NO_ESCRIBIBLE = "kill_switch_no_escribible"
AUDITORIA_FALLIDA = "kill_switch_auditoria_fallida"
EVENTO_ACTIVADO = "kill_switch_activated"
EVENTO_LIBERADO = "kill_switch_released"

RUTAS_FRENADAS = frozenset({
    ("POST", "/api/chat"),
    ("POST", "/api/image/generate"),
    ("POST", "/api/command"),
    ("POST", "/api/pipelines"),
    ("POST", "/api/pipelines/{pipeline_id}/resume"),
})

SQL_REGISTRAR = "INSERT INTO kill_switch_audit (accion, user_id, at) VALUES (%s, %s, UTC_TIMESTAMP(6))"
# Ordena por idx_kill_switch_audit_at (con el id de desempate, que InnoDB ya
# guarda en el índice). El JOIN va por PRIMARY. EXPLAIN en
# tests/test_kill_switch_endpoints.py.
SQL_ULTIMO = (
    "SELECT a.accion, a.user_id, u.email, a.at FROM kill_switch_audit a "
    "LEFT JOIN jax_users u ON u.user_id = a.user_id "
    "ORDER BY a.at DESC, a.id DESC LIMIT 1"
)

_cambio = asyncio.Lock()


class InterruptorNoEscribible(RuntimeError):
    """El archivo del freno no se pudo escribir ni borrar: nada cambió."""


class AuditoriaDelInterruptorFallida(RuntimeError):
    """El cambio no quedó auditado; el freno quedó PUESTO."""


class _NadaQueQuitar(Exception):
    pass


def activo() -> bool:
    return interruptor.interruptor_activo()


def _escribir(ruta, contenido: str) -> bool:
    try:
        return interruptor.escribir_pausa(ruta, contenido)
    except OSError as exc:
        raise InterruptorNoEscribible(str(exc)) from exc


def _borrar(ruta) -> bool:
    try:
        return interruptor.borrar_pausa(ruta)
    except OSError as exc:
        raise InterruptorNoEscribible(str(exc)) from exc


def _contenido(accion: str, usuario: AuthUser) -> str:
    return json.dumps({"accion": accion, "user_id": str(usuario.user_id), "at": iso_utc(utc_ahora())})


async def _registrar(cur, accion: str, user_id) -> None:
    if accion not in ACCIONES:
        raise ValueError(f"acción de kill switch desconocida: {accion!r}")
    await cur.execute(SQL_REGISTRAR, (accion, int(user_id)))


async def estado() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_ULTIMO)
            fila = await cur.fetchone()
    ultimo = None if fila is None else {
        "accion": fila[0], "user_id": fila[1], "email": fila[2], "at": iso_utc(fila[3]),
    }
    return {"activo": activo(), "ultimo": ultimo}


async def activar(usuario: AuthUser) -> dict:
    async with _cambio:
        ruta = interruptor.ruta_del_interruptor()
        puesto = await asyncio.to_thread(_escribir, ruta, _contenido("activar", usuario))
        if not puesto:
            return {"activo": True, "cambio": False}
        await event_bus.publicar_a_todos(EVENTO_ACTIVADO, {"activo": True})
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await _registrar(cur, "activar", usuario.user_id)
        except Exception as exc:  # fail-closed: el freno ya quedó puesto; la falta de auditoría se relanza como 500 y queda en el journal
            logger.error("kill switch ACTIVADO por user_id=%s sin auditoría: %r", usuario.user_id, exc)
            raise AuditoriaDelInterruptorFallida("activar") from exc
        return {"activo": True, "cambio": True}


async def reanudar(usuario: AuthUser) -> dict:
    async with _cambio:
        ruta = interruptor.ruta_del_interruptor()
        if not interruptor.interruptor_activo(ruta):
            return {"activo": False, "cambio": False}
        quitado = False
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                await _registrar(cur, "reanudar", usuario.user_id)
                quitado = await asyncio.to_thread(_borrar, ruta)
                if not quitado:
                    raise _NadaQueQuitar
        except _NadaQueQuitar:
            return {"activo": False, "cambio": False}
        except InterruptorNoEscribible:
            raise
        except Exception as exc:  # fail-closed: si se borró y no se pudo confirmar la auditoría, el freno se vuelve a poner antes de relanzar
            if quitado:
                await asyncio.to_thread(_escribir, ruta, _contenido("reactivado_sin_auditoria", usuario))
            logger.error("kill switch: reanudar de user_id=%s sin auditoría, freno repuesto=%s: %r",
                         usuario.user_id, quitado, exc)
            raise AuditoriaDelInterruptorFallida("reanudar") from exc
        await event_bus.publicar_a_todos(EVENTO_LIBERADO, {"activo": False})
        return {"activo": False, "cambio": True}


async def exigir_mesa_libre(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Dependencia de las rutas que EJECUTAN (RUTAS_FRENADAS). Después de la
    autenticación: un anónimo recibe 401, no el estado del freno."""
    if activo():
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=KILL_SWITCH_ACTIVO)
    return user
```

- [ ] **Step 5: Verde**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch.py tests/test_no_fail_open_except.py tests/test_las_manos_health_broadcast.py tests/test_websocket_isolation.py -q | tail -1`
Expected: verde (`test_kill_switch.py`: 13 passed).

Mutaciones (revertir cada una):
- En `activar`, mover la auditoría ANTES de `_escribir`: cae `test_activar_con_la_auditoria_caida...`.
- Quitar el `if quitado: ... _escribir(...)`: cae `test_reanudar_si_la_confirmacion_falla...`.
- Quitar `async with _cambio:` de `activar` y agregar un `await asyncio.sleep(0)` antes del `_escribir`: el test de concurrencia sigue verde (lo cuida `os.link`), y eso se anota en el ledger como evidencia de que el lock es orden de auditoría y no exclusión.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-b add backend/kill_switch.py backend/db/migrations.py backend/jax_engine/events.py backend/jax_engine/schemas.py backend/tests/test_kill_switch.py
git -C /home/fruiz/worktrees/jax-platform-frente-b commit -m "feat(kill-switch): activar/reanudar con auditoría fail-closed, difusión a todos y dependencia de mesa libre

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: jax-platform · endpoints de admin y estado en `/api/state`

**Files:**
- Create: `backend/api/admin/kill_switch.py`
- Modify: `backend/api/admin/__init__.py`, `backend/main.py` (import de routers de admin + `app.include_router`), `backend/api/state.py`, `backend/tests/identidades.py` (`borrar_usuario`)
- Test: `backend/tests/test_kill_switch_endpoints.py`

**Interfaces:**
- Consumes: `kill_switch.estado/activar/reanudar/activo/SQL_ULTIMO/InterruptorNoEscribible/AuditoriaDelInterruptorFallida/NO_ESCRIBIBLE/AUDITORIA_FALLIDA`; `auth.middleware.require_superadmin`.
- Produces: las tres rutas y `kill_switch_active` en `/api/state`.

- [ ] **Step 1: Test que falla** — `backend/tests/test_kill_switch_endpoints.py`:

```python
"""Endpoints del kill switch (plan 2026-09-16-frente-b-kill-switch, Task 7).
Contra jax_memory_test (conftest). El freno es la ruta temporal de la suite."""
import os

import pytest

import interruptor
import kill_switch
from tests.identidades import auth, cabeceras, sql, token_para

ACTIVAR = "/api/admin/kill-switch/activar"
REANUDAR = "/api/admin/kill-switch/reanudar"
ESTADO = "/api/admin/kill-switch"
ES_ROOT = os.geteuid() == 0


def _superadmin(usuarios):
    user_id, _ = usuarios(role="superadmin")
    return user_id, auth(token_para(user_id, role="superadmin"))


async def _auditoria(user_id):
    filas = await sql("SELECT accion, user_id FROM kill_switch_audit WHERE user_id = %s ORDER BY id",
                      (user_id,), True)
    return [tuple(f) for f in filas]


def test_solo_un_superadmin_ve_o_toca_el_freno(client):
    h = cabeceras(client, "ks-operador")
    assert client.get(ESTADO, headers=h).status_code == 403
    assert client.post(ACTIVAR, headers=h).status_code == 403
    assert client.post(REANUDAR, headers=h).status_code == 403
    assert not interruptor.interruptor_activo()


def test_sin_token_no_hay_freno(client):
    assert client.post(ACTIVAR).status_code in (401, 403)
    assert not interruptor.interruptor_activo()


def test_ciclo_completo_con_auditoria(client, usuarios):
    user_id, h = _superadmin(usuarios)
    r = client.post(ACTIVAR, headers=h)
    assert (r.status_code, r.json()) == (200, {"activo": True, "cambio": True})
    assert interruptor.interruptor_activo()
    visto = client.get(ESTADO, headers=h).json()
    assert visto["activo"] is True
    assert (visto["ultimo"]["accion"], visto["ultimo"]["user_id"]) == ("activar", user_id)
    assert client.post(ACTIVAR, headers=h).json() == {"activo": True, "cambio": False}
    assert client.post(REANUDAR, headers=h).json() == {"activo": False, "cambio": True}
    assert not interruptor.interruptor_activo()
    assert client.post(REANUDAR, headers=h).json() == {"activo": False, "cambio": False}
    assert client.portal.call(_auditoria, user_id) == [("activar", user_id), ("reanudar", user_id)]


def test_el_estado_llega_a_cualquier_usuario_por_api_state(client, usuarios):
    _, h = _superadmin(usuarios)
    operador = cabeceras(client, "ks-estado-operador")
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is False
    client.post(ACTIVAR, headers=h)
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is True
    client.post(REANUDAR, headers=h)
    assert client.get("/api/state", headers=operador).json()["kill_switch_active"] is False


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_si_no_se_puede_escribir_da_503_y_no_audita(client, usuarios):
    user_id, h = _superadmin(usuarios)
    carpeta = interruptor.ruta_del_interruptor().parent
    carpeta.chmod(0o500)
    try:
        r = client.post(ACTIVAR, headers=h)
    finally:
        carpeta.chmod(0o700)
    assert (r.status_code, r.json()["detail"]) == (503, "kill_switch_no_escribible")
    assert client.portal.call(_auditoria, user_id) == []


def test_el_ultimo_cambio_usa_el_indice_sin_filesort(client, usuarios):
    user_id, _ = usuarios(role="superadmin")

    async def medir():
        await sql(
            "INSERT INTO kill_switch_audit (accion, user_id, at) "
            "SELECT IF(seq % 2 = 0, 'activar', 'reanudar'), %s, UTC_TIMESTAMP(6) - INTERVAL seq SECOND "
            "FROM seq_1_to_300", (user_id,))
        await sql("ANALYZE TABLE kill_switch_audit", (), True)
        return await sql("EXPLAIN " + kill_switch.SQL_ULTIMO, (), True)

    plan = client.portal.call(medir)
    primera = plan[0]  # id, select_type, table, type, possible_keys, key, key_len, ref, rows, Extra
    assert primera[2] == "a"
    assert primera[5] == "idx_kill_switch_audit_at", plan
    assert "filesort" not in (primera[9] or ""), plan
    assert "temporary" not in (primera[9] or ""), plan
```

En `backend/tests/identidades.py`, `borrar_usuario`: primera línea del cuerpo, antes del DELETE de `user_admin_audit`:

```python
    await sql("DELETE FROM kill_switch_audit WHERE user_id = %s", (user_id,))
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch_endpoints.py -q`
Expected: FAIL. Los endpoints dan 404; `test_solo_un_superadmin...` falla por `404 != 403`; `kill_switch_active` da KeyError; el EXPLAIN pasa o falla según el optimizador. Anotar el resultado.

- [ ] **Step 3: Implementar**

`backend/api/admin/kill_switch.py`:

```python
"""Endpoints del kill switch (2026-09-16, frente B). Sólo superadmin. El
estado para cualquier usuario va en /api/state.kill_switch_active."""
from fastapi import APIRouter, Depends, HTTPException

import kill_switch
from auth.middleware import require_superadmin
from auth.models import AuthUser

router = APIRouter(prefix="/api/admin")


@router.get("/kill-switch")
async def ver_kill_switch(user: AuthUser = Depends(require_superadmin)):
    return await kill_switch.estado()


@router.post("/kill-switch/activar")
async def activar_kill_switch(user: AuthUser = Depends(require_superadmin)):
    try:
        return await kill_switch.activar(user)
    except kill_switch.InterruptorNoEscribible as exc:
        raise HTTPException(status_code=503, detail=kill_switch.NO_ESCRIBIBLE) from exc
    except kill_switch.AuditoriaDelInterruptorFallida as exc:
        raise HTTPException(status_code=500, detail=kill_switch.AUDITORIA_FALLIDA) from exc


@router.post("/kill-switch/reanudar")
async def reanudar_kill_switch(user: AuthUser = Depends(require_superadmin)):
    try:
        return await kill_switch.reanudar(user)
    except kill_switch.InterruptorNoEscribible as exc:
        raise HTTPException(status_code=503, detail=kill_switch.NO_ESCRIBIBLE) from exc
    except kill_switch.AuditoriaDelInterruptorFallida as exc:
        raise HTTPException(status_code=500, detail=kill_switch.AUDITORIA_FALLIDA) from exc
```

`api/admin/__init__.py`: `from .kill_switch import router as kill_switch_router` y `"kill_switch_router",` en `__all__`. `main.py`: `kill_switch_router,` en el `from api.admin import (...)` y `app.include_router(kill_switch_router)` después de `app.include_router(smtp_router)`. Si A-33 ya convirtió eso en un bucle sobre una tupla, se agrega a la tupla.

`api/state.py`: `import kill_switch` y, antes de `return datos`:

```python
    # Global, para todos los roles (2026-09-16, frente B): quien no es
    # superadmin tiene que saber que la Mesa está frenada. Un stat por pedido,
    # sin caché (el freno no espera un TTL).
    datos["kill_switch_active"] = kill_switch.activo()
```

- [ ] **Step 4: Verde (con DB y sin DB)**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch_endpoints.py tests/test_state_por_duenio.py tests/test_state_http_pooling.py -q | tail -1`
Expected: verde (`test_kill_switch_endpoints.py`: 6 passed).

Si el EXPLAIN muestra filesort: NO se cambia el test. Primero se prueba el índice `(at, id)` en la migración, se mide de nuevo y se anota en el ledger qué dijo el optimizador.

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && JAX_CI_NO_DB=1 /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch_endpoints.py -q -rs | tail -3`
Expected: 6 skipped (piden `client`).

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-b add backend/api/admin/kill_switch.py backend/api/admin/__init__.py backend/main.py backend/api/state.py backend/tests/identidades.py backend/tests/test_kill_switch_endpoints.py
git -C /home/fruiz/worktrees/jax-platform-frente-b commit -m "feat(kill-switch): endpoints de superadmin y kill_switch_active en /api/state

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: jax-platform · la Mesa responde 423 (+ peor caso de la Mesa)

**Files:**
- Modify: `backend/api/chat.py:1013`, `backend/api/image.py:43`, `backend/api/command.py:31`, `backend/api/pipelines.py:116,181-184`
- Test: `backend/tests/test_kill_switch_mesa.py`

**Interfaces:**
- Consumes: `kill_switch.exigir_mesa_libre`, `kill_switch.RUTAS_FRENADAS`.
- Produces: 423 `kill_switch_activo` en las cinco rutas.

- [ ] **Step 1: Test que falla** — `backend/tests/test_kill_switch_mesa.py`:

```python
"""La Mesa frenada (plan 2026-09-16-frente-b-kill-switch, Task 8). Con el
freno puesto, las rutas que EJECUTAN responden 423 antes de tocar un modelo,
Jacobs o el disco; al reanudar vuelven a aceptar. Cancelar NO se frena."""
import pytest
from fastapi.routing import APIRoute, iter_route_contexts

import kill_switch
from tests.identidades import auth, cabeceras, token_para

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")


def _dependencias(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependencias(sub)


def test_las_rutas_que_ejecutan_piden_la_mesa_libre():
    from main import app

    efectivas = [rc for rc in iter_route_contexts(app.routes) if isinstance(rc.original_route, APIRoute)]
    assert ("POST", "/api/chat") in {(m, rc.path) for rc in efectivas for m in rc.methods}
    frenadas = {
        (metodo, rc.path)
        for rc in efectivas
        if kill_switch.exigir_mesa_libre in set(_dependencias(rc.dependant))
        for metodo in rc.methods
    }
    assert frenadas == set(kill_switch.RUTAS_FRENADAS)
    assert ("POST", "/api/pipelines/{pipeline_id}/cancel") not in frenadas


def test_peor_caso_en_la_mesa(client, usuarios, monkeypatch):
    from api import chat as chat_mod
    import http_client

    async def sin_modelo(*args, **kwargs):
        raise AssertionError("con el freno puesto no se llama a ningún modelo")

    class _SinJacobs:
        async def post(self, *args, **kwargs):
            raise AssertionError("con el freno puesto no se habla con Jacobs")

    async def cliente_sin_jacobs():
        return _SinJacobs()

    monkeypatch.setattr(chat_mod, "_invoke_facet", sin_modelo)
    monkeypatch.setattr(http_client, "get_http_client", cliente_sin_jacobs)
    for modulo in ("api.pipelines", "api.image"):
        mod = __import__(modulo, fromlist=["x"])
        if hasattr(mod, "get_http_client"):
            monkeypatch.setattr(mod, "get_http_client", cliente_sin_jacobs)

    admin_id, _ = usuarios(role="superadmin")
    admin = auth(token_para(admin_id, role="superadmin"))
    operador = cabeceras(client, "ks-mesa-operador")

    assert client.post("/api/admin/kill-switch/activar", headers=admin).status_code == 200
    casos = [
        ("/api/chat", {"message": "hola", "facet": "jax_local"}),
        ("/api/image/generate", {"prompt": "un faro"}),
        ("/api/command", {"command": "ls", "mode": "dry_run"}),
        ("/api/pipelines", {"name": "t", "objective": "o"}),
        ("/api/pipelines/00000000-0000-0000-0000-000000000000/resume", None),
    ]
    for ruta, cuerpo in casos:
        r = client.post(ruta, json=cuerpo, headers=operador)
        assert (r.status_code, r.json().get("detail")) == (423, "kill_switch_activo"), ruta

    assert client.post("/api/admin/kill-switch/reanudar", headers=admin).status_code == 200
    r = client.post("/api/chat", json={"message": "hola", "facet": "__no_existe__"}, headers=operador)
    assert r.status_code == 400  # pasó el freno; lo detiene la validación, nunca el modelo
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch_mesa.py -q`
Expected:
- FAIL `test_las_rutas...`: `set() != {...}`;
- FAIL `test_peor_caso...`: `/api/chat` da 400 o AssertionError "no se llama a ningún modelo", no 423.

- [ ] **Step 3: Implementar.** En cada archivo, `from kill_switch import exigir_mesa_libre` y cambiar SÓLO el `Depends` de estas firmas:
- `api/chat.py`: `async def chat(req: ChatRequest, background_tasks: BackgroundTasks, user: AuthUser = Depends(exigir_mesa_libre)):`
- `api/image.py`: `async def generate_image(req: ImageRequest, user: AuthUser = Depends(exigir_mesa_libre)):`
- `api/command.py`: `async def create_command(req: CommandRequest, user: AuthUser = Depends(exigir_mesa_libre)):`
- `api/pipelines.py`: `async def create_pipeline(request: Request, user: AuthUser = Depends(exigir_mesa_libre)):` y en `resume_pipeline`, `user: AuthUser = Depends(exigir_mesa_libre),`

`get_current_user` sigue importado donde otras rutas lo usan (`grep -n get_current_user` en cada archivo antes de tocar imports).

- [ ] **Step 4: Verde y suites vecinas**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_kill_switch_mesa.py tests/test_chat_facet_validation.py tests/test_command_ownership.py tests/test_command_path_traversal.py tests/test_pipeline_ownership.py tests/test_pipelines_identity_injection.py tests/test_fijar_password.py -q | tail -1`
Expected: verde. `test_fijar_password.py::test_solo_me_y_mi_cuenta_admiten_la_marca` sigue verde: `exigir_mesa_libre` depende de `get_current_user`, que rechaza la marca.

Mutación: sacar `exigir_mesa_libre` de `resume_pipeline` → caen los dos tests.

- [ ] **Step 5: Suite completa y pisos del backend**

Run (con DB, dos veces): `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -p no:cacheprovider | tail -1`
Run (sin DB, dos veces): `cd /home/fruiz/worktrees/jax-platform-frente-b/backend && pwd && JAX_CI_NO_DB=1 /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -p no:cacheprovider | tail -1`

En `.github/workflows/policy.yml`:
- `PISO_PASSED = 1175` (job con DB) → el número medido;
- `JAX_CI_MIN_PASSED: "614"` (sin DB) → el número medido.

Los puros nuevos son 3 de `test_interruptor_arranque.py`, 13 de `test_kill_switch.py` y 1 de rutas, así que se espera sin DB `614 + 17 = 631`; vale el número medido. Cada uno lleva un comentario con qué suma. `MAX_SKIPS` no cambia (verificar).

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-b add backend/api/chat.py backend/api/image.py backend/api/command.py backend/api/pipelines.py backend/tests/test_kill_switch_mesa.py .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-platform-frente-b commit -m "feat(kill-switch): la Mesa responde 423 kill_switch_activo en chat, imagen, comando y pipelines

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: jax-platform · el frontend muestra el freno real

**Files:**
- Modify: `frontend/src/components/BottomBar/KillSwitch.jsx` (reescritura), `KillSwitch.test.jsx` (reescritura)
- Modify: `frontend/src/store/useJaxStore.js` (`loadState` :567-580, `handleEvent` :300-303, `activateKillSwitch` :558-564)
- Create: `frontend/src/store/useJaxStore.killSwitch.test.js`, `frontend/src/api/client.killSwitch.test.js`
- Modify: `frontend/src/api/client.js` (interceptor de respuesta), `frontend/src/api/errores.js`
- Modify: `frontend/src/components/BottomBar/BottomBar.jsx:150,189,210,238` (+ `BottomBar.test.jsx`), `frontend/src/components/RightPanel/RightPanel.jsx:44-52`
- Modify: `frontend/src/i18n/es.js:93-106`, `frontend/src/i18n/en.js:93-103`
- Modify: `.github/workflows/policy.yml` (piso de vitest, línea ~507)

**Interfaces:**
- Consumes: `POST /api/admin/kill-switch/activar|reanudar` → `{activo, cambio}`; `GET /api/state` → `kill_switch_active`; eventos `kill_switch_activated` y `kill_switch_released`; `Dialogo`, `ConfirmacionSuma`, `codigoDe`.
- Produces: `activarKillSwitch`, `reanudarKillSwitch` y `textoDeKillSwitch(t, err)` (ver Interfaces).

- [ ] **Step 1: Tests que fallan**

`frontend/src/components/BottomBar/KillSwitch.test.jsx` (reemplaza el archivo):

```jsx
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Kill switch real (2026-09-16, frente B). Antes el botón ponía
// killSwitchActive en true ANTES de llamar a un endpoint que no existía y se
// tragaba el 404: decía "detenido" sin detener nada.
vi.mock('../../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../../api/client'
import KillSwitch from './KillSwitch'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const INICIAL = useJaxStore.getState()

function pintar() {
  return render(<I18nProvider><KillSwitch /></I18nProvider>)
}

function como(role, killSwitchActive = false) {
  useJaxStore.setState({ user: { user_id: '7', role }, killSwitchActive })
}

beforeEach(() => {
  useJaxStore.setState(INICIAL, true)
  api.post.mockReset()
  api.get.mockReset()
  localStorage.clear()
})

describe('KillSwitch -- quién lo ve', () => {
  it('un operator no ve el botón con el freno suelto', () => {
    como('operator')
    pintar()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('un operator ve el aviso con el freno puesto, sin Reanudar', () => {
    como('operator', true)
    pintar()
    expect(screen.getByText(es.killSwitchActive)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: es.killResumeButton })).not.toBeInTheDocument()
  })
})

describe('KillSwitch -- activar', () => {
  it('abre un Dialogo propio y no llama al backend hasta confirmar', () => {
    como('superadmin')
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    expect(screen.getByRole('dialog', { name: es.killConfirmTitle })).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('confirmar activa y el estado sale de la respuesta', async () => {
    como('superadmin')
    api.post.mockResolvedValue({ data: { activo: true, cambio: true } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/kill-switch/activar'))
    await waitFor(() => expect(useJaxStore.getState().killSwitchActive).toBe(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('si falla, lo dice con su código y no finge que se detuvo', async () => {
    como('superadmin')
    api.post.mockRejectedValue({ response: { status: 503, data: { detail: 'kill_switch_no_escribible' } } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorNoEscribible)
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
  })

  it('un error sin código muestra el genérico de activar', async () => {
    como('superadmin')
    api.post.mockRejectedValue(new Error('red caída'))
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorActivar)
  })
})

describe('KillSwitch -- reanudar con suma', () => {
  it('pide la suma y recién ahí reanuda', async () => {
    como('superadmin', true)
    api.post.mockResolvedValue({ data: { activo: false, cambio: true } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    const etiqueta = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]').textContent
    const [, a, b] = etiqueta.match(/(\d+) \+ (\d+)/)
    const confirmar = within(dialogo).getByRole('button', { name: es.killResumeConfirm })
    expect(confirmar).toBeDisabled()
    fireEvent.change(within(dialogo).getByLabelText(etiqueta), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/kill-switch/reanudar'))
    await waitFor(() => expect(useJaxStore.getState().killSwitchActive).toBe(false))
  })

  it('con la suma mal no reanuda', () => {
    como('superadmin', true)
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    const etiqueta = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]').textContent
    fireEvent.change(within(dialogo).getByLabelText(etiqueta), { target: { value: '-1' } })
    expect(within(dialogo).getByRole('button', { name: es.killResumeConfirm })).toBeDisabled()
    expect(api.post).not.toHaveBeenCalled()
  })
})

describe('KillSwitch -- textos', () => {
  const NUEVAS = ['killConfirmTitle', 'killConfirmMessage', 'killResumeButton', 'killResumeTitle',
    'killResumeMessage', 'killResumeConfirm', 'killSwitchReleasedToast', 'killSwitchErrorActivar',
    'killSwitchErrorReanudar', 'killSwitchErrorNoEscribible', 'killSwitchErrorAuditoria', 'kill_switch_activo']

  it('cada clave nueva existe en es y en, sin vacíos', () => {
    for (const clave of NUEVAS) {
      expect(typeof es[clave] === 'string' && es[clave].trim() !== '', `es.${clave}`).toBe(true)
      expect(typeof en[clave] === 'string' && en[clave].trim() !== '', `en.${clave}`).toBe(true)
    }
  })

  it('el botón y su confirmación no comparten rótulo (un lector de pantalla los confunde)', () => {
    expect(es.killResumeButton).not.toBe(es.killResumeConfirm)
    expect(en.killResumeButton).not.toBe(en.killResumeConfirm)
  })

  it('las claves del botón viejo ya no existen', () => {
    for (const clave of ['killConfirm', 'killSwitchStoppedToast']) {
      expect(es[clave]).toBeUndefined()
      expect(en[clave]).toBeUndefined()
    }
  })

  it('el botón conserva el rótulo KILL', () => {
    expect(es.killButton).toBe('KILL')
    expect(en.killButton).toBe('KILL')
  })
})
```

`frontend/src/store/useJaxStore.killSwitch.test.js`:

```js
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'
import es from '../i18n/es.js'

const INICIAL = useJaxStore.getState()

describe('useJaxStore -- kill switch real', () => {
  beforeEach(() => {
    useJaxStore.setState(INICIAL, true)
    vi.clearAllMocks()
  })

  it('loadState toma el estado real del freno', async () => {
    api.get.mockResolvedValue({ data: { facets: {}, active_pipelines: {}, las_manos_alive: true, kill_switch_active: true } })
    await useJaxStore.getState().loadState()
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })

  it('kill_switch_released lo apaga y avisa', () => {
    useJaxStore.setState({ killSwitchActive: true })
    useJaxStore.getState().handleEvent({ event_type: 'kill_switch_released', payload: { activo: false } })
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
    expect(useJaxStore.getState().toasts.at(-1).message).toBe(es.killSwitchReleasedToast)
  })

  it('activarKillSwitch no enciende nada si el backend falla, y lo propaga', async () => {
    api.post.mockRejectedValue(new Error('x'))
    await expect(useJaxStore.getState().activarKillSwitch()).rejects.toThrow('x')
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
  })

  it('reanudarKillSwitch apaga según la respuesta', async () => {
    useJaxStore.setState({ killSwitchActive: true })
    api.post.mockResolvedValue({ data: { activo: false, cambio: true } })
    await useJaxStore.getState().reanudarKillSwitch()
    expect(api.post).toHaveBeenCalledWith('/admin/kill-switch/reanudar')
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
  })

  it('activateKillSwitch ya no existe', () => {
    expect(useJaxStore.getState().activateKillSwitch).toBeUndefined()
  })
})
```

`frontend/src/api/client.killSwitch.test.js` (mismo arnés de mocks que `client.test.js`):

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'

const requestUse = vi.fn()
const responseUse = vi.fn()

vi.mock('axios', () => {
  const instance = { interceptors: { request: { use: requestUse }, response: { use: responseUse } } }
  const axiosFn = vi.fn(() => Promise.resolve({ data: 'retried' }))
  axiosFn.create = vi.fn(() => instance)
  axiosFn.post = vi.fn()
  return { default: axiosFn }
})

const setStateMock = vi.fn()
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: { getState: vi.fn(() => ({ token: 't', user: { user_id: '7' } })), setState: setStateMock },
}))

import { textoDeKillSwitch } from './errores'
import es from '../i18n/es.js'

let onRejected

beforeEach(async () => {
  vi.resetModules()
  setStateMock.mockReset()
  responseUse.mockClear()
  await import('./client')
  onRejected = responseUse.mock.calls[0][1]
})

describe('interceptor -- 423 del kill switch', () => {
  it('enciende el aviso en el store y rechaza igual', async () => {
    const err = { response: { status: 423, data: { detail: 'kill_switch_activo' } }, config: { url: '/chat' } }
    await expect(onRejected(err)).rejects.toBe(err)
    expect(setStateMock).toHaveBeenCalledWith({ killSwitchActive: true })
  })

  it('un 423 de otra cosa no toca el freno', async () => {
    const err = { response: { status: 423, data: { detail: 'cuenta_bloqueada' } }, config: { url: '/auth/login' } }
    await expect(onRejected(err)).rejects.toBe(err)
    expect(setStateMock).not.toHaveBeenCalledWith({ killSwitchActive: true })
  })
})

describe('textoDeKillSwitch', () => {
  it('traduce el 423 del freno y deja pasar lo demás', () => {
    expect(textoDeKillSwitch(es, { response: { status: 423, data: { detail: 'kill_switch_activo' } } })).toBe(es.kill_switch_activo)
    expect(textoDeKillSwitch(es, { response: { status: 400, data: { detail: 'kill_switch_activo' } } })).toBeNull()
    expect(textoDeKillSwitch(es, new Error('red'))).toBeNull()
  })
})
```

En `frontend/src/components/BottomBar/BottomBar.test.jsx` agregar (e importar `waitFor` y `es`, y `api` desde `'../../api/client'`):

```jsx
describe('BottomBar -- la Mesa frenada', () => {
  it('un 423 del kill switch se muestra traducido, no el código', async () => {
    useJaxStore.setState({ activeFacet: 'jax_local', messages: [] })
    api.post.mockRejectedValue({ response: { status: 423, data: { detail: 'kill_switch_activo' } } })
    renderBar()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hola' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar' }))
    await waitFor(() => expect(
      useJaxStore.getState().messages.some((m) => m.content.includes(es.kill_switch_activo))).toBe(true))
    expect(useJaxStore.getState().messages.some((m) => m.content.includes('kill_switch_activo'))).toBe(false)
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/frontend && pwd && npx vitest run src/components/BottomBar/KillSwitch.test.jsx src/store/useJaxStore.killSwitch.test.js src/api/client.killSwitch.test.js src/components/BottomBar/BottomBar.test.jsx`
(si el worktree no tiene `node_modules`: `npm ci` primero, con Node de `/home/fruiz/.nvm/versions/node/v24.16.0/bin`)
Expected: FAIL. Faltan claves, `activarKillSwitch` no existe, `textoDeKillSwitch` no se exporta, y el chat muestra `**Error:** kill_switch_activo`.

- [ ] **Step 3: Implementar**

`i18n/es.js`:
- Borrar `killConfirm` y `killSwitchStoppedToast`.
- Reescribir el comentario de las líneas 102-104: `// killSwitchActive: aviso persistente. killSwitchToast / killSwitchReleasedToast: el evento de WS (llega a todas las pestañas, también a quien lo activó).`
- Agregar:

```js
  killConfirmTitle: 'Detener todo',
  killConfirmMessage: 'Se detienen la Mesa y todo lo que corre en LAS MANOS y en Jacobs. Lo que esté en vuelo se aborta.',
  killResumeButton: 'Reanudar',
  killResumeTitle: 'Reanudar JAX',
  killResumeMessage: 'La Mesa, LAS MANOS y Jacobs vuelven a aceptar trabajo. Lo abortado no se reanuda solo.',
  killResumeConfirm: 'Sí, reanudar',
  killSwitchReleasedToast: 'Kill switch liberado: JAX acepta trabajo',
  killSwitchErrorActivar: 'No se pudo activar el kill switch',
  killSwitchErrorReanudar: 'No se pudo reanudar',
  killSwitchErrorNoEscribible: 'El servidor no puede escribir el interruptor: nada cambió',
  killSwitchErrorAuditoria: 'El freno quedó puesto, pero la auditoría falló: revisá el registro del servidor',
  kill_switch_activo: 'Kill switch activo: JAX está detenido',
```

`i18n/en.js`: borrar las mismas dos y agregar:

```js
  killConfirmTitle: 'Stop everything',
  killConfirmMessage: 'The Mesa and everything running in LAS MANOS and Jacobs stop. Work in flight is aborted.',
  killResumeButton: 'Resume',
  killResumeTitle: 'Resume JAX',
  killResumeMessage: 'The Mesa, LAS MANOS and Jacobs accept work again. Aborted work does not resume by itself.',
  killResumeConfirm: 'Yes, resume',
  killSwitchReleasedToast: 'Kill switch released: JAX accepts work',
  killSwitchErrorActivar: 'Could not activate the kill switch',
  killSwitchErrorReanudar: 'Could not resume',
  killSwitchErrorNoEscribible: 'The server cannot write the switch: nothing changed',
  killSwitchErrorAuditoria: 'The brake stayed on, but the audit failed: check the server log',
  kill_switch_activo: 'Kill switch active: JAX is stopped',
```

`api/errores.js`, al final:

```js
// 423 `kill_switch_activo` (2026-09-16, frente B): chat, imagen, comando y
// pipelines responden así con el freno puesto. Devuelve el texto traducido,
// o null si el error es otro (cada pantalla pone su genérico).
export function textoDeKillSwitch(t, err) {
  return err?.response?.status === 423 && codigoDe(err) === 'kill_switch_activo' ? t.kill_switch_activo : null
}
```

`api/client.js`: constante `const KILL_SWITCH_ACTIVO = 'kill_switch_activo'` junto a `CAMBIO_REQUERIDO`. Primer bloque dentro del manejador de error de `api.interceptors.response.use`, antes del de U34:

```js
    // Kill switch (2026-09-16, frente B): cualquier pedido frenado enciende el
    // aviso, aunque el WS se haya perdido el evento. Sin reintento.
    if (err.response?.status === 423 && codigoDe(err) === KILL_SWITCH_ACTIVO) {
      useJaxStore.setState({ killSwitchActive: true })
      return Promise.reject(err)
    }
```

`store/useJaxStore.js`:
- En `handleEvent`, dejar `kill_switch_activated` como está y agregar:

```js
    if (event_type === 'kill_switch_released') {
      set({ killSwitchActive: false })
      get().addToast({ type: 'success', message: _t().killSwitchReleasedToast })
    }
```

- Reemplazar `activateKillSwitch` entero por:

```js
  // Kill switch real (2026-09-16, frente B). El estado sale de la respuesta
  // (o del evento de WS), nunca antes: si falla, no se finge que se detuvo.
  // El error se propaga para que KillSwitch lo muestre.
  activarKillSwitch: async () => {
    const { data } = await api.post('/admin/kill-switch/activar')
    set({ killSwitchActive: data.activo === true })
  },

  reanudarKillSwitch: async () => {
    const { data } = await api.post('/admin/kill-switch/reanudar')
    set({ killSwitchActive: data.activo === true })
  },
```

- En `loadState`, dentro del `set({...})`, agregar `killSwitchActive: data.kill_switch_active === true,`.

`components/BottomBar/KillSwitch.jsx` (reemplazo completo):

```jsx
import { memo, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import { codigoDe } from '../../api/errores'
import Dialogo from '../Dialogo'
import ConfirmacionSuma from '../ConfirmacionSuma'

// Kill switch real (2026-09-16, frente B). Muestra el estado REAL (loadState
// + eventos de WS + el 423 que enciende el interceptor). Sólo un superadmin
// activa y reanuda (el backend lo exige); cualquiera ve el aviso. Activar es
// rápido (Dialogo); reanudar pide la suma (ConfirmacionSuma). Si la llamada
// falla, se dice: no hay catch vacío.
const ERRORES = {
  kill_switch_no_escribible: 'killSwitchErrorNoEscribible',
  kill_switch_auditoria_fallida: 'killSwitchErrorAuditoria',
}

function KillSwitch() {
  const activo = useJaxStore((s) => s.killSwitchActive)
  const esSuperadmin = useJaxStore((s) => s.user?.role === 'superadmin')
  const activar = useJaxStore((s) => s.activarKillSwitch)
  const reanudar = useJaxStore((s) => s.reanudarKillSwitch)
  const { t } = useI18n()
  const [dialogo, setDialogo] = useState(null)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  async function ejecutar(accion, claveGenerica) {
    setError(null)
    setEnviando(true)
    try {
      await accion()
    } catch (err) {
      setError(ERRORES[codigoDe(err)] ?? claveGenerica)
    } finally {
      setEnviando(false)
      setDialogo(null)
    }
  }

  function abrir(cual) {
    setError(null)
    setDialogo(cual)
  }

  const aviso = error && <span role="alert" className="text-xs text-peligro">{t[error]}</span>

  if (activo) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 px-3 py-1 rounded-lg bg-peligro-fondo border border-peligro-solido text-peligro text-xs font-bold">
          <span className="w-2 h-2 rounded-full bg-peligro-solido" />
          {t.killSwitchActive}
        </div>
        {esSuperadmin && (
          <button
            type="button"
            onClick={() => abrir('reanudar')}
            className="px-2 py-1 rounded bg-superficie-2 text-texto hover:text-texto-fuerte text-xs font-semibold"
          >
            {t.killResumeButton}
          </button>
        )}
        {aviso}
        {dialogo === 'reanudar' && (
          <ConfirmacionSuma
            titulo={t.killResumeTitle}
            mensaje={t.killResumeMessage}
            textoConfirmar={t.killResumeConfirm}
            onConfirmar={() => ejecutar(reanudar, 'killSwitchErrorReanudar')}
            onCancelar={() => setDialogo(null)}
          />
        )}
      </div>
    )
  }

  if (!esSuperadmin) return null

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => abrir('activar')}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-peligro-fondo border border-peligro-borde hover:border-peligro-solido text-peligro text-xs font-bold uppercase tracking-widest transition-all"
        title={t.killTitle}
      >
        <span className="w-2 h-2 rounded-full bg-peligro-solido animate-pulse" />
        {t.killButton}
      </button>
      {aviso}
      {dialogo === 'activar' && (
        <Dialogo idTitulo="kill-switch-activar-titulo" titulo={t.killConfirmTitle} onCerrar={() => setDialogo(null)}>
          <p className="text-sm text-texto-suave mb-4">{t.killConfirmMessage}</p>
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => setDialogo(null)}
              className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors"
            >
              {t.cancel}
            </button>
            <button
              type="button"
              disabled={enviando}
              onClick={() => ejecutar(activar, 'killSwitchErrorActivar')}
              className="px-4 py-1.5 rounded-lg bg-peligro-solido hover:bg-peligro-solido-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors"
            >
              {t.killConfirmYes}
            </button>
          </div>
        </Dialogo>
      )}
    </div>
  )
}

export default memo(KillSwitch)
```

`BottomBar.jsx`: `import { textoDeKillSwitch } from '../../api/errores'`. En los cuatro `catch`, anteponer el texto del freno:
- `const detail = textoDeKillSwitch(t, err) || err.response?.data?.detail || t.errorFacet` (chat);
- lo mismo con `t.errorTask` (comando), `t.errorImagen` (imagen) y `t.errorPipeline` (pipeline).

Si A-51 ya pasó estos `catch` a `codigoDe`, se suma el caso `kill_switch_activo` a ese mapeo en lugar de esta línea.

`RightPanel.jsx`: `import { textoDeKillSwitch } from '../../api/errores'` y en `handleResume`, `setAviso({ pipelineId, clave: textoDeKillSwitch(t, e) ? 'kill_switch_activo' : 'approveError' })`. El render ya hace `t[avisoVigente.clave]`. Verificar con `grep -n "const { t }\|useI18n" RightPanel.jsx` que `t` está en el ámbito.

- [ ] **Step 4: Verde y la suite entera**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-b/frontend && pwd && npx vitest run --reporter=default --reporter=json --outputFile=/tmp/claude-1000/vitest-frente-b.json && node -e 'const r=require("/tmp/claude-1000/vitest-frente-b.json");console.log(r.numPassedTests,r.numFailedTests)'`
Expected: 0 failed. Pasan `contraste.test.js` (sin colores crudos) y la guarda de diálogos del navegador.

Correr dos veces. En `.github/workflows/policy.yml` (job `frontend-tests`), reemplazar `r.numPassedTests !== 448` por el número medido, con comentario: `// 448 -> <N> el 2026-09-16 (frente B, Task 9): KillSwitch.test.jsx reescrito (+N), useJaxStore.killSwitch.test.js (+5), client.killSwitch.test.js (+3), BottomBar.test.jsx (+1).`

Mutaciones (revertir):
- En `activarKillSwitch`, poner `set({ killSwitchActive: true })` antes del POST: cae `activarKillSwitch no enciende nada...`.
- En KillSwitch, reemplazar el `Dialogo` por una llamada directa: cae `abre un Dialogo propio...`.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-b add frontend/src/components/BottomBar/KillSwitch.jsx frontend/src/components/BottomBar/KillSwitch.test.jsx frontend/src/store/useJaxStore.js frontend/src/store/useJaxStore.killSwitch.test.js frontend/src/api/client.js frontend/src/api/errores.js frontend/src/api/client.killSwitch.test.js frontend/src/components/BottomBar/BottomBar.jsx frontend/src/components/BottomBar/BottomBar.test.jsx frontend/src/components/RightPanel/RightPanel.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-platform-frente-b commit -m "feat(kill-switch): el botón muestra el freno real, activa con Dialogo, reanuda con suma y dice cuando falla

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Carga — arnés previo al merge (gate) y guion k6

**Files:**
- Create (scratchpad, NO en el repo): `<scratchpad>/carga_kill_switch_test.py`, `<scratchpad>/carga-kill-switch.md`, `<scratchpad>/carga-kill-switch.json`
- Create (repo jax): `/home/fruiz/worktrees/jax-frente-b/loadtest/kill-switch.js`

**Interfaces:**
- Consumes: las rutas de las Tasks 7 y 8, `tests.identidades`, el fixture `client`.
- Produces: números (rps, p50/p95/p99) y el guion k6 para la Task 14.

- [ ] **Step 1: Worktrees de scratch.**
- `git -C /home/fruiz/jax-platform worktree add --detach /home/fruiz/worktrees/carga-ks-base origin/master` (la base, sin el freno);
- `git -C /home/fruiz/jax-platform worktree add --detach /home/fruiz/worktrees/carga-ks-head feat/kill-switch-real`.

El arnés se copia a `backend/tests/` de cada uno, NUNCA al de la rama.

- [ ] **Step 2: El arnés** — `carga_kill_switch_test.py`:

```python
"""Carga del frente B. Corre DENTRO de pytest (conftest: jax_memory_test, sello
y freno temporales). httpx sobre ASGITransport en el loop del portal. Nunca
/etc/jax/.env. Escribe CARGA_SALIDA (json)."""
import asyncio
import json
import os
import random
import time
from collections import Counter

import httpx
import pytest

from tests.identidades import auth, cabeceras, sql, token_para

TIENE_FRENO = os.path.exists(os.path.join(os.path.dirname(__file__), "..", "kill_switch.py"))
RESULTADOS = {}


async def _rafaga(app, metodo, ruta, cabeceras_, cuerpo, concurrencia, total):
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://carga") as c:
        latencias, codigos, cuerpos = [], Counter(), []
        semaforo = asyncio.Semaphore(concurrencia)

        async def uno():
            async with semaforo:
                t0 = time.perf_counter()
                r = await c.request(metodo, ruta, headers=cabeceras_, json=cuerpo)
                latencias.append((time.perf_counter() - t0) * 1000)
                codigos[r.status_code] += 1
                cuerpos.append(r.json() if r.headers.get("content-type", "").startswith("application/json") else None)

        t0 = time.perf_counter()
        await asyncio.gather(*(uno() for _ in range(total)))
        duracion = time.perf_counter() - t0
    latencias.sort()

    def p(q):
        return round(latencias[min(len(latencias) - 1, int(q * len(latencias)))], 2)

    return {"rps": round(total / duracion, 1), "p50": p(0.50), "p95": p(0.95), "p99": p(0.99),
            "codigos": dict(codigos), "cuerpos": cuerpos}


def _correr(client, *args):
    from main import app
    return client.portal.call(_rafaga, app, *args)


@pytest.fixture(autouse=True)
def _sin_modelo(monkeypatch):
    from api import chat as chat_mod

    async def sin_modelo(*a, **k):
        raise AssertionError("la carga nunca llama a un modelo")

    monkeypatch.setattr(chat_mod, "_invoke_facet", sin_modelo)


def test_A_camino_caliente_sin_freno(client, chat_sin_memoria):
    op = cabeceras(client, "carga-ks-operador")
    for c in (1, 10, 50):
        r = _correr(client, "POST", "/api/chat", op, {"message": "carga", "facet": "__carga__"}, c, 500)
        assert set(r["codigos"]) == {400}, r["codigos"]
        RESULTADOS[f"A_chat_400_c{c}"] = {k: v for k, v in r.items() if k != "cuerpos"}
    r = _correr(client, "GET", "/api/state", op, None, 50, 1000)
    assert set(r["codigos"]) == {200}
    RESULTADOS["A_state_c50"] = {k: v for k, v in r.items() if k != "cuerpos"}


@pytest.mark.skipif(not TIENE_FRENO, reason="base sin kill switch")
def test_B_la_mesa_frenada(client, usuarios, chat_sin_memoria):
    uid, _ = usuarios(role="superadmin")
    admin = auth(token_para(uid, role="superadmin"))
    op = cabeceras(client, "carga-ks-operador")
    assert client.post("/api/admin/kill-switch/activar", headers=admin).status_code == 200
    casos = {
        "chat": ("/api/chat", {"message": "carga", "facet": "jax_local"}),
        "image": ("/api/image/generate", {"prompt": "carga"}),
        "command": ("/api/command", {"command": "ls", "mode": "dry_run"}),
        "pipelines": ("/api/pipelines", {"name": "carga", "objective": "carga"}),
        "resume": ("/api/pipelines/00000000-0000-0000-0000-000000000000/resume", None),
    }
    for nombre, (ruta, cuerpo) in casos.items():
        for c in (10, 50, 100):
            r = _correr(client, "POST", ruta, op, cuerpo, c, 1000)
            assert set(r["codigos"]) == {423}, (nombre, r["codigos"])
            RESULTADOS[f"B_{nombre}_423_c{c}"] = {k: v for k, v in r.items() if k != "cuerpos"}
    for c in (10, 50):
        r = _correr(client, "GET", "/api/admin/kill-switch", admin, None, c, 500)
        assert set(r["codigos"]) == {200}
        RESULTADOS[f"C_get_admin_c{c}"] = {k: v for k, v in r.items() if k != "cuerpos"}
        antes = client.portal.call(sql, "SELECT COUNT(*) FROM kill_switch_audit WHERE user_id = %s", (uid,), True)[0][0]
        r = _correr(client, "POST", "/api/admin/kill-switch/activar", admin, None, c, 500)
        despues = client.portal.call(sql, "SELECT COUNT(*) FROM kill_switch_audit WHERE user_id = %s", (uid,), True)[0][0]
        assert set(r["codigos"]) == {200} and all(b["cambio"] is False for b in r["cuerpos"])
        assert antes == despues
        RESULTADOS[f"C_activar_idempotente_c{c}"] = {k: v for k, v in r.items() if k != "cuerpos"}
    client.post("/api/admin/kill-switch/reanudar", headers=admin)


@pytest.mark.skipif(not TIENE_FRENO, reason="base sin kill switch")
def test_D_carrera_activar_reanudar(client, usuarios):
    import interruptor
    uid, _ = usuarios(role="superadmin")
    admin = auth(token_para(uid, role="superadmin"))
    rng = random.Random(20260916)
    cambios_totales = 0
    for ronda in range(10):
        rutas = [rng.choice(["activar", "reanudar"]) for _ in range(20)]

        async def mezcla():
            from main import app
            transporte = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transporte, base_url="http://carga") as c:
                return await asyncio.gather(*(c.post(f"/api/admin/kill-switch/{r}", headers=admin) for r in rutas))

        respuestas = client.portal.call(mezcla)
        assert all(r.status_code == 200 for r in respuestas), [r.status_code for r in respuestas]
        cambios_totales += sum(r.json()["cambio"] for r in respuestas)
        filas = client.portal.call(sql, "SELECT accion FROM kill_switch_audit WHERE user_id = %s ORDER BY at, id", (uid,), True)
        acciones = [f[0] for f in filas]
        assert len(acciones) == cambios_totales, (ronda, len(acciones), cambios_totales)
        assert all(a != b for a, b in zip(acciones, acciones[1:])), acciones  # alternan: nunca dos iguales seguidas
        if acciones:
            assert interruptor.interruptor_activo() == (acciones[-1] == "activar")
    RESULTADOS["D_carrera"] = {"rondas": 10, "pedidos": 200, "cambios": cambios_totales}
    client.post("/api/admin/kill-switch/reanudar", headers=admin)


def test_zz_volcar(client):
    with open(os.environ["CARGA_SALIDA"], "w") as f:
        json.dump(RESULTADOS, f, indent=2)
```

- [ ] **Step 3: Correr base y HEAD** (cada uno dos veces; se anota la segunda):

```bash
S=/tmp/claude-1000/-home-fruiz/a45892c8-a810-4711-ac7c-1c7a9dd014c2/scratchpad
for W in base head; do
  cp $S/carga_kill_switch_test.py /home/fruiz/worktrees/carga-ks-$W/backend/tests/
  (cd /home/fruiz/worktrees/carga-ks-$W/backend && pwd && CARGA_SALIDA=$S/carga-ks-$W.json /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/carga_kill_switch_test.py -q -s -p no:cacheprovider)
done
```

Expected: base `2 passed, 2 skipped`; head `4 passed`. Sin 5xx.

**Criterio (gate de merge):**
- `A_chat_400_c50.p95(head) ≤ 1,10 × p95(base)`, y lo mismo en `A_state_c50`;
- B: 100 % 423 en las cinco rutas;
- C: `cambio=false` y 0 filas nuevas;
- D: filas = cambios, alternancia, y el archivo coincide con la última fila.

Si algo no se cumple, no hay merge: ronda de arreglos y la carga se repite sobre el HEAD nuevo.

- [ ] **Step 4: Informe** `<scratchpad>/carga-kill-switch.md`:
- tabla base/head (rps, p50/p95/p99 por escenario y concurrencia);
- el reparto de D;
- el EXPLAIN de `SQL_ULTIMO` que dio la Task 7, tal cual;
- con cuántos concurrentes empieza a degradar el camino del 423, que es el primer `c` donde p95 más que duplica al de `c=10`;
- veredicto.

Borrar los dos worktrees de scratch: `git -C /home/fruiz/jax-platform worktree remove /home/fruiz/worktrees/carga-ks-base && git -C /home/fruiz/jax-platform worktree remove /home/fruiz/worktrees/carga-ks-head`.

- [ ] **Step 5: El guion k6 para producción** — `/home/fruiz/worktrees/jax-frente-b/loadtest/kill-switch.js`:

```js
// Kill switch bajo carga, EN PRODUCCIÓN y SOLO CON EL FRENO PUESTO (frente B,
// 2026-09-16). LAS CUATRO DEL RENDIMIENTO, política 4.
//
// POR QUÉ ES SEGURO. El escenario `freno` manda un chat a una faceta que NO
// existe: con el freno puesto responde 423 antes de todo; si el freno no
// estuviera puesto, respondería 400 por faceta desconocida (api/chat.py
// valida antes de la memoria y del modelo). Nunca llega a un modelo. Además,
// setup() aborta si el freno no está puesto. `activar_idempotente` pide
// activar lo que ya está activo: 200 con cambio=false, sin fila de auditoría.
//
// USO (el TOKEN es un access token de superadmin que da Fernando para esta
// ventana; vence en 15 min; nunca se escribe en un archivo):
//   k6 run -e TOKEN=... loadtest/kill-switch.js

import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

const BASE = __ENV.BASE || 'http://127.0.0.1:8080';
const TOKEN = __ENV.TOKEN;
const VUS = parseInt(__ENV.VUS || '50', 10);
const CABECERAS = { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

http.setResponseCallback(http.expectedStatuses(200, 423));

const rampa = (vus) => [
  { duration: '5s', target: vus },
  { duration: '20s', target: vus },
  { duration: '5s', target: 0 },
];

export const options = {
  scenarios: {
    freno: { executor: 'ramping-vus', exec: 'freno', stages: rampa(VUS) },
    admin: { executor: 'ramping-vus', exec: 'admin', stages: rampa(Math.max(1, Math.floor(VUS / 5))) },
    activar_idempotente: { executor: 'ramping-vus', exec: 'activarIdempotente', stages: rampa(10) },
  },
  thresholds: {
    'http_req_duration{escenario:freno}': ['p(95)<500'],
    'http_req_duration{escenario:admin}': ['p(95)<500'],
    'http_req_duration{escenario:activar}': ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

export function setup() {
  if (!TOKEN) exec.test.abort('falta TOKEN');
  const r = http.get(`${BASE}/api/admin/kill-switch`, { headers: CABECERAS });
  if (r.status !== 200 || r.json('activo') !== true) {
    exec.test.abort(`el freno NO está puesto (status ${r.status}): esta carga sólo corre con el kill switch activo`);
  }
}

export function freno() {
  const r = http.post(`${BASE}/api/chat`, JSON.stringify({ message: 'carga-kill-switch', facet: '__carga_kill_switch__' }),
    { headers: CABECERAS, tags: { escenario: 'freno' } });
  check(r, { '423 kill_switch_activo': (x) => x.status === 423 && x.json('detail') === 'kill_switch_activo' });
}

export function admin() {
  const r = http.get(`${BASE}/api/admin/kill-switch`, { headers: CABECERAS, tags: { escenario: 'admin' } });
  check(r, { 'admin: activo': (x) => x.status === 200 && x.json('activo') === true });
  const s = http.get(`${BASE}/api/state`, { headers: CABECERAS, tags: { escenario: 'admin' } });
  check(s, { 'state: kill_switch_active': (x) => x.status === 200 && x.json('kill_switch_active') === true });
}

export function activarIdempotente() {
  const r = http.post(`${BASE}/api/admin/kill-switch/activar`, null, { headers: CABECERAS, tags: { escenario: 'activar' } });
  check(r, { 'activar sin cambio': (x) => x.status === 200 && x.json('cambio') === false });
}
```

Run: `/home/fruiz/bin/k6 inspect /home/fruiz/worktrees/jax-frente-b/loadtest/kill-switch.js > /dev/null && echo ok`
Expected: `ok` (valida la sintaxis sin disparar pedidos).

- [ ] **Step 6: Commit (jax)**

```bash
git -C /home/fruiz/worktrees/jax-frente-b add loadtest/kill-switch.js
git -C /home/fruiz/worktrees/jax-frente-b commit -m "feat(loadtest): k6 del kill switch (423 y admin), sólo con el freno puesto

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Review final, PRs, canario de CI y merge

- [ ] **Step 1: Review final** (opus), sobre `origin/master..feat/kill-switch-real` en los dos repos, con este brief:
  - las 10 discrepancias;
  - fail-closed en los tres arranques;
  - el orden de `activar` y `reanudar`;
  - que ningún lector quede fuera (`grep -rn "kill_switch\|PAUSE" --include=*.py`);
  - `Path.exists` sobre el freno (0 casos);
  - i18n y tokens;
  - P10.

  Los hallazgos se arreglan ANTES de los PRs: no se difieren.
- [ ] **Step 2: Rebase** sobre `origin/master` en los dos worktrees (coordinación con A/C/D) y suites completas otra vez (jax `tests-puros`; jax-platform con DB, sin DB y vitest). Si cambió master, la carga de la Task 10 se repite sobre el HEAD nuevo.
- [ ] **Step 3: PRs.** Primero jax-platform: `gh pr create --repo fjruizhn/jax-platform --base master --head feat/kill-switch-real --title "Kill switch real (frente B): escritor, auditoría, 423 en la Mesa y UI"`. Después jax: `gh pr create --repo fjruizhn/Jax --base master --head feat/kill-switch-real --title "Kill switch real (frente B): la ruta del freno desde JAX_KILL_SWITCH_PATH, lectores fail-closed y freno en vuelo"`. El cuerpo de cada uno lleva:
  - las discrepancias;
  - los números de la Task 10;
  - el orden de merge y deploy;
  - `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

  El PR de jax dice que `mirror-sync` queda rojo hasta mergear el de jax-platform, porque clona master.
- [ ] **Step 4: Canario (rojo sobre el sha real, por API).** En cada rama, un commit que rompe a propósito:
  - jax: en `jax/core/interruptor.py`, `except OSError:` → `return False`. Jobs esperados en rojo: `tests-puros` (y `mirror-sync` queda rojo por drift).
  - jax-platform, backend: en `kill_switch.exigir_mesa_libre`, `if activo():` → `if False:`. Job esperado en rojo: `backend-tests-con-db`.
  - jax-platform, frontend: en `activarKillSwitch`, `set({ killSwitchActive: true })` antes del POST. Job esperado en rojo: `frontend-tests`.

  Push. Verificar con `gh run list --repo <repo> --branch feat/kill-switch-real --json headSha,name,conclusion -L 20` que esos jobs dan `failure` sobre el sha del canario (`git -C <worktree> rev-parse HEAD`). Después `git -C <worktree> revert --no-edit HEAD`, push, y los mismos jobs en `success` sobre el sha nuevo.
- [ ] **Step 5: Gate y merge de jax-platform.**
  - `gh pr checks <N> --repo fjruizhn/jax-platform` sin nada fuera de SUCCESS;
  - `gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid` = `git ls-remote https://github.com/fjruizhn/jax-platform.git refs/heads/feat/kill-switch-real` = HEAD local;
  - `gh pr merge <N> --repo fjruizhn/jax-platform --merge --match-head-commit <sha>`.

  **Mergear NO despliega**: producción sirve desde `/home/fruiz/jax-platform`, que no se toca hasta la Task 13.
- [ ] **Step 6: Gate y merge de jax.**
  - `gh run rerun <id-del-run> --repo fjruizhn/Jax --failed` (ahora `mirror-sync` clona el master con `backend/interruptor.py`);
  - el mismo gate por `headRefOid`;
  - `gh pr merge <N> --repo fjruizhn/Jax --merge --match-head-commit <sha>`.

---

### Task 12: Infra con sudo (la corre el controlador, con GO de Fernando)

Todo con `sudo -n`. Cada comando se muestra a Fernando antes de correrlo.

- [ ] **Step 1: Línea base**

```bash
stat -c '%U:%G %a %s' /etc/jax/.env
sudo -n grep -c '^JAX_KILL_SWITCH_PATH=' /etc/jax/.env
test -e /etc/jax/PAUSE && echo "PAUSE VIEJO PRESENTE" || echo "sin PAUSE viejo"
test -d /etc/jax/interruptor && echo "YA EXISTE" || echo "no existe"
sudo -n tail -c1 /etc/jax/.env | od -An -c
```

Expected:
- `root:fruiz 660 <tamaño>`, `0`, `sin PAUSE viejo`, `no existe`, `\n`.

Si hay PAUSE viejo: PARAR. JAX está frenado hoy y el estado tiene que cruzar a la ruta nueva. Se pregunta a Fernando.

- [ ] **Step 2: Backup del .env con restauración probada (Principio VI)**

```bash
TS=$(date +%Y%m%d-%H%M%S)
sudo -n cp -a /etc/jax/.env /etc/jax/.env.backup-pre-kill-switch-$TS
sudo -n cmp /etc/jax/.env /etc/jax/.env.backup-pre-kill-switch-$TS && echo IDENTICO
sudo -n stat -c '%U:%G %a %s' /etc/jax/.env /etc/jax/.env.backup-pre-kill-switch-$TS
echo $TS
```

Expected: `IDENTICO` y el mismo dueño, modo y tamaño en los dos. Anotar `$TS` en el ledger.

- [ ] **Step 3: Directorio y variable**

```bash
sudo -n install -d -o root -g fruiz -m 2770 /etc/jax/interruptor
stat -c '%U:%G %a' /etc/jax/interruptor
echo 'JAX_KILL_SWITCH_PATH=/etc/jax/interruptor/PAUSE' | sudo -n tee -a /etc/jax/.env > /dev/null
sudo -n grep -c '^JAX_KILL_SWITCH_PATH=/etc/jax/interruptor/PAUSE$' /etc/jax/.env
stat -c '%U:%G %a' /etc/jax/.env
```

Expected: `root:fruiz 2770`, `1`, `root:fruiz 660`.

- [ ] **Step 4: La restauración se prueba en seco**

```bash
sudo -n bash -c "diff <(grep -v '^JAX_KILL_SWITCH_PATH=' /etc/jax/.env) /etc/jax/.env.backup-pre-kill-switch-$TS" && echo "RESTAURABLE"
```

Expected: `RESTAURABLE`: el backup es el archivo actual sin la línea nueva. Restaurar sería `sudo -n cp -a /etc/jax/.env.backup-pre-kill-switch-$TS /etc/jax/.env`, y no se ejecuta.

- [ ] **Step 5: Los procesos pueden escribir y leer (como `fruiz`, sin sudo)**

```bash
/home/fruiz/jax/.venv/bin/python - <<'PY'
import os, tempfile
d = "/etc/jax/interruptor"
fd, p = tempfile.mkstemp(prefix=".sonda-", dir=d); os.close(fd)
print("escribe:", os.path.exists(p), oct(os.stat(p).st_mode & 0o7777), os.stat(p).st_gid)
os.unlink(p)
print("lista vacía:", [x for x in os.listdir(d)])
PY
```

Expected:
- `escribe: True`, con gid 1000 (el setgid hereda `fruiz`);
- `lista vacía: []`.

Los servicios en marcha no ven la variable hasta reiniciar, y el código viejo la ignora: no hay efecto todavía.

---

### Task 13: Deploy con 0 en vuelo — lectores primero, escritor último

**Orden y por qué.**
1. Infra (Task 12).
2. jax, los lectores: LAS MANOS con Jacobs. Desde acá el REPL y `--task` también leen la ruta nueva, porque el lanzador corre desde `~/jax`.
3. Prueba del lector en producción.
4. Backend de jax-platform, el escritor.
5. Frontend.

Entre 2 y 4 no existe escritor nuevo: el frontend viejo sigue haciendo POST a `/api/kill-switch`, que da 404 como hoy. Así nunca hay un lector mirando la ruta vieja mientras el escritor escribe la nueva.

- [ ] **Step 1: 0 en vuelo** (sólo lectura):

```bash
pgrep -af "jax.core.main" || echo "sin REPL ni --task"
/home/fruiz/jax/.venv/bin/python - <<'PY'
import json, time
ultimo = {}
with open('/home/fruiz/jax/las_manos/logs/motor_jobs.jsonl', encoding='utf-8') as f:
    for linea in f:
        d = json.loads(linea); ultimo[d['job_id']] = d
vivos = [j for j, d in ultimo.items() if d['status'] in ('pending', 'running') and time.time() - d['created_at'] < 86400]
print('jobs de motor en vuelo (24 h):', len(vivos), vivos[:5])
PY
( set -a; . /etc/jax/.env; set +a
  mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -N -e \
  "SELECT status, COUNT(*) FROM jacobs_pipelines WHERE status IN ('pending','running') GROUP BY status" )
```

Expected: `sin REPL ni --task`, `0` jobs y ninguna fila. Si algo está en vuelo, se espera o se pregunta a Fernando. Nunca se reinicia encima.

- [ ] **Step 2: Línea base**

```bash
curl -s http://127.0.0.1:7777/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/admin/kill-switch/activar
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
git -C /home/fruiz/jax rev-parse HEAD; git -C /home/fruiz/jax-platform rev-parse HEAD
```

Anotar todo:
- `kill_switch_active` false;
- `200`;
- el código actual de la ruta nueva (se espera `404` o `405`);
- el hash servido;
- los dos sha, que son el rollback.

- [ ] **Step 3: jax (lectores)**

```bash
git -C /home/fruiz/jax fetch origin && git -C /home/fruiz/jax switch master && git -C /home/fruiz/jax pull --ff-only
git -C /home/fruiz/jax log -1 --oneline
readlink /home/fruiz/jax/las_manos/interruptor.py
sudo -n /usr/bin/systemctl restart jax-las-manos.service
sleep 3; curl -s http://127.0.0.1:7777/health
PID=$(systemctl show -p MainPID --value jax-las-manos.service)
tr '\0' '\n' < /proc/$PID/environ | grep '^JAX_KILL_SWITCH_PATH='
readlink /proc/$PID/cwd
sudo -n /usr/bin/journalctl -u jax-las-manos.service --since '-3 min' --no-pager | tail -40
```

Expected:
- HEAD = el merge de jax;
- `../jax/core/interruptor.py`;
- `/health` con `"status":"alive"` y `"kill_switch_active":false`;
- `JAX_KILL_SWITCH_PATH=/etc/jax/interruptor/PAUSE`;
- cwd `/home/fruiz/jax/las_manos`;
- journal sin tracebacks.

Rollback si no arranca: `git -C /home/fruiz/jax switch --detach <sha-de-la-línea-base> && sudo -n /usr/bin/systemctl restart jax-las-manos.service`. La variable queda: es inocua para el código viejo.

- [ ] **Step 4: El lector ve el freno nuevo en producción** (GO explícito de Fernando: frena JAX unos segundos, con 0 en vuelo):

```bash
cd /home/fruiz/jax/las_manos && pwd && /home/fruiz/jax/las_manos/.venv/bin/python - <<'PY'
import time, json, urllib.request
from pathlib import Path
from interruptor import escribir_pausa, borrar_pausa, interruptor_activo
ruta = Path("/etc/jax/interruptor/PAUSE")
salud = lambda: json.load(urllib.request.urlopen("http://127.0.0.1:7777/health"))["kill_switch_active"]
print("antes:", salud())
print("puesto:", escribir_pausa(ruta, '{"accion": "prueba-de-lector-deploy-frente-b"}'))
print("LAS MANOS lo ve:", salud())
print("quitado:", borrar_pausa(ruta))
print("después:", salud(), interruptor_activo(ruta))
PY
```

Expected: `antes: False`, `puesto: True`, `LAS MANOS lo ve: True`, `quitado: True`, `después: False False`. Tiempo total menor a 2 s.

- [ ] **Step 5: jax-platform backend (escritor)**

```bash
git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only
sudo -n /usr/bin/systemctl restart jax-platform.service
sleep 4; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health
PID=$(systemctl show -p MainPID --value jax-platform.service)
tr '\0' '\n' < /proc/$PID/environ | grep '^JAX_KILL_SWITCH_PATH='
readlink /proc/$PID/cwd
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/admin/kill-switch/activar
test -e /etc/jax/interruptor/PAUSE && echo "PUESTO (MAL)" || echo "suelto"
sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40
( set -a; . /etc/jax/.env; set +a
  mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e \
  "SHOW CREATE TABLE kill_switch_audit\G; SELECT COUNT(*) FROM kill_switch_audit; EXPLAIN SELECT a.accion, a.user_id, u.email, a.at FROM kill_switch_audit a LEFT JOIN jax_users u ON u.user_id = a.user_id ORDER BY a.at DESC, a.id DESC LIMIT 1" )
```

Expected:
- `200`;
- la variable en el entorno del proceso;
- cwd `/home/fruiz/jax-platform/backend`;
- la ruta nueva sin token da `401` (antes era el código de la línea base);
- `suelto`, y journal sin tracebacks;
- tabla con el CHECK y el índice, `0` filas;
- EXPLAIN sin filesort (con 0 filas puede decir "Impossible WHERE" o similar: se anota tal cual).

Sin dump: la migración sólo hace `CREATE TABLE IF NOT EXISTS` de una tabla nueva (Task 6, Step 3), y no hay ALTER ni DML sobre tablas existentes.

- [ ] **Step 6: Frontend**

```bash
cd /home/fruiz/jax-platform/frontend && pwd && npm run build && ls dist/assets/index-*.js
set -a; . /etc/jax/.env; set +a
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-kill-switch-$(date +%Y%m%d-%H%M%S) && sudo diff -rq /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-kill-switch-* | tail -1; echo backup-ok"
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

Expected: `backup-ok`, con el backup IDÉNTICO (diff vacío). Si ya hay backups anteriores, el diff se hace contra el nombre exacto recién creado. El `index-*.js` servido es el que acaba de construirse. Anotar el hash.

---

### Task 14: Verificación en vivo del peor caso (con Fernando) y k6 en producción

- [ ] **Step 1: La UI, en claro/oscuro y es/en** (Fernando, superadmin, pestaña nueva o recarga forzada):
  1. El botón KILL abre "Detener todo". Escape cierra; el foco vuelve al botón; el fondo no responde a Tab. Cancelar: no pasa nada (`test -e /etc/jax/interruptor/PAUSE` → no existe).
  2. Un usuario de prueba operator (si Fernando lo crea) no ve KILL.
- [ ] **Step 2: EL PEOR CASO.** Fernando lanza desde la Mesa un pipeline con un step de motor (objetivo largo; lo elige Fernando). El controlador vigila, en sólo lectura:

```bash
tail -n 0 -F /home/fruiz/jax/las_manos/logs/motor_jobs.jsonl | /home/fruiz/jax/.venv/bin/python -u -c "
import sys, json
for l in sys.stdin:
    d = json.loads(l); print(d['job_id'][:8], d['motor'], d['status'], (d.get('error') or '')[:90], d.get('finished_at'))"
```

Si en 2 minutos no aparece ningún job `running` del pipeline, PARAR y preguntar a Fernando qué objetivo usar. No se inventa un dispatch a mano. Con el job en `running`, Fernando pulsa KILL y confirma. Se anota:
- el mtime del freno: `stat -c %y /etc/jax/interruptor/PAUSE`;
- el `finished_at` y el `error` del job: se espera `failed` con `killed_by_switch` en ≤ 5 s (≤ 0,25 s si lo cortó Jacobs);
- `curl -s http://127.0.0.1:7777/health` → `kill_switch_active: true`;
- el estado final del pipeline (sólo lectura): `SELECT status FROM jacobs_pipelines WHERE pipeline_id = '<id>'` y `SELECT event_type, payload FROM jacobs_events WHERE pipeline_id = '<id>' ORDER BY id DESC LIMIT 5`. Antes se verifica el nombre de la tabla de eventos con `SHOW TABLES LIKE 'jacobs%'`.
- la auditoría: `SELECT accion, user_id, at FROM kill_switch_audit ORDER BY id DESC LIMIT 1` → `activar` con el id de Fernando.
- [ ] **Step 3: La Mesa frenada.**
  - Chat, imagen, comando y pipeline muestran "Kill switch activo: JAX está detenido" (y en en: "Kill switch active: JAX is stopped").
  - Una segunda pestaña o navegador ve el aviso sin recargar, por el evento de WS.
  - El ojo HAL dice KILL SWITCH.
  - `test -e /etc/jax/PAUSE` → no existe: la ruta vieja no se usa.
- [ ] **Step 4: k6 con el freno puesto** (Fernando pasa un access token de superadmin para esta ventana):

`/home/fruiz/bin/k6 run -e TOKEN=<token> /home/fruiz/jax/loadtest/kill-switch.js`

Expected: exit 0 y thresholds en verde. Anotar rps, p95 por escenario y `checks`. Anotar también si hubo que bajar `VUS` por degradación (se prueba `-e VUS=100` y se anota con cuántos p95 más que duplica). Después, `SELECT COUNT(*) FROM kill_switch_audit` sigue igual que en el Step 2 (el `activar_idempotente` no escribe).
- [ ] **Step 5: Reanudar.**
  - Fernando pulsa Reanudar, resuelve la suma y confirma. Aparece el toast "Kill switch liberado".
  - `/health` → `kill_switch_active: false`; el archivo no existe; la auditoría tiene `reanudar`.
  - Un chat a jax_local ("hola") responde. Un pipeline nuevo arranca.
- [ ] **Step 6: Cierre.** Borrar el backup del frontend en la VM sólo cuando Fernando dé por buena la verificación. Anotar el nombre del backup en DEUDA y fijar la fecha de borrado en la entrada.

---

### Task 15: Biblioteca

- [ ] **Step 1: `jax/DEUDA.md`** (PR propio en jax, rama `docs/kill-switch-real`). Sección nueva inmediatamente antes de la línea `## Cerrado — tanda A: gobernanza con el catálogo de la DB, \`capability.mode\`, rol \`plataforma\` (2026-09-14)`:
  - `## Cerrado — kill switch real (2026-09-16)`;
  - **VERDAD OPERACIONAL** con fecha y hora del deploy verificado;
  - los PRs y sha de los dos repos, y el `index-*.js`;
  - las 10 discrepancias y cómo se resolvió cada una;
  - el defecto de `Path.exists`, con la medición;
  - la carrera `cancelled` contra `killed_by_switch`;
  - los números de la Task 10 y del k6 de la Task 14;
  - el EXPLAIN tal cual;
  - el peor caso en vivo: tiempos, estado del job, estado del pipeline, auditoría;
  - el backup de `.env` con su `$TS` y la restauración probada;
  - los límites declarados (P2: chat o imagen en vuelo) y las respuestas de Fernando a P1 y P2.
- [ ] **Step 2: `jax/CONTEXT.md`.**
  - §2, línea 19: `- Kill Switch: archivo de \`JAX_KILL_SWITCH_PATH\` (en \`/etc/jax/.env\`; directorio \`/etc/jax/interruptor/\` root:fruiz 2770). Lo escribe sólo jax-platform (Admin, con auditoría en \`kill_switch_audit\`); lo leen LAS MANOS, Jacobs (por step, cada 250 ms) y el REPL. Sin la variable nada arranca.`
  - Al final de §9, una entrada `- **2026-09-16: Kill switch real.** ...` con qué se hizo y por qué, y las lecciones:
    - un freno sin escritor es un cartel;
    - `Path.exists` falla abierto ante EACCES;
    - Jacobs cancela antes que el watcher y eso cambiaba la causa registrada;
    - lectores primero, escritor último.

    Como pendientes, sólo lo que Fernando decida en P1 y P2, con fecha.
- [ ] **Step 3: PR de docs, CI verde por headSha y merge.** Después, `git -C /home/fruiz/jax pull --ff-only`. Borrar los worktrees `/home/fruiz/worktrees/jax-frente-b` y `/home/fruiz/worktrees/jax-platform-frente-b` y las ramas locales mergeadas. Actualizar la memoria del proyecto y el ledger.

---

## Autorrevisión (hecha al escribir el plan)

**Cobertura del spec B:**

| Requisito | Dónde |
|---|---|
| Directorio `/etc/jax/interruptor/` root:fruiz 2770, archivo `PAUSE`, variable en `.env` | Task 12 |
| Lectores de jax leen `JAX_KILL_SWITCH_PATH`; `kill_switch_path` de config.toml se elimina | Tasks 3, 4 y 5 (+ los que el spec no nombraba, discrepancia 1) |
| Sin la variable, arrancan con error explícito | Task 3 (`server.py` al importar), Task 5 (REPL y `--task`), Task 2 (plataforma); Jacobs lanza en cada chequeo |
| `kill_switch.py` con `estado`, `activar`, `reanudar`; escritura atómica | Tasks 1, 2 y 6 |
| Tabla `kill_switch_audit` (id, accion, user_id, at, índice por `at`) | Task 6 (+ CHECK de acción), EXPLAIN en las Tasks 7 y 13 |
| Endpoints superadmin GET, activar y reanudar | Task 7 |
| Evento WS activated/released a todos | Task 6 (`publicar_a_todos`, discrepancia 7) |
| 423 `kill_switch_activo` en chat, image, command y creación de pipelines | Task 8 (+ resume, discrepancia 6) |
| Lo que corre en LAS MANOS aborta; Jacobs aborta | Task 3 (watchers fail-closed, carrera), Task 4 (por step, no sólo por ola) |
| UI con estado real; Dialogo para activar; ConfirmacionSuma para reanudar; sin `catch {}` | Task 9 |
| Peor caso probado | Tasks 3 y 4 (motor y step en vuelo), Task 8 (Mesa 423 → reanudar → acepta), Task 3 cross-repo (escritor real de la plataforma), Task 14 (en vivo) |
| Deploy: sudo con backup, reinicios, 0 en vuelo | Tasks 12 y 13 |
| Reglas comunes (TDD, i18n, tokens, fail-closed, carga, CI con canario, mirror-sync, Biblioteca) | Global Constraints, Tasks 10, 11 y 15 |

**Placeholders:** los únicos valores que se completan al ejecutar son medidos o de la ejecución: `<N>` del PR, `<sha>`, `<id>` del pipeline, `<token>`, los pisos medidos y `$TS`. No hay "TBD" ni pasos sin código.

**Consistencia de nombres:** verificada con búsqueda sobre este documento. Son iguales en las Interfaces, las implementaciones y los tests:
- funciones y clases: `ruta_del_interruptor`, `interruptor_activo`, `escribir_pausa`, `borrar_pausa`, `correr_con_interruptor`, `InterruptorSinConfigurar`, `InterruptorActivado`, `InterruptorNoEscribible`, `AuditoriaDelInterruptorFallida`, `exigir_mesa_libre`, `publicar_a_todos`, `activarKillSwitch`, `reanudarKillSwitch`, `textoDeKillSwitch`;
- constantes: `RUTAS_FRENADAS`, `SQL_ULTIMO`;
- códigos: `kill_switch_activo`, `kill_switch_no_escribible`, `kill_switch_auditoria_fallida`;
- eventos: `kill_switch_activated`, `kill_switch_released`.

**Riesgos:**
1. **Carrera entre el cancel de Jacobs y el watcher.** La cierra la rama nueva del `CancelledError` (Task 3). Si el worker tiene más de un bloque que marca CANCELLED, el paso exige tocarlos todos (`grep`).
2. **`import main` en el runner sin DB** (Task 2, control). Si cae por otra razón, se para y se investiga; no se relaja el test.
3. **El EXPLAIN sobre una tabla chica.** Se siembran 300 filas + `ANALYZE`. Si hay filesort, primero se mide un índice `(at, id)`.
4. **Conflictos con los frentes A, C y D en archivos compartidos.** Rebase antes del PR y reglas de resolución en Global Constraints.
5. **El k6 en producción.** Es seguro por diseño: faceta inexistente, `setup` que aborta sin freno, activar idempotente. Aun así corre en la ventana de verificación, con GO.
