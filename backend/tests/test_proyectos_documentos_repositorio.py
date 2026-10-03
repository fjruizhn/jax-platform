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

    def archivar(self, pid):
        r = self.client.post(f"{P}/{pid}/estado", headers=self.h, json={"estado": "ARCHIVED"})
        assert r.status_code == 200, r.text

    def insertar(self, pid, sha, nombre="x.pdf", ruta=None, bytes_=1, tipo="pdf"):
        return _pool_call(self.client, repo.insertar, project_id=pid, sha256=sha, nombre_original=nombre,
                          ruta_entrada=ruta or f"entrada/l/{nombre}", bytes_=bytes_, tipo=tipo,
                          subido_por=self.usuario, roles_escritura=("OWNER", "CONTRIBUTOR", "REVIEWER"))

    def fila(self, doc_id):
        return self.client.portal.call(
            sql, "SELECT estado, job_id, carpeta_procesado, error, oculto_at, oculto_por "
            "FROM project_documents WHERE id=%s", (doc_id,), True)[0]

    def owner(self, project_id):
        tenant_id = self.client.portal.call(sql, "SELECT tenant_id FROM jax_project_scope WHERE project_id=%s",
                                            (project_id,), True)[0][0]
        return repo.PlatformProcessingOwnership(int(tenant_id), self.usuario, project_id)


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
    activo, archivado = e.proyecto(), e.proyecto()
    a = e.insertar(activo, "d" * 64, "a.pdf")
    e.insertar(archivado, "e" * 64, "b.pdf")
    e.archivar(archivado)          # se archiva DESPUES: insertar ya no entra en un proyecto no ACTIVE
    filas = [f for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000)
             if f["project_id"] in (activo, archivado)]
    assert [f["id"] for f in filas] == [a]
    assert filas[0]["owner"] == e.owner(activo) and filas[0]["project_uuid"]
    assert set(filas[0]) == {"id", "project_id", "project_uuid", "ruta_entrada", "owner"}


def test_tomar_en_cola_usa_el_scope_y_no_el_reflejo_de_projects(e):
    p = e.proyecto()
    d = e.insertar(p, "d" * 64)
    # `projects.status` es un reflejo: la fuente es jax_project_scope.
    e.client.portal.call(sql, "UPDATE jax_project_scope SET status='DISABLED' WHERE project_id=%s", (p,))
    assert d not in [f["id"] for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000)]


def test_tomar_en_cola_respeta_limite_orden_y_estado(e):
    p = e.proyecto()
    ids = [e.insertar(p, f"{i:064x}", f"{i}.pdf") for i in range(3)]
    _pool_call(e.client, repo.marcar_despachadas, ids=[ids[0]], job_id="job-x", owner=e.owner(p))
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
    _pool_call(e.client, repo.marcar_despachadas, ids=[a, b, c], job_id="job-1", owner=e.owner(p))
    assert e.fila(a)[:2] == ("pendiente", "job-1")
    assert {row["job_id"] for row in _pool_call(e.client, repo.trabajos_abiertos)} == {"job-1"}

    _pool_call(e.client, repo.aplicar_resultado, job_id="job-1", ruta_entrada="entrada/l/0.pdf",
               estado="listo", carpeta_procesado="proc/0", error=None, owner=e.owner(p))
    _pool_call(e.client, repo.aplicar_resultado, job_id="job-1", ruta_entrada="entrada/l/1.pdf",
               estado="error", carpeta_procesado=None, error="ilegible", owner=e.owner(p))
    assert e.fila(a)[0] == "listo" and e.fila(a)[2] == "proc/0"
    assert e.fila(b)[0] == "error" and e.fila(b)[3] == "ilegible"
    # el tercero sigue abierto, asi que el trabajo sigue abierto
    assert {row["job_id"] for row in _pool_call(e.client, repo.trabajos_abiertos)} == {"job-1"}

    _pool_call(e.client, repo.marcar_job_perdido, job_id="job-1", owner=e.owner(p))
    assert e.fila(c)[0] == "error" and e.fila(c)[3] == "trabajo_perdido"
    # lo ya resuelto no se pisa
    assert e.fila(a)[0] == "listo" and e.fila(b)[3] == "ilegible"
    assert "job-1" not in {row["job_id"] for row in _pool_call(e.client, repo.trabajos_abiertos)}


