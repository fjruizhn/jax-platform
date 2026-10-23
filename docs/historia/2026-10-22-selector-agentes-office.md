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

## Publicación y comprobación en Hall9000 / atem-ai

El 22 de octubre de 2026, Codex activó el par inmutable JXP
`90626d129c7fd12f219d8acb9ef2319338f59cb3` / JAX
`d62721f4438cd80a579e2ba48fcf05c04dffdc19` en Hall9000 y el frontend
del mismo SHA JXP en atem-ai. El manifiesto del par tiene SHA-256
`f61cdf83f620a89e68452e6345090ffc76e9362768c9d50e2bc0e41c32d118b4`;
el público, `5bb512586f22dd97565faea6dd3859ed14ec1e316fcb08dade5f47f18363032b`.
Por HTTPS, `/virtual-office` devolvió 200 con `index.html` SHA-256
`e07926c0e537fa0e6cc37870ba99fe436d425d70bf6b5e40598b5bdbd27bc83e` y
`/assets/index-wwQR_kr2.js` devolvió 200 con SHA-256
`aaa637d6ff1cdfe9a9fd41aed8067fbe27e4edeaa2badf07a7fbe58fd0f35a69`.
Las rutas protegidas de proyectos y agentes devolvieron 401 sin sesión;
la ruta administrativa ajena al vhost devolvió 404. El motor `glm-office`
quedó habilitado en el catálogo, versión 1, modelo `zhipu/glm-5.3`.

Antes de la migración se hizo un dump comprimido de `jax_memory` (SHA-256
`a58f3396d339c62d3898df3e0e9bf642b060de1694c3845da79d2d7b369b18ae`)
en `/var/lib/jax-office/activation-backups/selector-90626d12/`. Se restauró
en una MariaDB 12.3.3 descartable y coincidieron los conteos de `jax_users`,
`facts`, `memory_objects`, invitaciones, grants, perfiles y releases. La
instantánea pública para reversión está en
`/var/lib/jax-office/activation-backups/office-activation.J6eybeHi`
en atem-ai. Tras la publicación, 200 peticiones a la SPA y 200 a la ruta
protegida de agentes, con 80 concurrentes, dieron 200/200 respuestas
esperadas en cada serie; SPA 415.2 req/s y p95 335.2 ms, ruta protegida
428.4 req/s y p95 393.7 ms.

La activación descubrió dos defectos de upgrade: el bootstrap instalado aún
no reconocía los tres archivos de ACL del paquete nuevo, y el provisionador
no reconocía el estado real previo de 65 grants exactos al migrar a los 68
del selector. Se instaló un bootstrap externo root-owned del SHA fuente
verificado; tras comprobar que los otros 65 grants coincidían exactamente,
se agregaron únicamente `INSERT,UPDATE` de `office_agent`,
`office_agent_binding` y `office_agent_run`. El provisionador terminó y el
servicio web pasó su comprobación efectiva de proceso, selección y salud.
El código de reconocimiento de ese upgrade queda en el mismo PR del selector.
El launcher del SHA inicial no importaba el CLI con Python `-I`; el fix del
SHA `6e502624d737f2837e9383ca59cc3b3916af5ae9` se instaló por paquete
inmutable nuevo y se comprobó ejecutando la activación de nuevo:
`changed=false`, versión 1.

Una llamada real mínima a `https://api.z.ai/api/paas/v4/chat/completions`
con la credencial activa y `glm-5.3` devolvió **HTTP 429, código 1113** el
22 de octubre. Esto prueba llegada al proveedor, pero no una respuesta del
modelo. La disponibilidad de inferencia queda pendiente de resolver en la
cuenta/clave estándar de Z.ai; no se cambia al endpoint de un plan distinto
por suposición. La invitación real por correo y las acciones autenticadas de
un administrador siguen pendientes del primer ingreso de Fernando con su
cuenta existente. El SMTP con TLS y autenticación ya había pasado preflight;
el worker de invitaciones conserva resultado `success`, sin invitaciones
pendientes todavía. Decisión de publicación: Fernando Ruiz; ejecución y
medición: Codex. La alternativa de usar checkouts mutables para el servicio
se descartó en favor de releases inmutables auditables.
