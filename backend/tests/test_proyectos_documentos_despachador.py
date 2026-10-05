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
    # El ANCLA de los permisos: `proyectos/` con setgid, como en produccion (almacen.py exige que cada nivel que
    # abre o crea debajo tenga setgid y el grupo de esta carpeta). Un tmp_path pelado no lo trae.
    (tmp_path / "proyectos").mkdir()
    os.chmod(tmp_path / "proyectos", 0o2770)
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
    assert all(c == {"project_uuid": e.uuid, "rutas": c["rutas"]} for c in cuerpos)
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
    (422, {"code": "ruta_invalida"}, "http_4xx"),
    (400, "pedido malo", "http_4xx"),
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
    assert e.fila(b) == ("error", "J1", None, "procesamiento_fallido")
    assert not pa.exists()
    assert pb.exists()                               # error sin carpeta_procesado: puede ser el unico original


def test_con_carpeta_procesado_borra_y_quita_el_lote_vacio(e):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J1b")
    e.las_manos.estados["J1b"] = _trabajo("J1b", "completed", [
        _resultado(r, "error", carpeta=f"proyectos/{e.uuid}/procesado/abc", error="extractor")])
    e.ciclo()
    assert e.fila(doc)[0] == "error"
    assert not p.exists() and not p.parent.exists()  # el original ya esta en fuente/; el lote vacio se va
    assert (e.workspace / "proyectos" / e.uuid / "entrada").is_dir()


@pytest.mark.parametrize("estado,carpeta", [
    ("error", None),                                 # LAS MANOS fallo antes de asegurar fuente/ (ENOSPC, jail...)
    ("rechazado", None),
    ("cancelado", None),
    ("ok", None),
    ("ok", "proyectos/11111111-1111-4111-8111-111111111111/procesado/x"),   # carpeta de OTRO proyecto
    ("ok", "procesado/x"),
    ("ok", "proyectos/{uuid}/fuente/x"),
    ("ok", "proyectos/{uuid}/procesado/../entrada"),
    ("ok", "proyectos/{uuid}/procesado/"),
])
def test_sin_carpeta_procesado_propia_la_copia_de_entrada_se_queda(e, estado, carpeta):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J1c")
    carpeta = carpeta.format(uuid=e.uuid) if carpeta else carpeta
    e.las_manos.estados["J1c"] = _trabajo("J1c", "completed", [_resultado(r, estado, carpeta=carpeta, error="x")])
    e.ciclo()
    assert e.fila(doc)[0] != "pendiente"             # el estado se aplico
    assert p.exists() and p.read_bytes() == b"%PDF-1.4 x"


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


@pytest.mark.parametrize("de_las_manos,causa", [
    ("ok", None), ("parcial", None), ("sin_extractor", None), ("cancelado", None),
    ("error", "procesamiento_fallido"), ("rechazado", "rechazado"), ("raro", "estado_desconocido")])
def test_la_fila_guarda_un_codigo_estable_y_nunca_el_texto_de_las_manos(e, de_las_manos, causa):
    r = e.ruta("l1", "a.pdf")
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J2b")
    e.las_manos.estados["J2b"] = _trabajo("J2b", "completed", [
        _resultado(r, de_las_manos, error=f"Errno 28 /srv/x/{r}: No space left")])
    e.ciclo()
    assert e.fila(doc)[3] == causa
    assert causa is None or causa in despachador.CAUSAS_DE_ERROR


def test_el_detalle_va_al_log_con_rutas_relativas_al_workspace(e, caplog):
    r = e.ruta("l1", "a.pdf")
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J2c")
    detalle = f"[Errno 28] No space left: '{e.workspace}/{r}' (copia en /otra/raiz/fuente/a.pdf)"
    e.las_manos.estados["J2c"] = _trabajo("J2c", "completed", [_resultado(r, "error", error=detalle)])
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    assert e.fila(doc)[3] == "procesamiento_fallido"
    assert f"'{r}'" in caplog.text and "No space left" in caplog.text and str(doc) in caplog.text
    assert str(e.workspace) not in caplog.text and "/otra/raiz" not in caplog.text


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
    assert e.fila(doc)[0] == "error" and e.fila(doc)[3] == "trabajo_fallido"


def test_trabajo_perdido_pasa_a_error(e):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J6")
    e.las_manos.estados["J6"] = (404, {"detail": "no encontrado"})
    e.ciclo()
    assert e.fila(doc) == ("error", "J6", None, "trabajo_perdido")
    assert p.exists()                                # sin resultado no hay nada procesado: el archivo se queda


