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

FECHAS EN UTC (MINOR-6, auditoría adversarial, 2026-09-27): todas las
columnas de fecha de esta tabla (`iniciado_en`, `terminado_en`) se escriben
con `UTC_TIMESTAMP()`, nunca `NOW()` -- la sesión de MariaDB de esta app
corre en `SYSTEM` (CST, UTC-6, ver `tiempo.py`); un `NOW()` leído como si
fuera UTC y mandado al navegador saldría 6 horas corrido. Se serializan con
`tiempo.iso_utc()`, nunca `str()`.

SIN LATIDO (MAJOR-A, tercera ronda de la auditoría adversarial, 2026-09-27):
esta tabla tuvo una columna `latido_en`, estampada entre pasos de
`sync_all()`, para detectar corridas huérfanas. Se retiró por completo --
ver `marcar_huerfanas_interrumpidas()` -- porque un solo paso más lento que
el margen de detección (p.ej. Gemini paginando su catálogo) marcaba
"caída" una corrida viva. El criterio nuevo usa el candado de MariaDB que
`model_catalog.sync_all()` ya sostiene durante TODO el sync, no un latido
propio."""
from __future__ import annotations

import json
import logging

from db.connection import get_pool
from redaccion import redactar_secretos
from tiempo import iso_utc

logger = logging.getLogger("catalogo_sync_registro")

#: Candado de MariaDB para el chequeo+alta ATÓMICOS de "¿hay uno corriendo?
#: si no, reservo mi fila" -- DISTINTO del candado de trabajo real de
#: `model_catalog.sync_all()` ('jax_catalogo_sync'). Éste se toma y se
#: suelta en microsegundos, sólo durante la sección crítica de abajo, nunca
#: durante el sync en sí (que puede tardar minutos -- ver `TimeoutStartSec`
#: del .service).
_NOMBRE_CANDADO_GATE = "jax_catalogo_sync_gate"

#: MAJOR-A (tercera ronda de la auditoría adversarial, 2026-09-27): huérfana
#: = el candado de trabajo real (`model_catalog._NOMBRE_CANDADO_SYNC`) está
#: LIBRE Y ya pasó este margen desde que se reservó la fila. Reemplaza al
#: criterio de LATIDO de la ronda anterior (columna `latido_en`, estampada
#: sólo ENTRE pasos de `sync_all()`): un único paso más lento que el umbral
#: de latido -- p.ej. un proveedor paginando su catálogo -- marcaba "caída"
#: una corrida viva y sana en cualquier `GET /sync/estado` que cayera en el
#: medio. `IS_FREE_LOCK()` no tiene ese problema: la conexión dedicada de
#: `sync_all()` sostiene el candado durante TODO el sync sin importar cuánto
#: tarde un paso -- MariaDB lo libera SÓLO cuando esa conexión termina o
#: muere, garantizado, sin depender de que nadie avise nada mientras tanto.
#: El margen cubre la ventana real entre que `reservar_ejecucion()` inserta
#: la fila (bajo el candado de GATE, corto) y el momento en que `sync_all()`
#: -- recién llamado después, desde `ejecutar_reservada()` -- alcanza a
#: tomar SU candado de trabajo: en esa ventana el candado está libre de
#: verdad (todavía nadie lo tomó) aunque la corrida esté sana.
MARGEN_GRACIA_SEGUNDOS = 60

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
    orden de `_CAMPOS_EJECUCION`) en un dict JSON-serializable para la API.

    MINOR-6: `iso_utc()`, no `str()` -- `iniciado_en`/`terminado_en` se
    escriben con `UTC_TIMESTAMP()` (ver docstring del módulo); `iso_utc()`
    sabe que un `datetime` sin tzinfo que le llega YA es UTC y le pone la
    zona explícita, para que `new Date(...)` del navegador no lo lea como
    hora local."""
    d = dict(zip(_CAMPOS_EJECUCION, fila))
    for campo in ("iniciado_en", "terminado_en"):
        d[campo] = iso_utc(d[campo])
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
    """Una fila 'corriendo' cuyo candado de trabajo real está LIBRE Y ya
    pasó `MARGEN_GRACIA_SEGUNDOS` desde que se reservó es un proceso que ya
    no existe -- se marca 'error' con un resultado explícito, nunca queda
    'corriendo' para siempre. Las DOS condiciones son necesarias: el candado
    libre por sí solo no alcanza (la ventana entre reservar la fila y que
    `sync_all()` lo tome todavía no pasó, ver `MARGEN_GRACIA_SEGUNDOS`), y
    el margen vencido por sí solo tampoco (una corrida legítima y viva puede
    tardar mucho más que el margen -- lo que importa es si el candado sigue
    sostenido, no cuánto lleva corriendo).

    Filas 'corriendo' de ANTES de este cambio (con el criterio viejo de
    latido) resuelven igual, sin caso especial: si el proceso que las creó
    ya no existe, su candado de trabajo también está libre."""
    from model_catalog import _NOMBRE_CANDADO_SYNC

    resultado_huerfana = json.dumps({
        "error": (
            "interrumpida: candado de trabajo libre y más de "
            f"{MARGEN_GRACIA_SEGUNDOS}s desde iniciado_en (proceso caído)"
        )
    })
    await cur.execute(
        "UPDATE catalogo_sync_ejecucion SET estado='error', terminado_en=UTC_TIMESTAMP(), resultado=%s "
        "WHERE estado='corriendo' AND IS_FREE_LOCK(%s)=1 "
        "AND iniciado_en < UTC_TIMESTAMP() - INTERVAL %s SECOND",
        (resultado_huerfana, _NOMBRE_CANDADO_SYNC, MARGEN_GRACIA_SEGUNDOS),
    )


