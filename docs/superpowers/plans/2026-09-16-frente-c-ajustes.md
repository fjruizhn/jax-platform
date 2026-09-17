# Frente C · Ajustes que mandan — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que los cinco ajustes de Admin → Configuración (`session_timeout_min`, `max_pipelines`, `web_task_retention_days`, `lang_default`, `system_name`) manden de verdad sobre el código, con el valor que el código hace cumplir hoy como valor inicial.

**Architecture:** un módulo `backend/ajustes.py` es la única lectura tipada y validada de esas cinco claves de `axioma_config`. Lee las cinco en una consulta por PRIMARY, las cachea con TTL (`JAX_AJUSTES_TTL_S`) y el PUT de `/api/admin/config` invalida el caché de forma explícita (proceso único, `exigir_un_solo_proceso`). Un valor ausente o inválido lanza `AjusteIlegible` → 503 `ajuste_ilegible` con la clave, nunca un default. Los consumidores: emisión y renovación del refresh (`api/auth.py` + `auth/jwt.py`), el cupo de pipelines (`api/pipelines.py` + `jax_engine/resource_manager.py`), el reaper de owner files (`jax_engine/owner_cleanup.py`) y el endpoint público `GET /api/apariencia`, que el frontend usa para el idioma inicial y el nombre del sistema (título del documento, Login, logotipo del encabezado y cabecera de Admin). Una migración de datos, que corre **una sola vez** (tabla marcador `axioma_migracion_de_datos`), fija los valores de hoy.

**Tech Stack:** FastAPI + aiomysql (Python 3.14 en `backend/.venv`), MariaDB 12.3 (`jax_memory_test` para tests), python-jose, React 19 + zustand 5 + vitest 4, k6 v2.2.0 (`~/bin/k6`).

**Spec:** `docs/superpowers/specs/2026-09-16-hallazgos-auditoria-design.md`, sección **C** (en la rama `docs/hallazgos-auditoria-2026-09-16`, worktree `/home/fruiz/worktrees/jax-platform-hallazgos-docs`). Evidencia cruda en `docs/superpowers/specs/anexo-a/`. Quien ejecuta lee el spec y este plan.

---

## Discrepancias con el spec (medidas contra `26c9cd5` y `jax` `bd95237`, 2026-09-16)

Ninguna cambia la tabla de semántica del spec. Cada una trae la evidencia y la opción más fiel. **Las marcadas con GATE se le presentan a Fernando antes de ejecutar la tarea indicada.**

1. **El refresh no se rota: la vida de la sesión es ABSOLUTA, no deslizante.** El spec nombra como consumidor la "emisión/rotación de refresh". No hay rotación: `api/auth.py:180-181` — *"La cookie NO se rota: rotarla haría deslizante la sesión de 7 días"*. `/refresh` solo emite un access nuevo. Solo emiten refresh `login` (`api/auth.py:161`) y `POST /me/password` (`api/auth.py:335`), los dos por `_emitir_tokens`. **Opción fiel:** `session_timeout_min` = vida absoluta del refresh desde que se emitió. Se aplica en tres lugares: el `exp` del JWT, el `max_age` de la cookie y, **además, en `/refresh`**, contra un claim `iat` nuevo. Sin lo último, acortar el ajuste no alcanzaría a las sesiones abiertas hasta 7 días después: el ajuste no mandaría. **Consecuencia (GATE, Task 16):** un refresh emitido antes del deploy no trae `iat` y se rechaza con 401 `sesion_expirada`, así que **cada sesión abierta vuelve a iniciar sesión una vez** después del deploy. La alternativa es suponer `iat = exp − 604800` para los tokens viejos, pero es código que queda muerto a los 7 días (una solución temporal), así que no se hace.
2. **`max_pipelines` no puede superar el tope de Jacobs, y la pantalla hoy ofrece hasta 5.** `jax/jacobs/policy.py:17` define `MAX_PARALLEL_PIPELINES = 3`, con la cabecera *"Candados duros: no diferibles, no configurables en v0.1"*. `jacobs/store.py:357-365` cuenta **todos** los pipelines `pending/running`, de todos los tenants e invocadores, y `jacobs/routes.py:145-155` rechaza el cuarto con 422. `AdminSettings.jsx:97` ofrece `max="5"`: un 4 o un 5 serían una promesa que Jacobs no cumple. **Decisión con evidencia: el valor NO viaja a jax.** Es una cuota por tenant de la plataforma, acotada por arriba por el candado global de Jacobs, que sigue sin ser configurable. Para que ese techo no se desincronice, `backend/ajustes.py` lleva una copia textual de `MAX_PARALLEL_PIPELINES` y se agrega la familia `tope_pipelines` a `jax/scripts/check_mirror_sync.py` (Task 14). Rango del servidor: 1..3.
3. **`web_task_retention_days` gobierna menos de lo que su nombre promete.** `owner_cleanup.py` solo borra los `web-task-*_owner.json`, y lo hace cuando la misión y el resultado ya no están o cuando el owner file supera la edad (hoy 30 días fijos, `owner_cleanup.py:24`). La misión y el resultado los poda `jax/scripts/cleanup.sh`, por CANTIDAD (deja los últimos 10) y sin scheduler. Cuando se borra el owner file, `GET /api/command/{id}` responde 404 al dueño (`api/command.py:79-82`). **Opción fiel:** el consumidor es el del spec (`owner_cleanup`). Su efecto real, "días que el dueño puede ver el resultado de una tarea web", queda dicho en la ayuda de la pantalla (i18n). **GATE (Task 18):** la poda por cantidad de `cleanup.sh`, que no tiene scheduler, queda fuera de la sección C. Se registra como HECHO en DEUDA y se le pregunta a Fernando si entra en otro frente. No se decide acá.
4. **`system_name` = "valor actual": hoy la fila NO manda.** La UI muestra "Axioma" fijo desde i18n (`brandName`, `loginTitle`, `loginButton`, `adminBack`, `smtpDesc`, y `AdminSidebar.jsx:26` literal), sin importar lo que diga la fila. `DEFAULT_CONFIG` siembra "Axioma", pero un admin pudo haberla cambiado sin efecto visible. **Opción fiel:** la migración conserva la fila si existe (INSERT IGNORE) y la crea con "Axioma" si falta. **GATE (Task 16, Step 1):** si el dump muestra una fila `system_name` distinta de `Axioma`, o una que el validador rechaza, se PARA y se le pregunta a Fernando qué nombre rige: aplicarla cambiaría la UI al desplegar.
5. **"Encabezado" incluye el logotipo, que hoy es marca y no texto.** `LogoAxioma.jsx` y `AdminSidebar.jsx:24-26` declaran "Axioma" como *marca, no texto a traducir*. **Opción fiel:** el texto del logotipo y de la cabecera de Admin pasa a ser `system_name` (el lema sigue en i18n, y la tipografía y los tokens dorados no cambian). También se interpolan `adminBack` y `smtpDesc`, que nombran al sistema: dejar "Axioma" fijo ahí contradiría el ajuste en la misma pantalla. La meta `description` de `index.html` ("En memoria de Jairo Urbina") no se toca: es un homenaje, no el nombre de la instancia. El `<title>` estático de `index.html` queda como respaldo hasta que corra el JS.
6. **Los defaults de `DEFAULT_CONFIG` contradicen al código** (`config_admin.py:13-21`: sesión `60`, pipelines `1`, retención `7`; el código hace cumplir 10080 / 3 / 30). Las filas de producción probablemente muestran esos valores sin que manden. No choca con el spec: es justo lo que la migración corrige. Las cinco claves salen de `DEFAULT_CONFIG` (si faltan, la respuesta tiene que ser un error visible y no una fila recreada en silencio con un default).
7. **Riesgo que el spec no nombra: 503 en el login.** "Valor ilegible → 503" aplicado a `session_timeout_min` deja a TODOS sin poder iniciar sesión, incluido el admin que lo arreglaría. Mitigaciones del plan: (a) cada ajuste se valida por separado, así que un `system_name` roto no tumba el login; (b) el PUT valida con la regla de igualdad de la base (collation), así que solo una edición SQL directa deja un valor ilegible; (c) el runbook SQL de recuperación queda en DEUDA (Task 18). La semántica del spec se mantiene.
8. **Solapamientos con el frente A (mismos archivos).** A-17 (`ws_notifications` en `DEFAULT_CONFIG`), A-19 (`_crear_token` en `auth/jwt.py`), A-24 (quitar el `asyncio.Lock` de `ResourceManager`), A-29 (`diccionarioActivo()` en el store) y A-51 (código estable para "Límite de N pipelines"). Este plan: no toca `ws_notifications`; cambia la firma pública de `create_refresh_token`; cambia la firma de `can_start_pipeline` sin tocar el lock; hace que el store use `idiomaInicial()` (A-29 lo reubica después); y resuelve **el mensaje de límite de pipelines** con código `pipelines_limite_alcanzado` + `limite`, así que A-51 ya no lo necesita. **El que mergea segundo rebasa y re-mide pisos.**

---

## Global Constraints

- **Worktree:** todo se hace en `/home/fruiz/worktrees/jax-platform-frente-c`, rama `feat/ajustes-que-mandan`. **Nunca** se edita, testea ni commitea en `/home/fruiz/jax-platform` ni en `/home/fruiz/jax`: son los checkouts de producción. La única excepción es el deploy (pull `--ff-only` después del merge). La parte de jax va en `/home/fruiz/worktrees/jax-frente-c`, rama `feat/espejo-tope-pipelines`.
- **Git:** `git -C <ruta absoluta>` siempre. Archivos de a uno (`git add <ruta>`), **nunca** `git add -A` ni `git add .`, y nunca `.impeccable/` ni el symlink de `node_modules`. **Nunca `git stash`.** Commits: asunto, cuerpo y trailer:
  ```bash
  git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "<asunto>" -m "<cuerpo>" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
  ```
  El cuerpo de cada PR termina con `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Python:** `/home/fruiz/jax-platform/backend/.venv/bin/python` (solo se usa, no se modifica), con cwd `backend/` del worktree. Forma de todos los comandos de test: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest <archivo> -v`.
- **Barrera de DB:** `/etc/jax/.env` apunta a **PRODUCCIÓN** fuera de pytest. Solo pytest toca la DB, y `tests/conftest.py` fuerza `JAX_DB_NAME=jax_memory_test`. Ningún script suelto carga `/etc/jax/.env` sin fijar antes `JAX_DB_NAME=jax_memory_test` (la única excepción son los pasos de deploy y de verificación en vivo, que declaran que tocan producción). Dentro de una corrutina corrida por `client.portal.call` no va `pytest.raises`: la corrutina captura y devuelve, y la aserción va afuera.
- **node_modules:** `ln -s /home/fruiz/jax-platform/frontend/node_modules /home/fruiz/worktrees/jax-platform-frente-c/frontend/node_modules` (no se commitea). Vitest: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run <archivo>`.
- **TDD:** cada test nuevo se ve **rojo contra el código viejo** antes del arreglo, y el paso lo dice con la razón esperada (un control que no falla no valida). Los tests de guarda que ya pasan se declaran como tales.
- **i18n:** ningún texto visible literal. Toda clave nueva o cambiada va en `frontend/src/i18n/es.js` **y** `en.js`, y un test afirma que existe en los dos. Los errores del backend son **códigos estables** (`detail` string o `{code, ...}`).
- **Dark/light:** solo clases de tokens ya existentes (`text-texto-tenue`, `bg-hundido`, `border-borde-control`...); `src/tema/contraste.test.js` escanea todo `src`. Ningún color nuevo.
- **Diálogos:** nada de `confirm/alert/prompt` (con o sin `window.`). Este frente no agrega diálogos.
- **Sin hardcoding:** el TTL sale de `JAX_AJUSTES_TTL_S` (default 30, falla fuerte si no es positivo y finito). Los rangos viven en `ajustes.DEFINICIONES` y la pantalla los recibe del servidor. El tope de pipelines es el espejo de Jacobs.
- **Fail-closed:** ajuste ausente o inválido → `AjusteIlegible` → 503 `{"detail": {"code": "ajuste_ilegible", "clave": ...}}`. En el reaper (fondo), un ajuste ilegible **no borra nada** y registra ERROR. Todo `except Exception` nuevo lleva `# fail-soft: <razón>` (scanner `tests/test_no_fail_open_except.py`); este plan no agrega ninguno.
- **Caché:** el de `ajustes.py` declara su invalidación en el mismo commit que lo crea: TTL + `ajustes.invalidar()` en el PUT (en `finally`), con guarda de generación contra una lectura en vuelo. Vale porque hay un solo proceso: `ajustes.py` llama a `exigir_un_solo_proceso`.
- **LAS CUATRO:** (1) `EXPLAIN` de `ajustes.CONSULTA` real en un test (PRIMARY, sin filesort ni temporary). (2) Caché con invalidación explícita. (3) Nada bloqueante en `async def` (el reaper sigue en `asyncio.to_thread`). (4) Prueba de carga con k6 de login, refresh y creación de pipeline, antes y después, **antes del merge** (Task 15). Sin número medido no hay GO.
- **CI:** los pisos de `.github/workflows/policy.yml` se ponen **exactos y medidos** (Task 12), sobre el valor vigente después del rebase: vitest `numPassedTests`, `PISO_PASSED` (con DB) y `JAX_CI_MIN_PASSED` (sin DB). Se verifica rompiéndolo (Task 13).
- **Mirror-sync:** familia nueva `tope_pipelines` (`jax/jacobs/policy.py` ↔ `jax-platform/backend/ajustes.py`). ORDEN: la plataforma mergea primero, porque el job `mirror-sync` de jax clona `jax-platform` master.
- **Deploy:** backend `sudo -n /usr/bin/systemctl restart jax-platform.service` con 0 pipelines `pending/running`. Frontend: build → backup en la VM → rsync a `/tmp/axioma-deploy/` → `sudo rsync -a --delete --exclude .user.ini --chown=www:www` a `/www/wwwroot/axioma-ia.io/`. Dump previo de `axioma_config` con restauración probada fila por fila.
- **Biblioteca:** `jax/DEUDA.md` y `jax/CONTEXT.md` antes de cerrar (Task 18), con procedencia y tipo (HECHO / DECISIÓN / VERDAD OPERACIONAL / HISTORIA).

---

## Mapa de archivos

| Archivo | Responsabilidad | Task |
|---|---|---|
| `backend/ajustes.py` (nuevo) | Definiciones, validación, caché, `valor()`, `invalidar()`, `limites()`, `AjusteIlegible` + su respuesta 503, espejo `MAX_PARALLEL_PIPELINES` | 1 |
| `backend/main.py` | Registrar el handler de `AjusteIlegible` | 1 |
| `backend/tests/conftest.py` | Fixture `ajustes_en_db` | 1 |
| `backend/tests/test_ajustes.py` (nuevo) | Tests del módulo | 1 |
| `backend/db/migrations.py` | Tabla `axioma_migracion_de_datos` + `_ajustes_que_mandan_v1` | 2 |
| `backend/api/admin/config_admin.py` | `DEFAULT_CONFIG` sin las cinco; PUT valida por collation e invalida; GET devuelve `limites` | 2, 3 |
| `backend/tests/test_migracion_ajustes.py`, `test_config_admin_ajustes.py` (nuevos) | Tests | 2, 3 |
| `backend/auth/jwt.py`, `backend/api/auth.py` | `iat` + vida pedida; lectura del ajuste antes de escribir; `/refresh` mide la vida | 4 |
| `backend/tests/identidades.py`, `test_sesiones_token_version.py` | Firma nueva de `create_refresh_token` | 4 |
| `backend/tests/test_ajuste_sesion.py` (nuevo) | Tests | 4 |
| `backend/jax_engine/resource_manager.py`, `backend/api/pipelines.py` | Cupo por ajuste, código `pipelines_limite_alcanzado` | 5 |
| `backend/tests/test_ajuste_max_pipelines.py` (nuevo), `test_pipeline_resource_release.py` (comentario) | Tests | 5 |
| `frontend/src/components/BottomBar/errorDePipeline.js` (+test), `BottomBar.jsx` | Traducir el 429 | 5 |
| `backend/jax_engine/owner_cleanup.py`, `backend/tests/test_owner_cleanup.py` | Retención por ajuste | 6 |
| `backend/api/apariencia.py`, `backend/tests/test_apariencia.py` | `lang_default` y `system_name` públicos | 7 |
| `frontend/src/store/useApariencia.js` (+test), `frontend/src/apariencia/sincronizarApariencia.js` (+test), `store/useTema.js` (+test), `App.jsx` | Una sola lectura de `/apariencia`; título del documento | 8 |
| `frontend/src/i18n/idioma.js` (+test), `i18n/index.jsx` (+`index.test.jsx`), `store/useJaxStore.js` | Idioma inicial | 9 |
| `LogoAxioma.jsx`, `Login.jsx`, `admin/AdminSidebar.jsx`, `pages/admin/AdminSmtp.jsx`, i18n, tests | Nombre del sistema | 10 |
| `frontend/src/pages/admin/AdminSettings.jsx` (+test), i18n | Rangos del servidor, sin valores inventados, errores y ayudas | 11 |
| `.github/workflows/policy.yml` | Pisos | 12 |
| `jax`: `scripts/check_mirror_sync.py`, `jacobs/policy.py` (comentario), `loadtest/ajustes-sesion.js`, `loadtest/ajustes-pipelines.js`, `DEUDA.md`, `CONTEXT.md` | Espejo, carga y Biblioteca | 14, 18 |

---

### Task 1: Módulo `ajustes.py` — lectura tipada, validación, caché e invalidación

**Files:**
- Create: `backend/ajustes.py`
- Create: `backend/tests/test_ajustes.py`
- Modify: `backend/main.py` (junto a la creación de `app`, línea ~115)
- Modify: `backend/tests/conftest.py` (fixture nueva al final)

**Interfaces:**
- Consumes: `auth.rate_limit.exigir_un_solo_proceso(env, argv)`, `auth.jwt.ACCESS_EXPIRE_SECONDS`, `db.connection.get_pool()`, `validacion.tiene_caracteres_de_control(str) -> bool`.
- Produces (lo usan las Tasks 2-7, 14 y 15):
  - constantes `SESION = "session_timeout_min"`, `MAX_PIPELINES = "max_pipelines"`, `RETENCION = "web_task_retention_days"`, `IDIOMA = "lang_default"`, `NOMBRE = "system_name"`, `CLAVES: tuple[str, ...]` (en ese orden), `IDIOMAS = ("es", "en")`, `NOMBRE_MAX = 60`, `MAX_PARALLEL_PIPELINES  = 3`, `CONSULTA: str`, `TTL_S: float`;
  - `class ValorInvalido(ValueError)`; `class AjusteIlegible(Exception)` con `.clave`, `.motivo` (`"ausente"` | `"invalido"`) y `codigo = "ajuste_ilegible"`;
  - `DEFINICIONES: dict[str, Definicion]` (`Definicion.interpretar(str) -> int | str`, `Definicion.limites: dict`);
  - `interpretar(clave: str, texto: str) -> int | str` (lanza `ValorInvalido`), `limites() -> dict`, `ttl_desde_entorno(texto: str) -> float`;
  - `class CacheDeAjustes(cargar, ttl_s, reloj=time.monotonic)` con `async filas() -> dict[str, str]` e `invalidar() -> None`;
  - `async valor(clave: str) -> int | str` (lanza `AjusteIlegible`), `invalidar() -> None`;
  - `async respuesta_de_ajuste_ilegible(request, exc) -> JSONResponse` (503).
  - Fixture de pytest `ajustes_en_db` → `SimpleNamespace(poner(**valores), quitar(clave), filas() -> dict[str, str], validos: dict[str, str])`.

- [ ] **Step 0: Preparar el worktree**

```bash
git -C /home/fruiz/jax-platform fetch origin
git -C /home/fruiz/jax-platform log --oneline -1 origin/master
git -C /home/fruiz/jax-platform worktree add -b feat/ajustes-que-mandan /home/fruiz/worktrees/jax-platform-frente-c origin/master
ln -s /home/fruiz/jax-platform/frontend/node_modules /home/fruiz/worktrees/jax-platform-frente-c/frontend/node_modules
git -C /home/fruiz/worktrees/jax-platform-frente-c status --short
```
Expected: `origin/master` en `26c9cd5`, o en un merge posterior de los frentes A, B o D. Si es posterior, se anota el sha en el ledger y se vuelven a verificar las líneas citadas en "Discrepancias" (sobre todo `config_admin.py`, `auth/jwt.py`, `resource_manager.py` y `useJaxStore.js`) antes de seguir. `status` muestra solo el symlink sin rastrear.

- [ ] **Step 1: Fixture `ajustes_en_db` en `backend/tests/conftest.py`** (al final del archivo)

```python
@pytest.fixture
def ajustes_en_db(client):
    """Las cinco filas de los ajustes que mandan (frente C, 2026-09-16), con
    restauración: guarda lo que había, deja escribir/quitar filas y al final
    las repone tal cual. Invalida el caché de `ajustes` en cada cambio, así
    ningún test ve un valor de otro. Pide `client`: sin DB se salta sola."""
    from types import SimpleNamespace

    import ajustes
    from tests.identidades import sql

    marcadores = ", ".join(["%s"] * len(ajustes.CLAVES))
    seleccion = f"SELECT config_key, config_value FROM axioma_config WHERE config_key IN ({marcadores})"
    antes = dict(client.portal.call(sql, seleccion, ajustes.CLAVES, True))

    def poner(**valores):
        for clave, valor in valores.items():
            client.portal.call(
                sql,
                "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)",
                (clave, valor),
            )
        ajustes.invalidar()

    def quitar(clave):
        client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key = %s", (clave,))
        ajustes.invalidar()

    def filas():
        return dict(client.portal.call(sql, seleccion, ajustes.CLAVES, True))

    ajustes.invalidar()
    yield SimpleNamespace(
        poner=poner, quitar=quitar, filas=filas,
        validos={"session_timeout_min": "10080", "max_pipelines": "3",
                 "web_task_retention_days": "30", "lang_default": "es", "system_name": "Axioma"},
    )
    client.portal.call(sql, f"DELETE FROM axioma_config WHERE config_key IN ({marcadores})", ajustes.CLAVES)
    for clave, valor in antes.items():
        client.portal.call(sql, "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s)", (clave, valor))
    ajustes.invalidar()
```

