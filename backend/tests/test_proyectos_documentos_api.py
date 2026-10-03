"""API de documentos de un proyecto (Proyectos E2a, T6, 2026-10-03).

Es el punto de entrada de archivos de clientes: cada prueba mira el codigo HTTP,
el cuerpo Y el estado en disco y en la base. El workspace es `tmp_path`
(`JAX_WORKSPACE_DIR`), nunca `~/jax-workspace`. Cada prueba arma su propio
tenant y sus propios proyectos por la API (igual que test_proyectos_api.py).
"""
import asyncio
import hashlib
import io
import json
import os
import stat
import logging
import threading
import time
import uuid
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from adjuntos import cuota
from proyectos_documentos import almacen, tipos
from proyectos_documentos import repositorio as repo
import kill_switch
from tests.identidades import auth, cabeceras, sql, token_para, uid

P = "/api/proyectos"
MIB = 1024 * 1024
ARCHIVO = "proyectos.documentos.max_bytes_archivo"
ARCHIVOS_LOTE = "proyectos.documentos.max_archivos_lote"
BYTES_LOTE = "proyectos.documentos.max_bytes_lote"


def _code(r):
    return r.json()["detail"]["code"]


def _pdf(texto="x"):
    return ("application/pdf", f"%PDF-1.4 {texto}".encode())


def _parte(nombre, contenido):
    """Una parte `archivos` del multipart."""
    if isinstance(contenido, str):
        contenido = f"%PDF-1.4 {contenido}".encode()
    return ("archivos", (nombre, contenido, "application/pdf"))


class Entorno:
    """Un tenant con un dueno que crea proyectos, y un usuario por papel."""

    def __init__(self, client):
        self.client = client
        self.tenant = f"docs-api-{uuid.uuid4().hex}"
        self.dueno = self.usuario("dueno")
        self.proyectos = []

    def usuario(self, etiqueta, tenant=None):
        return cabeceras(self.client, f"{tenant or self.tenant}-{etiqueta}", tenant_id=tenant or self.tenant)

    def _id(self, etiqueta):
        return int(uid(self.client, f"{self.tenant}-{etiqueta}", "operator", self.tenant))

    def _email(self, etiqueta):
        return self.client.portal.call(sql, "SELECT email FROM jax_users WHERE user_id=%s",
                                       (self._id(etiqueta),), True)[0][0]

    def proyecto(self, archivar=False):
        r = self.client.post(P, headers={**self.dueno, "Idempotency-Key": str(uuid.uuid4())},
                             json={"nombre": "P", "descripcion": None})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        self.proyectos.append(pid)
        if archivar:
            r = self.client.post(f"{P}/{pid}/estado", headers=self.dueno, json={"estado": "ARCHIVED"})
            assert r.status_code == 200, r.text
        return SimpleProyecto(pid, r.json()["uuid"] if archivar else self._uuid(pid))

    def _uuid(self, pid):
        return self.client.get(f"{P}/{pid}", headers=self.dueno).json()["uuid"]

    def miembro(self, proyecto, etiqueta, papel):
        h = self.usuario(etiqueta)
        r = self.client.post(f"{P}/{proyecto.id}/miembros", headers=self.dueno,
                             json={"email": self._email(etiqueta), "papel": papel})
        assert r.status_code == 201, r.text
        return h

    def subir(self, proyecto, headers, partes):
        return self.client.post(f"{P}/{proyecto.id}/documentos", headers=headers, files=partes)

    def filas(self, proyecto):
        return list(self.client.portal.call(
            sql, "SELECT id, nombre_original, ruta_entrada, bytes, tipo, estado, sha256, job_id, oculto_at "
                 "FROM project_documents WHERE project_id=%s ORDER BY id", (proyecto.id,), True))


class SimpleProyecto:
    def __init__(self, id_, uuid_):
        self.id = id_
        self.uuid = uuid_


@pytest.fixture(autouse=True)
def _sin_despachador(monkeypatch):
    """Una subida avisa al despachador, que hablaria con un LAS MANOS real y cambiaria el
    estado de las filas que estas pruebas afirman. El aviso se prueba en
    test_proyectos_documentos_despachador.py."""
    from proyectos_documentos import despachador
    monkeypatch.setattr(despachador, "despachar_ahora", lambda: None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def ent(client, workspace, ajustes_en_db):
    entorno = Entorno(client)
    # Topes iniciales de la migracion: cada prueba que los cambia los repone sola
    # (ajustes_en_db restaura lo que habia).
    yield entorno
    for pid in entorno.proyectos:
        client.portal.call(sql, "DELETE FROM project_documents WHERE project_id=%s", (pid,))


def _en_disco(workspace: Path) -> list[Path]:
    raiz = workspace / "proyectos"
    return sorted(p for p in raiz.rglob("*") if p.is_file()) if raiz.exists() else []


# ------------------------------------------------------------------ subir

def test_contributor_sube_y_queda_en_cola(ent, workspace):
    p = ent.proyecto()
    h = ent.miembro(p, "contrib", "CONTRIBUTOR")
    r = ent.subir(p, h, [_parte("a.pdf", "a"), ("archivos", ("b.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 202, r.text
    cuerpo = r.json()
    assert [a["nombre"] for a in cuerpo["aceptados"]] == ["a.pdf"]
    assert cuerpo["ignorados"] == [{"nombre": "b.exe", "motivo": "tipo_no_admitido"}]
    assert set(cuerpo) == {"lote", "aceptados", "ignorados"} and cuerpo["lote"]
    escritos = list((workspace / "proyectos" / p.uuid / "entrada").rglob("*.pdf"))
    assert len(escritos) == 1 and escritos[0].parent.name == cuerpo["lote"]
    assert escritos[0].read_bytes() == b"%PDF-1.4 a"
    assert stat.S_IMODE(escritos[0].stat().st_mode) == 0o660
    filas = ent.filas(p)
    assert len(filas) == 1
    id_, nombre, ruta, bytes_, tipo, estado, sha, job, oculto = filas[0]
    assert id_ == cuerpo["aceptados"][0]["id"] and nombre == "a.pdf" and tipo == "pdf"
    assert estado == "en_cola" and job is None and oculto is None
    assert ruta == f"proyectos/{p.uuid}/entrada/{cuerpo['lote']}/a.pdf"
    assert (workspace / ruta).read_bytes() == b"%PDF-1.4 a"
    assert bytes_ == len(b"%PDF-1.4 a") and sha == hashlib.sha256(b"%PDF-1.4 a").hexdigest()
    # el .exe no dejo nada, y jamas se toca `fuente/`
    assert [x.name for x in _en_disco(workspace)] == ["a.pdf"]
    assert not (workspace / "proyectos" / p.uuid / "fuente").exists()


def test_el_dueno_tambien_sube(ent, workspace):
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 202 and len(r.json()["aceptados"]) == 1


def test_viewer_no_sube_403_y_no_escribe(ent, workspace):
    p = ent.proyecto()
    h = ent.miembro(p, "lector", "VIEWER")
    r = ent.subir(p, h, [_parte("a.pdf", "a")])
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"
    assert _en_disco(workspace) == [] and not (workspace / "proyectos").exists()
    assert ent.filas(p) == []


def test_no_miembro_404_igual_que_inexistente(ent, workspace):
    p = ent.proyecto()
    ajeno_otro_tenant = ent.usuario("ajeno", tenant=f"otro-{uuid.uuid4().hex}")
    ajeno_mismo_tenant = ent.usuario("sin-membresia")
    inexistente = ent.client.post(f"{P}/999999999/documentos", headers=ajeno_otro_tenant,
                                  files=[_parte("a.pdf", "a")])
    assert inexistente.status_code == 404
    for h in (ajeno_otro_tenant, ajeno_mismo_tenant):
        r = ent.subir(p, h, [_parte("a.pdf", "a")])
        assert r.status_code == 404
        assert r.json() == inexistente.json() == {"detail": {"code": "proyecto_no_encontrado"}}
    assert not (workspace / "proyectos").exists() and ent.filas(p) == []


def test_sin_sesion_401(ent, workspace):
    p = ent.proyecto()
    r = ent.client.post(f"{P}/{p.id}/documentos", files=[_parte("a.pdf", "a")])
    assert r.status_code == 401 and not (workspace / "proyectos").exists()


def test_proyecto_archivado_409_y_no_escribe(ent, workspace):
    p = ent.proyecto(archivar=True)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo"
    assert not (workspace / "proyectos").exists() and ent.filas(p) == []


def test_proyecto_oculto_409_para_su_admin_y_404_para_el_resto(ent, workspace):
    admin = cabeceras(ent.client, f"{ent.tenant}-adm", role="admin", tenant_id=ent.tenant)
    r = ent.client.post(P, headers={**admin, "Idempotency-Key": str(uuid.uuid4())},
                        json={"nombre": "P", "descripcion": None})
    assert r.status_code == 201, r.text
    p = SimpleProyecto(r.json()["id"], r.json()["uuid"])
    ent.proyectos.append(p.id)
    for estado in ("ARCHIVED", "HIDDEN"):
        r = ent.client.post(f"{P}/{p.id}/estado", headers=admin, json={"estado": estado})
        assert r.status_code == 200 and r.json()["estado"] == estado, r.text
    r = ent.subir(p, admin, [_parte("a.pdf", "a")])
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo"
    assert not (workspace / "proyectos").exists() and ent.filas(p) == []


def test_viewer_en_archivado_recibe_403_no_409(ent, workspace):
    # el papel se mira antes que el estado: un lector no aprende nada del estado
    p = ent.proyecto()
    h = ent.miembro(p, "lector", "VIEWER")
    assert ent.client.post(f"{P}/{p.id}/estado", headers=ent.dueno, json={"estado": "ARCHIVED"}).status_code == 200
    r = ent.subir(p, h, [_parte("a.pdf", "a")])
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"


def test_solo_tipos_no_admitidos_202_y_no_escribe_nada(ent, workspace):
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [("archivos", ("nota.txt", b"hola", "text/plain")),
                                 ("archivos", ("viejo.xls", b"x", "application/vnd.ms-excel")),
                                 ("archivos", ("sin_extension", b"x", "application/octet-stream"))])
    assert r.status_code == 202
    assert r.json()["aceptados"] == []
    assert [i["motivo"] for i in r.json()["ignorados"]] == ["tipo_no_admitido"] * 3
    assert _en_disco(workspace) == [] and ent.filas(p) == []


def test_el_tipo_se_decide_por_la_extension_en_cualquier_mayuscula(ent, workspace):
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte("Informe.PDF", "1")])
    assert r.status_code == 202 and r.json()["aceptados"][0]["nombre"] == "Informe.PDF"
    assert ent.filas(p)[0][4] == "pdf"


def test_sin_archivos_422(ent, workspace):
    p = ent.proyecto()
    r = ent.client.post(f"{P}/{p.id}/documentos", headers=ent.dueno, data={"x": "1"})
    assert r.status_code == 422 and _code(r) == "datos_invalidos"


# ------------------------------------------------------------------ topes

def test_tope_real_no_el_declarado(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVO: str(MIB)})
    p = ent.proyecto()
    grande = b"%PDF" + b"0" * (MIB + 10)
    r = ent.subir(p, ent.dueno, [_parte("ok.pdf", "ok"), _parte("grande.pdf", grande)])
    assert r.status_code == 202, r.text
    assert {"nombre": "grande.pdf", "motivo": "demasiado_grande"} in r.json()["ignorados"]
    assert [a["nombre"] for a in r.json()["aceptados"]] == ["ok.pdf"]
    assert [x.name for x in _en_disco(workspace)] == ["ok.pdf"]     # sin parcial
    assert [f[1] for f in ent.filas(p)] == ["ok.pdf"]


def test_archivo_justo_en_el_tope_entra(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVO: str(MIB)})
    p = ent.proyecto()
    justo = b"%PDF" + b"0" * (MIB - 4)
    assert len(justo) == MIB
    r = ent.subir(p, ent.dueno, [_parte("justo.pdf", justo)])
    assert r.status_code == 202 and [a["nombre"] for a in r.json()["aceptados"]] == ["justo.pdf"]
    assert _en_disco(workspace)[0].stat().st_size == MIB


