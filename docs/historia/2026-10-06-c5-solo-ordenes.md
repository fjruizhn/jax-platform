# C5 SOLO_ORDENES en jax-platform — 2026-10-06

**Tipo:** HISTORIA. **Fuente:** encargo `/home/fruiz/encargos-codex/jax-c5-solo-ordenes-r2.md`, diff y resultados de los worktrees. **Decidió:** Fernando Ruiz, 2026-10-06.

PR #209 conserva la migración/administración de `ejecutor.c5_auditor_nube_solo_ordenes=false`. Esta ronda pasa `auditoria_afirmaciones` por `_turno` y presenta `NO_AUDITADA_SOLO_ORDENES`, `AUDITADA_POR_C5` y `NO_AUDITADA_ILEGIBLE` en `DetalleMision` con textos separados es/en y tokens de tema existentes. No se reutiliza `RETENIDA_POR_AUDITOR`, que significa otra cosa.

Se fijó un job exacto de integración con JAX #363 SHA `2c39dad6751e33140cc53613f3c625e5889bbc29`, que prueba la propagación del marcador. Orden decidido: integrar primero esta plataforma (que crea la clave cerrada), luego JAX (que lee y verifica la semilla migrada). La prueba de JAX no debe insertar la clave artificialmente.

Verificación local: frontend completo 1392 passed / 113 archivos; build exitoso; backend no-DB completo 2367 passed / 1477 skipped; la prueba nueva del marcador pasó individualmente. El build advirtió chunk JS mayor de 500 kB. Piso frontend actualizado de 1391 a 1392. Piso backend no-DB de 2366 a 2367. El piso backend con DB sube 3838→3839 por una prueba pura; no se midió localmente porque Docker negó acceso al socket y el entorno backend local detecta el puerto de producción 3308. CI DB debe revalidar el conteo y la migración.

No se tocó producción ni se integró ningún PR. La auditoría Tier 3 y los checks del SHA final quedan por registrar en la descripción de #209.