def test_error_largo_de_las_manos_no_llega_a_la_fila_y_la_fila_termina(e):
    r = e.ruta("l1", "a.pdf")
    p = e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J20")
    e.las_manos.estados["J20"] = _trabajo("J20", "completed", [_resultado(r, "error", error="x" * 1500)])
    e.ciclo()
    estado, _, _, error = e.fila(doc)
    assert estado == "error" and error == "procesamiento_fallido"     # el detalle va al log, no a la fila
    assert "J20" not in _con_pool(e.client, repo.trabajos_abiertos)
    assert p.exists()                                # error sin carpeta_procesado: la copia se queda


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
    e.las_manos.estados["J8"] = _trabajo("J8", "completed", [_resultado(r, "ok", carpeta=f"proyectos/{e.uuid}/procesado/x")])
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
    e.las_manos.estados["J9"] = _trabajo("J9", "completed", [_resultado(r, "ok", carpeta=f"proyectos/{e.uuid}/procesado/x")])
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
    e.las_manos.estados["J10"] = _trabajo("J10", "completed", [_resultado(r, "ok", carpeta=f"proyectos/{e.uuid}/procesado/x")])
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
    e.las_manos.estados["J11"] = _trabajo("J11", "completed", [_resultado(r, "ok", carpeta=f"proyectos/{e.uuid}/procesado/x")])
    e.ciclo()
    assert fuera.read_bytes() == b"ajeno" and destino.is_symlink()


def test_carpeta_del_lote_con_otro_archivo_no_se_quita(e):
    ra, rb = e.ruta("l1", "a.pdf"), e.ruta("l1", "b.pdf")
    pa, pb = e.archivo(ra), e.archivo(rb)
    a = e.insertar(ra, n=1)
    e.insertar(rb, n=2)
    e.abrir(a, "J12")
    e.las_manos.estados["J12"] = _trabajo("J12", "completed", [_resultado(ra, "ok", carpeta=f"proyectos/{e.uuid}/procesado/x")])
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


# ------------------------------------------------------------------ ronda final: menores 6, 7, 9b y 12

def test_saltar_un_proyecto_deja_rastro_con_el_proyecto_y_la_cantidad(e, caplog):
    [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(3)]
    e.las_manos.post_respuestas = [(422, {"detail": {"code": "project_uuid_invalido"}})]
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and e.uuid in r.getMessage()]
    assert avisos and "project_uuid_invalido" in avisos[0] and "3" in avisos[0], avisos


def _con_lock_ajeno(client, nombre_sql, funcion):
    """Otra conexion sostiene un GET_LOCK (nombre como expresion SQL) mientras corre `funcion`."""
    async def corre():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SELECT GET_LOCK({nombre_sql}, 0)")
                assert (await cur.fetchone())[0] == 1
            try:
                return await funcion(pool)
            finally:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT RELEASE_LOCK({nombre_sql})")
    return client.portal.call(corre)


def test_el_lock_del_despacho_es_de_esta_base_y_no_de_todo_el_servidor(e):
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    # El nombre viejo, global al servidor: otra base (otra instancia, otra suite) lo tendria.
    _con_lock_ajeno(e.client, "'proyectos_documentos_despacho'", despachador.ciclo)
    assert e.fila(doc)[0] == "pendiente"
    doc2 = e.insertar(e.ruta("l1", "b.pdf"), n=2)
    # El de ESTA base si frena: un solo despachador por base.
    _con_lock_ajeno(e.client, "CONCAT('proyectos_documentos_despacho:', DATABASE())", despachador.ciclo)
    assert e.fila(doc2)[0] == "en_cola"


def test_fallo_al_borrar_registra_el_tipo_y_la_ruta_relativa_sin_la_absoluta(e, caplog, monkeypatch):
    r = e.ruta("l1", "a.pdf")
    e.archivo(r)
    doc = e.insertar(r, n=1)
    e.abrir(doc, "J30")
    e.las_manos.estados["J30"] = _trabajo("J30", "completed", [
        _resultado(r, "ok", carpeta=f"proyectos/{e.uuid}/procesado/x")])

    def falla(workspace, project_uuid, ruta):
        raise PermissionError(13, "Permission denied", str(workspace / ruta))
    monkeypatch.setattr(despachador, "_borrar_de_entrada", falla)
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    assert "PermissionError" in caplog.text and r in caplog.text
    assert str(e.workspace) not in caplog.text                 # ni en el mensaje ni en un traceback


