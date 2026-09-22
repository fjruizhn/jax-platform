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


def test_el_cluster_trae_superviviente_verificado_y_texto(
        client_superadmin, casi_duplicados_de_prueba):
    """D5 (revision adversarial de jax-platform PR 146, MAYOR 3): el frontend
    arma el motivo ("sobrevive el verificado" / "sobrevive el más reciente")
    y el texto de la ficha con estos DOS campos -- no con `hechosPorId`,
    que solo tiene los 500 hechos mas recientes que carga GET /hechos.
    `superviviente_verificado` tiene que ser un bool JSON real (no 0/1
    crudo de MariaDB), y `superviviente_texto` el fact_text real."""
    ids = casi_duplicados_de_prueba
    verificado_id = ids[0]
    client_superadmin.portal.call(
        sql, "UPDATE facts SET is_verified = TRUE WHERE id = %s", (verificado_id,))
    texto_esperado = client_superadmin.portal.call(
        sql, "SELECT fact_text FROM facts WHERE id = %s", (verificado_id,), True)[0][0]
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    cluster = next(d for d in grupo["casi_duplicados"] if set(ids) <= set(d["ids"]))
    assert cluster["superviviente_id"] == verificado_id
    assert cluster["superviviente_verificado"] is True
    assert cluster["superviviente_texto"] == texto_esperado


def test_recortar_texto_no_toca_lo_corto():
    corto = "un hecho normal, sin recorte"
    assert memoria._recortar_texto(corto) == corto


def test_recortar_texto_corta_y_agrega_puntos_suspensivos():
    """M4 (revision adversarial de jax-platform PR 146, tercera vuelta): sin
    recorte, una redaccion larga y sin espacios podia desbordar la ventana
    de ConfirmacionSuma (frontend/src/components/ConfirmacionSuma.jsx,
    break-words). 280 caracteres, con '…' al final."""
    largo = "a" * 400
    recortado = memoria._recortar_texto(largo)
    assert len(recortado) == memoria._MAX_CARACTERES_TEXTO_SUPERVIVIENTE + 1  # +1 por el '…'
    assert recortado.endswith("…")
    assert recortado[:-1] == largo[:memoria._MAX_CARACTERES_TEXTO_SUPERVIVIENTE]


def test_superviviente_texto_llega_recortado_end_to_end(client_superadmin, casi_duplicados_de_prueba):
    """El recorte se aplica tambien en el camino real (GET /grupos), no solo
    en la funcion pura."""
    ids = casi_duplicados_de_prueba
    texto_largo = "palabra" * 60  # 420 caracteres, sin espacios
    client_superadmin.portal.call(
        sql, "UPDATE facts SET fact_text = %s WHERE id = %s", (texto_largo, ids[0]))
    client_superadmin.portal.call(
        sql, "UPDATE facts SET is_verified = TRUE WHERE id = %s", (ids[0],))
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    cluster = next(d for d in grupo["casi_duplicados"] if set(ids) <= set(d["ids"]))
    assert cluster["superviviente_id"] == ids[0]
    assert len(cluster["superviviente_texto"]) == memoria._MAX_CARACTERES_TEXTO_SUPERVIVIENTE + 1
    assert cluster["superviviente_texto"].endswith("…")


def test_created_at_null_no_revienta_el_agrupamiento(client_superadmin, casi_duplicados_de_prueba):
    """D8 (sospecha del revisor): un `created_at` NULL en un miembro del
    grupo no puede tirar un 500 -- ni en el `sorted()` de `_construir_grupos`
    ni en `_elegir_superviviente`. Extremo real: TODOS con NULL (nadie tiene
    fecha), el peor caso para un `sorted()` que compare None contra None."""
    ids = casi_duplicados_de_prueba
    for fid in ids:
        client_superadmin.portal.call(
            sql, "UPDATE facts SET created_at = NULL WHERE id = %s", (fid,))
    r = client_superadmin.get("/api/admin/memoria/grupos")
    assert r.status_code == 200, f"created_at NULL revento el endpoint: {r.text}"
    grupos = r.json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    cluster = next(d for d in grupo["casi_duplicados"] if set(ids) <= set(d["ids"]))
    assert cluster["superviviente_id"] in ids