def _interpretar_get_lock(obtenido):
    """MINOR-5 (auditoría adversarial, 2026-09-27): mismo criterio que
    `model_catalog._interpretar_get_lock` -- NULL es un ERROR real de
    MariaDB, nunca "ocupado". Reimplementada acá (función pura, sin
    import cruzado) en vez de reusar la de `model_catalog` porque esa es
    privada de ESE módulo y este archivo no depende de `model_catalog` al
    nivel de módulo (sólo con import perezoso adentro de las funciones que
    lo necesitan, ver `correr_sync_registrado`/`ejecutar_reservada`) --
    duplicar esta función de 3 líneas es más simple que forzar ese import."""
    if obtenido is None:
        return "error"
    if obtenido == 0:
        return "ocupado"
    return "obtenido"


async def reservar_ejecucion(cur, conn, *, origen: str, iniciado_por: int | None, pasos_total: int):
    """Sección crítica -- MINOR-11 (auditoría adversarial, 2026-09-27): NO
    hay transacción acá (el pool es `autocommit=True`, ver
    `db/connection.py`; `conn.commit()` más abajo no confirma nada que no
    esté ya confirmado). La atomicidad de "¿hay uno corriendo? si no,
    reservo" viene ÍNTEGRAMENTE del candado de gate (`GET_LOCK`/
    `RELEASE_LOCK`): mientras lo tiene esta conexión, ninguna otra puede
    pasar por el mismo `SELECT`+`INSERT` a la vez -- MariaDB serializa a
    quien espera el mismo nombre de candado, no hace falta `BEGIN`.

    Devuelve `(ejecucion_id, None)` si reservó, o `(None, resultado)` si ya
    había una corriendo, o si el candado de gate no se pudo confirmar libre
    -- `resultado` tiene la MISMA forma que `model_catalog.sync_all()`
    cuando encuentra su propio candado ocupado (`code='sync_en_curso'`): el
    llamador (el endpoint, el ejecutor programado) lo trata exactamente
    igual en los dos casos.

    MINOR-5: si `GET_LOCK` devuelve NULL (error real de MariaDB, no
    "ocupado"), se levanta `RuntimeError` -- no se confunde con
    `sync_en_curso`, mismo criterio que `model_catalog.sync_all()` para su
    propio candado."""
    await cur.execute("SELECT GET_LOCK(%s, 5)", (_NOMBRE_CANDADO_GATE,))
    (obtenido,) = await cur.fetchone()
    estado_candado = _interpretar_get_lock(obtenido)
    if estado_candado == "error":
        raise RuntimeError(
            f"reservar_ejecucion: GET_LOCK('{_NOMBRE_CANDADO_GATE}') devolvió NULL -- "
            "error de MariaDB, no candado ocupado")
    if estado_candado == "ocupado":
        logger.warning("catalogo_sync_registro: candado de gate ocupado por otra alta en curso")
        return None, _resultado_sync_en_curso()
    try:
        await marcar_huerfanas_interrumpidas(cur)
        await cur.execute("SELECT id FROM catalogo_sync_ejecucion WHERE estado='corriendo' LIMIT 1")
        fila = await cur.fetchone()
        if fila is not None:
            return None, _resultado_sync_en_curso()

        await cur.execute(
            "INSERT INTO catalogo_sync_ejecucion "
            "(origen, iniciado_por, estado, paso_actual, pasos_total, iniciado_en) "
            "VALUES (%s, %s, 'corriendo', 0, %s, UTC_TIMESTAMP())",
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
    tiempo). Ya NO estampa ningún latido (MAJOR-A, tercera ronda de la
    auditoría adversarial): el criterio de huérfanas usa el candado de
    trabajo, no un latido propio -- ver `marcar_huerfanas_interrumpidas`."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE catalogo_sync_ejecucion SET paso_actual=%s, pasos_total=%s, detalle_paso=%s "
                "WHERE id=%s",
                (paso_actual, pasos_total, (detalle_paso or "")[:100], ejecucion_id),
            )
        await conn.commit()


