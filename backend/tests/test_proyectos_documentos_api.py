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
from auth.models import AuthUser
from proyectos_documentos import almacen, original, tipos
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
    # El ANCLA de los permisos: `proyectos/` con setgid, como en produccion (almacen.py exige que cada nivel que
    # abre o crea debajo tenga setgid y el grupo de esta carpeta). Un tmp_path pelado no lo trae.
    (tmp_path / "proyectos").mkdir()
    os.chmod(tmp_path / "proyectos", 0o2770)
    return tmp_path


@pytest.fixture
def ent(client, workspace, ajustes_en_db):
    entorno = Entorno(client)
    # Topes iniciales de la migracion: cada prueba que los cambia los repone sola
    # (ajustes_en_db restaura lo que habia).
    yield entorno
    for pid in entorno.proyectos:
        client.portal.call(sql, "DELETE FROM project_documents WHERE project_id=%s", (pid,))


def _proyectos_vacio(workspace: Path) -> bool:
    """Nada colgando del ancla `proyectos/` (que el fixture crea): la prueba de «no se escribio nada»."""
    return not any((workspace / "proyectos").iterdir())


def _en_disco(workspace: Path) -> list[Path]:
    raiz = workspace / "proyectos"
    return sorted(p for p in raiz.rglob("*") if p.is_file()) if raiz.exists() else []


def test_pdf_confirmado_por_contenido_se_guarda_con_sufijo_pdf():
    from api.proyectos_documentos import _nombre_de_almacenamiento

    assert _nombre_de_almacenamiento("estado.docx", "estado.docx", "pdf") == "estado.pdf"
    assert _nombre_de_almacenamiento("estado.docx", "estado.docx", None) == "estado.docx"


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


def test_pdf_escaneado_desde_chat_se_encola_y_expone_su_estado(ent, workspace, tmp_path, monkeypatch):
    """El PDF crudo del chat entra por la misma biblioteca y cola durable de LAS MANOS."""
    from api import upload as upload_mod
    from starlette.datastructures import UploadFile
    from tests.adjuntos_muestras import pdf_con_texto

    proyecto = ent.proyecto()
    adjuntos = tmp_path / "adjuntos"
    adjuntos.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(adjuntos))
    usuario = AuthUser(user_id=str(ent._id("dueno")), tenant_id=ent.tenant, role="operator")
    # El tipo lo verificó el contenido de los bytes; el nombre de usuario puede
    # tener un sufijo engañoso y no debe cambiar la ruta del extractor.
    archivo = UploadFile(io.BytesIO(pdf_con_texto(["", ""])), filename="estado.docx")
    resultado = asyncio.run(upload_mod.upload_file(file=archivo, user=usuario, project_id=proyecto.id))

    assert resultado["tipo"] == "pdf_procesando"
    assert resultado["project_id"] == proyecto.id
    assert resultado["document_id"] == ent.filas(proyecto)[0][0]
    assert ent.filas(proyecto)[0][5] == "en_cola"
    assert [p.name for p in _en_disco(workspace)] == ["estado.pdf"]
    assert ent.filas(proyecto)[0][1] == "estado.docx"
    assert ent.filas(proyecto)[0][4] == "pdf"
    assert list(adjuntos.rglob("*.dato")) == []


def test_estado_de_documento_exige_que_pertenezca_al_proyecto(ent, workspace):
    proyecto = ent.proyecto()
    alta = ent.subir(proyecto, ent.dueno, [_parte("estado.pdf", "estado")])
    documento_id = alta.json()["aceptados"][0]["id"]

    estado = ent.client.get(f"{P}/{proyecto.id}/documentos/{documento_id}", headers=ent.dueno)
    assert estado.status_code == 200, estado.text
    assert estado.json()["id"] == documento_id
    assert estado.json()["estado"] == "en_cola"
    assert estado.json()["error"] is None

    otro = ent.proyecto()
    ajeno = ent.client.get(f"{P}/{otro.id}/documentos/{documento_id}", headers=ent.dueno)
    assert ajeno.status_code == 404


def test_el_dueno_tambien_sube(ent, workspace):
    p = ent.proyecto()
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 202 and len(r.json()["aceptados"]) == 1


def test_viewer_no_sube_403_y_no_escribe(ent, workspace):
    p = ent.proyecto()
    h = ent.miembro(p, "lector", "VIEWER")
    r = ent.subir(p, h, [_parte("a.pdf", "a")])
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"
    assert _en_disco(workspace) == [] and _proyectos_vacio(workspace)
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
    assert _proyectos_vacio(workspace) and ent.filas(p) == []


def test_sin_sesion_401(ent, workspace):
    p = ent.proyecto()
    r = ent.client.post(f"{P}/{p.id}/documentos", files=[_parte("a.pdf", "a")])
    assert r.status_code == 401 and _proyectos_vacio(workspace)


def test_proyecto_archivado_409_y_no_escribe(ent, workspace):
    p = ent.proyecto(archivar=True)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo"
    assert _proyectos_vacio(workspace) and ent.filas(p) == []


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
    assert _proyectos_vacio(workspace) and ent.filas(p) == []


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
    _ancla(tmp_path)
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
    _ancla(tmp_path)
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
    _ancla(tmp_path)
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

