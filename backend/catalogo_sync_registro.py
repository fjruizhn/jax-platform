"""Registro de ejecuciones del sync del catálogo (2026-09-27, pedido de
Fernando: avance real + "última actualización" siempre visible).

`catalogo_sync_ejecucion` es una fila por CORRIDA (manual o programada) del
sync completo (`model_catalog.sync_all()`). Este módulo es la orquestación
COMPARTIDA entre las dos vías que hoy disparan un sync -- el endpoint
`POST /admin/models/sync` (en segundo plano) y el ejecutor programado
(`catalogo_modelos_ejecutor.py`) -- para que ninguna de las dos pueda
divergir en qué significa "hay un sync corriendo" ni "cuándo fue la última
actualización" (misma Regla Absoluta que ya aplica `model_catalog.sync_all()`
para "qué significa que el catálogo esté sano").
"""
from __future__ import annotations

import json
import logging

from db.connection import get_pool
from redaccion import redactar_secretos

logger = logging.getLogger("catalogo_sync_registro")

#: Candado de MariaDB para el chequeo+alta ATÓMICOS de "¿hay uno corriendo?
#: si no, reservo mi fila" -- DISTINTO del candado de trabajo real de
#: `model_catalog.sync_all()` ('jax_catalogo_sync'). Éste se toma y se
#: suelta en microsegundos, sólo durante la sección crítica de abajo, nunca
#: durante el sync en sí (que puede tardar minutos -- ver `TimeoutStartSec`
#: del .service).
_NOMBRE_CANDADO_GATE = "jax_catalogo_sync_gate"

#: `TimeoutStartSec` real de `jax-catalogo-modelos.service` (ver
#: ops/migration/systemd-units/) -- una fila 'corriendo' más vieja que esto
#: es un proceso que systemd ya mató (o que crasheó de otra forma sin poder
#: finalizar su propia fila): huérfana, nunca un sync legítimo todavía en
#: curso.
TIMEOUT_HUERFANA_SEGUNDOS = 30 * 60

#: Retención (pedido de Fernando, 2026-09-27): conserva como mucho estas
#: filas TERMINADAS (nunca la que está 'corriendo') y nada más viejo que
#: RETENCION_DIAS -- lo que sea más estricto en cada caso.
RETENCION_FILAS = 200
RETENCION_DIAS = 90

_CAMPOS_EJECUCION = (
    "id", "origen", "iniciado_por", "estado", "paso_actual", "pasos_total",
    "detalle_paso", "iniciado_en", "terminado_en", "resultado",
)


def fila_a_dict(fila) -> dict:
    """Convierte una fila cruda de `catalogo_sync_ejecucion` (columnas en el
    orden de `_CAMPOS_EJECUCION`) en un dict JSON-serializable para la API."""
    d = dict(zip(_CAMPOS_EJECUCION, fila))
    for campo in ("iniciado_en", "terminado_en"):
        if d[campo] is not None:
            d[campo] = str(d[campo])
    if d["resultado"] is not None:
        try:
            d["resultado"] = json.loads(d["resultado"])
        except (TypeError, ValueError):  # fail-soft: un JSON corrupto no puede tumbar /sync/estado
            d["resultado"] = None
    return d


def _resultado_sync_en_curso() -> dict:
    """MISMA forma que `model_catalog._respuesta_sync_en_curso()` -- se
    importa esa función privada a propósito (ver el import de abajo) en vez
    de redefinir el dict acá: una sola fuente de "qué significa sync en
    curso", nunca dos formas que puedan divergir."""
    from model_catalog import _respuesta_sync_en_curso
    return _respuesta_sync_en_curso()


async def marcar_huerfanas_interrumpidas(cur) -> None:
    """Una fila 'corriendo' con más de `TIMEOUT_HUERFANA_SEGUNDOS` es un
    proceso que ya no existe -- se marca 'error' con un resultado explícito,
    nunca queda 'corriendo' para siempre."""
    resultado_huerfana = json.dumps({
        "error": f"interrumpida: más de {TIMEOUT_HUERFANA_SEGUNDOS}s sin terminar (proceso caído)"
    })
    await cur.execute(
        "UPDATE catalogo_sync_ejecucion SET estado='error', terminado_en=NOW(), resultado=%s "
        "WHERE estado='corriendo' AND iniciado_en < NOW() - INTERVAL %s SECOND",
        (resultado_huerfana, TIMEOUT_HUERFANA_SEGUNDOS),
    )


