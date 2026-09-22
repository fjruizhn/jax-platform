"""Pantalla de Memoria — API de lectura, aprobación, corrección, caducidad y
agrupamiento por tema de `facts` (jax_memory). Plan:
docs/superpowers/plans/2026-09-20-memoria-admin.md (Task 3, 4 y 5). Spec:
docs/superpowers/specs/2026-09-18-memoria-admin-design.md.

«La memoria sin procedencia es otra forma de suposición» (Protocolo de la
Memoria Viva) -- cada hecho devuelve su procedencia SIEMPRE, aunque sea None.

No hay aprobación automática (spec §2.2) ni endpoint de borrado (spec §5):
un hecho falso no se borra en silencio, se marca como corregido.

Dos accesos a datos, a propósito:
  - LECTURA (`SQL_LISTAR`/`SQL_CONTAR`): SQL propio contra `db.connection.get_pool()`
    (mismo patrón que config_admin.py), no `MemoryDB.get_facts()` -- ese método
    arma la consulta en Python con un ORDER BY (`importance`) que no coincide
    con `idx_facts_revision`, así que no hay una constante de módulo que
    reproduzca "la consulta real" tal como pide el test del EXPLAIN
    (test_memoria_indices.py). Con SQL propio, la que se ve acá ES la que
    corre.
  - ESCRITURA de un solo UPDATE (aprobar, caducar): se reusa `MemoryDB`
    (helper de api/chat.py, sin duplicar el sys.path.insert -- corrección de
    Fernando 2026-09-20) porque ya tiene `verify_fact`/`expire_fact`.
  - Corregir es la excepción: crea un fact Y supera al viejo. Antecedente
    real, jax-platform#107 (un evento fuera de la transacción del turno fue
    una carrera) -- por eso NO se compone `MemoryDB.save_fact` +
    `MemoryDB.supersede_fact` (cada uno abre y confirma su propia conexión):
    van los dos SQL a mano dentro de UNA transacción de
    `db/transaccion.py` (mismo pool que config_admin.py, misma base física
    que MemoryDB -- `jax_memory`/`jax_memory_test`).

Task 5 agrega un tercer import de `jax.memory`, y a propósito NO delega el
`sys.path.insert(JAX_REPO_PATH)` en el side-effect de `api/chat.py` (como
hacía la primera versión de este archivo): `api/admin/__init__.py` importa
`.memoria` DESDE DENTRO del propio import de `api.chat` (chat.py línea ~53
importa `api.admin.usage`, que fuerza a ejecutar `api/admin/__init__.py`
primero, que importa `.memoria`) -- en ese momento `api.chat` está a MEDIAS
importado (el `sys.path.insert` real vive más abajo, línea ~109) y
`from jax.memory...` revienta con `ModuleNotFoundError: No module named
'jax'`. Reproducido corriendo la suite COMPLETA (rompía sólo ahí, nunca
corriendo este archivo de tests solo -- por eso hay que medirlo con la
suite entera, no con el archivo suelto). Por eso acá se fija el mismo
`sys.path` de forma DEFENSIVA e IDEMPOTENTE, con el mismo helper que usa
`api/chat.py`, antes de tocar `jax.memory`.
"""
import asyncio
import json
import logging
import math
import sys
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api import chat as _chat_mod
from auth.middleware import require_superadmin
from auth.models import AuthUser
from config_entorno import ruta_absoluta_requerida
from db.connection import get_pool
from db.transaccion import AISLAMIENTO_ADMIN, transaccion

_JAX_REPO = str(ruta_absoluta_requerida("JAX_REPO_PATH"))
if _JAX_REPO not in sys.path:
    sys.path.insert(0, _JAX_REPO)

# jax.memory.db reutiliza, sin duplicar: la banda de distancia YA calibrada
# ("candidato a correccion" = "misma afirmacion, otras palabras", medido en
# jax/memory/db.py) y la columna de embedding configurable (sin hardcoding,
# spec 2026-09-12 bge-m3 -- JAX_MEMORY_EMBED_COLUMN).
from jax.memory.db import CORRECTION_DISTANCE_THRESHOLD as _UMBRAL_MISMO_TEMA  # noqa: E402
from jax.memory.db import _col as _columna_embedding  # noqa: E402
from jax.memory.db import _nonzero_embedding_sql as _embedding_no_cero_sql  # noqa: E402
from jax.memory.embedding_config import CONFIG as _EMBED_CFG  # noqa: E402

router = APIRouter(prefix="/api/admin/memoria")

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Listado
# --------------------------------------------------------------------------
# `(%s IS NULL OR is_verified = %s)`: aiomysql/pymysql sustituyen los `%s` en
# el CLIENTE antes de mandar el texto al servidor (no son prepared statements
# de verdad) -- por eso, cuando `verificado` es False/True, MariaDB VE la
# constante literal (`0 IS NULL OR is_verified = 0`) y la pliega en tiempo de
# planificación, usando `idx_facts_revision` sin filesort. Medido a mano
# contra jax_memory_test el 2026-09-20 (las tres formas: con filtro, sin
# filtro, y con IN(0,1) -- esta última NO sirve: MariaDB no la reduce a un
# rango indexado y cae a filesort). Con `verificado=None` (listado sin
# filtrar, "todo") el predicado se pliega a TRUE y esa consulta puntual SÍ
# hace filesort -- es una limitación real del índice ya fijado en la Task 1
# (is_verified como primera columna), no un descuido de esta consulta; con
# el volumen actual (116 hechos) es inofensivo. `SQL_LISTAR`/`ARGS_EJEMPLO`
# protegen el camino caliente de verdad: la cola de revisión
# (`verificado=False`, spec §1 -- "115 hechos esperan revisión").
SQL_LISTAR = (
    "SELECT id, fact_text, fact_type, confidence, is_verified, verified_by, "
    "verified_at, expires_at, "
    "(expires_at IS NOT NULL AND expires_at <= NOW()) AS vencido, "
    "created_at, source_message_id, source_facet, superseded_by "
    "FROM facts "
    "WHERE (%s IS NULL OR is_verified = %s) "
    "AND (%s = 1 OR superseded_by IS NULL) "
    "AND (%s = 1 OR expires_at IS NULL OR expires_at > NOW()) "
    "ORDER BY expires_at DESC, created_at DESC "
    "LIMIT %s"
)
# verificado=False, incluir_superados=False, incluir_vencidos=False, limite=20:
# la cola de revisión por defecto. test_memoria_indices.py corre EXPLAIN
# sobre ESTA tupla exacta.
ARGS_EJEMPLO = (False, False, False, False, 20)

SQL_CONTAR = (
    "SELECT COUNT(*) FROM facts "
    "WHERE (%s IS NULL OR is_verified = %s) "
    "AND (%s = 1 OR superseded_by IS NULL) "
    "AND (%s = 1 OR expires_at IS NULL OR expires_at > NOW())"
)

_COLUMNAS_LISTAR = (
    "id", "fact_text", "fact_type", "confidence", "is_verified", "verified_by",
    "verified_at", "expires_at", "vencido", "created_at", "source_message_id",
    "source_facet", "superseded_by",
)


def _iso(momento) -> Optional[str]:
    return momento.isoformat() if momento is not None else None


