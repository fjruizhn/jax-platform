"""El drenaje del respaldo de uso (plan 2026-09-15-cola-durable-uso, Task 3).

`uso/cola.py` deposita; esto es lo único que inserta. Tres procesos dejan filas
en el mismo directorio (`jax-platform`, `jacobs` y `motor_registry`) y uno solo
drena: la plataforma, que es la dueña de `axioma_usage` y la única con
migraciones.

Lo que hace que esto sea seguro de repetir es `spool_id` + `INSERT IGNORE`
contra el UNIQUE `uniq_axioma_usage_spool_id` (Task 2): si el proceso se muere
entre el INSERT y el borrado del archivo, el ciclo siguiente reinserta la misma
fila y la base la rechaza sola. **Un duplicado es un ÉXITO**, no un error: la
fila ya está cobrada, y el archivo se borra igual -- si no, se quedaría dando
vueltas para siempre, drenando y fallando en cada ciclo.

Dos decisiones que no son de estilo:

- **`created_at` se inserta EXPLÍCITO**, con la hora que guardó el escritor, y
  nunca se deja al DEFAULT de la columna (Ruling C-4). Ésa es la hora del
  TURNO: si la pusiera el reintento, una caída de dos horas movería el costo al
  día siguiente y el informe por período mentiría. Viaja como epoch por
  `FROM_UNIXTIME()` para que la conversión a la zona de la sesión de MariaDB la
  haga la base y no un `str()` de este lado.
- **Una fila que el INSERT rechaza no frena al resto del lote.** Cada fila va
  en su propio cursor: un dato imposible en una fila no puede dejar sin
  recuperar las otras diez mil.

`origen` NO se inserta: identifica al proceso que depositó, no al gasto, y
`axioma_usage` no tiene esa columna. Son las trece del archivo menos ésa.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from db.connection import get_pool
from redaccion import texto_de_error
from uso import cola
# marcar_reintento vive en api/admin/usage.py y no acá porque cola.py/reintento
# se leen junto con el contrato copiado al repo jax, y jax NO drena. El dato es
# de la plataforma.
from api.admin.usage import marcar_reintento

LOGGER = "uso.reintento"
logger = logging.getLogger(LOGGER)

# --- configuración (sin hardcoding) ----------------------------------------
VARIABLE_INTERVALO = "JAX_USAGE_RETRY_INTERVAL_SECONDS"
INTERVALO_POR_DEFECTO = 60
VARIABLE_LOTE = "JAX_USAGE_RETRY_BATCH_SIZE"
#: Cota del lote por ciclo: un respaldo de 50.000 filas (el tope de cola.py) no
#: puede convertirse en una transacción de 50.000 INSERT que bloquee la tabla
#: que usan chat e image en el camino del usuario. Lo que sobra espera al ciclo
#: siguiente, que llega en `intervalo()` segundos.
#: 500 y no 200: medido bajo carga el 2026-09-15 (Task 5 del plan), 500 drena
#: 835 filas/s contra 747 y deja el p99 del turno del usuario en 1,60 ms contra
#: 4,68 -- mejor en los dos ejes a la vez, porque menos ciclos son menos
#: recorridos del respaldo compitiendo con el turno. No es "más es mejor": con
#: 1000+ se vuelve inestable y el p99 salta a 23 ms. DECISIÓN de Fernando
#: (2026-09-15) tras el NO-GO de la prueba de carga.
LOTE_POR_DEFECTO = 500

_MOTIVO_MAX = 255

#: Las doce columnas del INSERT, en el orden de `cola.CAMPOS` menos `origen`.
COLUMNAS = (
    "spool_id", "created_at", "tenant_id", "user_id", "facet", "model",
    "tokens_in", "tokens_out", "cost_usd", "request_type", "status", "job_id",
)
_MARCAS = ", ".join(
    "FROM_UNIXTIME(%s)" if c == "created_at" else "%s" for c in COLUMNAS)
INSERT = (
    f"INSERT IGNORE INTO axioma_usage ({', '.join(COLUMNAS)}) "
    f"VALUES ({_MARCAS})"
)


def _numero(variable: str, por_defecto, convertir):
    crudo = os.environ.get(variable)
    if not crudo:
        return por_defecto
    try:
        valor = convertir(crudo)
    except (TypeError, ValueError):
        # fail-soft acotado: un valor mal escrito en el entorno no puede apagar
        # el drenaje en silencio -- se usa el default y queda el aviso.
        logger.warning("%s no es un número (%r); se usa %s", variable, crudo, por_defecto)
        return por_defecto
    return valor if valor > 0 else por_defecto


def intervalo() -> float:
    """Segundos entre ciclos."""
    return _numero(VARIABLE_INTERVALO, INTERVALO_POR_DEFECTO, float)


def lote() -> int:
    """Filas por ciclo."""
    return _numero(VARIABLE_LOTE, LOTE_POR_DEFECTO, int)


# --- conversión de la fila del archivo a la fila de la tabla ----------------

def _entero(valor):
    """`tenant_id`/`user_id` se guardan en el archivo tal como los recibió el
    escritor (strings, en el caso de la plataforma) y la tabla los quiere INT.
    Está dicho en el docstring de `record_usage`: es parte del contrato con
    jax. Un valor no numérico entra como NULL -- "no se sabe" -- y no rompe el
    drenaje de las demás filas."""
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _epoch(valor):
    """El instante del turno en segundos desde epoch, para `FROM_UNIXTIME`.

    Una hora sin zona se interpreta como UTC: los tres escritores del contrato
    la escriben con `datetime.now(timezone.utc).isoformat()`. Si no se puede
    leer, devuelve None y la columna cae a `CURRENT_TIMESTAMP` -- la hora del
    reintento es peor que la del turno, pero mucho mejor que perder la fila.
    """
    if not isinstance(valor, str):
        return None
    try:
        cuando = datetime.fromisoformat(valor)
    except ValueError:
        logger.warning("drenaje: created_at ilegible (%r); queda la hora del reintento", valor)
        return None
    if cuando.tzinfo is None:
        cuando = cuando.replace(tzinfo=timezone.utc)
    return cuando.timestamp()


def _valores(fila: dict) -> tuple:
    return (
        fila["spool_id"],
        _epoch(fila.get("created_at")),
        _entero(fila.get("tenant_id")),
        _entero(fila.get("user_id")),
        fila.get("facet"),
        fila.get("model"),
        fila.get("tokens_in"),
        fila.get("tokens_out"),
        fila.get("cost_usd"),
        fila.get("request_type"),
        fila.get("status"),
        fila.get("job_id"),
    )


# --- el drenaje ------------------------------------------------------------

async def _insertar(filas: list, resumen: dict) -> list:
    """Inserta el lote y devuelve los `spool_id` que YA ESTÁN en la tabla --
    los que entraron ahora y los que la rechazó el UNIQUE. Los dos casos son
    éxito: en los dos, el archivo sobra."""
    pool = await get_pool()
    hechas = []
    async with pool.acquire() as conn:
        for fila in filas:
            try:
                async with conn.cursor() as cur:
                    await cur.execute(INSERT, _valores(fila))
                    entro = (cur.rowcount or 0) > 0
            except Exception as error:  # fail-soft: un dato imposible en UNA fila no puede dejar sin drenar al resto del lote; esa fila NO se quita del respaldo (se reintenta) y el motivo queda en el resumen y en el log
                resumen["fallidas"] += 1
                resumen["motivo"] = texto_de_error(error)[:_MOTIVO_MAX]
                logger.warning(
                    "drenaje del respaldo de uso: la fila %s no entró: %s",
                    fila.get("spool_id"), resumen["motivo"])
                continue
            hechas.append(fila["spool_id"])
            if entro:
                resumen["insertadas"] += 1
            else:
                resumen["duplicadas"] += 1
        await conn.commit()
    return hechas


async def drenar(limite: int | None = None) -> dict:
    """Un ciclo: lee lo pendiente, lo inserta y borra del respaldo lo que ya
    está en la tabla. NUNCA propaga -- corre en una tarea de fondo, y una
    excepción que salga mata el loop y deja el respaldo sin drenar hasta el
    próximo reinicio.

    Con la base caída no borra NADA y devuelve el motivo: las filas siguen en
    disco y el ciclo siguiente las recupera.

    `cola.leer_pendientes` propaga `OSError` a propósito (una cola ilegible no
    puede quedar muda); el que lo envuelve es este `try`, y lo hace RUIDOSO
    -- sin un ERROR en el log, una cola que no se puede leer crece invisible.
    """
    resumen = {
        "leidas": 0, "insertadas": 0, "duplicadas": 0,
        "quitadas": 0, "fallidas": 0, "motivo": None,
    }
    try:
        filas = await cola.leer_pendientes(limite or lote())
        resumen["leidas"] = len(filas)
        if filas:
            hechas = await _insertar(filas, resumen)
            if hechas:
                resumen["quitadas"] = await cola.quitar(hechas)
    except Exception as error:  # fail-soft: tarea de fondo -- propagar mataría el loop y el respaldo dejaría de drenarse; nada se borra, el motivo queda en el resumen y el ERROR en el log, y el ciclo siguiente reintenta
        resumen["motivo"] = texto_de_error(error)[:_MOTIVO_MAX]
        logger.error("drenaje del respaldo de uso: el ciclo falló: %s", resumen["motivo"])
    finally:
        # Marca de VIDA, no de éxito: un ciclo que corrió y no pudo insertar
        # sigue siendo un drenaje vivo. Sin esto, "hay 5 pendientes" no
        # distingue una cola que avanza de un reintento muerto.
        marcar_reintento()
    if resumen["leidas"]:
        logger.info(
            "drenaje del respaldo de uso: leidas=%d insertadas=%d duplicadas=%d "
            "quitadas=%d fallidas=%d",
            resumen["leidas"], resumen["insertadas"], resumen["duplicadas"],
            resumen["quitadas"], resumen["fallidas"])
    return resumen


def _corriendo_bajo_pytest() -> bool:
    import sys
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


async def start_reintento_de_uso(forzado: bool = False) -> None:
    """El loop de fondo. Mismo patrón que `start_owner_file_cleanup`.

    **Drena ANTES del primer sleep**: un reinicio después de una caída tiene
    que recuperar enseguida, no al minuto.

    No arranca bajo pytest, igual que `start_facet_canary`: el fixture `client`
    levanta el lifespan de la app entero, y un loop de fondo insertando en
    `jax_memory_test` mientras corren los tests es ruido no determinista.
    `forzado=True` es sólo para el test que ejercita el loop.
    """
    if not forzado and _corriendo_bajo_pytest():
        logger.warning("reintento de uso: no arranca bajo pytest")
        return
    while True:
        try:
            await drenar()
        except Exception:  # fail-soft: loop en background, mismo patrón que owner_cleanup.py -- `drenar` promete no propagar, pero si rompiera la promesa el loop no puede morir; el próximo ciclo reintenta
            logger.warning("reintento de uso: el ciclo falló", exc_info=True)
        await asyncio.sleep(intervalo())
