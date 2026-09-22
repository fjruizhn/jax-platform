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
import logging
import pathlib
from datetime import datetime

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
    junta, no sirve.

    Ronda 2026-09-22: cada cluster de `casi_duplicados` ahora es un objeto
    `{ids, superviviente_id}` (antes, una lista de ids a secas) -- el
    backend declara quien sobrevive (_elegir_superviviente), el frontend ya
    no lo adivina con "el primero de la lista"."""
    ids = set(casi_duplicados_de_prueba)
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    juntos = [g for g in grupos if ids <= set(g["hechos"])]
    assert juntos, f"los tres casi-duplicados quedaron en grupos distintos: {grupos}"
    cluster = next((d for d in juntos[0]["casi_duplicados"] if ids <= set(d["ids"])), None)
    assert cluster, f"no quedaron marcados como casi-duplicados: {juntos[0]}"
    assert cluster["superviviente_id"] in ids


def test_el_superviviente_del_casi_duplicado_es_el_verificado_aunque_sea_mas_viejo(
        client_superadmin, casi_duplicados_de_prueba):
    """Decision de Fernando (2026-09-22): el verificado gana al mas reciente.

    `created_at` es un `TIMESTAMP` de precision de SEGUNDO (verificado con
    SHOW COLUMNS): sembrar los tres hechos de la fixture uno tras otro en el
    mismo test casi siempre los deja con el MISMO segundo -- un control que
    dependiera de ese orden natural no fallaria con la mutacion "siempre gana
    el mas reciente" (medido: escapo sin este ajuste). Por eso acá se fuerzan
    tres `created_at` bien separados a mano, y se verifica que el mas viejo
    de los TRES (y el unico verificado) sigue ganando al mas nuevo."""
    ids = casi_duplicados_de_prueba
    mas_viejo, medio, mas_nuevo = ids
    client_superadmin.portal.call(
        sql, "UPDATE facts SET created_at = '2020-01-01 00:00:00' WHERE id = %s", (mas_viejo,))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET created_at = '2022-01-01 00:00:00' WHERE id = %s", (medio,))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET created_at = '2026-09-22 00:00:00' WHERE id = %s", (mas_nuevo,))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET is_verified = TRUE WHERE id = %s", (mas_viejo,))
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    cluster = next(d for d in grupo["casi_duplicados"] if set(ids) <= set(d["ids"]))
    assert cluster["superviviente_id"] == mas_viejo, (
        f"el verificado (id {mas_viejo}, el mas viejo de los tres) tenia que "
        f"ganar al mas reciente ({mas_nuevo}): {cluster}")


def test_no_propone_fundir_una_sintesis_con_su_propia_fuente(
        client_superadmin, casi_duplicados_de_prueba):
    """El hallazgo real de esta ronda: una sintesis construida a partir de un
    hecho no puede salir marcada como casi-duplicado DE ESE MISMO hecho --
    "fundir en el mas reciente" aprobaria la sintesis (con partes
    inventadas) y superaria al hecho fuente, aunque estuviera verificado."""
    ids = casi_duplicados_de_prueba
    fuente, sintesis = ids[0], ids[1]
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_fact_ids = %s WHERE id = %s",
        (json.dumps([fuente]), sintesis))
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    for cluster in grupo["casi_duplicados"]:
        assert not ({fuente, sintesis} <= set(cluster["ids"])), (
            f"la sintesis {sintesis} quedo agrupada con su propia fuente {fuente}: {cluster}")


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


def test_la_consulta_de_activos_sigue_usando_el_indice_tras_agregar_source_fact_ids(client_superadmin):
    """LAS CUATRO DEL RENDIMIENTO, regla 1 (indexing): `SQL_ACTIVOS_CON_
    VECTOR` (la consulta que trae las filas del detector de casi-duplicados,
    ronda 2026-09-22) gano una columna (`source_fact_ids`) para poder excluir
    sintesis de sus propias fuentes -- se verifica con EXPLAIN, sobre el SQL
    real, que agregar una columna al SELECT no saco a la consulta de
    `idx_facts_active` ni metio un filesort/temporal nuevos."""
    plan = client_superadmin.portal.call(_explain, memoria.SQL_ACTIVOS_CON_VECTOR, ())
    texto = " ".join(str(c) for fila in plan for c in fila)
    assert "idx_facts_active" in texto, f"no usa el indice de superseded_by: {texto}"
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


def _casi_duplicados_ingenuo(miembros: list) -> list[list[int]]:
    """La implementación de ANTES del arreglo, tal cual, como referencia.

    Vive en el repo a propósito: la afirmación "el arreglo no cambia el
    resultado" no vale si la comparación se corrió una vez en un scratchpad y
    no la puede repetir nadie -- ni CI ni un auditor.
    """
    import math as _m
    if len(miembros) < 2 or len(miembros) > memoria._MAX_MIEMBROS_CASI_DUPLICADO:
        return []
    vectores = {m[0]: json.loads(m[4]) for m in miembros}
    ids = list(vectores)
    uf = memoria._UnionFind(ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = vectores[ids[i]], vectores[ids[j]]
            punto = sum(x * y for x, y in zip(a, b))
            na = _m.sqrt(sum(x * x for x in a))
            nb = _m.sqrt(sum(y * y for y in b))
            d = float("inf") if na == 0 or nb == 0 else 1 - punto / (na * nb)
            if d <= memoria._UMBRAL_MISMO_TEMA:
                uf.unir(ids[i], ids[j])
    return [sorted(c) for c in uf.componentes() if len(c) > 1]


def _grupo_con_clusters(n: int, semilla: int, dim: int = 128,
                        escalas: bool = False) -> list:
    """Miembros repartidos a distintas distancias del umbral, para que el
    resultado NO sea trivial (ni todos juntos ni todos separados).

    `escalas=True` les da además NORMAS muy distintas entre sí. Hace falta
    para el centinela del cableado de normas: los embeddings reales son casi
    unitarios, así que con normas todas ~1 confundir la de `a` con la de `b`
    no cambia el resultado y el centinela no discriminaría nada.
    """
    import math as _m
    import random
    r = random.Random(semilla)
    centro = [r.gauss(0, 1) for _ in range(dim)]
    nc = _m.sqrt(sum(x * x for x in centro))
    centro = [x / nc for x in centro]
    miembros = []
    for i in range(n):
        t = r.choice([0.002, 0.01, 0.05, 0.9])  # cerca, medio, lejos del umbral
        v = [centro[k] * _m.sqrt(1 - t) + r.gauss(0, 1) * _m.sqrt(t) / _m.sqrt(dim)
             for k in range(dim)]
        if escalas:
            factor = r.choice([0.01, 1.0, 7.0, 250.0])
            v = [x * factor for x in v]
        miembros.append((i, f"hecho {i}", True, None, json.dumps(v)))
    return miembros


@pytest.mark.parametrize("n, semilla, escalas", [
    (2, 1, False), (5, 2, False), (17, 3, False), (40, 4, False), (90, 5, False),
    (25, 6, True), (60, 7, True),  # con normas muy dispares entre si
])
def test_el_camino_de_produccion_da_LO_MISMO_que_la_implementacion_vieja(n, semilla, escalas):
    """Compara el RESULTADO de `_casi_duplicados_del_grupo` -- el camino de 4
    argumentos, el único que corre en producción -- contra la implementación
    anterior al arreglo.

    **Por qué existe.** Los otros tests miran la firma de DOS argumentos y
    CUÁNTAS veces se llama `_norma`. Ninguno miraba el cableado
    `normas[a], normas[b]`. Una auditoría adversarial lo demostró mutando ese
    cableado a `normas[a], normas[a]`: los 28 tests seguían en verde con los
    grupos saliendo mal. Un control que no falla con el defecto presente no
    valida nada.
    """
    miembros = _grupo_con_clusters(n, semilla, escalas=escalas)
    assert memoria._casi_duplicados_del_grupo(miembros) == _casi_duplicados_ingenuo(miembros)


def test_la_norma_de_cada_vector_es_la_SUYA(monkeypatch):
    """Centinela directo del cableado: si se le pasa la norma de otro vector,
    la distancia cambia. Con normas cruzadas a propósito, el resultado NO puede
    coincidir con el ingenuo."""
    # `escalas=True`: con normas todas ~1 el cruce no cambiaria nada y el
    # centinela seria de los que nunca fallan.
    miembros = _grupo_con_clusters(30, semilla=11, escalas=True)
    correcto = memoria._casi_duplicados_del_grupo(miembros)

    original = memoria._distancia_coseno
    monkeypatch.setattr(memoria, "_distancia_coseno",
                        lambda a, b, na=None, nb=None: original(a, b, na, na))
    cruzado = memoria._casi_duplicados_del_grupo(miembros)

    assert cruzado != correcto, (
        "usar la norma de `a` como norma de `b` dio el MISMO resultado: "
        "este grupo no discrimina y el centinela no sirve")


# --- Ronda 2026-09-22: una sintesis no se agrupa con su propia fuente ------
#
# Hallazgo real de Fernando en la pantalla de Memoria: #160 (extractor,
# verificado) y #161 (sintesis de #160 y otros seis) salian marcados como
# casi-duplicados. "Fundir en el mas reciente" habria aprobado #161 (con
# partes inventadas por el sintetizador) y SUPERADO a #160, ya verificado.
#
# `miembros` gana un SEXTO elemento opcional (source_fact_ids, JSON o None) a
# proposito al FINAL de la tupla -- las tuplas de 5 elementos que usan los
# tests de arriba (_grupo_sintetico, _grupo_con_clusters) siguen sin tocar,
# sin source_fact_ids, y su comportamiento no cambia (ver
# test_el_camino_de_produccion_da_LO_MISMO_que_la_implementacion_vieja, que
# sigue en verde con tuplas de 5).

def _con_vector_identico(*definiciones):
    """definiciones: (id, es_sintesis_de_ids_o_None). Los tres miembros
    comparten el MISMO vector (distancia 0) para que el union-find los una
    de entrada -- lo que hay que probar es que la exclusion los separa
    DESPUES, no que la distancia los una."""
    v = json.dumps([1.0, 0.0, 0.0, 0.0])
    return [
        (mid, f"hecho {mid}", True, None, v,
         json.dumps(list(fuentes)) if fuentes else None)
        for mid, fuentes in definiciones
    ]


def test_una_sintesis_no_se_agrupa_con_su_propia_fuente():
    miembros = _con_vector_identico((160, None), (161, [160]))
    assert memoria._casi_duplicados_del_grupo(miembros) == []


def test_caso_de_tres_fuente_sintesis_y_tercero_cercano_a_ambos():
    """El caso de guardia que el brief pide explicitamente: A (fuente) y B
    (sintesis de A) no pueden salir juntos, ni siquiera conectados via un
    tercer miembro C que si esta cerca de los dos."""
    miembros = _con_vector_identico((160, None), (161, [160]), (162, None))
    grupos = memoria._casi_duplicados_del_grupo(miembros)
    for g in grupos:
        assert not ({160, 161} <= set(g)), f"fuente y sintesis quedaron juntas: {g}"


def test_source_fact_ids_ilegible_se_trata_como_vacio_y_se_registra(caplog):
    """Un dato de trazabilidad roto no bloquea el agrupamiento (Principio
    VIII: un 'no se pudo leer' honesto, no un fundir que se cuelga) -- pero
    tampoco se ignora en silencio: queda un WARNING con el fact_id."""
    v = json.dumps([1.0, 0.0, 0.0, 0.0])
    a = (1, "a", True, None, v, "{esto no es JSON valido")
    b = (2, "b", True, None, v, None)
    with caplog.at_level(logging.WARNING):
        grupos = memoria._casi_duplicados_del_grupo([a, b])
    assert grupos == [[1, 2]], "un source_fact_ids ilegible no puede bloquear la agrupacion"
    avisos = [r.message for r in caplog.records if "source_fact_ids" in r.message]
    assert avisos and "1" in avisos[0], f"no se registro el dato malo: {caplog.records}"


def test_elegir_superviviente_el_verificado_gana_aunque_sea_mas_viejo():
    info = {
        1: (False, datetime(2026, 9, 20)),
        2: (True, datetime(2026, 9, 1)),
        3: (False, datetime(2026, 9, 22)),
    }
    assert memoria._elegir_superviviente([1, 2, 3], info) == 2


def test_elegir_superviviente_sin_ninguno_verificado_gana_el_mas_reciente():
    info = {1: (False, datetime(2026, 9, 20)), 2: (False, datetime(2026, 9, 22))}
    assert memoria._elegir_superviviente([1, 2], info) == 2


def test_elegir_superviviente_con_dos_verificados_gana_el_mas_reciente_de_esos():
    info = {
        1: (True, datetime(2026, 9, 1)),
        2: (True, datetime(2026, 9, 10)),
        3: (False, datetime(2026, 9, 22)),
    }
    assert memoria._elegir_superviviente([1, 2, 3], info) == 2
