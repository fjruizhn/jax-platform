# Administración de usuarios · Etapa 1 — Correo saliente (SMTP) configurable desde Admin

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el correo saliente se configure desde la pantalla de Admin (en la base, con la contraseña cifrada), con "Probar conexión" paso a paso y un correo de prueba, y que la recuperación de contraseña lo use — copiando el comportamiento de AteneaERP.

**Architecture:** Un módulo de dominio `backend/smtp_config.py` lee e interpreta las claves `smtp.*` de `axioma_config` (tabla clave/valor que ya existe), cifra la contraseña con `crypto_secrets.encrypt_secret` y la descifra con `decrypt_db_secret`, detecta el estado corrupto y envía con `smtplib` y el certificado TLS **verificado**. Un router `backend/api/admin/smtp.py` expone cuatro endpoints de superadmin. `api/auth.py::_send_reset_email` deja de leer `SMTP_*` del entorno y usa ese módulo. Los endpoints genéricos `/api/admin/config` dejan de ver y de escribir `smtp.*`. En el frontend se agrega la pantalla `AdminSmtp.jsx`, con su entrada en la barra lateral de Admin.

**Tech Stack:** FastAPI + Python 3.14 (`backend/.venv`), aiomysql, `cryptography` (Fernet), `smtplib`/`ssl` de la biblioteca estándar, pytest (+pytest-asyncio, `asyncio_mode = auto`); React 19 + Vite + Tailwind, vitest + Testing Library.

**Spec:** `/home/fruiz/jax-platform/docs/superpowers/specs/2026-09-12-administracion-usuarios-design.md` (§3.1, §5, §6 punto 1). Referencia a copiar: `/home/fruiz/ateneaerp/app/Services/SmtpService.php`, `/home/fruiz/ateneaerp/app/Http/Controllers/Api/SmtpController.php`, `/home/fruiz/ateneaerp/app/Models/SystemSetting.php`, `/home/fruiz/ateneaerp/resources/views/emails/smtp_test.blade.php`.

## Global Constraints

- **Repo:** `/home/fruiz/jax-platform`. Rama nueva desde `master` actualizado: `git -C /home/fruiz/jax-platform fetch origin && git -C /home/fruiz/jax-platform switch -c feat/admin-usuarios-1-smtp origin/master`. Siempre `git -C <ruta>` y nunca un `cd` que pueda fallar en silencio.
- **TDD obligatorio:** test primero, correrlo y **ver el rojo por el motivo esperado** (el que dice cada paso), implementación mínima, verde, commit.
- **Backend tests:** en `backend/tests/`, con `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest ...`. Los que usan el fixture `client` corren contra `jax_memory_test` (lo fuerza `tests/conftest.py`); los puros (sin `client` y sin DB) corren también en el job sin DB del CI. Un helper async se llama desde un test con `client.portal.call(fn, *args)`.
- **Ningún test modifica `user_id=1`** (el superadmin sembrado en `jax_memory_test`): muchos archivos firman tokens para él.
- **Pisos de CI exactos** en `/home/fruiz/jax-platform/.github/workflows/policy.yml`, hoy **`PISO_PASSED = 427`** (job con DB), **`JAX_CI_MIN_PASSED: "221"`** (job sin DB) y **`r.numPassedTests !== 87`** (vitest). Cada tarea que agrega tests sube el piso que corresponde con el número **MEDIDO**, no el calculado, y deja un comentario con el porqué, como los que ya hay.
- **P10:** todo `except Exception` lleva en la MISMA línea `# fail-soft: <razón>` (scanner `tests/test_no_fail_open_except.py`). Un `except` acotado que solo hace `pass` también lo lleva.
- **BackgroundTasks:** nunca `.add_task` crudo, siempre `jax_engine.background.add_safe_task` (`tests/test_policy_background_tasks.py`).
- **Async:** nada bloqueante en un `async def`. `smtplib` va SIEMPRE dentro de `asyncio.to_thread`.
- **Diferencia deliberada con AteneaERP:** verificación TLS **ENCENDIDA** (`ssl.create_default_context()`, que verifica cadena y nombre). AteneaERP la apaga (`verify_peer => false`).
- **Sin caché** de la configuración SMTP (spec §3.1: se envía poco; se lee al enviar).
- **i18n es/en** para todo texto visible. El backend devuelve CÓDIGOS estables en `detail` (string, o `{"code": ..., "server": ...}` cuando hay respuesta del servidor que mostrar) y el frontend los traduce.
- **Modo claro/oscuro:** solo clases `slate` que `frontend/src/index.css` ya sobrescribe bajo `html.light-mode`, más los rojos y verdes que ya usa la UI (`text-red-400`, `text-green-400`, `bg-red-900/30`, `border-red-800`, `bg-green-900/30`, `border-green-800`).
- **Sin migraciones** en esta etapa: `axioma_config` ya existe.
- **Commits** sin `--no-verify` (esta etapa no toca `backend/db/seed.py`).
- **YAGNI:** nada de spec §4 (registro por autoservicio, 2FA, multi-tenant, derecho al olvido). El destinatario del correo de prueba es el superadmin autenticado, como dice el spec; no se agrega un campo "destinatario".
- **Cierre de etapa:** PR → CI verde verificado por `headSha` ANTES de mergear → despliegue backend y frontend → verificación en vivo, con el correo real SOLO con permiso explícito de Fernando.

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backend/validacion.py` | Crear | `EMAIL_MAX = 254`, `email_valido()`; lo reusan esta etapa (remitente) y la 5 (email de usuario) |
| `backend/smtp_config.py` | Crear | Dominio SMTP: leer/interpretar/guardar `smtp.*`, estado corrupto, armar mensaje, enviar, probar conexión |
| `backend/api/admin/smtp.py` | Crear | `GET/PUT /api/admin/smtp`, `POST /api/admin/smtp/test-connection`, `POST /api/admin/smtp/test` |
| `backend/api/admin/__init__.py` | Modificar | exportar `smtp_router` |
| `backend/main.py` | Modificar | registrar `smtp_router` |
| `backend/api/admin/config_admin.py` | Modificar | excluir `smtp.*` del GET y rechazarlo en el PUT |
| `backend/api/auth.py` | Modificar | `_procesar_recuperacion` y `_send_reset_email` usan `smtp_config` |
| `backend/tests/test_smtp_config.py` | Crear | tests puros del dominio |
| `backend/tests/test_smtp_endpoints.py` | Crear | tests de endpoints con `client` |
| `backend/tests/test_login_residuos.py` | Modificar | adaptar 2 tests a la firma nueva de `_send_reset_email`, agregar 1 |
| `frontend/src/pages/admin/AdminSmtp.jsx` | Crear | pantalla "Correo (SMTP)" |
| `frontend/src/pages/admin/AdminSmtp.test.jsx` | Crear | tests vitest |
| `frontend/src/pages/Admin.jsx` | Modificar | ruta `smtp` |
| `frontend/src/components/admin/AdminSidebar.jsx` | Modificar | entrada `smtp` |
| `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js` | Modificar | textos |
| `.github/workflows/policy.yml` | Modificar | tres pisos |

## Interfaces que esta etapa PRODUCE (las consumen las etapas 4 y 5)

```python
# backend/validacion.py
EMAIL_MAX: int = 254
def email_valido(valor: str) -> bool

# backend/smtp_config.py
CLAVES: tuple[str, ...]            # las 7 claves smtp.*
CLAVE_SECRETA: str = "smtp.password"
MASCARA: str = "••••••••"
class SmtpNoDisponible(Exception): codigo: str          # base
class SmtpNoConfigurado(SmtpNoDisponible): codigo = "smtp_no_configurado"
class SmtpConfigCorrupta(SmtpNoDisponible): codigo = "smtp_config_corrupta"; motivo: str
class SmtpExigeContrasena(Exception): codigo = "smtp_exige_contrasena"; motivo: str
class SmtpPasoFallido(Exception): codigo: str; servidor: str
@dataclass(frozen=True) class SmtpSettings: host, port, encryption, user, password, from_name, from_email
async def leer_filas() -> dict[str, str]
async def guardar_filas(filas: dict[str, str]) -> None
async def cargar_settings() -> SmtpSettings            # lanza SmtpNoConfigurado / SmtpConfigCorrupta
def interpretar(filas: dict[str, str]) -> SmtpSettings
def motivo_de_corrupcion(filas: dict[str, str]) -> str | None
def estado_para_pantalla(filas: dict[str, str]) -> dict
def filas_a_guardar(filas_actuales: dict[str, str], datos: dict) -> dict[str, str]
def construir_mensaje(settings: SmtpSettings, destinatario: str, asunto: str, texto: str, html: str) -> EmailMessage
def enviar(settings: SmtpSettings, mensaje: EmailMessage) -> None        # bloqueante: SIEMPRE en asyncio.to_thread
def probar_conexion(host: str, port: int, encryption: str, user: str, password: str) -> None  # bloqueante; lanza SmtpPasoFallido

# backend/api/auth.py
def _send_reset_email(settings: smtp_config.SmtpSettings, to_email: str, reset_link: str) -> None  # bloqueante, LANZA si falla
```

Frontend: `t.smtpErrors` (objeto código → texto) y `t.smtpErrorGeneric` existen desde esta etapa.

---

### Task 1: Dominio SMTP puro (`validacion.py` + `smtp_config.py`)

**Files:**
- Create: `backend/validacion.py`
- Create: `backend/smtp_config.py`
- Test: `backend/tests/test_smtp_config.py`
- Modify: `.github/workflows/policy.yml` (pisos `JAX_CI_MIN_PASSED` y `PISO_PASSED`)

**Interfaces:**
- Consumes: `crypto_secrets.encrypt_secret(value: str) -> str` (lanza `RuntimeError` sin `FERNET_KEY`), `crypto_secrets.decrypt_db_secret(value: str) -> str` (devuelve `""` si no descifra o si falta `FERNET_KEY`), `db.connection.get_pool()`.
- Produces: todo lo de `validacion.py` y `smtp_config.py` listado arriba.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `backend/tests/test_smtp_config.py`:

```python
"""Dominio del correo saliente (2026-09-12, administración de usuarios, etapa 1).

Copia el COMPORTAMIENTO de AteneaERP (SmtpService / SystemSetting, D-9):
  - un dominio nunca configurado (sin smtp.host) es AUSENCIA legítima;
  - configurado y con una clave faltante o una contraseña que no descifra es
    ESTADO CORRUPTO: el envío queda deshabilitado con un motivo explícito, y
    reconfigurar exige volver a escribir la contraseña;
  - la contraseña nunca sale: la pantalla recibe una máscara.
Diferencia deliberada: la verificación del certificado TLS queda ENCENDIDA.

Todos puros: sin DB y sin red (smtplib reemplazado por dobles).
"""
import smtplib
import ssl

import pytest
from cryptography.fernet import Fernet

import smtp_config
from validacion import email_valido


@pytest.fixture(autouse=True)
def clave_fernet(monkeypatch):
    # crypto_secrets lee FERNET_KEY de os.environ en cada llamada: una clave
    # propia por test, sin depender de /etc/jax/.env (el runner no lo tiene).
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())


