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
import time
import os
import re
import threading
import uuid

import httpx
import pytest

import credencial_las_manos
from catalogo_modelos_ejecutor import Desenlace
from proyectos_documentos import despachador
from proyectos_documentos import repositorio as repo
from tests.identidades import cabeceras, sql, uid

P = "/api/proyectos"
CREDENCIAL = "c" * credencial_las_manos.LARGO_MINIMO


# Contrato de idempotencia de LAS MANOS, COPIADO de jax: las_manos/_procesamiento_idempotencia_db_test.py
# (CONTRATO_IDEMPOTENCIA, probado alli contra la ruta real con MariaDB). Cada fila: (estado del trabajo que ya tiene
# esa clave o None, el pedido es el mismo, resultado). Si se cambia una tabla, se cambia la otra. La falsa de abajo
# la cumple, y `test_la_falsa_de_las_manos_cumple_el_contrato_compartido` lo comprueba.
CONTRATO_IDEMPOTENCIA = (
    (None, True, "crea"),
    ("pending", True, "mismo"),
    ("running", True, "mismo"),
    ("cancelling", True, "mismo"),
    ("completed", True, "mismo"),
    ("failed", True, "crea"),
    ("cancelled", True, "crea"),
    ("rejected", True, "crea"),
    ("pending", False, "conflicto"),
    ("failed", False, "conflicto"),
    ("completed", False, "conflicto"),
    # Desenlaces que NO son un trabajo (503 con codigo estable; ver la tabla de jax para su origen).
    ("confirmado_ausente", True, "desconocido"),
    ("almacen_sin_integridad", True, "no_disponible"),
)
ESTADOS_QUE_SE_REINTENTAN = frozenset({"failed", "cancelled", "rejected"})


def decision_del_contrato(estado_previo, mismo_pedido):
    """La regla del contrato, escrita una vez: la usa la falsa y la verifica la tabla."""
    if estado_previo == "almacen_sin_integridad":      # global: antes que nada, como en la ruta real
        return "no_disponible"
    if estado_previo is None:
        return "crea"
    if not mismo_pedido:
        return "conflicto"
    if estado_previo == "confirmado_ausente":
        return "desconocido"
    return "crea" if estado_previo in ESTADOS_QUE_SE_REINTENTAN else "mismo"


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
        self.claves = []                      # `Idempotency-Key` de cada POST de ESTE proyecto (None si no vino)
        self.idempotente = False              # True: cumple el contrato de LAS MANOS (misma clave -> mismo trabajo)
        self.trabajos_creados = []            # job_id de cada trabajo NUEVO que esta falsa creo
        self._por_clave = {}                  # clave -> (job_id, huella del pedido)
        self.estado_de_trabajo = {}           # job_id -> estado del trabajo que esta falsa creo

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
            clave = request.headers.get("Idempotency-Key")
            self.claves.append(clave)
            if self.post_respuestas:
                siguiente = self.post_respuestas.pop(0)
                if isinstance(siguiente, Exception):
                    raise siguiente
                return httpx.Response(siguiente[0], json=siguiente[1])
            huella = json.dumps(sorted(cuerpo["rutas"]))
            if self.idempotente and clave in self._por_clave:
                previo_id, previo_huella = self._por_clave[clave]
                resultado = decision_del_contrato(self.estado_de_trabajo[previo_id], previo_huella == huella)
                if resultado == "conflicto":
                    return httpx.Response(409, json={"detail": {"code": "idempotency_key_reuse"}})
                if resultado == "desconocido":
                    return httpx.Response(503, json={"detail": {"code": "idempotencia_estado_desconocido"}})
                if resultado == "no_disponible":
                    return httpx.Response(503, json={"detail": {"code": "procesamiento_no_disponible"}})
                if resultado == "mismo":
                    return httpx.Response(202, json={"job_id": previo_id, "estado": self.estado_de_trabajo[previo_id]},
                                          headers={"Idempotent-Replayed": "true"})
            job_id = f"job-{uuid.uuid4().hex}"
            self.trabajos_creados.append(job_id)
            self.estado_de_trabajo[job_id] = "pending"
            if self.idempotente and clave is not None:
                self._por_clave[clave] = (job_id, huella)
            return httpx.Response(202, json={"job_id": job_id})
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
    despachador._freno_incertidumbre_activo = False
    despachador._ultimo_aviso_freno_incertidumbre = None
    despachador._aviso_freno_incertidumbre_pendiente = False
    despachador._supresion_freno_incertidumbre_registrada = False
    despachador._aviso_freno_incidente_entregado = False
    despachador._fallos_aviso_freno_incertidumbre = 0
    despachador._ultimo_fallo_aviso_freno_incertidumbre = None
    despachador._ultima_actividad_freno_incertidumbre = None
    despachador._pausa_previa_al_incidente = None
    entorno = Entorno(client, workspace, monkeypatch)
    despachador._cursor_de_cola = 0
    despachador._trozos_trabados.clear()
    despachador._claves_desconocidas.clear()
    yield entorno
    despachador._cursor_de_cola = 0
    despachador._trozos_trabados.clear()
    despachador._claves_desconocidas.clear()
    despachador._en_incertidumbre.clear()
    despachador._freno_incertidumbre_activo = False
    despachador._ultimo_aviso_freno_incertidumbre = None
    despachador._aviso_freno_incertidumbre_pendiente = False
    despachador._supresion_freno_incertidumbre_registrada = False
    despachador._aviso_freno_incidente_entregado = False
    despachador._fallos_aviso_freno_incertidumbre = 0
    despachador._ultimo_fallo_aviso_freno_incertidumbre = None
    despachador._ultima_actividad_freno_incertidumbre = None
    despachador._pausa_previa_al_incidente = None
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
    # Un 503 es GLOBAL: en el segundo ciclo corta de nuevo en el MISMO primer trozo (el del pdf) y el del jpg no se envia;
    # no hay salto ni trozo recordado. Un POST por ciclo.
    e.las_manos.post_respuestas = [(503, {"detail": "texto"})]
    e.ciclo()
    assert len(e.las_manos.posts) == 2 and e.fila(jpg)[0] == "en_cola" and e.fila(pdf)[0] == "en_cola"
    assert despachador._trozos_trabados == set()


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


