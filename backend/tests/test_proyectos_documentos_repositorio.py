"""Repositorio de `project_documents` (Proyectos E2a, T5, 2026-10-03).

La tabla la crea el gancho de jax (migracion 006a). Cada prueba arma su propio
tenant y sus proyectos por la API (igual que test_proyectos_api.py), asi que
ninguna ve lo que dejo otra. Las filas de documentos se borran al terminar.
"""
import ast
import os
import uuid
from pathlib import Path

import pytest

from proyectos_documentos import repositorio as repo
from proyectos_documentos import tipos
from tests.identidades import cabeceras, sql, uid

P = "/api/proyectos"


def _pool_call(client, funcion, *args, **kwargs):
    async def corre():
        from db.connection import get_pool
        return await funcion(await get_pool(), *args, **kwargs)
    return client.portal.call(corre)


class Entorno:
    def __init__(self, client):
        self.client = client
        self.tenant = f"docs-{uuid.uuid4().hex}"
        self.h = cabeceras(client, f"{self.tenant}-a", tenant_id=self.tenant)
        self.usuario = int(uid(client, f"{self.tenant}-a", "operator", self.tenant))
        self.proyectos = []

    def proyecto(self, archivado=False):
        r = self.client.post(P, headers={**self.h, "Idempotency-Key": str(uuid.uuid4())},
                             json={"nombre": "P", "descripcion": None})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        self.proyectos.append(pid)
        if archivado:
            r = self.client.post(f"{P}/{pid}/estado", headers=self.h, json={"estado": "ARCHIVED"})
            assert r.status_code == 200, r.text
        return pid

    def insertar(self, pid, sha, nombre="x.pdf", ruta=None, bytes_=1, tipo="pdf"):
        return _pool_call(self.client, repo.insertar, project_id=pid, sha256=sha, nombre_original=nombre,
                          ruta_entrada=ruta or f"entrada/l/{nombre}", bytes_=bytes_, tipo=tipo,
                          subido_por=self.usuario)

    def fila(self, doc_id):
        return self.client.portal.call(
            sql, "SELECT estado, job_id, carpeta_procesado, error, oculto_at, oculto_por "
                 "FROM project_documents WHERE id=%s", (doc_id,), True)[0]


@pytest.fixture
def e(client):
    entorno = Entorno(client)
    yield entorno
    for pid in entorno.proyectos:
        client.portal.call(sql, "DELETE FROM project_documents WHERE project_id=%s", (pid,))


# ------------------------------------------------------------------ tipos

