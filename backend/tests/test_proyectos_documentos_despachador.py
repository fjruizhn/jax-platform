"""Despachador de fondo de documentos de proyecto (Proyectos E2a, T7, 2026-10-03).

LAS MANOS es falsa (`httpx.MockTransport`); la base y el disco son reales (la base
de test y `tmp_path` como `JAX_WORKSPACE_DIR`, nunca `~/jax-workspace`). La base de
test puede traer filas `en_cola` de otros tests, asi que cada prueba mira SOLO los
pedidos de su propio `project_uuid`, y los trabajos que no son suyos los da por en
marcha (sin resultados) para no tocarlos.
"""
import asyncio
import json
import logging
import os
import uuid

import httpx
import pytest

import credencial_las_manos
from proyectos_documentos import despachador
from proyectos_documentos import repositorio as repo
from tests.identidades import cabeceras, sql, uid

P = "/api/proyectos"
CREDENCIAL = "c" * credencial_las_manos.LARGO_MINIMO


class LasManosFalsa:
    """Registra los POST de un proyecto y responde lo que la prueba decida."""

    def __init__(self, project_uuid):
        self.project_uuid = project_uuid
        self.posts = []                       # cuerpos de los POST de ESTE proyecto
        self.post_respuestas = []             # cola de (status, json) o una excepcion; vacia -> 202
        self.estados = {}                     # job_id -> (status, json) del GET
        self.demora = 0.0
        self.sin_credencial = 0
        self.consultas = []                   # job_id de cada GET

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get(credencial_las_manos.ENCABEZADO) != CREDENCIAL:
            self.sin_credencial += 1
        if request.method == "POST":
            cuerpo = json.loads(request.content)
            if cuerpo["project_uuid"] != self.project_uuid:
                return httpx.Response(202, json={"job_id": f"ajeno-{uuid.uuid4().hex}"})
            if self.demora:
                await asyncio.sleep(self.demora)
            self.posts.append(cuerpo)
            if self.post_respuestas:
                siguiente = self.post_respuestas.pop(0)
                if isinstance(siguiente, Exception):
                    raise siguiente
                return httpx.Response(siguiente[0], json=siguiente[1])
            return httpx.Response(202, json={"job_id": f"job-{uuid.uuid4().hex}"})
        job_id = request.url.path.rsplit("/", 1)[-1]
        if job_id in self.estados:
            self.consultas.append(job_id)
            if isinstance(self.estados[job_id], Exception):
                raise self.estados[job_id]
            status, cuerpo = self.estados[job_id]
            return httpx.Response(status, json=cuerpo)
        return httpx.Response(200, json={"job_id": job_id, "estado": "running", "resultados": []})


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("JAX_WORKSPACE_DIR", str(tmp_path))
    return tmp_path


class Entorno:
    def __init__(self, client, workspace, monkeypatch):
        self.client = client
        self.workspace = workspace
        self.tenant = f"desp-{uuid.uuid4().hex}"
        self.h = cabeceras(client, f"{self.tenant}-a", tenant_id=self.tenant)
        self.usuario = int(uid(client, f"{self.tenant}-a", "operator", self.tenant))
        self.email = client.portal.call(sql, "SELECT email FROM jax_users WHERE user_id=%s",
                                        (self.usuario,), True)[0][0]
        self.proyectos = []
        self.project_id, self.uuid = self.proyecto()
        self.las_manos = LasManosFalsa(self.uuid)
        self.cliente_http = httpx.AsyncClient(transport=httpx.MockTransport(self.las_manos))

        async def cliente():
            return self.cliente_http
        monkeypatch.setattr(despachador, "get_http_client", cliente)
        monkeypatch.setenv(credencial_las_manos.VARIABLE, CREDENCIAL)

    def proyecto(self):
        r = self.client.post(P, headers={**self.h, "Idempotency-Key": str(uuid.uuid4())},
                             json={"nombre": "P", "descripcion": None})
        assert r.status_code == 201, r.text
        self.proyectos.append(r.json()["id"])
        return r.json()["id"], r.json()["uuid"]

    def estado_proyecto(self, estado):
        r = self.client.post(f"{P}/{self.project_id}/estado", headers=self.h, json={"estado": estado})
        assert r.status_code == 200, r.text

    def ruta(self, lote, nombre, carpeta="entrada"):
        return f"proyectos/{self.uuid}/{carpeta}/{lote}/{nombre}"

    def archivo(self, ruta, contenido=b"%PDF-1.4 x"):
        destino = self.workspace / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
        return destino

    def insertar(self, ruta, n=None, nombre="x.pdf"):
        n = n if n is not None else uuid.uuid4().int
        return _insertar(self.client, self.project_id, self.usuario, f"{n:064x}"[-64:], nombre, ruta)

    def abrir(self, doc_id, job_id):
        """Deja la fila `pendiente` con ese job, como si el despacho ya hubiera pasado."""
        self.client.portal.call(
            sql, "UPDATE project_documents SET estado='pendiente', job_id=%s WHERE id=%s", (job_id, doc_id))

    def fila(self, doc_id):
        return self.client.portal.call(
            sql, "SELECT estado, job_id, carpeta_procesado, error FROM project_documents WHERE id=%s",
            (doc_id,), True)[0]

    def ciclo(self):
        return _con_pool(self.client, despachador.ciclo)


