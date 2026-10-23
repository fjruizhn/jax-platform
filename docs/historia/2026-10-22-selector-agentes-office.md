# Selector de agentes privados de Office — 2026-10-22

Se preparó el selector de motor y la ejecución de agentes privados por
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
`OFFICE_BROKER_ALLOWED_BASE_URLS` desde `/etc/jax/.env` por el provisionador
al archivo `runtime.env` que consume web.
En MariaDB desechable, un principal web con sólo `SELECT` fue rechazado al
insertar `office_engine`; el principal migrador con `SELECT, INSERT, UPDATE`
activó el mismo motor con éxito.

Validación medida en MariaDB desechable `127.0.0.1:33994`, sin llamada ni cargo
al proveedor: 44 pruebas focales de API, broker, esquema y CLI pasaron sobre
el paquete combinado; 47 de activación y contratos, 6 del panel, y build de
producción. Con el esquema y límites finales, un probe de 80 ejecuciones de
cuatro usuarios en un proyecto, hasta 8 simultáneas y proveedor simulado con
10 ms de latencia, completó 80/80: 288.64 ejecuciones/s, p95 27.7 ms y máximo
30.6 ms. El probe no mide la latencia ni disponibilidad real de Z.ai.

Orden de instalación para evitar una reinicialización con una clave todavía
desconocida: (1) agregar `OFFICE_BROKER_ALLOWED_BASE_URLS` a `/etc/jax/.env`,
(2) instalar el paquete runtime auditado, (3) activar la release emparejada,
que migra y provisiona permisos antes de arrancar web, (4) ejecutar el launcher en
modo `office-engine` para registrar el primer motor, y (5) publicar el
frontend. La activación usa, por ejemplo,
`office-engine /etc/jax/migrate.env --engine-key glm-office --display-name GLM --provider-id zhipu --model-id glm-5.3 --classifications INTERNAL_CONFIDENTIAL --activate`.
