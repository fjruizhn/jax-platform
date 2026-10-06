# Roles de plataforma y tenant: `superadmin` y `admin`

**Estado:** propuesta para auditoría de Fernando; no autoriza implementación ni despliegue.  
**Decisión de Fernando (2026-10-06):** «superadmin gobierna sobre todo, admin sobre su tenant».  
**Base declarada para implementación:** `jax-platform` `origin/master` en `d283d900` (SHA completo `d283d900e1da5ef557ddd53dcde2f0808306785c`); decisión e inventario inicial de superficie en jax-platform PR #204.
**Alcance M6:** establecer el contrato de autoridad antes de habilitar el nuevo rol `admin`.

## 1. Modelo de autoridad

La autoridad se resuelve desde la fila vigente de `jax_users` en cada request. Los claims de rol y tenant del JWT son transporte y se contrastan con esa fila; nunca conceden autoridad por sí mismos. Se mantiene la consulta por usuario existente y no se introduce caché de roles.

| Rol en la fila activa | Alcance resuelto | Permisos |
|---|---|---|
| `superadmin` | `PLATFORM_ALL` | Ve y gobierna todos los tenants y la superficie de plataforma, sujeto a la cuenta protegida de Fernando (§3.1). Su `tenant_id` identifica su cuenta, no limita su autoridad. Las lecturas entre tenants se permiten; las mutaciones entre tenants quedan cerradas hasta superar la compuerta verificable de §8. En lecturas tenant-scoped, una lista sin filtro recorre todos los tenants y un filtro explícito solo acota resultados. |
| `admin` | `TENANT(<tenant_id autoritativo>)` | Administra únicamente datos de su tenant y cuentas de usuarios ordinarios (`operator`/`viewer`). Las cuentas y los datos pertenecientes a cuentas `admin`/`superadmin` quedan fuera de su alcance en todas las superficies: usuarios, memoria, pipelines, uso, dashboard, auditoría, ocultamiento y restauración. El tenant se obtiene de `jax_users`; query, body, header o JWT no pueden cambiarlo. |
| `operator` | `USER` | Sin cambio de permisos ni de alcance respecto del contrato actual. |
| `viewer` | `USER` | Sin cambio. Aunque la medición de producción del 2026-10-06 encontró solo `superadmin` y `operator`, el código actual lo reconoce y la migración no lo elimina. |
| `NULL` o cualquier otro valor | `DENY` | Fallo cerrado en toda ruta autenticada, incluyendo HTTP, refresh, WS y SSE. Respuesta de sesión inválida uniforme; sin degradar el rol a `operator`. El logout puede limpiar la cookie sin conceder acceso. Esto requiere trabajo: hoy `verificar_sesion` (`backend/auth/middleware.py:64-96`) no rechaza un rol desconocido antes de construir `AuthUser`; añadir la validación exacta del rol vigente en la fila DB. |

Un rechazo por rol válido pero insuficiente es `403`. Un `admin` que señale un recurso o usuario ajeno a su tenant recibe `404`, incluso en operaciones por lote; el lote es atómico y no deja cambios parciales. El backend es la frontera de autorización; ocultar controles en frontend no sustituye estas comprobaciones.

El resolvedor es único y compartido por endpoints, tareas auxiliares y proyecciones de rol. No se permite inferir `PLATFORM_ALL` a partir de `tenant_id`, ni mantener comparaciones ad hoc que diverjan de este contrato. El rol de la fila DB prevalece sobre una claim JWT manipulada o antigua. Los identificadores de rol se comparan byte por byte y con distinción de mayúsculas/minúsculas en DB y JAX; queda prohibido normalizar con `.lower()` u otra transformación antes de autorizar (§5).

## 2. Superficie administrativa y matriz por endpoint

La columna **superadmin** describe el contrato global posterior a M6. **Admin** indica tanto el filtro obligatorio como sus límites de mutación. Donde dice «denegado» la ruta continúa siendo exclusiva de plataforma. Los recursos se ocultan con `404` si el identificador apunta fuera del tenant.

### 2.1 `backend/api/admin/**`