def _hecho_de_fila(fila) -> dict:
    d = dict(zip(_COLUMNAS_LISTAR, fila))
    return {
        "id": d["id"],
        "texto": d["fact_text"],
        "tipo": d["fact_type"],
        "confianza": d["confidence"],
        "verificado": bool(d["is_verified"]),
        "verificado_por": d["verified_by"],
        "verificado_at": _iso(d["verified_at"]),
        "vence_at": _iso(d["expires_at"]),
        "vencido": bool(d["vencido"]),
        "creado_at": _iso(d["created_at"]),
        "superado_por": d["superseded_by"],
        "procedencia": {
            "mensaje_id": d["source_message_id"],
            "faceta": d["source_facet"],
        },
    }


def _args_filtro(verificado, incluir_superados, incluir_vencidos):
    return (verificado, verificado, incluir_superados, incluir_vencidos)


@router.get("/hechos")
async def listar_hechos(
    verificado: Optional[bool] = Query(default=None),
    incluir_vencidos: bool = Query(default=False),
    incluir_superados: bool = Query(default=False),
    limite: int = Query(default=20, ge=1, le=500),
    user: AuthUser = Depends(require_superadmin),
):
    base = _args_filtro(verificado, incluir_superados, incluir_vencidos)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_LISTAR, (*base, limite))
            filas = await cur.fetchall()
            await cur.execute(SQL_CONTAR, base)
            total = (await cur.fetchone())[0]
    return {"hechos": [_hecho_de_fila(f) for f in filas], "total": total}


# --------------------------------------------------------------------------
# Escritura -- el que produce no aprueba (spec §2.2): `verified_by` /
# `superseded_by_user` SIEMPRE es el `user_id` de `require_superadmin`, nunca
# un valor implícito. No hay aprobación automática: ningún camino de este
# módulo pone `is_verified = TRUE` sin ese `user_id` real.
# --------------------------------------------------------------------------
async def _memoria_conectada():
    """Reusa el MemoryDB compartido de api/chat.py (mismo pool que el chat,
    misma jax_memory) -- sin un sys.path.insert nuevo (corrección de
    Fernando, 2026-09-20)."""
    if not await _chat_mod._ensure_memory():
        raise HTTPException(status_code=503, detail="memoria_no_disponible")
    return _chat_mod._memory


class AprobarBody(BaseModel):
    ids: list[int]


@router.post("/hechos/aprobar")
async def aprobar_hechos(body: AprobarBody, user: AuthUser = Depends(require_superadmin)):
    if not body.ids:
        return {"aprobados": 0}
    memoria = await _memoria_conectada()
    autor = int(user.user_id)
    aprobados = 0
    for fact_id in body.ids:
        ok = await memoria.verify_fact(fact_id, autor)
        # M1 (auditoria adversarial 2026-09-20): verify_fact devuelve None
        # cuando la base no respondio (contrato de tres estados, jax/memory/
        # db.py), no cuando el id no existe. Antes esto sumaba 0 en silencio
        # y el lote terminaba en 200 {"aprobados": N} de MENOS, sin que el
        # superadmin tuviera forma de saber que el resto del lote NUNCA se
        # intento -- fail-closed: se corta el lote y se avisa.
        if ok is None:
            raise HTTPException(status_code=503, detail="memoria_no_disponible")
        if ok:
            aprobados += 1
    return {"aprobados": aprobados}


class CorregirBody(BaseModel):
    texto: str


@router.post("/hechos/{fact_id}/corregir")
async def corregir_hecho(fact_id: int, body: CorregirBody,
                         user: AuthUser = Depends(require_superadmin)):
    texto = body.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="texto_vacio")
    memoria = await _memoria_conectada()
    # El embedding se calcula ANTES de abrir la transacción (llamada HTTP a
    # Ollama, spec §4 async: nada bloqueante -- ni de más, ni adentro de un
    # BEGIN sosteniendo un lock). Si falla, la fila queda con el vector cero
    # por defecto y la repara backfill_zero_embeddings() (jax/memory/db.py),
    # igual que cualquier otro fact: no es un caso especial de esta pantalla.
    embedding = await memoria.get_embedding(texto)
    autor = int(user.user_id)

    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        await cur.execute(
            "SELECT fact_type, confidence, user_id, project_id, importance, "
            "superseded_by FROM facts WHERE id = %s FOR UPDATE",
            (fact_id,),
        )
        vieja = await cur.fetchone()
        if vieja is None:
            raise HTTPException(status_code=404, detail="hecho_no_encontrado")
        fact_type, confidence, user_id, project_id, importance, superseded_by = vieja
        if superseded_by is not None:
            raise HTTPException(status_code=409, detail="hecho_ya_superado")

        if embedding:
            await cur.execute(
                "INSERT INTO facts (fact_uuid, fact_text, fact_type, confidence, "
                "is_verified, user_id, project_id, importance, embedding_bge_m3) "
                "VALUES (UUID(), %s, %s, %s, FALSE, %s, %s, %s, VEC_FromText(%s))",
                (texto, fact_type, confidence, user_id, project_id, importance,
                 json.dumps(embedding)),
            )
        else:
            await cur.execute(
                "INSERT INTO facts (fact_uuid, fact_text, fact_type, confidence, "
                "is_verified, user_id, project_id, importance) "
                "VALUES (UUID(), %s, %s, %s, FALSE, %s, %s, %s)",
                (texto, fact_type, confidence, user_id, project_id, importance),
            )
        nuevo_id = cur.lastrowid

        # Misma transacción que el INSERT de arriba: las dos escrituras
        # confirman juntas o ninguna (jax-platform#107).
        await cur.execute(
            "UPDATE facts SET superseded_by = %s, superseded_at = NOW(), "
            "superseded_by_user = %s WHERE id = %s",
            (nuevo_id, autor, fact_id),
        )
    return {"nuevo_id": nuevo_id}


class FundirBody(BaseModel):
    superviviente_id: int
    absorbidos: list[int]


async def _aprobar_en_cursor(cur, autor: int, fact_id: int) -> None:
    """Mismo SQL que `MemoryDB.verify_fact` (jax/memory/db.py), sobre ESTE
    cursor -- ver el docstring de `fundir_hechos` para el porque de no
    componer el metodo (pool/conexion distintos, jax-platform#107)."""
    await cur.execute(
        "UPDATE facts SET is_verified = TRUE, verified_at = NOW(), "
        "verified_by = %s WHERE id = %s",
        (autor, fact_id),
    )


async def _superar_en_cursor(cur, autor: int, absorbido_id: int, superviviente_id: int) -> None:
    """Mismo SQL que `MemoryDB.supersede_fact`, sobre ESTE cursor. Funcion
    propia (no una linea inline en el bucle de `fundir_hechos`) para que un
    test pueda monkeypatchear UN absorbido y demostrar la atomicidad de la
    transaccion completa (ronda 2026-09-22, test_memoria_fundir.py)."""
    await cur.execute(
        "UPDATE facts SET superseded_by = %s, superseded_at = NOW(), "
        "superseded_by_user = %s WHERE id = %s",
        (superviviente_id, autor, absorbido_id),
    )


