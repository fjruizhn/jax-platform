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
from catalogo_modelos_ejecutor import Desenlace, _enviar_telegram_con_desenlace
from credencial_las_manos import encabezados_procesamiento
from db.connection import get_pool
from http_client import get_http_client
from jax_engine.state import LAS_MANOS_URL
from proyectos_documentos import almacen, original, tipos
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
    # Contrato de LAS MANOS (jax#338) en `ResultadoArchivo.error`: un formato de imagen que el OCR
    # no procesa con fidelidad es un error visible que nombra el formato (`formato_<f>`).
    "formato_no_soportado",    # formato no soportado sin nombre (sufijo ausente, invalido o desconocido)
    "formato_gif_animado",     # GIF con mas de un cuadro
    "formato_webp_animado",    # WebP con mas de un cuadro
    "formato_gris_16_bits",    # escala de grises de 16 bits
    "formato_coma_flotante",   # pixeles en coma flotante
    "formato_entero_32_bits",  # pixeles enteros de 32 bits
    "formato_bmp_16_bits",     # BMP de 16 bits
    "archivo_ilegible",        # la imagen esta danada o no se puede abrir
    "archivo_no_procesable",   # el OCR no pudo leerla: danada o en un formato no soportado
    "imagen_demasiado_grande", # la imagen excede lo que el OCR procesa
    "ocr_tiempo_excedido",     # el OCR tardo mas que su plazo
    "ocr_sin_memoria",         # el OCR se quedo sin memoria
})
# Una ruta absoluta que no es del workspace: se deja solo su ultimo tramo.
_RUTA_ABSOLUTA = re.compile(r"(?<![\w.~-])/(?:[^\s'\"/]+/)+([^\s'\"/]*)")

_dormir = asyncio.sleep
_reloj = time.monotonic
# id de fila -> hasta cuando (segun `_reloj`) no se re-despacha. En memoria de ESTE proceso.
_en_incertidumbre: dict[int, float] = {}
_avisos: set[asyncio.Task] = set()
_avisos_freno_incertidumbre: set[asyncio.Task] = set()
_freno_incertidumbre_activo = False
_ultimo_aviso_freno_incertidumbre: float | None = None
_aviso_freno_incertidumbre_pendiente = False
_supresion_freno_incertidumbre_registrada = False
_aviso_freno_incidente_entregado = False
# Fallos seguidos del envio y cuando ocurrio el ultimo (segun `_reloj`): fijan la espera antes del reintento.
_fallos_aviso_freno_incertidumbre = 0
_ultimo_fallo_aviso_freno_incertidumbre: float | None = None
# Ultimo ciclo con el freno activo, y cuanto duro la pausa entre el incidente anterior y el actual (fijada al
# empezar el incidente): el contador de fallos solo se reinicia tras una entrega confirmada o si esa pausa
# alcanzo un enfriamiento completo. Un incidente que termina y vuelve a empezar DENTRO del enfriamiento (100
# filas que vencen juntas cada pocos minutos) es el mismo problema: no reinicia la espera.
_ultima_actividad_freno_incertidumbre: float | None = None
_pausa_previa_al_incidente: float | None = None


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


# `ResultadoArchivo.error` de LAS MANOS (jax#338): `formato_no_soportado[:<formato>]` y cinco codigos
# sin sufijo. El sufijo solo vale si cumple `[a-z0-9_]{1,40}` y esta en la lista conocida.
_FORMATOS_NO_SOPORTADOS = frozenset({"gif_animado", "webp_animado", "gris_16_bits", "coma_flotante",
                                     "entero_32_bits", "bmp_16_bits"})
_ERRORES_DEL_ARCHIVO = frozenset({"archivo_ilegible", "archivo_no_procesable", "imagen_demasiado_grande",
                                  "ocr_tiempo_excedido", "ocr_sin_memoria"})
_SUFIJO_DE_FORMATO = re.compile(r"[a-z0-9_]{1,40}")


def codigo_del_error_del_archivo(error: object) -> str | None:
    """Codigo estable de CAUSAS_DE_ERROR para el `error` de un archivo de LAS MANOS (funcion pura), o
    None si no es del contrato: entonces sigue el camino de la ficha. Nunca devuelve texto crudo."""
    if not isinstance(error, str):
        return None
    if error in _ERRORES_DEL_ARCHIVO:
        return error if error in CAUSAS_DE_ERROR else None
    base, separador, sufijo = error.partition(":")
    if base != "formato_no_soportado":
        return None
    if separador and _SUFIJO_DE_FORMATO.fullmatch(sufijo) and sufijo in _FORMATOS_NO_SOPORTADOS:
        codigo = f"formato_{sufijo}"
        # Defensa: un formato sumado a la tabla sin sumarlo a CAUSAS_DE_ERROR cae al generico, nunca se guarda.
        return codigo if codigo in CAUSAS_DE_ERROR else "formato_no_soportado"
    return "formato_no_soportado"


