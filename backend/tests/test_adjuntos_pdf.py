"""PDF adjunto -> texto (frente D, 2026-09-16). Antes: `import pdfplumber`
sin instalar, 422 "No module named 'pdfplumber'" para todo PDF (ficha 3)."""
import io

import pytest

from adjuntos import pdf
from tests.adjuntos_muestras import pdf_con_texto


def test_extrae_el_texto_de_todas_las_paginas():
    texto, recortado = pdf.extraer_texto(pdf_con_texto(["uno", "dos"]), max_paginas=20, max_chars=8000)
    assert "uno" in texto and "dos" in texto
    assert recortado is False


def test_recorta_por_caracteres():
    texto, recortado = pdf.extraer_texto(pdf_con_texto(["abcdefghij" * 5]), max_paginas=20, max_chars=12)
    assert len(texto) == 12
    assert recortado is True


def test_no_pasa_de_max_paginas_y_lo_dice():
    texto, recortado = pdf.extraer_texto(pdf_con_texto(["p1", "p2", "p3"]), max_paginas=2, max_chars=8000)
    assert "p2" in texto and "p3" not in texto
    assert recortado is True


def test_pdf_sin_texto_extraible():
    with pytest.raises(pdf.PdfSinTexto):
        pdf.extraer_texto(pdf_con_texto(["", ""]), max_paginas=20, max_chars=8000)


def test_basura_con_firma_de_pdf_es_ilegible():
    with pytest.raises(pdf.PdfIlegible):
        pdf.extraer_texto(b"%PDF-1.4\nesto no es un pdf" * 3, max_paginas=20, max_chars=8000)


def test_pdf_cifrado_es_ilegible():
    from pypdf import PdfReader, PdfWriter

    escritor = PdfWriter()
    escritor.append(PdfReader(io.BytesIO(pdf_con_texto(["secreto"]))))
    escritor.encrypt(user_password="clave", algorithm="AES-256")
    salida = io.BytesIO()
    escritor.write(salida)
    with pytest.raises(pdf.PdfIlegible):
        pdf.extraer_texto(salida.getvalue(), max_paginas=20, max_chars=8000)