@router.post("/hechos/fundir")
async def fundir_hechos(body: FundirBody, user: AuthUser = Depends(require_superadmin)):
    """Fundir casi-duplicados es SUPERSEDER, no caducar (decision de
    Fernando, 2026-09-20): caducar dice "esto dejo de valer"; superar dice
    "esto fue reemplazado POR AQUELLO". Tres hechos que dicen lo mismo no son
    tres hechos vencidos -- son uno con tres redacciones, y `superseded_by`
    reconstruye esa cadena.

    Mismo par de columnas que `MemoryDB.supersede_fact(old_fact_id,
    new_fact_id, superseded_by_user)` (jax/memory/db.py -- esa aridad esta
    pensada justo para este llamador humano, ver su propio docstring: "el
    humano (la pantalla de Memoria)"). A proposito NO se compone el metodo:
    abre y confirma SU PROPIA conexion (`self.pool.acquire()` sobre el pool
    de MemoryDB), un pool DISTINTO al de esta transaccion (`db/transaccion.py`,
    sobre `db.connection.get_pool()`) aunque los dos apunten a la misma base
    fisica -- exactamente la misma razon por la que `corregir_hecho` de
    arriba tampoco compone `MemoryDB.save_fact`/`supersede_fact`
    (jax-platform#107: cada uno abre y confirma su propia conexion). Llamar a
    `supersede_fact` una vez por absorbido dejaria "fundir a medias" posible
    si algo fallara a mitad del lote -- lo que este endpoint existe para
    evitar. Por eso van los UPDATE a mano, con el MISMO SQL que
    `supersede_fact` ejecuta, sobre el MISMO cursor, dentro de la MISMA
    transaccion con `FOR UPDATE` (mismo patron que `corregir_hecho`).

    Ronda 2026-09-22 (hallazgo de Fernando): "fundir en el mas reciente"
    podia aprobar una SINTESIS (con partes inventadas por el sintetizador) y
    con eso SUPERAR a un hecho YA verificado. Si el superviviente todavia no
    estaba verificado, se aprueba EN esta misma transaccion -- mismo efecto
    que `/hechos/aprobar`, sin la segunda llamada HTTP que el frontend hacia
    antes (dos llamadas separadas dejaban una ventana real: si la segunda
    fallaba, el hecho quedaba aprobado sin fundir).

    Ronda 146 (revision adversarial de jax-platform PR 146, D1/D3, decision de
    Fernando): el endpoint EXIGE la regla, no solo la propone -- antes un
    cliente (a mano, o un bug del frontend) podia mandar CUALQUIER
    `superviviente_id`, y lo unico que se validaba era que no hubiera
    verificados perdiendo su verificacion. Ahora:
      - el lote entero (superviviente + absorbidos) tiene que ser
        compatible PAR A PAR -- mismo `source_facet` (sintesis con sintesis,
        no-sintesis con no-sintesis) y sin citas cruzadas en
        `source_fact_ids` -- o 409 `fundir_sintesis_con_no_sintesis`. Mismo
        criterio que usa el detector (`_compatibles_para_fundir`, D1): la
        API no permite a mano lo que el detector ya no propone.
      - el `superviviente_id` tiene que ser EXACTAMENTE el que calcula
        `_elegir_superviviente` sobre ese mismo lote (el verificado mas
        reciente; sin ninguno verificado, el mas reciente a secas; empate
        de fecha lo desempata el id mayor -- D4), o 409
        `superviviente_no_es_el_de_la_regla`. Esto DEJA REDUNDANTE al viejo
        409 `superviviente_no_verificado` de la ronda anterior: si el lote
        tiene algun verificado, `_elegir_superviviente` SIEMPRE elige uno
        verificado, asi que un `superviviente_id` sin verificar que coincida
        con la regla implica que NINGUN miembro del lote esta verificado --
        el viejo codigo de error ya no es alcanzable, y se elimino (no se
        dejo como código muerto).

    Tercera vuelta (revision adversarial de jax-platform PR 146): dos
    hallazgos mas.
      - MAJOR 1: la compatibilidad de citas ahora usa el CIERRE TRANSITIVO
        del grafo completo de `source_fact_ids` (`_cierre_transitivo_de_
        citas`), el MISMO que usa el detector -- no solo la cita directa de
        CADA fila del lote. Una cita indirecta (A cita a B, B cita a C) deja
        a A y C incompatibles igual, aunque nunca se hayan citado
        directamente.
      - MAJOR 2: un hecho vencido (`expires_at` en el pasado) no puede
        fundirse -- ni como superviviente ni como absorbido -- 409
        `hecho_vencido`.
    """
    # Sin duplicados, mismo orden de llegada: absorbidos=[7, 7, 8] funde una
    # sola vez al 7.
    absorbidos = list(dict.fromkeys(body.absorbidos))
    if not absorbidos:
        return {"superados": 0}
    # Un hecho superado por si mismo es un ciclo: rechazarlo es mas barato
    # que explicarlo despues.
    if body.superviviente_id in absorbidos:
        raise HTTPException(status_code=400, detail="superviviente_en_absorbidos")
    autor = int(user.user_id)
    ids = [body.superviviente_id, *absorbidos]

    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        marcadores = ", ".join(["%s"] * len(ids))
        await cur.execute(
            f"SELECT id, superseded_by, is_verified, created_at, source_facet, "
            f"expires_at FROM facts WHERE id IN ({marcadores}) FOR UPDATE",
            ids,
        )
        filas = await cur.fetchall()
        superados_de = {}
        info = {}
        facet_por_id = {}
        vencidos = {}
        for fid, superseded_by, is_verified, created_at, source_facet, expires_at in filas:
            superados_de[fid] = superseded_by
            info[fid] = (bool(is_verified), created_at)
            facet_por_id[fid] = source_facet
            vencidos[fid] = expires_at

        if any(i not in superados_de for i in ids):
            raise HTTPException(status_code=404, detail="hecho_no_encontrado")
        # Encadenar sobre una cadena rota confunde la historia: ni el
        # superviviente ni ningun absorbido pueden estar ya superados. Todo o
        # nada: esta comprobacion corre para TODOS los ids ANTES de escribir
        # el primer UPDATE, así que un solo hecho ya superado en el lote
        # basta para que NINGUNO cambie.
        if any(superados_de[i] is not None for i in ids):
            raise HTTPException(status_code=409, detail="hecho_ya_superado")

        # MAJOR 2 (revision adversarial de jax-platform PR 146, tercera
        # vuelta): un hecho vencido no puede fundirse -- ni como
        # superviviente (resucitaria un hecho que dejo de pesar en la
        # busqueda, sin pasar por /caducar de vuelta) ni como absorbido
        # (perderia su propia fecha de vencimiento en silencio, fundida en
        # otro que no la tiene). `NOW()` se evalua en la MISMA transaccion
        # que ya trajo la fila con FOR UPDATE -- no hay ventana entre leer y
        # decidir.
        await cur.execute("SELECT NOW()")
        (ahora,) = await cur.fetchone()
        if any(vencidos[i] is not None and vencidos[i] <= ahora for i in ids):
            raise HTTPException(status_code=409, detail="hecho_vencido")

        # MAJOR 1, tercera vuelta: el cierre de citas se calcula sobre TODO
        # el grafo (mismo criterio que el detector, `agrupar_por_tema`) --
        # con el MISMO cursor de esta transaccion, para no abrir una segunda
        # conexion mientras el lote sigue bloqueado con FOR UPDATE.
        citas_directas = await _cargar_citas_directas(cur)
        cierre_citas = _cierre_transitivo_de_citas(citas_directas)

        # D3: compatibilidad PAR A PAR de todo el lote -- antes de decidir
        # quien sobrevive, porque si el lote mezcla tipos la operacion es
        # invalida sea cual sea el superviviente elegido.
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if not _compatibles_para_fundir(ids[i], ids[j], facet_por_id, cierre_citas):
                    raise HTTPException(status_code=409, detail="fundir_sintesis_con_no_sintesis")

        # D3: el superviviente solicitado tiene que ser el que la regla
        # calcularia para ESTE lote -- no el grupo entero que vio el
        # detector, que puede ser mas grande que lo que el cliente decidio
        # fundir de una vez.
        superviviente_correcto = _elegir_superviviente(ids, info)
        if superviviente_correcto != body.superviviente_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "superviviente_no_es_el_de_la_regla",
                    "superviviente_correcto": superviviente_correcto,
                },
            )

        if not info[body.superviviente_id][0]:
            await _aprobar_en_cursor(cur, autor, body.superviviente_id)

        for absorbido_id in absorbidos:
            await _superar_en_cursor(cur, autor, absorbido_id, body.superviviente_id)
    return {"superados": len(absorbidos)}


