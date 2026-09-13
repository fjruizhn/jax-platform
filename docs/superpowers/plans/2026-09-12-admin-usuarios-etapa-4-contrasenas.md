# Administración de usuarios · Etapa 4 — Contraseñas: Mi cuenta y enlace de recuperación por admin

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada usuario cambie su propia contraseña desde "Mi cuenta" (cerrando sus otras sesiones), que el superadmin envíe un enlace de recuperación que falla con un error visible si no hay correo, que completar un reset corte las sesiones viejas, y que una sola regla (8 caracteres, 72 bytes) valga en backend y frontend.

**Architecture:** `backend/auth/password_rules.py` define la regla y la usan el alta, el reset y Mi cuenta. `POST /api/auth/me/password` exige la contraseña actual, comparte el límite de intentos del login, sube `token_version` y le entrega tokens nuevos a la sesión que hizo el cambio (con `_emitir_tokens`, de la etapa 2). `POST /api/admin/users/{id}/reset-link` reusa el núcleo de la recuperación (`_crear_enlace_de_recuperacion` + `_send_reset_email`, extraídos de `_procesar_recuperacion`). Responde 503 con código estable si SMTP no está disponible y 502 si el servidor rechaza el envío. `/reset-password` sube `token_version`, marca el token como usado de forma atómica y audita. En el frontend se agregan `lib/reglasPassword.js` (compartida con ResetPassword), el modal `MiCuentaModal`, que se abre con el correo de la barra, y el botón "Enviar enlace" en la tabla de usuarios.

**Tech Stack:** FastAPI, aiomysql, bcrypt (vía `db.seed._hash` en `asyncio.to_thread`), pytest; React 19, Zustand, vitest.

**Spec:** `/home/fruiz/jax-platform/docs/superpowers/specs/2026-09-12-administracion-usuarios-design.md` (§3.4; de §3.2 "token_version sube en: cambio de contraseña (propio o por enlace)"; de §3.1 "503 en el reset por admin"; §5; §6 punto 4).

## Global Constraints

- **Repo:** `/home/fruiz/jax-platform`; rama desde `master` con las etapas 1-3 mergeadas: `git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform switch -c feat/admin-usuarios-4-contrasenas origin/master`. Siempre `git -C <ruta>`.
- **TDD** con el rojo visto por el motivo que dice cada paso.
- **Backend tests:** `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest ...`; con `client` → `jax_memory_test`. Identidades reales con `tests/identidades.py` y el fixture `usuarios`. **Ningún test modifica `user_id=1`** (solo actor). Ningún test sale a la red: todo envío se reemplaza.
- **Pisos exactos** en `.github/workflows/policy.yml`: leer los valores ACTUALES y subirlos con el número MEDIDO y un comentario.
- **P10**, **`add_safe_task`**, **nada bloqueante en async:** bcrypt (costo 12, ~150 ms) va con `asyncio.to_thread(_hash, ...)`; `smtplib` con `asyncio.to_thread`. `reset_password` hoy llama `_hash` directo en el event loop: se corrige en esta etapa porque se toca esa función.
- **Regla única:** mínimo **8 caracteres** (contados como los cuenta Python: puntos de código) y máximo **72 bytes** UTF-8 (límite de bcrypt; `db.seed.BCRYPT_MAX_BYTES`).
- **Códigos estables:** `password_actual_incorrecta` (400; **no 401**: un 401 dispara el refresh del frontend y, al fallar, lo desloguea), `password_corta`/`password_larga` (400) en Mi cuenta y en el alta; en `/reset-password` se conservan `reset_password_corta`/`reset_password_larga` (su frontend y sus tests ya los usan); `smtp_no_configurado`/`smtp_config_corrupta` (503), `{"code": "smtp_envio_fallido", "server": ...}` (502), `usuario_no_encontrado` (404), `usuario_no_activo` (409).
- **El admin espera el envío del enlace a propósito** (`asyncio.to_thread`, no BackgroundTask): el spec pide que vea el error y no un éxito falso. El `forgot-password` público sigue en segundo plano y neutro.
- **i18n es/en** y **modo claro/oscuro** en todo lo nuevo.
- **Sin migraciones** en esta etapa. Commits sin `--no-verify`. **YAGNI:** nada de spec §4.
- **Cierre:** PR → CI verde por `headSha` → despliegue backend + frontend → verificación en vivo (el correo real SOLO con permiso explícito de Fernando).

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/auth/password_rules.py` | Crear | `MIN_CARACTERES`, `problema_de_password()` |
| `backend/api/admin/users.py` | Modificar | alta con la regla y hash en hilo; `POST /users/{id}/reset-link` |
| `backend/api/auth.py` | Modificar | `POST /me/password`; `_crear_enlace_de_recuperacion`; `reset_password` con versión, token atómico y auditoría |
| `backend/tests/test_contrasenas.py` | Crear | tests |
| `frontend/src/lib/reglasPassword.js` | Crear | la misma regla en el frontend |
| `frontend/src/pages/ResetPassword.jsx` | Modificar | usa `reglasPassword` |
| `frontend/src/store/useJaxStore.js` | Modificar | acción `cambiarMiPassword` |
| `frontend/src/components/MiCuentaModal.jsx` | Crear | modal Mi cuenta |
| `frontend/src/components/BarraUsuario.jsx` | Modificar | el correo abre Mi cuenta |
| `frontend/src/pages/admin/AdminUsers.jsx` | Modificar | acción "Enviar enlace" |
| tests vitest | Crear/Modificar | `lib/reglasPassword.test.js`, `components/MiCuentaModal.test.jsx`, `components/BarraUsuario.test.jsx`, `pages/admin/AdminUsers.test.jsx` |
| `frontend/src/i18n/es.js`, `en.js` | Modificar | textos |
| `.github/workflows/policy.yml` | Modificar | pisos |

## Interfaces

**Consumes (etapas anteriores, firmas exactas):**

```python
# etapa 1
smtp_config.cargar_settings() -> SmtpSettings            # lanza SmtpNoConfigurado / SmtpConfigCorrupta (base SmtpNoDisponible, .codigo)
smtp_config.SmtpSettings(host, port, encryption, user, password, from_name, from_email)
api.auth._send_reset_email(settings, to_email, reset_link) -> None   # bloqueante, lanza OSError/SMTPException
# etapa 2
api.auth._emitir_tokens(response, user_id: str, tenant_id: str, role: str, token_version: int) -> str
auth.middleware.get_current_user -> AuthUser(user_id, tenant_id, role, email=None, token_version)
tests.identidades: sql, crear_usuario, borrar_usuario, token_para, auth ; fixture usuarios
# etapa 3
db.transaccion.transaccion()  # async context manager -> cursor
user_audit.registrar(cur, actor_user_id, target_user_id, action, detail=None, ip=None)
api.admin.users: _ip(request), VALID_ROLES, transaccion importado, create_user con auditoría
# existentes
db.seed._hash(plain) -> str (ValueError > 72 bytes) ; db.seed.verify_password(plain, hashed) (async) ; db.seed.BCRYPT_MAX_BYTES = 72
auth.rate_limit.check_login_rate(request, email) ; rate_limit.client_ip ; rate_limit.TRUSTED_PROXIES
```
```js
// etapa 3
import { mensajeDeError } from './erroresAdmin'   // t.adminErrors[code] || t.adminErrorGeneric
// i18n existentes: t.resetPasswordShort, t.resetPasswordLong, t.resetPasswordMismatch, t.resetPasswordLabel, t.resetPasswordConfirm
```

**Produces:**

```python
# backend/auth/password_rules.py
MIN_CARACTERES = 8
def problema_de_password(password: str) -> str | None    # "corta" | "larga" | None
# backend/api/auth.py
POST /api/auth/me/password {current_password, new_password} -> {"access_token", "token_type"} + cookie refresh_token nueva
async def _crear_enlace_de_recuperacion(user_id: int, client_ip: str) -> str
# backend/api/admin/users.py
POST /api/admin/users/{id}/reset-link -> {"ok": true, "to": email}
```
```js
// frontend/src/lib/reglasPassword.js
export const MIN_CARACTERES = 8
export const BCRYPT_MAX_BYTES = 72
export function problemaDePassword(password) // 'corta' | 'larga' | null
// store: cambiarMiPassword(actual, nueva) -> Promise (guarda el access nuevo)
// frontend/src/components/MiCuentaModal.jsx: export default function MiCuentaModal({ onCerrar })
```

---

### Task 1: Regla única de contraseña, en el alta y en `/reset-password`

**Files:**
- Create: `backend/auth/password_rules.py`
- Modify: `backend/api/admin/users.py` (imports, borrar el `_hash` local, `create_user`)
- Test: `backend/tests/test_contrasenas.py`
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `db.seed._hash`, `db.seed.BCRYPT_MAX_BYTES`.
- Produces: `problema_de_password`, `MIN_CARACTERES`; alta con 400 `password_corta`/`password_larga`.

- [ ] **Step 1: Tests que fallan**

Crear `backend/tests/test_contrasenas.py`:

```python
"""Contraseñas (2026-09-12, administración de usuarios, etapa 4, spec §3.4).

Una sola regla en todo el sistema: mínimo 8 caracteres, máximo 72 bytes
(bcrypt 5 LANZA con más, antes era un 500). Mi cuenta exige la actual y
cierra las otras sesiones; el reset por admin es un enlace por correo que
falla a la vista si no hay SMTP; completar un reset corta las sesiones
viejas.
"""
import uuid

