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
from api.admin.usage import marcar_reintento, marcar_rechazada

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

VARIABLE_MAX_INTENTOS = "JAX_USAGE_RETRY_MAX_ATTEMPTS"
#: Intentos fallidos CONSECUTIVOS antes de mandar una fila a cuarentena.
#: 3 y no 1: un lote puede fallar entero por la base -- un bloqueo, un
#: reinicio a mitad de ciclo, un `max_connections` -- y no queremos cuarentena
#: por eso. Tres ciclos son tres minutos con el intervalo por defecto.
MAX_INTENTOS_POR_DEFECTO = 3

#: Adónde va la fila que la base rechaza SIEMPRE. Es un subdirectorio propio y
#: NO `cola.SUBDIRECTORIO_CORRUPTOS`: son dos cosas distintas y la acción del
#: admin también. Un corrupto no se puede leer (un JSON roto, un archivo de una
#: versión vieja del contrato); éste se lee perfecto y la base no lo quiere --
#: hay que mirar el DATO. Mezclarlos en la misma bandeja manda al admin a
#: buscar donde no es.
SUBDIRECTORIO_RECHAZADAS = "rechazadas"
#: El motivo real de la base viaja al lado del archivo, no adentro: el
#: contenido del `.json` es el contrato con el repo jax y no se toca.
SUFIJO_MOTIVO = ".motivo.txt"

_MOTIVO_MAX = 255

#: Los códigos de error del SERVIDOR que quieren decir "el dato de ESTA fila no
#: entra". Son los únicos que cuentan contra `max_intentos()`.
#:
#: Se clasifica por CÓDIGO y no por clase de excepción a propósito: pymysql
#: mapea a `DataError` sólo los códigos que tiene en su tabla y manda todo el
#: resto de los errores del servidor a `OperationalError` -- el 1292 (una fecha
#: imposible) llega como `OperationalError`, igual que un "Lost connection".
#: Mirar la clase confundiría precisamente los dos casos que esta tarea existe
#: para distinguir.
#:
#: La lista es un ALLOWLIST y eso es fail-closed en la dirección que importa:
#: lo que no está acá se trata como transitorio y NO cuenta. Una fila venenosa
#: que sobrevive unos ciclos de más cuesta un INSERT por ciclo; una fila SANA
#: en cuarentena es un cobro que no se recupera nunca. Por eso quedan afuera
#: 1040 (too many connections), 1213 (deadlock), 1205 (lock wait), 1045/1049 y
#: 1146 (esquema a mitad de un despliegue): ninguno es culpa de la fila.
#: 1062 tampoco está: con `INSERT IGNORE` no llega, y un duplicado es ÉXITO.
_CODIGOS_DE_RECHAZO_DE_DATO = frozenset({
    1048,  # ER_BAD_NULL_ERROR -- columna NOT NULL en NULL
    1171,  # ER_PRIMARY_CANT_HAVE_NULL
    1230,  # ER_NO_DEFAULT
    1263,  # ER_WARN_NULL_TO_NOTNULL
    1264,  # ER_WARN_DATA_OUT_OF_RANGE -- un tokens_in que no entra en el INT
    1265,  # ER_WARN_DATA_TRUNCATED
    1292,  # ER_TRUNCATED_WRONG_VALUE -- una fecha imposible
    1366,  # ER_TRUNCATED_WRONG_VALUE_FOR_FIELD
    1367,  # ER_ILLEGAL_VALUE_FOR_TYPE
    1406,  # ER_DATA_TOO_LONG -- un facet de más de 30 chars
    1441,  # ER_DATETIME_FUNCTION_OVERFLOW
    1452,  # ER_NO_REFERENCED_ROW_2 -- clave foránea
    1690,  # ER_DATA_OUT_OF_RANGE
})

#: Intentos fallidos consecutivos por `spool_id`. EN MEMORIA, y es una
#: decisión, no un descuido: el formato del archivo del respaldo es el contrato
#: con el repo jax (`cola.CAMPOS`), y agregarle un contador obligaría a
#: desplegar los dos repos coordinados para algo que no lo necesita. El precio
#: de tenerlo en memoria es que un reinicio del servicio le devuelve a una fila
#: venenosa unos pocos intentos -- acotado, y a lo sumo cuesta tres INSERT más
#: por reinicio. El precio de tenerlo en el archivo sería un despliegue
#: acoplado entre jax y jax-platform, para siempre.
_intentos: dict[str, int] = {}

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


def max_intentos() -> int:
    """Intentos fallidos consecutivos antes de la cuarentena."""
    return _numero(VARIABLE_MAX_INTENTOS, MAX_INTENTOS_POR_DEFECTO, int)


def intentos() -> dict:
    """Copia del contador por `spool_id`. Para los tests y para mirar en vivo."""
    return dict(_intentos)


def reset_intentos() -> None:
    """Vuelve los contadores a cero. Para los tests y para un arranque limpio."""
    _intentos.clear()


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


