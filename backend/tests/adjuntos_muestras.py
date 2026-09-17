"""Bytes de muestra para los tests de adjuntos (frente D, 2026-09-16).

Las imágenes son firma mágica + relleno: el servidor NO decodifica píxeles,
solo verifica la firma, así que no hace falta una imagen real. El PDF se arma
a mano con offsets de xref correctos para no depender de un escritor."""
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"relleno-png" * 4
JPEG = b"\xff\xd8\xff\xe0" + b"relleno-jpeg" * 4
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"relleno-webp" * 4
GIF = b"GIF89a" + b"relleno-gif" * 4


def pdf_con_texto(paginas: list[str]) -> bytes:
    """PDF 1.4 válido, una línea de texto ASCII (Helvetica) por página.
    Una cadena vacía da una página sin texto extraíble."""
    objetos: list[bytes] = []
    n = len(paginas)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, texto in enumerate(paginas):
        contenido = (f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
                     if texto else b"")
        objetos.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
             f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>").encode())
        objetos.append(b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream")
    salida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for numero, cuerpo in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida += f"{numero} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n".encode()
    salida += b"0000000000 65535 f \n"
    for offset in offsets:
        salida += f"{offset:010d} 00000 n \n".encode()
    salida += (f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
               f"startxref\n{inicio_xref}\n%%EOF\n").encode()
    return bytes(salida)