from auth.password_rules import problema_de_password
from tests.identidades import auth, sql, token_para

CLAVE = "clave-vieja-123"
NUEVA = "clave-nueva-456"


def _admin():
    return auth(token_para(1, role="superadmin"))


# ---------------------------------------------------------------- puros

def test_menos_de_8_caracteres_es_corta():
    assert problema_de_password("1234567") == "corta"
    assert problema_de_password("12345678") is None


def test_mas_de_72_bytes_es_larga_aunque_tenga_menos_caracteres():
    assert problema_de_password("ñ" * 37) == "larga"   # 37 caracteres, 74 bytes
    assert problema_de_password("ñ" * 36) is None      # 72 bytes: el máximo


def test_cuenta_caracteres_como_python_no_unidades_utf16():
    assert problema_de_password("😀" * 7) == "corta"    # 7 caracteres (28 bytes)


# ------------------------------------------------------------------ alta

def test_el_alta_aplica_la_regla_y_no_da_500(client):
    email = f"test-alta-regla-{uuid.uuid4().hex[:10]}@example.invalid"
    for password, codigo in (("corta", "password_corta"), ("x" * 73, "password_larga")):
        r = client.post("/api/admin/users", json={"email": email, "role": "viewer", "password": password},
                        headers=_admin())
        assert (r.status_code, r.json()["detail"]) == (400, codigo)
    ((cuantos,),) = client.portal.call(sql, "SELECT COUNT(*) FROM jax_users WHERE email = %s", (email,), True)
    assert cuantos == 0
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_contrasenas.py -q`
Expected: error de colección `ModuleNotFoundError: No module named 'auth.password_rules'`. (Con el módulo creado y el alta sin tocar, `test_el_alta_aplica_la_regla_y_no_da_500` da `200` con "corta" y `500` con 73 bytes: el `_hash` local de users.py no tiene tope y bcrypt 5 lanza `ValueError`.)

- [ ] **Step 3: Implementación mínima**

Crear `backend/auth/password_rules.py`:

```python
"""Regla única de contraseña (2026-09-12, admin usuarios etapa 4, spec §3.4).

Mínimo 8 caracteres -- contados como puntos de código, que es lo que cuenta
len() de Python y lo que imita frontend/src/lib/reglasPassword.js --, y máximo
72 BYTES: bcrypt solo usa los primeros 72, y bcrypt 5 (el instalado) LANZA
con más (db/seed.py::BCRYPT_MAX_BYTES).
"""
from db.seed import BCRYPT_MAX_BYTES

MIN_CARACTERES = 8


def problema_de_password(password: str) -> str | None:
    if len(password) < MIN_CARACTERES:
        return "corta"
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        return "larga"
    return None
```

En `backend/api/admin/users.py`:

1. Borrar la línea `import bcrypt` y la función local:

```python
def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
```

2. Agregar a los imports (después de `from typing import Optional`) `import asyncio`, y después de `from auth import rate_limit` agregar `from auth.password_rules import problema_de_password`; después de `from db.connection import get_pool` agregar `from db.seed import _hash`. (El `_hash` de db/seed.py tiene el tope de 72 bytes; el local no lo tenía.)

3. En `create_user`, reemplazar:

```python
    ph = _hash(req.password)
    async with transaccion() as cur:
```

por:

```python
    problema = problema_de_password(req.password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    # bcrypt de costo 12 (~150 ms de CPU): en un hilo, no en el event loop.
    ph = await asyncio.to_thread(_hash, req.password)
    async with transaccion() as cur:
```

- [ ] **Step 4: Verde** — `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_contrasenas.py tests/test_admin_usuarios_guardas.py -q` → verde (4 nuevos + los de la etapa 3, incluido `test_alta_audita`).
- [ ] **Step 5: Pisos medidos** — sin DB +3; con DB +4.
- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/auth/password_rules.py backend/api/admin/users.py backend/tests/test_contrasenas.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(auth): regla única de contraseña (8 caracteres, 72 bytes); el alta ya no da 500"
```

---

### Task 2: Mi cuenta — `POST /api/auth/me/password`

**Files:**
- Modify: `backend/api/auth.py` (imports; modelo y endpoint nuevos, después de `logout`)
- Test: `backend/tests/test_contrasenas.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `_emitir_tokens` (etapa 2), `transaccion` y `user_audit.registrar` (etapa 3), `problema_de_password` (Task 1), `rate_limit.check_login_rate`.
- Produces: `POST /api/auth/me/password`.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_contrasenas.py`:

```python
# ------------------------------------------------------------- Mi cuenta

from auth import rate_limit  # noqa: E402
from auth.jwt import decode_token  # noqa: E402
from auth.rate_limit import SlidingWindowLimiter  # noqa: E402


async def _version(user_id):
    ((tv,),) = await sql("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,), True)
    return tv


async def _acciones(target):
    filas = await sql("SELECT action FROM user_admin_audit WHERE target_user_id = %s ORDER BY id", (target,), True)
    return [f[0] for f in filas]


def _cambiar(client, token, actual, nueva):
    try:
        return client.post("/api/auth/me/password", json={"current_password": actual, "new_password": nueva},
                           headers=auth(token))
    finally:
        client.cookies.clear()


def _login(client, email, password):
    try:
        return client.post("/api/auth/login", json={"email": email, "password": password})
    finally:
        client.cookies.clear()


def test_mi_cuenta_exige_la_contrasena_actual(client, usuarios):
    u, _ = usuarios(password=CLAVE)
    r = _cambiar(client, token_para(u), "equivocada", NUEVA)
    assert (r.status_code, r.json()["detail"]) == (400, "password_actual_incorrecta")
    assert client.portal.call(_version, u) == 0


def test_mi_cuenta_aplica_la_regla(client, usuarios):
    u, _ = usuarios(password=CLAVE)
    assert _cambiar(client, token_para(u), CLAVE, "corta").json()["detail"] == "password_corta"
    assert _cambiar(client, token_para(u), CLAVE, "x" * 73).json()["detail"] == "password_larga"
    assert client.portal.call(_version, u) == 0


def test_mi_cuenta_cambia_cierra_las_otras_sesiones_y_da_tokens_nuevos(client, usuarios):
    u, email = usuarios(password=CLAVE)
    viejo = token_para(u)
    r = client.post("/api/auth/me/password", json={"current_password": CLAVE, "new_password": NUEVA},
                    headers=auth(viejo))
    cookie = r.cookies.get("refresh_token")
    client.cookies.clear()
    assert r.status_code == 200, r.text
    nuevo = r.json()["access_token"]
    assert decode_token(nuevo)["tv"] == 1 and decode_token(cookie)["tv"] == 1
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401, "las otras sesiones se cierran"
    assert client.get("/api/auth/me", headers=auth(nuevo)).status_code == 200, "la actual sigue con tokens nuevos"
    assert _login(client, email, CLAVE).status_code == 401
    assert _login(client, email, NUEVA).status_code == 200
    assert client.portal.call(_acciones, u) == ["password_changed_self"]


def test_mi_cuenta_tiene_el_limite_del_login(client, usuarios, monkeypatch):
    monkeypatch.setattr(rate_limit, "LOGIN_IP_LIMITER", SlidingWindowLimiter(2, 60, 1000))
    monkeypatch.setattr(rate_limit, "TRUSTED_PROXIES", frozenset())
    u, _ = usuarios(password=CLAVE)
    for _ in range(2):
        assert _cambiar(client, token_para(u), "equivocada", NUEVA).status_code == 400
    r = _cambiar(client, token_para(u), "equivocada", NUEVA)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_contrasenas.py -q -k mi_cuenta`
Expected: los cuatro fallan porque la ruta no existe (`404`, o `405` si `frontend/dist` está montado en `/`): `KeyError: 'detail'` o la aserción de estado.

- [ ] **Step 3: Implementación mínima**

En `backend/api/auth.py`:

1. Imports: después de `from jax_engine.background import add_safe_task` agregar:

```python
import user_audit
from auth.password_rules import problema_de_password
from db.transaccion import transaccion
```

2. Después de la función `logout` agregar:

```python
class CambioPasswordRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


@router.post("/me/password", response_model=RefreshResponse)
async def cambiar_mi_password(
    req: CambioPasswordRequest,
    request: Request,
    response: Response,
    user: AuthUser = Depends(get_current_user),
):
    """Mi cuenta (2026-09-12, admin usuarios etapa 4, spec §3.4). Exige la
    contraseña actual, con el mismo límite de intentos que el login (sin él,
    un token robado sirve para adivinar la contraseña a velocidad de CPU).
    Sube token_version: se cierran las OTRAS sesiones, y esta recibe tokens
    nuevos (access en la respuesta, refresh en la cookie)."""
    user_id = int(user.user_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email, password_hash FROM jax_users WHERE user_id = %s", (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sesion_invalida")
    email, password_hash = fila
    rate_limit.check_login_rate(request, email)
    if not await verify_password(req.current_password, password_hash):
        # 400 y no 401: un 401 dispara el refresh del frontend y, al fallar, lo desloguea.
        raise HTTPException(status_code=400, detail="password_actual_incorrecta")
    problema = problema_de_password(req.new_password)
    if problema:
        raise HTTPException(status_code=400, detail=f"password_{problema}")
    nuevo_hash = await asyncio.to_thread(_hash, req.new_password)
    ip = rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)
    async with transaccion() as cur:
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, token_version = token_version + 1, "
            "failed_attempts = 0, locked_until = NULL WHERE user_id = %s",
            (nuevo_hash, user_id),
        )
        await cur.execute("SELECT token_version FROM jax_users WHERE user_id = %s", (user_id,))
        (nueva_version,) = await cur.fetchone()
        await user_audit.registrar(cur, user_id, user_id, "password_changed_self", None, ip)
    access = _emitir_tokens(response, user.user_id, user.tenant_id, user.role, nueva_version)
    return RefreshResponse(access_token=access)
```

- [ ] **Step 4: Verde** — `-k mi_cuenta` → `4 passed`; suite completa → 0 failed.
- [ ] **Step 5: Piso medido** — con DB +4.
- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/auth.py backend/tests/test_contrasenas.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(auth): Mi cuenta -- cambiar la propia contraseña cierra las otras sesiones"
```

---

### Task 3: Enlace de recuperación por admin, y el reset completado corta las sesiones

**Files:**
- Modify: `backend/api/auth.py` (`_procesar_recuperacion`, nueva `_crear_enlace_de_recuperacion`, `reset_password`)
- Modify: `backend/api/admin/users.py` (imports; endpoint `reset-link`)
- Test: `backend/tests/test_contrasenas.py` (agregar)
- Modify: `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `smtp_config.cargar_settings`, `smtp_config.SmtpNoDisponible`, `_send_reset_email(settings, to, link)` (etapa 1); `transaccion`, `user_audit.registrar`, `_ip` (etapa 3).
- Produces: `_crear_enlace_de_recuperacion(user_id, client_ip) -> str`; `POST /api/admin/users/{id}/reset-link`; `/reset-password` con `token_version + 1`, token de un solo uso bajo concurrencia y auditoría `password_reset_completed`.

- [ ] **Step 1: Tests que fallan**

Agregar al final de `backend/tests/test_contrasenas.py`:

```python
# ------------------------------------------------ enlace de recuperación

import smtplib  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

import smtp_config  # noqa: E402


@pytest.fixture
def smtp_configurado(monkeypatch):
    async def cargar():
        return smtp_config.SmtpSettings(host="mail.example.test", port=587, encryption="tls", user="u",
                                        password="p", from_name="Axioma", from_email="no-reply@example.test")

    monkeypatch.setattr(smtp_config, "cargar_settings", cargar)


def _enlace(client, user_id, cabeceras=None):
    return client.post(f"/api/admin/users/{user_id}/reset-link", headers=cabeceras or _admin())


async def _tokens(user_id):
    return [f[0] for f in await sql("SELECT token FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
                                    (user_id,), True)]


def test_enlace_sin_smtp_configurado_es_503_y_no_crea_token(client, usuarios, monkeypatch):
    async def sin_configurar():
        raise smtp_config.SmtpNoConfigurado("nunca configurado")

    monkeypatch.setattr(smtp_config, "cargar_settings", sin_configurar)
    u, _ = usuarios()
    r = _enlace(client, u)
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_no_configurado")
    assert client.portal.call(_tokens, u) == []


def test_enlace_crea_token_manda_al_correo_guardado_y_audita(client, usuarios, smtp_configurado, monkeypatch):
    from api import auth as auth_mod
    enviados = []
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append((s.host, to, link)))
    u, email = usuarios()
    r = _enlace(client, u)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "to": email}
    (token,) = client.portal.call(_tokens, u)
    ((host, destino, link),) = enviados
    assert (host, destino) == ("mail.example.test", email) and token in link
    assert client.portal.call(_acciones, u) == ["reset_link_sent"]


def test_enlace_con_smtp_que_falla_es_502_con_la_respuesta(client, usuarios, smtp_configurado, monkeypatch):
    from api import auth as auth_mod

    def falla(s, to, link):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    monkeypatch.setattr(auth_mod, "_send_reset_email", falla)
    u, _ = usuarios()
    r = _enlace(client, u)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "smtp_envio_fallido"
    assert "unexpectedly closed" in r.json()["detail"]["server"]
    assert client.portal.call(_acciones, u) == [], "no se audita un envío que no salió"


def test_enlace_a_inactivo_o_inexistente(client, usuarios, smtp_configurado):
    u, _ = usuarios(status="inactive")
    r = _enlace(client, u)
    assert (r.status_code, r.json()["detail"]) == (409, "usuario_no_activo")
    r = _enlace(client, 10**9)
    assert (r.status_code, r.json()["detail"]) == (404, "usuario_no_encontrado")


def test_enlace_solo_superadmin(client, usuarios, smtp_configurado):
    u, _ = usuarios()
    assert _enlace(client, u, auth(token_para(u))).status_code == 403


def test_completar_el_reset_cierra_las_sesiones_y_audita(client, usuarios):
    u, email = usuarios(password=CLAVE)
    viejo = token_para(u)
    token = str(uuid.uuid4())
    client.portal.call(sql, "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                            "VALUES (%s, %s, %s, 'test')", (u, token, datetime.utcnow() + timedelta(hours=1)))
    r = client.post("/api/auth/reset-password", json={"token": token, "password": NUEVA})
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me", headers=auth(viejo)).status_code == 401
    assert client.portal.call(_version, u) == 1
    assert client.portal.call(_acciones, u) == ["password_reset_completed"]
    assert _login(client, email, NUEVA).status_code == 200
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "otra-clave-789"})
    assert (r.status_code, r.json()["detail"]) == (400, "reset_token_usado")
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_contrasenas.py -q -k "enlace or reset"`
Expected: los de `reset-link` fallan porque la ruta no existe (`404`/`405`); `test_completar_el_reset_cierra_las_sesiones_y_audita` falla en `/api/auth/me` con el token viejo → `200` (hoy el reset no toca `token_version`).

- [ ] **Step 3: Implementación mínima**

En `backend/api/auth.py`, reemplazar la función `_procesar_recuperacion` completa (desde `async def _procesar_recuperacion` hasta su línea `logger.exception(...)` inclusive) por:

```python
async def _crear_enlace_de_recuperacion(user_id: int, client_ip: str) -> str:
    """Invalida los tokens pendientes del usuario, crea uno nuevo (1 hora) y
    devuelve el enlace. Lo comparten el forgot-password público y el reset
    por admin (etapa 4)."""
    token = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(hours=1)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
                (user_id,),
            )
            await cur.execute(
                "INSERT INTO password_reset_tokens (user_id, token, expires_at, ip_address) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, token, expires_at, client_ip),
            )
    frontend_origin = os.getenv("FRONTEND_ORIGIN", "https://axioma-ia.io")
    return f"{frontend_origin}/reset-password?token={token}"


async def _procesar_recuperacion(email: str, client_ip: str) -> None:
    """Crea el token y manda el correo, fuera del request. El correo va al
    email GUARDADO, no al que mandó el cliente."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, email FROM jax_users WHERE email = %s AND status = 'active'",
                    (email,),
                )
                row = await cur.fetchone()
        if not row:
            return

        user_id, email_guardado = row
        # Sin correo configurado (o con la configuración rota) no se crea un
        # token que nadie va a recibir: queda en el log con el código. La
        # respuesta pública ya salió, neutra, antes de esto.
        try:
            settings = await smtp_config.cargar_settings()
        except smtp_config.SmtpNoDisponible as exc:
            logger.error("Recuperación de contraseña: correo deshabilitado (%s); no se creó el token", exc.codigo)
            return

        reset_link = await _crear_enlace_de_recuperacion(user_id, client_ip)
        # smtplib es bloqueante: a un hilo, nunca en el event loop.
        await asyncio.to_thread(_send_reset_email, settings, email_guardado, reset_link)
    except Exception:  # fail-soft: corre después de responder; no hay a quién devolverle el error, queda en el log
        logger.exception("Recuperación de contraseña: falló el procesamiento en segundo plano")
```

Reemplazar la función `reset_password` completa por:

```python
@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
    # Códigos estables (2026-09-12): el frontend los traduce con i18n. La regla
    # es la única del sistema (auth/password_rules.py); los códigos de esta
    # pantalla se conservan.
    problema = problema_de_password(req.password)
    if problema == "corta":
        raise HTTPException(status_code=400, detail="reset_password_corta")
    if problema == "larga":
        raise HTTPException(status_code=400, detail="reset_password_larga")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, user_id, expires_at, used FROM password_reset_tokens WHERE token = %s",
                (req.token,),
            )
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=400, detail="reset_token_invalido")

    token_id, user_id, expires_at, used = row

    if used:
        raise HTTPException(status_code=400, detail="reset_token_usado")

    if expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="reset_token_expirado")

    # bcrypt de costo 12 (~150 ms): en un hilo. Antes corría en el event loop.
    new_hash = await asyncio.to_thread(_hash, req.password)
    ip = rate_limit.client_ip(request, rate_limit.TRUSTED_PROXIES)

    async with transaccion() as cur:
        # Reclamar el token es lo PRIMERO y es atómico: con dos envíos
        # simultáneos del mismo enlace, solo uno cambia la fila (el otro ve
        # 0 filas y revierte). Un UPDATE que cambia FALSE -> TRUE siempre
        # reporta 1 fila afectada; si el WHERE lo excluye, 0.
        await cur.execute(
            "UPDATE password_reset_tokens SET used = TRUE WHERE id = %s AND used = FALSE",
            (token_id,),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=400, detail="reset_token_usado")
        # Contraseña nueva por enlace: todas las sesiones viejas se cortan (spec §3.2).
        await cur.execute(
            "UPDATE jax_users SET password_hash = %s, failed_attempts = 0, locked_until = NULL, "
            "token_version = token_version + 1 WHERE user_id = %s",
            (new_hash, user_id),
        )
        await user_audit.registrar(cur, user_id, user_id, "password_reset_completed", None, ip)

    return {"ok": True, "message": "Contraseña actualizada correctamente"}
```

En `backend/api/admin/users.py`:

1. Imports: después de `import asyncio` agregar `import smtplib`; después de `import user_audit` agregar `import smtp_config` y `from api import auth as auth_api`.

2. Al final del archivo agregar:

```python
@router.post("/users/{user_id}/reset-link")
async def send_reset_link(user_id: int, request: Request, user: AuthUser = Depends(require_superadmin)):
    """Reset por admin = enlace por correo (spec §3.4). Reusa el núcleo de la
    recuperación pública (_crear_enlace_de_recuperacion + _send_reset_email),
    pero NO su envoltorio fail-soft: acá el admin ESPERA el envío (en un hilo)
    y ve el error. Un 200 sin correo sería el éxito falso que el spec prohíbe."""
    try:
        settings = await smtp_config.cargar_settings()
    except smtp_config.SmtpNoDisponible as exc:
        raise HTTPException(status_code=503, detail=exc.codigo) from exc
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email, status FROM jax_users WHERE user_id = %s", (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise HTTPException(status_code=404, detail="usuario_no_encontrado")
    email, estado = fila
    if estado != "active":
        raise HTTPException(status_code=409, detail="usuario_no_activo")
    ip = _ip(request)
    enlace = await auth_api._crear_enlace_de_recuperacion(user_id, ip)
    try:
        await asyncio.to_thread(auth_api._send_reset_email, settings, email, enlace)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=502, detail={"code": "smtp_envio_fallido", "server": str(exc)}) from exc
    async with transaccion() as cur:
        await user_audit.registrar(cur, int(user.user_id), user_id, "reset_link_sent", {"to": email}, ip)
    return {"ok": True, "to": email}
```

- [ ] **Step 4: Verde** — `tests/test_contrasenas.py tests/test_login_residuos.py` → verde (los códigos de reset de la etapa 1 siguen intactos). Suite completa → 0 failed.
- [ ] **Step 5: Piso medido** — con DB +6.
- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/auth.py backend/api/admin/users.py backend/tests/test_contrasenas.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): enlace de recuperación por admin (503 sin SMTP); el reset completado corta las sesiones"
```

---

### Task 4: Frontend — regla compartida, Mi cuenta y "Enviar enlace"

**Files:**
- Create: `frontend/src/lib/reglasPassword.js`, `frontend/src/lib/reglasPassword.test.js`
- Modify: `frontend/src/pages/ResetPassword.jsx`
- Modify: `frontend/src/store/useJaxStore.js`
- Create: `frontend/src/components/MiCuentaModal.jsx`, `frontend/src/components/MiCuentaModal.test.jsx`
- Modify: `frontend/src/components/BarraUsuario.jsx`, `frontend/src/components/BarraUsuario.test.jsx`
- Modify: `frontend/src/pages/admin/AdminUsers.jsx`, `frontend/src/pages/admin/AdminUsers.test.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`, `.github/workflows/policy.yml`

**Interfaces:**
- Consumes: `POST /api/auth/me/password`, `POST /api/admin/users/{id}/reset-link`; `mensajeDeError` y `t.adminErrors` (etapa 3); `PasswordInput` (pasa `id` y el resto de props al `<input>`).
- Produces: `problemaDePassword`, `cambiarMiPassword`, `MiCuentaModal`.

- [ ] **Step 1: Tests que fallan**

Crear `frontend/src/lib/reglasPassword.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { problemaDePassword } from './reglasPassword'