def codigo_de_la_razon(razon: object) -> str:
    """Codigo estable de CAUSAS_DE_ERROR para el texto libre de `ficha.json -> detalle.razon`
    (funcion pura): la tabla `_RAZONES_DEL_OCR` por frase exacta normalizada; todo lo demas es el
    generico `procesamiento_fallido`."""
    if not isinstance(razon, str):
        return "procesamiento_fallido"
    codigo = _RAZONES_DEL_OCR.get(_normalizada(razon), "procesamiento_fallido")
    # Defensa: una razon sumada a la tabla sin sumar su codigo a CAUSAS_DE_ERROR no se guarda.
    return codigo if codigo in CAUSAS_DE_ERROR else "procesamiento_fallido"


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
            error = (codigo_del_error_del_archivo(resultado.get("error"))
                     or ("rechazado" if crudo == "rechazado" else "procesamiento_fallido"))
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


async def _despachar(pool) -> None:
    """Una o varias PASADAS sobre la cola. Cada pasada pide `LIMITE_DE_FILAS_POR_CICLO` filas por `id` SIN las
    clases ya frenadas en este ciclo y las despacha por grupos (proyecto, dueno, clase). Un 503
    `extractores_no_disponibles` frena la CLASE entera (la falta de una biblioteca no es de un proyecto) hasta
    el fin del ciclo: sus filas quedan en_cola y la pasada siguiente pide las que siguen, sin esa clase. Asi,
    1.000 pdf atascados en los ids bajos no llenan la ventana y las imagenes que llegaron despues salen en el
    mismo ciclo (MAJOR-N2). Se repite mientras una pasada frene una clase nueva: como mucho una pasada por clase."""
    global _freno_incertidumbre_activo, _supresion_freno_incertidumbre_registrada
    global _aviso_freno_incidente_entregado, _fallos_aviso_freno_incertidumbre
    global _ultima_actividad_freno_incertidumbre, _pausa_previa_al_incidente
    frenadas: set[str] = set()
    por_trabajo = await ajustes.valor(ajustes.DOC_RUTAS_POR_TRABAJO)
    ahora = _reloj()
    for i in [i for i, hasta in _en_incertidumbre.items() if hasta <= ahora]:
        del _en_incertidumbre[i]
    # FRENO: si LAS MANOS corta las conexiones despues de recibir el pedido, las filas en incertidumbre se acumulan
    # (cada una, hasta VENTANA_DE_INCERTIDUMBRE_SEGUNDOS) y cada reintento puede duplicar el trabajo. Con
    # `2 * rutas_por_trabajo` o mas, el ciclo NO despacha nada (falla cerrado) y la lista que se le pasa a la
    # consulta (`NOT IN`) queda acotada.
    freno_activo = len(_en_incertidumbre) >= 2 * por_trabajo
    if not _en_incertidumbre:
        _freno_incertidumbre_activo = False
        _supresion_freno_incertidumbre_registrada = False
        _aviso_freno_incidente_entregado = False
        # El contador de fallos NO se reinicia aca: ver `_pausa_previa_al_incidente`.
    if freno_activo:
        logger.warning("proyectos_documentos: %s fila(s) en incertidumbre (tope %s = 2 x rutas_por_trabajo): este "
                       "ciclo no despacha nada hasta que venzan; revisar si LAS MANOS corta las conexiones",
                       len(_en_incertidumbre), 2 * por_trabajo)
        if not _freno_incertidumbre_activo:
            _freno_incertidumbre_activo = True
            _aviso_freno_incidente_entregado = False
            _pausa_previa_al_incidente = (None if _ultima_actividad_freno_incertidumbre is None
                                          else ahora - _ultima_actividad_freno_incertidumbre)
        _ultima_actividad_freno_incertidumbre = ahora
        if _aviso_freno_incertidumbre_pendiente:
            return
        if _aviso_freno_incidente_entregado:
            return
        clave = ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S
        try:
            enfriamiento = await ajustes.valor(clave)
            clave = ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S
            reintento = await ajustes.valor(clave)
        except Exception as exc:  # fail-soft: config ilegible suprime solo el aviso; el freno cerrado sigue activo
            # Nombra la clave que de verdad fallo (AjusteIlegible la trae; otro error, la que se estaba leyendo).
            clave_fallida = exc.clave if isinstance(exc, ajustes.AjusteIlegible) else clave
            logger.error("proyectos_documentos: no se pudo leer el ajuste %s del aviso de incertidumbre (%s); "
                         "se reintentará en el próximo ciclo", clave_fallida, type(exc).__name__)
            return
        if (_fallos_aviso_freno_incertidumbre and _pausa_previa_al_incidente is not None
                and _pausa_previa_al_incidente >= enfriamiento):
            _fallos_aviso_freno_incertidumbre = 0  # paso un enfriamiento completo sin incidente
        _pausa_previa_al_incidente = None  # se evalua una vez por incidente, no en cada ciclo
        if _fallos_aviso_freno_incertidumbre and _ultimo_fallo_aviso_freno_incertidumbre is not None:
            # Tras un fallo (entrega incierta) se espera 1x, 2x, 4x... el minimo, con tope en el enfriamiento
            # (o en el minimo si el enfriamiento es menor): sin esto se reenvia en cada ciclo.
            espera = min(reintento * 2 ** min(_fallos_aviso_freno_incertidumbre - 1, 30),
                         max(enfriamiento, reintento))
            if ahora - _ultimo_fallo_aviso_freno_incertidumbre < espera:
                return
        if (_ultimo_aviso_freno_incertidumbre is None
                or ahora - _ultimo_aviso_freno_incertidumbre >= enfriamiento):
            _supresion_freno_incertidumbre_registrada = False
            _programar_aviso_freno_incertidumbre(len(_en_incertidumbre), 2 * por_trabajo)
        elif not _supresion_freno_incertidumbre_registrada:
            logger.warning("proyectos_documentos: aviso de incertidumbre suprimido por enfriamiento; "
                           "el freno sigue activo y se reintentará al vencer")
            _supresion_freno_incertidumbre_registrada = True
        return
    saltados: set[tuple[str, object]] = set()
    for _pasada in range(len(tipos.CLASES) + 1):
        antes = len(frenadas)
        if await _pasada_de_despacho(pool, por_trabajo, frenadas, saltados) == "cortar":
            return
        if len(frenadas) == antes:
            return


