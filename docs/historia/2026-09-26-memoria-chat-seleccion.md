# Memoria B9 representativa en el chat

Historia y evidencia · Codex, 2026-09-26. Encargo de Fernando de restaurar
la memoria de JAX. No declara despliegue ni recuperación en producción.

El código anterior pedía20 revisiones recientes. El plan legacy publica
primero facts y después decisiones y pendientes, con la fecha real de
publicación B9. Los últimos20 podían excluir todos los FACT aunque la
adopción fuera correcta. Recordar contenido de un ACTION_ITEM no demuestra
recuperación de un FACT legacy.

Se conserva la frontera autorizada B9 y su renderer. La selección usa una
ventana de hasta100 candidatos, hasta20 envelopes enteros y32000 caracteres
renderizados, con cupos10 FACT/5 DECISION_MEMORY/5 ACTION_ITEM y relleno de
cupos libres por recencia. Mantiene el orden original entre seleccionados.
La configuración valida límites y coherencia; el máximo de candidatos es100,
igual al contrato vigente del reader. No modifica fuente, timestamps, scopes,
resolución vigente ni clasificación de confianza. Un registro que no cabe
se omite entero. No se promete recuperar cualquier recuerdo antiguo: no
hay búsqueda por relevancia y la ventana permanece limitada.

Alternativas descartadas: reordenar la adopción para hacer pasar la prueba,
fabricar recencia o corregir un hecho sin una corrección real. También se
descartó aumentar el límite de candidatos más allá del contrato del reader.

Carga real del reader en MariaDB12.3.3, base exclusiva jax_test, más de1000
objetos canónicos sintéticos de dos tenants, pool8 y diez peticiones por
participante. p95 limit20/limit100: concurrencia1=4,34/16,63ms;
concurrencia10=22,17/112,10ms; concurrencia25=64,24/239,19ms.
A25, throughput561,77/139,08 solicitudes/s. Se verificaron tenant y dueño
de todos los envelopes devueltos. Evidencia privada en Hall9000:
/tmp/codex-memory-reader100-load.json.

La ventana mayor tiene un costo medido; es un cambio funcional de selección,
no una mejora de rendimiento. Estos datos sintéticos no acreditan capacidad
productiva ni el mayor volumen futuro. Pruebas aisladas de composición y
selección:30 aprobadas, sin cargar conftest ni credenciales productivas. La
regresión contra el código anterior seleccionaba20 memorias sin ningún FACT.
Con100 envelopes de4000 caracteres Unicode cada uno,1000 selecciones puras
en la Mac dieron p95=2,914ms,max3,372ms y356,39 selecciones/s; el contexto
resultante mide24057 caracteres, dentro del límite32000. Se conserva entero
el único FACT que cabe en ese peor caso sintético, sin truncarlo.

Composición SQL real: se crearon136 objetos canónicos sintéticos en orden
96 FACT,22 decisiones,18 pendientes mediante PersistentMemoryAPI. La
función real _scope_for_chat resolvió las identidades desde la DB y la
función real _prompt_memory_context usó ProjectScopeAuthorityResolver y el
reader B9. Resultado:10 FACT,5 decisiones,5 pendientes;20 envelopes,
2898 caracteres. Otro usuario activo del mismo tenant obtuvo0 envelopes.
No se simularon el reader ni el resolvedor: sólo se inyectó el pool de la
base de pruebas para impedir acceso a producción. No constituye un turno
autenticado de navegador ni prueba de adopción legacy productiva. Evidencia
privada: /tmp/codex-memory-chat-real-composition.json en Hall9000.

Quedan pendientes revisión del SHA exacto, CI, integración,
despliegue y turno real autenticado sobre un FACT con binding legacy real.
