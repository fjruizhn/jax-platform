# Administración de usuarios · Etapa 2 — Sesiones que se cortan de verdad (`token_version`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que desactivar a un usuario, bajarle el rol o invalidar su versión de token corte su acceso en el request siguiente (HTTP, `/refresh` y WebSocket), leyendo el estado y el rol de la base y no del JWT.

**Architecture:** Una migración idempotente agrega `jax_users.token_version`. Access y refresh token llevan `tv`. `auth/middleware.py` expone `verificar_sesion(payload, tipo)` (async): lee `status, role, token_version` por clave primaria y rechaza con 401 si el usuario no existe, no está `active` o `tv` no coincide. `get_current_user` pasa a ser una dependencia async que la usa, y el rol sale de la base. `/refresh` y el handshake del WebSocket llaman a la misma función. Antes de cambiar el middleware se migran los tests existentes que firman tokens para identidades inventadas, para que la suite siga verde por las razones correctas.

**Tech Stack:** FastAPI, aiomysql, python-jose, pytest (+pytest-asyncio), MariaDB (11.8 en CI, 12.3.3 en producción).

**Spec:** `/home/fruiz/jax-platform/docs/superpowers/specs/2026-09-12-administracion-usuarios-design.md` (§3.2, §5, §6 punto 2).

## Global Constraints

- **Repo — CORREGIDO 2026-09-14:** se trabaja en el WORKTREE `/home/fruiz/worktrees/jax-platform-etapa2` (rama `feat/admin-usuarios-etapa2-sesiones`, creada desde `origin/master` `bfab4de`), NUNCA en `/home/fruiz/jax-platform`: de ese checkout sirve el servicio `jax-platform`. En las Tasks 0-5, donde un comando dice `/home/fruiz/jax-platform/...`, se usa el worktree; el Python es `/home/fruiz/jax-platform/backend/.venv/bin/python` (el worktree no tiene `.venv`) y `frontend/node_modules` es un symlink al del checkout. Solo la Task 6 (despliegue, después del merge) usa `/home/fruiz/jax-platform`. Siempre `git -C <ruta>`.
- **TDD obligatorio**, con el rojo visto por el motivo que dice cada paso. Donde un test es un **control** que ya pasa hoy, el plan lo dice en el mismo paso: un control que no falla no valida el cambio, solo impide que el refactor pierda algo que ya andaba.
- **Backend tests:** `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest ...`. Con `client` → `jax_memory_test`. Helpers async desde un test: `client.portal.call(fn, *args)` (sin kwargs: usar `functools.partial`).
- **Ningún test modifica `user_id=1`** (superadmin sembrado): muchos archivos firman tokens para él con `tv=0`, y desde esta etapa un `token_version` distinto de 0 en esa fila rompería la suite entera.
- **Pisos exactos** en `.github/workflows/policy.yml`: `PISO_PASSED` (job con DB), `JAX_CI_MIN_PASSED` (job sin DB) y `numPassedTests` (vitest). Leer su valor ACTUAL en el archivo antes de tocarlo. Medidos el 2026-09-14 en `bfab4de`: **611 / 301 / 125** (el 463 / 240 / 92 original quedó viejo tras la etapa 1 y el lote de deuda del 09-14). Cada tarea sube los pisos con el número **MEDIDO** y un comentario con el porqué.
- **P10:** `except Exception` con `# fail-soft: <razón>` en la misma línea. **BackgroundTasks:** solo `add_safe_task`. **Async:** nada bloqueante; la consulta nueva va por aiomysql.
- **LAS CUATRO:** la consulta del middleware va por `PRIMARY` (verificada con `EXPLAIN` en un test y en producción). **Sin caché de entrada** (spec §3.2: sin medición no hay caché). Prueba de carga de un endpoint autenticado ANTES y DESPUÉS, con el número escrito en la Biblioteca (`/home/fruiz/jax/DEUDA.md`).
- **Compatibilidad al desplegar:** un token emitido antes del despliegue no trae `tv` y vale como `tv=0` (spec §3.2): nadie queda afuera.
- **`tenant_id` sigue saliendo del token** (el spec solo pide rol, estado y versión desde la base). Además es intencional para los tests: los de chat usan tenants NO numéricos a propósito, porque `api/chat.py` solo entra al camino de memoria semántica cuando `user_id` **y** `tenant_id` parsean a entero (`api/chat.py`, bloque "Memoria semántica").
- **Migraciones:** idempotentes en `backend/db/migrations.py` (lista `_COLUMNS`), nada de ALTER a mano.
- **`/refresh` no rota la cookie de refresh:** emite un access nuevo con el rol y la versión actuales. Rotarla convertiría los 7 días fijos en una sesión deslizante sin fin, y la versión ya invalida el refresh viejo. Las etapas que suben `token_version` para la sesión actual (etapa 4, Mi cuenta) emiten las dos cookies con `_emitir_tokens`.
- **Qué NO es de esta etapa:** subir `token_version` en cambio de rol/estado, "cerrar sesiones" y auditoría (etapa 3); contraseñas (etapa 4); baja (etapa 5). Esta etapa construye el mecanismo; el corte por desactivación o degradación ya funciona desde acá porque el middleware lee `status` y `role` de la base.
- Commits sin `--no-verify` (no se toca `backend/db/seed.py`). **YAGNI:** nada de spec §4.
- **Cierre:** PR → CI verde por `headSha` → despliegue backend **y frontend** (desde 2026-09-14 la etapa incluye la Task 4b; deploy con `--exclude .user.ini` en los dos saltos del rsync, aaPanel lo deja inmutable) → verificación en vivo (§5) → número de carga en la Biblioteca.

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/db/migrations.py` | Modificar | `_COLUMNS`: `jax_users.token_version INT NOT NULL DEFAULT 0` |
| `backend/auth/jwt.py` | Modificar | `tv` en access y refresh |
| `backend/auth/models.py` | Modificar | `AuthUser.token_version: int = 0` |
| `backend/auth/middleware.py` | Modificar | `verificar_sesion` async por PK; `get_current_user` async |
| `backend/api/auth.py` | Modificar | login emite `tv`; `_emitir_tokens`; `/refresh` verifica contra la base |
| `backend/main.py` | Modificar | handshake del WS con `verificar_sesion` |
| `backend/tests/identidades.py` | Crear | identidades reales para tests (fábrica, borrado, tokens) |
| `backend/tests/conftest.py` | Modificar | fixture `usuarios` |
| 14 archivos de `backend/tests/` | Modificar | dejan de firmar tokens para ids inventados |
| `backend/tests/test_sesiones_token_version.py` | Crear | tests de la etapa |
| `.github/workflows/policy.yml` | Modificar | pisos |

## Interfaces

**Consumes (etapa 1):** nada de la etapa 1 se usa acá. `tests/test_smtp_endpoints.py` ya firma con `user_id=1` real y crea su operador real, así que sigue valiendo.

**Produces (las usan las etapas 3, 4 y 5):**

```python
# backend/auth/jwt.py
def create_access_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str   # payload["tv"]
def create_refresh_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str  # payload["tv"]

# backend/auth/models.py
class AuthUser(BaseModel): user_id: str; tenant_id: str; role: str; email: Optional[str] = None; token_version: int = 0