// La misma regla que backend/auth/password_rules.py (2026-09-12, etapa 4).
describe('problemaDePassword', () => {
  it('menos de 8 caracteres es corta', () => {
    expect(problemaDePassword('1234567')).toBe('corta')
    expect(problemaDePassword('12345678')).toBe(null)
  })

  it('más de 72 BYTES es larga aunque tenga menos de 72 caracteres', () => {
    expect(problemaDePassword('ñ'.repeat(37))).toBe('larga')
    expect(problemaDePassword('ñ'.repeat(36))).toBe(null)
  })

  it('cuenta caracteres como el backend: un emoji es uno (no dos unidades UTF-16)', () => {
    expect(problemaDePassword('😀'.repeat(7))).toBe('corta')
  })
})
```

Crear `frontend/src/components/MiCuentaModal.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Mi cuenta (2026-09-12, etapa 4): cambiar la propia contraseña exige la
// actual; la regla es la misma del backend y los errores se traducen.
const cambiarMock = vi.fn()
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ cambiarMiPassword: cambiarMock }),
}))

import MiCuentaModal from './MiCuentaModal'
import { I18nProvider } from '../i18n/index.jsx'

function llenar(actual, nueva, confirmar) {
  render(<I18nProvider><MiCuentaModal onCerrar={vi.fn()} /></I18nProvider>)
  fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: actual } })
  fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: nueva } })
  fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: confirmar } })
  fireEvent.click(screen.getByRole('button', { name: 'Cambiar contraseña' }))
}