# --- la fila venenosa -------------------------------------------------------
# Una fila que el INSERT rechaza SIEMPRE no se quita del respaldo y se
# reintenta en cada ciclo para siempre: ocupa lugar contra el tope, gasta un
# INSERT por ciclo y nada lo dice. Lo que sigue la manda a `rechazadas/`
# después de `max_intentos()` fallos CONSECUTIVOS.
#
# Todo depende de distinguir "falló el ciclo porque la base está caída" de
# "falló esta fila porque la base la rechaza". Con la base caída fallan TODAS
# las filas, y si el contador no supiera la diferencia una caída de más de N
# ciclos mandaría la cola entera a cuarentena: convertiría una demora
# recuperable en una pérdida definitiva, que es lo contrario de para qué
# existe el respaldo. La distinción está en DOS lugares, a propósito:
#
#   1. `la_base_rechaza_esta_fila()`, por fila, con allowlist de códigos.
#   2. el ciclo entero: si NINGUNA fila entró y hubo errores que no son de
#      dato, no se manda nada a cuarentena aunque algún código haya caído en
#      el allowlist. Es la segunda baranda contra un error mal clasificado.

def _errno_de(error) -> int | None:
    """El código de error de MySQL/MariaDB, si lo hay. Los errores del driver
    son `Error(errno, mensaje)`; los que no vienen de la base (un `OSError`,
    un `RuntimeError`) no tienen y devuelven None."""
    args = getattr(error, "args", ())
    if args and isinstance(args[0], int) and not isinstance(args[0], bool):
        return args[0]
    return None


def la_base_rechaza_esta_fila(error) -> bool:
    """True sólo si el servidor rechazó el DATO de esta fila. Sin código, o
    con un código que no está en el allowlist, es False: transitorio."""
    return _errno_de(error) in _CODIGOS_DE_RECHAZO_DE_DATO


def _rechazar(spool_id: str, motivo: str) -> None:
    """Mueve la fila a `rechazadas/` y deja el motivo al lado. Sincrónico:
    siempre detrás de `asyncio.to_thread`, como todo el I/O de `cola.py`.

    Se MUEVE, no se borra: el dato no se tira, y un admin que arregla el
    esquema puede devolver el archivo al respaldo y que entre.
    """
    if not spool_id or spool_id in (".", "..") or spool_id != os.path.basename(spool_id):
        # `spool_id` sale del NOMBRE del archivo (`cola._leer_lote`), así que
        # esto no debería pasar nunca; el guard está porque acá se arma una
        # ruta y una ruta armada sin mirar es como se sale de un directorio.
        raise ValueError(f"spool_id inválido, no se mueve: {spool_id!r}")
    directorio = cola.directorio_del_respaldo()
    destino = directorio / SUBDIRECTORIO_RECHAZADAS
    destino.mkdir(parents=True, exist_ok=True)
    nombre = f"{spool_id}{cola.SUFIJO}"
    # Primero el MOVIMIENTO y después el motivo: si se hiciera al revés y el
    # `replace` fallara, quedaría un motivo huérfano de una fila que sigue
    # pendiente. Al revés, lo peor es una fila en cuarentena sin el texto, que
    # igual tiene el `logger.error` con el motivo.
    os.replace(directorio / nombre, destino / nombre)
    (destino / f"{spool_id}{SUFIJO_MOTIVO}").write_text(motivo, encoding="utf-8")


async def _mandar_a_cuarentena(candidatas: list) -> int:
    """Mueve las que agotaron los intentos. Devuelve cuántas movió de verdad."""
    movidas = 0
    for spool_id, motivo in candidatas:
        try:
            await asyncio.to_thread(_rechazar, spool_id, motivo)
        except Exception as error:  # fail-soft: si no se puede mover (disco lleno, sólo lectura), la fila sigue pendiente y el ciclo siguiente reintenta; NO se cuenta como pérdida, que sería inventarla en el tablero
            logger.warning(
                "no se pudo mover %s a %s/: %s",
                spool_id, SUBDIRECTORIO_RECHAZADAS, texto_de_error(error))
            continue
        _intentos.pop(spool_id, None)
        movidas += 1
        # PÉRDIDA: la fila salió del respaldo y no va a entrar a la tabla. Un
        # ERROR, una sola vez -- el WARNING por intento ya avisó las anteriores.
        logger.error(
            "registro de uso RECHAZADO por la base tras %d intentos: %s (%s); "
            "se mueve a %s/ y NO se cobra",
            max_intentos(), spool_id, motivo, SUBDIRECTORIO_RECHAZADAS)
        marcar_rechazada(spool_id, motivo)
    return movidas


# --- el drenaje ------------------------------------------------------------

