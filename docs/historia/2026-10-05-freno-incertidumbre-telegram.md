# Aviso Telegram al activar el freno de incertidumbre

**Fecha:** 2026-10-06 (corrección de ronda 2)
**Tipo:** HISTORIA
**Quién decidió:** Fernando, encargo `jxp-freno-telegram-r2.md`; Codex implementó en la rama del PR #203.

## Corrección de la ronda anterior

La ronda inicial afirmó que systemd entregaba una credencial Telegram utilizable y que el subprocess registraba errores. La auditoría del 2026-10-06 midió lo contrario en la instalación: la unidad no garantizaba `LoadCredential`, el archivo `/etc/restic/telegram.env` no era legible por `jaxsvc`, el helper podía terminar con código cero sin enviar y el stderr se descartaba. Por eso el aviso podía perderse sin dejar rastro. Las afirmaciones anteriores de envío y diagnóstico quedan corregidas por esta entrada.

## Qué se hizo

El despachador usa `_enviar_telegram` de `backend/catalogo_modelos_ejecutor.py`, que consulta `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` desde `/etc/jax/.env` y devuelve si Telegram confirmó la entrega. Se quitó `LoadCredential` de la unidad versionada, junto con el subprocess a `lib-avisar.sh` y la prueba que fijaba esa solución.

El freno solo se rearma cuando el contador de incertidumbre llega a cero. El intervalo de enfriamiento se configura en `axioma_config` y aparece en administración. Los envíos quedan en tareas independientes; al detener el despachador se cancelan y esperan antes de salir. Si Telegram no confirma, el log registra un error sin credenciales.

## Verificación

- `backend/tests/test_ajustes.py backend/tests/test_proyectos_documentos_despachador.py` con `JAX_CI_NO_DB=1`: 70 pasaron, 116 omitidas por requerir DB.
- Suite completa backend sin DB, sobre pin JAX `0604754384…`: 2361 pasaron, 1476 omitidas y 3 warnings. No se conectó a `jax_memory` ni a otra DB del host.
- Mutante que esperaba directamente el envío: la prueba de no bloqueo falló como se esperaba antes de restaurar la tarea independiente.
- `frontend/src/pages/admin/AdminSettings.test.jsx`: 35 pasaron.
- `tests/test_no_fail_open_except.py`: 16 pasaron.
- Piso sin DB medido: 2358 → 2361. Piso con DB propuesto: 3830 → 3833; queda pendiente de confirmar por el runner aislado del PR.

## Pendiente

Confirmar en CI de GitHub la suite con MariaDB desechable y fijar el piso con DB usando el conteo observado. No integrar el PR desde esta sesión.
