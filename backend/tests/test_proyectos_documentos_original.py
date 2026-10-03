"""Ubicar el original de un documento en `proyectos/<uuid>/fuente/` (reprocesar).

Sin base de datos: solo disco (`tmp_path`). Lo que importa es que el archivo devuelto sea
el de la fila (sha256 igual) y que nunca se siga un enlace simbolico.
"""
import hashlib
import json
import os

import pytest

from proyectos_documentos import original

U = "11111111-2222-3333-4444-555555555555"
CONTENIDO = b"%PDF-1.4 el original"
SHA = hashlib.sha256(CONTENIDO).hexdigest()
PROCESADO = f"proyectos/{U}/procesado/doc1"


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "proyectos" / U / "fuente").mkdir(parents=True)
    return tmp_path


def _fuente(ws, ruta, contenido=CONTENIDO):
    destino = ws / "proyectos" / U / "fuente" / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    return destino


def _ficha(ws, origen, carpeta=PROCESADO):
    d = ws / carpeta
    d.mkdir(parents=True, exist_ok=True)
    (d / "ficha.json").write_text(json.dumps({"origen": origen, "detalle": {"razon": "x"}}))


def _buscar(ws, nombre="a.pdf", sha=SHA, carpeta=PROCESADO, bytes_=len(CONTENIDO)):
    return original.buscar_original(ws, U, sha256=sha, nombre_original=nombre, carpeta_procesado=carpeta,
                                    bytes_=bytes_)


def test_por_ficha_devuelve_la_ruta_relativa_al_workspace(ws):
    _fuente(ws, "lactovi/sub/escaneo.pdf")
    _ficha(ws, "fuente/lactovi/sub/escaneo.pdf")
    assert _buscar(ws, nombre="otro-nombre.pdf") == f"proyectos/{U}/fuente/lactovi/sub/escaneo.pdf"


def test_por_ficha_con_sha_distinto_se_rechaza(ws):
    _fuente(ws, "a.pdf", b"otro contenido")
    _ficha(ws, "fuente/a.pdf")
    assert _buscar(ws) is None


def test_ficha_con_sha_distinto_cae_a_la_busqueda_y_encuentra_el_bueno(ws):
    _fuente(ws, "malo.pdf", b"otro contenido")
    _fuente(ws, "bueno.pdf")
    _ficha(ws, "fuente/malo.pdf")
    assert _buscar(ws) == f"proyectos/{U}/fuente/bueno.pdf"


def test_por_sha_sin_ficha(ws):
    _fuente(ws, "x/y/z.pdf")
    _fuente(ws, "x/otro.pdf", b"nada que ver")
    assert _buscar(ws, carpeta=None) == f"proyectos/{U}/fuente/x/y/z.pdf"


def test_por_sha_con_varios_prefiere_el_que_se_llama_igual(ws):
    for ruta in ("a/copia1.pdf", "b/informe.pdf", "c/copia2.pdf"):
        _fuente(ws, ruta)
    assert _buscar(ws, nombre="informe.pdf", carpeta=None) == f"proyectos/{U}/fuente/b/informe.pdf"
    # el nombre original puede traer carpeta: se compara el ultimo tramo
    assert _buscar(ws, nombre="lote/informe.pdf", carpeta=None) == f"proyectos/{U}/fuente/b/informe.pdf"


def test_por_sha_sin_coincidencia_de_nombre_devuelve_alguno_con_ese_sha(ws):
    _fuente(ws, "a/uno.pdf")
    assert _buscar(ws, nombre="informe.pdf", carpeta=None) == f"proyectos/{U}/fuente/a/uno.pdf"


def test_no_encontrado(ws):
    _fuente(ws, "a.pdf", b"otro")
    assert _buscar(ws, carpeta=None) is None
    assert _buscar(ws, carpeta=f"proyectos/{U}/procesado/no-existe") is None


def test_sin_carpeta_fuente(tmp_path):
    assert _buscar(tmp_path, carpeta=None) is None


def test_symlink_a_archivo_fuera_de_fuente_se_rechaza_por_busqueda_y_por_ficha(ws, tmp_path_factory):
    fuera = tmp_path_factory.mktemp("fuera") / "real.pdf"
    fuera.write_bytes(CONTENIDO)                                   # mismo sha: solo el enlace lo delata
    enlace = ws / "proyectos" / U / "fuente" / "a.pdf"
    enlace.symlink_to(fuera)
    assert _buscar(ws, carpeta=None) is None
    _ficha(ws, "fuente/a.pdf")
    assert _buscar(ws) is None