# backend/auth/middleware.py
SESION_INVALIDA = "sesion_invalida"
SQL_ESTADO_DE_SESION = "SELECT status, role, token_version FROM jax_users WHERE user_id = %s"
async def verificar_sesion(payload: dict, tipo: str) -> AuthUser   # tipo: "access" | "refresh"; 401 sesion_invalida
async def get_current_user(credentials = Depends(bearer)) -> AuthUser
def require_superadmin(user: AuthUser = Depends(get_current_user)) -> AuthUser   # sin cambios

# backend/api/auth.py
def _emitir_tokens(response: Response, user_id: str, tenant_id: str, role: str, token_version: int) -> str
    # setea la cookie refresh_token (httponly, samesite=lax, 7 días) y devuelve el access

# backend/tests/identidades.py
async def sql(consulta: str, args: tuple = (), fetch: bool = False)   # filas si fetch, si no lastrowid
async def crear_usuario(role="operator", status="active", password=None, token_version=0) -> tuple[int, str]  # (user_id, email)
async def borrar_usuario(user_id: int) -> None
def uid(client, etiqueta: str, role: str = "operator") -> str
def token_de(client, etiqueta: str, role: str = "operator", tenant_id: str = "1") -> str
def cabeceras(client, etiqueta: str, role: str = "operator", tenant_id: str = "1") -> dict
def token_para(user_id, role="operator", tv=0, tenant_id="1", tipo="access") -> str
def auth(token: str) -> dict                                            # {"Authorization": "Bearer ..."}

# backend/tests/conftest.py
@pytest.fixture usuarios(client) -> crear(**kw) -> (user_id, email)     # borra lo creado al terminar
```

---

### Task 0: Prueba de carga ANTES del cambio (línea de base)

**Files:** ninguno. Resultado anotado para la Task 6.

- [ ] **Step 1: Token de corta vida para user_id=1 (no se guarda en ningún archivo)**

```bash
cd /home/fruiz/jax-platform/backend
set -a; . /etc/jax/.env; set +a
TOKEN=$(.venv/bin/python -c "from auth.jwt import create_access_token; print(create_access_token('1', '1', 'superadmin'))")
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/auth/me
```
Expected: `200` (el access vive 15 minutos). Si el código de producción ya tiene la etapa 2, esta tarea no aplica.

- [ ] **Step 2: Medir `/api/auth/me` (endpoint autenticado liviano: una consulta hoy, dos después)**

```bash
for c_n in "1 200" "10 500" "50 1000"; do set -- $c_n
  python3 /home/fruiz/jax/scripts/load_test.py --url http://127.0.0.1:8080/api/auth/me -c $1 -n $2 -H "Authorization: Bearer $TOKEN"
done
```
Anotar para cada concurrencia: peticiones, errores, rps, p50, p95, p99, con la hora CST. Esto es la línea de base de la Task 6.

---

### Task 1: Migración `token_version` y `tv` en los tokens

**Files:**
- Modify: `backend/db/migrations.py` (lista `_COLUMNS`, después de la tupla de `locked_until`)
- Modify: `backend/auth/jwt.py`
- Modify: `backend/auth/models.py`
- Create: `backend/tests/test_sesiones_token_version.py`
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `db.migrations._COLUMNS` (tuplas `(tabla, columna, ALTER)` aplicadas por `run_migrations` si `_column_exists` da falso).
- Produces: `create_access_token(..., token_version=0)`, `create_refresh_token(..., token_version=0)`, `AuthUser.token_version`, columna `jax_users.token_version`.

- [ ] **Step 1: Tests que fallan**

Crear `backend/tests/test_sesiones_token_version.py`:

```python
"""Sesiones que se cortan de verdad (2026-09-12, administración de usuarios, etapa 2).

Antes: get_current_user solo decodificaba el JWT (rol del token) y /refresh
reemitía el access con el rol del refresh token, sin consultar la base.
Desactivar, borrar o bajar de rol NO cortaba la sesión: el usuario seguía
entrando hasta 7 días, y un superadmin degradado seguía siéndolo (spec §1,
hallazgo 1).

Ahora: access y refresh llevan `tv`; cada request lee status, role y
token_version por clave primaria; el rol sale de la base. Un token de antes
del despliegue (sin `tv`) vale como tv=0.
"""
import time

from jose import jwt

from auth.jwt import ALGORITHM, SECRET, create_access_token, create_refresh_token, decode_token


# ------------------------------------------------------------- puros

def test_access_token_lleva_la_version():
    assert decode_token(create_access_token("5", "1", "operator"))["tv"] == 0
    assert decode_token(create_access_token("5", "1", "operator", 3))["tv"] == 3


def test_refresh_token_lleva_la_version():
    payload = decode_token(create_refresh_token("5", "1", "operator", 7))
    assert (payload["tv"], payload["type"]) == (7, "refresh")


# ---------------------------------------------------------- esquema

async def _columna():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' AND COLUMN_NAME = 'token_version'")
            return [tuple(f) for f in await cur.fetchall()]


def test_columna_token_version(client):
    assert client.portal.call(_columna) == [("int", "NO", "0")]
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_sesiones_token_version.py -q`
Expected: los dos puros fallan con `KeyError: 'tv'` (y el segundo además con `TypeError` por el cuarto argumento). `test_columna_token_version` falla con `assert [] == [('int', 'NO', '0')]`.

- [ ] **Step 3: Implementación mínima**

En `backend/db/migrations.py`, en `_COLUMNS`, después de la línea `("jax_users", "locked_until", "ALTER TABLE jax_users ADD COLUMN locked_until DATETIME NULL"),` agregar:

```python
    # Sesiones que se cortan de verdad (2026-09-12, admin usuarios etapa 2):
    # access y refresh llevan `tv`; subir esta columna invalida TODOS los
    # tokens del usuario en el request siguiente. NOT NULL DEFAULT 0 para las
    # filas existentes: un token viejo (sin `tv`) vale como 0 y nadie queda
    # afuera al desplegar.
    ("jax_users", "token_version", "ALTER TABLE jax_users ADD COLUMN token_version INT NOT NULL DEFAULT 0"),
```

Reemplazar `backend/auth/jwt.py` desde `def create_access_token` hasta el final de `create_refresh_token` por:

```python
# `tv` = jax_users.token_version al emitir (2026-09-12, admin usuarios etapa
# 2). El default 0 es el mismo valor con que se leen los tokens emitidos antes
# de que existiera `tv` (spec §3.2), y lo usan los tests que firman para
# user_id=1, cuya versión nunca cambia.
def create_access_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
        "exp": int(time.time()) + ACCESS_EXPIRE_SECONDS,
        "type": "access",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, tenant_id: str, role: str, token_version: int = 0) -> str:
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "tv": int(token_version),
        "exp": int(time.time()) + REFRESH_EXPIRE_SECONDS,
        "type": "refresh",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)
```

En `backend/auth/models.py`, en `class AuthUser`, después de `email: Optional[str] = None` agregar `token_version: int = 0`.

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_sesiones_token_version.py -q`
Expected: `3 passed` (el fixture `client` arranca la app, que corre `run_migrations` y crea la columna en `jax_memory_test`).

- [ ] **Step 5: Pisos medidos**