def _con_pool(client, funcion):
    async def corre():
        from db.connection import get_pool
        return await funcion(await get_pool())
    return client.portal.call(corre)


def _insertar(client, project_id, usuario, sha, nombre, ruta):
    async def corre():
        from db.connection import get_pool
        return await repo.insertar(
            await get_pool(), project_id=project_id, sha256=sha, nombre_original=nombre, ruta_entrada=ruta,
            bytes_=10, tipo="pdf", subido_por=usuario, roles_escritura=("OWNER", "CONTRIBUTOR", "REVIEWER"))
    return client.portal.call(corre)


@pytest.fixture
def e(client, workspace, monkeypatch):
    entorno = Entorno(client, workspace, monkeypatch)
    yield entorno
    despachador._en_incertidumbre.clear()
    for pid in entorno.proyectos:
        client.portal.call(sql, "DELETE FROM project_documents WHERE project_id=%s", (pid,))


def _resultado(archivo, estado, carpeta=None, error=None):
    return {"archivo": archivo, "estado": estado, "carpeta_procesado": carpeta, "error": error}


def _trabajo(job_id, estado, resultados):
    return (200, {"job_id": job_id, "estado": estado, "resultados": resultados})


# ------------------------------------------------------------------ despacho

def test_despacha_en_trozos_de_rutas_por_trabajo(e):
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1, nombre=f"{i}.pdf") for i in range(120)]
    e.ciclo()
    cuerpos = e.las_manos.posts
    assert [len(c["rutas"]) for c in cuerpos] == [50, 50, 20]
    assert all(c["project_uuid"] == e.uuid and c["usuario"] == e.email for c in cuerpos)
    assert [r for c in cuerpos for r in c["rutas"]] == [e.ruta("l1", f"{i}.pdf") for i in range(120)]
    filas = [e.fila(i) for i in ids]
    assert {f[0] for f in filas} == {"pendiente"}
    assert len({f[1] for f in filas}) == 3          # un job_id nuevo por despacho
    assert e.las_manos.sin_credencial == 0


def test_429_deja_en_cola_y_reintenta(e):
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(3)]
    e.las_manos.post_respuestas = [(429, {"detail": "sin capacidad"})]
    e.ciclo()
    assert len(e.las_manos.posts) == 1
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 3
    e.ciclo()
    assert len(e.las_manos.posts) == 2
    filas = [e.fila(i) for i in ids]
    assert {f[0] for f in filas} == {"pendiente"} and len({f[1] for f in filas}) == 1 and filas[0][1]


def test_las_manos_caida_no_pierde_filas(e):
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    e.las_manos.post_respuestas = [httpx.ConnectError("caida")]
    e.ciclo()                                        # no lanza hacia afuera
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 2
    e.ciclo()                                        # ConnectError = "no llego": se reintenta normal
    assert len(e.las_manos.posts) == 2
    assert {e.fila(i)[0] for i in ids} == {"pendiente"}