def test_los_archivos_se_fijan_a_0660_y_las_carpetas_heredan_sin_chmod(ent, workspace, monkeypatch):
    """Bajo `proyectos/` hay ACL por defecto y setgid. El archivo es 0660 aunque el umask diga otra
    cosa; las CARPETAS no se chmodean nunca (un chmod de jaxsvc, que no es del grupo, les borraria el
    setgid) y heredan el setgid y el grupo del padre."""
    p = ent.proyecto()
    chmods = []
    real = os.fchmod
    monkeypatch.setattr(almacen.os, "fchmod", lambda fd, modo: (chmods.append(stat.S_ISDIR(os.fstat(fd).st_mode)),
                                                                  real(fd, modo))[1])
    anterior = os.umask(0o077)
    try:
        r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    finally:
        os.umask(anterior)
    assert r.status_code == 202, r.text
    assert chmods and not any(chmods)                             # solo se chmodeo archivos, ninguna carpeta
    archivo = _en_disco(workspace)[0]
    assert stat.S_IMODE(archivo.stat().st_mode) == 0o660
    padre = workspace / "proyectos"
    for carpeta in (archivo.parent.parent.parent, archivo.parent.parent, archivo.parent):
        assert carpeta.stat().st_mode & stat.S_ISGID, carpeta             # setgid heredado
        assert carpeta.stat().st_gid == padre.stat().st_gid
        assert carpeta.stat().st_mode & 0o007 == 0
    assert archivo.parent.parent.parent.parent == padre


def test_carpeta_existente_no_se_toca_y_las_nuevas_no_se_chmodean(tmp_path, monkeypatch):
    """Solo se crea lo que falta, sin chmod: una carpeta previa conserva el suyo."""
    previa = tmp_path / "proyectos"
    previa.mkdir(mode=0o2750)
    os.chmod(previa, 0o2750)                                              # el ancla: setgid, otros sin nada
    llamadas = []
    monkeypatch.setattr(almacen.os, "fchmod", lambda *a: llamadas.append(a))
    monkeypatch.setattr(almacen.os, "chmod", lambda *a, **k: llamadas.append(a))
    u = str(uuid.uuid4())
    c1 = almacen.abrir_carpeta_lote(tmp_path, u, "lote1")
    assert stat.S_IMODE(previa.stat().st_mode) == 0o2750
    assert (previa / u / "entrada" / "lote1").is_dir()
    c2 = almacen.abrir_carpeta_lote(tmp_path, u, "lote2")                 # idempotente sobre lo ya creado
    assert sorted(x.name for x in (previa / u / "entrada").iterdir()) == ["lote1", "lote2"]
    assert llamadas == []
    c1.cerrar()
    c2.cerrar()


def _mkdir_que_pierde(que):
    """Un `os.mkdir` que crea la carpeta y luego le quita lo heredado (lo que haria un chmod de jaxsvc)."""
    real = os.mkdir

    def mkdir(nombre, modo, dir_fd=None):
        real(nombre, modo, dir_fd=dir_fd)
        if nombre == "entrada":
            que(nombre, dir_fd)
    return mkdir


def test_una_carpeta_sin_setgid_despues_de_crear_falla_cerrado_y_no_se_corrige(tmp_path, monkeypatch):
    (tmp_path / "proyectos").mkdir()
    os.chmod(tmp_path / "proyectos", 0o2770)
    monkeypatch.setattr(almacen.os, "mkdir", _mkdir_que_pierde(
        lambda n, fd: os.chmod(n, 0o770, dir_fd=fd)))                     # un chmod explicito borra el setgid
    u = str(uuid.uuid4())
    with pytest.raises(almacen.HerenciaDeCarpetaRota, match="setgid"):
        almacen.abrir_carpeta_lote(tmp_path, u, "lote1")
    assert not (tmp_path / "proyectos" / u).exists() or not any((tmp_path / "proyectos" / u).iterdir())
    assert not (tmp_path / "proyectos" / u / "entrada").exists()


def test_una_carpeta_con_bits_para_otros_que_el_padre_no_tiene_falla_cerrado(tmp_path, monkeypatch):
    (tmp_path / "proyectos").mkdir()
    os.chmod(tmp_path / "proyectos", 0o2770)
    monkeypatch.setattr(almacen.os, "mkdir", _mkdir_que_pierde(
        lambda n, fd: os.chmod(n, 0o2775, dir_fd=fd)))
    with pytest.raises(almacen.HerenciaDeCarpetaRota, match="otros"):
        almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "lote1")


def test_si_el_padre_ya_da_bits_a_otros_el_hijo_puede_tenerlos(tmp_path, monkeypatch):
    (tmp_path / "proyectos").mkdir()
    os.chmod(tmp_path / "proyectos", 0o2775)
    anterior = os.umask(0)
    try:
        c = almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "lote1")
    finally:
        os.umask(anterior)
    c.cerrar()


def test_subir_con_una_carpeta_sin_herencia_500_almacen_herencia_rota_y_no_queda_nada(ent, workspace, monkeypatch):
    p = ent.proyecto()
    monkeypatch.setattr(almacen.os, "mkdir", _mkdir_que_pierde(lambda n, fd: os.chmod(n, 0o770, dir_fd=fd)))
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 500 and _code(r) == "almacen_herencia_rota", r.text
    assert ent.filas(p) == [] and _en_disco(workspace) == []


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
    # Los niveles de arriba, reales y como en produccion (con setgid: almacen exige el invariante en cada uno).
    if nivel != "proyectos":
        ruta["proyectos"].mkdir()
        os.chmod(ruta["proyectos"], 0o2770)
    if nivel in ("entrada", "lote"):
        ruta["uuid"].mkdir()
        os.chmod(ruta["uuid"], 0o2770)
    if nivel == "lote":
        ruta["entrada"].mkdir()
        os.chmod(ruta["entrada"], 0o2770)
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
    assert _proyectos_vacio(workspace) and _en_disco(workspace) == []
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
    _ancla(tmp_path)
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
    _ancla(tmp_path)
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
    _ancla(tmp_path)
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
    _ancla(tmp_path)
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