Job sin DB: `JAX_CI_NO_DB=1 .venv/bin/python -m pytest -q | tail -1` → esperado piso anterior + 2. Job con DB: suite completa → esperado piso anterior + 3. Actualizar `JAX_CI_MIN_PASSED` y `PISO_PASSED` con sus comentarios ("admin usuarios etapa 2, Task 1: 2 puros de `tv` + 1 de esquema").

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/db/migrations.py backend/auth/jwt.py backend/auth/models.py backend/tests/test_sesiones_token_version.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(auth): jax_users.token_version y tv en access/refresh"
```

---

### Task 2: Identidades reales en los tests existentes

Esta tarea no agrega tests: cambia **14 archivos** que hoy firman tokens para ids que no existen en `jax_users` (`"test-keys-pooling-user"`, `"attacker"`, `"test-user-a"`...), más uno que firma para `user_id="1"` con rol `"user"` esperando un 403 (`test_admin_motors_endpoints.py::_user_headers`). Con el middleware de la Task 3, los primeros responderían 401, y el segundo recibiría el rol real de user 1 (superadmin) y su 403 pasaría a 200. Se migran ANTES del middleware, con la suite verde antes y después.

Inventario verificado con `grep -rn create_access_token backend/tests` el 2026-09-12. **No** cambian (siguen siendo válidos con la consulta por PK): los que firman para `"1"` con rol superadmin o con un rol irrelevante para lo que prueban (`test_admin_facet_bindings_endpoints.py`, `test_admin_models_endpoints.py`, `test_pipelines_identity_injection.py`, `test_motors_endpoint.py`, `test_image_http_pooling.py`, `test_grounding_config_revalidation.py`, y `_superadmin_headers` de `test_admin_motors_endpoints.py`), y los que llaman handlers directo con `AuthUser(...)` sin pasar por HTTP (`test_command_ownership.py`, `test_pipeline_ownership.py`, la parte directa de `test_command_path_traversal.py`, el test de carrera de `test_websocket_isolation.py`).

**Files:**
- Create: `backend/tests/identidades.py`
- Modify: `backend/tests/conftest.py` (fixture `usuarios`, al final)
- Modify: `backend/tests/test_keys_http_pooling.py`, `test_pipelines_http_pooling.py`, `test_dashboard_http_pooling.py`, `test_admin_keys_model_source.py`, `test_admin_keys_n1.py`, `test_facet_model_wiring.py`, `test_admin_motors_endpoints.py`, `test_chat_facet_validation.py`, `test_chat_grounding_wiring.py`, `test_shadow_validation.py`, `test_shadow_origin.py`, `test_chat_contract_wrapper.py`, `test_command_path_traversal.py`, `test_websocket_isolation.py`

**Interfaces:**
- Consumes: `create_access_token(..., token_version=0)`, `create_refresh_token(...)` (Task 1), columna `token_version` (Task 1).
- Produces: `tests/identidades.py` y el fixture `usuarios` (ver Interfaces arriba).

- [ ] **Step 1: Medir la suite antes de tocar nada**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest -q 2>&1 | tail -2`
Anotar `passed`/`skipped` (debería coincidir con `PISO_PASSED` de la Task 1). Después de la migración el número tiene que ser EXACTAMENTE el mismo.

- [ ] **Step 2: Crear `backend/tests/identidades.py`**

```python
"""Identidades REALES para los tests que pasan por la autenticación
(2026-09-12, administración de usuarios, etapa 2).

Desde esta etapa, get_current_user consulta jax_users por clave primaria en
cada request: un token firmado para un id inventado ("test-keys-pooling-user")
responde 401, y uno firmado para user_id=1 con rol "operator" recibe el rol
REAL de la base (superadmin). Cada test que se autentica pide acá una fila
verdadera.

- `uid`/`token_de`/`cabeceras`: UNA fila por (etiqueta, rol), creada una vez
  por sesión y reutilizada. No se borra: un id nunca queda colgando de un token
  que otro test todavía usa.
- `crear_usuario`/`borrar_usuario` (y el fixture `usuarios` de conftest.py):
  filas descartables para los tests que cambian estado, rol o versión.
- `tenant_id` sigue saliendo del token. Los tests de chat pasan tenants NO
  numéricos a propósito: api/chat.py solo entra al camino de memoria semántica
  cuando user_id Y tenant_id son enteros.
- user_id=1 (el superadmin sembrado) no se toca desde ningún test.
"""
import secrets
import uuid

import bcrypt

from auth.jwt import create_access_token, create_refresh_token

_CACHE: dict[tuple[str, str], str] = {}


async def sql(consulta, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(consulta, args)
            if fetch:
                return await cur.fetchall()
            return cur.lastrowid


def _hash(password):
    # rounds=4: el costo real (12) no le agrega nada a un test y cuesta ~150 ms.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


async def crear_usuario(role="operator", status="active", password=None, token_version=0):
    email = f"test-u-{uuid.uuid4().hex[:12]}@example.invalid"
    user_id = await sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
        "VALUES (1, %s, %s, %s, %s, %s)",
        (email, _hash(password or secrets.token_urlsafe(12)), role, status, token_version))
    return user_id, email


async def borrar_usuario(user_id):
    await sql("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
    await sql("DELETE FROM jax_users WHERE user_id = %s", (user_id,))


async def _obtener_o_crear(etiqueta, role):
    email = f"test-ident-{etiqueta}-{role}@example.invalid"
    filas = await sql("SELECT user_id FROM jax_users WHERE email = %s", (email,), True)
    if filas:
        user_id = filas[0][0]
        await sql("UPDATE jax_users SET role = %s, status = 'active', token_version = 0 WHERE user_id = %s",
                  (role, user_id))
        return user_id
    return await sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) VALUES (1, %s, %s, %s, 'active')",
        (email, _hash(secrets.token_urlsafe(12)), role))


def uid(client, etiqueta, role="operator"):
    clave = (etiqueta, role)
    if clave not in _CACHE:
        _CACHE[clave] = str(client.portal.call(_obtener_o_crear, etiqueta, role))
    return _CACHE[clave]


def token_de(client, etiqueta, role="operator", tenant_id="1"):
    return create_access_token(uid(client, etiqueta, role), tenant_id, role)


def cabeceras(client, etiqueta, role="operator", tenant_id="1"):
    return auth(token_de(client, etiqueta, role, tenant_id))


def token_para(user_id, role="operator", tv=0, tenant_id="1", tipo="access"):
    fabrica = create_access_token if tipo == "access" else create_refresh_token
    return fabrica(str(user_id), tenant_id, role, tv)


def auth(token):
    return {"Authorization": f"Bearer {token}"}
```

- [ ] **Step 3: Fixture `usuarios` en `backend/tests/conftest.py`**

Agregar al final del archivo:

```python
@pytest.fixture
def usuarios(client):
    """Fábrica de usuarios REALES en jax_users que se borran al terminar el
    test (2026-09-12, admin usuarios etapa 2): crear(**kw) -> (user_id, email),
    con los kwargs de tests/identidades.py::crear_usuario. Pide `client`, así
    que en el job sin DB se salta sola (Regla 1)."""
    from functools import partial

    from tests.identidades import borrar_usuario, crear_usuario

    creados = []

    def crear(**kw):
        user_id, email = client.portal.call(partial(crear_usuario, **kw))
        creados.append(user_id)
        return user_id, email

    yield crear
    for user_id in creados:
        client.portal.call(borrar_usuario, user_id)
```

- [ ] **Step 4: Migrar los 14 archivos con un script que falla si algo no calza**

