# Administración de usuarios · Fijar contraseña por admin + cambio obligatorio en el próximo login

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un superadmin pueda fijarle a mano la contraseña a OTRO usuario, y que ese usuario quede obligado a cambiarla en su próximo login. La obligación la hace cumplir el BACKEND (Ruling U34): mientras la marca esté activa, la sesión sólo sirve para ver quién es (`GET /api/auth/me`), cambiar la contraseña (`POST /api/auth/me/password`), renovar el access (`POST /api/auth/refresh`) y salir (`POST /api/auth/logout`). Todo lo demás responde `403 cambio_de_password_requerido`, y el WebSocket y el SSE se rechazan. El enlace de recuperación sigue existiendo.

**Decisiones que rigen (vinculantes):**
- DECISIÓN de Fernando (2026-09-15 ~11:30, chat): el admin puede escribirle a mano la contraseña a otro usuario → **revierte U2** (2026-09-12, "reset por admin = sólo enlace").
- DECISIÓN de Fernando (2026-09-15, AskUserQuestion): ese usuario DEBE cambiarla en el próximo login.
- Ruling U34 (controlador): la obligación la cumple el backend con `jax_users.must_change_password`. Mi cuenta y el reset por enlace la limpian. El frontend muestra Mi cuenta como diálogo no cerrable.
- U33: toda transacción nueva en READ COMMITTED (`AISLAMIENTO_ADMIN`). U11: escrituras de admin con `_leer_para_actualizar` y su orden fijo. U9: el corte de conexiones va después del commit y es fail-soft. U27: todo modal va sobre `Dialogo`. U25: un solo modal a la vez. U29: sin número de carga no hay merge.

**Architecture:**
1. Una migración idempotente agrega `must_change_password BOOLEAN NOT NULL DEFAULT FALSE` por `_COLUMNS`.
2. `verificar_sesion` lee la columna en su ÚNICA consulta por PK y rechaza por defecto a una sesión con la marca (403). Sólo dos llamadores la admiten:
   - la dependencia nueva `get_current_user_con_cambio_pendiente`, que usan únicamente `/me` y `/me/password`;
   - `/refresh`, que llama a `verificar_sesion` a mano.

   `get_current_user`, `require_superadmin`, el SSE (`Depends(get_current_user)` más `reverificar_sesion`) y el handshake del WS usan el camino por defecto, que rechaza. Así una ruta nueva no puede olvidarse de la regla: para aceptar la marca tiene que pedirla de forma explícita, y dos tests lo vigilan (recorrido de rutas y búsqueda en el código).
3. `POST /api/admin/users/{id}/password` hashea en un hilo y después abre `transaccion(AISLAMIENTO_ADMIN)`. Adentro: `_leer_para_actualizar` → 404 → un UPDATE (hash, `token_version + 1`, marca, desbloqueo) → DELETE de los enlaces pendientes → auditoría `password_set_by_admin` sin secretos. Después del commit, `_cortar_conexiones`.
4. En el frontend:
   - `RequireAuth` muestra el cambio obligatorio EN LUGAR de la app cuando `user.must_change_password`. Así no se monta el Dashboard, ni su WS, ni sus polls.
   - El interceptor, ante un 403 `cambio_de_password_requerido`, prende la marca en el store.
   - `Dialogo` gana la prop `cerrable`.
   - AdminUsers gana "Fijar contraseña" con `FijarPasswordModal`.

**Tech Stack:** FastAPI 0.139.2 (resuelve las dependencias ANTES que los parámetros de ruta y cuerpo: lo verifiqué leyendo `solve_dependencies`), aiomysql, MariaDB 12.3, pytest; React 19, vitest.

**Base:** la rama de la etapa 5 (`feat/admin-usuarios-etapa5`, HEAD `d3d492d` al escribir esto). La Task 4 de la etapa 5 (ConfirmacionSuma, `abrirBaja`, borrado de `handleDelete`) todavía no está en esa rama. **Este plan arranca sobre master con la etapa 5 mergeada y desplegada**, y las anclas de AdminUsers.jsx, AdminUsers.test.jsx, es.js y en.js se re-verifican contra ese master al despachar cada tarea (como se hizo en U20/U28).

## Global Constraints

- **Repo y rama:** `/home/fruiz/jax-platform`; worktree propio desde `origin/master` con la etapa 5 mergeada: `git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform worktree add /home/fruiz/worktrees/jax-platform-fijar-password -b feat/admin-usuarios-fijar-password origin/master`. Siempre `git -C <ruta>` y `pwd` en el mismo comando. **Nada de `git stash`**: si hay que apartar algo, se hace un commit WIP.
- **Barrera de DB:** los tests corren SÓLO por pytest (`conftest.py` fuerza `JAX_DB_NAME=jax_memory_test` y aísla el sello). Nunca se cargan `/etc/jax/.env` ni `JAX_DB_*` en una shell para correr tests, scripts o la carga. `/etc/jax/.env` apunta a PRODUCCIÓN (incidente de la tanda A T1, 2026-09-14). La única lectura de producción es la del deploy (dump + SELECT de esquema), con GO de Fernando.
- **Ningún test modifica `user_id=1`** (sólo actúa como actor). Las identidades salen de `tests/identidades.py` y del fixture `usuarios`.
- **Deltas quirúrgicos:** se reemplaza el bloque exacto que dice cada paso; no se pega un archivo completo.
- **READ COMMITTED** en toda transacción nueva (`transaccion(AISLAMIENTO_ADMIN)`, U33). El orden de bloqueo es conjunto de superadmins → usuario → tokens.
- **Hora:** `tiempo.utc_ahora()`, nunca `datetime.utcnow()` ni `NOW()` nuevos.
- **Sin `except` fail-open:** el único fail-soft nuevo es el que ya trae `_cortar_conexiones`. `test_no_fail_open_except.py` sigue verde.
- **Códigos estables, nunca texto de usuario en el backend:**

  | Código | HTTP | Cuándo |
  |---|---|---|
  | `cambio_de_password_requerido` | 403 | la sesión tiene la marca y la ruta no la admite |
  | `password_corta`, `password_larga` | 400 | la contraseña nueva no cumple la regla única |
  | `password_igual_a_la_actual` | 400 | Mi cuenta con la marca, si la nueva es igual a la actual (P1) |
  | `usuario_no_encontrado` | 404 | el usuario no existe, o está dado de baja |
  | `auto_accion_prohibida` | 403 | el admin intenta fijarse su propia contraseña |
  | `Solo superadmin` | 403 | texto heredado de `require_superadmin`; no se toca en esta rama |

- **Secretos:** ni la contraseña ni el hash van en la auditoría, el log, la respuesta ni un toast.
- **LAS CUATRO DEL RENDIMIENTO:**
  - *Indexing:* EXPLAIN sobre las consultas REALES. `SQL_ESTADO_DE_SESION` sigue `const/PRIMARY` (test existente `test_la_consulta_del_middleware_va_por_primary`). El DELETE de enlaces va por el índice de la FK `user_id`. El UPDATE va por PK.
  - *Cache:* no hay caché nuevo. La consulta de sesión sigue sin caché, a propósito: su invalidación sería la propia `token_version`.
  - *Async:* bcrypt en `asyncio.to_thread` y ANTES de la transacción; nada bloqueante en el camino del request.
  - *Load test:* es la Task 5, y es un gate.
  - `verificar_sesion` corre en CADA request: la carga mide el antes/después del camino caliente.
- **i18n es/en** para cada texto nuevo. **Tokens del tema** (`src/tema/tokens.css`), nunca colores crudos; el escaneo de contraste de todo `src` sigue verde.
- **Accesibilidad:** diálogos sobre `Dialogo` (portal, `#root` inert, foco, `role="dialog"`/`aria-modal`/`aria-labelledby`), cada campo con `<label>`, región `role="status"` montada.
- **Pisos exactos** en `.github/workflows/policy.yml`: leer el valor ACTUAL (en `d3d492d` es DB 845/1, sin DB 371/475 y vitest 315; la etapa 5 los sube antes de mergear) y subirlo con el número MEDIDO dos veces y un comentario que diga qué suma.
- **TDD:** rojo visto por el motivo que dice cada paso, y después verde. Toda mutación que pida el review tiene que caer.
- **Proceso:** SDD con review por tarea, review final (opus), carga (Task 5), PR con CI por `headSha`, deploy con dump previo de `jax_users` y verificación en vivo con Fernando. Commits sin `--no-verify`, con la atribución de la sesión.

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/db/migrations.py` | Modificar | `must_change_password` en `_COLUMNS` |
| `backend/auth/models.py` | Modificar | `AuthUser.must_change_password`; `MeResponse`/`LoginResponse` con la marca |
| `backend/auth/middleware.py` | Modificar | columna en `SQL_ESTADO_DE_SESION`, rechazo 403 por defecto, `get_current_user_con_cambio_pendiente`, `RUTAS_CON_CAMBIO_PENDIENTE` |
| `backend/api/auth.py` | Modificar | login y `/me` con la marca; `/refresh` la admite; `/me` y `/me/password` usan la dependencia permisiva; Mi cuenta y `/reset-password` la limpian |
| `backend/api/admin/users.py` | Modificar | `FijarPasswordRequest`, `POST /users/{id}/password` |
| `backend/user_audit.py` | Modificar | acción `password_set_by_admin` |
| `backend/tests/test_fijar_password.py` | Crear | todos los tests backend de esta rama |
| `frontend/src/components/Dialogo.jsx` (+ test) | Modificar | prop `cerrable` |
| `frontend/src/lib/useCerrarConEscape.js` | Modificar | tolera `onCerrar` nulo |
| `frontend/src/components/MiCuentaModal.jsx` (+ test) | Modificar | modo `obligatorio` |
| `frontend/src/components/RequireAuth.jsx` (+ test) | Crear (se mueve desde App.jsx) | puerta del cambio obligatorio |
| `frontend/src/App.jsx` | Modificar | importa `RequireAuth` |
| `frontend/src/store/useJaxStore.js` (+ test) | Modificar | `cambiarMiPassword` apaga la marca |
| `frontend/src/api/client.js` (+ test) | Modificar | 403 `cambio_de_password_requerido` → marca en el store |
| `frontend/src/components/admin/FijarPasswordModal.jsx` (+ test) | Crear | modal del admin |
| `frontend/src/pages/admin/AdminUsers.jsx` (+ test) | Modificar | botón, exclusión mutua, toast |
| `frontend/src/i18n/es.js`, `en.js` | Modificar | textos |
| `.github/workflows/policy.yml` | Modificar | pisos |

## Interfaces

**Consume (firmas exactas en la base):**

```python
# auth/middleware.py
SQL_ESTADO_DE_SESION: str
verificar_sesion(payload: dict, tipo: str) -> AuthUser
reverificar_sesion(user) -> AuthUser
get_current_user(credentials) -> AuthUser
require_superadmin(user) -> AuthUser

# api/admin/users.py
_leer_para_actualizar(cur, user_id) -> (role, status, email) | None   # excluye 'deleted'; bloquea superadmins -> destino
guarda_auto_accion(actor_id, target_id)
_ip(request)
AISLAMIENTO_ADMIN            # reexportado

