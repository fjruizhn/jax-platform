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
- E3 en CI con MariaDB y el mutante `sleep(3)` siguen pendientes de resultado.
- Contexto solicitado `/home/fruiz/jax-platform/CONTEXT.md` no existe en el checkout principal; tampoco hay `CLAUDE.md` en su raíz.

## Pendiente / siguiente paso

1. Publicar los cambios en `origin/feat/e3-respaldo-chat`.
2. Revisar CI de PR #208, incluyendo E3 normal, mutante, autorizaciones y pisos. Corregir fallos y volver a medir pisos si aplica.
3. Si CI mide OCR solo con el servicio simulado, conservar como pendiente la medición con LAS MANOS real en host JAX, con operador y ventana asignados por Fernando antes de GO de producción.
4. Registrar SHA, run de CI y métricas E3 en la Biblioteca; no integrar a `main` ni tocar producción como parte de este encargo.
