# F2-E structured projection — reconciliación de Platform

**Fecha:** 2026-10-05

**Rama:** `phase2/f2e-structured-projection-platform-20261004`
**Ámbito:** Platform PR #190; sin cambios de SR2, producción, Faro, F2-F ni F2-G.

## Qué se hizo

- Se integró `origin/master` `64b6d1ff706d4d200e757460531c102676b8105a` en el commit
  `205efc40b3e8f474ae4491a3a3f02cfd70e061e1`. El único conflicto fue
  `.github/workflows/policy.yml`; se conservaron los pisos medidos de master:
  `PISO_PASSED = 3671` y `JAX_CI_MIN_PASSED = "2219"`.
- Se actualizaron los cuatro pines exactos de JAX en `policy.yml` al SHA publicado de
  JAX #351: `c2c0aa4d58128ef62333972ce72ce41257b8a1de`.
- Se restauró la paridad E2a en la única allowlist de Platform para documentos de
  proyecto: `apng`, `jpe`, `jfif`, `mpo` y `gif` se sumaron a
  `EXTENSIONES_ACEPTADAS` y a su aserción explícita de regresión.

## Por qué

`test_extensiones_aceptadas_coinciden_con_la_compuerta_de_jax` extrae por AST la lista
canónica `EXTENSIONES_IMAGEN` y sus alias de JAX. Contra el SHA publicado, fallaba con
esas cinco extensiones presentes en JAX y ausentes en Platform. El gate exacto de
igualdad y el mutante que rechaza una adición unilateral siguen vigentes.

## Verificación

- RED: el test de paridad contra
  `/home/fruiz/worktrees/f2e-structured-projection-jax` en `c2c0aa4` falló mostrando
  exactamente `apng`, `jpe`, `jfif`, `mpo` y `gif` como ausentes en Platform.
- GREEN focal: allowlist, paridad AST y mutante: `3 passed`.
- F2-E exact pair contra el mismo JAX: `46 passed in 1.10s`; cubre el productor
  autenticado, F2-B, proyección F2-C, bytes/lifecycle F2-D y clones de esquema/triggers.
- Se verificó estáticamente que `policy.yml` contiene los cuatro pines nuevos y ningún
  pin anterior `840579839d37821440b8018edb99fb074e540c66`.
- GitHub Actions, corrida `37283413689` sobre
  `23fd7915b86c5cb9dcad8672a7f306dabe3574c8`, confirmó los dos suites genéricos:
  con DB `colectados=3679 passed=3678 skipped=1 failed=0 errors=0`; sin DB
  `colectados=3679 passed=2226 skipped=1453 failed=0 errors=0`. Los pisos se
  elevaron a `3678` y `2226`; el gate con DB conserva `MAX_SKIPS = 1`.

## Pendiente

- Requiere auditoría independiente de escalón 3 sobre el SHA final antes de cualquier
  integración.

## Carga de descartados — evidencia operacional

**Fecha:** 2026-10-05. **Fuente:** `loadtest/_descartados_resultados.json` y
la limpieza informada por el orquestador. El proceso medido arrancó con JAX
`e5919f152361d4e91144ae1a55bd47cab28e8150` y Platform
`23253ece9b4a5a204918d09dc8cf0d2387838d67`. Los cambios posteriores de
validación nullable, workflow y pisos no estuvieron en el camino caliente de
esta carga; estos resultados no se atribuyen al par exacto posterior.

- Siembra: escala `5,000` pipelines, extremo `5,003`, `5,000` eventos y
  `2,020` eventos de ruido. La limpieza final borró `15,005` pipelines y tres
  usuarios; quedaron cero residuales.
- Camino gobernado escala, `GET /api/pipelines`: c=1 `11.63 rps`, p95
  `101.67 ms`; c=50 `12.23 rps`, p95 `4331.45 ms`, sin errores. La primera
  degradación fue c=100: `1996/2000` respuestas correctas, p95 `11023.84 ms`;
  c=200 terminó `1538/2000`.
- Camino descartados: la primera degradación fue c=100, con `1981/2000`
  respuestas correctas. Los listados administrativos y la auditoría no
  tuvieron errores hasta c=200: admin `282.18 rps`, p95 `3628.02 ms`; auditoría
  `256.79 rps`, p95 `3601.10 ms`.
- **Smoke posterior del par exacto:** tras el registro anterior se ejecutó
  `CARGA_RAPIDA=1` con JAX
  `d9bf5ce947826e53e2b6f0088cc580eef2d307aa` y Platform
  `29ec83c278f01dec3f5068941a8aa37188a06824`. El arnés aislado completó c=1
  sin errores ni respuestas no 200 en los siete caminos: escala activo
  `200/200` (p95 `86.74 ms`), escala descartados `200/200` (p95 `91.22 ms`),
  extremo activo `200/200` (p95 `11.83 ms`), extremo descartados `200/200`
  (p95 `94.02 ms`), admin, auditoría y auditoría con ruido. Es una confirmación
  funcional del par exacto, no una sustitución ni reinterpretación de la carga
  completa anterior.

## Decisiones y alternativas