def _programar_aviso_freno_incertidumbre(cantidad: int, umbral: int) -> None:
    """Encola Telegram en su propia tarea: un canal lento no alarga el ciclo del despachador."""
    global _aviso_freno_incertidumbre_pendiente
    _aviso_freno_incertidumbre_pendiente = True
    tarea = asyncio.get_running_loop().create_task(_entregar_aviso_freno_incertidumbre(cantidad, umbral))
    _avisos_freno_incertidumbre.add(tarea)
    tarea.add_done_callback(_avisos_freno_incertidumbre.discard)


async def _entregar_aviso_freno_incertidumbre(cantidad: int, umbral: int) -> None:
    """Confirma el enfriamiento solo tras entrega; un fallo queda listo para reintento."""
    global _ultimo_aviso_freno_incertidumbre, _aviso_freno_incertidumbre_pendiente
    global _aviso_freno_incidente_entregado, _fallos_aviso_freno_incertidumbre
    global _ultimo_fallo_aviso_freno_incertidumbre
    try:
        desenlace = await _enviar_aviso_freno_incertidumbre(cantidad, umbral)
        if desenlace is Desenlace.FALLO_CIERTO:
            _fallos_aviso_freno_incertidumbre += 1
            _ultimo_fallo_aviso_freno_incertidumbre = _reloj()
        else:
            # ENTREGADO, o DESCONOCIDO (ReadTimeout/corte despues de enviar): el aviso cuenta como entregado para
            # este incidente y NO se reintenta -- un duplicado es peor que perderlo, y el siguiente incidente
            # (pasado el enfriamiento) vuelve a avisar. Solo la entrega CONFIRMADA reinicia el contador.
            _ultimo_aviso_freno_incertidumbre = _reloj()
            _aviso_freno_incidente_entregado = True
            if desenlace is Desenlace.ENTREGADO:
                _fallos_aviso_freno_incertidumbre = 0
    finally:
        _aviso_freno_incertidumbre_pendiente = False


async def _enviar_aviso_freno_incertidumbre(cantidad: int, umbral: int) -> Desenlace:
    """Envío best-effort por el canal compartido; nunca manipula ni registra credenciales."""
    mensaje = ("⚠️ JAX: freno de incertidumbre activo\n"
               f"El despachador de documentos dejó de enviar trabajos: {cantidad} filas tienen "
               f"desenlace incierto (umbral {umbral}). Se reanudará al vencer la ventana de incertidumbre. "
               "Revisar si LAS MANOS está cortando las conexiones.")
    try:
        desenlace = await _enviar_telegram_con_desenlace(mensaje)
    except Exception as exc:  # fail-soft: un aviso ausente o roto no puede frenar el despachador
        logger.error("proyectos_documentos: falló el envío del aviso de incertidumbre (%s)", type(exc).__name__)
        return Desenlace.FALLO_CIERTO
    if desenlace is not Desenlace.ENTREGADO:
        logger.error("proyectos_documentos: Telegram no confirmó la entrega del aviso de incertidumbre (%s); "
                     "revisar credenciales y el motivo registrado por catalogo_modelos_ejecutor", desenlace.value)
    return desenlace


