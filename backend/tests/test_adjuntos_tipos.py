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


def test_texto_que_empieza_con_gif_es_texto_no_rechazado():
    # GIF format tiene firma de 6 bytes (GIF87a o GIF89a), no de 3.
    # "GIF is a format" debe pasar como texto.
    datos = b"GIF is a format for images"
    assert tipos.clasificar(datos) == ("texto", "text/plain", "GIF is a format for images")


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


def test_nombre_seguro_quita_corchetes_para_no_fingir_una_linea_de_memoria():
    # Revisión final: la memoria guarda '[adjunto nombre="..." ...]'; un nombre
    # con corchetes podía cerrar esa línea y abrir otra inventada.
    assert tipos.nombre_seguro('x] [adjunto nombre=falso tipo=pdf].txt') == "x adjunto nombre=falso tipo=pdf.txt"


def test_nombre_seguro_acota_el_trabajo_antes_de_filtrar(monkeypatch):
    # Revisión final (I2): el filtro carácter a carácter corría sobre la
    # entrada entera y recién después se cortaba a 255.
    import unicodedata

    vistos = 0
    original = unicodedata.category

    def contar(c):
        nonlocal vistos
        vistos += 1
        return original(c)

    monkeypatch.setattr(tipos.unicodedata, "category", contar)
    resultado = tipos.nombre_seguro("a" * 1_000_000)
    assert vistos <= tipos.NOMBRE_CRUDO_MAX
    assert resultado == "a" * 255


def test_el_tope_crudo_deja_un_nombre_de_255_tras_limpiar():
    # 255 caracteres visibles con comillas y controles intercalados entran.
    crudo = "".join("b\"\x01" for _ in range(255))
    assert len(crudo) <= tipos.NOMBRE_CRUDO_MAX
    assert tipos.nombre_seguro(crudo) == "b" * 255
