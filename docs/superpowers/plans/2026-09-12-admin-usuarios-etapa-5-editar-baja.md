# Administración de usuarios · Etapa 5 — Editar (con email) y dar de baja con ConfirmacionSuma

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el superadmin edite email, rol y estado con validación y códigos estables, y que "eliminar" pase a ser una **baja**: la cuenta queda inutilizable, sale de la lista, libera el correo y conserva su historial. La baja se confirma con una suma (componente reutilizable) y todo error se muestra traducido.

**Architecture:** Una migración idempotente agrega `jax_users.deleted_at`/`deleted_by` y ensancha `email` a `VARCHAR(320)`: el correo renombrado (`<original>#baja-<id>-<yyyymmdd>`) no cabe en 254. `PUT /api/admin/users/{id}` valida el email (formato, 254, único) además de rol y estado. `POST /api/admin/users/{id}/baja` pone `status='deleted'`, `deleted_at`, `deleted_by`, renombra el email y sube `token_version`, todo en una transacción con las guardas de la etapa 3 y la auditoría `baja`. Se elimina el `DELETE`. Un usuario dado de baja deja de existir para la lista y para toda acción. En el frontend se agregan `ConfirmacionSuma` (reusable), el campo email en el modal de edición y la acción "Dar de baja".

**Tech Stack:** FastAPI, aiomysql, PyMySQL (`pymysql.err.IntegrityError`), MariaDB, pytest; React 19, vitest.

**Spec:** `/home/fruiz/jax-platform/docs/superpowers/specs/2026-09-12-administracion-usuarios-design.md` (§3.5; de §3.2 "token_version sube en: baja"; de §3.3 "rechazan dar de baja al último"; §5; §6 punto 5).

## Global Constraints

- **Repo:** `/home/fruiz/jax-platform`; rama desde `master` con las etapas 1-4 mergeadas: `git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform switch -c feat/admin-usuarios-5-baja origin/master`. Siempre `git -C <ruta>`.
- **TDD** con el rojo visto por el motivo que dice cada paso.
- **Backend tests:** `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest ...`; con `client` → `jax_memory_test`; identidades reales con `tests/identidades.py` y el fixture `usuarios`. **Ningún test modifica `user_id=1`** (solo actor).
- **Pisos exactos** en `.github/workflows/policy.yml`: leer los valores ACTUALES y subirlos con el número MEDIDO y un comentario. Esta etapa BORRA un test de la etapa 3 (el del `DELETE`): el comentario lo dice.
- **P10**, **`add_safe_task`**, **nada bloqueante en async**.
- **Eliminar = dar de baja** (decisión de Fernando, spec §2): nada de `DELETE`. Se conserva el historial. El login ya rechaza lo que no es `active`.
- **Códigos estables:** `email_invalido` (400), `email_ya_existe` (409), `rol_invalido` (400), `estado_invalido` (400; `deleted` solo por la baja), `usuario_no_encontrado` (404, también para un dado de baja), `auto_accion_prohibida` (403), `ultimo_superadmin` (409).
- **Email:** se recorta (`strip`), máximo `validacion.EMAIL_MAX = 254`, formato de `validacion.email_valido` (etapa 1), único. La columna pasa a 320 para que quepa el renombre de la baja: 254 + `#baja-` + id (hasta 10 dígitos) + `-` + 8 ≤ 280.
- **i18n es/en** y **modo claro/oscuro**: los errores, en toasts traducidos y nunca tragados.
- **Migraciones** idempotentes en `backend/db/migrations.py` (`_COLUMNS`, `_COLUMN_WIDENS`); sin ALTER a mano. Commits sin `--no-verify`.
- **YAGNI (spec §4):** la baja NO borra la memoria del usuario (derecho al olvido: ronda propia).
- **Cierre:** PR → CI verde por `headSha` → despliegue backend + frontend → verificación en vivo.

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/db/migrations.py` | Modificar | `deleted_at`, `deleted_by`; `email` a `VARCHAR(320)` (CREATE y ensanche) |
| `backend/api/admin/users.py` | Modificar | `email_de_baja`, `PUT` con email, `POST /baja`, sin `DELETE`, lista y acciones sin dados de baja, alta con códigos |
| `backend/tests/test_admin_usuarios_baja.py` | Crear | tests |
| `backend/tests/test_admin_usuarios_guardas.py` | Modificar | borrar el test del `DELETE` (el endpoint deja de existir) |
| `frontend/src/components/ConfirmacionSuma.jsx` | Crear | confirmación por suma, reusable |
| `frontend/src/components/ConfirmacionSuma.test.jsx` | Crear | tests |
| `frontend/src/components/admin/EditarUsuarioModal.jsx` | Modificar | campo email |
| `frontend/src/components/admin/EditarUsuarioModal.test.jsx` | Modificar | +1 test |
| `frontend/src/pages/admin/AdminUsers.jsx` | Modificar | "Dar de baja" con ConfirmacionSuma; sin `window.confirm` |
| `frontend/src/pages/admin/AdminUsers.test.jsx` | Modificar | +2 tests |
| `frontend/src/i18n/es.js`, `en.js` | Modificar | textos |
| `.github/workflows/policy.yml` | Modificar | pisos |

## Interfaces

**Consumes (etapas anteriores, firmas exactas):**

```python
# etapa 1
validacion.EMAIL_MAX = 254 ; validacion.email_valido(valor: str) -> bool
smtp_config.SmtpSettings(...) ; smtp_config.cargar_settings()
# etapa 2
tests.identidades: sql, crear_usuario, borrar_usuario, token_para, auth ; fixture usuarios
# etapa 3
db.transaccion.transaccion() ; user_audit.registrar(cur, actor, target, action, detail=None, ip=None)
api.admin.users: _ip(request), guarda_auto_accion(actor_id, target_id), exigir_invariante(cur, target_id, rol_actual,
    estado_actual, nuevo_rol, nuevo_estado), otros_superadmins_activos(cur, excluido), _leer_para_actualizar(cur, user_id),
    VALID_ROLES, ESTADOS_EDITABLES, UpdateUserRequest(extra="forbid"), unlock_user, revoke_sessions, user_audit_history