def test_marcar_despachadas_sin_ids_no_hace_nada(e):
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[], job_id="job-vacio", owner=e.owner(e.proyecto())) == []


def test_marcar_despachadas_solo_toca_filas_en_cola(e):
    p = e.proyecto()
    d = e.insertar(p, "7" * 64)
    otro = e.insertar(p, "8" * 64)
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-a", owner=e.owner(p)) == [d]
    # el segundo despachador pierde `d` y gana solo lo que seguia en cola
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[d, otro], job_id="job-b", owner=e.owner(p)) == [otro]
    assert e.fila(d)[:2] == ("pendiente", "job-a")
    assert e.fila(otro)[:2] == ("pendiente", "job-b")


def test_resultado_tardio_no_pisa_un_estado_terminal(e):
    p = e.proyecto()
    d = e.insertar(p, "6" * 64, ruta="entrada/l/t.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-t", owner=e.owner(p))
    _pool_call(e.client, repo.marcar_job_perdido, job_id="job-t", owner=e.owner(p))
    n = _pool_call(e.client, repo.aplicar_resultado, job_id="job-t", ruta_entrada="entrada/l/t.pdf",
                   estado="listo", carpeta_procesado="proc/t", error=None, owner=e.owner(p))
    assert n == 0
    assert e.fila(d)[0] == "error" and e.fila(d)[3] == "trabajo_perdido" and e.fila(d)[2] is None


def test_aplicar_resultado_devuelve_filas_y_acepta_procesando(e):
    p = e.proyecto()
    d = e.insertar(p, "5" * 64, ruta="entrada/l/u.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-u", owner=e.owner(p))
    assert _pool_call(e.client, repo.aplicar_resultado, job_id="job-u", ruta_entrada="entrada/l/u.pdf",
                      estado="procesando", carpeta_procesado=None, error=None, owner=e.owner(p)) == 1
    assert _pool_call(e.client, repo.aplicar_resultado, job_id="job-u", ruta_entrada="entrada/l/u.pdf",
                      estado="listo", carpeta_procesado="proc/u", error=None, owner=e.owner(p)) == 1
    assert e.fila(d)[0] == "listo"


def test_dos_trabajos_con_la_misma_ruta_no_se_tocan(e):
    p = e.proyecto()
    a = e.insertar(p, "3" * 64, nombre="a.pdf", ruta="entrada/l/mismo.pdf")
    b = e.insertar(p, "4" * 64, nombre="b.pdf", ruta="entrada/l/mismo.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[a], job_id="job-1", owner=e.owner(p))
    _pool_call(e.client, repo.marcar_despachadas, ids=[b], job_id="job-2", owner=e.owner(p))
    assert _pool_call(e.client, repo.aplicar_resultado, job_id="job-1", ruta_entrada="entrada/l/mismo.pdf",
                      estado="listo", carpeta_procesado="proc/a", error=None, owner=e.owner(p)) == 1
    assert e.fila(a)[0] == "listo" and e.fila(b)[0] == "pendiente"


def test_aplicar_resultado_con_estado_invalido_lanza_antes_de_tocar(e):
    p = e.proyecto()
    d = e.insertar(p, "2" * 64, ruta="entrada/l/v.pdf")
    _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="job-v", owner=e.owner(p))
    for malo in ("en_cola", "pendiente", "inventado", ""):
        with pytest.raises(ValueError):
            _pool_call(e.client, repo.aplicar_resultado, job_id="job-v", ruta_entrada="entrada/l/v.pdf",
                       estado=malo, carpeta_procesado=None, error=None, owner=e.owner(p))
    assert e.fila(d)[0] == "pendiente"


def test_owner_tuple_blocks_cross_project_tenant_and_user_mutations(e):
    p = e.proyecto()
    d = e.insertar(p, "a" * 64, ruta="entrada/l/owner.pdf")
    owner = e.owner(p)
    for forged in (
        repo.PlatformProcessingOwnership(owner.tenant_id, owner.user_id, owner.project_id + 1),
        repo.PlatformProcessingOwnership(owner.tenant_id + 1, owner.user_id, owner.project_id),
        repo.PlatformProcessingOwnership(owner.tenant_id, owner.user_id + 1, owner.project_id),
    ):
        assert _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="cross", owner=forged) == []
        assert e.fila(d)[:2] == ("en_cola", None)
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[d], job_id="owned", owner=owner) == [d]


