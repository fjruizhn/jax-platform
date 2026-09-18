# Carga: historial de pipelines — lista y detalle (2026-09-18)

Rama `feat/historial-de-pipelines` (jax-platform). Task 10, LAS CUATRO DEL
RENDIMIENTO #4: sin número medido no hay GO. Registrado por Mr. Hyde. Todas
las horas en CST (hall9000), corrida ~09:56–09:58 CST. Los números salen de
las corridas; ninguno es estimado.

**Herramienta (ronda de arreglo 1, 2026-09-18): `loadtest/historial_orquestar.py`.**
Reproduce esta corrida completa — siembra el peor caso, levanta Jacobs falso
y el backend real, mide, limpia — con un solo comando desde la raíz del
repo: `python3 loadtest/historial_orquestar.py`. Las constantes del peor
caso (600 pipelines, 50 abortados, 2.860.000 filas de relleno, 6 pasos de
80.000 caracteres, niveles de concurrencia 1/25/50/100/150/200) están arriba
de ese archivo, no enterradas en el cuerpo; el generador de datos vive en
`loadtest/historial_seed.py` y la limpieza en `loadtest/historial_limpiar.py`
(esta última corre SIEMPRE al terminar, incluso si la carga revienta a mitad
de camino). El script se niega a arrancar si detecta que apunta a
`jax_memory`, o a los puertos de producción `:7777`/`:8080` — ver
`_verificar_no_apunta_a_produccion()`. Los números de este documento son los
de la corrida original; volver a correr el script no los reemplaza sin
actualizar este archivo con fecha nueva (una medición vieja es una VERDAD
OPERACIONAL caducada).

## Qué se midió y por qué

- `GET /api/pipelines` — el listado del historial (Task 7/7b: trae
  `duracion_s`, `costo_usd` real cruzado con `axioma_usage.pipeline_id`, y
  `causa` para los pipelines detenidos).
- `GET /api/pipelines/{id}/results` — el detalle (proxy real a Jacobs vía
  `JACOBS_URL`, con `_require_pipeline_owner` corriendo contra la base en
  cada pedido).

**Peor caso, no el feliz**, explícito para evitar la trampa de U5 (un p95 de
2,99 ms que medía a FastAPI devolviendo un literal, no el servicio):

- Usuario con **600 pipelines**, no tres. Los 50 más recientes (`ORDER BY
  created_at DESC`, así que caen en la primera página, sin pedir offset)
  están `aborted`: la página que se mide es la que dispara la consulta de
  causa (`sql_eventos_de_causa`) para las 50 y la de costo
  (`sql_costo_por_pipeline`) para las 50 — el camino más caro del endpoint,
  no el vacío.
- `jacobs_events`: **11.050 eventos** para esos 50 pipelines (200 de relleno
  + 20 `STEP_FAILED` + 1 `PIPELINE_ABORTED` por pipeline), igual que la
  ronda anterior (2026-09-17), para poder comparar antes/después del índice.
- `axioma_usage` con **2.866.218 filas** (5.018 propias de la base de tests +
  2.860.000 de relleno insertadas para esta corrida, set-based con el motor
  `SEQUENCE` de MariaDB) — misma escala que la ronda anterior (el
  `AUTO_INCREMENT` venía en 2.864.195, señal de que esa ronda había llegado
  a un volumen parecido).
- El detalle: pipeline con **6 pasos reales**, cada uno con `prompt_completo`
  y `salida_completa` de **80.000 caracteres** (~20.000 tokens a 4
  caracteres/token), `modelo_real` y `dependencias` — el "pipeline con más
  pasos y más salida" que pide el brief, no un pipeline de juguete.
- Concurrencia: c=1, 25, 50, 100, 150, 200 (25 es la que fijaron las rondas
  anteriores de este proyecto; se subió más para encontrar el punto de
  degradación, que el brief pide explícito).

## Método de aislamiento

- Base `jax_memory_test` (nunca `jax_memory`) — verificado con `SELECT
  DATABASE()` antes de escribir una fila y con `/proc/<pid>/environ` del
  proceso real después de levantarlo (`JAX_DB_NAME=jax_memory_test`,
  `JACOBS_URL=http://127.0.0.1:17777/jacobs`). Servicios de producción
  `:7777` y `:8080` (usuario `jaxsvc`) sin tocar — confirmado con `ss -ltnp`
  antes y después de la corrida.
- Jacobs falso propio en `127.0.0.1:17777` (HTTP real,
  `ThreadingHTTPServer`, no un stub en proceso): contesta `GET
  /jacobs/pipeline/{id}/results` con el cuerpo grande de arriba y exige la
  misma credencial de servicio (`X-Jax-Credencial-Servicio`) que exige LAS
  MANOS real. Lo que se mide es el costo de la Mesa (auth con relectura de
  sesión, dueño, proxy, saneo de la respuesta) — mismo criterio que
  `jacobs_falso.py` de la suite y que la ronda de carga anterior.
