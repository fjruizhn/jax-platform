"""SQL de `project_documents` (E2a, T5). Todo parametrizado; cada funcion recibe
el pool aiomysql de la plataforma. La tabla la crea el gancho de jax (006a): no
se redefine aca.

Indices (declarados en la migracion, verificados con EXPLAIN en
tests/test_proyectos_documentos_repositorio.py):
  - listar          -> idx_project_documents_lista (project_id, oculto_at, id)
  - tomar_en_cola   -> idx_project_documents_despacho (estado, job_id, id)
"""
from __future__ import annotations

import aiomysql

_ESTADOS_ABIERTOS = ("pendiente", "procesando")
_ERROR_DUPLICADO = 1062

_COLUMNAS_LISTA = (
    "d.id, d.nombre_original, d.bytes, d.tipo, d.estado, d.error, u.email, d.created_at, d.oculto_at"
)
_BASE_LISTA = (
    f"SELECT {_COLUMNAS_LISTA} FROM project_documents d {{indice}} JOIN jax_users u ON u.user_id = d.subido_por "
    "WHERE d.project_id = %s AND d.oculto_at IS {nulo} AND d.id < %s ORDER BY d.id DESC LIMIT %s"
)
SQL_LISTAR_VISIBLES = _BASE_LISTA.format(nulo="NULL", indice="")
# Con filas ocultas reales (medido: 200 de 1000), sin la pista el optimizador elige
# uq_project_documents_sha (mismo prefijo project_id) y ordena todo el proyecto; con
# FORCE INDEX recorre solo `oculto_at IS NOT NULL` en el indice de la lista.
SQL_LISTAR_OCULTOS = _BASE_LISTA.format(nulo="NOT NULL", indice="FORCE INDEX (idx_project_documents_lista)")

# `job_id IS NULL` fija el prefijo (estado, job_id) del indice de despacho y deja
# `id` ya ordenado: sin eso, MariaDB ordena aparte. Una fila en_cola nunca tiene
# job_id (marcar_despachadas lo pone junto con el estado `pendiente`).
SQL_TOMAR_EN_COLA = (
    "SELECT d.id, d.project_id, p.project_uuid, d.ruta_entrada, u.email "
    "FROM project_documents d "
    "JOIN projects p ON p.id = d.project_id "
    "JOIN jax_project_scope s ON s.project_id = d.project_id AND s.status = 'ACTIVE' "
    "JOIN jax_users u ON u.user_id = d.subido_por "
    "WHERE d.estado = 'en_cola' AND d.job_id IS NULL ORDER BY d.id LIMIT %s"
)


class ProyectoNoActivo(Exception):
    """El proyecto ya no esta ACTIVE en `jax_project_scope`: no se inserto nada."""


# El INSERT lee `jax_project_scope` y solo inserta si el proyecto sigue ACTIVE: la
# comprobacion y la escritura son UNA sentencia, sin carrera con un archivado que
# llegue en medio de la subida. (InnoDB toma un candado compartido sobre la fila de
# scope que lee, asi un cambio de estado concurrente espera a este commit.)
SQL_INSERTAR = (
    "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, "
    "bytes, tipo, subido_por) "
    "SELECT %s, %s, %s, %s, %s, %s, %s FROM jax_project_scope "
    "WHERE project_id = %s AND status = 'ACTIVE' LIMIT 1"
)


async def insertar(pool, *, project_id: int, sha256: str, nombre_original: str, ruta_entrada: str,
                   bytes_: int, tipo: str, subido_por: int) -> int | None:
    """Id de la fila nueva, o None si ese sha256 ya esta en el proyecto.
    ProyectoNoActivo si el proyecto dejo de estar ACTIVE (no inserta nada)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(SQL_INSERTAR, (project_id, sha256, nombre_original, ruta_entrada,
                                                 bytes_, tipo, subido_por, project_id))
            except aiomysql.IntegrityError as exc:
                if exc.args and exc.args[0] == _ERROR_DUPLICADO:
                    return None
                raise
            if not cur.rowcount:
                await conn.commit()
                raise ProyectoNoActivo(project_id)
            nuevo = cur.lastrowid
        await conn.commit()
        return nuevo


async def existente_por_sha(pool, *, project_id: int, sha256: str) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, oculto_at IS NOT NULL FROM project_documents WHERE project_id = %s AND sha256 = %s",
                (project_id, sha256))
            fila = await cur.fetchone()
    return None if fila is None else {"id": fila[0], "oculto": bool(fila[1])}


async def listar(pool, *, project_id: int, ocultos: bool, antes_de: int | None, limite: int) -> list[dict]:
    consulta = SQL_LISTAR_OCULTOS if ocultos else SQL_LISTAR_VISIBLES
    # Sin cursor: `id` es BIGINT UNSIGNED; 2**63-1 es mayor que cualquier id real.
    tope = 2**63 - 1 if antes_de is None else antes_de
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(consulta, (project_id, tope, limite))
            filas = await cur.fetchall()
    return [{"id": f[0], "nombre": f[1], "bytes": f[2], "tipo": f[3], "estado": f[4], "error": f[5],
             "subido_por_email": f[6], "creado": f[7], "oculto": f[8] is not None} for f in filas]


async def _actualizar_de_proyecto(pool, actualiza: str, args: tuple, project_id: int, documento_id: int) -> bool:
    """UPDATE sobre un documento de ESE proyecto. rowcount cuenta filas CAMBIADAS,
    asi que si fue 0 hay que distinguir "ya estaba asi" de "no es de este proyecto"."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(actualiza, (*args, documento_id, project_id))
            if cur.rowcount:
                await conn.commit()
                return True
            await cur.execute("SELECT 1 FROM project_documents WHERE id = %s AND project_id = %s",
                              (documento_id, project_id))
            existe = await cur.fetchone() is not None
        await conn.commit()
        return existe