| Endpoint | `superadmin` | `admin` |
|---|---|---|
| `GET /api/admin/config` | Configuración global, sin filtro | Denegado |
| `PUT /api/admin/config` | Actualiza configuración global, sin filtro | Denegado |
| `GET /api/admin/credentials` | Todas las credenciales de plataforma | Denegado |
| `POST /api/admin/credentials/{provider_id}/rotate` | Rota credencial global | Denegado |
| `POST /api/admin/credentials/{provider_id}/revoke` | Revoca credencial global | Denegado |
| `POST /api/admin/credentials/{provider_id}/test` | Prueba credencial global | Denegado |
| `GET /api/admin/dashboard` | Tablero agregado de todos los tenants y salud de plataforma; filtro de tenant opcional para las métricas de tenant | Solo agregados de su tenant: uso, hechos, pipelines y usuarios. Sin salud de servicios, RAM, DB, llaves, credenciales ni cifras de otros tenants |
| `GET /api/admin/facet-bindings` | Catálogo global de bindings | Denegado |
| `PUT /api/admin/facet-bindings/{facet_key}` | Cambia binding global | Denegado |
| `GET /api/admin/keys` | Catálogo global de llaves | Denegado |
| `GET /api/admin/tenants` (endpoint nuevo; directorio de tenants) | Directorio de tenants existentes, apto para elegir destinos administrativos | Denegado |
| `POST /api/admin/keys/{provider_id}/test` | Prueba llave global | Denegado |
| `GET /api/admin/kill-switch` | Estado global | Denegado |
| `POST /api/admin/kill-switch/activar` | Activa freno global | Denegado |
| `POST /api/admin/kill-switch/reanudar` | Reanuda plataforma global | Denegado |
| `GET /api/admin/memoria/hechos` | Todos los hechos, sin filtro por tenant por defecto; filtro opcional | Solo hechos ordinarios cuya pertenencia verificable corresponde a su tenant; hechos de `user_id` admin/superadmin se excluyen como 404 si se piden por id; paginación y filtros después del alcance |
| `POST /api/admin/memoria/hechos/aprobar` | Aprueba hechos de cualquier tenant | Solo hechos ordinarios del tenant; hechos asociados a `user_id` admin/superadmin se ocultan como 404; alcance y rol comprobados dentro de la transacción que bloquea los hechos; lote todo-o-nada |
| `POST /api/admin/memoria/hechos/{fact_id}/corregir` | Corrige hecho de cualquier tenant | Solo hecho ordinario del tenant; hechos de usuario admin/superadmin se ocultan como 404; alcance y rol comprobados bajo el mismo bloqueo transaccional |
| `POST /api/admin/memoria/hechos/fundir` | Funde hechos de cualquier tenant | Solo conjunto íntegro de hechos ordinarios del tenant; cualquier hecho ajeno o de usuario admin/superadmin rechaza el lote como 404 sin mutación |
| `POST /api/admin/memoria/hechos/{fact_id}/caducar` | Caduca hecho de cualquier tenant | Solo hecho ordinario del tenant; hechos de usuario admin/superadmin se ocultan como 404; alcance y rol comprobados bajo el mismo bloqueo transaccional |
| `GET /api/admin/memoria/grupos` | Grupos globales y de todos los tenants | Grupos, miembros, vecinos y cierre transitivo de citas confinados al tenant y a usuarios ordinarios; excluye hechos de cuentas admin/superadmin |
| `GET /api/admin/models` | Catálogo global de modelos | Denegado |
| `PUT /api/admin/models/{model_ref}/contrato-dispatch` | Cambia contrato global | Denegado |
| `POST /api/admin/models/sync` | Sincronización global | Denegado |
| `GET /api/admin/models/sync/estado` | Estado de sync global | Denegado |
| `GET /api/admin/models/sync/config` | Configuración global de sync | Denegado |
| `PUT /api/admin/models/sync/config` | Cambia configuración global de sync | Denegado |
| `GET /api/admin/models/proposals` | Propuestas globales | Denegado |
| `POST /api/admin/models/proposals/{proposal_id}/approve` | Aprueba propuesta global | Denegado |
| `POST /api/admin/models/proposals/{proposal_id}/reject` | Rechaza propuesta global | Denegado |
| `GET /api/admin/motors` | Catálogo global de motores | Denegado |
| `POST /api/admin/motors` | Crea motor global | Denegado |
| `PATCH /api/admin/motors/{key}` | Cambia motor global | Denegado |
| `GET /api/admin/pipelines/ocultos` | Todos los pipelines ocultos de cualquier tenant; filtro opcional | Solo pipelines de usuarios ordinarios del tenant, con `tenant_id` derivado del actor |
| `GET /api/admin/pipelines/descartados` | Todos los descartados de cualquier tenant; filtro opcional | Solo descartados de usuarios ordinarios del tenant, con `tenant_id` derivado del actor |
| `GET /api/admin/repo` | Repositorio global de la plataforma | Denegado |
| `GET /api/admin/repo/file?target_tenant_id=…&pipeline_id=…` | Lee salida de repositorio perteneciente al tenant indicado; lectura cross-tenant permitida | Denegado |
| `DELETE /api/admin/repo/file` | Borra salida perteneciente al tenant indicado solo si la compuerta §8 permite esa mutación; exige `target_tenant_id` | Denegado |
| `GET /api/admin/smtp` | Configuración SMTP global | Denegado |
| `PUT /api/admin/smtp` | Cambia SMTP global | Denegado |
| `POST /api/admin/smtp/test-connection` | Prueba conexión SMTP global | Denegado |
| `POST /api/admin/smtp/test` | Envía prueba desde SMTP global | Denegado |
| `GET /api/admin/usage` | Uso agregado de todos los tenants; filtro opcional | Solo filas y agregados de usuarios ordinarios del tenant del actor |
| `GET /api/admin/users` | Todos los tenants; filtro opcional | Solo cuentas ordinarias del tenant del actor, incluyendo bajas; las cuentas admin/superadmin se omiten y consultar su id da 404 |
| `POST /api/admin/users` | Crea usuario en tenant destino explícito y existente; puede crear `admin` | Crea invitación pendiente para `operator`/`viewer` solo en su tenant; `tenant_id` suministrado se rechaza; no reserva correo ni crea cuenta hasta confirmar el buzón |
| `PUT /api/admin/users/{user_id}` | Actualiza usuario de cualquier tenant excepto la cuenta protegida; puede asignar/quitar `admin` sujeto a invariantes | Actualiza usuarios de su tenant con rol `operator` o `viewer`; no puede asignar/quitar roles elevados ni editar cuentas `admin`/`superadmin`; cambio de correo aplica §4 |
| `POST /api/admin/users/{user_id}/unlock` | Desbloquea usuario de cualquier tenant excepto la cuenta protegida | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/revoke-sessions` | Revoca sesiones en cualquier tenant excepto la cuenta protegida | Solo usuario ordinario del tenant |
| `GET /api/admin/users/{user_id}/audit` | Historial del usuario de cualquier tenant | Solo historial de usuario ordinario del tenant; sin datos de otros tenants |
| `POST /api/admin/users/{user_id}/reset-link` | Emite enlace para usuario de cualquier tenant, excepto la cuenta protegida | Solo usuario ordinario del tenant; no emite automáticamente un reset-link al crear una cuenta cuyo correo el destinatario aún no confirmó |
| `POST /api/admin/users/{user_id}/password` | Fija contraseña de usuario de cualquier tenant, excepto la cuenta protegida | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/baja` | Da de baja usuario de cualquier tenant, excepto la cuenta protegida; respeta el último-superadmin global | Solo usuario ordinario del tenant |

