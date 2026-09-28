"""Configuración del sync PROGRAMADO del catálogo de modelos (2026-09-27,
pedido de Fernando: un modal para encender/apagar el timer y elegir cada
cuánto corre, en vez de editar systemd a mano).

Principio IV (sin hardcoding): la fila única de `catalogo_sync_config` es la
ÚNICA fuente de "cada cuánto" -- el timer de systemd pasa a
`OnCalendar=hourly` (ver ops/migration/systemd-units/jax-catalogo-modelos.timer)
y `catalogo_modelos_ejecutor.py` decide, leyendo esta tabla, si de verdad
toca sincronizar en cada corrida horaria.

Dos capas separadas a propósito:
- Aritmética PURA (`validar_config`, `sumar_meses`, `proxima_corrida`,
  `toca_correr`): sin DB, fácil de probar exhaustivamente.
- Lectura/escritura de la fila (`leer_config`, `actualizar_config`): reciben
  un cursor YA abierto -- el llamador decide la transacción (el ejecutor lee
  dentro de la suya, el endpoint de la API escribe dentro de la suya, con su
  propia auditoría).
"""
from __future__ import annotations

import calendar
import datetime as _dt
import json
import logging

logger = logging.getLogger("catalogo_sync_config")

UNIDADES_VALIDAS = ("horas", "dias", "semanas", "meses")

# Límites razonables por unidad (mínimo, máximo) -- validados SIEMPRE en el
# backend (Principio: la regla de negocio no vive solo en el formulario).
LIMITES = {
    "horas": (1, 720),   # hasta 30 días
    "dias": (1, 90),
    "semanas": (1, 12),
    "meses": (1, 12),
}

#: El valor de HOY (decisión de Fernando, 2026-09-27): conservar el
#: comportamiento actual (cada 6 horas, encendido) al introducir el apagador.
VALORES_POR_DEFECTO = {"habilitado": True, "cada_valor": 6, "cada_unidad": "horas"}

_CAMPOS_CONFIG = ("habilitado", "cada_valor", "cada_unidad", "actualizado_por", "actualizado_en")


class ConfigInvalidaError(ValueError):
    """`cada_valor`/`cada_unidad` fuera de rango, o unidad desconocida.
    `campo` identifica cuál, para que el 422 del endpoint pueda señalarlo
    sin que el frontend tenga que parsear el mensaje."""

    def __init__(self, mensaje: str, campo: str):
        super().__init__(mensaje)
        self.campo = campo


def validar_config(cada_valor, cada_unidad) -> None:
    if cada_unidad not in UNIDADES_VALIDAS:
        raise ConfigInvalidaError(
            f"cada_unidad debe ser una de {UNIDADES_VALIDAS}, no {cada_unidad!r}", "cada_unidad")
    # bool es subclase de int en Python -- True/False no son "un entero"
    # válido acá, aunque isinstance(True, int) sea verdadero.
    if not isinstance(cada_valor, int) or isinstance(cada_valor, bool):
        raise ConfigInvalidaError(f"cada_valor debe ser un entero, no {cada_valor!r}", "cada_valor")
    minimo, maximo = LIMITES[cada_unidad]
    if not (minimo <= cada_valor <= maximo):
        raise ConfigInvalidaError(
            f"cada_valor para {cada_unidad!r} debe estar entre {minimo} y {maximo}, no {cada_valor}",
            "cada_valor")


def sumar_meses(momento: _dt.datetime, meses: int) -> _dt.datetime:
    """Meses de CALENDARIO, no 30 días -- el día se recorta al último día
    del mes destino si no existe (31-ene + 1 mes -> 28-feb, o 29-feb en
    bisiesto). Conserva hora/minuto/segundo."""
    mes_total = momento.month - 1 + meses
    anio = momento.year + mes_total // 12
    mes = mes_total % 12 + 1
    ultimo_dia_del_mes = calendar.monthrange(anio, mes)[1]
    dia = min(momento.day, ultimo_dia_del_mes)
    return momento.replace(year=anio, month=mes, day=dia)