async def ocultar(pool, *, project_id: int, documento_id: int, user_id: int) -> bool:
    return await _actualizar_de_proyecto(
        pool,
        "UPDATE project_documents SET oculto_at = COALESCE(oculto_at, CURRENT_TIMESTAMP(6)), "
        "oculto_por = COALESCE(oculto_por, %s) WHERE id = %s AND project_id = %s",
        (user_id,), project_id, documento_id)


async def restaurar(pool, *, project_id: int, documento_id: int) -> bool:
    return await _actualizar_de_proyecto(
        pool,
        "UPDATE project_documents SET oculto_at = NULL, oculto_por = NULL WHERE id = %s AND project_id = %s",
        (), project_id, documento_id)


async def tomar_en_cola(pool, *, limite: int) -> list[dict]:
    """Filas `en_cola` de proyectos ACTIVE, por `id`. `jax_project_scope.status` es
    la fuente de verdad del ciclo de vida (B9); `projects.status` solo lo refleja.
    El email viaja como `usuario` a LAS MANOS."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_TOMAR_EN_COLA, (limite,))
            filas = await cur.fetchall()
    return [{"id": f[0], "project_id": f[1], "project_uuid": f[2], "ruta_entrada": f[3],
             "subido_por_email": f[4]} for f in filas]


async def marcar_despachadas(pool, *, ids: list[int], job_id: str) -> list[int]:
    """en_cola -> pendiente + job_id. Solo toca filas en_cola: una ya despachada no
    cambia de trabajo. Devuelve los ids que EFECTIVAMENTE pasaron a pendiente (los
    que este llamado gano), para que el despachador sepa si debe seguir con ellos."""
    if not ids:
        return []
    marcadores = ", ".join(["%s"] * len(ids))
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE project_documents SET estado = 'pendiente', job_id = %s "
                f"WHERE estado = 'en_cola' AND id IN ({marcadores})", (job_id, *ids))
            await cur.execute(
                f"SELECT id FROM project_documents WHERE job_id = %s AND estado = 'pendiente' "
                f"AND id IN ({marcadores}) ORDER BY id", (job_id, *ids))
            ganados = [f[0] for f in await cur.fetchall()]
        await conn.commit()
    return ganados


async def trabajos_abiertos(pool) -> list[str]:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT job_id FROM project_documents "
                "WHERE estado IN ('pendiente', 'procesando') AND job_id IS NOT NULL")
            return [f[0] for f in await cur.fetchall()]


ESTADOS_DE_RESULTADO = frozenset({"procesando", "listo", "parcial", "error", "sin_extractor", "cancelado"})


async def aplicar_resultado(pool, *, job_id: str, ruta_entrada: str, estado: str,
                            carpeta_procesado: str | None, error: str | None) -> int:
    """Aplica el resultado de LAS MANOS a la fila de ese trabajo y esa ruta. Solo
    toca filas `pendiente`/`procesando`: un resultado tardio no pisa un estado
    terminal ni borra `trabajo_perdido`. Devuelve las filas cambiadas.

    Invariante de quien inserta: dentro de un mismo trabajo `ruta_entrada` es unica
    (la API de subida da nombres unicos por lote); si dos filas del mismo trabajo la
    compartieran, el resultado se aplicaria a las dos."""
    if estado not in ESTADOS_DE_RESULTADO:
        raise ValueError(f"estado de resultado invalido: {estado!r}")
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE project_documents SET estado = %s, carpeta_procesado = %s, error = %s "
                "WHERE job_id = %s AND ruta_entrada = %s AND estado IN ('pendiente', 'procesando')",
                (estado, carpeta_procesado, error, job_id, ruta_entrada))
            cambiadas = cur.rowcount
        await conn.commit()
    return cambiadas


async def marcar_job_perdido(pool, *, job_id: str) -> None:
    """Lo que seguia abierto en ese trabajo pasa a error; lo ya resuelto no se pisa."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE project_documents SET estado = 'error', error = 'trabajo_perdido' "
                "WHERE job_id = %s AND estado IN ('pendiente', 'procesando')", (job_id,))
        await conn.commit()
