# Administración de usuarios · Etapa 3 — Invariantes, auto-acciones prohibidas y auditoría

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el sistema no pueda quedarse sin superadmin activo, que nadie se degrade/desactive/borre a sí mismo, que cada acción de administración quede registrada con actor, destino, detalle e IP, y que la tabla de usuarios muestre errores traducidos, "cerrar sesiones" y un historial.

**Architecture:** Migración idempotente de la tabla `user_admin_audit` (índice `(target_user_id, ts)`) y de un índice `jax_users(role, status)` para el conteo de superadmins. Un módulo `backend/user_audit.py` escribe (dentro de la misma transacción que el cambio) y lee el historial. `backend/db/transaccion.py` da un context manager async con BEGIN/COMMIT/ROLLBACK sobre el pool `autocommit=True`. `api/admin/users.py` aplica las guardas (`guarda_auto_accion`, `exigir_invariante` con `SELECT ... FOR UPDATE`), sube `token_version` en cambio de rol/estado y en "cerrar sesiones", quita el `user_id == 1` literal y expone `GET /users/{id}/audit`. En el frontend: `Toast` montado en Admin, un traductor único de errores de admin, modal de edición (rol y estado), "Cerrar sesiones" e historial.

**Tech Stack:** FastAPI, aiomysql (transacciones explícitas), MariaDB JSON, pytest; React 19, vitest + Testing Library.

**Spec:** `/home/fruiz/jax-platform/docs/superpowers/specs/2026-09-12-administracion-usuarios-design.md` (§3.3; de §3.2 "token_version sube en: cambio de rol, cambio de estado, y la acción cerrar sus sesiones"; §5; §6 punto 3).

## Global Constraints

- **Repo:** `/home/fruiz/jax-platform`; rama desde `master` con las etapas 1 y 2 mergeadas: `git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform switch -c feat/admin-usuarios-3-invariantes origin/master`. Siempre `git -C <ruta>`.
- **TDD** con el rojo visto por el motivo que dice cada paso.
- **Backend tests:** `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest ...`; con `client` → `jax_memory_test`. Identidades reales con `tests/identidades.py` y el fixture `usuarios` (etapa 2). **Ningún test modifica `user_id=1`**: se lo usa solo como ACTOR (`token_para(1, role="superadmin")`), nunca como destino.
- **Pisos exactos** en `.github/workflows/policy.yml` (`PISO_PASSED`, `JAX_CI_MIN_PASSED`, `numPassedTests`): leer su valor ACTUAL antes de tocarlos y subirlos con el número MEDIDO y un comentario.
- **P10** (`# fail-soft:` en la misma línea de todo `except Exception`), **BackgroundTasks** solo con `add_safe_task`, **nada bloqueante** en async.
- **Códigos estables** en `detail`: `auto_accion_prohibida` (403), `ultimo_superadmin` (409), `usuario_no_encontrado` (404), `rol_invalido` (400), `estado_invalido` (400). El frontend los traduce con `t.adminErrors`.
- **Invariante bajo concurrencia:** el conteo de otros superadmins activos y el cambio van en la MISMA transacción, con `FOR UPDATE`. Si no, dos admins que se degradan mutuamente al mismo tiempo dejan el sistema sin superadmin.
- **Auditoría en la misma transacción que el cambio:** o quedan los dos o ninguno.
- **LAS CUATRO:** el historial filtra por `target_user_id` y ordena por `ts` → índice `(target_user_id, ts)`; el conteo filtra por `role, status` → índice `(role, status)`. Verificado en producción con `EXPLAIN` (Task 5).
- **i18n es/en** y **modo claro/oscuro** (clases `slate` que `index.css` sobrescribe; rojos/verdes ya usados en la UI).
- **Migraciones** idempotentes en `backend/db/migrations.py`; sin ALTER a mano. Commits sin `--no-verify`.
- **Fuera de esta etapa:** contraseñas, reset por admin, Mi cuenta (etapa 4); email editable, baja y ConfirmacionSuma (etapa 5). El `DELETE /users/{id}` sigue existiendo en esta etapa, con las guardas nuevas; la etapa 5 lo reemplaza por la baja. El campo `password` sale de `PUT /users/{id}` (Task 2): la contraseña la cambia el dueño en "Mi cuenta" y el admin manda un enlace (etapa 4).
- **Cierre:** PR → CI verde por `headSha` → despliegue backend + frontend → verificación en vivo.

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/db/migrations.py` | Modificar | tabla `user_admin_audit`; lista `_INDEXES` + `_index_exists`; índice `idx_jax_users_role_status` |
| `backend/db/transaccion.py` | Crear | `transaccion()`: BEGIN/COMMIT/ROLLBACK |
| `backend/user_audit.py` | Crear | `ACCIONES`, `registrar(cur, ...)`, `historial(target)` |
| `backend/api/admin/users.py` | Modificar | guardas, invariante, `token_version`, auditoría, `revoke-sessions`, `GET audit` |
| `backend/tests/identidades.py` | Modificar | `borrar_usuario` borra también su auditoría |
| `backend/tests/test_user_audit.py` | Crear | tabla, índices, transacción, `registrar` |
| `backend/tests/test_admin_usuarios_guardas.py` | Crear | guardas, invariante, acciones auditadas, historial |
| `frontend/src/pages/Admin.jsx` | Modificar | monta `<Toast />` (hoy los toasts no se ven en Admin) |
| `frontend/src/pages/admin/erroresAdmin.js` | Crear | `codigoDeError`, `mensajeDeError` |
| `frontend/src/components/admin/EditarUsuarioModal.jsx` | Crear | editar rol y estado |
| `frontend/src/components/admin/HistorialUsuario.jsx` | Crear | historial corto |
| `frontend/src/pages/admin/AdminUsers.jsx` | Modificar | acciones nuevas; errores en toasts; i18n de "intentos" y fecha |
| `frontend/src/components/admin/EditarUsuarioModal.test.jsx`, `frontend/src/pages/admin/AdminUsers.test.jsx` | Crear | tests vitest |
| `frontend/src/i18n/es.js`, `en.js` | Modificar | textos |
| `.github/workflows/policy.yml` | Modificar | pisos |

## Interfaces

**Consumes (etapas anteriores):**

```python
# etapa 2
auth.middleware.require_superadmin -> AuthUser   # rol leído de la base
auth.models.AuthUser(user_id: str, tenant_id: str, role: str, email=None, token_version: int = 0)
tests.identidades: sql(consulta, args=(), fetch=False), crear_usuario(...), borrar_usuario(user_id),
                   token_para(user_id, role="operator", tv=0, tenant_id="1", tipo="access"), auth(token)
conftest fixture usuarios(client) -> crear(**kw) -> (user_id, email)
# existentes
auth.rate_limit.client_ip(request, trusted: frozenset[str]) -> str ; auth.rate_limit.TRUSTED_PROXIES
```
```js
// etapa 1
t.smtpServerSaid(texto)   // "Respuesta del servidor: ..."
```

**Produces (las usan las etapas 4 y 5):**

```python
# backend/db/transaccion.py
@asynccontextmanager
async def transaccion():  # yield cursor; COMMIT al salir, ROLLBACK ante cualquier excepción

# backend/user_audit.py
ACCIONES: frozenset[str]  # create, update_email, update_role, update_status, reset_link_sent,
                          # unlock, sessions_revoked, baja, password_changed_self, password_reset_completed
async def registrar(cur, actor_user_id: int, target_user_id: int, action: str,
                    detail: dict | None = None, ip: str | None = None) -> None   # ValueError si action no está en ACCIONES
async def historial(target_user_id: int, limite: int = 50) -> list[dict]
    # [{"id", "ts" (iso), "actor_user_id", "actor_email", "action", "detail" (dict|None), "ip"}], más nuevo primero