- El consumidor Platform ahora prepara todos los argumentos cerrados
  `{"pipeline_id", "status"}` y solicita una sola vez
  `JacobsPipelineStatusResolver.evidence_many(...)`. Conserva después una
  resolución, recibo, referencia y claim independientes por fila: el lote
  reduce las lecturas canónicas, sin convertir el estado del productor en
  autoridad. La API batch de JAX quedó publicada en
  `3f2393fae56564d02123892c68cb9eda13d233ca`; su resultado mantiene el orden
  y la cardinalidad de los argumentos.
- Platform falla cerrado antes de construir candidato si el lote no es una
  tupla de igual cardinalidad, si hay IDs de pipeline duplicados en el
  productor, o si una evidencia no está ligada al `ResponseScope` de la
  respuesta. La resolución individual también rechaza una evidencia fuera de
  orden porque sus argumentos canónicos no coinciden. La regresión verifica
  una sola lectura batch, recibos/claims por cada fila, y los cuatro fallos
  cerrados (cardinalidad, orden, alcance e ID duplicado).
- Verificación focal posterior: `backend/tests/test_governed_pipeline_list.py`
  pasó `11`; `backend/tests/test_historial_pipelines.py` pasó `15`, ambos con
  el checkout JAX que contiene la API batch y el esquema cerrado compatible.
- Los cuatro pines exactos de JAX en `policy.yml` avanzaron desde
  `c2c0aa4d58128ef62333972ce72ce41257b8a1de` a la cabeza final publicada
  `0c925e63ee28e62bbc96975cbf807773d209b147`. El job exacto F2-E se midió
  contra esa cabeza con `53 passed, 0 skipped`, por lo que su piso subió de
  `46` a `53`. Los pisos genéricos permanecen en `3678` con DB y `2226` sin
  DB, y se conserva `MAX_SKIPS = 1`.
- Se corrigió el caso de una página autenticada vacía: Platform conserva la
  proyección F2-C y los bytes F2-D, pero no solicita evidencia canónica cuando
  no hay filas, porque el contrato batch de JAX admite de uno a cincuenta IDs.
  Las dos formas válidas (activa y descartada) producen `200` sin claims,
  recibos ni lecturas de estado. También se cierran antes del resolvedor una
  clave superior desconocida, `pipelines` ausente, o `pipelines` que no es una
  lista.
- El commit de Platform bloquea la reentrada de transporte: sólo
  `OUTPUT_PREPARED` puede pasar a `TRANSPORT_COMMITTING`; un resultado
  `OUTPUT_COMMITTED_TO_TRANSPORT` o `TRANSPORT_OUTCOME_UNKNOWN` es terminal y
  no vuelve a enviar bytes. Los cuatro pines exactos avanzaron a JAX publicado
  `e5919f152361d4e91144ae1a55bd47cab28e8150`. Los pisos medidos se mantienen
  hasta una nueva corrida CI.
- El arnés de carga de descartados conserva ahora el `JAX_REPO_PATH` explícito
  del operador después de leer `/etc/jax/.env`: exige un checkout JAX absoluto
  existente y verifica en `/proc/<pid>/environ` que el backend real recibió la
  misma ruta, además de la base de prueba y el Jacobs falso. El hallazgo fue
  que el import del entorno de producción reemplazaba el checkout de la rama
  por `/srv/jax-prod/jax`, causando `governed_output_unavailable`; la carga no
  se ejecuta si falta, no es absoluto, no existe o no tiene la forma de un
  checkout JAX.
- La suite exacta F2-E pasó `58` sin skips contra
  `e5919f152361d4e91144ae1a55bd47cab28e8150`; su piso se elevó de `53` a
  `58` por las cinco regresiones no omitidas del cierre C. Los pisos genéricos
  y `MAX_SKIPS` no cambiaron.
- JAX corrigió la proyección de `descartado_at=NULL` real en páginas posteriores
  de descartados. Platform no cambió la paginación pública: avanzó sus cuatro
  pines a `d9bf5ce947826e53e2b6f0088cc580eef2d307aa` y añadió
  `test_pipelines_descartados_cursor.py` al par exacto. La medición conjunta
  dio `89 passed, 0 skipped`, por lo que el piso exacto subió de `58` a `89`;
  los pisos genéricos continúan `3678`/`2226` hasta CI verde.
- La corrida CI verde `37291824110`, sobre
  `1157f71aa172fad07581c68c27706ce5a9e275a0`, aplicó Rule 5 y reemplazó los
  pisos genéricos ya obsoletos: con DB `colectados=3691, passed=3690,
  skipped=1` y sin DB `colectados=3691, passed=2238, skipped=1453`, ambos sin
  fallos ni errores. `policy.yml` eleva los mínimos a `3690` y `2238`; conserva
  `MAX_SKIPS=1` y los cuatro pines JAX `d9bf5ce947826e53e2b6f0088cc580eef2d307aa`.
- La vista `estado=discarded` termina las consultas y cálculos de costo dentro
  de `pool.acquire()`, libera la conexión, y sólo después espera el límite de
  gobernanza F2-B/F2-C/F2-D. La regresión usa un pool espía y prueba tanto la
  liberación previa como la forma completa del DTO; no modifica el lote F2-B,
  cuya decisión arquitectónica sigue separada.
- Se mantuvieron los pisos más altos de `master`; no se conservaron los valores de la
  rama (`3646` y `2218`) porque bajar un mínimo medido ocultaría regresiones.
- No se debilitó el gate de paridad ni se alteró el flujo F2-E. Cambiar o excluir el test
  habría ocultado la divergencia publicada en JAX en lugar de resolverla.
