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
    del trozo pasan a `error` `http_4xx` (el codigo de LAS MANOS va al log).

`project_documents.error` guarda SOLO un codigo estable de CAUSAS_DE_ERROR, que el frontend
traduce. El texto de LAS MANOS (en ingles, con rutas absolutas) va solo al log, con las rutas
recortadas a relativas al workspace (`_sin_rutas_absolutas`).

BORRADO de `entrada/`: se borra la copia de `proyectos/<uuid>/entrada/...` (y la carpeta del
lote si quedo vacia) SOLO si el resultado de ESE archivo trae `carpeta_procesado` bajo
`proyectos/<project_uuid de la fila>/procesado/` y la fila cambio a un estado final. LAS MANOS
llena `carpeta_procesado` recien despues de asegurar el original en `fuente/`
(jax `procesamiento/ingesta.py::ingerir`); un `error` sin carpeta (ENOSPC, EACCES, jail, otro
`JAX_WORKSPACE_DIR`), un `rechazado`, un `cancelado` o una carpeta de otro proyecto significan
que la copia de `entrada/` puede ser el UNICO original: se queda. Tampoco se borra sin resultado
(trabajo perdido, fallido o sin ese archivo). Una ruta bajo `fuente/` -- los documentos que
trajo LACTOVI -- NUNCA se borra: es el original. El camino se recorre desde `JAX_WORKSPACE_DIR` con
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
import re
import stat
import time
import unicodedata
from pathlib import Path

import httpx

import ajustes
from credencial_las_manos import encabezados_procesamiento
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.state import LAS_MANOS_URL
from proyectos_documentos import almacen, original
from proyectos_documentos import repositorio as repo

logger = logging.getLogger(__name__)

# Cada cuanto corre el despachador de fondo, y cuanto espera una respuesta de LAS MANOS.
INTERVALO_SEGUNDOS = 10
TIMEOUT_HTTP_SEGUNDOS = 10.0
# Tope de filas `en_cola` que una vuelta toma; lo que sobre sale en la siguiente.
LIMITE_DE_FILAS_POR_CICLO = 1000

# El nombre de GET_LOCK es global al SERVIDOR de MariaDB: lleva la base para que dos bases en
# el mismo servidor (otra instancia, una suite de pruebas) no se frenen entre si.
NOMBRE_DEL_LOCK = "proyectos_documentos_despacho:"
_LOCK_SQL = "CONCAT(%s, DATABASE())"

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
# Codigo del 503 del freno de extractores de LAS MANOS (Jax#335): solo frena los lotes con el tipo afectado.
CODIGO_EXTRACTORES_NO_DISPONIBLES = "extractores_no_disponibles"
PREFIJO_DEMASIADAS_RUTAS = "demasiadas rutas"

# Codigos estables que puede guardar `project_documents.error` (ronda final, menor 5). El
# frontend los traduce en `proyectos.documentos.causas` (la prueba de paridad lee ESTA lista);
# lo desconocido cae al generico. `trabajo_perdido` lo escribe repositorio.marcar_job_perdido.
CAUSAS_DE_ERROR = frozenset({
    "procesamiento_fallido",   # LAS MANOS devolvio `error` para ese archivo
    "rechazado",               # LAS MANOS rechazo ese archivo (fuera del jail, no es un archivo)
    "estado_desconocido",      # un estado de archivo que este despachador no conoce
    "sin_resultado",           # el trabajo termino sin resultado de ese archivo
    "trabajo_fallido",         # el trabajo entero fallo o fue rechazado
    "trabajo_perdido",         # LAS MANOS ya no conoce el trabajo (reinicio)
    "http_4xx",                # LAS MANOS rechazo el pedido de forma definitiva
    "ruta_ajena",              # la ruta de la fila no es de su proyecto: no se mando
    "ocr_sin_texto",           # el OCR no saco texto util (ficha.json -> detalle.razon)
    "ocr_confianza_baja",      # el OCR reconocio palabras pero con confianza baja (ruido, desenfoque)
})
# Una ruta absoluta que no es del workspace: se deja solo su ultimo tramo.
_RUTA_ABSOLUTA = re.compile(r"(?<![\w.~-])/(?:[^\s'\"/]+/)+([^\s'\"/]*)")

