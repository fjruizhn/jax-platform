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


# ------------------------- fix wave de la revisión final (2026-09-13)

@pytest.mark.parametrize("campo,valor", [
    ("host", "atacante.example.test"), ("port", 2525), ("encryption", "ssl"), ("user", "otro@example.test"),
])
def test_la_contrasena_guardada_no_se_reusa_contra_otro_servidor(campo, valor):
    # CRÍTICO de la revisión final: con la máscara y un host (o puerto,
    # cifrado, usuario) distinto, la contraseña guardada viajaría a un
    # servidor elegido por quien llama. Solo se reusa contra el MISMO.
    datos = _datos(password=smtp_config.MASCARA, **{campo: valor})
    with pytest.raises(smtp_config.SmtpReescribirContrasena) as exc:
        smtp_config.filas_a_guardar(_filas(), datos)
    assert exc.value.codigo == "smtp_reescribir_contrasena_al_cambiar_servidor"
    with pytest.raises(smtp_config.SmtpReescribirContrasena):
        smtp_config.contrasena_para_reusar(_filas(), datos)


def test_la_contrasena_guardada_se_reusa_contra_el_mismo_servidor():
    datos = _datos(password=smtp_config.MASCARA, from_name="Otro nombre")
    assert smtp_config.contrasena_para_reusar(_filas(), datos) == "clave-smtp-de-prueba"
    filas = smtp_config.filas_a_guardar(_filas(), datos)
    assert smtp_config.CLAVE_SECRETA not in filas and filas["smtp.from_name"] == "Otro nombre"


def test_con_contrasena_nueva_se_puede_cambiar_de_servidor():
    filas = smtp_config.filas_a_guardar(_filas(), _datos(password="nueva", host="otro.example.test"))
    assert filas["smtp.host"] == "otro.example.test"
    assert smtp_config.decrypt_db_secret(filas[smtp_config.CLAVE_SECRETA]) == "nueva"


def test_contrasena_para_reusar_sin_guardada_o_ilegible():
    with pytest.raises(smtp_config.SmtpExigeContrasena) as exc:
        smtp_config.contrasena_para_reusar({}, _datos())
    assert exc.value.motivo == "sin_contrasena"
    ajena = Fernet(Fernet.generate_key()).encrypt(b"otra").decode()
    with pytest.raises(smtp_config.SmtpExigeContrasena) as exc:
        smtp_config.contrasena_para_reusar(_filas(**{"smtp.password": ajena}), _datos())
    assert exc.value.motivo == "password_ilegible"


def test_el_repr_de_los_settings_no_muestra_la_contrasena():
    s = smtp_config.interpretar(_filas())
    assert "clave-smtp-de-prueba" not in repr(s)


def test_pantalla_sin_host_no_muestra_mascara_de_una_contrasena_que_no_descifra():
    ajena = Fernet(Fernet.generate_key()).encrypt(b"otra").decode()
    assert smtp_config.estado_para_pantalla({"smtp.password": ajena})["password"] == ""
    sobrante = smtp_config.encrypt_secret("sobrante")
    assert smtp_config.estado_para_pantalla({"smtp.password": sobrante})["password"] == smtp_config.MASCARA


@pytest.mark.parametrize("puerto", ["0", "65536", "99999"])
def test_puerto_fuera_de_rango_es_corrupto(puerto):
    assert smtp_config.motivo_de_corrupcion(_filas(**{"smtp.port": puerto})) == "valor_invalido:smtp.port"


@pytest.mark.parametrize("clave", ["smtp.host", "smtp.user", "smtp.from_name"])
def test_caracteres_de_control_guardados_son_corruptos(clave):
    # Una fila vieja con un salto de línea en el remitente rompía TODOS los
    # correos (EmailMessage lanza ValueError): se nombra como estado corrupto.
    assert smtp_config.motivo_de_corrupcion(_filas(**{clave: "a\r\nBcc: x@y"})) == f"valor_invalido:{clave}"


def test_caracteres_de_control():
    from validacion import tiene_caracteres_de_control
    for malo in ("a\nb", "a\rb", "a\tb", "a\x00b", "a\x1fb", "a\x7fb"):
        assert tiene_caracteres_de_control(malo), repr(malo)
    assert not tiene_caracteres_de_control("Axioma · Infraestructura ñ")


def test_clave_de_cifrado_utilizable(monkeypatch):
    from crypto_secrets import clave_de_cifrado_utilizable
    assert clave_de_cifrado_utilizable()
    monkeypatch.setenv("FERNET_KEY", "no-es-una-clave-fernet")
    assert not clave_de_cifrado_utilizable()
    monkeypatch.delenv("FERNET_KEY")
    assert not clave_de_cifrado_utilizable()