def test_reencolar_usa_al_uploader_autenticado_para_propiedad_y_despacho(ent, workspace, monkeypatch):
    from proyectos_documentos import despachador
    p = ent.proyecto()
    b = ent.miembro(p, "bea", "CONTRIBUTOR")
    doc = ent.subir(p, ent.dueno, [_parte("de_a.pdf", "compartido")]).json()["aceptados"][0]["id"]
    _atascar(ent, doc)
    # `usuario` no es parte del contrato multipart. Aunque un cliente lo inyecte con la
    # identidad del primer uploader, la dependencia autenticada es la unica autoridad.
    r = ent.client.post(f"{P}/{p.id}/documentos", headers=b, files=[_parte("de_b.pdf", "compartido")],
                        data={"usuario": ent._email("dueno")})
    assert r.status_code == 202, r.text
    assert r.json()["aceptados"] == [{"id": doc, "nombre": "de_b.pdf"}], r.text
    tenant_id, subido_por, project_id, nombre, email = ent.client.portal.call(
        sql,
        "SELECT s.tenant_id, d.subido_por, d.project_id, d.nombre_original, u.email "
        "FROM project_documents d JOIN jax_project_scope s ON s.project_id=d.project_id "
        "JOIN jax_users u ON u.user_id=d.subido_por AND u.tenant_id=s.tenant_id WHERE d.id=%s",
        (doc,), True)[0]
    assert (subido_por, project_id, nombre, email) == (ent._id("bea"), p.id, "de_b.pdf", ent._email("bea"))
    assert subido_por != ent._id("dueno")

    pedidos = []

    async def las_manos(request):
        cuerpo = json.loads(request.content) if request.method == "POST" else None
        if cuerpo and cuerpo["project_uuid"] == p.uuid:
            pedidos.append((cuerpo, request.headers))
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
    assert len(pedidos) == 1
    cuerpo, encabezados = pedidos[0]
    assert cuerpo == {"project_uuid": p.uuid, "rutas": [f"proyectos/{p.uuid}/entrada/{r.json()['lote']}/de_b.pdf"]}
    assert "usuario" not in cuerpo
    assert encabezados["X-Jax-Processing-Owner-Version"] == "processing-owner.1"
    assert encabezados["X-Jax-Processing-Tenant-Id"] == str(tenant_id)
    assert encabezados["X-Jax-Processing-User-Id"] == str(subido_por)
    assert encabezados["X-Jax-Processing-Project-Id"] == str(project_id)


def test_usuario_multipart_falsificado_no_otorga_insert_ni_reencolar_a_un_viewer(ent, workspace):
    p = ent.proyecto()
    bea = ent.miembro(p, "bea", "CONTRIBUTOR")
    lector = ent.miembro(p, "lector", "VIEWER")

    # El INSERT toma al uploader desde el JWT de Bea, aunque el multipart diga otra cosa.
    insertado = ent.client.post(f"{P}/{p.id}/documentos", headers=bea,
                                files=[_parte("de_bea.pdf", "contenido")],
                                data={"usuario": ent._email("dueno")})
    assert insertado.status_code == 202, insertado.text
    doc = insertado.json()["aceptados"][0]["id"]
    subido_por = ent.client.portal.call(sql, "SELECT subido_por FROM project_documents WHERE id=%s", (doc,), True)[0][0]
    assert subido_por == ent._id("bea")
    assert subido_por != ent._id("dueno")

    # Un VIEWER no puede convertir el nombre del uploader en permiso de escritura: ni
    # reencola el mismo hash ni inserta otro hash bajo la identidad multipart de Bea.
    _atascar(ent, doc)
    antes = ent.client.portal.call(sql, "SELECT estado, subido_por, nombre_original FROM project_documents WHERE id=%s",
                                  (doc,), True)[0]
    total_antes = ent.client.portal.call(sql, "SELECT COUNT(*) FROM project_documents WHERE project_id=%s", (p.id,), True)[0][0]
    for nombre, contenido in (("forjado.pdf", "contenido"), ("nuevo-forjado.pdf", "nuevo-contenido")):
        bloqueado = ent.client.post(f"{P}/{p.id}/documentos", headers=lector,
                                    files=[_parte(nombre, contenido)], data={"usuario": ent._email("bea")})
        assert bloqueado.status_code == 403 and _code(bloqueado) == "papel_insuficiente"
        despues = ent.client.portal.call(sql, "SELECT estado, subido_por, nombre_original FROM project_documents WHERE id=%s",
                                        (doc,), True)[0]
        total_despues = ent.client.portal.call(sql, "SELECT COUNT(*) FROM project_documents WHERE project_id=%s", (p.id,), True)[0][0]
        assert despues == antes and total_despues == total_antes


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


# ------------------------------------------------------------------ reprocesar