def _filas(**cambios):
    filas = {
        "smtp.host": "mail.example.test",
        "smtp.port": "587",
        "smtp.encryption": "tls",
        "smtp.user": "no-reply@example.test",
        "smtp.password": smtp_config.encrypt_secret("clave-smtp-de-prueba"),
        "smtp.from_name": "Axioma",
        "smtp.from_email": "no-reply@example.test",
    }
    filas.update(cambios)
    return {k: v for k, v in filas.items() if v is not None}


def _datos(**cambios):
    datos = {
        "host": "mail.example.test", "port": 587, "encryption": "tls",
        "user": "no-reply@example.test", "password": None,
        "from_name": "Axioma", "from_email": "no-reply@example.test",
    }
    datos.update(cambios)
    return datos


# ------------------------------------------------------------ interpretación

def test_sin_host_es_no_configurado_y_no_corrupto():
    assert smtp_config.motivo_de_corrupcion({}) is None
    estado = smtp_config.estado_para_pantalla({})
    assert estado["configurado"] is False and estado["corrupta"] is False
    assert estado["password"] == ""


def test_interpretar_sin_host_lanza_no_configurado():
    with pytest.raises(smtp_config.SmtpNoConfigurado) as exc:
        smtp_config.interpretar({"smtp.port": "587"})
    assert exc.value.codigo == "smtp_no_configurado"


def test_clave_ausente_con_host_es_corrupta():
    filas = _filas(**{"smtp.port": None})
    assert smtp_config.motivo_de_corrupcion(filas) == "clave_ausente:smtp.port"
    with pytest.raises(smtp_config.SmtpConfigCorrupta) as exc:
        smtp_config.interpretar(filas)
    assert exc.value.codigo == "smtp_config_corrupta"
    assert exc.value.motivo == "clave_ausente:smtp.port"


def test_password_que_no_descifra_es_corrupta_y_la_pantalla_no_muestra_mascara():
    ajena = Fernet(Fernet.generate_key()).encrypt(b"otra").decode()  # como si rotara FERNET_KEY
    filas = _filas(**{"smtp.password": ajena})
    assert smtp_config.motivo_de_corrupcion(filas) == "password_ilegible"
    estado = smtp_config.estado_para_pantalla(filas)
    assert estado["corrupta"] is True and estado["motivo"] == "password_ilegible"
    assert estado["password"] == ""  # sin máscara: no hay contraseña utilizable


def test_interpretar_completo_descifra_la_contrasena():
    s = smtp_config.interpretar(_filas())
    assert (s.host, s.port, s.encryption, s.password) == (
        "mail.example.test", 587, "tls", "clave-smtp-de-prueba")


def test_pantalla_muestra_mascara_y_nunca_el_cifrado():
    filas = _filas()
    estado = smtp_config.estado_para_pantalla(filas)
    assert estado["password"] == smtp_config.MASCARA
    assert filas["smtp.password"] not in estado.values()
    assert "clave-smtp-de-prueba" not in estado.values()
    assert estado["configurado"] is True and estado["corrupta"] is False


# ------------------------------------------------------------------ guardado

def test_guardar_con_mascara_conserva_la_contrasena_guardada():
    filas = smtp_config.filas_a_guardar(_filas(), _datos(password=smtp_config.MASCARA))
    assert smtp_config.CLAVE_SECRETA not in filas  # no se reescribe
    assert filas["smtp.host"] == "mail.example.test" and filas["smtp.port"] == "587"


def test_guardar_con_contrasena_nueva_la_cifra():
    filas = smtp_config.filas_a_guardar(_filas(), _datos(password="nueva-clave"))
    cifrada = filas[smtp_config.CLAVE_SECRETA]
    assert cifrada != "nueva-clave"
    assert smtp_config.decrypt_db_secret(cifrada) == "nueva-clave"


def test_guardar_sobre_estado_corrupto_exige_contrasena():
    ajena = Fernet(Fernet.generate_key()).encrypt(b"otra").decode()
    corrupta = _filas(**{"smtp.password": ajena})
    with pytest.raises(smtp_config.SmtpExigeContrasena) as exc:
        smtp_config.filas_a_guardar(corrupta, _datos(password=smtp_config.MASCARA))
    assert exc.value.motivo == "password_ilegible"
    filas = smtp_config.filas_a_guardar(corrupta, _datos(password="reescrita"))
    assert smtp_config.decrypt_db_secret(filas[smtp_config.CLAVE_SECRETA]) == "reescrita"


def test_guardar_la_primera_vez_exige_contrasena():
    with pytest.raises(smtp_config.SmtpExigeContrasena) as exc:
        smtp_config.filas_a_guardar({}, _datos(password=""))
    assert exc.value.motivo == "sin_contrasena"


# ------------------------------------------------------------------- envío

def _smtp_falso(registro, *, extensiones=("starttls", "auth"), ehlo_code=250,
                starttls_error=None, login_error=None, connect_error=None):
    class _Falso:
        def __init__(self, host, port, timeout=None, context=None):
            if connect_error is not None:
                raise connect_error
            self.host, self.port, self.timeout = host, port, timeout
            self.contexto_ssl = context
            self.contexto_starttls = None
            self.llamadas = []
            registro.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.llamadas.append("exit")
            return False

        def ehlo(self):
            self.llamadas.append("ehlo")
            return ehlo_code, b"mail.example.test"

        def has_extn(self, nombre):
            return nombre.lower() in extensiones

        def starttls(self, context=None):
            self.llamadas.append("starttls")
            self.contexto_starttls = context
            if starttls_error is not None:
                raise starttls_error

        def login(self, user, password):
            self.llamadas.append(("login", user, password))
            if login_error is not None:
                raise login_error

        def send_message(self, mensaje):
            self.llamadas.append(("send", mensaje["To"]))

        def quit(self):
            self.llamadas.append("quit")

        def close(self):
            self.llamadas.append("close")

    return _Falso


def _verifica(contexto):
    return contexto.verify_mode == ssl.CERT_REQUIRED and contexto.check_hostname is True


def test_enviar_por_starttls_verifica_el_certificado(monkeypatch):
    registro = []
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso(registro))
    s = smtp_config.interpretar(_filas())
    msg = smtp_config.construir_mensaje(s, "dest@example.test", "asunto", "texto", "<p>html</p>")
    smtp_config.enviar(s, msg)
    (srv,) = registro
    assert srv.timeout == smtp_config.TIMEOUT_S
    assert _verifica(srv.contexto_starttls), "AteneaERP la apaga; acá debe quedar encendida"
    assert ("login", "no-reply@example.test", "clave-smtp-de-prueba") in srv.llamadas
    assert ("send", "dest@example.test") in srv.llamadas


def test_enviar_por_ssl_verifica_el_certificado(monkeypatch):
    registro = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", _smtp_falso(registro))
    s = smtp_config.interpretar(_filas(**{"smtp.encryption": "ssl", "smtp.port": "465"}))
    smtp_config.enviar(s, smtp_config.construir_mensaje(s, "d@example.test", "a", "t", "<p>h</p>"))
    (srv,) = registro
    assert _verifica(srv.contexto_ssl)
    assert "starttls" not in srv.llamadas


def test_probar_conexion_informa_la_autenticacion_rechazada(monkeypatch):
    registro = []
    error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso(registro, login_error=error))
    with pytest.raises(smtp_config.SmtpPasoFallido) as exc:
        smtp_config.probar_conexion("mail.example.test", 587, "tls", "u", "p")
    assert exc.value.codigo == "smtp_auth_rechazada"
    assert exc.value.servidor == "535 5.7.8 Authentication failed"
    assert "quit" in registro[0].llamadas


def test_probar_conexion_informa_starttls_ausente(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso([], extensiones=("auth",)))
    with pytest.raises(smtp_config.SmtpPasoFallido) as exc:
        smtp_config.probar_conexion("mail.example.test", 587, "tls", "u", "p")
    assert exc.value.codigo == "smtp_starttls_no_disponible"


def test_probar_conexion_informa_la_conexion_rechazada(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso([], connect_error=ConnectionRefusedError(111, "Connection refused")))
    with pytest.raises(smtp_config.SmtpPasoFallido) as exc:
        smtp_config.probar_conexion("mail.example.test", 587, "tls", "u", "p")
    assert exc.value.codigo == "smtp_conexion_fallida"
    assert "Connection refused" in exc.value.servidor


def test_probar_conexion_informa_el_certificado_invalido(monkeypatch):
    error = ssl.SSLCertVerificationError(1, "certificate verify failed: Hostname mismatch")
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso([], starttls_error=error))
    with pytest.raises(smtp_config.SmtpPasoFallido) as exc:
        smtp_config.probar_conexion("mail.example.test", 587, "tls", "u", "p")
    assert exc.value.codigo == "smtp_tls_fallido"
    assert "certificate verify failed" in exc.value.servidor


def test_probar_conexion_exitosa_cierra_con_quit(monkeypatch):
    registro = []
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso(registro))
    smtp_config.probar_conexion("mail.example.test", 587, "tls", "u", "p")
    (srv,) = registro
    assert srv.llamadas[:2] == ["ehlo", "starttls"]
    assert ("login", "u", "p") in srv.llamadas and srv.llamadas[-1] == "quit"
    assert _verifica(srv.contexto_starttls)


def test_mensaje_lleva_remitente_destinatario_y_html():
    s = smtp_config.interpretar(_filas())
    msg = smtp_config.construir_mensaje(s, "dest@example.test", "Asunto", "texto plano", "<p>html</p>")
    assert msg["From"] == "Axioma <no-reply@example.test>"
    assert msg["To"] == "dest@example.test" and msg["Subject"] == "Asunto"
    assert msg["Message-ID"].endswith("@example.test>")
    tipos = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in tipos and "text/html" in tipos


def test_email_valido():
    assert email_valido("no-reply@axioma-ia.io")
    for malo in ("", "sin-arroba", "a@b", "a b@c.io", "@c.io", "a@.io", "a@" + "b" * 250 + ".io"):
        assert not email_valido(malo), malo
```

- [ ] **Step 2: Correr los tests y verlos fallar**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_smtp_config.py -q`
Expected: la colección falla con `ModuleNotFoundError: No module named 'smtp_config'` (y `validacion`).

- [ ] **Step 3: Implementación mínima**

Crear `backend/validacion.py`:

```python
"""Validaciones compartidas (2026-09-12, administración de usuarios).

EMAIL_MAX = 254: máximo de RFC 5321, el mismo tope que ya usan LoginRequest y
ForgotPasswordRequest. No hay `email-validator` instalado (medido
2026-09-12): la regla es deliberadamente simple, una sola arroba, sin
espacios y con un punto en el dominio. Lo que decide si la dirección existe es
el servidor de correo, no esta función.
"""
import re

EMAIL_MAX = 254
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def email_valido(valor: str) -> bool:
    return bool(valor) and len(valor) <= EMAIL_MAX and _EMAIL.match(valor) is not None
```

Crear `backend/smtp_config.py`:

```python
"""Correo saliente configurable desde Admin (2026-09-12, administración de
usuarios, etapa 1). Copia el COMPORTAMIENTO de AteneaERP
(app/Services/SmtpService.php, SmtpController.php, Models/SystemSetting.php)
con la infraestructura de jax-platform:

  - las claves viven en `axioma_config` (clave/valor), no en /etc/jax/.env;
  - la contraseña se guarda con crypto_secrets.encrypt_secret (Fernet,
    FERNET_KEY) y se lee con decrypt_db_secret, que devuelve "" si no
    descifra: nunca el texto cifrado como si fuera la contraseña;
  - ESTADO CORRUPTO (D-9 de AteneaERP): con smtp.host presente, una clave que
    falta o una contraseña que no descifra deja el envío DESHABILITADO con un
    motivo explícito. Nunca cae a otra configuración en silencio.
    Simplificación declarada respecto de AteneaERP: no hay "testigo" de
    instalación; smtp.host es el que separa "nunca configurado" de "roto".

DIFERENCIA DELIBERADA: AteneaERP apaga la verificación del certificado
(verify_peer => false) para aceptar autofirmados. Acá queda ENCENDIDA:
mail.axioma-ia.io tiene un Let's Encrypt *.axioma-ia.io válido (medido
2026-09-12), y un TLS sin verificar solo protege contra quien no está mirando.

Sin caché: se envía poco, se lee al enviar (spec §3.1). `enviar` y
`probar_conexion` son BLOQUEANTES (smtplib): se llaman siempre dentro de
asyncio.to_thread.
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from crypto_secrets import decrypt_db_secret, encrypt_secret
from db.connection import get_pool

CLAVES = (
    "smtp.host", "smtp.port", "smtp.encryption", "smtp.user",
    "smtp.password", "smtp.from_name", "smtp.from_email",
)
CLAVE_SECRETA = "smtp.password"
MASCARA = "••••••••"
CIFRADOS = ("tls", "ssl", "none")
TIMEOUT_S = 10


class SmtpNoDisponible(Exception):
    codigo = "smtp_no_disponible"


class SmtpNoConfigurado(SmtpNoDisponible):
    codigo = "smtp_no_configurado"


class SmtpConfigCorrupta(SmtpNoDisponible):
    codigo = "smtp_config_corrupta"

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class SmtpExigeContrasena(Exception):
    codigo = "smtp_exige_contrasena"

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class SmtpPasoFallido(Exception):
    def __init__(self, codigo: str, servidor: str = ""):
        super().__init__(f"{codigo}: {servidor}")
        self.codigo = codigo
        self.servidor = servidor


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    encryption: str
    user: str
    password: str
    from_name: str
    from_email: str


# --------------------------------------------------------------- lectura

def motivo_de_corrupcion(filas: dict[str, str]) -> str | None:
    """None si el dominio está sano o si nunca se configuró (sin smtp.host:
    ausencia legítima). Si no, el motivo, como código estable."""
    if not filas.get("smtp.host"):
        return None
    for clave in CLAVES:
        if clave not in filas:
            return f"clave_ausente:{clave}"
    if not decrypt_db_secret(filas[CLAVE_SECRETA]):
        return "password_ilegible"
    if not filas["smtp.port"].isdigit():
        return "valor_invalido:smtp.port"
    if filas["smtp.encryption"] not in CIFRADOS:
        return "valor_invalido:smtp.encryption"
    return None


def interpretar(filas: dict[str, str]) -> SmtpSettings:
    if not filas.get("smtp.host"):
        raise SmtpNoConfigurado("smtp.host ausente: el correo saliente nunca se configuró")
    motivo = motivo_de_corrupcion(filas)
    if motivo is not None:
        raise SmtpConfigCorrupta(motivo)
    return SmtpSettings(
        host=filas["smtp.host"],
        port=int(filas["smtp.port"]),
        encryption=filas["smtp.encryption"],
        user=filas["smtp.user"],
        password=decrypt_db_secret(filas[CLAVE_SECRETA]),
        from_name=filas["smtp.from_name"],
        from_email=filas["smtp.from_email"],
    )


def estado_para_pantalla(filas: dict[str, str]) -> dict:
    """Lo que ve la pantalla. La contraseña NUNCA sale: máscara si hay una
    guardada que descifra, "" si no. Con el estado corrupto se nombra la
    causa: un formulario vacío se leería como "sin configurar"."""
    motivo = motivo_de_corrupcion(filas)
    puerto = filas.get("smtp.port", "")
    cifrado = filas.get("smtp.encryption", "")
    return {
        "host": filas.get("smtp.host", ""),
        "port": int(puerto) if puerto.isdigit() else 587,
        "encryption": cifrado if cifrado in CIFRADOS else "tls",
        "user": filas.get("smtp.user", ""),
        "password": MASCARA if motivo is None and filas.get(CLAVE_SECRETA) else "",
        "from_name": filas.get("smtp.from_name", ""),
        "from_email": filas.get("smtp.from_email", ""),
        "configurado": bool(filas.get("smtp.host")),
        "corrupta": motivo is not None,
        "motivo": motivo,
    }


def filas_a_guardar(filas_actuales: dict[str, str], datos: dict) -> dict[str, str]:
    """Filas a escribir. La contraseña solo se reemplaza si llega una nueva
    distinta de la máscara. Con el estado corrupto se PUEDE reconfigurar
    (bloquearlo dejaría como salida la cirugía en la base), pero exigiendo
    volver a escribir la contraseña -- igual que AteneaERP."""
    nueva = datos.get("password") or ""
    trae_nueva = nueva not in ("", MASCARA)
    motivo = motivo_de_corrupcion(filas_actuales)
    if not trae_nueva:
        if motivo is not None:
            raise SmtpExigeContrasena(motivo)
        if not filas_actuales.get(CLAVE_SECRETA):
            raise SmtpExigeContrasena("sin_contrasena")
    filas = {
        "smtp.host": datos["host"],
        "smtp.port": str(int(datos["port"])),
        "smtp.encryption": datos["encryption"],
        "smtp.user": datos["user"],
        "smtp.from_name": datos["from_name"],
        "smtp.from_email": datos["from_email"],
    }
    if trae_nueva:
        filas[CLAVE_SECRETA] = encrypt_secret(nueva)
    return filas


async def leer_filas() -> dict[str, str]:
    marcadores = ", ".join(["%s"] * len(CLAVES))
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # config_key es PRIMARY KEY: el IN va por el índice.
            await cur.execute(
                f"SELECT config_key, config_value FROM axioma_config WHERE config_key IN ({marcadores})",
                CLAVES,
            )
            return {clave: valor for clave, valor in await cur.fetchall()}


async def guardar_filas(filas: dict[str, str]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for clave, valor in filas.items():
                await cur.execute(
                    "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)",
                    (clave, valor),
                )


async def cargar_settings() -> SmtpSettings:
    return interpretar(await leer_filas())


# ----------------------------------------------------------------- envío

def construir_mensaje(settings: SmtpSettings, destinatario: str, asunto: str,
                      texto: str, html: str) -> EmailMessage:
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = formataddr((settings.from_name, settings.from_email))
    mensaje["To"] = destinatario
    mensaje["Date"] = formatdate(localtime=True)
    mensaje["Message-ID"] = make_msgid(domain=settings.from_email.rsplit("@", 1)[-1])
    mensaje.set_content(texto)
    mensaje.add_alternative(html, subtype="html")
    return mensaje


def _contexto_tls() -> ssl.SSLContext:
    # CERT_REQUIRED + check_hostname: verifica cadena y nombre. Ver docstring.
    return ssl.create_default_context()


def _abrir(host: str, port: int, encryption: str, contexto: ssl.SSLContext):
    if encryption == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_S, context=contexto)
    return smtplib.SMTP(host, port, timeout=TIMEOUT_S)


def enviar(settings: SmtpSettings, mensaje: EmailMessage) -> None:
    """Bloqueante. Lanza OSError / smtplib.SMTPException si falla: quien
    llama decide qué hacer (el reset público lo registra; el admin lo ve)."""
    contexto = _contexto_tls()
    with _abrir(settings.host, settings.port, settings.encryption, contexto) as servidor:
        servidor.ehlo()
        if settings.encryption == "tls":
            servidor.starttls(context=contexto)
            servidor.ehlo()
        if settings.user:
            servidor.login(settings.user, settings.password)
        servidor.send_message(mensaje)


def _texto(exc: smtplib.SMTPResponseException) -> str:
    error = exc.smtp_error
    if isinstance(error, bytes):
        error = error.decode(errors="replace")
    return f"{exc.smtp_code} {error}".strip()


def _cerrar(servidor) -> None:
    try:
        servidor.quit()
    except (OSError, smtplib.SMTPException):  # fail-soft: el QUIT es de cortesía; la prueba ya dio su veredicto y el socket se cierra igual
        servidor.close()


def probar_conexion(host: str, port: int, encryption: str, user: str, password: str) -> None:
    """Saludo, EHLO, STARTTLS y AUTH, sin enviar nada. Lanza SmtpPasoFallido
    con el código del paso que falló y lo que respondió el servidor -- el
    mismo recorrido que SmtpController::testConnection de AteneaERP, con el
    certificado verificado."""
    contexto = _contexto_tls()
    try:
        servidor = _abrir(host, port, encryption, contexto)
    except smtplib.SMTPConnectError as exc:
        raise SmtpPasoFallido("smtp_saludo_inesperado", _texto(exc)) from exc
    except ssl.SSLError as exc:
        raise SmtpPasoFallido("smtp_tls_fallido", str(exc)) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise SmtpPasoFallido("smtp_conexion_fallida", str(exc)) from exc
    try:
        codigo, respuesta = servidor.ehlo()
        if codigo != 250:
            raise SmtpPasoFallido("smtp_ehlo_fallido", f"{codigo} {respuesta.decode(errors='replace')}")
        if encryption == "tls":
            if not servidor.has_extn("starttls"):
                raise SmtpPasoFallido("smtp_starttls_no_disponible")
            try:
                servidor.starttls(context=contexto)
            except ssl.SSLError as exc:
                raise SmtpPasoFallido("smtp_tls_fallido", str(exc)) from exc
            except smtplib.SMTPResponseException as exc:
                raise SmtpPasoFallido("smtp_starttls_no_disponible", _texto(exc)) from exc
            servidor.ehlo()
        try:
            servidor.login(user, password)
        except smtplib.SMTPAuthenticationError as exc:
            raise SmtpPasoFallido("smtp_auth_rechazada", _texto(exc)) from exc
        except smtplib.SMTPNotSupportedError as exc:
            raise SmtpPasoFallido("smtp_auth_no_soportada", str(exc)) from exc
    except SmtpPasoFallido:
        raise
    except (OSError, smtplib.SMTPException) as exc:
        raise SmtpPasoFallido("smtp_conexion_fallida", str(exc)) from exc
    finally:
        _cerrar(servidor)
```

- [ ] **Step 4: Correr los tests y verlos pasar**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_smtp_config.py -q`
Expected: `19 passed`.

Run también: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_no_fail_open_except.py -q`
Expected: `1 passed` (el `except` de `_cerrar` lleva su marca).

- [ ] **Step 5: Subir los pisos con el número medido**

Medir el job sin DB: `cd /home/fruiz/jax-platform/backend && JAX_CI_NO_DB=1 .venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -3`. Esperado, si `master` no cambió: `240 passed` (221 + 19). En `.github/workflows/policy.yml`, en el bloque de comentarios que termina en `JAX_CI_MIN_PASSED: "221"`, agregar antes de esa línea:

```yaml
      # Subido de 221 a 240 (2026-09-12, administración de usuarios etapa 1,
      # SMTP desde Admin): los 19 tests de tests/test_smtp_config.py son puros
      # (dominio SMTP con smtplib reemplazado por dobles) y corren sin DB.
      # Medido con JAX_CI_NO_DB=1: <passed> / <skipped>.
```

y cambiar la línea a `JAX_CI_MIN_PASSED: "<medido>"`. En el bloque de `PISO_PASSED`, agregar el comentario equivalente (`Subido de 427 a <medido> ... 19 puros de test_smtp_config.py`) y el valor medido con `.venv/bin/python -m pytest -q` completo (esperado 446). Si el número medido no es el esperado, NO se ajusta el esperado: se investiga la diferencia.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/validacion.py backend/smtp_config.py backend/tests/test_smtp_config.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(smtp): dominio de correo saliente configurable, copia de AteneaERP con TLS verificado"
```

---

### Task 2: Endpoints de Admin para SMTP y exclusión de `smtp.*` en `/api/admin/config`

**Files:**
- Create: `backend/api/admin/smtp.py`
- Modify: `backend/api/admin/__init__.py`, `backend/main.py:67-78` (import) y `backend/main.py:121-143` (include_router)
- Modify: `backend/api/admin/config_admin.py`
- Test: `backend/tests/test_smtp_endpoints.py`
- Modify: `.github/workflows/policy.yml` (`PISO_PASSED`)

**Interfaces:**
- Consumes: todo `smtp_config` (Task 1); `validacion.email_valido`; `auth.middleware.require_superadmin`; `auth.rate_limit.SlidingWindowLimiter(max_hits, window_s, max_keys=20_000)` y `parse_rate(spec) -> (int, float)`.
- Produces:
  - `GET /api/admin/smtp` → `smtp_config.estado_para_pantalla(...)` (claves `host, port, encryption, user, password, from_name, from_email, configurado, corrupta, motivo`).
  - `PUT /api/admin/smtp` (body `SmtpUpdate`) → `{"ok": true}`; 400 `smtp_from_email_invalido`; 422 `smtp_exige_contrasena`; 503 `smtp_sin_clave_de_cifrado`.
  - `POST /api/admin/smtp/test-connection` (body `SmtpConexion`) → `{"ok": true}`; 422 `smtp_sin_contrasena` / `smtp_password_ilegible` / `{"code": <paso>, "server": <texto>}`.
  - `POST /api/admin/smtp/test` → `{"ok": true, "to": <email del superadmin>}`; 503 `smtp_no_configurado`/`smtp_config_corrupta`; 502 `{"code": "smtp_envio_fallido", "server": ...}`; 429 `smtp_demasiadas_pruebas` con `Retry-After`.
  - `api.admin.smtp.SMTP_TEST_LIMITER` (los tests lo reemplazan).
  - `GET /api/admin/config` ya no lista `smtp.*`; `PUT /api/admin/config` con una clave `smtp.*` → 400 `config_clave_reservada` y no escribe nada.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `backend/tests/test_smtp_endpoints.py`:

```python
"""Endpoints de Admin para el correo saliente (2026-09-12, etapa 1).

Contra jax_memory_test (conftest.py). Cada test deja axioma_config sin
filas smtp.* al terminar. Ningún test sale a la red: smtplib queda
reemplazado por un doble que explota, y cada test que necesita "enviar" o
"probar" reemplaza la función de smtp_config que corresponde.
"""
import uuid

import pytest
from cryptography.fernet import Fernet

import smtp_config
from auth.jwt import create_access_token
from auth.rate_limit import SlidingWindowLimiter

CLAVE = "clave-smtp-de-prueba"


def _admin():
    # user_id=1 es el superadmin sembrado en jax_memory_test; nadie lo modifica.
    return {"Authorization": f"Bearer {create_access_token('1', '1', 'superadmin')}"}


async def _sql(q, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(q, args)
            return await cur.fetchall() if fetch else cur.lastrowid


async def _borrar_smtp():
    await _sql("DELETE FROM axioma_config WHERE config_key LIKE %s", ("smtp.%",))


async def _filas_smtp():
    return dict(await _sql("SELECT config_key, config_value FROM axioma_config WHERE config_key LIKE %s",
                           ("smtp.%",), True))


@pytest.fixture(autouse=True)
def entorno(client, monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())

    class _SinRed:
        def __init__(self, *a, **k):
            raise AssertionError("un test de SMTP intentó abrir una conexión real")

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _SinRed)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SinRed)
    import api.admin.smtp as smtp_mod
    monkeypatch.setattr(smtp_mod, "SMTP_TEST_LIMITER", SlidingWindowLimiter(100, 300, 1000))
    client.portal.call(_borrar_smtp)
    yield
    client.portal.call(_borrar_smtp)


def _config(**cambios):
    datos = {"host": "mail.example.test", "port": 587, "encryption": "tls",
             "user": "no-reply@example.test", "password": CLAVE,
             "from_name": "Axioma", "from_email": "no-reply@example.test"}
    datos.update(cambios)
    return datos


def _guardar(client, **cambios):
    return client.put("/api/admin/smtp", json=_config(**cambios), headers=_admin())


# ----------------------------------------------------------- GET / PUT

def test_sin_configurar(client):
    r = client.get("/api/admin/smtp", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json()["configurado"] is False and r.json()["password"] == ""


def test_guardar_cifra_la_contrasena_y_el_get_solo_muestra_la_mascara(client):
    assert _guardar(client).status_code == 200
    filas = client.portal.call(_filas_smtp)
    assert filas["smtp.password"] != CLAVE
    assert smtp_config.decrypt_db_secret(filas["smtp.password"]) == CLAVE
    cuerpo = client.get("/api/admin/smtp", headers=_admin()).json()
    assert cuerpo["password"] == smtp_config.MASCARA
    assert CLAVE not in cuerpo.values() and filas["smtp.password"] not in cuerpo.values()


def test_guardar_con_la_mascara_conserva_la_contrasena(client):
    assert _guardar(client).status_code == 200
    antes = client.portal.call(_filas_smtp)["smtp.password"]
    assert _guardar(client, password=smtp_config.MASCARA, host="otro.example.test").status_code == 200
    despues = client.portal.call(_filas_smtp)
    assert despues["smtp.password"] == antes and despues["smtp.host"] == "otro.example.test"


def test_la_primera_vez_exige_contrasena(client):
    r = _guardar(client, password="")
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_exige_contrasena")
    assert client.portal.call(_filas_smtp) == {}


def test_estado_corrupto_se_nombra_y_reconfigurar_exige_contrasena(client):
    assert _guardar(client).status_code == 200
    ajena = Fernet(Fernet.generate_key()).encrypt(b"x").decode()  # como si rotara FERNET_KEY
    client.portal.call(_sql, "UPDATE axioma_config SET config_value = %s WHERE config_key = 'smtp.password'", (ajena,))
    cuerpo = client.get("/api/admin/smtp", headers=_admin()).json()
    assert (cuerpo["corrupta"], cuerpo["motivo"], cuerpo["password"]) == (True, "password_ilegible", "")
    r = _guardar(client, password=smtp_config.MASCARA)
    assert (r.status_code, r.json()["detail"]) == (422, "smtp_exige_contrasena")
    assert _guardar(client, password="reescrita").status_code == 200
    assert client.get("/api/admin/smtp", headers=_admin()).json()["corrupta"] is False


def test_remitente_invalido(client):
    r = _guardar(client, from_email="no-es-un-correo")
    assert (r.status_code, r.json()["detail"]) == (400, "smtp_from_email_invalido")


async def _crear_operador():
    email = f"test-smtp-op-{uuid.uuid4().hex[:10]}@example.invalid"
    user_id = await _sql(
        "INSERT INTO jax_users (tenant_id, email, password_hash, role, status) "
        "VALUES (1, %s, 'sin-login', 'operator', 'active')", (email,))
    return user_id


async def _borrar_usuario(user_id):
    await _sql("DELETE FROM jax_users WHERE user_id = %s", (user_id,))


def test_solo_superadmin(client):
    # Operador REAL (no un token con rol inventado): desde la etapa 2 el rol
    # sale de la base, y este test tiene que seguir diciendo lo mismo.
    user_id = client.portal.call(_crear_operador)
    try:
        token = create_access_token(str(user_id), "1", "operator")
        r = client.get("/api/admin/smtp", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
    finally:
        client.portal.call(_borrar_usuario, user_id)


# ------------------------------------------------------ probar conexión

def test_probar_conexion_usa_la_contrasena_guardada_si_llega_la_mascara(client, monkeypatch):
    assert _guardar(client).status_code == 200
    vistos = []
    monkeypatch.setattr(smtp_config, "probar_conexion", lambda *a: vistos.append(a))
    datos = {k: v for k, v in _config(password=smtp_config.MASCARA).items() if k not in ("from_name", "from_email")}
    r = client.post("/api/admin/smtp/test-connection", json=datos, headers=_admin())
    assert r.status_code == 200, r.text
    assert vistos == [("mail.example.test", 587, "tls", "no-reply@example.test", CLAVE)]


def test_probar_conexion_devuelve_el_paso_exacto(client, monkeypatch):
    def falla(*a):
        raise smtp_config.SmtpPasoFallido("smtp_auth_rechazada", "535 5.7.8 Authentication failed")

    monkeypatch.setattr(smtp_config, "probar_conexion", falla)
    datos = {k: v for k, v in _config().items() if k not in ("from_name", "from_email")}
    r = client.post("/api/admin/smtp/test-connection", json=datos, headers=_admin())
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "smtp_auth_rechazada", "server": "535 5.7.8 Authentication failed"}


# ------------------------------------------------------- correo de prueba

def test_prueba_sin_configurar_es_503(client):
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_no_configurado")


def test_prueba_con_config_corrupta_es_503(client):
    assert _guardar(client).status_code == 200
    client.portal.call(_sql, "DELETE FROM axioma_config WHERE config_key = 'smtp.from_name'")
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (503, "smtp_config_corrupta")


def test_prueba_va_al_correo_del_superadmin(client, monkeypatch):
    assert _guardar(client).status_code == 200
    enviados = []
    monkeypatch.setattr(smtp_config, "enviar", lambda s, msg: enviados.append((s.host, msg["To"])))
    (fila,) = client.portal.call(_sql, "SELECT email FROM jax_users WHERE user_id = 1", (), True)
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "to": fila[0]}
    assert enviados == [("mail.example.test", fila[0])]


def test_prueba_tiene_limite_de_intentos(client, monkeypatch):
    import api.admin.smtp as smtp_mod
    monkeypatch.setattr(smtp_mod, "SMTP_TEST_LIMITER", SlidingWindowLimiter(2, 300, 1000))
    monkeypatch.setattr(smtp_config, "enviar", lambda s, msg: None)
    assert _guardar(client).status_code == 200
    for _ in range(2):
        assert client.post("/api/admin/smtp/test", headers=_admin()).status_code == 200
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (429, "smtp_demasiadas_pruebas")
    assert int(r.headers["Retry-After"]) >= 1