def test_freno_de_incertidumbre_encola_un_aviso_sin_esperar_telegram(e, monkeypatch):
    """Activar el freno programa Telegram una vez y deja terminar el ciclo aunque el envío siga pendiente."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    envios = []

    async def enviar(cantidad, umbral):
        envios.append((cantidad, umbral))
        started.set()
        await asyncio.to_thread(release.wait)
        finished.set()
        return Desenlace.ENTREGADO

    monkeypatch.setattr(despachador.ajustes, "valor", _ajuste_con_enfriamiento(3600))
    monkeypatch.setattr(despachador, "_enviar_aviso_freno_incertidumbre", enviar)
    ahora = despachador._reloj()
    for i in range(100):
        despachador._en_incertidumbre[10**12 + i] = ahora + 1000
    retornado = threading.Event()

    def ciclo():
        try:
            e.ciclo()
        finally:
            retornado.set()

    worker = threading.Thread(target=ciclo)
    try:
        worker.start()
        assert started.wait(1), "el aviso no se programó"
        assert retornado.wait(1), "el ciclo esperó a que terminara el envío"
        assert not release.is_set(), "la prueba liberó el envío antes de terminar el ciclo"
        assert not finished.is_set()
        assert len(envios) == 1

        e.ciclo()  # el envío sigue pendiente: el ciclo no espera ni duplica
        assert len(envios) == 1
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(timeout=2)
        e.client.portal.call(_esperar_tareas_de_aviso, despachador)


def test_freno_de_incertidumbre_rearma_en_cero_y_respeta_enfriamiento(e, monkeypatch):
    envios = []

    async def enviar(cantidad, umbral):
        envios.append((cantidad, umbral))
        return Desenlace.ENTREGADO

    reloj = [1000.0]
    monkeypatch.setattr(despachador, "_reloj", lambda: reloj[0])
    monkeypatch.setattr(despachador, "_enviar_aviso_freno_incertidumbre", enviar)
    monkeypatch.setattr(despachador.ajustes, "valor", _ajuste_con_enfriamiento(3600))
    try:
        _activar_freno(e, 10**12, reloj[0] + 1000)
        assert len(envios) == 1

        despachador._en_incertidumbre.clear()
        e.ciclo()  # solo cero rearma el freno
        assert not despachador._freno_incertidumbre_activo

        reloj[0] += 3599
        _activar_freno(e, 10**13, reloj[0] + 1000)
        assert len(envios) == 1, "el enfriamiento debe evitar el aviso tras un rebote inmediato"

        despachador._en_incertidumbre.clear()
        e.ciclo()
        reloj[0] += 1
        _activar_freno(e, 10**14, reloj[0] + 1000)
        assert len(envios) == 2
    finally:
        e.client.portal.call(_esperar_tareas_de_aviso, despachador)


@pytest.mark.asyncio
async def test_freno_histeresis_no_rearma_al_bajar_uno_bajo_umbral(monkeypatch):
    """100→99→100 repetido no rearma el freno ni genera avisos repetidos."""
    envios = []

    async def enviar(cantidad, umbral):
        envios.append((cantidad, umbral))
        return Desenlace.ENTREGADO

    reloj = [1000.0]
    monkeypatch.setattr(despachador, "_reloj", lambda: reloj[0])
    monkeypatch.setattr(despachador, "_enviar_aviso_freno_incertidumbre", enviar)
    monkeypatch.setattr(despachador.ajustes, "valor", _ajuste_con_enfriamiento(60))
    monkeypatch.setattr(despachador, "_freno_incertidumbre_activo", False)
    monkeypatch.setattr(despachador, "_ultimo_aviso_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_aviso_freno_incertidumbre_pendiente", False)
    monkeypatch.setattr(despachador, "_aviso_freno_incidente_entregado", False)
    monkeypatch.setattr(despachador, "_fallos_aviso_freno_incertidumbre", 0)
    monkeypatch.setattr(despachador, "_ultimo_fallo_aviso_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_ultima_actividad_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_pausa_previa_al_incidente", None)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)
    try:
        despachador._en_incertidumbre.clear()
        despachador._en_incertidumbre.update({10**16 + i: reloj[0] + 1000 for i in range(100)})
        await despachador._despachar(None)
        await _esperar_tareas_de_aviso(despachador)
        assert len(envios) == 1
        for i in range(5):
            del despachador._en_incertidumbre[10**16 + i]
            await despachador._despachar(None)  # 99: el freno sigue activo
            despachador._en_incertidumbre[10**17 + i] = reloj[0] + 1000
            reloj[0] += 1
            await despachador._despachar(None)  # 100 otra vez, sin haber llegado a cero
            await _esperar_tareas_de_aviso(despachador)
        assert len(envios) == 1
    finally:
        despachador._en_incertidumbre.clear()
        await _esperar_tareas_de_aviso(despachador)


@pytest.mark.asyncio
async def test_freno_reintenta_lectura_y_envio_y_avisa_al_vencer_enfriamiento(monkeypatch, caplog):
    envios = []
    reloj = [1000.0]
    lecturas_fallidas = [True]
    entregas_fallidas = [True]

    async def leer(clave):
        if clave == despachador.ajustes.DOC_RUTAS_POR_TRABAJO:
            return 50
        if clave == despachador.ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S:
            if lecturas_fallidas.pop(0) if lecturas_fallidas else False:
                raise OSError("ajuste temporalmente ilegible")
            return 10
        if clave.endswith("freno_incertidumbre_reintento_s"):
            return 1
        raise AssertionError(f"ajuste inesperado: {clave}")

    async def enviar(cantidad, umbral):
        envios.append((cantidad, umbral))
        if entregas_fallidas:
            entregas_fallidas.pop()
            return Desenlace.FALLO_CIERTO
        return Desenlace.ENTREGADO

    monkeypatch.setattr(despachador, "_reloj", lambda: reloj[0])
    monkeypatch.setattr(despachador.ajustes, "valor", leer)
    monkeypatch.setattr(despachador, "_enviar_aviso_freno_incertidumbre", enviar)
    monkeypatch.setattr(despachador, "_freno_incertidumbre_activo", False)
    monkeypatch.setattr(despachador, "_ultimo_aviso_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_aviso_freno_incertidumbre_pendiente", False)
    monkeypatch.setattr(despachador, "_aviso_freno_incidente_entregado", False)
    monkeypatch.setattr(despachador, "_fallos_aviso_freno_incertidumbre", 0)
    monkeypatch.setattr(despachador, "_ultimo_fallo_aviso_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_ultima_actividad_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_pausa_previa_al_incidente", None)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)
    try:
        despachador._en_incertidumbre.clear()
        despachador._en_incertidumbre.update({10**18 + i: reloj[0] + 1000 for i in range(100)})
        with caplog.at_level(logging.ERROR):
            await despachador._despachar(None)  # lectura fallida: conserva posibilidad de reintento
            await despachador._despachar(None)  # envío falla
            await _esperar_tareas_de_aviso(despachador)
            reloj[0] += 1  # la espera minima tras el fallo (1 s en este ajuste)
            await despachador._despachar(None)  # reintento tras la espera
            await _esperar_tareas_de_aviso(despachador)
        assert len(envios) == 2
        assert "no se pudo leer el ajuste proyectos.documentos.freno_incertidumbre_enfriamiento_s" in caplog.text

        # Nuevo incidente dentro del enfriamiento; mientras siga activo, un ciclo
        # posterior al vencimiento debe entregar un aviso para este incidente.
        despachador._en_incertidumbre.clear()
        await despachador._despachar(None)
        despachador._en_incertidumbre.update({10**19 + i: reloj[0] + 1000 for i in range(100)})
        await despachador._despachar(None)  # suprimido por enfriamiento
        reloj[0] += 10
        await despachador._despachar(None)  # el freno continúa activo: ya puede avisar
        await _esperar_tareas_de_aviso(despachador)
        assert len(envios) == 3
        assert "suprimido por enfriamiento" in caplog.text
    finally:
        despachador._en_incertidumbre.clear()
        await _esperar_tareas_de_aviso(despachador)


@pytest.mark.asyncio
async def test_envio_que_falla_siempre_se_reintenta_con_espera_creciente(monkeypatch):
    """Un envio fallido (p. ej. ReadTimeout con entrega incierta) no se repite en cada ciclo: 30 ciclos de 10 s
    con 60 s de espera base que se duplica dan 3 envios (t=0, 60, 180), no 30."""
    envios = []
    reloj = [1000.0]

    async def enviar(cantidad, umbral):
        envios.append(reloj[0])
        return Desenlace.FALLO_CIERTO

    monkeypatch.setattr(despachador, "_reloj", lambda: reloj[0])
    monkeypatch.setattr(despachador, "_enviar_aviso_freno_incertidumbre", enviar)
    monkeypatch.setattr(despachador.ajustes, "valor", _ajuste_con_enfriamiento(3600))
    monkeypatch.setattr(despachador, "_freno_incertidumbre_activo", False)
    monkeypatch.setattr(despachador, "_ultimo_aviso_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_aviso_freno_incertidumbre_pendiente", False)
    monkeypatch.setattr(despachador, "_aviso_freno_incidente_entregado", False)
    monkeypatch.setattr(despachador, "_fallos_aviso_freno_incertidumbre", 0)
    monkeypatch.setattr(despachador, "_ultimo_fallo_aviso_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_ultima_actividad_freno_incertidumbre", None)
    monkeypatch.setattr(despachador, "_pausa_previa_al_incidente", None)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)
    try:
        despachador._en_incertidumbre.clear()
        despachador._en_incertidumbre.update({10**20 + i: reloj[0] + 100000 for i in range(100)})
        for _ in range(30):
            await despachador._despachar(None)
            await _esperar_tareas_de_aviso(despachador)
            reloj[0] += 10
        assert 2 <= len(envios) <= 4, envios
        huecos = [b - a for a, b in zip(envios, envios[1:])]
        assert huecos == sorted(huecos) and huecos[0] >= 60, huecos
    finally:
        despachador._en_incertidumbre.clear()
        await _esperar_tareas_de_aviso(despachador)



async def _esperar_tareas_de_aviso(despachador):
    if despachador._avisos_freno_incertidumbre:
        await asyncio.gather(*tuple(despachador._avisos_freno_incertidumbre))


def _ajuste_con_enfriamiento(segundos):
    async def leer(clave):
        if clave == despachador.ajustes.DOC_RUTAS_POR_TRABAJO:
            return 50
        if clave == despachador.ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S:
            return segundos
        if clave.endswith("freno_incertidumbre_reintento_s"):
            return 60
        raise AssertionError(f"ajuste inesperado: {clave}")
    return leer


async def _pasada_vacia(*_args):
    return None, 0, 0        # (accion, ultimo id, filas leidas): sin corte y cola recorrida


def _activar_freno(e, primer_id, hasta):
    for i in range(100):
        despachador._en_incertidumbre[primer_id + i] = hasta
    e.ciclo()
    e.client.portal.call(_esperar_tareas_de_aviso, despachador)


@pytest.mark.asyncio
async def test_aviso_freno_usa_emisor_telegram_compartido_y_registra_fallo(monkeypatch, caplog):
    llamadas = []

    async def enviar(mensaje):
        llamadas.append(mensaje)
        return Desenlace.FALLO_CIERTO

    monkeypatch.setattr(despachador, "_enviar_telegram_con_desenlace", enviar, raising=False)
    with caplog.at_level(logging.ERROR):
        await despachador._enviar_aviso_freno_incertidumbre(100, 100)

    assert len(llamadas) == 1
    assert "freno de incertidumbre" in llamadas[0]
    assert "100 filas" in llamadas[0] and "umbral 100" in llamadas[0]
    assert "Telegram" in caplog.text and "entreg" in caplog.text.lower()


@pytest.mark.asyncio
async def test_shutdown_despachador_cancela_y_espera_envios_pendientes(monkeypatch):
    despachador._en_incertidumbre.clear()
    despachador._freno_incertidumbre_activo = False
    despachador._ultimo_aviso_freno_incertidumbre = None
    started = asyncio.Event()
    cancelled = asyncio.Event()
    para_dormir = asyncio.Event()

    async def enviar(cantidad, umbral):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def ciclo(_pool):
        await despachador._despachar(None)

    async def get_pool():
        return None

    async def dormir(_segundos):
        await para_dormir.wait()

    monkeypatch.setattr(despachador, "_corriendo_bajo_pytest", lambda: False)
    monkeypatch.setattr(despachador, "ciclo", ciclo)
    monkeypatch.setattr(despachador, "get_pool", get_pool)
    monkeypatch.setattr(despachador, "_dormir", dormir)
    monkeypatch.setattr(despachador.ajustes, "valor", _ajuste_con_enfriamiento(3600))
    monkeypatch.setattr(despachador, "_enviar_aviso_freno_incertidumbre", enviar)
    for i in range(100):
        despachador._en_incertidumbre[10**15 + i] = despachador._reloj() + 1000

    task = asyncio.create_task(despachador.start_despachador(forzado=True))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(asyncio.sleep(0), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert not despachador._avisos_freno_incertidumbre
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        despachador._en_incertidumbre.clear()
        despachador._freno_incertidumbre_activo = False
        despachador._ultimo_aviso_freno_incertidumbre = None


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
# sesión y repartía documentos mientras corrían los tests: en el PR #192,
# test_mapa_de_estados[parcial-parcial] quedaba 'pendiente' porque el ciclo de
# fondo tenía el GET_LOCK del despacho y el ciclo del test volvía sin hacer nada.
# (El otro intermitente de esa CI, el sync de Gemini, venía de _poll_las_manos:
# ver tests/test_state_tareas_de_fondo_bajo_pytest.py.)
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


# ------------------------------------------------------------------ idempotencia del despacho (auditoria de E3)

def test_el_post_lleva_una_clave_de_idempotencia_en_el_encabezado(e):
    e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.ciclo()
    assert len(e.las_manos.claves) == 1
    clave = e.las_manos.claves[0]
    assert clave and re.fullmatch(r"[A-Za-z0-9._:\-]{16,128}", clave), clave   # el formato que LAS MANOS acepta


def test_cortar_entre_el_202_y_marcar_despachadas_y_reiniciar_produce_un_solo_trabajo(e, monkeypatch):
    """EL RIESGO DE LA AUDITORIA: LAS MANOS acepto (202) y el proceso murio antes de atar las filas al job_id.
    Al reiniciar, la memoria del proceso (`_en_incertidumbre`) esta vacia y las filas siguen `en_cola`: el
    reenvio lleva la MISMA clave y LAS MANOS (que cumple el contrato) devuelve el MISMO trabajo."""
    e.las_manos.idempotente = True
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(3)]

    async def cortado(*a, **k):
        raise RuntimeError("el proceso murio justo despues del 202")
    real = despachador.repo.marcar_despachadas
    monkeypatch.setattr(despachador.repo, "marcar_despachadas", cortado)
    with pytest.raises(RuntimeError):
        e.ciclo()
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 3          # nada quedo atado
    assert len(e.las_manos.trabajos_creados) == 1

    # REINICIO: memoria del proceso vacia, codigo normal.
    monkeypatch.setattr(despachador.repo, "marcar_despachadas", real)
    despachador._en_incertidumbre.clear()
    e.ciclo()

    assert len(e.las_manos.posts) == 2 and e.las_manos.claves[0] == e.las_manos.claves[1]
    assert len(e.las_manos.trabajos_creados) == 1, "el reenvio no debe crear un segundo trabajo (OCR duplicado)"
    filas = [e.fila(i) for i in ids]
    assert {f[0] for f in filas} == {"pendiente"} and {f[1] for f in filas} == {e.las_manos.trabajos_creados[0]}


def test_sin_la_clave_estable_el_mismo_corte_si_duplica_el_trabajo(e, monkeypatch):
    """Control negativo del test anterior: con una clave que cambia en cada envio (el comportamiento de antes)
    el mismo corte deja DOS trabajos en LAS MANOS. Si este test dejara de fallar sin la clave, el de arriba
    no probaria nada."""
    e.las_manos.idempotente = True
    e.insertar(e.ruta("l1", "a.pdf"), n=1)
    monkeypatch.setattr(despachador, "clave_de_idempotencia", lambda *a, **k: f"jxp-doc-v1-{uuid.uuid4().hex}")

    async def cortado(*a, **k):
        raise RuntimeError("el proceso murio justo despues del 202")
    real = despachador.repo.marcar_despachadas
    monkeypatch.setattr(despachador.repo, "marcar_despachadas", cortado)
    with pytest.raises(RuntimeError):
        e.ciclo()
    monkeypatch.setattr(despachador.repo, "marcar_despachadas", real)
    despachador._en_incertidumbre.clear()
    e.ciclo()
    assert len(e.las_manos.trabajos_creados) == 2


def test_un_reencolado_es_otro_intento_logico_y_cambia_la_clave(e):
    """Una fila que termino (error) y vuelve a `en_cola` (reprocesar / re-subida) es OTRO intento: con la
    misma clave LAS MANOS devolveria el trabajo viejo ya terminado y el documento nunca se reprocesaria."""
    e.las_manos.idempotente = True
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    e.ciclo()
    primero = e.las_manos.trabajos_creados[0]
    e.client.portal.call(sql, "UPDATE project_documents SET estado='error', error='procesamiento_fallido' "
                              "WHERE id=%s", (doc,))
    e.client.portal.call(sql, "UPDATE project_documents SET estado='en_cola', job_id=NULL, error=NULL "
                              "WHERE id=%s", (doc,))                       # lo que hace repo.reprocesar
    e.ciclo()
    assert e.las_manos.claves[0] != e.las_manos.claves[1]
    assert len(e.las_manos.trabajos_creados) == 2 and e.fila(doc)[1] == e.las_manos.trabajos_creados[1] != primero


def test_409_de_clave_reusada_no_convierte_documentos_sanos_en_error(e, caplog):
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    e.las_manos.post_respuestas = [(409, {"detail": {"code": "idempotency_key_reuse"}})]
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 2          # no es culpa del documento
    assert any("idempotency_key_reuse" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


def test_422_de_clave_invalida_tampoco_marca_error(e):
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    e.las_manos.post_respuestas = [(422, {"detail": {"code": "idempotency_key_invalida"}})]
    e.ciclo()
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 2


@pytest.mark.parametrize("previo,mismo,esperado", CONTRATO_IDEMPOTENCIA)
def test_la_falsa_de_las_manos_cumple_el_contrato_compartido(e, previo, mismo, esperado):
    """La falsa se comporta como la ruta real de LAS MANOS (misma tabla que su prueba de contrato en jax)."""
    falsa = e.las_manos
    falsa.idempotente = True

    def post(rutas):
        req = httpx.Request("POST", "http://las-manos/procesamiento/trabajos", headers={"Idempotency-Key": "k" * 20},
                            json={"project_uuid": e.uuid, "rutas": rutas})
        return e.client.portal.call(falsa, req)
    viejo = None
    if previo is not None:
        viejo = post(["a.pdf"]).json()["job_id"]
        falsa.estado_de_trabajo[viejo] = previo
    r = post(["a.pdf"] if mismo else ["otro.pdf"])
    if esperado == "conflicto":
        assert r.status_code == 409
    elif esperado == "desconocido":
        assert r.status_code == 503 and r.json()["detail"]["code"] == "idempotencia_estado_desconocido"
    elif esperado == "no_disponible":
        assert r.status_code == 503 and r.json()["detail"]["code"] == "procesamiento_no_disponible"
    elif esperado == "mismo":
        assert r.status_code == 202 and r.json()["job_id"] == viejo
    else:
        assert r.status_code == 202 and r.json()["job_id"] != viejo


def test_un_409_de_clave_en_un_proyecto_no_frena_a_los_demas(e, caplog):
    """El 409 de la clave salta ESE proyecto; el otro (otro tenant/proyecto) sigue despachando en la misma vuelta."""
    mi = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    otro_id, otro_uuid = e.proyecto()
    otro = _insertar(e.client, otro_id, e.usuario, f"{uuid.uuid4().int:064x}"[-64:], "b.pdf",
                     f"proyectos/{otro_uuid}/entrada/l1/b.pdf")
    e.las_manos.post_respuestas = [(409, {"detail": {"code": "idempotency_key_reuse"}})]
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert e.fila(mi)[:2] == ("en_cola", None)                       # el trozo con clave rechazada no se toco
    assert e.fila(otro)[0] == "pendiente"                            # el otro proyecto salio en la misma vuelta
    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert e.uuid in msg and "se salta el trozo del proyecto" in msg and "idempotency_key_reuse" in msg
    # El hash corto de la clave (el mismo `abreviar` de LAS MANOS) une esta linea con la suya.
    assert despachador.abreviar_clave(e.las_manos.claves[0]) in msg and e.las_manos.claves[0] not in msg


def test_ocultar_y_restaurar_una_fila_en_cola_no_cambia_su_clave(e):
    """Ocultar y restaurar no son un intento nuevo: la clave sigue igual. Un reencolado de verdad si la cambia."""
    doc = e.insertar(e.ruta("l1", "a.pdf"), n=1)

    def clave():
        filas = [f for f in _con_pool(e.client, lambda pool: repo.tomar_en_cola(pool, limite=100000))
                 if f["id"] == doc]
        return despachador.clave_de_idempotencia(e.uuid, filas)
    antes = clave()
    assert _con_pool(e.client, lambda pool: repo.ocultar(pool, project_id=e.project_id, documento_id=doc,
                                                         user_id=e.usuario)) is True
    assert clave() == antes
    assert _con_pool(e.client, lambda pool: repo.restaurar(pool, project_id=e.project_id, documento_id=doc)) is True
    assert clave() == antes
    e.client.portal.call(sql, "UPDATE project_documents SET estado='error', error='procesamiento_fallido' WHERE id=%s", (doc,))
    e.client.portal.call(sql, "UPDATE project_documents SET estado='en_cola', job_id=NULL, error=NULL WHERE id=%s", (doc,))
    assert clave() != antes


def test_el_hash_corto_de_la_clave_es_el_mismo_que_el_de_las_manos():
    """Vector fijo, copiado de la prueba de jax (`abreviar` de procesamiento_idempotencia): si una de las dos
    cambia, las lineas de log de las dos puntas dejan de unirse."""
    assert despachador.abreviar_clave("jxp-doc-a2dad605d3914f47985f83924083ec04") == "776783dd9d97"


def test_un_409_que_llena_la_ventana_no_deja_sin_despacho_al_otro_proyecto(e, monkeypatch):
    """MINOR-D: con LIMITE=3 y tres filas del proyecto rechazado, la ventana se llenaba con filas que se saltaban
    y el otro proyecto nunca entraba. Ahora la pasada siguiente excluye al (proyecto, dueno) saltado."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    mias = [e.insertar(e.ruta("l1", f"a{i}.pdf"), n=None) for i in range(3)]
    otro_id, otro_uuid = e.proyecto()
    otro = _insertar(e.client, otro_id, e.usuario, f"{uuid.uuid4().int:064x}"[-64:], "b.pdf",
                     f"proyectos/{otro_uuid}/entrada/l1/b.pdf")
    e.las_manos.post_respuestas = [(409, {"detail": {"code": "idempotency_key_reuse"}})] * 10
    e.ciclo()                                           # UNA vuelta alcanza
    assert [e.fila(i)[0] for i in mias] == ["en_cola"] * 3
    assert e.fila(otro)[0] == "pendiente"


