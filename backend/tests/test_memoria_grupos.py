"""Agrupar por tema (Task 5, spec §2.1): "junte las cosas del mismo tema",
casi-duplicados vistos juntos y marcados.

CORRECCION de Fernando (2026-09-20) al plan original: el plan ancla el test a
los hechos 136/138/139 de PRODUCCION ("JAX no tiene capacidad nativa para
SQL", tres redacciones, 2026-09-04). Esos ids no existen en `jax_memory_test`
-- un test que depende de datos de produccion no es un test. Este archivo
siembra su propio trio de casi-duplicados, con embeddings REALES (bge-m3), y
usa los ids que la fixture decide.

Distancias reales medidas el 2026-09-20 entre las tres redacciones de abajo
(script ad-hoc contra jax_memory_test, bge-m3, ver `tests/fixtures/
memoria_casi_duplicados_bge_m3.json`):
    dist(0,1) = 0.0977   dist(0,2) = 0.1445   dist(1,2) = 0.0968
Las tres quedan muy por debajo de CORRECTION_DISTANCE_THRESHOLD (0.25, la
banda ya calibrada en jax/memory/db.py) y muy por encima de
DUP_DISTANCE_THRESHOLD (0.05) -- exactamente el caso real que el
deduplicador de insercion no atrapa (spec §1: "estan parafraseados, no
repetidos") y que esta pantalla existe para resolver.

Los embeddings NO se calculan en el test: `tests/conftest.py` FIJA
`JAX_OLLAMA_URL=http://ollama.invalid:11434` a proposito (hallazgo del frente
E, 2026-09-16) para que un test que se olvide de mockear el transporte le
pegue a un DNS que nunca resuelve, no al Ollama real. Por eso los tres
vectores se precalcularon UNA vez (script ad-hoc, mismo bge-m3 de
produccion) y se sembraron como JSON en el repo -- el test los inserta tal
cual, sin red.
"""
import json
import pathlib

import pytest

# Primero: dispara el sys.path.insert(JAX_REPO_PATH) que hace api/chat.py al
# importarse (memoria.py lo importa en su primera linea). Sin este orden,
# `from jax.memory...` de abajo falla si este modulo se colecciona antes que
# cualquier otro que ya haya tocado api.chat.
from api.admin import memoria  # noqa: E402
from jax.memory.embedding_config import CONFIG as _EMBED  # noqa: E402
from tests.identidades import sql  # noqa: E402

_FIXTURE_EMBEDDINGS = pathlib.Path(__file__).parent / "fixtures" / \
    "memoria_casi_duplicados_bge_m3.json"


async def _crear_fact_con_embedding(texto, embedding, fact_type="technical"):
    return await sql(
        "INSERT INTO facts (fact_uuid, fact_text, fact_type, is_verified, "
        f"{_EMBED.column}) VALUES (UUID(), %s, %s, FALSE, VEC_FromText(%s))",
        (texto, fact_type, json.dumps(embedding)),
    )


@pytest.fixture
def casi_duplicados_de_prueba(request):
    """Siembra el trio de casi-duplicados (ver docstring del modulo, y
    tests/fixtures/memoria_casi_duplicados_bge_m3.json) y lo borra al
    terminar. Se salta sola si no hay `client` (Regla 1 de conftest.py: sin
    DB, sin fixture)."""
    if "client" not in request.fixturenames:
        yield None
        return
    client = request.getfixturevalue("client")
    datos = json.loads(_FIXTURE_EMBEDDINGS.read_text(encoding="utf-8"))
    assert len(datos["embeddings"][0]) == _EMBED.dim, (
        f"la fixture tiene embeddings de {len(datos['embeddings'][0])} dims, "
        f"la config activa espera {_EMBED.dim} -- regenerar el JSON"
    )
    ids = []
    for texto, embedding in zip(datos["textos"], datos["embeddings"]):
        ids.append(client.portal.call(_crear_fact_con_embedding, texto, embedding))
    yield ids
    for fid in ids:
        client.portal.call(sql, "DELETE FROM facts WHERE id = %s", (fid,))


def test_listar_grupos_exige_superadmin(client):
    r = client.get("/api/admin/memoria/grupos")
    assert r.status_code == 401


def test_los_casi_duplicados_salen_juntos_y_marcados(client_superadmin,
                                                       casi_duplicados_de_prueba):
    """Spec §2.1: "estos tres dicen lo mismo". Si el agrupamiento no los
    junta, no sirve."""
    ids = set(casi_duplicados_de_prueba)
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    juntos = [g for g in grupos if ids <= set(g["hechos"])]
    assert juntos, f"los tres casi-duplicados quedaron en grupos distintos: {grupos}"
    assert any(ids <= set(d) for d in juntos[0]["casi_duplicados"]), \
        f"no quedaron marcados como casi-duplicados: {juntos[0]}"