def test_prueba_que_el_servidor_rechaza_es_502_con_su_respuesta(client, monkeypatch):
    import smtplib

    def rechaza(s, msg):
        raise smtplib.SMTPRecipientsRefused({"x@y": (550, b"5.1.1 User unknown")})

    monkeypatch.setattr(smtp_config, "enviar", rechaza)
    assert _guardar(client).status_code == 200
    r = client.post("/api/admin/smtp/test", headers=_admin())
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "smtp_envio_fallido"
    assert "User unknown" in r.json()["detail"]["server"]


# -------------------------------------- /api/admin/config no ve smtp.*

def test_config_generico_no_lista_smtp(client):
    assert _guardar(client).status_code == 200
    claves = [i["key"] for i in client.get("/api/admin/config", headers=_admin()).json()["config"]]
    assert claves and not [c for c in claves if c.startswith("smtp.")]


def test_config_generico_rechaza_escribir_smtp(client):
    assert _guardar(client).status_code == 200
    antes = client.portal.call(_filas_smtp)
    r = client.put("/api/admin/config", json=[{"key": "system_name", "value": "Axioma"},
                                               {"key": "smtp.password", "value": "en-claro"}],
                   headers=_admin())
    assert (r.status_code, r.json()["detail"]) == (400, "config_clave_reservada")
    assert client.portal.call(_filas_smtp) == antes
```

- [ ] **Step 2: Correr los tests y verlos fallar**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_smtp_endpoints.py -q`
Expected: los de `/api/admin/smtp` fallan porque la ruta no existe: `404` en los GET y `404`/`405` en PUT/POST (si `frontend/dist` existe, `main.py` monta el estático en `/` y un método que no es GET sobre una ruta desconocida responde `405`). `test_config_generico_no_lista_smtp` falla porque aparecen claves `smtp.*` y `test_config_generico_rechaza_escribir_smtp` porque el PUT responde `200`.

- [ ] **Step 3: Implementación mínima**

Crear `backend/api/admin/smtp.py`:

```python
"""Pantalla "Correo (SMTP)" de Admin (2026-09-12, etapa 1). Copia de
SmtpController de AteneaERP (show/update/testConnection/test) sobre
smtp_config. Solo superadmin."""
import asyncio
import logging
import math
import os
import smtplib
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import smtp_config
from auth.middleware import require_superadmin
from auth.models import AuthUser
from auth.rate_limit import SlidingWindowLimiter, parse_rate
from db.connection import get_pool
from validacion import EMAIL_MAX, email_valido

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")

# El correo de prueba sale de verdad: sin límite, un superadmin (o un token
# robado) podría usar el servidor para inundar un buzón. 5 cada 5 minutos por
# usuario alcanza para probar, corregir y volver a probar.
SMTP_TEST_LIMITER = SlidingWindowLimiter(*parse_rate(os.getenv("JAX_SMTP_TEST_RATE", "5/300")))

ASUNTO_PRUEBA = "Axioma — Prueba de SMTP"
TEXTO_PRUEBA = (
    "Este es un correo de prueba enviado desde Axioma para verificar que la "
    "configuración SMTP funciona.\n\nSi lo recibiste, el correo saliente está bien configurado."
)
HTML_PRUEBA = (
    "<p>Este es un <strong>correo de prueba</strong> enviado desde <strong>Axioma</strong> "
    "para verificar que la configuración SMTP funciona.</p>"
    "<p>Si lo recibiste, el correo saliente está bien configurado.</p>"
)


class SmtpConexion(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    encryption: Literal["tls", "ssl", "none"]
    user: str = Field(min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, max_length=255)


class SmtpUpdate(SmtpConexion):
    from_name: str = Field(min_length=1, max_length=255)
    from_email: str = Field(min_length=3, max_length=EMAIL_MAX)


@router.get("/smtp")
async def ver_smtp(user: AuthUser = Depends(require_superadmin)):
    return smtp_config.estado_para_pantalla(await smtp_config.leer_filas())


@router.put("/smtp")
async def guardar_smtp(req: SmtpUpdate, user: AuthUser = Depends(require_superadmin)):
    if not email_valido(req.from_email):
        raise HTTPException(status_code=400, detail="smtp_from_email_invalido")
    actuales = await smtp_config.leer_filas()
    motivo_previo = smtp_config.motivo_de_corrupcion(actuales)
    try:
        filas = smtp_config.filas_a_guardar(actuales, req.model_dump())
    except smtp_config.SmtpExigeContrasena as exc:
        # 422 y no 500: quien opera lo resuelve volviendo a escribir la contraseña.
        raise HTTPException(status_code=422, detail=exc.codigo) from exc
    except RuntimeError as exc:
        # encrypt_secret sin FERNET_KEY: nunca se guarda en claro.
        raise HTTPException(status_code=503, detail="smtp_sin_clave_de_cifrado") from exc
    await smtp_config.guardar_filas(filas)
    if motivo_previo is not None:
        logger.warning("SMTP reconfigurado sobre un estado corrupto (motivo anterior: %s) por user_id=%s",
                       motivo_previo, user.user_id)
    return {"ok": True}


@router.post("/smtp/test-connection")
async def probar_conexion_smtp(req: SmtpConexion, user: AuthUser = Depends(require_superadmin)):
    password = req.password or ""
    if password in ("", smtp_config.MASCARA):
        guardada = (await smtp_config.leer_filas()).get(smtp_config.CLAVE_SECRETA, "")
        password = smtp_config.decrypt_db_secret(guardada) if guardada else ""
        if guardada and not password:
            raise HTTPException(status_code=422, detail="smtp_password_ilegible")
        if not password:
            raise HTTPException(status_code=422, detail="smtp_sin_contrasena")
    try:
        await asyncio.to_thread(smtp_config.probar_conexion, req.host, req.port, req.encryption, req.user, password)
    except smtp_config.SmtpPasoFallido as exc:
        raise HTTPException(status_code=422, detail={"code": exc.codigo, "server": exc.servidor}) from exc
    return {"ok": True}


async def _email_de(user_id: int) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email FROM jax_users WHERE user_id = %s", (user_id,))
            fila = await cur.fetchone()
    if fila is None:
        raise HTTPException(status_code=401, detail="sesion_invalida")
    return fila[0]


@router.post("/smtp/test")
async def enviar_prueba_smtp(user: AuthUser = Depends(require_superadmin)):
    retry = SMTP_TEST_LIMITER.hit(str(user.user_id))
    if retry is not None:
        raise HTTPException(status_code=429, detail="smtp_demasiadas_pruebas",
                            headers={"Retry-After": str(max(1, math.ceil(retry)))})
    try:
        settings = await smtp_config.cargar_settings()
    except smtp_config.SmtpNoDisponible as exc:
        raise HTTPException(status_code=503, detail=exc.codigo) from exc
    destinatario = await _email_de(int(user.user_id))
    mensaje = smtp_config.construir_mensaje(settings, destinatario, ASUNTO_PRUEBA, TEXTO_PRUEBA, HTML_PRUEBA)
    try:
        # La prueba se ESPERA a propósito (quien la pide quiere el veredicto),
        # pero en un hilo: smtplib no toca el event loop.
        await asyncio.to_thread(smtp_config.enviar, settings, mensaje)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("Correo de prueba SMTP a %s falló: %s", destinatario, exc)
        raise HTTPException(status_code=502, detail={"code": "smtp_envio_fallido", "server": str(exc)}) from exc
    logger.info("Correo de prueba SMTP enviado a %s por user_id=%s", destinatario, user.user_id)
    return {"ok": True, "to": destinatario}
```

En `backend/api/admin/__init__.py`, después de `from .motors import router as admin_motors_router` agregar `from .smtp import router as smtp_router`, y en `__all__`, después de `"admin_motors_router",`, agregar `"smtp_router",`.

En `backend/main.py`, dentro del `from api.admin import (...)` agregar `smtp_router,` después de `admin_motors_router,`, y después de `app.include_router(admin_motors_router)` agregar `app.include_router(smtp_router)`.

En `backend/api/admin/config_admin.py`, reemplazar las funciones `get_config` y `update_config` por:

```python
# smtp.* tiene su propia pantalla (api/admin/smtp.py): la contraseña va
# cifrada. Por acá el PUT la guardaría en claro y el GET devolvería el texto
# cifrado (spec §3.1). Se excluye por prefijo, no por lista.
PREFIJO_RESERVADO = "smtp."


@router.get("/config")
async def get_config(user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_defaults(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT config_key, config_value FROM axioma_config "
                "WHERE config_key NOT LIKE %s ORDER BY config_key",
                (PREFIJO_RESERVADO + "%",),
            )
            rows = await cur.fetchall()
    return {"config": [{"key": r[0], "value": r[1]} for r in rows]}


class ConfigItem(BaseModel):
    key: str
    value: str


@router.put("/config")
async def update_config(items: List[ConfigItem], user: AuthUser = Depends(require_superadmin)):
    # Antes de escribir NADA: un lote con una clave reservada no se aplica a medias.
    if any(item.key.startswith(PREFIJO_RESERVADO) for item in items):
        raise HTTPException(status_code=400, detail="config_clave_reservada")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for item in items:
                await cur.execute(
                    "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE config_value = %s",
                    (item.key, item.value, item.value),
                )
    return {"ok": True}
```

y cambiar la primera línea del archivo a `from fastapi import APIRouter, Depends, HTTPException`.

- [ ] **Step 4: Correr los tests y verlos pasar**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_smtp_endpoints.py tests/test_smtp_config.py -q`
Expected: `35 passed` (16 + 19).

- [ ] **Step 5: Subir `PISO_PASSED` con el número medido**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest -q 2>&1 | tail -3`. Esperado: `462 passed, 1 skipped` (446 + 16). Agregar el comentario (`Subido de <anterior> a <medido> ... tests/test_smtp_endpoints.py agrega 16 con client`) y el valor medido en `PISO_PASSED`. El job sin DB no cambia: los 16 piden `client`.

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/admin/smtp.py backend/api/admin/__init__.py backend/main.py backend/api/admin/config_admin.py backend/tests/test_smtp_endpoints.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(smtp): endpoints de Admin (ver, guardar, probar conexión, correo de prueba); /config ya no ve smtp.*"
```

---

### Task 3: La recuperación de contraseña usa la configuración de la base

**Files:**
- Modify: `backend/api/auth.py:1-20` (imports), `backend/api/auth.py:199-269` (`_procesar_recuperacion`, `_send_reset_email`)
- Modify: `backend/tests/test_login_residuos.py:160-183` (2 tests) y agregar 1 test
- Modify: `.github/workflows/policy.yml` (`PISO_PASSED`)

**Interfaces:**
- Consumes: `smtp_config.cargar_settings()`, `smtp_config.SmtpNoDisponible`, `smtp_config.construir_mensaje(...)`, `smtp_config.enviar(...)`.
- Produces: `api.auth._send_reset_email(settings, to_email, reset_link) -> None`, bloqueante, **lanza** si falla (la etapa 4 lo llama desde el reset por admin y muestra el error). `_procesar_recuperacion(email, client_ip)` mantiene su firma y sigue siendo fail-soft.

- [ ] **Step 1: Escribir/adaptar los tests que fallan**

En `backend/tests/test_login_residuos.py`, reemplazar el test `test_recuperacion_de_cuenta_real_crea_token_y_manda_el_correo` y el test `test_recuperacion_de_email_inexistente_no_manda_nada` completos por:

```python
def _smtp_de_prueba():
    import smtp_config
    return smtp_config.SmtpSettings(
        host="mail.example.test", port=587, encryption="tls", user="u", password="p",
        from_name="Axioma", from_email="no-reply@example.test")