def test_lote_que_cruza_el_tope_de_archivos_413_conserva_lo_anterior(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVOS_LOTE: "2"})
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte("uno.pdf", "1"), _parte("dos.pdf", "2"), _parte("tres.pdf", "3")])
    assert r.status_code == 413 and _code(r) == "lote_demasiado_grande"
    detalle = r.json()["detail"]
    assert [a["nombre"] for a in detalle["aceptados"]] == ["uno.pdf", "dos.pdf"] and detalle["lote"]
    assert sorted(x.name for x in _en_disco(workspace)) == ["dos.pdf", "uno.pdf"]
    assert [f[1] for f in ent.filas(p)] == ["uno.pdf", "dos.pdf"]
    assert {f[5] for f in ent.filas(p)} == {"en_cola"}


def test_lote_que_cruza_el_tope_de_bytes_413_borra_el_parcial(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVO: str(MIB), BYTES_LOTE: str(MIB)})
    p = ent.proyecto()
    a = b"%PDF" + b"a" * (600 * 1024)
    b = b"%PDF" + b"b" * (600 * 1024)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", a), _parte("b.pdf", b), _parte("c.pdf", "c")])
    assert r.status_code == 413 and _code(r) == "lote_demasiado_grande"
    assert [x["nombre"] for x in r.json()["detail"]["aceptados"]] == ["a.pdf"]
    assert [x.name for x in _en_disco(workspace)] == ["a.pdf"]       # b sin parcial, c ni se intento
    assert [f[1] for f in ent.filas(p)] == ["a.pdf"]


def test_sin_espacio_507_y_no_escribe(ent, workspace, monkeypatch):
    p = ent.proyecto()
    pedidos = []

    async def sin_espacio(directorio, necesario):
        pedidos.append(necesario)
        raise cuota.SinEspacio()

    monkeypatch.setattr(cuota, "exigir_disco_libre", sin_espacio)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 507 and _code(r) == "sin_espacio"
    assert pedidos == [1024 * MIB]            # max_bytes_lote por defecto
    assert _en_disco(workspace) == [] and ent.filas(p) == []


# ------------------------------------------------------------- duplicados

def test_duplicado_y_duplicado_oculto(ent, workspace):
    p = ent.proyecto()
    r1 = ent.subir(p, ent.dueno, [_parte("a.pdf", "mismo")])
    doc = r1.json()["aceptados"][0]["id"]
    # mismo contenido con otro nombre, en otro lote y dentro del mismo lote
    r2 = ent.subir(p, ent.dueno, [_parte("copia.pdf", "mismo")])
    assert r2.status_code == 202 and r2.json()["aceptados"] == []
    assert r2.json()["ignorados"] == [{"nombre": "copia.pdf", "motivo": "duplicado"}]
    r3 = ent.subir(p, ent.dueno, [_parte("n1.pdf", "otro"), _parte("n2.pdf", "otro")])
    assert [a["nombre"] for a in r3.json()["aceptados"]] == ["n1.pdf"]
    assert r3.json()["ignorados"] == [{"nombre": "n2.pdf", "motivo": "duplicado"}]
    # oculto: se dice distinto, y NO se resucita
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/ocultar", headers=ent.dueno).status_code == 204
    r4 = ent.subir(p, ent.dueno, [_parte("a_de_nuevo.pdf", "mismo")])
    assert r4.json()["aceptados"] == []
    assert r4.json()["ignorados"] == [{"nombre": "a_de_nuevo.pdf", "motivo": "duplicado_oculto"}]
    assert [f[1] for f in ent.filas(p)] == ["a.pdf", "n1.pdf"]
    assert ent.filas(p)[0][8] is not None                           # sigue oculto
    assert sorted(x.name for x in _en_disco(workspace)) == ["a.pdf", "n1.pdf"]   # los duplicados no dejaron archivo


def test_el_mismo_contenido_en_otro_proyecto_no_es_duplicado(ent, workspace):
    p1, p2 = ent.proyecto(), ent.proyecto()
    assert ent.subir(p1, ent.dueno, [_parte("a.pdf", "x")]).json()["aceptados"]
    r = ent.subir(p2, ent.dueno, [_parte("a.pdf", "x")])
    assert [a["nombre"] for a in r.json()["aceptados"]] == ["a.pdf"] and r.json()["ignorados"] == []


def test_duplicado_concurrente_una_sola_fila(ent, workspace, monkeypatch):
    """Dos POST simultaneos con el mismo contenido. La barrera deja a los dos
    llegar a `insertar` antes de que ninguno inserte: asi ningun SELECT previo
    puede haber decidido, y quien decide es la restriccion unica de la base."""
    p = ent.proyecto()
    resultados = []
    barrera = []
    original = repo.insertar

    async def insertar_en_pareja(*args, **kwargs):
        if not barrera:
            barrera.append(asyncio.Barrier(2))
        await asyncio.wait_for(barrera[0].wait(), 15)
        resultado = await original(*args, **kwargs)
        resultados.append(resultado)
        return resultado

    monkeypatch.setattr(repo, "insertar", insertar_en_pareja)

    def sube(nombre):
        return ent.subir(p, ent.dueno, [_parte(nombre, "contenido identico")])

    with ThreadPoolExecutor(2) as pool:
        r1, r2 = list(pool.map(sube, ["uno.pdf", "dos.pdf"]))
    assert r1.status_code == r2.status_code == 202
    aceptados = r1.json()["aceptados"] + r2.json()["aceptados"]
    ignorados = r1.json()["ignorados"] + r2.json()["ignorados"]
    assert len(aceptados) == 1 and len(ignorados) == 1 and ignorados[0]["motivo"] == "duplicado"
    assert len(resultados) == 2 and sorted(x is None for x in resultados) == [False, True]   # el INSERT perdedor chocó con la unica
    assert len(ent.filas(p)) == 1
    assert len(_en_disco(workspace)) == 1                           # el perdedor borro su copia


# ----------------------------------------------------------------- nombres

