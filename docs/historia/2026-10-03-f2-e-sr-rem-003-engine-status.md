# AXIOMA 3.0 — GOBERNANZA UNIVERSAL

## F2-E-SR-REM-003 — ENGINE_STATUS atomic health observation

**HISTORIA · 2026-10-03 · Codex**

### Qué se corrigió

`JAXEngineState` ahora mantiene la última sonda terminada de LAS MANOS como una
sola `LasManosHealthObservation` inmutable (`alive`, `observed_at`). La lectura
que usa gobernanza toma esa referencia bajo el mismo `RLock` que publica la
sonda. Ya no compone evidencia desde `las_manos_alive` y
`last_health_check`, que sólo quedan como campos de compatibilidad y se
actualizan dentro de la misma sección crítica.

La llamada HTTP y el timestamp de terminación ocurren antes del lock; la
publicación de eventos ocurre después. Una sonda repetida con el mismo estado
reemplaza la observación y refresca su tiempo sin emitir un falso evento de
cambio. Antes de la primera sonda terminada no hay observación acreditable.

### Por qué

El escritor anterior podía publicar primero el booleano y después la hora. Un
resolver concurrente podía obtener el estado nuevo con la hora de la sonda
anterior: un hecho que nunca existió. F2-B exige que `ENGINE_STATUS` sea una
observación actual coherente, cuyo `observed_at` es la finalización de la
sonda, nunca la hora en que el resolver leyó el estado.

### Source/version decision

Se conserva `f2-e.runtime-status.2` y la identidad/configuración existente de
la fuente. La corrección ocurre antes de integrar o desplegar F2-E-SR: la
verificación de producción encontró que el Platform desplegado no contiene el
bridge SR y que no existe un camino de receipts F2-E-SR desplegado al cual haya
que invalidar. La identidad ya describe la sonda servidor-propietaria fija; no
se hizo un bump ceremonial por una corrección pre-merge de atomicidad.

### Cobertura local

Los tests de la fuente prueban primer éxito/fallo, ausencia antes de sonda,
refresh para `alive → alive` y `down → down`, ambos sentidos de transición,
lecturas sin refresco, y una intercalación bloqueada sobre el límite de
publicación que sólo permite la observación completa anterior o posterior. El
bridge exact-pair sigue emitiendo evidencia F2-B y una observación sin nueva
sonda vence por freshness.

La regresión de concurrencia ejecuta la sonda real y pausa el escritor después
de escribir el campo de compatibilidad `las_manos_alive`. Con el lector legado
reconstruido temporalmente desde los dos campos, los dos sentidos fallaron:
el lector terminó mientras el escritor seguía pausado y habría devuelto el
tuple mezclado. Restaurada la lectura de la referencia atómica, el lector queda
bloqueado hasta que la publicación termina y devuelve la observación nueva
completa. El comando focal exact-pair, con el checkout JAX
`9e13b0def684a2cea8ebc602960700c4df7a34c1`, terminó con 24 passed y 0 skipped;
al incluir la prueba de broadcast, 26 passed.

### Fuera de alcance

No se modificaron JAX, Faro, F2-F, F2-E general, `JOB_STATUS`,
`PIPELINE_STATUS`, `FACET_RUNTIME_STATUS`, detección estructurada, JobStore ni
la configuración de identidad de fuente. No hubo despliegue ni integración.