def test_symlink_a_carpeta_fuera_de_fuente_no_se_recorre(ws, tmp_path_factory):
    fuera = tmp_path_factory.mktemp("fuera")
    (fuera / "real.pdf").write_bytes(CONTENIDO)
    (ws / "proyectos" / U / "fuente" / "carpeta").symlink_to(fuera, target_is_directory=True)
    assert _buscar(ws, carpeta=None) is None
    _ficha(ws, "fuente/carpeta/real.pdf")
    assert _buscar(ws) is None


def test_fuente_misma_es_un_enlace(ws, tmp_path_factory):
    fuera = tmp_path_factory.mktemp("fuera")
    (fuera / "a.pdf").write_bytes(CONTENIDO)
    (ws / "proyectos" / U / "fuente").rmdir()
    (ws / "proyectos" / U / "fuente").symlink_to(fuera, target_is_directory=True)
    assert _buscar(ws, carpeta=None) is None


@pytest.mark.parametrize("origen", ["../fuente/a.pdf", "fuente/../fuente/a.pdf", "entrada/a.pdf", "/etc/passwd",
                                    "fuente/", "fuente", "fuente//a.pdf", "fuente/./a.pdf", 7, None,
                                    f"proyectos/{U}/fuente/a.pdf"])
def test_origen_de_la_ficha_que_no_es_fuente_slash_ruta_se_ignora(ws, origen):
    _fuente(ws, "a.pdf", b"otro")                    # sha distinto: si lo siguiera, lo veriamos devuelto
    d = ws / PROCESADO
    d.mkdir(parents=True)
    (d / "ficha.json").write_text(json.dumps({"origen": origen}))
    assert _buscar(ws) is None


def test_ficha_ilegible_o_gigante_o_enlace_no_rompe_y_cae_a_la_busqueda(ws, tmp_path_factory):
    _fuente(ws, "a.pdf")
    d = ws / PROCESADO
    d.mkdir(parents=True)
    ficha = d / "ficha.json"
    for contenido in (b"{no es json", b"[1,2]", b"\xff\xfe", b" " * (original.FICHA_MAX_BYTES + 1)):
        ficha.write_bytes(contenido)
        assert _buscar(ws) == f"proyectos/{U}/fuente/a.pdf", contenido[:10]
    ficha.unlink()
    ficha.symlink_to(tmp_path_factory.mktemp("f") / "no.json")
    assert _buscar(ws) == f"proyectos/{U}/fuente/a.pdf"


@pytest.mark.parametrize("carpeta", [f"proyectos/otro/procesado/x", f"proyectos/{U}/entrada/x",
                                     f"proyectos/{U}/procesado", "../x", "/abs/x", f"proyectos/{U}/procesado/../x"])
def test_carpeta_procesado_que_no_es_de_este_proyecto_no_se_lee(ws, carpeta):
    _fuente(ws, "a.pdf", b"otro")
    destino = ws / carpeta if not carpeta.startswith(("/", "..")) else None
    if destino is not None:
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "ficha.json").write_text(json.dumps({"origen": "fuente/a.pdf"}))
    assert _buscar(ws, carpeta=carpeta) is None


def test_prefiltro_por_tamano_no_deja_pasar_un_archivo_de_otro_tamano(ws):
    _fuente(ws, "a.pdf")
    assert _buscar(ws, carpeta=None, bytes_=len(CONTENIDO) + 1) is None


def test_el_tope_de_archivos_visitados_corta_la_busqueda(ws, monkeypatch):
    monkeypatch.setattr(original, "TOPE_ENTRADAS", 3)
    for i in range(5):
        _fuente(ws, f"{i}.pdf", b"x" * (len(CONTENIDO) + 0))
    _fuente(ws, "z.pdf")
    assert _buscar(ws, carpeta=None) is None


def test_no_modifica_nada_en_disco(ws):
    f = _fuente(ws, "a.pdf")
    antes = os.stat(f).st_mtime_ns
    _buscar(ws, carpeta=None)
    assert os.stat(f).st_mtime_ns == antes and f.read_bytes() == CONTENIDO
