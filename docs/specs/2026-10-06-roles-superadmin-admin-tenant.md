# Roles de plataforma y tenant: `superadmin` y `admin`

**Estado:** propuesta para auditoría de Fernando; no autoriza implementación ni despliegue.  
**Decisión de Fernando (2026-10-06):** «superadmin gobierna sobre todo, admin sobre su tenant».  
**Base inspeccionada:** `jax-platform` `origin/master` en `4e0e711`; decisión e inventario inicial de superficie en jax-platform PR #204.  
**Alcance M6:** establecer el contrato de autoridad antes de habilitar el nuevo rol `admin`.

## 1. Modelo de autoridad

La autoridad se resuelve desde la fila vigente de `jax_users` en cada request. Los claims de rol y tenant del JWT son transporte y se contrastan con esa fila; nunca conceden autoridad por sí mismos. Se mantiene la consulta por usuario existente y no se introduce caché de roles.

| Rol en la fila activa | Alcance resuelto | Permisos |
|---|---|---|
| `superadmin` | `PLATFORM_ALL` | Ve y opera todos los tenants y toda la superficie de plataforma. Su `tenant_id` identifica su cuenta, no limita su autoridad. En recursos de tenant, una lista sin filtro recorre todos los tenants; puede usar un filtro explícito para acotar resultados. |
| `admin` | `TENANT(<tenant_id autoritativo>)` | Administra únicamente datos y usuarios de su tenant en las superficies enumeradas abajo. El tenant se obtiene de `jax_users`; query, body, header o JWT no pueden cambiarlo. |
| `operator` | `USER` | Sin cambio de permisos ni de alcance respecto del contrato actual. |
| `viewer` | `USER` | Sin cambio. Aunque la medición de producción del 2026-10-06 encontró solo `superadmin` y `operator`, el código actual lo reconoce y la migración no lo elimina. |
| `NULL` o cualquier otro valor | `DENY` | Fallo cerrado en toda ruta autenticada, incluyendo HTTP, refresh, WS y SSE. Respuesta de sesión inválida uniforme; sin degradar el rol a `operator`. El logout puede limpiar la cookie sin conceder acceso. |

Un rechazo por rol válido pero insuficiente es `403`. Un `admin` que señale un recurso o usuario ajeno a su tenant recibe `404`, incluso en operaciones por lote; el lote es atómico y no deja cambios parciales. El backend es la frontera de autorización; ocultar controles en frontend no sustituye estas comprobaciones.