def proxima_corrida(desde: _dt.datetime, cada_valor: int, cada_unidad: str) -> _dt.datetime:
    """Cuándo tocaría la PRÓXIMA corrida, contando desde `desde` (la última
    actualización exitosa)."""
    if cada_unidad == "horas":
        return desde + _dt.timedelta(hours=cada_valor)
    if cada_unidad == "dias":
        return desde + _dt.timedelta(days=cada_valor)
    if cada_unidad == "semanas":
        return desde + _dt.timedelta(weeks=cada_valor)
    if cada_unidad == "meses":
        return sumar_meses(desde, cada_valor)
    raise ConfigInvalidaError(f"cada_unidad desconocida: {cada_unidad!r}", "cada_unidad")


#: MAJOR-1 (auditoría adversarial, 2026-09-27): el timer de systemd corre
#: `OnCalendar=hourly` con `RandomizedDelaySec=2min` (ver ops/migration/
#: systemd-units/jax-catalogo-modelos.timer) más la duración real del propio
#: chequeo (medida ~4s, el caso "no toca" es barato -- una lectura de
#: config y un SELECT). Sin tolerancia, comparar `ahora >= proxima_corrida`
#: a secas hace que "cada 1h" a veces rinda 1-2h y "cada 6h" a veces rinda
#: 6-7h: el tick que "debería" tocar puede llegar unos segundos ANTES del
#: instante exacto (el jitter del tick anterior fue más grande que el de
#: éste), y entonces hay que esperar al tick siguiente -- una hora entera
#: de más. TOLERANCIA_SEGUNDOS (10 min) es mayor que el peor jitter posible
#: de un solo tick (RandomizedDelaySec=2min + ~4s de duración): perdona
#: exactamente esa holgura, nunca acumula entre corridas (cada cálculo
#: parte de la última EXITOSA real, no de un calendario rígido -- ver
#: `toca_correr`).
TOLERANCIA_SEGUNDOS = 10 * 60


def toca_correr(ultima_exitosa: _dt.datetime | None, ahora: _dt.datetime, cada_valor: int, cada_unidad: str) -> bool:
    """¿Ya pasó el intervalo desde la última actualización EXITOSA?

    Sin ninguna corrida exitosa previa, toca siempre (arranque de una base
    nueva, o venimos encadenando corridas con problemas -- el timer corre
    cada hora ahora y reintenta hasta que un sync exitoso vuelva a fijar la
    marca; no hay "última exitosa" con la que esperar).

    MINOR-2 (auditoría adversarial, 2026-09-27): si `ultima_exitosa` diera
    DESPUÉS de `ahora` (reloj o zona horaria movidos hacia atrás -- misma
    anomalía que `model_catalog._nuevos_desde_marca_bajo_candado` ya cubre
    del lado de la marca de "nuevos"), no hay nada confiable contra qué
    comparar: se corre igual (nunca se "atasca" esperando una fecha del
    futuro que tal vez nunca llegue) y se deja constancia con un `warning`
    -- el llamador (`catalogo_modelos_ejecutor._correr`) es quien decide si
    además avisa a alguien.

    MAJOR-1: la comparación real perdona `TOLERANCIA_SEGUNDOS` -- ver su
    docstring."""
    if ultima_exitosa is None:
        return True
    if ultima_exitosa > ahora:
        logger.warning(
            "catalogo_sync_config.toca_correr: última actualización exitosa (%s) da DESPUÉS "
            "de ahora (%s) -- reloj o zona horaria movidos hacia atrás; se corre igual",
            ultima_exitosa, ahora,
        )
        return True
    limite = proxima_corrida(ultima_exitosa, cada_valor, cada_unidad) - _dt.timedelta(seconds=TOLERANCIA_SEGUNDOS)
    return ahora >= limite