def test_tipo_de_acepta_las_extensiones_del_extractor_en_minusculas():
    assert tipos.EXTENSIONES_ACEPTADAS == frozenset(
        {"pdf", "xlsx", "xlsm", "docx", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"})
    assert tipos.tipo_de("Informe FINAL.PDF") == "pdf"
    assert tipos.tipo_de("a.b.tiff") == "tiff"


@pytest.mark.parametrize("nombre", ["archivo.exe", "viejo.xls", "datos.csv", "nota.txt", "leeme.md", "sin_extension", ".pdf", "termina.", "x.pdf.zip", ""])
def test_tipo_de_rechaza_lo_demas(nombre):
    assert tipos.tipo_de(nombre) is None


def test_extensiones_aceptadas_coinciden_con_la_compuerta_de_jax():
    """Se lee `procesamiento/compuerta.py` por ruta (sin importar el paquete jax) y se
    comparan los conjuntos: si el extractor cambia, esta prueba lo dice."""
    raiz = os.environ.get("JAX_REPO_PATH", "").strip()
    assert raiz, "JAX_REPO_PATH es requerido"
    arbol = ast.parse((Path(raiz) / "procesamiento" / "compuerta.py").read_text(encoding="utf-8"))
    constantes = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and len(nodo.targets) == 1 and isinstance(nodo.targets[0], ast.Name):
            if nodo.targets[0].id in ("IMAGENES", "EXCEL", "WORD"):
                constantes[nodo.targets[0].id] = ast.literal_eval(nodo.value)
    assert set(constantes) == {"IMAGENES", "EXCEL", "WORD"}
    de_compuerta = {e.lstrip(".") for c in constantes.values() for e in c} | {"pdf"}
    assert tipos.EXTENSIONES_ACEPTADAS == de_compuerta


# ------------------------------------------------------------------ repositorio

def test_insertar_duplicado_devuelve_none(e):
    p = e.proyecto()
    a = e.insertar(p, "b" * 64, "x.pdf", "entrada/l1/x.pdf")
    b = e.insertar(p, "b" * 64, "y.pdf", "entrada/l2/y.pdf")
    assert a is not None and b is None


def test_el_mismo_sha_en_otro_proyecto_no_choca(e):
    a = e.insertar(e.proyecto(), "b" * 64)
    b = e.insertar(e.proyecto(), "b" * 64)
    assert a is not None and b is not None


def test_existente_por_sha(e):
    p = e.proyecto()
    assert _pool_call(e.client, repo.existente_por_sha, project_id=p, sha256="a" * 64) is None
    d = e.insertar(p, "a" * 64)
    assert _pool_call(e.client, repo.existente_por_sha, project_id=p, sha256="a" * 64) == {"id": d, "oculto": False}
    _pool_call(e.client, repo.ocultar, project_id=p, documento_id=d, user_id=e.usuario)
    assert _pool_call(e.client, repo.existente_por_sha, project_id=p, sha256="a" * 64) == {"id": d, "oculto": True}


def test_listar_excluye_ocultos_y_pagina(e):
    p = e.proyecto()
    ids = [e.insertar(p, f"{i:064x}", f"{i}.pdf") for i in range(3)]
    assert _pool_call(e.client, repo.ocultar, project_id=p, documento_id=ids[1], user_id=e.usuario)
    visibles = _pool_call(e.client, repo.listar, project_id=p, ocultos=False, antes_de=None, limite=50)
    assert [d["id"] for d in visibles] == [ids[2], ids[0]]
    ocultos = _pool_call(e.client, repo.listar, project_id=p, ocultos=True, antes_de=None, limite=50)
    assert [d["id"] for d in ocultos] == [ids[1]]
    pagina = _pool_call(e.client, repo.listar, project_id=p, ocultos=False, antes_de=ids[2], limite=50)
    assert [d["id"] for d in pagina] == [ids[0]]
    una = _pool_call(e.client, repo.listar, project_id=p, ocultos=False, antes_de=None, limite=1)
    assert [d["id"] for d in una] == [ids[2]]


def test_listar_devuelve_la_forma_del_contrato(e):
    p = e.proyecto()
    d = e.insertar(p, "f" * 64, "Plano.PDF", bytes_=77)
    [fila] = _pool_call(e.client, repo.listar, project_id=p, ocultos=False, antes_de=None, limite=10)
    assert set(fila) == {"id", "nombre", "bytes", "tipo", "estado", "error", "subido_por_email", "creado", "oculto"}
    assert fila["id"] == d and fila["nombre"] == "Plano.PDF" and fila["bytes"] == 77
    assert fila["tipo"] == "pdf" and fila["estado"] == "en_cola" and fila["error"] is None
    assert fila["subido_por_email"].endswith("@example.invalid") and fila["oculto"] is False
    assert fila["creado"] is not None


def test_listar_no_mezcla_proyectos(e):
    p, q = e.proyecto(), e.proyecto()
    e.insertar(q, "1" * 64)
    assert _pool_call(e.client, repo.listar, project_id=p, ocultos=False, antes_de=None, limite=10) == []


def test_ocultar_y_restaurar(e):
    p = e.proyecto()
    d = e.insertar(p, "c" * 64)
    assert _pool_call(e.client, repo.ocultar, project_id=p, documento_id=d, user_id=e.usuario) is True
    assert e.fila(d)[4] is not None and e.fila(d)[5] == e.usuario
    # idempotente: ocultar lo ya oculto sigue siendo True (el documento es del proyecto)
    assert _pool_call(e.client, repo.ocultar, project_id=p, documento_id=d, user_id=e.usuario) is True
    assert _pool_call(e.client, repo.restaurar, project_id=p, documento_id=d) is True
    assert e.fila(d)[4] is None and e.fila(d)[5] is None
    assert _pool_call(e.client, repo.restaurar, project_id=p, documento_id=d) is True


def test_ocultar_documento_de_otro_proyecto_es_false(e):
    p, otro = e.proyecto(), e.proyecto()
    d = e.insertar(otro, "c" * 64)
    assert _pool_call(e.client, repo.ocultar, project_id=p, documento_id=d, user_id=e.usuario) is False
    assert _pool_call(e.client, repo.restaurar, project_id=p, documento_id=d) is False
    assert e.fila(d)[4] is None
    assert _pool_call(e.client, repo.ocultar, project_id=p, documento_id=10**12, user_id=e.usuario) is False


def test_tomar_en_cola_ignora_proyectos_archivados(e):
    activo, archivado = e.proyecto(), e.proyecto(archivado=True)
    a = e.insertar(activo, "d" * 64, "a.pdf")
    e.insertar(archivado, "e" * 64, "b.pdf")
    filas = [f for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000)
             if f["project_id"] in (activo, archivado)]
    assert [f["id"] for f in filas] == [a]
    assert filas[0]["subido_por_email"] and filas[0]["project_uuid"]
    assert set(filas[0]) == {"id", "project_id", "project_uuid", "ruta_entrada", "subido_por_email"}


def test_tomar_en_cola_usa_el_scope_y_no_el_reflejo_de_projects(e):
    p = e.proyecto()
    d = e.insertar(p, "d" * 64)
    # `projects.status` es un reflejo: la fuente es jax_project_scope.
    e.client.portal.call(sql, "UPDATE jax_project_scope SET status='DISABLED' WHERE project_id=%s", (p,))
    assert d not in [f["id"] for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000)]


