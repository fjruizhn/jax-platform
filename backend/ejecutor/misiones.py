"""Misiones del Ejecutor desde la plataforma (SP2, 2026-09-17): la vía de producto.

La plataforma NO ejecuta nada por su cuenta. Lanza, por turno, el runner del repo jax
(`python -m jax.ejecutor.mision_servicio`), que recorre el camino gobernado completo:
arranque condicionado a los seis contratos con las máquinas de la misión, vigía de C5
(`vigia_servicio`, el módulo de la unidad ejecutor-vigia@), jaula de la cuenta contra el
proxy de C3, cita literal y auditor. El runner emite eventos (una línea JSON) y un
`resultado`; la plataforma, dueña de estas tablas, los guarda: bitácora y turno.

Reglas:
- Sólo máquinas ELEGIBLES: activas y, si cargan datos de clientes, sólo si la compuerta de
  C5 (`ejecutor.c5_auditor_admite_datos_de_clientes`) está abierta O el auditor local está
  disponible de verdad (spec 2026-09-18-auditor-local-opcion.md §4: DECISIÓN DE FERNANDO --
  el auditor se elige según la máquina de la misión, ver `jax/ejecutor/contratos/
  eleccion_c5.py::elegir_auditor_faceta`). «Disponible de verdad» es
  `provider.is_local` del proveedor bindeado a `ejecutor.auditor_faceta_local`, NUNCA el
  nombre de la faceta: la compuerta no se borra, sigue gobernando el caso que de verdad
  importa -- que un auditor DE NUBE termine viendo datos de clientes porque el auditor
  local quedó mal bindeado. Ilegible = ni compuerta abierta ni auditor local disponible. La
  compuerta (y el auditor local) se revalidan en CADA turno (el arranque del runner los
  vuelve a exigir igual, vía `eleccion_c5.validar_eleccion`).
- Un turno a la vez en toda la plataforma: el registro de C3, el latido del vigía y la pausa
  son uno solo (arranque.py lo exige también: `vigia_ya_activo`).
- Con la pausa del Ejecutor puesta no se lanza nada (423).
- Una sesión del arnés por misión (`sesion_id`); el turno n>1 la retoma por id.
- Un turno `en_curso` al arrancar la plataforma quedó huérfano (systemd mata el grupo del
  servicio al reiniciar: el runner y su vigía con él) → `interrumpido`/`plataforma_reiniciada`.
- Sin textos para personas: códigos. La frase la pone el frontend.
"""
from __future__ import annotations

import asyncio
import json

import redaccion
import logging
import os
import uuid
from pathlib import Path

from db.connection import get_pool
from db.transaccion import transaccion
from ejecutor import pausa
from tiempo import iso_utc

logger = logging.getLogger(__name__)

CLAVE_COMPUERTA = "ejecutor.c5_auditor_admite_datos_de_clientes"
ESTADOS_TERMINALES = frozenset({"completado", "rechazado", "fallido"})
EVENTOS_TERMINALES = frozenset({"turno_completado", "turno_rechazado", "turno_fallido"})
ESTADO_DE_MISION = {"en_curso": "en_curso", "completado": "completada", "rechazado": "rechazada",
                    "fallido": "fallida", "interrumpido": "interrumpida"}
# Una línea del runner trae el resultado entero (salidas crudas incluidas). El tope de cada
# salida lo pone el arnés (JAX_EJECUTOR_BASH_MAX_OUTPUT_LENGTH); esto sólo acota la memoria
# de una línea para que un runner roto no la agote. Más largo = runner_salida_invalida.
LIMITE_DE_LINEA_BYTES = 32 * 1024 * 1024
LIMITE_DE_LISTA = 100