class _Malos:
    """La falsa compara `cuerpo["project_uuid"] != self.project_uuid`: con esto, los proyectos que estan en `s`
    responden lo encolado (409) y el resto responde 202."""
    def __init__(self):
        self.s = set()

    def __ne__(self, otro):
        return otro not in self.s

    def __eq__(self, otro):
        return otro in self.s


def test_52_proyectos_en_409_mas_uno_sano_el_sano_se_despacha_con_el_cursor(e, monkeypatch):
    """MINOR-G: con LIMITE=3 y 52 proyectos de 3 filas cuyo trozo LAS MANOS rechaza, las listas de exclusion y el
    tope de 50 pasadas dejaban al proyecto 53 sin despacho. El cursor por id recorre la cola entera en UNA vuelta."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    consultas = {"n": 0}
    real = repo.tomar_en_cola

    async def contada(pool, **kw):
        consultas["n"] += 1
        return await real(pool, **kw)
    monkeypatch.setattr(repo, "tomar_en_cola", contada)
    malos = _Malos()
    e.las_manos.project_uuid = malos
    base = uuid.uuid4().int % 10**12 * 10**6
    for p in range(52):
        pid, puuid = e.proyecto()
        malos.s.add(puuid)
        filas = ", ".join(f"({pid}, '{base + p * 1000 + i:064x}', 'a{i}.pdf', "
                          f"'proyectos/{puuid}/entrada/l1/a{i}.pdf', 10, 'pdf', {e.usuario})" for i in range(3))
        e.client.portal.call(sql, "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, "
                                  f"bytes, tipo, subido_por) VALUES {filas}")
    sano_id, sano_uuid = e.proyecto()
    sano = _insertar(e.client, sano_id, e.usuario, f"{uuid.uuid4().int:064x}"[-64:], "b.pdf",
                     f"proyectos/{sano_uuid}/entrada/l1/b.pdf")
    e.las_manos.post_respuestas = [(409, {"detail": {"code": "idempotency_key_reuse"}})] * 1000
    e.ciclo()
    assert e.fila(sano)[0] == "pendiente"
    # una consulta por ventana (157 filas / 3 = 53 ventanas, mas una final): no 50 consultas por cada pasada
    assert consultas["n"] <= 60, consultas
    assert despachador._cursor_de_cola == 0, "recorrida la cola, el cursor vuelve al principio"


def test_un_ciclo_cortado_retoma_desde_el_inicio_de_la_ventana_cortada(e, monkeypatch):
    """El cursor persiste entre vueltas: si LAS MANOS corta (429) en la ventana 2, la vuelta siguiente empieza ahi
    y no se saltea las filas que no llegaron a despacharse."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(9)]
    e.las_manos.post_respuestas = [(202, {"job_id": "job-1"}), (429, {"detail": "sin capacidad"})]
    e.ciclo()                              # ventana 1 (ids 1-3) sale; ventana 2 (4-6) recibe 429 y corta
    assert [e.fila(i)[0] for i in ids[:3]] == ["pendiente"] * 3
    assert [e.fila(i)[0] for i in ids[3:]] == ["en_cola"] * 6
    assert despachador._cursor_de_cola == ids[2], despachador._cursor_de_cola
    e.ciclo()                              # retoma en la ventana 2: ahora si salen todas
    assert {e.fila(i)[0] for i in ids} <= {"pendiente", "procesando"}          # la sincronizacion pudo avanzar el 1.o
    assert [e.fila(i)[1] is not None for i in ids] == [True] * 9
    assert despachador._cursor_de_cola == 0