async def reservar_ejecucion(cur, conn, *, origen: str, iniciado_por: int | None, pasos_total: int):
    """Sección crítica ATÓMICA (candado de gate): limpia huérfanas, chequea
    si ya hay una fila 'corriendo' viva y, si no, reserva una nueva fila EN
    LA MISMA transacción.

    Devuelve `(ejecucion_id, None)` si reservó, o `(None, resultado)` si ya
    había una corriendo -- `resultado` tiene la MISMA forma que
    `model_catalog.sync_all()` cuando encuentra su propio candado ocupado
    (`code='sync_en_curso'`): el llamador (el endpoint, el ejecutor
    programado) lo trata exactamente igual en los dos casos."""
    await cur.execute("SELECT GET_LOCK(%s, 5)", (_NOMBRE_CANDADO_GATE,))
    (obtenido,) = await cur.fetchone()
    if obtenido != 1:
        # El propio candado de gate está ocupado (otra alta en curso AHORA
        # MISMO, 5s no alcanzaron) o GET_LOCK reventó (NULL) -- en cualquier
        # caso no se puede confirmar que no hay nada corriendo: se trata
        # igual que "ya hay uno corriendo", nunca como luz verde.
        logger.warning("catalogo_sync_registro: candado de gate no se pudo tomar (obtenido=%r)", obtenido)
        return None, _resultado_sync_en_curso()
    try:
        await marcar_huerfanas_interrumpidas(cur)
        await cur.execute("SELECT id FROM catalogo_sync_ejecucion WHERE estado='corriendo' LIMIT 1")
        fila = await cur.fetchone()
        if fila is not None:
            return None, _resultado_sync_en_curso()

        await cur.execute(
            "INSERT INTO catalogo_sync_ejecucion (origen, iniciado_por, estado, paso_actual, pasos_total) "
            "VALUES (%s, %s, 'corriendo', 0, %s)",
            (origen, iniciado_por, pasos_total),
        )
        ejecucion_id = cur.lastrowid
        await conn.commit()
        return ejecucion_id, None
    finally:
        await cur.execute("SELECT RELEASE_LOCK(%s)", (_NOMBRE_CANDADO_GATE,))


