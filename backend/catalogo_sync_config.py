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


def _config_es_valida(cada_valor, cada_unidad) -> bool:
    try:
        validar_config(cada_valor, cada_unidad)
        return True
    except ConfigInvalidaError:
        return False


async def leer_config_cruda(cur, *, for_update: bool = False) -> dict:
    """La fila única (id=1), SIN VALIDAR -- nunca levanta `ConfigInvalidaError`,
    ni con una fila corrupta. `_seed_catalogo_sync_config` (db/migrations.py)
    la siembra siempre -- si falta, es una base sin migrar, no un estado
    normal a tolerar en silencio (eso sí sigue siendo un `RuntimeError`).

    MINOR-2 (tercera ronda de la auditoría adversarial, 2026-09-27): hace
    falta poder LEER una config inválida para poder REPARARLA -- auditar el
    "antes" tal cual (marcado como inválido, no descartado) y aplicar un
    "después" que sí valida. `leer_config()` (más abajo) es un envoltorio que
    SÍ exige una fila válida, para quien necesita un error ruidoso ante una
    corrupta (el ejecutor programado -- ese comportamiento NO cambia).

    MINOR-3: `for_update=True` agrega `FOR UPDATE` -- lo usa
    `actualizar_config()` dentro de su propia transacción, para que dos PUT
    simultáneos no lean el mismo "antes" y auditen un cambio que nunca pasó
    (mismo criterio que `config_audit.SQL_ANTERIOR`)."""
    sql = ("SELECT habilitado, cada_valor, cada_unidad, actualizado_por, actualizado_en "
           "FROM catalogo_sync_config WHERE id=1")
    if for_update:
        sql += " FOR UPDATE"
    await cur.execute(sql)
    fila = await cur.fetchone()
    if fila is None:
        raise RuntimeError(
            "catalogo_sync_config no tiene la fila id=1 -- ¿faltó correr run_migrations()?")
    habilitado, cada_valor, cada_unidad, actualizado_por, actualizado_en = fila
    return {
        "habilitado": bool(habilitado),
        "cada_valor": cada_valor,
        "cada_unidad": cada_unidad,
        "actualizado_por": actualizado_por,
        "actualizado_en": actualizado_en,
        "valida": _config_es_valida(cada_valor, cada_unidad),
    }


async def leer_config(cur) -> dict:
    """Envoltorio de `leer_config_cruda()` que EXIGE una fila válida:
    `ConfigInvalidaError` si no lo es. Usado por el ejecutor programado
    (`catalogo_modelos_ejecutor.py`), que tiene que salir con error (código 1)
    ante una fila corrupta -- nunca interpretar basura como "corré cada hora"
    o "no corras nunca" en silencio (MINOR-2: este comportamiento no cambia).

    `GET /admin/models/sync/config` usa `leer_config_cruda()` directo -- ya
    NUNCA 500 ante una fila inválida (ver `api/admin/models.py`)."""
    cruda = await leer_config_cruda(cur)
    validar_config(cruda["cada_valor"], cruda["cada_unidad"])  # repropaga con el mensaje real de validar_config
    return {k: v for k, v in cruda.items() if k != "valida"}


#: `config_key` fijo bajo el que queda el historial de esta pantalla en
#: `axioma_config_audit` -- MAJOR-2, auditoría adversarial 2026-09-27. No es
#: una clave de `axioma_config` (esta configuración vive en su propia tabla
#: tipada, no en el almacén genérico) -- se reusa la MISMA tabla de
#: auditoría que `config_audit.py`, con su propio `origen` ('catalogo_sync',
#: ver ORIGENES en config_audit.py y el CHECK en db/migrations.py), porque
#: sirve exactamente al mismo propósito (quién cambió qué configuración y
#: cuándo) y no hace falta una tabla de auditoría nueva para eso.
CONFIG_KEY_AUDITORIA = "catalogo_sync_config"

#: `valida` incluido a propósito (MINOR-2, tercera ronda de la auditoría
#: adversarial, 2026-09-27): al reparar una fila corrupta, el "antes" queda
#: marcado como inválido en el propio rastro de auditoría -- no sólo se
#: audita QUÉ valores tenía, sino que NO eran usables.
_CAMPOS_AUDITABLES = ("habilitado", "cada_valor", "cada_unidad", "valida")


async def actualizar_config(cur, *, habilitado: bool, cada_valor, cada_unidad: str,
                            actualizado_por: int, ip: str | None = None) -> dict:
    """Valida el valor NUEVO antes de escribir (nada se toca si
    `cada_valor`/`cada_unidad` no pasan `validar_config`) -- pero el "antes"
    se lee SIN validar (MINOR-2, tercera ronda de la auditoría adversarial,
    2026-09-27): con `leer_config()` (que valida y levanta
    `ConfigInvalidaError`), una fila YA corrupta no se podía reparar nunca --
    `actualizar_config()` reventaba antes de llegar al UPDATE. Con
    `leer_config_cruda(cur, for_update=True)` (MINOR-3: bajo `FOR UPDATE`,
    misma transacción) se audita el "antes" TAL CUAL, marcado inválido si lo
    es, y se aplica el "después" ya validado.

    El UPDATE y su auditoría van con el MISMO cursor -- el llamador
    (`PUT /admin/models/sync/config`) lo abre con `db.transaccion.transaccion()`,
    así que si la auditoría revienta, el UPDATE se revierte con ella
    (fail-closed, mismo criterio que `api/admin/config_admin.py::update_config`).
    Un `actualizado_por` sin cambios reales (mismos 4 valores, incluida
    validez) NO escribe auditoría -- mismo criterio que
    `config_audit.escribir()`: guardar lo mismo no es un cambio que auditar.

    MINOR-6: la auditoría la escribe `config_audit.auditar()` -- el ÚNICO
    escritor de `axioma_config_audit` en todo el árbol (antes, esta función
    tenía su propio INSERT crudo; ver el detector en
    tests/test_config_audit.py)."""
    validar_config(cada_valor, cada_unidad)
    antes = await leer_config_cruda(cur, for_update=True)
    await cur.execute(
        "UPDATE catalogo_sync_config SET habilitado=%s, cada_valor=%s, cada_unidad=%s, "
        "actualizado_por=%s, actualizado_en=UTC_TIMESTAMP() WHERE id=1",
        (bool(habilitado), cada_valor, cada_unidad, actualizado_por),
    )
    despues = await leer_config_cruda(cur)

    antes_auditable = {k: antes[k] for k in _CAMPOS_AUDITABLES}
    despues_auditable = {k: despues[k] for k in _CAMPOS_AUDITABLES}
    if antes_auditable != despues_auditable:
        from config_audit import auditar
        await auditar(
            cur, actor_user_id=actualizado_por, config_key=CONFIG_KEY_AUDITORIA,
            valor_anterior=json.dumps(antes_auditable, default=str),
            valor_nuevo=json.dumps(despues_auditable, default=str),
            origen="catalogo_sync", ip=ip,
        )

    logger.info(
        "catalogo_sync_config actualizado by=%s antes=%s despues=%s",
        actualizado_por, json.dumps(antes_auditable, default=str), json.dumps(despues_auditable, default=str),
    )
    return {k: v for k, v in despues.items() if k != "valida"}
