# Aviso Telegram al activar el freno de incertidumbre

**Fecha:** 2026-10-06 (corrección de ronda 2)
**Tipo:** HISTORIA
**Quién decidió:** Fernando, encargo `jxp-freno-telegram-r2.md`; Codex implementó en la rama del PR #203.

## Corrección de la ronda anterior

La ronda inicial afirmó que systemd entregaba una credencial Telegram utilizable y que el subprocess registraba errores. La auditoría del 2026-10-06 midió lo contrario en la instalación: la unidad no garantizaba `LoadCredential`, el archivo `/etc/restic/telegram.env` no era legible por `jaxsvc`, el helper podía terminar con código cero sin enviar y el stderr se descartaba. Por eso el aviso podía perderse sin dejar rastro. Las afirmaciones anteriores de envío y diagnóstico quedan corregidas por esta entrada.

## Qué se hizo

El despachador usa `_enviar_telegram` de `backend/catalogo_modelos_ejecutor.py`, que consulta `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` desde `/etc/jax/.env` y devuelve si Telegram confirmó la entrega. Se quitó `LoadCredential` de la unidad versionada, junto con el subprocess a `lib-avisar.sh` y la prueba que fijaba esa solución.

El freno solo se rearma cuando el contador de incertidumbre llega a cero. El intervalo de enfriamiento se configura en `axioma_config` y aparece en administración. Los envíos quedan en tareas independientes; al detener el despachador se cancelan y esperan antes de salir. Si Telegram no confirma, el log registra un error sin credenciales.

El envío distingue tres desenlaces (`Desenlace` en `catalogo_modelos_ejecutor.py`). Entregado: Telegram confirmó. Fallo cierto: no salió nada procesable (sin credenciales ni cliente, conexión rechazada, ProxyError, UnsupportedProtocol, LocalProtocolError, cliente cerrado, WriteError, WriteTimeout, y toda respuesta HTTP de error salvo 504/524, con cuerpo JSON o el HTML de un proxy). Desconocido: pudo haber llegado (ReadTimeout, ReadError, RemoteProtocolError, 504/524 de gateway, 200 con cuerpo ilegible). Un desconocido cuenta como entregado SOLO para el enfriamiento: no se reintenta antes de que venza (un duplicado es peor que perderlo), pero si el freno sigue activo el recordatorio se repite cada enfriamiento, igual que tras una entrega confirmada. El enfriamiento tiene un mínimo de 60 s (por defecto 3600, el que siembra la migración): el 0 ya no existe y un 0 guardado se lee como ilegible, falla cerrado (log de error, sin aviso). Cada aviso se distingue por su texto: «aviso N» (N cuenta los avisos dados del incidente; un reintento tras un fallo cierto conserva el número) y «activo desde AAAA-MM-DD HH:MM <zona>» (inicio del incidente sin pausa, el mismo en todos sus avisos). La zona sale de `JAX_ZONA_HORARIA` (nombre IANA; por defecto `America/Tegucigalpa`, validada con zoneinfo al importar el despachador; como `backend/main.py` importa el despachador, un nombre desconocido impide arrancar la API entera de jax-platform, no solo el despachador: fallo cerrado deliberado, igual que `JAX_AJUSTES_TTL_S`), no de la zona del host; un host en UTC igual muestra la hora de Tegucigalpa. Un desconocido (ReadTimeout) cuenta para N: tras él, el recordatorio dice «aviso 2». Un fallo cierto reintenta con espera creciente sobre la base `proyectos.documentos.freno_incertidumbre_reintento_s` (60 s por defecto; no es el mínimo del enfriamiento): 1x, 2x, 4x esa base, con tope en el mayor entre el enfriamiento y la base. El contador de fallos solo se reinicia tras una entrega CONFIRMADA o tras una pausa de un enfriamiento completo entre incidentes; no al terminar cada incidente ni tras un desconocido.

**Al reiniciar el proceso** se pierde todo lo que vive en memoria: las filas en incertidumbre (`_en_incertidumbre`) y el estado del aviso (N, la hora de inicio del incidente, el contador de fallos y el enfriamiento). Como el freno se calcula sobre esas filas, **el reinicio apaga el freno sin aviso**: las filas que estaban en incertidumbre siguen `en_cola` y pueden despacharse de nuevo hacia LAS MANOS (riesgo de duplicado ya aceptado en el docstring de `despachador.py`). Solo un incidente nuevo, con incertidumbre acumulada después del reinicio, vuelve a activar el freno y da un nuevo «aviso 1», con la hora de esa nueva acumulación. `_enviar_telegram` conserva su contrato booleano para los demás llamadores.

## Verificación

- `backend/tests/test_ajustes.py backend/tests/test_proyectos_documentos_despachador.py` con `JAX_CI_NO_DB=1`: 70 pasaron, 116 omitidas por requerir DB.
- Suite completa backend sin DB, sobre pin JAX `0604754384…`: 2361 pasaron, 1476 omitidas y 3 warnings. No se conectó a `jax_memory` ni a otra DB del host.
- Mutante que esperaba directamente el envío: la prueba de no bloqueo falló como se esperaba antes de restaurar la tarea independiente.
- `frontend/src/pages/admin/AdminSettings.test.jsx`: 35 pasaron.
- `tests/test_no_fail_open_except.py`: 16 pasaron.
- Piso sin DB medido localmente: 2358 → 2361; CI también pasó el job sin DB en SHA `e5c1f94`.
- El primer job aislado con DB en SHA `e5c1f94` contó 3834 pasadas y una fallida: la prueba de migración no incluía la clave nueva en su expectativa. Se añadió la clave a la expectativa.
- CI de la ronda 2 (SHA `205ce8a4e2d97013bc0dcaa3bf95ed234de52872`, run `37451121724`: 3835 pasadas con DB y 2361 sin DB; ambos jobs verdes. Frontend: 1392/0/1392. Se fijaron los pisos respectivos en esos valores.
- Ronda 9 (fecha, hora y zona en el aviso; el desconocido cuenta para N; mínimo de la ayuda desde `limites`): los pisos se fijaron antes de correr la CI como base del runner en `e8d2cca` + 35 pruebas nuevas (3875+35 y 2394+35), y la CI del SHA `329b4dce89b06c2cfccf2237275cc746dd6c3022`, run `37525242051`, los confirmó leyendo el log: job con DB `colectados=3912 passed=3910 skipped=2 failed=0 errors=0`; job sin DB `2429 passed, 1483 skipped`; frontend 1393/0/1393 (comparación de igualdad). Rigen esos pisos, no los de la ronda 2.

## Estado

PR #203 actualizado y CI verde. Queda abierto para integración; esta sesión no lo integra.