def test_no_propone_fundir_una_sintesis_con_su_propia_fuente(
        client_superadmin, casi_duplicados_de_prueba):
    """El hallazgo real de esta ronda: una sintesis construida a partir de un
    hecho no puede salir marcada como casi-duplicado DE ESE MISMO hecho --
    "fundir en el mas reciente" aprobaria la sintesis (con partes
    inventadas) y superaria al hecho fuente, aunque estuviera verificado.

    Ronda 146 (D1): marca AL MENOS `source_facet='synthesis'`, que es el
    criterio primario desde esta ronda -- `source_fact_ids` solo (sin la
    faceta) ya no dispara la exclusion, a proposito (ver
    test_sintesis_con_source_fact_ids_null_igual_se_separa_de_no_sintesis y
    el docstring de `_tipo_sintesis`)."""
    ids = casi_duplicados_de_prueba
    fuente, sintesis = ids[0], ids[1]
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_facet = 'synthesis', source_fact_ids = %s WHERE id = %s",
        (json.dumps([fuente]), sintesis))
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    for cluster in grupo["casi_duplicados"]:
        assert not ({fuente, sintesis} <= set(cluster["ids"])), (
            f"la sintesis {sintesis} quedo agrupada con su propia fuente {fuente}: {cluster}")


def test_no_propone_fundir_una_sintesis_con_source_facet_solo_sin_source_fact_ids(
        client_superadmin, casi_duplicados_de_prueba):
    """D1, el caso que motivo el cambio de criterio: una sintesis SIN
    `source_fact_ids` (NULL, dato de trazabilidad perdido) tiene que seguir
    excluida de los no-sintesis del grupo -- la exclusion es por
    `source_facet`, no depende de que la cadena de ids este completa."""
    ids = casi_duplicados_de_prueba
    sintesis = ids[1]
    otros = [i for i in ids if i != sintesis]
    client_superadmin.portal.call(
        sql, "UPDATE facts SET source_facet = 'synthesis', source_fact_ids = NULL WHERE id = %s",
        (sintesis,))
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    grupo = next(g for g in grupos if set(ids) <= set(g["hechos"]))
    for cluster in grupo["casi_duplicados"]:
        assert not (({sintesis} | set(otros)) <= set(cluster["ids"])), (
            f"la sintesis sin source_fact_ids quedo agrupada con los no-sintesis: {cluster}")


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


def test_la_consulta_de_activos_sigue_usando_el_indice_tras_agregar_source_facet(client_superadmin):
    """LAS CUATRO DEL RENDIMIENTO, regla 1 (indexing): `SQL_ACTIVOS_CON_
    VECTOR` (la consulta que trae las filas del detector de casi-duplicados)
    gano una columna (`source_facet`, ronda 2026-09-22) -- se verifica con
    EXPLAIN, sobre el SQL real, que agregar una columna al SELECT no saco a
    la consulta de `idx_facts_active` ni metio un filesort/temporal nuevos.
    (Tercera vuelta: `source_fact_ids` SALIO de esta consulta -- ver
    test_sql_citas_no_tiene_indice_util_pero_el_costo_es_chico, mas abajo,
    para la consulta que la reemplaza.)"""
    plan = client_superadmin.portal.call(_explain, memoria.SQL_ACTIVOS_CON_VECTOR, ())
    texto = " ".join(str(c) for fila in plan for c in fila)
    assert "idx_facts_active" in texto, f"no usa el indice de superseded_by: {texto}"
    assert "Using filesort" not in texto, f"ordena en memoria: {texto}"
    assert "Using temporary" not in texto, f"tabla temporal: {texto}"


