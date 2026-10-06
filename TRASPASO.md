# Traspaso continuo — E3 respaldo chat

**Fecha:** 2026-10-06
**Rama:** `feat/e3-respaldo-chat`
**Worktree:** `/home/fruiz/wt/jxp-e3-respaldo`
**PR:** #208 en borrador.

## Estado

- SHA `a998c98` pasó frontend, backend sin DB y guardias; auditoría estática no encontró defectos de código, pero rechazó por falta de pruebas DB y carga.
- CI con MariaDB aislada falló solo en `test_pdf_escaneado_desde_chat_se_encola_y_expone_su_estado`: el test usaba el tenant textual del fixture en `AuthUser`. La ruta requiere el ID numérico derivado por `tests.identidades._tenant_db_id`; el test se corrigió.
- El cambio de esta sesión es solo backend test y reporte. No hay cambios de producción en el backend.
- Continúa pendiente una medición real de 20 usuarios con un PDF en proceso. El job genérico `loadtest-tests` no mide este E3.

## Siguiente

1. Commit y push del arreglo del test de tenant.
2. Esperar CI DB del nuevo SHA; si falla, investigar el primer error de ese SHA.
3. Solo con DB aislada, medir 20 turnos concurrentes mientras un escaneo está en proceso, registrar p95 y actualizar el reporte.
4. Eliminar este handoff en un commit separado y conservar su información en la Biblioteca antes de pedir auditoría final.
5. No integrar ni desplegar; no editar `PENDIENTES.md`.

**Fuente de autoridad:** encargo `/home/fruiz/encargos-codex/jxp-e3-respaldo-chat.md`; la arquitectura de cola durable fue verificada por el arquitecto Tier 3; los hallazgos de revisión exactos están en el historial de esta sesión y resumidos en `docs/carga-e3-respaldo-chat-2026-10-06.md`.