@pytest.mark.parametrize("falla", [
    httpx.ReadTimeout("x"), httpx.RemoteProtocolError("x"), httpx.ReadError("x")])
def test_desenlace_incierto_no_se_redespacha_hasta_pasar_la_ventana(e, monkeypatch, caplog, falla):
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    ahora = [1000.0]
    monkeypatch.setattr(despachador, "_reloj", lambda: ahora[0])
    e.las_manos.post_respuestas = [falla]
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert len(e.las_manos.posts) == 1
    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert "desenlace incierto: posible trabajo duplicado en LAS MANOS" in msg and e.uuid in msg
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 2
    e.ciclo()                                        # dentro de la ventana: no se reenvia
    assert len(e.las_manos.posts) == 1
    ahora[0] += despachador.VENTANA_DE_INCERTIDUMBRE_SEGUNDOS + 1
    e.ciclo()                                        # pasada la ventana: sale
    assert len(e.las_manos.posts) == 2
    assert {e.fila(i)[0] for i in ids} == {"pendiente"}


def test_422_demasiadas_rutas_deja_en_cola_y_loguea_la_configuracion(e, caplog):
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.las_manos.post_respuestas = [(422, {"detail": "demasiadas rutas en un solo trabajo: 60 > 50"})]
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert e.fila(doc)[:2] == ("en_cola", None)
    assert any("rutas_por_trabajo mayor que el tope de LAS MANOS" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)


@pytest.mark.parametrize("estado,detail", [
    (503, {"code": "base_no_disponible"}),
    (422, {"code": "proyecto_no_activo"}),
    (422, {"code": "project_uuid_invalido"}),
    (500, "boom"),
    (401, "credencial"),
    (403, "credencial"),
    (408, "lento"),
])
def test_respuestas_que_dejan_las_filas_en_cola(e, estado, detail, caplog):
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.las_manos.post_respuestas = [(estado, {"detail": detail})]
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    assert e.fila(doc)[:2] == ("en_cola", None)
    if estado in (401, 403, 408, 500, 503) and detail != {"code": "proyecto_no_activo"}:
        errores = [r for r in caplog.records if r.levelno == logging.ERROR and str(estado) in r.getMessage()]
        assert errores and e.uuid in errores[0].getMessage()
        assert CREDENCIAL not in caplog.text


@pytest.mark.parametrize("estado,detail,causa", [
    (422, {"code": "ruta_invalida"}, "ruta_invalida"),
    (400, "pedido malo", "http_400"),
])
def test_otro_4xx_pasa_las_filas_del_trozo_a_error_con_el_codigo(e, estado, detail, causa):
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.las_manos.post_respuestas = [(estado, {"detail": detail})]
    e.ciclo()
    assert e.fila(doc) == ("error", None, None, causa)
    e.ciclo()                                        # ya no es en_cola: no se reenvia
    assert len(e.las_manos.posts) == 1


def test_dos_ciclos_simultaneos_no_despachan_dos_veces(e):
    [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(3)]
    e.las_manos.demora = 0.3

    async def dos():
        from db.connection import get_pool
        pool = await get_pool()
        await asyncio.gather(despachador.ciclo(pool), despachador.ciclo(pool))
    e.client.portal.call(dos)
    assert len(e.las_manos.posts) == 1


def test_proyecto_archivado_con_filas_en_cola_no_se_despacha(e):
    docs = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    e.estado_proyecto("ARCHIVED")
    e.ciclo()
    assert e.las_manos.posts == []
    assert [e.fila(i)[0] for i in docs] == ["en_cola"] * 2
    e.estado_proyecto("ACTIVE")
    e.ciclo()
    assert len(e.las_manos.posts) == 1
    assert [e.fila(i)[0] for i in docs] == ["pendiente"] * 2


# ------------------------------------------------------------------ sincronizacion