def test_agrupar_no_bloquea_el_event_loop():
    """Spec §4: agrupar 116 hechos por similitud no puede bloquear el
    request. Con 116 es trivial; con 10.000 no. Se disena para el segundo
    caso: la funcion es async y no usa nada bloqueante."""
    import inspect
    fuente = inspect.getsource(memoria.agrupar_por_tema)
    assert "await" in fuente
    for bloqueante in ("time.sleep", "requests.", "subprocess.run"):
        assert bloqueante not in fuente


async def _explain(sql_texto, args):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("EXPLAIN " + sql_texto, args)
            return await cur.fetchall()


def test_el_agrupamiento_usa_el_indice_vectorial(client_superadmin):
    """Antecedente de esta casa (2026-09-11, jax_memory/messages): el HNSW
    existia y un JOIN de mas lo descartaba en silencio -- 58,5 ms contra
    0,4 ms con 1.149 filas. Este test mira el EXPLAIN de la consulta que
    agrupar_por_tema() corre DE VERDAD para buscar vecinos
    (`memoria.SQL_VECINOS`/`memoria.ARGS_VECINOS_EJEMPLO`), no una parecida
    escrita a mano."""
    plan = client_superadmin.portal.call(_explain, memoria.SQL_VECINOS,
                                          memoria.ARGS_VECINOS_EJEMPLO)
    texto = " ".join(str(c) for fila in plan for c in fila)
    assert "idx_embedding_bge_m3" in texto, f"no usa el indice vectorial: {texto}"
    assert "Using filesort" not in texto, f"ordena en memoria: {texto}"
    assert "Using temporary" not in texto, f"tabla temporal: {texto}"


# --- rendimiento del chequeo de casi-duplicados (2026-09-20) -------------------------------
#
# Medido a 10.000 hechos: `_casi_duplicados_del_grupo` se llevaba el 61 % del
# request entero -- 94.695 distancias coseno a 47,4 us cada una = 4,12 s. NO era
# el N+1 de vecinos, que es el 27 %. Dos causas, las dos dentro de esta funcion:
# el producto punto en Python puro, y la NORMA de cada vector recomputada en
# cada par (en un grupo de 90, la norma de un vector se recalculaba 89 veces).

def _grupo_sintetico(n: int, dim: int = 8) -> list:
    """n miembros con la forma que espera `_casi_duplicados_del_grupo`:
    (id, fact_text, is_verified, created_at, vector_texto)."""
    import random
    r = random.Random(20260920)
    return [(i, f"hecho {i}", True, None,
             json.dumps([r.random() for _ in range(dim)])) for i in range(n)]


def test_la_norma_se_calcula_una_vez_por_vector_no_una_por_par(monkeypatch):
    """La comprobacion es DETERMINISTA (se cuentan las llamadas), no de reloj:
    un test de tiempo seria un flake y ademas no diria por que.

    Con 20 miembros hay 190 pares. Si la norma se calcula dentro del bucle,
    se llama 380 veces; si se calcula una vez por vector, 20."""
    miembros = _grupo_sintetico(20)
    llamadas = []
    original = memoria._norma
    monkeypatch.setattr(memoria, "_norma",
                        lambda v: (llamadas.append(1), original(v))[1])

    memoria._casi_duplicados_del_grupo(miembros)

    assert len(llamadas) == len(miembros), (
        f"{len(llamadas)} normas para {len(miembros)} vectores "
        f"({len(miembros)*(len(miembros)-1)//2} pares): se esta recomputando por par")


def test_la_distancia_rapida_da_LO_MISMO_que_la_formula_ingenua():
    """El arreglo es de velocidad, no de resultado. Si cambia un digito,
    cambian los grupos que ve Fernando."""
    import math as _m
    import random
    r = random.Random(7)
    for _ in range(50):
        a = [r.random() for _ in range(64)]
        b = [r.random() for _ in range(64)]
        ingenua = 1 - (sum(x * y for x, y in zip(a, b))
                       / (_m.sqrt(sum(x * x for x in a)) * _m.sqrt(sum(y * y for y in b))))
        assert memoria._distancia_coseno(a, b) == pytest.approx(ingenua, abs=1e-12)


def test_un_vector_en_ceros_no_rompe_ni_se_cuela():
    """Norma 0: distancia infinita, nunca un ZeroDivisionError ni un 0.0 falso
    (un 0.0 falso seria 'duplicado exacto')."""
    assert memoria._distancia_coseno([0.0] * 8, [1.0] * 8) == float("inf")
    assert memoria._distancia_coseno([1.0] * 8, [0.0] * 8) == float("inf")