def _otro_proyecto_con_filas(e, k, estado_archivado=False):
    """Un proyecto del mismo dueno con `k` filas en_cola (opcionalmente ARCHIVED despues de insertarlas)."""
    pid, puuid = e.proyecto()
    base = uuid.uuid4().int % 10**12 * 10**6
    ids = [_insertar(e.client, pid, e.usuario, f"{base + i:064x}"[-64:], f"o{i}.pdf",
                     f"proyectos/{puuid}/entrada/l1/o{i}.pdf") for i in range(k)]
    if estado_archivado:
        r = e.client.post(f"{P}/{pid}/estado", headers=e.h, json={"estado": "ARCHIVED"})
        assert r.status_code == 200, r.text
    return pid, puuid, ids


def test_una_clave_desconocida_salta_ese_proyecto_y_no_frena_a_los_demas(e, caplog):
    """MAJOR-A: el 503 `idempotencia_estado_desconocido` es PERMANENTE para esa clave (7 dias hasta la purga): cortar la
    vuelta frenaria a todos los tenants en cada ciclo. Se salta el proyecto, el sano sale en la misma vuelta, el
    cursor no se envenena y el log lleva el hash corto de la clave."""
    mi = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    _pid, _puuid, otros = _otro_proyecto_con_filas(e, 1)
    e.las_manos.post_respuestas = [(503, {"detail": {"code": "idempotencia_estado_desconocido"}})]
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert e.fila(mi)[:2] == ("en_cola", None)                          # el proyecto desconocido sigue esperando
    assert e.fila(otros[0])[0] == "pendiente"                           # el otro salio en la MISMA vuelta
    assert despachador._cursor_de_cola == 0
    msg = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert "DESCONOCIDO" in msg and e.uuid in msg and despachador.abreviar_clave(e.las_manos.claves[0]) in msg
    assert e.las_manos.claves[0] not in msg and "jax:docs/runbooks/idempotencia-clave-desconocida.md" in msg