def test_nombres_hostiles(ent, workspace):
    p = ent.proyecto()
    nombres = ["../../etc/passwd.pdf", "con\x00nul.pdf", "x" * 600 + ".pdf", "🙂🙂.pdf",
               "Informe.PDF", "informe.pdf", "..\\..\\win.pdf", "/abs/ruta.pdf", ".pdf.pdf"]
    partes = [("archivos", (n, f"%PDF {i}".encode(), "application/pdf")) for i, n in enumerate(nombres)]
    r = ent.client.post(f"{P}/{p.id}/documentos", headers=ent.dueno, files=partes)
    assert r.status_code == 202, r.text
    raiz = (workspace / "proyectos" / p.uuid / "entrada").resolve()
    for q in (workspace / "proyectos").rglob("*"):
        assert q.resolve().is_relative_to(raiz) or q.is_dir()
    en_disco = [q.name for q in raiz.rglob("*") if q.is_file()]
    assert len(en_disco) == len(set(n.lower() for n in en_disco))       # unicos sin distinguir mayusculas
    assert len(en_disco) == len(r.json()["aceptados"]) == len(ent.filas(p))
    # nada escapo de entrada/<lote>/
    assert not (workspace / "etc").exists() and not (workspace.parent / "etc").exists()
    for q in raiz.rglob("*"):
        if q.is_file():
            assert q.parent.parent == raiz              # exactamente entrada/<lote>/<archivo>
    for nombre in en_disco:
        assert len(nombre.encode()) <= 255 and "/" not in nombre and "\x00" not in nombre
    assert "passwd.pdf" in en_disco and "win.pdf" in en_disco and "ruta.pdf" in en_disco
    assert "Informe.PDF" in en_disco and "informe (2).pdf" in en_disco and "pdf.pdf" in en_disco
    # la fila guarda una ruta relativa al workspace dentro de entrada/<lote>/
    for f in ent.filas(p):
        assert f[2].startswith(f"proyectos/{p.uuid}/entrada/{r.json()['lote']}/") and ".." not in f[2].split("/")
        assert (workspace / f[2]).is_file()
    # `nombre_original` es el nombre ORIGINAL como texto (NFC, sin controles, <= 1024): el
    # de disco es otro. Nada se recorta a la baja ni se aplana a un componente.
    guardados = sorted(f[1] for f in ent.filas(p))
    # (httpx escapa los controles del nombre como %XX antes de enviarlo: el NUL llega "%00".)
    esperados = sorted(almacen.nombre_para_mostrar(n.replace("\x00", "%00")) for n in nombres)
    assert guardados == esperados
    assert "../../etc/passwd.pdf" in guardados and "x" * 600 + ".pdf" in guardados and "/abs/ruta.pdf" in guardados
    assert "con%00nul.pdf" in guardados and not any("\x00" in g for g in guardados)
    assert sorted(a["nombre"] for a in r.json()["aceptados"]) == guardados


def test_nombre_para_mostrar_unitario():
    assert almacen.nombre_para_mostrar("con\x00nul\x07\u200b.pdf") == "connul.pdf"           # controles y de formato fuera
    assert almacen.nombre_para_mostrar("e\u0301.pdf") == "\u00e9.pdf"                        # NFC
    assert almacen.nombre_para_mostrar("a/b\\c.pdf") == "a/b\\c.pdf"                        # la ruta relativa se conserva como texto
    assert almacen.nombre_para_mostrar("z" * 2000) == "z" * 1024
    assert almacen.nombre_para_mostrar(None) == ""


def test_nombre_original_con_carpeta_y_recortado_a_1024(ent, workspace):
    p = ent.proyecto()
    largo = "y" * 1100 + ".pdf"
    r = ent.subir(p, ent.dueno, [_parte("Cliente/Contratos/2026/e\u0301.pdf", "1"), _parte(largo, "2")])
    assert r.status_code == 202, r.text
    por_nombre = {f[1]: f for f in ent.filas(p)}
    assert set(por_nombre) == {"Cliente/Contratos/2026/\u00e9.pdf", largo[:1024]}      # ruta relativa conservada, NFC, 1024
    carpeta = por_nombre["Cliente/Contratos/2026/\u00e9.pdf"][2]
    assert carpeta.endswith("/\u00e9.pdf") and carpeta.count("/") == 4                 # en disco: un solo componente
    assert por_nombre[largo[:1024]][2].rsplit("/", 1)[1] == ("y" * 196) + ".pdf"        # el seguro, a 200


def test_misma_carpeta_nombres_repetidos_se_numeran_sin_pisarse(ent, workspace):
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "1"), _parte("A.PDF", "2"), _parte("a.pdf", "3")])
    assert r.status_code == 202 and len(r.json()["aceptados"]) == 3
    nombres = sorted(x.name.lower() for x in _en_disco(workspace))
    assert nombres == ["a (2).pdf", "a (3).pdf", "a.pdf"]
    contenidos = {x.read_bytes() for x in _en_disco(workspace)}
    assert contenidos == {b"%PDF-1.4 1", b"%PDF-1.4 2", b"%PDF-1.4 3"}      # ninguno piso a otro


def test_nombre_seguro_unitario():
    assert almacen.nombre_seguro("../../etc/passwd.pdf", set()) == "passwd.pdf"
    assert almacen.nombre_seguro("a\\b\\c.pdf", set()) == "c.pdf"
    assert almacen.nombre_seguro("con\x00nul\x07.pdf", set()) == "connul.pdf"
    assert almacen.nombre_seguro("é.pdf", set()) == "é.pdf"                 # NFC
    assert almacen.nombre_seguro(".pdf", set()) == "documento.pdf"
    assert almacen.nombre_seguro("\x00.pdf", set()) == "documento.pdf"
    assert almacen.nombre_seguro("   .PDF", set()) == "documento.PDF"
    larga = almacen.nombre_seguro("x" * 600 + ".pdf", set())
    assert len(larga) == 200 and larga.endswith(".pdf")
    emojis = almacen.nombre_seguro("🙂" * 300 + ".pdf", set())
    assert len(emojis.encode()) <= 200 and emojis.endswith(".pdf")                  # el limite de bytes del sistema de archivos
    assert almacen.nombre_seguro("a.pdf", {"A.PDF"}) == "a (2).pdf"
    assert almacen.nombre_seguro("a.pdf", {"a.pdf", "a (2).pdf"}) == "a (3).pdf"
    assert almacen.nombre_seguro("a.pdf", {"b.pdf"}) == "a.pdf"


def test_carpeta_entrada_solo_arma_rutas_dentro_de_entrada(tmp_path):
    u = str(uuid.uuid4())
    assert almacen.carpeta_entrada(tmp_path, u, "abc-DEF_123") == tmp_path / "proyectos" / u / "entrada" / "abc-DEF_123"
    for malo in ("..", "../x", "a/b", "", ".", "a\x00b", "x" * 200):
        with pytest.raises(ValueError):
            almacen.carpeta_entrada(tmp_path, u, malo)
    for malo in ("..", "a/b", "", "../fuera"):
        with pytest.raises(ValueError):
            almacen.carpeta_entrada(tmp_path, malo, "lote")


class _Subida:
    """Lo minimo de un UploadFile que lee `escribir_streaming`: su `.file`."""

    def __init__(self, contenido: bytes):
        self.file = io.BytesIO(contenido)


async def test_escribir_streaming_cuenta_lo_leido_y_borra_el_parcial(tmp_path):
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "l1")
    try:
        destino = carpeta.ruta / "ok.bin"
        total, sha = await almacen.escribir_streaming(_Subida(b"a" * (2 * MIB + 5)), carpeta, "ok.bin", 3 * MIB)
        assert total == 2 * MIB + 5 and sha == hashlib.sha256(b"a" * (2 * MIB + 5)).hexdigest()
        assert destino.stat().st_size == total and stat.S_IMODE(destino.stat().st_mode) == 0o660
        with pytest.raises(almacen.DemasiadoGrande):
            await almacen.escribir_streaming(_Subida(b"a" * (3 * MIB + 1)), carpeta, "grande.bin", 3 * MIB)
        assert not (carpeta.ruta / "grande.bin").exists()
        # no lee mucho mas alla del tope: corta en cuanto lo cruza
        leido = []

        class Contador(io.BytesIO):
            def read(self, n=-1):
                bloque = super().read(n)
                leido.append(len(bloque))
                return bloque

        sub = _Subida(b"")
        sub.file = Contador(b"a" * (50 * MIB))
        with pytest.raises(almacen.DemasiadoGrande):
            await almacen.escribir_streaming(sub, carpeta, "otro.bin", MIB)
        assert sum(leido) <= 2 * MIB and not (carpeta.ruta / "otro.bin").exists()
        # nunca pisa un archivo que ya existe
        with pytest.raises(FileExistsError):
            await almacen.escribir_streaming(_Subida(b"x"), carpeta, "ok.bin", MIB)
        assert destino.stat().st_size == total
        # ni sigue un enlace puesto en el lugar del archivo
        fuera = tmp_path / "fuera.txt"
        os.symlink(fuera, carpeta.ruta / "enlace.pdf")
        with pytest.raises(OSError):
            await almacen.escribir_streaming(_Subida(b"x"), carpeta, "enlace.pdf", MIB)
        assert not fuera.exists()
    finally:
        carpeta.cerrar()


async def test_cancelacion_antes_de_abrir_no_deja_archivo_huerfano(tmp_path, monkeypatch):
    """La cancelacion llega mientras el hilo todavia no hizo `os.open`: cuando el hilo
    sigue, la bandera lo detiene y, de todos modos, el archivo no puede quedar."""
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "l1")
    try:
        entro, compuerta = threading.Event(), threading.Event()
        original = almacen._crear

        def lenta(fd, nombre):
            entro.set()
            assert compuerta.wait(10)
            return original(fd, nombre)

        monkeypatch.setattr(almacen, "_crear", lenta)
        tarea = asyncio.create_task(almacen.escribir_streaming(_Subida(b"x" * 100), carpeta, "a.pdf", MIB))
        assert await asyncio.to_thread(entro.wait, 10)
        tarea.cancel()
        asyncio.get_running_loop().call_later(0.2, compuerta.set)      # el hilo sigue DESPUES de cancelar
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert list(carpeta.ruta.iterdir()) == []
        assert threading.active_count() >= 1
    finally:
        carpeta.cerrar()