# backend/api/admin/users.py
VALID_ROLES = {"superadmin", "operator", "viewer"}
ESTADOS_EDITABLES = {"active", "inactive"}
def _ip(request: Request) -> str
def guarda_auto_accion(actor_id: int, target_id: int) -> None                      # 403 auto_accion_prohibida
def pierde_superadmin_activo(rol_actual, estado_actual, nuevo_rol, nuevo_estado) -> bool
async def otros_superadmins_activos(cur, excluido: int) -> int                     # FOR UPDATE
async def exigir_invariante(cur, target_id, rol_actual, estado_actual, nuevo_rol, nuevo_estado) -> None  # 409 ultimo_superadmin
async def _leer_para_actualizar(cur, user_id: int) -> tuple | None                 # (role, status) FOR UPDATE
# Endpoints: PUT /api/admin/users/{id} {role?, status?} (extra=forbid), POST /users/{id}/unlock,
#            POST /users/{id}/revoke-sessions, GET /users/{id}/audit -> {"entries": [...]}, DELETE /users/{id} (con guardas)
```
```js
// frontend/src/pages/admin/erroresAdmin.js
export function codigoDeError(err) -> string | undefined
export function mensajeDeError(t, err) -> string   // t.adminErrors[code] || t.adminErrorGeneric (+ respuesta del servidor si viene)
// frontend/src/components/admin/EditarUsuarioModal.jsx
export default function EditarUsuarioModal({ usuario, onGuardar /* (cambios) => Promise */, onCerrar })
// frontend/src/components/admin/HistorialUsuario.jsx
export default function HistorialUsuario({ usuario, onCerrar })
// i18n: t.adminErrors (objeto código → texto), t.adminErrorGeneric, t.adminAuditActions (acción → texto)
```

---

### Task 1: Tabla de auditoría, índices, transacción y `user_audit`

**Files:**
- Modify: `backend/db/migrations.py`
- Create: `backend/db/transaccion.py`, `backend/user_audit.py`
- Modify: `backend/tests/identidades.py` (`borrar_usuario`)
- Test: `backend/tests/test_user_audit.py`
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `db.migrations._TABLES`, `_COLUMN_WIDENS`, `run_migrations`; `db.connection.get_pool` (pool `autocommit=True`).
- Produces: `transaccion`, `user_audit.{ACCIONES, registrar, historial, SQL_HISTORIAL}`, tabla e índices.

- [ ] **Step 1: Tests que fallan**

Crear `backend/tests/test_user_audit.py`:

```python
"""Registro de acciones de administración (2026-09-12, admin usuarios etapa 3).

Hasta acá la única auditoría era credential_audit (credenciales de
proveedores): cambiar el rol de alguien, desactivarlo o borrarlo no dejaba
rastro (spec §1, hallazgo 5).
"""
import asyncio
import uuid

import pytest

import user_audit
from tests.identidades import sql


class _CursorFalso:
    def __init__(self):
        self.ejecutado = []

    async def execute(self, consulta, args=()):
        self.ejecutado.append((consulta, args))


def test_registrar_solo_acepta_acciones_conocidas():
    cur = _CursorFalso()
    asyncio.run(user_audit.registrar(cur, 1, 2, "update_role", {"from": "superadmin", "to": "operator"}, "203.0.113.5"))
    ((consulta, args),) = cur.ejecutado
    assert "INSERT INTO user_admin_audit" in consulta
    assert args == (1, 2, "update_role", '{"from": "superadmin", "to": "operator"}', "203.0.113.5")
    with pytest.raises(ValueError):
        asyncio.run(user_audit.registrar(cur, 1, 2, "borrar_todo"))
    assert len(cur.ejecutado) == 1, "una acción desconocida no se escribe"


def test_tabla_e_indices(client):
    filas = client.portal.call(
        sql,
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() "
        "AND INDEX_NAME IN ('idx_user_admin_audit_target_ts', 'idx_jax_users_role_status') "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX", (), True)
    assert [tuple(f) for f in filas] == [
        ("jax_users", "idx_jax_users_role_status", 1, "role"),
        ("jax_users", "idx_jax_users_role_status", 2, "status"),
        ("user_admin_audit", "idx_user_admin_audit_target_ts", 1, "target_user_id"),
        ("user_admin_audit", "idx_user_admin_audit_target_ts", 2, "ts"),
    ]


def test_transaccion_revierte_todo_si_algo_falla(client):
    from db.transaccion import transaccion
    marca = f"test-tx-{uuid.uuid4().hex[:8]}"

    async def falla_a_mitad():
        async with transaccion() as cur:
            await cur.execute("INSERT INTO axioma_config (config_key, config_value) VALUES (%s, 'x')", (marca,))
            raise RuntimeError("a mitad de camino")

    with pytest.raises(RuntimeError):
        client.portal.call(falla_a_mitad)
    ((cuantas,),) = client.portal.call(sql, "SELECT COUNT(*) FROM axioma_config WHERE config_key = %s", (marca,), True)
    assert cuantas == 0
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_user_audit.py -q`
Expected: error de colección `ModuleNotFoundError: No module named 'user_audit'`.

- [ ] **Step 3: Implementación mínima**

Crear `backend/db/transaccion.py`:

```python
"""Transacción explícita sobre el pool (2026-09-12, admin usuarios etapa 3).

El pool de db/connection.py es autocommit=True: cada sentencia se confirma
sola. Cuando un cambio y su auditoría (o un conteo y el cambio que ese conteo
autoriza) tienen que ir juntos, esto abre BEGIN, confirma al salir y revierte
ante CUALQUIER excepción -- incluida la HTTPException de una guarda.
"""
from contextlib import asynccontextmanager

from .connection import get_pool


@asynccontextmanager
async def transaccion():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor() as cur:
                yield cur
        except BaseException:
            await conn.rollback()
            raise
        await conn.commit()
```

Crear `backend/user_audit.py`:

```python
"""Registro de acciones de administración de usuarios (2026-09-12, admin
usuarios etapa 3, spec §3.3).

`registrar` recibe el CURSOR de la transacción del cambio: o quedan el cambio
y su registro, o ninguno. Una acción fuera de ACCIONES es un error de
programación y lanza antes de escribir. La IP la pone quien llama con
auth.rate_limit.client_ip (X-Real-IP solo si viene del proxy de confianza).
"""
from __future__ import annotations

import json

from db.connection import get_pool

ACCIONES = frozenset({
    "create", "update_email", "update_role", "update_status", "reset_link_sent",
    "unlock", "sessions_revoked", "baja", "password_changed_self", "password_reset_completed",
})

# Filtra por target_user_id y ordena por ts: idx_user_admin_audit_target_ts.
# El JOIN a jax_users va por PRIMARY (eq_ref).
SQL_HISTORIAL = (
    "SELECT a.id, a.ts, a.actor_user_id, u.email, a.action, a.detail, a.ip "
    "FROM user_admin_audit a LEFT JOIN jax_users u ON u.user_id = a.actor_user_id "
    "WHERE a.target_user_id = %s ORDER BY a.ts DESC, a.id DESC LIMIT %s"
)


async def registrar(cur, actor_user_id: int, target_user_id: int, action: str,
                    detail: dict | None = None, ip: str | None = None) -> None:
    if action not in ACCIONES:
        raise ValueError(f"acción de auditoría desconocida: {action!r}")
    await cur.execute(
        "INSERT INTO user_admin_audit (actor_user_id, target_user_id, action, detail, ip) "
        "VALUES (%s, %s, %s, %s, %s)",
        (actor_user_id, target_user_id, action,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None, ip),
    )


async def historial(target_user_id: int, limite: int = 50) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_HISTORIAL, (target_user_id, limite))
            filas = await cur.fetchall()
    return [
        {
            "id": f[0],
            "ts": f[1].isoformat() if f[1] else None,
            "actor_user_id": f[2],
            "actor_email": f[3],
            "action": f[4],
            "detail": json.loads(f[5]) if f[5] else None,
            "ip": f[6],
        }
        for f in filas
    ]