El resolvedor es único y compartido por endpoints, tareas auxiliares y proyecciones de rol. No se permite inferir `PLATFORM_ALL` a partir de `tenant_id`, ni mantener comparaciones ad hoc que diverjan de este contrato. El rol de la fila DB prevalece sobre una claim JWT manipulada o antigua.

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
| `POST /api/admin/keys/{provider_id}/test` | Prueba llave global | Denegado |
| `GET /api/admin/kill-switch` | Estado global | Denegado |
| `POST /api/admin/kill-switch/activar` | Activa freno global | Denegado |
| `POST /api/admin/kill-switch/reanudar` | Reanuda plataforma global | Denegado |
| `GET /api/admin/memoria/hechos` | Todos los hechos, sin filtro por tenant por defecto; filtro opcional | Solo hechos cuya pertenencia verificable corresponde a su tenant, con paginación y filtros aplicados después del alcance |
| `POST /api/admin/memoria/hechos/aprobar` | Aprueba hechos de cualquier tenant | Solo hechos del tenant; comprobar alcance dentro de la transacción que bloquea los hechos; lote todo-o-nada |
| `POST /api/admin/memoria/hechos/{fact_id}/corregir` | Corrige hecho de cualquier tenant | Solo hecho del tenant; alcance comprobado bajo el mismo bloqueo transaccional |
| `POST /api/admin/memoria/hechos/fundir` | Funde hechos de cualquier tenant | Solo conjunto íntegro del tenant; rechaza lote mixto sin mutación |
| `POST /api/admin/memoria/hechos/{fact_id}/caducar` | Caduca hecho de cualquier tenant | Solo hecho del tenant; alcance comprobado bajo el mismo bloqueo transaccional |
| `GET /api/admin/memoria/grupos` | Grupos globales y de todos los tenants | Grupos, miembros, vecinos y cierre transitivo de citas confinados a su tenant |
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
| `GET /api/admin/pipelines/ocultos` | Todos los pipelines ocultos, sin filtro por defecto; filtro opcional | Solo pipelines del tenant, con `tenant_id` obligatorio derivado del actor |
| `GET /api/admin/pipelines/descartados` | Todos los descartados, sin filtro por defecto; filtro opcional | Solo descartados del tenant, con `tenant_id` obligatorio derivado del actor |
| `GET /api/admin/repo` | Repositorio global de la plataforma | Denegado |
| `GET /api/admin/repo/file` | Lee archivo global autorizado | Denegado |
| `DELETE /api/admin/repo/file` | Borra archivo global autorizado | Denegado |
| `GET /api/admin/smtp` | Configuración SMTP global | Denegado |
| `PUT /api/admin/smtp` | Cambia SMTP global | Denegado |
| `POST /api/admin/smtp/test-connection` | Prueba conexión SMTP global | Denegado |
| `POST /api/admin/smtp/test` | Envía prueba desde SMTP global | Denegado |
| `GET /api/admin/usage` | Uso agregado de todos los tenants; filtro opcional | Solo filas y agregados con `tenant_id` del actor |
| `GET /api/admin/users` | Todos los tenants; filtro opcional | Usuarios del tenant del actor, incluyendo bajas solo dentro de ese tenant |
| `POST /api/admin/users` | Crea usuario en tenant destino explícito y existente; puede crear `admin` | Crea `operator` o `viewer` solo en su tenant; `tenant_id` suministrado se rechaza |
| `PUT /api/admin/users/{user_id}` | Actualiza usuario de cualquier tenant; puede asignar/quitar `admin` sujeto a invariantes | Actualiza usuarios de su tenant con rol `operator` o `viewer`; no puede asignar `admin`/`superadmin`, ni editar cuentas `admin`/`superadmin` |
| `POST /api/admin/users/{user_id}/unlock` | Desbloquea usuario de cualquier tenant | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/revoke-sessions` | Revoca sesiones en cualquier tenant | Solo usuario ordinario del tenant |
| `GET /api/admin/users/{user_id}/audit` | Historial del usuario de cualquier tenant | Solo historial de usuario ordinario del tenant; sin datos de otros tenants |
| `POST /api/admin/users/{user_id}/reset-link` | Emite enlace para usuario de cualquier tenant | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/password` | Fija contraseña de usuario de cualquier tenant | Solo usuario ordinario del tenant |
| `POST /api/admin/users/{user_id}/baja` | Da de baja usuario de cualquier tenant; respeta el último-superadmin global | Solo usuario ordinario del tenant |

El dashboard no expone a `admin` campos globales simplemente filtrando algunas consultas: su respuesta se construye con una proyección tenant-only. Sus consultas de uso, usuarios, pipelines y memoria aplican alcance en SQL antes de agregar. El superadmin obtiene todos los tenants cuando no proporciona filtro.

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
| `POST /api/pipelines/{pipeline_id}/hide` | Oculta pipeline de cualquier tenant, sin filtro | Oculta pipeline de su tenant; comprobar alcance sobre el pipeline antes de mutar |
| `POST /api/pipelines/{pipeline_id}/restore` | Restaura pipeline de cualquier tenant, sin filtro | Restaura pipeline de su tenant; comprobar alcance antes de mutar |
| `POST /api/pipelines/{pipeline_id}/recover` | Recupera descarte de cualquier tenant | Recupera descarte de su tenant; el dueño conserva el comportamiento actual |
| `GET /api/pipelines/{pipeline_id}/auditoria-descarte` | Lee auditoría de descarte de cualquier tenant | Solo tenant propio; el dueño conserva el comportamiento actual |

El indicador `ejecutor` que produce chat y cualquier lectura de pipelines ocultos/descartados aplican el mismo resolvedor. No se replica `role == "superadmin"` en código de chat, pipeline u otras superficies. Las rutas ordinarias de pipeline conservan sus permisos actuales de propietario para `operator`/`viewer`; donde se habilite administración por tenant, el filtro se deriva del resolvedor, nunca de IDs del cliente.

### 2.3 Superficie de plataforma explícita

Solo `superadmin` accede a credenciales, kill-switch, SMTP, configuración, modelos, motores, bindings, repositorio, Ejecutor y auditoría forense. Estas superficies no se convierten en tenant-scoped para `admin`: se deniegan. `superadmin` las opera globalmente y también puede usar en cualquier tenant las superficies tenant-scoped asignadas a `admin`.

## 3. Gestión y ciclo de vida de usuarios