# etapa 4
api.admin.users.create_user (regla de contraseña + hash en hilo), api.admin.users.send_reset_link
```
```js
// etapa 3
mensajeDeError(t, err) ; EditarUsuarioModal({ usuario, onGuardar, onCerrar }) ; t.adminErrors (objeto)
// AdminUsers.jsx (etapas 3-4): avisarError, avisarExito, load, estados editando/historialDe, ACCION, ACCION_NEUTRA
```

**Produces:**

```python
# backend/api/admin/users.py
def email_de_baja(email: str, user_id: int, fecha: date) -> str      # "<email>#baja-<id>-<yyyymmdd>"
PUT  /api/admin/users/{id}      {email?, role?, status?}
POST /api/admin/users/{id}/baja -> {"ok": true}
# _leer_para_actualizar(cur, user_id) -> (role, status, email) | None   (excluye status='deleted')
```
```js
// frontend/src/components/ConfirmacionSuma.jsx
export function numerosAlAzar(aleatorio = Math.random) // [a, b], a en 10..49, b en 1..9
export default function ConfirmacionSuma({ titulo, mensaje, textoConfirmar, onConfirmar, onCancelar, numeros })
```

---

### Task 1: Migración — `deleted_at`, `deleted_by` y email de 320

**Files:**
- Modify: `backend/db/migrations.py` (`CREATE_USERS`, `_COLUMNS`, `_COLUMN_WIDENS`)
- Test: `backend/tests/test_admin_usuarios_baja.py` (crear, con el test de esquema)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `_COLUMNS` (tuplas `(tabla, columna, ALTER)`), `_COLUMN_WIDENS` (tuplas `(tabla, columna, largo_min, ALTER MODIFY)` aplicadas si `_column_too_narrow`).
- Produces: columnas `deleted_at DATETIME NULL`, `deleted_by INT NULL`, `email VARCHAR(320)`.

- [ ] **Step 1: Test que falla**

Crear `backend/tests/test_admin_usuarios_baja.py`:

```python
"""Editar y dar de baja (2026-09-12, administración de usuarios, etapa 5, spec §3.5).

Eliminar = dar de baja (decisión de Fernando): la cuenta queda inutilizable,
sale de la lista y libera el correo; se conserva el historial. Nada de DELETE:
9 tablas tienen user_id y solo 3 con FK a jax_users, así que borrar de verdad
dejaba memoria, costos y pipelines huérfanos o fallaba (spec §1, hallazgo 7).
"""
import json
import uuid
from datetime import date

from api.admin import users as users_mod
from tests.identidades import auth, borrar_usuario, sql, token_para


def _admin():
    return auth(token_para(1, role="superadmin"))


def test_columnas_de_la_baja_y_ancho_del_correo(client):
    filas = client.portal.call(
        sql,
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'jax_users' "
        "AND COLUMN_NAME IN ('email', 'deleted_at', 'deleted_by') ORDER BY COLUMN_NAME", (), True)
    assert [tuple(f) for f in filas] == [("deleted_at", "datetime", None), ("deleted_by", "int", None),
                                        ("email", "varchar", 320)]
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_baja.py -q`
Expected: FAIL; la lista solo trae `('email', 'varchar', 100)`.

- [ ] **Step 3: Implementación mínima**

En `backend/db/migrations.py`:

1. En `CREATE_USERS`, cambiar `  email VARCHAR(100) NOT NULL UNIQUE,` por `  email VARCHAR(320) NOT NULL UNIQUE,`.

2. En `_COLUMNS`, después de la tupla `("jax_users", "token_version", "ALTER TABLE jax_users ADD COLUMN token_version INT NOT NULL DEFAULT 0"),` agregar:

```python
    # Baja en vez de DELETE (2026-09-12, admin usuarios etapa 5, spec §3.5).
    ("jax_users", "deleted_at", "ALTER TABLE jax_users ADD COLUMN deleted_at DATETIME NULL"),
    ("jax_users", "deleted_by", "ALTER TABLE jax_users ADD COLUMN deleted_by INT NULL"),
```

3. En `_COLUMN_WIDENS`, reemplazar

```python
        "ALTER TABLE axioma_usage MODIFY COLUMN model VARCHAR(100) NOT NULL",
    ),
]
```

por:

```python
        "ALTER TABLE axioma_usage MODIFY COLUMN model VARCHAR(100) NOT NULL",
    ),
    # jax_users.email era VARCHAR(100), pero se valida hasta 254 (RFC 5321) y la
    # baja lo renombra a <original>#baja-<id>-<yyyymmdd> para liberar la
    # dirección: hasta 254 + 26. 320 deja margen. MODIFY conserva el UNIQUE
    # (índice de 1280 bytes en utf8mb4, bajo el límite de 3072 de InnoDB).
    (
        "jax_users", "email", 320,
        "ALTER TABLE jax_users MODIFY COLUMN email VARCHAR(320) NOT NULL",
    ),
]
```

- [ ] **Step 4: Verde** — `tests/test_admin_usuarios_baja.py` → `1 passed`.
- [ ] **Step 5: Piso medido** — con DB +1.
- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/db/migrations.py backend/tests/test_admin_usuarios_baja.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(db): jax_users.deleted_at/deleted_by y email de 320 para la baja"
```

---

### Task 2: `PUT` con email validado y alta con códigos estables

**Files:**
- Modify: `backend/api/admin/users.py` (imports, `_leer_para_actualizar`, `create_user`, `UpdateUserRequest`, `update_user`)
- Test: `backend/tests/test_admin_usuarios_baja.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `validacion.email_valido`, `EMAIL_MAX`; guardas y auditoría (etapa 3); regla de contraseña (etapa 4).
- Produces: `PUT {email?, role?, status?}` con `update_email` auditado; `_leer_para_actualizar` devuelve `(role, status, email)` y excluye a los dados de baja; alta con `email_invalido`/`email_ya_existe`/`rol_invalido`.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_admin_usuarios_baja.py`:

```python
# --------------------------------------------------------------- editar

async def _fila(user_id):
    filas = await sql("SELECT email, role, status, token_version, deleted_at, deleted_by FROM jax_users "
                      "WHERE user_id = %s", (user_id,), True)
    return tuple(filas[0]) if filas else None


async def _acciones(target):
    return [tuple(f) for f in await sql("SELECT action, detail FROM user_admin_audit WHERE target_user_id = %s "
                                        "ORDER BY id", (target,), True)]


def _put(client, target, **cuerpo):
    return client.put(f"/api/admin/users/{target}", json=cuerpo, headers=_admin())


def test_editar_el_correo_cambia_y_audita_sin_cerrar_sesiones(client, usuarios):
    u, email = usuarios()
    nuevo = f"test-nuevo-{uuid.uuid4().hex[:10]}@example.invalid"
    r = _put(client, u, email=f"  {nuevo}  ")
    assert r.status_code == 200, r.text
    fila = client.portal.call(_fila, u)
    assert (fila[0], fila[3]) == (nuevo, 0)
    ((accion, detalle),) = client.portal.call(_acciones, u)
    assert (accion, json.loads(detalle)) == ("update_email", {"from": email, "to": nuevo})


def test_correo_invalido(client, usuarios):
    u, email = usuarios()
    for malo in ("sin-arroba", "a@b", "a" * 250 + "@x.io"):
        r = _put(client, u, email=malo)
        assert (r.status_code, r.json()["detail"]) == (400, "email_invalido"), malo
    assert client.portal.call(_fila, u)[0] == email


def test_correo_repetido(client, usuarios):
    _, email_a = usuarios()
    b, email_b = usuarios()
    r = _put(client, b, email=email_a)
    assert (r.status_code, r.json()["detail"]) == (409, "email_ya_existe")
    assert client.portal.call(_fila, b)[0] == email_b


def test_alta_con_codigos_estables(client, usuarios):
    _, existente = usuarios()

    def alta(email, role="viewer"):
        r = client.post("/api/admin/users", json={"email": email, "role": role, "password": "clave-larga-1"},
                        headers=_admin())
        return r.status_code, r.json().get("detail")

    assert alta(existente) == (409, "email_ya_existe")
    assert alta("no-es-un-correo") == (400, "email_invalido")
    assert alta(f"test-rol-{uuid.uuid4().hex[:8]}@example.invalid", role="dios") == (400, "rol_invalido")
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_baja.py -q`
Expected: los tres de `PUT` reciben `422` (`extra="forbid"` todavía no conoce `email`); `test_alta_con_codigos_estables` recibe `"Email ya existe"`, `200` y `"Rol inválido: dios"`.

- [ ] **Step 3: Implementación mínima**

En `backend/api/admin/users.py`:

1. Imports: cambiar `from datetime import datetime` por `from datetime import date, datetime`; después de `import user_audit` agregar `from pymysql.err import IntegrityError`; después de `from db.transaccion import transaccion` agregar `from validacion import email_valido`.

2. Reemplazar `_leer_para_actualizar` por:

```python
async def _leer_para_actualizar(cur, user_id: int):
    # Un dado de baja no existe para ninguna acción (etapa 5): 404.
    await cur.execute(
        "SELECT role, status, email FROM jax_users WHERE user_id = %s AND status <> 'deleted' FOR UPDATE",
        (user_id,),
    )
    return await cur.fetchone()
```

3. Reemplazar la función `create_user` completa (desde `@router.post("/users")` hasta su `return`) por:

```python
@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request, user: AuthUser = Depends(require_superadmin)):
    email = req.email.strip()
    if not email_valido(email):
        raise HTTPException(status_code=400, detail="email_invalido")
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="rol_invalido")
    problema = problema_de_password(req.password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    # bcrypt de costo 12 (~150 ms de CPU): en un hilo, no en el event loop.
    ph = await asyncio.to_thread(_hash, req.password)
    async with transaccion() as cur:
        await cur.execute("SELECT COUNT(*) FROM jax_users WHERE email = %s", (email,))
        (count,) = await cur.fetchone()
        if count > 0:
            raise HTTPException(status_code=409, detail="email_ya_existe")
        try:
            await cur.execute(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) VALUES (1, %s, %s, %s, 'active')",
                (email, ph, req.role),
            )
        except IntegrityError as exc:
            # El UNIQUE es el respaldo ante dos altas simultáneas del mismo correo.
            raise HTTPException(status_code=409, detail="email_ya_existe") from exc
        new_id = cur.lastrowid
        await user_audit.registrar(cur, int(user.user_id), new_id, "create",
                                   {"email": email, "role": req.role}, _ip(request))
    return {"user_id": new_id, "email": email, "role": req.role, "status": "active"}
```

4. Reemplazar `class UpdateUserRequest` y la función `update_user` completas por:

```python
class UpdateUserRequest(BaseModel):
    # extra="forbid": un campo que no existe (p. ej. password) responde 422 en
    # vez de ignorarse en silencio.
    model_config = ConfigDict(extra="forbid")
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    email_pedido = req.email.strip() if req.email is not None else None
    if email_pedido is not None and not email_valido(email_pedido):
        raise HTTPException(status_code=400, detail="email_invalido")
    if req.role is not None and req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="rol_invalido")
    if req.status is not None and req.status not in ESTADOS_EDITABLES:
        raise HTTPException(status_code=400, detail="estado_invalido")
    actor_id = int(user.user_id)
    async with transaccion() as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual, email_actual = actual
        nuevo_rol = req.role if req.role is not None else rol_actual
        nuevo_estado = req.status if req.status is not None else estado_actual
        nuevo_email = email_pedido if email_pedido is not None else email_actual
        cambia_rol, cambia_estado = nuevo_rol != rol_actual, nuevo_estado != estado_actual
        cambia_email = nuevo_email != email_actual
        if not (cambia_rol or cambia_estado or cambia_email):
            return {"ok": True}
        corta_sesiones = cambia_rol or cambia_estado
        if corta_sesiones:
            guarda_auto_accion(actor_id, user_id)
            await exigir_invariante(cur, user_id, rol_actual, estado_actual, nuevo_rol, nuevo_estado)
        if cambia_email:
            await cur.execute("SELECT 1 FROM jax_users WHERE email = %s AND user_id <> %s", (nuevo_email, user_id))
            if await cur.fetchone():
                raise HTTPException(status_code=409, detail="email_ya_existe")
        try:
            # Rol o estado nuevos: todas las sesiones se cortan (spec §3.2). El
            # email solo no las corta: la identidad es el user_id.
            await cur.execute(
                "UPDATE jax_users SET email = %s, role = %s, status = %s, "
                "token_version = token_version + %s WHERE user_id = %s",
                (nuevo_email, nuevo_rol, nuevo_estado, 1 if corta_sesiones else 0, user_id),
            )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="email_ya_existe") from exc
        ip = _ip(request)
        if cambia_email:
            await user_audit.registrar(cur, actor_id, user_id, "update_email",
                                       {"from": email_actual, "to": nuevo_email}, ip)
        if cambia_rol:
            await user_audit.registrar(cur, actor_id, user_id, "update_role", {"from": rol_actual, "to": nuevo_rol}, ip)
        if cambia_estado:
            await user_audit.registrar(cur, actor_id, user_id, "update_status",
                                       {"from": estado_actual, "to": nuevo_estado}, ip)
    return {"ok": True}
```