def test_tomar_en_cola_respeta_limite_orden_y_estado(e):
    p = e.proyecto()
    ids = [e.insertar(p, f"{i:064x}", f"{i}.pdf") for i in range(3)]
    _pool_call(e.client, repo.marcar_despachadas, ids=[ids[0]], job_id="job-x")
    propios = [f["id"] for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000) if f["project_id"] == p]
    assert propios == [ids[1], ids[2]]
    # limite=1 trae solo la fila en cola mas antigua de TODA la tabla
    una = _pool_call(e.client, repo.tomar_en_cola, limite=1)
    assert len(una) == 1
    todas = _pool_call(e.client, repo.tomar_en_cola, limite=100000)
    assert una[0]["id"] == todas[0]["id"]


def test_tomar_en_cola_incluye_filas_de_fuente(e):
    p = e.proyecto()
    d = e.insertar(p, "9" * 64, "viejo.pdf", ruta=f"proyectos/{uuid.uuid4()}/fuente/viejo.pdf")
    assert d in [f["id"] for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000)]


def test_despacho_resultado_y_trabajo_perdido(e):
    p = e.proyecto()
    a, b, c = (e.insertar(p, f"{i:064x}", f"{i}.pdf", ruta=f"entrada/l/{i}.pdf") for i in range(3))
    _pool_call(e.client, repo.marcar_despachadas, ids=[a, b, c], job_id="job-1")
    assert e.fila(a)[:2] == ("pendiente", "job-1")
    assert "job-1" in _pool_call(e.client, repo.trabajos_abiertos)

    _pool_call(e.client, repo.aplicar_resultado, job_id="job-1", ruta_entrada="entrada/l/0.pdf",
               estado="listo", carpeta_procesado="proc/0", error=None)
    _pool_call(e.client, repo.aplicar_resultado, job_id="job-1", ruta_entrada="entrada/l/1.pdf",
               estado="error", carpeta_procesado=None, error="ilegible")
    assert e.fila(a)[0] == "listo" and e.fila(a)[2] == "proc/0"
    assert e.fila(b)[0] == "error" and e.fila(b)[3] == "ilegible"
    # el tercero sigue abierto, asi que el trabajo sigue abierto
    assert "job-1" in _pool_call(e.client, repo.trabajos_abiertos)

    _pool_call(e.client, repo.marcar_job_perdido, job_id="job-1")
    assert e.fila(c)[0] == "error" and e.fila(c)[3] == "trabajo_perdido"
    # lo ya resuelto no se pisa
    assert e.fila(a)[0] == "listo" and e.fila(b)[3] == "ilegible"
    assert "job-1" not in _pool_call(e.client, repo.trabajos_abiertos)


def test_marcar_despachadas_sin_ids_no_hace_nada(e):
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[], job_id="job-vacio") == []


def test_marcar_despachadas_solo_toca_filas_en_cola(e):
    p = e.proyecto()
    d = e.insertar(p, "7" * 64)
    otro = e.insertar(p, "8" * 64)
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-a") == [d]
    # el segundo despachador pierde `d` y gana solo lo que seguia en cola
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[d, otro], job_id="job-b") == [otro]
    assert e.fila(d)[:2] == ("pendiente", "job-a")
    assert e.fila(otro)[:2] == ("pendiente", "job-b")


def test_resultado_tardio_no_pisa_un_estado_terminal(e):
    p = e.proyecto()
    d = e.insertar(p, "6" * 64, ruta="entrada/l/t.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-t")
    _pool_call(e.client, repo.marcar_job_perdido, job_id="job-t")
    n = _pool_call(e.client, repo.aplicar_resultado, job_id="job-t", ruta_entrada="entrada/l/t.pdf",
                   estado="listo", carpeta_procesado="proc/t", error=None)
    assert n == 0
    assert e.fila(d)[0] == "error" and e.fila(d)[3] == "trabajo_perdido" and e.fila(d)[2] is None


def test_aplicar_resultado_devuelve_filas_y_acepta_procesando(e):
    p = e.proyecto()
    d = e.insertar(p, "5" * 64, ruta="entrada/l/u.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-u")
    assert _pool_call(e.client, repo.aplicar_resultado, job_id="job-u", ruta_entrada="entrada/l/u.pdf",
                      estado="procesando", carpeta_procesado=None, error=None) == 1
    assert _pool_call(e.client, repo.aplicar_resultado, job_id="job-u", ruta_entrada="entrada/l/u.pdf",
                      estado="listo", carpeta_procesado="proc/u", error=None) == 1
    assert e.fila(d)[0] == "listo"