_dormir = asyncio.sleep
_reloj = time.monotonic
# id de fila -> hasta cuando (segun `_reloj`) no se re-despacha. En memoria de ESTE proceso.
_en_incertidumbre: dict[int, float] = {}
_avisos: set[asyncio.Task] = set()


# ---------------------------------------------------------------- borrado de entrada/

def _original_a_salvo(project_uuid: str, carpeta_procesado: str | None) -> bool:
    """True solo si LAS MANOS devolvio una `carpeta_procesado` de ESTE proyecto: la prueba de
    que el original ya esta en `fuente/` y la copia de `entrada/` sobra."""
    if not isinstance(carpeta_procesado, str):
        return False
    partes = carpeta_procesado.split("/")
    return (len(partes) >= 4 and partes[:3] == ["proyectos", project_uuid, "procesado"]
            and not any(p in ("", ".", "..") for p in partes))


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


async def borrar_copia(fila: dict) -> None:
    """Borra la copia de `entrada/` de una fila ({id, project_uuid, ruta_entrada}); solo bajo
    `proyectos/<project_uuid>/entrada/`. Un fallo queda en el log y no se propaga."""
    def borrar() -> None:
        _borrar_de_entrada(almacen.cargar_workspace(), fila["project_uuid"], fila["ruta_entrada"] or "")

    try:
        await asyncio.to_thread(borrar)
    except Exception as exc:  # fail-soft: no se pudo borrar la copia de entrada/; el estado ya quedo aplicado y la fila queda para conciliar a mano, no se detiene el ciclo
        # Sin exc_info: el traceback y el texto de un OSError llevan la ruta absoluta. Basta
        # el tipo y la ruta relativa al workspace, que es la de la fila.
        logger.error("proyectos_documentos: no se pudo borrar la copia de entrada del documento %s "
                     "(%s, ruta %r; se deja en disco)", fila["id"], type(exc).__name__, fila["ruta_entrada"])


# ---------------------------------------------------------------- sincronizacion

def _sin_rutas_absolutas(texto: object) -> str:
    """Texto de LAS MANOS apto para el log: las rutas del workspace quedan relativas a el y
    cualquier otra ruta absoluta queda reducida a su ultimo tramo. Recortado a 500."""
    texto = str(texto)
    try:
        texto = texto.replace(f"{almacen.cargar_workspace()}/", "")
    except Exception:  # fail-soft: sin workspace configurado no hay prefijo que quitar; el enmascarado de abajo igual corre
        pass
    return _RUTA_ABSOLUTA.sub(r".../\1", texto)[:500]