def test_no_disponible_corta_la_vuelta_pero_no_envenena_el_cursor(e, caplog):
    """MAJOR-A: `procesamiento_no_disponible` es global y transitorio: corta (nada que despachar mientras dure), con el
    hash corto en el log, y al volver el almacen la cola sale; el cursor no queda atrapado."""
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    e.las_manos.post_respuestas = [(503, {"detail": {"code": "procesamiento_no_disponible"}})]
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    assert [e.fila(i)[:2] for i in ids] == [("en_cola", None)] * 2
    assert despachador._cursor_de_cola == 0
    assert any(despachador.abreviar_clave(e.las_manos.claves[0]) in r.getMessage() and "sin integridad" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)
    e.ciclo()                                                            # el almacen volvio
    assert {e.fila(i)[0] for i in ids} == {"pendiente"} and despachador._cursor_de_cola == 0


def test_un_trozo_que_corta_por_un_500_de_su_proyecto_no_bloquea_la_cola(e, monkeypatch):
    """A sano (ids 1-3), V con un 500 persistente (4-6) y B (7-12), con ventanas de 3. V corta el ciclo UNA vez y entra al
    conjunto de trabados; en el ciclo siguiente se salta y B sale. Antes el corte caia siempre en V y B no salia nunca."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    _a, _au, a_ids = _otro_proyecto_con_filas(e, 3)
    v_ids = [e.insertar(e.ruta("l1", f"v{i}.pdf"), n=i + 1) for i in range(3)]       # V = el proyecto de `e`: 500
    _b, _bu, b_ids = _otro_proyecto_con_filas(e, 6)
    e.las_manos.post_respuestas = [(500, {"detail": "boom"})] * 20
    e.ciclo()
    assert {e.fila(i)[0] for i in a_ids} == {"pendiente"} and {e.fila(i)[0] for i in b_ids} == {"en_cola"}
    assert despachador._trozos_trabados == {v_ids[0]} and despachador._cursor_de_cola == a_ids[-1]
    posts = len(e.las_manos.posts)
    e.ciclo()                                                          # V se salta SIN enviarse; B sale
    assert len(e.las_manos.posts) == posts, "el trozo trabado no se vuelve a enviar en esta pasada"
    assert {e.fila(i)[0] for i in b_ids} == {"pendiente"}
    assert {e.fila(i)[:2] for i in v_ids} == {("en_cola", None)}       # V sigue esperando, no paso a error
    assert despachador._cursor_de_cola == 0 and despachador._trozos_trabados == set()    # pasada completa: se vacia


def test_dos_trozos_trabados_en_la_misma_ventana_la_cola_sale_en_trabados_mas_uno_ciclos(e, monkeypatch):
    """Escenario del auditor: A sano, V y W trabados (500) en la MISMA ventana y B detras. Cota: k trabados + 1 ciclos:
    con k = 2, B sale en el ciclo 3 y NO antes."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 9)
    malos = _Malos()
    e.las_manos.project_uuid = malos
    _a, _au, a_ids = _otro_proyecto_con_filas(e, 3)                    # A sano: ids 1-3
    v, vu, v_ids = _otro_proyecto_con_filas(e, 3)
    w, wu, w_ids = _otro_proyecto_con_filas(e, 3)
    malos.s.update({vu, wu})                                           # V y W consumen los 500; el resto responde 202
    _b, _bu, b_ids = _otro_proyecto_con_filas(e, 6)                    # B detras: segunda ventana
    e.las_manos.post_respuestas = [(500, {"detail": "boom"})] * 50
    e.ciclo()                                                          # A sale; V corta -> {V}
    assert {e.fila(i)[0] for i in a_ids} == {"pendiente"} and len(despachador._trozos_trabados) == 1
    e.ciclo()                                                          # V saltado; W corta -> {V, W}
    assert len(despachador._trozos_trabados) == 2 and {e.fila(i)[0] for i in b_ids} == {"en_cola"}
    posts = len(e.las_manos.posts)
    e.ciclo()                                                          # V y W saltados; B sale; pasada completa
    assert {e.fila(i)[0] for i in b_ids} == {"pendiente"}, "B tenia que salir en el ciclo k + 1 = 3"
    assert len(e.las_manos.posts) == posts and despachador._trozos_trabados == set() and despachador._cursor_de_cola == 0
    assert {e.fila(i)[:2] for i in v_ids + w_ids} == {("en_cola", None)}


