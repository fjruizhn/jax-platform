"""Ajustes de administración que MANDAN (spec 2026-09-16-hallazgos-auditoria §C,
decisión de Fernando del mismo día: los cinco, con el valor que el código
hacía cumplir como valor inicial).

Hasta el 2026-09-16 la pantalla Configuración guardaba estas cinco claves en
axioma_config y NADIE las leía: la sesión duraba 7 días fijos (auth/jwt.py),
el cupo era 3 fijo (resource_manager.py), (la retención de tareas web, 30 días fija, se retiró en T16 con /command), el idioma 'es' y el nombre "Axioma" desde i18n.

Reglas:
  - Una sola consulta por PRIMARY para las cinco (CONSULTA; EXPLAIN en
    tests/test_ajustes.py).
  - Validación por clave con rangos del SERVIDOR. Un valor ausente o inválido
    es AjusteIlegible -> 503 `ajuste_ilegible` con la clave: nunca un default
    silencioso. Cada clave se valida por separado: un system_name roto no
    tumba el login.
  - Caché con TTL (JAX_AJUSTES_TTL_S) e invalidación EXPLÍCITA en el PUT de
    /api/admin/config. La invalidación vive en memoria de UN proceso, por eso
    este módulo exige un solo worker. El TTL acota lo que tarda en verse una
    edición hecha por fuera del PUT (SQL a mano, migración).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
import time
import weakref
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable

from fastapi.responses import JSONResponse

from auth.constantes import ACCESS_EXPIRE_SECONDS
from auth.rate_limit import exigir_un_solo_proceso
from db.connection import get_pool
from validacion import tiene_caracteres_de_control

logger = logging.getLogger(__name__)

# La invalidación del PUT no cruzaría a otro worker.
exigir_un_solo_proceso(os.environ, sys.argv)

# Espejo TEXTUAL de jax/jacobs/policy.py (familia `tope_pipelines` de
# jax/scripts/check_mirror_sync.py, que compara el segmento del AST: el doble
# espacio es parte de la copia). Jacobs cuenta TODOS los pipelines
# pending/running, de todos los tenants e invocadores, y rechaza el que
# excede con 422: un max_pipelines por tenant mayor que esto sería una
# promesa que Jacobs no cumple.
MAX_PARALLEL_PIPELINES  = 3

SESION = "session_timeout_min"
MAX_PIPELINES = "max_pipelines"
IDIOMA = "lang_default"
NOMBRE = "system_name"
# Umbral de confirmación de costo de un pipeline (spec 2026-09-17 §6.1): por
# encima de este costo máximo, o con un paso sin precio, la Mesa pide
# confirmación en ventana propia. 0 = confirmar siempre.
CONFIRMAR_USD = "pipeline_confirmar_usd"
# Cuántas veces puede el árbitro de Jacobs DEVOLVER un pipeline antes de
# rendirse y terminarlo en `disputed` (spec 2026-09-18-arbitro-devuelve §3.3).
# El nombre canónico lo fija `jacobs/store.py::_CONFIG_KEY_TOPE_DEVOLUCIONES`
# en el repo jax, que es quien lo LEE; acá sólo se administra. Prefijo
# `jacobs.` y no `ejecutor.`: es del orquestador, no del Ejecutor de Contratos.
#
# CERO es válido y no es un error: es el estado fail-closed que impone
# `get_tope_devoluciones()` cuando falta la fila -- el árbitro puede objetar
# pero no devuelve, y la primera objeción termina el pipeline en `disputed`.
TOPE_DEVOLUCIONES = "jacobs.tope_devoluciones"
# Edad máxima, en segundos, de un respaldo para que C2 lo dé por vigente (Ejecutor
# de Contratos). La siembra db/migrations.py::_ejecutor_reglas_v1 con 86400 y la
# LEE el repo jax por SQL directo (ejecutor/contratos/exportar.py: entero > 0), no
# por este módulo: acá sólo se administra y se acota. El diseño de C2 pide 30 h
# (108000): con 24 h el Ejecutor queda sin respaldo vigente entre una corrida del
# respaldo y la siguiente. El valor de diseño de C2 es 108000 (30 h). El mínimo (1 h) y el
# máximo (7 días) son la baranda TÉCNICA de esta rama para que un valor absurdo no se
# guarde; el techo lo decide Fernando, y si hace falta otro se cambia acá con su motivo.
C2_EDAD_MAX_S = "ejecutor.c2_edad_max_s"
C2_EDAD_MAX_S_MIN = 3600
C2_EDAD_MAX_S_MAX = 7 * 86400
# Topes de la subida de documentos de un proyecto (Proyectos E2a, T5; Fernando,
# 2026-09-25: 100 MB por archivo, 250 archivos o 1 GB por lote). Los valores
# iniciales los siembra db/migrations.py::_proyectos_documentos_topes_v1.
DOC_MAX_BYTES_ARCHIVO = "proyectos.documentos.max_bytes_archivo"
DOC_MAX_ARCHIVOS_LOTE = "proyectos.documentos.max_archivos_lote"
DOC_MAX_BYTES_LOTE = "proyectos.documentos.max_bytes_lote"
DOC_RUTAS_POR_TRABAJO = "proyectos.documentos.rutas_por_trabajo"
# Subidas de documentos SIMULTANEAS (E2a ronda final, MAJOR-4): por usuario y en todo el
# servicio. Las usa proyectos_documentos/cupo_de_subidas.py; las siembra la misma migracion.
DOC_SUBIDAS_POR_USUARIO = "proyectos.documentos.subidas_por_usuario"
DOC_SUBIDAS_GLOBALES = "proyectos.documentos.subidas_globales"
# Reprocesos de documentos SIMULTANEOS (jax-platform#186): cupo PROPIO, aparte del de subir, porque un
# reprocesar sin ficha recorre y hashea todo `fuente/` (medido: ~16 s con 4 a la vez en un `fuente/` de
# 20.000 archivos) y no puede dejar sin cupo a las subidas. Las usa proyectos_documentos/cupo_de_reprocesos.py;
# las siembra db/migrations.py::_proyectos_documentos_topes_v1.
DOC_REPROCESAR_POR_USUARIO = "proyectos.documentos.reprocesar_por_usuario"
DOC_REPROCESAR_GLOBALES = "proyectos.documentos.reprocesar_globales"
CLAVES = (SESION, MAX_PIPELINES, IDIOMA, NOMBRE, CONFIRMAR_USD, TOPE_DEVOLUCIONES,
          DOC_MAX_BYTES_ARCHIVO, DOC_MAX_ARCHIVOS_LOTE, DOC_MAX_BYTES_LOTE, DOC_RUTAS_POR_TRABAJO,
          DOC_SUBIDAS_POR_USUARIO, DOC_SUBIDAS_GLOBALES, DOC_REPROCESAR_POR_USUARIO, DOC_REPROCESAR_GLOBALES)
# Claves que esta pantalla administra y valida pero que ESTE servicio no lee (las lee el
# repo jax por SQL directo). Quedan fuera de CLAVES a propósito: CLAVES arma la consulta
# única del caché de lectura, y sumar una clave que nadie lee acá (a) la haría "ilegible"
# en el arranque si falta la fila y (b) con 15 claves sobre una tabla de ~25 filas el
# optimizador prefiere recorrer la tabla entera (EXPLAIN: type ALL) y el control
# `test_explain_de_la_consulta_real_va_por_primary` falla.
C5_AUDITOR_NUBE_SOLO_ORDENES = "ejecutor.c5_auditor_nube_solo_ordenes"
CLAVES_SOLO_ADMINISTRADAS = (C2_EDAD_MAX_S, C5_AUDITOR_NUBE_SOLO_ORDENES)

MIB = 1024 * 1024
GIB = 1024 * MIB
# El techo de rutas por trabajo es el de LAS MANOS: un valor mayor lo rechazaría
# ella con 422, así que acá sería una promesa que no se cumple.
DOC_RUTAS_POR_TRABAJO_MAX = 50
# Techos de las subidas simultaneas: cada subida en vuelo puede volcar hasta
# max_bytes_lote a TMPDIR antes de cualquier tope, asi que el techo global acota ese disco
# (50 x 1 GiB con los topes de hoy). Mas que eso no es un tope sino apagarlo.
DOC_SUBIDAS_POR_USUARIO_MAX = 10
DOC_SUBIDAS_GLOBALES_MAX = 50
# Techo de los reprocesos simultaneos: cada uno es un recorrido completo de `fuente/` (CPU y GIL),
# asi que el techo es bajo; mas de 4 a la vez ya degradaba la lista de documentos (medido).
DOC_REPROCESAR_MAX = 4

# Baranda ELEGIDA EN ESTA RAMA (2026-09-20), no en el spec: el spec fija el
# valor inicial en 2 y explica el porqué del tope --«sin tope, dos modelos
# pueden discutir toda la noche gastando dinero real»-- pero no pone un techo.
# Cinco vueltas ya son una discusión larga; si alguna vez hace falta más, se
# sube acá con su motivo escrito, no con un UPDATE a mano.
TOPE_DEVOLUCIONES_MAX = 5

IDIOMAS = ("es", "en")
NOMBRE_MAX = 60
# Un refresh que viva menos que el access no se llegaría a usar.
# OJO: subir ACCESS_EXPIRE_SECONDS sube SESION_MIN y puede dejar ilegible un
# session_timeout_min ya guardado; lo mismo bajar MAX_PARALLEL_PIPELINES con
# max_pipelines. El arranque lo nombra en un ERROR (avisar_claves_ilegibles,
# main.py) y el endpoint que lo lea responde 503.
SESION_MIN = ACCESS_EXPIRE_SECONDS // 60
SESION_MAX = 10080  # 7 días: la vida que el código hacía cumplir el 2026-09-16
CONFIRMAR_USD_MAX = "999999.99"
CONFIRMAR_USD_DECIMALES = 2
# Canónico: sin ceros a la izquierda, punto decimal, hasta 2 decimales, ASCII.
_MONTO_USD = re.compile(r"(0|[1-9][0-9]{0,5})(\.[0-9]{1,2})?")

CONSULTA = (
    "SELECT config_key, config_value FROM axioma_config WHERE config_key IN ("
    + ", ".join(["%s"] * len(CLAVES)) + ")"
)


class ValorInvalido(ValueError):
    pass


class ClaveNoLeida(LookupError):
    """`valor()` no lee esta clave: se administra acá pero la lee otro servicio."""


class AjusteIlegible(Exception):
    codigo = "ajuste_ilegible"

    def __init__(self, clave: str, motivo: str):
        super().__init__(f"{clave}: {motivo}")
        self.clave = clave
        self.motivo = motivo


def _entero(minimo: int, maximo: int) -> Callable[[str], int]:
    def interpretar(texto: str) -> int:
        canonico = texto.isascii() and texto.isdigit() and (texto == "0" or not texto.startswith("0"))
        if not canonico or not minimo <= int(texto) <= maximo:
            raise ValorInvalido(texto)
        return int(texto)
    return interpretar


def _monto_usd(texto: str) -> Decimal:
    if not texto.isascii() or not _MONTO_USD.fullmatch(texto):
        raise ValorInvalido(texto)
    monto = Decimal(texto)
    # El regex fija la FORMA (canónico, hasta 2 decimales); el rango se
    # compara explícito contra la constante, igual que _entero, para que no
    # pueda divergir en silencio de lo que limites() publica.
    if not Decimal("0") <= monto <= Decimal(CONFIRMAR_USD_MAX):
        raise ValorInvalido(texto)
    return monto


def _idioma(texto: str) -> str:
    if texto not in IDIOMAS:
        raise ValorInvalido(texto)
    return texto


def _nombre(texto: str) -> str:
    if (not texto or texto != texto.strip() or len(texto) > NOMBRE_MAX
            or tiene_caracteres_de_control(texto)):
        raise ValorInvalido(texto)
    return texto


@dataclass(frozen=True)
class Definicion:
    interpretar: Callable[[str], int | str | Decimal]
    limites: dict


def _booleano_estricto(texto: str) -> bool:
    if texto == "true":
        return True
    if texto == "false":
        return False
    raise ValorInvalido(texto)


DEFINICIONES: dict[str, Definicion] = {
    SESION: Definicion(_entero(SESION_MIN, SESION_MAX), {"min": SESION_MIN, "max": SESION_MAX}),
    MAX_PIPELINES: Definicion(_entero(1, MAX_PARALLEL_PIPELINES), {"min": 1, "max": MAX_PARALLEL_PIPELINES}),
    IDIOMA: Definicion(_idioma, {"opciones": list(IDIOMAS)}),
    NOMBRE: Definicion(_nombre, {"max_largo": NOMBRE_MAX}),
    # Los montos viajan como string: un float de JSON no es un monto exacto.
    CONFIRMAR_USD: Definicion(_monto_usd, {"min": "0", "max": CONFIRMAR_USD_MAX,
                                           "decimales": CONFIRMAR_USD_DECIMALES}),
    TOPE_DEVOLUCIONES: Definicion(_entero(0, TOPE_DEVOLUCIONES_MAX),
                                  {"min": 0, "max": TOPE_DEVOLUCIONES_MAX}),
    C2_EDAD_MAX_S: Definicion(_entero(C2_EDAD_MAX_S_MIN, C2_EDAD_MAX_S_MAX),
                              {"min": C2_EDAD_MAX_S_MIN, "max": C2_EDAD_MAX_S_MAX}),
    C5_AUDITOR_NUBE_SOLO_ORDENES: Definicion(_booleano_estricto, {"opciones": ["true", "false"]}),
    DOC_MAX_BYTES_ARCHIVO: Definicion(_entero(MIB, 2 * GIB), {"min": MIB, "max": 2 * GIB}),
    DOC_MAX_ARCHIVOS_LOTE: Definicion(_entero(1, 1000), {"min": 1, "max": 1000}),
    DOC_MAX_BYTES_LOTE: Definicion(_entero(MIB, 10 * GIB), {"min": MIB, "max": 10 * GIB}),
    DOC_RUTAS_POR_TRABAJO: Definicion(_entero(1, DOC_RUTAS_POR_TRABAJO_MAX),
                                      {"min": 1, "max": DOC_RUTAS_POR_TRABAJO_MAX}),
    DOC_SUBIDAS_POR_USUARIO: Definicion(_entero(1, DOC_SUBIDAS_POR_USUARIO_MAX),
                                        {"min": 1, "max": DOC_SUBIDAS_POR_USUARIO_MAX}),
    DOC_SUBIDAS_GLOBALES: Definicion(_entero(1, DOC_SUBIDAS_GLOBALES_MAX),
                                     {"min": 1, "max": DOC_SUBIDAS_GLOBALES_MAX}),
    DOC_REPROCESAR_POR_USUARIO: Definicion(_entero(1, DOC_REPROCESAR_MAX), {"min": 1, "max": DOC_REPROCESAR_MAX}),
    DOC_REPROCESAR_GLOBALES: Definicion(_entero(1, DOC_REPROCESAR_MAX), {"min": 1, "max": DOC_REPROCESAR_MAX}),
}


def interpretar(clave: str, texto: str) -> int | str | Decimal:
    return DEFINICIONES[clave].interpretar(texto)


def limites() -> dict:
    return {clave: dict(d.limites) for clave, d in DEFINICIONES.items()}


def ttl_desde_entorno(texto: str) -> float:
    try:
        ttl = float(texto)
    except ValueError as exc:
        raise ValueError(f"JAX_AJUSTES_TTL_S inválido {texto!r}: segundos > 0") from exc
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"JAX_AJUSTES_TTL_S inválido {texto!r}: segundos > 0")
    return ttl


TTL_S = ttl_desde_entorno(os.getenv("JAX_AJUSTES_TTL_S", "30"))


class CacheDeAjustes:
    """Filas crudas con TTL. `invalidar()` sube la generación: una lectura que
    empezó antes de invalidar entrega lo que leyó a quien la pidió, pero no lo
    guarda. Un lock por event loop (mismo motivo que db/connection.py: en la
    suite conviven dos loops) evita que N requests recarguen a la vez."""

    def __init__(self, cargar: Callable[[], Awaitable[dict[str, str]]], ttl_s: float,
                 reloj: Callable[[], float] = time.monotonic):
        self._cargar = cargar
        self._ttl = ttl_s
        self._reloj = reloj
        self._filas: dict[str, str] | None = None
        self._vence = 0.0
        self._generacion = 0
        self._locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()

    def _vigentes(self) -> dict[str, str] | None:
        if self._filas is not None and self._reloj() < self._vence:
            return self._filas
        return None

    def invalidar(self) -> None:
        self._filas = None
        self._generacion += 1

    async def filas(self) -> dict[str, str]:
        vigentes = self._vigentes()
        if vigentes is not None:
            return vigentes
        loop = asyncio.get_running_loop()
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            vigentes = self._vigentes()
            if vigentes is not None:
                return vigentes
            generacion = self._generacion
            filas = await self._cargar()
            if generacion == self._generacion:
                self._filas = filas
                self._vence = self._reloj() + self._ttl
            return filas


async def _leer_filas() -> dict[str, str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CONSULTA, CLAVES)
            return {clave: valor for clave, valor in await cur.fetchall()}


_cache = CacheDeAjustes(_leer_filas, TTL_S)


def invalidar() -> None:
    _cache.invalidar()


async def valor(clave: str) -> int | str | Decimal:
    if clave in CLAVES_SOLO_ADMINISTRADAS:
        raise ClaveNoLeida(f"{clave}: se administra en este servicio pero la lee otro; no está en el caché")
    filas = await _cache.filas()
    if clave not in filas:
        raise AjusteIlegible(clave, "ausente")
    try:
        return interpretar(clave, filas[clave])
    except ValorInvalido:
        raise AjusteIlegible(clave, "invalido") from None


async def claves_ilegibles() -> list[tuple[str, str]]:
    """(clave, motivo) de cada ajuste que hoy no se puede leer, en el orden de
    CLAVES. Un error de la base NO se traga: sube."""
    ilegibles = []
    for clave in CLAVES:
        try:
            await valor(clave)
        except AjusteIlegible as exc:
            ilegibles.append((exc.clave, exc.motivo))
    return ilegibles


async def avisar_claves_ilegibles() -> list[tuple[str, str]]:
    """Chequeo del arranque (ruling R16, 2026-09-17): un ERROR por clave
    ilegible, con clave y motivo -- nunca el valor. No tumba el arranque: el
    503 de cada endpoint ya es fail-closed; esto lo hace visible antes."""
    ilegibles = await claves_ilegibles()
    for clave, motivo in ilegibles:
        logger.error("arranque: ajuste %s ilegible en axioma_config (%s)", clave, motivo)
    return ilegibles


async def respuesta_de_ajuste_ilegible(request, exc: AjusteIlegible) -> JSONResponse:
    # El valor no se loguea: la clave y el motivo alcanzan para arreglarlo.
    logger.error("ajuste %s ilegible en axioma_config (%s)", exc.clave, exc.motivo)
    return JSONResponse(status_code=503, content={"detail": {"code": AjusteIlegible.codigo, "clave": exc.clave}})