def test_sql_citas_no_tiene_indice_util_pero_el_costo_es_chico(client_superadmin):
    """MAJOR 1a (revision adversarial de jax-platform PR 146, tercera
    vuelta): `SQL_CITAS` (`SELECT id, source_fact_ids FROM facts WHERE
    source_fact_ids IS NOT NULL`) alimenta el cierre transitivo de citas.
    `source_fact_ids` es `longtext` SIN indice (verificado con `SHOW INDEX
    FROM facts` contra jax_memory_test) -- EXPLAIN tiene que dar `type=ALL`
    (full scan), y esto NO es un defecto a esconder: se deja escrito acá, tal
    como pide LAS CUATRO DEL RENDIMIENTO #1 ("buscar Using filesort/Using
    temporary... y si hay trabajo, decirlo"). El costo absoluto (pocas filas
    reales -- sólo las síntesis tienen `source_fact_ids`) se midió aparte,
    contra la base de carga de 10.000 filas: ver
    docs/carga-memoria-146-2026-09-22.md."""
    plan = client_superadmin.portal.call(_explain, memoria.SQL_CITAS, ())
    texto = " ".join(str(c) for fila in plan for c in fila)
    assert "ALL" in texto, (
        f"se esperaba un full scan (sin indice util para source_fact_ids IS NOT NULL): {texto}")


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

    memoria._casi_duplicados_del_grupo(miembros, {})

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
    assert memoria._casi_duplicados_del_grupo(miembros, {}) == _casi_duplicados_ingenuo(miembros)


def test_la_norma_de_cada_vector_es_la_SUYA(monkeypatch):
    """Centinela directo del cableado: si se le pasa la norma de otro vector,
    la distancia cambia. Con normas cruzadas a propósito, el resultado NO puede
    coincidir con el ingenuo."""
    # `escalas=True`: con normas todas ~1 el cruce no cambiaria nada y el
    # centinela seria de los que nunca fallan.
    miembros = _grupo_con_clusters(30, semilla=11, escalas=True)
    correcto = memoria._casi_duplicados_del_grupo(miembros, {})

    original = memoria._distancia_coseno
    monkeypatch.setattr(memoria, "_distancia_coseno",
                        lambda a, b, na=None, nb=None: original(a, b, na, na))
    cruzado = memoria._casi_duplicados_del_grupo(miembros, {})

    assert cruzado != correcto, (
        "usar la norma de `a` como norma de `b` dio el MISMO resultado: "
        "este grupo no discrimina y el centinela no sirve")


# --- Ronda 2026-09-22 -> 146: una sintesis no se agrupa con un no-sintesis ---
#
# Hallazgo real de Fernando en la pantalla de Memoria: #160 (extractor,
# verificado) y #161 (sintesis de #160 y otros seis) salian marcados como
# casi-duplicados. "Fundir en el mas reciente" habria aprobado #161 (con
# partes inventadas por el sintetizador) y SUPERADO a #160, ya verificado.
#
# Ronda 146 (revision adversarial de jax-platform PR 146, D1): la primera
# version de este arreglo (2026-09-22) solo miraba `source_fact_ids`, y una
# revision adversarial encontro que eso no cerraba sintesis de SEGUNDO orden,
# una fuente ya superada, ni una sintesis con `source_fact_ids` NULL. Ahora
# el criterio primario es `source_facet == 'synthesis'` (D1): sintesis y
# no-sintesis NUNCA comparten cluster, sin importar `source_fact_ids`. Dos
# sintesis SI pueden agruparse entre si, salvo que una cite a la otra (ahi
# el CIERRE TRANSITIVO de citas sigue siendo el criterio -- ver mas abajo,
# tercera vuelta).
#
# `miembros` gana un SEXTO elemento opcional (source_facet) al FINAL de la
# tupla -- las tuplas de 5 elementos que usan los tests de arriba
# (_grupo_sintetico, _grupo_con_clusters) siguen sin tocar, y su
# comportamiento no cambia (ver test_el_camino_de_produccion_da_LO_MISMO_
# que_la_implementacion_vieja, que sigue en verde con tuplas de 5).
#
# Tercera vuelta (MAJOR 1, revision adversarial de jax-platform PR 146): el
# SEPTIMO elemento (source_fact_ids) SALIO de la tupla de `miembros` -- el
# criterio de citas dejo de ser "lo que trae cada fila" y paso a ser el
# CIERRE TRANSITIVO de TODO el grafo (`memoria._cierre_transitivo_de_citas`),
# que se arma aparte con `_citas()` (mas abajo) y se pasa como segundo
# argumento a `_casi_duplicados_del_grupo`.