def _ingerido(ent, p, workspace, headers, *, estado="sin_extractor", nombre="a.pdf", con_ficha=True,
              error=None, mover=True, copiar=False):
    """Un documento subido y luego «ingerido» como lo deja LAS MANOS: el original en
    `fuente/`, `carpeta_procesado` con su ficha y la fila en `estado`."""
    contenido = f"%PDF-1.4 {nombre}".encode()
    r = ent.subir(p, headers, [("archivos", (nombre, contenido, "application/pdf"))])
    doc = r.json()["aceptados"][0]["id"]
    fila = [f for f in ent.filas(p) if f[0] == doc][0]
    entrada = workspace / fila[2]
    fuente = workspace / "proyectos" / p.uuid / "fuente" / "lactovi" / nombre
    fuente.parent.mkdir(parents=True, exist_ok=True)
    if copiar:
        fuente.write_bytes(entrada.read_bytes())
    elif mover:
        entrada.rename(fuente)
    procesado = f"proyectos/{p.uuid}/procesado/{doc}"
    if con_ficha:
        (workspace / procesado).mkdir(parents=True)
        (workspace / procesado / "ficha.json").write_text(json.dumps({"origen": f"fuente/lactovi/{nombre}"}))
    ent.client.portal.call(
        sql, "UPDATE project_documents SET estado=%s, carpeta_procesado=%s, error=%s, job_id='job-viejo' WHERE id=%s",
        (estado, procesado if con_ficha else None, error, doc))
    return doc, fuente


def _fila_rep(ent, doc):
    return ent.client.portal.call(sql, "SELECT estado, ruta_entrada, job_id, error, subido_por "
                                       "FROM project_documents WHERE id=%s", (doc,), True)[0]


def test_reprocesar_202_deja_la_fila_en_cola_apuntando_a_fuente_y_no_toca_el_disco(ent, workspace, caplog):
    p = ent.proyecto()
    contrib = ent.miembro(p, "contrib", "CONTRIBUTOR")
    doc, fuente = _ingerido(ent, p, workspace, contrib)
    antes = _en_disco(workspace)
    caplog.set_level(logging.INFO)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)    # otro miembro: el dueno
    assert r.status_code == 202, r.text
    assert r.json() == {"id": doc, "estado": "en_cola"}
    estado, ruta, job, error, subido_por = _fila_rep(ent, doc)
    assert (estado, ruta, job, error) == ("en_cola", f"proyectos/{p.uuid}/fuente/lactovi/a.pdf", None, None)
    assert subido_por == ent._id("dueno")                         # la fila pasa a ser de quien reprocesa
    assert _en_disco(workspace) == antes and fuente.is_file()
    # quien reprocesa queda en el log (la tabla no tiene donde)
    mensajes = [m for m in caplog.messages if "reprocesado" in m]
    assert mensajes and str(ent._id("dueno")) in mensajes[0] and str(doc) in mensajes[0], caplog.messages
    assert str(ent._id("contrib")) in mensajes[0]                  # el subido_por anterior tambien queda


def test_reprocesar_desde_error_con_o_sin_motivo(ent, workspace):
    p = ent.proyecto()
    a, _ = _ingerido(ent, p, workspace, ent.dueno, estado="error", nombre="a.pdf", error=None)
    b, _ = _ingerido(ent, p, workspace, ent.dueno, estado="error", nombre="b.pdf", error="procesamiento_fallido")
    for doc in (a, b):
        assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
        assert _fila_rep(ent, doc)[:4] == ("en_cola", f"proyectos/{p.uuid}/fuente/lactovi/{'a' if doc == a else 'b'}.pdf",
                                       None, None)


def test_reprocesar_por_sha_cuando_no_hay_ficha(ent, workspace):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno, con_ficha=False, estado="error", error="trabajo_perdido")
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    assert _fila_rep(ent, doc)[1] == f"proyectos/{p.uuid}/fuente/lactovi/a.pdf"


@pytest.mark.parametrize("estado", ["en_cola", "pendiente", "procesando", "listo", "parcial", "cancelado"])
def test_reprocesar_otro_estado_409_no_reprocesable(ent, workspace, estado):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno, estado=estado)
    antes = _fila_rep(ent, doc)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "no_reprocesable"
    assert _fila_rep(ent, doc) == antes


def test_reprocesar_tipo_sin_extractor_409_no_reprocesable(ent, workspace):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    ent.client.portal.call(sql, "UPDATE project_documents SET nombre_original='a.txt' WHERE id=%s", (doc,))
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "no_reprocesable"
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_sin_original_409_original_no_encontrado_y_no_cambia_la_fila_rep(ent, workspace):
    p = ent.proyecto()
    doc, fuente = _ingerido(ent, p, workspace, ent.dueno)
    fuente.unlink()
    antes = _fila_rep(ent, doc)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "original_no_encontrado"
    assert _fila_rep(ent, doc) == antes