def test_sincroniza_resultados_y_borra_entrada(e):
    ra, rb = e.ruta("l1", "a.pdf"), e.ruta("l1", "b.pdf")
    pa, pb = e.archivo(ra), e.archivo(rb)
    a, b = e.insertar(ra, n=1), e.insertar(rb, n=2)
    e.abrir(a, "J1"), e.abrir(b, "J1")
    e.las_manos.estados["J1"] = _trabajo("J1", "completed", [
        _resultado(ra, "ok", carpeta=f"proyectos/{e.uuid}/procesado/a"),
        _resultado(rb, "error", error="PDF corrupto")])
    e.ciclo()
    assert e.fila(a) == ("listo", "J1", f"proyectos/{e.uuid}/procesado/a", None)
    assert e.fila(b) == ("error", "J1", None, "PDF corrupto")
    assert not pa.exists() and not pb.exists()
    assert not pa.parent.exists()                    # la carpeta del lote quedo vacia y se fue
    assert (e.workspace / "proyectos" / e.uuid / "entrada").is_dir()


@pytest.mark.parametrize("de_las_manos,esperado", [
    ("ok", "listo"), ("parcial", "parcial"), ("error", "error"), ("rechazado", "error"),
    ("sin_extractor", "sin_extractor"), ("cancelado", "cancelado")])
def test_mapa_de_estados(e, de_las_manos, esperado):
    r = e.ruta("l1", "a.pdf")
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J2")
    e.las_manos.estados["J2"] = _trabajo("J2", "completed", [_resultado(r, de_las_manos, error="motivo")])
    e.ciclo()
    assert e.fila(doc)[0] == esperado


def test_trabajo_en_marcha_sin_resultado_del_archivo_pasa_a_procesando(e):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J3")
    e.las_manos.estados["J3"] = _trabajo("J3", "running", [])
    e.ciclo()
    assert e.fila(doc)[0] == "procesando" and p.exists()


def test_trabajo_terminado_sin_resultado_del_archivo_es_error(e):
    r = e.ruta("l1", "a.pdf")
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J4")
    e.las_manos.estados["J4"] = _trabajo("J4", "completed", [])
    e.ciclo()
    assert e.fila(doc)[0] == "error" and e.fila(doc)[3] == "sin_resultado"


def test_trabajo_fallido_pasa_las_filas_a_error(e):
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.abrir(doc, "J5")
    e.las_manos.estados["J5"] = (200, {"job_id": "J5", "estado": "failed", "error": "se cayo", "resultados": []})
    e.ciclo()
    assert e.fila(doc)[0] == "error"


def test_trabajo_perdido_pasa_a_error(e):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J6")
    e.las_manos.estados["J6"] = (404, {"detail": "no encontrado"})
    e.ciclo()
    assert e.fila(doc) == ("error", "J6", None, "trabajo_perdido")
    assert p.exists()                                # sin resultado no hay nada procesado: el archivo se queda


def test_error_largo_se_recorta_y_la_fila_termina(e):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J20")
    e.las_manos.estados["J20"] = _trabajo("J20", "completed", [_resultado(r, "error", error="x" * 1500)])
    e.ciclo()
    estado, _, _, error = e.fila(doc)
    assert estado == "error" and error == "x" * 1000
    assert "J20" not in _con_pool(e.client, repo.trabajos_abiertos)
    assert not p.exists()


def test_un_timeout_corta_la_sincronizacion_de_la_vuelta(e):
    for n, job in enumerate(("J21", "J22"), start=1):
        e.abrir(e.insertar(e.ruta("l1", f"{n}.pdf"), n=n), job)
        e.las_manos.estados[job] = httpx.ReadTimeout("lento")
    e.ciclo()
    assert len(e.las_manos.consultas) == 1           # el segundo ni se pregunto


def test_consulta_fallida_no_cambia_el_estado(e):
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.abrir(doc, "J7")
    e.las_manos.estados["J7"] = (503, {"detail": "x"})
    e.ciclo()
    assert e.fila(doc)[:2] == ("pendiente", "J7")


# ------------------------------------------------------------------ borrado

def test_no_borra_ruta_entrada_bajo_fuente(e):
    r = e.ruta("l1", "original.pdf", carpeta="fuente")
    p = e.archivo(r)
    antes = sorted(x.name for x in p.parent.parent.rglob("*"))
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J8")
    e.las_manos.estados["J8"] = _trabajo("J8", "completed", [_resultado(r, "ok", carpeta="proc/x")])
    e.ciclo()
    assert e.fila(doc)[0] == "listo"
    assert p.exists() and p.read_bytes() == b"%PDF-1.4 x"
    assert sorted(x.name for x in p.parent.parent.rglob("*")) == antes