5. En `delete_user` (se borra entera en la Task 3), cambiar mientras tanto `rol_actual, estado_actual = actual` por `rol_actual, estado_actual, _email = actual`, para que la suite siga verde entre tareas.

- [ ] **Step 4: Verde** — `tests/test_admin_usuarios_baja.py tests/test_admin_usuarios_guardas.py tests/test_contrasenas.py` → verde (los de la etapa 3 siguen pasando con la forma nueva). Suite completa → 0 failed.
- [ ] **Step 5: Piso medido** — con DB +4.
- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/admin/users.py backend/tests/test_admin_usuarios_baja.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): editar el correo con validación y códigos estables; alta con códigos estables"
```

---

### Task 3: La baja reemplaza al `DELETE`

**Files:**
- Modify: `backend/api/admin/users.py` (`list_users`, `email_de_baja`, `dar_de_baja`, borrar `delete_user`, `send_reset_link`)
- Modify: `backend/tests/test_admin_usuarios_guardas.py` (borrar el test del `DELETE`)
- Test: `backend/tests/test_admin_usuarios_baja.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `guarda_auto_accion`, `exigir_invariante`, `_leer_para_actualizar` (Task 2), `user_audit.registrar`, `_ip`.
- Produces: `email_de_baja`, `POST /api/admin/users/{id}/baja`; `GET /users` sin dados de baja; `DELETE /users/{id}` → 405.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_admin_usuarios_baja.py`:

```python
# ----------------------------------------------------------------- baja

import pytest  # noqa: E402

import smtp_config  # noqa: E402


def _baja(client, target, cabeceras=None):
    return client.post(f"/api/admin/users/{target}/baja", headers=cabeceras or _admin())


def test_email_de_baja_libera_la_direccion_y_cabe_en_la_columna():
    assert users_mod.email_de_baja("ana@x.io", 42, date(2026, 9, 12)) == "ana@x.io#baja-42-20260912"
    maximo = "a" * 249 + "@x.io"  # 254: el máximo aceptado
    assert len(users_mod.email_de_baja(maximo, 2**31 - 1, date(2026, 9, 12))) <= 320


def test_baja_inutiliza_la_cuenta_renombra_el_correo_y_audita(client, usuarios):
    u, email = usuarios(password="clave-de-la-baja-1")
    viejo = token_para(u)
    r = _baja(client, u)
    assert r.status_code == 200, r.text
    email_f, _rol, estado, tv, deleted_at, deleted_by = client.portal.call(_fila, u)
    assert (estado, tv, deleted_by) == ("deleted", 1, 1) and deleted_at is not None
    assert email_f == users_mod.email_de_baja(email, u, deleted_at.date())
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401
    r = client.post("/api/auth/login", json={"email": email, "password": "clave-de-la-baja-1"})
    client.cookies.clear()
    assert r.status_code == 401
    ((accion, detalle),) = client.portal.call(_acciones, u)
    assert (accion, json.loads(detalle)) == ("baja", {"email": email})


def test_la_baja_libera_el_correo(client, usuarios):
    u, email = usuarios()
    assert _baja(client, u).status_code == 200
    r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": "clave-larga-1"},
                    headers=_admin())
    assert r.status_code == 200, r.text
    client.portal.call(borrar_usuario, r.json()["user_id"])


def test_la_lista_no_muestra_a_los_dados_de_baja(client, usuarios):
    vivo, _ = usuarios()
    ido, _ = usuarios()
    assert _baja(client, ido).status_code == 200
    ids = [x["user_id"] for x in client.get("/api/admin/users", headers=_admin()).json()["users"]]
    assert vivo in ids and ido not in ids


def test_nadie_se_da_de_baja_a_si_mismo_ni_al_ultimo_superadmin(client, usuarios, monkeypatch):
    s, _ = usuarios(role="superadmin")
    r = _baja(client, s, auth(token_para(s, role="superadmin")))
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")

    async def ninguno(cur, excluido):
        return 0

    monkeypatch.setattr(users_mod, "otros_superadmins_activos", ninguno)
    r = _baja(client, s)
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    assert client.portal.call(_fila, s)[2] == "active"


def test_un_dado_de_baja_ya_no_se_toca(client, usuarios, monkeypatch):
    async def cargar():
        return smtp_config.SmtpSettings(host="mail.example.test", port=587, encryption="tls", user="u",
                                        password="p", from_name="Axioma", from_email="no-reply@example.test")

    monkeypatch.setattr(smtp_config, "cargar_settings", cargar)
    u, _ = usuarios()
    assert _baja(client, u).status_code == 200
    r = _baja(client, u)
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")
    assert _put(client, u, role="viewer").json()["detail"] == "usuario_no_encontrado"
    for accion in ("unlock", "revoke-sessions", "reset-link"):
        r = client.post(f"/api/admin/users/{u}/{accion}", headers=_admin())
        assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado"), accion