@pytest.mark.parametrize("ruta", [
    "proyectos/11111111-1111-4111-8111-111111111111/entrada/l1/a.pdf",   # otro proyecto
    "entrada/l1/a.pdf",
    "proyectos/{uuid}/../11111111-1111-4111-8111-111111111111/entrada/l1/a.pdf",
    "/proyectos/{uuid}/entrada/l1/a.pdf",
])
def test_ruta_que_no_es_del_proyecto_no_se_manda_y_queda_ruta_ajena(e, caplog, ruta):
    ruta = ruta.format(uuid=e.uuid)
    doc = e.insertar(ruta, n=1)
    propia = e.insertar(e.ruta("l1", "b.pdf"), n=2)
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert e.fila(doc) == ("error", None, None, "ruta_ajena")
    assert "ruta_ajena" in despachador.CAUSAS_DE_ERROR
    assert [c["rutas"] for c in e.las_manos.posts] == [[e.ruta("l1", "b.pdf")]]   # la ajena no viajo
    assert e.fila(propia)[0] == "pendiente"
    assert any(str(doc) in r.getMessage() and "ruta_ajena" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)


# ------------------------------------------------------------------ motivo del error (ficha.json)
# Frases REALES de `detalle.razon` en las fichas de LACTOVI (13, 15 y 2 casos).
RAZON_OCR_VACIO = "el OCR no devolvio texto util"
RAZON_CONFIANZA = ("mas de la mitad de las palabras reconocidas tienen confianza baja "
                   "(probable ruido o desenfoque) -- ver palabras_dudosas")
RAZON_PDF = "ninguna pagina del PDF dio texto util via OCR"


# TODAS las razones de estado `error` de jax (origin/master, `procesamiento/extractores/ocr.py` y
# `pdf.py`), copiadas literal. Solo las tres primeras son del OCR real.
RAZONES_DE_JAX_QUE_NO_SON_OCR = [
    "sin capa de texto util; corresponde OCR",                                   # pdf.py:341 (no paso por OCR)
    "modo_lectura no soportado: 'x'",                                            # pdf.py:253
    "pdfplumber no esta instalado: No module named 'pdfplumber'",                # pdf.py:262
    "no se pudo leer: ValueError: PDF roto",                                     # pdf.py:325
    "no existe el archivo: fuente/a.pdf",                                        # ocr.py:422
    "pdftoppm no esta instalado (poppler-utils); no se puede rasterizar el PDF para OCR",   # ocr.py:431
    "no se pudo rasterizar el PDF con pdftoppm",                                 # ocr.py:457
    "no se pudo correr tesseract sobre la imagen",                               # ocr.py:467
    "fallo inesperado en OCR: OSError: disco lleno",                             # ocr.py:474
    "tesseract no esta instalado",                                               # ocr.py:400 (sin_extractor)
]


@pytest.mark.parametrize("razon,codigo", [
    (RAZON_OCR_VACIO, "ocr_sin_texto"),                                           # ocr.py:287
    (RAZON_PDF, "ocr_sin_texto"),                                                 # ocr.py:360
    (RAZON_CONFIANZA, "ocr_confianza_baja"),                                      # ocr.py:289
    ("El OCR no devolvió texto útil", "ocr_sin_texto"),                           # tildes y mayusculas
    ("el  OCR   no devolvio texto util ", "ocr_sin_texto"),                       # espacios repetidos
    ("Mas de la mitad de las palabras reconocidas tienen confianza baja (probable ruido o "
     "desenfoque) -- ver palabras_dudosas", "ocr_confianza_baja"),
    *[(r, "procesamiento_fallido") for r in RAZONES_DE_JAX_QUE_NO_SON_OCR],
    ("el OCR no devolvio texto util y ademas algo mas", "procesamiento_fallido"),  # exacta, no «contiene»
    ("la hoja no dio texto util", "procesamiento_fallido"),
    ("", "procesamiento_fallido"),
    (None, "procesamiento_fallido"),
    (7, "procesamiento_fallido"),
    ({"x": 1}, "procesamiento_fallido"),
])
def test_codigo_de_la_razon_es_una_tabla_de_frases_exactas_de_jax(razon, codigo):
    assert despachador.codigo_de_la_razon(razon) == codigo
    assert codigo in despachador.CAUSAS_DE_ERROR


FORMATOS_NO_SOPORTADOS = ["gif_animado", "webp_animado", "gris_16_bits", "coma_flotante", "entero_32_bits",
                          "bmp_16_bits"]
GENERICOS_DEL_CONTRATO = ["archivo_ilegible", "archivo_no_procesable", "imagen_demasiado_grande",
                          "ocr_tiempo_excedido", "ocr_sin_memoria"]