def test_same_job_with_two_owner_contexts_is_quarantined(e):
    p, q = e.proyecto(), e.proyecto()
    a, b = e.insertar(p, "b" * 64), e.insertar(q, "c" * 64)
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[a], job_id="ambiguous", owner=e.owner(p)) == [a]
    assert _pool_call(e.client, repo.marcar_despachadas, ids=[b], job_id="ambiguous", owner=e.owner(q)) == [b]
    assert "ambiguous" not in {row["job_id"] for row in _pool_call(e.client, repo.trabajos_abiertos)}


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


def test_insertar_solo_si_el_proyecto_sigue_activo(e):
    p = e.proyecto()
    assert e.insertar(p, "a" * 64) is not None
    assert e.insertar(p, "a" * 64) is None                # duplicado en un proyecto ACTIVE: sigue siendo None
    e.archivar(p)
    with pytest.raises(repo.ProyectoNoActivo):
        e.insertar(p, "b" * 64)
    e.client.portal.call(sql, "UPDATE jax_project_scope SET status='DISABLED' WHERE project_id=%s", (p,))
    with pytest.raises(repo.ProyectoNoActivo):
        e.insertar(p, "c" * 64)
    filas = e.client.portal.call(sql, "SELECT sha256 FROM project_documents WHERE project_id=%s", (p,), True)
    assert [f[0] for f in filas] == ["a" * 64]            # lo rechazado no dejo fila


def test_insertar_exige_membresia_activa_con_papel_de_escritura(e):
    p = e.proyecto()
    ajeno = int(uid(e.client, f"{e.tenant}-ajeno", "operator", e.tenant))
    args = dict(project_id=p, nombre_original="x.pdf", ruta_entrada="entrada/l/x.pdf", bytes_=1, tipo="pdf")
    roles = ("OWNER", "CONTRIBUTOR", "REVIEWER")
    # quien nunca fue miembro: sin papel
    with pytest.raises(repo.MembresiaPerdida) as sin:
        _pool_call(e.client, repo.insertar, sha256="1" * 64, subido_por=ajeno, roles_escritura=roles, **args)
    assert sin.value.papel is None
    # miembro ACTIVE pero con un papel que no escribe: se informa el papel que tiene
    with pytest.raises(repo.MembresiaPerdida) as papel:
        _pool_call(e.client, repo.insertar, sha256="2" * 64, subido_por=e.usuario, roles_escritura=("VIEWER",), **args)
    assert papel.value.papel == "OWNER"
    assert _pool_call(e.client, repo.insertar, sha256="3" * 64, subido_por=e.usuario, roles_escritura=roles,
                      **args) is not None
    with pytest.raises(ValueError):
        _pool_call(e.client, repo.insertar, sha256="4" * 64, subido_por=e.usuario, roles_escritura=(), **args)
    filas = e.client.portal.call(sql, "SELECT sha256 FROM project_documents WHERE project_id=%s", (p,), True)
    assert [f[0] for f in filas] == ["3" * 64]


def test_insertar_exige_usuario_activo_en_su_tenant(e):
    p = e.proyecto()
    args = dict(project_id=p, nombre_original="x.pdf", ruta_entrada="entrada/l/x.pdf", bytes_=1, tipo="pdf",
                subido_por=e.usuario, roles_escritura=("OWNER", "CONTRIBUTOR", "REVIEWER"))
    assert _pool_call(e.client, repo.insertar, sha256="5" * 64, **args) is not None
    propio = e.client.portal.call(sql, "SELECT tenant_id FROM jax_users WHERE user_id=%s", (e.usuario,), True)[0][0]
    otro = e.client.portal.call(sql, "SELECT tenant_id FROM jax_tenants WHERE tenant_id <> %s LIMIT 1",
                                (propio,), True)[0][0]
    try:
        e.client.portal.call(sql, "UPDATE jax_users SET status='inactive' WHERE user_id=%s", (e.usuario,))
        with pytest.raises(repo.MembresiaPerdida) as perdida:
            _pool_call(e.client, repo.insertar, sha256="6" * 64, **args)
        assert perdida.value.papel is None                      # para E1, un proyecto no visible
        # activo, pero en OTRO tenant que el del proyecto: tampoco
        e.client.portal.call(sql, "UPDATE jax_users SET status='active', tenant_id=%s WHERE user_id=%s",
                             (otro, e.usuario))
        with pytest.raises(repo.MembresiaPerdida):
            _pool_call(e.client, repo.insertar, sha256="7" * 64, **args)
    finally:
        e.client.portal.call(sql, "UPDATE jax_users SET status='active', tenant_id=%s WHERE user_id=%s",
                             (propio, e.usuario))
    filas = e.client.portal.call(sql, "SELECT sha256 FROM project_documents WHERE project_id=%s", (p,), True)
    assert [f[0] for f in filas] == ["5" * 64]


