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
import math
import sys
from datetime import datetime
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
            f"SELECT id, superseded_by FROM facts WHERE id IN ({marcadores}) FOR UPDATE",
            ids,
        )
        estado = {fila[0]: fila[1] for fila in await cur.fetchall()}
        if any(i not in estado for i in ids):
            raise HTTPException(status_code=404, detail="hecho_no_encontrado")
        # Encadenar sobre una cadena rota confunde la historia: ni el
        # superviviente ni ningun absorbido pueden estar ya superados. Todo o
        # nada: esta comprobacion corre para TODOS los ids ANTES de escribir
        # el primer UPDATE, así que un solo hecho ya superado en el lote
        # basta para que NINGUNO cambie.
        if any(estado[i] is not None for i in ids):
            raise HTTPException(status_code=409, detail="hecho_ya_superado")

        for absorbido_id in absorbidos:
            await cur.execute(
                "UPDATE facts SET superseded_by = %s, superseded_at = NOW(), "
                "superseded_by_user = %s WHERE id = %s",
                (body.superviviente_id, autor, absorbido_id),
            )
    return {"superados": len(absorbidos)}


class CaducarBody(BaseModel):
    vence_at: Optional[str] = None


@router.post("/hechos/{fact_id}/caducar")
async def caducar_hecho(fact_id: int, body: CaducarBody,
                        user: AuthUser = Depends(require_superadmin)):
    memoria = await _memoria_conectada()
    expira = None
    if body.vence_at:
        try:
            expira = datetime.fromisoformat(body.vence_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="vence_at_invalido") from None
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
    f"VEC_ToText({_COLUMNA_EMBED}) AS vector_texto "
    "FROM facts "
    "WHERE superseded_by IS NULL "
    "AND (expires_at IS NULL OR expires_at > NOW()) "
    f"AND {_embedding_no_cero_sql(_COLUMNA_EMBED)}"
)

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


def _distancia_coseno(a: list, b: list) -> float:
    """Distancia coseno en Python puro (1 - similitud), para el chequeo de
    casi-duplicados dentro de un grupo ya pequeño -- sin volver a golpear la
    DB por cada par, reusando los vectores que agrupar_por_tema() ya trajo."""
    punto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if norma_a == 0 or norma_b == 0:
        return float("inf")
    return 1 - punto / (norma_a * norma_b)


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


def _casi_duplicados_del_grupo(miembros: list) -> list[list[int]]:
    """miembros: lista de (id, fact_text, is_verified, created_at, vector).
    Devuelve subconjuntos (>=2 elementos) cuyos miembros están, par a par,
    a distancia <= UMBRAL_MISMO_TEMA -- verificación exacta, no la cadena de
    vecinos-más-cercanos que formó el grupo (que puede conectar A con C vía
    B sin que A y C estén realmente cerca)."""
    if len(miembros) < 2 or len(miembros) > _MAX_MIEMBROS_CASI_DUPLICADO:
        return []
    vectores = {m[0]: json.loads(m[4]) for m in miembros}
    ids = list(vectores)
    uf = _UnionFind(ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if _distancia_coseno(vectores[ids[i]], vectores[ids[j]]) <= _UMBRAL_MISMO_TEMA:
                uf.unir(ids[i], ids[j])
    return [sorted(c) for c in uf.componentes() if len(c) > 1]


def _construir_grupos(filas: list, vecinos: list) -> list[dict]:
    """CPU pura (sin await): se corre en un hilo aparte (asyncio.to_thread)
    para no bloquear el event loop a escala de 10.000 hechos.

    filas: (id, fact_text, is_verified, created_at, vector) de cada hecho
    activo. vecinos: [(fact_id, [(vecino_id, distancia), ...]), ...], el
    resultado de SQL_VECINOS para cada fila."""
    datos = {f[0]: f for f in filas}
    uf = _UnionFind(datos.keys())
    for fact_id, cercanos in vecinos:
        for vecino_id, distancia in cercanos:
            if vecino_id in datos and distancia is not None \
                    and distancia <= _UMBRAL_MISMO_TEMA:
                uf.unir(fact_id, vecino_id)

    grupos = []
    for miembros_ids in uf.componentes():
        miembros = sorted((datos[i] for i in miembros_ids),
                          key=lambda f: f[3], reverse=True)  # created_at DESC
        grupos.append({
            "tema": miembros[0][1],
            "hechos": [m[0] for m in miembros],
            "sin_verificar": sum(1 for m in miembros if not m[2]),
            "casi_duplicados": _casi_duplicados_del_grupo(miembros),
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

    return await asyncio.to_thread(_construir_grupos, filas, vecinos)


@router.get("/grupos")
async def listar_grupos(user: AuthUser = Depends(require_superadmin)):
    return {"grupos": await agrupar_por_tema()}
