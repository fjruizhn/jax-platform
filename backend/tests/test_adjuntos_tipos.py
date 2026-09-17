"""El tipo de un adjunto lo deciden los BYTES (frente D, 2026-09-16). Antes
(api/upload.py en 26c9cd5) mandaba el content_type del cliente: cualquier
cosa declarada image/* pasaba como imagen, y lo demás caía a "texto" con
decode latin-1 -- binarios incluidos. Fichas 3 y 4 del anexo A."""
import pytest

from adjuntos import tipos
from tests.adjuntos_muestras import GIF, JPEG, PNG, WEBP, pdf_con_texto


@pytest.mark.parametrize("datos,mime", [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
def test_imagen_por_firma(datos, mime):
    assert tipos.clasificar(datos) == ("imagen", mime, None)


def test_pdf_por_firma():
    clase, mime, texto = tipos.clasificar(pdf_con_texto(["hola"]))
    assert (clase, mime, texto) == ("pdf", "application/pdf", None)


def test_texto_utf8_devuelve_el_texto():
    assert tipos.clasificar("año, café ✓".encode("utf-8")) == ("texto", "text/plain", "año, café ✓")


def test_svg_es_texto_nunca_imagen():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>'
    assert tipos.clasificar(svg)[0] == "texto"


@pytest.mark.parametrize("datos", [GIF, b"MZ\x90\x00\x03", "cafe\xe9".encode("latin-1")])
def test_lo_no_permitido_se_rechaza(datos):
    with pytest.raises(tipos.TipoNoPermitido):
        tipos.clasificar(datos)


def test_vacio_se_rechaza_aparte():
    with pytest.raises(tipos.AdjuntoVacio):
        tipos.clasificar(b"")


def test_nombre_seguro_quita_rutas_control_y_comillas():
    assert tipos.nombre_seguro('C:\\x\\..\\"in\nforme".pdf') == "informe.pdf"
    assert tipos.nombre_seguro("../../etc/passwd") == "passwd"
    assert tipos.nombre_seguro(None) == ""
    assert len(tipos.nombre_seguro("a" * 400)) == 255
