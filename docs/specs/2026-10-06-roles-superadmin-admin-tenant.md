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
| `GET /api/admin/tenants` (directorio de tenants) | Directorio de tenants existentes, apto para elegir destinos administrativos | Denegado |
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
| `GET /api/admin/repo/file` | Lee archivo global autorizado | Denegado |
| `DELETE /api/admin/repo/file` | Borra archivo global autorizado | Denegado |
| `GET /api/admin/smtp` | Configuración SMTP global | Denegado |
| `PUT /api/admin/smtp` | Cambia SMTP global | Denegado |
| `POST /api/admin/smtp/test-connection` | Prueba conexión SMTP global | Denegado |
| `POST /api/admin/smtp/test` | Envía prueba desde SMTP global | Denegado |
| `GET /api/admin/usage` | Uso agregado de todos los tenants; filtro opcional | Solo filas y agregados de usuarios ordinarios del tenant del actor |
| `GET /api/admin/users` | Todos los tenants; filtro opcional | Solo cuentas ordinarias del tenant del actor, incluyendo bajas; las cuentas admin/superadmin se omiten y consultar su id da 404 |
| `POST /api/admin/users` | Crea usuario en tenant destino explícito y existente; puede crear `admin` | Crea `operator` o `viewer` solo en su tenant; `tenant_id` suministrado se rechaza |
| `PUT /api/admin/users/{user_id}` | Actualiza usuario de cualquier tenant excepto la cuenta protegida; puede asignar/quitar `admin` sujeto a invariantes | Actualiza usuarios de su tenant con rol `operator` o `viewer`; no puede asignar `admin`/`superadmin`, ni editar cuentas `admin`/`superadmin` |
| `POST /api/admin/users/{user_id}/unlock` | Desbloquea usuario de cualquier tenant excepto la cuenta protegida | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/revoke-sessions` | Revoca sesiones en cualquier tenant excepto la cuenta protegida | Solo usuario ordinario del tenant |
| `GET /api/admin/users/{user_id}/audit` | Historial del usuario de cualquier tenant | Solo historial de usuario ordinario del tenant; sin datos de otros tenants |
| `POST /api/admin/users/{user_id}/reset-link` | Emite enlace para usuario de cualquier tenant, excepto la cuenta protegida | Solo usuario ordinario del tenant; no emite automáticamente un reset-link al crear una cuenta cuyo correo el destinatario aún no confirmó |
| `POST /api/admin/users/{user_id}/password` | Fija contraseña de usuario de cualquier tenant, excepto la cuenta protegida | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/baja` | Da de baja usuario de cualquier tenant, excepto la cuenta protegida; respeta el último-superadmin global | Solo usuario ordinario del tenant |

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

La clasificación de `_require_pipeline_owner` (hoy exige mismo `user_id` Y `tenant_id`, con excepción de lectura de ocultos solo para superadmin) queda reemplazada por el resolvedor central: `superadmin` puede leer por id pipelines de cualquier tenant y leer los ocultos; `admin` solo accede a pipelines de usuarios ordinarios de su tenant; `operator`/`viewer` conservan el ownership actual. En `/results`, `/resume`, `/cancel`, `/discard`, `/continue` y `/continue/preflight` se aplica la clasificación de la matriz anterior; también a `GET /{pipeline_id}`, `hide`, `restore`, `recover`, auditoría y listados. El preflight debe permanecer de solo lectura para habilitar lectura cross-tenant. No se replica `role == "superadmin"` en código de chat, pipeline u otras superficies. Los filtros de tenant y rol del dueño se aplican antes de devolver, ordenar, paginar o agregar.

### 2.3 Superficie de plataforma explícita

Solo `superadmin` accede a credenciales, kill-switch, SMTP, configuración, modelos, motores, bindings, repositorio, Ejecutor, directorio de tenants y auditoría forense. Estas superficies no se convierten en tenant-scoped para `admin`: se deniegan. La cuenta protegida de Fernando está exceptuada de las mutaciones administrativas globales. `superadmin` puede leer datos entre tenants y operar dentro de su propio tenant; toda mutación que cruce tenant(s) queda bloqueada hasta cumplir §8.

## 3. Gestión y ciclo de vida de usuarios

### 3.1 Cuenta protegida de Fernando