async def test_cancelacion_a_mitad_de_la_escritura_borra_el_parcial(tmp_path):
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "l1")
    try:
        leyendo, compuerta = threading.Event(), threading.Event()

        class Lenta(io.BytesIO):
            def __init__(self):
                super().__init__(b"a" * (3 * MIB))
                self.bloques = 0

            def read(self, n=-1):
                self.bloques += 1
                if self.bloques == 2:
                    leyendo.set()
                    assert compuerta.wait(10)
                return super().read(n)

        sub = _Subida(b"")
        sub.file = Lenta()
        tarea = asyncio.create_task(almacen.escribir_streaming(sub, carpeta, "a.pdf", 10 * MIB))
        assert await asyncio.to_thread(leyendo.wait, 10)
        assert (carpeta.ruta / "a.pdf").exists()                      # ya hay un parcial en disco
        tarea.cancel()
        asyncio.get_running_loop().call_later(0.2, compuerta.set)
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert list(carpeta.ruta.iterdir()) == []
    finally:
        carpeta.cerrar()


# ------------------------------------------------------------------ listar

def test_listar_visibles_y_paginar(ent, workspace):
    p = ent.proyecto()
    h = ent.miembro(p, "lector", "VIEWER")
    ent.subir(p, ent.dueno, [_parte(f"d{i}.pdf", str(i)) for i in range(5)])
    r = ent.client.get(f"{P}/{p.id}/documentos?limite=2", headers=h)
    assert r.status_code == 200
    cuerpo = r.json()
    assert [d["nombre"] for d in cuerpo["documentos"]] == ["d4.pdf", "d3.pdf"]
    assert cuerpo["siguiente"] == cuerpo["documentos"][-1]["id"]
    assert {"id", "nombre", "bytes", "tipo", "estado", "error", "subido_por_email", "creado", "oculto"} <= set(cuerpo["documentos"][0])
    assert cuerpo["documentos"][0]["estado"] == "en_cola" and cuerpo["documentos"][0]["oculto"] is False
    r = ent.client.get(f"{P}/{p.id}/documentos?limite=2&antes_de={cuerpo['siguiente']}", headers=h)
    assert [d["nombre"] for d in r.json()["documentos"]] == ["d2.pdf", "d1.pdf"]
    r = ent.client.get(f"{P}/{p.id}/documentos?limite=2&antes_de={r.json()['siguiente']}", headers=h)
    assert [d["nombre"] for d in r.json()["documentos"]] == ["d0.pdf"] and r.json()["siguiente"] is None


def test_listar_validaciones_y_visibilidad(ent, workspace):
    p = ent.proyecto()
    ajeno = ent.usuario("ajeno", tenant=f"otro-{uuid.uuid4().hex}")
    r = ent.client.get(f"{P}/{p.id}/documentos", headers=ajeno)
    assert r.status_code == 404 and _code(r) == "proyecto_no_encontrado"
    for query in ("limite=0", "limite=101", "vista=otra", "antes_de=0", "antes_de=abc"):
        r = ent.client.get(f"{P}/{p.id}/documentos?{query}", headers=ent.dueno)
        assert r.status_code == 422 and _code(r) == "datos_invalidos", query
    assert ent.client.get(f"{P}/{p.id}/documentos").status_code == 401


def test_listar_en_proyecto_archivado_se_puede(ent, workspace):
    p = ent.proyecto()
    ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert ent.client.post(f"{P}/{p.id}/estado", headers=ent.dueno, json={"estado": "ARCHIVED"}).status_code == 200
    r = ent.client.get(f"{P}/{p.id}/documentos", headers=ent.dueno)
    assert r.status_code == 200 and [d["nombre"] for d in r.json()["documentos"]] == ["a.pdf"]


# --------------------------------------------------- ocultar y restaurar

def test_ocultar_restaurar_y_vista_ocultos(ent, workspace):
    p = ent.proyecto()
    contrib = ent.miembro(p, "contrib", "CONTRIBUTOR")
    lector = ent.miembro(p, "lector", "VIEWER")
    r = ent.subir(p, contrib, [_parte("a.pdf", "a"), _parte("b.pdf", "b")])
    a, b = (x["id"] for x in r.json()["aceptados"])
    base = f"{P}/{p.id}/documentos"

    # el lector no oculta ni restaura: 403 y la base queda igual
    for accion in ("ocultar", "restaurar"):
        r = ent.client.post(f"{base}/{a}/{accion}", headers=lector)
        assert r.status_code == 403 and _code(r) == "papel_insuficiente"
    assert all(f[8] is None for f in ent.filas(p))

    r = ent.client.post(f"{base}/{a}/ocultar", headers=contrib)
    assert r.status_code == 204 and r.content == b""
    assert [f[0] for f in ent.filas(p) if f[8] is not None] == [a]
    # idempotente
    assert ent.client.post(f"{base}/{a}/ocultar", headers=contrib).status_code == 204

    visibles = ent.client.get(base, headers=lector).json()["documentos"]
    assert [d["id"] for d in visibles] == [b]
    ocultos = ent.client.get(f"{base}?vista=ocultos", headers=contrib).json()["documentos"]
    assert [d["id"] for d in ocultos] == [a] and ocultos[0]["oculto"] is True
    # ocultos exige CONTRIBUTOR
    r = ent.client.get(f"{base}?vista=ocultos", headers=lector)
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"
    # y ocultar no toca el archivo en disco ni el estado de procesamiento
    assert len(_en_disco(workspace)) == 2 and {f[5] for f in ent.filas(p)} == {"en_cola"}

    r = ent.client.post(f"{base}/{a}/restaurar", headers=contrib)
    assert r.status_code == 204
    assert all(f[8] is None for f in ent.filas(p))
    assert ent.client.post(f"{base}/{a}/restaurar", headers=contrib).status_code == 204     # idempotente
    assert [d["id"] for d in ent.client.get(base, headers=lector).json()["documentos"]] == [b, a]
    assert ent.client.get(f"{base}?vista=ocultos", headers=contrib).json()["documentos"] == []


def test_ocultar_documento_de_otro_proyecto_o_inexistente_404(ent, workspace):
    p1, p2 = ent.proyecto(), ent.proyecto()
    a = ent.subir(p1, ent.dueno, [_parte("a.pdf", "a")]).json()["aceptados"][0]["id"]
    for accion in ("ocultar", "restaurar"):
        r = ent.client.post(f"{P}/{p2.id}/documentos/{a}/{accion}", headers=ent.dueno)
        assert r.status_code == 404 and _code(r) == "documento_no_encontrado"
        r = ent.client.post(f"{P}/{p1.id}/documentos/999999999/{accion}", headers=ent.dueno)
        assert r.status_code == 404 and _code(r) == "documento_no_encontrado"
    assert all(f[8] is None for f in ent.filas(p1))                  # el de p1 no se toco


def test_ocultar_de_un_no_miembro_404_de_proyecto(ent, workspace):
    p = ent.proyecto()
    a = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")]).json()["aceptados"][0]["id"]
    ajeno = ent.usuario("ajeno", tenant=f"otro-{uuid.uuid4().hex}")
    r = ent.client.post(f"{P}/{p.id}/documentos/{a}/ocultar", headers=ajeno)
    assert r.status_code == 404 and _code(r) == "proyecto_no_encontrado"
    assert ent.filas(p)[0][8] is None


# ------------------------------------------------------------------ limites

def test_limites_publica_los_ajustes_y_las_extensiones(ent, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVO: str(5 * MIB), ARCHIVOS_LOTE: "7", BYTES_LOTE: str(50 * MIB)})
    r = ent.client.get(f"{P}/documentos/limites", headers=ent.dueno)
    assert r.status_code == 200
    assert r.json() == {"max_bytes_archivo": 5 * MIB, "max_archivos_lote": 7, "max_bytes_lote": 50 * MIB,
                        "extensiones": sorted(tipos.EXTENSIONES_ACEPTADAS)}
    # cualquier usuario con sesion, sea o no miembro de algo; sin sesion, no
    assert ent.client.get(f"{P}/documentos/limites", headers=ent.usuario("nadie")).status_code == 200
    assert ent.client.get(f"{P}/documentos/limites").status_code == 401


def test_limites_con_ajuste_ilegible_falla_cerrado(ent, ajustes_en_db):
    ajustes_en_db.quitar(ARCHIVO)
    r = ent.client.get(f"{P}/documentos/limites", headers=ent.dueno)
    assert r.status_code == 503
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 503 and ent.filas(p) == []


# ------------------------------------------------------------------- modos