SQL_MAQUINAS = ("SELECT nombre, rol, con_datos_de_clientes, activo FROM ejecutor_host ORDER BY nombre")
SQL_COMPUERTA = "SELECT config_value FROM axioma_config WHERE config_key = %s"
# ¿El auditor local (`ejecutor.auditor_faceta_local`) está bindeado a un proveedor
# REALMENTE local? Mira `provider.is_local`, nunca el nombre de la faceta: un
# 'auditor_local' bindeado por error a un proveedor de nube no debe abrir esta puerta. Sin
# fila (config ausente, o la faceta sin binding 'primary') → NULL → False en Python, fail
# closed: la compuerta sigue siendo la única puerta.
SQL_AUDITOR_LOCAL_DISPONIBLE = (
    "SELECT p.is_local FROM axioma_config c "
    "JOIN facet_binding b ON b.facet_key = c.config_value AND b.role = 'primary' "
    "JOIN provider p ON p.id = b.provider_id "
    "WHERE c.config_key = 'ejecutor.auditor_faceta_local'"
)
SQL_TURNO_EN_CURSO = "SELECT mision_id, n FROM ejecutor_turno WHERE estado = 'en_curso' LIMIT 1"
SQL_MISION = "SELECT id, objetivo, maquinas, sesion_id, created_at, updated_at FROM ejecutor_mision WHERE id = %s"
SQL_TURNOS = ("SELECT n, instruccion, estado, codigo, resultado, sesion_iniciada, iniciado_at, terminado_at "
              "FROM ejecutor_turno WHERE mision_id = %s ORDER BY n")
SQL_BITACORA = ("SELECT id, turno, evento, datos, at FROM ejecutor_bitacora "
                "WHERE mision_id = %s AND id > %s ORDER BY id")
# Dos consultas y no un JOIN: con el JOIN el optimizador recorría ejecutor_turno ENTERO
# (medido con EXPLAIN y volumen, 2026-09-17). La lista va por idx_ejecutor_mision_actualizada
# y el último turno de cada una por uk_ejecutor_turno_mision_n.
SQL_LISTAR = ("SELECT id, objetivo, maquinas, created_at, updated_at FROM ejecutor_mision "
              "ORDER BY updated_at DESC LIMIT %s")
SQL_ULTIMO_TURNO = ("SELECT t.n, t.estado FROM ejecutor_turno t WHERE t.mision_id = %s "
                    "ORDER BY t.n DESC LIMIT 1")
SQL_BITACORA_INSERTAR = ("INSERT INTO ejecutor_bitacora (mision_id, turno, evento, datos, at) "
                         "VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(6))")
SQL_AUDITAR_PAUSA = "INSERT INTO ejecutor_pausa_audit (accion, user_id, at) VALUES (%s, %s, UTC_TIMESTAMP(6))"


class ErrorDelEjecutor(Exception):
    def __init__(self, estado: int, detalle):
        super().__init__(estado)
        self.estado, self.detalle = estado, detalle


class SinConfigurar(RuntimeError):
    """`args[0]` es la variable que falta o no vale."""


class PausaNoEscribible(RuntimeError):
    pass


class AuditoriaDePausaFallida(RuntimeError):
    pass


_lanzamiento = asyncio.Lock()
_cambio_de_pausa = asyncio.Lock()
_tareas: set[asyncio.Task] = set()


# --- configuración ----------------------------------------------------------------------------

def _runner() -> tuple[list[str], str, dict]:
    """(argv, cwd, entorno) del runner. Sin defaults: sin intérprete ejecutable o sin repo jax
    absoluto, no se lanza nada (503 `ejecutor_sin_configurar`). El entorno es el del servicio
    (/etc/jax/.env): el runner necesita las mismas JAX_EJECUTOR_* que el vigía y el proxy."""
    python = os.environ.get("JAX_EJECUTOR_PYTHON", "").strip()
    if not python or not Path(python).is_absolute() or not os.access(python, os.X_OK):
        raise SinConfigurar("JAX_EJECUTOR_PYTHON")
    repo = os.environ.get("JAX_REPO_PATH", "").strip()
    if not repo or not Path(repo).is_absolute():
        raise SinConfigurar("JAX_REPO_PATH")
    entorno = dict(os.environ)
    entorno["PYTHONPATH"] = f"{repo}:{repo}/las_manos"
    entorno["PYTHONDONTWRITEBYTECODE"] = "1"
    return [python, "-m", "jax.ejecutor.mision_servicio"], repo, entorno


def compuerta_abierta(valor) -> bool:
    """Sólo el literal `true` la abre (la migración la siembra `false`). Otra cosa, cerrada."""
    return valor == "true"


def _ruta_de_la_pausa() -> Path:
    try:
        return pausa.ruta_de_la_pausa()
    except pausa.PausaSinConfigurar as exc:
        raise ErrorDelEjecutor(503, "ejecutor_sin_configurar") from exc


# --- lectura ----------------------------------------------------------------------------------

async def _consultar(consulta: str, args=(), una=False):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(consulta, args)
            return await (cur.fetchone() if una else cur.fetchall())