def test_recuperacion_de_cuenta_real_crea_token_y_manda_el_correo(client, monkeypatch):
    import smtp_config
    from api import auth as auth_mod
    enviados = []

    async def configurado():
        return _smtp_de_prueba()

    monkeypatch.setattr(smtp_config, "cargar_settings", configurado)
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append((s.host, to, link)))
    email = _email()
    user_id = client.portal.call(_crear_con_hash, email, bcrypt.hashpw(b"clave-x", bcrypt.gensalt(rounds=4)).decode())
    try:
        client.portal.call(auth_mod._procesar_recuperacion, email, "203.0.113.5")
        filas = client.portal.call(
            _sql, "SELECT token, ip_address FROM password_reset_tokens WHERE user_id = %s AND used = FALSE",
            (user_id,), True)
        assert len(filas) == 1 and filas[0][1] == "203.0.113.5", filas
        assert len(enviados) == 1 and enviados[0][:2] == ("mail.example.test", email)
        assert filas[0][0] in enviados[0][2], "el enlace no lleva el token guardado"
    finally:
        client.portal.call(_borrar, email)


def test_recuperacion_de_email_inexistente_no_manda_nada(client, monkeypatch):
    from api import auth as auth_mod
    enviados = []
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append(to))
    client.portal.call(auth_mod._procesar_recuperacion, _email(), "203.0.113.5")
    assert enviados == []


def test_recuperacion_sin_smtp_configurado_no_crea_token_ni_envia(client, monkeypatch):
    # El forgot-password público sigue respondiendo neutro (spec §3.1); en
    # segundo plano, sin correo configurado, no se crea un token que nadie
    # va a recibir, y queda en el log por qué.
    import smtp_config
    from api import auth as auth_mod
    enviados = []

    async def sin_configurar():
        raise smtp_config.SmtpNoConfigurado("nunca configurado")

    monkeypatch.setattr(smtp_config, "cargar_settings", sin_configurar)
    monkeypatch.setattr(auth_mod, "_send_reset_email", lambda s, to, link: enviados.append(to))
    email = _email()
    user_id = client.portal.call(_crear_con_hash, email, bcrypt.hashpw(b"clave-x", bcrypt.gensalt(rounds=4)).decode())
    try:
        client.portal.call(auth_mod._procesar_recuperacion, email, "203.0.113.5")
        filas = client.portal.call(_sql, "SELECT id FROM password_reset_tokens WHERE user_id = %s", (user_id,), True)
        assert filas == () or list(filas) == []
        assert enviados == []
    finally:
        client.portal.call(_borrar, email)
```

- [ ] **Step 2: Correr y ver el rojo**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_login_residuos.py -q -k recuperacion`
Expected: `test_recuperacion_de_cuenta_real_crea_token_y_manda_el_correo` falla con `TypeError: <lambda>() missing 1 required positional argument: 'link'` (hoy se llama con dos argumentos), que `_procesar_recuperacion` registra en el log; la aserción `len(enviados) == 1` es la que falla. `test_recuperacion_sin_smtp_configurado_no_crea_token_ni_envia` falla porque se crea el token.

- [ ] **Step 3: Implementación mínima**

En `backend/api/auth.py`:

1. Borrar las líneas `import smtplib`, `from email.mime.text import MIMEText` y `from email.mime.multipart import MIMEMultipart`. Después de `from jax_engine.background import add_safe_task` agregar `import smtp_config`.

2. En `_procesar_recuperacion`, justo después de `user_id, email_guardado = row`, agregar:

```python
        # Sin correo configurado (o con la configuración rota) no se crea un
        # token que nadie va a recibir: queda en el log con el código. La
        # respuesta pública ya salió, neutra, antes de esto.
        try:
            settings = await smtp_config.cargar_settings()
        except smtp_config.SmtpNoDisponible as exc:
            logger.error("Recuperación de contraseña: correo deshabilitado (%s); no se creó el token", exc.codigo)
            return
```

y cambiar la línea `await asyncio.to_thread(_send_reset_email, email_guardado, reset_link)` por `await asyncio.to_thread(_send_reset_email, settings, email_guardado, reset_link)`.

3. Reemplazar la función `_send_reset_email` completa por:

```python
ASUNTO_RECUPERACION = "Recuperación de contraseña — Axioma"


def _send_reset_email(settings: smtp_config.SmtpSettings, to_email: str, reset_link: str) -> None:
    """Bloqueante (smtplib): se llama dentro de asyncio.to_thread. LANZA si el
    servidor falla -- el reset público lo registra en _procesar_recuperacion;
    el reset por admin (etapa 4) se lo muestra al admin. Antes leía SMTP_* del
    entorno, que nunca estuvieron definidas: "¿Olvidaste tu contraseña?" no
    envió un solo correo (spec §1, hallazgo 8)."""
    texto = (
        f"Para restablecer tu contraseña, accede al siguiente enlace:\n\n{reset_link}\n\n"
        "Este enlace expira en 1 hora."
    )
    html = (
        "<p>Para restablecer tu contraseña, haz clic en el siguiente enlace:</p>"
        f'<p><a href="{reset_link}">{reset_link}</a></p>'
        "<p>Este enlace expira en 1 hora. Si no solicitaste este cambio, ignora este correo.</p>"
    )
    mensaje = smtp_config.construir_mensaje(settings, to_email, ASUNTO_RECUPERACION, texto, html)
    smtp_config.enviar(settings, mensaje)
```

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest tests/test_login_residuos.py tests/test_smtp_endpoints.py tests/test_smtp_config.py tests/test_no_fail_open_except.py tests/test_policy_background_tasks.py -q`
Expected: todo en verde (13 de residuos, 16, 19 y los de política).

Verificar además que no queda ninguna lectura de `SMTP_` del entorno: `grep -rn "SMTP_HOST\|SMTP_PORT\|SMTP_USER\|SMTP_PASSWORD\|SMTP_FROM" /home/fruiz/jax-platform/backend --include=*.py | grep -v tests/` → sin salida.

- [ ] **Step 5: Piso**

Medir con la suite completa (esperado `463 passed, 1 skipped`, uno más que la tarea anterior) y actualizar `PISO_PASSED` con su comentario (`+1: test_recuperacion_sin_smtp_configurado_no_crea_token_ni_envia`).

- [ ] **Step 6: Commit**

```bash
git -C /home/fruiz/jax-platform add backend/api/auth.py backend/tests/test_login_residuos.py .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "fix(auth): la recuperación de contraseña envía con la configuración SMTP de la base"
```

---

### Task 4: Pantalla "Correo (SMTP)" en Admin

**Files:**
- Create: `frontend/src/pages/admin/AdminSmtp.jsx`
- Test: `frontend/src/pages/admin/AdminSmtp.test.jsx`
- Modify: `frontend/src/pages/Admin.jsx` (import + ruta), `frontend/src/components/admin/AdminSidebar.jsx` (NAV_ITEMS)
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`
- Modify: `.github/workflows/policy.yml` (piso vitest)

**Interfaces:**
- Consumes: los cuatro endpoints de Task 2 (vía `api` de `frontend/src/api/client.js`, `baseURL: '/api'`), `PasswordInput` (`frontend/src/components/PasswordInput.jsx`, pasa `className` al `<input>`), `useI18n()` → `{ lang, setLang, t }`.
- Produces: `t.smtpErrors` (código → texto), `t.smtpErrorGeneric`, ruta `/admin/smtp`.

- [ ] **Step 1: Test que falla**

Crear `frontend/src/pages/admin/AdminSmtp.test.jsx`:

```jsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Pantalla "Correo (SMTP)" (2026-09-12, etapa 1): copia de AteneaERP. La
// contraseña nunca vuelve del backend (llega una máscara), el estado corrupto
// se nombra, y cada error es un código que se traduce.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminSmtp from './AdminSmtp'
import { I18nProvider } from '../../i18n/index.jsx'

const MASCARA = '••••••••'
const GUARDADA = {
  host: 'mail.axioma-ia.io', port: 587, encryption: 'tls', user: 'no-reply@axioma-ia.io',
  password: MASCARA, from_name: 'Axioma', from_email: 'no-reply@axioma-ia.io',
  configurado: true, corrupta: false, motivo: null,
}

function renderSmtp() {
  return render(<I18nProvider><AdminSmtp /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset(); api.put.mockReset(); api.post.mockReset()
  localStorage.clear()
})

describe('AdminSmtp', () => {
  it('muestra la máscara y la manda tal cual al guardar (conserva la guardada)', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.put.mockResolvedValue({ data: { ok: true } })
    const { container } = renderSmtp()
    await waitFor(() => expect(container.querySelector('input[type="password"]')).toHaveValue(MASCARA))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/smtp', expect.objectContaining({ password: MASCARA, port: 587 })))
    expect(await screen.findByText('Configuración SMTP guardada.')).toBeInTheDocument()
  })

  it('estado corrupto: nombra el motivo y pide volver a escribir la contraseña', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, password: '', corrupta: true, motivo: 'password_ilegible' } })
    renderSmtp()
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(/no se puede descifrar/)
    expect(alerta).toHaveTextContent(/Volvé a escribir la contraseña/)
  })

  it('probar conexión muestra el paso exacto traducido y lo que respondió el servidor', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post.mockRejectedValue({ response: { status: 422, data: { detail: { code: 'smtp_auth_rechazada', server: '535 5.7.8 Authentication failed' } } } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: 'Probar conexión' }))
    const estado = await screen.findByRole('status')
    expect(estado).toHaveTextContent('El servidor rechazó el usuario o la contraseña.')
    expect(estado).toHaveTextContent('Respuesta del servidor: 535 5.7.8 Authentication failed')
  })

  it('guardar sin contraseña la primera vez muestra el error traducido', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, host: '', password: '', configurado: false } })
    api.put.mockRejectedValue({ response: { status: 422, data: { detail: 'smtp_exige_contrasena' } } })
    renderSmtp()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(await screen.findByText('Escribí la contraseña: no hay una guardada que se pueda usar.')).toBeInTheDocument()
  })

  it('correo de prueba: sin SMTP traduce el 503; con éxito dice a quién se envió', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post
      .mockRejectedValueOnce({ response: { status: 503, data: { detail: 'smtp_no_configurado' } } })
      .mockResolvedValueOnce({ data: { ok: true, to: 'fernando@rich-hn.com' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: 'Enviar correo de prueba' }))
    expect(await screen.findByText('El correo saliente no está configurado.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Enviar correo de prueba' }))
    expect(await screen.findByText('Correo de prueba enviado a fernando@rich-hn.com.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Rojo**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run src/pages/admin/AdminSmtp.test.jsx`
Expected: FAIL con `Failed to resolve import "./AdminSmtp"`.

- [ ] **Step 3: Implementación mínima**

Crear `frontend/src/pages/admin/AdminSmtp.jsx`:

```jsx
import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import PasswordInput from '../../components/PasswordInput'

// Correo saliente (2026-09-12, etapa 1 de administración de usuarios): copia
// del comportamiento de AteneaERP. La contraseña nunca vuelve del backend: si
// hay una guardada llega la máscara, y guardar con la máscara la conserva.
// Cada error es un código estable que se traduce; si el servidor SMTP
// respondió algo, se muestra además tal cual (es lo que hace falta para
// arreglarlo).
const MASCARA = '••••••••'
const VACIO = { host: '', port: 587, encryption: 'tls', user: '', password: '', from_name: '', from_email: '' }
const INPUT = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500'
const LABEL = 'block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider'
const BOTON = 'px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50'

function codigoDe(err) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.code
}

export default function AdminSmtp() {
  const { t } = useI18n()
  const [form, setForm] = useState(VACIO)
  const [estado, setEstado] = useState({ corrupta: false, motivo: null })
  const [ocupado, setOcupado] = useState(null)
  const [resultado, setResultado] = useState(null)

  function mensaje(err) {
    const code = codigoDe(err)
    const base = (code && t.smtpErrors[code]) || t.smtpErrorGeneric
    const servidor = err?.response?.data?.detail?.server
    return servidor ? `${base} ${t.smtpServerSaid(servidor)}` : base
  }

  function motivoLegible(motivo) {
    const [tipo, clave] = (motivo || '').split(':')
    if (tipo === 'password_ilegible') return t.smtpMotivoPasswordIlegible
    if (tipo === 'clave_ausente') return t.smtpMotivoClaveAusente(clave)
    if (tipo === 'valor_invalido') return t.smtpMotivoValorInvalido(clave)
    return t.smtpErrorGeneric
  }

  async function cargar() {
    const { data } = await api.get('/admin/smtp')
    setForm({
      host: data.host, port: data.port, encryption: data.encryption, user: data.user,
      password: data.password, from_name: data.from_name, from_email: data.from_email,
    })
    setEstado({ corrupta: data.corrupta, motivo: data.motivo })
  }

  useEffect(() => {
    cargar().catch((err) => setResultado({ ok: false, texto: mensaje(err) }))
  }, [])

  function set(campo, valor) {
    setForm((f) => ({ ...f, [campo]: valor }))
  }

  async function accion(nombre, llamada, textoOk) {
    setOcupado(nombre)
    setResultado(null)
    try {
      const respuesta = await llamada()
      setResultado({ ok: true, texto: textoOk(respuesta?.data) })
    } catch (err) {
      setResultado({ ok: false, texto: mensaje(err) })
    } finally {
      setOcupado(null)
    }
  }

  function guardar(e) {
    e.preventDefault()
    accion('guardar', async () => {
      const r = await api.put('/admin/smtp', { ...form, port: Number(form.port) })
      await cargar()
      return r
    }, () => t.smtpSaved)
  }

  function probarConexion() {
    const { host, port, encryption, user, password } = form
    accion('conexion', () => api.post('/admin/smtp/test-connection', { host, port: Number(port), encryption, user, password }),
      () => t.smtpConnectionOk)
  }

  function enviarPrueba() {
    accion('prueba', () => api.post('/admin/smtp/test'), (data) => t.smtpTestSent(data?.to))
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-100 mb-1">{t.smtpTitle}</h1>
      <p className="text-xs text-slate-500 mb-6">{t.smtpDesc}</p>

      {estado.corrupta && (
        <div role="alert" className="max-w-lg mb-4 text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
          <strong>{t.smtpCorruptTitle}:</strong> {motivoLegible(estado.motivo)}. {t.smtpCorruptDesc}
        </div>
      )}

      <form onSubmit={guardar} className="max-w-lg space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <label className={LABEL} htmlFor="smtp-host">{t.smtpHost}</label>
            <input id="smtp-host" className={INPUT} value={form.host} onChange={(e) => set('host', e.target.value)} required />
          </div>
          <div>
            <label className={LABEL} htmlFor="smtp-port">{t.smtpPort}</label>
            <input id="smtp-port" type="number" min="1" max="65535" className={INPUT} value={form.port} onChange={(e) => set('port', e.target.value)} required />
          </div>
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-enc">{t.smtpEncryption}</label>
          <select id="smtp-enc" className={INPUT} value={form.encryption} onChange={(e) => set('encryption', e.target.value)}>
            <option value="tls">{t.smtpEncTls}</option>
            <option value="ssl">{t.smtpEncSsl}</option>
            <option value="none">{t.smtpEncNone}</option>
          </select>
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-user">{t.smtpUser}</label>
          <input id="smtp-user" className={INPUT} value={form.user} onChange={(e) => set('user', e.target.value)} required autoComplete="off" />
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-pass">{t.smtpPassword}</label>
          <PasswordInput id="smtp-pass" className={INPUT} value={form.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" />
          {form.password === MASCARA && <p className="mt-1 text-xs text-slate-500">{t.smtpPasswordHint}</p>}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL} htmlFor="smtp-from-name">{t.smtpFromName}</label>
            <input id="smtp-from-name" className={INPUT} value={form.from_name} onChange={(e) => set('from_name', e.target.value)} required />
          </div>
          <div>
            <label className={LABEL} htmlFor="smtp-from-email">{t.smtpFromEmail}</label>
            <input id="smtp-from-email" type="email" className={INPUT} value={form.from_email} onChange={(e) => set('from_email', e.target.value)} required />
          </div>
        </div>

        {resultado && (
          <div role="status" className={`text-sm rounded-lg px-3 py-2 border ${resultado.ok ? 'text-green-400 bg-green-900/30 border-green-800' : 'text-red-400 bg-red-900/30 border-red-800'}`}>
            {resultado.texto}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-2">
          <button type="submit" disabled={ocupado !== null} className={`${BOTON} bg-purple-600 hover:bg-purple-700 text-white`}>
            {ocupado === 'guardar' ? t.smtpSaving : t.smtpSave}
          </button>
          <button type="button" onClick={probarConexion} disabled={ocupado !== null} className={`${BOTON} bg-slate-700 hover:bg-slate-600 text-slate-300`}>
            {ocupado === 'conexion' ? t.smtpTesting : t.smtpTestConnection}
          </button>
          <button type="button" onClick={enviarPrueba} disabled={ocupado !== null} className={`${BOTON} bg-slate-700 hover:bg-slate-600 text-slate-300`}>
            {ocupado === 'prueba' ? t.smtpSending : t.smtpSendTest}
          </button>
        </div>
      </form>
    </div>
  )
}
```

En `frontend/src/pages/Admin.jsx`: agregar `import AdminSmtp from './admin/AdminSmtp'` después de `import AdminCosts from './admin/AdminCosts'`, y `<Route path="smtp" element={<AdminSmtp />} />` después de la ruta `costs`.

En `frontend/src/components/admin/AdminSidebar.jsx`, en `NAV_ITEMS`, después de la línea de `settings` agregar:

```js
  { path: 'smtp',      labelKey: 'adminSmtp',      icon: '✉' },
```

En `frontend/src/i18n/es.js`, insertar ANTES de la línea `  // Restaurar tareas pendientes (useJaxStore.js)`:

```js
  // Correo saliente (SMTP) — AdminSmtp.jsx (2026-09-12, admin usuarios etapa 1)
  adminSmtp: 'Correo (SMTP)',
  smtpTitle: 'Correo saliente (SMTP)',
  smtpDesc: 'Servidor con el que Axioma envía los enlaces de recuperación de contraseña.',
  smtpHost: 'Servidor',
  smtpPort: 'Puerto',
  smtpEncryption: 'Cifrado',
  smtpEncTls: 'STARTTLS',
  smtpEncSsl: 'SSL/TLS',
  smtpEncNone: 'Sin cifrado',
  smtpUser: 'Usuario',
  smtpPassword: 'Contraseña',
  smtpPasswordHint: 'Hay una contraseña guardada. Dejá la máscara para conservarla o escribí una nueva.',
  smtpFromName: 'Nombre del remitente',
  smtpFromEmail: 'Correo del remitente',
  smtpSave: 'Guardar',
  smtpSaving: 'Guardando…',
  smtpSaved: 'Configuración SMTP guardada.',
  smtpTestConnection: 'Probar conexión',
  smtpTesting: 'Probando…',
  smtpConnectionOk: 'Conexión y autenticación verificadas.',
  smtpSendTest: 'Enviar correo de prueba',
  smtpSending: 'Enviando…',
  smtpTestSent: (to) => `Correo de prueba enviado a ${to}.`,
  smtpCorruptTitle: 'La configuración guardada está dañada',
  smtpCorruptDesc: 'El envío de correos está deshabilitado. Volvé a escribir la contraseña y guardá.',
  smtpMotivoPasswordIlegible: 'la contraseña guardada no se puede descifrar (¿cambió FERNET_KEY?)',
  smtpMotivoClaveAusente: (clave) => `falta el valor ${clave}`,
  smtpMotivoValorInvalido: (clave) => `el valor ${clave} no es válido`,
  smtpServerSaid: (texto) => `Respuesta del servidor: ${texto}`,
  smtpErrorGeneric: 'No se pudo completar la operación.',
  smtpErrors: {
    smtp_exige_contrasena: 'Escribí la contraseña: no hay una guardada que se pueda usar.',
    smtp_from_email_invalido: 'El correo del remitente no es válido.',
    smtp_sin_contrasena: 'No hay contraseña SMTP guardada: escribila para probar.',
    smtp_password_ilegible: 'La contraseña guardada no se puede descifrar: escribila de nuevo.',
    smtp_sin_clave_de_cifrado: 'El servidor no tiene FERNET_KEY: no se puede guardar la contraseña cifrada.',
    smtp_no_configurado: 'El correo saliente no está configurado.',
    smtp_config_corrupta: 'La configuración SMTP está dañada: el envío está deshabilitado.',
    smtp_envio_fallido: 'El servidor SMTP no aceptó el correo.',
    smtp_demasiadas_pruebas: 'Demasiadas pruebas seguidas. Esperá unos minutos.',
    smtp_conexion_fallida: 'No se pudo conectar con el servidor. Revisá servidor, puerto y cifrado.',
    smtp_saludo_inesperado: 'El servidor respondió un saludo inesperado.',
    smtp_ehlo_fallido: 'El servidor rechazó el saludo EHLO.',
    smtp_starttls_no_disponible: 'El servidor no ofrece STARTTLS en ese puerto.',
    smtp_tls_fallido: 'Falló la negociación TLS (certificado inválido o nombre que no coincide).',
    smtp_auth_rechazada: 'El servidor rechazó el usuario o la contraseña.',
    smtp_auth_no_soportada: 'El servidor no admite autenticación en esa conexión.',
  },

```

En `frontend/src/i18n/en.js`, insertar ANTES de `  // Restoring pending tasks (useJaxStore.js)`:

```js
  // Outgoing email (SMTP) — AdminSmtp.jsx (2026-09-12, user admin stage 1)
  adminSmtp: 'Email (SMTP)',
  smtpTitle: 'Outgoing email (SMTP)',
  smtpDesc: 'Server Axioma uses to send password recovery links.',
  smtpHost: 'Server',
  smtpPort: 'Port',
  smtpEncryption: 'Encryption',
  smtpEncTls: 'STARTTLS',
  smtpEncSsl: 'SSL/TLS',
  smtpEncNone: 'No encryption',
  smtpUser: 'Username',
  smtpPassword: 'Password',
  smtpPasswordHint: 'A password is stored. Leave the mask to keep it or type a new one.',
  smtpFromName: 'Sender name',
  smtpFromEmail: 'Sender email',
  smtpSave: 'Save',
  smtpSaving: 'Saving…',
  smtpSaved: 'SMTP settings saved.',
  smtpTestConnection: 'Test connection',
  smtpTesting: 'Testing…',
  smtpConnectionOk: 'Connection and authentication verified.',
  smtpSendTest: 'Send test email',
  smtpSending: 'Sending…',
  smtpTestSent: (to) => `Test email sent to ${to}.`,
  smtpCorruptTitle: 'The stored settings are damaged',
  smtpCorruptDesc: 'Sending email is disabled. Type the password again and save.',
  smtpMotivoPasswordIlegible: 'the stored password cannot be decrypted (did FERNET_KEY change?)',
  smtpMotivoClaveAusente: (clave) => `the value ${clave} is missing`,
  smtpMotivoValorInvalido: (clave) => `the value ${clave} is not valid`,
  smtpServerSaid: (texto) => `Server response: ${texto}`,
  smtpErrorGeneric: 'The operation could not be completed.',
  smtpErrors: {
    smtp_exige_contrasena: 'Type the password: there is no stored one that can be used.',
    smtp_from_email_invalido: 'The sender email is not valid.',
    smtp_sin_contrasena: 'No SMTP password is stored: type it to test.',
    smtp_password_ilegible: 'The stored password cannot be decrypted: type it again.',
    smtp_sin_clave_de_cifrado: 'The server has no FERNET_KEY: the password cannot be stored encrypted.',
    smtp_no_configurado: 'Outgoing email is not configured.',
    smtp_config_corrupta: 'The SMTP settings are damaged: sending is disabled.',
    smtp_envio_fallido: 'The SMTP server did not accept the email.',
    smtp_demasiadas_pruebas: 'Too many tests in a row. Wait a few minutes.',
    smtp_conexion_fallida: 'Could not connect to the server. Check server, port and encryption.',
    smtp_saludo_inesperado: 'The server answered with an unexpected greeting.',
    smtp_ehlo_fallido: 'The server rejected the EHLO greeting.',
    smtp_starttls_no_disponible: 'The server does not offer STARTTLS on that port.',
    smtp_tls_fallido: 'TLS negotiation failed (invalid certificate or name mismatch).',
    smtp_auth_rechazada: 'The server rejected the username or password.',
    smtp_auth_no_soportada: 'The server does not allow authentication on that connection.',
  },

```

- [ ] **Step 4: Verde**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run`
Expected: `92 passed` (87 + 5), 0 fallidos.

- [ ] **Step 5: Piso de vitest**

En `.github/workflows/policy.yml`, job `frontend-tests`, cambiar `r.numPassedTests !== 87` y el mensaje `se esperaban 87` por el número medido (esperado 92), y sumar al comentario del job: `92 desde el 2026-09-12 (admin usuarios etapa 1: AdminSmtp.test.jsx agrega 5).`

- [ ] **Step 6: Modo claro/oscuro, a mano**

`cd /home/fruiz/jax-platform/frontend && npx vite --host 127.0.0.1 --port 5174`, entrar a `http://127.0.0.1:5174/admin/smtp` (el proxy `/api` de vite.config.js va a `localhost:8080`) y alternar el tema con el ícono de la barra: los campos y textos siguen legibles en los dos modos. Cortar el servidor al terminar.

- [ ] **Step 7: Commit**

```bash
git -C /home/fruiz/jax-platform add frontend/src/pages/admin/AdminSmtp.jsx frontend/src/pages/admin/AdminSmtp.test.jsx frontend/src/pages/Admin.jsx frontend/src/components/admin/AdminSidebar.jsx frontend/src/i18n/es.js frontend/src/i18n/en.js .github/workflows/policy.yml
git -C /home/fruiz/jax-platform commit -m "feat(admin): pantalla Correo (SMTP) con probar conexión y correo de prueba"
```

---

### Task 5: PR, CI por headSha, despliegue y verificación en vivo

**Files:** ninguno de código. Resultado en la Biblioteca: `/home/fruiz/jax/DEUDA.md` (ahí vive la VERDAD OPERACIONAL de jax-platform con fecha; ver la entrada "Prueba de carga — VERDAD OPERACIONAL, 2026-09-12 13:21 CST").

- [ ] **Step 1: Suite completa local**

```bash
cd /home/fruiz/jax-platform/backend && .venv/bin/python -m pytest -q 2>&1 | tail -3
cd /home/fruiz/jax-platform/backend && JAX_CI_NO_DB=1 .venv/bin/python -m pytest -q 2>&1 | tail -3
cd /home/fruiz/jax-platform/frontend && npx vitest run 2>&1 | tail -5
```
Expected: los tres números iguales a los pisos escritos en `policy.yml`, 0 fallidos.

- [ ] **Step 2: PR**

```bash
git -C /home/fruiz/jax-platform push -u origin feat/admin-usuarios-1-smtp
gh pr create --repo fjruizhn/jax-platform --base master --head feat/admin-usuarios-1-smtp \
  --title "Admin usuarios · etapa 1: correo saliente (SMTP) configurable desde Admin" \
  --body "Spec: docs/superpowers/specs/2026-09-12-administracion-usuarios-design.md §3.1. Plan: docs/superpowers/plans/2026-09-12-admin-usuarios-etapa-1-smtp.md. Copia de AteneaERP con TLS verificado; /api/admin/config deja de ver smtp.*; la recuperación de contraseña envía con la configuración de la base."
```
(El cuerpo del PR termina con las líneas de atribución que indique la sesión.)

- [ ] **Step 3: Gate de CI por headSha (ANTES de mergear)**

```bash
gh pr checks <N> --repo fjruizhn/jax-platform
gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid
git -C /home/fruiz/jax-platform ls-remote origin refs/heads/feat/admin-usuarios-1-smtp
```
Expected: ningún check fuera de `SUCCESS` (ni pending, ni skipped inesperado) y `headRefOid` == el sha de `ls-remote`. Si no se cumple, no se mergea.

- [ ] **Step 4: Merge y despliegue del backend**

```bash
gh pr merge <N> --repo fjruizhn/jax-platform --merge
git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only
sudo -n /usr/bin/systemctl restart jax-platform.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health
sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40
```
Expected: `200`, y en el journal el arranque sin tracebacks. Verificar además `readlink /proc/$(systemctl show -p MainPID --value jax-platform.service)/cwd` → `/home/fruiz/jax-platform/backend` (el servicio sirve desde el checkout).

- [ ] **Step 5: Despliegue del frontend**

```bash
cd /home/fruiz/jax-platform/frontend && npm run build
ls /home/fruiz/jax-platform/frontend/dist/assets/index-*.js
```
Anotar el nombre del `index-*.js`. En la VM dev (`172.16.20.11`; puerto y usuario SSH en `/etc/jax/.env` como `JAX_SSH_PORT` y `JAX_SSH_USER`):

```bash
set -a; . /etc/jax/.env; set +a
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-admin-usuarios-1-$(date +%Y%m%d-%H%M%S)"
rsync -a --delete --exclude .user.ini -e "ssh -p $JAX_SSH_PORT" /home/fruiz/jax-platform/frontend/dist/ "$JAX_SSH_USER@172.16.20.11:/tmp/axioma-deploy/"
ssh -p "$JAX_SSH_PORT" "$JAX_SSH_USER@172.16.20.11" "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```
Expected: el `index-*.js` servido es el recién construido. (Sin `--exclude .user.ini` el `--delete` falla con código 23: aaPanel lo deja inmutable.)

- [ ] **Step 6: Verificación en vivo — configuración y "Probar conexión"**

Fernando carga en `https://axioma-ia.io/admin/smtp`: servidor `mail.axioma-ia.io`, puerto `587`, cifrado `STARTTLS`, usuario `no-reply@axioma-ia.io`, su contraseña, remitente `Axioma` / `no-reply@axioma-ia.io`. Guardar → "Probar conexión" en verde. Comprobar en la base que la contraseña quedó cifrada (sin mostrar su valor):

```bash
set -a; . /etc/jax/.env; set +a
mariadb -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" -p"$JAX_DB_PASSWORD" jax_memory -e "SELECT config_key, LEFT(config_value, 6) AS inicio, LENGTH(config_value) AS largo FROM axioma_config WHERE config_key LIKE 'smtp.%'"
```
Expected: `smtp.password` empieza con `gAAAAA` (token Fernet), no con el texto de la contraseña. Verificar también que `GET /api/admin/config` (Admin → Configuración) no muestra ninguna clave `smtp.*`.

- [ ] **Step 7: GATE — correo de prueba real (requiere permiso explícito de Fernando)**

NO se ejecuta sin un "sí" de Fernando en la conversación. Con el permiso: botón "Enviar correo de prueba". Fernando abre el correo recibido → "Mostrar original" / ver cabeceras, y copia la línea `Authentication-Results`. Criterio de cierre (spec §3.1): contiene `dkim=pass` **y** `spf=pass`. Que la clave DKIM esté publicada no prueba que el servidor firme: si sale `dkim=none` o `dkim=fail`, la etapa NO se cierra y se abre el diagnóstico del firmado en `mail.axioma-ia.io`.

- [ ] **Step 8: Registrar en la Biblioteca**

Agregar a `/home/fruiz/jax/DEUDA.md` (y commitear en el repo `jax` por su propio PR, según las reglas de ese repo) una entrada fechada: "SMTP de jax-platform configurado desde Admin — VERDAD OPERACIONAL <fecha hora CST>": PR y sha desplegado, `index-*.js` servido, resultado de "Probar conexión", y la línea `Authentication-Results` con `dkim=`/`spf=` tal cual. Lecciones y pendientes con fecha.

---

## Autorrevisión (hecha al escribir el plan)

- **Cobertura de §3.1:** claves `smtp.*` (Task 1), cifrado con `encrypt_secret`/`decrypt_db_secret` (Task 1), 4 endpoints con máscara y "solo se reemplaza si llega una nueva distinta de la máscara" (Tasks 1-2), probar conexión con el error de cada paso (Tasks 1-2), correo de prueba al superadmin con límite (Task 2), estado corrupto visible, reconfigurar exige contraseña, envío deshabilitado con error explícito (Tasks 1-2, 503 en `/smtp/test` y log en el reset público), "sin configurar" → forgot-password neutro (Task 3), TLS verificado (Task 1, tests 11-12 y 16-17), `/api/admin/config` excluye `smtp.*` (Task 2), `_send_reset_email` lee la base sin caché (Task 3), verificación con `dkim=pass`/`spf=pass` (Task 5, gate). El 503 del reset por admin es de la etapa 4.
- **Placeholders:** `<N>`, `<medido>` y `<fecha hora CST>` son valores que solo existen al ejecutar (número de PR y conteos medidos). No hay pasos de código sin código.
- **Nombres:** `SmtpNoDisponible.codigo`, `SmtpPasoFallido.codigo/.servidor`, `SMTP_TEST_LIMITER`, `_send_reset_email(settings, to_email, reset_link)` son iguales en tests, implementación e Interfaces.