- [ ] **Step 2: Escribir los tests que fallan** — `backend/tests/test_ajustes.py`

```python
"""Ajustes de administración que mandan (spec 2026-09-16-hallazgos-auditoria §C).

Puros: la validación por clave (rangos del SERVIDOR, no del <input>), el TTL
del entorno, el caché (TTL, invalidación explícita, y que una invalidación
durante una lectura en vuelo no deje guardado lo viejo), los límites públicos,
el proceso único y la respuesta 503. Con DB (jax_memory_test): la lectura
real, que una fila ausente o inválida sea un ERROR y no un default, y el
EXPLAIN de la consulta real.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import ajustes

BACKEND = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ puros

def test_entero_acepta_los_bordes_y_rechaza_fuera_de_rango():
    sesion = ajustes.DEFINICIONES[ajustes.SESION].interpretar
    assert sesion("15") == 15 and sesion("10080") == 10080
    for texto in ("14", "10081", "0"):
        with pytest.raises(ajustes.ValorInvalido):
            sesion(texto)
    tope = ajustes.DEFINICIONES[ajustes.MAX_PIPELINES].interpretar
    assert [tope("1"), tope("3")] == [1, 3]
    with pytest.raises(ajustes.ValorInvalido):
        tope("4")


def test_entero_rechaza_lo_que_no_es_un_entero_canonico():
    retencion = ajustes.DEFINICIONES[ajustes.RETENCION].interpretar
    for texto in ("", " 30", "30 ", "+30", "30.0", "030", "٣٠", "treinta", "-1"):
        with pytest.raises(ajustes.ValorInvalido):
            retencion(texto)


def test_idioma_solo_admite_es_y_en():
    idioma = ajustes.DEFINICIONES[ajustes.IDIOMA].interpretar
    assert idioma("es") == "es" and idioma("en") == "en"
    for texto in ("", "ES", "fr", "es ", "español"):
        with pytest.raises(ajustes.ValorInvalido):
            idioma(texto)


def test_nombre_del_sistema_sin_espacios_alrededor_ni_control_ni_largo_de_mas():
    nombre = ajustes.DEFINICIONES[ajustes.NOMBRE].interpretar
    assert nombre("Axioma") == "Axioma"
    assert nombre("x" * ajustes.NOMBRE_MAX) == "x" * ajustes.NOMBRE_MAX
    for texto in ("", " ", " Axioma", "Axioma ", "x" * (ajustes.NOMBRE_MAX + 1), "Axi\noma", "Axi\x00oma"):
        with pytest.raises(ajustes.ValorInvalido):
            nombre(texto)


def test_ttl_del_entorno_falla_fuerte_si_no_es_positivo_y_finito():
    assert ajustes.ttl_desde_entorno("30") == 30.0
    for texto in ("", "abc", "0", "-5", "nan", "inf"):
        with pytest.raises(ValueError, match="JAX_AJUSTES_TTL_S"):
            ajustes.ttl_desde_entorno(texto)


class _Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _cargador(respuestas):
    llamadas = []

    async def cargar():
        llamadas.append(1)
        return dict(respuestas[min(len(llamadas), len(respuestas)) - 1])

    return cargar, llamadas


async def test_la_cache_no_recarga_dentro_del_ttl():
    cargar, llamadas = _cargador([{"max_pipelines": "3"}])
    reloj = _Reloj()
    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=reloj)
    assert await cache.filas() == {"max_pipelines": "3"}
    reloj.t += 29.9
    assert await cache.filas() == {"max_pipelines": "3"}
    assert len(llamadas) == 1


async def test_la_cache_recarga_al_vencer_el_ttl():
    cargar, llamadas = _cargador([{"max_pipelines": "3"}, {"max_pipelines": "2"}])
    reloj = _Reloj()
    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=reloj)
    await cache.filas()
    reloj.t += 30
    assert await cache.filas() == {"max_pipelines": "2"}
    assert len(llamadas) == 2


async def test_invalidar_obliga_a_recargar_aunque_no_haya_vencido():
    cargar, llamadas = _cargador([{"max_pipelines": "3"}, {"max_pipelines": "2"}])
    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=_Reloj())
    await cache.filas()
    cache.invalidar()
    assert await cache.filas() == {"max_pipelines": "2"}
    assert len(llamadas) == 2


async def test_una_invalidacion_durante_la_carga_no_deja_guardado_lo_viejo():
    entro, soltar = asyncio.Event(), asyncio.Event()
    respuestas = [{"max_pipelines": "3"}, {"max_pipelines": "1"}]
    llamadas = []

    async def cargar():
        llamadas.append(1)
        if len(llamadas) == 1:
            entro.set()
            await soltar.wait()
        return dict(respuestas[len(llamadas) - 1])

    cache = ajustes.CacheDeAjustes(cargar, ttl_s=30, reloj=_Reloj())
    en_vuelo = asyncio.create_task(cache.filas())
    await entro.wait()
    cache.invalidar()  # el PUT confirmó un valor nuevo mientras la lectura vieja seguía en vuelo
    soltar.set()
    assert await en_vuelo == {"max_pipelines": "3"}  # quien pidió antes recibe lo que leyó
    assert await cache.filas() == {"max_pipelines": "1"}  # pero no quedó guardado
    assert len(llamadas) == 2


def test_limites_publicos_y_tope_espejado_de_jacobs():
    assert ajustes.MAX_PARALLEL_PIPELINES == 3
    assert ajustes.limites() == {
        "session_timeout_min": {"min": 15, "max": 10080},
        "max_pipelines": {"min": 1, "max": 3},
        "web_task_retention_days": {"min": 1, "max": 365},
        "lang_default": {"opciones": ["es", "en"]},
        "system_name": {"max_largo": 60},
    }


def test_el_modulo_se_niega_a_correr_con_varios_workers():
    r = subprocess.run(
        [sys.executable, "-c", "import ajustes"],
        cwd=BACKEND, env={**os.environ, "WEB_CONCURRENCY": "2"},
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
    assert "workers" in r.stderr


async def test_ajuste_ilegible_responde_503_con_codigo_y_clave():
    r = await ajustes.respuesta_de_ajuste_ilegible(None, ajustes.AjusteIlegible("max_pipelines", "invalido"))
    assert r.status_code == 503
    assert json.loads(r.body) == {"detail": {"code": "ajuste_ilegible", "clave": "max_pipelines"}}


# ------------------------------------------------------------------ con DB

async def _leer_todos():
    return {clave: await ajustes.valor(clave) for clave in ajustes.CLAVES}


async def _error_de(clave):
    try:
        await ajustes.valor(clave)
    except ajustes.AjusteIlegible as exc:
        return (exc.clave, exc.motivo)
    return None


async def _explain():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN " + ajustes.CONSULTA, ajustes.CLAVES)
            return await cur.fetchall(), [d[0] for d in cur.description]


def test_lee_los_valores_tipados_de_la_tabla(client, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "max_pipelines": "2", "lang_default": "en"})
    assert client.portal.call(_leer_todos) == {
        "session_timeout_min": 10080, "max_pipelines": 2, "web_task_retention_days": 30,
        "lang_default": "en", "system_name": "Axioma",
    }


def test_fila_ausente_es_un_error_y_no_tumba_a_los_demas(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    ajustes_en_db.quitar("max_pipelines")
    assert client.portal.call(_error_de, "max_pipelines") == ("max_pipelines", "ausente")
    assert client.portal.call(_error_de, "system_name") is None


def test_valor_invalido_es_un_error_y_no_un_default(client, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60 min"})
    assert client.portal.call(_error_de, "session_timeout_min") == ("session_timeout_min", "invalido")


def test_explain_de_la_consulta_real_va_por_primary(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    filas, columnas = client.portal.call(_explain)
    plan = dict(zip(columnas, filas[0]))
    assert plan["key"] == "PRIMARY", plan
    extra = plan.get("Extra") or ""
    assert "filesort" not in extra and "temporary" not in extra, plan
```

