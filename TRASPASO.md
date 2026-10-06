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
- El nuevo paso focal de CI ejecutó la medición con `-s`: CI `37454479696`, SHA `30989bf`, 20/20 respuestas 200, p95 `721.1 ms`, máximo `769.4 ms`. La DB fue MariaDB 12.3.3 desechable; LAS MANOS devolvió un estado `running` simulado y el proveedor de chat tuvo demora fija de 30 ms.
- Primera corrida DB del SHA `8ba8445` llegó a `3837 passed, 2 skipped` y falló en el test de carga antes de medir: `BlockingPortal.call` no admite argumentos nombrados. La subida cambió a POST autenticado por `/api/chat/upload`.
- Segunda corrida DB ejecutó los 20 turnos pero dejó la fila durable con job simulado abierto. Falló después en `test_despacho_resultado_y_trabajo_perdido`, que comparte esa DB y esperaba encontrar solo sus propios jobs. Se agregó limpieza de la fila E3 al final del test para aislar los datos; la tercera corrida pasó.
- El job genérico `loadtest-tests` no mide este E3.
- `backend-tests-con-db` completó `3838 passed, 2 skipped`; se actualizó el piso en `.github/workflows/policy.yml` a 3838. Este cambio de piso y el reporte requieren otra corrida CI sobre el SHA final.

## Siguiente

1. Revisar reporte, workflow y conteo; commit/push con este handoff actualizado.
2. Esperar la corrida CI del SHA final; corregir cualquier falla y reauditar si el código cambia.
3. Eliminar este handoff en un commit separado y conservar su información en la Biblioteca antes de pedir auditoría final.
4. Reauditar el SHA final. No integrar ni desplegar; no editar `PENDIENTES.md`.

**Fuente de autoridad:** encargo `/home/fruiz/encargos-codex/jxp-e3-respaldo-chat.md`; la arquitectura de cola durable fue verificada por el arquitecto Tier 3; los hallazgos de revisión exactos están en el historial de esta sesión y resumidos en `docs/carga-e3-respaldo-chat-2026-10-06.md`.