def test_dos_trabajos_con_la_misma_ruta_no_se_tocan(e):
    p = e.proyecto()
    a = e.insertar(p, "3" * 64, nombre="a.pdf", ruta="entrada/l/mismo.pdf")
    b = e.insertar(p, "4" * 64, nombre="b.pdf", ruta="entrada/l/mismo.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[a], job_id="job-1")
    _pool_call(e.client, repo.marcar_despachadas, ids=[b], job_id="job-2")
    assert _pool_call(e.client, repo.aplicar_resultado, job_id="job-1", ruta_entrada="entrada/l/mismo.pdf",
                      estado="listo", carpeta_procesado="proc/a", error=None) == 1
    assert e.fila(a)[0] == "listo" and e.fila(b)[0] == "pendiente"


def test_aplicar_resultado_con_estado_invalido_lanza_antes_de_tocar(e):
    p = e.proyecto()
    d = e.insertar(p, "2" * 64, ruta="entrada/l/v.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-v")
    for malo in ("en_cola", "pendiente", "inventado", ""):
        with pytest.raises(ValueError):
            _pool_call(e.client, repo.aplicar_resultado, job_id="job-v", ruta_entrada="entrada/l/v.pdf",
                       estado=malo, carpeta_procesado=None, error=None)
    assert e.fila(d)[0] == "pendiente"


# ------------------------------------------------------------------ EXPLAIN (camino caliente)

def _sembrar(client, pid, usuario, n, desde):
    async def corre():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, bytes, tipo, subido_por) "
                    "VALUES (%s, %s, %s, %s, 1, 'pdf', %s)",
                    [(pid, f"{desde + i:064x}", f"{i}.pdf", f"entrada/l/{i}.pdf", usuario) for i in range(n)])
            await conn.commit()
    client.portal.call(corre)


def _plan(client, consulta, args):
    async def corre():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("ANALYZE TABLE project_documents")
                await cur.fetchall()
                await cur.execute("EXPLAIN " + consulta, args)
                return await cur.fetchall(), [d[0] for d in cur.description]
    filas, columnas = client.portal.call(corre)
    return [dict(zip(columnas, f)) for f in filas]


def test_explain_de_listar_y_tomar_en_cola_usan_su_indice(e):
    # 1000 filas repartidas en 10 proyectos: con un solo proyecto el filtro por
    # project_id no seria selectivo y el optimizador haria bien en recorrer la PK.
    proyectos = [e.proyecto() for _ in range(10)]
    for i, pid in enumerate(proyectos):
        _sembrar(e.client, pid, e.usuario, 100, desde=10**9 + i * 1000)
    # 200 de las 1000 filas, ocultas de verdad (los dos primeros proyectos enteros),
    # para que el plan de ocultos no sea el de un rango vacio. Visibles: otro proyecto.
    for pid in proyectos[:2]:
        e.client.portal.call(sql, "UPDATE project_documents SET oculto_at = NOW(6), oculto_por = %s "
                                  "WHERE project_id = %s", (e.usuario, pid))
    p, con_ocultos = proyectos[5], proyectos[0]
    plan = _plan(e.client, repo.SQL_LISTAR_VISIBLES, (p, 2**62, 50))
    doc = next(f for f in plan if f["table"] == "d")
    assert doc["key"] == "idx_project_documents_lista", plan
    assert "filesort" not in (doc["Extra"] or "") and "temporary" not in (doc["Extra"] or ""), plan
    # La vista de ocultos recorre `oculto_at IS NOT NULL` (un rango sobre la segunda
    # columna del indice), asi que sale ordenada por oculto_at y no por id: ahi el
    # filesort es inherente al indice de la migracion 006a, y solo ordena los ocultos
    # del proyecto (pocos, y es una vista de consulta ocasional). Se exige el indice.
    plan = _plan(e.client, repo.SQL_LISTAR_OCULTOS, (con_ocultos, 2**62, 50))
    doc = next(f for f in plan if f["table"] == "d")
    assert doc["key"] == "idx_project_documents_lista", plan
    assert "temporary" not in (doc["Extra"] or ""), plan
    plan = _plan(e.client, repo.SQL_TOMAR_EN_COLA, (100,))
    doc = next(f for f in plan if f["table"] == "d")
    assert doc["key"] == "idx_project_documents_despacho", plan
    for f in plan:
        assert "filesort" not in (f["Extra"] or "") and "temporary" not in (f["Extra"] or ""), plan
