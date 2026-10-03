"""Despachador de fondo de documentos de proyecto (E2a, T7).

Una vuelta (`ciclo`) hace, en este orden y bajo `GET_LOCK` (un solo despachador a la
vez aunque haya varios procesos o un aviso inmediato en medio):

  1. SINCRONIZA cada trabajo abierto de LAS MANOS (`GET /procesamiento/trabajos/{id}`)
     y baja el estado de cada archivo a `project_documents`. Un 404 es un trabajo
     perdido (LAS MANOS reinicio): sus filas abiertas pasan a `error`. Una consulta que
     falla de otra forma no cambia nada: se reintenta en la proxima vuelta.
  2. DESPACHA las filas `en_cola` de proyectos ACTIVE, agrupadas por (proyecto, quien
     subio) y en trozos de `proyectos.documentos.rutas_por_trabajo`, con un POST por
     trozo (`POST /procesamiento/trabajos`). Cada despacho usa el `job_id` que devuelve
     LAS MANOS; nunca uno propio.

Que hacer con cada respuesta del POST:
  - 202                                -> `pendiente` con ese `job_id`.
  - 401, 403, 408, 429, 5xx, red caida, timeout -> las filas siguen `en_cola`; se corta la
    vuelta y se reintenta en la siguiente. Un fallo de configuracion o de capacidad no
    convierte documentos sanos en `error`; las respuestas HTTP dejan un `logger.error`.
  - 422 `proyecto_no_activo` o `project_uuid_invalido` -> siguen `en_cola`; se salta ESE
    proyecto (se archivo entre medio; al reactivarlo salen).
  - los demas 4xx (400, un 422 con otro codigo...) -> rechazan el pedido mismo: las filas
    del trozo pasan a `error` con el codigo.

BORRADO de `entrada/`: cuando LAS MANOS devuelve el resultado de un archivo (ok o no) y la
fila cambia a un estado final, se borra la copia de `proyectos/<uuid>/entrada/...` (el
original queda en `fuente/`) y la carpeta del lote si quedo vacia. Una ruta bajo `fuente/`
-- los documentos que trajo LACTOVI -- NUNCA se borra: es el original. REGLA (aceptada por el
controlador): se borra SOLO si LAS MANOS devolvio resultado de ESE archivo. Sin resultado
(trabajo perdido, fallido o sin ese archivo) el archivo se queda: no hay nada procesado que
lo reemplace. El camino se recorre desde `JAX_WORKSPACE_DIR` con
`openat(O_NOFOLLOW)` y `unlink(dir_fd=)`, como `almacen.abrir_carpeta_lote`: un enlace
simbolico en cualquier nivel no se sigue, la fila queda y se deja el error en el log.

RIESGOS ACEPTADOS (decision del controlador, 2026-10-03):
  - Desenlace incierto del POST (`ReadTimeout`, `RemoteProtocolError`, `ReadError`): LAS MANOS
    pudo crear el trabajo sin que lo sepamos. Las filas del trozo siguen `en_cola` pero NO se
    re-despachan durante VENTANA_DE_INCERTIDUMBRE_SEGUNDOS (registro en memoria del proceso: un
    reinicio lo olvida). Si aun asi se despacha dos veces, el duplicado procesa los mismos
    archivos de forma atomica y su resultado se ignora (la fila ya esta atada a otro job_id).
    La solucion completa seria una clave idempotente en LAS MANOS (mejora futura).
  - Corte de la conexion que sostiene el `GET_LOCK` a mitad de ciclo: el servidor suelta el
    lock y otro despachador podria entrar mientras este sigue; mismo desenlace que arriba
    (trabajo duplicado cuyo resultado se ignora), no perdida de datos.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import os
import stat
import time
from pathlib import Path

import httpx

import ajustes
from credencial_las_manos import encabezados_las_manos
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.state import LAS_MANOS_URL
from proyectos_documentos import almacen
from proyectos_documentos import repositorio as repo

logger = logging.getLogger(__name__)

# Cada cuanto corre el despachador de fondo, y cuanto espera una respuesta de LAS MANOS.
INTERVALO_SEGUNDOS = 10
TIMEOUT_HTTP_SEGUNDOS = 10.0
# Tope de filas `en_cola` que una vuelta toma; lo que sobre sale en la siguiente.
LIMITE_DE_FILAS_POR_CICLO = 1000

NOMBRE_DEL_LOCK = "proyectos_documentos_despacho"

# Estado de un archivo en LAS MANOS -> `project_documents.estado`.
ESTADO_DE_ARCHIVO = {
    "ok": "listo",
    "parcial": "parcial",
    "error": "error",
    "rechazado": "error",
    "sin_extractor": "sin_extractor",
    "cancelado": "cancelado",
}
ESTADOS_FINALES = frozenset({"listo", "parcial", "error", "sin_extractor", "cancelado"})
# Estados de un TRABAJO que ya no cambian (`JobStatus` de LAS MANOS).
TRABAJO_TERMINADO = frozenset({"completed", "failed", "cancelled", "rejected"})
CODIGOS_QUE_DEJAN_EN_COLA = frozenset({"proyecto_no_activo", "project_uuid_invalido"})
# 4xx que hablan del llamador o del cupo, no del documento: las filas siguen en_cola.
SIN_CULPA_DEL_DOCUMENTO = frozenset({401, 403, 408, 429})

# Desenlace incierto: el POST pudo llegar y LAS MANOS pudo crear el trabajo, pero no hubo
# respuesta. (`ConnectError` NO esta aqui: ahi el pedido no llego.)
ERRORES_DE_DESENLACE_INCIERTO = (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError)
# Cuanto no se vuelve a despachar un trozo de desenlace incierto.
VENTANA_DE_INCERTIDUMBRE_SEGUNDOS = 5 * 60
# Prefijo del 422 en texto con que LAS MANOS rechaza un pedido con mas rutas que su tope.
PREFIJO_DEMASIADAS_RUTAS = "demasiadas rutas"

_dormir = asyncio.sleep
_reloj = time.monotonic
# id de fila -> hasta cuando (segun `_reloj`) no se re-despacha. En memoria de ESTE proceso.
_en_incertidumbre: dict[int, float] = {}
_avisos: set[asyncio.Task] = set()


# ---------------------------------------------------------------- borrado de entrada/

def _borrar_de_entrada(workspace: Path, project_uuid: str, ruta_entrada: str) -> bool:
    """Sincrona (to_thread). True si la copia ya no esta (la borro o ya no existia); False
    si la ruta NO es de `proyectos/<project_uuid>/entrada/` y por eso no se toco.
    `almacen.RutaInsegura` si un nivel del camino, o el archivo, es un enlace (o no es
    una carpeta / un archivo comun): no se sigue ni se borra nada."""
    partes = ruta_entrada.split("/")
    if (len(partes) < 5 or partes[:3] != ["proyectos", project_uuid, "entrada"]
            or any(p in ("", ".", "..") for p in partes)):
        return False
    fds = [os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for nombre in partes[:-1]:
            try:
                fds.append(os.open(nombre, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
            except FileNotFoundError:  # fail-soft: la carpeta ya no esta, no hay nada que borrar (borrar es idempotente)
                return True
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise almacen.RutaInsegura(f"{nombre!r} no es una carpeta real (enlace?)") from None
                raise
        archivo = partes[-1]
        try:
            modo = os.stat(archivo, dir_fd=fds[-1], follow_symlinks=False).st_mode
        except FileNotFoundError:  # fail-soft: el archivo ya no esta; borrar es idempotente
            modo = None
        if modo is not None:
            if not stat.S_ISREG(modo):
                raise almacen.RutaInsegura(f"{archivo!r} no es un archivo comun (enlace?)")
            os.unlink(archivo, dir_fd=fds[-1])
        try:
            os.rmdir(partes[-2], dir_fd=fds[-2])
        except OSError:  # fail-soft: el lote aun tiene otros archivos o ya no esta; es limpieza, no un resultado
            pass
        return True
    finally:
        for fd in fds:
            os.close(fd)


async def _borrar_copia(fila: dict) -> None:
    def borrar() -> None:
        _borrar_de_entrada(almacen.cargar_workspace(), fila["project_uuid"], fila["ruta_entrada"] or "")

    try:
        await asyncio.to_thread(borrar)
    except Exception:  # fail-soft: no se pudo borrar la copia de entrada/; el estado ya quedo aplicado y la fila queda para conciliar a mano, no se detiene el ciclo
        partes = (fila["ruta_entrada"] or "").split("/")
        logger.error("proyectos_documentos: no se pudo borrar la copia de entrada del documento %s "
                     "(lote %r, archivo %r; se deja en disco)", fila["id"],
                     partes[-2] if len(partes) > 1 else "", partes[-1], exc_info=True)


# ---------------------------------------------------------------- sincronizacion

def _decidir(fila: dict, resultado: dict | None, trabajo: dict) -> tuple[str, str | None, str | None] | None:
    """(estado, carpeta_procesado, error) para una fila abierta, o None si no cambia."""
    if resultado is not None:
        crudo = resultado.get("estado")
        estado = ESTADO_DE_ARCHIVO.get(crudo)
        error = resultado.get("error")
        if estado is None:
            return "error", None, f"estado_desconocido:{crudo}"[:1000]
        if estado == "error" and not error:
            error = crudo
        return estado, resultado.get("carpeta_procesado"), error
    terminado = trabajo.get("estado")
    if terminado not in TRABAJO_TERMINADO:
        return "procesando", None, None
    if terminado == "cancelled":
        return "cancelado", None, None
    if terminado == "completed":
        return "error", None, "sin_resultado"
    return "error", None, str(trabajo.get("error") or terminado)[:1000]


async def _sincronizar_trabajo(pool, job_id: str) -> None:
    cliente = await get_http_client()
    respuesta = await cliente.get(f"{LAS_MANOS_URL}/procesamiento/trabajos/{job_id}",
                                  headers=encabezados_las_manos(), timeout=TIMEOUT_HTTP_SEGUNDOS)
    if respuesta.status_code == 404:
        await repo.marcar_job_perdido(pool, job_id=job_id)
        return
    if respuesta.status_code != 200:
        logger.warning("proyectos_documentos: LAS MANOS respondio %s al consultar el trabajo %s",
                       respuesta.status_code, job_id)
        return
    trabajo = respuesta.json()
    por_archivo = {r.get("archivo"): r for r in trabajo.get("resultados") or [] if isinstance(r, dict)}
    for fila in await repo.filas_abiertas_de_trabajo(pool, job_id=job_id):
        try:
            resultado = por_archivo.get(fila["ruta_entrada"])
            decision = _decidir(fila, resultado, trabajo)
            if decision is None:
                continue
            estado, carpeta, error = decision
            cambiadas = await repo.aplicar_resultado(
                pool, job_id=job_id, ruta_entrada=fila["ruta_entrada"], estado=estado,
                carpeta_procesado=carpeta, error=error)
            if cambiadas and resultado is not None and estado in ESTADOS_FINALES:
                await _borrar_copia(fila)
        except Exception:  # fail-soft: una fila que no se pudo aplicar no detiene a las demas del trabajo; la proxima vuelta la reintenta y queda en el log
            logger.warning("proyectos_documentos: no se pudo aplicar el resultado del documento %s (trabajo %s)",
                           fila["id"], job_id, exc_info=True)


async def _sincronizar(pool) -> None:
    for job_id in await repo.trabajos_abiertos(pool):
        try:
            await _sincronizar_trabajo(pool, job_id)
        except httpx.TimeoutException:  # fail-soft: LAS MANOS no contesta; consultar el resto de los trabajos seria esperar un timeout por cada uno, y todos siguen abiertos para la proxima vuelta
            logger.warning("proyectos_documentos: LAS MANOS no contesto al consultar el trabajo %s; "
                           "se corta la sincronizacion de esta vuelta", job_id)
            return
        except Exception:  # fail-soft: LAS MANOS caida o ilegible; el trabajo sigue abierto y se consulta de nuevo en la proxima vuelta
            logger.warning("proyectos_documentos: no se pudo sincronizar el trabajo %s", job_id, exc_info=True)


# ---------------------------------------------------------------- despacho

def _codigo_de(respuesta) -> str:
    try:
        detalle = respuesta.json().get("detail")
    except Exception:  # fail-soft: cuerpo que no es JSON; se usa el codigo HTTP como causa
        detalle = None
    if isinstance(detalle, dict) and isinstance(detalle.get("code"), str):
        return detalle["code"]
    return f"http_{respuesta.status_code}"


def _detalle_en_texto(respuesta) -> str:
    try:
        detalle = respuesta.json().get("detail")
    except Exception:  # fail-soft: cuerpo que no es JSON; sin texto que reconocer
        return ""
    return detalle if isinstance(detalle, str) else ""


async def _despachar_trozo(pool, project_uuid: str, usuario: str, trozo: list[dict]) -> str:
    """'seguir' | 'saltar_proyecto' | 'cortar'. Las filas solo cambian de estado cuando la
    respuesta es definitiva (202 o un 4xx que no es de reintento)."""
    ids = [f["id"] for f in trozo]
    try:
        cliente = await get_http_client()
        respuesta = await cliente.post(
            f"{LAS_MANOS_URL}/procesamiento/trabajos",
            json={"project_uuid": project_uuid, "rutas": [f["ruta_entrada"] for f in trozo], "usuario": usuario},
            headers=encabezados_las_manos(), timeout=TIMEOUT_HTTP_SEGUNDOS)
    except ERRORES_DE_DESENLACE_INCIERTO:  # fail-soft: el pedido pudo llegar; las filas siguen en_cola pero no se re-despachan durante la ventana, para no duplicar el trabajo
        hasta = _reloj() + VENTANA_DE_INCERTIDUMBRE_SEGUNDOS
        for i in ids:
            _en_incertidumbre[i] = hasta
        logger.error("proyectos_documentos: desenlace incierto: posible trabajo duplicado en LAS MANOS "
                     "(proyecto %s, %s fila(s); no se reintentan durante %s s)", project_uuid, len(ids),
                     VENTANA_DE_INCERTIDUMBRE_SEGUNDOS, exc_info=True)
        return "cortar"
    except Exception:  # fail-soft: LAS MANOS caida (el pedido no llego), o credencial ausente; las filas siguen en_cola y se reintenta en la proxima vuelta
        logger.warning("proyectos_documentos: no se pudo despachar al proyecto %s", project_uuid, exc_info=True)
        return "cortar"
    estado = respuesta.status_code
    if estado == 202:
        try:
            job_id = respuesta.json()["job_id"]
        except Exception:  # fail-soft: 202 sin job_id legible; no hay a que atar las filas, siguen en_cola
            job_id = None
        if not isinstance(job_id, str) or not job_id:
            logger.error("proyectos_documentos: LAS MANOS acepto el trabajo sin job_id legible (proyecto %s)",
                         project_uuid)
            return "cortar"
        ganadas = await repo.marcar_despachadas(pool, ids=ids, job_id=job_id)
        if len(ganadas) != len(ids):
            logger.warning("proyectos_documentos: el trabajo %s tomo %s de %s filas (las demas ya no estaban en_cola)",
                           job_id, len(ganadas), len(ids))
        return "seguir"
    if estado == 422 and _codigo_de(respuesta) in CODIGOS_QUE_DEJAN_EN_COLA:
        return "saltar_proyecto"
    if estado == 422 and _detalle_en_texto(respuesta).startswith(PREFIJO_DEMASIADAS_RUTAS):
        # Fallo de configuracion, no del documento: nunca lo convierte en `error`.
        logger.error("proyectos_documentos: rutas_por_trabajo mayor que el tope de LAS MANOS (proyecto %s, "
                     "%s ruta(s) en el trozo); las filas siguen en_cola", project_uuid, len(ids))
        return "cortar"
    if estado in SIN_CULPA_DEL_DOCUMENTO or estado >= 500 or not 400 <= estado < 500:
        # Un fallo de configuracion o de capacidad (credencial, cupo, caida) no es culpa del
        # documento: nunca lo convierte en `error`. Queda en_cola y el log dice que paso.
        logger.error("proyectos_documentos: LAS MANOS respondio %s (%s) al despachar %s fila(s) del proyecto %s; "
                     "siguen en_cola y se reintenta", estado, _codigo_de(respuesta), len(ids), project_uuid)
        return "cortar"
    codigo = _codigo_de(respuesta)
    logger.error("proyectos_documentos: LAS MANOS rechazo el trabajo del proyecto %s (%s, %s)",
                 project_uuid, estado, codigo)
    await repo.marcar_error_en_cola(pool, ids=ids, error=codigo)
    return "seguir"


async def _despachar(pool) -> None:
    por_grupo: dict[tuple[str, str], list[dict]] = {}
    ahora = _reloj()
    for i in [i for i, hasta in _en_incertidumbre.items() if hasta <= ahora]:
        del _en_incertidumbre[i]
    for fila in await repo.tomar_en_cola(pool, limite=LIMITE_DE_FILAS_POR_CICLO):
        if fila["id"] in _en_incertidumbre:
            continue
        por_grupo.setdefault((fila["project_uuid"], fila["subido_por_email"]), []).append(fila)
    if not por_grupo:
        return
    por_trabajo = await ajustes.valor(ajustes.DOC_RUTAS_POR_TRABAJO)
    for (project_uuid, usuario), filas in por_grupo.items():
        for i in range(0, len(filas), por_trabajo):
            accion = await _despachar_trozo(pool, project_uuid, usuario, filas[i:i + por_trabajo])
            if accion == "cortar":
                return
            if accion == "saltar_proyecto":
                break


# ---------------------------------------------------------------- ciclo y tarea de fondo

async def ciclo(pool) -> None:
    """Una vuelta. `GET_LOCK` en una conexion dedicada que se mantiene todo el ciclo: si otro
    despachador lo tiene, esta vuelta no hace nada."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT GET_LOCK(%s, 0)", (NOMBRE_DEL_LOCK,))
            if (await cur.fetchone())[0] != 1:
                return
        try:
            await _sincronizar(pool)
            await _despachar(pool)
        finally:
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT RELEASE_LOCK(%s)", (NOMBRE_DEL_LOCK,))
            except BaseException:
                # Sin soltar el lock, la conexion que lo tiene volveria al pool con el lock puesto
                # y ningun despachador podria correr: se cierra (el servidor lo libera al cortar).
                conn.close()
                raise


