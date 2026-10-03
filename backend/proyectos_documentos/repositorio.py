"""SQL de `project_documents` (E2a, T5). Todo parametrizado; cada funcion recibe
el pool aiomysql de la plataforma. La tabla la crea el gancho de jax (006a): no
se redefine aca.

Indices (declarados en la migracion, verificados con EXPLAIN en
tests/test_proyectos_documentos_repositorio.py):
  - listar          -> idx_project_documents_lista (project_id, oculto_at, id), FORCE INDEX en
                       las dos vistas: sin la pista el optimizador puede elegir otro indice
                       (PRIMARY en un proyecto grande y antiguo, uq_project_documents_sha con
                       ocultos) y leer miles de filas para devolver una pagina.
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
_INDICE_LISTA = "FORCE INDEX (idx_project_documents_lista)"
# Un proyecto grande y ANTIGUO con muchas filas mas nuevas de otros proyectos: sin la pista
# MariaDB recorre PRIMARY hacia atras (medido en T12: 3.839 filas leidas para devolver 51).
# Es camino caliente: la pestana Documentos repite esta consulta cada 5 s.
SQL_LISTAR_VISIBLES = _BASE_LISTA.format(nulo="NULL", indice=_INDICE_LISTA)
# Con filas ocultas reales (medido: 200 de 1000), sin la pista el optimizador elige
# uq_project_documents_sha (mismo prefijo project_id) y ordena todo el proyecto; con
# FORCE INDEX recorre solo `oculto_at IS NOT NULL` en el indice de la lista.
SQL_LISTAR_OCULTOS = _BASE_LISTA.format(nulo="NOT NULL", indice=_INDICE_LISTA)

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


class MembresiaPerdida(Exception):
    """`subido_por` ya no es miembro ACTIVE con papel de escritura: no se inserto nada.
    `papel` es el que le queda (None si dejo de ser miembro o de estar activo en su tenant)."""

    def __init__(self, papel: str | None):
        super().__init__(papel)
        self.papel = papel


# El INSERT lee `jax_project_scope` y `jax_project_membership` (la misma fuente que la
# autorizacion) y solo inserta si el proyecto sigue ACTIVE Y quien sube sigue siendo
# miembro ACTIVE con uno de los `roles_escritura`, y activo en su tenant: comprobacion y escritura son UNA
# sentencia, sin carrera con un archivado o una baja que lleguen en medio de la subida.
# (InnoDB toma un candado compartido sobre las filas que lee, asi un cambio concurrente
# espera a este commit.)
_SQL_INSERTAR = (
    "INSERT INTO project_documents (project_id, sha256, nombre_original, ruta_entrada, "
    "bytes, tipo, subido_por) "
    "SELECT %s, %s, %s, %s, %s, %s, %s FROM jax_project_scope s "
    "WHERE s.project_id = %s AND s.status = 'ACTIVE' AND EXISTS ("
    "SELECT 1 FROM jax_project_membership m WHERE m.project_id = s.project_id "
    "AND m.tenant_id = s.tenant_id AND m.user_id = %s AND m.status = 'ACTIVE' "
    "AND m.project_role IN ({roles})) "
    # Quien sube sigue activo en SU tenant: el mismo predicado que `_usuario` de
    # jax/memory/project_queries.py (47-53), por donde pasa `_proyecto_visible` en E1.
    # Copiado, no reutilizado: aquel es Python sobre un cursor y esto es UNA sentencia.
    "AND EXISTS (SELECT 1 FROM jax_users u WHERE u.user_id = %s AND u.tenant_id = s.tenant_id "
    "AND LOWER(u.status) = 'active') LIMIT 1"
)
_SQL_POR_QUE_NO = (
    "SELECT s.status, m.project_role, u.user_id FROM jax_project_scope s "
    "LEFT JOIN jax_project_membership m ON m.project_id = s.project_id AND m.tenant_id = s.tenant_id "
    "AND m.user_id = %s AND m.status = 'ACTIVE' "
    "LEFT JOIN jax_users u ON u.user_id = %s AND u.tenant_id = s.tenant_id AND LOWER(u.status) = 'active' "
    "WHERE s.project_id = %s LIMIT 1"
)


async def insertar(pool, *, project_id: int, sha256: str, nombre_original: str, ruta_entrada: str,
                   bytes_: int, tipo: str, subido_por: int, roles_escritura: tuple[str, ...]) -> int | None:
    """Id de la fila nueva, o None si ese sha256 ya esta en el proyecto.
    ProyectoNoActivo / MembresiaPerdida si el proyecto dejo de estar ACTIVE o quien sube
    perdio la membresia o el papel (no inserta nada). `roles_escritura` lo decide el
    llamador con la capacidad de B9: aqui no hay tabla de papeles."""
    if not roles_escritura:
        raise ValueError("roles_escritura vacio: nadie podria insertar")
    consulta = _SQL_INSERTAR.format(roles=", ".join(["%s"] * len(roles_escritura)))
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(consulta, (project_id, sha256, nombre_original, ruta_entrada, bytes_, tipo,
                                             subido_por, project_id, subido_por, *roles_escritura, subido_por))
            except aiomysql.IntegrityError as exc:
                if exc.args and exc.args[0] == _ERROR_DUPLICADO:
                    return None
                raise
            if not cur.rowcount:
                await cur.execute(_SQL_POR_QUE_NO, (subido_por, subido_por, project_id))
                fila = await cur.fetchone()
                await conn.commit()
                if fila is None:
                    raise ProyectoNoActivo(project_id)
                # Mismo orden que la ruta (404 -> 403 -> 409): quien ya no ve el proyecto no
                # aprende que se archivo. Un usuario ya no activo en su tenant es, para E1, un
                # proyecto no visible (papel None -> 404).
                papel = fila[1] if fila[2] is not None else None
                if papel is None or papel not in roles_escritura or fila[0] == "ACTIVE":
                    raise MembresiaPerdida(papel)
                raise ProyectoNoActivo(project_id)
            nuevo = cur.lastrowid
        await conn.commit()
        return nuevo


async def reencolar_atascado(pool, *, project_id: int, sha256: str, ruta_entrada: str) -> tuple[int, str | None] | None:
    """Ronda final, menor 8: si el documento con ese sha256 quedo en `error` SIN
    `carpeta_procesado` (LAS MANOS no llego a asegurarlo en fuente/) y esta visible, vuelve a
    `en_cola` con la copia recien subida (`job_id` y `error` en NULL) en vez de ser un
    «duplicado» para siempre. Devuelve (id, ruta_entrada anterior) o None si no habia nada
    que re-encolar. Un oculto no se resucita (mismo contrato que el duplicado oculto).
    SELECT ... FOR UPDATE y UPDATE en una transaccion: dos subidas iguales a la vez re-encolan
    una sola vez."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await conn.begin()
            try:
                await cur.execute(
                    "SELECT id, ruta_entrada FROM project_documents WHERE project_id = %s AND sha256 = %s "
                    "AND estado = 'error' AND carpeta_procesado IS NULL AND oculto_at IS NULL FOR UPDATE",
                    (project_id, sha256))
                fila = await cur.fetchone()
                if fila is None:
                    await conn.rollback()
                    return None
                await cur.execute(
                    "UPDATE project_documents SET estado = 'en_cola', job_id = NULL, error = NULL, ruta_entrada = %s "
                    "WHERE id = %s", (ruta_entrada, fila[0]))
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
    return fila[0], fila[1]


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