class CaducarBody(BaseModel):
    vence_at: Optional[str] = None


# TIMESTAMP en MariaDB/MySQL: rango real `1970-01-01 00:00:01` a
# `2038-01-19 03:14:07`, los dos en UTC (entero con signo de 32 bits desde
# el epoch). Se valida ANTES de tocar la base -- no se le pide a MariaDB que
# decida qué hacer con un valor fuera de rango (según el modo SQL, trunca,
# lo cambia por 0000-00-00, o rechaza con error de driver: ninguna de esas
# tres es "400 vence_at_invalido" con un detail legible).
_TIMESTAMP_MIN_UTC = datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
_TIMESTAMP_MAX_UTC = datetime(2038, 1, 19, 3, 14, 7, tzinfo=timezone.utc)


class _ZonaHorariaNoResuelta(RuntimeError):
    """`CONVERT_TZ` devolvió `NULL`: la base no pudo resolver
    `@@session.time_zone` contra el valor recibido (p.ej. una zona con
    nombre sin las tablas `mysql.time_zone*` cargadas). Es un fallo de
    infraestructura, no "sin caducidad" -- `expire_fact(None)` QUITARÍA la
    caducidad en vez de ponerla, así que este caso no puede llegar ahí."""


async def _hora_local_de_base(momento: datetime) -> datetime:
    """Convierte un datetime CON ZONA a la hora local que usa la base
    (`@@session.time_zone`, en producción SYSTEM -- Honduras, UTC-6, sin
    horario de verano hoy).

    La conversión la hace MariaDB con `CONVERT_TZ`, no una resta de horas a
    mano: `expires_at` se compara con `NOW()` (hora local de sesión, ver
    `SQL_LISTAR`/`SQL_VECINOS` arriba), y `CONVERT_TZ(dt, '+00:00',
    @@session.time_zone)` sigue dando la hora correcta aunque el sistema
    tuviera horario de verano (lee el tzdata real del SO vía 'SYSTEM'), cosa
    que restar un offset fijo en Python no podría. Verificado en la base de
    TEST (nunca en producción, `/etc/jax/.env` no se carga desde este
    módulo ni desde sus tests): con `@@session.time_zone = SYSTEM`,
    `CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', @@session.time_zone)` da el mismo
    valor que `NOW()` -- ver `test_memoria_api.py::
    test_los_dos_pools_ven_la_misma_zona_horaria` y el resto de la suite de
    caducar.

    El parámetro es el objeto `datetime` (naive, en UTC), no una cadena
    armada con `strftime`: `strftime("%Y", ...)` no garantiza el relleno a 4
    dígitos para años < 1000 (depende de la libc del runner), y el escapador
    de fechas del driver (`pymysql.converters.escape_datetime`) SÍ rellena
    siempre con `{0.year:04}` -- se le pasa el trabajo a él.

    Usa el mismo pool que el resto de este módulo (`db.connection.get_pool`,
    la conexión de jax-platform contra `jax_memory`) -- ninguna de las dos
    conexiones (ésta, y la de `MemoryDB.expire_fact` en `jax/memory/db.py`)
    fija un `time_zone` de sesión propio, así que las dos ven la misma
    `@@session.time_zone` del servidor (comprobado en vivo por
    `test_los_dos_pools_ven_la_misma_zona_horaria`, no solo supuesto)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT CONVERT_TZ(%s, '+00:00', @@session.time_zone)",
                (momento.astimezone(timezone.utc).replace(tzinfo=None),),
            )
            fila = await cur.fetchone()
    if fila is None or fila[0] is None:
        raise _ZonaHorariaNoResuelta(
            "CONVERT_TZ devolvió NULL -- @@session.time_zone no se pudo resolver")
    return fila[0]


@router.post("/hechos/{fact_id}/caducar")
async def caducar_hecho(fact_id: int, body: CaducarBody,
                        user: AuthUser = Depends(require_superadmin)):
    memoria = await _memoria_conectada()
    expira = None
    if body.vence_at:
        try:
            crudo = datetime.fromisoformat(body.vence_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="vence_at_invalido") from None
        # El frontend (Memoria.jsx) siempre manda `new Date().toISOString()`,
        # que SIEMPRE trae `Z` (UTC). Una fecha sin zona es ambigua -- no hay
        # forma de saber si es UTC, hora local, u otra cosa -- así que se
        # rechaza en vez de adivinar (2026-09-22: el defecto de las ~6 horas
        # de retraso era justo tratar un `Z` como si no tuviera zona).
        if crudo.tzinfo is None:
            raise HTTPException(status_code=400, detail="vence_at_sin_zona") from None
        crudo_utc = crudo.astimezone(timezone.utc)
        if not (_TIMESTAMP_MIN_UTC <= crudo_utc <= _TIMESTAMP_MAX_UTC):
            raise HTTPException(status_code=400, detail="vence_at_invalido") from None
        try:
            expira = await _hora_local_de_base(crudo)
        except Exception:
            # Cualquier fallo al convertir la zona (driver/red, o
            # `_ZonaHorariaNoResuelta` si CONVERT_TZ dio NULL) es un fallo de
            # infraestructura: 503 memoria_no_disponible, mismo contrato de
            # tres estados (M1, auditoría adversarial 2026-09-20) que el
            # resto del endpoint. Nunca sigue a `expire_fact(None)`: eso
            # QUITARÍA la caducidad en vez de ponerla. Re-lanza (no traga el
            # error): no necesita la marca `# fail-soft:` del detector
            # no-fail-open-except -- ese detector exige la marca sólo cuando
            # un except amplio NO relanza nada.
            raise HTTPException(status_code=503, detail="memoria_no_disponible") from None
    ok = await memoria.expire_fact(fact_id, expira)
    # M1 (auditoria adversarial 2026-09-20): mismo contrato de tres estados
    # que aprobar_hechos -- None es "la base no respondio", nunca "no
    # encontrado". Antes un None se leia como 404, una mentira sobre un
    # hecho que SI existe.
    if ok is None:
        raise HTTPException(status_code=503, detail="memoria_no_disponible")
    if not ok:
        raise HTTPException(status_code=404, detail="hecho_no_encontrado")
    return {"ok": True}


# --------------------------------------------------------------------------
# Agrupar por tema (Task 5, spec §2.1) -- "junte las cosas del mismo tema",
# con los casi-duplicados vistos juntos y marcados: "estos tres dicen lo
# mismo".
#
# Usa el indice vectorial HNSW de `embedding_bge_m3`, verificado con EXPLAIN
# (test_memoria_grupos.py::test_el_agrupamiento_usa_el_indice_vectorial).
# NINGUNA consulta de acá hace JOIN contra `facts`: el antecedente de esta
# casa (2026-09-11, jax_memory/messages) es que un JOIN de más saca al
# optimizador del índice vectorial en silencio -- 58,5 ms contra 0,4 ms con
# 1.149 filas. Por eso, igual que `_find_nearest_fact()` en jax/memory/db.py,
# cada vecino se busca con un SELECT propio (`SQL_VECINOS`), nunca con un
# self-join.
#
# UMBRAL_MISMO_TEMA reutiliza CORRECTION_DISTANCE_THRESHOLD (0.25) -- la
# banda YA calibrada en jax/memory/db.py para "esto describe el mismo hecho,
# con otras palabras". No se inventa un umbral nuevo: medido el 2026-09-20
# contra tres redacciones reales de la misma afirmación (ver
# tests/fixtures/memoria_casi_duplicados_bge_m3.json), la distancia entre
# ellas da 0,0968-0,1445 -- muy por debajo de 0,25 -- y una redacción sin
# relación queda en ~0,77. Ese hueco es el que separa "mismo tema" de "temas
# distintos".
#
# Diseño para 10.000 hechos (spec §4), no para 116: las búsquedas de vecinos
# son consultas async independientes (una por hecho, con el índice haciendo
# el trabajo pesado en el servidor) que corren en paralelo acotadas por un
# semáforo -- y el trabajo puramente de CPU (union-find + verificación de
# casi-duplicados) va a un hilo aparte (`asyncio.to_thread`) para no
# bloquear el event loop mientras se calcula.
# --------------------------------------------------------------------------
_COLUMNA_EMBED = _columna_embedding()

# Cuántos vecinos más cercanos se piden por hecho al construir el grafo de
# temas. Un puñado alcanza: un grupo real rara vez tiene más de unos pocos
# miembros, y un vecino que quede fuera del top-K está, casi siempre, en
# otro tema (más lejos que UMBRAL_MISMO_TEMA).
_K_VECINOS_TEMA = 8

# Cuántas búsquedas de vecinos corren en paralelo. El pool de este módulo
# (db.connection.get_pool) tiene maxsize=10 (db/connection.py); se deja
# margen para que otros requests sigan sirviéndose mientras corre ésta.
_CONCURRENCIA_VECINOS = 6

# Grupos más grandes que esto no se verifican par-a-par para casi_duplicados
# (costo O(m²) del clustering exacto): quedan agrupados por "tema" igual,
# simplemente sin sub-marcar duplicados exactos dentro del grupo. A escala
# de hoy (116 hechos) ningún grupo se acerca a este tamaño.
_MAX_MIEMBROS_CASI_DUPLICADO = 90

SQL_ACTIVOS_CON_VECTOR = (
    "SELECT id, fact_text, is_verified, created_at, "
    f"VEC_ToText({_COLUMNA_EMBED}) AS vector_texto, source_facet "
    "FROM facts "
    "WHERE superseded_by IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW()) "
    f"AND {_embedding_no_cero_sql(_COLUMNA_EMBED)}"
)
# `source_facet` va AL FINAL (índice 5) a propósito: `_casi_duplicados_
# del_grupo` sigue leyendo el vector en el índice 4 tal cual lo hacía antes
# de la ronda 2026-09-22, así que las tuplas sintéticas de 5 elementos que
# ya usan los tests de rendimiento de este archivo (sin facet) siguen
# funcionando sin tocar -- `len(m) > 5` decide si hay sexto elemento.
#
# Ronda 146, tercera vuelta (MAJOR 1): esta consulta YA NO trae
# `source_fact_ids` -- el criterio de citas dejó de ser "lo que trae CADA
# fila del grupo" y pasó a ser el CIERRE TRANSITIVO de TODO el grafo de
# citas (`SQL_CITAS`/`_cierre_transitivo_de_citas`, más abajo), que
# necesita ver facts fuera de `miembros` (otros grupos, superados,
# vencidos). Guardar `source_fact_ids` acá también hubiera quedado como
# dato muerto: nada lo volvía a leer.

# La consulta que agrupar_por_tema() corre DE VERDAD para cada hecho, vecino
# a vecino -- mismo patrón que _find_nearest_fact() (jax/memory/db.py). El
# vector de consulta es el propio VEC_ToText() de la fila (ver
# SQL_ACTIVOS_CON_VECTOR): no hace falta volver a pedírselo a MariaDB.
SQL_VECINOS = (
    "SELECT id, "
    f"VEC_DISTANCE_COSINE({_COLUMNA_EMBED}, VEC_FromText(%s)) AS distancia "
    "FROM facts "
    "WHERE id != %s AND superseded_by IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW()) "
    f"AND {_embedding_no_cero_sql(_COLUMNA_EMBED)} "
    f"ORDER BY VEC_DISTANCE_COSINE({_COLUMNA_EMBED}, VEC_FromText(%s)) ASC "
    "LIMIT %s"
)
# id=0 y un vector no-nulo cualquiera (no hace falta un hecho real): alcanza
# para que EXPLAIN vea la consulta REAL, con la misma forma y los mismos
# tipos de parámetro. test_memoria_grupos.py corre EXPLAIN sobre esta tupla
# exacta.
_VECTOR_EJEMPLO = json.dumps([0.0001] * _EMBED_CFG.dim)
ARGS_VECINOS_EJEMPLO = (_VECTOR_EJEMPLO, 0, _VECTOR_EJEMPLO, _K_VECINOS_TEMA)


def _norma(v: list) -> float:
    """Norma euclídea de un vector.

    Es una función propia, y no una línea dentro de `_distancia_coseno`, por
    dos motivos: para poder calcularla UNA vez por vector en vez de una por
    par, y para que un test pueda CONTAR las llamadas. El defecto que esto
    cierra era justamente recomputarla dentro del bucle de pares -- en un
    grupo de 90 miembros, la norma de cada vector se recalculaba 89 veces.
    """
    return math.sqrt(math.sumprod(v, v))


def _distancia_coseno(a: list, b: list,
                      norma_a: float | None = None,
                      norma_b: float | None = None) -> float:
    """Distancia coseno (1 - similitud), para el chequeo de casi-duplicados
    dentro de un grupo ya pequeño -- sin volver a golpear la DB por cada par,
    reusando los vectores que agrupar_por_tema() ya trajo.

    `math.sumprod` (stdlib, C) en vez de `sum(x*y for ...)`: 8,2x más rápido,
    medido a 1.024 dimensiones sobre 4.005 pares (47,4 us -> 5,8 us por par). A
    10.000 hechos esta función se llama 94.695 veces por request, así que ese
    factor es el 61 % del tiempo del endpoint.

    **NO da el mismo bit que `sum(generador)`**, y conviene decirlo con el
    número: `sumprod` acumula compensado, y sobre los 94.435 pares REALES de la
    base de carga el 2,87 % difiere en los últimos bits, con un error máximo de
    3,3e-16. Lo que hace seguro el cambio no es una identidad que no existe,
    sino el MARGEN: la distancia real más cercana al umbral 0,25 está a
    2,94e-02, o sea **8,8e13 veces el error**. Ninguna decisión de agrupamiento
    puede darse vuelta por eso. (Medido el 2026-09-20; la identidad bit a bit
    se afirmó primero y una auditoría adversarial la refutó.)

    `norma_a`/`norma_b` se aceptan ya calculadas: quien compara m vectores
    par a par las calcula una vez cada una y no m-1 veces.
    """
    if len(a) != len(b):
        # `math.sumprod` levanta ValueError donde `zip` truncaba en silencio.
        # Hoy no es alcanzable (`embedding_bge_m3` es VECTOR(1024) NOT NULL: la
        # dimensión la impone MariaDB), pero en una migración de embeddings
        # conviven dos columnas de distinta dimensión -- ya pasó el 2026-09-12.
        # Comparar vectores de distinto largo no tiene respuesta correcta, así
        # que se dice en vez de truncar o de reventar con un error del stdlib.
        raise ValueError(
            f"vectores de distinta dimensión: {len(a)} y {len(b)}. "
            "¿Quedaron dos columnas de embeddings conviviendo?")
    na = _norma(a) if norma_a is None else norma_a
    nb = _norma(b) if norma_b is None else norma_b
    if na == 0 or nb == 0:
        return float("inf")
    return 1 - math.sumprod(a, b) / (na * nb)


class _UnionFind:
    """Union-find mínimo (path halving + union por raíz), para agrupar por
    componentes conexas -- tanto el grafo de temas como, dentro de cada
    grupo, el sub-grafo de casi-duplicados."""

    def __init__(self, elementos):
        self._padre = {e: e for e in elementos}

    def encontrar(self, x):
        while self._padre[x] != x:
            self._padre[x] = self._padre[self._padre[x]]
            x = self._padre[x]
        return x

    def unir(self, a, b):
        ra, rb = self.encontrar(a), self.encontrar(b)
        if ra != rb:
            self._padre[ra] = rb

    def componentes(self):
        grupos: dict = {}
        for elemento in self._padre:
            grupos.setdefault(self.encontrar(elemento), []).append(elemento)
        return list(grupos.values())


def _parse_fuentes(valor, fact_id) -> frozenset:
    """`source_fact_ids` es JSON (lista de ids) o NULL -- lo que escribe el
    worker de síntesis de segundo orden (jax/memory/synthesis_worker.py) para
    trazar de qué hechos sale un insight (jax/memory/db.py::save_fact). Un
    hecho normal (extracción directa de conversación) no lo trae, y eso no es
    un dato malo: es "sin fuentes", frozenset() sin marcar nada.

    Ilegible (JSON roto, no es una lista de números) SÍ es un dato malo --
    pero fundir no puede colgarse por un problema de trazabilidad ajeno
    (Principio VIII: un 'no se pudo leer' honesto, no un 500). Se trata como
    'sin fuentes' Y se registra -- nunca en silencio."""
    if not valor:
        return frozenset()
    try:
        return frozenset(int(x) for x in json.loads(valor))
    except (TypeError, ValueError):  # fail-soft: source_fact_ids ilegible se trata como "sin fuentes" (no bloquea el agrupamiento); se cuenta con el WARNING de abajo, nunca en silencio
        logger.warning(
            "facts.source_fact_ids ilegible en fact_id=%s: %r", fact_id, valor)
        return frozenset()


SQL_CITAS = "SELECT id, source_fact_ids FROM facts WHERE source_fact_ids IS NOT NULL"
# Ronda 146, tercera vuelta (MAJOR 1, revisión adversarial de jax-platform
# PR 146): SIN filtrar por `superseded_by`/`expires_at` a propósito -- una
# síntesis puede citar a un hecho que después se superó o venció, y la
# cadena de citas tiene que seguir cerrada igual (una síntesis no deja de
# ser síntesis DE algo sólo porque ese algo ya no está activo). `EXPLAIN`
# (test_memoria_grupos.py) confirma que no hay índice útil para
# `source_fact_ids IS NOT NULL` -- `source_fact_ids` es `longtext`, sin
# índice (verificado con `SHOW INDEX FROM facts` contra jax_memory_test):
# full scan, `type=ALL`. Medido contra la base de carga de 10.000 filas
# (docs/carga-memoria-146-2026-09-22.md): son pocas las filas con
# `source_fact_ids` no nulo (las síntesis, no todo `facts`), así que el
# costo absoluto es chico aunque el plan sea un scan completo.


async def _cargar_citas_directas(cur) -> dict:
    """id -> frozenset de ids que ESE id cita directamente (una fila de
    `source_fact_ids`), para TODOS los facts que tienen ese dato -- activos
    o no. Un solo SELECT, reusado por el detector (`agrupar_por_tema`, su
    propia conexión) y por `fundir_hechos` (el cursor de SU transacción,
    para no abrir una segunda conexión en medio del `FOR UPDATE`)."""
    await cur.execute(SQL_CITAS)
    return {fid: _parse_fuentes(raw, fid) for fid, raw in await cur.fetchall()}


def _cierre_transitivo_de_citas(citas_directas: dict) -> dict:
    """id -> frozenset de TODOS los ids que ese id cita, transitivamente
    (BFS sobre el grafo dirigido "cita a" que arma `citas_directas`).

    MAJOR 1 (revisión adversarial de jax-platform PR 146, tercera vuelta):
    la exclusión de la ronda anterior sólo miraba la cita DIRECTA -- S1 cita
    a S2 los separaba, pero S1 citando a S2 que a su vez cita a S3 (segundo
    grado) no separaba a S1 de S3. Peor: con las tres en el mismo union-find,
    S1 y S3 (incompatibles, aunque sea indirectamente) podían terminar en el
    mismo componente igual, conectados vía S2 -- el MISMO bug de bridging
    que D2 ya había cerrado para el cruce de tipos, reaparecido acá porque
    la cita sólo se miraba a UN salto. El cierre transitivo (esta función)
    hace que "A cita a B, aunque sea indirectamente" sea una propiedad
    ESTABLE del grafo completo, calculada una vez por request -- no algo
    que dependa de si A y B llegaron a compararse directo."""
    cierre = {}
    for origen in citas_directas:
        visto: set = set()
        cola = list(citas_directas.get(origen, ()))
        while cola:
            actual = cola.pop()
            if actual in visto:
                continue
            visto.add(actual)
            cola.extend(citas_directas.get(actual, ()))
        cierre[origen] = frozenset(visto)
    return cierre


def _relacionados_por_cita(a_id, b_id, cierre_citas: dict) -> bool:
    """True si `a_id` cita a `b_id` o `b_id` cita a `a_id`, a CUALQUIER
    profundidad (ver `_cierre_transitivo_de_citas`) -- nunca si sólo
    comparten un ancestro en común (dos síntesis que citan a la MISMA fuente,
    sin citarse entre sí, siguen siendo compatibles -- D1: "dos síntesis
    entre sí sí pueden agruparse")."""
    return b_id in cierre_citas.get(a_id, frozenset()) or a_id in cierre_citas.get(b_id, frozenset())


def _tipo_sintesis(facet) -> bool:
    """`source_facet == 'synthesis'` es la faceta que escribe
    `jax/memory/synthesis_worker.py` sobre un insight de segundo orden.

    Ronda 146 (D1, decisión de Fernando, revisión adversarial de
    jax-platform PR 146): la faceta SOLA alcanza para separar síntesis de
    no-síntesis. La versión anterior (2026-09-22, este mismo archivo) sólo
    miraba `source_fact_ids`, y eso dejaba tres huecos:
      - síntesis de SEGUNDO orden (S2 cita a S1, que cita a A -- S2 nunca
        tiene a A en su PROPIO `source_fact_ids`, así que la exclusión
        directa no lo veía).
      - una fuente ya SUPERADA (el id que `source_fact_ids` referencia ya no
        está activo -- pero da igual: la faceta de la síntesis no cambia
        aunque su fuente original haya sido reemplazada).
      - una síntesis con `source_fact_ids` NULL (dato de trazabilidad
        ausente -- con la versión anterior, eso la dejaba SIN exclusión
        alguna, agrupable con cualquier no-síntesis cercana).
    Los tres quedan cerrados de una vez con la faceta, que no depende de que
    la cadena de ids esté completa ni de que el id referenciado siga vivo."""
    return facet == "synthesis"


def _compatibles_para_fundir(a_id, b_id, facet_por_id: dict, cierre_citas: dict) -> bool:
    """Dos hechos pueden compartir un cluster de casi-duplicados -- y, por
    extensión, fundirse juntos (D3: `fundir_hechos` exige esta MISMA regla,
    no sólo la propone) -- sólo si:
      1. son del MISMO tipo (los dos síntesis, o los dos no) -- D1.
      2. ninguno cita al otro, a NINGUNA profundidad -- `cierre_citas` (MAJOR
         1, tercera vuelta) para separar dos síntesis que se citan entre sí,
         directa O transitivamente (D1: "dos síntesis entre sí sí pueden
         agruparse", salvo que una cite a la otra)."""
    if _tipo_sintesis(facet_por_id[a_id]) != _tipo_sintesis(facet_por_id[b_id]):
        return False
    if _relacionados_por_cita(a_id, b_id, cierre_citas):
        return False
    return True


def _casi_duplicados_del_grupo(miembros: list, cierre_citas: dict) -> list[list[int]]:
    """miembros: lista de (id, fact_text, is_verified, created_at, vector[,
    source_facet]) -- el sexto elemento es opcional (retrocompatible con las
    tuplas sintéticas de 5 que ya usan los tests de rendimiento de este
    archivo, que no tocan síntesis). `cierre_citas`: el cierre transitivo
    de TODO el grafo de citas (`_cierre_transitivo_de_citas`), calculado UNA
    vez por request -- no se reconstruye acá porque necesita ver facts que
    pueden no estar en `miembros` (una síntesis puede citar a un hecho de
    OTRO grupo, o ya superado/vencido). Devuelve subconjuntos (>=2
    elementos): cada uno es una componente CONEXA del grafo de pares a
    distancia <= UMBRAL_MISMO_TEMA -- es decir, sus miembros están
    conectados por una CADENA de saltos, cada uno <= UMBRAL_MISMO_TEMA, pero
    el PAR en sí puede estar más lejos.

    CORRECCIÓN (ronda 146, revisión adversarial de jax-platform PR 146): el
    docstring anterior decía "verificación exacta, par a par" -- era FALSO,
    y lo era desde antes de esta ronda: un componente conexo (union-find)
    nunca garantizó que TODOS los pares dentro de él estén bajo el umbral,
    sólo que hay un camino de saltos cortos entre ellos (ver
    `test_el_camino_de_produccion_da_LO_MISMO_que_la_implementacion_vieja`,
    que exige justamente esta semántica de componente conexo).

    Ronda 2026-09-22 → 146 (D1/D2, decisión de Fernando): una SÍNTESIS
    (`source_facet == 'synthesis'`) nunca puede compartir cluster con un
    hecho que NO lo es -- ni siquiera conectados vía un tercer miembro
    (bridging). La primera versión de este arreglo (2026-09-22) armaba el
    componente por distancia y DESPUÉS sacaba a los miembros conflictivos --
    una revisión adversarial demostró que eso deja pares FALSOS: si A-S y
    S-C están cerca pero A-C está lejos, sacar a S del componente {A,S,C}
    devolvía `[[A,C]]` aunque A y C NO estén cerca entre sí (MAYOR 1). La
    corrección (D2) fue no dejar que una arista incompatible se cree NUNCA:
    `_compatibles_para_fundir` se consulta ANTES de cada `uf.unir(...)`.

    MAJOR 1, tercera vuelta (revisión adversarial de jax-platform PR 146):
    esa corrección alcanzaba para tipos cruzados (bloqueo TOTAL, nunca se
    unen sin importar el puente) pero no para dos síntesis del MISMO tipo
    con una cita indirecta -- S1 cita a S2 (incompatibles), pero si S3 (sin
    relación con ninguna) está cerca de las dos, S1 y S2 podían terminar en
    el MISMO componente igual, conectados vía S3 -- el bridging reaparece
    cuando la incompatibilidad es puntual (una arista) en vez de total (un
    tipo entero). Por eso ahora, DESPUÉS de `uf.componentes()`, cada
    componente final se revalida PAR A PAR (no sólo las aristas que se
    unieron): si queda algún par incompatible adentro, el componente entero
    se descarta -- fail-closed, el detector propone de menos, nunca de más.
    Cualquier otra forma de no-transitividad que aparezca en el futuro queda
    cubierta por este mismo chequeo, sin tener que anticiparla."""
    if len(miembros) < 2 or len(miembros) > _MAX_MIEMBROS_CASI_DUPLICADO:
        return []
    vectores = {m[0]: json.loads(m[4]) for m in miembros}
    facet_por_id = {m[0]: (m[5] if len(m) > 5 else None) for m in miembros}
    # UNA norma por vector, no una por par: con 90 miembros son 90 raíces en
    # vez de 8.010. Es el arreglo medido del 2026-09-20 (61 % del request).
    normas = {i: _norma(v) for i, v in vectores.items()}
    ids = list(vectores)
    uf = _UnionFind(ids)
    for i in range(len(ids)):
        a = ids[i]
        for j in range(i + 1, len(ids)):
            b = ids[j]
            # La compatibilidad se consulta ANTES que la distancia: evita
            # calcular una distancia coseno (la parte cara, medida el
            # 2026-09-20) para un par que de todos modos no se va a unir --
            # pero YA NO es la única garantía (ver la revalidación de abajo).
            if not _compatibles_para_fundir(a, b, facet_por_id, cierre_citas):
                continue
            if _distancia_coseno(vectores[a], vectores[b],
                                 normas[a], normas[b]) <= _UMBRAL_MISMO_TEMA:
                uf.unir(a, b)

    resultado = []
    for componente in uf.componentes():
        if len(componente) < 2:
            continue
        comp = sorted(componente)
        limpio = all(
            _compatibles_para_fundir(comp[i], comp[j], facet_por_id, cierre_citas)
            for i in range(len(comp)) for j in range(i + 1, len(comp))
        )
        if limpio:
            resultado.append(comp)
    return resultado


def _elegir_superviviente(ids: list, info: dict) -> int:
    """Decisión de Fernando: el verificado gana al más reciente. Si hay
    algún verificado entre `ids`, sobrevive el verificado más reciente; si
    no hay ninguno, sobrevive el más reciente a secas.

    D4 (ronda 146): empate en `created_at` (la columna es un `TIMESTAMP` de
    precisión de SEGUNDO -- dos hechos sembrados en la misma corrida caen
    fácil en el mismo segundo) lo desempata el id MAYOR -- el que se
    insertó después.

    D8 (ronda 146): `created_at` admite NULL (verificado con `SHOW COLUMNS`
    contra `jax_memory_test`, nunca contra producción). NULL se trata como
    "el más antiguo posible": nunca le gana a una fecha real, y si TODOS los
    candidatos tienen NULL el desempate cae sólo en el id.

    `info`: id -> (is_verified, created_at)."""
    def _clave(i):
        creado = info[i][1]
        return (creado if creado is not None else datetime.min, i)

    verificados = [i for i in ids if info[i][0]]
    candidatos = verificados or ids
    return max(candidatos, key=_clave)


# M4 (revisión adversarial de jax-platform PR 146, tercera vuelta):
# `superviviente_texto` viaja tal cual desde `fact_text`, que no tiene
# límite de longitud en el frontend (spec §2.1 no lo impone). Sin recorte,
# una redacción larga y sin espacios podía desbordar la ventana de
# `ConfirmacionSuma` (probado con `break-words`, ver FichaDeHecho.jsx/
# ConfirmacionSuma.jsx). 280 caracteres alcanza para reconocer el hecho sin
# volver ilegible la ventana.
_MAX_CARACTERES_TEXTO_SUPERVIVIENTE = 280


def _recortar_texto(texto: str) -> str:
    if len(texto) <= _MAX_CARACTERES_TEXTO_SUPERVIVIENTE:
        return texto
    return texto[:_MAX_CARACTERES_TEXTO_SUPERVIVIENTE].rstrip() + "…"


def _construir_grupos(filas: list, vecinos: list, cierre_citas: dict) -> list[dict]:
    """CPU pura (sin await): se corre en un hilo aparte (asyncio.to_thread)
    para no bloquear el event loop a escala de 10.000 hechos.

    filas: (id, fact_text, is_verified, created_at, vector, source_facet) de
    cada hecho activo. vecinos: [(fact_id, [(vecino_id, distancia), ...]),
    ...], el resultado de SQL_VECINOS para cada fila. `cierre_citas`: el
    cierre transitivo de TODO el grafo de citas (MAJOR 1, tercera vuelta),
    calculado UNA vez en `agrupar_por_tema` y pasado tal cual a
    `_casi_duplicados_del_grupo` para cada componente.

    Ronda 2026-09-22: cada cluster de `casi_duplicados` deja de ser una
    lista de ids a secas -- pasa a un objeto con `superviviente_id`
    (`_elegir_superviviente`, el verificado gana al más reciente). El
    backend declara quién sobrevive UNA sola vez acá; ni el frontend ni
    `POST /hechos/fundir` vuelven a adivinarlo con "el primero de la
    lista".

    D5 (ronda 146, revisión adversarial de jax-platform PR 146, MAYOR 3): el
    cluster también trae `superviviente_verificado` y `superviviente_texto`
    -- el frontend arma el motivo ("sobrevive el verificado" / "sobrevive el
    más reciente, quedará aprobado al fundir") y el texto de la ficha con
    ESTOS datos, no con `hechosPorId` (que sólo tiene los primeros 500
    hechos cargados por `GET /hechos`; un cluster puede incluir ids que ese
    cap dejó afuera). `superviviente_texto` viaja recortado (M4, ver
    `_recortar_texto`)."""
    datos = {f[0]: f for f in filas}
    uf = _UnionFind(datos.keys())
    for fact_id, cercanos in vecinos:
        for vecino_id, distancia in cercanos:
            if vecino_id in datos and distancia is not None \
                    and distancia <= _UMBRAL_MISMO_TEMA:
                uf.unir(fact_id, vecino_id)

    grupos = []
    for miembros_ids in uf.componentes():
        # D8: `created_at` NULL no puede reventar el ordenamiento (antes,
        # comparar None contra un datetime real levantaba TypeError) -- se
        # trata como "el más antiguo posible", igual que en
        # `_elegir_superviviente`. El id como segundo criterio hace el orden
        # determinista incluso si dos miembros empatan en fecha.
        miembros = sorted(
            (datos[i] for i in miembros_ids),
            key=lambda f: (f[3] if f[3] is not None else datetime.min, f[0]),
            reverse=True,
        )
        info = {m[0]: (m[2], m[3]) for m in miembros}  # id -> (is_verified, created_at)
        textos = {m[0]: m[1] for m in miembros}
        clusters = []
        for cluster in _casi_duplicados_del_grupo(miembros, cierre_citas):
            sid = _elegir_superviviente(cluster, info)
            clusters.append({
                "ids": cluster,
                "superviviente_id": sid,
                "superviviente_verificado": bool(info[sid][0]),
                "superviviente_texto": _recortar_texto(textos[sid]),
            })
        grupos.append({
            "tema": miembros[0][1],
            "hechos": [m[0] for m in miembros],
            "sin_verificar": sum(1 for m in miembros if not m[2]),
            "casi_duplicados": clusters,
        })

    grupos.sort(key=lambda g: len(g["hechos"]), reverse=True)
    return grupos


async def agrupar_por_tema() -> list[dict]:
    """Agrupa los hechos activos por cercanía semántica (spec §2.1). Ver el
    comentario de módulo de arriba para el diseño completo (índice,
    umbral, escala)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SQL_ACTIVOS_CON_VECTOR)
            filas = await cur.fetchall()
    if not filas:
        return []

    # MAJOR 1, tercera vuelta: el cierre de citas se calcula UNA vez por
    # request, sobre TODO el grafo (no sólo los miembros de un grupo) --
    # conexión propia, antes de repartir el trabajo de vecinos.
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            citas_directas = await _cargar_citas_directas(cur)
    cierre_citas = _cierre_transitivo_de_citas(citas_directas)

    semaforo = asyncio.Semaphore(_CONCURRENCIA_VECINOS)

    async def _vecinos_de(fact_id, vector_texto):
        async with semaforo:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        SQL_VECINOS,
                        (vector_texto, fact_id, vector_texto, _K_VECINOS_TEMA),
                    )
                    return fact_id, await cur.fetchall()

    vecinos = await asyncio.gather(*(_vecinos_de(f[0], f[4]) for f in filas))

    return await asyncio.to_thread(_construir_grupos, filas, vecinos, cierre_citas)


@router.get("/grupos")
async def listar_grupos(user: AuthUser = Depends(require_superadmin)):
    return {"grupos": await agrupar_por_tema()}