# otros módulos
db.transaccion.transaccion(aislamiento=None)
auth.conexiones._cortar_conexiones(user_id)
auth.password_rules.problema_de_password(p) -> "corta" | "larga" | None
db.seed._hash(p) ; db.seed.verify_password(p, h)   # async
user_audit.registrar(cur, actor, target, action, detail=None, ip=None)
```

```js
Dialogo({ idTitulo, titulo, claseTitulo, onCerrar, className, children })
useCerrarConEscape(onCerrar)
MiCuentaModal({ onCerrar })
problemaDePassword(p)
mensajeDeError(t, err)
codigoDe(err)
useJaxStore: user, token, cambiarMiPassword, logout, addToast
```

**Produce:**

```python
# auth/middleware.py
CAMBIO_DE_PASSWORD_REQUERIDO = "cambio_de_password_requerido"
RUTAS_CON_CAMBIO_PENDIENTE = frozenset({("GET", "/api/auth/me"), ("POST", "/api/auth/me/password")})
async def verificar_sesion(payload: dict, tipo: str, *, admite_cambio_pendiente: bool = False) -> AuthUser
async def get_current_user_con_cambio_pendiente(credentials) -> AuthUser
# AuthUser.must_change_password: bool ; MeResponse.must_change_password ; LoginResponse.must_change_password

# api/admin/users.py
POST /api/admin/users/{id}/password   {new_password}  -> {"ok": true}
```

```js
Dialogo({ ..., cerrable = true })
MiCuentaModal({ onCerrar, obligatorio = false })
FijarPasswordModal({ usuario, onFijar, onCerrar })
RequireAuth({ children })
```

---

### Task 1: Migración y marca en la fila de sesión

**Files:**
- Modify: `backend/db/migrations.py` (`_COLUMNS`)
- Modify: `backend/auth/models.py` (`AuthUser`)
- Modify: `backend/auth/middleware.py` (`SQL_ESTADO_DE_SESION`, desempaque en `verificar_sesion`)
- Test: `backend/tests/test_fijar_password.py` (crear)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consume: `_COLUMNS` (tuplas `(tabla, columna, ALTER)`, idempotente por `information_schema`).
- Produce:
  - la columna `must_change_password`;
  - `AuthUser.must_change_password`;
  - `SQL_ESTADO_DE_SESION` con 5 columnas, igual por PK.

  Todavía no cambia ningún comportamiento: sólo se lee y se informa.

- [ ] **Step 1: Tests que fallan**

Crear `backend/tests/test_fijar_password.py`:

```python
"""Fijar contraseña por admin y cambio obligatorio (2026-09-15, admin usuarios,
DECISIONES de Fernando que revierten U2; Ruling U34).

El superadmin le fija la contraseña a OTRO usuario; ese usuario queda con
jax_users.must_change_password y, mientras la tenga, su sesión sólo sirve para
/me, /me/password, /refresh y /logout (403 cambio_de_password_requerido en
todo lo demás, y WS/SSE rechazados). Mi cuenta y el reset por enlace la limpian.
"""
import uuid
from datetime import timedelta

import pytest

from auth.middleware import verificar_sesion
from tests.identidades import auth, sql, token_para
from tiempo import utc_ahora

CLAVE = "clave-vieja-123"
NUEVA = "clave-nueva-456"
FIJADA = "clave-fijada-789"


def _admin():
    return auth(token_para(1, role="superadmin"))


async def _marcar(user_id, valor=True):
    await sql("UPDATE jax_users SET must_change_password = %s WHERE user_id = %s", (valor, user_id))


async def _marca(user_id):
    ((m,),) = await sql("SELECT must_change_password FROM jax_users WHERE user_id = %s", (user_id,), True)
    return bool(m)


# --------------------------------------------------------------- esquema

def test_columna_must_change_password_no_nula_y_falsa_por_defecto(client):
    filas = client.portal.call(
        sql,
        "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' AND COLUMN_NAME = 'must_change_password'",
        (), True)
    assert [tuple(f) for f in filas] == [("tinyint", "NO", "0")]


def test_verificar_sesion_informa_la_marca(client, usuarios):
    u, _ = usuarios()
    payload = {"type": "access", "user_id": str(u), "tenant_id": "1", "tv": 0}
    assert client.portal.call(verificar_sesion, payload, "access").must_change_password is False
    client.portal.call(_marcar, u)
    # En esta tarea sólo se INFORMA; la Task 2 la hace cumplir.
    assert client.portal.call(verificar_sesion, payload, "access").must_change_password is True
```

- [ ] **Step 2: Rojo**

Run: `cd <worktree>/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_fijar_password.py -q`

Expected: FAIL.
- El primer test da `[] != [("tinyint", "NO", "0")]`.
- El segundo cae en `_marcar` con `Unknown column 'must_change_password'`.

- [ ] **Step 3: Implementación mínima**

1. En `backend/db/migrations.py`, en `_COLUMNS`, después de la tupla de `deleted_by`, agregar:

```python
    # Cambio obligatorio de contraseña (2026-09-15, fijar contraseña por admin,
    # Ruling U34). NOT NULL DEFAULT FALSE: las filas existentes quedan sin la
    # marca y nadie queda encerrado al desplegar. La prende sólo
    # POST /api/admin/users/{id}/password; la apagan Mi cuenta y /reset-password.
    ("jax_users", "must_change_password",
     "ALTER TABLE jax_users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"),
```

2. En `backend/auth/models.py`, en `class AuthUser`, después de `token_version: int = 0`, agregar `must_change_password: bool = False`.

3. En `backend/auth/middleware.py`:
   - reemplazar la constante por
     `SQL_ESTADO_DE_SESION = "SELECT status, role, token_version, email, must_change_password FROM jax_users WHERE user_id = %s"`;
   - cambiar `estado, rol, tv_base, email = fila` por `estado, rol, tv_base, email, cambio_pendiente = fila`;
   - en el `AuthUser(...)`, agregar `must_change_password=bool(cambio_pendiente),`.

   Actualizar el docstring del módulo: "lee status, role, token_version, email y must_change_password POR CLAVE PRIMARIA".

- [ ] **Step 4: Verde**
  - `tests/test_fijar_password.py tests/test_sesiones_token_version.py` en verde.
  - `test_la_consulta_del_middleware_va_por_primary` sigue en `const/PRIMARY` y `test_me_hace_una_sola_consulta_a_jax_users` sigue en 1: la columna va en la MISMA consulta.
  - Suite completa con 0 failed.
- [ ] **Step 5: Pisos medidos**
  - Con DB: +2.
  - Sin DB: sin cambio en passed; skips +2.
- [ ] **Step 6: Commit**

```bash
git -C <worktree> add backend/db/migrations.py backend/auth/models.py backend/auth/middleware.py backend/tests/test_fijar_password.py .github/workflows/policy.yml
git -C <worktree> commit -m "feat(db): jax_users.must_change_password y la marca en la única consulta de sesión"
```

---

### Task 2: El backend hace cumplir la marca; `/me`, login, Mi cuenta y reset

**Files:**
- Modify: `backend/auth/middleware.py`
- Modify: `backend/auth/models.py` (`MeResponse`, `LoginResponse`)
- Modify: `backend/api/auth.py` (`login`, `refresh`, `me`, `cambiar_mi_password`, `reset_password`)
- Test: `backend/tests/test_fijar_password.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Produce:
  - `verificar_sesion(..., *, admite_cambio_pendiente=False)`;
  - `get_current_user_con_cambio_pendiente`;
  - `RUTAS_CON_CAMBIO_PENDIENTE`;
  - `CAMBIO_DE_PASSWORD_REQUERIDO`;
  - `must_change_password` en las respuestas de login y `/me`.

**Diseño (dónde vive el control: UN lugar).** `verificar_sesion` es el único punto por el que pasa toda sesión: HTTP, refresh, handshake del WS y re-verificación de WS/SSE.

- El control va ahí, después de los 401 (existencia, estado, versión). Así un token revocado sigue siendo 401, nunca 403.
- Se niega por defecto. Sólo lo saltea quien pasa `admite_cambio_pendiente=True`, y eso lo hacen exactamente dos puntos del código:
  - `get_current_user_con_cambio_pendiente` (middleware), que sólo usan `/me` y `/me/password`;
  - `/refresh` (api/auth.py): sin él, el access de 15 min vence mientras la persona está en el diálogo, y la manda al login en vez de dejarla terminar.
- `/logout` no autentica: siempre pasa.
- Cómo se garantiza que ninguna ruta nueva lo olvide:
  1. toda ruta que no lo pida explícitamente hereda el rechazo (`get_current_user` y `require_superadmin` no cambian de firma);
  2. `test_solo_me_y_mi_cuenta_admiten_la_marca` recorre `app.routes` y exige que el conjunto de rutas cuya cadena de dependencias contiene la dependencia permisiva sea IGUAL a `RUTAS_CON_CAMBIO_PENDIENTE`;
  3. `test_nadie_mas_admite_la_marca` busca `admite_cambio_pendiente=True` en todo `backend/` (fuera de tests) y exige exactamente `auth/middleware.py` y `api/auth.py`, una vez cada uno.
- WS: `verificar_sesion(payload, "access")` en `main.py` lanza el 403, que cae en el `except HTTPException` existente → 4001. `api/websocket.js` no reintenta un 4001.
- SSE: `Depends(get_current_user)` → 403 antes de abrir el stream. Además `reverificar_sesion` también rechaza.
- **Contraseña igual a la actual (P1, ver Preguntas abiertas):** con la marca activa, Mi cuenta rechaza `new_password == current_password` con `400 password_igual_a_la_actual`. Si no, la persona "cambia" a la misma contraseña que el admin conoce y la decisión (2) queda vacía. Es una comparación de strings: no cuesta un bcrypt extra, porque `current_password` ya se verificó contra el hash. Sin la marca, Mi cuenta no cambia.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_fijar_password.py`:

```python
# ----------------------------------------------------- la marca se cumple

import pathlib  # noqa: E402

from fastapi.routing import APIRoute  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import auth.middleware as mw  # noqa: E402
from tests.identidades import _hash  # noqa: E402


def _login(client, email, password):
    try:
        return client.post("/api/auth/login", json={"email": email, "password": password})
    finally:
        client.cookies.clear()


def _cambiar(client, token, actual, nueva):
    try:
        return client.post("/api/auth/me/password", json={"current_password": actual, "new_password": nueva},
                           headers=auth(token))
    finally:
        client.cookies.clear()


def test_con_la_marca_solo_entran_me_mi_cuenta_refresh_y_logout(client, usuarios):
    s, _ = usuarios(role="superadmin")
    client.portal.call(_marcar, s)
    h = auth(token_para(s, role="superadmin"))
    for metodo, url in (("GET", "/api/facets"), ("GET", "/api/admin/users"), ("GET", "/api/pipelines")):
        r = client.request(metodo, url, headers=h)
        assert (r.status_code, r.json()["detail"]) == (403, "cambio_de_password_requerido"), url
    assert client.get("/api/auth/me", headers=h).status_code == 200
    r = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={token_para(s, tipo='refresh')}"})
    client.cookies.clear()
    assert r.status_code == 200, r.text
    assert client.post("/api/auth/logout").status_code == 200


def test_sin_la_marca_nada_cambia(client, usuarios):
    u, _ = usuarios()
    assert client.get("/api/facets", headers=auth(token_para(u))).status_code == 200


def test_una_sesion_revocada_con_la_marca_sigue_siendo_401(client, usuarios):
    u, _ = usuarios(token_version=1)
    client.portal.call(_marcar, u)
    r = client.get("/api/facets", headers=auth(token_para(u, tv=0)))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")