def despachar_ahora() -> None:
    """Programa un `ciclo` inmediato, sin esperarlo. Necesita el loop de la plataforma
    corriendo (se llama desde un handler). Si ya hay uno programado sin terminar, no
    suma otro: lo que llegue despues sale en la vuelta del intervalo."""
    if any(not t.done() for t in _avisos):
        return
    tarea = asyncio.get_running_loop().create_task(_ciclo_inmediato())
    _avisos.add(tarea)
    tarea.add_done_callback(_avisos.discard)


async def _ciclo_inmediato() -> None:
    try:
        await ciclo(await get_pool())
    except Exception:  # fail-soft: el aviso es un adelanto; si falla, la vuelta del intervalo hace lo mismo
        logger.warning("proyectos_documentos: el ciclo inmediato fallo", exc_info=True)


async def start_despachador():
    """Tarea de fondo del lifespan (corre al arrancar, despues duerme; nunca muere por un
    fallo). Intervalo: INTERVALO_SEGUNDOS."""
    while True:
        try:
            await ciclo(await get_pool())
        except Exception:  # fail-soft: loop de despacho en background, mismo patron que start_limpieza_de_adjuntos -- nunca debe tumbar el proceso; las filas siguen en_cola y el proximo ciclo reintenta
            logger.warning("proyectos_documentos: el despachador fallo, se reintenta en el proximo ciclo",
                           exc_info=True)
        await _dormir(INTERVALO_SEGUNDOS)