La cuenta de Fernando es intocable para cualquier otro principal, incluidos todos los `superadmin`: nadie puede quitarle el rol, cambiar su estado, borrarla/darla de baja, cambiar correo, contraseña o tenant, ni revocar sus sesiones. Solo la sesión autenticada de Fernando puede efectuar esas acciones sobre su propia cuenta por los flujos de autoservicio que correspondan. La protección identifica la cuenta por `user_id` estable, nunca por correo, tenant, rol ni nombre. Ese id se fija una sola vez en un registro de identidad de plataforma controlado por servidor, cuyo valor inicial se provisiona desde configuración de despliegue; la aplicación no puede cambiarlo. El resolvedor exige un único registro válido que apunte a una única fila de `jax_users`; si falta, hay duplicidad, inconsistencia o no existe la fila objetivo, toda operación administrativa que pudiera modificar una cuenta falla cerrada. No se busca sustituto por correo ni se deja que el cliente elija el id protegido. La regla cubre todas las superficies, endpoints, tareas y operaciones por lote, y se aplica antes de bloquear o mutar el objetivo.

1. Solo un `superadmin` crea un `admin`, promueve un usuario a `admin`, degrada o desactiva un `admin`, y crea/promueve/degrada/desactiva otro `superadmin`. El `admin` no puede crear ni modificar cuentas `admin` o `superadmin`. Puede gestionar `operator` y `viewer` ordinarios de su tenant. No se impone mínimo de admins por tenant; el superadmin de plataforma es la vía de recuperación.
2. Para crear usuarios en otro tenant, `superadmin` debe elegir un `tenant_id` existente mediante el directorio `GET /api/admin/tenants`, legible solo por `superadmin`, para que el destino no se invente ni se descubra mediante ensayo. Crear `admin` requiere además confirmar transaccionalmente el tenant destino y sincronizar su membresía administrativa derivada en JAX.
3. Crear o cambiar un rol actualiza fila de usuario, `token_version`, autoridad derivada JAX y auditoría en una sola transacción. Un fallo de validación, escritura de auditoría o sincronización de membresías revierte el conjunto. Tras commit se cortan las sesiones/conexiones que deban perder autoridad.
4. `admin` no puede apuntar a otro tenant mediante ruta, lote, query, header ni body. `tenant_id` de una cuenta es inmutable por API para todos los roles; un cambio de tenant requiere migración explícita, y la migración conserva íntegra la visibilidad de memoria existente —si no puede probarlo, aborta sin cambiar el tenant—. El tenant del actor viene de la fila vigente de `jax_users`. La mutación verifica tenant y rol ordinario del objetivo en la misma transacción y bajo el mismo bloqueo que el cambio.
5. El invariante «siempre queda al menos un superadmin activo» pasa a ser global a la plataforma, no por tenant. Los cambios concurrentes que podrían quitar el último superadmin se serializan con el mutex global y el orden de candados de §3.2. Admins no pueden alterar esta clase de cuenta y ningún otro actor puede modificar la cuenta protegida.
6. El código JAX debe reconocer `admin` con semántica de tenant y `superadmin` con semántica global antes de que se habilite la primera cuenta `admin`. Un actor global no se presenta como miembro de un tenant ajeno ni falsifica `ScopeContext`: se define y valida en JAX un contrato explícito para la autoridad de plataforma, leído de la cuenta activa en `jax_users`. Se retira el alias administrativo `super_admin`; solo los valores canónicos exactos de esta spec conceden esas capacidades.

### 3.2 Serialización y orden total de candados

Las operaciones administrativas que puedan tocar más de un tenant usan un mutex global compartido por todos los escritores, además de los candados por tenant. Toda ruta adquiere candados en un único orden total: mutex global → candado(s) de tenant por `tenant_id` ascendente → actor → objetivo. Para una operación con varios objetivos, se bloquean los ids de objetivo en orden ascendente después del actor; no se adquiere un candado de orden previo mientras se conserva uno posterior. El mutex global se mantiene hasta commit/rollback. La implementación y todas las rutas de mutación deben usar el mismo protocolo; si no pueden obtener el conjunto completo, abortan antes de escribir. Esto evita ciclos de espera entre solicitudes concurrentes y hace atómico el conteo del último superadmin.

## 4. Email único global