async def leer_config(cur) -> dict:
    """La fila única (id=1). `_seed_catalogo_sync_config` (db/migrations.py)
    la siembra siempre -- si falta, es una base sin migrar, no un estado
    normal a tolerar en silencio.

    MINOR-3 (auditoría adversarial, 2026-09-27): la fila se valida con
    `validar_config` antes de devolverse -- una fila corrupta (escrita a
    mano, o por un bug futuro que sortee `actualizar_config`) tiene que
    volverse un ERROR ruidoso (el ejecutor programado sale 1, ver
    `catalogo_modelos_ejecutor._correr`), nunca un `toca_correr()` que
    interprete basura como "corré cada hora" o "no corras nunca" en
    silencio."""
    await cur.execute(
        "SELECT habilitado, cada_valor, cada_unidad, actualizado_por, actualizado_en "
        "FROM catalogo_sync_config WHERE id=1"
    )
    fila = await cur.fetchone()
    if fila is None:
        raise RuntimeError(
            "catalogo_sync_config no tiene la fila id=1 -- ¿faltó correr run_migrations()?")
    habilitado, cada_valor, cada_unidad, actualizado_por, actualizado_en = fila
    validar_config(cada_valor, cada_unidad)
    return {
        "habilitado": bool(habilitado),
        "cada_valor": cada_valor,
        "cada_unidad": cada_unidad,
        "actualizado_por": actualizado_por,
        "actualizado_en": actualizado_en,
    }


#: `config_key` fijo bajo el que queda el historial de esta pantalla en
#: `axioma_config_audit` -- MAJOR-2, auditoría adversarial 2026-09-27. No es
#: una clave de `axioma_config` (esta configuración vive en su propia tabla
#: tipada, no en el almacén genérico) -- se reusa la MISMA tabla de
#: auditoría que `config_audit.py`, con su propio `origen` ('catalogo_sync',
#: ver ORIGENES en config_audit.py y el CHECK en db/migrations.py), porque
#: sirve exactamente al mismo propósito (quién cambió qué configuración y
#: cuándo) y no hace falta una tabla de auditoría nueva para eso.
CONFIG_KEY_AUDITORIA = "catalogo_sync_config"

_SQL_AUDITORIA = (
    "INSERT INTO axioma_config_audit "
    "(ts, actor_user_id, config_key, valor_anterior, valor_nuevo, origen, ip) "
    "VALUES (UTC_TIMESTAMP(6), %s, %s, %s, %s, 'catalogo_sync', %s)"
)

_CAMPOS_AUDITABLES = ("habilitado", "cada_valor", "cada_unidad")


async def actualizar_config(cur, *, habilitado: bool, cada_valor, cada_unidad: str,
                            actualizado_por: int, ip: str | None = None) -> dict:
    """Valida ANTES de escribir (nada se toca si `cada_valor`/`cada_unidad`
    no pasan `validar_config`). El UPDATE y su auditoría van con el MISMO
    cursor -- el llamador (`PUT /admin/models/sync/config`) lo abre con
    `db.transaccion.transaccion()`, así que si el INSERT de auditoría de
    abajo revienta, el UPDATE se revierte con él (fail-closed, mismo
    criterio que `api/admin/config_admin.py::update_config`). Un
    `actualizado_por` sin cambios reales (mismos 3 valores) NO escribe
    auditoría -- mismo criterio que `config_audit.escribir()`: guardar lo
    mismo no es un cambio que auditar."""
    validar_config(cada_valor, cada_unidad)
    antes = await leer_config(cur)
    await cur.execute(
        "UPDATE catalogo_sync_config SET habilitado=%s, cada_valor=%s, cada_unidad=%s, "
        "actualizado_por=%s, actualizado_en=UTC_TIMESTAMP() WHERE id=1",
        (bool(habilitado), cada_valor, cada_unidad, actualizado_por),
    )
    despues = await leer_config(cur)

    antes_auditable = {k: antes[k] for k in _CAMPOS_AUDITABLES}
    despues_auditable = {k: despues[k] for k in _CAMPOS_AUDITABLES}
    if antes_auditable != despues_auditable:
        await cur.execute(_SQL_AUDITORIA, (
            int(actualizado_por), CONFIG_KEY_AUDITORIA,
            json.dumps(antes_auditable, default=str), json.dumps(despues_auditable, default=str),
            ip,
        ))

    logger.info(
        "catalogo_sync_config actualizado by=%s antes=%s despues=%s",
        actualizado_por, json.dumps(antes_auditable, default=str), json.dumps(despues_auditable, default=str),
    )
    return despues