async def _auditor_local_disponible() -> bool:
    """¿Hay un auditor local REALMENTE local bindeado? Ver el comentario de
    SQL_AUDITOR_LOCAL_DISPONIBLE: mira provider.is_local, nunca el nombre de la faceta."""
    fila = await _consultar(SQL_AUDITOR_LOCAL_DISPONIBLE, una=True)
    return bool(fila and fila[0])


async def maquinas() -> list[dict]:
    filas = await _consultar(SQL_MAQUINAS)
    compuerta = await _consultar(SQL_COMPUERTA, (CLAVE_COMPUERTA,), una=True)
    abierta = compuerta_abierta(compuerta[0] if compuerta else None)
    # Spec 2026-09-18-auditor-local-opcion.md §4: la compuerta deja de ser el ÚNICO camino
    # -- una máquina con datos de clientes también es elegible si el auditor que se va a
    # usar es local de verdad. La compuerta NO se borra: sigue gobernando el caso que de
    # verdad importa (auditor local mal bindeado a un proveedor de nube).
    cubierto = abierta or await _auditor_local_disponible()
    salida = []
    for nombre, rol, con_clientes, activo in filas:
        motivo = ("maquina_inactiva" if not activo
                  else "maquina_con_datos_de_clientes" if con_clientes and not cubierto else None)
        salida.append({"nombre": nombre, "rol": rol, "con_datos_de_clientes": bool(con_clientes),
                       "activo": bool(activo), "elegible": motivo is None, "motivo_no_elegible": motivo})
    return salida


async def _compuerta() -> str:
    fila = await _consultar(SQL_COMPUERTA, (CLAVE_COMPUERTA,), una=True)
    return "abierta" if compuerta_abierta(fila[0] if fila else None) else "cerrada"


async def _turno_en_curso() -> dict | None:
    fila = await _consultar(SQL_TURNO_EN_CURSO, una=True)
    return None if fila is None else {"mision_id": fila[0], "n": fila[1]}


async def estado() -> dict:
    ruta = _ruta_de_la_pausa()
    return {"pausa": await asyncio.to_thread(pausa.leer_pausa, ruta), "compuerta_datos_de_clientes": await _compuerta(),
            "auditor_local_disponible": await _auditor_local_disponible(),
            "maquinas": await maquinas(), "turno_en_curso": await _turno_en_curso()}


def _json(valor, defecto):
    if valor is None:
        return defecto
    try:
        return json.loads(valor)
    except (TypeError, ValueError):  # fail-soft: una celda ilegible se muestra vacía; el estado y el código del turno siguen a la vista
        logger.error("ejecutor: JSON ilegible en la base")
        return defecto


def _turno(fila) -> dict:
    n, instruccion, estado_t, codigo, resultado, _, iniciado, terminado = fila
    r = _json(resultado, {})
    return {"n": n, "instruccion": instruccion, "estado": estado_t, "codigo": codigo,
            "iniciado_at": iso_utc(iniciado), "terminado_at": iso_utc(terminado),
            "rechazo": r.get("rechazo", []), "afirmaciones": r.get("afirmaciones", []),
            "descartadas": r.get("descartadas", []), "crudas": r.get("crudas", []),
            "verificacion": r.get("verificacion", {})}


async def detalle(mision_id: str) -> dict:
    fila = await _consultar(SQL_MISION, (mision_id,), una=True)
    if fila is None:
        raise ErrorDelEjecutor(404, "ejecutor_mision_inexistente")
    turnos = await _consultar(SQL_TURNOS, (mision_id,))
    en_curso = await _turno_en_curso()
    ultimo = turnos[-1][2] if turnos else "fallido"
    return {"id": fila[0], "objetivo": fila[1], "maquinas": _json(fila[2], []),
            "estado": ESTADO_DE_MISION.get(ultimo, "fallida"),
            "puede_continuar": en_curso is None and any(t[5] for t in turnos),
            "created_at": iso_utc(fila[4]), "updated_at": iso_utc(fila[5]),
            "turnos": [_turno(t) for t in turnos]}


async def listar(limite: int) -> list[dict]:
    limite = max(1, min(int(limite), LIMITE_DE_LISTA))
    salida = []
    for f in await _consultar(SQL_LISTAR, (limite,)):
        ultimo = await _consultar(SQL_ULTIMO_TURNO, (f[0],), una=True)
        salida.append({"id": f[0], "objetivo": f[1], "maquinas": _json(f[2], []),
                       "estado": ESTADO_DE_MISION.get(ultimo[1] if ultimo else None, "fallida"),
                       "turnos": ultimo[0] if ultimo else 0,
                       "created_at": iso_utc(f[3]), "updated_at": iso_utc(f[4])})
    return salida