def test_enviar_sin_cifrado_no_hace_starttls_pero_si_login(monkeypatch):
    registro = []
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso(registro))
    s = smtp_config.interpretar(_filas(**{"smtp.encryption": "none", "smtp.port": "25"}))
    smtp_config.enviar(s, smtp_config.construir_mensaje(s, "d@example.test", "a", "t", "<p>h</p>"))
    (srv,) = registro
    assert "starttls" not in srv.llamadas
    assert ("login", "no-reply@example.test", "clave-smtp-de-prueba") in srv.llamadas


def test_probar_conexion_sin_cifrado_no_hace_starttls_pero_si_login(monkeypatch):
    registro = []
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso(registro))
    smtp_config.probar_conexion("mail.example.test", 25, "none", "u", "p")
    (srv,) = registro
    assert "starttls" not in srv.llamadas and ("login", "u", "p") in srv.llamadas


def test_probar_conexion_con_contrasena_no_ascii_da_codigo_estable(monkeypatch):
    # smtplib codifica el AUTH en ascii: sin esto, UnicodeEncodeError -> 500.
    error = UnicodeEncodeError("ascii", "clavé", 4, 5, "ordinal not in range(128)")
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso([], login_error=error))
    with pytest.raises(smtp_config.SmtpPasoFallido) as exc:
        smtp_config.probar_conexion("mail.example.test", 587, "tls", "u", "clavé")
    assert exc.value.codigo == "smtp_password_no_ascii"


# ------------------------------- fix wave 2 de la revisión final (2026-09-13)

def test_decrypt_db_secret_con_clave_malformada_es_vacio(monkeypatch):
    # "Clave malformada" == "sin clave", como dice su docstring: nunca un 500.
    cifrada = smtp_config.encrypt_secret("x")
    monkeypatch.setenv("FERNET_KEY", "no-es-una-clave-fernet")
    assert smtp_config.decrypt_db_secret(cifrada) == ""
    assert smtp_config.decrypt_db_secret("") == ""


def _error_idna(host):
    try:
        host.encode("idna")
    except UnicodeError as exc:
        return exc
    raise AssertionError(f"{host!r} codificó bien en IDNA")


def test_probar_conexion_con_host_idna_invalido_es_conexion_fallida(monkeypatch):
    # getaddrinfo codifica el host en IDNA: una etiqueta de >63 caracteres o
    # "a..b" lanza UnicodeError (no OSError) desde smtplib.SMTP().
    error = _error_idna("a" * 64 + ".example.test")
    assert isinstance(error, UnicodeEncodeError)  # la real, medida 2026-09-13
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso([], connect_error=error))
    with pytest.raises(smtp_config.SmtpPasoFallido) as exc:
        smtp_config.probar_conexion("a" * 64 + ".example.test", 587, "tls", "u", "p")
    assert exc.value.codigo == "smtp_conexion_fallida"


def test_enviar_con_host_idna_invalido_lanza_oserror(monkeypatch):
    # Quien llama a enviar maneja OSError/SMTPException: el UnicodeError del
    # host no puede escaparse (en /smtp/test sería un 500).
    monkeypatch.setattr(smtplib, "SMTP", _smtp_falso([], connect_error=_error_idna("a..b")))
    s = smtp_config.interpretar(_filas(**{"smtp.host": "a..b"}))
    with pytest.raises(OSError):
        smtp_config.enviar(s, smtp_config.construir_mensaje(s, "d@example.test", "a", "t", "<p>h</p>"))


def test_decrypt_db_secret_con_clave_malformada_avisa_en_el_log(monkeypatch, caplog):
    # Item H (2026-09-13): "" en silencio escondía una FERNET_KEY rota también
    # para keys.py y credentials.py. Sigue devolviendo "", pero avisa, sin la clave.
    cifrada = smtp_config.encrypt_secret("x")
    monkeypatch.setenv("FERNET_KEY", "no-es-una-clave-fernet")
    with caplog.at_level("WARNING", logger="crypto_secrets"):
        assert smtp_config.decrypt_db_secret(cifrada) == ""
    avisos = [r.getMessage() for r in caplog.records if r.name == "crypto_secrets"]
    assert avisos and "FERNET_KEY" in avisos[0]
    assert not [m for m in avisos if "no-es-una-clave-fernet" in m or cifrada in m]


# ------------------- destinatario de la prueba (2026-09-13, decisión de Fernando)

def test_destino_de_prueba_ausente_o_invalido_no_corrompe_el_envio():
    # smtp.test_to es opcional: su ausencia nunca vuelve corrupta la config, y
    # guardada inválida solo afecta al default de la prueba, no al envío.
    assert smtp_config.CLAVE_DESTINO_PRUEBA not in smtp_config.CLAVES
    assert smtp_config.CLAVE_DESTINO_PRUEBA in smtp_config.CLAVES_OPCIONALES
    assert smtp_config.motivo_de_corrupcion(_filas()) is None
    rota = _filas(**{"smtp.test_to": "basura\r\nBcc: x@y.io"})
    assert smtp_config.motivo_de_corrupcion(rota) is None
    assert smtp_config.interpretar(rota).host == "mail.example.test"