Se conserva el índice/constraint global `UNIQUE(email)` y la respuesta `409 email_ya_existe` sin revelar tenant, user id ni estado. El residuo real es que un `admin` autenticado puede usar el `409` como oráculo para saber si un correo está ocupado en cualquier tenant y puede acaparar una dirección global si la alta reserva el correo antes de que su titular lo confirme. Se acepta el oráculo residual con estas mitigaciones obligatorias: (a) todo `409` de una operación de alta por `admin`, tanto por chequeo previo como por colisión concurrente del constraint, escribe un evento de auditoría durable con `actor_user_id`, `actor_tenant_id`, operación, resultado, origen y request/trace id, sin guardar el correo consultado; la respuesta al cliente nunca incluye tenant, user id ni estado; (b) `axioma_config` define `admin.users.create.max_per_hour`, tope configurable de intentos por administrador y hora; se identifica al actor por `user_id`, se cuenta cada intento de alta incluso los 409, y llegar al límite impide nuevas altas con respuesta uniforme; si falta la clave o no tiene valor válido y finito, el admin no puede crear usuarios; (c) el alta por `admin` no inserta la cuenta en `jax_users` ni reserva el `UNIQUE(email)` hasta que el destinatario inicie y complete una verificación de buzón; no se envía reset-link, credencial ni invitación automática a una dirección sin confirmar. El destinatario inicia la verificación en el flujo público de activación; solo tras probar control del buzón se materializa la cuenta pendiente. Así el admin no puede apropiarse ni bloquear preventivamente una dirección ajena. El `superadmin` conserva el comportamiento actual de alta y entrega de enlace.

El chequeo previo solo mejora el mensaje. El constraint de DB sigue siendo la autoridad ante altas concurrentes, también entre tenants, y todo duplicado recibe el mismo 409. La auditoría del 409 se confirma aun cuando la inserción de usuario revierte por duplicado; si no se puede registrar el evento, no se devuelve un 409 exitoso sin rastro. El alta de `admin` no devuelve ni registra cuál tenant ya usa el correo. Las reglas actuales de baja y liberación de correo se conservan.

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

No se reconstruye un rol histórico desde la cuenta actual. Para filas anteriores, los campos no disponibles se guardan como `NULL`/`legacy_unknown`, nunca fabricados. Toda ruta de lectura valida alcance: superadmin puede consultar entre tenants y `admin` solo auditoría de usuarios ordinarios de su tenant. Si en el historial o en un evento de baja el actor es un `superadmin` de otro tenant, al `admin` se le muestra el actor como «administración de la plataforma»; no se revela su correo ni otro dato identificable. El cambio, incremento de `token_version`, sincronización JAX y evento se confirman juntos; si uno falla, ninguno queda aplicado.

## 7. Resolución tenant de hechos y otras filas

Para un `admin`, un hecho pertenece al tenant de su `user_id` según la fila vigente de `jax_users`, siempre que el usuario sea ordinario (`operator`/`viewer`). Hechos cuyo `user_id` sea `admin` o `superadmin` quedan fuera del alcance admin aunque compartan tenant: corregir, aprobar, caducar o fundirlos responde `404` como si el id fuera ajeno; lo mismo aplica a consulta por id. Si un hecho no tiene usuario y está asociado a proyecto, pertenece al `tenant_id` autoritativo de `jax_project_scope`. Si ambas referencias existen, deben coincidir. Un hecho sin usuario ni proyecto es global y solo lo ve/opera `superadmin`. Filas inconsistentes o de pertenencia irresoluble quedan ocultas al `admin`, generan señal de integridad y no se reparan implícitamente.

La comprobación se realiza dentro de la transacción y bloqueo de cada mutación. Fusión y operaciones por lote son todo-o-nada; citas, agrupamientos y recorrido transitivo no cruzan tenants ni incluyen hechos de usuarios admin/superadmin para un admin. Ocultar/restaurar pipelines, listar ocultos/descartados y leer uso de cuentas admin/superadmin también quedan fuera del alcance admin y responden 404 al consultar un objetivo por id. Las consultas de pipelines, usuarios, uso y dashboard incluyen filtros de tenant y rol del dueño en SQL antes de ordenar, paginar o agregar. El superadmin no hereda un filtro accidental de su propio tenant.

## 8. Dependencias entre repositorios y despliegue