```

En `backend/db/migrations.py`:

1. Inmediatamente ANTES de la línea `_TABLES = [` agregar:

```python
# Registro de acciones de administración de usuarios (2026-09-12, admin
# usuarios etapa 3, spec §3.3). Sin FK a jax_users a propósito: el historial
# sobrevive a lo que le pase a la fila (y la baja no borra filas).
CREATE_USER_ADMIN_AUDIT = """
CREATE TABLE IF NOT EXISTS user_admin_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  actor_user_id INT NOT NULL,
  target_user_id INT NOT NULL,
  action VARCHAR(40) NOT NULL,
  detail JSON NULL,
  ip VARCHAR(45) NULL,
  INDEX idx_user_admin_audit_target_ts (target_user_id, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

```

2. En `_TABLES`, después de `    ("facet_health_alert", CREATE_FACET_HEALTH_ALERT),` agregar `    ("user_admin_audit", CREATE_USER_ADMIN_AUDIT),`.

3. Inmediatamente DESPUÉS del cierre de la lista `_COLUMN_WIDENS` (la línea `]` que sigue a `"ALTER TABLE axioma_usage MODIFY COLUMN model VARCHAR(100) NOT NULL",` y `    ),`) agregar:

```python


# (tabla, índice, DDL) -- agrega un índice a una tabla EXISTENTE si falta.
# Las tablas nuevas lo declaran en su CREATE TABLE.
# idx_jax_users_role_status: el conteo de superadmins activos de la
# invariante (api/admin/users.py::otros_superadmins_activos) filtra por
# role y status, con FOR UPDATE (2026-09-12, admin usuarios etapa 3).
_INDEXES = [
    ("jax_users", "idx_jax_users_role_status",
     "ALTER TABLE jax_users ADD INDEX idx_jax_users_role_status (role, status)"),
]


async def _index_exists(cur, table_name: str, index_name: str) -> bool:
    await cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    row = await cur.fetchone()
    return bool(row and row[0] > 0)
```

4. En `run_migrations`, después del bloque:

```python
            for table_name, column_name, min_length, ddl in _COLUMN_WIDENS:
                if await _column_too_narrow(cur, table_name, column_name, min_length):
                    await cur.execute(ddl)
```

agregar:

```python

            for table_name, index_name, ddl in _INDEXES:
                if not await _index_exists(cur, table_name, index_name):
                    await cur.execute(ddl)
```

En `backend/tests/identidades.py`, reemplazar `borrar_usuario` por:

```python
async def borrar_usuario(user_id):
    # La auditoría no tiene FK (a propósito): se limpia a mano lo que dejó el test.
    await sql("DELETE FROM user_admin_audit WHERE target_user_id = %s OR actor_user_id = %s", (user_id, user_id))
    await sql("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
    await sql("DELETE FROM jax_users WHERE user_id = %s", (user_id,))
```

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_user_audit.py tests/test_no_fail_open_except.py -q`
Expected: `3 passed` + el scanner verde (el `except BaseException` re-lanza; no es un except-pass).

- [ ] **Step 5: Pisos medidos** — sin DB: piso actual + 1; con DB: piso actual + 3. Comentarios "admin usuarios etapa 3, Task 1".

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/db/migrations.py backend/db/transaccion.py backend/user_audit.py backend/tests/identidades.py backend/tests/test_user_audit.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): tabla user_admin_audit, índice role/status y transacción explícita"
```

---

### Task 2: Guardas de auto-acción e invariante de superadmin en `PUT` y `DELETE`

**Files:**
- Modify: `backend/api/admin/users.py` (imports, helpers nuevos, `update_user`, `delete_user`)
- Test: `backend/tests/test_admin_usuarios_guardas.py`
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `transaccion()`, `user_audit.registrar` (Task 1); `tests.identidades`, fixture `usuarios` (etapa 2).
- Produces: `guarda_auto_accion`, `pierde_superadmin_activo`, `otros_superadmins_activos`, `exigir_invariante`, `_leer_para_actualizar`, `_ip`, `ESTADOS_EDITABLES`; `PUT` con `extra="forbid"`; `DELETE` con guardas.

- [ ] **Step 1: Confirmar que ningún test existente depende del PUT/DELETE viejo**

Run: `grep -rn "admin/users" /home/fruiz/jax-platform/backend/tests`
Expected: solo archivos de esta ronda. Si aparece otro, leerlo antes de seguir: su expectativa puede cambiar con las guardas.

- [ ] **Step 2: Tests que fallan**

Crear `backend/tests/test_admin_usuarios_guardas.py`:

```python
"""Invariantes y guardas de administración de usuarios (2026-09-12, etapa 3).

Antes: update_user no tenía guardas (se podía degradar o desactivar al último
superadmin, o a uno mismo) y la única protección era `user_id == 1` literal
en delete_user (spec §1, hallazgo 2). Ahora: siempre al menos un superadmin
activo (409), nadie actúa sobre sí mismo (403), y el literal desaparece.

user_id=1 aparece SOLO como actor. Para "el último superadmin" se reemplaza
otros_superadmins_activos: en jax_memory_test user 1 siempre es un
superadmin activo, así que el caso real no se puede armar sin tocarlo. El
conteo en sí se prueba aparte, contra la base.
"""
import json

import pytest
from fastapi import HTTPException

from api.admin import users as users_mod
from tests.identidades import auth, sql, token_para


def _admin():
    return auth(token_para(1, role="superadmin"))


def _put(client, target, cabeceras=None, **cuerpo):
    return client.put(f"/api/admin/users/{target}", json=cuerpo, headers=cabeceras or _admin())


async def _fila(user_id):
    filas = await sql("SELECT role, status, token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return tuple(filas[0]) if filas else None


async def _auditoria(target):
    filas = await sql("SELECT actor_user_id, action, detail, ip FROM user_admin_audit "
                      "WHERE target_user_id = %s ORDER BY id", (target,), True)
    return [tuple(f) for f in filas]


async def _ninguno(cur, excluido):
    return 0


# ---------------------------------------------------------------- puros

def test_nadie_actua_sobre_si_mismo():
    with pytest.raises(HTTPException) as exc:
        users_mod.guarda_auto_accion(7, 7)
    assert (exc.value.status_code, exc.value.detail) == (403, "auto_accion_prohibida")
    users_mod.guarda_auto_accion(7, 8)


def test_que_cambios_le_quitan_un_superadmin_activo_al_sistema():
    p = users_mod.pierde_superadmin_activo
    assert p("superadmin", "active", "operator", "active")
    assert p("superadmin", "active", "superadmin", "inactive")
    assert p("superadmin", "active", "superadmin", "deleted")
    assert not p("superadmin", "active", "superadmin", "active")
    assert not p("superadmin", "inactive", "operator", "inactive")
    assert not p("operator", "active", "superadmin", "active")


# ------------------------------------------------------------------- PUT

def test_un_superadmin_no_puede_degradarse_a_si_mismo(client, usuarios):
    s, _ = usuarios(role="superadmin")
    r = _put(client, s, auth(token_para(s, role="superadmin")), role="operator")
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")
    assert client.portal.call(_fila, s) == ("superadmin", "active", 0)


def test_nadie_puede_desactivarse_a_si_mismo(client, usuarios):
    s, _ = usuarios(role="superadmin")
    r = _put(client, s, auth(token_para(s, role="superadmin")), status="inactive")
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")
    assert client.portal.call(_fila, s) == ("superadmin", "active", 0)


def test_no_se_degrada_al_ultimo_superadmin_activo(client, usuarios, monkeypatch):
    s, _ = usuarios(role="superadmin")
    monkeypatch.setattr(users_mod, "otros_superadmins_activos", _ninguno)
    r = _put(client, s, role="operator")
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    r = _put(client, s, status="inactive")
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    assert client.portal.call(_fila, s) == ("superadmin", "active", 0)
    assert client.portal.call(_auditoria, s) == [], "la transacción revierte: ni cambio ni registro"


def test_degradar_con_otro_superadmin_cambia_sube_la_version_y_audita(client, usuarios, monkeypatch):
    from auth import rate_limit
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset({"testclient"}))
    s, _ = usuarios(role="superadmin")
    r = _put(client, s, {**_admin(), "X-Real-IP": "203.0.113.9"}, role="operator")
    assert r.status_code == 200, r.text
    assert client.portal.call(_fila, s) == ("operator", "active", 1)
    ((actor, accion, detalle, ip),) = client.portal.call(_auditoria, s)
    assert (actor, accion, json.loads(detalle), ip) == (1, "update_role", {"from": "superadmin", "to": "operator"}, "203.0.113.9")


def test_cambio_de_estado_sube_la_version_y_audita(client, usuarios):
    o, _ = usuarios()
    assert _put(client, o, status="inactive").status_code == 200
    assert client.portal.call(_fila, o) == ("operator", "inactive", 1)
    assert [a[1] for a in client.portal.call(_auditoria, o)] == ["update_status"]


def test_sin_cambios_no_sube_la_version_ni_audita(client, usuarios):
    o, _ = usuarios()
    assert _put(client, o, role="operator", status="active").status_code == 200
    assert client.portal.call(_fila, o) == ("operator", "active", 0)
    assert client.portal.call(_auditoria, o) == []


def test_valores_invalidos_y_campos_desconocidos(client, usuarios):
    o, _ = usuarios()
    assert _put(client, o, role="dios").json()["detail"] == "rol_invalido"
    assert _put(client, o, status="deleted").json()["detail"] == "estado_invalido"
    # La contraseña ya no se cambia por acá (Mi cuenta / enlace, etapa 4).
    assert _put(client, o, password="una-clave-cualquiera").status_code == 422
    assert _put(client, 10**9, role="viewer").json()["detail"] == "usuario_no_encontrado"
    assert client.portal.call(_fila, o) == ("operator", "active", 0)


def test_cuenta_los_otros_superadmins_activos(client, usuarios):
    a, _ = usuarios(role="superadmin")

    async def contar(excluido):
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                return await users_mod.otros_superadmins_activos(cur, excluido)

    base = client.portal.call(contar, a)
    usuarios(role="superadmin", status="inactive")
    usuarios(role="operator")
    assert client.portal.call(contar, a) == base
    usuarios(role="superadmin")
    assert client.portal.call(contar, a) == base + 1
    ((independiente,),) = client.portal.call(
        sql, "SELECT COUNT(*) FROM jax_users WHERE role = 'superadmin' AND status = 'active' AND user_id <> %s",
        (a,), True)
    assert client.portal.call(contar, a) == independiente


# ---------------------------------------------------------------- DELETE

def test_delete_usa_las_guardas_y_no_el_literal_user_id_1(client, usuarios, monkeypatch):
    s, _ = usuarios(role="superadmin")
    r = client.delete(f"/api/admin/users/{s}", headers=auth(token_para(s, role="superadmin")))
    assert (r.status_code, r.json()["detail"]) == (403, "auto_accion_prohibida")
    monkeypatch.setattr(users_mod, "otros_superadmins_activos", _ninguno)
    r = client.delete(f"/api/admin/users/{s}", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (409, "ultimo_superadmin")
    assert client.portal.call(_fila, s) is not None
```

- [ ] **Step 3: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_guardas.py -q`
Expected: los puros fallan con `AttributeError: module 'api.admin.users' has no attribute 'guarda_auto_accion'`/`'pierde_superadmin_activo'`; los de PUT responden `200` donde se espera 403/409 (y `test_valores_invalidos...` recibe `Rol inválido` en vez de `rol_invalido`); `test_cuenta_los_otros_superadmins_activos` falla por `AttributeError`; el de DELETE responde `200` al borrarse a sí mismo.

- [ ] **Step 4: Implementación mínima**

En `backend/api/admin/users.py`, reemplazar el bloque de imports y constantes (desde `import bcrypt` hasta `VALID_ROLES = {...}`) por:

```python
import bcrypt
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

import user_audit
from auth import rate_limit
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool
from db.transaccion import transaccion

router = APIRouter(prefix="/api/admin")

VALID_ROLES = {"superadmin", "operator", "viewer"}
# `deleted` NO se pone por acá: solo la baja (etapa 5).
ESTADOS_EDITABLES = {"active", "inactive"}
```

Después de la función `_hash` agregar:

```python
def _ip(request: Request) -> str:
    return rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)


# --------------------------------------------------------------- guardas
# (2026-09-12, admin usuarios etapa 3, spec §3.3). Reemplazan al literal
# `user_id == 1` que tenía delete_user: lo que se protege no es una fila, es
# que el sistema tenga siempre al menos un superadmin activo.

def guarda_auto_accion(actor_id: int, target_id: int) -> None:
    """Nadie se degrada, se desactiva ni se da de baja a sí mismo; su
    contraseña la cambia en "Mi cuenta"."""
    if actor_id == target_id:
        raise HTTPException(status_code=403, detail="auto_accion_prohibida")


def pierde_superadmin_activo(rol_actual: str, estado_actual: str, nuevo_rol: str, nuevo_estado: str) -> bool:
    return rol_actual == "superadmin" and estado_actual == "active" and (
        nuevo_rol != "superadmin" or nuevo_estado != "active")


async def otros_superadmins_activos(cur, excluido: int) -> int:
    # FOR UPDATE, dentro de la transacción del cambio: dos admins que se
    # degradan mutuamente a la vez se serializan acá, y el segundo ve el
    # resultado del primero. Filtra por idx_jax_users_role_status.
    await cur.execute(
        "SELECT user_id FROM jax_users WHERE role = 'superadmin' AND status = 'active' "
        "AND user_id <> %s FOR UPDATE",
        (excluido,),
    )
    return len(await cur.fetchall())


async def exigir_invariante(cur, target_id: int, rol_actual: str, estado_actual: str,
                            nuevo_rol: str, nuevo_estado: str) -> None:
    if pierde_superadmin_activo(rol_actual, estado_actual, nuevo_rol, nuevo_estado) \
            and await otros_superadmins_activos(cur, target_id) == 0:
        raise HTTPException(status_code=409, detail="ultimo_superadmin")


async def _leer_para_actualizar(cur, user_id: int):
    await cur.execute("SELECT role, status FROM jax_users WHERE user_id = %s FOR UPDATE", (user_id,))
    return await cur.fetchone()
```

Reemplazar `class UpdateUserRequest` y la función `update_user` completas por:

```python
class UpdateUserRequest(BaseModel):
    # extra="forbid": un campo que ya no existe (password) responde 422 en vez
    # de ignorarse en silencio. La contraseña la cambia el dueño en Mi cuenta
    # o por enlace de recuperación (etapa 4).
    model_config = ConfigDict(extra="forbid")
    role: Optional[str] = None
    status: Optional[str] = None


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    request: Request,
    user: AuthUser = Depends(require_superadmin),
):
    if req.role is not None and req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="rol_invalido")
    if req.status is not None and req.status not in ESTADOS_EDITABLES:
        raise HTTPException(status_code=400, detail="estado_invalido")
    actor_id = int(user.user_id)
    async with transaccion() as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual = actual
        nuevo_rol = req.role if req.role is not None else rol_actual
        nuevo_estado = req.status if req.status is not None else estado_actual
        cambia_rol, cambia_estado = nuevo_rol != rol_actual, nuevo_estado != estado_actual
        if not (cambia_rol or cambia_estado):
            return {"ok": True}
        guarda_auto_accion(actor_id, user_id)
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, nuevo_rol, nuevo_estado)
        # Rol o estado nuevos: todas las sesiones del usuario se cortan en el
        # request siguiente (spec §3.2).
        await cur.execute(
            "UPDATE jax_users SET role = %s, status = %s, token_version = token_version + 1 WHERE user_id = %s",
            (nuevo_rol, nuevo_estado, user_id),
        )
        ip = _ip(request)
        if cambia_rol:
            await user_audit.registrar(cur, actor_id, user_id, "update_role", {"from": rol_actual, "to": nuevo_rol}, ip)
        if cambia_estado:
            await user_audit.registrar(cur, actor_id, user_id, "update_status",
                                       {"from": estado_actual, "to": nuevo_estado}, ip)
    return {"ok": True}
```

Reemplazar la función `delete_user` completa por:

```python
@router.delete("/users/{user_id}")
async def delete_user(user_id: int, user: AuthUser = Depends(require_superadmin)):
    # Sigue existiendo hasta la etapa 5 (que lo reemplaza por la baja). El
    # literal `user_id == 1` ya no está: lo reemplazan las guardas.
    guarda_auto_accion(int(user.user_id), user_id)
    async with transaccion() as cur:
        actual = await _leer_para_actualizar(cur, user_id)
        if actual is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        rol_actual, estado_actual = actual
        await exigir_invariante(cur, user_id, rol_actual, estado_actual, rol_actual, "deleted")
        await cur.execute("DELETE FROM jax_users WHERE user_id = %s", (user_id,))
    return {"ok": True}
```

- [ ] **Step 4b: Cortar los WebSocket ya abiertos** *(enmienda 2026-09-14, revisión final de la etapa 2, hallazgo M-1)*

`verificar_sesion` corta el WS solo en el handshake: una pestaña abierta de un usuario degradado,
desactivado o borrado seguiría recibiendo eventos hasta reconectar. Donde nace el incremento de
`token_version` (y el borrado) se cierran también sus conexiones vivas:

- `backend/jax_engine/websocket_hub.py`: nuevo `async def close_user(self, user_id: str, code: int = 4001) -> int`
  — saca bajo `_lock` todas las conexiones del usuario, las cierra fuera del lock con ese código
  (una excepción al cerrar una no impide cerrar las demás) y devuelve cuántas cerró.
- `update_user` (cuando cambia rol o estado), `revoke_sessions` (Task 3) y `delete_user`: llamar
  `await ws_hub.close_user(str(user_id))` **después** de que la transacción confirma (nunca
  dentro: un rollback no debe haber cortado sesiones).
- Tests: el hub cierra todas las conexiones del usuario con 4001 y no toca las de otro; cada uno
  de los tres endpoints llama al hub tras el commit y no lo llama si la guarda responde 409/404.

*(M-4, anotado:* `tenant_id` sigue saliendo del token — aceptado por el spec §3.2 con un solo
tenant. Si llega multi-tenant, `verificar_sesion` lo lee de `jax_users` igual que el rol.)*

- [ ] **Step 5: Verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_guardas.py tests/test_user_audit.py -q` → `14 passed` (11 + 3) más los tests del Step 4b.
Run: suite completa → 0 failed.

- [ ] **Step 6: Pisos medidos** — sin DB +2 (los dos puros); con DB +11.

- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/admin/users.py backend/tests/test_admin_usuarios_guardas.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): siempre un superadmin activo, nadie actúa sobre sí mismo; sin user_id == 1 literal"
```

---

### Task 3: Acciones auditadas — alta, desbloqueo, cerrar sesiones — e historial

**Files:**
- Modify: `backend/api/admin/users.py` (`create_user`, `unlock_user`, nuevo `revoke_sessions`, nuevo `user_audit_history`)
- Test: `backend/tests/test_admin_usuarios_guardas.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `transaccion`, `user_audit.registrar/historial`, `_leer_para_actualizar`, `_ip` (Tasks 1-2).
- Produces: `POST /api/admin/users/{id}/revoke-sessions` → `{"ok": true}` (404 `usuario_no_encontrado`); `GET /api/admin/users/{id}/audit` → `{"entries": [...]}`; `create` y `unlock` auditados.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_admin_usuarios_guardas.py`:

```python
# ---------------------------------------------- acciones auditadas e historial

import uuid  # noqa: E402

from tests.identidades import borrar_usuario  # noqa: E402


def test_cerrar_sesiones_corta_los_tokens_y_audita(client, usuarios):
    o, _ = usuarios()
    viejo = token_para(o)
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 200
    assert client.post(f"/api/admin/users/{o}/revoke-sessions", headers=_admin()).status_code == 200
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401
    assert client.portal.call(_fila, o)[2] == 1
    assert [a[1] for a in client.portal.call(_auditoria, o)] == ["sessions_revoked"]
    r = client.post("/api/admin/users/1000000000/revoke-sessions", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")


def test_desbloquear_audita(client, usuarios):
    o, _ = usuarios()
    client.portal.call(sql, "UPDATE jax_users SET failed_attempts = 5, "
                            "locked_until = NOW() + INTERVAL 10 MINUTE WHERE user_id = %s", (o,))
    assert client.post(f"/api/admin/users/{o}/unlock", headers=_admin()).status_code == 200
    ((intentos, bloqueo),) = client.portal.call(sql, "SELECT failed_attempts, locked_until FROM jax_users "
                                                     "WHERE user_id = %s", (o,), True)
    assert (intentos, bloqueo) == (0, None)
    assert [a[1] for a in client.portal.call(_auditoria, o)] == ["unlock"]


def test_alta_audita(client):
    email = f"test-alta-{uuid.uuid4().hex[:10]}@example.invalid"
    r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": "clave-larga-1"},
                    headers=_admin())
    assert r.status_code == 200, r.text
    nuevo = r.json()["user_id"]
    try:
        ((actor, accion, detalle, _ip),) = client.portal.call(_auditoria, nuevo)
        assert (actor, accion, json.loads(detalle)) == (1, "create", {"email": email, "role": "viewer"})
    finally:
        client.portal.call(borrar_usuario, nuevo)


def test_historial_devuelve_las_ultimas_50_mas_nuevas_primero(client, usuarios):
    o, _ = usuarios()
    valores = ", ".join(["(1, %s, 'unlock', %s, NOW(6) - INTERVAL %s SECOND)"] * 55)
    args = tuple(x for i in range(55) for x in (o, json.dumps({"n": i}), 55 - i))
    client.portal.call(sql, "INSERT INTO user_admin_audit (actor_user_id, target_user_id, action, detail, ts) "
                            f"VALUES {valores}", args)
    r = client.get(f"/api/admin/users/{o}/audit", headers=_admin())
    assert r.status_code == 200, r.text
    entradas = r.json()["entries"]
    assert len(entradas) == 50
    assert (entradas[0]["detail"], entradas[-1]["detail"]) == ({"n": 54}, {"n": 5})
    ((email_1,),) = client.portal.call(sql, "SELECT email FROM jax_users WHERE user_id = 1", (), True)
    assert entradas[0]["actor_email"] == email_1 and entradas[0]["action"] == "unlock"


def test_historial_solo_superadmin(client, usuarios):
    o, _ = usuarios()
    assert client.get(f"/api/admin/users/{o}/audit", headers=auth(token_para(o))).status_code == 403
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_guardas.py -q -k "sesiones or desbloquear or alta or historial"`
Expected: `revoke-sessions` y `audit` → `404`/`405` (no existen); `test_desbloquear_audita` y `test_alta_audita` → la auditoría vacía (`[]` / `ValueError: not enough values to unpack`). `test_historial_solo_superadmin` → `404` en vez de `403`.

- [ ] **Step 3: Implementación mínima**

En `backend/api/admin/users.py`, reemplazar `create_user` completa por:

```python
@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request, user: AuthUser = Depends(require_superadmin)):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Rol inválido: {req.role}")

    ph = _hash(req.password)
    async with transaccion() as cur:
        await cur.execute("SELECT COUNT(*) FROM jax_users WHERE email = %s", (req.email,))
        (count,) = await cur.fetchone()
        if count > 0:
            raise HTTPException(status_code=409, detail="Email ya existe")
        await cur.execute(
            "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) VALUES (1, %s, %s, %s, 'active')",
            (req.email, ph, req.role),
        )
        new_id = cur.lastrowid
        await user_audit.registrar(cur, int(user.user_id), new_id, "create",
                                   {"email": req.email, "role": req.role}, _ip(request))
    return {"user_id": new_id, "email": req.email, "role": req.role, "status": "active"}
```

Reemplazar `unlock_user` completa por:

```python
@router.post("/users/{user_id}/unlock")
async def unlock_user(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    async with transaccion() as cur:
        if await _leer_para_actualizar(cur, user_id) is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        await cur.execute(
            "UPDATE jax_users SET failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (user_id,),
        )
        await user_audit.registrar(cur, int(user.user_id), user_id, "unlock", None, _ip(request))
    return {"ok": True}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_sessions(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    # Sube la versión: todos los tokens del usuario (access, refresh y el
    # próximo handshake de WS) quedan inválidos en el request siguiente.
    async with transaccion() as cur:
        if await _leer_para_actualizar(cur, user_id) is None:
            raise HTTPException(status_code=404, detail="usuario_no_encontrado")
        await cur.execute("UPDATE jax_users SET token_version = token_version + 1 WHERE user_id = %s", (user_id,))
        await user_audit.registrar(cur, int(user.user_id), user_id, "sessions_revoked", None, _ip(request))
    return {"ok": True}


@router.get("/users/{user_id}/audit")
async def user_audit_history(user_id: int, user: AuthUser = Depends(require_superadmin)):
    return {"entries": await user_audit.historial(user_id)}
```

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_admin_usuarios_guardas.py tests/test_user_audit.py -q` → `19 passed`. Suite completa → 0 failed.

- [ ] **Step 5: Pisos medidos** — con DB +5.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/admin/users.py backend/tests/test_admin_usuarios_guardas.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): alta y desbloqueo auditados, cerrar sesiones, historial por usuario"
```

---

### Task 4: Frontend — toasts en Admin, errores traducidos, edición, cerrar sesiones e historial

**Files:**
- Modify: `frontend/src/pages/Admin.jsx`
- Create: `frontend/src/pages/admin/erroresAdmin.js`
- Create: `frontend/src/components/admin/EditarUsuarioModal.jsx`, `frontend/src/components/admin/HistorialUsuario.jsx`
- Modify: `frontend/src/pages/admin/AdminUsers.jsx` (archivo completo)
- Test: `frontend/src/components/admin/EditarUsuarioModal.test.jsx`, `frontend/src/pages/admin/AdminUsers.test.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`, `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: endpoints de las Tasks 2-3; `useJaxStore((s) => s.addToast)` (`addToast({ type, message })`, tipos `error|warning|info|success`); `Toast` (default export de `frontend/src/components/Notifications/Toast.jsx`); `t.smtpServerSaid` (etapa 1).
- Produces: `erroresAdmin.js`, `EditarUsuarioModal`, `HistorialUsuario` (ver Interfaces arriba).

- [ ] **Step 1: Tests que fallan**

Crear `frontend/src/components/admin/EditarUsuarioModal.test.jsx`:

```jsx
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import EditarUsuarioModal from './EditarUsuarioModal'
import { I18nProvider } from '../../i18n/index.jsx'

const USUARIO = { user_id: 2, email: 'b@x.io', role: 'superadmin', status: 'active' }

function renderModal(onGuardar = vi.fn().mockResolvedValue()) {
  render(<I18nProvider><EditarUsuarioModal usuario={USUARIO} onGuardar={onGuardar} onCerrar={vi.fn()} /></I18nProvider>)
  return onGuardar
}

describe('EditarUsuarioModal', () => {
  it('envía solo lo que cambió', () => {
    const onGuardar = renderModal()
    fireEvent.change(screen.getByLabelText('Rol'), { target: { value: 'operator' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(onGuardar).toHaveBeenCalledWith({ role: 'operator' })
  })

  it('el estado no ofrece "deleted" (eso es la baja) y sin cambios no se puede guardar', () => {
    renderModal()
    const opciones = [...screen.getByLabelText('Estado').querySelectorAll('option')].map((o) => o.value)
    expect(opciones).toEqual(['active', 'inactive'])
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled()
  })
})
```

Crear `frontend/src/pages/admin/AdminUsers.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Tabla de usuarios (2026-09-12, admin usuarios etapa 3): los errores del
// backend llegan como códigos y se muestran traducidos en un toast -- antes
// cada acción terminaba en `.catch(() => {})`.
const addToastMock = vi.fn()
vi.mock('../../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ addToast: addToastMock }),
}))
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))

import api from '../../api/client'
import AdminUsers from './AdminUsers'
import { I18nProvider } from '../../i18n/index.jsx'

const USUARIO = {
  user_id: 2, email: 'b@x.io', role: 'superadmin', status: 'active', created_at: null,
  last_login: null, failed_attempts: 0, locked_until: null, is_locked: false,
}
const HISTORIAL = [{
  id: 9, ts: '2026-09-12T10:00:00', actor_user_id: 1, actor_email: 'fernando@rich-hn.com',
  action: 'update_role', detail: { from: 'superadmin', to: 'operator' }, ip: '203.0.113.5',
}]

function renderUsers() {
  return render(<I18nProvider><AdminUsers /></I18nProvider>)
}

beforeEach(() => {
  addToastMock.mockReset()
  api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); api.delete.mockReset()
  api.get.mockImplementation((url) => Promise.resolve(
    url === '/admin/users' ? { data: { users: [USUARIO] } } : { data: { entries: HISTORIAL } }))
  localStorage.clear()
})

describe('AdminUsers', () => {
  it('un 409 ultimo_superadmin al editar aparece traducido en un toast', async () => {
    api.put.mockRejectedValue({ response: { status: 409, data: { detail: 'ultimo_superadmin' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText('Rol'), { target: { value: 'operator' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'No se puede: tiene que quedar al menos un superadmin activo.',
    }))
    expect(api.put).toHaveBeenCalledWith('/admin/users/2', { role: 'operator' })
  })

  it('cerrar sesiones llama al endpoint y avisa', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Cerrar sesiones' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/revoke-sessions'))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Se cerraron todas las sesiones de b@x.io.',
    }))
  })

  it('el historial muestra la acción traducida, el cambio y quién la hizo', async () => {
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Historial' }))
    const dialogo = await screen.findByRole('dialog')
    expect(await within(dialogo).findByText('Cambio de rol')).toBeInTheDocument()
    expect(within(dialogo).getByText('superadmin → operator')).toBeInTheDocument()
    expect(within(dialogo).getByText('por fernando@rich-hn.com')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run src/components/admin/EditarUsuarioModal.test.jsx src/pages/admin/AdminUsers.test.jsx`
Expected: `EditarUsuarioModal.test.jsx` falla con `Failed to resolve import "./EditarUsuarioModal"`; `AdminUsers.test.jsx` falla con `Unable to find role="button" and name "Editar"`.

- [ ] **Step 3: Implementación mínima**

Crear `frontend/src/pages/admin/erroresAdmin.js`:

```js
// Errores de las pantallas de administración (2026-09-12, admin usuarios etapa 3).
// El backend responde un código estable en `detail` (string, o {code, server}
// cuando hay una respuesta de un servidor externo que mostrar). Acá se traduce;
// un código sin traducción cae en el mensaje genérico, nunca se muestra crudo.
export function codigoDeError(err) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.code
}

export function mensajeDeError(t, err) {
  const code = codigoDeError(err)
  const base = (code && t.adminErrors[code]) || t.adminErrorGeneric
  const servidor = err?.response?.data?.detail?.server
  return servidor ? `${base} ${t.smtpServerSaid(servidor)}` : base
}
```

Crear `frontend/src/components/admin/EditarUsuarioModal.jsx`:

```jsx
import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'

// Editar rol y estado (2026-09-12, admin usuarios etapa 3). Manda SOLO lo que
// cambió; qué está permitido lo decide el backend (último superadmin,
// auto-acciones) y el padre muestra el error traducido. `deleted` no es una
// opción: eso es la baja.
const ROLES = ['superadmin', 'operator', 'viewer']
const ESTADOS = ['active', 'inactive']
const CAMPO = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500'

export default function EditarUsuarioModal({ usuario, onGuardar, onCerrar }) {
  const { t } = useI18n()
  const [role, setRole] = useState(usuario.role)
  const [status, setStatus] = useState(usuario.status)
  const [guardando, setGuardando] = useState(false)

  const cambios = {}
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

Crear `frontend/src/components/admin/HistorialUsuario.jsx`:

```jsx
import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import { mensajeDeError } from '../../pages/admin/erroresAdmin'

// Historial corto de un usuario (2026-09-12, admin usuarios etapa 3): las
// últimas 50 acciones de administración sobre él, la más nueva primero.
export default function HistorialUsuario({ usuario, onCerrar }) {
  const { lang, t } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  const [entradas, setEntradas] = useState(null)
  const locale = lang === 'en' ? 'en-US' : 'es-HN'

  useEffect(() => {
    api.get(`/admin/users/${usuario.user_id}/audit`)
      .then((r) => setEntradas(r.data.entries))
      .catch((err) => {
        addToast({ type: 'error', message: mensajeDeError(t, err) })
        setEntradas([])
      })
  }, [usuario.user_id])

  function cambio(detalle) {
    if (!detalle || detalle.from === undefined) return null
    return `${detalle.from} → ${detalle.to}`
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="historial-titulo"
        className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-lg shadow-2xl">
        <h2 id="historial-titulo" className="text-sm font-semibold text-slate-200 mb-4">{t.adminHistoryTitle(usuario.email)}</h2>
        {entradas !== null && entradas.length === 0 && <p className="text-sm text-slate-500">{t.adminHistoryEmpty}</p>}
        <ul className="space-y-2 max-h-96 overflow-y-auto">
          {(entradas || []).map((e) => (
            <li key={e.id} className="text-xs border-b border-slate-800 pb-2">
              <div className="flex justify-between gap-2">
                <span className="font-semibold text-slate-200">{t.adminAuditActions[e.action] || t.adminAuditUnknown}</span>
                <span className="text-slate-500">{e.ts ? new Date(e.ts).toLocaleString(locale) : ''}</span>
              </div>
              {cambio(e.detail) && <div className="text-slate-300">{cambio(e.detail)}</div>}
              <div className="text-slate-500">
                <span>{t.adminHistoryBy(e.actor_email || e.actor_user_id)}</span>
                {e.ip && <span> · {e.ip}</span>}
              </div>
            </li>
          ))}
        </ul>
        <div className="flex justify-end pt-4">
          <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminHistoryClose}</button>
        </div>
      </div>
    </div>
  )
}
```

Reemplazar `frontend/src/pages/admin/AdminUsers.jsx` completo por:

```jsx
import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import PasswordInput from '../../components/PasswordInput'
import EditarUsuarioModal from '../../components/admin/EditarUsuarioModal'
import HistorialUsuario from '../../components/admin/HistorialUsuario'
import { mensajeDeError } from './erroresAdmin'

// Gestión de usuarios (2026-09-12, admin usuarios etapa 3). Cada acción
// muestra su error traducido en un toast: antes todas terminaban en
// `.catch(() => {})` y el admin no se enteraba de nada. Qué está permitido lo
// decide el backend (último superadmin, auto-acciones): el botón de eliminar
// ya no se esconde para user_id 1.
const ROLES = ['superadmin', 'operator', 'viewer']
const ACCION = 'text-xs px-2 py-0.5 rounded transition-colors'
const ACCION_NEUTRA = `${ACCION} bg-slate-700 hover:bg-slate-600 text-slate-300`

export default function AdminUsers() {
  const { lang, t } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  const [users, setUsers] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ email: '', role: 'operator', password: '' })
  const [saving, setSaving] = useState(false)
  const [editando, setEditando] = useState(null)
  const [historialDe, setHistorialDe] = useState(null)
  const locale = lang === 'en' ? 'en-US' : 'es-HN'

  function avisarError(err) {
    addToast({ type: 'error', message: mensajeDeError(t, err) })
  }

  function avisarExito(message) {
    addToast({ type: 'success', message })
  }

  function load() {
    api.get('/admin/users').then((r) => setUsers(r.data.users)).catch(avisarError)
  }

  useEffect(() => { load() }, [])

  async function handleCreate(e) {
    e.preventDefault()
    setSaving(true)
    try {
      await api.post('/admin/users', form)
      setShowCreate(false)
      setForm({ email: '', role: 'operator', password: '' })
      load()
    } catch (err) {
      avisarError(err)
    } finally {
      setSaving(false)
    }
  }

  async function guardarEdicion(cambios) {
    try {
      await api.put(`/admin/users/${editando.user_id}`, cambios)
      setEditando(null)
      avisarExito(t.adminUserSaved)
      load()
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleUnlock(u) {
    try {
      await api.post(`/admin/users/${u.user_id}/unlock`)
      load()
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleRevoke(u) {
    try {
      await api.post(`/admin/users/${u.user_id}/revoke-sessions`)
      avisarExito(t.adminSessionsRevoked(u.email))
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleDelete(u) {
    if (!window.confirm(t.adminDeleteConfirm(u.email))) return
    try {
      await api.delete(`/admin/users/${u.user_id}`)
      load()
    } catch (err) {
      avisarError(err)
    }
  }

  function statusBadge(u) {
    if (u.is_locked) return <span className="text-xs font-semibold text-orange-400">{t.adminUserLocked}</span>
    if (u.status === 'active') return <span className="text-xs font-semibold text-green-400">{t.adminUserActive}</span>
    return <span className="text-xs font-semibold text-slate-500">{t.adminUserInactive}</span>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-slate-100">{t.adminUsersTitle}</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold transition-colors"
        >
          + {t.adminUserCreate}
        </button>
      </div>

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminUserEmail, t.adminUserRole, t.adminUserStatus, t.adminUserLastLogin, t.adminUserActions].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {users.map((u) => (
              <tr key={u.user_id} className={`hover:bg-slate-800/30 transition-colors ${u.is_locked ? 'bg-orange-950/10' : 'bg-slate-900/50'}`}>
                <td className="px-4 py-3 text-slate-200">{u.email}</td>
                <td className="px-4 py-3 text-xs text-slate-300">{u.role}</td>
                <td className="px-4 py-3">
                  {statusBadge(u)}
                  {u.failed_attempts > 0 && !u.is_locked && (
                    <span className="ml-2 text-xs text-slate-600">{t.adminUserFailedAttempts(u.failed_attempts)}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">
                  {u.last_login ? new Date(u.last_login).toLocaleString(locale) : '—'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <button onClick={() => setEditando(u)} className={ACCION_NEUTRA}>{t.adminUserEdit}</button>
                    {u.is_locked && (
                      <button onClick={() => handleUnlock(u)} className={`${ACCION} bg-orange-900/40 hover:bg-orange-900/60 text-orange-400`}>
                        {t.adminUserUnlock}
                      </button>
                    )}
                    <button onClick={() => handleRevoke(u)} className={ACCION_NEUTRA}>{t.adminUserRevokeSessions}</button>
                    <button onClick={() => setHistorialDe(u)} className={ACCION_NEUTRA}>{t.adminUserHistory}</button>
                    <button onClick={() => handleDelete(u)} className={`${ACCION} bg-red-900/40 hover:bg-red-900/60 text-red-400`}>
                      {t.adminUserDelete}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editando && <EditarUsuarioModal usuario={editando} onGuardar={guardarEdicion} onCerrar={() => setEditando(null)} />}
      {historialDe && <HistorialUsuario usuario={historialDe} onCerrar={() => setHistorialDe(null)} />}

      {showCreate && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-sm font-semibold text-slate-200 mb-4">{t.adminCreateTitle}</h2>
            <form onSubmit={handleCreate} className="space-y-3">
              <input
                type="email"
                placeholder={t.adminUserEmail}
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                required
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500"
              />
              <select
                value={form.role}
                onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500"
              >
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              <PasswordInput
                placeholder={t.adminCreatePassword}
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                required
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500"
              />
              <div className="flex gap-2 justify-end pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
                <button type="submit" disabled={saving} className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors">{saving ? t.attachUploading : t.adminCreateSubmit}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
```

En `frontend/src/pages/Admin.jsx`: agregar `import Toast from '../components/Notifications/Toast'` después de `import AdminSidebar from '../components/admin/AdminSidebar'`, y dentro del `<div className="flex h-dvh ...">`, después de `</main>`, agregar `<Toast />` (hoy los toasts solo se montan en Dashboard: en Admin eran invisibles).

En `frontend/src/i18n/es.js`, insertar ANTES de `  // Restaurar tareas pendientes (useJaxStore.js)`:

```js
  // Administración de usuarios — etapa 3 (guardas, sesiones, historial, 2026-09-12)
  adminUserEdit: 'Editar',
  adminUserEditTitle: (email) => `Editar ${email}`,
  adminUserSave: 'Guardar',
  adminUserSaved: 'Usuario actualizado.',
  adminUserRevokeSessions: 'Cerrar sesiones',
  adminSessionsRevoked: (email) => `Se cerraron todas las sesiones de ${email}.`,
  adminUserHistory: 'Historial',
  adminHistoryTitle: (email) => `Historial de ${email}`,
  adminHistoryEmpty: 'Sin acciones registradas.',
  adminHistoryClose: 'Cerrar',
  adminHistoryBy: (actor) => `por ${actor}`,
  adminUserFailedAttempts: (n) => `(${n} intentos)`,
  adminErrorGeneric: 'No se pudo completar la acción.',
  adminErrors: {
    ultimo_superadmin: 'No se puede: tiene que quedar al menos un superadmin activo.',
    auto_accion_prohibida: 'No podés cambiar tu propio rol ni tu estado, ni darte de baja. Tu contraseña se cambia en "Mi cuenta".',
    usuario_no_encontrado: 'El usuario no existe.',
    rol_invalido: 'Rol inválido.',
    estado_invalido: 'Estado inválido.',
    sesion_invalida: 'Tu sesión ya no es válida. Volvé a entrar.',
  },
  adminAuditActions: {
    create: 'Alta',
    update_email: 'Cambio de correo',
    update_role: 'Cambio de rol',
    update_status: 'Cambio de estado',
    reset_link_sent: 'Enlace de recuperación enviado',
    unlock: 'Desbloqueo',
    sessions_revoked: 'Sesiones cerradas',
    baja: 'Baja',
    password_changed_self: 'Cambió su contraseña',
    password_reset_completed: 'Restableció su contraseña',
  },
  adminAuditUnknown: 'Acción desconocida',

```

En `frontend/src/i18n/en.js`, insertar ANTES de `  // Restoring pending tasks (useJaxStore.js)`:

```js
  // User administration — stage 3 (guards, sessions, history, 2026-09-12)
  adminUserEdit: 'Edit',
  adminUserEditTitle: (email) => `Edit ${email}`,
  adminUserSave: 'Save',
  adminUserSaved: 'User updated.',
  adminUserRevokeSessions: 'Sign out everywhere',
  adminSessionsRevoked: (email) => `All sessions of ${email} were closed.`,
  adminUserHistory: 'History',
  adminHistoryTitle: (email) => `History of ${email}`,
  adminHistoryEmpty: 'No recorded actions.',
  adminHistoryClose: 'Close',
  adminHistoryBy: (actor) => `by ${actor}`,
  adminUserFailedAttempts: (n) => `(${n} attempts)`,
  adminErrorGeneric: 'The action could not be completed.',
  adminErrors: {
    ultimo_superadmin: 'Not allowed: at least one active superadmin must remain.',
    auto_accion_prohibida: 'You cannot change your own role or status, or remove yourself. Change your password in "My account".',
    usuario_no_encontrado: 'The user does not exist.',
    rol_invalido: 'Invalid role.',
    estado_invalido: 'Invalid status.',
    sesion_invalida: 'Your session is no longer valid. Sign in again.',
  },
  adminAuditActions: {
    create: 'Created',
    update_email: 'Email changed',
    update_role: 'Role changed',
    update_status: 'Status changed',
    reset_link_sent: 'Recovery link sent',
    unlock: 'Unlocked',
    sessions_revoked: 'Sessions closed',
    baja: 'Removed',
    password_changed_self: 'Changed their password',
    password_reset_completed: 'Reset their password',
  },
  adminAuditUnknown: 'Unknown action',

```

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run` → piso actual + 5, 0 fallidos.

- [ ] **Step 5: Piso de vitest** — `numPassedTests !== <medido>` (esperado 97 si el piso era 92) con su comentario.

- [ ] **Step 6: Claro/oscuro a mano** — vite en `127.0.0.1:5174`, `/admin/users`: editar, historial y toasts legibles en los dos temas.

- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/jax-platform add frontend/src/pages/Admin.jsx frontend/src/pages/admin/erroresAdmin.js frontend/src/components/admin/EditarUsuarioModal.jsx frontend/src/components/admin/EditarUsuarioModal.test.jsx frontend/src/components/admin/HistorialUsuario.jsx frontend/src/pages/admin/AdminUsers.jsx frontend/src/pages/admin/AdminUsers.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): errores traducidos en toasts, edición de rol/estado, cerrar sesiones e historial"
```

---

### Task 5: PR, CI por headSha, despliegue y verificación en vivo

- [ ] **Step 1: Suite local completa** (con DB, sin DB, vitest) = pisos del archivo, 0 fallidos.
- [ ] **Step 2: PR** — rama `feat/admin-usuarios-3-invariantes`, título "Admin usuarios · etapa 3: invariantes, auto-acciones y auditoría", cuerpo con spec §3.3 y el plan.
- [ ] **Step 3: Gate por headSha** — `gh pr checks <N>` sin nada fuera de `SUCCESS` y `gh pr view <N> --json headRefOid -q .headRefOid` == `git -C /home/fruiz/jax-platform ls-remote origin refs/heads/feat/admin-usuarios-3-invariantes`. Recién ahí `gh pr merge <N> --merge`.
- [ ] **Step 4: Backend** — `git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only`; `sudo -n /usr/bin/systemctl restart jax-platform.service`; `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health` → 200; `sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40` sin tracebacks.
- [ ] **Step 5: Tabla, índices y planes en producción**

```bash
set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "
SHOW INDEX FROM user_admin_audit; SHOW INDEX FROM jax_users WHERE Key_name = 'idx_jax_users_role_status';
EXPLAIN SELECT a.id, a.ts, a.actor_user_id, u.email, a.action, a.detail, a.ip FROM user_admin_audit a LEFT JOIN jax_users u ON u.user_id = a.actor_user_id WHERE a.target_user_id = 1 ORDER BY a.ts DESC, a.id DESC LIMIT 50;
EXPLAIN SELECT user_id FROM jax_users WHERE role = 'superadmin' AND status = 'active' AND user_id <> 1;"
```
Expected: los dos índices existen; el primer EXPLAIN lista `idx_user_admin_audit_target_ts` en `possible_keys` para `a` y `eq_ref`/`PRIMARY` para `u`; el segundo lista `idx_jax_users_role_status` en `possible_keys`. Con tablas de pocas filas el optimizador puede preferir `ALL`: anotarlo tal cual. Lo que importa es que el índice exista y sea candidato; si `Using filesort` aparece con el índice elegido, es trabajo antes de cerrar.

- [ ] **Step 6: Frontend** — `cd /home/fruiz/jax-platform/frontend && npm run build`; backup en la VM dev; rsync en dos saltos con `--exclude .user.ini`; hash servido. Los comandos exactos:

```bash
set -a; . /etc/jax/.env; set +a
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-admin-usuarios-3-$(date +%Y%m%d-%H%M%S)"
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
ls /home/fruiz/jax-platform/frontend/dist/assets/index-*.js; curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

- [ ] **Step 7: Verificación en vivo (avisar a Fernando; usa un usuario de prueba que se borra al final)**
  1. Fernando, en `/admin/users`, intenta editar SU propia fila y quitarse el rol → toast "No podés cambiar tu propio rol…"; su fila no cambió.
  2. Crear un usuario de prueba operador desde la UI → su "Historial" muestra "Alta por <email de Fernando>" con la IP pública real (no la del proxy: prueba de que `JAX_TRUSTED_PROXIES` está bien).
  3. Iniciar sesión con ese usuario en una ventana privada; en la principal, "Cerrar sesiones" sobre él → en la privada, el request siguiente lo saca al login. El historial muestra "Sesiones cerradas".
  4. Eliminar el usuario de prueba (todavía por `DELETE`; la baja llega en la etapa 5).
- [ ] **Step 8: Biblioteca** — entrada fechada en `/home/fruiz/jax/DEUDA.md` (PR propio en `jax`): sha desplegado, `index-*.js`, EXPLAIN de producción tal cual, resultado de los 4 puntos.

---

## Autorrevisión (hecha al escribir el plan)

- **Cobertura de §3.3:** invariante con 409 estable (Task 2, bajo `FOR UPDATE`); 403 a auto-acciones (Task 2); `user_id == 1` eliminado (Task 2, test del DELETE); tabla con el índice pedido y sus 10 acciones (Task 1; esta etapa escribe `create`, `update_role`, `update_status`, `unlock` y `sessions_revoked`; las etapas 4 y 5 escriben las demás); IP por `rate_limit.client_ip` (test con `X-Real-IP` desde un proxy de confianza); `GET /users/{id}/audit` con las últimas 50 e historial en la UI (Tasks 3-4). De §3.2: `token_version` sube en rol, estado y "cerrar sesiones" (Tasks 2-3).
- **Hallazgos resueltos de paso, con evidencia:** `Admin.jsx` no montaba `Toast` (los toasts de admin eran invisibles); `AdminUsers.jsx` tenía `({n} intentos)` en español fijo y `toLocaleString('es-HN')` fijo; el alta tragaba sus errores.
- **Placeholders:** `<N>` y los conteos medidos son de ejecución.
- **Nombres:** `guarda_auto_accion`, `pierde_superadmin_activo`, `otros_superadmins_activos`, `exigir_invariante`, `_leer_para_actualizar`, `transaccion`, `user_audit.registrar/historial`, `mensajeDeError`, `EditarUsuarioModal({usuario, onGuardar, onCerrar})`, iguales en tests, código e Interfaces.