def test_reprocesar_con_el_original_cambiado_409_original_no_encontrado(ent, workspace):
    p = ent.proyecto()
    doc, fuente = _ingerido(ent, p, workspace, ent.dueno)
    fuente.write_bytes(b"otro contenido distinto")
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "original_no_encontrado"
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_con_el_original_como_enlace_fuera_de_fuente_409(ent, workspace, tmp_path_factory):
    p = ent.proyecto()
    doc, fuente = _ingerido(ent, p, workspace, ent.dueno)
    fuera = tmp_path_factory.mktemp("fuera") / "a.pdf"
    fuera.write_bytes(fuente.read_bytes())
    fuente.unlink()
    fuente.symlink_to(fuera)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "original_no_encontrado"
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_404_documento_de_otro_proyecto_o_inexistente_o_proyecto_ajeno(ent, workspace):
    p1, p2 = ent.proyecto(), ent.proyecto()
    doc, _ = _ingerido(ent, p2, workspace, ent.dueno)
    for d in (doc, 99999999):
        r = ent.client.post(f"{P}/{p1.id}/documentos/{d}/reprocesar", headers=ent.dueno)
        assert r.status_code == 404 and _code(r) == "documento_no_encontrado"
    ajeno = ent.usuario("ajeno", tenant=f"otro-{uuid.uuid4().hex}")
    r = ent.client.post(f"{P}/{p2.id}/documentos/{doc}/reprocesar", headers=ajeno)
    assert r.status_code == 404 and _code(r) == "proyecto_no_encontrado"
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_lector_403_y_anonimo_401(ent, workspace):
    p = ent.proyecto()
    lector = ent.miembro(p, "lector", "VIEWER")
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=lector)
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar").status_code == 401
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_en_proyecto_archivado_409(ent, workspace):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    assert ent.client.post(f"{P}/{p.id}/estado", headers=ent.dueno, json={"estado": "ARCHIVED"}).status_code == 200
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "proyecto_no_activo"
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_avisa_al_despachador(ent, workspace, monkeypatch):
    from proyectos_documentos import despachador
    avisos = []
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    monkeypatch.setattr(despachador, "despachar_ahora", lambda: avisos.append(1))   # despues de subir, que tambien avisa
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    assert avisos == [1]


def test_reprocesar_con_el_freno_puesto_423_y_no_cambia_nada(client, usuarios, ent, workspace):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    admin_id, _ = usuarios(role="superadmin")
    admin = auth(token_para(admin_id, role="superadmin"))
    assert client.post("/api/admin/kill-switch/activar", headers=admin).status_code == 200
    try:
        r = client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
        assert (r.status_code, r.json().get("detail")) == (423, "kill_switch_activo")
        assert _fila_rep(ent, doc)[0] == "sin_extractor"
    finally:
        assert client.post("/api/admin/kill-switch/reanudar", headers=admin).status_code == 200
    assert client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202


def test_reprocesar_reasigna_la_fila_a_quien_pide_y_el_despachador_manda_su_identidad(ent, workspace):
    from tests.test_proyectos_documentos_repositorio import _pool_call
    p = ent.proyecto()
    a = ent.miembro(p, "a", "CONTRIBUTOR")
    b = ent.miembro(p, "b", "CONTRIBUTOR")
    doc, _ = _ingerido(ent, p, workspace, a)
    assert _fila_rep(ent, doc)[4] == ent._id("a")
    ent.client.portal.call(sql, "UPDATE jax_project_membership SET status='REVOKED' "
                                "WHERE project_id=%s AND user_id=%s", (p.id, ent._id("a")))
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=b)
    assert r.status_code == 202, r.text
    assert _fila_rep(ent, doc)[4] == ent._id("b")
    filas = _pool_call(ent.client, repo.tomar_en_cola, limite=100000)
    owner = [f["owner"] for f in filas if f["id"] == doc][0]
    assert owner.user_id == ent._id("b")


def test_reprocesar_borra_la_copia_vieja_de_entrada_y_deja_el_original_de_fuente(ent, workspace, caplog):
    p = ent.proyecto()
    doc, fuente = _ingerido(ent, p, workspace, ent.dueno, copiar=True)
    ruta_vieja = ent.client.portal.call(sql, "SELECT ruta_entrada FROM project_documents WHERE id=%s", (doc,), True)[0][0]
    assert "/entrada/" in ruta_vieja and (workspace / ruta_vieja).is_file()
    caplog.set_level(logging.INFO)
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    assert not (workspace / ruta_vieja).exists() and not (workspace / ruta_vieja).parent.exists()
    assert fuente.is_file()
    assert any(ruta_vieja in m for m in caplog.messages if "reprocesado" in m)


def test_reprocesar_con_ruta_que_ya_es_de_fuente_no_borra_nada(ent, workspace):
    p = ent.proyecto()
    doc, fuente = _ingerido(ent, p, workspace, ent.dueno)
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    ent.client.portal.call(sql, "UPDATE project_documents SET estado='sin_extractor' WHERE id=%s", (doc,))
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    assert fuente.is_file() and _fila_rep(ent, doc)[1] == f"proyectos/{p.uuid}/fuente/lactovi/a.pdf"


def test_reprocesar_un_documento_oculto_409_no_reprocesable(ent, workspace):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/ocultar", headers=ent.dueno).status_code == 204
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 409 and _code(r) == "no_reprocesable"
    assert _fila_rep(ent, doc)[0] == "sin_extractor"


def test_reprocesar_con_fuente_ilegible_503_y_la_fila_no_cambia(ent, workspace, monkeypatch):
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    antes = _fila_rep(ent, doc)

    def ilegible(*a, **k):
        raise original.FuenteIlegible("EIO")
    monkeypatch.setattr(original, "buscar_original", ilegible)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 503 and _code(r) == "fuente_ilegible"
    assert _fila_rep(ent, doc) == antes


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora los permisos")
def test_reprocesar_con_una_carpeta_de_fuente_sin_permiso_503_y_no_409_falso(ent, workspace):
    p = ent.proyecto()
    doc, fuente = _ingerido(ent, p, workspace, ent.dueno, con_ficha=False)
    fuente.write_bytes(b"otro")                                    # no coincide: hay que recorrer
    cerrada = workspace / "proyectos" / p.uuid / "fuente" / "zzz"
    cerrada.mkdir()
    cerrada.chmod(0)
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    finally:
        cerrada.chmod(0o700)
    assert r.status_code == 503 and _code(r) == "fuente_ilegible"


