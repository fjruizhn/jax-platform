# Aviso Telegram al activar el freno de incertidumbre

**Fecha:** 2026-10-05  
**Tipo:** HISTORIA  
**Quién decidió:** Fernando, en el encargo entregado para esta sesión; Hyde queda a cargo de auditar e integrar.

## Qué se hizo

El despachador de documentos ahora manda un aviso Telegram cuando el número de filas con desenlace incierto alcanza `2 × proyectos.documentos.rutas_por_trabajo`, el umbral que suspende nuevos despachos. El aviso usa `lib-avisar.sh` y `TELEGRAM_CREDS`, con defaults de Hall9000 (`/opt/backup-scripts/lib/lib-avisar.sh` y `/etc/restic/telegram.env`) que se pueden sustituir por variables de entorno. La unidad versionada entrega `telegram.env` mediante `LoadCredential`, porque el servicio corre como `jaxsvc` y el archivo fuente es accesible solo por `fruiz`.

El envío corre como tarea independiente con un subprocess asíncrono. No retrasa el ciclo del despachador ni una petición que lo haya despertado. Se avisa una vez por activación; cuando el conjunto de filas baja del umbral, el freno se rearma para una activación posterior. Los errores del aviso quedan en el log y no alteran el freno.

## Por qué

El freno ya fallaba cerrado ante suficientes desenlaces inciertos para evitar duplicar trabajos de LAS MANOS, pero su única señal era un warning repetido en el log. Sin el aviso, la cola podía quedarse congelada sin que el operador lo viera.

## Lecciones técnicas

- La prueba mantiene Telegram bloqueado y confirma que el ciclo de base de datos termina mientras el envío sigue pendiente.
- systemd copia la credencial privada al directorio de credenciales del servicio. El subprocess solo hereda `PATH`, la ruta de `lib-avisar.sh` y la ruta dentro de `CREDENTIALS_DIRECTORY`; no hereda secretos del proceso de la aplicación. El token se lee desde el archivo por la librería compartida.
- El intento de aviso es best-effort, igual que `lib-avisar.sh`: si Telegram o el helper falla, el freno permanece puesto y el ciclo del despachador sigue vivo.

## Verificación

Se corrió `backend/tests/test_proyectos_documentos_despachador.py` contra MariaDB 12.3.3 desechable en `127.0.0.1:33991`: 150 pruebas pasaron (122 warnings ya emitidos por las migraciones de la plantilla). La prueba nueva primero falló porque faltaba el encolado y luego pasó con la implementación. La suite amplia del backend en `JAX_CI_NO_DB=1` dio 2342 pasaron, 1459 omitidas y 3 warnings. No se usó la MariaDB de producción ni se tocó producción.

## Pendientes

Hyde debe auditar el SHA final con escalón 3 e integrar el PR según el encargo. No se registran pendientes nuevos.
