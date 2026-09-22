"""Siembra del PEOR CASO para la carga de la pantalla de Memoria (Task 7,
docs/superpowers/plans/2026-09-20-memoria-admin.md). 10.000 hechos, no los
116 de hoy -- el grupo mas grande, el usuario con mas hechos, una proporcion
realista de casi-duplicados, y hechos vencidos y superados mezclados.

BARRERA DURA: escribe SOLO contra `BASE_DE_PRUEBA` (una base PROPIA, clonada
por esquema desde jax_memory_test con `mysqldump --no-data`, jamas
jax_memory ni la jax_memory_test compartida por el resto de la suite). Si
`SELECT DATABASE()` no coincide, no escribe una sola fila.

EMBEDDINGS SINTETICOS PERO COHERENTES (JAX_OLLAMA_URL apunta a un host
invalido a proposito en los tests de esta casa): cada hecho activo se genera
como blend(centroide_del_tema, vector_al_azar) en la esfera unidad de 1024
dimensiones (bge-m3). Con esa construccion, dist_coseno(centroide, miembro)
= 1 - sqrt(1-t): t chico (0.01-0.04) da distancia ~0.01-0.02 (por debajo de
DUP_DISTANCE_THRESHOLD=0.05 -- "casi duplicado exacto"); t medio (0.05-0.20)
da distancia ~0.03-0.11 (por debajo de CORRECTION_DISTANCE_THRESHOLD=0.25 --
"mismo tema, otra redaccion"); dos centroides al azar en 1024 dimensiones
caen a distancia ~1.0 (separacion total). Verificado a mano antes de sembrar
(ver sesion de carga, 2026-09-20): centroide-tight 0.008-0.018,
centroide-loose 0.04-0.10, tight-tight 0.025, loose-loose 0.14,
cross-cluster 1.008 -- el hueco que separa "mismo tema" de "temas distintos"
se sostiene.

USO:
    python3 loadtest/memoria_seed.py <base_de_prueba> loadtest/_memoria_seed_result.json

Requiere que la base ya tenga tenant_id=1 (la crea `db/seed.run_seed()` al
arrancar el backend contra esa base) y el esquema de `facts` con
`verified_by`/`superseded_by_user`/`idx_facts_revision` ya migrado (lo trae
el clon de jax_memory_test). Requiere `sudo -n cat /etc/jax/.env` para las
credenciales de conexion.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta

import numpy as np
import pymysql

DIM = 1024
N_TOTAL = 10_000
N_SUPERSEDED = 500       # correcciones: hecho viejo reemplazado por uno nuevo
N_EXPIRED_PASADO = 500   # vencidos (excluidos por defecto de /hechos y de /grupos)
N_EXPIRED_FUTURO = 200   # con vence_at fijado pero AUN vigente (variedad de UI)
FRACCION_VERIFICADA = 0.10   # skew real: casi todo espera revision (spec: "115 esperan revision")
N_USUARIOS_EXTRA = 40
PESO_USUARIO_PESADO = 0.30   # "el usuario con mas hechos": 30% de los 10.000

# MAJOR A (revision adversarial de jax-platform PR 146, ronda 4): la siembra
# de carga NUNCA ponia `source_fact_ids`/`source_facet='synthesis'` en NINGUN
# hecho -- SQL_CITAS (backend/api/admin/memoria.py) devolvia 0 filas contra
# esta base, así que el costo de MAJOR 1/`_cierre_transitivo_de_citas` nunca
# se medía de verdad (docs/carga-memoria-146-2026-09-22.md lo afirmaba con
# 0 síntesis sembradas -- falso, corregido esta ronda). "Medir cuántas hay en
# la base de TEST clonada; si no, 10%" (decisión del brief): la base de test
# de esta sesión está vacía (0 facts, medido con una consulta ad-hoc vía
# pytest antes de escribir esto, nunca contra jax_memory) -- se usa el piso
# de 10% del brief.
# Los dos overridables por entorno -- para poder sembrar DOS bases
# comparables (con y sin síntesis) con el MISMO script, sin bifurcarlo.
# Puestos en 0 los dos, la siembra reproduce el comportamiento de antes de
# esta ronda (ninguna fila con `source_fact_ids`).
FRACCION_SINTESIS = float(os.environ.get("MEMORIA_SEED_FRACCION_SINTESIS", "0.10"))
N_CADENAS_SINTESIS = int(os.environ.get("MEMORIA_SEED_N_CADENAS_SINTESIS", "200"))


def _cargar_env_produccion() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _tamanos_de_clusters(rng: np.random.Generator, n_total: int) -> list[int]:
    """El peor caso: UN grupo enorme (el mas grande), un puñado cerca del
    tope de _MAX_MIEMBROS_CASI_DUPLICADO (90, api/admin/memoria.py) para
    ejercitar la verificacion O(m^2) de casi-duplicados, y una cola larga de
    grupos chicos/pares de casi-duplicados -- la forma real de una memoria
    acumulada, no una distribucion pareja."""
    tamanos = [600] + [85] * 20  # mega-grupo + 20 grupos cerca del tope (2.300)
    resto = n_total - sum(tamanos)
    while resto > 0:
        tam = min(resto, int(rng.integers(2, 31)))
        tamanos.append(tam)
        resto -= tam
    return tamanos


def _vector_miembro(rng: np.random.Generator, centroide: np.ndarray, t: float) -> np.ndarray:
    r = _unit(rng.normal(size=DIM))
    v = np.sqrt(1 - t) * centroide + np.sqrt(t) * r
    return _unit(v)


# Plantillas de texto -- variedad tematica reconocible de este ecosistema,
# para que la pantalla se vea real (no lorem ipsum) al mirarla en el
# navegador. El contenido no importa para la medicion: lo que importa es el
# embedding.
_SUJETOS = [
    "hall9000", "atem-ai", "Sesamo", "la base jax_memory", "el pool de conexiones",
    "el indice HNSW de facts", "la cola de revision", "el backup 3-2-1",
    "el router Omada", "la VM dev", "el servicio jax-las-manos", "el ejecutor",
    "el tablero de pipelines", "la pantalla de Memoria", "el auditor local",
    "la faceta hyde", "la faceta thot", "el arbitro de disputas", "R2 Bucket Lock",
    "el certificado del controlador", "la migracion de embeddings", "el spool de uso",
]
_PLANTILLAS = [
    "{sujeto} corre en {detalle}",
    "{sujeto} se configuro con {detalle}",
    "La decision sobre {sujeto} fue {detalle}",
    "{sujeto} depende de {detalle}",
    "Se midio {sujeto} contra {detalle}",
    "Preferencia registrada: {sujeto} usa {detalle}",
    "{sujeto} quedo documentado en {detalle}",
]
_DETALLES = [
    "el puerto 3308", "MariaDB 12.3.3", "un indice compuesto", "credenciales en /etc/jax/.env",
    "un umbral de 0.25", "la base de tests", "un semaforo de concurrencia",
    "un EXPLAIN verificado", "la migracion Task 1", "el pool de 10 conexiones",
    "una ventana propia (sin confirm del navegador)", "el i18n de es.js/en.js",
    "un respaldo restaurado", "el checkout de produccion separado",
]


def _texto_de_hecho(rng: np.random.Generator, cluster_idx: int) -> str:
    sujeto = _SUJETOS[cluster_idx % len(_SUJETOS)]
    plantilla = _PLANTILLAS[int(rng.integers(0, len(_PLANTILLAS)))]
    detalle = _DETALLES[int(rng.integers(0, len(_DETALLES)))]
    return plantilla.format(sujeto=sujeto, detalle=detalle)


FACT_TYPES = ["user", "technical", "social", "preference", "project", "financial"]


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("uso: memoria_seed.py <base_de_prueba> <ruta-de-salida.json>")
    base_de_prueba = sys.argv[1]
    salida_path = sys.argv[2]

    env = _cargar_env_produccion()
    conn = pymysql.connect(
        host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        database=base_de_prueba, autocommit=False, charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    if db != base_de_prueba:
        raise RuntimeError(f"conectado a {db!r}, no a {base_de_prueba!r} -- ABORTANDO sin escribir nada.")
    if db in ("jax_memory", "jax_memory_test"):
        raise RuntimeError(f"{db!r} es una base compartida/de produccion -- ABORTANDO.")
    print(f"conectado a {db}", file=sys.stderr)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jax_tenants WHERE tenant_id = 1")
        (n_tenant,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM facts")
        (n_facts_previos,) = cur.fetchone()
    if n_tenant == 0:
        raise RuntimeError(
            "tenant_id=1 no existe -- arrancar primero el backend contra esta base "
            "(run_seed() lo crea junto con el superadmin user_id=1)."
        )
    if n_facts_previos != 0:
        raise RuntimeError(
            f"facts ya tiene {n_facts_previos} filas en {db!r} -- esta base ya fue "
            "sembrada. Este script no re-siembra sobre datos existentes."
        )

    rng = np.random.default_rng(20260920)

    # --- usuarios: uno "pesado" (30% de los hechos) + N_USUARIOS_EXTRA mas ---
    usuarios = []
    with conn.cursor() as cur:
        for i in range(N_USUARIOS_EXTRA):
            email = f"carga-memoria-{i:03d}-{uuid.uuid4().hex[:8]}@example.invalid"
            cur.execute(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
                "VALUES (1, %s, 'x', 'operator', 'active', 0)",
                (email,),
            )
            usuarios.append(cur.lastrowid)
    conn.commit()
    usuario_pesado = usuarios[0]
    print(f"{len(usuarios)} usuarios operator creados; usuario_pesado={usuario_pesado}", file=sys.stderr)

    pesos = np.full(len(usuarios), (1 - PESO_USUARIO_PESADO) / (len(usuarios) - 1))
    pesos[0] = PESO_USUARIO_PESADO
    asignacion_usuario = rng.choice(usuarios, size=N_TOTAL, p=pesos)

    # --- clusters tematicos (embeddings coherentes) ---
    tamanos = _tamanos_de_clusters(rng, N_TOTAL)
    print(f"{len(tamanos)} clusters, mayor={max(tamanos)}, total={sum(tamanos)}", file=sys.stderr)
    assert sum(tamanos) == N_TOTAL

    filas = []  # (fact_uuid, texto, tipo, embedding_json, user_id)
    cluster_del_indice = []
    idx_mega_grupo = None
    for cluster_idx, tam in enumerate(tamanos):
        centroide = _unit(rng.normal(size=DIM))
        if tam == max(tamanos) and idx_mega_grupo is None:
            idx_mega_grupo = cluster_idx
        for _ in range(tam):
            tight = rng.random() < 0.3
            t = float(rng.uniform(0.01, 0.04)) if tight else float(rng.uniform(0.05, 0.20))
            v = _vector_miembro(rng, centroide, t)
            texto = _texto_de_hecho(rng, cluster_idx)
            tipo = FACT_TYPES[int(rng.integers(0, len(FACT_TYPES)))]
            uid = int(asignacion_usuario[len(filas)])
            filas.append((str(uuid.uuid4()), texto, tipo, json.dumps(v.tolist()), uid))
            cluster_del_indice.append(cluster_idx)

    print(f"{len(filas)} vectores generados, insertando en lotes...", file=sys.stderr)
    t0 = time.time()
    ids = []
    LOTE = 250
    with conn.cursor() as cur:
        for i in range(0, len(filas), LOTE):
            lote = filas[i:i + LOTE]
            cur.executemany(
                "INSERT INTO facts (fact_uuid, fact_text, fact_type, confidence, "
                "is_verified, user_id, importance, embedding_bge_m3) "
                "VALUES (%s, %s, %s, 0.9, FALSE, %s, 3, VEC_FromText(%s))",
                [(fu, txt, tipo, uid, emb) for (fu, txt, tipo, emb, uid) in lote],
            )
            conn.commit()
            primero = cur.lastrowid - len(lote) + 1
            ids.extend(range(primero, primero + len(lote)))
            if (i // LOTE) % 10 == 0:
                print(f"  {i + len(lote)}/{len(filas)}", file=sys.stderr)
    print(f"{len(ids)} facts insertados en {time.time() - t0:.1f}s "
          f"(ids {min(ids)}..{max(ids)})", file=sys.stderr)

    # --- overlay: verificados (10%, autor=1 el superadmin) ---
    n_verificados = int(N_TOTAL * FRACCION_VERIFICADA)
    ids_np = np.array(ids)
    rng.shuffle(ids_np)
    ids_verificados = ids_np[:n_verificados].tolist()
    resto = ids_np[n_verificados:]
    with conn.cursor() as cur:
        for i in range(0, len(ids_verificados), 500):
            lote = ids_verificados[i:i + 500]
            marcadores = ",".join(["%s"] * len(lote))
            cur.execute(
                f"UPDATE facts SET is_verified = TRUE, verified_at = NOW(), verified_by = 1 "
                f"WHERE id IN ({marcadores})",
                lote,
            )
    conn.commit()
    print(f"{len(ids_verificados)} marcados verificados (verified_by=1)", file=sys.stderr)

    # --- overlay: superados (correcciones) -- old apunta a new, disjuntos ---
    ids_superseded_old = resto[:N_SUPERSEDED].tolist()
    ids_superseded_new = resto[N_SUPERSEDED:N_SUPERSEDED * 2].tolist()
    resto2 = resto[N_SUPERSEDED * 2:]
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE facts SET superseded_by = %s, superseded_at = NOW(), superseded_by_user = 1 "
            "WHERE id = %s",
            list(zip(ids_superseded_new, ids_superseded_old)),
        )
    conn.commit()
    print(f"{len(ids_superseded_old)} marcados superados (superseded_by -> otro hecho)", file=sys.stderr)

    # --- overlay: vencidos (pasado) y con vencimiento futuro ---
    ids_vencidos = resto2[:N_EXPIRED_PASADO].tolist()
    ids_futuro = resto2[N_EXPIRED_PASADO:N_EXPIRED_PASADO + N_EXPIRED_FUTURO].tolist()
    ahora = datetime.now()
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE facts SET expires_at = %s WHERE id = %s",
            [((ahora - timedelta(days=int(d))).strftime("%Y-%m-%d %H:%M:%S"), int(fid))
             for fid, d in zip(ids_vencidos, rng.integers(1, 200, size=len(ids_vencidos)))],
        )
        cur.executemany(
            "UPDATE facts SET expires_at = %s WHERE id = %s",
            [((ahora + timedelta(days=int(d))).strftime("%Y-%m-%d %H:%M:%S"), int(fid))
             for fid, d in zip(ids_futuro, rng.integers(1, 200, size=len(ids_futuro)))],
        )
    conn.commit()
    print(f"{len(ids_vencidos)} vencidos (pasado), {len(ids_futuro)} con vencimiento futuro", file=sys.stderr)

    # --- overlay: síntesis (MAJOR A, ronda 4) -- proporción realista +
    # peor caso de cadenas de 2do/3er orden. `pool` es una COPIA de
    # `ids_np` (nunca el array en sí): `resto`/`resto2`, arriba, son VISTAS
    # de `ids_np` (slicing de numpy no copia) -- barajar `ids_np` de nuevo
    # acá mutaría esas vistas en el lugar y correría el riesgo de que un
    # id ya usado como "vencido" o "superado" cambiara de posición en
    # `resto`/`resto2` a mitad de la siembra. Marcar un hecho como síntesis
    # no tiene ningún conflicto con que ADEMÁS esté verificado, superado o
    # vencido -- las categorías son ortogonales, por eso no hace falta
    # excluir nada del pool.
    pool = ids_np.copy()
    rng.shuffle(pool)
    n_sintesis_plana = int(N_TOTAL * FRACCION_SINTESIS)
    pool_sintesis_plana = pool[:n_sintesis_plana]
    with conn.cursor() as cur:
        lote = []
        for sid in pool_sintesis_plana.tolist():
            citado = int(rng.choice(pool))
            while citado == sid:
                citado = int(rng.choice(pool))
            lote.append(("synthesis", json.dumps([citado]), sid))
        for i in range(0, len(lote), 500):
            cur.executemany(
                "UPDATE facts SET source_facet = %s, source_fact_ids = %s WHERE id = %s",
                lote[i:i + 500],
            )
    conn.commit()
    print(f"{len(pool_sintesis_plana)} marcados síntesis (cita plana, un solo salto -- "
          f"{FRACCION_SINTESIS:.0%} de {N_TOTAL})", file=sys.stderr)

    # Peor caso: cadenas de 3 (base NO síntesis -> S2 cita a base -> S3 cita
    # a S2) -- ejercita el cierre TRANSITIVO (`_cierre_transitivo_de_citas`,
    # MAJOR 1) a profundidad 2, no sólo la cita directa de un salto.
    pool_cadenas = pool[n_sintesis_plana:n_sintesis_plana + N_CADENAS_SINTESIS * 3]
    with conn.cursor() as cur:
        lote = []
        n_cadenas_reales = len(pool_cadenas) // 3
        for i in range(n_cadenas_reales):
            base, s2, s3 = (int(x) for x in pool_cadenas[i * 3:i * 3 + 3])
            lote.append(("synthesis", json.dumps([base]), s2))
            lote.append(("synthesis", json.dumps([s2]), s3))
        for i in range(0, len(lote), 500):
            cur.executemany(
                "UPDATE facts SET source_facet = %s, source_fact_ids = %s WHERE id = %s",
                lote[i:i + 500],
            )
    conn.commit()
    print(f"{n_cadenas_reales} cadenas de síntesis de 2do/3er orden sembradas "
          f"({len(lote)} filas síntesis encadenadas)", file=sys.stderr)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM facts")
        (total,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM facts WHERE is_verified = FALSE")
        (n_no_verificados,) = cur.fetchone()

    ids_grupo_mas_grande = ids_np[[i for i, c in enumerate(cluster_del_indice) if c == idx_mega_grupo]].tolist() \
        if idx_mega_grupo is not None else []

    resultado = {
        "base": base_de_prueba,
        "n_total": total,
        "n_no_verificados": n_no_verificados,
        "n_verificados": len(ids_verificados),
        "n_superseded": len(ids_superseded_old),
        "n_vencidos_pasado": len(ids_vencidos),
        "n_con_vencimiento_futuro": len(ids_futuro),
        "usuario_pesado": usuario_pesado,
        "n_usuarios": len(usuarios),
        "n_clusters": len(tamanos),
        "tamano_grupo_mas_grande": max(tamanos),
        "grupo_mas_grande_facts_sample": (ids_grupo_mas_grande[:5] if ids_grupo_mas_grande else []),
        # MAJOR A (ronda 4): antes SIEMPRE 0 -- SQL_CITAS devolvía 0 filas.
        "n_sintesis_plana": len(pool_sintesis_plana),
        "n_cadenas_sintesis": n_cadenas_reales,
        "n_filas_con_source_fact_ids": len(pool_sintesis_plana) + len(lote),
    }
    with open(salida_path, "w") as f:
        json.dump(resultado, f, indent=2)
    print(json.dumps(resultado, indent=2), file=sys.stderr)
    print("SEED OK", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