def test_con_la_marca_el_ws_se_cierra_con_4001(client, usuarios):
    u, _ = usuarios()
    client.portal.call(_marcar, u)
    with client.websocket_connect(f"/ws/{u}") as ws:
        ws.send_json({"type": "auth", "token": token_para(u)})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4001


def test_verificar_sesion_con_la_marca_rechaza_salvo_que_se_admita(client, usuarios):
    """Cubre el SSE: su re-verificación (reverificar_sesion) usa el camino por defecto."""
    from fastapi import HTTPException

    u, _ = usuarios()
    client.portal.call(_marcar, u)
    payload = {"type": "access", "user_id": str(u), "tenant_id": "1", "tv": 0}
    with pytest.raises(HTTPException) as exc:
        client.portal.call(verificar_sesion, payload, "access")
    assert (exc.value.status_code, exc.value.detail) == (403, "cambio_de_password_requerido")
    with pytest.raises(HTTPException):
        client.portal.call(mw.reverificar_sesion, mw.AuthUser(user_id=str(u), tenant_id="1", role="operator"))

    async def admitida():
        return await verificar_sesion(payload, "access", admite_cambio_pendiente=True)

    assert client.portal.call(admitida).must_change_password is True


def _dependencias(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependencias(sub)


def test_solo_me_y_mi_cuenta_admiten_la_marca():
    """Puro (importa la app, no la arranca). Una ruta nueva que pida la
    dependencia permisiva sin sumarse a la lista rompe este test."""
    from main import app

    permisivas = {
        (metodo, ruta.path)
        for ruta in app.routes if isinstance(ruta, APIRoute)
        if mw.get_current_user_con_cambio_pendiente in set(_dependencias(ruta.dependant))
        for metodo in ruta.methods
    }
    assert permisivas == set(mw.RUTAS_CON_CAMBIO_PENDIENTE)


def test_nadie_mas_admite_la_marca():
    raiz = pathlib.Path(__file__).resolve().parents[1]
    hallados = {}
    for archivo in raiz.rglob("*.py"):
        partes = archivo.relative_to(raiz).parts
        if partes[0] in ("tests", ".venv") or "site-packages" in partes:
            continue
        n = archivo.read_text(encoding="utf-8").count("admite_cambio_pendiente=True")
        if n:
            hallados[archivo.relative_to(raiz).as_posix()] = n
    assert hallados == {"auth/middleware.py": 1, "api/auth.py": 1}


def test_login_y_me_informan_la_marca(client, usuarios):
    u, email = usuarios(password=CLAVE)
    assert _login(client, email, CLAVE).json()["must_change_password"] is False
    client.portal.call(_marcar, u)
    r = _login(client, email, CLAVE)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.get("/api/auth/me", headers=auth(r.json()["access_token"])).json()["must_change_password"] is True


def test_mi_cuenta_limpia_la_marca_y_la_app_vuelve(client, usuarios):
    u, _ = usuarios(password=CLAVE)
    client.portal.call(_marcar, u)
    r = _cambiar(client, token_para(u), CLAVE, NUEVA)
    assert r.status_code == 200, r.text
    assert client.portal.call(_marca, u) is False
    nuevo = auth(r.json()["access_token"])
    assert client.get("/api/auth/me", headers=nuevo).json()["must_change_password"] is False
    assert client.get("/api/facets", headers=nuevo).status_code == 200


def test_mi_cuenta_con_la_marca_no_acepta_la_misma_contrasena(client, usuarios):
    u, _ = usuarios(password=CLAVE)
    client.portal.call(_marcar, u)
    r = _cambiar(client, token_para(u), CLAVE, CLAVE)
    assert (r.status_code, r.json()["detail"]) == (400, "password_igual_a_la_actual")
    assert client.portal.call(_marca, u) is True


def test_el_reset_por_enlace_limpia_la_marca(client, usuarios):
    u, email = usuarios(password=CLAVE)
    client.portal.call(_marcar, u)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, utc_ahora() + timedelta(hours=1)))
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA}).status_code == 200
    assert client.portal.call(_marca, u) is False
    assert _login(client, email, NUEVA).json()["must_change_password"] is False
```

- [ ] **Step 2: Rojo**

Run: `pytest tests/test_fijar_password.py -q`

Expected:
- `..._solo_entran_me...`: da `200 != 403`.
- `..._ws_se_cierra...`: recibe `auth_ok` y no hay `WebSocketDisconnect`.
- `..._rechaza_salvo...` y `test_solo_me...`: `AttributeError` (`admite_cambio_pendiente` y `get_current_user_con_cambio_pendiente` no existen).
- `test_nadie_mas...`: da `{}`.
- `..._informan_la_marca`: `KeyError 'must_change_password'`.
- `..._limpia_la_marca...` y `..._reset...`: la marca queda en `True`.
- `..._misma_contrasena`: da 200.
- `test_sin_la_marca_nada_cambia` y `..._revocada..._401` pasan desde el rojo: son CONTROLES que fijan que el cambio no mueve lo existente. Dejarlo anotado.

**Seguridad del rojo:** el primer test llama a rutas reales sin la marca aplicada. Sólo se usan GET de lectura (facets, usuarios, pipelines), sin efectos ni LLM.

- [ ] **Step 3: Implementación mínima**

1. `backend/auth/middleware.py`:

   a. Después de `SESION_INVALIDA = "sesion_invalida"`, agregar:

```python
CAMBIO_DE_PASSWORD_REQUERIDO = "cambio_de_password_requerido"
# Las ÚNICAS rutas que aceptan una sesión con must_change_password (Ruling
# U34). Además de ellas: /api/auth/refresh (llama a verificar_sesion a mano) y
# /api/auth/logout (no autentica). Todo lo demás se niega por defecto.
# tests/test_fijar_password.py fija que esta lista y las rutas que piden
# get_current_user_con_cambio_pendiente son el mismo conjunto.
RUTAS_CON_CAMBIO_PENDIENTE = frozenset({("GET", "/api/auth/me"), ("POST", "/api/auth/me/password")})
```

   b. Firma: `async def verificar_sesion(payload: dict, tipo: str, *, admite_cambio_pendiente: bool = False) -> AuthUser:`.

   c. Después de `if estado != "active" or tv_token != int(tv_base): raise _rechazo()`, agregar:

```python
    # Después de los 401: una sesión revocada sigue siendo 401 (el frontend la
    # manda al login); una válida con la marca, 403 (la manda al cambio).
    if cambio_pendiente and not admite_cambio_pendiente:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=CAMBIO_DE_PASSWORD_REQUERIDO)