REP_POR_USUARIO = "proyectos.documentos.reprocesar_por_usuario"
REP_GLOBALES = "proyectos.documentos.reprocesar_globales"


def test_reprocesar_usa_su_propio_cupo_429_sin_recorrer_nada_y_lo_suelta_siempre(ent, workspace, ajustes_en_db,
                                                                                  monkeypatch):
    from proyectos_documentos import cupo_de_reprocesos, cupo_de_subidas
    ajustes_en_db.poner(**{REP_POR_USUARIO: "1", REP_GLOBALES: "1"})
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    usuario = str(ent._id("dueno"))
    antes, antes_subidas = cupo_de_reprocesos.en_uso(), cupo_de_subidas.en_uso()
    recorridos = []
    real = original.buscar_original
    monkeypatch.setattr(original, "buscar_original", lambda *a, **k: (recorridos.append(1), real(*a, **k))[1])
    assert cupo_de_reprocesos.tomar(usuario, por_usuario=1, globales=1)      # un reprocesar ya en curso
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
        assert r.status_code == 429 and _code(r) == "reprocesos_simultaneos", r.text
        assert recorridos == []                                              # no recorrio nada
        assert _fila_rep(ent, doc)[0] == "sin_extractor"
    finally:
        cupo_de_reprocesos.soltar(usuario)
    assert cupo_de_reprocesos.en_uso() == antes
    assert cupo_de_subidas.en_uso() == antes_subidas                         # nunca toco el cupo de subir
    # exito: se suelta
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    assert cupo_de_reprocesos.en_uso() == antes
    # error de negocio (409 original_no_encontrado) despues de tomarlo: se suelta
    ent.client.portal.call(sql, "UPDATE project_documents SET estado='sin_extractor' WHERE id=%s", (doc,))
    monkeypatch.setattr(original, "buscar_original", lambda *a, **k: None)
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 409
    assert cupo_de_reprocesos.en_uso() == antes

    # 503 fuente_ilegible: se suelta
    def ilegible(*a, **k):
        raise original.FuenteIlegible("x")
    monkeypatch.setattr(original, "buscar_original", ilegible)
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 503
    assert cupo_de_reprocesos.en_uso() == antes

    # un error inesperado (500) tambien
    def revienta(*a, **k):
        raise RuntimeError("inesperado")
    monkeypatch.setattr(original, "buscar_original", revienta)
    with pytest.raises(RuntimeError):
        ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert cupo_de_reprocesos.en_uso() == antes
    assert cupo_de_subidas.en_uso() == antes_subidas


def test_el_cupo_global_de_reprocesos_corta_a_otro_usuario(ent, workspace, ajustes_en_db):
    from proyectos_documentos import cupo_de_reprocesos
    ajustes_en_db.poner(**{REP_POR_USUARIO: "1", REP_GLOBALES: "1"})
    p = ent.proyecto()
    otro = ent.miembro(p, "otro", "CONTRIBUTOR")
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    assert cupo_de_reprocesos.tomar("ajeno-en-curso", por_usuario=1, globales=1)
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=otro)
        assert r.status_code == 429 and _code(r) == "reprocesos_simultaneos"
    finally:
        cupo_de_reprocesos.soltar("ajeno-en-curso")
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=otro).status_code == 202


def test_una_subida_concurrente_a_un_reprocesar_en_curso_no_recibe_429_por_su_culpa(ent, workspace, ajustes_en_db):
    from proyectos_documentos import cupo_de_reprocesos
    ajustes_en_db.poner(**{REP_POR_USUARIO: "1", REP_GLOBALES: "1", POR_USUARIO: "1", GLOBALES: "1"})
    p = ent.proyecto()
    usuario = str(ent._id("dueno"))
    assert cupo_de_reprocesos.tomar(usuario, por_usuario=1, globales=1)      # reprocesar en curso, cupo lleno
    try:
        r = ent.subir(p, ent.dueno, [_parte("nueva.pdf", "nueva")])
        assert r.status_code == 202, r.text                                   # la subida usa SU cupo (1/1, libre)
    finally:
        cupo_de_reprocesos.soltar(usuario)


def test_un_reprocesar_no_recibe_429_porque_las_subidas_llenaron_su_cupo(ent, workspace, ajustes_en_db):
    from proyectos_documentos import cupo_de_subidas
    ajustes_en_db.poner(**{POR_USUARIO: "1", GLOBALES: "1"})
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    usuario = str(ent._id("dueno"))
    assert cupo_de_subidas.tomar(usuario, por_usuario=1, globales=1)         # una subida en vuelo, cupo lleno
    try:
        assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 202
    finally:
        cupo_de_subidas.soltar(usuario)


# --------------------------------------------------------- Retry-After en los 429