async def bitacora(mision_id: str, desde: int = 0) -> list[dict]:
    if await _consultar("SELECT 1 FROM ejecutor_mision WHERE id = %s", (mision_id,), una=True) is None:
        raise ErrorDelEjecutor(404, "ejecutor_mision_inexistente")
    return [{"id": f[0], "turno": f[1], "evento": f[2], "datos": _json(f[3], {}), "at": iso_utc(f[4])}
            for f in await _consultar(SQL_BITACORA, (mision_id, max(0, int(desde))))]


# --- lanzar -----------------------------------------------------------------------------------

async def _validar_maquinas(pedidas: list[str]) -> None:
    por_nombre = {m["nombre"]: m for m in await maquinas()}
    for nombre in pedidas:
        m = por_nombre.get(nombre)
        if m is None:
            raise ErrorDelEjecutor(422, {"codigo": "ejecutor_maquina_desconocida", "maquina": nombre})
        if not m["elegible"]:
            raise ErrorDelEjecutor(403, {"codigo": "ejecutor_maquina_no_elegible", "maquina": nombre,
                                         "motivo": m["motivo_no_elegible"]})


async def _barreras_de_lanzamiento(ruta_pausa: Path) -> None:
    if await asyncio.to_thread(pausa.pausa_puesta, ruta_pausa):
        raise ErrorDelEjecutor(423, "ejecutor_pausado")
    if await _turno_en_curso() is not None:
        raise ErrorDelEjecutor(409, "ejecutor_turno_en_curso")


def _runner_o_503():
    try:
        return _runner()
    except SinConfigurar as exc:
        logger.error("ejecutor: runner sin configurar variable=%s", exc.args[0])
        raise ErrorDelEjecutor(503, "ejecutor_sin_configurar") from exc


#: `objetivo` e `instruccion` van a columnas TEXT: el tope son 65.535 BYTES,
#: no caracteres. No es una politica inventada aca -- es lo que la base puede
#: guardar (`db/migrations.py`, `ejecutor_mision.objetivo` y
#: `ejecutor_turno.instruccion`).
LIMITE_TEXTO_BYTES = 65535


def _texto_guardable(valor, codigo_vacio: str, codigo_ilegible: str, codigo_largo: str) -> str:
    """Texto del cliente que va a una columna TEXT: lo devuelve listo o da 422.

    Sin esto el error salia del DRIVER y llegaba al cliente como 500 (auditoria
    adversarial del 2026-09-20). Dos caminos, los dos con dato del cliente:

      * mas de 65.535 bytes -> `pymysql.err.DataError (1406, "Data too long")`;
      * un surrogate solitario (el escape JSON de un medio par, que
        `json.loads` ACEPTA) ->
        `UnicodeEncodeError: surrogates not allowed` al codificar la consulta.

    Un texto que la base no puede guardar es un dato malo del cliente (422), no
    una falla del servidor (500). Se mide en bytes codificados, no en `len()`:
    un texto de 30.000 caracteres con tildes pasa de 65.535 bytes.
    """
    texto = valor.strip() if isinstance(valor, str) else ""
    if not texto:
        raise ErrorDelEjecutor(422, codigo_vacio)
    try:
        crudo = texto.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ErrorDelEjecutor(422, codigo_ilegible) from exc
    if len(crudo) > LIMITE_TEXTO_BYTES:
        raise ErrorDelEjecutor(422, codigo_largo)
    return texto