def test_delete_ya_no_existe(client, usuarios):
    u, _ = usuarios()
    assert client.delete(f"/api/admin/users/{u}", headers=_admin()).status_code == 405
    assert client.portal.call(_fila, u) is not None


def test_baja_solo_superadmin(client, usuarios):
    u, _ = usuarios()
    otro, _ = usuarios()
    assert _baja(client, otro, auth(token_para(u))).status_code == 403
```

En `backend/tests/test_admin_usuarios_guardas.py`, borrar el bloque desde la línea `# ---------------------------------------------------------------- DELETE` hasta el final de `test_delete_usa_las_guardas_y_no_el_literal_user_id_1` (su última línea es `    assert client.portal.call(_fila, s) is not None`). Su reemplazo es `test_nadie_se_da_de_baja_a_si_mismo_ni_al_ultimo_superadmin` y `test_delete_ya_no_existe`.

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_baja.py -q`
Expected: `test_email_de_baja...` → `AttributeError: ... 'email_de_baja'`; los de `/baja` → `404`/`405` (la ruta no existe); `test_la_lista_no_muestra...` falla antes, en la baja; `test_delete_ya_no_existe` → `200` (el DELETE todavía borra).

- [ ] **Step 3: Implementación mínima**

En `backend/api/admin/users.py`:

1. En `list_users`, cambiar `"FROM jax_users ORDER BY user_id"` por `"FROM jax_users WHERE status <> 'deleted' ORDER BY user_id"`.

2. Borrar la función `delete_user` completa (con su decorador `@router.delete("/users/{user_id}")`).

3. En `send_reset_link`, cambiar `"SELECT email, status FROM jax_users WHERE user_id = %s"` por `"SELECT email, status FROM jax_users WHERE user_id = %s AND status <> 'deleted'"` (un dado de baja responde 404, como en todas las acciones).

4. Al final del archivo agregar:

```python
def email_de_baja(email: str, user_id: int, fecha: date) -> str:
    """El correo de un dado de baja se renombra para LIBERAR la dirección
    (spec §3.5); el original queda en la auditoría. Cabe en VARCHAR(320)."""
    return f"{email}#baja-{user_id}-{fecha:%Y%m%d}"


@router.post("/users/{user_id}/baja")
async def dar_de_baja(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    """Eliminar = dar de baja (decisión de Fernando, spec §2): la cuenta queda
    inutilizable (status='deleted', versión nueva: toda sesión muere en el
    request siguiente), sale de la lista y libera el correo. Se conserva todo
    lo demás, historial incluido. Nada de DELETE."""
    actor_id = int(user.user_id)
    guarda_auto_accion(actor_id, user_id)
    ahora = datetime.utcnow().replace(microsecond=0)
    async with transaccion() as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual, email_actual = actual
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, rol_actual, "deleted")
        await cur.execute(
            "UPDATE jax_users SET status = 'deleted', deleted_at = %s, deleted_by = %s, email = %s, "
            "token_version = token_version + 1, failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (ahora, actor_id, email_de_baja(email_actual, user_id, ahora.date()), user_id),
        )
        await user_audit.registrar(cur, actor_id, user_id, "baja", {"email": email_actual}, _ip(request))
    return {"ok": True}
```

- [ ] **Step 4: Verde** — `tests/test_admin_usuarios_baja.py tests/test_admin_usuarios_guardas.py tests/test_contrasenas.py` → verde. Suite completa → 0 failed. Confirmar que no queda ningún `DELETE` de usuarios: `grep -n "router.delete" /home/fruiz/jax-platform/backend/api/admin/users.py` → sin salida.
- [ ] **Step 5: Pisos medidos** — sin DB +1 (`test_email_de_baja...` es puro); con DB: +8 de este archivo −1 borrado de guardas = +7. El comentario dice las dos cosas.
- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/admin/users.py backend/tests/test_admin_usuarios_baja.py backend/tests/test_admin_usuarios_guardas.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): la baja reemplaza al DELETE -- cuenta inutilizable, correo liberado, historial conservado"
```

---

### Task 4: Frontend — ConfirmacionSuma, email en la edición y "Dar de baja"

**Files:**
- Create: `frontend/src/components/ConfirmacionSuma.jsx`, `frontend/src/components/ConfirmacionSuma.test.jsx`
- Modify: `frontend/src/components/admin/EditarUsuarioModal.jsx` (archivo completo), `EditarUsuarioModal.test.jsx` (+1)
- Modify: `frontend/src/pages/admin/AdminUsers.jsx`, `AdminUsers.test.jsx` (+2)
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`, `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `POST /api/admin/users/{id}/baja`, `PUT` con `email`; `mensajeDeError`, `t.adminErrors` (etapas 3-4).
- Produces: `ConfirmacionSuma`, `numerosAlAzar` (ver Interfaces arriba).

- [ ] **Step 1: Tests que fallan**

Crear `frontend/src/components/ConfirmacionSuma.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import ConfirmacionSuma, { numerosAlAzar } from './ConfirmacionSuma'
import { I18nProvider } from '../i18n/index.jsx'

// Confirmación por suma (2026-09-12, admin usuarios etapa 5): el botón
// destructivo se habilita solo con la respuesta correcta. Reusable para todo
// borrado futuro; reemplaza a window.confirm.
function renderSuma(props = {}) {
  const onConfirmar = props.onConfirmar || vi.fn().mockResolvedValue()
  const onCancelar = props.onCancelar || vi.fn()
  render(
    <I18nProvider>
      <ConfirmacionSuma titulo="Dar de baja a b@x.io" mensaje="Irreversible." textoConfirmar="Dar de baja"
        onConfirmar={onConfirmar} onCancelar={onCancelar} numeros={[12, 7]} />
    </I18nProvider>
  )
  return { onConfirmar, onCancelar }
}

describe('ConfirmacionSuma', () => {
  it('el botón destructivo se habilita solo con la respuesta correcta', () => {
    renderSuma()
    const boton = screen.getByRole('button', { name: 'Dar de baja' })
    expect(boton).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '18' } })
    expect(boton).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '19' } })
    expect(boton).toBeEnabled()
  })

  it('con la respuesta correcta, confirmar llama a onConfirmar', async () => {
    const { onConfirmar } = renderSuma()
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '19' } })
    fireEvent.click(screen.getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(onConfirmar).toHaveBeenCalledTimes(1))
  })

  it('cancelar llama a onCancelar y no confirma', () => {
    const { onConfirmar, onCancelar } = renderSuma()
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    expect(onCancelar).toHaveBeenCalledTimes(1)
    expect(onConfirmar).not.toHaveBeenCalled()
  })

  it('los números al azar quedan en rango (a de 10 a 49, b de 1 a 9)', () => {
    expect(numerosAlAzar(() => 0)).toEqual([10, 1])
    expect(numerosAlAzar(() => 0.9999)).toEqual([49, 9])
  })
})
```