Guardar como `$SCRATCH/migrar_identidades.py` (en el scratchpad de la sesión, NO en el repo) y correrlo con `cd /home/fruiz/jax-platform/backend && .venv/bin/python $SCRATCH/migrar_identidades.py`:

```python
"""Migra los tests que firman tokens para identidades inventadas a
identidades reales (tests/identidades.py). Cada reemplazo declara cuántas
veces tiene que aplicar: si el árbol cambió, el script aborta sin escribir."""
import re
import sys
from pathlib import Path

T = Path("/home/fruiz/jax-platform/backend/tests")
cambios = {}


def leer(nombre):
    return cambios.get(nombre) or (T / nombre).read_text()


def reemplazar(nombre, viejo, nuevo, veces=1):
    texto = leer(nombre)
    n = texto.count(viejo)
    if n != veces:
        sys.exit(f"{nombre}: {viejo[:60]!r} aparece {n} veces, se esperaban {veces}")
    cambios[nombre] = texto.replace(viejo, nuevo)


def importar(nombre, linea):
    texto = leer(nombre)
    if linea in texto:
        return
    lineas = texto.splitlines(keepends=True)
    # Un import de verdad, no una línea de docstring que empiece con "from ...".
    es_import = re.compile(r"^(import [\w.]+(\s|$)|from [\w.]+ import )")
    for i, l in enumerate(lineas):
        if es_import.match(l):
            lineas.insert(i, linea + "\n")
            cambios[nombre] = "".join(lineas)
            return
    sys.exit(f"{nombre}: sin imports a nivel de módulo")


def quitar_import_sin_uso(nombre):
    texto = leer(nombre)
    if "create_access_token(" not in texto:
        cambios[nombre] = re.sub(r"^[ \t]*from auth\.jwt import create_access_token\n", "", texto, flags=re.M)


# --- 1. Archivos con constantes USER_ID inventadas y helper sin argumentos
for nombre, etiqueta, rol, helper in [
    ("test_keys_http_pooling.py", "keys-pooling", "superadmin", "_superadmin_headers"),
    ("test_dashboard_http_pooling.py", "dashboard-pooling", "superadmin", "_superadmin_headers"),
    ("test_admin_keys_model_source.py", "admin-keys-model-source", "superadmin", "_superadmin_headers"),
    ("test_admin_keys_n1.py", "admin-keys-n1", "superadmin", "_superadmin_headers"),
]:
    viejo_uid = re.search(r'^USER_ID = "[^"]+"\n', leer(nombre), re.M).group(0)
    reemplazar(nombre, viejo_uid, "")
    reemplazar(nombre,
               f'def {helper}():\n    token = create_access_token(USER_ID, TENANT_ID, "{rol}")\n'
               '    return {"Authorization": f"Bearer {token}"}\n',
               f'def {helper}(client):\n    return cabeceras(client, "{etiqueta}", "{rol}", TENANT_ID)\n')
    texto = leer(nombre)
    cambios[nombre] = texto.replace(f"{helper}()", f"{helper}(client)")
    importar(nombre, "from tests.identidades import cabeceras")
    quitar_import_sin_uso(nombre)

# test_facet_model_wiring.py: operador, tenant NO numérico (sin memoria semántica)
n = "test_facet_model_wiring.py"
reemplazar(n, 'USER_ID = "test-facet-user"\n', "")
reemplazar(n, 'def _auth_headers():\n    token = create_access_token(USER_ID, TENANT_ID, "operator")\n'
              '    return {"Authorization": f"Bearer {token}"}\n',
           'def _auth_headers(client):\n    return cabeceras(client, "facet-model-wiring", "operator", TENANT_ID)\n')
cambios[n] = leer(n).replace("_auth_headers()", "_auth_headers(client)")
importar(n, "from tests.identidades import cabeceras")
quitar_import_sin_uso(n)

# test_pipelines_http_pooling.py: el id real también es el dueño de la fila
n = "test_pipelines_http_pooling.py"
reemplazar(n, 'USER_ID = "test-pipelines-pooling-user"\n', 'ETIQUETA = "pipelines-pooling"\n')
reemplazar(n, 'def _headers():\n    token = create_access_token(USER_ID, TENANT_ID, "operator")\n'
              '    return {"Authorization": f"Bearer {token}"}\n',
           'def _headers(client):\n    return cabeceras(client, ETIQUETA, "operator", TENANT_ID)\n')
reemplazar(n, "async def _insert_owned_row(pipeline_id):", "async def _insert_owned_row(pipeline_id, user_id):")
reemplazar(n, "(pipeline_id, time.time(), time.time(), USER_ID, TENANT_ID, time.time()),",
           "(pipeline_id, time.time(), time.time(), user_id, TENANT_ID, time.time()),")
reemplazar(n, "client.portal.call(_insert_owned_row, pipeline_id)",
           'client.portal.call(_insert_owned_row, pipeline_id, uid(client, ETIQUETA, "operator"))')
cambios[n] = leer(n).replace("_headers()", "_headers(client)")
importar(n, "from tests.identidades import cabeceras, uid")
quitar_import_sin_uso(n)

# test_admin_motors_endpoints.py: solo el helper de "no superadmin" cambia
n = "test_admin_motors_endpoints.py"
reemplazar(n, 'def _user_headers():\n    token = create_access_token(USER_ID, TENANT_ID, "user")\n'
              '    return {"Authorization": f"Bearer {token}"}\n',
           'def _user_headers(client):\n    # Operador REAL: con el rol leído de la base, un token de user_id=1 con\n'
           '    # rol "user" sería superadmin y este 403 pasaría a 200.\n'
           '    return cabeceras(client, "admin-motors", "operator", TENANT_ID)\n')
reemplazar(n, "headers=_user_headers())", "headers=_user_headers(client))")
importar(n, "from tests.identidades import cabeceras")

# --- 2. Tokens en línea para ids inventados: una identidad real por etiqueta
EN_LINEA = re.compile(r'create_access_token\(\s*"([^"]+)",\s*"([^"]+)",\s*"operator",?\s*\)')
for nombre, veces in [
    ("test_chat_facet_validation.py", 9),
    ("test_chat_grounding_wiring.py", 2),
    ("test_shadow_validation.py", 2),
    ("test_shadow_origin.py", 1),
    ("test_chat_contract_wrapper.py", 2),
]:
    if nombre == "test_shadow_origin.py":
        reemplazar(nombre,
                   '    token = create_access_token(\n'
                   '        f"test-origin-user-{user_suffix}", f"test-origin-tenant-{user_suffix}", "operator")\n',
                   '    token = token_de(client, f"test-origin-user-{user_suffix}", "operator",\n'
                   '                     f"test-origin-tenant-{user_suffix}")\n')
    texto, n_sub = EN_LINEA.subn(lambda m: f'token_de(client, "{m.group(1)}", "operator", "{m.group(2)}")',
                                 leer(nombre))
    if n_sub != veces:
        sys.exit(f"{nombre}: {n_sub} tokens en línea, se esperaban {veces}")
    cambios[nombre] = texto
    importar(nombre, "from tests.identidades import token_de")
    quitar_import_sin_uso(nombre)

n = "test_command_path_traversal.py"
reemplazar(n, 'create_access_token("attacker", "1", "operator")', 'token_de(client, "path-traversal-attacker")')
reemplazar(n, 'create_access_token("real-user", "1", "operator")', 'token_de(client, "path-traversal-real-user")')
importar(n, "from tests.identidades import token_de")
quitar_import_sin_uso(n)

# --- 3. WebSocket: el path /ws/{user_id} y el token tienen que ser del mismo id REAL
n = "test_websocket_isolation.py"
reemplazar(n, '    """An event addressed to user A must not leak to user B in the same tenant."""\n',
           '    """An event addressed to user A must not leak to user B in the same tenant."""\n'
           '    a, b = uid(client, "ws-aislamiento-a"), uid(client, "ws-aislamiento-b")\n')
reemplazar(n, '    """Two tabs for the same user: closing one must not silence the other."""\n',
           '    """Two tabs for the same user: closing one must not silence the other."""\n'
           '    a = uid(client, "ws-aislamiento-a")\n')
texto = leer(n)
texto = texto.replace('"/ws/test-user-a"', 'f"/ws/{a}"').replace('"/ws/test-user-b"', 'f"/ws/{b}"')
texto = texto.replace('"test-user-a"', "a").replace('"test-user-b"', "b")
if "test-user-" in texto:
    sys.exit(f"{n}: quedó un id inventado")
cambios[n] = texto
importar(n, "from tests.identidades import uid")

for nombre, texto in cambios.items():
    (T / nombre).write_text(texto)
    print("migrado", nombre)
```