async def crear(user_id, objetivo, pedidas) -> dict:
    objetivo = _texto_guardable(objetivo, "ejecutor_objetivo_vacio",
                                "ejecutor_objetivo_ilegible", "ejecutor_objetivo_largo")
    # El orden importa: `set(pedidas)` revienta con TypeError si un elemento no es
    # hasheable (un dict, una lista), y eso salia como 500 -- un dato mal tipado del
    # cliente contado como falla del servidor. El `all(isinstance(...))` va PRIMERO,
    # asi cuando se construye el set ya se sabe que todos son str.
    if (not isinstance(pedidas, list) or not pedidas
            or not all(isinstance(m, str) and m.strip() == m and m for m in pedidas)
            or len(set(pedidas)) != len(pedidas)):
        raise ErrorDelEjecutor(422, "ejecutor_sin_maquinas")
    runner = _runner_o_503()
    ruta_pausa = _ruta_de_la_pausa()
    async with _lanzamiento:
        await _validar_maquinas(pedidas)
        await _barreras_de_lanzamiento(ruta_pausa)
        mision_id, sesion = str(uuid.uuid4()), str(uuid.uuid4())
        async with transaccion() as cur:
            await cur.execute(
                "INSERT INTO ejecutor_mision (id, user_id, objetivo, maquinas, sesion_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (mision_id, int(user_id), objetivo, json.dumps(pedidas), sesion))
            await cur.execute(
                "INSERT INTO ejecutor_turno (mision_id, n, instruccion, estado, iniciado_at) "
                "VALUES (%s, 1, %s, 'en_curso', UTC_TIMESTAMP(6))", (mision_id, objetivo))
            await cur.execute(SQL_BITACORA_INSERTAR, (mision_id, None, "mision_creada",
                                                      json.dumps({"maquinas": pedidas, "user_id": int(user_id)})))
        _lanzar(mision_id, 1, {"mision_id": mision_id, "n": 1, "sesion": sesion, "objetivo": objetivo,
                               "instruccion": objetivo, "hosts": pedidas}, runner)
    return await detalle(mision_id)


async def continuar(user_id, mision_id: str, instruccion) -> dict:
    instruccion = _texto_guardable(instruccion, "ejecutor_instruccion_vacia",
                                   "ejecutor_instruccion_ilegible", "ejecutor_instruccion_larga")
    runner = _runner_o_503()
    ruta_pausa = _ruta_de_la_pausa()
    async with _lanzamiento:
        fila = await _consultar(SQL_MISION, (mision_id,), una=True)
        if fila is None:
            raise ErrorDelEjecutor(404, "ejecutor_mision_inexistente")
        pedidas = _json(fila[2], [])
        await _validar_maquinas(pedidas)
        await _barreras_de_lanzamiento(ruta_pausa)
        turnos = await _consultar(SQL_TURNOS, (mision_id,))
        if not any(t[5] for t in turnos):
            raise ErrorDelEjecutor(409, "ejecutor_mision_sin_sesion")
        n = turnos[-1][0] + 1
        async with transaccion() as cur:
            await cur.execute(
                "INSERT INTO ejecutor_turno (mision_id, n, instruccion, estado, iniciado_at) "
                "VALUES (%s, %s, %s, 'en_curso', UTC_TIMESTAMP(6))", (mision_id, n, instruccion))
            await cur.execute("UPDATE ejecutor_mision SET updated_at = UTC_TIMESTAMP(6) WHERE id = %s", (mision_id,))
        _lanzar(mision_id, n, {"mision_id": mision_id, "n": n, "sesion": fila[3], "objetivo": fila[1],
                               "instruccion": instruccion, "hosts": pedidas}, runner)
    return await detalle(mision_id)


def _lanzar(mision_id: str, n: int, pedido: dict, runner) -> None:
    tarea = asyncio.create_task(_correr_turno(mision_id, n, pedido, runner))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)


async def _anotar(mision_id: str, turno, evento: str, datos) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_BITACORA_INSERTAR, (mision_id, turno, evento, json.dumps(datos, ensure_ascii=False)))


async def _cerrar_turno(mision_id: str, n: int, estado_t: str, codigo, resultado: dict, evento=None) -> bool:
    """Sólo cierra un turno que sigue `en_curso`: uno ya reconciliado no se pisa.

    `evento` = (nombre, datos) del cierre. Va en la MISMA transacción: si se anotara después del
    commit, quien lea en esa ventana ve la misión ya terminal con la bitácora un evento atrás
    (carrera vista en CI el 2026-09-17). El evento se escribe sólo si este llamador cerró el turno.
    """
    async with transaccion() as cur:
        await cur.execute(
            "UPDATE ejecutor_turno SET estado = %s, codigo = %s, resultado = %s, sesion_iniciada = %s, "
            "terminado_at = UTC_TIMESTAMP(6) WHERE mision_id = %s AND n = %s AND estado = 'en_curso'",
            (estado_t, codigo, json.dumps(resultado, ensure_ascii=False),
             bool(resultado.get("sesion_iniciada")), mision_id, n))
        cerrado = cur.rowcount == 1
        await cur.execute("UPDATE ejecutor_mision SET updated_at = UTC_TIMESTAMP(6) WHERE id = %s", (mision_id,))
        if cerrado and evento is not None:
            nombre, datos = evento
            await cur.execute(SQL_BITACORA_INSERTAR, (mision_id, n, nombre, json.dumps(datos, ensure_ascii=False)))
    return cerrado


