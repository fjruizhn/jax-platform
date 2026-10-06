# Traspaso — E3 respaldo del chat, ronda 2

- Fecha: 2026-10-06
- Responsable: Codex
- Rama: `feat/e3-respaldo-chat`, base `88da011`
- Encargo: cerrar 1 Major y 5 Minor del informe de auditoría escalón 3 para PR #208.

## Hecho

- E3 ahora mide 20 usuarios desde upload concurrente de PDFs escaneados al máximo configurado hasta chat, despacho durable y sondeo terminal. Solo se simula el servicio OCR remoto.
- CI incluye el tope de p95 y una corrida del mismo E3 con `time.sleep(3)` inyectado; esa corrida debe fallar por superar el umbral.
- Se añadieron cinco casos de autorización en la entrada de upload del chat y la guardia del freno antes de disco/DB.
- El frontend reintenta fallos transitorios con backoff y tope total configurable, traduce estado/causa y libera el selector al terminar.
- Pisos de CI actualizados a 1397 frontend, 2364 backend sin DB y 3844 backend con DB; deben confirmarse en el run de CI de esta rama.
- `docs/carga-e3-respaldo-chat-2026-10-06.md` distingue lo medido localmente de lo pendiente en CI y de OCR real.

## Verificación local

- Frontend: 113 archivos, 1397 pruebas pasaron.
- Backend unitario sin DB: 3 pasaron; `py_compile` pasó.
- `git diff --check` pasó.
- PDF de 20 páginas validado con tamaño exacto de 10 MiB.
- Backend completo sin DB: 2362 pasaron, 1482 omitidas, 2 fallaron porque el checkout JAX indicado por el entorno no contiene `procesamiento_routes`; CI trae su pin.
- MariaDB de CI no está disponible localmente. No se habilitó conexión a `jax_memory` ni se usó la excepción de DB local.
- E3 con MariaDB pasó; falta validar el mutante `sleep(3)` con la configuración aislada de pool del último cambio.
- Primer run sobre `c6cb5a7` (policy 37483798706) encontró que el test E3 exige estar parametrizado también en la suite completa y que los casos de autorización deben crear su directorio de adjuntos para superar el guard de cuota. Correcciones en el árbol: p95 configurable con default 25000 ms y directorio temporal creado. CI debe revalidar.
- En `18c4364`, E3 normal midió `p95=max=1891.9 ms` (20 × 20 páginas × 10 MiB, 20 dispatches). Los cinco casos de autorización y el piso DB pasaron en la suite completa. El paso dedicado de mutación no vale: el `sleep(3)` al inicio de encolar saturó el pool PDF de 1 proceso y dio `503 adjuntos_reintentar` antes de llegar a p95. La revisión actual fija 8 workers (máximo permitido) y timeout 60 s (máximo permitido) solo en la corrida mutante, para medir el bloqueo del event loop sin confundirlo con saturación del pool.
- Contexto solicitado `/home/fruiz/jax-platform/CONTEXT.md` no existe en el checkout principal; tampoco hay `CLAUDE.md` en su raíz.

## Pendiente / siguiente paso

1. Publicar los cambios en `origin/feat/e3-respaldo-chat`.
2. Revisar CI de PR #208, incluyendo E3 normal, mutante, autorizaciones y pisos. Corregir fallos y volver a medir pisos si aplica.
3. Si CI mide OCR solo con el servicio simulado, conservar como pendiente la medición con LAS MANOS real en host JAX, con operador y ventana asignados por Fernando antes de GO de producción.
4. Registrar SHA, run de CI y métricas E3 en la Biblioteca; no integrar a `main` ni tocar producción como parte de este encargo.