def test_explain_de_listar_visibles_usa_su_indice_en_un_proyecto_viejo_y_grande(e):
    # Ronda final, MAJOR-3 (medido en T12): con un proyecto grande y ANTIGUO y muchas filas
    # mas nuevas de otros proyectos, sin la pista MariaDB recorria PRIMARY hacia atras
    # (3.839 filas leidas para devolver 51). La lista visible es camino caliente: la
    # pestana la repite cada 5 s.
    viejo = e.proyecto()
    _sembrar(e.client, viejo, e.usuario, 2000, desde=2 * 10**9)
    for i in range(4):
        _sembrar(e.client, e.proyecto(), e.usuario, 2000, desde=2 * 10**9 + (i + 1) * 10**5)
    plan = _plan(e.client, repo.SQL_LISTAR_VISIBLES, (viejo, 2**62, 51))
    doc = next(f for f in plan if f["table"] == "d")
    assert doc["key"] == "idx_project_documents_lista", plan
    assert "filesort" not in (doc["Extra"] or "") and "temporary" not in (doc["Extra"] or ""), plan


def test_archivado_y_usuario_inactivo_a_la_vez_es_no_visible_no_409(e):
    # Ronda final, 9a: mismo orden que la ruta (404 -> 403 -> 409). Un usuario ya no activo
    # no debe aprender que el proyecto se archivo.
    p = e.proyecto()
    e.archivar(p)
    args = dict(project_id=p, nombre_original="x.pdf", ruta_entrada="entrada/l/x.pdf", bytes_=1, tipo="pdf",
                subido_por=e.usuario, roles_escritura=("OWNER", "CONTRIBUTOR", "REVIEWER"))
    try:
        e.client.portal.call(sql, "UPDATE jax_users SET status='inactive' WHERE user_id=%s", (e.usuario,))
        with pytest.raises(repo.MembresiaPerdida) as perdida:
            _pool_call(e.client, repo.insertar, sha256="8" * 64, **args)
        assert perdida.value.papel is None
        e.client.portal.call(sql, "UPDATE jax_users SET status='active' WHERE user_id=%s", (e.usuario,))
        # activo pero con un papel que no escribe, en archivado: 403 antes que 409
        with pytest.raises(repo.MembresiaPerdida) as papel:
            _pool_call(e.client, repo.insertar, sha256="9" * 64, **{**args, "roles_escritura": ("VIEWER",)})
        assert papel.value.papel == "OWNER"
    finally:
        e.client.portal.call(sql, "UPDATE jax_users SET status='active' WHERE user_id=%s", (e.usuario,))


def test_reencolar_exige_las_mismas_condiciones_que_el_insert(e):
    p = e.proyecto()
    doc = e.insertar(p, "e" * 64)
    e.client.portal.call(sql, "UPDATE project_documents SET estado='error', error='x' WHERE id=%s", (doc,))
    ajeno = int(uid(e.client, f"{e.tenant}-ajeno", "operator", e.tenant))
    roles = ("OWNER", "CONTRIBUTOR", "REVIEWER")
    args = dict(project_id=p, sha256="e" * 64, ruta_entrada="entrada/l2/x.pdf", nombre_original="x.pdf")
    with pytest.raises(repo.MembresiaPerdida) as sin:                # no miembro: 404
        _pool_call(e.client, repo.reencolar_atascado, subido_por=ajeno, roles_escritura=roles, **args)
    assert sin.value.papel is None
    with pytest.raises(repo.MembresiaPerdida) as papel:              # papel que no escribe: 403
        _pool_call(e.client, repo.reencolar_atascado, subido_por=e.usuario, roles_escritura=("VIEWER",), **args)
    assert papel.value.papel == "OWNER"
    e.archivar(p)
    with pytest.raises(repo.ProyectoNoActivo):                       # archivado: 409
        _pool_call(e.client, repo.reencolar_atascado, subido_por=e.usuario, roles_escritura=roles, **args)
    assert e.fila(doc)[0] == "error"                                 # nada cambio