def _miembro(mid, vector, facet=None, verificado=True, creado=None):
    return (mid, f"hecho {mid}", verificado, creado, json.dumps(vector), facet)


def _con_vector_identico(*definiciones):
    """definiciones: (id, facet). Todos comparten el MISMO vector (distancia
    0) para que, sin la exclusion de tipo/cita, el union-find los uniria de
    entrada -- lo que hay que probar es que la exclusion los separa, no que
    la distancia los una."""
    v = [1.0, 0.0, 0.0, 0.0]
    return [_miembro(mid, v, facet=facet) for mid, facet in definiciones]


def _citas(directas: dict) -> dict:
    """directas: {id: [ids que cita]} -> el cierre transitivo real
    (memoria._cierre_transitivo_de_citas), NO una reimplementacion de test:
    si el algoritmo del cierre se rompe, este helper se rompe con el, en vez
    de esconder el defecto detras de una copia que nunca se desincroniza a
    proposito pero podria hacerlo por accidente."""
    return memoria._cierre_transitivo_de_citas({k: frozenset(v) for k, v in directas.items()})


def test_una_sintesis_no_se_agrupa_con_su_propia_fuente():
    miembros = _con_vector_identico((160, None), (161, "synthesis"))
    cierre = _citas({161: [160]})
    assert memoria._casi_duplicados_del_grupo(miembros, cierre) == []


def test_caso_de_tres_fuente_sintesis_y_tercero_cercano_a_ambos():
    """El caso de guardia que el brief pide explicitamente: A (fuente) y B
    (sintesis de A) no pueden salir juntos, ni siquiera conectados via un
    tercer miembro C que si esta cerca de los dos -- Y ADEMAS (D6, MENOR 7:
    "un control que no falla no valida nada") A y C, que SI son del mismo
    tipo y estan a distancia 0, tienen que seguir agrupados entre si: si la
    exclusion fuera "descartar el componente entero" en vez de bloquear SOLO
    la arista de tipo cruzado, este assert se caeria tambien -- acá NO hay
    par incompatible dentro de {160,162} (161 nunca entra a ese componente,
    por tipo), así que la revalidación final (MAJOR 1b) no tiene nada que
    objetar."""
    miembros = _con_vector_identico((160, None), (161, "synthesis"), (162, None))
    cierre = _citas({161: [160]})
    grupos = memoria._casi_duplicados_del_grupo(miembros, cierre)
    for g in grupos:
        assert not ({160, 161} <= set(g)), f"fuente y sintesis quedaron juntas: {g}"
    assert [160, 162] in grupos, (
        f"160 y 162 (mismo tipo, misma distancia) tenian que seguir agrupados: {grupos}")


def test_dos_sintesis_que_no_se_citan_entre_si_se_agrupan():
    """D1: "Dos sintesis entre si SI pueden agruparse" -- el bloqueo es por
    TIPO cruzado o por CITA, no por ser sintesis a secas. Este es el "grupo
    valido" que el brief pide explicitamente: SIGUE proponiendose."""
    miembros = _con_vector_identico((201, "synthesis"), (202, "synthesis"))
    assert memoria._casi_duplicados_del_grupo(miembros, {}) == [[201, 202]]


def test_dos_sintesis_que_se_citan_entre_si_no_se_agrupan():
    """D1, la excepcion explicita: dos sintesis SI se excluyen si una cita a
    la otra -- source_facet solo no alcanza para separar ESTE caso, porque
    las dos son 'synthesis'."""
    miembros = _con_vector_identico((201, "synthesis"), (202, "synthesis"))
    cierre = _citas({202: [201]})
    assert memoria._casi_duplicados_del_grupo(miembros, cierre) == []