**Compuerta verificable para escrituras entre tenants:** hasta que existan y estén implementados/auditados en ambos repositorios (1) el permiso explícito `PLATFORM_ALL` y el contrato global correspondiente en JAX —incluida autoridad leída de `jax_users` sin membresía tenant falsificada—, (2) el mutex global y el orden total de candados de §3.2 compartidos por todos los escritores, y (3) `target_tenant_id` obligatorio en la auditoría de cada mutación que tiene objetivo tenant, las lecturas cross-tenant de superadmin están permitidas y toda mutación cross-tenant está denegada. La guarda se evalúa en backend antes de cualquier efecto; una falta, error o versión parcial de cualquiera de los tres requisitos conserva el bloqueo. Esta prohibición cubre cuentas, roles, sesiones, memoria, pipelines (incluidos hide/restore/recover/resume/cancel/discard/continue), uso y cualquier otra superficie tenant-scoped. Mutar dentro del propio tenant y las operaciones globales de plataforma siguen sus permisos específicos. Al satisfacer la compuerta, se habilita la escritura entre tenants para el superadmin, excepto sobre la cuenta protegida de Fernando.

El rol `admin` no se habilita hasta que plataforma y JAX implementen y auditen este contrato en ambos lados. En particular, JAX debe soportar creación/promoción global por superadmin y administración de tenant por admin, sin inferencias desde alias o membresías falsas. La interfaz puede reflejar permisos luego del backend, pero no se despliega ninguna capacidad antes de que su control de autoridad exista en ambos repositorios (Principio IX).

La spec no autoriza migrar producción, emitir cuentas admin, desplegar ni integrar cambios. Cada acto externo conserva sus GO, ventana o regla vigente aplicables. La decisión conceptual de Fernando aquí registrada es la fuente del modelo; los detalles técnicos de implementación siguen sujetos a revisión adversarial antes de habilitarlo.

## 9. Criterios verificables de aceptación

- Matriz completa por endpoint para superadmin (lecturas globales, escrituras cross-tenant bajo compuerta), admin propio/ajeno, operator, viewer y rol desconocido; incluye el directorio de tenants.
- El superadmin cuyo tenant hogar es A lee tenant B; mientras falte cualquier condición de §8, una escritura a B es denegada antes de producir efectos. Cumplida la compuerta, las escrituras admitidas no usan un predicado derivado de A; el filtro opcional solo acota lecturas.
- Un admin no puede alterar su alcance con JWT, query, body, lote ni header; IDs ajenos responden 404 y una solicitud por lote no deja cambios parciales.
- Claims JWT que afirman `superadmin` con fila DB `operator` no elevan acceso; filas con rol desconocido no reciben capacidades HTTP, WS, SSE o refresh.
- Admin no obtiene respuestas o KPI de plataforma a través del dashboard ni acceso indirecto a modelos, SMTP, credenciales, kill-switch, Ejecutor o auditoría global.
- `409 email_ya_existe` es idéntico para colisiones dentro/entre tenants y bajo carrera concurrente; cada 409 de alta admin queda auditado, cuenta para `admin.users.create.max_per_hour`, y esa alta no envía reset-link a un buzón sin verificar; el email global permanece único.
- Una cuenta identificada solo por el `user_id` protegido de Fernando no puede ser modificada por otro usuario admin/superadmin en ninguna superficie; configuración ausente o incongruente falla cerrada.
- El rol DB usa `utf8mb4_bin`; CHECK, middleware y JAX aceptan únicamente los bytes exactos de las cuatro literales; cualquier valor restante aborta la migración y el middleware lo rechaza.
- `_require_pipeline_owner` y todos los endpoints por id aplican la matriz de §2.2; admin nunca lee ni muta pipelines de cuentas admin/superadmin, y operator/viewer conservan ownership.
- La concurrencia de escritura toma candados solo en orden global → tenants por id ascendente → actor → objetivos por id ascendente; pruebas de conflicto verifican ausencia de deadlock y rollback completo.
- Carrera de democión/baja de los últimos superadmins: al menos una operación falla y siempre queda uno activo.
- Mutación de rol, sesiones, membresías JAX y auditoría revierten juntas bajo fallos inyectados; auditoría no inventa datos históricos y siempre lleva request/trace id obligatorio, generado/propagado desde HTTP, WS o CLI.
- Hechos huérfanos, globales, inconsistentes, lotes mixtos y citas cruzadas se prueban con el contrato de §7.
- El contrato JAX y el contrato de plataforma reconocen exactamente los mismos roles y alcances antes de crear el primer admin.
- `EXPLAIN` verifica índices para filtros tenant de toda consulta afectada; suites prueban autorización negativa/positiva por endpoint y casos de concurrencia.
- Dashboard, usuarios, uso, memoria y pipelines se someten a carga en el peor caso antes de producción; las mediciones registran concurrencia, RPS, p95 y punto de degradación.