async def _pasada_de_despacho(pool, por_trabajo: int, frenadas: set[str], saltados: set) -> str | None:
    """Una pasada. Devuelve 'cortar' si hay que dejar el ciclo; agrega a `frenadas` las clases que LAS MANOS frene."""
    por_grupo: dict[tuple[str, object, str], list[dict]] = {}
    ajenas: list[tuple[int, object]] = []
    # Las filas con desenlace incierto no se piden: contarian contra el LIMIT y despues se saltarian, y con
    # LIMITE o mas de ellas las sanas de atras nunca entrarian en la ventana. Viven en la memoria de este proceso
    # (no en la base), asi que se pasan como ids; la condicion es temporal y ya se podo arriba por `_reloj`.
    for fila in await repo.tomar_en_cola(pool, limite=LIMITE_DE_FILAS_POR_CICLO, excluir_clases=frozenset(frenadas),
                                         excluir_ids=frozenset(_en_incertidumbre)):
        if fila["id"] in _en_incertidumbre:      # respaldo: se agrego una entre la consulta y aqui
            continue
        if not _ruta_del_proyecto(fila["project_uuid"], fila["ruta_entrada"]):
            ajenas.append((fila["id"], fila["owner"]))
            continue
        por_grupo.setdefault((fila["project_uuid"], fila["owner"], tipos.clase_de(fila["ruta_entrada"])), []).append(fila)
    if ajenas:
        logger.error("proyectos_documentos: %s fila(s) con una ruta que no es de su proyecto pasan a error "
                     "ruta_ajena sin mandarse a LAS MANOS: %s", len(ajenas), ajenas[:20])
        for owner in {owner for _, owner in ajenas}:
            await repo.marcar_error_en_cola(pool, ids=[id_ for id_, current in ajenas if current == owner],
                                             error="ruta_ajena", owner=owner)
    for (project_uuid, contexto, clase), filas in por_grupo.items():
        if clase in frenadas or (project_uuid, contexto) in saltados:
            continue
        for i in range(0, len(filas), por_trabajo):
            accion = await _despachar_trozo(pool, project_uuid, contexto, filas[i:i + por_trabajo])
            if accion == "cortar":
                return "cortar"
            if accion == "saltar_grupo":
                frenadas.add(clase)                         # la clase entera, en todos los proyectos, hasta el fin del ciclo
                break
            if accion == "saltar_proyecto":
                saltados.add((project_uuid, contexto))      # el proyecto entero, tambien sus otras clases
                break
    return None


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


def _corriendo_bajo_pytest() -> bool:
    import sys
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


async def start_despachador(forzado: bool = False):
    """Tarea de fondo del lifespan (corre al arrancar, despues duerme; nunca muere por un
    fallo). Intervalo: INTERVALO_SEGUNDOS.

    No arranca bajo pytest, igual que `start_reintento_de_uso` y `start_facet_canary`:
    el fixture `client` levanta el lifespan entero y un despacho de fondo durante los
    tests es ruido no determinista (2026-10-04, fallo intermitente de CI en el PR #192:
    test_mapa_de_estados[parcial-parcial] quedaba 'pendiente' porque el ciclo de fondo
    tomaba el GET_LOCK del despacho y el ciclo del test volvia sin hacer nada).
    `forzado=True` es solo para el test que ejercita el loop."""
    if not forzado and _corriendo_bajo_pytest():
        logger.warning("proyectos_documentos: el despachador no arranca bajo pytest")
        return
    try:
        while True:
            try:
                await ciclo(await get_pool())
            except Exception:  # fail-soft: loop de despacho en background, mismo patron que start_limpieza_de_adjuntos -- nunca debe tumbar el proceso; las filas siguen en_cola y el proximo ciclo reintenta
                logger.warning("proyectos_documentos: el despachador fallo, se reintenta en el proximo ciclo",
                               exc_info=True)
            await _dormir(INTERVALO_SEGUNDOS)
    finally:
        await _cancelar_avisos_freno_incertidumbre()


async def _cancelar_avisos_freno_incertidumbre() -> None:
    """Cancela y espera los envíos al apagar el despachador; no deja tareas huérfanas."""
    tareas = tuple(_avisos_freno_incertidumbre)
    for tarea in tareas:
        if not tarea.done():
            tarea.cancel()
    if tareas:
        await asyncio.gather(*tareas, return_exceptions=True)