def _normalizada(texto: str) -> str:
    """Sin tildes, en minusculas y con los espacios repetidos en uno."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.casefold()) if not unicodedata.combining(c))
    return " ".join(sin_tildes.split())


# Las UNICAS razones de estado `error` que escribe el OCR real de jax (origin/master,
# `procesamiento/extractores/ocr.py:287`, `:289` y `:360`), copiadas literal y normalizadas. Es una
# tabla de frases EXACTAS, no una busqueda de palabras: `pdf.py:341` dice «sin capa de texto util;
# corresponde OCR» y esa NO paso por OCR. Una razon nueva de jax es `procesamiento_fallido` hasta
# que alguien la agregue aqui.
_RAZONES_DEL_OCR = {
    _normalizada("el OCR no devolvio texto util"): "ocr_sin_texto",
    _normalizada("ninguna pagina del PDF dio texto util via OCR"): "ocr_sin_texto",
    _normalizada("mas de la mitad de las palabras reconocidas tienen confianza baja "
                 "(probable ruido o desenfoque) -- ver palabras_dudosas"): "ocr_confianza_baja",
}


def codigo_de_la_razon(razon: object) -> str:
    """Codigo estable de CAUSAS_DE_ERROR para el texto libre de `ficha.json -> detalle.razon`
    (funcion pura): la tabla `_RAZONES_DEL_OCR` por frase exacta normalizada; todo lo demas es el
    generico `procesamiento_fallido`."""
    if not isinstance(razon, str):
        return "procesamiento_fallido"
    return _RAZONES_DEL_OCR.get(_normalizada(razon), "procesamiento_fallido")


async def _motivo_del_error(fila: dict, carpeta: str | None) -> str:
    """El codigo del motivo de un `error` de LAS MANOS, leido de la ficha de su carpeta (solo si
    es `proyectos/<uuid de la fila>/procesado/...`, como el borrado de entrada/). Nunca falla:
    sin ficha o con cualquier problema es `procesamiento_fallido`, y el resultado se aplica igual."""
    if not _original_a_salvo(fila["project_uuid"], carpeta):
        return "procesamiento_fallido"
    try:
        ficha = await asyncio.to_thread(original.leer_ficha, almacen.cargar_workspace(), fila["project_uuid"], carpeta)
    except Exception as exc:  # fail-soft: el motivo es un detalle; sin leerlo el resultado se aplica con el generico
        logger.warning("proyectos_documentos: no se pudo leer la ficha del documento %s (%s)", fila["id"],
                       type(exc).__name__)
        return "procesamiento_fallido"
    detalle = ficha.get("detalle") if ficha else None
    return codigo_de_la_razon(detalle.get("razon") if isinstance(detalle, dict) else None)


def _decidir(fila: dict, resultado: dict | None, trabajo: dict) -> tuple[str, str | None, str | None] | None:
    """(estado, carpeta_procesado, error) para una fila abierta, o None si no cambia. `error`
    es siempre un codigo de CAUSAS_DE_ERROR (o None)."""
    if resultado is not None:
        crudo = resultado.get("estado")
        estado = ESTADO_DE_ARCHIVO.get(crudo)
        if estado is None:
            return "error", None, "estado_desconocido"
        error = None
        if estado == "error":
            error = "rechazado" if crudo == "rechazado" else "procesamiento_fallido"
        return estado, resultado.get("carpeta_procesado"), error
    terminado = trabajo.get("estado")
    if terminado not in TRABAJO_TERMINADO:
        return "procesando", None, None
    if terminado == "cancelled":
        return "cancelado", None, None
    if terminado == "completed":
        return "error", None, "sin_resultado"
    return "error", None, "trabajo_fallido"


def _registrar_detalle(fila: dict, job_id: str, resultado: dict | None, trabajo: dict, estado: str) -> None:
    """El detalle que la fila ya no guarda, al log y sin rutas absolutas."""
    if resultado is not None:
        if resultado.get("error") or estado == "error":
            logger.warning("proyectos_documentos: documento %s (trabajo %s): LAS MANOS dijo %r: %s", fila["id"],
                           job_id, _sin_rutas_absolutas(resultado.get("estado")),
                           _sin_rutas_absolutas(resultado.get("error") or ""))
    elif estado == "error" and trabajo.get("error"):
        logger.warning("proyectos_documentos: documento %s (trabajo %s %r): %s", fila["id"], job_id,
                       _sin_rutas_absolutas(trabajo.get("estado")), _sin_rutas_absolutas(trabajo.get("error")))


async def _sincronizar_trabajo(pool, job_id: str, contexto) -> None:
    cliente = await get_http_client()
    respuesta = await cliente.get(f"{LAS_MANOS_URL}/procesamiento/trabajos/{job_id}",
                                  headers=encabezados_procesamiento(contexto), timeout=TIMEOUT_HTTP_SEGUNDOS)
    if respuesta.status_code == 404:
        await repo.marcar_job_perdido(pool, job_id=job_id, owner=contexto)
        return
    if respuesta.status_code != 200:
        logger.warning("proyectos_documentos: LAS MANOS respondio %s al consultar el trabajo %s",
                       respuesta.status_code, job_id)
        return
    trabajo = respuesta.json()
    por_archivo = {r.get("archivo"): r for r in trabajo.get("resultados") or [] if isinstance(r, dict)}
    for fila in await repo.filas_abiertas_de_trabajo(pool, job_id=job_id, owner=contexto):
        try:
            resultado = por_archivo.get(fila["ruta_entrada"])
            decision = _decidir(fila, resultado, trabajo)
            if decision is None:
                continue
            estado, carpeta, error = decision
            if estado == "error" and error == "procesamiento_fallido":
                error = await _motivo_del_error(fila, carpeta)
            cambiadas = await repo.aplicar_resultado(
                pool, job_id=job_id, ruta_entrada=fila["ruta_entrada"], estado=estado,
                carpeta_procesado=carpeta, error=error, owner=contexto)
            if cambiadas:
                _registrar_detalle(fila, job_id, resultado, trabajo, estado)
            if (cambiadas and resultado is not None and estado in ESTADOS_FINALES
                    and _original_a_salvo(fila["project_uuid"], carpeta)):
                await borrar_copia(fila)
        except Exception:  # fail-soft: una fila que no se pudo aplicar no detiene a las demas del trabajo; la proxima vuelta la reintenta y queda en el log
            logger.warning("proyectos_documentos: no se pudo aplicar el resultado del documento %s (trabajo %s)",
                           fila["id"], job_id, exc_info=True)


async def _sincronizar(pool) -> None:
    for trabajo_abierto in await repo.trabajos_abiertos(pool):
        job_id, contexto = trabajo_abierto["job_id"], trabajo_abierto["owner"]
        try:
            await _sincronizar_trabajo(pool, job_id, contexto)
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


async def _despachar_trozo(pool, project_uuid: str, contexto, trozo: list[dict]) -> str:
    """'seguir' | 'saltar_grupo' | 'saltar_proyecto' | 'cortar'. Las filas solo cambian de estado cuando la
    respuesta es definitiva (202 o un 4xx que no es de reintento)."""
    ids = [f["id"] for f in trozo]
    try:
        cliente = await get_http_client()
        respuesta = await cliente.post(
            f"{LAS_MANOS_URL}/procesamiento/trabajos",
            json={"project_uuid": project_uuid, "rutas": [f["ruta_entrada"] for f in trozo]},
            headers=encabezados_procesamiento(contexto), timeout=TIMEOUT_HTTP_SEGUNDOS)
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
        ganadas = await repo.marcar_despachadas(pool, ids=ids, job_id=job_id, owner=contexto)
        if len(ganadas) != len(ids):
            logger.warning("proyectos_documentos: el trabajo %s tomo %s de %s filas (las demas ya no estaban en_cola)",
                           job_id, len(ganadas), len(ids))
        return "seguir"
    if estado == 422 and _codigo_de(respuesta) in CODIGOS_QUE_DEJAN_EN_COLA:
        logger.warning("proyectos_documentos: LAS MANOS respondio 422 %s para el proyecto %s; %s fila(s) siguen "
                       "en espera y se salta el proyecto en esta vuelta", _codigo_de(respuesta), project_uuid, len(ids))
        return "saltar_proyecto"
    if estado == 422 and _detalle_en_texto(respuesta).startswith(PREFIJO_DEMASIADAS_RUTAS):
        # Fallo de configuracion, no del documento: nunca lo convierte en `error`.
        logger.error("proyectos_documentos: rutas_por_trabajo mayor que el tope de LAS MANOS (proyecto %s, "
                     "%s ruta(s) en el trozo); las filas siguen en_cola", project_uuid, len(ids))
        return "cortar"
    if estado == 503 and _codigo_de(respuesta) == CODIGO_EXTRACTORES_NO_DISPONIBLES:
        # El freno de extractores de LAS MANOS (Jax#335) contesta SOLO por los lotes que traen el tipo
        # afectado, y el grupo (proyecto, dueno, clase) es homogeneo: todo el resto del grupo recibiria el
        # mismo 503, asi que se salta entero (un solo POST) y se sigue con los demas grupos. Cortar aqui
        # congelaria el sistema entero porque falta una biblioteca de UN tipo.
        logger.error("proyectos_documentos: LAS MANOS no tiene los extractores del trozo (proyecto %s, %s fila(s)); "
                     "siguen en_cola, con el resto de su grupo, y se sigue con los demas grupos", project_uuid, len(ids))
        return "saltar_grupo"
    if estado in SIN_CULPA_DEL_DOCUMENTO or estado >= 500 or not 400 <= estado < 500:
        # Un fallo de configuracion o de capacidad (credencial, cupo, caida) no es culpa del
        # documento: nunca lo convierte en `error`. Queda en_cola y el log dice que paso.
        logger.error("proyectos_documentos: LAS MANOS respondio %s (%s) al despachar %s fila(s) del proyecto %s; "
                     "siguen en_cola y se reintenta", estado, _codigo_de(respuesta), len(ids), project_uuid)
        return "cortar"
    logger.error("proyectos_documentos: LAS MANOS rechazo el trabajo del proyecto %s (%s, %s)",
                 project_uuid, estado, _codigo_de(respuesta))
    await repo.marcar_error_en_cola(pool, ids=ids, error="http_4xx", owner=contexto)
    return "seguir"


def _ruta_del_proyecto(project_uuid: str, ruta: str | None) -> bool:
    """La ruta de una fila tiene que estar bajo `proyectos/<su project_uuid>/`, sin tramos
    vacios ni `.`/`..`: LAS MANOS solo recibe rutas del proyecto que el pedido nombra."""
    partes = (ruta or "").split("/")
    return (len(partes) >= 4 and partes[:2] == ["proyectos", project_uuid]
            and not any(p in ("", ".", "..") for p in partes))


_CLASES_DE_EXTENSION = {"pdf": "pdf", "xlsx": "excel", "xlsm": "excel", "docx": "word"}


def _clase_de_extension(ruta: str) -> str:
    """pdf / excel / word / otro (las imagenes y lo demas), por la extension de la ruta: lo que
    LAS MANOS frena es por tipo, asi que un trozo no mezcla clases."""
    extension = ruta.rsplit(".", 1)[-1].lower() if "." in ruta.rsplit("/", 1)[-1] else ""
    return _CLASES_DE_EXTENSION.get(extension, "otro")


async def _despachar(pool) -> None:
    por_grupo: dict[tuple[str, object, str], list[dict]] = {}
    ajenas: list[tuple[int, object]] = []
    ahora = _reloj()
    for i in [i for i, hasta in _en_incertidumbre.items() if hasta <= ahora]:
        del _en_incertidumbre[i]
    for fila in await repo.tomar_en_cola(pool, limite=LIMITE_DE_FILAS_POR_CICLO):
        if fila["id"] in _en_incertidumbre:
            continue
        if not _ruta_del_proyecto(fila["project_uuid"], fila["ruta_entrada"]):
            ajenas.append((fila["id"], fila["owner"]))
            continue
        clase = _clase_de_extension(fila["ruta_entrada"])
        por_grupo.setdefault((fila["project_uuid"], fila["owner"], clase), []).append(fila)
    if ajenas:
        logger.error("proyectos_documentos: %s fila(s) con una ruta que no es de su proyecto pasan a error "
                     "ruta_ajena sin mandarse a LAS MANOS: %s", len(ajenas), ajenas[:20])
        for owner in {owner for _, owner in ajenas}:
            await repo.marcar_error_en_cola(pool, ids=[id_ for id_, current in ajenas if current == owner],
                                             error="ruta_ajena", owner=owner)
    if not por_grupo:
        return
    por_trabajo = await ajustes.valor(ajustes.DOC_RUTAS_POR_TRABAJO)
    saltados: set[tuple[str, object]] = set()
    for (project_uuid, contexto, _clase), filas in por_grupo.items():
        if (project_uuid, contexto) in saltados:
            continue
        for i in range(0, len(filas), por_trabajo):
            accion = await _despachar_trozo(pool, project_uuid, contexto, filas[i:i + por_trabajo])
            if accion == "cortar":
                return
            if accion == "saltar_grupo":
                break                                       # todo el grupo (proyecto, dueno, clase) queda en_cola
            if accion == "saltar_proyecto":
                saltados.add((project_uuid, contexto))      # el proyecto entero, tambien sus otras clases
                break


# ---------------------------------------------------------------- ciclo y tarea de fondo

async def ciclo(pool) -> None:
    """Una vuelta. `GET_LOCK` en una conexion dedicada que se mantiene todo el ciclo: si otro
    despachador lo tiene, esta vuelta no hace nada."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT GET_LOCK({_LOCK_SQL}, 0)", (NOMBRE_DEL_LOCK,))
            if (await cur.fetchone())[0] != 1:
                return
        try:
            await _sincronizar(pool)
            await _despachar(pool)
        finally:
            try:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT RELEASE_LOCK({_LOCK_SQL})", (NOMBRE_DEL_LOCK,))
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