def test_las_filas_de_id_bajo_reactivadas_detras_del_cursor_salen_en_k_mas_uno_ciclos(e, monkeypatch):
    """Escenario del auditor (r7): un proyecto sano primero (cursor = su ultimo id), V con DOS trozos que dan 500 y un
    proyecto reactivado con ids bajos DETRAS del cursor. Cota: con k = 2 trozos trabados, las filas bajas salen k + 1 = 3
    ciclos despues de reactivarse (V1 se salta y V2 se traba; se completa la pasada y el cursor vuelve a 0; se leen)."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    pid, _puuid, bajas = _otro_proyecto_con_filas(e, 3, estado_archivado=True)         # ids bajos, fuera de la cola
    _a, _au, a_ids = _otro_proyecto_con_filas(e, 3)                                     # sano: ventana 1
    v_ids = [e.insertar(e.ruta("l1", f"v{i}.pdf"), n=i + 1) for i in range(6)]          # V = proyecto de `e`: 2 trozos en 500
    e.las_manos.post_respuestas = [(500, {"detail": "boom"})] * 50
    e.ciclo()                                                          # A sale; V1 corta y se traba
    assert {e.fila(i)[0] for i in a_ids} == {"pendiente"}
    assert despachador._cursor_de_cola == a_ids[-1] > bajas[-1], "el cursor tiene que estar DETRAS de las filas bajas"
    assert despachador._trozos_trabados == {v_ids[0]}
    r = e.client.post(f"{P}/{pid}/estado", headers=e.h, json={"estado": "ACTIVE"})
    assert r.status_code == 200, r.text                                # las de id bajo vuelven a la cola, detras del cursor
    e.ciclo()                                                          # V1 se salta; V2 corta y se traba
    assert despachador._trozos_trabados == {v_ids[0], v_ids[3]}
    assert {e.fila(i)[:2] for i in bajas} == {("en_cola", None)}, "todavia detras del cursor"
    e.ciclo()                                                          # V1 y V2 se saltan; pasada completa: cursor a 0
    assert despachador._cursor_de_cola == 0 and despachador._trozos_trabados == set()
    e.ciclo()                                                          # k + 1 = 3 ciclos tras reactivar: las bajas salen
    assert {e.fila(i)[0] for i in bajas} <= {"pendiente", "procesando"}
    assert all(e.fila(i)[1] is not None for i in bajas), "las filas bajas tienen que haberse despachado"
    assert {e.fila(i)[:2] for i in v_ids} == {("en_cola", None)}       # V sigue esperando, no paso a error


def test_con_un_corte_global_sostenido_las_filas_reactivadas_detras_del_cursor_se_leen_al_recuperarse(e, monkeypatch):
    """Sonda A del auditor (r7): un 429 sostenido con el cursor adelantado. Un corte global en la primera ventana del
    ciclo no hace progreso: el cursor vuelve a 0, y las filas reactivadas con ids bajos se leen en la PRIMERA pasada tras
    recuperarse (sin esto esperaban a que la pasada llegara al final de la cola, sin tope mientras el corte siguiera)."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    pid, bajas_uuid, bajas = _otro_proyecto_con_filas(e, 3, estado_archivado=True)
    altas = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(6)]
    afectados = _Malos()                                               # el 429 es de LAS MANOS entera: lo reciben los dos proyectos
    afectados.s.update({e.uuid, bajas_uuid})
    e.las_manos.project_uuid = afectados
    e.las_manos.post_respuestas = [(202, {"job_id": "job-1"})] + [(429, {"detail": "sin capacidad"})] * 4
    e.ciclo()                                                          # ventana 1 sale; la 2 recibe 429: corta con progreso
    assert despachador._cursor_de_cola == altas[2] > bajas[-1]
    r = e.client.post(f"{P}/{pid}/estado", headers=e.h, json={"estado": "ACTIVE"})
    assert r.status_code == 200, r.text
    for _ in range(3):                                                 # el 429 sigue: ningun POST sale
        e.ciclo()
    assert {e.fila(i)[:2] for i in bajas} == {("en_cola", None)}
    assert despachador._cursor_de_cola == 0, "un corte global sin progreso reinicia el cursor"
    e.ciclo()                                                          # LAS MANOS se recupero: primera pasada desde 0
    assert all(e.fila(i)[1] is not None for i in bajas), "las bajas tienen que leerse en la primera pasada tras recuperar"
    assert all(e.fila(i)[1] is not None for i in altas)


def test_un_error_global_no_duplica_los_post_y_la_cota_cuenta_los_trozos_saltados(e):
    """Un 429 (global) corta en el primer trozo que lo recibe y no entra al conjunto de trabados, asi que cada ciclo
    manda la MISMA cantidad de POST (no alternos ni crecientes). Sin claves desconocidas por delante, es UN POST por
    ciclo; con ellas, la cota es 1 + un POST por cada trozo saltado de la ventana por delante del corte (cada uno recibio
    su 503 `idempotencia_estado_desconocido`; no es un doble envio: es otro trozo)."""
    pdf = e.insertar(e.ruta("l1", "a.pdf"), n=1, nombre="a.pdf")
    jpg = e.insertar(e.ruta("l1", "a.jpg"), n=2, nombre="a.jpg")
    e.las_manos.post_respuestas = [(429, {"detail": "sin capacidad"})] * 10
    for ciclo in range(1, 5):
        e.ciclo()
        assert len(e.las_manos.posts) == ciclo, f"ciclo {ciclo}: {len(e.las_manos.posts)} POST"
        assert despachador._trozos_trabados == set()
    assert e.fila(pdf)[0] == "en_cola" and e.fila(jpg)[0] == "en_cola"
    del e.las_manos.posts[:]
    e.las_manos.post_respuestas = [(503, {"detail": {"code": "idempotencia_estado_desconocido"}}),
                                   (429, {"detail": "sin capacidad"})] * 5
    for ciclo in range(1, 4):                                          # un trozo saltado (503) + el que corta (429) = 2
        e.ciclo()
        assert len(e.las_manos.posts) == 2 * ciclo, f"ciclo {ciclo}: {len(e.las_manos.posts)} POST (cota: 1 + 1 saltado)"
        assert despachador._trozos_trabados == set()


