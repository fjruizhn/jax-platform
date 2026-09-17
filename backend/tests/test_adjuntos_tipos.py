"""El tipo de un adjunto lo deciden los BYTES (frente D, 2026-09-16). Antes
(api/upload.py en 26c9cd5) mandaba el content_type del cliente: cualquier
cosa declarada image/* pasaba como imagen, y lo demás caía a "texto" con
decode latin-1 -- binarios incluidos. Fichas 3 y 4 del anexo A."""
import pytest

from adjuntos import tipos
from tests.adjuntos_muestras import GIF, JPEG, PNG, WEBP, pdf_con_texto


def _clasificar(tmp_path, datos, max_chars=8000):
    ruta = tmp_path / "subida"
    ruta.write_bytes(datos)
    return tipos.clasificar_archivo(ruta, max_chars)


@pytest.mark.parametrize("datos,mime", [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
def test_imagen_por_firma(tmp_path, datos, mime):
    assert _clasificar(tmp_path, datos) == tipos.Clasificacion("imagen", mime, None, False)


def test_pdf_por_firma(tmp_path):
    assert _clasificar(tmp_path, pdf_con_texto(["hola"])) == tipos.Clasificacion(
        "pdf", "application/pdf", None, False)


def test_texto_utf8_devuelve_el_texto(tmp_path):
    assert _clasificar(tmp_path, "año, café ✓".encode("utf-8")) == tipos.Clasificacion(
        "texto", "text/plain", "año, café ✓", False)


def test_svg_es_texto_nunca_imagen(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>'
    assert _clasificar(tmp_path, svg).clase == "texto"


def test_texto_que_empieza_con_gif_es_texto_no_rechazado(tmp_path):
    # GIF format tiene firma de 6 bytes (GIF87a o GIF89a), no de 3.
    # "GIF is a format" debe pasar como texto.
    datos = b"GIF is a format for images"
    assert _clasificar(tmp_path, datos).texto == "GIF is a format for images"


@pytest.mark.parametrize("datos", [GIF, b"MZ\x90\x00\x03", "cafe\xe9".encode("latin-1")])
def test_lo_no_permitido_se_rechaza(tmp_path, datos):
    with pytest.raises(tipos.TipoNoPermitido):
        _clasificar(tmp_path, datos)


def test_vacio_se_rechaza_aparte(tmp_path):
    with pytest.raises(tipos.AdjuntoVacio):
        _clasificar(tmp_path, b"")


# RD2 (2026-09-17): el texto se valida leyendo el archivo por bloques (en un
# hilo), no cargando 10 MB. Los bordes de bloque no pueden cambiar el veredicto.

def test_texto_recortado_por_caracteres_sin_cargar_todo(tmp_path):
    c = _clasificar(tmp_path, "ñandú y más".encode(), max_chars=5)
    assert (c.texto, c.recortado) == ("ñandú", True)


def test_un_caracter_multibyte_partido_entre_bloques_es_texto(tmp_path, monkeypatch):
    monkeypatch.setattr(tipos, "BLOQUE_DE_TEXTO", 7)
    texto = "abcdef" + "ñ" * 10  # la primera ñ empieza en el byte 6 y termina en el 7
    c = _clasificar(tmp_path, texto.encode())
    assert (c.clase, c.texto, c.recortado) == ("texto", texto, False)


def test_utf8_invalido_despues_del_primer_bloque_es_415(tmp_path, monkeypatch):
    monkeypatch.setattr(tipos, "BLOQUE_DE_TEXTO", 8)
    with pytest.raises(tipos.TipoNoPermitido):
        _clasificar(tmp_path, b"a" * 40 + b"\xff" + b"b" * 10, max_chars=3)


def test_nul_despues_del_recorte_igual_es_415(tmp_path, monkeypatch):
    monkeypatch.setattr(tipos, "BLOQUE_DE_TEXTO", 8)
    with pytest.raises(tipos.TipoNoPermitido):
        _clasificar(tmp_path, b"a" * 40 + b"\x00", max_chars=3)


def test_secuencia_utf8_truncada_al_final_es_415(tmp_path):
    with pytest.raises(tipos.TipoNoPermitido):
        _clasificar(tmp_path, "hola ñ".encode()[:-1])


def test_la_lectura_de_texto_va_por_bloques_acotados(tmp_path, monkeypatch):
    leidos = []
    original = tipos._leer_bloque

    def espia(f, n):
        leidos.append(n)
        return original(f, n)

    monkeypatch.setattr(tipos, "_leer_bloque", espia)
    _clasificar(tmp_path, b"x" * (3 * tipos.BLOQUE_DE_TEXTO + 5), max_chars=10)
    assert leidos and max(leidos) <= tipos.BLOQUE_DE_TEXTO


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