def test_modos_explicitos_no_dependen_del_umask(ent, workspace):
    """Bajo `proyectos/` hay ACL por defecto y setgid: un 0600 anula el acceso de
    fruiz (mascara `---`). Archivo 0660 y carpetas 2770 (con S_ISGID) aunque el umask diga otra
    cosa, en toda la cadena que crea la subida."""
    p = ent.proyecto()
    anterior = os.umask(0o077)
    try:
        r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    finally:
        os.umask(anterior)
    assert r.status_code == 202, r.text
    archivo = _en_disco(workspace)[0]
    assert stat.S_IMODE(archivo.stat().st_mode) == 0o660
    for carpeta in (archivo.parent, archivo.parent.parent, archivo.parent.parent.parent,
                    archivo.parent.parent.parent.parent):
        assert stat.S_IMODE(carpeta.stat().st_mode) == 0o2770, carpeta        # 0770 + setgid heredable
    assert archivo.parent.parent.parent.parent == workspace / "proyectos"


def test_carpeta_existente_no_se_toca(tmp_path):
    """Solo se fija el modo de lo que se crea: una carpeta previa conserva el suyo."""
    previa = tmp_path / "proyectos"
    previa.mkdir(mode=0o750)
    os.chmod(previa, 0o750)
    u = str(uuid.uuid4())
    c1 = almacen.abrir_carpeta_lote(tmp_path, u, "lote1")
    assert stat.S_IMODE(previa.stat().st_mode) == 0o750
    assert stat.S_IMODE((previa / u / "entrada" / "lote1").stat().st_mode) == 0o2770
    c2 = almacen.abrir_carpeta_lote(tmp_path, u, "lote2")                 # idempotente sobre lo ya creado
    assert sorted(x.name for x in (previa / u / "entrada").iterdir()) == ["lote1", "lote2"]
    c1.cerrar()
    c2.cerrar()


# --------------------------------------------- enlaces simbolicos en el camino

def _foto(raiz: Path) -> list[str]:
    """Todo lo que cuelga de `raiz`, sin seguir enlaces (los enlaces cuentan como entradas)."""
    visto = []
    for base, carpetas, archivos in os.walk(raiz, followlinks=False):
        for nombre in [*carpetas, *archivos]:
            visto.append(os.path.relpath(os.path.join(base, nombre), raiz))
    return sorted(visto)