Expected: 14 líneas `migrado ...` y ningún `sys.exit`. *(Corregido 2026-09-14: el script original llamaba `quitar_import_sin_uso` antes de `importar`; en `test_admin_keys_model_source.py`, cuyo único import es el de `create_access_token`, eso dejaba el archivo sin imports y abortaba con "sin imports a nivel de módulo". Ahora cada par importa primero.)* Si el script aborta, NO se corrige el conteo esperado a ciegas: se lee el archivo, se entiende qué cambió en el árbol y se ajusta el reemplazo.

- [ ] **Step 5: Revisar el diff y verificar que no quedan ids inventados**

```bash
git -C /home/fruiz/jax-platform diff --stat
grep -rn 'create_access_token("\(test-\|attacker\|real-user\)' /home/fruiz/jax-platform/backend/tests; echo "sin salida = ok"
grep -rn '_user_headers()\|_superadmin_headers()\|_auth_headers()\|_headers()' /home/fruiz/jax-platform/backend/tests/test_keys_http_pooling.py /home/fruiz/jax-platform/backend/tests/test_dashboard_http_pooling.py /home/fruiz/jax-platform/backend/tests/test_admin_keys_model_source.py /home/fruiz/jax-platform/backend/tests/test_admin_keys_n1.py /home/fruiz/jax-platform/backend/tests/test_facet_model_wiring.py /home/fruiz/jax-platform/backend/tests/test_pipelines_http_pooling.py /home/fruiz/jax-platform/backend/tests/test_admin_motors_endpoints.py; echo "sin salida = ok"
```
Leer el diff completo de los 14 archivos: cada cambio tiene que ser uno de los reemplazos del script, nada más.

- [ ] **Step 6: La suite da el MISMO número que en el Step 1**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest -q 2>&1 | tail -2` y `JAX_CI_NO_DB=1 .venv/bin/python -m pytest -q 2>&1 | tail -2`
Expected: `passed` idéntico al del Step 1, 0 failed, y el job sin DB idéntico a su piso. Esto es un control: con el middleware viejo estos tests ya pasaban. El valor aparece en la Task 3, cuando el middleware nuevo NO los rompe.

- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/tests/identidades.py backend/tests/conftest.py backend/tests/test_keys_http_pooling.py backend/tests/test_pipelines_http_pooling.py backend/tests/test_dashboard_http_pooling.py backend/tests/test_admin_keys_model_source.py backend/tests/test_admin_keys_n1.py backend/tests/test_facet_model_wiring.py backend/tests/test_admin_motors_endpoints.py backend/tests/test_chat_facet_validation.py backend/tests/test_chat_grounding_wiring.py backend/tests/test_shadow_validation.py backend/tests/test_shadow_origin.py backend/tests/test_chat_contract_wrapper.py backend/tests/test_command_path_traversal.py backend/tests/test_websocket_isolation.py
git -C /home/fruiz/jax-platform commit -m "test: identidades reales en los tests que se autentican (preparación para token_version)"
```

---

### Task 3: Middleware async por clave primaria, login con `tv` y `/refresh` contra la base

**Files:**
- Modify: `backend/auth/middleware.py` (archivo completo)
- Modify: `backend/api/auth.py` (login, `_emitir_tokens`, refresh)
- Test: `backend/tests/test_sesiones_token_version.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `tests.identidades.{sql, token_para, auth}`, fixture `usuarios` (Task 2); `create_*_token(..., token_version)` (Task 1).
- Produces: `verificar_sesion`, `SQL_ESTADO_DE_SESION`, `SESION_INVALIDA`, `get_current_user` async, `_emitir_tokens`.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_sesiones_token_version.py`:

```python
# ---------------------------------------------------------- middleware

from tests.identidades import auth, sql, token_para  # noqa: E402


def _me(client, token):
    return client.get("/api/auth/me", headers=auth(token))


def _usuarios_admin(client, token):
    return client.get("/api/admin/users", headers=auth(token))


def test_usuario_inexistente_no_entra(client):
    ((libre,),) = client.portal.call(sql, "SELECT COALESCE(MAX(user_id), 0) + 1000 FROM jax_users", (), True)
    r = _me(client, token_para(libre))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")


def test_user_id_no_numerico_no_entra(client):
    # Antes pasaba: el rol "superadmin" salía del token sin mirar la base.
    r = _usuarios_admin(client, create_access_token("no-es-un-id", "1", "superadmin"))
    assert r.status_code == 401


def test_usuario_desactivado_pierde_el_acceso_en_el_request_siguiente(client, usuarios):
    user_id, _ = usuarios()
    token = token_para(user_id)
    assert _me(client, token).status_code == 200
    client.portal.call(sql, "UPDATE jax_users SET status = 'inactive' WHERE user_id = %s", (user_id,))
    assert _me(client, token).status_code == 401


def test_version_distinta_no_entra(client, usuarios):
    user_id, _ = usuarios(token_version=3)
    assert _me(client, token_para(user_id, tv=2)).status_code == 401
    assert _me(client, token_para(user_id, tv=3)).status_code == 200


def test_token_de_antes_del_despliegue_sin_tv_vale_como_cero(client, usuarios):
    # CONTROL: ya pasa hoy. Protege la regla de spec §3.2 (nadie queda afuera
    # al desplegar) de un refactor que exija `tv`.
    user_id, _ = usuarios()
    viejo = jwt.encode({"user_id": str(user_id), "tenant_id": "1", "role": "operator",
                        "exp": int(time.time()) + 600, "type": "access"}, SECRET, algorithm=ALGORITHM)
    assert _me(client, viejo).status_code == 200


def test_el_rol_sale_de_la_base_no_del_token(client, usuarios):
    user_id, _ = usuarios(role="operator")
    token = token_para(user_id, role="superadmin")
    assert _usuarios_admin(client, token).status_code == 403
    assert _me(client, token).json()["role"] == "operator"


def test_superadmin_degradado_pierde_admin_al_instante(client, usuarios):
    user_id, _ = usuarios(role="superadmin")
    token = token_para(user_id, role="superadmin")
    assert _usuarios_admin(client, token).status_code == 200
    client.portal.call(sql, "UPDATE jax_users SET role = 'operator' WHERE user_id = %s", (user_id,))
    assert _usuarios_admin(client, token).status_code == 403


def test_refresh_token_no_sirve_como_access(client, usuarios):
    # CONTROL: ya pasa hoy (el middleware viejo miraba `type`).
    user_id, _ = usuarios()
    assert _me(client, token_para(user_id, tipo="refresh")).status_code == 401


def test_la_consulta_del_middleware_va_por_primary(client):
    from auth.middleware import SQL_ESTADO_DE_SESION

    async def explicar():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("EXPLAIN " + SQL_ESTADO_DE_SESION, (1,))
                columnas = [d[0] for d in cur.description]
                return [dict(zip(columnas, fila)) for fila in await cur.fetchall()]

    (plan,) = client.portal.call(explicar)
    assert (plan["type"], plan["key"]) == ("const", "PRIMARY"), plan


# --------------------------------------------------------------- refresh

def _refresh(client, token):
    try:
        # Cookie explícita: el jar del `client` de sesión puede traer la de
        # otro test (login); http.cookiejar no pisa un Cookie ya puesto.
        return client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={token}"})
    finally:
        client.cookies.clear()


def test_refresh_con_version_vieja_no_emite(client, usuarios):
    user_id, _ = usuarios(token_version=1)
    r = _refresh(client, token_para(user_id, tv=0, tipo="refresh"))
    assert (r.status_code, r.json()["detail"]) == (401, "sesion_invalida")


def test_refresh_de_usuario_desactivado_no_emite(client, usuarios):
    user_id, _ = usuarios(status="inactive")
    assert _refresh(client, token_para(user_id, tipo="refresh")).status_code == 401


def test_refresh_emite_con_el_rol_y_la_version_actuales(client, usuarios):
    user_id, _ = usuarios(role="superadmin", token_version=2)
    r = _refresh(client, token_para(user_id, role="operator", tv=2, tipo="refresh"))
    assert r.status_code == 200, r.text
    nuevo = decode_token(r.json()["access_token"])
    assert (nuevo["user_id"], nuevo["role"], nuevo["tv"], nuevo["type"]) == (str(user_id), "superadmin", 2, "access")


def test_access_token_no_sirve_como_refresh(client, usuarios):
    # CONTROL: ya pasa hoy.
    user_id, _ = usuarios()
    assert _refresh(client, token_para(user_id)).status_code == 401


# ----------------------------------------------------------------- login

def test_login_emite_la_version_actual(client, usuarios):
    user_id, email = usuarios(password="clave-de-prueba-9", token_version=4)
    r = client.post("/api/auth/login", json={"email": email, "password": "clave-de-prueba-9"})
    client.cookies.clear()
    assert r.status_code == 200, r.text
    assert decode_token(r.json()["access_token"])["tv"] == 4
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_sesiones_token_version.py -q`
Expected (motivos): `test_usuario_inexistente_no_entra` → detail `'Usuario no encontrado'` ≠ `'sesion_invalida'`; `test_user_id_no_numerico_no_entra` → 200; desactivado, versión distinta, rol de la base y degradado → 200 donde se espera 401/403; `test_la_consulta_del_middleware_va_por_primary` → `ImportError: cannot import name 'SQL_ESTADO_DE_SESION'`; refresh con versión vieja y desactivado → 200; refresh emite → `nuevo["role"] == 'operator'`; login → `tv == 0`. Pasan hoy SOLO los tres marcados CONTROL.

- [ ] **Step 3: Implementación mínima**

Reemplazar `backend/auth/middleware.py` completo por:

```python
"""Autenticación por request (2026-09-12, admin usuarios etapa 2).

Antes solo se decodificaba el JWT: el rol salía del token y nada miraba la
base, así que desactivar, borrar o degradar a alguien no cortaba su sesión
hasta que el token vencía (spec §1, hallazgo 1). Ahora cada request lee
status, role y token_version POR CLAVE PRIMARIA (EXPLAIN: const/PRIMARY,
fijado en tests/test_sesiones_token_version.py).

Sin caché a propósito (LAS CUATRO §2: sin medición no hay caché). Si algún día
hiciera falta uno, su invalidación es la propia token_version.

`tenant_id` sigue saliendo del token (el spec pide rol, estado y versión).
Un token sin `tv` (emitido antes de esta etapa) vale como tv=0.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db.connection import get_pool

from .jwt import decode_token
from .models import AuthUser

bearer = HTTPBearer(auto_error=True)

SESION_INVALIDA = "sesion_invalida"
SQL_ESTADO_DE_SESION = "SELECT status, role, token_version FROM jax_users WHERE user_id = %s"


def _rechazo() -> HTTPException:
    # El mismo 401 para todos los casos (no existe, inactivo, versión vieja,
    # tipo equivocado): la respuesta no dice cuál.
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESION_INVALIDA)


async def verificar_sesion(payload: dict, tipo: str) -> AuthUser:
    if payload.get("type") != tipo:
        raise _rechazo()
    try:
        user_id = int(payload["user_id"])
        tv_token = int(payload.get("tv", 0))
    except (KeyError, TypeError, ValueError):
        raise _rechazo() from None
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_ESTADO_DE_SESION, (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise _rechazo()
    estado, rol, tv_base = fila
    if estado != "active" or tv_token != int(tv_base):
        raise _rechazo()
    return AuthUser(
        user_id=str(user_id),
        tenant_id=str(payload.get("tenant_id", "")),
        role=rol,
        token_version=int(tv_base),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthUser:
    return await verificar_sesion(decode_token(credentials.credentials), "access")


def require_superadmin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo superadmin")
    return user
```

En `backend/api/auth.py`:

1. Cambiar `from auth.jwt import create_access_token, create_refresh_token, decode_token` por `from auth.jwt import REFRESH_EXPIRE_SECONDS, create_access_token, create_refresh_token, decode_token` y `from auth.middleware import get_current_user` por `from auth.middleware import get_current_user, verificar_sesion`.

2. En `login`, cambiar el SELECT a:

```python
                "SELECT user_id, tenant_id, email, password_hash, role, status, "
                "failed_attempts, locked_until, token_version "
                "FROM jax_users WHERE email = %s",
```

y la línea de desempaquetado a `user_id, tenant_id, email, password_hash, role, user_status, failed_attempts, locked_until, token_version = row`.

3. En `login`, reemplazar desde `access = create_access_token(str(user_id), str(tenant_id), role)` hasta el cierre de `response.set_cookie(...)` por:

```python
    access = _emitir_tokens(response, str(user_id), str(tenant_id), role, token_version)
```

4. Antes de `@router.post("/login", ...)` agregar:

```python
def _emitir_tokens(response: Response, user_id: str, tenant_id: str, role: str, token_version: int) -> str:
    """Emite access + refresh con la versión vigente; el refresh va en la
    cookie HttpOnly. Lo usan el login y, desde la etapa 4, el cambio de
    contraseña propio (que sube la versión y tiene que dejarle a ESTA sesión
    tokens nuevos)."""
    refresh_token = create_refresh_token(user_id, tenant_id, role, token_version)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=REFRESH_EXPIRE_SECONDS,
        samesite="lax",
    )
    return create_access_token(user_id, tenant_id, role, token_version)
```

5. Reemplazar la función `refresh` completa por:

```python
@router.post("/refresh", response_model=RefreshResponse)
async def refresh(refresh_token: str = Cookie(None)):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sin refresh token")
    # La misma verificación que cada request (etapa 2): un refresh de un
    # usuario desactivado o con la versión vieja ya no reemite nada. El access
    # nuevo lleva el rol y la versión de la BASE, no los del refresh.
    # La cookie NO se rota: rotarla haría deslizante la sesión de 7 días, y la
    # versión ya invalida el refresh viejo cuando hace falta.
    user = await verificar_sesion(decode_token(refresh_token), "refresh")
    access = create_access_token(user.user_id, user.tenant_id, user.role, user.token_version)
    return RefreshResponse(access_token=access)
```

- [ ] **Step 4: Verde, y la suite entera sigue verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_sesiones_token_version.py -q` → `17 passed`.
Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest -q 2>&1 | tail -3` → 0 failed. Si falla algún test que no es de esta etapa, es un sitio que firma para un id inventado y que la Task 2 no vio: se migra con `tests/identidades.py` y se anota en el reporte. Nunca se le agrega un `dependency_overrides` para esquivar la autenticación.

- [ ] **Step 5: Pisos medidos**

`PISO_PASSED` = medido (esperado piso de la Task 1 + 14). El job sin DB no cambia (los 14 piden `client`). Comentario: "admin usuarios etapa 2, Task 3: 14 de middleware/refresh/login/EXPLAIN".

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/auth/middleware.py backend/api/auth.py backend/tests/test_sesiones_token_version.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(auth): cada request verifica estado, rol y token_version en la base; /refresh también"
```

---

### Task 4: WebSocket — la misma verificación al conectar

**Files:**
- Modify: `backend/main.py` (import de `verificar_sesion`; handshake de `websocket_endpoint`)
- Test: `backend/tests/test_sesiones_token_version.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `auth.middleware.verificar_sesion(payload, "access") -> AuthUser`.
- Produces: el WS cierra con `4001` si la sesión no es válida; si lo es, registra al usuario con el rol y el tenant de `verificar_sesion`.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_sesiones_token_version.py`:

```python
# ------------------------------------------------------------- WebSocket

from starlette.websockets import WebSocketDisconnect  # noqa: E402


def _ws_auth(client, user_id, token):
    with client.websocket_connect(f"/ws/{user_id}") as ws:
        ws.send_json({"type": "auth", "token": token})
        try:
            return ws.receive_json()
        except WebSocketDisconnect as exc:
            return exc.code


def test_ws_de_usuario_desactivado_se_cierra_con_4001(client, usuarios):
    user_id, _ = usuarios(status="inactive")
    assert _ws_auth(client, user_id, token_para(user_id)) == 4001


def test_ws_con_version_vieja_se_cierra_con_4001(client, usuarios):
    user_id, _ = usuarios(token_version=1)
    assert _ws_auth(client, user_id, token_para(user_id, tv=0)) == 4001


def test_ws_de_usuario_real_activo_autentica(client, usuarios):
    # CONTROL: ya pasa hoy.
    user_id, _ = usuarios()
    assert _ws_auth(client, user_id, token_para(user_id)) == {"type": "auth_ok"}
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_sesiones_token_version.py -q -k ws_`
Expected: los dos primeros fallan con `assert {'type': 'auth_ok'} == 4001` (hoy el handshake solo decodifica). El tercero es control y pasa.

- [ ] **Step 3: Implementación mínima**

En `backend/main.py`, después de `from auth.jwt import decode_token` agregar `from auth.middleware import verificar_sesion`. En `websocket_endpoint`, dentro del `try`, después del bloque:

```python
        if str(payload.get("user_id")) != str(user_id):
            await websocket.close(code=4001)
            return
```

agregar:

```python
        # La misma verificación que cada request HTTP (admin usuarios etapa
        # 2): usuario existente, `active` y con la versión de token vigente.
        # Un HTTPException cae en el `except Exception` de abajo -> 4001.
        sesion = await verificar_sesion(payload, "access")
```

y reemplazar las dos líneas posteriores al `try`:

```python
    tenant_id = str(payload["tenant_id"])
    role = payload["role"]
```

por:

```python
    tenant_id = sesion.tenant_id
    role = sesion.role  # de la base, no del token
```

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_sesiones_token_version.py tests/test_websocket_isolation.py -q` → todo verde (20 + los de aislamiento).
Run: la suite completa → 0 failed.

- [ ] **Step 5: Piso** — `PISO_PASSED` = medido (esperado + 3). Comentario: "Task 4: 3 del handshake WS".

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/main.py backend/tests/test_sesiones_token_version.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(ws): el handshake verifica estado, rol y token_version en la base"
```

---

### Task 4b: El frontend dice por qué se cerró la sesión (agregada 2026-09-14, decisión de Fernando)

**Por qué:** desde la Task 3 un usuario desactivado o degradado recibe 401. `frontend/src/api/client.js:20-29` intenta `/api/auth/refresh` y, si también falla, borra la sesión **en silencio** (`useJaxStore.setState({ token: null, user: null })`): la persona queda afuera sin explicación.

**Files:**
- Modify: `frontend/src/api/client.js` (guardar el motivo antes de borrar la sesión), `frontend/src/store/useJaxStore.js` (campo `avisoSesion`, clave de i18n o `null`), `frontend/src/pages/Login.jsx` (mostrarlo con `components/AlertaError.jsx`; se borra al iniciar sesión), `frontend/src/i18n/es.js` + `en.js` (`sesion_invalida`, `sesion_expirada`).
- Tests: `frontend/src/api/client.test.js` (crear) y `frontend/src/pages/Login.test.jsx`.
- Backend: el código `sesion_invalida` sale de **una** constante compartida. Hoy ya lo produce `backend/api/admin/smtp.py:161`; la Task 3 define la constante y `smtp.py` la importa (dos productores del mismo código con dos literales es deuda).

**Comportamiento:** si el refresh falla, el interceptor guarda `avisoSesion = 'sesion_invalida'` cuando el backend lo dijo (detail `sesion_invalida` en la respuesta del 401 o del refresh) y `'sesion_expirada'` en cualquier otro caso. Login lo muestra traducido; un login exitoso lo borra. Nunca se borra la sesión sin dejar el motivo.

- [ ] **Step 1 (RED):** tests de vitest: (a) refresh fallido con `sesion_invalida` → `avisoSesion === 'sesion_invalida'`; (b) refresh fallido sin código → `'sesion_expirada'`; (c) Login con `avisoSesion` muestra `es.sesion_invalida` en un `role="alert"`; (d) login exitoso lo borra; (e) las dos claves existen en es y en en. Verlos fallar por el motivo esperado.
- [ ] **Step 2:** implementar lo mínimo para verde.
- [ ] **Step 3:** vitest completo; `numPassedTests` en `policy.yml` sube de 125 al número **medido**, con comentario.
- [ ] **Step 4:** commit.

### Task 5: PR y gate de CI

- [ ] **Step 1: Suite local completa** (con DB, sin DB, vitest): los tres números iguales a los pisos del archivo.
- [ ] **Step 2: PR**

```bash
git -C /home/fruiz/jax-platform push -u origin feat/admin-usuarios-2-sesiones
gh pr create --repo fjruizhn/jax-platform --base master --head feat/admin-usuarios-2-sesiones \
  --title "Admin usuarios · etapa 2: sesiones que se cortan de verdad (token_version)" \
  --body "Spec §3.2. Plan: docs/superpowers/plans/2026-09-12-admin-usuarios-etapa-2-sesiones.md. Migración token_version; middleware async por PRIMARY (EXPLAIN en test); /refresh y WS con la misma verificación; 14 archivos de test pasan a identidades reales."