@pytest.mark.parametrize("error,codigo", [
    *[(f"formato_no_soportado:{f}", f"formato_{f}") for f in FORMATOS_NO_SOPORTADOS],   # jax#338
    ("formato_no_soportado:jpeg_2000", "formato_no_soportado"),                          # formato nuevo de jax
    ("formato_no_soportado", "formato_no_soportado"),                                    # sin sufijo
    ("formato_no_soportado:", "formato_no_soportado"),
    ("formato_no_soportado:GIF animado!", "formato_no_soportado"),                       # sufijo fuera de [a-z0-9_]
    ("formato_no_soportado:" + "a" * 41, "formato_no_soportado"),
    *[(g, g) for g in GENERICOS_DEL_CONTRATO],
    ("Traceback: boom en /home/x/y.png", None),                                          # texto arbitrario: no se guarda
    ("archivo_ilegible: detalle extra", None),                                           # exacto, no «empieza con»
    ("", None), (None, None), (7, None), ({"x": 1}, None),
])
def test_codigo_del_error_del_archivo_mapea_el_contrato_de_las_manos(error, codigo):
    assert despachador.codigo_del_error_del_archivo(error) == codigo
    assert codigo is None or codigo in despachador.CAUSAS_DE_ERROR


@pytest.mark.parametrize("error,codigo", [
    ("formato_no_soportado:gif_animado", "formato_gif_animado"),
    ("formato_no_soportado:que_no_conozco", "formato_no_soportado"),
    ("ocr_sin_memoria", "ocr_sin_memoria"),
    ("texto arbitrario con /ruta/absoluta", "procesamiento_fallido"),
])
def test_decidir_usa_el_error_del_archivo_antes_que_la_ficha(error, codigo):
    fila = {"id": 1, "project_uuid": "u"}
    resultado = _resultado("a.gif", "error", carpeta=None, error=error)
    estado, _carpeta, causa = despachador._decidir(fila, resultado, {"estado": "completed"})
    assert (estado, causa) == ("error", codigo)
    assert causa in despachador.CAUSAS_DE_ERROR


def test_un_error_con_codigo_del_contrato_no_lee_la_ficha(e):
    carpeta = _con_ficha(e, json.dumps({"detalle": {"razon": RAZON_OCR_VACIO}}))
    r = e.ruta("l1", "JF1.gif")
    doc = e.insertar(r, n=abs(hash("JF1")))
    e.abrir(doc, "JF1")
    e.las_manos.estados["JF1"] = _trabajo("JF1", "completed", [
        _resultado(r, "error", carpeta=carpeta, error="formato_no_soportado:gif_animado")])
    e.ciclo()
    assert e.fila(doc)[0] == "error" and e.fila(doc)[3] == "formato_gif_animado"


def _con_ficha(e, ficha_texto, carpeta=None, nombre="doc"):
    carpeta = carpeta or f"proyectos/{e.uuid}/procesado/{nombre}"
    destino = e.workspace / carpeta
    destino.mkdir(parents=True, exist_ok=True)
    if ficha_texto is not None:
        (destino / "ficha.json").write_text(ficha_texto)
    return carpeta


def _error_con_carpeta(e, carpeta, job="JM1", estado="error"):
    r = e.ruta("l1", f"{job}.pdf")
    doc = e.insertar(r, n=abs(hash(job)))
    e.abrir(doc, job)
    e.las_manos.estados[job] = _trabajo(job, "completed", [_resultado(r, estado, carpeta=carpeta, error="x")])
    e.ciclo()
    return doc


@pytest.mark.parametrize("razon,codigo", [
    (RAZON_OCR_VACIO, "ocr_sin_texto"), (RAZON_PDF, "ocr_sin_texto"), (RAZON_CONFIANZA, "ocr_confianza_baja"),
    ("algo que no conocemos", "procesamiento_fallido")])
def test_un_error_con_ficha_guarda_el_codigo_estable_del_motivo(e, razon, codigo):
    carpeta = _con_ficha(e, json.dumps({"origen": "fuente/a.pdf", "detalle": {"razon": razon}}))
    doc = _error_con_carpeta(e, carpeta)
    assert e.fila(doc)[0] == "error" and e.fila(doc)[3] == codigo


@pytest.mark.parametrize("ficha", [None, "{no es json", "[]", '{"detalle": 3}', '{"detalle": {"razon": null}}',
                                   '{"detalle": {}}'])
def test_ficha_ausente_o_ilegible_deja_el_generico_y_el_estado_se_aplica(e, ficha):
    carpeta = _con_ficha(e, ficha)
    doc = _error_con_carpeta(e, carpeta)
    assert (e.fila(doc)[0], e.fila(doc)[3]) == ("error", "procesamiento_fallido")