def test_los_429_de_reprocesar_y_de_subir_llevan_retry_after(ent, workspace, ajustes_en_db):
    from api import proyectos_documentos as api_docs
    from proyectos_documentos import cupo_de_reprocesos, cupo_de_subidas
    assert api_docs.REINTENTAR_DESPUES_S == 2
    ajustes_en_db.poner(**{REP_POR_USUARIO: "1", REP_GLOBALES: "1", POR_USUARIO: "1", GLOBALES: "1"})
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    usuario = str(ent._id("dueno"))
    assert cupo_de_reprocesos.tomar(usuario, por_usuario=1, globales=1)
    assert cupo_de_subidas.tomar(usuario, por_usuario=1, globales=1)
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
        assert r.status_code == 429 and r.headers.get("retry-after") == "2", dict(r.headers)
        r = ent.subir(p, ent.dueno, [_parte("x.pdf", "x")])
        assert r.status_code == 429 and _code(r) == "subidas_simultaneas" and r.headers.get("retry-after") == "2", dict(r.headers)
    finally:
        cupo_de_reprocesos.soltar(usuario)
        cupo_de_subidas.soltar(usuario)
    r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    assert r.status_code == 202 and "retry-after" not in r.headers


# ----------------------------------------------- el invariante absoluto contra el ancla (`proyectos/`)

def _ancla(tmp_path, modo=0o2770):
    (tmp_path / "proyectos").mkdir(exist_ok=True)
    os.chmod(tmp_path / "proyectos", modo)
    return tmp_path / "proyectos"


def test_un_nivel_existente_sin_setgid_falla_cerrado_sin_crear_nada(tmp_path):
    """MINOR-N3: antes solo se comparaba contra el padre; un `proyectos/<uuid>/` ya creado sin setgid (como el que
    dejaba el fchmod viejo) lo aceptaba y colgaba de el todo lo nuevo."""
    ancla = _ancla(tmp_path)
    u = str(uuid.uuid4())
    (ancla / u).mkdir()
    os.chmod(ancla / u, 0o770)                                            # existe, sin setgid
    antes = _foto(tmp_path)
    with pytest.raises(almacen.HerenciaDeCarpetaRota, match="setgid"):
        almacen.abrir_carpeta_lote(tmp_path, u, "lote1")
    assert _foto(tmp_path) == antes                                       # ni entrada/ ni el lote


def test_un_nivel_intermedio_existente_sin_setgid_tambien(tmp_path):
    ancla = _ancla(tmp_path)
    u = str(uuid.uuid4())
    (ancla / u / "entrada").mkdir(parents=True)
    os.chmod(ancla / u, 0o2770)
    os.chmod(ancla / u / "entrada", 0o770)
    with pytest.raises(almacen.HerenciaDeCarpetaRota):
        almacen.abrir_carpeta_lote(tmp_path, u, "lote1")
    assert not (ancla / u / "entrada" / "lote1").exists()


def test_el_ancla_misma_sin_setgid_falla_cerrado(tmp_path):
    _ancla(tmp_path, 0o770)
    with pytest.raises(almacen.HerenciaDeCarpetaRota, match="proyectos"):
        almacen.abrir_carpeta_lote(tmp_path, str(uuid.uuid4()), "lote1")
    assert list((tmp_path / "proyectos").iterdir()) == []


def test_un_nivel_con_otro_grupo_que_el_ancla_falla_cerrado(tmp_path):
    """El grupo se compara con el del ancla. Sin depender de que este usuario pertenezca a otro grupo (en un runner
    puede no pasar): el ancla se simula con un gid distinto del real del nivel."""
    from types import SimpleNamespace
    d = tmp_path / "nivel"
    d.mkdir()
    os.chmod(d, 0o2770)
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        ancla = SimpleNamespace(st_gid=d.stat().st_gid + 12345)
        with pytest.raises(almacen.HerenciaDeCarpetaRota, match="grupo"):
            almacen._verificar_nivel(ancla, fd, fd, "nivel", False)
        assert almacen._verificar_nivel(SimpleNamespace(st_gid=d.stat().st_gid), fd, fd, "nivel", False).st_gid == d.stat().st_gid
    finally:
        os.close(fd)


def test_todo_en_orden_abre_y_crea_con_setgid_y_el_grupo_del_ancla(tmp_path):
    ancla = _ancla(tmp_path)
    u = str(uuid.uuid4())
    (ancla / u).mkdir()
    os.chmod(ancla / u, 0o2770)                                           # existente y correcto: se acepta
    c = almacen.abrir_carpeta_lote(tmp_path, u, "lote1")
    try:
        for d in (ancla / u, ancla / u / "entrada", ancla / u / "entrada" / "lote1"):
            assert d.stat().st_mode & stat.S_ISGID and d.stat().st_gid == ancla.stat().st_gid
    finally:
        c.cerrar()


def test_subir_con_el_uuid_del_proyecto_sin_setgid_500_y_no_crea_nada(ent, workspace):
    p = ent.proyecto()
    (workspace / "proyectos" / p.uuid).mkdir()
    os.chmod(workspace / "proyectos" / p.uuid, 0o770)
    antes = _foto(workspace)
    r = ent.subir(p, ent.dueno, [_parte("a.pdf", "a")])
    assert r.status_code == 500 and _code(r) == "almacen_herencia_rota", r.text
    assert ent.filas(p) == [] and _foto(workspace) == antes


# ------------------------------------------- orden del cupo y rastro de los 409 (ronda 2, MINOR-N4)

