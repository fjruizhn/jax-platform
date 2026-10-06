# Traspaso continuo — E3 respaldo chat

**Fecha:** 2026-10-06
**Rama:** `feat/e3-respaldo-chat`
**Worktree:** `/home/fruiz/wt/jxp-e3-respaldo`
**PR:** #208 en borrador.

## Estado

- SHA `a998c98` pasó frontend, backend sin DB y guardias; auditoría estática no encontró defectos de código, pero rechazó por falta de pruebas DB y carga.
- CI con MariaDB aislada falló solo en `test_pdf_escaneado_desde_chat_se_encola_y_expone_su_estado`: el test usaba el tenant textual del fixture en `AuthUser`. La ruta requiere el ID numérico derivado por `tests.identidades._tenant_db_id`; el test se corrigió.
- El cambio de esta sesión es solo backend test y reporte. No hay cambios de producción en el backend.
- Se añadió `test_carga_e3_20_usuarios_con_pdf_en_proceso`: MariaDB, proyecto, membresías, cola durable, dispatcher y `/api/chat` corren en la base efímera de CI; LAS MANOS conserva el estado `running` durante los turnos. El test imprime p95 y máximo.
- El nuevo paso focal de CI vuelve a ejecutar la medición con `-s` para que el número quede visible en el log. Aún no hay p95; falta CI del SHA actual.
- Primera corrida DB del SHA `8ba8445` llegó a `3837 passed, 2 skipped` y falló en el test de carga antes de medir: `BlockingPortal.call` no admite argumentos nombrados. La subida se cambió a POST autenticado por `/api/chat/upload`; espera nueva corrida.
- El job genérico `loadtest-tests` no mide este E3.

## Siguiente

1. Correr verificación local y commit/push del arreglo con este handoff actualizado.
2. Esperar CI DB del nuevo SHA; si falla, investigar el primer error de ese SHA.
3. Registrar el p95 emitido por el job y actualizar el reporte y piso de pruebas medido.
4. Eliminar este handoff en un commit separado y conservar su información en la Biblioteca antes de pedir auditoría final.
5. Reauditar el SHA final. No integrar ni desplegar; no editar `PENDIENTES.md`.

**Fuente de autoridad:** encargo `/home/fruiz/encargos-codex/jxp-e3-respaldo-chat.md`; la arquitectura de cola durable fue verificada por el arquitecto Tier 3; los hallazgos de revisión exactos están en el historial de esta sesión y resumidos en `docs/carga-e3-respaldo-chat-2026-10-06.md`.