`GET /api/admin/tenants` es un endpoint nuevo, exclusivo de `superadmin`; devuelve `tenant_id`, nombre visible y estado activo/inactivo, sin usuarios, correos ni otros datos personales. Incluye tenants inactivos para que el operador pueda distinguirlos, pero `POST /api/admin/users` solo acepta destinos activos. No permite mutaciones.

`/api/admin/repo/file` representa salidas tenant-scoped generadas por pipelines, no archivos globales. Cada salida se identifica por `pipeline_id`; toda lectura o borrado exige `target_tenant_id` y la pertenencia se resuelve desde la fila autoritativa del dueño del pipeline. Lectura cross-tenant de superadmin está permitida; borrar salida de otro tenant queda denegado por §8 y toda mutación admitida registra `target_tenant_id`. No se acepta una ruta de archivo arbitraria del cliente.

El dashboard no expone a `admin` campos globales simplemente filtrando algunas consultas: su respuesta se construye con una proyección tenant-only y omite cuentas admin/superadmin y sus datos. Sus consultas de uso, usuarios, pipelines y memoria aplican alcance y rol del propietario en SQL antes de ordenar, paginar o agregar. El superadmin obtiene todos los tenants cuando no proporciona filtro.

### 2.2 Rutas fuera de `backend/api/admin/**` protegidas por `require_superadmin` o comparación equivalente