def test_el_conjunto_de_trabados_deja_rastro_al_entrar_al_saltar_y_al_vaciarse(e, monkeypatch, caplog):
    """P-2: un trozo que entra al conjunto de trabados, cada ciclo que lo salta (UN log por ciclo, con el conteo y los ids
    acotados) y el vaciado al completar la pasada se registran; el log del 500 ya no dice que «se reintenta» a secas."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    v_ids = [e.insertar(e.ruta("l1", f"v{i}.pdf"), n=i + 1) for i in range(3)]
    _b, _bu, b_ids = _otro_proyecto_con_filas(e, 3)
    e.las_manos.post_respuestas = [(500, {"detail": "boom"})] * 20
    with caplog.at_level(logging.WARNING):
        e.ciclo()                                                      # V corta y entra al conjunto
    msgs = [r.getMessage() for r in caplog.records]
    entrada = [m for m in msgs if "entra al conjunto de trabados" in m]
    assert len(entrada) == 1 and str(v_ids[0]) in entrada[0] and e.uuid in entrada[0]
    texto_500 = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "respondio 500" in r.getMessage()]
    assert len(texto_500) == 1 and "trabado" in texto_500[0] and "se reintenta en la vuelta siguiente" not in texto_500[0]
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        e.ciclo()                                                      # V se salta; B sale; pasada completa
    msgs = [r.getMessage() for r in caplog.records]
    saltos = [m for m in msgs if "saltado(s) sin enviarse" in m]
    assert len(saltos) == 1 and "1 trozo(s)" in saltos[0] and str(v_ids[0]) in saltos[0], msgs
    vaciado = [m for m in msgs if "se vacia el conjunto de trabados" in m]
    assert len(vaciado) == 1 and str(v_ids[0]) in vaciado[0], msgs
    assert {e.fila(i)[0] for i in b_ids} == {"pendiente"}


def test_el_log_de_trozos_saltados_es_uno_por_ciclo_y_acota_los_ids(e, monkeypatch, caplog):
    """P-2: con mas trozos trabados que `MAXIMO_IDS_EN_EL_LOG` el log nombra solo los primeros y cuenta el resto, y es UNO
    por ciclo (no uno por trozo). 120 filas = 3 trozos de [50, 50, 20], los tres trabados."""
    monkeypatch.setattr(despachador, "MAXIMO_IDS_EN_EL_LOG", 2)
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1, nombre=f"{i}.pdf") for i in range(120)]
    despachador._trozos_trabados.update({ids[0], ids[50], ids[100]})
    with caplog.at_level(logging.WARNING):
        e.ciclo()
    saltos = [r.getMessage() for r in caplog.records if "saltado(s) sin enviarse" in r.getMessage()]
    assert len(saltos) == 1 and "3 trozo(s)" in saltos[0] and ", ..." in saltos[0], saltos
    assert str(ids[0]) in saltos[0] and str(ids[50]) in saltos[0] and str(ids[100]) not in saltos[0]
    assert e.las_manos.posts == []


def test_el_422_de_formato_de_clave_es_global_y_da_un_solo_error(e, caplog):
    """MINOR-3: el formato lo arma este codigo para todos los trozos: corta la vuelta y deja UN ERROR, no uno por trozo."""
    e.insertar(e.ruta("l1", "a.pdf"), n=1, nombre="a.pdf")
    e.insertar(e.ruta("l1", "a.jpg"), n=2, nombre="a.jpg")
    e.las_manos.post_respuestas = [(422, {"detail": {"code": "idempotency_key_invalida"}})] * 5
    with caplog.at_level(logging.ERROR):
        e.ciclo()
    errores = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "FORMATO" in r.getMessage()]
    assert len(errores) == 1 and len(e.las_manos.posts) == 1


def test_con_mas_claves_que_el_tope_se_avisa_un_solo_agregado(e, monkeypatch):
    """MINOR-1: llena la tabla de claves vistas, las nuevas NO expulsan a las viejas ni re-avisan: un aviso agregado, con el
    mismo enfriamiento."""
    enviados = []

    async def enviar(mensaje):
        enviados.append(mensaje)
        return Desenlace.ENTREGADO
    monkeypatch.setattr(despachador, "_enviar_telegram_con_desenlace", enviar)
    monkeypatch.setattr(despachador, "MAXIMO_CLAVES_DESCONOCIDAS", 2)
    malos = _Malos()
    e.las_manos.project_uuid = malos
    filas = []
    for _ in range(5):
        _p, puuid, ids = _otro_proyecto_con_filas(e, 1)
        malos.s.add(puuid)
        filas += ids
    e.las_manos.post_respuestas = [(503, {"detail": {"code": "idempotencia_estado_desconocido"}})] * 5
    e.ciclo()
    e.client.portal.call(_esperar_tareas_de_aviso, despachador)
    individuales = [m for m in enviados if "mas de" not in m]
    agregados = [m for m in enviados if "mas de 2 claves" in m]
    assert len(individuales) == 2 and len(agregados) == 1, enviados          # 2 por clave + 1 agregado (las otras 3 no avisan)
    assert set(despachador._claves_desconocidas) == {*list(despachador._claves_desconocidas)[:2], despachador.CLAVE_AGREGADA}
    assert len(despachador._claves_desconocidas) == 3
    assert {e.fila(i)[0] for i in filas} == {"en_cola"}


def test_un_409_de_clave_salta_solo_ese_trozo_y_los_demas_del_proyecto_salen(e):
    """MINOR-4b: 120 filas del mismo proyecto = 3 trozos de 50; el primero recibe el 409: los otros dos salen."""
    ids = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(120)]
    e.las_manos.post_respuestas = [(409, {"detail": {"code": "idempotency_key_reuse"}})]
    e.ciclo()
    estados = [e.fila(i)[0] for i in ids]
    assert estados[:50] == ["en_cola"] * 50 and set(estados[50:]) == {"pendiente"}, set(estados)


def test_una_clave_desconocida_salta_solo_su_trozo_avisa_una_vez_y_agrupa_el_log(e, caplog, monkeypatch):
    """MINOR-4a: la clave DESCONOCIDA dura 7+ dias. Una vez por clave: ERROR con el runbook de jax y UN Telegram por el canal
    de #203; los ciclos siguientes no repiten ni el log ni el aviso hasta vencer el enfriamiento (con el contador de lo
    omitido). Una clave distinta avisa aparte."""
    enviados = []

    async def enviar(mensaje):
        enviados.append(mensaje)
        return Desenlace.ENTREGADO
    monkeypatch.setattr(despachador, "_enviar_telegram_con_desenlace", enviar)
    ahora = [1000.0]
    monkeypatch.setattr(despachador, "_reloj", lambda: ahora[0])
    primero = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(2)]
    desconocida = (503, {"detail": {"code": "idempotencia_estado_desconocido"}})
    e.las_manos.post_respuestas = [desconocida] * 5
    with caplog.at_level(logging.ERROR):
        e.ciclo()
        e.client.portal.call(_esperar_tareas_de_aviso, despachador)
        for _ in range(3):
            e.ciclo()                                # la misma clave, 3 ciclos mas
            e.client.portal.call(_esperar_tareas_de_aviso, despachador)
    errores = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "DESCONOCIDO" in r.getMessage()]
    clave_corta = despachador.abreviar_clave(e.las_manos.claves[0])
    assert len(errores) == 1 and clave_corta in errores[0] and "jax:docs/runbooks/idempotencia-clave-desconocida.md" in errores[0]
    assert len(enviados) == 1 and clave_corta in enviados[0] and "jax:docs/runbooks/idempotencia-clave-desconocida.md" in enviados[0]
    assert e.las_manos.claves[0] not in "".join(enviados) + "".join(errores)
    assert {e.fila(i)[0] for i in primero} == {"en_cola"}
    # vence el enfriamiento: UN log mas, con las repeticiones omitidas; el aviso NO se repite (ya se entrego)
    enfriamiento = e.client.portal.call(despachador.ajustes.valor, despachador.ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S)
    ahora[0] += enfriamiento + 1
    with caplog.at_level(logging.ERROR):
        e.ciclo()
        e.client.portal.call(_esperar_tareas_de_aviso, despachador)
    errores = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "DESCONOCIDO" in r.getMessage()]
    assert len(errores) == 2 and "3 repeticion(es)" in errores[1] and len(enviados) == 1


def test_cruce_freno_de_incertidumbre_cursor_y_trozos_trabados(e, monkeypatch):
    """El freno de incertidumbre (#203) corta ANTES del cursor: no lee la cola ni mueve el cursor ni el conjunto de trabados.
    Al vencer, el trozo trabado se salta SIN enviarse y un proyecto que llega detras sale."""
    monkeypatch.setattr(despachador, "LIMITE_DE_FILAS_POR_CICLO", 3)
    _b, _bu, b_ids = _otro_proyecto_con_filas(e, 3)                    # ids bajos: ventana 1
    mios = [e.insertar(e.ruta("l1", f"{i}.pdf"), n=i + 1) for i in range(3)]     # ventana 2: el proyecto de `e`
    e.las_manos.post_respuestas = [(500, {"detail": "boom"})]
    e.ciclo()                                       # ventana 1 (B) sale; la 2 recibe 500: se traba y corta
    cursor, trabados = despachador._cursor_de_cola, set(despachador._trozos_trabados)
    assert {e.fila(i)[0] for i in b_ids} == {"pendiente"} and cursor == b_ids[-1] and trabados == {mios[0]}
    hasta = despachador._reloj() + 600
    for i in range(100):                            # freno ACTIVO (>= 2 x rutas_por_trabajo en incertidumbre)
        despachador._en_incertidumbre[10**12 + i] = hasta
    posts = len(e.las_manos.posts)
    e.ciclo()
    assert len(e.las_manos.posts) == posts          # el freno no despacha nada...
    assert (despachador._cursor_de_cola, despachador._trozos_trabados) == (cursor, trabados)    # ...ni toca el estado
    despachador._en_incertidumbre.clear()           # vence la ventana
    _c, _cu, c_ids = _otro_proyecto_con_filas(e, 3)  # un proyecto que llega detras
    e.ciclo()                                       # retoma en la ventana 2: `mios` se salta sin enviarse; C sale
    assert len(e.las_manos.posts) == posts, "el trozo trabado no se envio"
    assert {e.fila(i)[0] for i in mios} == {"en_cola"} and {e.fila(i)[0] for i in c_ids} == {"pendiente"}
    assert despachador._cursor_de_cola == 0 and despachador._trozos_trabados == set()


def test_no_disponible_corta_la_vuelta_y_no_deja_salir_a_otro_proyecto(e):
    """MINOR-3: `procesamiento_no_disponible` es GLOBAL: con dos proyectos, el segundo NO se despacha en el ciclo cortado
    (si se tratara como un salto de proyecto, saldria)."""
    mio = e.insertar(e.ruta("l1", "a.pdf"), n=1)
    _b, _bu, b_ids = _otro_proyecto_con_filas(e, 1)
    e.las_manos.post_respuestas = [(503, {"detail": {"code": "procesamiento_no_disponible"}})]
    e.ciclo()
    assert e.fila(mio)[:2] == ("en_cola", None) and e.fila(b_ids[0])[0] == "en_cola"
    assert len(e.las_manos.posts) == 1
    e.ciclo()                                       # el almacen volvio
    assert e.fila(mio)[0] == "pendiente" and e.fila(b_ids[0])[0] == "pendiente"


@pytest.mark.parametrize("filas,pct_en_cola,tope_s", [(50_000, 5, 3), (50_000, 100, 5), (200_000, 100, 15)])
def test_explain_y_tiempo_de_la_consulta_real_con_cursor_con_la_cola_llena(e, filas, pct_en_cola, tope_s):
    """MINOR-D, el PEOR caso: la cola llena. Sembrado con volumen (20 proyectos, 300 usuarios) se mide el EXPLAIN de la
    consulta real y el recorrido COMPLETO de la cola por ventanas de 1000. Sin pistas (medido, segundos): 50k/5%: 0,32;
    50k/100%: 15,62; 200k/5%: 3,96; 200k/100%: 276,71 (recorrido cuadratico: `Using temporary; Using filesort` en cada
    ventana). Con STRAIGHT_JOIN + FORCE INDEX: 0,01 / 0,18 / 0,04 / 0,71. Los topes de aqui son 10 a 20 veces eso: un
    runner lento no los rompe y volver al plan viejo, si."""
    marca = uuid.uuid4().hex[:12]
    limite = 1000
    tenant_db = e.client.portal.call(sql, "SELECT tenant_id FROM jax_users WHERE user_id=%s", (e.usuario,), True)[0][0]
    e.client.portal.call(sql, "INSERT INTO jax_users (tenant_id, email, password_hash) "
                              "SELECT %s, CONCAT('vol-', %s, '-', seq, '@vol.test'), 'x' FROM seq_1_to_300",
                         (tenant_db, marca))
    usuarios = [r[0] for r in e.client.portal.call(
        sql, "SELECT user_id FROM jax_users WHERE email LIKE %s ORDER BY user_id", (f"vol-{marca}-%",), True)]
    assert len(usuarios) == 300
    proyectos = [e.proyecto() for _ in range(20)]
    por_proyecto = filas // 20
    try:
        for pid, puuid in proyectos:
            e.client.portal.call(
                sql, "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, bytes, tipo, "
                     "subido_por, estado) SELECT %s, SHA2(CONCAT(%s, %s, seq), 256), CONCAT('a', seq, '.pdf'), "
                     "CONCAT('proyectos/', %s, '/entrada/l1/a', seq, '.pdf'), 10, 'pdf', %s + (seq MOD 300), "
                     "IF(seq MOD 100 < %s, 'en_cola', 'listo') FROM seq_1_to_" + str(por_proyecto),
                (pid, marca, pid, puuid, usuarios[0], pct_en_cola))
        e.client.portal.call(sql, "ANALYZE TABLE project_documents")
        ids_propios = ",".join(str(p[0]) for p in proyectos)
        en_cola = e.client.portal.call(
            sql, f"SELECT COUNT(*) FROM project_documents WHERE project_id IN ({ids_propios}) AND estado='en_cola'",
            (), True)[0][0]
        assert en_cola == filas * pct_en_cola // 100
        consulta = repo.sql_tomar_en_cola(frozenset({"excel"}), 2, True)
        params = (10**12, 10**12 + 1, filas // 2, limite)
        plan = e.client.portal.call(sql, "EXPLAIN " + consulta, params, True)
        extra = " ".join(str(f[9]) for f in plan).lower()
        assert "temporary" not in extra and "filesort" not in extra, plan
        assert plan[0][2] == "d" and plan[0][5] == "idx_project_documents_despacho", plan      # `d` primero, por el indice
        analisis = json.loads(e.client.portal.call(sql, "ANALYZE FORMAT=JSON " + consulta, params, True)[0][0])

        def filas_leidas(nodo, salida):
            if isinstance(nodo, dict):
                t = nodo.get("table")
                if isinstance(t, dict) and t.get("table_name") == "d":
                    salida.append(float(t.get("r_rows") or 0))
                for v in nodo.values():
                    filas_leidas(v, salida)
            elif isinstance(nodo, list):
                for v in nodo:
                    filas_leidas(v, salida)
            return salida
        leidas = filas_leidas(analisis, [])
        assert leidas and max(leidas) <= 3 * limite, leidas              # del orden de la ventana, no de la tabla

        async def recorrer(pool):
            cursor, total, ventanas = 0, 0, 0
            propios = {p[0] for p in proyectos}
            q = repo.sql_tomar_en_cola(frozenset(), 0, True)
            t0 = time.monotonic()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    while True:
                        await cur.execute(q, (cursor, limite))
                        lote = await cur.fetchall()
                        ventanas += 1
                        total += sum(1 for f in lote if f[1] in propios)
                        if len(lote) < limite:
                            return time.monotonic() - t0, total, ventanas
                        cursor = lote[-1][0]
        dt, total, ventanas = _con_pool(e.client, recorrer)
        assert total == en_cola, (total, en_cola)
        assert dt <= tope_s, f"recorrer {ventanas} ventanas tardo {dt:.2f}s (tope {tope_s}s): el plan volvio a ser cuadratico"
    finally:
        e.client.portal.call(sql, "DELETE FROM project_documents WHERE project_id IN ("
                             + ",".join(str(p[0]) for p in proyectos) + ") AND bytes = %s", (10,))
        e.client.portal.call(sql, "DELETE FROM jax_users WHERE email LIKE %s AND password_hash = %s",
                             (f"vol-{marca}-%", "x"))