async def _insertar(filas: list, resumen: dict) -> tuple:
    """Inserta el lote. Devuelve `(hechas, candidatas, hubo_error_ajeno)`:

    - `hechas`: los `spool_id` que YA ESTÁN en la tabla -- los que entraron
      ahora y los que rechazó el UNIQUE. Los dos casos son éxito: en los dos,
      el archivo sobra.
    - `candidatas`: `(spool_id, motivo)` de las que agotaron `max_intentos()`
      rechazos de DATO consecutivos. Todavía no se mueven: eso lo decide
      `drenar` cuando sabe cómo le fue al ciclo entero.
    - `hubo_error_ajeno`: si falló alguna fila por algo que NO es culpa suya.
    """
    pool = await get_pool()
    hechas = []
    candidatas = []
    hubo_error_ajeno = False
    async with pool.acquire() as conn:
        for fila in filas:
            spool_id = fila["spool_id"]
            try:
                async with conn.cursor() as cur:
                    await cur.execute(INSERT, _valores(fila))
                    entro = (cur.rowcount or 0) > 0
            except Exception as error:  # fail-soft: un dato imposible en UNA fila no puede dejar sin drenar al resto del lote; esa fila NO se quita del respaldo (se reintenta) y el motivo queda en el resumen y en el log
                motivo = texto_de_error(error)[:_MOTIVO_MAX]
                resumen["fallidas"] += 1
                resumen["motivo"] = motivo
                if not la_base_rechaza_esta_fila(error):
                    # No es culpa de la fila: la conexión, un bloqueo, un
                    # permiso, el esquema a mitad de un despliegue. NO cuenta
                    # contra el máximo, y además le REINICIA el contador: los
                    # intentos son consecutivos.
                    hubo_error_ajeno = True
                    _intentos.pop(spool_id, None)
                    logger.warning(
                        "drenaje del respaldo de uso: la fila %s no entró: %s",
                        spool_id, motivo)
                    continue
                cuantos = _intentos.get(spool_id, 0) + 1
                _intentos[spool_id] = cuantos
                logger.warning(
                    "drenaje del respaldo de uso: la base rechazó la fila %s "
                    "(intento %d de %d): %s",
                    spool_id, cuantos, max_intentos(), motivo)
                if cuantos >= max_intentos():
                    candidatas.append((spool_id, motivo))
                continue
            hechas.append(spool_id)
            _intentos.pop(spool_id, None)  # entró: el contador vuelve a cero
            if entro:
                resumen["insertadas"] += 1
            else:
                resumen["duplicadas"] += 1
        await conn.commit()
    return hechas, candidatas, hubo_error_ajeno


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
        "quitadas": 0, "fallidas": 0, "rechazadas": 0, "motivo": None,
    }
    filas = []
    try:
        filas = await cola.leer_pendientes(limite or lote())
        resumen["leidas"] = len(filas)
        if filas:
            hechas, candidatas, ajeno = await _insertar(filas, resumen)
            if hechas:
                resumen["quitadas"] = await cola.quitar(hechas)
            # La SEGUNDA baranda contra "toda la cola a cuarentena". Un ciclo
            # donde no entró NADA y hubo errores que no son de dato tiene la
            # forma de una caída: la conexión se murió a mitad del lote y
            # explotó fila por fila. En un ciclo así no se manda nada a
            # cuarentena aunque algún código haya caído en el allowlist, y los
            # contadores se borran enteros -- si la base está caída, TODAS
            # fallan y NINGUNA es venenosa.
            if ajeno and not (resumen["insertadas"] + resumen["duplicadas"]):
                _intentos.clear()
            elif candidatas:
                resumen["rechazadas"] = await _mandar_a_cuarentena(candidatas)
        # Poda, y FUERA del `if filas` a propósito: un contador sólo existe
        # para una fila que falló, y una fila que falló sigue pendiente y es de
        # las más viejas, así que vuelve a entrar al lote del ciclo siguiente.
        # La que NO vuelve -- porque el tope la descartó por vieja
        # (`cola._hacer_lugar`) o porque otro proceso la drenó -- dejaría su
        # entrada colgada para siempre, y el caso más común de eso es
        # justamente el ciclo con la cola YA VACÍA. Acotado a lo leído en este
        # ciclo, el dict no crece en un servicio que no se reinicia nunca.
        leidos = {f["spool_id"] for f in filas}
        for spool_id in [k for k in _intentos if k not in leidos]:
            del _intentos[spool_id]
    except Exception as error:  # fail-soft: tarea de fondo -- propagar mataría el loop y el respaldo dejaría de drenarse; nada se borra, el motivo queda en el resumen y el ERROR en el log, y el ciclo siguiente reintenta
        resumen["motivo"] = texto_de_error(error)[:_MOTIVO_MAX]
        logger.error("drenaje del respaldo de uso: el ciclo falló: %s", resumen["motivo"])
        # El ciclo falló entero (el pool caído, el respaldo ilegible): ninguna
        # fila tuvo su oportunidad, así que ningún intento cuenta.
        _intentos.clear()
    finally:
        # Marca de VIDA, no de éxito: un ciclo que corrió y no pudo insertar
        # sigue siendo un drenaje vivo. Sin esto, "hay 5 pendientes" no
        # distingue una cola que avanza de un reintento muerto.
        marcar_reintento()
    if resumen["leidas"]:
        logger.info(
            "drenaje del respaldo de uso: leidas=%d insertadas=%d duplicadas=%d "
            "quitadas=%d fallidas=%d rechazadas=%d",
            resumen["leidas"], resumen["insertadas"], resumen["duplicadas"],
            resumen["quitadas"], resumen["fallidas"], resumen["rechazadas"])
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
