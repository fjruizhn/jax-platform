"""Frente A (2026-09-16): recortes de admin con remedio corregido por
terceros (spec A.2). Puros."""
import logging
import smtplib
from pathlib import Path

import pytest

import smtp_config
from api.admin import keys, models

BACKEND = Path(__file__).resolve().parent.parent
REGISTRO = logging.getLogger("prueba.smtp")


# A-39: solo las ramas comunes. ValueError queda en cada sitio (en smtp.py el
# ValueError de construir_mensaje va en otro try; en users.py, entre las dos).
def test_unicode_encode_error_es_502_y_nunca_loguea_la_excepcion(caplog):
    exc = UnicodeEncodeError("ascii", "clave-ñ-secreta", 6, 7, "ordinal not in range(128)")
    with caplog.at_level(logging.WARNING, logger="prueba.smtp"):
        http = smtp_config.http_de_fallo_de_envio(exc, REGISTRO, "Correo de prueba SMTP", "a@b.c")
    assert (http.status_code, http.detail) == (502, {"code": "smtp_password_no_ascii", "server": ""})
    assert "clave-ñ-secreta" not in caplog.text
    assert [r.getMessage() for r in caplog.records] == [
        "Correo de prueba SMTP a a@b.c: la contraseña SMTP guardada no es ASCII (AUTH)"]


@pytest.mark.parametrize("exc", [OSError("conexion rechazada"), smtplib.SMTPException("550 rechazado")])
def test_oserror_y_smtpexception_son_502_con_la_respuesta_del_servidor(caplog, exc):
    with caplog.at_level(logging.WARNING, logger="prueba.smtp"):
        http = smtp_config.http_de_fallo_de_envio(exc, REGISTRO, "Enlace de recuperación (admin)", "x@y.z")
    assert (http.status_code, http.detail) == (502, {"code": "smtp_envio_fallido", "server": str(exc)})
    assert [r.getMessage() for r in caplog.records] == [f"Enlace de recuperación (admin) a x@y.z falló: {exc}"]


def test_otra_excepcion_no_se_traduce_en_silencio():
    with pytest.raises(TypeError):
        smtp_config.http_de_fallo_de_envio(ValueError("x"), REGISTRO, "c", "d")


# A-46
def test_el_usuario_de_las_llaves_legadas_tiene_nombre():
    assert keys.USUARIO_LLAVES_LEGADO == 1
    fuente = (BACKEND / "api/admin/keys.py").read_text(encoding="utf-8")
    assert "user_id=1" not in fuente
    assert "user_id = 1" not in fuente
    assert "VALUES (1," not in fuente


# A-25
def test_las_columnas_del_catalogo_son_una_tupla_y_no_cambian():
    assert isinstance(models._MODEL_FIELDS, tuple)
    assert models._MODEL_FIELDS == (
        "id", "provider_id", "model_id", "is_alias", "context_window", "supports_tool_use",
        "supports_structured_output", "input_modalities", "price_input_per_1m_usd",
        "price_output_per_1m_usd", "price_cache_per_1m_usd", "release_date",
        "deprecation_date", "status", "source", "source_checked_at", "consecutive_misses",
        "max_tokens_param", "max_output_tokens",
    )
    assert models._MODEL_COLUMNS == ", ".join(models._MODEL_FIELDS)
    assert "incidente thot" in (BACKEND / "api/admin/models.py").read_text(encoding="utf-8")


# A-47: desde T6-2 la key de Gemini va en la cabecera x-goog-api-key.
def test_el_comentario_de_gemini_no_dice_query_string():
    assert "query string" not in (BACKEND / "api/admin/credentials.py").read_text(encoding="utf-8")