def test_sintesis_de_segundo_orden_nunca_se_agrupa_con_la_fuente_original():
    """D1: el caso que la version basada solo en `source_fact_ids` DIRECTO no
    cerraba -- S2 (sintesis de S1) no cita a A en su PROPIO source_fact_ids
    (solo cita a S1). La faceta ya los separa (los tres tipos no coinciden:
    A no es sintesis), asi que esto verifica el caso simple; el caso donde
    la faceta NO alcanza (dos sintesis del MISMO tipo con cita indirecta) se
    prueba mas abajo (MAJOR 1, tercera vuelta)."""
    miembros = _con_vector_identico(
        (1, None),                 # A: fuente original, no-sintesis
        (2, "synthesis"),          # S1: sintesis de A
        (3, "synthesis"),          # S2: sintesis de S1 (NO cita a A directo)
    )
    cierre = _citas({2: [1], 3: [2]})
    grupos = memoria._casi_duplicados_del_grupo(miembros, cierre)
    for g in grupos:
        assert 1 not in g or not ({2, 3} & set(g)), (
            f"la fuente original quedo agrupada con una sintesis (directa o de 2do orden): {g}")


def test_sintesis_con_source_fact_ids_null_igual_se_separa_de_no_sintesis():
    """D1: el tercer caso que source_fact_ids solo no cerraba -- una sintesis
    SIN dato de trazabilidad (NULL, sin entrada en el cierre de citas) tiene
    que seguir excluida de los no-sintesis, porque la separacion primaria es
    por FACETA."""
    miembros = _con_vector_identico((1, None), (2, "synthesis"))
    assert memoria._casi_duplicados_del_grupo(miembros, {}) == []


def test_source_fact_ids_ilegible_se_trata_como_vacio_y_se_registra(caplog):
    """Un dato de trazabilidad roto no bloquea el agrupamiento (Principio
    VIII: un 'no se pudo leer' honesto, no un fundir que se cuelga) -- pero
    tampoco se ignora en silencio: queda un WARNING con el fact_id. Tercera
    vuelta: el parseo de `source_fact_ids` ilegible ahora ocurre al construir
    `citas_directas` (`_cargar_citas_directas`/`_citas` en este archivo), no
    dentro de `_casi_duplicados_del_grupo` -- se prueba directo contra
    `_cierre_transitivo_de_citas` pasando por `_parse_fuentes`, el mismo
    parser de siempre."""
    with caplog.at_level(logging.WARNING):
        cierre = memoria._cierre_transitivo_de_citas(
            {1: memoria._parse_fuentes("{esto no es JSON valido", 1), 2: frozenset()})
    v = [1.0, 0.0, 0.0, 0.0]
    miembros = [_miembro(1, v), _miembro(2, v)]
    grupos = memoria._casi_duplicados_del_grupo(miembros, cierre)
    assert grupos == [[1, 2]], "un source_fact_ids ilegible no puede bloquear la agrupacion"
    avisos = [r.message for r in caplog.records if "source_fact_ids" in r.message]
    assert avisos and "1" in avisos[0], f"no se registro el dato malo: {caplog.records}"


# --- D2 (MAYOR 1): recalcular componentes tras excluir, no reusar el previo ---
#
# El revisor demostro que la PRIMERA version de esta ronda (armar el
# componente por distancia y DESPUES sacar a los conflictivos) dejaba pares
# FALSOS: A-S y S-C cerca, A-C lejos -- sacar a S de {A,S,C} devolvia
# [[A,C]] aunque A y C NO esten cerca. Vectores unitarios en 2D, elegidos a
# mano y verificados con la formula real (no supuestos):
#   A=[1,0]  S=[0.8,0.6]  C=[0.28,0.96]
#   dist(A,S) = 1-0.8  = 0.20  (<= UMBRAL_MISMO_TEMA=0.25 -- puente valido)
#   dist(S,C) = 1-0.8  = 0.20  (<= 0.25 -- puente valido)
#   dist(A,C) = 1-0.28 = 0.72  (> 0.25 -- A y C NO son casi-duplicados)