# ------------------------------------------------------------------ reprocesar
ROLES = ("OWNER", "CONTRIBUTOR", "REVIEWER")
FUENTE = "proyectos/u-1/fuente/x.pdf"


def _a_estado(e, doc, estado, **cols):
    sets = ", ".join(["estado=%s"] + [f"{c}=%s" for c in cols])
    e.client.portal.call(sql, f"UPDATE project_documents SET {sets} WHERE id=%s", (estado, *cols.values(), doc))


def _reprocesar(e, p, doc, ruta=FUENTE, usuario=None, roles=ROLES):
    return _pool_call(e.client, repo.reprocesar, project_id=p, documento_id=doc, ruta_fuente=ruta,
                      user_id=e.usuario if usuario is None else usuario, roles_escritura=roles)


def _sin_extractor(e, p, sha="a" * 64, nombre="x.pdf"):
    d = e.insertar(p, sha, nombre)
    _a_estado(e, d, "sin_extractor", carpeta_procesado="proyectos/u-1/procesado/c", job_id="job-9",
              error="algo")
    return d


def test_reprocesar_pasa_a_en_cola_con_la_ruta_de_fuente_y_limpia_job_y_error(e):
    p = e.proyecto()
    d = _sin_extractor(e, p)
    assert _reprocesar(e, p, d) is True
    estado, job_id, carpeta, error, oculto_at, _ = e.fila(d)
    assert (estado, job_id, error) == ("en_cola", None, None)
    assert e.client.portal.call(sql, "SELECT ruta_entrada FROM project_documents WHERE id=%s", (d,), True)[0][0] == FUENTE
    assert carpeta == "proyectos/u-1/procesado/c"          # no se pierde donde quedo la ficha
    # y el despachador la ve
    assert d in [f["id"] for f in _pool_call(e.client, repo.tomar_en_cola, limite=100000)]
    # ya no esta en sin_extractor: repetirlo no hace nada
    assert _reprocesar(e, p, d) is False


@pytest.mark.parametrize("estado", ["en_cola", "pendiente", "procesando", "listo", "parcial", "cancelado"])
def test_reprocesar_solo_desde_sin_extractor_o_error(e, estado):
    p = e.proyecto()
    d = e.insertar(p, "b" * 64)
    _a_estado(e, d, estado)
    antes = e.client.portal.call(sql, "SELECT ruta_entrada, job_id, error FROM project_documents WHERE id=%s", (d,), True)
    assert _reprocesar(e, p, d) is False
    assert e.fila(d)[0] == estado
    assert e.client.portal.call(sql, "SELECT ruta_entrada, job_id, error FROM project_documents WHERE id=%s",
                                (d,), True) == antes


@pytest.mark.parametrize("error", [None, "procesamiento_fallido", "ocr_sin_texto"])
def test_reprocesar_tambien_desde_error_con_o_sin_motivo(e, error):
    p = e.proyecto()
    d = e.insertar(p, "c" * 64)
    _a_estado(e, d, "error", error=error, job_id="job-7", carpeta_procesado="proyectos/u-1/procesado/c")
    assert _reprocesar(e, p, d) is True
    estado, job_id, _, err, _, _ = e.fila(d)
    assert (estado, job_id, err) == ("en_cola", None, None)


def test_reprocesar_desde_error_conserva_las_mismas_condiciones(e):
    p1, p2 = e.proyecto(), e.proyecto()
    tipo_malo = e.insertar(p1, "d" * 64, "x.txt")
    otro = e.insertar(p2, "e" * 64)
    ok = e.insertar(p1, "f" * 64)
    for d in (tipo_malo, otro, ok):
        _a_estado(e, d, "error", error="procesamiento_fallido")
    assert _reprocesar(e, p1, tipo_malo) is False          # tipo sin extractor
    assert _reprocesar(e, p1, otro) is False               # otro proyecto
    ajeno = int(uid(e.client, f"{e.tenant}-ajeno", "operator", e.tenant))
    assert _reprocesar(e, p1, ok, usuario=ajeno) is False  # sin escritura
    e.archivar(p1)
    assert _reprocesar(e, p1, ok) is False                 # proyecto archivado
    assert {e.fila(d)[0] for d in (tipo_malo, otro, ok)} == {"error"}