def test_pantalla_expone_test_to():
    assert smtp_config.estado_para_pantalla({})["test_to"] == ""
    assert smtp_config.estado_para_pantalla(_filas())["test_to"] == ""
    estado = smtp_config.estado_para_pantalla(_filas(**{"smtp.test_to": "prueba@example.test"}))
    assert estado["test_to"] == "prueba@example.test"


@pytest.mark.parametrize("valor,guardado", [(" prueba@example.test ", "prueba@example.test"),
                                            ("", ""), ("   ", ""), (None, "")])
def test_filas_a_guardar_con_test_to(valor, guardado):
    filas = smtp_config.filas_a_guardar(_filas(), _datos(password="nueva", test_to=valor))
    assert filas[smtp_config.CLAVE_DESTINO_PRUEBA] == guardado


def test_filas_a_guardar_sin_test_to_no_toca_la_fila():
    filas = smtp_config.filas_a_guardar(_filas(), _datos(password="nueva"))
    assert smtp_config.CLAVE_DESTINO_PRUEBA not in filas


@pytest.mark.parametrize("malo", ["no-es-un-correo", "a@example.test\r\nBcc: x@y.io", "a@example.test\x00"])
def test_filas_a_guardar_rechaza_test_to_invalido(malo):
    with pytest.raises(smtp_config.SmtpDestinatarioInvalido) as exc:
        smtp_config.filas_a_guardar(_filas(), _datos(password="nueva", test_to=malo))
    assert exc.value.codigo == "smtp_destinatario_invalido"


def test_destinatario_de_prueba_resuelve_pedido_guardado_o_sesion():
    guardado = _filas(**{"smtp.test_to": "guardado@example.test"})
    elegir = smtp_config.destinatario_de_prueba
    # 1) el pedido (strip) gana; 2) si no, el guardado; 3) si no, None = la sesión.
    assert elegir(" pedido@example.test ", guardado) == "pedido@example.test"
    for vacio in (None, "", "   "):
        assert elegir(vacio, guardado) == "guardado@example.test"
        assert elegir(vacio, _filas()) is None
        assert elegir(vacio, _filas(**{"smtp.test_to": ""})) is None


@pytest.mark.parametrize("malo", ["no-es-un-correo", "a@example.test\r\nBcc: x@y.io", "a@example.test\x7f"])
def test_destinatario_de_prueba_invalido_pedido_o_guardado(malo):
    with pytest.raises(smtp_config.SmtpDestinatarioInvalido):
        smtp_config.destinatario_de_prueba(malo, _filas())
    with pytest.raises(smtp_config.SmtpDestinatarioInvalido):
        smtp_config.destinatario_de_prueba(None, _filas(**{"smtp.test_to": malo}))


# ------------- un solo destinatario (revisión, 2026-09-13)
# email_valido deja pasar , ; < > " ( ): "postmaster,a@b.io" se volvía DOS
# destinatarios en el To y "x;y@b.io" se truncaba a "x" (medido por el revisor).

VARIAS_O_RARAS = ["postmaster,a@b.io", "x;y@b.io", '"a"@b.io', "<a@b.io>", "a@b.io,", "a(b)@c.io",
                  "a\\b@c.io", "a[b]@c.io"]
UNICAS = ["fernando@rich-hn.com", "a.b+c@sub.example.test", "no-reply@axioma-ia.io"]


# Ruling U32 (etapa 5, Task 3 fix ronda 1, 2026-09-15): el dominio de
# email_valido solo admite [A-Za-z0-9-]. Estas dos tienen la rareza EN EL
# DOMINIO ("b.io>", "b.io,") y ahora las rechaza ya email_valido; el resto la
# tiene en la parte local y sigue siendo trabajo de direccion_unica_valida.
RAREZA_EN_EL_DOMINIO = {"<a@b.io>", "a@b.io,"}


@pytest.mark.parametrize("malo", VARIAS_O_RARAS)
def test_direccion_unica_valida_rechaza_listas_y_caracteres_de_encabezado(malo):
    import validacion
    assert validacion.email_valido(malo) is (malo not in RAREZA_EN_EL_DOMINIO)
    assert validacion.direccion_unica_valida(malo) is False


@pytest.mark.parametrize("bueno", UNICAS)
def test_direccion_unica_valida_acepta_una_direccion(bueno):
    import validacion
    assert validacion.direccion_unica_valida(bueno) is True


@pytest.mark.parametrize("malo", VARIAS_O_RARAS)
def test_destinatario_de_prueba_rechaza_varias_direcciones(malo):
    with pytest.raises(smtp_config.SmtpDestinatarioInvalido):
        smtp_config.destinatario_de_prueba(malo, _filas())
    with pytest.raises(smtp_config.SmtpDestinatarioInvalido):
        smtp_config.destinatario_de_prueba(None, _filas(**{"smtp.test_to": malo}))
    with pytest.raises(smtp_config.SmtpDestinatarioInvalido):
        smtp_config.filas_a_guardar(_filas(), _datos(password="nueva", test_to=malo))