1. Solo un `superadmin` crea un `admin`, promueve un usuario a `admin`, degrada o desactiva un `admin`, y crea/promueve/degrada/desactiva otro `superadmin`. El `admin` no puede crear ni modificar cuentas `admin` o `superadmin`. Puede gestionar `operator` y `viewer` ordinarios de su tenant. No se impone mínimo de admins por tenant; el superadmin de plataforma es la vía de recuperación.
2. Para crear usuarios en otro tenant, `superadmin` debe identificar un `tenant_id` existente. La API debe disponer de un directorio de tenants legible solo por `superadmin` (o equivalente autorizado) para que el destino no se invente ni se descubra mediante ensayo. Crear `admin` requiere además confirmar transaccionalmente el tenant destino y sincronizar su membresía administrativa derivada en JAX.
3. Crear o cambiar un rol actualiza fila de usuario, `token_version`, autoridad derivada JAX y auditoría en una sola transacción. Un fallo de validación, escritura de auditoría o sincronización de membresías revierte el conjunto. Tras commit se cortan las sesiones/conexiones que deban perder autoridad.
4. `admin` no puede apuntar a otro tenant mediante ruta, lote, query, header ni body. El tenant del actor viene de la fila vigente de `jax_users`. La mutación verifica tenant y rol del objetivo en la misma transacción y bajo el mismo bloqueo que el cambio.
5. El invariante «siempre queda al menos un superadmin activo» pasa a ser global a la plataforma, no por tenant. Los cambios concurrentes que podrían quitar el último superadmin se serializan con un mutex global común a todos los escritores; dos demociones concurrentes no pueden dejar cero superadmins. Admins no pueden alterar esta clase de cuenta.
6. El código JAX debe reconocer `admin` con semántica de tenant y `superadmin` con semántica global antes de que se habilite la primera cuenta `admin`. Un actor global no se presenta como miembro de un tenant ajeno ni falsifica `ScopeContext`: se define y valida en JAX un contrato explícito para la autoridad de plataforma, leído de la cuenta activa en `jax_users`. Se retira el alias administrativo `super_admin`; solo los valores canónicos exactos de esta spec conceden esas capacidades.

## 4. Email único global

Se conserva el índice/constraint global `UNIQUE(email)` y la respuesta `409 email_ya_existe` sin revelar tenant, user id ni estado. El login y recuperación existentes localizan la identidad por correo sin tenant; permitir duplicados obligaría a rediseñar esos flujos, rate limits y búsquedas de identidad. El `409` ya puede indicar a un usuario autenticado que un correo no está disponible globalmente, por lo que conservarlo no añade una nueva clase de divulgación respecto del contrato actual. Se documenta esa inferencia residual; cambiar la unicidad a `(tenant_id, email)` queda fuera de M6.

El chequeo previo solo mejora el mensaje. El constraint de DB sigue siendo la autoridad ante altas concurrentes, también entre tenants, y todo duplicado recibe el mismo 409. El alta de `admin` no devuelve ni registra cuál tenant ya usa el correo. Las reglas actuales de baja y liberación de correo se conservan.

## 5. Columna `role` y migración

En el esquema actual `jax_users.role` es `VARCHAR(20) DEFAULT 'operator'`, no `ENUM`. Por tanto no se propone una conversión ficticia de enum. La migración aditiva establece el conjunto exacto `superadmin`, `admin`, `operator`, `viewer`, mantiene `VARCHAR(20)`, lo declara `NOT NULL` y añade un `CHECK` equivalente en creación nueva y bases existentes.

Antes del DDL, la migración cuenta roles agrupados, `NULL` y longitudes inválidas. Si aparece un valor desconocido, nulo o mayor de 20 caracteres, aborta cerrada con el valor y conteo necesarios para resolverlo explícitamente; no lo convierte a `operator` ni a otro rol. La medición de producción recibida para 2026-10-06 reportó `superadmin` (1) y `operator` (2) en un tenant; debe repetirse inmediatamente antes de aplicar la migración. Se conserva `viewer` porque el código y las pruebas lo reconocen, aunque no apareciera en esa medición.

El middleware conserva el rechazo ante cualquier rol desconocido aunque el CHECK exista: cubre bases legadas, corrupción y desfases de versión. La migración es idempotente conforme al patrón existente y tiene downgrade documentado; si ya existen admins, una versión anterior no debe ejecutarse hasta revertir de forma explícita esos roles con una sesión autorizada. La DB no altera roles por sí sola.

## 6. Auditoría de cambios de rol

