# Aviso Telegram al activar el freno de incertidumbre

**Fecha:** 2026-10-06 (corrección de ronda 2)
**Tipo:** HISTORIA
**Quién decidió:** Fernando, encargo `jxp-freno-telegram-r2.md`; Codex implementó en la rama del PR #203.

## Corrección de la ronda anterior

La ronda inicial afirmó que systemd entregaba una credencial Telegram utilizable y que el subprocess registraba errores. La auditoría del 2026-10-06 midió lo contrario en la instalación: la unidad no garantizaba `LoadCredential`, el archivo `/etc/restic/telegram.env` no era legible por `jaxsvc`, el helper podía terminar con código cero sin enviar y el stderr se descartaba. Por eso el aviso podía perderse sin dejar rastro. Las afirmaciones anteriores de envío y diagnóstico quedan corregidas por esta entrada.

## Qué se hizo

El despachador usa `_enviar_telegram` de `backend/catalogo_modelos_ejecutor.py`, que consulta `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` desde `/etc/jax/.env` y devuelve si Telegram confirmó la entrega. Se quitó `LoadCredential` de la unidad versionada, junto con el subprocess a `lib-avisar.sh` y la prueba que fijaba esa solución.

El freno solo se rearma cuando el contador de incertidumbre llega a cero. El intervalo de enfriamiento se configura en `axioma_config` y aparece en administración. Los envíos quedan en tareas independientes; al detener el despachador se cancelan y esperan antes de salir. Si Telegram no confirma, el log registra un error sin credenciales.

El envío distingue tres desenlaces (`Desenlace` en `catalogo_modelos_ejecutor.py`). Entregado: Telegram confirmó. Fallo cierto: no salió nada procesable (sin credenciales ni cliente, conexión rechazada, ProxyError, UnsupportedProtocol, LocalProtocolError, cliente cerrado, WriteError, WriteTimeout, y toda respuesta HTTP de error salvo 504/524, con cuerpo JSON o el HTML de un proxy). Desconocido: pudo haber llegado (ReadTimeout, ReadError, RemoteProtocolError, 504/524 de gateway, 200 con cuerpo ilegible). Un desconocido cuenta como entregado SOLO para el enfriamiento: no se reintenta antes de que venza (un duplicado es peor que perderlo), pero si el freno sigue activo el recordatorio se repite cada enfriamiento, igual que tras una entrega confirmada. El enfriamiento tiene un mínimo de 60 s (por defecto 3600, el que siembra la migración): el 0 ya no existe y un 0 guardado se lee como ilegible, falla cerrado (log de error, sin aviso). Cada aviso se distingue por su texto: «aviso N» (N cuenta los avisos dados del incidente; un reintento tras un fallo cierto conserva el número) y «activo desde HH:MM» (hora local de inicio del incidente sin pausa, la misma en todos sus avisos). Un fallo cierto reintenta con espera creciente (1x, 2x, 4x el mínimo, con tope en el mayor entre enfriamiento y mínimo). El contador de fallos solo se reinicia tras una entrega CONFIRMADA o tras una pausa de un enfriamiento completo entre incidentes; no al terminar cada incidente ni tras un desconocido. `_enviar_telegram` conserva su contrato booleano para los demás llamadores.

## Verificación

- `backend/tests/test_ajustes.py backend/tests/test_proyectos_documentos_despachador.py` con `JAX_CI_NO_DB=1`: 70 pasaron, 116 omitidas por requerir DB.
- Suite completa backend sin DB, sobre pin JAX `0604754384…`: 2361 pasaron, 1476 omitidas y 3 warnings. No se conectó a `jax_memory` ni a otra DB del host.
- Mutante que esperaba directamente el envío: la prueba de no bloqueo falló como se esperaba antes de restaurar la tarea independiente.
- `frontend/src/pages/admin/AdminSettings.test.jsx`: 35 pasaron.
- `tests/test_no_fail_open_except.py`: 16 pasaron.
- Piso sin DB medido localmente: 2358 → 2361; CI también pasó el job sin DB en SHA `e5c1f94`.
- El primer job aislado con DB en SHA `e5c1f94` contó 3834 pasadas y una fallida: la prueba de migración no incluía la clave nueva en su expectativa. Se añadió la clave a la expectativa.
- CI del SHA final `205ce8a4e2d97013bc0dcaa3bf95ed234de52872`, run `37451121724`: 3835 pasadas con DB y 2361 sin DB; ambos jobs verdes. Frontend: 1392/0/1392. Se fijaron los pisos respectivos en esos valores.

## Estado

PR #203 actualizado y CI verde. Queda abierto para integración; esta sesión no lo integra.