beforeEach(() => {
  cambiarMock.mockReset()
  localStorage.clear()
})

describe('MiCuentaModal', () => {
  it('si la confirmación no coincide no llama al backend', () => {
    llenar('vieja-clave', 'nueva-clave-9', 'otra-clave-9')
    expect(screen.getByText('Las contraseñas no coinciden')).toBeInTheDocument()
    expect(cambiarMock).not.toHaveBeenCalled()
  })

  it('más de 72 bytes se frena antes de llamar al backend', () => {
    llenar('vieja-clave', 'ñ'.repeat(40), 'ñ'.repeat(40))
    expect(screen.getByText(/demasiado larga/)).toBeInTheDocument()
    expect(cambiarMock).not.toHaveBeenCalled()
  })

  it('la contraseña actual incorrecta se muestra traducida', async () => {
    cambiarMock.mockRejectedValue({ response: { status: 400, data: { detail: 'password_actual_incorrecta' } } })
    llenar('equivocada', 'nueva-clave-9', 'nueva-clave-9')
    expect(await screen.findByText('La contraseña actual no es correcta.')).toBeInTheDocument()
  })

  it('con éxito avisa que las otras sesiones se cerraron', async () => {
    cambiarMock.mockResolvedValue()
    llenar('vieja-clave', 'nueva-clave-9', 'nueva-clave-9')
    expect(await screen.findByText('Contraseña cambiada. Tus otras sesiones se cerraron.')).toBeInTheDocument()
    expect(cambiarMock).toHaveBeenCalledWith('vieja-clave', 'nueva-clave-9')
  })
})
```

En `frontend/src/components/BarraUsuario.test.jsx`, cambiar la línea del mock:

```jsx
  useJaxStore: (selector) => selector({ user: usuario, logout: logoutMock }),