@pytest.mark.parametrize("nombre", ["x.txt", "x.zip", "informe", ".pdf", "informe.", "x.pdf ", "x.exe.bak"])
def test_reprocesar_rechaza_un_tipo_sin_extractor(e, nombre):
    p = e.proyecto()
    d = _sin_extractor(e, p, nombre=nombre)
    assert _reprocesar(e, p, d) is False
    assert e.fila(d)[0] == "sin_extractor"


@pytest.mark.parametrize("nombre", ["X.PDF", "informe.Docx", "carpeta/a.b.xlsx", "foto.JPEG"])
def test_reprocesar_acepta_los_tipos_con_extractor_sin_distinguir_mayusculas(e, nombre):
    p = e.proyecto()
    d = _sin_extractor(e, p, nombre=nombre)
    assert tipos.tipo_de(nombre) is not None
    assert _reprocesar(e, p, d) is True and e.fila(d)[0] == "en_cola"


def test_reprocesar_no_toca_un_documento_de_otro_proyecto(e):
    p1, p2 = e.proyecto(), e.proyecto()
    d = _sin_extractor(e, p2)
    assert _reprocesar(e, p1, d) is False
    assert e.fila(d)[0] == "sin_extractor"
    assert _reprocesar(e, p1, 99999999) is False


def test_reprocesar_exige_proyecto_activo(e):
    p = e.proyecto()
    d = _sin_extractor(e, p)
    e.archivar(p)
    assert _reprocesar(e, p, d) is False
    assert e.fila(d)[0] == "sin_extractor"


def test_reprocesar_exige_papel_de_escritura_y_membresia_activa(e):
    p = e.proyecto()
    d = _sin_extractor(e, p)
    ajeno = int(uid(e.client, f"{e.tenant}-ajeno", "operator", e.tenant))
    assert _reprocesar(e, p, d, usuario=ajeno) is False                 # nunca fue miembro
    assert _reprocesar(e, p, d, roles=("VIEWER",)) is False             # su papel (OWNER) no esta en la lista
    e.client.portal.call(sql, "UPDATE jax_project_membership SET project_role='VIEWER' "
                              "WHERE project_id=%s AND user_id=%s", (p, e.usuario))
    assert _reprocesar(e, p, d) is False                                # lector de verdad
    e.client.portal.call(sql, "UPDATE jax_project_membership SET project_role='CONTRIBUTOR', status='REVOKED' "
                              "WHERE project_id=%s AND user_id=%s", (p, e.usuario))
    assert _reprocesar(e, p, d) is False                                # miembro dado de baja
    assert e.fila(d)[0] == "sin_extractor"
    e.client.portal.call(sql, "UPDATE jax_project_membership SET status='ACTIVE' "
                              "WHERE project_id=%s AND user_id=%s", (p, e.usuario))
    assert _reprocesar(e, p, d) is True


def test_reprocesar_exige_usuario_activo_en_su_tenant(e):
    p = e.proyecto()
    d = _sin_extractor(e, p)
    try:
        e.client.portal.call(sql, "UPDATE jax_users SET status='inactive' WHERE user_id=%s", (e.usuario,))
        assert _reprocesar(e, p, d) is False
    finally:
        e.client.portal.call(sql, "UPDATE jax_users SET status='active' WHERE user_id=%s", (e.usuario,))
    assert e.fila(d)[0] == "sin_extractor"


def test_reprocesar_sin_roles_de_escritura_levanta(e):
    p = e.proyecto()
    d = _sin_extractor(e, p)
    with pytest.raises(ValueError):
        _reprocesar(e, p, d, roles=())


def test_documento_para_reprocesar_devuelve_lo_que_hace_falta_y_respeta_el_proyecto(e):
    p1, p2 = e.proyecto(), e.proyecto()
    d = _sin_extractor(e, p1, nombre="Informe.PDF")
    fila = _pool_call(e.client, repo.documento_para_reprocesar, project_id=p1, documento_id=d)
    assert fila == {"estado": "sin_extractor", "nombre_original": "Informe.PDF", "sha256": "a" * 64,
                    "bytes": 1, "carpeta_procesado": "proyectos/u-1/procesado/c", "subido_por": e.usuario}
    assert _pool_call(e.client, repo.documento_para_reprocesar, project_id=p2, documento_id=d) is None