| Endpoint | `superadmin` | `admin` |
|---|---|---|
| `GET /api/audit` | Auditoría forense global, todos los tenants | Denegado |
| `POST /api/facets/{facet}/status` | Cambia estado global de faceta | Denegado |
| `GET /api/ejecutor/estado` | Estado global del Ejecutor | Denegado |
| `POST /api/ejecutor/pausa/poner` | Pausa Ejecutor global | Denegado |
| `POST /api/ejecutor/pausa/quitar` | Reanuda Ejecutor global | Denegado |
| `GET /api/ejecutor/misiones` | Todas las misiones globales | Denegado |
| `POST /api/ejecutor/misiones` | Crea misión global | Denegado |
| `GET /api/ejecutor/repos` | Repos del Ejecutor global | Denegado |
| `GET /api/ejecutor/misiones/{mision_id}` | Cualquier misión global | Denegado |
| `GET /api/ejecutor/misiones/{mision_id}/bitacora` | Bitácora global | Denegado |
| `POST /api/ejecutor/misiones/{mision_id}/turnos` | Continúa misión global | Denegado |
| `GET /api/pipelines/{pipeline_id}` y `GET /api/pipelines/{pipeline_id}/results` | Lee por id cualquier tenant, incluso ocultos | Solo pipeline de usuario ordinario de su tenant; ajeno o dueño admin/superadmin = 404 |
| `POST /api/pipelines/{pipeline_id}/resume` | Mutación de su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/cancel` | Mutación de su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/discard` | Mutación de su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/continue` | Mutación de su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/continue/preflight` | Preflight de lectura por id en cualquier tenant; no inicia ni altera pipeline | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/hide` | Mutación de su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/restore` | Mutación de su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/recover` | Recupera en su tenant; cross-tenant denegada hasta la compuerta de §8 | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `GET /api/pipelines/{pipeline_id}/auditoria-descarte` | Lee auditoría por id de cualquier tenant | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| Listados de pipelines ocultos/descartados y uso | Todos los tenants, filtro opcional; excluye únicamente lo que la cuenta protegida resguarda | Solo pipelines/uso de usuarios ordinarios del tenant; nunca los de admin/superadmin |

La clasificación de `_require_pipeline_owner` (hoy exige mismo `user_id` Y `tenant_id`, con excepción de lectura de ocultos solo para superadmin) queda reemplazada por el resolvedor central: `superadmin` puede leer por id pipelines de cualquier tenant y leer los ocultos; `admin` solo accede a pipelines de usuarios ordinarios de su tenant; `operator`/`viewer` conservan el ownership actual. En `/results`, `/resume`, `/cancel`, `/discard`, `/continue` y `/continue/preflight` se aplica la clasificación de la matriz anterior; también a `GET /{pipeline_id}`, `hide`, `restore`, `recover`, auditoría y listados. Cada lectura o mutación tenant-scoped resuelve el tenant real del recurso; `target_tenant_id` es obligatorio en la auditoría de las mutaciones y coincide con ese tenant. Lecturas cross-tenant de superadmin se permiten; toda mutación cross-tenant queda denegada hasta §8. El preflight debe permanecer de solo lectura para habilitar lectura cross-tenant. No se replica `role == "superadmin"` en código de chat, pipeline u otras superficies. Los filtros de tenant y rol del dueño se aplican antes de devolver, ordenar, paginar o agregar.

### 2.3 Superficie de plataforma explícita

Solo `superadmin` accede a credenciales, kill-switch, SMTP, configuración, modelos, motores, bindings, repositorio, Ejecutor, directorio de tenants y auditoría forense. Estas superficies no se convierten en tenant-scoped para `admin`: se deniegan. La cuenta protegida de Fernando está exceptuada de las mutaciones administrativas globales. `superadmin` puede leer datos entre tenants y operar dentro de su propio tenant; toda mutación que cruce tenant(s) queda bloqueada hasta cumplir §8.

## 3. Gestión y ciclo de vida de usuarios

### 3.1 Cuenta protegida de Fernando

La cuenta de Fernando es intocable para cualquier otro principal, incluidos todos los `superadmin`: nadie puede quitarle el rol, cambiar su estado, borrarla/darla de baja, cambiar correo, contraseña o tenant, ni revocar sus sesiones. Solo la sesión autenticada de Fernando puede efectuar esas acciones sobre su propia cuenta por los flujos de autoservicio que correspondan, con la excepción de recuperación indicada abajo. La protección identifica la cuenta por `user_id` estable, nunca por correo, tenant, rol ni nombre. Ese id vive en una tabla dedicada de identidad de plataforma, insertada solo por migración o comando root de operación; la aplicación no tiene endpoint ni ruta de escritura para ese registro. El resolvedor exige un único registro válido que apunte a una única fila de `jax_users`; si falta, hay duplicidad, inconsistencia o no existe la fila objetivo, toda operación administrativa que pudiera modificar una cuenta falla cerrada. No se busca sustituto por correo ni se deja que el cliente elija el id protegido. La regla cubre todas las superficies, endpoints, tareas y operaciones por lote, y se aplica antes de bloquear o mutar el objetivo. El `user_id` protegido nunca se almacena en `axioma_config` ni puede cambiarse mediante `PUT /api/admin/config`.

**Recuperación protegida (decisión técnica de Hyde, estricta, para ratificación de Fernando):** la cuenta protegida de Fernando NUNCA se recupera por correo. `forgot-password` con su correo responde lo mismo que a cualquier correo (sin revelar nada) pero NO crea token ni envía nada. Su recuperación es solo por un comando de operación en hall9000 ejecutado por root que exige un código TOTP válido de Fernando (el mismo secreto TOTP de su ventana; no lo duplica ni lo expone), auditado. El bloqueo automático por intentos fallidos SÍ aplica a su cuenta (protege contra fuerza bruta), con desbloqueo solo por ese mismo comando (o por expiración del bloqueo si ya existe), nunca por otro superadmin.

Cambiar la configuración SMTP o `FRONTEND_ORIGIN` requiere autorización global y deja auditoría durable con actor, campos cambiados y request/trace id; tras commit se notifica a Fernando por Telegram. La notificación no incluye secretos SMTP. Ningún reset token, incluso uno emitido antes del cambio, permite recuperar la cuenta protegida.

1. Solo un `superadmin` crea un `admin`, promueve un usuario a `admin`, degrada o desactiva un `admin`, y crea/promueve/degrada/desactiva otro `superadmin`. El `admin` no puede crear ni modificar cuentas `admin` o `superadmin`. Puede gestionar `operator` y `viewer` ordinarios de su tenant. No se impone mínimo de admins por tenant; el superadmin de plataforma es la vía de recuperación.
2. Para crear usuarios en otro tenant, `superadmin` debe elegir un `tenant_id` existente mediante el directorio `GET /api/admin/tenants`, legible solo por `superadmin`, para que el destino no se invente ni se descubra mediante ensayo. Crear `admin` requiere además confirmar transaccionalmente el tenant destino y sincronizar su membresía administrativa derivada en JAX.
3. Crear o cambiar un rol actualiza fila de usuario, `token_version`, autoridad derivada JAX y auditoría en una sola transacción. Un fallo de validación, escritura de auditoría o sincronización de membresías revierte el conjunto. Tras commit se cortan las sesiones/conexiones que deban perder autoridad.
4. `admin` no puede apuntar a otro tenant mediante ruta, lote, query, header ni body. `tenant_id` de una cuenta es inmutable por API para todos los roles. Una migración de tenant de usuario aborta siempre; no existe flujo de migración que cambie `tenant_id`, y nunca toca la cuenta protegida. El tenant del actor viene de la fila vigente de `jax_users`. La mutación verifica tenant y rol ordinario del objetivo en la misma transacción y bajo el mismo bloqueo que el cambio.
5. El invariante «siempre queda al menos un superadmin activo» pasa a ser global a la plataforma, no por tenant. Los cambios concurrentes que podrían quitar el último superadmin se serializan con el mutex global y el orden de candados de §3.2. Admins no pueden alterar esta clase de cuenta y ningún otro actor puede modificar la cuenta protegida.
6. El código JAX debe reconocer `admin` con semántica de tenant y `superadmin` con semántica global antes de que se habilite la primera cuenta `admin`. Un actor global no se presenta como miembro de un tenant ajeno ni falsifica `ScopeContext`: se define y valida en JAX un contrato explícito para la autoridad de plataforma, leído de la cuenta activa en `jax_users`. Se retira el alias administrativo `super_admin`; solo los valores canónicos exactos de esta spec conceden esas capacidades.

### 3.2 Serialización y orden total de candados

Las operaciones administrativas que puedan tocar más de un tenant usan un mutex global compartido por todos los escritores, además de los candados por tenant. El mutex es una fila singleton precreada en una tabla dedicada: la transacción la adquiere con `SELECT … FOR UPDATE` y la conserva hasta commit/rollback; no se usa `GET_LOCK` ni un mutex en memoria. Toda ruta adquiere candados en un único orden total: mutex global → candado(s) de tenant por `tenant_id` ascendente → actor → objetivo. Para una operación con varios objetivos, se bloquean los ids de objetivo en orden ascendente después del actor; no se adquiere un candado de orden previo mientras se conserva uno posterior. Al comprobar el rol/tenant del dueño de una fila (p. ej. hecho o pipeline), la fila de `jax_users` se lee con `SELECT … FOR SHARE` después del candado del tenant y antes de bloquear/mutar el objetivo; esa lectura compartida permanece hasta commit/rollback. El dueño, tenant y rol se validan en esa misma transacción. La implementación y todas las rutas de mutación deben usar el mismo protocolo; si no pueden obtener el conjunto completo, abortan antes de escribir. Esto evita ciclos de espera entre solicitudes concurrentes y hace atómico el conteo del último superadmin.

## 4. Email único global

Se conserva el índice/constraint global `UNIQUE(email)` y la respuesta `409 email_ya_existe` sin revelar tenant, user id ni estado. El residuo real es que un `admin` autenticado puede usar el `409` como oráculo para saber si un correo está ocupado en cualquier tenant y puede acaparar una dirección global si la alta o el cambio de correo reserva la dirección antes de que su titular la confirme. Se acepta el oráculo residual con estas mitigaciones obligatorias: (a) todo `409` de alta o cambio de correo por `admin`, tanto por chequeo previo como por colisión concurrente del constraint, escribe un evento de auditoría durable con `actor_user_id`, `actor_tenant_id`, operación, resultado, origen y request/trace id, sin guardar el correo consultado; la respuesta al cliente nunca incluye tenant, user id ni estado. La auditoría del 409 sobrevive al rollback de la mutación; si no puede escribirse, la operación falla cerrada; (b) `axioma_config` define `admin.users.email_attempts.max_per_hour`, tope de intentos por administrador y hora compartido entre altas y cambios de correo. Se identifica al actor por `user_id`; cada intento, incluido 409, incrementa en una tabla durable de contadores en base de datos dentro de la transacción serializada, nunca en memoria por worker. El límite uniforme impide nuevas altas y cambios de correo; si falta la clave o no tiene valor válido y finito, esas operaciones quedan denegadas; (c) la alta por `admin` crea una invitación pendiente, no una fila en `jax_users`, y no reserva `UNIQUE(email)` hasta que el destinatario confirma control del buzón y el tenant que acepta. El cambio de correo por `admin` tampoco reserva el nuevo correo ni lo aplica antes de esa confirmación. No se envía reset-link ni credencial; solo se envía el enlace de verificación necesario para que el titular complete explícitamente la aceptación. El `superadmin` conserva el comportamiento actual de alta y entrega de enlace, sujeto a la protección de cuenta de §3.1.

El flujo de pendientes se aloja en una tabla dedicada, sin índice único global sobre correo: admite varias altas/cambios pendientes para el mismo correo en distintos tenants y no expone si el correo ya pertenece a otro tenant. Cada registro guarda `invite_id`, tipo (`create` o `change_email`), tenant destino, rol ordinario permitido o `target_user_id`, correo normalizado cifrado, hash del token, actor y expiración; el token en claro solo se entrega al correo y nunca se guarda en logs. `POST /api/admin/users` devuelve al admin el enlace opaco/`invite_id`, que este comparte con el destinatario por un canal acordado fuera del correo no confirmado. El destinatario abre ese enlace; `POST /api/public/invitations/{invite_id}/verification` recibe el correo y solicita la verificación del buzón con respuesta/tiempo uniformes. Solo entonces se envía el enlace de verificación. Ese enlace muestra el tenant y exige una acción explícita «acepto este tenant» en `POST /api/public/invitations/{invite_id}/confirm` antes de confirmar. Para varias invitaciones al mismo correo, solo gana la primera confirmación explícita y válida; confirma únicamente la invitación/tenant seleccionado y revoca las demás invitaciones pendientes para ese correo. En un cambio de correo, la confirmación vuelve a autorizar y bloquear al actor, tenant y usuario ordinario; verifica que la cuenta no sea la protegida, cambia el correo, incrementa `token_version`, revoca sesiones y audita en la misma transacción. No se libera ni modifica el correo vigente hasta ese commit. El token es de un solo uso, tiene TTL máximo de 24 horas, y puede revocarlo el admin emisor mientras siga pendiente. Los endpoints públicos aplican límite configurable y fail-closed por IP y por hash de correo; los intentos y revocaciones quedan auditados sin registrar el correo. Confirmar en carrera bloquea las invitaciones del correo en orden estable, verifica aún no existir una cuenta con ese correo, materializa la cuenta en `jax_users` para `create` o aplica el cambio descrito para `change_email`, y consume/revoca todas las invitaciones en una transacción; un conflicto devuelve respuesta neutra y no deja reservas. El límite por correo no permite descubrir cuentas existentes.

| Operación | Actor y ruta | Contrato |
|---|---|---|
| Crear pendiente | `admin`: `POST /api/admin/users` | Registra solo `operator`/`viewer` para su tenant y devuelve un `invite_id` opaco; no crea usuario ni reserva correo. |
| Iniciar verificación | Público: `POST /api/public/invitations/{invite_id}/verification` | Recibe correo; respuesta y tiempo uniformes; solo envía verificación si coincide con pendiente vigente. Límite por IP y hash de correo. |
| Confirmar alta/cambio | Público: `POST /api/public/invitations/{invite_id}/confirm` | Token de un uso más aceptación explícita del tenant mostrado; atomiza alta o cambio de correo con auditoría, consume invitación y revoca duplicadas del correo. |

El chequeo previo solo mejora el mensaje. El constraint de DB sigue siendo la autoridad ante altas concurrentes, también entre tenants, y todo duplicado recibe el mismo 409. Los intentos y auditoría del 409 se confirman aun cuando la inserción falla: la inserción de cuenta se aísla con savepoint y, ante colisión, se revierte solo a ese savepoint; contador y auditoría se conservan y se confirman en la transacción exterior. Si no se puede registrar ambos, la operación falla cerrada y no devuelve el 409. El alta de `admin` no devuelve ni registra cuál tenant ya usa el correo. Las reglas actuales de baja y liberación de correo se conservan. La invitación pendiente no envía correo por iniciativa del admin: el destinatario debe iniciar la solicitud pública de verificación con el identificador de invitación que recibió por un canal acordado. Solo esa solicitud envía el enlace de verificación; no se envían invitaciones ni mensajes a una dirección no confirmada.

## 5. Columna `role` y migración

En el esquema actual `jax_users.role` es `VARCHAR(20) DEFAULT 'operator'`, no `ENUM`. Por tanto no se propone una conversión ficticia de enum. La migración aditiva establece el conjunto exacto `superadmin`, `admin`, `operator`, `viewer`, mantiene `VARCHAR(20)`, lo declara `NOT NULL`, fija la intercalación de la columna a `utf8mb4_bin` y añade un `CHECK` con esa misma semántica binaria en creación nueva y bases existentes. Tanto el `CHECK` como el middleware comparan los bytes exactos con las cuatro literales canónicas; por ejemplo, `SUPERADMIN`, `Admin` y valores con espacios o bytes distintos no coinciden.

Antes del DDL, la migración cuenta roles agrupados por bytes, `NULL` y longitudes inválidas. Si aparece cualquier valor que no sea idéntico byte a byte a una de las cuatro literales, es nulo o excede 20 caracteres, aborta cerrada mostrando valor escapado/hex y conteo para resolverlo explícitamente; no lo convierte ni recorta. La misma comprobación vuelve a ejecutarse dentro de la migración antes de cambiar collation/constraint. La medición de producción recibida para 2026-10-06 reportó `superadmin` (1) y `operator` (2) en un tenant; debe repetirse inmediatamente antes de aplicar la migración. Se conserva `viewer` porque el código y las pruebas lo reconocen, aunque no apareciera en esa medición.

El middleware debe rechazar cualquier rol desconocido aunque exista el CHECK: cubre bases legadas, corrupción y desfases de versión. Hoy ese rechazo no está implementado en `verificar_sesion` (`backend/auth/middleware.py:64-96`) y es trabajo pendiente, no comportamiento actual. También `jax/memory/scope_authority.py:20` normaliza roles con `.lower()`; debe dejar de hacerlo y comparar exactamente los valores canónicos, compartiendo el contrato con JAX. La migración es idempotente conforme al patrón existente y tiene downgrade documentado; si ya existen admins, una versión anterior no debe ejecutarse hasta revertir de forma explícita esos roles con una sesión autorizada. La DB no altera roles por sí sola.

## 6. Auditoría de cambios de rol

Cada asignación, promoción, degradación o remoción efectiva de rol deja evidencia atómica con la mutación. Se reutiliza `user_admin_audit`, ampliándola con `actor_role_at_action`, `actor_tenant_id` y `target_tenant_id`, y un índice que permita consultar por tenant y objetivo. El evento conserva como mínimo:

- timestamp UTC;
- actor y rol/tenant vigentes al actuar;
- usuario objetivo y su tenant;
- `from_role` y `to_role` (incluido `null` solo para alta, si el contrato del evento así lo representa);
- IP derivada del proxy confiable;
- request/trace id obligatorio, generado en el punto de entrada si no llega uno válido y propagado sin perderlo hasta la transacción y el evento;
- transporte de entrada (`HTTP`, `WS` o `CLI`) y punto de entrada/operación que inició el cambio.

No se reconstruye un rol histórico desde la cuenta actual. Para filas anteriores, los campos no disponibles se guardan como `NULL`/`legacy_unknown`, nunca fabricados. Toda ruta de lectura valida alcance: superadmin puede consultar entre tenants y `admin` solo auditoría de usuarios ordinarios de su tenant. Si en el historial o en un evento de baja el actor corresponde a cualquier cuenta `admin` o `superadmin`, al `admin` se le muestra «administración»; no se revela correo ni otro dato identificable, aunque sea del mismo tenant. La misma ocultación aplica a los eventos de bajas. El cambio, incremento de `token_version`, sincronización JAX y evento se confirman juntos; si uno falla, ninguno queda aplicado.

## 7. Resolución tenant de hechos y otras filas

Para un `admin`, un hecho pertenece al tenant de su `user_id` según la fila vigente de `jax_users`, siempre que el usuario sea ordinario (`operator`/`viewer`). Hechos cuyo `user_id` sea `admin` o `superadmin` quedan fuera del alcance admin aunque compartan tenant: corregir, aprobar, caducar o fundirlos responde `404` como si el id fuera ajeno; lo mismo aplica a consulta por id. Si un hecho no tiene usuario y está asociado a proyecto, pertenece al `tenant_id` autoritativo de `jax_project_scope`. Si ambas referencias existen, deben coincidir. Un hecho sin usuario ni proyecto es global y solo lo ve/opera `superadmin`. Filas inconsistentes o de pertenencia irresoluble quedan ocultas al `admin`, generan señal de integridad y no se reparan implícitamente.

La comprobación se realiza dentro de la transacción y bloqueo de cada mutación. Fusión y operaciones por lote son todo-o-nada; citas, agrupamientos y recorrido transitivo no cruzan tenants ni incluyen hechos de usuarios admin/superadmin para un admin. Ocultar/restaurar pipelines, listar ocultos/descartados y leer uso de cuentas admin/superadmin también quedan fuera del alcance admin y responden 404 al consultar un objetivo por id. Las consultas de pipelines, usuarios, uso y dashboard incluyen filtros de tenant y rol del dueño en SQL antes de ordenar, paginar o agregar. El superadmin no hereda un filtro accidental de su propio tenant.

## 8. Dependencias entre repositorios y despliegue

**Compuerta verificable para escrituras entre tenants:** es una constante de código/versionado derivada de capacidades y versiones de esquema auditadas, nunca una clave de `axioma_config` ni dato que la aplicación pueda cambiar. Hasta que existan y estén implementados/auditados en ambos repositorios (1) el permiso explícito `PLATFORM_ALL` y el contrato global correspondiente en JAX —incluida autoridad leída de `jax_users` sin membresía tenant falsificada—, (2) el mutex global por fila y el orden total de candados de §3.2 compartidos por todos los escritores, y (3) `target_tenant_id` obligatorio en la auditoría de cada mutación que tiene objetivo tenant, las lecturas cross-tenant de superadmin están permitidas y toda mutación cross-tenant está denegada. La guarda se evalúa en backend antes de cualquier efecto; una falta, error o versión parcial de cualquiera de los tres requisitos conserva el bloqueo. Esta prohibición cubre cuentas, roles, sesiones, memoria, pipelines (incluidos hide/restore/recover/resume/cancel/discard/continue), salidas de `/api/admin/repo/file`, uso y cualquier otra superficie tenant-scoped. Mutar dentro del propio tenant y las operaciones globales de plataforma siguen sus permisos específicos. Al satisfacer la compuerta, se habilita la escritura entre tenants para el superadmin, excepto sobre la cuenta protegida de Fernando. `PUT /api/admin/config` no puede alterar esta constante ni el `user_id` protegido; solicitudes que intenten escribir claves para esos valores se rechazan y no cambian estado.

El rol `admin` no se habilita hasta que plataforma y JAX implementen y auditen este contrato en ambos lados. En particular, JAX debe soportar creación/promoción global por superadmin y administración de tenant por admin, sin inferencias desde alias o membresías falsas. La interfaz puede reflejar permisos luego del backend, pero no se despliega ninguna capacidad antes de que su control de autoridad exista en ambos repositorios (Principio IX).

La spec no autoriza migrar producción, emitir cuentas admin, desplegar ni integrar cambios. Cada acto externo conserva sus GO, ventana o regla vigente aplicables. La decisión conceptual de Fernando aquí registrada es la fuente del modelo; los detalles técnicos de implementación siguen sujetos a revisión adversarial antes de habilitarlo.

## 9. Criterios verificables de aceptación

- Matriz completa por endpoint para superadmin (lecturas globales, escrituras cross-tenant bajo compuerta), admin propio/ajeno, operator, viewer y rol desconocido; incluye el directorio de tenants.
- El superadmin cuyo tenant hogar es A lee tenant B; mientras falte cualquier condición de §8, una escritura a B es denegada antes de producir efectos. Cumplida la compuerta, las escrituras admitidas no usan un predicado derivado de A; el filtro opcional solo acota lecturas.
- Un admin no puede alterar su alcance con JWT, query, body, lote ni header; IDs ajenos responden 404 y una solicitud por lote no deja cambios parciales.
- Claims JWT que afirman `superadmin` con fila DB `operator` no elevan acceso; filas con rol desconocido no reciben capacidades HTTP, WS, SSE o refresh.
- Admin no obtiene respuestas o KPI de plataforma a través del dashboard ni acceso indirecto a modelos, SMTP, credenciales, kill-switch, Ejecutor o auditoría global.
- `409 email_ya_existe` es idéntico para colisiones dentro/entre tenants y bajo carrera concurrente; cada 409 de alta o cambio de correo admin queda auditado y cuenta contra el límite compartido configurable por actor, con contador durable en DB. Ni altas ni cambios de correo reservan el email antes de confirmación del destinatario.
- Una cuenta identificada solo por el `user_id` protegido de Fernando no puede ser modificada por otro usuario admin/superadmin en ninguna superficie; configuración ausente o incongruente falla cerrada.
- `forgot-password` para el correo protegido responde neutro y no crea token ni envía correo; un reset con cualquier token para esa cuenta falla. Cambiar SMTP o `FRONTEND_ORIGIN` deja auditoría y notifica a Fernando. El bloqueo automático por intentos fallidos funciona para la cuenta protegida; su desbloqueo solo ocurre por expiración existente o por el comando root de hall9000 con TOTP válido de Fernando, auditado.
- Prueba negativa extremo a extremo: el actor `S` cambia SMTP o `FRONTEND_ORIGIN`, solicita `forgot-password` para el correo protegido y no se crea token ni se envía correo; ejecutar `/reset-password` con cualquier token para esa cuenta falla. El cambio deja auditoría y notificación a Fernando.
- `PUT /api/admin/config` no puede cambiar el id protegido ni la constante/versionado de la compuerta §8; intentarlo no cambia esos valores.
- Altas y cambios de correo de admin comparten el límite por actor en contador durable de base; los 409 quedan auditados. La invitación pendiente no reserva el correo, permite varios tenants, exige selección explícita del tenant en el buzón, vence en 24 horas, es de un uso, revocable y con límite de tasa por IP/correo.
- `GET /api/admin/repo/file` solo lee salida por `pipeline_id` tras resolver su tenant; `DELETE` exige `target_tenant_id`, aplica §8 y audita el tenant objetivo.
- La migración de tenant aborta siempre y no puede modificar la cuenta protegida.
- El rol DB usa `utf8mb4_bin`; CHECK, middleware y JAX aceptan únicamente los bytes exactos de las cuatro literales; cualquier valor restante aborta la migración y el middleware lo rechaza.
- `_require_pipeline_owner` y todos los endpoints por id aplican la matriz de §2.2; admin nunca lee ni muta pipelines de cuentas admin/superadmin, y operator/viewer conservan ownership.
- La concurrencia de escritura toma candados solo en orden global → tenants por id ascendente → actor → objetivos por id ascendente; pruebas de conflicto verifican ausencia de deadlock y rollback completo.
- Carrera de democión/baja de los últimos superadmins: al menos una operación falla y siempre queda uno activo.
- Mutación de rol, sesiones, membresías JAX y auditoría revierten juntas bajo fallos inyectados; auditoría no inventa datos históricos y siempre lleva request/trace id obligatorio, generado/propagado desde HTTP, WS o CLI.
- Hechos huérfanos, globales, inconsistentes, lotes mixtos y citas cruzadas se prueban con el contrato de §7.
- El contrato JAX y el contrato de plataforma reconocen exactamente los mismos roles y alcances antes de crear el primer admin.
- `EXPLAIN` verifica índices para filtros tenant de toda consulta afectada; suites prueban autorización negativa/positiva por endpoint y casos de concurrencia.
- Dashboard, usuarios, uso, memoria y pipelines se someten a carga en el peor caso antes de producción; las mediciones registran concurrencia, RPS, p95 y punto de degradación.

## 10. Trazabilidad de hallazgos — ronda 3

| Hallazgo | Cierre en esta spec |
|---|---|
| BLOCK: SMTP → `forgot-password` → reset de la cuenta protegida | §3.1: `forgot-password` neutro sin token/envío; reset siempre falla; recuperación solo por comando root en hall9000 con TOTP existente, bloqueo anti-fuerza-bruta y auditoría. Cambios SMTP/`FRONTEND_ORIGIN` se auditan y notifican por Telegram. §9 agrega el criterio negativo de extremo a extremo. |
| M-A: id protegido y compuerta modificables por config | §3.1 fija identidad en tabla dedicada sin escritura de aplicación; §8 fija la compuerta como constante versionada. §9 exige probar que `PUT /api/admin/config` no cambia ninguna. |
| M-B: cambio de correo admin sin mitigaciones M1 | §2.1 aplica §4 al `PUT`; §4 extiende auditoría durable de 409, límite horario compartido con altas y confirmación antes de reserva también a cambios de correo. |
| M-C: invitación pendiente incompleta | §4 define tabla, múltiples invitaciones por correo/tenant, aceptación explícita del tenant, primera confirmación válida, TTL 24h, un uso, revocación y límites de tasa. El admin no envía invitación: el destinatario inicia el correo de verificación. |
| M-D: `/api/admin/repo/file` clasificado global | §2.1 lo reclasifica como salida tenant-scoped por `pipeline_id`, exige `target_tenant_id`, aplica lectura cross-tenant y compuerta para borrado; §8 lo incluye expresamente. |
| m-1: bloqueo del dueño durante autorización | §3.2 exige `SELECT … FOR SHARE` de `jax_users` después del candado de tenant y antes del objetivo, retenido hasta terminar la transacción. |
| m-2: contrato de `GET /api/admin/tenants` | §2.1 lo declara endpoint nuevo; define campos, exclusión de datos personales, inclusión de inactivos y destinos activos únicamente para altas. |
| m-3: contradicción sobre migrar tenant | §3.1 punto 4: toda migración de tenant aborta y nunca toca la cuenta protegida. |
| m-4: mutex y conteo por hora | §3.2 define fila singleton y `SELECT … FOR UPDATE`, descartando `GET_LOCK`; §4 usa contador durable de DB para el límite compartido. |
| m-5: identidad de actores elevados en historial/bajas | §6 oculta cualquier actor `admin` o `superadmin` como «administración», sin correo ni identificadores, incluido mismo tenant y eventos de baja. |
| m-6: filas de endpoints inconsistentes con §8 | §2.1/§2.2 y §8 uniforman las operaciones tenant-scoped: lectura cross-tenant permitida, mutación cross-tenant bloqueada hasta compuerta y `target_tenant_id` auditable obligatorio. |