```

por:

```jsx
  useJaxStore: (selector) => selector({ user: usuario, logout: logoutMock, cambiarMiPassword: vi.fn() }),
```

y agregar al final del archivo:

```jsx
describe('BarraUsuario — Mi cuenta', () => {
  it('un clic en el correo abre "Mi cuenta"', () => {
    renderBarra()
    fireEvent.click(screen.getByRole('button', { name: /fruiztorres@me.com/ }))
    expect(screen.getByRole('dialog', { name: 'Mi cuenta' })).toBeInTheDocument()
  })
})
```

Agregar al final de `frontend/src/pages/admin/AdminUsers.test.jsx`:

```jsx
describe('AdminUsers — enlace de recuperación', () => {
  it('sin SMTP configurado el 503 aparece traducido, no como éxito', async () => {
    api.post.mockRejectedValue({ response: { status: 503, data: { detail: 'smtp_no_configurado' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar enlace' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'El correo saliente no está configurado (Admin → Correo).',
    }))
    expect(api.post).toHaveBeenCalledWith('/admin/users/2/reset-link')
  })

  it('con éxito dice a qué correo se mandó', async () => {
    api.post.mockResolvedValue({ data: { ok: true, to: 'b@x.io' } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar enlace' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Enlace de recuperación enviado a b@x.io.',
    }))
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run src/lib src/components/MiCuentaModal.test.jsx src/components/BarraUsuario.test.jsx src/pages/admin/AdminUsers.test.jsx`
Expected: `reglasPassword.test.js` y `MiCuentaModal.test.jsx` fallan por `Failed to resolve import`; el nuevo de BarraUsuario, con `Unable to find role="button" and name /fruiztorres@me.com/`; los dos nuevos de AdminUsers, con `Unable to find role="button" and name "Enviar enlace"`. Los tests previos siguen verdes.

- [ ] **Step 3: Implementación mínima**

Crear `frontend/src/lib/reglasPassword.js`:

```js
// Regla única de contraseña (2026-09-12, admin usuarios etapa 4): la misma de
// backend/auth/password_rules.py. Mínimo 8 CARACTERES contados como puntos de
// código ([...s].length, igual que len() de Python; s.length contaría un emoji
// como 2) y máximo 72 BYTES: bcrypt no admite más (una ñ ocupa 2).
export const MIN_CARACTERES = 8
export const BCRYPT_MAX_BYTES = 72

export function problemaDePassword(password) {
  if ([...password].length < MIN_CARACTERES) return 'corta'
  if (new TextEncoder().encode(password).length > BCRYPT_MAX_BYTES) return 'larga'
  return null
}
```

En `frontend/src/pages/ResetPassword.jsx`:
- reemplazar las líneas

```jsx
// Límite del algoritmo bcrypt, no configuración: usa solo los primeros 72
// bytes, y el backend rechaza más (bcrypt 5 lanza error en vez de truncar).
const BCRYPT_MAX_BYTES = 72
```

por `import { problemaDePassword } from '../lib/reglasPassword'`;
- y reemplazar

```jsx
    if (password.length < 8) { setError(t.resetPasswordShort); return }
    // bcrypt no admite más de 72 BYTES (no caracteres: una ñ ocupa 2).
    if (new TextEncoder().encode(password).length > BCRYPT_MAX_BYTES) { setError(t.resetPasswordLong); return }
```

por:

```jsx
    // La misma regla que el backend (lib/reglasPassword.js).
    const problema = problemaDePassword(password)
    if (problema === 'corta') { setError(t.resetPasswordShort); return }
    if (problema === 'larga') { setError(t.resetPasswordLong); return }
```

En `frontend/src/store/useJaxStore.js`, inmediatamente antes de la línea `  setWsStatus: (wsStatus) => set({ wsStatus }),` agregar:

```js
  // Mi cuenta (2026-09-12, admin usuarios etapa 4): el backend sube la versión
  // de token -- cierra las OTRAS sesiones -- y le da a esta un access nuevo (y
  // la cookie de refresh nueva). Cambiar `token` reconecta el WebSocket con él
  // (useWebSocket depende de token).
  cambiarMiPassword: async (actual, nueva) => {
    const { data } = await api.post('/auth/me/password', { current_password: actual, new_password: nueva })
    set({ token: data.access_token })
  },

```

Crear `frontend/src/components/MiCuentaModal.jsx`:

```jsx
import { useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import PasswordInput from './PasswordInput'
import { problemaDePassword } from '../lib/reglasPassword'

// Mi cuenta (2026-09-12, admin usuarios etapa 4, spec §3.4): cambiar la propia
// contraseña. Exige la actual; al guardar se cierran las otras sesiones.
const CAMPO = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500'
const ETIQUETA = 'block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider'

export default function MiCuentaModal({ onCerrar }) {
  const { t } = useI18n()
  const cambiarMiPassword = useJaxStore((s) => s.cambiarMiPassword)
  const [actual, setActual] = useState('')
  const [nueva, setNueva] = useState('')
  const [confirmar, setConfirmar] = useState('')
  const [error, setError] = useState('')
  const [hecho, setHecho] = useState(false)
  const [enviando, setEnviando] = useState(false)

  const MENSAJES = {
    password_actual_incorrecta: t.myAccountWrongCurrent,
    password_corta: t.resetPasswordShort,
    password_larga: t.resetPasswordLong,
  }

  async function enviar(e) {
    e.preventDefault()
    setError('')
    const problema = problemaDePassword(nueva)
    if (problema === 'corta') { setError(t.resetPasswordShort); return }
    if (problema === 'larga') { setError(t.resetPasswordLong); return }
    if (nueva !== confirmar) { setError(t.resetPasswordMismatch); return }
    setEnviando(true)
    try {
      await cambiarMiPassword(actual, nueva)
      setHecho(true)
    } catch (err) {
      if (err?.response?.status === 429) setError(t.myAccountTooMany)
      else setError(MENSAJES[err?.response?.data?.detail] || t.myAccountError)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="mi-cuenta-titulo"
        className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h2 id="mi-cuenta-titulo" className="text-sm font-semibold text-slate-200 mb-1">{t.myAccount}</h2>
        <p className="text-xs text-slate-500 mb-4">{t.myAccountChangePassword}</p>
        {hecho ? (
          <div role="status" className="text-sm text-green-400 bg-green-900/30 border border-green-800 rounded-lg px-3 py-3">
            {t.myAccountDone}
          </div>
        ) : (
          <form onSubmit={enviar} className="space-y-3">
            <div>
              <label htmlFor="mi-cuenta-actual" className={ETIQUETA}>{t.myAccountCurrent}</label>
              <PasswordInput id="mi-cuenta-actual" value={actual} onChange={(e) => setActual(e.target.value)} className={CAMPO} autoComplete="current-password" required />
            </div>
            <div>
              <label htmlFor="mi-cuenta-nueva" className={ETIQUETA}>{t.resetPasswordLabel}</label>
              <PasswordInput id="mi-cuenta-nueva" value={nueva} onChange={(e) => setNueva(e.target.value)} className={CAMPO} autoComplete="new-password" required />
            </div>
            <div>
              <label htmlFor="mi-cuenta-confirmar" className={ETIQUETA}>{t.resetPasswordConfirm}</label>
              <PasswordInput id="mi-cuenta-confirmar" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} className={CAMPO} autoComplete="new-password" required />
            </div>
            {error && (
              <div className="text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">{error}</div>
            )}
            <div className="flex gap-2 justify-end pt-2">
              <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
              <button type="submit" disabled={enviando} className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold disabled:opacity-50 transition-colors">
                {enviando ? t.myAccountSubmitting : t.myAccountSubmit}
              </button>
            </div>
          </form>
        )}
        {hecho && (
          <div className="flex justify-end pt-4">
            <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminHistoryClose}</button>
          </div>
        )}
      </div>
    </div>
  )
}
```

En `frontend/src/components/BarraUsuario.jsx`:
- en la primera línea, antes de `import { Link } from 'react-router-dom'`, agregar `import { useState } from 'react'`, y después de `import { useI18n } from '../i18n/index.jsx'` agregar `import MiCuentaModal from './MiCuentaModal'`;
- dentro de `BarraUsuario()`, después de `const { lang, setLang, t } = useI18n()` agregar `const [miCuenta, setMiCuenta] = useState(false)`;
- reemplazar

```jsx
      <span className="text-xs text-slate-500 truncate max-w-[20rem]" title={user?.email}>
        {t.userLabel}: <span className="text-slate-300">{user?.email}</span>
      </span>
```

por:

```jsx
      {/* Mi cuenta (etapa 4): el correo es el acceso. Sin aria-label, a propósito:
          el nombre accesible sigue siendo "Usuario: <correo>". */}
      <button
        type="button"
        onClick={() => setMiCuenta(true)}
        title={t.myAccount}
        className="text-xs text-slate-500 truncate max-w-[20rem] rounded hover:text-slate-300 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        {t.userLabel}: <span className="text-slate-300">{user?.email}</span>
      </button>
      {miCuenta && <MiCuentaModal onCerrar={() => setMiCuenta(false)} />}
```

En `frontend/src/pages/admin/AdminUsers.jsx`:
- inmediatamente antes de `  async function handleDelete(u) {` agregar:

```jsx
  async function handleResetLink(u) {
    try {
      const { data } = await api.post(`/admin/users/${u.user_id}/reset-link`)
      avisarExito(t.adminResetLinkSent(data.to))
    } catch (err) {
      avisarError(err)
    }
  }

```

- después de la línea `<button onClick={() => handleRevoke(u)} className={ACCION_NEUTRA}>{t.adminUserRevokeSessions}</button>` agregar:

```jsx
                    <button onClick={() => handleResetLink(u)} className={ACCION_NEUTRA}>{t.adminUserSendResetLink}</button>
```

En `frontend/src/i18n/es.js`:
- en el objeto `adminErrors`, reemplazar `    sesion_invalida: 'Tu sesión ya no es válida. Volvé a entrar.',\n  },` por:

```js
    sesion_invalida: 'Tu sesión ya no es válida. Volvé a entrar.',
    usuario_no_activo: 'El usuario no está activo: no se le puede enviar un enlace.',
    smtp_no_configurado: 'El correo saliente no está configurado (Admin → Correo).',
    smtp_config_corrupta: 'La configuración de correo está dañada (Admin → Correo).',
    smtp_envio_fallido: 'El servidor de correo no aceptó el envío.',
    password_corta: 'La contraseña debe tener al menos 8 caracteres.',
    password_larga: 'La contraseña es demasiado larga (máximo 72 bytes; los acentos ocupan 2).',
  },
```

- e insertar ANTES de `  // Restaurar tareas pendientes (useJaxStore.js)`:

```js
  // Mi cuenta y enlace de recuperación — etapa 4 (2026-09-12)
  myAccount: 'Mi cuenta',
  myAccountChangePassword: 'Cambiar mi contraseña',
  myAccountCurrent: 'Contraseña actual',
  myAccountSubmit: 'Cambiar contraseña',
  myAccountSubmitting: 'Guardando…',
  myAccountDone: 'Contraseña cambiada. Tus otras sesiones se cerraron.',
  myAccountWrongCurrent: 'La contraseña actual no es correcta.',
  myAccountTooMany: 'Demasiados intentos. Esperá y volvé a intentarlo.',
  myAccountError: 'No se pudo cambiar la contraseña.',
  adminUserSendResetLink: 'Enviar enlace',
  adminResetLinkSent: (email) => `Enlace de recuperación enviado a ${email}.`,

```

En `frontend/src/i18n/en.js`:
- en `adminErrors`, reemplazar `    sesion_invalida: 'Your session is no longer valid. Sign in again.',\n  },` por:

```js
    sesion_invalida: 'Your session is no longer valid. Sign in again.',
    usuario_no_activo: 'The user is not active: a link cannot be sent.',
    smtp_no_configurado: 'Outgoing email is not configured (Admin → Email).',
    smtp_config_corrupta: 'The email settings are damaged (Admin → Email).',
    smtp_envio_fallido: 'The mail server did not accept the message.',
    password_corta: 'The password must be at least 8 characters.',
    password_larga: 'The password is too long (maximum 72 bytes; accented letters take 2).',
  },
```

- e insertar ANTES de `  // Restoring pending tasks (useJaxStore.js)`:

```js
  // My account and recovery link — stage 4 (2026-09-12)
  myAccount: 'My account',
  myAccountChangePassword: 'Change my password',
  myAccountCurrent: 'Current password',
  myAccountSubmit: 'Change password',
  myAccountSubmitting: 'Saving…',
  myAccountDone: 'Password changed. Your other sessions were closed.',
  myAccountWrongCurrent: 'The current password is not correct.',
  myAccountTooMany: 'Too many attempts. Wait and try again.',
  myAccountError: 'The password could not be changed.',
  adminUserSendResetLink: 'Send link',
  adminResetLinkSent: (email) => `Recovery link sent to ${email}.`,

```

- [ ] **Step 4: Verde** — `cd /home/fruiz/jax-platform/frontend && npx vitest run` → piso actual + 10 (3 + 4 + 1 + 2), 0 fallidos; `ResetPassword.test.jsx` sigue verde.
- [ ] **Step 5: Piso de vitest** con el número medido (esperado 107 si era 97) y su comentario.
- [ ] **Step 6: Claro/oscuro a mano** — vite en `127.0.0.1:5174`: el correo de la barra abre "Mi cuenta"; modal y botón "Enviar enlace" legibles en los dos temas.
- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/jax-platform add frontend/src/lib/reglasPassword.js frontend/src/lib/reglasPassword.test.js frontend/src/pages/ResetPassword.jsx frontend/src/store/useJaxStore.js frontend/src/components/MiCuentaModal.jsx frontend/src/components/MiCuentaModal.test.jsx frontend/src/components/BarraUsuario.jsx frontend/src/components/BarraUsuario.test.jsx frontend/src/pages/admin/AdminUsers.jsx frontend/src/pages/admin/AdminUsers.test.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(ui): Mi cuenta desde la barra y enviar enlace de recuperación desde Admin"
```

---

### Task 5: PR, CI por headSha, despliegue y verificación en vivo

- [ ] **Step 1: Suite local completa** (con DB, sin DB, vitest) = pisos del archivo.
- [ ] **Step 2: PR** — rama `feat/admin-usuarios-4-contrasenas`, título "Admin usuarios · etapa 4: Mi cuenta y enlace de recuperación por admin".
- [ ] **Step 3: Gate por headSha** — `gh pr checks <N> --repo fjruizhn/jax-platform` sin nada fuera de `SUCCESS`; `gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid` == `git -C /home/fruiz/jax-platform ls-remote origin refs/heads/feat/admin-usuarios-4-contrasenas`. Recién ahí `gh pr merge <N> --repo fjruizhn/jax-platform --merge`.
- [ ] **Step 4: Backend** — `git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only`; `sudo -n /usr/bin/systemctl restart jax-platform.service`; health `200`; `sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40` sin tracebacks.
- [ ] **Step 5: Frontend** — build, backup y rsync en dos saltos con `--exclude .user.ini`, hash servido:

```bash
cd /home/fruiz/jax-platform/frontend && npm run build
set -a; . /etc/jax/.env; set +a
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-admin-usuarios-4-$(date +%Y%m%d-%H%M%S)"
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
ls /home/fruiz/jax-platform/frontend/dist/assets/index-*.js; curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

- [ ] **Step 6: Verificación en vivo (avisar a Fernando)**
  1. Con un usuario de prueba creado desde Admin: iniciar sesión en dos ventanas; en una, "Mi cuenta" → cambiar la contraseña → mensaje de éxito, y esa ventana sigue funcionando (tokens nuevos). En la otra, el request siguiente la saca al login. El historial del usuario muestra "Cambió su contraseña".
  2. **GATE — correo real, solo con permiso explícito de Fernando:** "Enviar enlace" sobre un usuario de prueba cuyo correo sea un buzón que Fernando controle → toast "Enlace de recuperación enviado a …"; abrir el enlace, fijar una contraseña nueva; una sesión previa de ese usuario queda afuera. Historial: "Enlace de recuperación enviado" y "Restableció su contraseña".
  3. Sin permiso para el correo real, verificar solo el camino de error sin enviar nada: si SMTP estuviera sin configurar, el toast traducido del 503. No se desconfigura SMTP en producción para probarlo: ese caso ya lo cubre `test_enlace_sin_smtp_configurado_es_503_y_no_crea_token`.
  4. Borrar el usuario de prueba (todavía por `DELETE`; la baja llega en la etapa 5).
- [ ] **Step 7: Biblioteca** — entrada fechada en `/home/fruiz/jax/DEUDA.md` (PR propio en `jax`): sha, `index-*.js`, resultado de cada punto.

---

## Autorrevisión (hecha al escribir el plan)

- **Cobertura de §3.4:** regla única en backend y frontend (Task 1 y `lib/reglasPassword.js`, que usan ResetPassword y Mi cuenta); Mi cuenta desde el correo de `BarraUsuario`, con `PasswordInput` para actual, nueva y confirmación, `POST /api/auth/me/password`, que exige la actual, tiene el límite del login, sube `token_version` y entrega tokens nuevos (Tasks 2 y 4); reset por admin con `POST /users/{id}/reset-link`, que responde 503 con código estable sin SMTP (Task 3); `token_version` sube al completar el reset (Task 3). Auditoría: `password_changed_self`, `reset_link_sent`, `password_reset_completed`.
- **Diferencia con el texto del spec, declarada:** "reusa `_procesar_recuperacion`". Se reusa su núcleo (`_crear_enlace_de_recuperacion` + `_send_reset_email`), no la función entera: `_procesar_recuperacion` es fail-soft (traga el error para el forgot público) y el spec pide que el admin vea el error. Reusarla tal cual daba un éxito falso.
- **Correcciones de paso en código que se toca:** `reset_password` hacía bcrypt en el event loop y no reclamaba el token de forma atómica (dos envíos simultáneos del mismo enlace podían fijar dos contraseñas); el alta usaba un `_hash` sin tope de 72 bytes (500 con bcrypt 5).
- **Placeholders:** `<N>` y los conteos medidos son de ejecución.
- **Nombres:** `problema_de_password`/`problemaDePassword`, `_crear_enlace_de_recuperacion`, `cambiarMiPassword`, `MiCuentaModal({ onCerrar })`, iguales en tests, código e Interfaces.