```

- [ ] **Step 3: Gate por headSha (ANTES de mergear)**

```bash
gh pr checks <N> --repo fjruizhn/jax-platform
gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid
git -C /home/fruiz/jax-platform ls-remote origin refs/heads/feat/admin-usuarios-2-sesiones
```
Expected: todos `SUCCESS`, y `headRefOid` == sha remoto.

---

### Task 6: Despliegue, verificación en vivo y prueba de carga DESPUÉS

- [ ] **Step 1: Respaldo de `jax_users` con restauración probada (la migración hace un ALTER en producción)**

```bash
set -a; . /etc/jax/.env; set +a
F=/home/fruiz/backups/jax_users-pre-admin-usuarios-2-$(date +%Y%m%d-%H%M%S).sql
mkdir -p /home/fruiz/backups
mariadb-dump -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory jax_users > "$F"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -e "CREATE DATABASE jax_restore_check"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_restore_check < "$F"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -N -e "SELECT (SELECT COUNT(*) FROM jax_memory.jax_users), (SELECT COUNT(*) FROM jax_restore_check.jax_users)"
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" -e "DROP DATABASE jax_restore_check"
```
Expected: los dos conteos iguales. El dump de una tabla con FK a `jax_tenants` puede requerir `SET FOREIGN_KEY_CHECKS=0` al restaurar (mariadb-dump ya lo agrega en la cabecera). Si `JAX_DB_USER` no puede crear bases, PARAR y pedirle a Fernando cómo prefiere probar la restauración: un respaldo sin restauración probada no es respaldo.

- [ ] **Step 2: Merge y reinicio del backend**

```bash
gh pr merge <N> --repo fjruizhn/jax-platform --merge
git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only
sudo -n /usr/bin/systemctl restart jax-platform.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health
sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40
readlink /proc/$(systemctl show -p MainPID --value jax-platform.service)/cwd
```
Expected: `200`; journal sin tracebacks; cwd `/home/fruiz/jax-platform/backend`. En esta etapa no hay cambio de frontend: no se despliega.

- [ ] **Step 3: La columna existe y la consulta va por PRIMARY en producción**

```bash
set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "SELECT user_id, token_version FROM jax_users ORDER BY user_id; EXPLAIN SELECT status, role, token_version FROM jax_users WHERE user_id = 1"
```
Expected: todas las filas con `token_version = 0`; EXPLAIN `type=const`, `key=PRIMARY`.

- [ ] **Step 4: Verificación en vivo del spec §5 (avisar a Fernando antes: crea y borra un usuario de prueba en producción)**

```bash
cd /home/fruiz/jax-platform/backend && set -a; . /etc/jax/.env; set +a
ADMIN=$(.venv/bin/python -c "from auth.jwt import create_access_token; print(create_access_token('1', '1', 'superadmin', 0))")
PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
EM="verificacion-etapa2-$(date +%s)@example.invalid"
UIDP=$(curl -s -X POST http://127.0.0.1:8080/api/admin/users -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EM\",\"role\":\"superadmin\",\"password\":\"$PW\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['user_id'])")
TOK=$(curl -s -X POST http://127.0.0.1:8080/api/auth/login -H 'Content-Type: application/json' -d "{\"email\":\"$EM\",\"password\":\"$PW\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -o /dev/null -w 'admin antes: %{http_code}\n' -H "Authorization: Bearer $TOK" http://127.0.0.1:8080/api/admin/users
curl -s -o /dev/null -X PUT http://127.0.0.1:8080/api/admin/users/$UIDP -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d '{"role":"operator"}'
curl -s -o /dev/null -w 'admin degradado: %{http_code}\n' -H "Authorization: Bearer $TOK" http://127.0.0.1:8080/api/admin/users
curl -s -o /dev/null -X PUT http://127.0.0.1:8080/api/admin/users/$UIDP -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d '{"status":"inactive"}'
curl -s -o /dev/null -w 'desactivado: %{http_code}\n' -H "Authorization: Bearer $TOK" http://127.0.0.1:8080/api/auth/me
curl -s -o /dev/null -w 'baja del usuario de prueba: %{http_code}\n' -X DELETE http://127.0.0.1:8080/api/admin/users/$UIDP -H "Authorization: Bearer $ADMIN"
```
Expected: `admin antes: 200`, `admin degradado: 403`, `desactivado: 401`, borrado `200`. (En esta etapa todavía existe el `DELETE` y el `PUT` sin guardas: se reemplazan en las etapas 3 y 5.)

- [ ] **Step 5: Prueba de carga DESPUÉS, mismas condiciones que la Task 0**

```bash
TOKEN=$(.venv/bin/python -c "from auth.jwt import create_access_token; print(create_access_token('1', '1', 'superadmin', 0))")
for c_n in "1 200" "10 500" "50 1000"; do set -- $c_n
  python3 /home/fruiz/jax/scripts/load_test.py --url http://127.0.0.1:8080/api/auth/me -c $1 -n $2 -H "Authorization: Bearer $TOKEN"
done
```
Criterio: 0 errores. Si el p95 empeora más de lo que explica una consulta por PK extra (orden de 1 ms), o aparece degradación a una concurrencia menor que en la línea de base, NO se da GO: se diagnostica antes de seguir (y recién ahí se evalúa un caché, invalidado por `token_version`).

- [ ] **Step 6: Biblioteca**

Agregar a `/home/fruiz/jax/DEUDA.md` (PR propio en el repo `jax`) una entrada "Sesiones con token_version en jax-platform — VERDAD OPERACIONAL <fecha hora CST>", con la tabla antes/después (concurrencia, peticiones, errores, rps, p50, p95, p99), el EXPLAIN de producción, el resultado de la verificación en vivo, el sha desplegado y el respaldo (ruta y restauración probada).

---

## Autorrevisión (hecha al escribir el plan)

- **Cobertura de §3.2:** migración (Task 1), `tv` en ambos tokens (Task 1), `get_current_user` async por PK con los tres rechazos y el rol desde la base (Task 3), `/refresh` con la misma verificación y el rol y la versión actuales (Task 3), WebSocket (Task 4), tokens sin `tv` como 0 (control en la Task 3), sin caché + EXPLAIN + carga antes y después (Tasks 0, 3 y 6). "token_version sube en..." es de las etapas 3, 4 y 5; esta etapa deja `_emitir_tokens` para la 4.
- **Riesgo que el spec no mencionaba y el plan cubre:** 14 archivos de test firman tokens para ids inexistentes (Task 2). Sin esa migración, la etapa rompía la suite o, peor, se "arreglaba" con `dependency_overrides`, que dejaría a esos tests sin probar la autenticación.
- **Placeholders:** `<N>`, `<fecha hora CST>` y los conteos medidos son valores de ejecución.
- **Nombres consistentes:** `verificar_sesion`, `SQL_ESTADO_DE_SESION`, `SESION_INVALIDA = "sesion_invalida"`, `_emitir_tokens`, `token_para`, `usuarios`: iguales en tests, implementación e Interfaces.