LARGO_MAXIMO_DEL_ERROR = 1000
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
    # `error` es VARCHAR(1000): uno mas largo haria fallar el UPDATE y la fila quedaria abierta.
    error = error[:LARGO_MAXIMO_DEL_ERROR] if error else error
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


async def filas_abiertas_de_trabajo(pool, *, job_id: str) -> list[dict]:
    """Filas `pendiente`/`procesando` de un trabajo, con el uuid de su proyecto (el
    borrado de `entrada/` lo necesita)."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT d.id, d.ruta_entrada, p.project_uuid FROM project_documents d "
                "JOIN projects p ON p.id = d.project_id "
                "WHERE d.job_id = %s AND d.estado IN ('pendiente', 'procesando') ORDER BY d.id", (job_id,))
            filas = await cur.fetchall()
    return [{"id": f[0], "ruta_entrada": f[1], "project_uuid": f[2]} for f in filas]


async def marcar_error_en_cola(pool, *, ids: list[int], error: str) -> int:
    """en_cola -> error con su causa (LAS MANOS rechazo el pedido de forma definitiva).
    Solo toca filas en_cola. Devuelve las filas cambiadas."""
    if not ids:
        return 0
    marcadores = ", ".join(["%s"] * len(ids))
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE project_documents SET estado = 'error', error = %s "
                f"WHERE estado = 'en_cola' AND id IN ({marcadores})", (error[:LARGO_MAXIMO_DEL_ERROR], *ids))
            cambiadas = cur.rowcount
        await conn.commit()
    return cambiadas
