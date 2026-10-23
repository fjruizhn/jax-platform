# Selector de agentes privados de Office — 2026-10-22

Se integró el selector de motor y la ejecución de agentes privados por
proyecto. Los administradores crean agentes y cambian su motor; los miembros
internos ejecutan los agentes ya creados sin recibir el catálogo administrativo.
La repetición de una solicitud mientras sigue en proceso conserva su misma
clave de idempotencia y muestra que sigue en curso.

La activación inicial es explícita: el comando
`virtual_office.engine_activation_cli` recibe claves de proveedor/modelo y
clasificaciones, lee URL y credencial desde el catálogo durable y deja una
atestado de hash. No imprime secretos. El launcher emparejado acepta el modo
`office-engine` únicamente con `/etc/jax/migrate.env` regular, `root:root`, modo
`0600`; la ruta de proveedor permitida llega como
`OFFICE_BROKER_ALLOWED_BASE_URLS` desde ese mismo archivo de credenciales.
En MariaDB desechable, un principal web con sólo `SELECT` fue rechazado al
insertar `office_engine`; el principal migrador con `SELECT, INSERT, UPDATE`
activó el mismo motor con éxito.

Validación medida en MariaDB desechable `127.0.0.1:33994`, sin llamada ni cargo
al proveedor: 32 ejecuciones concurrentes contra transporte GLM simulado,
p95 de 0.0375 s y 777.9 ejecuciones/s. A esa concurrencia no se observó
degradación; el límite de esta prueba fue 32. Pruebas focales de API, broker,
CLI y launcher pasaron; el panel pasó 28 pruebas y compilación de producción.

Orden de instalación para evitar una reinicialización con una clave todavía
desconocida: (1) instalar el launcher emparejado auditado, (2) actualizar
atómicamente `runtime.env` con `OFFICE_BROKER_ALLOWED_BASE_URLS`, (3) activar
la release emparejada y ejecutar su migración, (4) ejecutar el launcher en
modo `office-engine` para registrar el primer motor, y (5) publicar el
frontend. La activación usa, por ejemplo,
`office-engine /etc/jax/migrate.env --engine-key glm-office --display-name GLM --provider-id zhipu --model-id glm-5.3 --classifications INTERNAL_CONFIDENTIAL --activate`.