Agregar al final del `describe('EditarUsuarioModal', ...)` de `frontend/src/components/admin/EditarUsuarioModal.test.jsx` (antes de su `})` final):

```jsx
  it('el correo viaja recortado y solo si cambió', () => {
    const onGuardar = renderModal()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: '  nuevo@x.io  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(onGuardar).toHaveBeenCalledWith({ email: 'nuevo@x.io' })
  })
```

Agregar al final de `frontend/src/pages/admin/AdminUsers.test.jsx`:

```jsx
describe('AdminUsers — baja y alta', () => {
  it('dar de baja exige la suma y recién ahí llama al backend', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Dar de baja' }))
    const dialogo = screen.getByRole('dialog')
    const confirmar = within(dialogo).getByRole('button', { name: 'Dar de baja' })
    expect(confirmar).toBeDisabled()
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
    expect(api.post).not.toHaveBeenCalled()
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/baja'))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({ type: 'success', message: 'b@x.io fue dado de baja.' }))
  })

  it('el alta con un correo repetido muestra el error traducido', async () => {
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: 'email_ya_existe' } } })
    const { container } = renderUsers()
    fireEvent.click(await screen.findByText(/Nuevo usuario/))
    fireEvent.change(screen.getByPlaceholderText('Email'), { target: { value: 'b@x.io' } })
    fireEvent.change(container.querySelector('input[type="password"]'), { target: { value: 'clave-larga-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'Ya existe un usuario con ese correo.',
    }))
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run src/components src/pages/admin`
Expected: `ConfirmacionSuma.test.jsx` → `Failed to resolve import "./ConfirmacionSuma"`; el nuevo de EditarUsuarioModal → `Unable to find a label with the text of: Email`; los nuevos de AdminUsers → `Unable to find role="button" and name "Dar de baja"`, y el del alta → toast con el mensaje genérico (`email_ya_existe` todavía no está en `adminErrors`).

- [ ] **Step 3: Implementación mínima**

Crear `frontend/src/components/ConfirmacionSuma.jsx`:

```jsx
import { useState } from 'react'
import { useI18n } from '../i18n/index.jsx'

// Confirmación por suma (2026-09-12, admin usuarios etapa 5, spec §3.5).
// "Resolvé a + b = ?": el botón destructivo se habilita solo con la respuesta
// correcta. Reusable para todo borrado futuro; `numeros` existe para los
// tests (por defecto, al azar).
export function numerosAlAzar(aleatorio = Math.random) {
  return [10 + Math.floor(aleatorio() * 40), 1 + Math.floor(aleatorio() * 9)]
}

export default function ConfirmacionSuma({ titulo, mensaje, textoConfirmar, onConfirmar, onCancelar, numeros }) {
  const { t } = useI18n()
  const [[a, b]] = useState(() => numeros || numerosAlAzar())
  const [respuesta, setRespuesta] = useState('')
  const [enviando, setEnviando] = useState(false)
  const correcta = respuesta.trim() !== '' && Number(respuesta) === a + b

  async function confirmar(e) {
    e.preventDefault()
    if (!correcta) return
    setEnviando(true)
    try {
      await onConfirmar()
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="confirmacion-suma-titulo"
        className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h2 id="confirmacion-suma-titulo" className="text-sm font-semibold text-slate-200 mb-2">{titulo}</h2>
        <p className="text-sm text-slate-400 mb-4">{mensaje}</p>
        <form onSubmit={confirmar} className="space-y-3">
          <label htmlFor="confirmacion-suma-respuesta" className="block text-sm text-slate-300">{t.confirmSumLabel(a, b)}</label>
          <input
            id="confirmacion-suma-respuesta"
            type="number"
            inputMode="numeric"
            autoFocus
            value={respuesta}
            onChange={(e) => setRespuesta(e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
          />
          <p className="text-xs text-slate-500">{t.confirmSumHint}</p>
          <div className="flex gap-2 justify-end pt-2">
            <button type="button" onClick={onCancelar} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
            <button type="submit" disabled={!correcta || enviando} className="px-4 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors">
              {textoConfirmar}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
```

Reemplazar `frontend/src/components/admin/EditarUsuarioModal.jsx` completo por:

```jsx
import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'

// Editar correo, rol y estado (2026-09-12, admin usuarios etapas 3 y 5).
// Manda SOLO lo que cambió (el correo, recortado); qué está permitido lo
// decide el backend (formato, único, último superadmin, auto-acciones) y el
// padre muestra el error traducido. `deleted` no es una opción: eso es la baja.
const ROLES = ['superadmin', 'operator', 'viewer']
const ESTADOS = ['active', 'inactive']
const CAMPO = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500'

export default function EditarUsuarioModal({ usuario, onGuardar, onCerrar }) {
  const { t } = useI18n()
  const [email, setEmail] = useState(usuario.email)
  const [role, setRole] = useState(usuario.role)
  const [status, setStatus] = useState(usuario.status)
  const [guardando, setGuardando] = useState(false)

  const cambios = {}
  if (email.trim() !== usuario.email) cambios.email = email.trim()
  if (role !== usuario.role) cambios.role = role
  if (status !== usuario.status) cambios.status = status
  const hayCambios = Object.keys(cambios).length > 0

  async function enviar(e) {
    e.preventDefault()
    setGuardando(true)
    try {
      await onGuardar(cambios)
    } finally {
      setGuardando(false)
    }
  }

  const etiquetaEstado = { active: t.adminUserActive, inactive: t.adminUserInactive }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="editar-usuario-titulo"
        className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h2 id="editar-usuario-titulo" className="text-sm font-semibold text-slate-200 mb-4">{t.adminUserEditTitle(usuario.email)}</h2>
        <form onSubmit={enviar} className="space-y-3">
          <div>
            <label htmlFor="editar-email" className="block text-xs text-slate-400 mb-1">{t.adminUserEmail}</label>
            <input id="editar-email" type="email" maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} className={CAMPO} />
          </div>
          <div>
            <label htmlFor="editar-rol" className="block text-xs text-slate-400 mb-1">{t.adminUserRole}</label>
            <select id="editar-rol" value={role} onChange={(e) => setRole(e.target.value)} className={CAMPO}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="editar-estado" className="block text-xs text-slate-400 mb-1">{t.adminUserStatus}</label>
            <select id="editar-estado" value={status} onChange={(e) => setStatus(e.target.value)} className={CAMPO}>
              {ESTADOS.map((s) => <option key={s} value={s}>{etiquetaEstado[s]}</option>)}
            </select>
          </div>
          <div className="flex gap-2 justify-end pt-2">
            <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
            <button type="submit" disabled={!hayCambios || guardando} className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors">{t.adminUserSave}</button>
          </div>
        </form>
      </div>
    </div>
  )
}
```

En `frontend/src/pages/admin/AdminUsers.jsx`:
- después de `import HistorialUsuario from '../../components/admin/HistorialUsuario'` agregar `import ConfirmacionSuma from '../../components/ConfirmacionSuma'`;
- después de `  const [historialDe, setHistorialDe] = useState(null)` agregar `  const [dandoDeBaja, setDandoDeBaja] = useState(null)`;
- reemplazar la función `handleDelete` completa:

```jsx
  async function handleDelete(u) {
    if (!window.confirm(t.adminDeleteConfirm(u.email))) return
    try {
      await api.delete(`/admin/users/${u.user_id}`)
      load()
    } catch (err) {
      avisarError(err)
    }
  }
```

por:

```jsx
  // Eliminar = dar de baja (etapa 5): confirmación por suma, nunca window.confirm.
  async function confirmarBaja() {
    const u = dandoDeBaja
    try {
      await api.post(`/admin/users/${u.user_id}/baja`)
      setDandoDeBaja(null)
      avisarExito(t.adminBajaDone(u.email))
      load()
    } catch (err) {
      avisarError(err)
    }
  }
```

- reemplazar el botón

```jsx
                    <button onClick={() => handleDelete(u)} className={`${ACCION} bg-red-900/40 hover:bg-red-900/60 text-red-400`}>
                      {t.adminUserDelete}
                    </button>
```

por:

```jsx
                    <button onClick={() => setDandoDeBaja(u)} className={`${ACCION} bg-red-900/40 hover:bg-red-900/60 text-red-400`}>
                      {t.adminUserBaja}
                    </button>
```

- después de la línea `{historialDe && <HistorialUsuario usuario={historialDe} onCerrar={() => setHistorialDe(null)} />}` agregar:

```jsx
      {dandoDeBaja && (
        <ConfirmacionSuma
          titulo={t.adminBajaTitle(dandoDeBaja.email)}
          mensaje={t.adminBajaMessage}
          textoConfirmar={t.adminUserBaja}
          onConfirmar={confirmarBaja}
          onCancelar={() => setDandoDeBaja(null)}
        />
      )}
```

En `frontend/src/i18n/es.js`:
- en `adminErrors`, reemplazar `    password_larga: 'La contraseña es demasiado larga (máximo 72 bytes; los acentos ocupan 2).',\n  },` por:

```js
    password_larga: 'La contraseña es demasiado larga (máximo 72 bytes; los acentos ocupan 2).',
    email_invalido: 'El correo no es válido.',
    email_ya_existe: 'Ya existe un usuario con ese correo.',
  },
```

- insertar ANTES de `  // Restaurar tareas pendientes (useJaxStore.js)`:

```js
  // Editar y dar de baja — etapa 5 (2026-09-12)
  adminUserBaja: 'Dar de baja',
  adminBajaTitle: (email) => `Dar de baja a ${email}`,
  adminBajaMessage: 'La cuenta queda inutilizable, sale de la lista y libera el correo. Su historial se conserva.',
  adminBajaDone: (email) => `${email} fue dado de baja.`,
  confirmSumLabel: (a, b) => `Resolvé ${a} + ${b} = ?`,
  confirmSumHint: 'Escribí el resultado para habilitar el botón.',

```

En `frontend/src/i18n/en.js`:
- en `adminErrors`, reemplazar `    password_larga: 'The password is too long (maximum 72 bytes; accented letters take 2).',\n  },` por:

```js
    password_larga: 'The password is too long (maximum 72 bytes; accented letters take 2).',
    email_invalido: 'The email is not valid.',
    email_ya_existe: 'A user with that email already exists.',
  },
```

- insertar ANTES de `  // Restoring pending tasks (useJaxStore.js)`:

```js
  // Edit and remove — stage 5 (2026-09-12)
  adminUserBaja: 'Remove',
  adminBajaTitle: (email) => `Remove ${email}`,
  adminBajaMessage: 'The account becomes unusable, leaves the list and frees the email. Its history is kept.',
  adminBajaDone: (email) => `${email} was removed.`,
  confirmSumLabel: (a, b) => `Solve ${a} + ${b} = ?`,
  confirmSumHint: 'Type the result to enable the button.',

```

Verificar que no queda uso de las claves viejas: `grep -rn "adminDeleteConfirm\|adminUserDelete\|window.confirm" /home/fruiz/jax-platform/frontend/src/pages/admin/AdminUsers.jsx` → sin salida.