def test_bridging_por_sintesis_no_produce_un_par_falso():
    a_vec, s_vec, c_vec = [1.0, 0.0], [0.8, 0.6], [0.28, 0.96]
    # Verificacion previa, sobre la formula real -- no un numero supuesto.
    assert memoria._distancia_coseno(a_vec, s_vec) == pytest.approx(0.20, abs=1e-9)
    assert memoria._distancia_coseno(s_vec, c_vec) == pytest.approx(0.20, abs=1e-9)
    assert memoria._distancia_coseno(a_vec, c_vec) == pytest.approx(0.72, abs=1e-9)

    miembros = [
        _miembro(1, a_vec, facet=None, verificado=True),        # A: fuente, verificada
        _miembro(2, s_vec, facet="synthesis"),                  # S: sintesis de A
        _miembro(3, c_vec, facet=None, verificado=False),       # C: no-sintesis, cerca de S
    ]
    cierre = _citas({2: [1]})
    grupos = memoria._casi_duplicados_del_grupo(miembros, cierre)
    assert [1, 3] not in grupos, (
        f"A y C quedaron agrupados via el puente de S, pese a estar a 0.72: {grupos}")
    assert not any({1, 3} <= set(g) for g in grupos), (
        f"A y C terminaron en el mismo componente por otra via: {grupos}")


# --- MAJOR 1, tercera vuelta (revision adversarial de jax-platform PR 146) --
#
# Caso reproducido por el revisor (scratchpad/repro.py): S2(#1, verificada),
# S3(#2, puente), S1(#3, cita a #1). Angulos 0/40/80 grados en 2D:
#   dist(1,2) = dist(2,3) = 1 - cos(40°) ≈ 0.234  (<= 0.25, puente valido)
#   dist(1,3) = 1 - cos(80°) ≈ 0.826  (> 0.25, MUY lejos)
# Antes de esta vuelta, `_construir_grupos` devolvia [[1,2,3]] -- el
# endpoint rechazaria SIEMPRE ese fundir con fundir_sintesis_con_no_sintesis
# (1 y 3 son incompatibles: 3 cita a 1), un grupo que el detector proponia
# pero que nunca se podia fundir. Fail-closed: el componente entero se
# descarta, no solo el par malo.

def _vector_angulo(grados: float) -> list:
    import math
    r = math.radians(grados)
    return [math.cos(r), math.sin(r)]


def test_caso_del_revisor_bridging_por_sintesis_mismo_tipo():
    v0, v40, v80 = _vector_angulo(0), _vector_angulo(40), _vector_angulo(80)
    assert memoria._distancia_coseno(v0, v40) == pytest.approx(0.2336, abs=1e-3)
    assert memoria._distancia_coseno(v40, v80) == pytest.approx(0.2336, abs=1e-3)
    assert memoria._distancia_coseno(v0, v80) == pytest.approx(0.8264, abs=1e-3)

    miembros = [
        _miembro(1, v0, facet="synthesis", verificado=True),
        _miembro(2, v40, facet="synthesis", verificado=False),
        _miembro(3, v80, facet="synthesis", verificado=False),
    ]
    cierre = _citas({3: [1]})  # S1 (#3) cita a S2 (#1) -- directo
    grupos = memoria._casi_duplicados_del_grupo(miembros, cierre)
    assert grupos == [], (
        f"el componente {{1,2,3}} tenia un par incompatible (1,3) y tenia que "
        f"descartarse ENTERO (fail-closed), no proponerse a medias: {grupos}")


def test_cita_de_segundo_grado_a_traves_de_un_puente_no_citado():
    """La cita es TRANSITIVA (MAJOR 1a): #12 cita a #11, #11 cita a #10 --
    #12 y #10 estan relacionados aunque NUNCA se citen directo. Un cuarto
    hecho (#13), sin relacion con ninguno, hace de puente espacial entre #10
    y #12 (que en si estan lejos) -- exactamente el patron de bridging que
    D2 cerro para tipos cruzados y que ahora tiene que cerrarse tambien para
    citas indirectas (MAJOR 1b: revalidacion final del componente)."""
    v0, v40, v80 = _vector_angulo(0), _vector_angulo(40), _vector_angulo(80)
    miembros = [
        _miembro(10, v0, facet="synthesis"),   # C: la fuente original
        _miembro(13, v40, facet="synthesis"),  # puente, sin relacion con nadie
        _miembro(12, v80, facet="synthesis"),  # A: cita a #11 (ausente del grupo), que cita a #10
    ]
    # #11 no esta en `miembros` (puede pertenecer a otro grupo, u otro tema)
    # pero SI tiene que entrar al grafo de citas -- por eso el cierre se
    # arma sobre TODO el grafo, no sobre los miembros de este grupo.
    cierre = _citas({11: [10], 12: [11]})
    grupos = memoria._casi_duplicados_del_grupo(miembros, cierre)
    assert not any({10, 12} <= set(g) for g in grupos), (
        f"#10 y #12 (relacionados por cita de 2do grado, via #11) quedaron "
        f"en el mismo cluster: {grupos}")


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


