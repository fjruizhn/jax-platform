# Traspaso — E3 respaldo del chat, ronda 2

- Fecha: 2026-10-06
- Responsable: Codex
- Rama: `feat/e3-respaldo-chat`, base `88da011`
- Encargo: cerrar 1 Major y 5 Minor del informe de auditoría escalón 3 para PR #208.

## Hecho

- E3 ahora mide 20 usuarios desde upload concurrente de PDFs escaneados al máximo configurado hasta chat, despacho durable y sondeo terminal. Solo se simula el servicio OCR remoto.
- CI incluye el tope p95 y la prueba de mutación `time.sleep(3)`; la mutación se acepta como detectada solo por aserción de p95.
- Se añadieron cinco casos de autorización en la entrada de upload del chat y la guardia del freno antes de disco/DB.
- El frontend reintenta fallos transitorios con backoff y tope total configurable, traduce estado/causa y libera el selector al terminar.
- Pisos medidos en CI: 1397 frontend, 2364 backend sin DB y 3847 backend con DB.
- `docs/carga-e3-respaldo-chat-2026-10-06.md` contiene la tabla final de hallazgo, cierre y evidencia, separa CI del OCR real.

## Verificación local

- Frontend: 113 archivos, 1397 pruebas pasaron.
- Backend unitario sin DB: 3 pasaron; `py_compile` pasó.
- `git diff --check` pasó.
- PDF de 20 páginas validado con tamaño exacto de 10 MiB.
- Backend completo sin DB: 2362 pasaron, 1482 omitidas, 2 fallaron porque el checkout JAX indicado por el entorno no contiene `procesamiento_routes`; CI trae su pin.
- MariaDB de CI no está disponible localmente. No se habilitó conexión a `jax_memory` ni se usó la excepción de DB local.
- Run push policy 37491432941 (`65b9449`) pasó. E3 normal `p95=1308.5 ms`, `max=1309.7 ms`; el mutante midió `61448.8 ms` sobre el tope `25000 ms`, y el wrapper pasó al verificar la aserción exacta. DB: 3847 passed, 2 skipped, 0 failed; no-DB: 2364 passed, 1482 skipped, 0 failed; frontend: 1397/1397.
- Auditoría escalón 3 de `65b9449`: APROBADO, sin BLOCK/MAJOR/MINOR abiertos. Incluye freno antes de escribir y antes de INSERT en lote multiarchivo; respuesta parcial conserva `detail`, status y headers, y elimina el archivo no registrado.
- Primer run sobre `c6cb5a7` (policy 37483798706) encontró que el test E3 exige estar parametrizado también en la suite completa y que los casos de autorización deben crear su directorio de adjuntos para superar el guard de cuota. Correcciones en el árbol: p95 configurable con default 25000 ms y directorio temporal creado. CI debe revalidar.
- En `18c4364`, E3 normal midió `p95=max=1891.9 ms` (20 × 20 páginas × 10 MiB, 20 dispatches). Los cinco casos de autorización y el piso DB pasaron en la suite completa. El paso dedicado de mutación no vale: el `sleep(3)` al inicio de encolar saturó el pool PDF de 1 proceso y dio `503 adjuntos_reintentar` antes de llegar a p95. La revisión actual fija 8 workers (máximo permitido) y timeout 60 s (máximo permitido) solo en la corrida mutante, para medir el bloqueo del event loop sin confundirlo con saturación del pool.
- Contexto solicitado `/home/fruiz/jax-platform/CONTEXT.md` no existe en el checkout principal; tampoco hay `CLAUDE.md` en su raíz.

## Pendiente / siguiente paso

1. Push policy 37491432941 pasó para `65b9449`; resultado PR y secret scan se consultan con `gh pr checks 208` al seguir el encargo.
2. Pendiente para 2026-10-07: Fernando asigna operador y ventana en host JAX para medir LAS MANOS real antes de GO; registrar configuración, concurrencia, p95, máximo, RPS y uso de recursos en la Biblioteca. Este encargo no integra a `main` ni toca producción.