Cada asignación, promoción, degradación o remoción efectiva de rol deja evidencia atómica con la mutación. Se reutiliza `user_admin_audit`, ampliándola con `actor_role_at_action`, `actor_tenant_id` y `target_tenant_id`, y un índice que permita consultar por tenant y objetivo. El evento conserva como mínimo:

- timestamp UTC;
- actor y rol/tenant vigentes al actuar;
- usuario objetivo y su tenant;
- `from_role` y `to_role` (incluido `null` solo para alta, si el contrato del evento así lo representa);
- IP derivada del proxy confiable y request/trace id cuando esté disponible.

No se reconstruye un rol histórico desde la cuenta actual. Para filas anteriores, los campos no disponibles se guardan como `NULL`/`legacy_unknown`, nunca fabricados. Toda ruta de lectura valida alcance: superadmin puede consultar entre tenants y `admin` solo auditoría de usuarios ordinarios de su tenant. El cambio, incremento de `token_version`, sincronización JAX y evento se confirman juntos; si uno falla, ninguno queda aplicado.

## 7. Resolución tenant de hechos y otras filas

Para un `admin`, un hecho pertenece al tenant de su `user_id` según la fila vigente de `jax_users`. Si no tiene usuario y está asociado a proyecto, pertenece al `tenant_id` autoritativo de `jax_project_scope`. Si ambas referencias existen, deben coincidir. Un hecho sin usuario ni proyecto es global y solo lo ve/opera `superadmin`. Filas inconsistentes o de pertenencia irresoluble quedan ocultas al `admin`, generan señal de integridad y no se reparan implícitamente.

La comprobación se realiza dentro de la transacción y bloqueo de cada mutación. Fusión y operaciones por lote son todo-o-nada; citas, agrupamientos y recorrido transitivo no cruzan tenants. Las consultas de pipelines, usuarios, uso y dashboard incluyen el filtro tenant en SQL antes de ordenar, paginar o agregar. El superadmin no hereda un filtro accidental de su propio tenant.

## 8. Dependencias entre repositorios y despliegue

El rol `admin` no se habilita hasta que plataforma y JAX implementen y auditen este contrato en ambos lados. En particular, JAX debe soportar creación/promoción global por superadmin y administración de tenant por admin, sin inferencias desde alias o membresías falsas. La interfaz puede reflejar permisos luego del backend, pero no se despliega ninguna capacidad antes de que su control de autoridad exista en ambos repositorios (Principio IX).

La spec no autoriza migrar producción, emitir cuentas admin, desplegar ni integrar cambios. Cada acto externo conserva sus GO, ventana o regla vigente aplicables. La decisión conceptual de Fernando aquí registrada es la fuente del modelo; los detalles técnicos de implementación siguen sujetos a revisión adversarial antes de habilitarlo.

## 9. Criterios verificables de aceptación

- Matriz completa por endpoint para superadmin global, admin propio, admin ajeno, operator, viewer y rol desconocido.
- El superadmin cuyo tenant hogar es A lista y muta tenant B sin un predicado derivado de A; el filtro opcional, si se usa, solo acota la salida.
- Un admin no puede alterar su alcance con JWT, query, body, lote ni header; IDs ajenos responden 404 y una solicitud por lote no deja cambios parciales.
- Claims JWT que afirman `superadmin` con fila DB `operator` no elevan acceso; filas con rol desconocido no reciben capacidades HTTP, WS, SSE o refresh.
- Admin no obtiene respuestas o KPI de plataforma a través del dashboard ni acceso indirecto a modelos, SMTP, credenciales, kill-switch, Ejecutor o auditoría global.
- `409 email_ya_existe` es idéntico para colisiones dentro/entre tenants y bajo carrera concurrente; el email global permanece único.
- Carrera de democión/baja de los últimos superadmins: al menos una operación falla y siempre queda uno activo.
- Mutación de rol, sesiones, membresías JAX y auditoría revierten juntas bajo fallos inyectados; auditoría no inventa datos históricos.
- Hechos huérfanos, globales, inconsistentes, lotes mixtos y citas cruzadas se prueban con el contrato de §7.
- El contrato JAX y el contrato de plataforma reconocen exactamente los mismos roles y alcances antes de crear el primer admin.
- `EXPLAIN` verifica índices para filtros tenant de toda consulta afectada; suites prueban autorización negativa/positiva por endpoint y casos de concurrencia.
- Dashboard, usuarios, uso, memoria y pipelines se someten a carga en el peor caso antes de producción; las mediciones registran concurrencia, RPS, p95 y punto de degradación.