def test_ficha_gigante_no_se_lee(e):
    carpeta = _con_ficha(e, json.dumps({"detalle": {"razon": RAZON_OCR_VACIO}}) + " " * (2 * 1024 * 1024))
    doc = _error_con_carpeta(e, carpeta)
    assert e.fila(doc)[3] == "procesamiento_fallido"


def test_ficha_que_es_un_enlace_no_se_sigue(e, tmp_path_factory):
    carpeta = _con_ficha(e, None)
    fuera = tmp_path_factory.mktemp("fuera") / "ficha.json"
    fuera.write_text(json.dumps({"detalle": {"razon": RAZON_OCR_VACIO}}))
    (e.workspace / carpeta / "ficha.json").symlink_to(fuera)
    doc = _error_con_carpeta(e, carpeta)
    assert e.fila(doc)[3] == "procesamiento_fallido"


def test_carpeta_de_otro_proyecto_no_se_lee(e):
    ajena = f"proyectos/11111111-1111-4111-8111-111111111111/procesado/x"
    _con_ficha(e, json.dumps({"detalle": {"razon": RAZON_OCR_VACIO}}), carpeta=ajena)
    doc = _error_con_carpeta(e, ajena)
    assert e.fila(doc)[3] == "procesamiento_fallido"


def test_sin_carpeta_o_rechazado_no_se_consulta_la_ficha(e):
    doc = _error_con_carpeta(e, None, job="JM2")
    assert e.fila(doc)[3] == "procesamiento_fallido"
    carpeta = _con_ficha(e, json.dumps({"detalle": {"razon": RAZON_OCR_VACIO}}), nombre="rech")
    doc = _error_con_carpeta(e, carpeta, job="JM3", estado="rechazado")
    assert e.fila(doc)[3] == "rechazado"                              # el rechazo no se reescribe


def test_un_fallo_al_leer_la_ficha_no_impide_aplicar_el_resultado(e, monkeypatch):
    from proyectos_documentos import original

    def revienta(*a, **k):
        raise RuntimeError("disco")
    monkeypatch.setattr(original, "leer_ficha", revienta)
    carpeta = _con_ficha(e, json.dumps({"detalle": {"razon": RAZON_OCR_VACIO}}))
    doc = _error_con_carpeta(e, carpeta, job="JM4")
    assert (e.fila(doc)[0], e.fila(doc)[3]) == ("error", "procesamiento_fallido")


# ------------------------------------------------------------------ freno de extractores (Jax#335)
EXTRACTORES = (503, {"detail": {"code": "extractores_no_disponibles"}})


def _clase(ruta):
    ext = ruta.rsplit(".", 1)[-1].lower()
    return {"pdf": "pdf", "xlsx": "excel", "xlsm": "excel", "docx": "word"}.get(ext, "otro")


def test_un_503_extractores_no_disponibles_salta_el_grupo_y_se_sigue_con_los_demas(e):
    pdfs = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1, nombre=f"{i}.pdf") for i in range(3)]
    jpgs = [e.insertar(e.ruta("l1", f"{i}.jpg"), n=100 + i, nombre=f"{i}.jpg") for i in range(3)]
    e.las_manos.post_respuestas = [EXTRACTORES]
    e.ciclo()
    assert len(e.las_manos.posts) == 2                                   # el pdf (503) y luego el jpg
    assert [e.fila(i)[0] for i in pdfs] == ["en_cola"] * 3               # quedan para el proximo ciclo
    assert [e.fila(i)[0] for i in jpgs] == ["pendiente"] * 3


def test_el_503_de_extractores_salta_el_resto_del_grupo_y_los_otros_grupos_salen(e):
    pdfs = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1, nombre=f"{i}.pdf") for i in range(120)]   # 3 trozos
    jpgs = [e.insertar(e.ruta("l1", f"{i}.jpg"), n=1000 + i, nombre=f"{i}.jpg") for i in range(3)]
    e.las_manos.post_respuestas = [EXTRACTORES]
    e.ciclo()
    cuerpos = e.las_manos.posts
    assert [len(c["rutas"]) for c in cuerpos] == [50, 3]               # un solo POST del grupo pdf, y el jpg
    assert all(r.endswith(".pdf") for r in cuerpos[0]["rutas"]) and all(r.endswith(".jpg") for r in cuerpos[1]["rutas"])
    assert [e.fila(i)[0] for i in pdfs] == ["en_cola"] * 120          # 0 despachados del grupo afectado
    assert [e.fila(i)[0] for i in jpgs] == ["pendiente"] * 3