- [ ] **Step 3: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_ajustes.py -v`
Expected: error de colección `ModuleNotFoundError: No module named 'ajustes'`. Es el rojo de los 16 tests.

- [ ] **Step 4: Implementar** — `backend/ajustes.py`

```python
"""Ajustes de administración que MANDAN (spec 2026-09-16-hallazgos-auditoria §C,
decisión de Fernando del mismo día: los cinco, con el valor que el código
hacía cumplir como valor inicial).

Hasta el 2026-09-16 la pantalla Configuración guardaba estas cinco claves en
axioma_config y NADIE las leía: la sesión duraba 7 días fijos (auth/jwt.py),
el cupo era 3 fijo (resource_manager.py), la retención 30 días fija
(owner_cleanup.py), el idioma 'es' y el nombre "Axioma" desde i18n.

Reglas:
  - Una sola consulta por PRIMARY para las cinco (CONSULTA; EXPLAIN en
    tests/test_ajustes.py).
  - Validación por clave con rangos del SERVIDOR. Un valor ausente o inválido
    es AjusteIlegible -> 503 `ajuste_ilegible` con la clave: nunca un default
    silencioso. Cada clave se valida por separado: un system_name roto no
    tumba el login.
  - Caché con TTL (JAX_AJUSTES_TTL_S) e invalidación EXPLÍCITA en el PUT de
    /api/admin/config. La invalidación vive en memoria de UN proceso, por eso
    este módulo exige un solo worker. El TTL acota lo que tarda en verse una
    edición hecha por fuera del PUT (SQL a mano, migración).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import time
import weakref
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi.responses import JSONResponse

from auth.jwt import ACCESS_EXPIRE_SECONDS
from auth.rate_limit import exigir_un_solo_proceso
from db.connection import get_pool
from validacion import tiene_caracteres_de_control

logger = logging.getLogger(__name__)

# La invalidación del PUT no cruzaría a otro worker.
exigir_un_solo_proceso(os.environ, sys.argv)

# Espejo TEXTUAL de jax/jacobs/policy.py (familia `tope_pipelines` de
# jax/scripts/check_mirror_sync.py, que compara el segmento del AST: el doble
# espacio es parte de la copia). Jacobs cuenta TODOS los pipelines
# pending/running, de todos los tenants e invocadores, y rechaza el que
# excede con 422: un max_pipelines por tenant mayor que esto sería una
# promesa que Jacobs no cumple.
MAX_PARALLEL_PIPELINES  = 3

SESION = "session_timeout_min"
MAX_PIPELINES = "max_pipelines"
RETENCION = "web_task_retention_days"
IDIOMA = "lang_default"
NOMBRE = "system_name"
CLAVES = (SESION, MAX_PIPELINES, RETENCION, IDIOMA, NOMBRE)

IDIOMAS = ("es", "en")
NOMBRE_MAX = 60
# Un refresh que viva menos que el access no se llegaría a usar.
SESION_MIN = ACCESS_EXPIRE_SECONDS // 60
SESION_MAX = 10080  # 7 días: la vida que el código hacía cumplir el 2026-09-16
RETENCION_MAX = 365

CONSULTA = (
    "SELECT config_key, config_value FROM axioma_config WHERE config_key IN ("
    + ", ".join(["%s"] * len(CLAVES)) + ")"
)


class ValorInvalido(ValueError):
    pass


class AjusteIlegible(Exception):
    codigo = "ajuste_ilegible"

    def __init__(self, clave: str, motivo: str):
        super().__init__(f"{clave}: {motivo}")
        self.clave = clave
        self.motivo = motivo


def _entero(minimo: int, maximo: int) -> Callable[[str], int]:
    def interpretar(texto: str) -> int:
        canonico = texto.isascii() and texto.isdigit() and (texto == "0" or not texto.startswith("0"))
        if not canonico or not minimo <= int(texto) <= maximo:
            raise ValorInvalido(texto)
        return int(texto)
    return interpretar


def _idioma(texto: str) -> str:
    if texto not in IDIOMAS:
        raise ValorInvalido(texto)
    return texto


def _nombre(texto: str) -> str:
    if (not texto or texto != texto.strip() or len(texto) > NOMBRE_MAX
            or tiene_caracteres_de_control(texto)):
        raise ValorInvalido(texto)
    return texto


@dataclass(frozen=True)
class Definicion:
    interpretar: Callable[[str], int | str]
    limites: dict


DEFINICIONES: dict[str, Definicion] = {
    SESION: Definicion(_entero(SESION_MIN, SESION_MAX), {"min": SESION_MIN, "max": SESION_MAX}),
    MAX_PIPELINES: Definicion(_entero(1, MAX_PARALLEL_PIPELINES), {"min": 1, "max": MAX_PARALLEL_PIPELINES}),
    RETENCION: Definicion(_entero(1, RETENCION_MAX), {"min": 1, "max": RETENCION_MAX}),
    IDIOMA: Definicion(_idioma, {"opciones": list(IDIOMAS)}),
    NOMBRE: Definicion(_nombre, {"max_largo": NOMBRE_MAX}),
}


def interpretar(clave: str, texto: str) -> int | str:
    return DEFINICIONES[clave].interpretar(texto)


def limites() -> dict:
    return {clave: dict(d.limites) for clave, d in DEFINICIONES.items()}


def ttl_desde_entorno(texto: str) -> float:
    try:
        ttl = float(texto)
    except ValueError as exc:
        raise ValueError(f"JAX_AJUSTES_TTL_S inválido {texto!r}: segundos > 0") from exc
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"JAX_AJUSTES_TTL_S inválido {texto!r}: segundos > 0")
    return ttl


TTL_S = ttl_desde_entorno(os.getenv("JAX_AJUSTES_TTL_S", "30"))


class CacheDeAjustes:
    """Filas crudas con TTL. `invalidar()` sube la generación: una lectura que
    empezó antes de invalidar entrega lo que leyó a quien la pidió, pero no lo
    guarda. Un lock por event loop (mismo motivo que db/connection.py: en la
    suite conviven dos loops) evita que N requests recarguen a la vez."""

    def __init__(self, cargar: Callable[[], Awaitable[dict[str, str]]], ttl_s: float,
                 reloj: Callable[[], float] = time.monotonic):
        self._cargar = cargar
        self._ttl = ttl_s
        self._reloj = reloj
        self._filas: dict[str, str] | None = None
        self._vence = 0.0
        self._generacion = 0
        self._locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()

    def _vigentes(self) -> dict[str, str] | None:
        if self._filas is not None and self._reloj() < self._vence:
            return self._filas
        return None

    def invalidar(self) -> None:
        self._filas = None
        self._generacion += 1

    async def filas(self) -> dict[str, str]:
        vigentes = self._vigentes()
        if vigentes is not None:
            return vigentes
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            vigentes = self._vigentes()
            if vigentes is not None:
                return vigentes
            generacion = self._generacion
            filas = await self._cargar()
            if generacion == self._generacion:
                self._filas = filas
                self._vence = self._reloj() + self._ttl
            return filas


async def _leer_filas() -> dict[str, str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CONSULTA, CLAVES)
            return {clave: valor for clave, valor in await cur.fetchall()}


_cache = CacheDeAjustes(_leer_filas, TTL_S)


def invalidar() -> None:
    _cache.invalidar()


async def valor(clave: str) -> int | str:
    filas = await _cache.filas()
    if clave not in filas:
        raise AjusteIlegible(clave, "ausente")
    try:
        return interpretar(clave, filas[clave])
    except ValorInvalido:
        raise AjusteIlegible(clave, "invalido") from None


async def respuesta_de_ajuste_ilegible(request, exc: AjusteIlegible) -> JSONResponse:
    # El valor no se loguea: la clave y el motivo alcanzan para arreglarlo.
    logger.error("ajuste %s ilegible en axioma_config (%s)", exc.clave, exc.motivo)
    return JSONResponse(status_code=503, content={"detail": {"code": AjusteIlegible.codigo, "clave": exc.clave}})
```

`backend/main.py`: sumar `import ajustes` a los imports y, inmediatamente después de `app = FastAPI(title="JAX Platform", version="0.1.0", lifespan=lifespan)`:

```python
# Frente C (2026-09-16): un ajuste de admin ilegible es un 503 con código, en
# cualquier endpoint que lo lea -- nunca un default silencioso (ajustes.py).
app.add_exception_handler(ajustes.AjusteIlegible, ajustes.respuesta_de_ajuste_ilegible)
```

- [ ] **Step 5: Verlos pasar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_ajustes.py -v`
Expected: `16 passed`. Si `test_explain_de_la_consulta_real_va_por_primary` da `type=ALL`: **no** se fuerza el índice sin medir. Se anota el `EXPLAIN` completo y las filas de la tabla en el ledger, se corre `ANALYZE TABLE axioma_config` en `jax_memory_test` y se vuelve a medir. Si sigue en `ALL`, se para y se reporta: DEUDA línea 968 midió `range` sobre PRIMARY para el IN de SMTP.

Mutación de control de la guarda de generación: borrar la línea `if generacion == self._generacion:` (dejando el cuerpo sin indentar) → `test_una_invalidacion_durante_la_carga_no_deja_guardado_lo_viejo` en rojo con `{'max_pipelines': '3'} != {'max_pipelines': '1'}`. Restaurar.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/ajustes.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_ajustes.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/conftest.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/main.py
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): lectura tipada de los ajustes de admin, con caché invalidable" -m "Frente C: ajustes.py valida por clave con rangos del servidor, lee las cinco claves por PRIMARY, cachea con TTL e invalidación explícita y responde 503 ajuste_ilegible en vez de un default. Espeja MAX_PARALLEL_PIPELINES de Jacobs." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migración de datos — los valores que rigen, una sola vez

**Files:**
- Modify: `backend/db/migrations.py` (DDL nuevo junto a `CREATE_AXIOMA_CONFIG`; `_TABLES` línea ~581; función nueva junto a `_migrar_gemini_a_cabecera`; llamada en `run_migrations` después de `await _drop_axioma_artifacts(cur)`)
- Modify: `backend/api/admin/config_admin.py:12-20` (`DEFAULT_CONFIG`)
- Create: `backend/tests/test_migracion_ajustes.py`

**Interfaces:**
- Consumes: `ajustes.SESION`, `MAX_PIPELINES`, `RETENCION`, `IDIOMA`, `NOMBRE`, `CLAVES`; fixture `ajustes_en_db`; `tests.identidades.sql`.
- Produces: tabla `axioma_migracion_de_datos(nombre VARCHAR(100) PK, aplicada_at TIMESTAMP)`; `MIGRACION_AJUSTES_V1 = "ajustes_que_mandan_v1"`; `VALORES_QUE_RIGEN_2026_09_16: dict[str, str]`; `NOMBRE_QUE_SE_MOSTRABA_2026_09_16 = "Axioma"`; `async _ajustes_que_mandan_v1(cur) -> None`. Después del deploy, las cinco filas existen en producción (Task 16).

- [ ] **Step 1: Escribir los tests que fallan** — `backend/tests/test_migracion_ajustes.py`

```python
"""Migración de los ajustes que mandan (spec 2026-09-16 §C): fija lo que el
código hacía cumplir en 26c9cd5 (no lo que decía DEFAULT_CONFIG), conserva el
system_name guardado, y corre UNA sola vez: un cambio posterior del admin no
se pisa en el próximo arranque. Contra jax_memory_test."""
import pytest

import ajustes
from db.migrations import MIGRACION_AJUSTES_V1, _ajustes_que_mandan_v1
from tests.identidades import sql


async def _migrar():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _ajustes_que_mandan_v1(cur)
        await conn.commit()


@pytest.fixture
def sin_marca(client, ajustes_en_db):
    marca = client.portal.call(sql, "SELECT nombre FROM axioma_migracion_de_datos WHERE nombre = %s",
                               (MIGRACION_AJUSTES_V1,), True)
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTES_V1,))
    yield ajustes_en_db
    client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTES_V1,))
    if marca:
        client.portal.call(sql, "INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_AJUSTES_V1,))


def test_primera_corrida_fija_lo_que_el_codigo_hacia_cumplir(client, sin_marca):
    sin_marca.poner(session_timeout_min="60", max_pipelines="1", web_task_retention_days="7",
                    lang_default="en", system_name="Mi Sistema")
    client.portal.call(_migrar)
    assert sin_marca.filas() == {
        "session_timeout_min": "10080", "max_pipelines": "3", "web_task_retention_days": "30",
        "lang_default": "es", "system_name": "Mi Sistema",
    }
    assert client.portal.call(sql, "SELECT COUNT(*) FROM axioma_migracion_de_datos WHERE nombre = %s",
                              (MIGRACION_AJUSTES_V1,), True) == ((1,),)


def test_sin_fila_de_nombre_la_crea_con_el_que_se_mostraba(client, sin_marca):
    sin_marca.poner(session_timeout_min="60")
    sin_marca.quitar("system_name")
    client.portal.call(_migrar)
    assert sin_marca.filas()["system_name"] == "Axioma"


def test_segunda_corrida_no_pisa_lo_que_el_admin_cambio(client, sin_marca):
    client.portal.call(_migrar)
    sin_marca.poner(max_pipelines="1", session_timeout_min="120")
    client.portal.call(_migrar)
    filas = sin_marca.filas()
    assert (filas["max_pipelines"], filas["session_timeout_min"]) == ("1", "120")


def test_default_config_ya_no_recrea_los_cinco_ajustes():
    from api.admin.config_admin import DEFAULT_CONFIG
    assert not set(DEFAULT_CONFIG) & set(ajustes.CLAVES)
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_migracion_ajustes.py -v`
Expected: error de colección `ImportError: cannot import name 'MIGRACION_AJUSTES_V1' from 'db.migrations'`.

- [ ] **Step 3: Implementar**

En `backend/db/migrations.py`, sumar `import ajustes` a los imports del módulo. Después de `CREATE_AXIOMA_CONFIG`:

```python
# Migraciones de DATOS que corren una sola vez (frente C, 2026-09-16). Las de
# esquema son idempotentes por inspección; una de datos que fija valores no lo
# es: correrla en cada arranque pisaría lo que el admin cambió después.
CREATE_AXIOMA_MIGRACION_DE_DATOS = """
CREATE TABLE IF NOT EXISTS axioma_migracion_de_datos (
  nombre VARCHAR(100) PRIMARY KEY,
  aplicada_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
```

En `_TABLES`, inmediatamente después de `("axioma_config", CREATE_AXIOMA_CONFIG),`:

```python
    ("axioma_migracion_de_datos", CREATE_AXIOMA_MIGRACION_DE_DATOS),
```

Función nueva, después de `_migrar_gemini_a_cabecera`:

```python
MIGRACION_AJUSTES_V1 = "ajustes_que_mandan_v1"
# Lo que el CÓDIGO hacía cumplir en master 26c9cd5 (2026-09-16), no lo que
# mostraba DEFAULT_CONFIG (60 / 1 / 7, que nadie leía):
VALORES_QUE_RIGEN_2026_09_16 = {
    ajustes.SESION: "10080",   # auth/jwt.py: REFRESH_EXPIRE_SECONDS = 7 * 24 * 3600
    ajustes.MAX_PIPELINES: "3",  # jax_engine/resource_manager.py: MAX_PIPELINES_PER_TENANT
    ajustes.RETENCION: "30",   # jax_engine/owner_cleanup.py: COMMAND_OWNER_MAX_AGE_SECONDS
    ajustes.IDIOMA: "es",      # frontend/src/i18n/index.jsx: jax_lang || 'es'
}
# El nombre que la UI mostraba (i18n brandName). La fila, si existe, se conserva:
# el deploy verifica antes que diga esto (plan frente C, Task 16).
NOMBRE_QUE_SE_MOSTRABA_2026_09_16 = "Axioma"


async def _ajustes_que_mandan_v1(cur) -> None:
    """Frente C (2026-09-16): los ajustes de admin pasan a mandar con el valor
    que ya regía. UNA vez (marcador en axioma_migracion_de_datos): después,
    lo que el admin guarde no se pisa al arrancar. Sin marcador y a medias
    (caída entre sentencias), la próxima corrida la completa: cada sentencia
    fija el mismo valor."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTES_V1,))
    if await cur.fetchone() is not None:
        return
    for clave, valor in VALORES_QUE_RIGEN_2026_09_16.items():
        await cur.execute(
            "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)",
            (clave, valor),
        )
    await cur.execute(
        "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
        (ajustes.NOMBRE, NOMBRE_QUE_SE_MOSTRABA_2026_09_16),
    )
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_AJUSTES_V1,))
```

En `run_migrations`, inmediatamente después de `await _drop_axioma_artifacts(cur)`:

```python
            await _ajustes_que_mandan_v1(cur)
```

En `backend/api/admin/config_admin.py`, reemplazar `DEFAULT_CONFIG`:

```python
# Frente C (2026-09-16): los cinco ajustes que mandan (session_timeout_min,
# max_pipelines, web_task_retention_days, lang_default, system_name) NO van
# acá. Sus filas las crea una vez db/migrations.py::_ajustes_que_mandan_v1, y
# si faltan, la respuesta es 503 ajuste_ilegible (ajustes.py): un GET de esta
# pantalla no puede recrearlas en silencio con un default.
DEFAULT_CONFIG = {
    "theme_default": "dark",
    "ws_notifications": "true",
}
```

- [ ] **Step 4: Verlos pasar, y que nada más se rompió**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_migracion_ajustes.py tests/test_ajustes.py tests/test_apariencia.py tests/test_smtp_endpoints.py -v`
Expected: todo `passed`. `test_apariencia.py` sigue igual: `apariencia.py` todavía importa `DEFAULT_CONFIG["theme_default"]`, que sigue estando.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/db/migrations.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/api/admin/config_admin.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_migracion_ajustes.py
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): migración única con los valores que el código hacía cumplir" -m "10080 min, 3 pipelines, 30 días e idioma es; system_name se conserva. Marcador en axioma_migracion_de_datos para no pisar cambios posteriores del admin. DEFAULT_CONFIG deja de sembrar 60/1/7." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `/api/admin/config` — el PUT valida con la regla de la base e invalida; el GET publica los límites

**Files:**
- Modify: `backend/api/admin/config_admin.py` (`get_config`, `_reservadas_para_la_base`, `update_config`)
- Create: `backend/tests/test_config_admin_ajustes.py`

**Interfaces:**
- Consumes: `ajustes.CLAVES`, `ajustes.interpretar`, `ajustes.ValorInvalido`, `ajustes.invalidar`, `ajustes.limites`, `ajustes.valor`; fixture `ajustes_en_db`; `tests.identidades.cabeceras(client, etiqueta, role)`.
- Produces: `GET /api/admin/config` → `{"config": [...], "limites": ajustes.limites()}`. `PUT` → 400 `{"detail": {"code": "config_valor_invalido", "clave": <clave canónica>}}` sin escribir nada del lote; si escribe, invalida el caché. Helpers `async _collation(cur) -> str` y `async _ajustes_para_la_base(cur, claves) -> dict[str, str]`.

- [ ] **Step 1: Escribir los tests que fallan** — `backend/tests/test_config_admin_ajustes.py`

```python
"""PUT/GET /api/admin/config con los ajustes que mandan (spec 2026-09-16 §C):
rangos del SERVIDOR, la misma regla de igualdad que la PRIMARY KEY (collation
uca1400_ai_ci: "MAX_PIPELINES" ES la fila max_pipelines), invalidación del
caché en el mismo request y límites publicados para la pantalla."""
import ajustes
from tests.identidades import cabeceras

ADMIN = "ajustes-config"


def _put(client, items):
    return client.put("/api/admin/config", json=items, headers=cabeceras(client, ADMIN, role="superadmin"))


def test_un_lote_con_un_valor_fuera_de_rango_no_escribe_nada(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = _put(client, [{"key": "system_name", "value": "Otro"}, {"key": "max_pipelines", "value": "4"}])
    assert (r.status_code, r.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "max_pipelines"}})
    assert ajustes_en_db.filas()["system_name"] == "Axioma"


def test_otra_grafia_de_la_misma_clave_tambien_se_valida(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = _put(client, [{"key": "MAX_PIPELINES", "value": "99"}])
    assert (r.status_code, r.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "max_pipelines"}})
    assert ajustes_en_db.filas()["max_pipelines"] == "3"


def test_un_put_bueno_se_ve_en_el_request_siguiente_sin_esperar_el_ttl(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    assert client.portal.call(ajustes.valor, "max_pipelines") == 3
    assert _put(client, [{"key": "max_pipelines", "value": "2"}]).status_code == 200
    assert client.portal.call(ajustes.valor, "max_pipelines") == 2


def test_get_publica_los_limites_del_servidor(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = client.get("/api/admin/config", headers=cabeceras(client, ADMIN, role="superadmin"))
    assert r.status_code == 200
    assert r.json()["limites"] == ajustes.limites()


def test_la_sesion_vigente_de_siete_dias_se_puede_guardar(client, ajustes_en_db):
    # Guarda (ya pasa con el código viejo, que no validaba): la pantalla vieja
    # topaba en 1440 y el backend nuevo NO puede rechazar el valor vigente.
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    assert _put(client, [{"key": "session_timeout_min", "value": "10080"}]).status_code == 200
    assert ajustes_en_db.filas()["session_timeout_min"] == "10080"
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_config_admin_ajustes.py -v`
Expected: 4 failed y 1 passed (la guarda). Razones: `200 != 400` en los dos primeros (el PUT viejo escribe el 4 y el 99), `3 != 2` en el de invalidación (nadie invalida y el TTL no venció) y `KeyError: 'limites'` en el GET.

- [ ] **Step 3: Implementar** — `backend/api/admin/config_admin.py`

Agregar `import ajustes` a los imports. Extraer la lectura de la collation y reutilizarla (reemplaza las primeras líneas de `_reservadas_para_la_base`):

```python
async def _collation(cur) -> str:
    await cur.execute(
        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'axioma_config' AND COLUMN_NAME = 'config_key'")
    fila = await cur.fetchone()
    if fila is None or not fila[0] or not _IDENTIFICADOR.fullmatch(fila[0]):
        # Fail-closed: sin collation verificable no se escribe nada.
        raise HTTPException(status_code=503, detail="config_collation_desconocida")
    return fila[0]  # identificador validado arriba: no es texto del cliente
```

En `_reservadas_para_la_base`, el cuerpo después de `if not claves: return set()` pasa a empezar con `collation = await _collation(cur)` (el resto queda igual). Helper nuevo:

```python
async def _ajustes_para_la_base(cur, claves: list[str]) -> dict[str, str]:
    """{clave pedida: clave de ajuste que la base considera IGUAL}. Misma
    autoridad que _reservadas_para_la_base: la collation de la columna decide
    la colisión de la PRIMARY KEY, así que "MAX_PIPELINES" o "máx_pipelines"
    escribirían la fila max_pipelines y tienen que validarse como tal."""
    if not claves:
        return {}
    collation = await _collation(cur)
    pedidas = " UNION ALL ".join(["SELECT %s AS k"] * len(claves))
    conocidas = " UNION ALL ".join(["SELECT %s AS a"] * len(ajustes.CLAVES))
    await cur.execute(
        f"SELECT p.k, c.a FROM ({pedidas}) p JOIN ({conocidas}) c "
        f"ON WEIGHT_STRING(p.k COLLATE {collation}) = WEIGHT_STRING(c.a COLLATE {collation})",
        (*claves, *ajustes.CLAVES),
    )
    return {pedida: conocida for pedida, conocida in await cur.fetchall()}
```

`get_config` devuelve también los límites:

```python
    return {
        "config": [{"key": r[0], "value": r[1]} for r in rows if r[0] not in reservadas],
        # Los rangos los decide el servidor (ajustes.py); la pantalla los usa,
        # no los copia (spec 2026-09-16 §C).
        "limites": ajustes.limites(),
    }
```

`update_config`, desde `pool = await get_pool()` hasta el final:

```python
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            claves = [item.key for item in items]
            if await _reservadas_para_la_base(cur, claves):
                raise HTTPException(status_code=400, detail="config_clave_reservada")
            # Antes de escribir NADA: un lote con un ajuste inválido no se aplica a medias.
            canonicas = await _ajustes_para_la_base(cur, claves)
            for item in items:
                canonica = canonicas.get(item.key)
                if canonica is None:
                    continue
                try:
                    ajustes.interpretar(canonica, item.value)
                except ajustes.ValorInvalido:
                    raise HTTPException(status_code=400,
                                        detail={"code": "config_valor_invalido", "clave": canonica}) from None
            try:
                for item in items:
                    await cur.execute(
                        "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE config_value = %s",
                        (item.key, item.value, item.value),
                    )
            finally:
                # Incluso a medias: lo que sí se escribió se tiene que ver ya.
                ajustes.invalidar()
    return {"ok": True}
```

- [ ] **Step 4: Verlos pasar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_config_admin_ajustes.py tests/test_smtp_endpoints.py tests/test_apariencia.py -v`
Expected: todo `passed`. Los tests de claves reservadas de SMTP no se mueven.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/api/admin/config_admin.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_config_admin_ajustes.py
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): el PUT de configuración valida con la regla de la base e invalida el caché" -m "config_valor_invalido con la clave canónica, sin escribir el lote; GET publica los límites del servidor." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Sesión — `session_timeout_min` es la vida absoluta del refresh

**Files:**
- Modify: `backend/auth/jwt.py` (`REFRESH_EXPIRE_SECONDS` se elimina; `create_refresh_token`)
- Modify: `backend/api/auth.py` (import; `_emitir_tokens`; `login`; `refresh`; `cambiar_mi_password`)
- Modify: `backend/tests/identidades.py:92-94` (`token_para`)
- Modify: `backend/tests/test_sesiones_token_version.py:29`
- Create: `backend/tests/test_ajuste_sesion.py`

**Interfaces:**
- Consumes: `ajustes.valor(ajustes.SESION) -> int` (minutos), `AjusteIlegible` + handler 503 (Task 1), fixtures `ajustes_en_db` y `usuarios`.
- Produces:
  - `create_refresh_token(user_id, tenant_id, role, token_version=0, *, vida_segundos: int) -> str`, con claims `iat` y `exp = iat + vida_segundos`;
  - en `api/auth.py`: `SESION_EXPIRADA = "sesion_expirada"`, `async vida_de_sesion_segundos() -> int`, `sesion_vencida(payload: dict, vida_segundos: int, ahora: float) -> bool`, `_emitir_tokens(response, user_id, tenant_id, role, token_version, vida_segundos) -> str`;
  - en `tests/identidades.py`: `VIDA_DE_REFRESH_EN_TESTS_S = 7 * 24 * 3600`.
  - El frontend no cambia: `api/client.js:100-107` ya muestra `sesion_expirada` cuando el refresh falla sin `sesion_invalida`.

- [ ] **Step 1: Escribir los tests que fallan** — `backend/tests/test_ajuste_sesion.py`

```python
"""session_timeout_min manda (spec 2026-09-16 §C): vida ABSOLUTA del refresh
desde que se emitió -- el refresh no se rota (api/auth.py). Se aplica al
emitir (exp y Max-Age de la cookie) y al renovar, contra `iat`: acortar el
ajuste alcanza a las sesiones ya abiertas. El access sigue en 15 min."""
import re
import time

import pytest
from jose import jwt as jose_jwt

from api.auth import sesion_vencida
from auth.jwt import ALGORITHM, SECRET, create_refresh_token, decode_token
from tests.identidades import auth, sql, token_para

CLAVE = "clave-ajuste-sesion-1"


# ------------------------------------------------------------------ puros

def test_el_refresh_lleva_iat_y_vence_segun_la_vida_pedida():
    antes = int(time.time())
    p = decode_token(create_refresh_token("5", "1", "operator", 2, vida_segundos=3600))
    assert antes <= p["iat"] <= int(time.time())
    assert (p["exp"] - p["iat"], p["type"], p["tv"]) == (3600, "refresh", 2)


def test_el_refresh_no_se_emite_sin_decir_cuanto_vive():
    with pytest.raises(TypeError):
        create_refresh_token("5", "1", "operator", 2)


def test_sesion_vencida_cuenta_desde_iat():
    assert sesion_vencida({"iat": 1000}, 3600, 1000 + 3600) is False
    assert sesion_vencida({"iat": 1000}, 3600, 1000 + 3601) is True
    assert sesion_vencida({}, 3600, 1000) is True  # emitido antes del ajuste: no hay con qué medirlo
    assert sesion_vencida({"iat": "1000"}, 3600, 1000) is True
    assert sesion_vencida({"iat": True}, 3600, 1000) is True


# ------------------------------------------------------------------ con DB

def _max_age(respuesta):
    m = re.search(r"refresh_token=[^;]*;.*?Max-Age=(\d+)", respuesta.headers.get("set-cookie", ""))
    return int(m.group(1)) if m else None


def _refresh(client, token):
    try:
        return client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={token}"})
    finally:
        client.cookies.clear()


def _refresh_emitido_hace(user_id, segundos, tv=0):
    iat = int(time.time()) - segundos
    return jose_jwt.encode(
        {"user_id": str(user_id), "tenant_id": "1", "role": "operator", "tv": tv,
         "iat": iat, "exp": iat + 7 * 24 * 3600, "type": "refresh"},
        SECRET, algorithm=ALGORITHM)


def test_el_login_emite_la_cookie_con_la_vida_del_ajuste(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    _, email = usuarios(password=CLAVE)
    try:
        r = client.post("/api/auth/login", json={"email": email, "password": CLAVE})
        cookie = r.cookies.get("refresh_token")
    finally:
        client.cookies.clear()
    assert r.status_code == 200, r.text
    assert _max_age(r) == 3600
    p = decode_token(cookie)
    assert p["exp"] - p["iat"] == 3600


def test_acortar_la_sesion_alcanza_a_las_que_ya_estan_abiertas(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    u, _ = usuarios()
    r = _refresh(client, _refresh_emitido_hace(u, 2 * 3600))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_expirada")


def test_dentro_de_la_vida_el_refresh_renueva(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    u, _ = usuarios()
    assert _refresh(client, _refresh_emitido_hace(u, 30 * 60)).status_code == 200


def test_un_refresh_sin_iat_ya_no_renueva(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    u, _ = usuarios()
    viejo = jose_jwt.encode(
        {"user_id": str(u), "tenant_id": "1", "role": "operator", "tv": 0,
         "exp": int(time.time()) + 3600, "type": "refresh"},
        SECRET, algorithm=ALGORITHM)
    r = _refresh(client, viejo)
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_expirada")


def test_con_el_ajuste_ilegible_el_login_responde_503_y_no_mata_la_sesion_vieja(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "abc"})
    u, email = usuarios(password=CLAVE, token_version=4)
    try:
        r = client.post("/api/auth/login", json={"email": email, "password": CLAVE})
    finally:
        client.cookies.clear()
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "ajuste_ilegible", "clave": "session_timeout_min"}})
    assert client.portal.call(sql, "SELECT token_version FROM jax_users WHERE user_id = %s", (u,), True) == ((4,),)


def test_cambiar_la_contrasena_emite_la_cookie_con_la_vida_del_ajuste(client, usuarios, ajustes_en_db):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "90"})
    u, _ = usuarios(password=CLAVE)
    try:
        r = client.post("/api/auth/me/password",
                        json={"current_password": CLAVE, "new_password": "Otra-clave-bien-larga-2026"},
                        headers=auth(token_para(u)))
    finally:
        client.cookies.clear()
    assert r.status_code == 200, r.text
    assert _max_age(r) == 5400
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_ajuste_sesion.py -v`
Expected: error de colección `ImportError: cannot import name 'sesion_vencida' from 'api.auth'`. Para ver cada razón por separado, correr con la import de `sesion_vencida` comentada: `KeyError: 'iat'`; `DID NOT RAISE TypeError`; `604800 != 3600`; refresh viejo `200 != 401`; sin `iat` `200 != 401`; login `200 != 503`; contraseña `604800 != 5400`. Después se descomenta la import.

- [ ] **Step 3: Implementar**

`backend/auth/jwt.py`: borrar la línea `REFRESH_EXPIRE_SECONDS = 7 * 24 * 3600` y reemplazar `create_refresh_token`:

```python
# Frente C (2026-09-16): la vida del refresh la decide el ajuste
# session_timeout_min (ajustes.py), no una constante. Se exige por nombre, sin
# default: un refresh emitido sin decir cuánto vive sería un default
# silencioso. `iat` permite medir la vida al renovar (api/auth.py::refresh),
# así que acortar el ajuste alcanza a las sesiones abiertas.
def create_refresh_token(user_id: str, tenant_id: str, role: str, token_version: int = 0, *,
                         vida_segundos: int) -> str:
    ahora = int(time.time())
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
        "iat": ahora,
        "exp": ahora + int(vida_segundos),
        "type": "refresh",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)
```

`backend/api/auth.py`:
- imports: `import time`, `import ajustes`, y la línea de jwt pasa a `from auth.jwt import create_access_token, create_refresh_token, decode_token`;
- debajo de `LOCKOUT_MINUTES = 15`:

```python
SESION_EXPIRADA = "sesion_expirada"


async def vida_de_sesion_segundos() -> int:
    """session_timeout_min en segundos. AjusteIlegible -> 503 (main.py)."""
    return await ajustes.valor(ajustes.SESION) * 60


def sesion_vencida(payload: dict, vida_segundos: int, ahora: float) -> bool:
    """Vida ABSOLUTA desde la emisión (el refresh no se rota). Sin `iat`
    entero, el token es anterior al frente C y no hay con qué medirlo: vencido
    (una sola vez, al desplegar, cada sesión vuelve a entrar)."""
    iat = payload.get("iat")
    if not isinstance(iat, int) or isinstance(iat, bool):
        return True
    return ahora - iat > vida_segundos
```

- `_emitir_tokens`:

```python
def _emitir_tokens(response: Response, user_id: str, tenant_id: str, role: str, token_version: int,
                   vida_segundos: int) -> str:
    """Emite access + refresh con la versión vigente; el refresh va en la
    cookie HttpOnly y vive lo que diga session_timeout_min (frente C). Lo
    usan el login y el cambio de contraseña propio. `vida_segundos` se lee
    ANTES de escribir nada: un ajuste ilegible no puede dejar la versión
    subida y la sesión sin tokens."""
    refresh_token = create_refresh_token(user_id, tenant_id, role, token_version, vida_segundos=vida_segundos)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=vida_segundos,
        samesite="lax",
    )
    return create_access_token(user_id, tenant_id, role, token_version)
```

- `login`: inmediatamente después de `rate_limit.check_login_rate(request, req.email)`:

```python
    # Antes de la DB, del bcrypt y del bump de token_version (frente C).
    vida = await vida_de_sesion_segundos()
```
  y la llamada pasa a `access = _emitir_tokens(response, str(user_id), str(tenant_id), role, token_version, vida)`.

- `refresh`, reemplazar desde `user = await verificar_sesion(...)` hasta el `return`:

```python
    payload = decode_token(refresh_token)
    user = await verificar_sesion(payload, "refresh", admite_cambio_pendiente=True)
    # Frente C: la vida se mide contra el ajuste VIGENTE, no contra el exp con
    # que se emitió -- acortar session_timeout_min alcanza a esta sesión.
    if sesion_vencida(payload, await vida_de_sesion_segundos(), time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_EXPIRADA)
    access = create_access_token(user.user_id, user.tenant_id, user.role, user.token_version)
    return RefreshResponse(access_token=access)
```

- `cambiar_mi_password`: primera línea del cuerpo, antes de `user_id = int(user.user_id)`:

```python
    vida = await vida_de_sesion_segundos()  # antes de la transacción que sube la versión (frente C)
```
  y la llamada final pasa a `access = _emitir_tokens(response, user.user_id, user.tenant_id, user.role, nueva_version, vida)`.

`backend/tests/identidades.py`, reemplazar `token_para`:

```python
# Vida de los refresh que firman los tests (frente C, 2026-09-16: la firma
# exige decirla). 7 días = la vida vigente; los tests que la miden fijan el
# ajuste por su cuenta (tests/test_ajuste_sesion.py).
VIDA_DE_REFRESH_EN_TESTS_S = 7 * 24 * 3600


def token_para(user_id, role="operator", tv=0, tenant_id="1", tipo="access"):
    if tipo == "access":
        return create_access_token(str(user_id), tenant_id, role, tv)
    return create_refresh_token(str(user_id), tenant_id, role, tv, vida_segundos=VIDA_DE_REFRESH_EN_TESTS_S)
```

`backend/tests/test_sesiones_token_version.py:29`: `payload = decode_token(create_refresh_token("5", "1", "operator", 7, vida_segundos=3600))`.

Confirmar que no queda ningún lector de la constante borrada:

Run: `grep -rn "REFRESH_EXPIRE_SECONDS" /home/fruiz/worktrees/jax-platform-frente-c/backend --include=*.py`
Expected: sin salida.

- [ ] **Step 4: Verlos pasar, junto con toda la suite de sesiones**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_ajuste_sesion.py tests/test_sesion_unica.py tests/test_sesiones_token_version.py tests/test_fijar_password.py tests/test_admin_usuarios_baja.py tests/test_auth.py tests/test_login_residuos.py tests/test_login_rate_limit.py tests/test_login_sin_enumeracion.py -v`
Expected: todo `passed`. Si falla un test existente que hace refresh con un token hecho a mano sin `iat`, se le agrega `iat` y el cambio queda anotado en el commit. No se relaja `sesion_vencida`.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/auth/jwt.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/api/auth.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/identidades.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_sesiones_token_version.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_ajuste_sesion.py
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): session_timeout_min es la vida absoluta del refresh" -m "exp, Max-Age y /refresh contra iat con el ajuste vigente; el ajuste se lee antes de subir token_version. Los refresh sin iat (anteriores al despliegue) vencen: una sola vez, cada sesión vuelve a entrar." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `max_pipelines` — cupo por tenant desde el ajuste, con código estable

**Files:**
- Modify: `backend/jax_engine/resource_manager.py`
- Modify: `backend/api/pipelines.py:115-121`
- Modify: `backend/tests/test_pipeline_resource_release.py:1-6` (docstring que cita la constante)
- Create: `backend/tests/test_ajuste_max_pipelines.py`
- Create: `frontend/src/components/BottomBar/errorDePipeline.js`, `frontend/src/components/BottomBar/errorDePipeline.test.js`
- Modify: `frontend/src/components/BottomBar/BottomBar.jsx` (`handlePipelineSubmit`, catch)
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`

**Interfaces:**
- Consumes: `ajustes.valor(ajustes.MAX_PIPELINES) -> int`; `http_client._client` (patrón de `test_pipeline_ownership.py`); `codigoDe(err)` de `api/errores.js`.
- Produces: `ResourceManager.can_start_pipeline(tenant_id: str, limite: int) -> bool` (se elimina `MAX_PIPELINES_PER_TENANT`); 429 `{"detail": {"code": "pipelines_limite_alcanzado", "limite": int}}`; `textoDeErrorDePipeline(t, err) -> string`; i18n `pipelines_limite_alcanzado(n)`.

- [ ] **Step 1: Escribir los tests del backend que fallan** — `backend/tests/test_ajuste_max_pipelines.py`

```python
"""max_pipelines manda (spec 2026-09-16 §C): cupo de pipelines activos POR
TENANT, leído por request desde el ajuste -- subirlo en Admin libera el cupo
sin reiniciar. Tope 3 = el candado global de Jacobs (ajustes.MAX_PARALLEL_PIPELINES)."""
import pytest
from fastapi import HTTPException

import http_client
from api.pipelines import create_pipeline
from auth.models import AuthUser
from jax_engine.resource_manager import ResourceManager, resource_manager
from tests.identidades import cabeceras

TENANT = "ajuste-max-pipelines"
USUARIO = AuthUser(user_id="ajuste-mp-user", tenant_id=TENANT, role="operator")


class _FakeResponse:
    def __init__(self, json_data, status_code):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def post(self, url, **kwargs):
        return self._response


class _FakeRequest:
    async def json(self):
        return {"name": "carga-de-cupo"}


async def test_el_cupo_lo_decide_el_limite_que_se_pasa():
    rm = ResourceManager()
    await rm.admit_pipeline("t", "p1")
    await rm.admit_pipeline("t", "p2")
    assert await rm.can_start_pipeline("t", 2) is False
    assert await rm.can_start_pipeline("t", 3) is True
    assert await rm.can_start_pipeline("otro", 1) is True


async def _crear():
    try:
        return await create_pipeline(request=_FakeRequest(), user=USUARIO)
    except HTTPException as exc:
        return exc


@pytest.fixture
def un_pipeline_activo(client):
    client.portal.call(resource_manager.admit_pipeline, TENANT, "ocupado-1")
    original = http_client._client
    # Si el cupo deja pasar, "Jacobs" responde 422: prueba que se llegó a él sin crear nada.
    http_client._client = _FakeClient(_FakeResponse({"detail": "jacobs_dijo_que_no"}, 422))
    yield
    http_client._client = original
    client.portal.call(resource_manager.release_pipeline, TENANT, "ocupado-1")


def test_con_el_ajuste_en_uno_el_segundo_se_rechaza_con_codigo(client, ajustes_en_db, un_pipeline_activo):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "max_pipelines": "1"})
    exc = client.portal.call(_crear)
    assert isinstance(exc, HTTPException)
    assert (exc.status_code, exc.detail) == (429, {"code": "pipelines_limite_alcanzado", "limite": 1})


def test_subir_el_ajuste_desde_admin_libera_el_cupo_sin_reiniciar(client, ajustes_en_db, un_pipeline_activo):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "max_pipelines": "1"})
    assert client.portal.call(_crear).status_code == 429
    r = client.put("/api/admin/config", json=[{"key": "max_pipelines", "value": "2"}],
                   headers=cabeceras(client, "ajustes-mp", role="superadmin"))
    assert r.status_code == 200
    exc = client.portal.call(_crear)
    assert (exc.status_code, exc.detail) == (422, "jacobs_dijo_que_no")
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_ajuste_max_pipelines.py -v`
Expected: 3 failed. `TypeError: can_start_pipeline() takes 2 positional arguments but 3 were given`; `(422, 'jacobs_dijo_que_no') != (429, {...})` (el límite 3 fijo deja pasar); en el tercero, `422 != 429` en la primera aserción.

- [ ] **Step 3: Implementar el backend**

`backend/jax_engine/resource_manager.py`: borrar `MAX_PIPELINES_PER_TENANT = 3` y reemplazar `can_start_pipeline`:

```python
    async def can_start_pipeline(self, tenant_id: str, limite: int) -> bool:
        # El límite lo decide el ajuste max_pipelines (frente C, 2026-09-16),
        # leído por request en api/pipelines.py: este objeto no toca la DB.
        async with self._lock:
            return len(self._active[tenant_id]) < limite
```

`backend/api/pipelines.py`: sumar `import ajustes` y reemplazar el bloque del chequeo al principio de `create_pipeline`:

```python
    limite = await ajustes.valor(ajustes.MAX_PIPELINES)
    if not await resource_manager.can_start_pipeline(user.tenant_id, limite):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            # Código estable + dato: el frontend lo traduce (errorDePipeline.js).
            detail={"code": "pipelines_limite_alcanzado", "limite": limite},
        )
```

`backend/tests/test_pipeline_resource_release.py`, docstring: reemplazar `concurrent slots (jax_engine/resource_manager.py MAX_PIPELINES_PER_TENANT).` por `concurrent slots (max_pipelines, ajustes.py -- era una constante hasta el frente C).`

- [ ] **Step 4: Verlos pasar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_ajuste_max_pipelines.py tests/test_pipeline_resource_release.py tests/test_pipeline_ownership.py tests/test_pipelines_identity_injection.py tests/test_pipelines_http_pooling.py -v`
Expected: todo `passed`.

Run: `grep -rn "MAX_PIPELINES_PER_TENANT" /home/fruiz/worktrees/jax-platform-frente-c --include=*.py`
Expected: sin salida.

- [ ] **Step 5: Test del frontend que falla** — `frontend/src/components/BottomBar/errorDePipeline.test.js`

```js
import { describe, it, expect } from 'vitest'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'
import { textoDeErrorDePipeline } from './errorDePipeline'

// 429 de POST /api/pipelines (frente C, 2026-09-16): el backend manda código y
// límite; antes mandaba "Límite de 3 pipelines concurrentes alcanzado" en
// español, con el 3 fijo, y BottomBar lo pegaba tal cual.
const rechazo = (detail) => ({ response: { status: 429, data: { detail } } })

describe('textoDeErrorDePipeline', () => {
  it('el límite de pipelines se traduce con el número, en español', () => {
    expect(textoDeErrorDePipeline(es, rechazo({ code: 'pipelines_limite_alcanzado', limite: 2 })))
      .toBe(es.pipelines_limite_alcanzado(2))
    expect(es.pipelines_limite_alcanzado(2)).toContain('2')
  })

  it('y en inglés', () => {
    expect(textoDeErrorDePipeline(en, rechazo({ code: 'pipelines_limite_alcanzado', limite: 1 })))
      .toBe(en.pipelines_limite_alcanzado(1))
    expect(en.pipelines_limite_alcanzado(1)).toContain('1')
  })

  it('un detail de texto se muestra tal cual', () => {
    expect(textoDeErrorDePipeline(es, rechazo('Kill switch activo'))).toBe('Kill switch activo')
  })

  it('sin detail, o con un código que no conoce, cae en el genérico', () => {
    expect(textoDeErrorDePipeline(es, {})).toBe(es.errorPipeline)
    expect(textoDeErrorDePipeline(es, rechazo({ code: 'otro' }))).toBe(es.errorPipeline)
  })
})
```

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run src/components/BottomBar/errorDePipeline.test.js`
Expected: FAIL, `Failed to resolve import "./errorDePipeline"`.

- [ ] **Step 6: Implementar el frontend**

`frontend/src/components/BottomBar/errorDePipeline.js`:

```js
import { codigoDe } from '../../api/errores'

// Texto del fallo al crear un pipeline. El 429 de cupo llega como código y
// límite (backend/api/pipelines.py, frente C); los demás detail de texto se
// muestran como llegan, y sin detail utilizable, el genérico.
export function textoDeErrorDePipeline(t, err) {
  const detail = err?.response?.data?.detail
  if (codigoDe(err) === 'pipelines_limite_alcanzado') return t.pipelines_limite_alcanzado(detail.limite)
  return typeof detail === 'string' && detail ? detail : t.errorPipeline
}
```

`BottomBar.jsx`: `import { textoDeErrorDePipeline } from './errorDePipeline'`, y en el `catch` de `handlePipelineSubmit` reemplazar `const detail = err.response?.data?.detail || t.errorPipeline` por `const detail = textoDeErrorDePipeline(t, err)`.

`es.js`, junto a `errorPipeline`:

```js
  pipelines_limite_alcanzado: (n) => n === 1
    ? 'Ya hay 1 pipeline activo, el máximo permitido. Espera a que termine.'
    : `Ya hay ${n} pipelines activos, el máximo permitido. Espera a que termine uno.`,
```

`en.js`, en la misma posición:

```js
  pipelines_limite_alcanzado: (n) => n === 1
    ? 'There is already 1 active pipeline, the maximum allowed. Wait for it to finish.'
    : `There are already ${n} active pipelines, the maximum allowed. Wait for one to finish.`,
```

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run src/components/BottomBar`
Expected: todos `passed` (4 nuevos).

- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/jax_engine/resource_manager.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/api/pipelines.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_pipeline_resource_release.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_ajuste_max_pipelines.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/BottomBar/errorDePipeline.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/BottomBar/errorDePipeline.test.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/BottomBar/BottomBar.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/es.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/en.js
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): max_pipelines decide el cupo por tenant, con código estable" -m "429 pipelines_limite_alcanzado con el límite leído del ajuste; el frontend lo traduce. Sin constante 3 ni texto en español." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `web_task_retention_days` — el reaper usa el ajuste y, si está ilegible, no borra

**Files:**
- Modify: `backend/jax_engine/owner_cleanup.py`
- Modify: `backend/tests/test_owner_cleanup.py`

**Interfaces:**
- Consumes: `ajustes.valor(ajustes.RETENCION) -> int` (días), `ajustes.AjusteIlegible`.
- Produces: `reap_orphaned_command_owner_files(missions_dir: Path, max_age_seconds: float) -> int` (parámetro obligatorio; se elimina `COMMAND_OWNER_MAX_AGE_SECONDS`); `async ciclo_de_limpieza(missions_dir: Path) -> int | None`; `SEGUNDOS_POR_DIA = 24 * 3600`.

- [ ] **Step 1: Escribir los tests que fallan** — al final de `backend/tests/test_owner_cleanup.py`, más imports arriba (`import logging`, `import ajustes`, `from jax_engine import owner_cleanup`)

```python
# ---------------------------------------------------------------------------
# Frente C (2026-09-16): la edad máxima sale de web_task_retention_days. Si el
# ajuste está ilegible, el ciclo NO borra nada (fail-closed: sin saber cuánto
# se retiene no se destruye) y lo deja en el log como ERROR.

def _owner_con_hermanos(carpeta, task_id, dias_de_edad):
    owner = carpeta / f"web-task-{task_id}_owner.json"
    owner.write_text(json.dumps({"tenant_id": "1", "user_id": "u"}))
    (carpeta / f"web-task-{task_id}.md").write_text("mission")
    (carpeta / f"web-task-{task_id}_result.md").write_text("result")
    viejo = time.time() - dias_de_edad * 24 * 3600
    os.utime(owner, (viejo, viejo))
    return owner


async def test_el_ciclo_usa_los_dias_del_ajuste(tmp_path, monkeypatch):
    async def valor(clave):
        assert clave == ajustes.RETENCION
        return 2
    monkeypatch.setattr(ajustes, "valor", valor)
    viejo = _owner_con_hermanos(tmp_path, "viejo", 3)
    reciente = _owner_con_hermanos(tmp_path, "reciente", 1)
    assert await owner_cleanup.ciclo_de_limpieza(tmp_path) == 1
    assert not viejo.exists() and reciente.exists()


async def test_con_el_ajuste_ilegible_no_borra_nada_y_lo_dice(tmp_path, monkeypatch, caplog):
    async def valor(clave):
        raise ajustes.AjusteIlegible(clave, "invalido")
    monkeypatch.setattr(ajustes, "valor", valor)
    viejo = _owner_con_hermanos(tmp_path, "viejo", 400)
    with caplog.at_level(logging.ERROR, logger="jax_engine.owner_cleanup"):
        assert await owner_cleanup.ciclo_de_limpieza(tmp_path) is None
    assert viejo.exists()
    assert "web_task_retention_days" in caplog.text
```

En los cuatro tests existentes que llaman `reap_orphaned_command_owner_files(tmp_path)` o `(missing)` sin edad, agregar `max_age_seconds=30 * 24 * 3600`. El que ya pasa `max_age_seconds=1800` queda igual.

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_owner_cleanup.py -v`
Expected: los 2 nuevos en FAIL con `AttributeError: module 'jax_engine.owner_cleanup' has no attribute 'ciclo_de_limpieza'`; los existentes en `passed`.

- [ ] **Step 3: Implementar** — `backend/jax_engine/owner_cleanup.py`

En el docstring, reemplazar `make this reaper a no-op in practice -- COMMAND_OWNER_MAX_AGE_SECONDS is a` por `make this reaper a no-op in practice -- the age cutoff (web_task_retention_days, ajustes.py, frente C 2026-09-16) is a`. Después: sumar `import logging` e `import ajustes`, y reemplazar desde `COMMAND_OWNER_MAX_AGE_SECONDS = ...` hasta el final:

```python
logger = logging.getLogger(__name__)

SEGUNDOS_POR_DIA = 24 * 3600
CLEANUP_INTERVAL_SECONDS = 6 * 3600  # cada 6 horas


def reap_orphaned_command_owner_files(missions_dir: Path, max_age_seconds: float) -> int:
    if not missions_dir.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    reaped = 0
    for owner_file in missions_dir.glob("web-task-*_owner.json"):
        task_id = owner_file.name[len("web-task-"):-len("_owner.json")]
        mission_file = missions_dir / f"web-task-{task_id}.md"
        result_file = missions_dir / f"web-task-{task_id}_result.md"
        try:
            siblings_gone = not mission_file.exists() and not result_file.exists()
            too_old = owner_file.stat().st_mtime < cutoff
            if siblings_gone or too_old:
                owner_file.unlink()
                reaped += 1
        except OSError:  # fail-soft: unlink en reaper con carrera TOCTOU benigna (el archivo ya pudo desaparecer); el proximo ciclo reintenta
            pass
    return reaped


async def ciclo_de_limpieza(missions_dir: Path) -> int | None:
    """Un ciclo con la retención VIGENTE (web_task_retention_days: días que
    el dueño de una tarea web puede seguir viendo su resultado -- sin owner
    file, GET /api/command/{id} le da 404). Ajuste ilegible: no borra nada."""
    try:
        dias = await ajustes.valor(ajustes.RETENCION)
    except ajustes.AjusteIlegible as exc:
        logger.error("limpieza de owner files omitida: el ajuste %s está ilegible (%s); "
                     "no se borra nada hasta corregirlo", exc.clave, exc.motivo)
        return None
    return await asyncio.to_thread(reap_orphaned_command_owner_files, missions_dir, dias * SEGUNDOS_POR_DIA)


async def start_owner_file_cleanup():
    # Import diferido: no hay ciclo real (jax_engine.state sólo importa
    # .schemas/.events/.resource_manager/http_client), pero mantiene este
    # módulo sin depender de que api.command ya esté cargado en el momento
    # en que jax_engine.owner_cleanup se importa.
    from api.command import MISSIONS_DIR

    while True:
        try:
            await ciclo_de_limpieza(MISSIONS_DIR)
        except Exception:  # fail-soft: loop de limpieza en background, reintenta cada intervalo; documentado como best-effort explicito
            pass  # best-effort: nunca debe tumbar el proceso
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
```

- [ ] **Step 4: Verlos pasar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_owner_cleanup.py tests/test_no_fail_open_except.py -v`
Expected: todo `passed`. El scanner P10 no encuentra un `except` nuevo sin marca.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/jax_engine/owner_cleanup.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_owner_cleanup.py
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): el reaper de owner files retiene lo que diga web_task_retention_days" -m "Con el ajuste ilegible no borra nada y registra ERROR." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `GET /api/apariencia` publica `lang_default` y `system_name`

**Files:**
- Modify: `backend/api/apariencia.py`
- Modify: `backend/tests/test_apariencia.py`

**Interfaces:**
- Consumes: `ajustes.valor(ajustes.IDIOMA)`, `ajustes.valor(ajustes.NOMBRE)`, `ajustes.IDIOMAS`; handler 503 (Task 1).
- Produces: `GET /api/apariencia` → `{"theme_default": "dark"|"light", "lang_default": "es"|"en", "system_name": str}`, sin autenticación, `Cache-Control: no-cache`; 503 `ajuste_ilegible` si idioma o nombre están ilegibles. Lo consume la Task 8.

- [ ] **Step 1: Ajustar y escribir los tests que fallan** — `backend/tests/test_apariencia.py`

Import nuevo: `from tests.identidades import cabeceras`. El fixture autouse pasa a:

```python
@pytest.fixture(autouse=True)
def fila_intacta(client, ajustes_en_db):
    ajustes_en_db.poner(lang_default="es", system_name="Axioma")
    antes = client.portal.call(_leer)
    yield
    client.portal.call(_restaurar, antes)
```

Cambios en los existentes:
- `test_devuelve_el_valor_guardado`: `assert r.json() == {"theme_default": "light", "lang_default": "es", "system_name": "Axioma"}`
- `test_fila_ausente_da_oscuro`: `assert client.get("/api/apariencia").json()["theme_default"] == "dark"`
- `test_valor_fuera_de_lista_da_oscuro`: `assert client.get("/api/apariencia").json()["theme_default"] == "dark", valor`
- `test_no_devuelve_otras_claves`: `assert set(r.json()) == {"theme_default", "lang_default", "system_name"}`
- docstring del módulo: `Público y sin parámetros: devuelve theme_default (validado contra {'dark','light'}, con respaldo 'dark') y, desde el frente C (2026-09-16), lang_default y system_name vía ajustes.py (ilegibles -> 503, sin respaldo).`

Nuevos, al final:

```python
def test_devuelve_el_idioma_y_el_nombre_del_sistema(client, ajustes_en_db):
    ajustes_en_db.poner(lang_default="en", system_name="Hal")
    j = client.get("/api/apariencia").json()
    assert (j["lang_default"], j["system_name"]) == ("en", "Hal")


def test_un_nombre_ilegible_responde_503_con_codigo_y_no_un_default(client, ajustes_en_db):
    ajustes_en_db.poner(system_name=" ")
    r = client.get("/api/apariencia")
    assert (r.status_code, r.json()) == (503, {"detail": {"code": "ajuste_ilegible", "clave": "system_name"}})


def test_un_cambio_desde_admin_se_ve_en_la_carga_siguiente(client, ajustes_en_db):
    assert client.get("/api/apariencia").json()["system_name"] == "Axioma"
    r = client.put("/api/admin/config", json=[{"key": "system_name", "value": "Axioma Lab"}],
                   headers=cabeceras(client, "ajustes-apariencia", role="superadmin"))
    assert r.status_code == 200
    assert client.get("/api/apariencia").json()["system_name"] == "Axioma Lab"
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_apariencia.py -v`
Expected: FAIL en `test_devuelve_el_valor_guardado` y `test_no_devuelve_otras_claves` (faltan dos claves), `KeyError: 'lang_default'`, `200 != 503` y `KeyError: 'system_name'` en los nuevos. Los demás pasan.

- [ ] **Step 3: Implementar** — `backend/api/apariencia.py`

Reemplazar el docstring del módulo, los imports y todo lo de abajo de `CONSULTA`:

```python
"""Apariencia pública de la instancia (spec 2026-09-14-tema-tokens §5.2;
frente C del spec 2026-09-16-hallazgos-auditoria).

GET /api/apariencia, SIN autenticación: el Login y el Reset lo necesitan antes
de que haya sesión, y la config de admin exige superadmin. Devuelve una lista
BLANCA de claves (no por prefijo excluido): smtp.* vive en la misma tabla y
este endpoint no puede devolverlo aunque se agreguen claves.

  - theme_default: validado contra una lista cerrada (termina en un atributo
    del DOM), con respaldo DEFAULT_CONFIG. Sin caché: una lectura por PRIMARY
    KEY (EXPLAIN en test_apariencia.py).
  - lang_default y system_name: por ajustes.py (caché con TTL, invalidado por
    el PUT de admin). Ilegibles -> 503 ajuste_ilegible, sin respaldo: el
    frontend se queda con el último conocido.
"""
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

import ajustes
from api.admin.config_admin import DEFAULT_CONFIG
from db.connection import get_pool

router = APIRouter()

CLAVE = "theme_default"
TEMAS = ("dark", "light")
# Por PRIMARY KEY (config_key). test_apariencia.py corre EXPLAIN sobre esta constante.
CONSULTA = "SELECT config_value FROM axioma_config WHERE config_key = %s"


class Apariencia(BaseModel):
    """Contrato de salida (M-1, revisión final del PR 1, 2026-09-14): un dict
    más grande (o un valor fuera de contrato) falla ruidoso en la validación
    de FastAPI en vez de salir al cliente."""

    theme_default: Literal["dark", "light"]
    lang_default: Literal[ajustes.IDIOMAS]
    system_name: str


@router.get("/api/apariencia", response_model=Apariencia)
async def apariencia(response: Response):
    response.headers["Cache-Control"] = "no-cache"
    idioma = await ajustes.valor(ajustes.IDIOMA)
    nombre = await ajustes.valor(ajustes.NOMBRE)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CONSULTA, (CLAVE,))
            fila = await cur.fetchone()
    valor = fila[0] if fila else None
    return {
        "theme_default": valor if valor in TEMAS else DEFAULT_CONFIG[CLAVE],
        "lang_default": idioma,
        "system_name": nombre,
    }
```

- [ ] **Step 4: Verlos pasar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_apariencia.py -v`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/api/apariencia.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/tests/test_apariencia.py
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): /api/apariencia publica el idioma inicial y el nombre del sistema" -m "Lista blanca de tres claves; idioma y nombre ilegibles responden 503 ajuste_ilegible." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend — un store de apariencia y UNA sola lectura de `/apariencia`

**Files:**
- Create: `frontend/src/store/useApariencia.js`, `frontend/src/store/useApariencia.test.js`
- Create: `frontend/src/apariencia/sincronizarApariencia.js`, `frontend/src/apariencia/sincronizarApariencia.test.js`
- Create: `frontend/src/i18n/idioma.js` (solo lo que usa esta Task; la Task 9 lo completa)
- Modify: `frontend/src/store/useTema.js` (se quita `sincronizarPredeterminado` y el import de `api`)
- Modify: `frontend/src/store/useTema.test.js` (salen los 4 tests de `sincronizarPredeterminado`, que se mudan)
- Modify: `frontend/src/App.jsx`

**Interfaces:**
- Consumes: `GET /api/apariencia` (Task 7); `useTema.getState().fijarPredeterminado(valor)`.
- Produces:
  - `i18n/idioma.js`: `IDIOMAS = ['es', 'en']`, `IDIOMA_DE_RESPALDO = 'es'`, `CLAVE_ELECCION_IDIOMA = 'jax_lang'`, `CLAVE_IDIOMA_PREDETERMINADO = 'jax_lang_default'`, `esIdioma(v) -> boolean`;
  - `store/useApariencia.js`: `CLAVE_NOMBRE = 'jax_system_name'`, `esNombre(v) -> boolean`, `useApariencia` (zustand) con `systemName: string|null`, `langDefault: 'es'|'en'|null` y `fijar(data)`, y `useNombreDelSistema(t) -> string` (`systemName ?? t.brandName`);
  - `apariencia/sincronizarApariencia.js`: `async sincronizarApariencia()` (rechaza si la red falla).
  - `useApariencia` **no** importa `api/client`: `I18nProvider` lo va a usar (Task 9) y lo renderizan 92 tests.

- [ ] **Step 1: Escribir los tests que fallan**

`frontend/src/store/useApariencia.test.js`:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useApariencia } from './useApariencia'

// Nombre del sistema e idioma predeterminado (frente C, 2026-09-16): llegan por
// GET /apariencia. El último conocido se guarda en este navegador (conveniencia
// por visitante) para titular la pestaña antes de la próxima respuesta.
beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
  document.title = 'Axioma'
})

describe('useApariencia', () => {
  it('fijar guarda el nombre y el idioma y titula el documento', () => {
    useApariencia.getState().fijar({ system_name: 'Hal', lang_default: 'en' })
    expect(useApariencia.getState()).toMatchObject({ systemName: 'Hal', langDefault: 'en' })
    expect(localStorage.getItem('jax_system_name')).toBe('Hal')
    expect(localStorage.getItem('jax_lang_default')).toBe('en')
    expect(document.title).toBe('Hal')
  })

  it('un nombre vacío, de espacios o que no es texto no se aplica; un idioma fuera de lista tampoco', () => {
    useApariencia.setState({ systemName: 'Axioma' })
    for (const valor of ['', '   ', 42, null, undefined]) {
      useApariencia.getState().fijar({ system_name: valor, lang_default: 'fr' })
      expect(useApariencia.getState()).toMatchObject({ systemName: 'Axioma', langDefault: null })
    }
    expect(document.title).toBe('Axioma')
  })

  it('al cargar el módulo toma el último nombre conocido y titula', async () => {
    localStorage.setItem('jax_system_name', 'Hal')
    localStorage.setItem('jax_lang_default', 'en')
    vi.resetModules()
    const { useApariencia: fresco } = await import('./useApariencia')
    expect(fresco.getState()).toMatchObject({ systemName: 'Hal', langDefault: 'en' })
    expect(document.title).toBe('Hal')
  })

  it('con localStorage bloqueado, importar y fijar no lanzan', async () => {
    const getItemOriginal = Storage.prototype.getItem
    const setItemOriginal = Storage.prototype.setItem
    Storage.prototype.getItem = () => { throw new DOMException('bloqueado', 'SecurityError') }
    Storage.prototype.setItem = () => { throw new DOMException('bloqueado', 'SecurityError') }
    try {
      vi.resetModules()
      const { useApariencia: fresco } = await import('./useApariencia')
      expect(fresco.getState().systemName).toBeNull()
      expect(() => fresco.getState().fijar({ system_name: 'Hal', lang_default: 'es' })).not.toThrow()
      expect(fresco.getState().systemName).toBe('Hal')
    } finally {
      Storage.prototype.getItem = getItemOriginal
      Storage.prototype.setItem = setItemOriginal
    }
  })
})
```

`frontend/src/apariencia/sincronizarApariencia.test.js`. Los cuatro primeros son los tests de `sincronizarPredeterminado` de `useTema.test.js`, mudados sin cambiar lo que afirman:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../api/client'
import { sincronizarApariencia } from './sincronizarApariencia'
import { useTema } from '../store/useTema'
import { useApariencia } from '../store/useApariencia'
import { aplicarTema } from '../tema/aplicarTema'

// Una sola petición a GET /apariencia por carga (frente C, 2026-09-16): trae el
// tema (spec 2026-09-14-tema-tokens §5), el idioma y el nombre del sistema.
const html = () => document.documentElement
const APARIENCIA = { lang_default: 'es', system_name: 'Axioma' }

beforeEach(() => {
  api.get.mockReset()
  localStorage.clear()
  aplicarTema('dark')
  useTema.setState({ theme: 'dark', predeterminado: null })
  useApariencia.setState({ systemName: null, langDefault: null })
  document.title = 'Axioma'
})

describe('sincronizarApariencia', () => {
  it('sin elección, el predeterminado del servidor se guarda y se aplica', async () => {
    api.get.mockResolvedValue({ data: { ...APARIENCIA, theme_default: 'light' } })
    await sincronizarApariencia()
    expect(api.get).toHaveBeenCalledWith('/apariencia')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
  })

  it('con elección guardada, un predeterminado distinto no cambia el tema', async () => {
    localStorage.setItem('jax_theme', 'dark')
    api.get.mockResolvedValue({ data: { ...APARIENCIA, theme_default: 'light' } })
    await sincronizarApariencia()
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
  })

  it('un valor fuera de lista del servidor se ignora', async () => {
    api.get.mockResolvedValue({ data: { ...APARIENCIA, theme_default: '<b>claro</b>' } })
    await sincronizarApariencia()
    expect(localStorage.getItem('jax_theme_default')).toBeNull()
    expect(useTema.getState().theme).toBe('dark')
  })

  it('si /apariencia falla, rechaza y se queda el último predeterminado conocido', async () => {
    localStorage.setItem('jax_theme_default', 'light')
    api.get.mockRejectedValue(new Error('red caída'))
    await expect(sincronizarApariencia()).rejects.toThrow('red caída')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
  })

  it('una sola petición deja el nombre, el idioma y el título del documento', async () => {
    api.get.mockResolvedValue({ data: { theme_default: 'dark', lang_default: 'en', system_name: 'Axioma Lab' } })
    await sincronizarApariencia()
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(useApariencia.getState()).toMatchObject({ systemName: 'Axioma Lab', langDefault: 'en' })
    expect(document.title).toBe('Axioma Lab')
  })
})
```

En `frontend/src/store/useTema.test.js`, borrar los `it(...)` titulados `sin elección, el predeterminado del servidor se guarda y se aplica`, `con elección guardada, un predeterminado distinto no cambia el tema`, `un valor fuera de lista del servidor se ignora` y `si /apariencia falla, rechaza y se queda el último predeterminado conocido`. Borrar también `vi.mock('../api/client', ...)`, `import api from '../api/client'` y `api.get.mockReset()` del `beforeEach`. En el comentario de cabecera: `el theme_default del sistema, que llega por GET /apariencia (apariencia/sincronizarApariencia.js)`.

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run src/store/useApariencia.test.js src/apariencia/sincronizarApariencia.test.js src/store/useTema.test.js`
Expected: los dos archivos nuevos en FAIL con `Failed to resolve import`; `useTema.test.js` en `passed` (quedan 5 tests).

- [ ] **Step 3: Implementar**

`frontend/src/i18n/idioma.js`:

```js
// Idioma de la interfaz (frente C, 2026-09-16). Sin React: lo usan
// I18nProvider, el store de apariencia y useJaxStore (fuera de React).
export const IDIOMAS = ['es', 'en']
export const IDIOMA_DE_RESPALDO = 'es'
export const CLAVE_ELECCION_IDIOMA = 'jax_lang' // lo que eligió el usuario
export const CLAVE_IDIOMA_PREDETERMINADO = 'jax_lang_default' // último lang_default conocido del sistema

export function esIdioma(valor) {
  return IDIOMAS.includes(valor)
}
```

`frontend/src/store/useApariencia.js`:

```js
import { create } from 'zustand'
import { CLAVE_IDIOMA_PREDETERMINADO, esIdioma } from '../i18n/idioma'

// Nombre del sistema e idioma predeterminado (frente C, 2026-09-16), desde
// GET /apariencia (apariencia/sincronizarApariencia.js). Sin import de
// api/client a propósito: I18nProvider usa este store y lo renderizan casi
// todos los tests. El último valor conocido se guarda en este navegador para
// titular la pestaña antes de la respuesta; el almacenamiento es fail-soft
// (Safari con cookies bloqueadas, iframe con sandbox), como useTema.
export const CLAVE_NOMBRE = 'jax_system_name'

function leer(clave) {
  try {
    return localStorage.getItem(clave)
  } catch {
    return null
  }
}

function escribir(clave, valor) {
  try {
    localStorage.setItem(clave, valor)
  } catch {
    // fail-soft: sin almacenamiento, el valor vive en memoria esta carga
  }
}

export function esNombre(valor) {
  return typeof valor === 'string' && valor.trim() !== ''
}

function titular(nombre) {
  if (esNombre(nombre)) document.title = nombre
}

const nombreGuardado = leer(CLAVE_NOMBRE)
const idiomaGuardado = leer(CLAVE_IDIOMA_PREDETERMINADO)
titular(nombreGuardado)

export const useApariencia = create((set) => ({
  systemName: esNombre(nombreGuardado) ? nombreGuardado : null,
  langDefault: esIdioma(idiomaGuardado) ? idiomaGuardado : null,

  // Lo que no pasa la validación no se aplica: se queda el último conocido.
  fijar: (data) => {
    const cambios = {}
    if (esNombre(data?.system_name)) {
      escribir(CLAVE_NOMBRE, data.system_name)
      titular(data.system_name)
      cambios.systemName = data.system_name
    }
    if (esIdioma(data?.lang_default)) {
      escribir(CLAVE_IDIOMA_PREDETERMINADO, data.lang_default)
      cambios.langDefault = data.lang_default
    }
    set(cambios)
  },
}))

// El nombre que muestra la UI. Antes de conocer el del servidor (primera
// visita, sin red), la marca de i18n: es lo que se mostraba hasta el frente C.
export function useNombreDelSistema(t) {
  const nombre = useApariencia((s) => s.systemName)
  return nombre ?? t.brandName
}
```

`frontend/src/apariencia/sincronizarApariencia.js`:

```js
import api from '../api/client'
import { useTema } from '../store/useTema'
import { useApariencia } from '../store/useApariencia'

// UNA petición por carga para todo lo público de la instancia (frente C,
// 2026-09-16): tema, idioma y nombre. Rechaza si falla: quien llama decide
// (App y AdminSettings se quedan con los últimos conocidos).
export async function sincronizarApariencia() {
  const { data } = await api.get('/apariencia')
  useTema.getState().fijarPredeterminado(data?.theme_default)
  useApariencia.getState().fijar(data)
}
```

`frontend/src/store/useTema.js`: borrar `import api from '../api/client'` y el método `sincronizarPredeterminado` completo, con su comentario. En el comentario de cabecera, reemplazar `lo usan App (sincroniza el predeterminado al montar)` por `lo usan apariencia/sincronizarApariencia.js (el predeterminado al montar)`. En el comentario de `fijarPredeterminadoComoEleccion`, cambiar `sincronizarPredeterminado` por `sincronizarApariencia`.

`frontend/src/App.jsx`: borrar `import { useTema } from './store/useTema'` y `const sincronizarTema = ...`, sumar `import { sincronizarApariencia } from './apariencia/sincronizarApariencia'`, y el segundo `useEffect` queda:

```jsx
  useEffect(() => {
    // Una vez por carga, también en Login y Reset (no hay sesión). Si falla,
    // se quedan el tema, el idioma y el nombre últimos conocidos: es
    // presentación, no autorización (spec 2026-09-14 §5.2; frente C).
    sincronizarApariencia().catch(() => {})
  }, [])
```

Run: `grep -rn "sincronizarPredeterminado" /home/fruiz/worktrees/jax-platform-frente-c/frontend/src`
Expected: sin salida.

- [ ] **Step 4: Verlos pasar, más la suite completa**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run`
Expected: 0 fallidos; `passed` = el de antes + 9 (4 de useApariencia + 5 de sincronizar) − 4 (mudados) = +5 respecto de la Task 5.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/idioma.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/store/useApariencia.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/store/useApariencia.test.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/apariencia/sincronizarApariencia.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/apariencia/sincronizarApariencia.test.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/store/useTema.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/store/useTema.test.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/App.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): store de apariencia y una sola lectura de /apariencia" -m "Tema, idioma y nombre del sistema en una petición; el título del documento sale del nombre." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Frontend — `lang_default` decide el idioma cuando el usuario no eligió

**Files:**
- Modify: `frontend/src/i18n/idioma.js` (sumar `idiomaInicial`)
- Create: `frontend/src/i18n/idioma.test.js`, `frontend/src/i18n/index.test.jsx`
- Modify: `frontend/src/i18n/index.jsx`
- Modify: `frontend/src/store/useJaxStore.js:8-13`

**Interfaces:**
- Consumes: `useApariencia((s) => s.langDefault)`, constantes de `i18n/idioma.js`.
- Produces: `idiomaInicial(almacen = globalThis.localStorage) -> 'es'|'en'` (elección > predeterminado guardado > `'es'`). `I18nProvider` sigue exponiendo `{ lang, setLang, t }` y solo `setLang` escribe `jax_lang`.

- [ ] **Step 1: Escribir los tests que fallan**

`frontend/src/i18n/idioma.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { idiomaInicial } from './idioma'

const almacen = (datos) => ({ getItem: (k) => (k in datos ? datos[k] : null) })

describe('idiomaInicial', () => {
  it('la elección del usuario gana', () => {
    expect(idiomaInicial(almacen({ jax_lang: 'es', jax_lang_default: 'en' }))).toBe('es')
  })
  it('sin elección, el predeterminado del sistema', () => {
    expect(idiomaInicial(almacen({ jax_lang_default: 'en' }))).toBe('en')
  })
  it('sin nada, español', () => {
    expect(idiomaInicial(almacen({}))).toBe('es')
  })
  it('valores fuera de lista se ignoran', () => {
    expect(idiomaInicial(almacen({ jax_lang: 'fr', jax_lang_default: '<b>' }))).toBe('es')
    expect(idiomaInicial(almacen({ jax_lang: 'fr', jax_lang_default: 'en' }))).toBe('en')
  })
  it('con el almacenamiento bloqueado, español sin lanzar', () => {
    const bloqueado = { getItem: () => { throw new DOMException('bloqueado', 'SecurityError') } }
    expect(idiomaInicial(bloqueado)).toBe('es')
  })
})
```

`frontend/src/i18n/index.test.jsx`:

```jsx
import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { I18nProvider, useI18n } from './index.jsx'
import { useApariencia } from '../store/useApariencia'

// lang_default (frente C, 2026-09-16): el idioma del sistema manda mientras
// la persona no elija; su elección (jax_lang) gana siempre.
function Idioma() {
  const { lang, t } = useI18n()
  return <span data-testid="idioma">{`${lang}|${t.emailLabel}`}</span>
}

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
})

describe('I18nProvider con idioma predeterminado', () => {
  it('sin elección, el predeterminado del sistema cambia la interfaz sin guardarse como elección', async () => {
    render(<I18nProvider><Idioma /></I18nProvider>)
    expect(screen.getByTestId('idioma').textContent.startsWith('es|')).toBe(true)
    act(() => useApariencia.getState().fijar({ lang_default: 'en' }))
    expect(screen.getByTestId('idioma').textContent.startsWith('en|')).toBe(true)
    expect(localStorage.getItem('jax_lang')).toBeNull()
  })

  it('con una elección guardada, el predeterminado no la pisa', () => {
    localStorage.setItem('jax_lang', 'es')
    render(<I18nProvider><Idioma /></I18nProvider>)
    act(() => useApariencia.getState().fijar({ lang_default: 'en' }))
    expect(screen.getByTestId('idioma').textContent.startsWith('es|')).toBe(true)
  })
})
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run src/i18n`
Expected: `idioma.test.js` en FAIL (`idiomaInicial is not a function` / `does not provide an export named 'idiomaInicial'`); `index.test.jsx`, primer test en FAIL (sigue `es|` después de fijar `en`); el segundo pasa (es guarda).

- [ ] **Step 3: Implementar**

Agregar a `frontend/src/i18n/idioma.js`:

```js
// Elección del usuario > último predeterminado del sistema > español.
export function idiomaInicial(almacen = globalThis.localStorage) {
  try {
    const eleccion = almacen.getItem(CLAVE_ELECCION_IDIOMA)
    if (esIdioma(eleccion)) return eleccion
    const predeterminado = almacen.getItem(CLAVE_IDIOMA_PREDETERMINADO)
    if (esIdioma(predeterminado)) return predeterminado
  } catch {
    // fail-soft: almacenamiento bloqueado -- idioma de respaldo
  }
  return IDIOMA_DE_RESPALDO
}
```

`frontend/src/i18n/index.jsx`, reemplazar el import de `react` y `I18nProvider`:

```jsx
import { createContext, useContext, useState } from 'react'
import es from './es.js'
import en from './en.js'
import { CLAVE_ELECCION_IDIOMA, IDIOMA_DE_RESPALDO, esIdioma } from './idioma'
import { useApariencia } from '../store/useApariencia'
```

```jsx
function eleccionGuardada() {
  try {
    const valor = localStorage.getItem(CLAVE_ELECCION_IDIOMA)
    return esIdioma(valor) ? valor : null
  } catch {
    return null // fail-soft: almacenamiento bloqueado
  }
}

export function I18nProvider({ children }) {
  // Frente C (2026-09-16): la ELECCIÓN de la persona (jax_lang) gana; si no
  // eligió, manda lang_default del sistema (useApariencia), y si todavía no se
  // conoce, español. Solo setLang escribe una elección.
  const [eleccion, setEleccion] = useState(eleccionGuardada)
  const langDefault = useApariencia((s) => s.langDefault)
  const lang = eleccion ?? (esIdioma(langDefault) ? langDefault : IDIOMA_DE_RESPALDO)

  function setLang(l) {
    setEleccion(l)
    try {
      localStorage.setItem(CLAVE_ELECCION_IDIOMA, l)
    } catch {
      // fail-soft: la elección vale para esta carga
    }
  }

  return (
    <I18nContext.Provider value={{ lang, setLang, t: LANGS[lang] || LANGS.es }}>
      {children}
    </I18nContext.Provider>
  )
}
```

`frontend/src/store/useJaxStore.js`: sumar `import { idiomaInicial } from '../i18n/idioma'` y reemplazar `_t` y su comentario:

```js
// Este módulo no es un componente — no puede usar el hook useI18n(). Lee la
// misma regla que I18nProvider (i18n/idioma.js: elección > predeterminado del
// sistema > español) para los mensajes que se generan acá (eventos de WS).
function _t() {
  return idiomaInicial() === 'en' ? en : es
}
```

- [ ] **Step 4: Verlos pasar, más la suite completa**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run`
Expected: 0 fallidos; +7 respecto de la Task 8.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/idioma.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/idioma.test.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/index.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/index.test.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/store/useJaxStore.js
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): lang_default decide el idioma mientras la persona no elija" -m "Elección > predeterminado del sistema > español, la misma regla en I18nProvider y en el store." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Frontend — `system_name` en el login, el encabezado y Admin

**Files:**
- Modify: `frontend/src/components/LogoAxioma.jsx`, `frontend/src/components/LogoAxioma.test.jsx`
- Modify: `frontend/src/pages/Login.jsx`, `frontend/src/pages/Login.test.jsx`
- Modify: `frontend/src/components/admin/AdminSidebar.jsx`
- Create: `frontend/src/components/admin/AdminSidebar.test.jsx`
- Modify: `frontend/src/pages/admin/AdminSmtp.jsx:150`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js` (`loginButton`, `adminBack` y `smtpDesc` pasan a funciones; se borra `loginTitle`)
- Create: `frontend/src/i18n/nombreDelSistema.test.js`

**Interfaces:**
- Consumes: `useNombreDelSistema(t)`, `useApariencia` (Task 8).
- Produces: `t.loginButton(nombre)`, `t.adminBack(nombre)`, `t.smtpDesc(nombre)` en es y en; `t.loginTitle` deja de existir.

- [ ] **Step 1: Escribir los tests que fallan**

`frontend/src/i18n/nombreDelSistema.test.js`:

```js
import { describe, it, expect } from 'vitest'
import es from './es.js'
import en from './en.js'

// system_name (frente C, 2026-09-16): los textos que nombran al sistema lo
// reciben; ninguno lleva "Axioma" fijo.
describe('textos con el nombre del sistema', () => {
  it('loginButton, adminBack y smtpDesc interpolan el nombre en es y en; loginTitle ya no existe', () => {
    for (const t of [es, en]) {
      for (const clave of ['loginButton', 'adminBack', 'smtpDesc']) {
        expect(typeof t[clave], clave).toBe('function')
        expect(t[clave]('Hal'), clave).toContain('Hal')
        expect(t[clave]('Hal'), clave).not.toContain('Axioma')
      }
      expect(t.loginTitle).toBeUndefined()
    }
  })
})
```

`LogoAxioma.test.jsx`: sumar `import { useApariencia } from '../store/useApariencia'`; el `beforeEach` pasa a `beforeEach(() => { localStorage.clear(); useApariencia.setState({ systemName: null, langDefault: null }) })`; nuevo test:

```jsx
  it('con un nombre del sistema configurado, el logotipo lo muestra en lugar de la marca', () => {
    useApariencia.setState({ systemName: 'Hal' })
    renderCon()
    expect(screen.getByText('Hal')).toBeInTheDocument()
    expect(screen.queryByText('Axioma')).not.toBeInTheDocument()
    expect(screen.getByText('Infraestructura Cognitiva Personal')).toBeInTheDocument()
  })
```

`Login.test.jsx`: sumar `import es from '../i18n/es.js'` e `import { useApariencia } from '../store/useApariencia'`; en el `beforeEach` existente (línea ~40), `useApariencia.setState({ systemName: null, langDefault: null })`; nuevo test:

```jsx
  it('el título y el botón usan el nombre del sistema', () => {
    useApariencia.setState({ systemName: 'Hal' })
    renderLogin()
    expect(screen.getByRole('heading', { name: 'Hal' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.loginButton('Hal') })).toBeInTheDocument()
  })
```

`frontend/src/components/admin/AdminSidebar.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import AdminSidebar from './AdminSidebar'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import { useApariencia } from '../../store/useApariencia'

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
})

describe('AdminSidebar', () => {
  it('la cabecera y el enlace de vuelta usan el nombre del sistema', () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<I18nProvider><MemoryRouter><AdminSidebar /></MemoryRouter></I18nProvider>)
    expect(screen.getByText(`Hal v${__APP_VERSION__}`)).toBeInTheDocument()
    expect(screen.getByText(es.adminBack('Hal'))).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run src/i18n/nombreDelSistema.test.js src/components/LogoAxioma.test.jsx src/pages/Login.test.jsx src/components/admin/AdminSidebar.test.jsx`
Expected: 4 FAIL: `expected 'string' to be 'function'`; `Unable to find an element with the text: Hal` (logotipo); `es.loginButton is not a function` (Login); `Unable to find an element with the text: Hal v...` (sidebar).

- [ ] **Step 3: Implementar**

`es.js`: borrar `loginTitle: 'Axioma',` y reemplazar:

```js
  loginButton: (nombre) => `Entrar a ${nombre}`,
```
```js
  adminBack: (nombre) => `Volver a ${nombre}`,
```
```js
  smtpDesc: (nombre) => `Servidor con el que ${nombre} envía los enlaces de recuperación de contraseña.`,
```

`en.js`: borrar `loginTitle: 'Axioma',` y reemplazar:

```js
  loginButton: (nombre) => `Enter ${nombre}`,
```
```js
  adminBack: (nombre) => `Back to ${nombre}`,
```
```js
  smtpDesc: (nombre) => `Server ${nombre} uses to send password recovery links.`,
```

`LogoAxioma.jsx`: `import { useNombreDelSistema } from '../store/useApariencia'`. En el cuerpo, `const nombre = useNombreDelSistema(t)`, y `{t.brandName}` pasa a `{nombre}`. Al comentario agregar: `Desde el frente C (2026-09-16) el texto es system_name (Admin → Configuración); antes de conocerlo, la marca de i18n.`

`Login.jsx`: `import { useNombreDelSistema } from '../store/useApariencia'`; después de `const { lang, setLang, t } = useI18n()`, `const nombre = useNombreDelSistema(t)`; `{t.loginTitle}` → `{nombre}`; `{loading ? t.loggingIn : t.loginButton}` → `{loading ? t.loggingIn : t.loginButton(nombre)}`.

`AdminSidebar.jsx`: `import { useNombreDelSistema } from '../../store/useApariencia'`; `const nombre = useNombreDelSistema(t)`. El comentario de las líneas 22-25 pasa a `{/* Versión desde package.json (__APP_VERSION__, vite.config.js); el nombre es system_name (frente C, 2026-09-16). */}`. `Axioma v{__APP_VERSION__}` → `{nombre} v{__APP_VERSION__}`. `{t.adminBack}` → `{t.adminBack(nombre)}`.

`AdminSmtp.jsx`: `import { useNombreDelSistema } from '../../store/useApariencia'`, `const nombre = useNombreDelSistema(t)` junto al `useI18n()`, y `{t.smtpDesc}` → `{t.smtpDesc(nombre)}`.

Run: `grep -rn "t\.loginTitle\|t\.loginButton\b[^(]\|t\.adminBack\b[^(]\|t\.smtpDesc\b[^(]\|>Axioma v" /home/fruiz/worktrees/jax-platform-frente-c/frontend/src --include=*.jsx --include=*.js`
Expected: sin salida.

- [ ] **Step 4: Verlos pasar, más la suite completa**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run`
Expected: 0 fallidos; +4 respecto de la Task 9 (el contraste y la guarda de diálogos siguen verdes).

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/es.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/en.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/nombreDelSistema.test.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/LogoAxioma.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/LogoAxioma.test.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/pages/Login.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/pages/Login.test.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/admin/AdminSidebar.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/components/admin/AdminSidebar.test.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/pages/admin/AdminSmtp.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): system_name en el login, el logotipo y la cabecera de Admin" -m "loginButton, adminBack y smtpDesc reciben el nombre; loginTitle se elimina." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Frontend — Configuración usa los rangos del servidor y no inventa valores

**Files:**
- Modify: `frontend/src/pages/admin/AdminSettings.jsx` (reescritura)
- Modify: `frontend/src/pages/admin/AdminSettings.test.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`

**Interfaces:**
- Consumes: `GET /api/admin/config` → `{config, limites}` (Task 3); 400 `{code: 'config_valor_invalido', clave}`; `sincronizarApariencia()` (Task 8).
- Produces: i18n `config_valor_invalido(campo)`, `adminSettingsTimeoutAyuda`, `adminSettingsMaxPipelinesAyuda(tope)`, `adminSettingsRetentionAyuda`; textos corregidos de `adminSettingsTimeout`, `adminSettingsMaxPipelines` y `adminSettingsRetention`.

- [ ] **Step 1: Escribir los tests que fallan** — `AdminSettings.test.jsx`

Arriba, reemplazar `const CONFIG = ...` por:

```js
const LIMITES = {
  session_timeout_min: { min: 15, max: 10080 },
  max_pipelines: { min: 1, max: 3 },
  web_task_retention_days: { min: 1, max: 365 },
  lang_default: { opciones: ['es', 'en'] },
  system_name: { max_largo: 60 },
}
const CONFIG = { data: { config: [{ key: 'system_name', value: 'Axioma' }], limites: LIMITES } }
```

Bloque nuevo al final:

```js
// Frente C (2026-09-16): los cinco ajustes mandan. La pantalla toma los rangos
// del servidor (antes: sesión 5..1440 cuando rige 10080, pipelines 1..5 cuando
// Jacobs no corre más de 3) y no muestra valores inventados (antes '60', '1',
// '7' cuando faltaba la fila).
describe('AdminSettings -- ajustes que mandan', () => {
  const COMPLETA = { data: { config: [
    { key: 'system_name', value: 'Axioma' }, { key: 'lang_default', value: 'es' },
    { key: 'theme_default', value: 'dark' }, { key: 'session_timeout_min', value: '10080' },
    { key: 'max_pipelines', value: '3' }, { key: 'web_task_retention_days', value: '30' },
  ], limites: LIMITES } }

  it('los campos toman mínimo, máximo y largo del servidor', async () => {
    api.get.mockResolvedValue(COMPLETA)
    renderSettings()
    const sesion = await screen.findByLabelText(es.adminSettingsTimeout)
    expect(sesion).toHaveAttribute('min', '15')
    expect(sesion).toHaveAttribute('max', '10080')
    expect(sesion).toHaveValue(10080)
    expect(screen.getByLabelText(es.adminSettingsMaxPipelines)).toHaveAttribute('max', '3')
    expect(screen.getByLabelText(es.adminSettingsSystemName)).toHaveAttribute('maxlength', '60')
  })

  it('sin fila guardada un campo queda vacío, no con un valor inventado', async () => {
    api.get.mockResolvedValue(CONFIG)
    renderSettings()
    expect(await screen.findByLabelText(es.adminSettingsTimeout)).toHaveValue(null)
    expect(screen.getByLabelText(es.adminSettingsMaxPipelines)).toHaveValue(null)
    expect(screen.getByLabelText(es.adminSettingsRetention)).toHaveValue(null)
  })

  it('un valor fuera de rango se nombra con la etiqueta del campo, en los dos idiomas', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockRejectedValue(rechazo(400, { code: 'config_valor_invalido', clave: 'max_pipelines' }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_valor_invalido(es.adminSettingsMaxPipelines))
    expect(en.config_valor_invalido(en.adminSettingsMaxPipelines)).toContain(en.adminSettingsMaxPipelines)
  })

  it('las ayudas existen en los dos idiomas', () => {
    for (const t of [es, en]) {
      expect(t.adminSettingsTimeoutAyuda).toBeTruthy()
      expect(t.adminSettingsRetentionAyuda).toBeTruthy()
      expect(t.adminSettingsMaxPipelinesAyuda(3)).toContain('3')
    }
  })

  it('un guardado bueno vuelve a pedir la apariencia', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    await guardar()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apariencia'))
  })
})
```

- [ ] **Step 2: Verlos fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run src/pages/admin/AdminSettings.test.jsx`
Expected: 5 FAIL en el bloque nuevo: `Unable to find a label with the text of: Timeout sesión (min)` (etiquetas sin `htmlFor`, y el texto cambia); `es.config_valor_invalido is not a function`; `adminSettingsTimeoutAyuda` undefined; `/apariencia` nunca pedido. Los 15 existentes pasan.

- [ ] **Step 3: Implementar**

`es.js`, en el bloque de `adminSettings*` (reemplazar las tres etiquetas y agregar):

```js
  config_valor_invalido: (campo) => `El valor de "${campo}" está fuera de lo permitido. No se guardó nada.`,
  adminSettingsTimeout: 'Duración de la sesión (min)',
  adminSettingsTimeoutAyuda: 'Desde que se inicia sesión hasta que hay que volver a entrar. Usarla no la alarga.',
  adminSettingsMaxPipelines: 'Pipelines activos a la vez',
  adminSettingsMaxPipelinesAyuda: (tope) => `Por organización. Jacobs no corre más de ${tope} en total.`,
  adminSettingsRetention: 'Retención de tareas web (días)',
  adminSettingsRetentionAyuda: 'Días que quien lanzó una tarea web puede seguir viendo su resultado.',
```

`en.js`, lo mismo:

```js
  config_valor_invalido: (campo) => `The value of "${campo}" is outside the allowed range. Nothing was saved.`,
  adminSettingsTimeout: 'Session length (min)',
  adminSettingsTimeoutAyuda: 'From sign-in until signing in again is required. Using it does not extend it.',
  adminSettingsMaxPipelines: 'Active pipelines at once',
  adminSettingsMaxPipelinesAyuda: (tope) => `Per organization. Jacobs runs no more than ${tope} in total.`,
  adminSettingsRetention: 'Web task retention (days)',
  adminSettingsRetentionAyuda: 'Days the person who launched a web task can still see its result.',
```

`frontend/src/pages/admin/AdminSettings.jsx`, completo:

```jsx
import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import { useTema } from '../../store/useTema'
import { sincronizarApariencia } from '../../apariencia/sincronizarApariencia'

// Códigos con los que PUT /admin/config rechaza (backend/api/admin/config_admin.py).
// Cada uno tiene su texto; cualquier otro cae en el genérico, nunca en silencio
// (2026-09-14: el catch estaba vacío y quien guardaba no veía por qué no se guardó).
const CODIGOS_CONOCIDOS = new Set(['config_clave_reservada', 'config_collation_desconocida'])

// Etiqueta de cada ajuste que el backend valida (frente C, 2026-09-16): el
// error config_valor_invalido nombra el campo, no la clave técnica.
const ETIQUETAS = {
  session_timeout_min: 'adminSettingsTimeout',
  max_pipelines: 'adminSettingsMaxPipelines',
  web_task_retention_days: 'adminSettingsRetention',
  lang_default: 'adminSettingsLang',
  system_name: 'adminSettingsSystemName',
}

const CLASE_ETIQUETA = 'block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1'
const CLASE_CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'
const CLASE_AYUDA = 'text-xs text-texto-tenue mt-1'

function errorDeGuardado(err) {
  const codigo = codigoDe(err)
  if (codigo === 'config_valor_invalido') return { codigo, clave: err.response.data.detail.clave }
  return { codigo: CODIGOS_CONOCIDOS.has(codigo) ? codigo : 'adminSettingsSaveError' }
}

function textoDeError(t, error) {
  if (error.codigo === 'config_valor_invalido') {
    return t.config_valor_invalido(t[ETIQUETAS[error.clave]] ?? error.clave)
  }
  return t[error.codigo] ?? t.adminSettingsSaveError
}

function CampoNumero({ id, etiqueta, ayuda, valor, limite, onChange }) {
  return (
    <div>
      <label htmlFor={id} className={CLASE_ETIQUETA}>{etiqueta}</label>
      <input id={id} type="number" min={limite?.min} max={limite?.max} value={valor ?? ''}
        onChange={e => onChange(e.target.value)} className={CLASE_CAMPO} />
      {ayuda && <p className={CLASE_AYUDA}>{ayuda}</p>}
    </div>
  )
}

export default function AdminSettings() {
  const { t } = useI18n()
  const [config, setConfig] = useState({})
  // Rangos del SERVIDOR (ajustes.py): la pantalla no los copia.
  const [limites, setLimites] = useState({})
  // Sin una carga buena no se guarda: "Guardar" enviaría una lista vacía que el backend acepta.
  const [cargado, setCargado] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  // Error vigente como { codigo, clave? }: se traduce al renderizar.
  const [error, setError] = useState(null)

  useEffect(() => {
    api.get('/admin/config').then(r => {
      const map = {}
      r.data.config.forEach(({ key, value }) => { map[key] = value })
      setConfig(map)
      setLimites(r.data.limites ?? {})
      setCargado(true)
    }).catch(() => setError({ codigo: 'adminSettingsLoadError' }))
  }, [])

  function set(key, value) {
    setConfig(c => ({ ...c, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const items = Object.entries(config).map(([key, value]) => ({ key, value: String(value) }))
      await api.put('/admin/config', items)
      // El nuevo predeterminado se ve en este navegador sin recargar (spec
      // §5.2.4). Decisión de Fernando (2026-09-14): acá también fija la
      // elección del propio admin, aunque tuviera otra.
      useTema.getState().fijarPredeterminadoComoEleccion(config.theme_default)
      // Nombre e idioma nuevos sin recargar (frente C). Si la lectura falla,
      // quedan los últimos conocidos: el guardado ya se confirmó.
      sincronizarApariencia().catch(() => {})
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      // Un "Guardado" de un intento anterior no puede convivir con este error.
      setSaved(false)
      setError(errorDeGuardado(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.adminSettingsTitle}</h1>

      <div className="max-w-lg space-y-4">
        <div>
          <label htmlFor="ajuste-system-name" className={CLASE_ETIQUETA}>{t.adminSettingsSystemName}</label>
          <input
            id="ajuste-system-name"
            value={config.system_name ?? ''}
            maxLength={limites.system_name?.max_largo}
            onChange={e => set('system_name', e.target.value)}
            className={CLASE_CAMPO}
          />
        </div>
        <div>
          <label htmlFor="ajuste-lang-default" className={CLASE_ETIQUETA}>{t.adminSettingsLang}</label>
          <select id="ajuste-lang-default" value={config.lang_default ?? ''} onChange={e => set('lang_default', e.target.value)} className={CLASE_CAMPO}>
            <option value="" disabled hidden />
            <option value="es">Español</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label htmlFor="ajuste-theme-default" className={CLASE_ETIQUETA}>{t.adminSettingsTheme}</label>
          <select id="ajuste-theme-default" value={config.theme_default || 'dark'} onChange={e => set('theme_default', e.target.value)} className={CLASE_CAMPO}>
            <option value="dark">{t.adminSettingsDark}</option>
            <option value="light">{t.adminSettingsLight}</option>
          </select>
        </div>
        <CampoNumero id="ajuste-session-timeout" etiqueta={t.adminSettingsTimeout} ayuda={t.adminSettingsTimeoutAyuda}
          valor={config.session_timeout_min} limite={limites.session_timeout_min}
          onChange={v => set('session_timeout_min', v)} />
        <CampoNumero id="ajuste-max-pipelines" etiqueta={t.adminSettingsMaxPipelines}
          ayuda={limites.max_pipelines ? t.adminSettingsMaxPipelinesAyuda(limites.max_pipelines.max) : null}
          valor={config.max_pipelines} limite={limites.max_pipelines}
          onChange={v => set('max_pipelines', v)} />
        <CampoNumero id="ajuste-retencion" etiqueta={t.adminSettingsRetention} ayuda={t.adminSettingsRetentionAyuda}
          valor={config.web_task_retention_days} limite={limites.web_task_retention_days}
          onChange={v => set('web_task_retention_days', v)} />

        {error && (
          <AlertaError className="text-sm">{textoDeError(t, error)}</AlertaError>
        )}

        <div className="pt-2">
          <button
            onClick={handleSave}
            disabled={saving || !cargado}
            className="px-5 py-2 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors"
          >
            {saved ? `✓ ${t.adminSettingsSaved}` : saving ? t.attachUploading : t.adminSettingsSave}
          </button>
        </div>
      </div>
    </div>
  )
}
```

(`theme_default || 'dark'` se conserva: no es de la sección C y su respaldo 'dark' es la regla del spec 2026-09-14. "Español"/"English" son endónimos y se conservan como estaban.)

- [ ] **Step 4: Verlos pasar, más la suite completa y el build**

Run: `cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run && npm run build`
Expected: 0 fallidos; +5 respecto de la Task 10; build sin errores.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/pages/admin/AdminSettings.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/pages/admin/AdminSettings.test.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/es.js
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/i18n/en.js
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "feat(ajustes): Configuración usa los rangos del servidor y no inventa valores" -m "Sesión hasta 10080, pipelines hasta el tope de Jacobs, ayudas que dicen el efecto real, config_valor_invalido con la etiqueta del campo y resincronización de la apariencia al guardar." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Pisos de CI, suite completa, revisión y PR

**Files:**
- Modify: `.github/workflows/policy.yml` (`numPassedTests` ~l.507, `PISO_PASSED` ~l.1325, `JAX_CI_MIN_PASSED` ~l.1799)

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: PR `feat/ajustes-que-mandan` → `master` con CI verde por `headSha`.

- [ ] **Step 1: Rebase sobre master y lectura de los pisos vigentes**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c fetch origin
git -C /home/fruiz/worktrees/jax-platform-frente-c rebase origin/master
grep -n "numPassedTests !== \|PISO_PASSED = \|JAX_CI_MIN_PASSED:" /home/fruiz/worktrees/jax-platform-frente-c/.github/workflows/policy.yml
```
Expected sobre `26c9cd5`: `448`, `1175`, `"614"`. Si otro frente mergeó antes, se usan los valores que muestra el grep. Un conflicto en un archivo de la Discrepancia 8 se resuelve conservando **las dos** intenciones y re-corriendo los tests de las dos ramas.

- [ ] **Step 2: Suite completa, dos veces por modo**

```bash
cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs --junitxml=/tmp/claude-1000/junit-con-db.xml
cd /home/fruiz/worktrees/jax-platform-frente-c/backend && JAX_CI_NO_DB=1 /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs --junitxml=/tmp/claude-1000/junit-sin-db.xml
cd /home/fruiz/worktrees/jax-platform-frente-c/frontend && npx vitest run --reporter=json --outputFile=/tmp/claude-1000/vitest.json
node -e 'const r=require("/tmp/claude-1000/vitest.json");console.log(r.numPassedTests, r.numFailedTests)'
```
(`/tmp/claude-1000/` = la raíz del scratchpad de la sesión que ejecuta; si su scratchpad es otro directorio, se usa ese.) Expected, con base `26c9cd5`:
- con DB: **+42** passed (1175 → 1217), 0 failed, skips sin cambios;
- sin DB: **+19** passed (614 → 633), 0 failed; los skips suben 23;
- vitest: **+25** (448 → 473), 0 fallidos.

Desglose para auditar: backend puros 12 + 1 + 3 + 1 + 2 = 19 y con `client` 4 + 3 + 5 + 6 + 2 + 3 = 23; vitest 4 + 5 + 7 + 4 + 5 = 25 (los 4 mudados de `useTema.test.js` no suman). Si una corrida difiere del número esperado, se investiga antes de escribir el piso. No se pone un número que no se midió dos veces.

- [ ] **Step 3: Escribir los pisos con comentario fechado**

En `policy.yml`, en los tres lugares, reemplazar el número y agregar al comentario inmediatamente anterior:

```
# <viejo> -> <nuevo> el 2026-09-16 (frente C, ajustes que mandan): ajustes.py
# (validación, caché con invalidación, proceso único, 503), migración única,
# PUT validado por collation, vida del refresh por iat, cupo de pipelines y
# retención por ajuste, /api/apariencia con idioma y nombre. Vistos en rojo
# contra 26c9cd5. Medido dos veces: <nuevo>.
```
(en vitest el comentario va con `//` dentro del `node -e`, siguiendo el estilo de las líneas vecinas).

- [ ] **Step 4: Revisión de código antes del PR**

Usar superpowers:requesting-code-review sobre `git -C /home/fruiz/worktrees/jax-platform-frente-c diff origin/master...HEAD`. Cada hallazgo se arregla en la rama antes del PR, con su test rojo → verde (regla de Fernando: sin hallazgos diferidos).

- [ ] **Step 5: Commit, push y PR**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c add .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "ci: pisos del frente C (ajustes que mandan)" -m "vitest, con DB y sin DB, medidos dos veces." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git -C /home/fruiz/worktrees/jax-platform-frente-c push -u origin feat/ajustes-que-mandan
gh pr create --repo fjruizhn/jax-platform --base master --head feat/ajustes-que-mandan \
  --title "Frente C · Ajustes que mandan" --body-file <scratchpad>/pr-frente-c.md
```
El cuerpo (escrito con Write en el scratchpad) lleva: la sección C del spec; las 8 discrepancias con su resolución; el aviso de que **cada sesión abierta vuelve a entrar una vez al desplegar**; el orden de merge con el PR de jax (la plataforma primero); los pisos; un lugar para los números de carga de la Task 15 (el cuerpo se edita con `gh pr edit --body-file` cuando estén); y el pie `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

### Task 13: Canario — CI se pone rojo si se rompe un ajuste

**Files:** ninguno en la rama del PR (rama descartable).

- [ ] **Step 1: Romper backend y frontend en una rama descartable**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c switch -c canario/ajustes-que-mandan
sed -i 's/MAX_PIPELINES: Definicion(_entero(1, MAX_PARALLEL_PIPELINES), {"min": 1, "max": MAX_PARALLEL_PIPELINES}),/MAX_PIPELINES: Definicion(_entero(1, MAX_PARALLEL_PIPELINES + 2), {"min": 1, "max": MAX_PARALLEL_PIPELINES + 2}),/' /home/fruiz/worktrees/jax-platform-frente-c/backend/ajustes.py
sed -i 's/ max={limite?.max} / /' /home/fruiz/worktrees/jax-platform-frente-c/frontend/src/pages/admin/AdminSettings.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c diff --stat
git -C /home/fruiz/worktrees/jax-platform-frente-c add backend/ajustes.py
git -C /home/fruiz/worktrees/jax-platform-frente-c add frontend/src/pages/admin/AdminSettings.jsx
git -C /home/fruiz/worktrees/jax-platform-frente-c commit -m "canario: tope de pipelines por encima de Jacobs y sin max en la pantalla (NO MERGEAR)" -m "Rama descartable: backend-tests-no-db, backend-tests-con-db y frontend-tests tienen que ponerse rojos." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git -C /home/fruiz/worktrees/jax-platform-frente-c push -u origin canario/ajustes-que-mandan
SHA=$(git -C /home/fruiz/worktrees/jax-platform-frente-c rev-parse HEAD)
```
Expected: `diff --stat` muestra 2 archivos con 1 línea cambiada cada uno. Si alguno muestra 0, el `sed` no matcheó: se corrige a mano el mismo cambio antes de commitear.

- [ ] **Step 2: Rojo por API sobre ese sha**

```bash
gh run list --repo fjruizhn/jax-platform --branch canario/ajustes-que-mandan --limit 1 --json databaseId,headSha,status,conclusion
gh api "repos/fjruizhn/jax-platform/commits/$SHA/check-runs" --jq '.check_runs[] | [.name, .conclusion] | @tsv'
```
Esperar a que termine. Expected: `backend-tests-no-db` **failure** (`test_limites_publicos_y_tope_espejado_de_jacobs`, `test_entero_acepta_los_bordes_y_rechaza_fuera_de_rango`), `backend-tests-con-db` **failure** (los mismos y `test_un_lote_con_un_valor_fuera_de_rango_no_escribe_nada`), `frontend-tests` **failure** (`los campos toman mínimo, máximo y largo del servidor`). Confirmar con `gh run view <id> --log-failed | grep -E "FAILED|✗|×"`. Si un job queda verde, el test no está cubierto por ese job: se arregla antes de seguir.

- [ ] **Step 3: Descartar el canario**

```bash
git -C /home/fruiz/worktrees/jax-platform-frente-c switch feat/ajustes-que-mandan
git -C /home/fruiz/worktrees/jax-platform-frente-c branch -D canario/ajustes-que-mandan
git -C /home/fruiz/worktrees/jax-platform-frente-c push origin --delete canario/ajustes-que-mandan
grep -c "MAX_PARALLEL_PIPELINES + 2" /home/fruiz/worktrees/jax-platform-frente-c/backend/ajustes.py
```
Expected: `0`. Anotar en el ledger el sha del canario, los tres jobs en rojo y los nombres de los tests.

- [ ] **Step 4: Gate por headSha del PR**

```bash
gh pr checks <N> --repo fjruizhn/jax-platform
gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid
git -C /home/fruiz/worktrees/jax-platform-frente-c ls-remote origin refs/heads/feat/ajustes-que-mandan
```
Expected: todos `SUCCESS` y `headRefOid` == sha remoto. Si no, no se sigue.

---

### Task 14: jax — espejo del tope de pipelines y guiones de carga (rama, sin PR todavía)

**Files (repo `jax`):**
- Modify: `scripts/check_mirror_sync.py` (familia nueva al final de `FAMILIAS`)
- Modify: `jacobs/policy.py:17` (solo un comentario encima)
- Create: `loadtest/ajustes-sesion.js`, `loadtest/ajustes-pipelines.js`

**Interfaces:**
- Consumes: `jax-platform/backend/ajustes.py` con `MAX_PARALLEL_PIPELINES  = 3` (Task 1).
- Produces: familia `tope_pipelines`; guiones k6 que usa la Task 15.

- [ ] **Step 1: Worktree de jax**

```bash
git -C /home/fruiz/jax fetch origin
git -C /home/fruiz/jax worktree add -b feat/espejo-tope-pipelines /home/fruiz/worktrees/jax-frente-c origin/master
```

- [ ] **Step 2: Ver que la familia todavía no existe (rojo)**

Run: `cd /home/fruiz/worktrees/jax-frente-c && JAX_PLATFORM_REPO_ROOT=/home/fruiz/worktrees/jax-platform-frente-c python3 scripts/check_mirror_sync.py; echo "exit=$?"`
Expected: `exit=0` y **ninguna** línea `[tope_pipelines]`: nadie vigila el tope. Eso es el rojo de esta tarea.

- [ ] **Step 3: Agregar la familia**

En `scripts/check_mirror_sync.py`, como último elemento de `FAMILIAS` (después de la familia `cola_uso`, antes del `)` que cierra la tupla):

```python
    Familia(
        nombre="tope_pipelines",
        canonico=JAX_ROOT / "jacobs" / "policy.py",
        espejos=(
            ("jax-platform", JAX_PLATFORM_ROOT / "backend" / "ajustes.py"),
        ),
        compartidos=("MAX_PARALLEL_PIPELINES",),
        nota="Frente C (2026-09-16): el ajuste max_pipelines de la plataforma es "
             "una cuota POR TENANT acotada por el candado GLOBAL de Jacobs (cuenta "
             "todos los pending/running). Si este tope cambia en Jacobs y no en la "
             "plataforma, Admin ofrece un valor que Jacobs rechaza con 422. Canonico: "
             "jacobs/policy.py. ORDEN DE MERGE: la plataforma primero (este job "
             "clona jax-platform master).",
    ),
```

En `jacobs/policy.py`, inmediatamente encima de `MAX_PARALLEL_PIPELINES  = 3`:

```python
# Espejado en jax-platform backend/ajustes.py (familia `tope_pipelines` de
# scripts/check_mirror_sync.py, frente C 2026-09-16): es el máximo del ajuste
# max_pipelines de Admin. Cambiarlo acá exige cambiar la copia en el mismo paso.
```

- [ ] **Step 4: Verde, y rojo rompiendo la copia**

```bash
cd /home/fruiz/worktrees/jax-frente-c && JAX_PLATFORM_REPO_ROOT=/home/fruiz/worktrees/jax-platform-frente-c python3 scripts/check_mirror_sync.py; echo "exit=$?"
cp -r /home/fruiz/worktrees/jax-platform-frente-c/backend <scratchpad>/espejo-roto-backend
sed -i 's/^MAX_PARALLEL_PIPELINES  = 3$/MAX_PARALLEL_PIPELINES  = 4/' <scratchpad>/espejo-roto-backend/ajustes.py
mkdir -p <scratchpad>/espejo-roto && ln -sfn <scratchpad>/espejo-roto-backend <scratchpad>/espejo-roto/backend
cd /home/fruiz/worktrees/jax-frente-c && JAX_PLATFORM_REPO_ROOT=<scratchpad>/espejo-roto python3 scripts/check_mirror_sync.py; echo "exit=$?"
```
Expected: primera corrida `[tope_pipelines]` → `sincronizado (0 divergencia(s) declarada(s))` y `exit=0` **solo si** las demás familias también están sincronizadas contra el worktree; si otra familia da DRIFT porque el worktree no tiene un cambio de master, se repite con `JAX_PLATFORM_REPO_ROOT=/home/fruiz/jax-platform` después del merge. Segunda corrida: `DRIFT: 'MAX_PARALLEL_PIPELINES (jax-platform)' difiere del canonico SIN declararlo` y `exit=1` (las otras familias pueden reportar "falta archivo": lo que importa es la línea de `tope_pipelines`; si sale `exit=2` por archivos faltantes, se copia el árbol `backend/` completo, como arriba). Se corre también `python -m pytest scripts/_check_mirror_sync_test.py -q` → `14 passed`.

- [ ] **Step 5: Guiones k6** — `loadtest/ajustes-sesion.js`

```js
// Carga del frente C (2026-09-16, jax-platform "ajustes que mandan") -- política 4.
//
// Mide los dos caminos de sesión que ahora LEEN un ajuste por request:
//   login   -> lee session_timeout_min antes del bcrypt y emite la cookie con esa vida
//   refresh -> lee session_timeout_min y mide la vida contra `iat`
// Contra una instancia AISLADA (base jax_memory_test, sin tareas de fondo), nunca
// contra producción: el login sube token_version (sesión única) y mataría sesiones reales.
//
// USO:
//   k6 run -e BASE=http://127.0.0.1:18080 -e ESCENARIO=login   -e EMAIL=... -e PASSWORD=... loadtest/ajustes-sesion.js
//   k6 run -e BASE=http://127.0.0.1:18080 -e ESCENARIO=refresh -e REFRESH=... loadtest/ajustes-sesion.js
// Credenciales y token por entorno; NUNCA se escriben en el archivo.

import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://127.0.0.1:18080';
const ESCENARIO = __ENV.ESCENARIO;
const VUS = parseInt(__ENV.VUS || (ESCENARIO === 'login' ? '10' : '25'), 10);

if (ESCENARIO !== 'login' && ESCENARIO !== 'refresh') {
  throw new Error('ESCENARIO tiene que ser login o refresh');
}

export const options = {
  stages: [
    { duration: '5s', target: VUS },
    { duration: '20s', target: VUS },
    { duration: '5s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  if (ESCENARIO === 'login') {
    const r = http.post(`${BASE}/api/auth/login`,
      JSON.stringify({ email: __ENV.EMAIL, password: __ENV.PASSWORD }),
      { headers: { 'Content-Type': 'application/json' }, tags: { escenario: 'login' } });
    check(r, { 'login 200': (res) => res.status === 200 });
  } else {
    const r = http.post(`${BASE}/api/auth/refresh`, null,
      { headers: { Cookie: `refresh_token=${__ENV.REFRESH}` }, tags: { escenario: 'refresh' } });
    check(r, { 'refresh 200': (res) => res.status === 200 });
  }
}
```

`loadtest/ajustes-pipelines.js`:

```js
// Carga del frente C (2026-09-16) -- política 4. POST /api/pipelines lee
// max_pipelines por request ANTES de decidir el cupo. Contra una instancia
// AISLADA con un Jacobs falso (responde al instante): el Jacobs real llama a
// un LLM para planear (20-40 s, GPU) y no se pone bajo carga.
// Con el cupo lleno, la mayoría de las respuestas son 429: ese es el camino
// que lee el ajuste y decide, y es el que se mide. Los 200 son las admisiones.
//
// USO: k6 run -e BASE=http://127.0.0.1:18080 -e TOKEN=... loadtest/ajustes-pipelines.js

import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://127.0.0.1:18080';
const VUS = parseInt(__ENV.VUS || '25', 10);

export const options = {
  stages: [
    { duration: '5s', target: VUS },
    { duration: '20s', target: VUS },
    { duration: '5s', target: 0 },
  ],
  thresholds: {
    checks: ['rate>0.99'],
  },
};

export default function () {
  const r = http.post(`${BASE}/api/pipelines`,
    JSON.stringify({ name: 'carga', objective: 'carga', mode: 'dry_run' }),
    {
      headers: { Authorization: `Bearer ${__ENV.TOKEN}`, 'Content-Type': 'application/json' },
      responseCallback: http.expectedStatuses(200, 429),
      tags: { escenario: 'pipelines' },
    });
  check(r, { '200 o 429': (res) => res.status === 200 || res.status === 429 });
}
```

- [ ] **Step 6: Commit (sin push hasta la Task 18)**

```bash
git -C /home/fruiz/worktrees/jax-frente-c add scripts/check_mirror_sync.py
git -C /home/fruiz/worktrees/jax-frente-c add jacobs/policy.py
git -C /home/fruiz/worktrees/jax-frente-c add loadtest/ajustes-sesion.js
git -C /home/fruiz/worktrees/jax-frente-c add loadtest/ajustes-pipelines.js
git -C /home/fruiz/worktrees/jax-frente-c commit -m "feat(mirror-sync): familia tope_pipelines y carga de los ajustes que mandan" -m "El máximo de max_pipelines en jax-platform es el candado global de Jacobs; el checker lo vigila. Guiones k6 de login, refresh y creación de pipeline." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Prueba de carga antes del merge — base `26c9cd5` contra la rama

**Files:** ninguno del repo. El arnés vive en el scratchpad. Los resultados van al ledger `jax-platform-frente-c/.superpowers/sdd/2026-09-16-frente-c/carga.md`, al cuerpo del PR y a la Task 18.

- [ ] **Step 1: Arnés en el scratchpad** (`SCR=<scratchpad de la sesión>`)

`$SCR/carga_app.py`:

```python
"""Instancia AISLADA para la carga del frente C. Rechaza arrancar si no apunta
a jax_memory_test. Sin tareas de fondo: nada de canario de facetas (llama a
proveedores reales), reaper de ~/jax/missions, poller ni drenaje del respaldo."""
import os
from contextlib import asynccontextmanager

if os.environ.get("JAX_DB_NAME") != "jax_memory_test":
    raise SystemExit("carga_app: JAX_DB_NAME tiene que ser jax_memory_test")
for variable in ("JAX_FACET_SEAL_PATH", "JAX_USAGE_SPOOL_DIR", "JACOBS_URL"):
    if not os.environ.get(variable):
        raise SystemExit(f"carga_app: falta {variable} (aislado del real)")

import main  # noqa: E402
from db.connection import close_pool, get_pool  # noqa: E402
from db.migrations import run_migrations  # noqa: E402
from http_client import close_http_client, get_http_client  # noqa: E402


@asynccontextmanager
async def _lifespan_de_carga(app):
    await get_pool()
    await get_http_client()
    await run_migrations()
    yield
    await close_http_client()
    await close_pool()


main.app.router.lifespan_context = _lifespan_de_carga
app = main.app
```

`$SCR/jacobs_falso.py`:

```python
import uuid

from fastapi import FastAPI

app = FastAPI()


@app.post("/jacobs/pipeline")
async def crear():
    return {"pipeline_id": str(uuid.uuid4()), "status": "running"}
```

`$SCR/entorno_carga.sh`:

```bash
set -a; . /etc/jax/.env; set +a
export JAX_DB_NAME=jax_memory_test
export JAX_FACET_SEAL_PATH="$SCR/sello-carga"
export JAX_USAGE_SPOOL_DIR="$SCR/spool-carga"; mkdir -p "$JAX_USAGE_SPOOL_DIR"
export JACOBS_URL=http://127.0.0.1:18777/jacobs
export JAX_LOGIN_RATE_IP=100000000/60
export JAX_LOGIN_RATE_EMAIL=100000000/60
export PYTHONPATH="$SCR"
```

- [ ] **Step 2: Checkout de la base y Jacobs falso**

```bash
git -C /home/fruiz/jax-platform worktree add --detach $SCR/base-26c9cd5 26c9cd5
```
En segundo plano (run_in_background): `cd $SCR && /home/fruiz/jax-platform/backend/.venv/bin/uvicorn jacobs_falso:app --host 127.0.0.1 --port 18777 --log-level warning`

- [ ] **Step 3: Usuarios de carga en `jax_memory_test`**

```bash
. $SCR/entorno_carga.sh
cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python - <<'PY'
import asyncio, json, os
assert os.environ["JAX_DB_NAME"] == "jax_memory_test"
from tests.identidades import crear_usuario
async def main():
    login = await crear_usuario(password="Carga-login-frente-c-2026")
    refresh = await crear_usuario(password="Carga-refresh-frente-c-2026")
    print(json.dumps({"login": login, "refresh": refresh}))
asyncio.run(main())
PY
```
Anotar los `(user_id, email)` en el ledger (son de la base de tests y se borran en el Step 7).

- [ ] **Step 4: Medir la BASE (`26c9cd5`)** — instancia en segundo plano (run_in_background):

```bash
. $SCR/entorno_carga.sh && /home/fruiz/jax-platform/backend/.venv/bin/uvicorn carga_app:app --app-dir $SCR/base-26c9cd5/backend --host 127.0.0.1 --port 18080 --no-access-log --log-level warning
```
Luego, en primer plano:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/health
k6 run -e BASE=http://127.0.0.1:18080 -e ESCENARIO=login -e EMAIL=<email login> -e PASSWORD=Carga-login-frente-c-2026 /home/fruiz/worktrees/jax-frente-c/loadtest/ajustes-sesion.js
RESP=$(curl -s -i -X POST http://127.0.0.1:18080/api/auth/login -H 'Content-Type: application/json' -d '{"email":"<email refresh>","password":"Carga-refresh-frente-c-2026"}')
REFRESH=$(printf '%s' "$RESP" | grep -i '^set-cookie: refresh_token=' | sed -E 's/^[Ss]et-[Cc]ookie: refresh_token=([^;]+);.*/\1/')
TOKEN=$(printf '%s' "$RESP" | tail -1 | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
k6 run -e BASE=http://127.0.0.1:18080 -e ESCENARIO=refresh -e REFRESH="$REFRESH" /home/fruiz/worktrees/jax-frente-c/loadtest/ajustes-sesion.js
k6 run -e BASE=http://127.0.0.1:18080 -e TOKEN="$TOKEN" /home/fruiz/worktrees/jax-frente-c/loadtest/ajustes-pipelines.js
```
Anotar por escenario: VUs, peticiones, rps, p50, p95, p99, `http_req_failed` y reparto de estados (pipelines: cuántos 200 y cuántos 429). Detener la instancia de la base (TaskStop del proceso en segundo plano).

- [ ] **Step 5: Medir la RAMA** — la misma instancia con `--app-dir /home/fruiz/worktrees/jax-platform-frente-c/backend`. Antes de los k6:

```bash
set -a; . $SCR/entorno_carga.sh; set +a
cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python - <<'PY'
import asyncio, os
assert os.environ["JAX_DB_NAME"] == "jax_memory_test"
from tests.identidades import sql
async def main():
    print(await sql("SELECT config_key, config_value FROM axioma_config WHERE config_key IN "
                    "('session_timeout_min','max_pipelines','web_task_retention_days','lang_default','system_name')", (), True))
asyncio.run(main())
PY
```
Expected: las cinco filas válidas (la instancia corrió la migración). Después, los mismos cuatro comandos del Step 4 contra la rama (el login previo al refresh se repite en esta instancia) y los mismos números.

- [ ] **Step 6: Criterio de GO**

- 0 errores fuera de lo esperado (pipelines: solo 200/429; login y refresh: solo 200).
- **refresh:** p95 de la rama ≤ p95 de la base + 5 ms. Es el camino que agregó trabajo, y con el caché caliente es un dict en memoria.
- **pipelines:** p95 de la rama ≤ p95 de la base + 5 ms.
- **login:** dominado por bcrypt (~150 ms). Rama dentro de ±10 % de la base.
- Se corre una tercera medición de refresh con `JAX_AJUSTES_TTL_S=0.001` exportado **solo** en la instancia de la rama (caché que siempre vence: el peor caso, una consulta por request) y se anota su p95. No es gate: es el costo sin caché, que queda registrado para justificar el caché (política 2: sin medición no hay caché).

Si un criterio falla: **no hay GO**. Se diagnostica (superpowers:systematic-debugging) antes de seguir. Los números se escriben en el ledger y en el cuerpo del PR (`gh pr edit <N> --repo fjruizhn/jax-platform --body-file <scratchpad>/pr-frente-c.md`).

- [ ] **Step 7: Limpiar**

```bash
. $SCR/entorno_carga.sh
cd /home/fruiz/worktrees/jax-platform-frente-c/backend && /home/fruiz/jax-platform/backend/.venv/bin/python - <<'PY'
import asyncio, os
assert os.environ["JAX_DB_NAME"] == "jax_memory_test"
from tests.identidades import borrar_usuario
async def main():
    for user_id in (<id login>, <id refresh>):
        await borrar_usuario(user_id)
asyncio.run(main())
PY
git -C /home/fruiz/jax-platform worktree remove $SCR/base-26c9cd5
```
Detener la instancia de la rama y el Jacobs falso (TaskStop). `git -C /home/fruiz/jax-platform worktree list` ya no muestra `base-26c9cd5`.

---

### Task 16: Despliegue

**Files:** ninguno. Toca PRODUCCIÓN (declarado).

- [ ] **Step 1: GATE con Fernando + respaldo de `axioma_config` con restauración probada fila por fila**

Antes de cualquier comando, avisar a Fernando: (a) **cada sesión abierta vuelve a iniciar sesión una vez** (Discrepancia 1); (b) el valor de `system_name` que queda. Esperar su GO.

```bash
set -a; . /etc/jax/.env; set +a
F=/home/fruiz/backups/axioma_config-pre-frente-c-$(date +%Y%m%d-%H%M%S).sql
mkdir -p /home/fruiz/backups
mariadb-dump -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory axioma_config > "$F"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -e "CREATE DATABASE jax_restore_check"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_restore_check < "$F"
Q="SELECT config_key, MD5(config_value), updated_at FROM %s.axioma_config ORDER BY config_key"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -N -e "$(printf "$Q" jax_memory)" > <scratchpad>/config-prod.tsv
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -N -e "$(printf "$Q" jax_restore_check)" > <scratchpad>/config-restaurada.tsv
diff <scratchpad>/config-prod.tsv <scratchpad>/config-restaurada.tsv && echo "RESTAURACION IDENTICA FILA POR FILA ($(wc -l < <scratchpad>/config-prod.tsv) filas)"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -e "DROP DATABASE jax_restore_check"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "SELECT config_key, config_value FROM axioma_config WHERE config_key IN ('session_timeout_min','max_pipelines','web_task_retention_days','lang_default','system_name') ORDER BY config_key"
```
Expected: `RESTAURACION IDENTICA FILA POR FILA`. La última consulta muestra el ANTES (probablemente 60 / 1 / 7), que se copia al ledger. **GATE:** si `system_name` no es exactamente `Axioma`, PARAR y preguntarle a Fernando qué nombre rige (Discrepancia 4). No se muestran valores `smtp.*`: la consulta los excluye. Si `JAX_DB_USER` no puede crear bases, PARAR y preguntarle a Fernando cómo prefiere probar la restauración.

- [ ] **Step 2: 0 pipelines en vuelo**

```bash
set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -N -e "SELECT COUNT(*) FROM jacobs_pipelines WHERE status IN ('pending','running')"
```
Expected: `0`. Si no, se espera y se repite. No se reinicia con pipelines en vuelo.

- [ ] **Step 3: Merge y reinicio del backend**

```bash
gh pr merge <N> --repo fjruizhn/jax-platform --merge
git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only
git -C /home/fruiz/jax-platform log --oneline -1
sudo -n /usr/bin/systemctl restart jax-platform.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health
sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40
readlink /proc/$(systemctl show -p MainPID --value jax-platform.service)/cwd
```
Expected: `200`; journal sin tracebacks ni `ajuste ... ilegible`; cwd `/home/fruiz/jax-platform/backend`.

- [ ] **Step 4: La migración quedó, una vez, y el endpoint público responde**

```bash
set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "SELECT config_key, config_value FROM axioma_config WHERE config_key IN ('session_timeout_min','max_pipelines','web_task_retention_days','lang_default','system_name') ORDER BY config_key; SELECT * FROM axioma_migracion_de_datos; EXPLAIN SELECT config_key, config_value FROM axioma_config WHERE config_key IN ('session_timeout_min','max_pipelines','web_task_retention_days','lang_default','system_name')"
curl -s http://127.0.0.1:8080/api/apariencia
```
Expected: `10080`, `3`, `30`, `es` y el `system_name` aprobado; una fila `ajustes_que_mandan_v1`; EXPLAIN con `key=PRIMARY`; `/api/apariencia` → `{"theme_default":"light","lang_default":"es","system_name":"Axioma"}` (`light` es el valor que dejó Fernando el 2026-09-14; si difiere, se anota, no se toca).

- [ ] **Step 5: Despliegue del frontend**

```bash
cd /home/fruiz/jax-platform/frontend && npm run build
ls /home/fruiz/jax-platform/frontend/dist/assets/index-*.js
set -a; . /etc/jax/.env; set +a
B=/www/wwwroot/axioma-ia.io.backup-pre-frente-c-$(date +%Y%m%d-%H%M%S)
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo cp -a /www/wwwroot/axioma-ia.io $B && sudo diff -rq /www/wwwroot/axioma-ia.io $B && echo RESPALDO IDENTICO"
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```
Expected: `RESPALDO IDENTICO`; el `index-*.js` servido es el recién construido. Anotar nombre del bundle y ruta del respaldo.

---

### Task 17: Verificación en vivo — cambiar cada ajuste, ver el efecto y restaurar

**Files:** ninguno. Toca PRODUCCIÓN (declarado). **GATE:** avisar a Fernando antes: crea y da de baja un usuario de prueba y cambia ajustes durante unos minutos.

- [ ] **Step 1: Token de admin y foto del estado**

```bash
cd /home/fruiz/jax-platform/backend && set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -N -e "SELECT user_id, tenant_id, role, token_version FROM jax_users WHERE user_id = 1"
ADMIN=$(.venv/bin/python -c "import sys; from auth.jwt import create_access_token; print(create_access_token('1', sys.argv[1], 'superadmin', int(sys.argv[2])))" <tenant_id> <token_version>)
curl -s -H "Authorization: Bearer $ADMIN" http://127.0.0.1:8080/api/admin/config > <scratchpad>/config-antes-vivo.json
python3 -c "import json; d=json.load(open('<scratchpad>/config-antes-vivo.json')); print({c['key']: c['value'] for c in d['config']}); print(d['limites'])"
```
Expected: `role=superadmin`; el GET trae las claves y `limites` con `max_pipelines.max = 3`. (Firmar un access con la versión vigente no mata la sesión de Fernando: no toca `token_version`.)

- [ ] **Step 2: `system_name` y `lang_default`**

```bash
PUT() { curl -s -o /dev/null -w "$1 -> %{http_code}\n" -X PUT http://127.0.0.1:8080/api/admin/config -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d "$2"; }
PUT nombre '[{"key":"system_name","value":"Axioma (verificación)"},{"key":"lang_default","value":"en"}]'
curl -s http://127.0.0.1:8080/api/apariencia
```
Expected: `200` y, **sin esperar el TTL**, `{"theme_default":...,"lang_default":"en","system_name":"Axioma (verificación)"}`. Fernando abre `https://axioma-ia.io/login` en una ventana privada (sin `jax_lang`) y confirma: pestaña "Axioma (verificación)", título y botón "Enter Axioma (verificación)" en inglés, en claro y en oscuro. En su ventana normal, con idioma ya elegido, el idioma **no** cambia. Restaurar:

```bash
PUT restaurar '[{"key":"system_name","value":"Axioma"},{"key":"lang_default","value":"es"}]'
curl -s http://127.0.0.1:8080/api/apariencia
```

- [ ] **Step 3: `session_timeout_min` con un usuario de prueba**

Se usa **10079** (un minuto menos que el vigente): la sesión de Fernando no puede tener esa edad, así que no se corta nadie real.

```bash
PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
EM="verificacion-frente-c-$(date +%s)@example.invalid"
UIDP=$(curl -s -X POST http://127.0.0.1:8080/api/admin/users -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d "{\"email\":\"$EM\",\"role\":\"operator\",\"password\":\"$PW\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['user_id'])")
PUT sesion '[{"key":"session_timeout_min","value":"10079"}]'
curl -s -i -X POST http://127.0.0.1:8080/api/auth/login -H 'Content-Type: application/json' -d "{\"email\":\"$EM\",\"password\":\"$PW\"}" | grep -i '^set-cookie: refresh_token' | grep -o 'Max-Age=[0-9]*'
TVP=$(mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -N -e "SELECT token_version FROM jax_users WHERE user_id = $UIDP")
VIEJO=$(.venv/bin/python -c "
import sys, time
from jose import jwt
from auth.jwt import SECRET, ALGORITHM
iat = int(time.time()) - 10079 * 60 - 30
print(jwt.encode({'user_id': sys.argv[1], 'tenant_id': '1', 'role': 'operator', 'tv': int(sys.argv[2]), 'iat': iat, 'exp': iat + 7*24*3600, 'type': 'refresh'}, SECRET, algorithm=ALGORITHM))" "$UIDP" "$TVP")
curl -s -w ' %{http_code}\n' -X POST http://127.0.0.1:8080/api/auth/refresh -H "Cookie: refresh_token=$VIEJO"
PUT restaurar-sesion '[{"key":"session_timeout_min","value":"10080"}]'
curl -s -w ' %{http_code}\n' -X POST http://127.0.0.1:8080/api/auth/refresh -H "Cookie: refresh_token=$VIEJO"
```
Expected: `Max-Age=604740`; primer refresh `{"detail":"sesion_expirada"} 401`; restaurado a 10080, el mismo token (edad 10079,5 min < 10080) → `200`. El tenant del usuario de prueba es el del admin que lo creó; si no es `1`, se usa el `tenant_id` de `SELECT tenant_id FROM jax_users WHERE user_id = $UIDP`.

- [ ] **Step 4: `max_pipelines` y `web_task_retention_days` — rangos del servidor en vivo**

```bash
PUT pipelines-fuera '[{"key":"max_pipelines","value":"4"}]'
PUT pipelines-mayus '[{"key":"MAX_PIPELINES","value":"4"}]'
PUT retencion-fuera '[{"key":"web_task_retention_days","value":"366"}]'
PUT pipelines-2 '[{"key":"max_pipelines","value":"2"}]'
PUT retencion-29 '[{"key":"web_task_retention_days","value":"29"}]'
curl -s -H "Authorization: Bearer $ADMIN" http://127.0.0.1:8080/api/admin/config | python3 -c "import sys,json; d={c['key']:c['value'] for c in json.load(sys.stdin)['config']}; print(d['max_pipelines'], d['web_task_retention_days'])"
PUT restaurar-resto '[{"key":"max_pipelines","value":"3"},{"key":"web_task_retention_days","value":"30"}]'
```
Expected: `400`, `400`, `400`, `200`, `200`; `2 29`; restaurado `200`. El 429 real del cupo necesita un pipeline activo (Jacobs + LLM). **Opcional, con GO explícito de Fernando:** con `max_pipelines=1`, Fernando lanza desde la Mesa un pipeline `supervised` y, mientras está en vuelo, un segundo. El segundo tiene que mostrar el aviso traducido "Ya hay 1 pipeline activo, el máximo permitido…". Después se restaura a 3. Si no hay GO, queda dicho en la Biblioteca que el 429 en vivo no se ejercitó y que lo cubren `test_ajuste_max_pipelines.py` y la carga.

- [ ] **Step 5: Baja del usuario de prueba y estado final idéntico**

```bash
curl -s -o /dev/null -w 'baja: %{http_code}\n' -X POST http://127.0.0.1:8080/api/admin/users/$UIDP/baja -H "Authorization: Bearer $ADMIN"
curl -s -H "Authorization: Bearer $ADMIN" http://127.0.0.1:8080/api/admin/config > <scratchpad>/config-despues-vivo.json
diff <(python3 -m json.tool <scratchpad>/config-antes-vivo.json) <(python3 -m json.tool <scratchpad>/config-despues-vivo.json) && echo "CONFIG IDENTICA A LA DE ANTES"
```
Expected: `baja: 200` y `CONFIG IDENTICA A LA DE ANTES`. Si difiere, se restaura la clave que difiere al valor de antes y se repite el diff.

- [ ] **Step 6: Pantalla de Configuración con Fernando**

Fernando abre Admin → Configuración, en claro y en oscuro: sesión muestra 10080 con máximo 10080, pipelines 3 con máximo 3 y la ayuda "Jacobs no corre más de 3 en total", retención 30 con su ayuda, y ningún campo con un valor inventado. Intenta guardar pipelines 4 escribiéndolo a mano: el navegador lo marca por el `max`; si lo fuerza, el aviso dice "El valor de "Pipelines activos a la vez" está fuera de lo permitido". Anotar su respuesta en el ledger.

---

### Task 18: Biblioteca y PR de jax

**Files (repo `jax`, worktree `/home/fruiz/worktrees/jax-frente-c`):**
- Modify: `DEUDA.md` (entrada nueva inmediatamente antes del primer `## Cerrado —`)
- Modify: `CONTEXT.md` (§9 Historial de hitos, línea nueva al final de la lista)

- [ ] **Step 1: Entrada en `DEUDA.md`**

Ubicar el punto de inserción: `grep -n "^## Cerrado —" /home/fruiz/worktrees/jax-frente-c/DEUDA.md | head -1`. Insertar antes de esa línea (valores entre `<>` = los medidos y anotados en las Tasks 12-17):

```markdown
## Cerrado — ajustes de admin que mandan (frente C, 2026-09-16)

**VERDAD OPERACIONAL <fecha hora CST>** (verificada en vivo, Task 17 del plan
`jax-platform/docs/superpowers/plans/2026-09-16-frente-c-ajustes.md`). jax-platform#<N> → `<sha>`,
frontend `<index-*.js>` (respaldo `<ruta>`, idéntico por `diff -rq`); jax#<M> (familia `tope_pipelines`).
Spec `docs/superpowers/specs/2026-09-16-hallazgos-auditoria-design.md` §C.

- **HISTORIA:** hasta el 2026-09-16, Admin → Configuración guardaba `session_timeout_min`, `max_pipelines`,
  `web_task_retention_days`, `lang_default` y `system_name` y NADIE los leía. Mostraba 60 / 1 / 7 (de
  `DEFAULT_CONFIG`) mientras el código hacía cumplir 7 días / 3 / 30 días.
- **DECISIÓN (Fernando, 2026-09-16):** los cinco mandan; valor inicial = el que regía. Migración única con
  marcador `axioma_migracion_de_datos.ajustes_que_mandan_v1`; ANTES en producción: `<valores del dump>`;
  respaldo `<ruta del dump>`, restauración probada fila por fila (<n> filas).
- **Semántica:** sesión = vida ABSOLUTA del refresh (no se rota), medida también en `/refresh` contra `iat`.
  Al desplegar, cada sesión volvió a entrar una vez. Pipelines = cuota por tenant, tope = candado global de
  Jacobs (`MAX_PARALLEL_PIPELINES`, espejado y vigilado por `check_mirror_sync.py`). Retención = días que el
  dueño de una tarea web puede ver su resultado (owner files). Idioma = el de la interfaz si la persona no
  eligió. Nombre = título, login, logotipo y cabecera de Admin.
- **Caché:** `ajustes.py`, TTL `JAX_AJUSTES_TTL_S` (30 s) + invalidación explícita en el PUT, un solo proceso
  (`exigir_un_solo_proceso`). Sin caché (TTL 0,001) refresh p95 = <x> ms; con caché = <y> ms.
- **Carga (k6, instancia aislada contra `jax_memory_test`, Jacobs falso), base `26c9cd5` → rama:**

  | escenario | VUs | peticiones | rps | p50 | p95 | p99 | errores |
  |---|---|---|---|---|---|---|---|
  | login (base) | 10 | <> | <> | <> | <> | <> | <> |
  | login (rama) | 10 | <> | <> | <> | <> | <> | <> |
  | refresh (base) | 25 | <> | <> | <> | <> | <> | <> |
  | refresh (rama) | 25 | <> | <> | <> | <> | <> | <> |
  | pipelines (base) 200/429 | 25 | <> | <> | <> | <> | <> | <> |
  | pipelines (rama) 200/429 | 25 | <> | <> | <> | <> | <> | <> |

  Se vuelve a medir si cambia `axioma_config`, el camino del login/refresh o el de creación de pipelines.
- **EXPLAIN en producción:** `ajustes.CONSULTA` → `<type>`, `key=PRIMARY`.
- **Runbook — un ajuste ilegible (503 `ajuste_ilegible`, incluido el login):** con `/etc/jax/.env`,
  `UPDATE axioma_config SET config_value='<valor válido>' WHERE config_key='<clave del 503>'` (rangos en
  `backend/ajustes.py::DEFINICIONES`). Se ve solo en ≤ 30 s (TTL), o reiniciar `jax-platform`.
- **CI:** pisos vitest <v>, con DB <c>, sin DB <s>; canario en rojo sobre `<sha canario>` (3 jobs).
- **HECHO (no verificado como problema; pregunta abierta a Fernando, <fecha>):** la misión y el resultado de
  las tareas web los poda `jax/scripts/cleanup.sh` por CANTIDAD (deja 10) y no tiene scheduler. El ajuste de
  retención gobierna solo los owner files. Fuera de la sección C: ¿entra en otro frente?
- **Verificación en vivo del 429 de cupo:** <"hecha con GO de Fernando: …" | "no ejercitada: requiere un pipeline real; cubierta por test_ajuste_max_pipelines.py y la carga">.
```

- [ ] **Step 2: Línea en `CONTEXT.md` §9**

```markdown
- **2026-09-16: Ajustes de admin que mandan (frente C).** Los cinco ajustes de Configuración pasan de decorativos a fuente de verdad (`jax-platform/backend/ajustes.py`, caché con invalidación). Sesión = vida absoluta del refresh; tope de pipelines espejado con Jacobs (`check_mirror_sync.py` familia `tope_pipelines`). Detalle y números en DEUDA.md.
```

- [ ] **Step 3: Commit, push, PR, CI y merge**

```bash
git -C /home/fruiz/worktrees/jax-frente-c add DEUDA.md
git -C /home/fruiz/worktrees/jax-frente-c add CONTEXT.md
git -C /home/fruiz/worktrees/jax-frente-c commit -m "docs(biblioteca): frente C, ajustes de admin que mandan" -m "VERDAD OPERACIONAL del despliegue, carga antes/después, runbook del 503 y pregunta abierta sobre cleanup.sh." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git -C /home/fruiz/worktrees/jax-frente-c fetch origin && git -C /home/fruiz/worktrees/jax-frente-c rebase origin/master
git -C /home/fruiz/worktrees/jax-frente-c push -u origin feat/espejo-tope-pipelines
gh pr create --repo fjruizhn/Jax --base master --head feat/espejo-tope-pipelines --title "Frente C · espejo del tope de pipelines, carga y Biblioteca" --body-file <scratchpad>/pr-jax-frente-c.md
```
Cuerpo: qué vigila la familia y por qué; orden de merge (jax-platform#<N> ya mergeado); los guiones de carga; la entrada de DEUDA; pie `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

```bash
gh pr checks <M> --repo fjruizhn/Jax
gh run view <id del job mirror-sync> --repo fjruizhn/Jax --log | grep -A1 "\[tope_pipelines\]"
gh pr view <M> --repo fjruizhn/Jax --json headRefOid -q .headRefOid
git -C /home/fruiz/worktrees/jax-frente-c ls-remote origin refs/heads/feat/espejo-tope-pipelines
```
Expected: todos `SUCCESS`; el log de `mirror-sync` muestra `[tope_pipelines]` → `sincronizado`; `headRefOid` == sha remoto. Entonces:

```bash
gh pr merge <M> --repo fjruizhn/Jax --merge
git -C /home/fruiz/jax switch master && git -C /home/fruiz/jax pull --ff-only
```
No hace falta reiniciar servicios de jax: `jacobs/policy.py` solo ganó un comentario.

- [ ] **Step 4: Cierre**

```bash
git -C /home/fruiz/jax-platform worktree remove /home/fruiz/worktrees/jax-platform-frente-c
git -C /home/fruiz/jax worktree remove /home/fruiz/worktrees/jax-frente-c
git -C /home/fruiz/jax-platform branch -d feat/ajustes-que-mandan
git -C /home/fruiz/jax branch -d feat/espejo-tope-pipelines
```
Antes de remover el worktree de la plataforma, copiar su ledger `.superpowers/sdd/2026-09-16-frente-c/` a `/home/fruiz/jax-platform/.superpowers/sdd/` (patrón de los frentes anteriores). Borrar del scratchpad los archivos ya volcados a la Biblioteca (dumps TSV, JSON de config, arnés de carga). El dump SQL de `/home/fruiz/backups/` **no** se borra hoy: se agenda con fecha en DEUDA (`borrar desde <fecha + 7 días>`).

---

## Autorrevisión

**1. Cobertura del spec §C:**
- `session_timeout_min` → Task 4 (emisión, cookie, `/refresh`) + Task 11 (pantalla) + Task 17 Step 3.
- `max_pipelines` → Task 5 (cupo y 429 traducido) + Tasks 1 y 14 (tope espejado) + Task 17 Step 4.
- `web_task_retention_days` → Task 6 + Task 17 Step 4.
- `lang_default` → Tasks 7, 8 y 9 + Task 17 Step 2.
- `system_name` → Tasks 7, 8 y 10 + Task 17 Step 2.
- "`ajustes.py`: lectura tipada y validada por clave (rangos del servidor)" → Tasks 1 y 3.
- "caché con TTL e invalidación explícita en el PUT (proceso único, `exigir_un_solo_proceso`)" → Tasks 1 y 3.
- "Valor ilegible → 503" → Task 1 (handler); Tasks 4 y 7 lo ejercitan de punta a punta; Task 6 en el camino de fondo.
- "Rangos de la pantalla corregidos (sesión hasta 10080)" → Tasks 3 y 11.
- "Migración idempotente + dump previo verificado fila por fila" → Task 2 + Task 16 Step 1.
- "Prueba de carga de login/refresh y creación de pipeline con el ajuste leído por request" → Task 15.
- Global Constraints del spec (TDD, i18n, dark/light, sin diálogos, config en DB/.env, fail-closed, caché con invalidación, las cuatro, barrera de DB, CI rompiéndolo, mirror-sync, deploy, Biblioteca) → Tasks 12, 13, 14, 16 y 18.

**2. Placeholders:** los `<N>`, `<M>`, `<sha>`, `<scratchpad>` y los números entre `<>` de DEUDA son valores de ejecución (ids de PR, rutas de la sesión, mediciones), no decisiones pendientes. Todo paso de código trae el código.

**3. Consistencia de nombres:** `ajustes.valor`, `ajustes.invalidar`, `ajustes.limites`, `ajustes.interpretar`, `ajustes.ValorInvalido`, `ajustes.AjusteIlegible(.clave, .motivo)`, `ajustes.CLAVES`, `ajustes.CONSULTA`, `ajustes.SESION/MAX_PIPELINES/RETENCION/IDIOMA/NOMBRE`, `MAX_PARALLEL_PIPELINES`, fixture `ajustes_en_db` (`poner/quitar/filas/validos`), `_ajustes_que_mandan_v1`, `MIGRACION_AJUSTES_V1`, `create_refresh_token(..., *, vida_segundos)`, `sesion_vencida`, `vida_de_sesion_segundos`, `SESION_EXPIRADA`, `_emitir_tokens(..., vida_segundos)`, `can_start_pipeline(tenant_id, limite)`, `ciclo_de_limpieza`, `pipelines_limite_alcanzado`, `config_valor_invalido`, `textoDeErrorDePipeline`, `useApariencia.fijar`, `useNombreDelSistema`, `sincronizarApariencia`, `idiomaInicial`: iguales en todas las tareas que los definen y los consumen.