- [ ] **Step 4: Verde** — `cd /home/fruiz/jax-platform/frontend && npx vitest run` → piso actual + 7 (4 + 1 + 2), 0 fallidos.
- [ ] **Step 5: Piso de vitest** medido (esperado 114 si era 107) con su comentario.
- [ ] **Step 6: Claro/oscuro a mano** — vite en `127.0.0.1:5174`: modal de edición con email, ConfirmacionSuma y toasts legibles en los dos temas.
- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/jax-platform add frontend/src/components/ConfirmacionSuma.jsx frontend/src/components/ConfirmacionSuma.test.jsx frontend/src/components/admin/EditarUsuarioModal.jsx frontend/src/components/admin/EditarUsuarioModal.test.jsx frontend/src/pages/admin/AdminUsers.jsx frontend/src/pages/admin/AdminUsers.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(ui): dar de baja con confirmación por suma; editar el correo"
```

---

### Task 5: PR, CI por headSha, despliegue y verificación en vivo

- [ ] **Step 1: Suite local completa** (con DB, sin DB, vitest) = pisos del archivo.
- [ ] **Step 2: PR** — rama `feat/admin-usuarios-5-baja`, título "Admin usuarios · etapa 5: editar y dar de baja (ConfirmacionSuma)".
- [ ] **Step 3: Gate por headSha** — `gh pr checks <N> --repo fjruizhn/jax-platform` sin nada fuera de `SUCCESS`; `gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid` == `git -C /home/fruiz/jax-platform ls-remote origin refs/heads/feat/admin-usuarios-5-baja`. Recién ahí `gh pr merge <N> --repo fjruizhn/jax-platform --merge`.
- [ ] **Step 4: Respaldo de `jax_users` con restauración probada** (la migración hace `ALTER ... MODIFY email` en producción): el mismo procedimiento de la etapa 2, Task 6, Step 1, con el nombre `jax_users-pre-admin-usuarios-5-<fecha>.sql`. Los conteos de origen y de restauración tienen que coincidir.
- [ ] **Step 5: Backend** — `git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only`; `sudo -n /usr/bin/systemctl restart jax-platform.service`; health `200`; journal sin tracebacks. Verificar el esquema:

```bash
set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "
SHOW COLUMNS FROM jax_users WHERE Field IN ('email', 'deleted_at', 'deleted_by');
SHOW INDEX FROM jax_users WHERE Column_name = 'email';
EXPLAIN SELECT user_id, email FROM jax_users WHERE status <> 'deleted' ORDER BY user_id;
EXPLAIN SELECT 1 FROM jax_users WHERE email = 'x@example.invalid' AND user_id <> 1;"
```
Expected: `email varchar(320)`, el índice UNIQUE de `email` sigue ahí; el EXPLAIN del chequeo de unicidad usa el UNIQUE de `email`. El de la lista recorre la tabla por `PRIMARY` (sin `Using filesort`): `jax_users` tiene decenas de filas y `status <> 'deleted'` no es selectivo. Anotarlo tal cual.

- [ ] **Step 6: Frontend** — build, backup y rsync en dos saltos con `--exclude .user.ini`, hash servido:

```bash
cd /home/fruiz/jax-platform/frontend && npm run build
set -a; . /etc/jax/.env; set +a
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-admin-usuarios-5-$(date +%Y%m%d-%H%M%S)"
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
ls /home/fruiz/jax-platform/frontend/dist/assets/index-*.js; curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

- [ ] **Step 7: Verificación en vivo del spec §5 (avisar a Fernando; usuario de prueba)**
  1. Crear un usuario de prueba `verificacion-etapa5@example.invalid` desde Admin; iniciar sesión con él en una ventana privada.
  2. Editar su correo desde el modal y volver a ponerlo: el historial muestra dos "Cambio de correo".
  3. "Dar de baja": el botón rojo del diálogo arranca deshabilitado, se habilita solo con la suma correcta. Tras confirmar, sale de la lista, y en la ventana privada el request siguiente lo saca al login; volver a entrar con sus credenciales da "Usuario o contraseña incorrectos".
  4. **La baja libera el correo:** crear otra vez `verificacion-etapa5@example.invalid` → se crea. Darlo de baja también (no queda nada de prueba activo).
  5. En la base: `SELECT user_id, email, status, deleted_at, deleted_by FROM jax_users WHERE email LIKE 'verificacion-etapa5@example.invalid%'` → dos filas `deleted`, con el sufijo `#baja-<id>-<yyyymmdd>`, `deleted_by` = id de Fernando; su `user_admin_audit` conserva `create`, `update_email` y `baja`.
- [ ] **Step 8: Biblioteca** — entrada fechada en `/home/fruiz/jax/DEUDA.md` (PR propio en `jax`): sha, `index-*.js`, EXPLAIN tal cual, resultado de los 5 puntos, respaldo. Registrar también el cierre de la ronda de administración de usuarios (etapas 1-5) con los pendientes que queden, con fecha.

---

## Autorrevisión (hecha al escribir el plan)

- **Cobertura de §3.5:** `PUT` valida email (formato, 254, único), rol (conjunto cerrado) y estado (`active`/`inactive`; `deleted` solo por la baja), con códigos estables traducidos (Task 2); baja con `status='deleted'`, `deleted_at`, `deleted_by`, email renombrado y `token_version + 1` (Task 3); `GET /users` sin dados de baja (Task 3); el login ya rechaza lo que no es `active` (test de login en la Task 3); ConfirmacionSuma reusable, que habilita solo con la respuesta correcta (Task 4); acciones de la tabla: editar, enviar enlace, desbloquear, cerrar sesiones, historial y dar de baja, con errores en toasts traducidos (etapas 3-5). Auditoría: `update_email` y `baja`.
- **Diferencia con el spec, declarada:** la columna `email` pasa de 100 a 320. El spec valida hasta 254, y el renombre de la baja agrega hasta 26 caracteres. Con 100, un correo válido de más de 74 caracteres daba "Data too long" al darlo de baja.
- **Fuera de alcance, respetado:** la memoria del usuario dado de baja no se toca (derecho al olvido, §4).
- **Placeholders:** `<N>`, `<fecha>` y los conteos medidos son de ejecución.
- **Nombres:** `email_de_baja`, `dar_de_baja`, `_leer_para_actualizar -> (role, status, email)`, `ConfirmacionSuma({titulo, mensaje, textoConfirmar, onConfirmar, onCancelar, numeros})`, `numerosAlAzar`, iguales en tests, código e Interfaces.
