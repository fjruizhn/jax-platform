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
- La vista `estado=discarded` termina las consultas y cálculos de costo dentro
  de `pool.acquire()`, libera la conexión, y sólo después espera el límite de
  gobernanza F2-B/F2-C/F2-D. La regresión usa un pool espía y prueba tanto la
  liberación previa como la forma completa del DTO; no modifica el lote F2-B,
  cuya decisión arquitectónica sigue separada.
- Se mantuvieron los pisos más altos de `master`; no se conservaron los valores de la
  rama (`3646` y `2218`) porque bajar un mínimo medido ocultaría regresiones.
- No se debilitó el gate de paridad ni se alteró el flujo F2-E. Cambiar o excluir el test
  habría ocultado la divergencia publicada en JAX en lugar de resolverla.