- Backend real del worktree (`main:app`), un solo proceso uvicorn en
  `127.0.0.1:18080` — la MISMA topología de proceso único que
  `jax-platform.service` en producción (sin `--workers`), así que el número
  no es optimista por tener más procesos que el real.
- Aislado además de la base: `JAX_USAGE_SPOOL_DIR`, `JAX_FACET_SEAL_PATH`,
  `JAX_KILL_SWITCH_PATH`, `JAX_EJECUTOR_PAUSA`/`JAX_EJECUTOR_PYTHON` (runner
  inexistente, no puede lanzar nada real), `JAX_MISSIONS_DIR`,
  `JAX_REPO_BASE`, `JAX_ADJUNTOS_DIR`, `JAX_PROXY_CARRIL_RAIZ` — todos en
  directorios temporales de la corrida, ninguno toca `/srv/jax-data/...` ni
  `/etc/jax/interruptor`. `CANARY_INTERVAL_SECONDS=0` (sin sondas pagas).
- Cliente de carga: `httpx.AsyncClient` real contra el puerto TCP (no
  `ASGITransport` en el mismo proceso) — HTTP de punta a punta, igual que un
  navegador.
- Usuario descartable (`tenant_id=1`, rol `operator`) creado en `jax_users`
  y token JWT firmado con el mismo secreto que usa producción
  (`JAX_JWT_SECRET`, solo contra esta instancia aislada).
- Al terminar: `os.killpg` sobre los dos procesos, puertos 17777/18080
  verificados libres con `ss -ltnp`; toda fila sembrada (pipelines, eventos,
  uso real y de relleno, usuario) BORRADA y verificada — `axioma_usage`
  volvió exacto a 5.018 filas, mismo número que antes de sembrar.

## Verificación de que se midió el servicio real, no un literal

Antes de la tanda de carga, una petición de control a cada endpoint,
impresa con su tamaño de cuerpo y su contenido:

```
GET /api/pipelines: status=200 bytes=22781 pipelines=50 has_more=True
  con_causa=50 con_costo_usd=50 costo_usd[0]=0.091356
GET /api/pipelines/{id}/results: status=200 bytes=961242 pasos=6
  modelo_real[0]=modelo-real-paso-0-2026-09
  len(prompt_completo[0])=80000 len(salida_completa[0])=80000
  dependencias[5]=[0, 1, 2, 3, 4]
```

`costo_usd=0.091356` es la suma real de las dos filas de `axioma_usage`
sembradas para ese pipeline (2 × 0,045678) — no un valor inventado. Los 50
pipelines de la página traen `causa` (el fallo detenido) y `costo_usd`: es
el peor caso, no el vacío. El detalle trae los 6 pasos con prompt y salida
de 80.000 caracteres cada uno y las dependencias reales — 961.242 bytes de
cuerpo, no un JSON de tres campos.

## Hallazgo: el índice compuesto de causa YA existe y se usa

La ronda anterior (2026-09-17) dejó pendiente re-medir cuando existiera
`idx_events_pipeline_tipo (pipeline_id, event_type)` — sin él, la consulta
de causa examinaba 11.050 filas para devolver 1.050 (r_filtered 9,5 %). Hoy
el índice YA está en el esquema de `jax_memory_test` y el `EXPLAIN` de la
consulta real (`sql_eventos_de_causa(50)`, contra los 50 pipelines
abortados de esta corrida) lo confirma:

```
EXPLAIN SELECT pipeline_id, id, event_type, payload FROM jacobs_events
WHERE pipeline_id IN (...50 ids...) AND event_type IN (...5 tipos...)

table=jacobs_events  type=range  possible_keys=idx_events_pipeline,idx_events_pipeline_tipo
key=idx_events_pipeline_tipo  key_len=348  rows=500  Extra=Using index condition
```

Sin filesort ni temporary. El plan pasó de escanear 11.050 filas a un rango
sobre el índice compuesto — la VERDAD OPERACIONAL pendiente de la ronda
anterior queda **cerrada**: el arreglo (repo jax, plan J/R20) está
desplegado en el esquema que usa esta base.

La consulta de costo (`sql_costo_por_pipeline(50)`) también corre limpia:

```
table=axioma_usage  type=range  key=idx_axioma_usage_pipeline
key_len=147  rows=100  Extra=Using index condition
```

Sin filesort ni temporary, con la tabla en 2.866.218 filas.

## Resultados — `GET /api/pipelines` (peor caso: 50/página, todos con causa y costo)

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 0 | 208,50 | 4,68 | 5,42 | 6,02 | 8,09 |
| 25 | 500 | 500 | 0 | 265,94 | 92,61 | 133,73 | 174,18 | 238,92 |
| 50 | 1000 | 1000 | 0 | 254,39 | 158,20 | 466,51 | 661,93 | 901,02 |
| 100 | 2000 | 2000 | 0 | 264,67 | 349,02 | 1009,90 | 1353,70 | 2074,13 |
| 150 | 2000 | 2000 | 0 | 260,59 | 452,98 | 1583,65 | 2141,47 | 3934,50 |
| 200 | 2000 | 2000 | 0 | 262,38 | 530,02 | 1846,01 | 2593,15 | 4305,64 |