def test_no_borra_un_archivo_de_otro_proyecto_aunque_diga_entrada(e):
    ajeno = "11111111-1111-4111-8111-111111111111"
    r = f"proyectos/{ajeno}/entrada/l1/a.pdf"
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J9")
    e.las_manos.estados["J9"] = _trabajo("J9", "completed", [_resultado(r, "ok", carpeta="x")])
    e.ciclo()
    assert e.fila(doc)[0] == "listo" and p.exists()


def test_symlink_en_el_camino_del_borrado_no_se_sigue(e, caplog):
    r = e.ruta("l1", "a.pdf")
    senuelo = e.workspace / "senuelo"
    senuelo.mkdir()
    (senuelo / "a.pdf").write_bytes(b"no me toques")
    entrada = e.workspace / "proyectos" / e.uuid / "entrada"
    entrada.mkdir(parents=True)
    (entrada / "l1").symlink_to(senuelo)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J10")
    e.las_manos.estados["J10"] = _trabajo("J10", "completed", [_resultado(r, "ok", carpeta="x")])
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    assert (senuelo / "a.pdf").read_bytes() == b"no me toques"
    assert (entrada / "l1").is_symlink()
    errores = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("borrar" in m.lower() and "l1" in m and "a.pdf" in m for m in errores)
    assert not any(str(e.workspace) in m for m in errores)     # lote y nombre, nunca la ruta completa
    assert e.fila(doc)[0] == "listo"                 # el estado ya se aplico; solo el borrado quedo pendiente


def test_symlink_como_archivo_no_se_borra_ni_se_sigue(e):
    r = e.ruta("l1", "a.pdf")
    fuera = e.workspace / "fuera.txt"
    fuera.write_bytes(b"ajeno")
    destino = e.workspace / r
    destino.parent.mkdir(parents=True)
    destino.symlink_to(fuera)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J11")
    e.las_manos.estados["J11"] = _trabajo("J11", "completed", [_resultado(r, "ok", carpeta="x")])
    e.ciclo()
    assert fuera.read_bytes() == b"ajeno" and destino.is_symlink()


def test_carpeta_del_lote_con_otro_archivo_no_se_quita(e):
    ra, rb = e.ruta("l1", "a.pdf"), e.ruta("l1", "b.pdf")
    pa, pb = e.archivo(ra), e.archivo(rb)
    a = e.insertar(ra, n=1)
    e.insertar(rb, n=2)
    e.abrir(a, "J12")
    e.las_manos.estados["J12"] = _trabajo("J12", "completed", [_resultado(ra, "ok", carpeta="x")])
    e.ciclo()
    assert not pa.exists() and pb.exists()


# ------------------------------------------------------------------ aviso desde la API

def _subir(e, nombre, contenido):
    return e.client.post(f"{P}/{e.project_id}/documentos", headers=e.h,
                         files=[("archivos", (nombre, contenido, "application/octet-stream"))])


def test_subida_avisa_al_despachador(e, monkeypatch):
    llamadas = []
    monkeypatch.setattr(despachador, "despachar_ahora", lambda: llamadas.append(1))
    r = _subir(e, "a.pdf", b"%PDF-1.4 uno")
    assert r.status_code == 202 and len(r.json()["aceptados"]) == 1, r.text
    assert llamadas == [1]
    r = _subir(e, "b.exe", b"MZ")                    # ignorado: cero aceptados
    assert r.status_code == 202 and r.json()["aceptados"] == []
    r = _subir(e, "a.pdf", b"%PDF-1.4 uno")          # duplicado: cero aceptados
    assert r.status_code == 202 and r.json()["aceptados"] == []
    assert llamadas == [1]


def test_un_fallo_del_aviso_no_rompe_la_subida(e, monkeypatch):
    def roto():
        raise RuntimeError("sin loop")
    monkeypatch.setattr(despachador, "despachar_ahora", roto)
    r = _subir(e, "a.pdf", b"%PDF-1.4 dos")
    assert r.status_code == 202 and len(r.json()["aceptados"]) == 1