def test_un_503_generico_sigue_cortando_el_ciclo_entero(e, caplog):
    pdf = e.insertar(e.ruta("l1", "a.pdf"), n=1, nombre="a.pdf")
    jpg = e.insertar(e.ruta("l1", "a.jpg"), n=2, nombre="a.jpg")
    e.las_manos.post_respuestas = [(503, {"detail": {"code": "base_no_disponible"}})]
    e.ciclo()
    assert len(e.las_manos.posts) == 1
    assert e.fila(pdf)[0] == "en_cola" and e.fila(jpg)[0] == "en_cola"
    e.las_manos.post_respuestas = [(503, {"detail": "texto"})]
    e.ciclo()
    assert len(e.las_manos.posts) == 2 and e.fila(jpg)[0] == "en_cola"


def test_el_503_de_extractores_con_otro_estado_no_se_confunde(e):
    pdf = e.insertar(e.ruta("l1", "a.pdf"), n=1, nombre="a.pdf")
    jpg = e.insertar(e.ruta("l1", "a.jpg"), n=2, nombre="a.jpg")
    e.las_manos.post_respuestas = [(500, {"detail": {"code": "extractores_no_disponibles"}})]
    e.ciclo()
    assert len(e.las_manos.posts) == 1                                   # solo el 503 salta; un 500 corta


def test_los_trozos_no_mezclan_clases_de_extension(e):
    nombres = ["a.pdf", "b.jpg", "c.xlsx", "d.docx", "e.pdf", "f.png", "g.xlsm", "h.docx", "i.tif"]
    for n, nombre in enumerate(nombres):
        e.insertar(e.ruta("l1", nombre), n=n + 1, nombre=nombre)
    e.ciclo()
    clases = [{_clase(r) for r in c["rutas"]} for c in e.las_manos.posts]
    assert all(len(c) == 1 for c in clases), clases
    assert sorted(next(iter(c)) for c in clases) == ["excel", "otro", "pdf", "word"]
    assert sorted(len(c["rutas"]) for c in e.las_manos.posts) == [2, 2, 2, 3]


def _insertar_en_bloque(e, nombres, desde):
    """Una sola sentencia: filas `en_cola` con sha unico y ruta `entrada/l1/<nombre>` (ids crecientes)."""
    filas = ", ".join(
        f"({e.project_id}, '{desde + i:064x}', '{n}', '{e.ruta('l1', n)}', 10, 'pdf', {e.usuario})"
        for i, n in enumerate(nombres))
    e.client.portal.call(sql, "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, "
                              f"bytes, tipo, subido_por) VALUES {filas}")


def test_mas_pdf_que_el_limite_con_extractores_caidos_no_dejan_sin_ventana_a_las_imagenes(e):
    """MAJOR-N2: con 1.001 pdf en cola (ids bajos), el LIMIT 1000 de la cola se llena solo de pdf y las
    imagenes con ids altos nunca entraban en la ventana mientras faltara pdfplumber."""
    limite = despachador.LIMITE_DE_FILAS_POR_CICLO
    pdfs = [f"p{i:04d}.pdf" for i in range(limite + 1)]
    _insertar_en_bloque(e, pdfs, 10_000)
    imagenes = [e.insertar(e.ruta("l1", f"i{i}.jpg"), n=50_000 + i, nombre=f"i{i}.jpg") for i in range(3)]
    e.las_manos.post_respuestas = [EXTRACTORES]                          # el primer trozo (pdf) da 503
    e.ciclo()
    rutas = [r for c in e.las_manos.posts for r in c["rutas"]]
    assert sum(r.endswith(".pdf") for r in rutas) == 50                  # un solo trozo de pdf se intento
    assert sorted(r for r in rutas if r.endswith(".jpg")) == sorted(e.ruta("l1", f"i{i}.jpg") for i in range(3))
    assert [e.fila(i)[0] for i in imagenes] == ["pendiente"] * 3         # las imagenes SI salen en ese mismo ciclo
    n_pdf_en_cola = e.client.portal.call(
        sql, "SELECT COUNT(*) FROM project_documents WHERE project_id=%s AND estado='en_cola'", (e.project_id,), True)[0][0]
    assert n_pdf_en_cola == limite + 1                                   # ningun pdf se despacho


