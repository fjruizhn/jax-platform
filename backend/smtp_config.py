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
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from crypto_secrets import decrypt_db_secret, encrypt_secret
from db.connection import get_pool
from validacion import tiene_caracteres_de_control

CLAVES = (
    "smtp.host", "smtp.port", "smtp.encryption", "smtp.user",
    "smtp.password", "smtp.from_name", "smtp.from_email",
)
CLAVE_SECRETA = "smtp.password"
MASCARA = "••••••••"
CIFRADOS = ("tls", "ssl", "none")
TIMEOUT_S = 10
# Los que definen A QUIÉN se le entrega la contraseña guardada (ver
# contrasena_para_reusar), y los que no pueden llevar caracteres de control
# (un salto de línea en un encabezado rompe el correo o inyecta otro).
CAMPOS_DE_SERVIDOR = ("host", "port", "encryption", "user")
CAMPOS_DE_TEXTO_PLANO = ("smtp.host", "smtp.user", "smtp.from_name")


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


class SmtpReescribirContrasena(Exception):
    """Se pidió reusar la contraseña guardada contra otro servidor (host,
    puerto, cifrado o usuario distinto del guardado)."""
    codigo = "smtp_reescribir_contrasena_al_cambiar_servidor"


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
    password: str = field(repr=False)  # nunca en un log ni en un traceback
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
    if not filas["smtp.port"].isdigit() or not 1 <= int(filas["smtp.port"]) <= 65535:
        return "valor_invalido:smtp.port"
    if filas["smtp.encryption"] not in CIFRADOS:
        return "valor_invalido:smtp.encryption"
    for clave in CAMPOS_DE_TEXTO_PLANO:
        if tiene_caracteres_de_control(filas[clave]):
            return f"valor_invalido:{clave}"
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
        # Máscara solo si la guardada DESCIFRA: sin smtp.host, motivo es None
        # aunque sobre una fila smtp.password ilegible.
        "password": MASCARA if motivo is None and decrypt_db_secret(filas.get(CLAVE_SECRETA, "")) else "",
        "from_name": filas.get("smtp.from_name", ""),
        "from_email": filas.get("smtp.from_email", ""),
        "configurado": bool(filas.get("smtp.host")),
        "corrupta": motivo is not None,
        "motivo": motivo,
    }


def trae_contrasena_nueva(datos: dict) -> bool:
    return (datos.get("password") or "") not in ("", MASCARA)


def contrasena_para_reusar(filas_actuales: dict[str, str], datos: dict) -> str:
    """LA regla de "¿se puede reusar la contraseña guardada?" -- la usan el
    guardado y la prueba de conexión. Devuelve la guardada, descifrada, solo
    si host, puerto, cifrado y usuario pedidos son los MISMOS que los
    guardados: si no, la contraseña viajaría a un servidor elegido por quien
    llama (revisión final, 2026-09-13). Lanza SmtpExigeContrasena si no hay
    una guardada utilizable y SmtpReescribirContrasena si cambió el servidor."""
    guardada = filas_actuales.get(CLAVE_SECRETA, "")
    if not guardada:
        raise SmtpExigeContrasena("sin_contrasena")
    password = decrypt_db_secret(guardada)
    if not password:
        raise SmtpExigeContrasena("password_ilegible")
    pedido = (str(datos["host"]), str(int(datos["port"])), str(datos["encryption"]), str(datos["user"]))
    actual = tuple(filas_actuales.get(f"smtp.{campo}", "") for campo in CAMPOS_DE_SERVIDOR)
    if pedido != actual:
        raise SmtpReescribirContrasena()
    return password


def filas_a_guardar(filas_actuales: dict[str, str], datos: dict) -> dict[str, str]:
    """Filas a escribir. La contraseña solo se reemplaza si llega una nueva
    distinta de la máscara; sin nueva, se conserva la guardada solo si
    contrasena_para_reusar lo permite. Con el estado corrupto se PUEDE
    reconfigurar (bloquearlo dejaría como salida la cirugía en la base), pero
    exigiendo volver a escribir la contraseña -- igual que AteneaERP."""
    trae_nueva = trae_contrasena_nueva(datos)
    if not trae_nueva:
        motivo = motivo_de_corrupcion(filas_actuales)
        if motivo is not None:
            raise SmtpExigeContrasena(motivo)
        contrasena_para_reusar(filas_actuales, datos)
    filas = {
        "smtp.host": datos["host"],
        "smtp.port": str(int(datos["port"])),
        "smtp.encryption": datos["encryption"],
        "smtp.user": datos["user"],
        "smtp.from_name": datos["from_name"],
        "smtp.from_email": datos["from_email"],
    }
    if trae_nueva:
        filas[CLAVE_SECRETA] = encrypt_secret(datos["password"])
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
    """Todas las filas en UNA transacción. Con el autocommit del pool, un
    INSERT por fila dejaba unos ms el host nuevo con la contraseña vieja
    (smtp.password va última): un envío en esa ventana autenticaba contra el
    host nuevo con la contraseña vieja (revisión final, 2026-09-13)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        autocommit_previo = conn.get_autocommit()
        await conn.autocommit(False)
        confirmado = False
        try:
            async with conn.cursor() as cur:
                for clave, valor in filas.items():
                    await cur.execute(
                        "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)",
                        (clave, valor),
                    )
            await conn.commit()
            confirmado = True
        finally:
            # Sin except: el error sigue su camino; acá solo se deshace y se
            # devuelve la conexión al pool como estaba.
            if not confirmado:
                await conn.rollback()
            await conn.autocommit(autocommit_previo)


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
    try:
        if encryption == "ssl":
            return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_S, context=contexto)
        return smtplib.SMTP(host, port, timeout=TIMEOUT_S)
    except UnicodeError as exc:
        # getaddrinfo codifica el host en IDNA: una etiqueta de más de 63
        # caracteres o "a..b" lanza UnicodeEncodeError, no OSError (medido
        # 2026-09-13). Es un host que no se puede resolver: OSError, que es lo
        # que maneja quien llama -- y no se confunde con el AUTH no ASCII.
        raise OSError(f"host inválido: {exc}") from exc


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
        except UnicodeEncodeError as exc:
            # smtplib codifica el AUTH en ascii. El endpoint ya rechaza una
            # contraseña nueva no ASCII; esto cubre filas viejas.
            raise SmtpPasoFallido("smtp_password_no_ascii") from exc
    except SmtpPasoFallido:
        raise
    except (OSError, smtplib.SMTPException) as exc:
        raise SmtpPasoFallido("smtp_conexion_fallida", str(exc)) from exc
    finally:
        _cerrar(servidor)
