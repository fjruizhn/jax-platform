"""API de documentos de un proyecto (Proyectos E2a, T6, 2026-10-03).

Es el punto de entrada de archivos de clientes: cada prueba mira el codigo HTTP,
el cuerpo Y el estado en disco y en la base. El workspace es `tmp_path`
(`JAX_WORKSPACE_DIR`), nunca `~/jax-workspace`. Cada prueba arma su propio
tenant y sus propios proyectos por la API (igual que test_proyectos_api.py).
"""
import asyncio
import hashlib
import io
import os
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from adjuntos import cuota
from proyectos_documentos import almacen, tipos
from proyectos_documentos import repositorio as repo
from tests.identidades import cabeceras, sql, uid

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
    assert stat.S_IMODE(escritos[0].stat().st_mode) == 0o600
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
    destino = tmp_path / "ok.bin"
    total, sha = await almacen.escribir_streaming(_Subida(b"a" * (2 * MIB + 5)), destino, 3 * MIB)
    assert total == 2 * MIB + 5 and sha == hashlib.sha256(b"a" * (2 * MIB + 5)).hexdigest()
    assert destino.stat().st_size == total and stat.S_IMODE(destino.stat().st_mode) == 0o600
    grande = tmp_path / "grande.bin"
    with pytest.raises(almacen.DemasiadoGrande):
        await almacen.escribir_streaming(_Subida(b"a" * (3 * MIB + 1)), grande, 3 * MIB)
    assert not grande.exists()
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
        await almacen.escribir_streaming(sub, tmp_path / "otro.bin", MIB)
    assert sum(leido) <= 2 * MIB and not (tmp_path / "otro.bin").exists()
    # nunca pisa un archivo que ya existe
    with pytest.raises(FileExistsError):
        await almacen.escribir_streaming(_Subida(b"x"), destino, MIB)
    assert destino.stat().st_size == total


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
