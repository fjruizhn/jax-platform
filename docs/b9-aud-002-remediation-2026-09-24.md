# Fase 1 / Bloque 9 — AUD-002, integración de Web Chat

**Fecha:** 2026-09-24. **Tipo:** HISTORIA y PENDIENTE. **Decisión:** Fernando autorizó remediar únicamente B9-AUD-001..007 y ejecutar después un targeted recheck; no autorizó merge ni despliegue.

## Qué se hizo y por qué

- JAX serializa el contenido ordinario de memoria como dato dentro de `PromptMemoryContext.render()`, preservando los encabezados de confianza que genera el código. HEAD de JAX publicado para la pareja prevista: `714be57322d2389ccf804d7666b49689890469c4`.
- Web Chat sigue usando ese serializador compartido antes de enviar el prompt a los transportes del modelo. En jax-platform, `efb4447da3da4130b828b28f7ceb697eb3e0e2f5` añadió `test_b9_aud_002_memory_payload_cannot_form_prompt_trust_sections` para fijar el límite estructural. La prueba falló contra el serializador previo y pasó con el actualizado; el conjunto local de pruebas de chat terminó con 37 passed y 12 skipped.
- `046ce8ac54696800307ae95a0f4e95a834a132de` añadió al job backend con DB un diagnóstico de nombres de pruebas fallidas y tipos de excepción obtenidos del JUnit existente. No modifica la ejecución ni los umbrales de CI.
- En `20c7effa090795a180161c4846cc69d4eabfbdb8`, el workflow `push` terminó verde (7/7 jobs), mientras el workflow `pull_request` falló en `backend-tests-con-db` por `test_el_cursor_del_usuario_da_las_mismas_paginas_que_offset_con_empates_y_null`, ajeno a B9. Se registra como resultado de CI, sin atribuirle causa ni alterar esa prueba.

**Lección técnica:** el límite de confianza debe imponerse al serializar la memoria compartida; Web Chat debe consumir ese único resultado en todos sus transportes. Una regresión del prompt compuesto comprueba que el límite llega hasta el punto de envío.

**Alternativas descartadas:** duplicar el escape en Web Chat habría creado dos reglas divergentes; cambiar los transportes por separado no correspondía al punto común donde se compone el prompt.

## Pendientes

- 2026-09-24 · Verificar CI de PR/merge-ref sobre los HEAD exactos finales de JAX y jax-platform, incluido este registro; la prueba local no sustituye el job Python 3.14 ni el backend con DB.
- 2026-09-24 · Ejecutar el targeted recheck formal de B9-AUD-001..007 sobre esa pareja exacta y registrar su veredicto. Al escribir este registro no hay veredicto de recheck posterior al AUDIT FAIL.
- 2026-09-24 · Mantener los PR #269 y #155 sin merge hasta cumplir los gates y recibir la decisión de integración aplicable. No hay despliegue autorizado.