def _linea(cruda: bytes):
    try:
        doc = json.loads(cruda)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("evento"), str) or not isinstance(doc.get("datos"), dict):
        return None
    return doc


#: Cuanto stderr del runner se conserva. La COLA, no la cabeza: la traza aparece DESPUES
#: de las lineas de INFO, asi que la cabeza es el ruido y la cola el diagnostico.
TOPE_STDERR_RUNNER = 8192


async def _drenar(flujo, tope: int) -> bytes:
    """Lee `flujo` hasta EOF conservando los ultimos `tope` bytes.

    No alcanza con `stderr=PIPE`: sin drenar, el pipe del sistema (~64 KB) se llena y el
    proceso se cuelga en su propio `write`. Mismo diseno que el vigia (jax#231)."""
    cola = bytearray()
    while True:
        trozo = await flujo.read(4096)
        if not trozo:
            return bytes(cola)
        cola.extend(trozo)
        if len(cola) > tope:
            del cola[:-tope]


async def _correr_turno(mision_id: str, n: int, pedido: dict, runner) -> None:
    argv, cwd, entorno = runner
    resultado, terminal_visto = None, False
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=entorno, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=LIMITE_DE_LINEA_BYTES)
        # El drenaje arranca YA. Con `stderr=PIPE` y nadie leyendo, el pipe del sistema
        # (~64 KB) se llena y el runner se cuelga en su propio `write` -- el stdout ya se
        # drena en el bucle de abajo, pero el stderr no. Ver `_drenar`.
        drenaje = asyncio.create_task(_drenar(proc.stderr, TOPE_STDERR_RUNNER))
        proc.stdin.write(json.dumps(pedido).encode())
        await proc.stdin.drain()
        proc.stdin.close()
        while True:
            try:
                cruda = await proc.stdout.readline()
            except ValueError:  # fail-soft: una línea por encima del tope; el turno se cierra fallido con código
                proc.kill()
                await _anotar(mision_id, n, "runner_salida_invalida", {"motivo": "linea_demasiado_larga"})
                break
            if not cruda:
                break
            doc = _linea(cruda)
            if doc is None:
                await _anotar(mision_id, n, "runner_salida_invalida", {})
                continue
            if doc["evento"] == "resultado":
                resultado = doc["datos"]
                continue
            terminal_visto = terminal_visto or doc["evento"] in EVENTOS_TERMINALES
            turno = doc.get("turno") if isinstance(doc.get("turno"), int) else n
            await _anotar(mision_id, turno, doc["evento"], doc["datos"])
        await proc.wait()
        err = ""
        try:
            err = redaccion.redactar_secretos((await asyncio.wait_for(drenaje, 5)).decode(errors="replace")) or ""
        except (asyncio.TimeoutError, asyncio.CancelledError):  # fail-soft: sin stderr se sigue; el resultado manda
            drenaje.cancel()
        if resultado is None:
            estado_t, codigo, resultado = "fallido", "runner_sin_cierre", {}
        elif resultado.get("estado") not in ESTADOS_TERMINALES:
            estado_t, codigo = "fallido", "runner_salida_invalida"
        else:
            estado_t, codigo = resultado["estado"], resultado.get("codigo")
        nombre = ("turno_rechazado" if estado_t == "rechazado" else
                  "turno_completado" if estado_t == "completado" else "turno_fallido")
        # El stderr SOLO cuando el turno no cerro bien: en el camino feliz es ruido, y un
        # log que siempre grita es un log que nadie lee. El 2026-09-20 un turno quedo en
        # `runner_sin_cierre` sin una sola linea para investigar.
        if codigo is not None and err.strip():
            await _anotar(mision_id, n, "runner_stderr", {"stderr": err[-2000:]})
        await _cerrar_turno(mision_id, n, estado_t, codigo, resultado,
                            None if terminal_visto else (nombre, {"codigo": codigo}))
    except Exception:  # fail-soft: la tarea de fondo no puede morir callada; el turno queda FALLIDO con código y el traceback en el journal
        logger.exception("ejecutor: el turno %s de la misión %s reventó", n, mision_id)
        try:
            await _cerrar_turno(mision_id, n, "fallido", "runner_error", {},
                                ("turno_fallido", {"codigo": "runner_error"}))
        except Exception:  # fail-soft: sin base no se puede anotar; al reiniciar, reconciliar_al_arrancar lo cierra como interrumpido
            logger.exception("ejecutor: no se pudo cerrar el turno %s de la misión %s", n, mision_id)