@pytest.mark.parametrize("destino", ["fuera", "hermano"])
@pytest.mark.parametrize("nivel", ["proyectos", "uuid", "entrada", "lote"])
def test_enlace_simbolico_en_el_camino_no_escribe_en_ningun_lado(ent, tmp_path, monkeypatch, nivel, destino):
    """El camino de MAJOR-1: `proyectos/U/entrada -> proyectos/V/entrada` hacia un
    hermano, o hacia fuera del workspace. Ningun nivel se sigue: respuesta con codigo
    estable, sin la ruta interna, y ni el destino del enlace ni nada mas recibe escrituras."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(ws))
    monkeypatch.setattr("api.proyectos_documentos._nuevo_lote", lambda: "loteFijo")
    p = ent.proyecto()
    objetivo = (tmp_path / "fuera") if destino == "fuera" else (ws / "hermano")
    objetivo.mkdir()
    ruta = {"proyectos": ws / "proyectos", "uuid": ws / "proyectos" / p.uuid,
            "entrada": ws / "proyectos" / p.uuid / "entrada",
            "lote": ws / "proyectos" / p.uuid / "entrada" / "loteFijo"}
    if nivel != "proyectos":                                         # los niveles de arriba, reales
        ruta["proyectos"].mkdir()
    if nivel in ("entrada", "lote"):
        ruta["uuid"].mkdir()
    if nivel == "lote":
        ruta["entrada"].mkdir()
    os.symlink(objetivo, ruta[nivel])
    antes_ws, antes_fuera = _foto(ws), _foto(tmp_path / "fuera") if (tmp_path / "fuera").exists() else []

    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 500 and r.json() == {"detail": {"code": "almacen_ruta_insegura"}}
    assert str(tmp_path) not in r.text and p.uuid not in r.text
    assert _foto(objetivo) == []                                      # ni en V ni afuera
    assert _foto(ws) == antes_ws                                      # sin residuos: ni carpetas nuevas
    assert (tmp_path / "fuera").exists() is (destino == "fuera") and _foto(tmp_path / "fuera") == antes_fuera
    assert ruta[nivel].is_symlink()                                   # el enlace sigue intacto
    assert ent.filas(p) == []


def test_enlace_a_otro_proyecto_no_escribe_en_el_otro(ent, workspace):
    """El escenario literal del revisor: `proyectos/U/entrada -> proyectos/V/entrada`."""
    u, v = ent.proyecto(), ent.proyecto()
    (workspace / "proyectos" / v.uuid / "entrada").mkdir(parents=True)
    (workspace / "proyectos" / u.uuid).mkdir(parents=True)
    os.symlink(workspace / "proyectos" / v.uuid / "entrada", workspace / "proyectos" / u.uuid / "entrada")
    r = ent.subir(u, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 500 and _code(r) == "almacen_ruta_insegura"
    assert _foto(workspace / "proyectos" / v.uuid) == ["entrada"]
    assert ent.filas(u) == [] and ent.filas(v) == []


# ------------------------------------------------------------------- freno

def test_con_el_freno_puesto_subir_ocultar_y_restaurar_responden_423(client, usuarios, ent, workspace):
    p = ent.proyecto()
    a = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")]).json()["aceptados"][0]["id"]
    antes = _en_disco(workspace)
    admin_id, _ = usuarios(role="superadmin")
    admin = auth(token_para(admin_id, role="superadmin"))
    assert client.post("/api/admin/kill-switch/activar", headers=admin).status_code == 200
    try:
        r = ent.subir(p, ent.dueno, [_parte("nuevo.pdf", "nuevo")])
        assert (r.status_code, r.json().get("detail")) == (423, "kill_switch_activo")
        for accion in ("ocultar", "restaurar"):
            r = client.post(f"{P}/{p.id}/documentos/{a}/{accion}", headers=ent.dueno)
            assert (r.status_code, r.json().get("detail")) == (423, "kill_switch_activo"), accion
        # un anonimo sigue recibiendo 401, no el estado del freno
        assert client.post(f"{P}/{p.id}/documentos", files=[_parte("x.pdf", "x")]).status_code == 401
        # las lecturas no se frenan
        assert client.get(f"{P}/{p.id}/documentos", headers=ent.dueno).status_code == 200
        assert client.get(f"{P}/documentos/limites", headers=ent.dueno).status_code == 200
        assert _en_disco(workspace) == antes and len(ent.filas(p)) == 1 and ent.filas(p)[0][8] is None
    finally:
        assert client.post("/api/admin/kill-switch/reanudar", headers=admin).status_code == 200
    r = ent.subir(p, ent.dueno, [_parte("nuevo.pdf", "nuevo")])
    assert r.status_code == 202 and len(r.json()["aceptados"]) == 1


# ---------------------------------------------- ocultar exige proyecto ACTIVE

def test_ocultar_y_restaurar_en_proyecto_archivado_409(ent, workspace):
    p = ent.proyecto()
    lector = ent.miembro(p, "lector", "VIEWER")
    a, b = (x["id"] for x in ent.subir(p, ent.dueno, [_parte("a.pdf", "a"), _parte("b.pdf", "b")]).json()["aceptados"])
    assert ent.client.post(f"{P}/{p.id}/documentos/{b}/ocultar", headers=ent.dueno).status_code == 204
    assert ent.client.post(f"{P}/{p.id}/estado", headers=ent.dueno, json={"estado": "ARCHIVED"}).status_code == 200
    antes = ent.filas(p)
    for accion, doc in (("ocultar", a), ("restaurar", b)):
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/{accion}", headers=ent.dueno)
        assert r.status_code == 409 and _code(r) == "proyecto_no_activo", accion
        # el papel se mira antes que el estado
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/{accion}", headers=lector)
        assert r.status_code == 403 and _code(r) == "papel_insuficiente", accion
    assert ent.filas(p) == antes                                     # ni a se oculto ni b se restauro
    # volver a ACTIVE los habilita de nuevo
    assert ent.client.post(f"{P}/{p.id}/estado", headers=ent.dueno, json={"estado": "ACTIVE"}).status_code == 200
    assert ent.client.post(f"{P}/{p.id}/documentos/{a}/ocultar", headers=ent.dueno).status_code == 204


# ------------------------- lo que cambia mientras se recibe el cuerpo (MAJOR-2)

def _despues_de_leer_el_cuerpo(monkeypatch, accion):
    """Corre `accion` (sincrona, con el cliente de prueba) justo despues de que Starlette
    termina de leer el multipart y antes de que la ruta toque el disco."""
    from starlette.requests import Request

    original = Request.form

    def form(self, *args, **kwargs):
        async def corre():
            resultado = await original(self, *args, **kwargs)
            await asyncio.to_thread(accion)
            return resultado
        return corre()

    monkeypatch.setattr(Request, "form", form)


def _nada(ent, p, workspace):
    assert not (workspace / "proyectos").exists() and _en_disco(workspace) == []
    assert ent.filas(p) == []


def test_archivado_mientras_se_recibe_el_cuerpo_409_y_no_queda_nada(ent, workspace, monkeypatch):
    p = ent.proyecto()
    _despues_de_leer_el_cuerpo(monkeypatch, lambda: ent.client.post(
        f"{P}/{p.id}/estado", headers=ent.dueno, json={"estado": "ARCHIVED"}))
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo"
    _nada(ent, p, workspace)


def test_miembro_quitado_mientras_se_recibe_el_cuerpo_404_y_no_queda_nada(ent, workspace, monkeypatch):
    p = ent.proyecto()
    contrib = ent.miembro(p, "contrib", "CONTRIBUTOR")
    _despues_de_leer_el_cuerpo(monkeypatch, lambda: ent.client.delete(
        f"{P}/{p.id}/miembros/{ent._id('contrib')}", headers=ent.dueno))
    r = ent.subir(p, contrib, [_parte("a.pdf", "a")])
    assert r.status_code == 404 and _code(r) == "proyecto_no_encontrado"
    _nada(ent, p, workspace)


def test_freno_puesto_mientras_se_recibe_el_cuerpo_423_y_no_queda_nada(client, usuarios, ent, workspace, monkeypatch):
    p = ent.proyecto()
    admin_id, _ = usuarios(role="superadmin")
    admin = auth(token_para(admin_id, role="superadmin"))
    _despues_de_leer_el_cuerpo(monkeypatch, lambda: client.post("/api/admin/kill-switch/activar", headers=admin))
    try:
        r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    finally:
        assert client.post("/api/admin/kill-switch/reanudar", headers=admin).status_code == 200
    assert (r.status_code, r.json().get("detail")) == (423, "kill_switch_activo")
    _nada(ent, p, workspace)


def test_archivado_despues_de_escribir_el_insert_lo_rechaza_y_se_borra_el_archivo(ent, workspace, monkeypatch):
    """La ventana que la re-comprobacion no cubre: el proyecto se archiva ENTRE la
    escritura y el INSERT. Decide el propio INSERT (solo inserta si sigue ACTIVE)."""
    p = ent.proyecto()
    original = almacen.escribir_streaming

    async def y_se_archiva(*args, **kwargs):
        resultado = await original(*args, **kwargs)
        r = await asyncio.to_thread(ent.client.post, f"{P}/{p.id}/estado", headers=ent.dueno,
                                    json={"estado": "ARCHIVED"})
        assert r.status_code == 200
        return resultado

    monkeypatch.setattr(almacen, "escribir_streaming", y_se_archiva)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a"), _parte("b.pdf", "b")])
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo"
    assert r.json()["detail"]["aceptados"] == []
    assert _en_disco(workspace) == [] and ent.filas(p) == []


def test_puede_escribir_sale_de_la_capacidad_de_b9():
    from api import proyectos_documentos as api_docs

    assert [api_docs._puede_escribir(x) for x in ("VIEWER", "CONTRIBUTOR", "REVIEWER", "OWNER")] == [False, True, True, True]
    assert not hasattr(api_docs, "_ESCRIBEN")                         # ninguna segunda tabla de papeles


# --------------------------------------------- exceso de archivos del multipart

def test_mas_archivos_que_el_tope_mas_uno_413_y_no_escribe(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVOS_LOTE: "2"})
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte(f"d{i}.pdf", str(i)) for i in range(5)])
    assert r.status_code == 413 and _code(r) == "lote_demasiado_grande"
    assert r.json()["detail"]["aceptados"] == []
    _nada(ent, p, workspace)


# ---------------------------------------- duplicados descartados no ocupan lote

def test_el_duplicado_descartado_no_cuenta_en_los_bytes_del_lote(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{ARCHIVO: str(MIB), BYTES_LOTE: str(MIB)})
    p = ent.proyecto()
    trozo = lambda letra: b"%PDF" + letra * (300 * 1024)               # 300 KiB cada uno
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", trozo(b"a")), _parte("a2.pdf", trozo(b"a")),
                                 _parte("c.pdf", trozo(b"c")), _parte("d.pdf", trozo(b"d"))])
    assert r.status_code == 202, r.text                                # 900 KiB reales < 1 MiB
    assert [a["nombre"] for a in r.json()["aceptados"]] == ["a.pdf", "c.pdf", "d.pdf"]
    assert r.json()["ignorados"] == [{"nombre": "a2.pdf", "motivo": "duplicado"}]


# ------------------------------------------------------- cancelacion (ronda 2)

async def test_cancelacion_despues_de_la_ultima_mirada_del_hilo_no_deja_huerfano(tmp_path, monkeypatch):
    """El caso del revisor: la cancelacion llega DESPUES de la ultima comprobacion de la
    bandera (`hexdigest` es lo ultimo que corre) y antes de que el resultado vuelva al
    loop. El hilo termina bien, y el archivo no puede quedar sin fila."""
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "l1")
    loop = asyncio.get_running_loop()
    caja = {}

    class Resumen:
        def __init__(self):
            self.h = hashlib.sha256()

        def update(self, b):
            self.h.update(b)

        def hexdigest(self):
            loop.call_soon_threadsafe(caja["tarea"].cancel)
            time.sleep(0.3)                                    # la cancelacion ya esta entregada
            return self.h.hexdigest()

    monkeypatch.setattr(almacen, "hashlib", SimpleNamespace(sha256=Resumen))
    try:
        caja["tarea"] = asyncio.ensure_future(almacen.escribir_streaming(_Subida(b"%PDF x"), carpeta, "a.pdf", 100))
        with pytest.raises(asyncio.CancelledError):
            await caja["tarea"]
        assert list(carpeta.ruta.iterdir()) == []
    finally:
        carpeta.cerrar()


async def test_una_segunda_cancelacion_no_saca_de_la_espera_al_hilo(tmp_path):
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "l1")
    try:
        leyendo, compuerta = threading.Event(), threading.Event()

        class Lenta(io.BytesIO):
            def __init__(self):
                super().__init__(b"a" * (3 * MIB))
                self.bloques = 0

            def read(self, n=-1):
                self.bloques += 1
                if self.bloques == 2:
                    leyendo.set()
                    assert compuerta.wait(10)
                return super().read(n)

        sub = _Subida(b"")
        sub.file = Lenta()
        tarea = asyncio.create_task(almacen.escribir_streaming(sub, carpeta, "a.pdf", 10 * MIB))
        assert await asyncio.to_thread(leyendo.wait, 10)
        tarea.cancel()
        await asyncio.sleep(0.1)
        tarea.cancel()                                         # la segunda, con el hilo todavia vivo
        await asyncio.sleep(0.1)
        assert not tarea.done()                                # sigue esperando al hilo
        compuerta.set()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert list(carpeta.ruta.iterdir()) == []
    finally:
        carpeta.cerrar()


# ------------------------------------------------- EEXIST con un enlace plantado

def test_enlace_plantado_en_el_nombre_del_archivo_informa_el_lote(ent, workspace, monkeypatch):
    p = ent.proyecto()
    fuera = workspace / "fuera.txt"
    original = almacen.abrir_carpeta_lote

    def con_enlace(*args, **kwargs):
        carpeta = original(*args, **kwargs)
        os.symlink(fuera, carpeta.ruta / "b.pdf")             # alguien planta el nombre del 2.o archivo
        return carpeta

    monkeypatch.setattr(almacen, "abrir_carpeta_lote", con_enlace)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a"), _parte("b.pdf", "b"), _parte("c.pdf", "c")])
    assert r.status_code == 500 and _code(r) == "almacen_ruta_insegura"
    detalle = r.json()["detail"]
    assert detalle["lote"] and [a["nombre"] for a in detalle["aceptados"]] == ["a.pdf"]   # lo ya registrado se informa
    assert not fuera.exists() and str(workspace) not in r.text
    assert [f[1] for f in ent.filas(p)] == ["a.pdf"]
    assert sorted(x.name for x in (workspace / "proyectos" / p.uuid / "entrada" / detalle["lote"]).iterdir()) == ["a.pdf", "b.pdf"]


# ---------------------------------- membresia entre la escritura y el INSERT

def _tras_escribir(monkeypatch, accion):
    original = almacen.escribir_streaming

    async def y_despues(*args, **kwargs):
        resultado = await original(*args, **kwargs)
        r = await asyncio.to_thread(accion)
        assert r.status_code in (200, 204), r.text
        return resultado

    monkeypatch.setattr(almacen, "escribir_streaming", y_despues)


def test_miembro_quitado_entre_la_escritura_y_el_insert_404_sin_fila_ni_archivo(ent, workspace, monkeypatch):
    p = ent.proyecto()
    contrib = ent.miembro(p, "contrib", "CONTRIBUTOR")
    _tras_escribir(monkeypatch, lambda: ent.client.delete(f"{P}/{p.id}/miembros/{ent._id('contrib')}", headers=ent.dueno))
    r = ent.subir(p, contrib, [_parte("a.pdf", "a")])
    assert r.status_code == 404 and _code(r) == "proyecto_no_encontrado"
    assert r.json()["detail"]["aceptados"] == [] and r.json()["detail"]["lote"]
    assert _en_disco(workspace) == [] and ent.filas(p) == []


def test_miembro_degradado_a_lector_entre_la_escritura_y_el_insert_403_sin_fila_ni_archivo(ent, workspace, monkeypatch):
    p = ent.proyecto()
    contrib = ent.miembro(p, "contrib", "CONTRIBUTOR")
    _tras_escribir(monkeypatch, lambda: ent.client.put(f"{P}/{p.id}/miembros/{ent._id('contrib')}",
                                                       headers=ent.dueno, json={"papel": "VIEWER"}))
    r = ent.subir(p, contrib, [_parte("a.pdf", "a")])
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"
    assert _en_disco(workspace) == [] and ent.filas(p) == []


# ------------------------------------------------------ commit incierto del INSERT

def test_insert_con_resultado_incierto_no_toca_el_disco_y_deja_rastro(ent, workspace, monkeypatch, caplog):
    import aiomysql

    p = ent.proyecto()
    original = repo.insertar

    async def se_cae_despues_del_commit(*args, **kwargs):
        await original(*args, **kwargs)                        # la fila SI se escribio
        raise aiomysql.OperationalError(2013, "Lost connection to MySQL server during query")

    monkeypatch.setattr(repo, "insertar", se_cae_despues_del_commit)
    with caplog.at_level(logging.ERROR, logger="api.proyectos_documentos"):
        r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 500 and _code(r) == "insercion_incierta"
    lote = r.json()["detail"]["lote"]
    assert lote and str(workspace) not in r.text
    assert [x.name for x in _en_disco(workspace)] == ["a.pdf"]            # el archivo SIGUE ahi
    assert [f[1] for f in ent.filas(p)] == ["a.pdf"]                      # y la fila existe: no se perdio el documento
    log = " ".join(rec.getMessage() for rec in caplog.records)
    assert lote in log and "a.pdf" in log and "conciliar" in log


# -------------------------------------------------------------- ronda 3

def test_usuario_inactivo_entre_la_escritura_y_el_insert_404_sin_fila_ni_archivo(ent, workspace, monkeypatch):
    p = ent.proyecto()
    contrib = ent.miembro(p, "contrib", "CONTRIBUTOR")
    uid_ = ent._id("contrib")

    def desactivar():
        ent.client.portal.call(sql, "UPDATE jax_users SET status='inactive' WHERE user_id=%s", (uid_,))
        return SimpleNamespace(status_code=200, text="")

    _tras_escribir(monkeypatch, desactivar)
    r = ent.subir(p, contrib, [_parte("a.pdf", "a")])
    assert r.status_code == 404 and _code(r) == "proyecto_no_encontrado"   # lo que E1 da a un usuario no activo
    assert r.json()["detail"]["lote"] and r.json()["detail"]["aceptados"] == []
    assert _en_disco(workspace) == [] and ent.filas(p) == []


async def test_la_espera_blindada_deja_rastro_con_lote_y_nombre_sin_la_ruta(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(almacen, "ESPERA_AVISO_S", 0.1)
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "loteX")
    try:
        leyendo, compuerta = threading.Event(), threading.Event()

        class Lenta(io.BytesIO):
            def __init__(self):
                super().__init__(b"a" * (3 * MIB))
                self.bloques = 0

            def read(self, n=-1):
                self.bloques += 1
                if self.bloques == 2:
                    leyendo.set()
                    assert compuerta.wait(10)
                return super().read(n)

        sub = _Subida(b"")
        sub.file = Lenta()
        with caplog.at_level(logging.WARNING, logger="proyectos_documentos.almacen"):
            tarea = asyncio.create_task(almacen.escribir_streaming(sub, carpeta, "a.pdf", 10 * MIB))
            assert await asyncio.to_thread(leyendo.wait, 10)
            tarea.cancel()
            await asyncio.sleep(0.45)
            compuerta.set()
            with pytest.raises(asyncio.CancelledError):
                await tarea
        avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(avisos) >= 2                                         # uno por cada intervalo de espera
        assert all("loteX" in a and "a.pdf" in a and str(tmp_path) not in a for a in avisos)
        assert list(carpeta.ruta.iterdir()) == []
    finally:
        carpeta.cerrar()


async def test_borrado_de_cancelacion_tardia_corre_fuera_del_hilo_del_loop(tmp_path, monkeypatch):
    """El `_borrar` de la rama de cancelacion tardia va por `to_thread`: no bloquea el loop."""
    carpeta = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "l1")
    loop = asyncio.get_running_loop()
    hilo_del_loop = threading.get_ident()
    caja, borrados = {}, []

    class Resumen:
        def __init__(self):
            self.h = hashlib.sha256()

        def update(self, b):
            self.h.update(b)

        def hexdigest(self):
            loop.call_soon_threadsafe(caja["tarea"].cancel)
            time.sleep(0.3)
            return self.h.hexdigest()

    original = almacen._borrar

    def espia(fd, nombre):
        borrados.append(threading.get_ident())
        return original(fd, nombre)

    monkeypatch.setattr(almacen, "hashlib", SimpleNamespace(sha256=Resumen))
    monkeypatch.setattr(almacen, "_borrar", espia)
    try:
        caja["tarea"] = asyncio.ensure_future(almacen.escribir_streaming(_Subida(b"%PDF x"), carpeta, "a.pdf", 100))
        with pytest.raises(asyncio.CancelledError):
            await caja["tarea"]
        assert len(borrados) == 1 and borrados[0] != hilo_del_loop
        assert list(carpeta.ruta.iterdir()) == []
    finally:
        carpeta.cerrar()


def test_fallo_al_consultar_el_duplicado_informa_el_lote(ent, workspace, monkeypatch):
    p = ent.proyecto()
    assert ent.subir(p, ent.dueno, [_parte("viejo.pdf", "mismo")]).status_code == 202

    async def falla(*args, **kwargs):
        raise RuntimeError("la base no responde")

    monkeypatch.setattr(repo, "existente_por_sha", falla)
    r = ent.subir(p, ent.dueno, [_parte("nuevo.pdf", "otro"), _parte("copia.pdf", "mismo"), _parte("z.pdf", "z")])
    assert r.status_code == 500 and _code(r) == "consulta_duplicado_fallida"
    detalle = r.json()["detail"]
    assert detalle["lote"] and [a["nombre"] for a in detalle["aceptados"]] == ["nuevo.pdf"]
    assert sorted(f[1] for f in ent.filas(p)) == ["nuevo.pdf", "viejo.pdf"]            # lo aceptado esta; z no se intento
    assert sorted(x.name for x in _en_disco(workspace)) == ["nuevo.pdf", "viejo.pdf"]  # la copia ya se habia borrado
    assert str(workspace) not in r.text


# ------------------------- subidas simultaneas (ronda final, MAJOR-4)

POR_USUARIO = "proyectos.documentos.subidas_por_usuario"
GLOBALES = "proyectos.documentos.subidas_globales"


class _CuerpoRetenido:
    """Un multipart que se entrega en dos tramos: el segundo espera a `soltar`. `leido`
    dice si la plataforma pidio el cuerpo; con `falla` el cliente se corta a mitad."""

    def __init__(self, nombre, contenido):
        pedido = httpx.Request("POST", "http://t/", files=[_parte(nombre, contenido)])
        self.tipo = pedido.headers["content-type"]
        self.cuerpo = pedido.read()
        self.leido = False
        self.falla = False
        self.soltar = None

    async def tramos(self):
        self.leido = True
        yield self.cuerpo[:10]
        await self.soltar.wait()
        if self.falla:
            raise RuntimeError("el cliente corto la subida")
        yield self.cuerpo[10:]


def _escenario(client, guion):
    """Corre `guion(http, subir)` en el loop de la app con un cliente ASGI real: los POST
    corren de verdad en paralelo y el cuerpo llega cuando la prueba lo suelta."""
    cuerpos = []

    async def corre():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://t") as http:
            async def subir(p, headers, cuerpo):
                cuerpo.soltar = cuerpo.soltar or asyncio.Event()
                cuerpos.append(cuerpo)
                # Tope de espera: sin cupo, un POST quedaria esperando un cuerpo que nunca llega.
                return await asyncio.wait_for(http.post(f"{P}/{p.id}/documentos", content=cuerpo.tramos(),
                                                        headers={**headers, "content-type": cuerpo.tipo}), 10)
            try:
                return await guion(subir)
            finally:
                for cuerpo in cuerpos:                  # nada queda colgado si la prueba falla
                    cuerpo.soltar.set()
                await asyncio.sleep(0.2)
    return client.portal.call(corre)


async def _hasta(condicion):
    for _ in range(500):
        if condicion():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("la condicion no se cumplio a tiempo")


def test_tercera_subida_simultanea_del_mismo_usuario_429_sin_leer_el_cuerpo(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{POR_USUARIO: "2", GLOBALES: "4"})
    p = ent.proyecto()
    a, b, c, d = (_CuerpoRetenido(f"{n}.pdf", n) for n in "abcd")

    async def guion(subir):
        primeras = [asyncio.create_task(subir(p, ent.dueno, x)) for x in (a, b)]
        await _hasta(lambda: a.leido and b.leido)              # las dos ya tienen su cupo
        tercera = await subir(p, ent.dueno, c)
        a.soltar.set()                                         # termina una: se libera su cupo
        primera = await primeras[0]
        cuarta = asyncio.create_task(subir(p, ent.dueno, d))
        await _hasta(lambda: d.leido)
        d.soltar.set()
        b.soltar.set()
        return tercera, primera, await cuarta, await primeras[1]

    tercera, primera, cuarta, segunda = _escenario(ent.client, guion)
    assert tercera.status_code == 429 and _code(tercera) == "subidas_simultaneas", tercera.text
    assert c.leido is False                                    # el 429 sale sin pedir el cuerpo
    assert (primera.status_code, segunda.status_code, cuarta.status_code) == (202, 202, 202)
    assert sorted(f[1] for f in ent.filas(p)) == ["a.pdf", "b.pdf", "d.pdf"]


def test_el_cupo_es_por_usuario_y_tambien_global(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{POR_USUARIO: "1", GLOBALES: "2"})
    p = ent.proyecto()
    otro = ent.miembro(p, "otro", "CONTRIBUTOR")
    tercero = ent.miembro(p, "tercero", "CONTRIBUTOR")
    a, b, c, d = (_CuerpoRetenido(f"{n}.pdf", n) for n in "abcd")

    async def guion(subir):
        en_vuelo = asyncio.create_task(subir(p, ent.dueno, a))
        await _hasta(lambda: a.leido)
        mismo_usuario = await subir(p, ent.dueno, b)            # 1 por usuario
        del_otro = asyncio.create_task(subir(p, otro, c))
        await _hasta(lambda: c.leido)                           # otro usuario si entra
        global_lleno = await subir(p, tercero, d)               # 2 en todo el servicio
        a.soltar.set(), c.soltar.set()
        return mismo_usuario, global_lleno, await en_vuelo, await del_otro

    mismo_usuario, global_lleno, primera, del_otro = _escenario(ent.client, guion)
    assert (mismo_usuario.status_code, _code(mismo_usuario)) == (429, "subidas_simultaneas")
    assert (global_lleno.status_code, _code(global_lleno)) == (429, "subidas_simultaneas")
    assert not b.leido and not d.leido
    assert (primera.status_code, del_otro.status_code) == (202, 202)


def test_el_cupo_se_libera_si_la_subida_falla_o_se_cancela(ent, workspace, ajustes_en_db):
    ajustes_en_db.poner(**{POR_USUARIO: "1", GLOBALES: "4"})
    p = ent.proyecto()
    cortada, cancelada, ultima = (_CuerpoRetenido(f"{n}.pdf", n) for n in ("x", "y", "z"))

    async def guion(subir):
        tarea = asyncio.create_task(subir(p, ent.dueno, cortada))
        await _hasta(lambda: cortada.leido)
        cortada.falla = True
        cortada.soltar.set()
        try:
            await tarea
        except Exception:  # fail-soft: la subida cortada puede fallar hacia el cliente; lo que se mira es el cupo
            pass
        tarea = asyncio.create_task(subir(p, ent.dueno, cancelada))
        await _hasta(lambda: cancelada.leido)                  # entro: el cupo estaba libre
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        ultima.soltar = asyncio.Event()
        ultima.soltar.set()
        return await subir(p, ent.dueno, ultima)

    final = _escenario(ent.client, guion)
    assert final.status_code == 202, final.text                 # ni el fallo ni la cancelacion se quedaron el cupo
    from proyectos_documentos import cupo_de_subidas
    assert cupo_de_subidas.en_uso() == (0, {})


# ------------------------- re-encolar una fila atascada en error (ronda final, menor 8)

def _atascar(ent, doc_id, carpeta=None, estado="error", ruta=None):
    ent.client.portal.call(
        sql, "UPDATE project_documents SET estado=%s, error='procesamiento_fallido', job_id='J-viejo', "
             "carpeta_procesado=%s, ruta_entrada=COALESCE(%s, ruta_entrada) WHERE id=%s",
        (estado, carpeta, ruta, doc_id))


def _fila(ent, doc_id):
    return ent.client.portal.call(
        sql, "SELECT estado, job_id, error, ruta_entrada, carpeta_procesado FROM project_documents WHERE id=%s",
        (doc_id,), True)[0]


def test_subir_de_nuevo_un_documento_en_error_sin_procesar_lo_reencola(ent, workspace):
    p = ent.proyecto()
    r1 = ent.subir(p, ent.dueno, [_parte("a.pdf", "mismo")])
    doc = r1.json()["aceptados"][0]["id"]
    vieja = workspace / ent.filas(p)[0][2]
    _atascar(ent, doc)
    r2 = ent.subir(p, ent.dueno, [_parte("a_otra_vez.pdf", "mismo")])
    assert r2.status_code == 202, r2.text
    assert r2.json()["aceptados"] == [{"id": doc, "nombre": "a_otra_vez.pdf"}] and r2.json()["ignorados"] == []
    estado, job, error, ruta, carpeta = _fila(ent, doc)
    assert (estado, job, error, carpeta) == ("en_cola", None, None, None)
    assert ruta == f"proyectos/{p.uuid}/entrada/{r2.json()['lote']}/a_otra_vez.pdf"
    assert (workspace / ruta).read_bytes() == b"%PDF-1.4 mismo"
    assert not vieja.exists()                                       # la copia vieja de entrada/ sobra
    assert len(ent.filas(p)) == 1


def test_en_error_con_carpeta_procesado_o_ya_listo_sigue_siendo_duplicado(ent, workspace):
    p = ent.proyecto()
    a = ent.subir(p, ent.dueno, [_parte("a.pdf", "uno")]).json()["aceptados"][0]["id"]
    b = ent.subir(p, ent.dueno, [_parte("b.pdf", "dos")]).json()["aceptados"][0]["id"]
    _atascar(ent, a, carpeta=f"proyectos/{p.uuid}/procesado/x")
    _atascar(ent, b, estado="listo")
    r = ent.subir(p, ent.dueno, [_parte("a2.pdf", "uno"), _parte("b2.pdf", "dos")])
    assert r.json()["aceptados"] == []
    assert [i["motivo"] for i in r.json()["ignorados"]] == ["duplicado", "duplicado"]
    assert _fila(ent, a)[0] == "error" and _fila(ent, b)[0] == "listo"


def test_reencolar_no_borra_una_ruta_vieja_bajo_fuente(ent, workspace):
    p = ent.proyecto()
    doc = ent.subir(p, ent.dueno, [_parte("a.pdf", "lactovi")]).json()["aceptados"][0]["id"]
    original = workspace / "proyectos" / p.uuid / "fuente" / "a.pdf"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"%PDF-1.4 lactovi")
    _atascar(ent, doc, ruta=f"proyectos/{p.uuid}/fuente/a.pdf")
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "lactovi")])
    assert [x["id"] for x in r.json()["aceptados"]] == [doc]
    assert original.read_bytes() == b"%PDF-1.4 lactovi"             # fuente/ nunca se borra


def test_oculto_en_error_no_se_resucita(ent, workspace):
    p = ent.proyecto()
    doc = ent.subir(p, ent.dueno, [_parte("a.pdf", "oculto")]).json()["aceptados"][0]["id"]
    _atascar(ent, doc)
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/ocultar", headers=ent.dueno).status_code == 204
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "oculto")])
    assert r.json()["ignorados"] == [{"nombre": "a.pdf", "motivo": "duplicado_oculto"}]
    assert _fila(ent, doc)[0] == "error"


# ------------------------- re-encolar: quien sube de nuevo y las mismas condiciones del INSERT (seguimiento 2)

def test_reencolar_registra_a_quien_subio_de_nuevo_y_el_despachador_manda_su_correo(ent, workspace, monkeypatch):
    from proyectos_documentos import despachador
    p = ent.proyecto()
    b = ent.miembro(p, "bea", "CONTRIBUTOR")
    doc = ent.subir(p, ent.dueno, [_parte("de_a.pdf", "compartido")]).json()["aceptados"][0]["id"]
    _atascar(ent, doc)
    r = ent.subir(p, b, [_parte("de_b.pdf", "compartido")])
    assert r.json()["aceptados"] == [{"id": doc, "nombre": "de_b.pdf"}], r.text
    subido_por, nombre = ent.client.portal.call(
        sql, "SELECT subido_por, nombre_original FROM project_documents WHERE id=%s", (doc,), True)[0]
    assert (subido_por, nombre) == (ent._id("bea"), "de_b.pdf")

    pedidos = []

    async def las_manos(request):
        cuerpo = json.loads(request.content) if request.method == "POST" else None
        if cuerpo and cuerpo["project_uuid"] == p.uuid:
            pedidos.append(cuerpo)
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": f"j-{uuid.uuid4().hex}"})
        return httpx.Response(200, json={"estado": "running", "resultados": []})

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(las_manos))

    async def get_cliente():
        return cliente
    monkeypatch.setattr(despachador, "get_http_client", get_cliente)
    import credencial_las_manos
    monkeypatch.setenv(credencial_las_manos.VARIABLE, "c" * credencial_las_manos.LARGO_MINIMO)

    async def ciclo():
        from db.connection import get_pool
        await despachador.ciclo(await get_pool())
    ent.client.portal.call(ciclo)
    assert [x["usuario"] for x in pedidos] == [ent._email("bea")]


def test_reencolar_en_un_proyecto_archivado_entre_medio_responde_409_y_no_cambia_la_fila(ent, workspace, monkeypatch):
    p = ent.proyecto()
    doc = ent.subir(p, ent.dueno, [_parte("a.pdf", "hueco")]).json()["aceptados"][0]["id"]
    _atascar(ent, doc)
    antes = _fila(ent, doc)
    original = repo.insertar

    async def insertar_y_archivar(*args, **kwargs):
        resultado = await original(*args, **kwargs)
        if resultado is None:   # el INSERT vio el duplicado; el proyecto se archiva antes del UPDATE
            await asyncio.to_thread(lambda: ent.client.post(f"{P}/{p.id}/estado", headers=ent.dueno,
                                                            json={"estado": "ARCHIVED"}))
        return resultado
    monkeypatch.setattr(repo, "insertar", insertar_y_archivar)
    r = ent.subir(p, ent.dueno, [_parte("a2.pdf", "hueco")])
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo", r.text
    assert _fila(ent, doc) == antes
    assert [x.name for x in _en_disco(workspace)] == ["a.pdf"]      # lo recien escrito se borro