def test_elegir_superviviente_por_created_at_no_por_id_mas_grande(caplog):
    """D6 (MENOR 7): un control que solo probara con el id mas grande
    coincidiendo con el mas reciente no discrimina "gana el mas reciente" de
    "gana el id mayor". Aca el MAS RECIENTE tiene el id MENOR."""
    info = {999: (False, datetime(2020, 1, 1)), 5: (False, datetime(2026, 9, 22))}
    assert memoria._elegir_superviviente([999, 5], info) == 5, (
        "eligio por id en vez de por fecha")


def test_elegir_superviviente_empate_de_fecha_lo_desempata_el_id_mayor():
    """D4: mismo `created_at` (precision de segundo, empate real) -> gana el
    id MAYOR -- el insertado despues.

    El id mayor (50) se pasa DE ULTIMO en la lista, a proposito: `max()` con
    claves empatadas devuelve la PRIMERA que encuentra en orden de
    iteracion -- un desempate roto que sólo mirara `created_at` (sin el id)
    hubiera devuelto igual el primero de la lista (7) con las tres fechas
    iguales, y ese resultado NO puede confundirse con "gana el mayor" salvo
    que el mayor este first. Medido: con `[50, 7, 33]` (el mayor primero)
    este test seguia en verde mutando el desempate -- por eso el orden
    importa acá."""
    mismo_segundo = datetime(2026, 9, 22, 10, 30, 0)
    info = {50: (False, mismo_segundo), 7: (False, mismo_segundo), 33: (False, mismo_segundo)}
    assert memoria._elegir_superviviente([7, 33, 50], info) == 50


def test_elegir_superviviente_created_at_null_es_el_mas_antiguo_posible():
    """D8: `created_at` admite NULL -- `SHOW COLUMNS FROM facts` contra
    `jax_memory_test` (NUNCA producción) da `created_at timestamp YES ...`,
    verificado el 2026-09-22 en esta misma ronda. NULL nunca le gana a una
    fecha real."""
    info = {1: (False, None), 2: (False, datetime(2020, 1, 1))}
    assert memoria._elegir_superviviente([1, 2], info) == 2, (
        "un created_at NULL le gano a una fecha real")


def test_elegir_superviviente_todos_con_created_at_null_desempata_por_id():
    info = {3: (False, None), 9: (False, None)}
    assert memoria._elegir_superviviente([3, 9], info) == 9


def test_construir_grupos_tolera_created_at_null_mezclado_con_fecha_real():
    """M1 (revision adversarial de jax-platform PR 146, tercera vuelta): el
    `sorted()` de `_construir_grupos` tiene que tolerar un NULL MEZCLADO con
    una fecha real -- no sólo "todos NULL"
    (test_created_at_null_no_revienta_el_agrupamiento, mas abajo, con DB
    real). "Todos NULL" no ejercita la comparacion None-vs-fecha real:
    `sorted` nunca necesita invocar `<` entre dos `None` porque son iguales
    por `==` y el desempate cae directo al id -- un mutante que sacara el
    `datetime.min` (dejando `(f[3], f[0])` a secas) seguiria pasando ESE
    caso. Este test unitario (sin DB) reproduce el caso mixto que el
    revisor demostro en scratchpad/repro.py."""
    v0, v10 = [1.0, 0.0], [0.99, 0.14]
    filas = [
        (20, "x", False, None, json.dumps(v0), None),                       # sin fecha
        (21, "y", False, datetime(2026, 9, 1), json.dumps(v10), None),      # con fecha
    ]
    vecinos = [(20, [(21, 0.01)]), (21, [(20, 0.01)])]
    grupos = memoria._construir_grupos(filas, vecinos, {})
    assert len(grupos) == 1
    assert set(grupos[0]["hechos"]) == {20, 21}