async def actualizar_progreso(ejecucion_id: int, paso_actual: int, pasos_total: int, detalle_paso: str) -> None:
    """Callback de avance real. Abre y cierra SU PROPIA conexión corta --
    nunca retiene la conexión dedicada que `model_catalog.sync_all()`
    necesita para sostener su candado durante TODO el sync (ver su
    docstring: soltarla antes de tiempo soltaría el candado antes de
    tiempo)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE catalogo_sync_ejecucion SET paso_actual=%s, pasos_total=%s, detalle_paso=%s WHERE id=%s",
                (paso_actual, pasos_total, (detalle_paso or "")[:100], ejecucion_id),
            )
        await conn.commit()


async def finalizar_ejecucion(ejecucion_id: int, estado: str, resultado: dict | None) -> None:
    """Cierra la fila: `estado` final (ok/con_problemas/error),
    `terminado_en=NOW()`, y el resumen -- pasado por `redactar_secretos`
    ANTES de guardarse, defensa en profundidad aunque los errores de
    proveedor que trae `resultado` ya vienen redactados desde
    `model_catalog.sync_all()`."""
    pool = await get_pool()
    resultado_crudo = json.dumps(resultado, default=str) if resultado is not None else None
    resultado_seguro = redactar_secretos(resultado_crudo) if resultado_crudo is not None else None
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE catalogo_sync_ejecucion SET estado=%s, terminado_en=NOW(), resultado=%s WHERE id=%s",
                (estado, resultado_seguro, ejecucion_id),
            )
        await conn.commit()


async def ultima_actualizacion_exitosa(cur):
    """`terminado_en` de la última corrida con `estado='ok'` -- cuenta
    tanto manuales como programadas (pedido de Fernando: "contando también
    las manuales"). `None` si nunca hubo ninguna exitosa."""
    await cur.execute(
        "SELECT terminado_en FROM catalogo_sync_ejecucion WHERE estado='ok' "
        "ORDER BY terminado_en DESC LIMIT 1"
    )
    fila = await cur.fetchone()
    return fila[0] if fila else None


async def limpiar_ejecuciones_viejas(cur) -> None:
    """Conserva como mucho `RETENCION_FILAS` filas TERMINADAS (nunca la que
    está 'corriendo') y nada más viejo que `RETENCION_DIAS` -- ambas
    condiciones aplican ("las últimas 200 O 90 días", lo que sea más
    estricto). Se ordena por `id` (monotónico, sin empates) y no por
    `iniciado_en` (que sí puede empatar en el segundo) para que "las
    RETENCION_FILAS más recientes" sea inequívoco."""
    await cur.execute(
        "DELETE FROM catalogo_sync_ejecucion WHERE estado != 'corriendo' "
        "AND iniciado_en < NOW() - INTERVAL %s DAY", (RETENCION_DIAS,))
    await cur.execute(
        "SELECT id FROM catalogo_sync_ejecucion WHERE estado != 'corriendo' "
        "ORDER BY id DESC LIMIT 1 OFFSET %s", (RETENCION_FILAS,))
    fila = await cur.fetchone()
    if fila is not None:
        await cur.execute(
            "DELETE FROM catalogo_sync_ejecucion WHERE estado != 'corriendo' AND id <= %s", (fila[0],))


async def correr_sync_registrado(*, origen: str, iniciado_por: int | None = None, marca_nuevos: str | None = None) -> dict:
    """Corre UN sync completo (`model_catalog.sync_all()`) con avance real
    registrado en `catalogo_sync_ejecucion`. Usado por LAS DOS vías (el
    endpoint manual, en segundo plano, y el ejecutor programado) -- una sola
    orquestación, nunca dos que puedan divergir (ver docstring del módulo).

    Devuelve el mismo dict que `model_catalog.sync_all()`, más
    `ejecucion_id` (`None` cuando ya había un sync corriendo -- ver
    `reservar_ejecucion`, en ese caso NO se llama a `sync_all()`)."""
    import model_catalog

    pasos_total = model_catalog.pasos_totales_de_sync()

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await limpiar_ejecuciones_viejas(cur)
        await conn.commit()
        async with conn.cursor() as cur:
            ejecucion_id, resultado_en_curso = await reservar_ejecucion(
                cur, conn, origen=origen, iniciado_por=iniciado_por, pasos_total=pasos_total)

    if ejecucion_id is None:
        return {**resultado_en_curso, "ejecucion_id": None}

    return await ejecutar_reservada(ejecucion_id, marca_nuevos=marca_nuevos)


async def ejecutar_reservada(ejecucion_id: int, *, marca_nuevos: str | None = None) -> dict:
    """La mitad de `correr_sync_registrado()` que corre DESPUÉS de reservar
    la fila -- separada para que `POST /admin/models/sync` pueda reservar
    SINCRÓNICAMENTE (para decidir 202 vs 409 antes de responder) y delegar
    esto a una `BackgroundTask` con el `ejecucion_id` ya en mano, sin
    reservar dos veces."""
    import model_catalog

    async def on_progreso(paso_actual: int, pasos_total_real: int, detalle_paso: str) -> None:
        await actualizar_progreso(ejecucion_id, paso_actual, pasos_total_real, detalle_paso)

    try:
        resultado = await model_catalog.sync_all(marca_nuevos=marca_nuevos, on_progreso=on_progreso)
    except Exception as e:  # fail-soft: un crash del sync no puede dejar la fila 'corriendo' para siempre
        motivo = redactar_secretos(f"{type(e).__name__}: {e}")
        logger.exception("catalogo_sync_registro: sync_all() reventó (ejecucion_id=%s)", ejecucion_id)
        await finalizar_ejecucion(ejecucion_id, "error", {"error": motivo})
        raise

    if resultado.get("code") == "sync_en_curso":
        # No debería pasar -- reservar_ejecucion() ya lo evita -- pero si
        # algún llamador futuro invocara model_catalog.sync_all() por fuera
        # de este registro y se cruzara con esto, la fila no queda
        # mintiendo "corriendo" para siempre.
        await finalizar_ejecucion(ejecucion_id, "error", {
            "error": "sync_all() encontró su propio candado ocupado pese a que "
                     "reservar_ejecucion() no vio nada corriendo -- carrera inesperada"})
    else:
        estado_final = "ok" if resultado.get("ok") else "con_problemas"
        await finalizar_ejecucion(ejecucion_id, estado_final, resultado)

    return {**resultado, "ejecucion_id": ejecucion_id}