async def reconciliar_al_arrancar() -> int:
    """Turnos `en_curso` de un proceso anterior → `interrumpido` con su evento. Devuelve cuántos."""
    async with transaccion() as cur:
        await cur.execute("SELECT mision_id, n FROM ejecutor_turno WHERE estado = 'en_curso' FOR UPDATE")
        huerfanos = await cur.fetchall()
        for mision_id, n in huerfanos:
            await cur.execute(
                "UPDATE ejecutor_turno SET estado = 'interrumpido', codigo = 'plataforma_reiniciada', "
                "terminado_at = UTC_TIMESTAMP(6) WHERE mision_id = %s AND n = %s", (mision_id, n))
            await cur.execute(SQL_BITACORA_INSERTAR, (mision_id, n, "turno_interrumpido",
                                                      json.dumps({"codigo": "plataforma_reiniciada"})))
    if huerfanos:
        logger.warning("ejecutor: %s turno(s) en curso de un arranque anterior quedaron interrumpidos", len(huerfanos))
    return len(huerfanos)


# --- el kill switch del modo: la pausa del Ejecutor --------------------------------------------

async def poner_pausa(user_id) -> dict:
    ruta = _ruta_de_la_pausa()
    async with _cambio_de_pausa:
        antes = await asyncio.to_thread(pausa.pausa_puesta, ruta)
        try:
            cambio = await asyncio.to_thread(pausa.poner_pausa, ruta, {"origen": "plataforma", "motivo": "pausa_manual",
                                                                       "user_id": str(user_id)})
        except OSError as exc:
            # El archivo es la verdad: si quedó puesta (sólo falló el fsync), se cuenta como puesta.
            if antes or not await asyncio.to_thread(pausa.pausa_puesta, ruta):
                raise PausaNoEscribible() from exc
            cambio = True
        if cambio:
            try:
                await _auditar_pausa("poner", user_id)
            except Exception as exc:  # fail-soft: la pausa queda PUESTA (ante la duda, frenado); se informa 500 con código
                logger.exception("ejecutor: pausa puesta por user_id=%s sin auditoría", user_id)
                raise AuditoriaDePausaFallida() from exc
    return await asyncio.to_thread(pausa.leer_pausa, ruta)


async def _auditar_pausa(accion: str, user_id) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_AUDITAR_PAUSA, (accion, int(user_id)))


async def quitar_pausa(user_id) -> dict:
    ruta = _ruta_de_la_pausa()
    async with _cambio_de_pausa:
        if not await asyncio.to_thread(pausa.pausa_puesta, ruta):
            return await asyncio.to_thread(pausa.leer_pausa, ruta)
        try:
            async with transaccion() as cur:
                await cur.execute(SQL_AUDITAR_PAUSA, ("quitar", int(user_id)))
                try:
                    await asyncio.to_thread(pausa.quitar_pausa, ruta)
                except OSError as exc:
                    if await asyncio.to_thread(pausa.pausa_puesta, ruta):
                        raise PausaNoEscribible() from exc
                    # Se quitó y sólo falló el fsync del directorio: se confirma la auditoría.
        except PausaNoEscribible:
            raise
        except Exception as exc:  # fail-soft: la auditoría no quedó; si la pausa ya se había quitado se REPONE (ante la duda, frenado) y se informa 500
            logger.exception("ejecutor: quitar la pausa por user_id=%s sin auditoría", user_id)
            if not await asyncio.to_thread(pausa.pausa_puesta, ruta):
                try:
                    await asyncio.to_thread(pausa.poner_pausa, ruta, {"origen": "plataforma",
                                                                      "motivo": "repuesta_sin_auditoria"})
                except OSError:  # fail-soft: se logea; el 500 de auditoría fallida sigue su curso
                    logger.exception("ejecutor: NO se pudo reponer la pausa tras la auditoría fallida")
            raise AuditoriaDePausaFallida() from exc
    return await asyncio.to_thread(pausa.leer_pausa, ruta)