def test_el_429_por_usuario_no_toca_la_base_y_el_global_se_toma_justo_antes_del_recorrido(ent, workspace, ajustes_en_db,
                                                                                         monkeypatch):
    from api import proyectos_documentos as api_docs
    from proyectos_documentos import cupo_de_reprocesos
    ajustes_en_db.poner(**{REP_POR_USUARIO: "1", REP_GLOBALES: "1"})
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    usuario = str(ent._id("dueno"))
    antes = cupo_de_reprocesos.en_uso()
    lecturas = []
    real_papel, real_doc = api_docs._con_papel, api_docs.repo.documento_para_reprocesar

    async def papel(*a, **k):
        lecturas.append("papel")
        return await real_papel(*a, **k)

    async def leer(*a, **k):
        lecturas.append("documento")
        return await real_doc(*a, **k)
    monkeypatch.setattr(api_docs, "_con_papel", papel)
    monkeypatch.setattr(api_docs.repo, "documento_para_reprocesar", leer)
    # el cupo POR USUARIO lleno: 429 sin una sola lectura de la base del endpoint
    assert cupo_de_reprocesos.tomar_usuario(usuario, por_usuario=1)
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
        assert r.status_code == 429 and _code(r) == "reprocesos_simultaneos" and lecturas == []
    finally:
        cupo_de_reprocesos.soltar_usuario(usuario)
    # el cupo GLOBAL lleno (otro usuario recorriendo): se lee la base, y el 429 sale antes del recorrido
    assert cupo_de_reprocesos.tomar("otro-recorriendo", por_usuario=1, globales=1)
    recorridos = []
    real = original.buscar_original
    monkeypatch.setattr(original, "buscar_original", lambda *a, **k: (recorridos.append(1), real(*a, **k))[1])
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
        assert r.status_code == 429 and lecturas == ["papel", "documento"] and recorridos == []
        assert cupo_de_reprocesos.en_uso() == (1, {"otro-recorriendo": 1})     # su lugar por usuario se solto
    finally:
        cupo_de_reprocesos.soltar("otro-recorriendo")
    assert cupo_de_reprocesos.en_uso() == antes


def test_el_lugar_por_usuario_se_suelta_tambien_en_404_403_y_409_previos(ent, workspace):
    from proyectos_documentos import cupo_de_reprocesos
    p = ent.proyecto()
    lector = ent.miembro(p, "lector", "VIEWER")
    doc, _ = _ingerido(ent, p, workspace, ent.dueno, estado="listo")
    antes = cupo_de_reprocesos.en_uso()
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=lector).status_code == 403
    assert ent.client.post(f"{P}/{p.id}/documentos/99999999/reprocesar", headers=ent.dueno).status_code == 404
    assert ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno).status_code == 409
    assert cupo_de_reprocesos.en_uso() == antes


def test_los_409_de_reprocesar_dejan_rastro_con_usuario_proyecto_y_documento(ent, workspace, caplog, monkeypatch):
    p = ent.proyecto()
    a, _ = _ingerido(ent, p, workspace, ent.dueno, estado="listo", nombre="a.pdf")
    b, fuente = _ingerido(ent, p, workspace, ent.dueno, nombre="b.pdf")
    fuente.unlink()
    caplog.set_level(logging.INFO)
    assert ent.client.post(f"{P}/{p.id}/documentos/{a}/reprocesar", headers=ent.dueno).status_code == 409
    assert ent.client.post(f"{P}/{p.id}/documentos/{b}/reprocesar", headers=ent.dueno).status_code == 409
    uid_ = str(ent._id("dueno"))
    por_codigo = {c: [m for m in caplog.messages if c in m] for c in ("no_reprocesable", "original_no_encontrado")}
    for codigo, doc in (("no_reprocesable", a), ("original_no_encontrado", b)):
        assert por_codigo[codigo], (codigo, caplog.messages)
        m = por_codigo[codigo][0]
        assert f"usuario {uid_}" in m and f"proyecto {p.id}" in m and f"documento {doc}" in m, m
    # y el 409 de «la fila cambio entre la lectura y el UPDATE»
    c, _ = _ingerido(ent, p, workspace, ent.dueno, nombre="c.pdf")
    caplog.clear()

    async def nada(*a, **k):
        return None
    monkeypatch.setattr("api.proyectos_documentos.repo.reprocesar", nada)
    assert ent.client.post(f"{P}/{p.id}/documentos/{c}/reprocesar", headers=ent.dueno).status_code == 409
    assert any("no_reprocesable" in m and f"documento {c}" in m and f"usuario {uid_}" in m for m in caplog.messages)


def test_el_429_del_cupo_global_de_reprocesar_deja_log_con_quien_pidio(ent, workspace, ajustes_en_db, caplog):
    """MINOR-R2: no se sabe ni se guarda quien tiene el lugar; solo el user_id que pidio y se quedo sin el."""
    from proyectos_documentos import cupo_de_reprocesos
    ajustes_en_db.poner(**{REP_POR_USUARIO: "1", REP_GLOBALES: "1"})
    p = ent.proyecto()
    doc, _ = _ingerido(ent, p, workspace, ent.dueno)
    assert cupo_de_reprocesos.tomar("otro-recorriendo", por_usuario=1, globales=1)
    caplog.set_level(logging.INFO)
    try:
        r = ent.client.post(f"{P}/{p.id}/documentos/{doc}/reprocesar", headers=ent.dueno)
    finally:
        cupo_de_reprocesos.soltar("otro-recorriendo")
    assert r.status_code == 429
    mensajes = [m for m in caplog.messages if "reprocesos_simultaneos" in m or "cupo global" in m]
    assert mensajes, caplog.messages
    m = mensajes[0]
    assert f"usuario {ent._id('dueno')}" in m and f"proyecto {p.id}" in m and f"documento {doc}" in m, m
    assert "otro-recorriendo" not in " ".join(caplog.messages)                    # quien tiene el lugar no se registra