Cuerpo: 22.781 bytes en TODAS las peticiones (50 pipelines, `has_more=true`,
50 con `causa`, 50 con `costo_usd`) — sin variación, confirma que ninguna
petición cayó en un camino más corto.

## Resultados — `GET /api/pipelines/{id}/results` (peor caso: 6 pasos, ~80.000 caracteres c/u)

| c | n | ok | errores | rps | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|---|---|---|---|
| 1 | 100 | 100 | 0 | 192,88 | 5,02 | 6,02 | 6,81 | 8,12 |
| 25 | 250 | 250 | 0 | 186,62 | 82,99 | 93,08 | 102,72 | 1084,93 |
| 50 | 500 | 500 | 0 | 223,44 | 167,63 | 216,68 | 1176,01 | 1271,43 |
| 100 | 1000 | 1000 | 0 | 217,11 | 344,09 | 622,14 | 1384,88 | 1610,61 |
| 150 | 1000 | 1000 | 0 | 226,50 | 474,51 | 1065,39 | 1527,28 | 2404,25 |
| 200 | 1000 | 1000 | 0 | 226,59 | 647,01 | 1382,54 | 1956,55 | 2509,16 |

Cuerpo: 961.242 bytes en TODAS las peticiones (6 pasos, prompt y salida
completos) — sin variación.

## Lectura: dónde empieza a degradarse

**0 errores en los dos endpoints, en las seis concurrencias.** No hay un
punto donde el servicio empiece a rechazar o caerse dentro de lo medido
(hasta c=200). Lo que sí degrada, con claridad, es la latencia de cola:

- El **rps satura ya en c=25** y no vuelve a subir con más concurrencia:
  ~260-265 rps en la lista, ~185-227 rps en el detalle, prácticamente
  planos de c=25 a c=200. Es la firma de un cuello de un solo proceso: un
  único worker uvicorn (la misma topología que `jax-platform.service` en
  producción, sin `--workers`) satura su capacidad de cómputo — armar el
  JSON de 22 KB con causa+costo por cada una de 50 filas, o parsear y
  re-serializar 961 KB de Jacobs — y todo pedido de más se pone en cola.
- Con el rps plano, el **p95 crece casi lineal con la concurrencia**: en la
  lista pasa de 5 ms (c=1) a 134 ms (c=25) a 467 ms (c=50) a 1.010 ms
  (c=100); en el detalle de 6 ms a 93 ms a 217 ms a 622 ms en los mismos
  puntos. Ahí está el costo real de "un solo proceso": lo que un usuario ve
  como demora es tiempo de cola detrás del proceso único, no trabajo por
  pedido (c=1 sigue por debajo de los 10 ms en los dos endpoints).
- **Umbral práctico** (p95 < 500 ms, un valor conservador de referencia, no
  un contrato): la lista lo cruza entre c=25 (134 ms) y c=50 (467 ms); el
  detalle recién lo cruza entre c=50 (217 ms) y c=100 (622 ms) — el detalle
  aguanta un poco más de concurrencia con p95 bajo, aunque su cuerpo es 40×
  más pesado, porque el trabajo de la lista (3 consultas + agregación en
  Python de causa/costo para 50 filas) pesa más por pedido que un proxy con
  poco trabajo propio.
- **Con 25 usuarios simultáneos ya hay cola visible** (p95 20-25× el de
  c=1); con 50 la cola es la mayoría de la latencia; de 100 en adelante el
  p95 supera el segundo en los dos endpoints. Para el volumen de usuarios
  simultáneos reales de la plataforma hoy, esto no es un bloqueo, pero es el
  número — no una suposición — del que depende decidir si hace falta más de
  un worker uvicorn cuando la concurrencia real se acerque a esa zona.

## VERDAD OPERACIONAL pendiente

- Estos números son de UN proceso uvicorn contra un Jacobs falso instantáneo
  para la parte que él sirve (el `/results` de Jacobs real puede tener su
  propio costo de armar la respuesta, no medido acá — lo que se midió es el
  costo de la Mesa: auth, dueño, proxy, saneo).
- Si cambia el esquema, el volumen de datos, la cantidad de workers de
  producción, o la infraestructura, este número caduca y hay que volver a
  medir (LAS CUATRO DEL RENDIMIENTO, política de vigencia).
- No se midió `OFFSET` profundo (paginación más allá de la primera página)
  ni el listado con pipelines corriendo activamente (`_poll_pipelines`) —
  fuera del alcance de esta ronda, que se enfocó en el peor caso que pide el
  brief (más pasos, más salida, más pipelines, c=25 y más).

En memoria de Jairo Urbina.