```

   d. Después de `get_current_user`, agregar:

```python
async def get_current_user_con_cambio_pendiente(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    """SOLO para RUTAS_CON_CAMBIO_PENDIENTE (/me y /me/password). Cualquier
    otra ruta usa get_current_user, que niega la sesión con la marca."""
    return await verificar_sesion(decode_token(credentials.credentials), "access", admite_cambio_pendiente=True)
```

2. `backend/auth/models.py`: en `LoginResponse` y en `MeResponse`, agregar `must_change_password: bool = False`.

3. `backend/api/auth.py`:

   a. Import: `from auth.middleware import get_current_user_con_cambio_pendiente, verificar_sesion`. Quitar `get_current_user` si queda sin uso: `/me` y `/me/password` pasan a la permisiva.

   b. `login`:
   - al SELECT, después de `token_version`, agregarle `, must_change_password`;
   - desempacar `..., locked_until, token_version, must_change_password = row`;
   - al `LoginResponse(...)`, agregarle `must_change_password=bool(must_change_password),`.

   c. `refresh`: `user = await verificar_sesion(decode_token(refresh_token), "refresh", admite_cambio_pendiente=True)`, con el comentario: "U34: la renovación se admite con la marca; sin ella, el access de 15 min vence en medio del cambio obligatorio".

   d. `me`: `user: AuthUser = Depends(get_current_user_con_cambio_pendiente)`, y en `MeResponse(...)` agregar `must_change_password=user.must_change_password,`.

   e. `cambiar_mi_password`:
   - `Depends(get_current_user_con_cambio_pendiente)`;
   - después de `if problema: raise ...`, agregar:

```python
    # Cambio obligatorio (U34, P1): con la marca, la nueva no puede ser la que
    # fijó el admin -- si no, el cambio no cambia nada. Comparación de strings:
    # la actual ya se verificó contra el hash.
    if user.must_change_password and req.new_password == req.current_password:
        raise HTTPException(status_code=400, detail="password_igual_a_la_actual")
```

   - en el UPDATE de la transacción, `"failed_attempts = 0, locked_until = NULL WHERE user_id = %s"` pasa a `"failed_attempts = 0, locked_until = NULL, must_change_password = FALSE WHERE user_id = %s"`.

   f. `reset_password`: en su UPDATE, `"token_version = token_version + 1 WHERE user_id = %s"` pasa a `"token_version = token_version + 1, must_change_password = FALSE WHERE user_id = %s"`. El comentario dice que la persona eligió su propia contraseña por el enlace.

   **Nota para la review:** Mi cuenta ya re-lee `token_version` bajo `FOR UPDATE`. Si el admin fija otra contraseña en la ventana de bcrypt, esta sesión da 401 y la marca queda (test en la Task 3).

- [ ] **Step 4: Verde**
  - `tests/test_fijar_password.py`, `tests/test_contrasenas.py`, `tests/test_sesiones_token_version.py`, `tests/test_sse_isolation.py` y `tests/test_websocket_isolation.py` en verde.
  - Suite completa con 0 failed.
  - Mutaciones que tienen que caer:
    - (a) quitar el `if cambio_pendiente ...` → caen 3 tests;
    - (b) `admite_cambio_pendiente=True` en `get_current_user` → caen `test_solo_me...` y `test_nadie_mas...`;
    - (c) quitar `must_change_password = FALSE` de Mi cuenta o de reset → cae su test;
    - (d) chequear la marca ANTES de los 401 → cae `..._revocada..._401`.
- [ ] **Step 5: Pisos medidos**
  - Con DB: +9.
  - Sin DB: +2 passed (`test_solo_me...` y `test_nadie_mas...` son puros); skips +9.
  - Si importar `main` pidiera DB en el job sin DB, el test pasa a pedir `client` y el comentario lo dice. Medirlo.
- [ ] **Step 6: Commit**

```bash
git -C <worktree> commit -am "feat(auth): con must_change_password la sesión sólo sirve para /me, Mi cuenta, refresh y logout -- negado por defecto"
```

(`git add` explícito de `backend/tests/test_fijar_password.py` si `-a` no lo toma: el archivo ya existe desde la Task 1.)

---

### Task 3: `POST /api/admin/users/{id}/password`

**Files:**
- Modify: `backend/api/admin/users.py` (endpoint nuevo al final de la sección de enlaces, antes de la baja)
- Modify: `backend/user_audit.py` (`ACCIONES`)
- Test: `backend/tests/test_fijar_password.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consume:
  - `_leer_para_actualizar`, `guarda_auto_accion`, `_ip`, `AISLAMIENTO_ADMIN`;
  - `problema_de_password`, `_hash`;
  - `_cortar_conexiones`, `user_audit.registrar`.
- Produce:
  - `FijarPasswordRequest(extra="forbid")`;
  - `fijar_password`;
  - la acción de auditoría `password_set_by_admin`.

**Decisiones de diseño:**
- **Inactivo: se PERMITE.** Fijar una contraseña no le da entrada: el login exige `active` DESPUÉS de verificar la contraseña (403 "Usuario inactivo"), y `verificar_sesion` también. Sirve para dejar la cuenta lista antes de reactivarla por el PUT, y la marca sobrevive a la reactivación: el primer login igual obliga a cambiarla.
  - Es distinto de "Enviar enlace", que rechaza al inactivo (409 `usuario_no_activo`) porque `/reset-password` no deja completar un enlace de un inactivo (U21): ese enlace sería inútil.
  - Acá no se entrega nada que se pueda usar hasta la reactivación.
  - El dado de baja da 404 (`_leer_para_actualizar` lo excluye).
- **Se limpia el bloqueo (`failed_attempts = 0, locked_until = NULL`):**
  - el bloqueo existe contra quien adivina la contraseña VIEJA, y la nueva no fue atacada;
  - el admin la entrega para que la persona entre YA: con el bloqueo vigente (hasta 15 min) el login daría 423 con la contraseña correcta recién dada;
  - es lo mismo que hacen `/reset-password` y Mi cuenta (U16) al cambiar la contraseña;
  - el límite por IP y por email del login (`rate_limit`) sigue intacto.
- **Se borran los enlaces pendientes (`used = FALSE`):** un enlace enviado antes no puede pisar después la contraseña que fijó el admin ni apagar la marca con una contraseña que el admin no eligió. Los usados quedan como historial. Mismo DELETE y mismo índice que la baja.
- **`token_version + 1` y corte:** la contraseña vieja deja de valer y toda sesión abierta con ella muere (spec §3.2: la versión sube en todo cambio de contraseña).
- **Auto-acción:** `guarda_auto_accion` → 403. El admin cambia la suya en Mi cuenta. Si se la fijara a sí mismo, quedaría con la marca y su propio 403.
- **Superadmin destino:** se permite (lo fija otro superadmin). No hace falta el invariante: el destino sigue siendo superadmin activo; sólo queda obligado a cambiar la contraseña.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_fijar_password.py`:

```python
# ------------------------------------------------------ endpoint del admin

import json  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

import api.admin.users as users_mod  # noqa: E402
import api.auth as auth_mod  # noqa: E402
from auth import conexiones as conexiones_mod  # noqa: E402
from jax_engine.websocket_hub import ws_hub  # noqa: E402


async def _version(user_id):
    ((tv,),) = await sql("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return tv


async def _auditoria(target):
    return [tuple(f) for f in await sql("SELECT action, detail FROM user_admin_audit WHERE target_user_id = %s "
                                        "ORDER BY id", (target,), True)]


@pytest.fixture
def cortes(monkeypatch):
    """U9: cada corte registra la token_version que ve OTRA conexión (si
    corriera dentro de la transacción, vería la vieja)."""
    registro = []

    async def ws(user_id, code=4001):
        registro.append(("ws", user_id, await _version(int(user_id))))
        return 0

    async def sse(user_id):
        registro.append(("sse", user_id, await _version(int(user_id))))
        return 0

    monkeypatch.setattr(ws_hub, "close_user", ws)
    monkeypatch.setattr(conexiones_mod, "close_user_streams", sse)
    return registro


def _fijar(client, target, password=FIJADA, cabeceras=None, **extra):
    return client.post(f"/api/admin/users/{target}/password", json={"new_password": password, **extra},
                       headers=cabeceras or _admin())


def test_fijar_password_cambia_corta_marca_y_audita_sin_la_contrasena(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    viejo = token_para(u)
    r = _fijar(client, u)
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401, "las sesiones viejas mueren"
    assert _login(client, email, CLAVE).status_code == 401
    r = _login(client, email, FIJADA)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.portal.call(_version, u) == 1
    ((accion, detalle),) = client.portal.call(_auditoria, u)
    assert (accion, detalle) == ("password_set_by_admin", None)
    assert cortes == [("ws", str(u), 1), ("sse", str(u), 1)], "corte tras el commit"


def test_fijar_password_aplica_la_regla_sin_tocar_nada(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)
    for password, codigo in (("corta", "password_corta"), ("ñ" * 37, "password_larga")):
        r = _fijar(client, u, password)
        assert (r.status_code, r.json()["detail"]) == (400, codigo)
    assert client.portal.call(_version, u) == 0 and client.portal.call(_marca, u) is False
    assert client.portal.call(_auditoria, u) == [] and cortes == []


def test_fijar_password_limpia_el_bloqueo(client, usuarios, cortes):
    u, email = usuarios(password=CLAVE)
    client.portal.call(sql, "UPDATE jax_users SET failed_attempts = 5, locked_until = %s WHERE user_id = %s",
                       (utc_ahora() + timedelta(minutes=15), u))
    assert _fijar(client, u).status_code == 200
    ((intentos, hasta),) = client.portal.call(
        sql, "SELECT failed_attempts, locked_until FROM jax_users WHERE user_id = %s", (u,), True)
    assert (intentos, hasta) == (0, None)
    assert _login(client, email, FIJADA).status_code == 200


def test_fijar_password_borra_solo_los_enlaces_pendientes(client, usuarios, cortes):
    u, _ = usuarios(password=CLAVE)
    pendiente, usado = str(uuid.uuid4()), str(uuid.uuid4())
    vence = utc_ahora() + timedelta(hours=1)
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address, used) "
                            "VALUES (%s, %s, %s, 'test', FALSE), (%s, %s, %s, 'test', TRUE)",
                       (u, pendiente, vence, u, usado, vence))
    assert _fijar(client, u).status_code == 200
    filas = client.portal.call(sql, "SELECT token FROM password_reset_tokens WHERE user_id = %s", (u,), True)
    assert [f[0] for f in filas] == [usado]
    r = client.post("/api/auth/reset-password", json={"token": pendiente, "password": NUEVA})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_invalido")
    assert client.portal.call(_marca, u) is True, "un enlace viejo no apaga la marca"


def test_fijar_password_a_un_inactivo_no_lo_activa(client, usuarios, cortes):
    u, email = usuarios(status="inactive", password=CLAVE)
    assert _fijar(client, u).status_code == 200
    ((estado,),) = client.portal.call(sql, "SELECT status FROM jax_users WHERE user_id = %s", (u,), True)
    assert estado == "inactive" and client.portal.call(_marca, u) is True
    r = _login(client, email, FIJADA)
    assert (r.status_code, r.json()["detail"]) == (403, "Usuario inactivo")


def test_fijar_password_rechazos(client, usuarios, cortes):
    s, _ = usuarios(role="superadmin")
    r = _fijar(client, s, cabeceras=auth(token_para(s, role="superadmin")))
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida"), "la propia va por Mi cuenta"
    ido, _ = usuarios()
    assert client.post(f"/api/admin/users/{ido}/baja", headers=_admin()).status_code == 200
    assert (_fijar(client, ido).status_code, _fijar(client, ido).json()["detail"]) == (404, "usuario_no_encontrado")
    assert _fijar(client, 2**31 - 1).json()["detail"] == "usuario_no_encontrado"
    op, _ = usuarios()
    otro, _ = usuarios()
    assert _fijar(client, otro, cabeceras=auth(token_para(op))).status_code == 403
    assert _fijar(client, otro, must_change_password=False).status_code == 422, "extra='forbid'"
    assert client.portal.call(_version, otro) == 0


def test_fijar_password_si_el_corte_falla_responde_igual(client, usuarios, monkeypatch):
    async def revienta(user_id, code=4001):
        raise RuntimeError("hub caído")

    monkeypatch.setattr(ws_hub, "close_user", revienta)
    monkeypatch.setattr(conexiones_mod, "close_user_streams", revienta)
    u, _ = usuarios()
    assert _fijar(client, u).status_code == 200
    assert client.portal.call(_version, u) == 1


def test_fijar_password_hashea_antes_y_bloquea_en_el_orden_fijo(client, usuarios, cortes, monkeypatch):
    """bcrypt ANTES de abrir la transacción (nunca con filas tomadas), READ
    COMMITTED, superadmins -> usuario -> tokens -> auditoría (U11, U21, U33)."""
    u, _ = usuarios()
    pasos = []
    hash_real, transaccion_real = users_mod._hash, users_mod.transaccion

    def hash_que_graba(p):
        pasos.append("HASH")
        return hash_real(p)

    class _Graba:
        def __init__(self, cur):
            self._cur = cur

        def __getattr__(self, nombre):
            return getattr(self._cur, nombre)

        async def execute(self, consulta, args=()):
            pasos.append(" ".join(consulta.split()))
            return await self._cur.execute(consulta, args)

    @asynccontextmanager
    async def con_registro(*args, **kw):
        pasos.append(("BEGIN", args, kw))
        async with transaccion_real(*args, **kw) as cur:
            yield _Graba(cur)

    monkeypatch.setattr(users_mod, "_hash", hash_que_graba)
    monkeypatch.setattr(users_mod, "transaccion", con_registro)
    assert _fijar(client, u).status_code == 200
    assert pasos[0] == "HASH"
    assert pasos[1] == ("BEGIN", ("READ COMMITTED",), {})
    sqls = [p for p in pasos[2:] if isinstance(p, str)]
    orden = [next(i for i, q in enumerate(sqls) if cond(q)) for cond in (
        lambda q: q == " ".join(users_mod.SQL_SUPERADMINS_ACTIVOS.split()),
        lambda q: q.startswith("SELECT role, status, email FROM jax_users") and q.endswith("FOR UPDATE"),
        lambda q: q.startswith("UPDATE jax_users SET password_hash"),
        lambda q: q.startswith("DELETE FROM password_reset_tokens WHERE user_id"),
        lambda q: q.startswith("INSERT INTO user_admin_audit"),
    )]
    assert orden == sorted(orden), sqls


def test_mi_cuenta_no_deshace_una_contrasena_fijada_en_el_medio(client, usuarios, cortes, monkeypatch):
    """El admin fija la contraseña mientras la persona verifica la actual en
    Mi cuenta (ventana de bcrypt): Mi cuenta da 401 y queda lo del admin."""
    u, email = usuarios(password=CLAVE)
    verificar_real = auth_mod.verify_password

    async def verifica_y_el_admin_fija(plain, hashed):
        ok = await verificar_real(plain, hashed)
        from httpx import ASGITransport, AsyncClient
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/admin/users/{u}/password", json={"new_password": FIJADA}, headers=_admin())
            assert r.status_code == 200, r.text
        return ok

    monkeypatch.setattr(auth_mod, "verify_password", verifica_y_el_admin_fija)
    r = _cambiar(client, token_para(u), CLAVE, NUEVA)
    monkeypatch.setattr(auth_mod, "verify_password", verificar_real)
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")
    assert client.portal.call(_marca, u) is True
    assert _login(client, email, FIJADA).status_code == 200
    assert [a for a, _ in client.portal.call(_auditoria, u)] == ["password_set_by_admin"]
```

- [ ] **Step 2: Rojo**

Expected:
- Los que llaman al endpoint reciben 404 o 405: la ruta no existe.
- `..._hashea_antes...` falla en el `assert ... == 200`.
- `..._mi_cuenta_no_deshace...` falla dentro del stub (el POST del admin da 404/405).

**Si `ASGITransport` dentro del stub no comparte el pool** (el portal del fixture `client` es el mismo event loop, así que debería compartirlo), usar la alternativa ya probada en `test_contrasenas.py`: una sentencia SQL directa que imite el efecto (`UPDATE ... token_version+1, must_change_password=TRUE`). En ese caso se pierde la prueba del endpoint real, y el informe lo dice.

- [ ] **Step 3: Implementación mínima**

1. `backend/user_audit.py`: en `ACCIONES`, agregar `"password_set_by_admin"` después de `"password_reset_completed"`.

2. `backend/api/admin/users.py`: antes de la sección `# ---- baja`, agregar:

```python
# ------------------------------------------------------- fijar contraseña
# (2026-09-15, DECISIONES de Fernando que revierten U2; Ruling U34). El admin
# escribe la contraseña de OTRO usuario y ese usuario queda obligado a
# cambiarla en su próximo login (must_change_password, que el backend hace
# cumplir en auth/middleware.py). El enlace de recuperación sigue existiendo.

class FijarPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(max_length=1024)


@router.post("/users/{user_id}/password")
async def fijar_password(user_id: int, req: FijarPasswordRequest, request: Request,
                         user: AuthUser = Depends(require_superadmin)):
    """Orden fijo (U11): guarda de auto-acción (la propia va por Mi cuenta),
    regla única, bcrypt en un hilo y ANTES de la transacción; en READ
    COMMITTED (U33): superadmins -> usuario (_leer_para_actualizar; 404 si no
    existe o está de baja) -> UPDATE -> enlaces pendientes -> auditoría. Tras
    el commit, el corte (U9, fail-soft).

    Un inactivo se permite: no le da entrada (el login exige 'active') y deja
    la cuenta lista para reactivarla, con la marca puesta. El bloqueo se
    limpia: protegía la contraseña vieja, y la nueva se entrega para usarla ya
    (igual que /reset-password y Mi cuenta, U16). Los enlaces pendientes se
    borran: uno viejo no puede pisar lo que fijó el admin ni apagar la marca."""
    actor_id = int(user.user_id)
    guarda_auto_accion(actor_id, user_id)
    problema = problema_de_password(req.new_password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    nuevo_hash = await asyncio.to_thread(_hash, req.new_password)
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        if await _leer_para_actualizar(cur, user_id) is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, token_version = token_version + 1, "
            "must_change_password = TRUE, failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (nuevo_hash, user_id),
        )
        await cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s AND used = FALSE", (user_id,))
        # Sin detalle: ni la contraseña ni el hash salen de jax_users.
        await user_audit.registrar(cur, actor_id, user_id, "password_set_by_admin", None, _ip(request))
    await _cortar_conexiones(user_id)
    return {"ok": True}
```

   - Import: `from pydantic import BaseModel, ConfigDict, Field`.
   - Actualizar el docstring de `guarda_auto_accion`: "Nadie se degrada, se desactiva, se da de baja ni se fija la contraseña a sí mismo".
   - Actualizar el comentario de `UpdateUserRequest`: la contraseña la cambia el dueño (Mi cuenta, enlace) o la fija el admin por `/password`.

- [ ] **Step 4: Verde**
  - `tests/test_fijar_password.py`, `test_contrasenas.py`, `test_admin_usuarios_baja.py`, `test_admin_usuarios_guardas.py` y `test_user_audit.py` en verde.
  - Suite completa con 0 failed.
  - Mutaciones que tienen que caer:
    - sin `token_version + 1`;
    - sin `must_change_password = TRUE`;
    - sin el DELETE de enlaces;
    - DELETE sin `used = FALSE`;
    - hash dentro de la transacción;
    - `transaccion()` sin nivel;
    - corte dentro de la transacción;
    - sin `guarda_auto_accion`.
- [ ] **Step 5: Pisos medidos**
  - Con DB: +9.
  - Sin DB: skips +9.
- [ ] **Step 6: Commit**

```bash
git -C <worktree> add backend/api/admin/users.py backend/user_audit.py backend/tests/test_fijar_password.py .github/workflows/policy.yml
git -C <worktree> commit -m "feat(admin): el superadmin fija la contraseña de otro usuario -- corta sesiones, obliga a cambiarla y mata los enlaces pendientes"
```

---

### Task 4: Frontend — "Fijar contraseña" y el cambio obligatorio no cerrable

**Files:**
- Modify: `frontend/src/lib/useCerrarConEscape.js`
- Modify: `frontend/src/components/Dialogo.jsx`, `Dialogo.test.jsx` (+1)
- Modify: `frontend/src/components/MiCuentaModal.jsx`, `MiCuentaModal.test.jsx` (+3)
- Create: `frontend/src/components/RequireAuth.jsx`, `RequireAuth.test.jsx` (+2)
- Modify: `frontend/src/App.jsx`: se borra la función local `RequireAuth` y se importa la del componente. Es un movimiento: el cuerpo se copia tal cual y se le agrega la rama nueva.
- Modify: `frontend/src/store/useJaxStore.js`, `useJaxStore.test.js` (+1)
- Modify: `frontend/src/api/client.js`, `client.test.js` (+2)
- Create: `frontend/src/components/admin/FijarPasswordModal.jsx`, `FijarPasswordModal.test.jsx` (+3)
- Modify: `frontend/src/pages/admin/AdminUsers.jsx`, `AdminUsers.test.jsx` (+2)
- Modify: `frontend/src/i18n/es.js`, `en.js`, `.github/workflows/policy.yml`

**Diseño:**
- **`Dialogo` cerrable:** la prop mínima es `cerrable = true`. Con `false`, Escape no hace nada: `useCerrarConEscape(cerrable ? onCerrar : null)`, y el hook tolera `null`. Lo demás no cambia: portal, inert, foco, ARIA. `Dialogo` nunca dibujó un botón de cerrar propio: los botones son de cada modal.
- **Dónde se muestra el cambio obligatorio:** en `RequireAuth`, que ya envuelve `/` y `/admin/*`. Si `user?.must_change_password`, renderiza `<MiCuentaModal obligatorio />` EN LUGAR de los hijos. Así Dashboard, su `useWebSocket`, sus polls y Admin no se montan: no hay tormenta de 403 ni 4001. Es la negación por defecto del backend, repetida en la UI.
- **`MiCuentaModal obligatorio`:**
  - título `t.forcedChangeTitle` y texto `t.forcedChangeIntro`;
  - sin Cancelar, sin Escape, sin el bloque "Cerrar" del éxito;
  - un botón "Cerrar sesión" (`t.logout`), porque `/logout` está en la lista permitida. Sin él, quien no recuerda la contraseña que le dio el admin sólo puede cerrar la pestaña. No cierra el diálogo: termina la sesión.
  - Al terminar con éxito, `cambiarMiPassword` apaga `user.must_change_password` en el store. `RequireAuth` re-renderiza la app y un toast traducido (`t.forcedChangeDone`) avisa. El `Toast` del Dashboard lo muestra al montarse: el toast vive 5 s en el store.
  - `password_igual_a_la_actual` se traduce.
- **Interceptor:** un 403 con código `cambio_de_password_requerido` prende la marca en `user` del store. Pasa, por ejemplo, si el admin la fijó y la persona entró desde otro lado con la contraseña nueva, o si un poll se cruzó. `RequireAuth` lleva a la persona al diálogo. No hay refresh ni reintento: el error sigue su curso. `restoreSession` y `login` ya guardan `user` con la marca que devuelve el backend.
- **Admin:**
  - botón `t.adminUserSetPassword` con `ACCION_NEUTRA`, al lado de "Enviar enlace";
  - `abrirFijarPassword(u)` cierra los demás modales (U25; la función `abrir*` de la etapa 5 para la baja también cierra este);
  - `FijarPasswordModal` va sobre `Dialogo`, con nueva + confirmar (`PasswordInput`) y `problemaDePassword`;
  - éxito: toast `t.adminPasswordSetDone(email)`; error: `mensajeDeError`.

- [ ] **Step 1: Tests que fallan**

`frontend/src/components/Dialogo.test.jsx`: agregar dentro del `describe` principal:

```jsx
  it('con cerrable={false}, Escape no cierra y el resto del contrato sigue', () => {
    const onCerrar = vi.fn()
    render(
      <Dialogo idTitulo="dlg-fijo" titulo="Obligatorio" onCerrar={onCerrar} cerrable={false}>
        <input aria-label="campo" />
      </Dialogo>
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Obligatorio' })).toBeInTheDocument()
    expect(root).toHaveAttribute('inert')
    expect(screen.getByLabelText('campo')).toHaveFocus()
  })
```

`frontend/src/components/MiCuentaModal.test.jsx`:
- el mock del store pasa a `selector({ cambiarMiPassword: cambiarMock, logout: logoutMock })`, con `const logoutMock = vi.fn()`;
- `logoutMock.mockReset()` en el `beforeEach`;
- agregar:

```jsx
describe('MiCuentaModal obligatorio (cambio exigido por el admin, U34)', () => {
  function renderObligatorio() {
    const onCerrar = vi.fn()
    render(<I18nProvider><MiCuentaModal obligatorio onCerrar={onCerrar} /></I18nProvider>)
    return onCerrar
  }

  it('no se puede cerrar: sin Cancelar y Escape no hace nada', () => {
    const onCerrar = renderObligatorio()
    expect(screen.getByRole('dialog', { name: 'Cambiá tu contraseña' })).toBeInTheDocument()
    expect(screen.getByText('Un administrador fijó tu contraseña. Para seguir, elegí una nueva.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancelar' })).not.toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).not.toHaveBeenCalled()
  })

  it('"Cerrar sesión" termina la sesión (logout está permitido)', () => {
    renderObligatorio()
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }))
    expect(logoutMock).toHaveBeenCalledTimes(1)
  })

  it('la misma contraseña que fijó el admin se rechaza traducida', async () => {
    cambiarMock.mockRejectedValue({ response: { status: 400, data: { detail: 'password_igual_a_la_actual' } } })
    renderObligatorio()
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: 'fijada-por-admin' } })
    fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: 'fijada-por-admin' } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: 'fijada-por-admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cambiar contraseña' }))
    expect(await screen.findByText('La nueva contraseña tiene que ser distinta de la que te dieron.')).toBeInTheDocument()
  })
})
```

(El texto de "Cerrar sesión" es `t.logout` de es.js. Re-verificar el literal al despachar.)

Crear `frontend/src/components/RequireAuth.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'

// U34: con must_change_password la app NO se monta (ni Dashboard, ni su WS,
// ni sus polls): sólo el cambio obligatorio.
let estado
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector(estado),
}))

import RequireAuth from './RequireAuth'
import { I18nProvider } from '../i18n/index.jsx'

function renderizar() {
  return render(
    <I18nProvider><MemoryRouter><RequireAuth><p>la app</p></RequireAuth></MemoryRouter></I18nProvider>
  )
}

beforeEach(() => {
  const root = document.getElementById('root') || Object.assign(document.createElement('div'), { id: 'root' })
  document.body.appendChild(root)
  estado = { token: 't', sessionRestoring: false, cambiarMiPassword: vi.fn(), logout: vi.fn(), user: { user_id: 5 } }
})

describe('RequireAuth', () => {
  it('sin la marca muestra la app', () => {
    renderizar()
    expect(screen.getByText('la app')).toBeInTheDocument()
  })

  it('con la marca muestra sólo el cambio obligatorio', () => {
    estado.user = { user_id: 5, must_change_password: true }
    renderizar()
    expect(screen.queryByText('la app')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Cambiá tu contraseña' })).toBeInTheDocument()
  })
})
```

`frontend/src/store/useJaxStore.test.js`: en `describe('cambiarMiPassword')`, agregar:

```js
  it('al terminar apaga must_change_password del usuario (U34)', async () => {
    useJaxStore.setState({ user: { user_id: 5, must_change_password: true } })
    api.post.mockResolvedValue({ data: { access_token: 'tok-nuevo' } })
    await useJaxStore.getState().cambiarMiPassword('fijada-por-admin', 'nueva-clave-9')
    expect(useJaxStore.getState().user).toEqual({ user_id: 5, must_change_password: false })
  })
```

`frontend/src/api/client.test.js`: agregar al final:

```js
describe('client.js -- 403 cambio_de_password_requerido (U34)', () => {
  const err403 = (detail) => ({ response: { status: 403, data: { detail } }, config: { headers: {} } })

  it('prende la marca en el usuario del store, sin refresh ni reintento', async () => {
    getStateMock.mockReturnValue({ token: 't', user: { user_id: 5 } })
    await expect(onRejected(err403('cambio_de_password_requerido'))).rejects.toBeTruthy()
    expect(setStateMock).toHaveBeenCalledWith({ user: { user_id: 5, must_change_password: true } })
    expect(axiosPostMock).not.toHaveBeenCalled()
  })

  it('otro 403 no toca el store', async () => {
    getStateMock.mockReturnValue({ token: 't', user: { user_id: 5 } })
    await expect(onRejected(err403('Solo superadmin'))).rejects.toBeTruthy()
    expect(setStateMock).not.toHaveBeenCalled()
  })
})
```

Crear `frontend/src/components/admin/FijarPasswordModal.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import FijarPasswordModal from './FijarPasswordModal'
import { I18nProvider } from '../../i18n/index.jsx'

function llenar(nueva, confirmar) {
  const onFijar = vi.fn().mockResolvedValue()
  render(<I18nProvider><FijarPasswordModal usuario={{ user_id: 2, email: 'b@x.io' }} onFijar={onFijar} onCerrar={vi.fn()} /></I18nProvider>)
  fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: nueva } })
  fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: confirmar } })
  fireEvent.click(screen.getByRole('button', { name: 'Fijar contraseña' }))
  return onFijar
}

describe('FijarPasswordModal', () => {
  it('es un diálogo con el correo y avisa que el usuario tendrá que cambiarla', () => {
    llenar('', '')
    expect(screen.getByRole('dialog', { name: 'Fijar la contraseña de b@x.io' })).toBeInTheDocument()
    expect(screen.getByText(/tendrá que cambiarla/)).toBeInTheDocument()
  })

  it('la regla y la confirmación se frenan antes del backend', () => {
    const onFijar = llenar('corta', 'corta')
    expect(screen.getByText('Mínimo 8 caracteres')).toBeInTheDocument()
    expect(onFijar).not.toHaveBeenCalled()
  })

  it('válida y confirmada, llama a onFijar con la contraseña', async () => {
    const onFijar = llenar('clave-fijada-789', 'clave-fijada-789')
    await waitFor(() => expect(onFijar).toHaveBeenCalledWith('clave-fijada-789'))
  })
})
```

(`getByLabelText('Nueva contraseña')` choca con el aria-label del ojito de `PasswordInput` sólo si coincide. Coincide con `t.resetPasswordLabel`, no con `t.showPassword`: re-verificarlo en el rojo.)

`frontend/src/pages/admin/AdminUsers.test.jsx`: agregar al final:

```jsx
describe('AdminUsers — fijar contraseña', () => {
  async function abrirYFijar() {
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Fijar contraseña' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText('Nueva contraseña'), { target: { value: 'clave-fijada-789' } })
    fireEvent.change(within(dialogo).getByLabelText('Confirmar contraseña'), { target: { value: 'clave-fijada-789' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Fijar contraseña' }))
  }

  it('fija, cierra el modal y avisa traducido', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    await abrirYFijar()
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/password', { new_password: 'clave-fijada-789' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Contraseña fijada para op@axioma-ia.io. Tendrá que cambiarla al entrar.',
    }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('abrir "Fijar contraseña" cierra la edición (un modal a la vez)', async () => {
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Fijar contraseña' }))
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: /Fijar la contraseña/ })).toBeInTheDocument()
  })
})
```

(Con un diálogo abierto, `#root` queda inert. En jsdom `inert` no bloquea `fireEvent`, y el segundo test lo aprovecha: prueba la exclusión, no el inert. Si el `abrir*` no cerrara la edición, habría 2 diálogos.)

- [ ] **Step 2: Rojo**

Run: `cd <worktree>/frontend && npx vitest run src/components src/pages/admin src/store src/api`

Expected:
- Dialogo: `onCerrar` llamado 1 vez.
- MiCuentaModal: no hay diálogo "Cambiá tu contraseña" ni botón "Cerrar sesión".
- RequireAuth: `Failed to resolve import "./RequireAuth"`.
- store: la marca queda en `true`.
- client: `setStateMock` no llamado.
- FijarPasswordModal: falla el import.
- AdminUsers: no hay botón "Fijar contraseña".

- [ ] **Step 3: Implementación mínima**

1. `lib/useCerrarConEscape.js`: `if (e.key === 'Escape') onCerrarRef.current()` pasa a `if (e.key === 'Escape') onCerrarRef.current?.()`. Comentario: "`null` = diálogo no cerrable (U34)".

2. `components/Dialogo.jsx`:
   - firma: se agrega `cerrable = true`;
   - `useCerrarConEscape(onCerrar)` pasa a `useCerrarConEscape(cerrable ? onCerrar : null)`;
   - comentario de cabecera: "- `cerrable={false}` (cambio obligatorio, U34): Escape no cierra; el modal no dibuja botón de cerrar".

3. `components/MiCuentaModal.jsx`:
   - firma `MiCuentaModal({ onCerrar, obligatorio = false })`;
   - `const logout = useJaxStore((s) => s.logout)`;
   - `const addToast = useJaxStore((s) => s.addToast)`;
   - en `MENSAJES`, agregar `password_igual_a_la_actual: t.myAccountSameAsCurrent`;
   - en `enviar`, después de `await cambiarMiPassword(actual, nueva)`: `if (obligatorio) { addToast?.({ type: 'success', message: t.forcedChangeDone }); return }` antes de `setHecho(true)`. El store apaga la marca y `RequireAuth` desmonta el diálogo;
   - `<Dialogo idTitulo="mi-cuenta-titulo" titulo={obligatorio ? t.forcedChangeTitle : t.myAccount} ... cerrable={!obligatorio} onCerrar={onCerrar}>`;
   - el `<p>` de subtítulo muestra `obligatorio ? t.forcedChangeIntro : t.myAccountChangePassword`;
   - el botón Cancelar se reemplaza por:

```jsx
              {obligatorio
                ? <button type="button" onClick={logout} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-peligro transition-colors">{t.logout}</button>
                : <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>}
```

   El bloque `{hecho && (...Cerrar...)}` no se alcanza en modo obligatorio. `addToast` puede no estar en los mocks viejos: por eso va con `?.`.

4. Crear `components/RequireAuth.jsx`:

```jsx
import { Navigate } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import MiCuentaModal from './MiCuentaModal'

// Puerta de la app (movida de App.jsx, 2026-09-15). Con must_change_password
// (el admin fijó la contraseña, Ruling U34) la app NO se monta: sólo el cambio
// obligatorio, no cerrable. El backend niega todo lo demás de todas formas
// (403 cambio_de_password_requerido); esto evita pedirlo.
export default function RequireAuth({ children }) {
  const token = useJaxStore((s) => s.token)
  const user = useJaxStore((s) => s.user)
  const sessionRestoring = useJaxStore((s) => s.sessionRestoring)
  if (sessionRestoring) return null
  if (!token) return <Navigate to="/login" replace />
  if (user?.must_change_password) {
    return <div className="h-dvh bg-fondo"><MiCuentaModal obligatorio onCerrar={() => {}} /></div>
  }
  return children
}
```

   En `App.jsx`: borrar `function RequireAuth(...) {...}` y agregar `import RequireAuth from './components/RequireAuth'`.

5. `store/useJaxStore.js`, `cambiarMiPassword`: `.then(({ data }) => { set({ token: data.access_token }) })` pasa a:

```js
      .then(({ data }) => {
        // U34: el cambio apaga la marca en el backend; acá también, y
        // RequireAuth vuelve a montar la app.
        const user = get().user
        set({ token: data.access_token, user: user ? { ...user, must_change_password: false } : user })
      })
```

6. `api/client.js`: antes de `const api = axios.create(`, agregar `const CAMBIO_REQUERIDO = 'cambio_de_password_requerido'`. En el interceptor de respuesta, al principio del manejador de error:

```js
    // U34: la sesión es válida pero el admin fijó la contraseña. Sin refresh
    // ni reintento: se prende la marca y RequireAuth muestra el cambio.
    if (err.response?.status === 403 && codigoDe(err) === CAMBIO_REQUERIDO) {
      const { user } = useJaxStore.getState()
      if (user) useJaxStore.setState({ user: { ...user, must_change_password: true } })
      return Promise.reject(err)
    }
```

7. Crear `components/admin/FijarPasswordModal.jsx`:

```jsx
import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import PasswordInput from '../PasswordInput'
import Dialogo from '../Dialogo'
import { problemaDePassword } from '../../lib/reglasPassword'

// Fijar la contraseña de otro usuario (2026-09-15, decisiones de Fernando que
// revierten U2). La regla es la única (lib/reglasPassword.js = backend). El
// POST, el toast y el cierre son del padre (onFijar).
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1'

export default function FijarPasswordModal({ usuario, onFijar, onCerrar }) {
  const { t } = useI18n()
  const [nueva, setNueva] = useState('')
  const [confirmar, setConfirmar] = useState('')
  const [error, setError] = useState('')
  const [guardando, setGuardando] = useState(false)

  async function enviar(e) {
    e.preventDefault()
    setError('')
    const problema = problemaDePassword(nueva)
    if (problema === 'corta') { setError(t.resetPasswordShort); return }
    if (problema === 'larga') { setError(t.resetPasswordLong); return }
    if (nueva !== confirmar) { setError(t.resetPasswordMismatch); return }
    setGuardando(true)
    try {
      await onFijar(nueva)
    } finally {
      setGuardando(false)
    }
  }

  return (
    <Dialogo idTitulo="fijar-password-titulo" titulo={t.adminSetPasswordTitle(usuario.email)} claseTitulo="text-sm font-semibold text-texto mb-1" onCerrar={onCerrar}>
      <p className="text-xs text-texto-tenue mb-4">{t.adminSetPasswordHint}</p>
      <form onSubmit={enviar} className="space-y-3">
        <div>
          <label htmlFor="fijar-nueva" className={ETIQUETA}>{t.resetPasswordLabel}</label>
          <PasswordInput id="fijar-nueva" value={nueva} onChange={(e) => setNueva(e.target.value)} autoComplete="new-password" required className={CAMPO} />
        </div>
        <div>
          <label htmlFor="fijar-confirmar" className={ETIQUETA}>{t.resetPasswordConfirm}</label>
          <PasswordInput id="fijar-confirmar" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} autoComplete="new-password" required className={CAMPO} />
        </div>
        <div role="status" className={error ? 'text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2' : undefined}>{error || null}</div>
        <div className="flex gap-2 justify-end pt-2">
          <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
          <button type="submit" disabled={guardando} className="px-4 py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
            {guardando ? t.adminSetPasswordSubmitting : t.adminUserSetPassword}
          </button>
        </div>
      </form>
    </Dialogo>
  )
}
```

   Nota para el test del diálogo: el primer test llama `llenar('', '')` y clica enviar. Con `required` el submit no dispara en navegador; en jsdom `fireEvent.click` sí envía, y el error "Mínimo 8" aparece. El test sólo mira el título y el aviso, así que el resultado no lo afecta.

8. `pages/admin/AdminUsers.jsx`, por delta sobre el archivo de la etapa 5:
   - import `FijarPasswordModal`;
   - estado `const [fijandoPassword, setFijandoPassword] = useState(null)`;
   - cada `abrir*` existente agrega `setFijandoPassword(null)`;
   - nuevo `abrirFijarPassword(u)`, que pone en null los demás (alta, edición, historial y baja de la etapa 5) y después `setFijandoPassword(u)`;
   - handler:

```jsx
  async function fijarPassword(nueva) {
    const u = fijandoPassword
    try {
      await api.post(`/admin/users/${u.user_id}/password`, { new_password: nueva })
      setFijandoPassword(null)
      avisarExito(t.adminPasswordSetDone(u.email))
    } catch (err) {
      avisarError(err)
    }
  }
```

   - después del botón de "Enviar enlace": `<button onClick={() => abrirFijarPassword(u)} className={ACCION_NEUTRA}>{t.adminUserSetPassword}</button>`;
   - junto a los otros modales: `{fijandoPassword && <FijarPasswordModal usuario={fijandoPassword} onFijar={fijarPassword} onCerrar={() => setFijandoPassword(null)} />}`.

9. i18n. En `es.js`, después de `adminResetLinkSent`:

```js
  // Fijar contraseña por admin y cambio obligatorio (2026-09-15, U34)
  adminUserSetPassword: 'Fijar contraseña',
  adminSetPasswordTitle: (email) => `Fijar la contraseña de ${email}`,
  adminSetPasswordHint: 'Sus sesiones abiertas se cierran y tendrá que cambiarla en su próximo inicio de sesión.',
  adminSetPasswordSubmitting: 'Guardando…',
  adminPasswordSetDone: (email) => `Contraseña fijada para ${email}. Tendrá que cambiarla al entrar.`,
  forcedChangeTitle: 'Cambiá tu contraseña',
  forcedChangeIntro: 'Un administrador fijó tu contraseña. Para seguir, elegí una nueva.',
  forcedChangeDone: 'Contraseña cambiada. Ya podés seguir.',
  myAccountSameAsCurrent: 'La nueva contraseña tiene que ser distinta de la que te dieron.',
```

   y en `adminAuditActions`, `password_set_by_admin: 'Contraseña fijada por un admin',`.

   En `en.js`:

```js
  // Password set by an admin and forced change (2026-09-15, U34)
  adminUserSetPassword: 'Set password',
  adminSetPasswordTitle: (email) => `Set the password of ${email}`,
  adminSetPasswordHint: 'Their open sessions are closed and they will have to change it at their next sign-in.',
  adminSetPasswordSubmitting: 'Saving…',
  adminPasswordSetDone: (email) => `Password set for ${email}. They will have to change it when signing in.`,
  forcedChangeTitle: 'Change your password',
  forcedChangeIntro: 'An administrator set your password. To continue, choose a new one.',
  forcedChangeDone: 'Password changed. You can continue.',
  myAccountSameAsCurrent: 'The new password must be different from the one you were given.',
```

   y `password_set_by_admin: 'Password set by an admin',`.

   Si existe un test de paridad de claves es/en, cubre esto; si no, verificarlo con `node -e` comparando `Object.keys`.

- [ ] **Step 4: Verde**
  - `npx vitest run`: piso actual + 14 (1 + 3 + 2 + 1 + 2 + 3 + 2), 0 fallidos.
  - `npm run build` OK.
  - El escaneo de contraste de todo `src` sigue verde con los componentes nuevos.
  - Mutaciones que tienen que caer:
    - `cerrable` ignorado;
    - la rama de `RequireAuth` quitada;
    - el interceptor sin la rama;
    - el store sin apagar la marca;
    - `abrirFijarPassword` sin cerrar la edición.
- [ ] **Step 5: Piso de vitest** medido, con su comentario.
- [ ] **Step 6: Claro/oscuro y es/en a mano** con vite en `127.0.0.1:5174` contra el backend de tests. Nunca contra producción: si hace falta sesión real, esto queda para la verificación en vivo (U23). Revisar el modal del admin y el diálogo obligatorio.
- [ ] **Step 7: Commit**

```bash
git -C <worktree> add frontend/src .github/workflows/policy.yml
git -C <worktree> commit -m "feat(ui): fijar contraseña desde Usuarios; cambio obligatorio no cerrable en lugar de la app"
```

---

### Task 5: Prueba de carga (gate de merge, U29)

**Files:**
- Create (scratchpad, NO en el repo): `carga_fijar_password_test.py`, `carga-fijar-password.md`, `carga-fijar.json`

**Cómo:** es el mismo arnés que `carga_etapa5_test.py`:
- pytest dentro de un worktree de scratch fijado al HEAD de la Task 4;
- `conftest.py` fuerza `jax_memory_test` y aísla el sello;
- `httpx.AsyncClient` sobre `ASGITransport` en el event loop del portal del fixture `client`;
- SMTP simulado a 150 ms donde haga falta;
- `close_user`/`close_user_streams` parchados con contador en `auth.conexiones` Y en `users_mod`, que importa `_cortar_conexiones` por nombre;
- el sello verificado intacto por mtime antes y después;
- nunca `/etc/jax/.env`.

**Escenarios (peor caso, con números):**
- **A · El camino caliente, antes y después.** `GET /api/auth/me` y `GET /api/facets` con un usuario sin la marca, a c=1/10/50: rps, p50/p95/p99. Se corre el mismo escenario sobre el master base (sin la columna) y sobre el HEAD. `verificar_sesion` corre en CADA request, así que esto es lo que más importa. Criterio: p95 del HEAD ≤ p95 base + 10 %. Si no se cumple, se para y se analiza antes de seguir.
- **B · El 403 con la marca.** Usuario con la marca, `GET /api/facets` a c=10/50/100: rps y p95. Tiene que costar lo mismo que un 401 (una consulta por PK).
- **C · El endpoint.** `POST /users/{id}/password` a c=1/10/20 sobre usuarios distintos. Lo domina bcrypt 12 (~150 ms). Anotar dónde satura; se espera en el ejecutor de 32 hilos, como en la etapa 4.
- **D · La carrera sobre el mismo usuario.** K=10/20 fijaciones simultáneas × 5 rondas. Todas dan 200 y quedan serializadas por la fila. Invariantes:
  - `token_version` final = inicial + K;
  - auditorías = K;
  - la marca queda en TRUE;
  - el hash final corresponde a UNA de las K contraseñas (login con cada una: exactamente una da 200);
  - 0 respuestas 5xx y 0 errores 1213.
- **E · Mezcla sobre el mismo usuario** ×20 rondas, todo en paralelo: fijar, PUT (rol y estado), revoke-sessions, unlock, reset-link, forgot-password, `/reset-password` con un token vivo, Mi cuenta con la sesión del usuario y, en la última ronda, la baja. Invariantes:
  - 0 5xx y 0 1213;
  - `token_version` = suma de los que la suben;
  - ningún enlace pendiente vivo después de una fijación que confirmó más tarde que su creación;
  - si la baja ganó, todo lo posterior da 404;
  - auditoría = éxitos.
- **F · Dos superadmins cruzados:** A le fija a B y B le fija a A ×20. Una de las dos puede dar 401/403, porque la primera en confirmar corta la sesión de la otra y la deja con la marca. Nunca 5xx ni 1213. Anotar el reparto.
- **G · EXPLAIN sobre las consultas REALES** en `jax_memory_test`:
  - `SQL_ESTADO_DE_SESION` → `const/PRIMARY`;
  - `DELETE FROM password_reset_tokens WHERE user_id = … AND used = FALSE` (`EXPLAIN DELETE`) → `ref` sobre el índice de `user_id`;
  - el UPDATE por PK;
  - el SELECT del login por email → `const` sobre el UNIQUE.

  Buscar `Using filesort` y `Using temporary`.

- [ ] **Step 1:** Escribir el arnés y correrlo: `cd <scratch-worktree>/backend && CARGA_SALIDA=<scratchpad>/carga-fijar.json /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest <scratchpad>/carga_fijar_password_test.py -q -s -p no:cacheprovider`. El archivo se copia al `tests/` del worktree de scratch, NUNCA al de la rama.
- [ ] **Step 2:** Escribir `carga-fijar-password.md` con los números, el reparto de F, los EXPLAIN tal cual, la saturación (con cuántos usuarios simultáneos degrada) y el veredicto para 2-10 admins.
- [ ] **Step 3:** Pasar el resumen al ledger. Si algo falla (5xx, 1213, invariante roto o A fuera del criterio), no hay merge: se abre una ronda de arreglos y la carga se repite sobre el HEAD nuevo.

---

### Task 6: PR, CI por headSha, deploy y verificación en vivo con Fernando

- [ ] **Step 1: Suite local completa** (con DB, sin DB y vitest), igual a los pisos del archivo. Dos corridas.
- [ ] **Step 2: Review final** (opus) de BASE..HEAD, con el brief: U34, deny-by-default, orden de bloqueos, secretos y el análisis de concurrencia de abajo. Sus hallazgos se arreglan ANTES del PR (no se difieren).
- [ ] **Step 3: PR.** Título "Admin usuarios · fijar contraseña por admin + cambio obligatorio (U34)". El cuerpo lleva: las decisiones de Fernando (revierten U2), los números de la Task 5 y el plan del deploy.
- [ ] **Step 4: Gate por headSha.**
  - `gh pr checks <N> --repo fjruizhn/jax-platform` sin nada fuera de `SUCCESS`.
  - `gh pr view <N> --json headRefOid -q .headRefOid` = `git ls-remote origin refs/heads/feat/admin-usuarios-fijar-password` = HEAD local.
  - Recién ahí `gh pr merge <N> --merge --match-head-commit <sha>`.
- [ ] **Step 5: Línea base antes del deploy.**
  - health 200;
  - hash servido;
  - `POST /api/admin/users/999999999/password` sin token: anotar el código (hoy la ruta no existe); después tiene que dar 401;
  - `SELECT COUNT(*) FROM jax_users` (sólo lectura).
- [ ] **Step 6: Deploy** con `deploy-fijar-password.sh` (scratchpad), copia de `deploy-etapa5.sh` con estos cambios:
  0. **Dump ANTES del reinicio.** El reinicio aplica el ALTER sobre `jax_users`. Dump de `jax_users password_reset_tokens user_admin_audit` con `--single-transaction`, conteo de filas por tabla contra la DB (tienen que coincidir), nombre `usuarios-pre-fijar-password-<fecha>.sql`.
     **Restauración probada** (Principio VI): el procedimiento de la etapa 2, Task 6, Step 1, sobre una base descartable. Los conteos restaurados tienen que coincidir. Sin eso no hay reinicio.
  1. `pull --ff-only` del checkout de producción en master, restart, health 200, journal sin tracebacks.
  2. Esquema (SELECT de sólo lectura):
     - `COLUMN_TYPE` de `must_change_password` = `tinyint(1)`, `IS_NULLABLE` = `NO`, `COLUMN_DEFAULT` = `0`;
     - `SELECT COUNT(*) FROM jax_users WHERE must_change_password` = 0 (nadie quedó encerrado);
     - `EXPLAIN` de `SQL_ESTADO_DE_SESION` con el id de Fernando → `const/PRIMARY`.
  3. Frontend: build, backup en la VM verificado IDÉNTICO, rsync en dos saltos con `--exclude .user.ini`.
  4. Smoke:
     - md5 servido = build;
     - la ruta nueva sin token da 401 (antes, el código de la línea base);
     - `/api/auth/me` sin token da 401/403;
     - Login DOM OK.
- [ ] **Step 7: Verificación en vivo con Fernando** (U23: sin credenciales scriptadas; lo hace Fernando, con un usuario de prueba):
  1. Crear `verificacion-fijar@example.invalid` e iniciar sesión con él en una ventana privada.
  2. Desde Admin → Usuarios, "Fijar contraseña" (modal con nueva y confirmar, en claro y oscuro, en es y en en). Toast traducido. En la ventana privada, el request siguiente la saca al login.
  3. Entrar con la contraseña fijada. Aparece "Cambiá tu contraseña": sin Cancelar, Escape no cierra, el fondo no responde a Tab, no se ve la app. Probar `/admin` a mano: sigue el diálogo.
  4. Intentar la misma contraseña: aparece el mensaje traducido. "Cerrar sesión" funciona. Volver a entrar y cambiarla de verdad: la app aparece y hay toast.
  5. Historial del usuario: "Contraseña fijada por un admin" y después "Cambió su contraseña".
  6. Enviarle un enlace (si Fernando da el buzón, U19), y después fijar la contraseña: el enlace viejo dice "enlace inválido".
  7. Un usuario bloqueado (5 intentos fallidos): después de fijarle la contraseña entra al instante.
  8. Baja del usuario de prueba. En la base (sólo lectura), su `user_admin_audit` tiene todo lo anterior y en ningún `detail` aparece la contraseña.
- [ ] **Step 8: Biblioteca.** Entrada fechada en `/home/fruiz/jax/DEUDA.md` (PR propio en jax) con: sha, `index-*.js`, EXPLAIN tal cual, números de la carga, dump y restauración, los 8 puntos en vivo, y **U2 revertido por decisión de Fernando (2026-09-15)**. Ledger y memoria actualizados. El worktree se borra después del merge.

---

## Autorrevisión (hecha al escribir el plan)

### Cobertura de las decisiones

| Decisión | Dónde se cumple |
|---|---|
| (1) El superadmin fija la contraseña de otro; el enlace sigue | Task 3 (endpoint); Task 4 (botón al lado de "Enviar enlace", que no se toca) |
| (2) Cambio obligatorio en el próximo login | Task 2 (backend: 403 por defecto, WS 4001, SSE 403 y re-verificación); Task 4 (diálogo no cerrable en lugar de la app) |
| U34 columna | Task 1: `BOOLEAN NOT NULL DEFAULT FALSE` por `_COLUMNS`, idempotente |
| U34 lista permitida | `/me`, `/me/password` (dependencia permisiva), `/refresh` (a mano), `/logout` (sin auth). Todo lo demás, 403. Vigilada por recorrido de rutas y búsqueda en el código |
| U34 una sola consulta | La columna va en `SQL_ESTADO_DE_SESION`; los tests existentes de `const/PRIMARY` y de "una sola SELECT en /me" siguen |
| Mi cuenta y `/reset-password` limpian la marca; `/me` la devuelve | Task 2 (también login y `restoreSession`) |
| Endpoint: auto-acción, baja → 404, inactivo permitido | Task 3, con la justificación escrita |
| `{new_password}` con la regla única; bcrypt en hilo antes de la transacción | Task 3, test del orden `HASH → BEGIN(READ COMMITTED) → …` |
| Transacción y UPDATE único (hash, versión, marca, desbloqueo), DELETE de pendientes, auditoría sin secretos, corte después del commit | Task 3 |
| READ COMMITTED y orden usuario → tokens | Task 3, test del orden de bloqueo |
| Frontend: botón, modal sobre `Dialogo`, `PasswordInput`, regla compartida, exclusión mutua, toast traducido; diálogo obligatorio con prop mínima `cerrable`; 403 → diálogo | Task 4 |
| Tokens, i18n es/en, accesibilidad | Task 4 |
| Proceso: SDD, TDD, pisos, reviews, carga, dump antes de la migración, verificación en vivo | Tasks 1-6 |

### Concurrencia

`fijar_password` toma: conjunto de superadmins (`FOR UPDATE`, en orden de `user_id`) → fila destino (PK) → UPDATE de la fila → DELETE de tokens pendientes por el índice `user_id` → INSERT de auditoría. Todo en READ COMMITTED: sólo bloqueos de registro, sin huecos (U33).

| Contra | Qué toma el otro | ¿Ciclo? | Resultado |
|---|---|---|---|
| **Baja** | El mismo prefijo (superadmins → usuario → tokens) | No: mismo orden | Se serializan. Si la baja va primero, fijar re-lee con `FOR UPDATE` (lectura actual) y da 404. Si fijar va primero, la baja pasa y borra los pendientes (ya no hay) |
| **PUT rol/estado/email** | Superadmins → usuario | No | Se serializan. Fijar sobre un recién desactivado: permitido, queda inactivo con la marca |
| **revoke-sessions / unlock** | Superadmins → usuario | No | Serializados. Las dos suben o limpian lo mismo; la versión suma las dos |
| **Mi cuenta** | SÓLO la fila del usuario, por PK. Mientras la tiene no pide otra fila de `jax_users` (la auditoría usa su propia fila) | No: Mi cuenta no espera nada que fijar tenga | Si fijar confirma durante el bcrypt de Mi cuenta, Mi cuenta re-lee `token_version`, da 401 y no escribe: queda la contraseña y la marca del admin (test en la Task 3). Si Mi cuenta confirma antes, fijar la pisa después: gana la acción posterior del admin, a propósito |
| **`/reset-password`** | Usuario por PK (REPEATABLE READ, registro) → token por PK (id) | No: los dos van usuario → tokens (U21) | Si fijar confirma antes, el token pendiente ya no existe: el reclamo afecta 0 filas y da 400 `reset_token_usado` (o `reset_token_invalido` si la lectura inicial ya no lo encontró). Si el reset confirma antes, fijar pisa la contraseña y vuelve a poner la marca |
| **`send_reset_link` / forgot-password** | Usuario por PK → DELETE+INSERT de tokens, en READ COMMITTED | No: usuario → tokens, sin pedir el conjunto de superadmins | Si el enlace va antes, fijar borra el token recién creado y el correo que sale después del commit lleva un enlace muerto (aceptable: la acción posterior del admin gana; mismo caso ya aceptado con la baja, U31). Si fijar va antes, el enlace es nuevo y válido; completarlo apaga la marca con una contraseña que eligió la persona (correcto) |
| **Login** | Lectura sin bloqueo + UPDATE por PK en autocommit | No | Un login con la contraseña vieja que leyó antes del commit emite tokens con la versión vieja, que mueren en el request siguiente (401), igual que hoy con Mi cuenta |

Cierre del análisis:
- **Superadmin destino:** si el destino está en el conjunto que fijar recorre y además tiene su fila tomada por Mi cuenta, fijar espera en esa fila sin que Mi cuenta espere nada: no hay ciclo.
- **Dos superadmins cruzados:** los dos recorren el conjunto en el mismo orden, el segundo espera sin tener nada tomado. Por eso la carga F es de corrección (401/403 por el corte), no de deadlock.

### Riesgos

1. **Camino caliente.** `verificar_sesion` corre en cada request HTTP, en el handshake del WS y en las re-verificaciones: una columna más y una rama. Riesgo bajo (misma fila, misma PK), pero se MIDE (Task 5A, criterio p95 ≤ base + 10 %).
2. **Una ruta que se salga de `get_current_user`.** Si una ruta autentica a mano (como hoy `/refresh`) sin la dependencia, el recorrido de rutas no la ve. La búsqueda en el código (`admite_cambio_pendiente=True`) cubre el permiso, pero no una ruta que llame `decode_token` y se saltee `verificar_sesion`, algo que ya hoy sería un bypass de la etapa 2. La review final lo verifica con grep de `decode_token(`: sólo en `middleware.py`, `api/auth.py` (refresh) y `main.py` (WS).
3. **Encierro accidental.** Si un bug dejara la marca en TRUE para todos, nadie (tampoco los admins) podría usar la app, salvo Mi cuenta. Mitigación:
   - `DEFAULT FALSE`, y el SELECT del deploy exige 0 marcados;
   - Mi cuenta siempre está disponible;
   - el dump previo.
4. **Mismo valor que el admin** (P1). Sin el rechazo, el cambio obligatorio se cumple con la misma contraseña que el admin conoce. Queda implementado según la recomendación; si Fernando dice que no, se quitan una línea y un test.
5. **Correo con enlace muerto** si "Enviar enlace" y "Fijar" se cruzan. Es aceptable y consistente con U31.
6. **Frontend fuera de sincronía.** Si la marca llega por un 403 mientras hay un modal de admin abierto, `RequireAuth` desmonta Admin: se pierde lo escrito en ese modal. Es aceptable: la sesión ya no puede hacer nada más que cambiar la contraseña.
7. **Anclas.** El plan se escribió sobre `d3d492d`, sin la Task 4 de la etapa 5: anclas de AdminUsers, tests e i18n a re-verificar al despachar.

### Qué tiene que cubrir la verificación en vivo

Task 6, Step 7. Los puntos de riesgo son 3 (no cerrable, fondo inert, `/admin` a mano), 4 (misma contraseña y logout), 6 (enlace viejo muerto) y 7 (desbloqueo). Todos en claro/oscuro y es/en, y la auditoría sin secretos.

### Nombres

`must_change_password`, `CAMBIO_DE_PASSWORD_REQUERIDO`, `RUTAS_CON_CAMBIO_PENDIENTE`, `admite_cambio_pendiente`, `get_current_user_con_cambio_pendiente`, `FijarPasswordRequest`, `fijar_password`, `password_set_by_admin`, `FijarPasswordModal({usuario, onFijar, onCerrar})`, `MiCuentaModal({onCerrar, obligatorio})`, `Dialogo({…, cerrable})` y `RequireAuth` son iguales en tests, código e Interfaces.

**Placeholders de ejecución:** `<worktree>`, `<N>`, `<fecha>` y los conteos medidos.

## Preguntas abiertas

- **P1 · ¿Rechazar que la contraseña nueva del cambio obligatorio sea igual a la que fijó el admin?**
  Recomendación: **sí, sólo con la marca activa** (`400 password_igual_a_la_actual`). Sin esto, la decisión (2) se cumple de nombre y el admin sigue conociendo la contraseña. Cuesta una comparación de strings. El plan ya lo trae (Task 2); si Fernando dice que no, se quitan el `if` y su test.
- **P2 · ¿El cambio obligatorio sigue pidiendo la contraseña actual?**
  Recomendación: **sí**. La persona acaba de entrar con ella, así que no le cuesta nada. Si alguien consigue un access token con la marca, sin la actual no puede fijar una contraseña propia. Además comparte el límite de intentos del login. El plan la mantiene.
