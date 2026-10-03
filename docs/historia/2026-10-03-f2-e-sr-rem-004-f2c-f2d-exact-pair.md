# AXIOMA 3.0 — GOBERNANZA UNIVERSAL

## F2-E-SR-REM-004 — F2-C/F2-D exact compatibility repair

**HISTORIA · 2026-10-03 · Codex**

### Qué se corrigió

`api.governed_chat` ahora declara una única triple de compatibilidad exacta:
`f2-c.renderer.3`, `f2-c.domain.5` y el conjunto de envelopes
`{f2-c.1}`. El puente compara los tres valores importados desde el checkout
JAX configurado contra esa constante; no acepta rangos, aliases, una versión
vieja ni una futura.

El fixture de integración crea su `GovernanceReceipt` con la versión real del
renderer del JAX emparejado. Así ejercita el renderer y el lifecycle reales en
lugar de conservar un receipt histórico incompatible.

### Por qué

El checkout exacto de JAX `9e13b0def684a2cea8ebc602960700c4df7a34c1` publica
renderer `.3` y domain `.5`, pero Platform mantenía el guard `.2`/`.2`.
`_core()` cerraba correctamente, pero contra el contrato equivocado: cualquier
respuesta Web Chat se degradaba a `UNAVAILABLE` y no producía unidad de
transporte gobernada.

La regresión ejecuta `_core()` con el JAX real y exige aceptación. Sustituye
determinísticamente las constantes del dominio por el par `.2`/`.2` y por un
par futuro `.4`/`.6`; ambos deben fallar cerrados. Con el guard previo, la
aceptación real falla con `configured JAX F2-C compatibility is unsupported`.
Tras el cambio, la suite de integración y los transportes lifecycle pasan.

### CI exacta

El mismo job `f2e-runtime-status-exact-pair` conserva las identidades exactas
de JAX PR #313 y Platform PR #175. Después de que pytest termina, el artefacto
registra que se ejercieron tanto las fuentes/bridge F2-E-SR como el lifecycle
F2-C/F2-D. El job ahora cubre fuentes runtime, bridge, broadcast de salud,
integración exacta F2-C y transporte lifecycle. La evidencia se escribe sólo
después de un pytest exitoso.

### Source/version decision

No cambia una fuente acreditada ni el contrato de receipts de runtime status:
es una corrección pre-merge de la guardia de dependencia Platform/JAX. No se
incrementó versión ni identidad por ceremonia.

### Fuera de alcance

No se modificaron JAX, la observación atómica `ENGINE_STATUS`, otros dominios
SR, Faro, F2-F ni F2-E general. No hubo push, merge, despliegue ni cambios de
producción.