async def finalizar_ejecucion(ejecucion_id: int, estado: str, resultado: dict | None) -> None:
    """Cierra la fila: `estado` final (ok/con_problemas/error),
    `terminado_en=UTC_TIMESTAMP()`, y el resumen -- pasado por
    `redactar_secretos` ANTES de guardarse, defensa en profundidad aunque
    los errores de proveedor que trae `resultado` ya vienen redactados
    desde `model_catalog.sync_all()`."""
    pool = await get_pool()
    resultado_crudo = json.dumps(resultado, default=str) if resultado is not None else None
    resultado_seguro = redactar_secretos(resultado_crudo) if resultado_crudo is not None else None
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE catalogo_sync_ejecucion SET estado=%s, terminado_en=UTC_TIMESTAMP(), resultado=%s "
                "WHERE id=%s",
                (estado, resultado_seguro, ejecucion_id),
            )
        await conn.commit()


async def ultima_actualizacion_exitosa(cur):
    """`iniciado_en` de la última corrida con `estado='ok'` -- cuenta
    tanto manuales como programadas (pedido de Fernando: "contando también
    las manuales"). `None` si nunca hubo ninguna exitosa.

    MINOR-1 (tercera ronda de la auditoría adversarial, 2026-09-27):
    `iniciado_en`, no `terminado_en` -- si se usara `terminado_en`, la
    propia DURACIÓN del sync se comería parte de la tolerancia de
    `catalogo_sync_config.toca_correr()` (una corrida de 9 minutos corriendo
    cada hora dejaría sólo 51 minutos reales hasta la próxima, no 60).

    MINOR-7: ignora filas 'ok' cuyo `iniciado_en` está en el FUTURO respecto
    de `UTC_TIMESTAMP()` -- un reloj/zona horaria adelantado no puede forzar
    al programador a creer que "toca correr" cada hora contra una fecha que
    todavía no llegó. Avisa (no calla) cuando excluye una fila así: es una
    situación anómala que alguien debería poder ver en los logs.
    """
    await cur.execute(
        "SELECT iniciado_en FROM catalogo_sync_ejecucion WHERE estado='ok' "
        "AND iniciado_en <= UTC_TIMESTAMP() ORDER BY iniciado_en DESC LIMIT 1"
    )
    fila = await cur.fetchone()
    resultado = fila[0] if fila else None

    await cur.execute(
        "SELECT 1 FROM catalogo_sync_ejecucion WHERE estado='ok' "
        "AND iniciado_en > UTC_TIMESTAMP() LIMIT 1"
    )
    if await cur.fetchone() is not None:
        logger.warning(
            "catalogo_sync_registro.ultima_actualizacion_exitosa: hay al menos una corrida "
            "'ok' con iniciado_en en el futuro -- se ignora para la cadencia (reloj o zona "
            "horaria movidos hacia adelante)"
        )
    return resultado


async def limpiar_ejecuciones_viejas(cur) -> None:
    """Conserva como mucho `RETENCION_FILAS` filas TERMINADAS (nunca la que
    está 'corriendo') y nada más viejo que `RETENCION_DIAS` -- ambas
    condiciones aplican ("las últimas 200 O 90 días", lo que sea más
    estricto). Se ordena por `id` (monotónico, sin empates) y no por
    `iniciado_en` (que sí puede empatar en el segundo) para que "las
    RETENCION_FILAS más recientes" sea inequívoco."""
    await cur.execute(
        "DELETE FROM catalogo_sync_ejecucion WHERE estado != 'corriendo' "
        "AND iniciado_en < UTC_TIMESTAMP() - INTERVAL %s DAY", (RETENCION_DIAS,))
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