def test_una_clase_frenada_se_salta_en_todos_los_grupos_del_ciclo_con_un_solo_post(e):
    """Un segundo proyecto con pdf en la misma vuelta no vuelve a pegarle al 503 ya conocido."""
    _insertar_en_bloque(e, [f"a{i}.pdf" for i in range(3)], 70_000)
    otro_id, otro_uuid = e.proyecto()
    e.client.portal.call(sql, "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, "
                              "bytes, tipo, subido_por) VALUES (%s, %s, 'b.pdf', %s, 10, 'pdf', %s)",
                         (otro_id, f"{80_000:064x}", f"proyectos/{otro_uuid}/entrada/l1/b.pdf", e.usuario))
    img = e.insertar(e.ruta("l1", "z.jpg"), n=90_000, nombre="z.jpg")
    e.las_manos.post_respuestas = [EXTRACTORES]
    e.ciclo()
    assert e.fila(img)[0] == "pendiente"
    assert [len(c["rutas"]) for c in e.las_manos.posts] == [3, 1]       # 1 POST pdf (503) + 1 jpg; el de otro proyecto no


def test_mas_filas_en_incertidumbre_que_el_limite_no_dejan_sin_ventana_a_las_sanas(e, monkeypatch):
    """Las filas con desenlace incierto (ReadTimeout...) cuentan contra el LIMIT de la cola y despues el despachador
    las salta: con mas de ellas que el limite, las sanas de atras nunca entraban en la ventana."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 5)
    inciertas = [e.insertar(e.ruta("l1", f"u{i}.pdf"), n=100 + i, nombre=f"u{i}.pdf") for i in range(8)]
    sanas = [e.insertar(e.ruta("l1", f"s{i}.pdf"), n=200 + i, nombre=f"s{i}.pdf") for i in range(3)]
    hasta = despachador._reloj() + 1000
    for i in inciertas:
        despachador._en_incertidumbre[i] = hasta
    e.ciclo()
    rutas = [r for c in e.las_manos.posts for r in c["rutas"]]
    assert sorted(rutas) == sorted(e.ruta("l1", f"s{i}.pdf") for i in range(3))          # solo las sanas salieron
    assert [e.fila(i)[0] for i in sanas] == ["pendiente"] * 3
    assert [e.fila(i)[0] for i in inciertas] == ["en_cola"] * 8                          # las inciertas esperan
    # vencida la ventana, vuelven a entrar
    despachador._en_incertidumbre.clear()
    e.ciclo()
    assert any(e.fila(i)[0] == "pendiente" for i in inciertas)


def test_con_100_o_mas_filas_en_incertidumbre_el_ciclo_no_despacha_nada_y_avisa(e, caplog):
    """MINOR-R1: si LAS MANOS corta las conexiones despues de recibir el pedido, la lista de filas en
    incertidumbre crece y multiplica los trabajos duplicados. Con `2 * rutas_por_trabajo` (100) o mas, el ciclo falla
    cerrado: ningun POST, y un warning con la cantidad."""
    sana = e.insertar(e.ruta("l1", "sana.pdf"), n=1, nombre="sana.pdf")
    hasta = despachador._reloj() + 1000
    for i in range(100):
        despachador._en_incertidumbre[10**12 + i] = hasta               # ids que no existen: solo cuentan
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    assert e.las_manos.posts == [] and e.fila(sana)[0] == "en_cola"
    avisos = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "incertidumbre" in r.getMessage()]
    assert avisos and "100" in avisos[0], caplog.text


def test_con_menos_de_100_filas_en_incertidumbre_se_despacha_normal(e):
    sana = e.insertar(e.ruta("l1", "sana.pdf"), n=1, nombre="sana.pdf")
    hasta = despachador._reloj() + 1000
    for i in range(99):
        despachador._en_incertidumbre[10**12 + i] = hasta
    e.ciclo()
    assert len(e.las_manos.posts) == 1 and e.fila(sana)[0] == "pendiente"


def test_el_freno_de_incertidumbre_sigue_a_rutas_por_trabajo_y_las_vencidas_no_cuentan(e, ajustes_en_db):
    ajustes_en_db.poner(**{"proyectos.documentos.rutas_por_trabajo": "2"})        # el freno pasa a 4
    sana = e.insertar(e.ruta("l1", "sana.pdf"), n=1, nombre="sana.pdf")
    ahora = despachador._reloj()
    for i in range(3):
        despachador._en_incertidumbre[10**12 + i] = ahora + 1000
    for i in range(5):
        despachador._en_incertidumbre[10**13 + i] = ahora - 1                     # ya vencidas: se podan, no cuentan
    e.ciclo()
    assert len(e.las_manos.posts) == 1 and e.fila(sana)[0] == "pendiente"
    despachador._en_incertidumbre[10**12 + 3] = ahora + 1000                      # 4 vigentes = 2 * 2: frena
    otra = e.insertar(e.ruta("l1", "otra.pdf"), n=2, nombre="otra.pdf")
    e.ciclo()
    assert len(e.las_manos.posts) == 1 and e.fila(otra)[0] == "en_cola"


# ------------------------------------------------------------------ causas de error: tablas y paridad

RUTA_CAUSAS_JSON = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "api", "causas_de_error.json")


def test_las_tablas_del_contrato_estan_dentro_de_causas_de_error():
    del_modulo = ({f"formato_{f}" for f in despachador._FORMATOS_NO_SOPORTADOS}
                  | despachador._ERRORES_DEL_ARCHIVO | {"formato_no_soportado"}
                  | set(despachador._RAZONES_DEL_OCR.values()))
    assert del_modulo <= despachador.CAUSAS_DE_ERROR


def test_un_formato_sumado_a_la_tabla_sin_sumarlo_a_las_causas_cae_al_generico(monkeypatch):
    monkeypatch.setattr(despachador, "_FORMATOS_NO_SOPORTADOS",
                        despachador._FORMATOS_NO_SOPORTADOS | {"bmp_32_bits"})
    assert despachador.codigo_del_error_del_archivo("formato_no_soportado:bmp_32_bits") == "formato_no_soportado"
    assert despachador.codigo_del_error_del_archivo("formato_no_soportado:gif_animado") == "formato_gif_animado"


def test_un_codigo_generico_sumado_a_la_tabla_sin_sumarlo_a_las_causas_no_se_guarda(monkeypatch):
    monkeypatch.setattr(despachador, "_ERRORES_DEL_ARCHIVO", despachador._ERRORES_DEL_ARCHIVO | {"ocr_nuevo"})
    assert despachador.codigo_del_error_del_archivo("ocr_nuevo") is None
    assert despachador.codigo_del_error_del_archivo("ocr_sin_memoria") == "ocr_sin_memoria"


def test_causas_de_error_coincide_con_el_json_de_referencia_del_frontend():
    """El frontend exige texto es/en para cada codigo de ese JSON: si CAUSAS_DE_ERROR cambia y el JSON
    no, falla aqui (en los dos sentidos); para regenerarlo: ver el mensaje."""
    with open(RUTA_CAUSAS_JSON, encoding="utf-8") as f:
        referencia = json.load(f)
    assert referencia == sorted(despachador.CAUSAS_DE_ERROR), (
        "frontend/src/api/causas_de_error.json no coincide con CAUSAS_DE_ERROR: regeneralo con "
        "json.dumps(sorted(despachador.CAUSAS_DE_ERROR), indent=2) y agrega el texto es/en de cada codigo nuevo")


def test_una_razon_del_ocr_con_codigo_fuera_de_las_causas_cae_al_generico(monkeypatch):
    monkeypatch.setattr(despachador, "_RAZONES_DEL_OCR",
                        {**despachador._RAZONES_DEL_OCR, despachador._normalizada("razon nueva"): "ocr_rotada"})
    assert despachador.codigo_de_la_razon("razon nueva") == "procesamiento_fallido"
    assert despachador.codigo_de_la_razon(RAZON_OCR_VACIO) == "ocr_sin_texto"


# --- el loop de fondo no corre bajo pytest (2026-10-04) ---------------------
# Hasta hoy start_despachador arrancaba con el lifespan del fixture `client` de
# sesión y repartía documentos de fondo mientras corrían los tests: dos fallos
# intermitentes de CI en el PR #192 (test_mapa_de_estados[parcial-parcial] quedaba
# 'pendiente' porque el ciclo de fondo se adelantaba, y el sync de Gemini contaba
# una 3a llamada porque el ciclo usaba el http_client global ya reemplazado).
# Mismo contrato que start_reintento_de_uso y start_facet_canary.

async def test_start_despachador_no_arranca_bajo_pytest(monkeypatch, caplog):
    ciclos = []

    async def falso(pool):
        ciclos.append(pool)

    monkeypatch.setattr(despachador, "ciclo", falso)
    with caplog.at_level(logging.WARNING, logger=despachador.logger.name):
        await despachador.start_despachador()
    assert ciclos == []
    assert "pytest" in caplog.text


async def test_start_despachador_forzado_corre_un_ciclo_y_se_cancela_limpio(monkeypatch):
    ciclos = []

    async def falso(pool):
        ciclos.append(pool)

    async def pool_falso():
        return "pool"

    async def dormir_largo(_segundos):
        await asyncio.sleep(3600)

    monkeypatch.setattr(despachador, "ciclo", falso)
    monkeypatch.setattr(despachador, "get_pool", pool_falso)
    monkeypatch.setattr(despachador, "_dormir", dormir_largo)
    tarea = asyncio.create_task(despachador.start_despachador(forzado=True))
    await asyncio.sleep(0.05)
    try:
        assert ciclos == ["pool"]
    finally:
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
