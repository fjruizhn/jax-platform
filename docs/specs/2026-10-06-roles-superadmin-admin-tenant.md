# Roles cooperativos de plataforma y tenant: superadmin, system_admin y admin

**Estado:** propuesta de contrato para auditoría de ronda 4; no autoriza implementación ni despliegue.
**Decisiones de Fernando (2026-10-06, en persona):** una cuenta `superadmin`, global y sin tenant; `system_admin` global, sin tenant, que no modifica al superadmin ni actúa sobre sus datos o agencia; `admin` de tenant; se conservan `operator` y `viewer`. Solo `superadmin` crea cuentas `system_admin`. La plataforma es cooperativa: los usuarios son parte del equipo y los tenants no son clientes SaaS.
**Base:** jax-platform PR #206, punta recibida `5cc711d2b61a8ceb9362ef253a3e8741fdfbc978`. Los nuevos roles requieren sincronización con JAX antes de habilitarse.
**Alcance M6:** fijar autoridad, titularidad y límites antes de habilitar `admin` o `system_admin`.

## 1. Modelo de autoridad

La autoridad se resuelve por la fila vigente de `jax_users` en cada request. El JWT solo transporta identidad y versión de sesión; sus claims de rol o tenant nunca conceden autoridad. No se cachean roles ni se infiere autoridad global de una membresía tenant.

| Rol canónico activo | Alcance | Permisos |
|---|---|---|
| `superadmin` | `PLATFORM_ALL`, sin tenant | Exactamente una cuenta activa de Fernando. Autoridad global sobre plataforma y tenants. Puede leer y actuar sobre sus datos y agencia. Nadie más puede cambiar su identidad, rol, estado, credenciales o sesiones (§3.1). |
| `system_admin` | `PLATFORM_ALL`, sin tenant | Administra la plataforma y usuarios de todos los tenants. No modifica ninguna cuenta `superadmin` ni los datos/agencia de su titular. Puede leerlos para operación, con auditoría (§2.3). Solo `superadmin` crea o promueve cuentas a este rol. |
| `admin` | `TENANT(tenant_id vigente)` | Administra solo su tenant y usuarios ordinarios `operator`/`viewer`. No administra otros admins, cuentas globales ni datos/agencia de usuarios elevados aunque compartan tenant. |
| `operator` | `USER(tenant_id, user_id)` | Conserva el alcance y permisos ordinarios actuales, sujetos al ownership del recurso. |
| `viewer` | `USER(tenant_id, user_id)` | Conserva el alcance actual de solo lectura. Se mantiene aunque no apareciera en la medición recibida del 2026-10-06. |
| Fila ausente/inactiva, rol no canónico o combinación rol/tenant inválida | `DENY` | Rechazo uniforme en HTTP, refresh, WS, SSE, CLI y tareas autenticadas; nunca degradar a `operator`. Logout puede limpiar cookie. |

`superadmin` y `system_admin` requieren `tenant_id IS NULL`. `admin`, `operator` y `viewer` requieren tenant activo existente. No se crea un tenant sintético de plataforma para representar roles globales. Un rechazo por rol válido insuficiente es `403`; para un `admin`, un objetivo de otro tenant o elevado se oculta como `404`, también en lotes. Los lotes son atómicos.

Un único contrato de resolución se usa en rutas, tareas, proyecciones y JAX. Roles se comparan byte por byte, con distinción de mayúsculas; no se normalizan con `.lower()` ni se infieren de tenant, alias, claim, membresía o propiedad de proyecto (§5).

## 2. Superficie administrativa y matriz por endpoint

Las reglas de la columna **superadmin** aplican también a **system_admin**, salvo las restricciones por datos/agencia y cuenta protegida de §2.3/§3.1. **Admin** indica tanto el filtro obligatorio como sus límites de mutación. Donde dice «denegado» la ruta continúa siendo exclusiva de plataforma. Los recursos se ocultan con `404` si el identificador apunta fuera del tenant.

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
| `GET /api/admin/repo/file?file_id=…` | Resuelve y lee archivo global o de cualquier tenant; system_admin solo lectura para artefactos del superadmin | Denegado |
| `DELETE /api/admin/repo/file` | Borra archivo operativo global o de cualquier tenant tras resolver ownership; system_admin no borra artefactos del superadmin | Denegado |
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

`GET /api/admin/tenants` es un endpoint nuevo, exclusivo de `superadmin` y `system_admin`; devuelve `tenant_id`, nombre visible y estado activo/inactivo, sin usuarios, correos ni otros datos personales. Incluye tenants inactivos para que el operador pueda distinguirlos, pero `POST /api/admin/users` solo acepta destinos activos. No permite mutaciones.

`/api/admin/repo/file` admite archivos `PLATFORM` y artefactos tenant-scoped. El cliente entrega un identificador lógico de archivo; el backend resuelve la ruta canónica y su owner/scope desde metadata persistida, nunca acepta una ruta filesystem arbitraria. Para documentos derivados de pipeline, la metadata resuelve el `pipeline_id` íntegro. Lectura y borrado derivan tenant objetivo desde el owner; system_admin puede borrar artefactos de otros usuarios según su permiso global, pero solo leer los atribuibles a superadmin. Toda mutación registra tenant objetivo o NULL solo para `PLATFORM`.

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
| `POST /api/pipelines/{pipeline_id}/resume` | Mutación en cualquier tenant; system_admin no modifica datos/agencia del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/cancel` | Mutación en cualquier tenant; system_admin no modifica datos/agencia del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/discard` | Mutación en cualquier tenant; system_admin no modifica datos/agencia del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/continue` | Mutación en cualquier tenant; system_admin no modifica datos/agencia del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/continue/preflight` | Preflight de lectura por id en cualquier tenant; no inicia ni altera pipeline | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/hide` | Mutación en cualquier tenant; system_admin no modifica datos/agencia del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/restore` | Mutación en cualquier tenant; system_admin no modifica datos/agencia del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `POST /api/pipelines/{pipeline_id}/recover` | Recupera cualquier tenant; system_admin no recupera pipeline del dueño superadmin (§2.3) | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| `GET /api/pipelines/{pipeline_id}/auditoria-descarte` | Lee auditoría por id de cualquier tenant | Solo pipeline de usuario ordinario de su tenant; ajeno/elevado = 404 |
| Listados de pipelines ocultos/descartados y uso | Todos los tenants, filtro opcional; excluye únicamente lo que la cuenta protegida resguarda | Solo pipelines/uso de usuarios ordinarios del tenant; nunca los de admin/superadmin |

El resolvedor central autoriza pipeline por id, owner y scope completos. Los roles globales leen y mutan pipelines de cualquier usuario salvo la agencia/datos del superadmin, definidos en §2.3; `admin` solo accede a usuarios ordinarios de su tenant; `operator`/`viewer` conservan ownership. La regla aplica a GET, results, preflight, resume, cancel, discard, continue, hide, restore, recover, auditoría y listados. Cada acceso resuelve id, owner y scope del recurso completo; toda mutación registra el tenant real derivado del recurso. No hay compuerta configurable. El preflight de rol global es de solo lectura, no inicia ni altera pipeline. No se replica comparación de rol ad hoc en chat/pipeline; filtros preceden agregación, orden y paginación.

`POST /api/ejecutor/misiones/{mision_id}/turnos` resuelve y bloquea misión, owner y scope dentro de la transacción que inserta el turno; revalida que el actor siga activo y autorizado antes de cualquier mutación. El turno durable registra el `user_id` y rol efectivos del actor, `mission_id`, owner/scope resueltos y resultado de autorización. Nunca atribuye el turno al dueño actual por defecto. `system_admin` puede leer la misión/bitácora del superadmin, pero no continuarla ni iniciar una acción bajo su identidad; un cambio concurrente de dueño/rol hace abortar la operación.

### 2.3 Superficie de plataforma y límite de datos/agencia

La administración de plataforma —configuración, credenciales, SMTP, modelos, motores, bindings, salud, kill-switch, seguridad, tenants, repositorio compartido y soporte operativo— corresponde a los roles globales. `admin` recibe únicamente las proyecciones tenant-only definidas por la matriz.

**Datos del superadmin** son cualquier contenido atribuido a su `user_id` o creado bajo su identidad: memoria/hechos, versiones, citas y relaciones; proyectos, documentos y membresías; pipelines, entradas, resultados y artefactos; archivos del repositorio generados por sus pipelines; y registros que revelen ese contenido. **Agencia del superadmin** incluye iniciar, ejecutar, continuar, reintentar, cancelar, descartar, ocultar/restaurar, recuperar o alterar una misión, pipeline, proyecto o proceso bajo su identidad; cambiar instrucciones, herramientas, bindings personales o estado de ejecución. Toda superficie nueva que lea su contenido o actúe bajo su identidad se clasifica como datos/agencia antes de habilitarse.

`system_admin` puede leer/observar estas superficies para operación, con auditoría de acceso: consultar memoria y procedencia, proyectos/membresías, pipelines/resultados, listar/leer archivos y ver misiones/bitácoras/estado del Ejecutor. No puede editar, aprobar, caducar o fundir esa memoria; cambiar proyectos o miembros; iniciar, continuar, reintentar, cancelar, descartar, ocultar, restaurar o recuperar sus pipelines; escribir/borrar artefactos; iniciar/continuar/controlar sus misiones; alterar instrucciones o cambiar su titularidad. En `/api/admin/repo/file`, todo archivo tiene scope/owner persistido; un archivo atribuible a pipeline/proyecto del superadmin es de solo lectura para system_admin, y uno sin clasificación falla cerrado. Archivos operativos globales sí pueden administrarse conforme al permiso de repositorio. Si una operación mezcla soporte global con mutación de datos/agencia del superadmin, se rechaza completa. La lectura no crea membresía ni cambia ownership.

`admin` no puede leer datos ni agencia de usuarios elevados, aunque compartan tenant. Agregados y listados filtran esas filas en SQL antes de ordenar, paginar o calcular indicadores. La autoridad de system_admin para lectura operativa se implementa como capacidad global read-only, no como membresía tenant ni OWNER implícito en JAX.

## 3. Gestión y ciclo de vida de usuarios

### 3.1 Única cuenta `superadmin` protegida

La identidad de Fernando es la única cuenta `superadmin`, global y sin tenant. Se identifica por un `user_id` inmutable en un registro dedicado de identidad de plataforma; solo migración/bootstrap controlado por root puede establecerlo. No vive en `axioma_config`, el cliente no elige el id y no hay sustitución automática por correo. La resolución requiere exactamente un registro y una fila activa de `jax_users` con rol exacto `superadmin` y `tenant_id IS NULL`; si falta, sobra o es inconsistente/inactiva, fallan cerradas las operaciones globales de cuenta, rol, sesión y recuperación. La cuenta SQL runtime de la API y la cuenta separada del Ejecutor no tienen `INSERT`, `UPDATE` ni `DELETE` sobre el registro de identidad ni DDL/`TRIGGER`/`DROP` en el schema de identidad; esas escrituras quedan reservadas a migración/bootstrap root. Tras el bootstrap, triggers de protección con DEFINER privilegiado rechazan cualquier `INSERT` de `jax_users` con rol `superadmin`, cualquier `UPDATE` que asigne ese rol a otra fila y cualquier `UPDATE` o `DELETE` de la fila protegida, independientemente de endpoint/campos. El bootstrap crea la única fila antes de instalar los triggers; ninguna identidad runtime puede crearla ni alterar/desactivar los triggers. La recuperación de credencial protegida usa únicamente el comando root y su conexión operativa separada, en mantenimiento controlado con auditoría; no existe escritura de esa fila desde la API. La regla de autorización es código/versionado, no configuración BD, y ninguna cuenta de aplicación puede escribirla.

Ninguna cuenta o proceso de aplicación, incluido system_admin, puede degradar, desactivar, borrar, cambiar correo/contraseña, rol, tenant, `token_version`, autenticadores o sesiones de Fernando. Autoservicio autenticado puede cambiar su credencial con sesión reforzada, pero no el rol ni identidad protegida. Ningún lote incluye esa cuenta. Solo la ventana/autoridad directa de Fernando puede actuar sobre sus propios datos/agencia; ningún otro actor, tarea o endpoint los modifica.

**Recuperación:** `forgot-password` responde de forma indistinguible para la cuenta protegida, sin token ni correo; cualquier reset token falla incluso si fue emitido antes. La recuperación solo se ejecuta con el comando operativo de Hall9000 bajo `root`, después de validar TOTP mediante el endpoint del relevo atem-ai `POST /ops/v1/totp/verify-action`, cuya URL viene de configuración segura del entorno. Cada solicitud incluye `action_id` aleatorio, id protegido, propósito `recover_protected_superadmin`, nonce, expiración breve y el código TOTP. El relevo devuelve aprobación firmada ligada a esos campos; la aprobación es de un uso, se consume atómicamente y el relevo no acepta reutilizar un código ya consumido para otra acción. Si el relevo cae, rechaza, la aprobación no coincide o el consumo falla, no se modifica la credencial y no existe fallback local ni por correo. El evento durable registra actor `root`, acción, resultado, `action_id` y referencia de verificación; nunca código o secreto. **Límite de confianza:** root en Hall9000 puede modificar binarios, leer memoria o falsificar solicitudes; el procedimiento no protege contra root comprometido.

El bloqueo por intentos contra Fernando cuenta por IP/origen confiable y ventana finita, nunca bloquea la cuenta completa. Umbral, ventana y duración son acotados; error de configuración aplica valores seguros para bloquear el origen y alertar, sin bloqueo permanente. Solo la recuperación anterior puede limpiar anticipadamente un bloqueo de origen. SMTP se audita durablemente con actor, campos no secretos, resultado y trace id; tras commit se notifica a Fernando por Telegram con reintento durable y fallo declarado si el transporte cae. `FRONTEND_ORIGIN` solo viene del entorno de despliegue: no se copia ni se mueve a `axioma_config`; su cambio queda auditado por el proceso de configuración de despliegue.

### 3.2 Ciclo de vida de cuentas y roles

Solo `superadmin` puede crear un `system_admin` o promover una cuenta a ese rol. `system_admin` puede administrar las cuentas `system_admin` ya existentes, salvo la cuenta protegida de Fernando, pero no puede crear ni promover a ese rol. Los roles globales pueden crear/promover/degradar/desactivar admins de tenant y administrar usuarios de cualquier tenant. `admin` solo puede invitar `operator`/`viewer` ordinarios dentro de su tenant; no crea, promueve, edita ni desactiva cuentas elevadas. `operator` y `viewer` no administran cuentas.

Toda promoción/degradación, cambio de estado o tenant, revocación de sesión y sincronización JAX aplica en una transacción indivisible con el evento de auditoría. Si plataforma/JAX no comparten transacción, se requiere protocolo durable prepare/commit: el nuevo rol no concede capacidad hasta confirmación de todos los participantes; falla o resultado desconocido bloquea autoridad y deja operación conciliable por `action_id`, sin repetir mutaciones. Un error no puede dejar el rol cambiado con membresías/auditoría obsoletas.

El `tenant_id` de `admin`, `operator` y `viewer` es inmutable por API; no existe endpoint de migración de tenant. Una migración operativa no cambia la cuenta protegida ni convierte actores globales en miembros. El invariante es una sola identidad superadmin protegida y activa, no un mínimo por tenant; la continuidad usa system_admin. Ningún perfil, reset, refresh, lote o escritura de config puede degradar accidentalmente a Fernando.

JAX resuelve `admin` solo dentro del tenant vigente y su sincronización automática alcanza proyectos de ese tenant. Los roles globales son capacidades de plataforma expresas, leídas desde `jax_users`; no falsifican `ScopeContext`, tenant, alias o membresía para obtener autoridad. Se elimina `super_admin` como alias: solo las cinco literales exactas de §5 conceden capacidad.

### 3.3 Serialización y orden total de candados

Las operaciones administrativas que puedan tocar más de un tenant usan un mutex global compartido por todos los escritores, además de los candados por tenant. El mutex es una fila singleton precreada en una tabla dedicada: la transacción la adquiere con `SELECT … FOR UPDATE` y la conserva hasta commit/rollback; no se usa `GET_LOCK` ni un mutex en memoria. Toda ruta adquiere candados en un único orden total: mutex global → candado(s) de tenant por `tenant_id` ascendente → conjunto de filas de usuario involucradas (actor, dueño y usuario objetivo) por `user_id` ascendente, con bloqueo compartido/exclusivo según operación → filas de recurso objetivo por id ascendente. Si una identidad ocupa más de un papel se bloquea una vez en ese orden. Las filas autoritativas de actor y dueño que confirman rol/tenant/estado quedan bloqueadas hasta terminar la transacción. La implementación y todas las rutas de mutación deben usar el mismo protocolo; si no pueden obtener el conjunto completo, abortan antes de escribir. Esto evita ciclos de espera entre solicitudes concurrentes y carreras de rol/dueño.

## 4. Email único global

Se conserva el índice/constraint global `UNIQUE(email)` y la respuesta `409 email_ya_existe` sin revelar tenant, user id ni estado. El residuo real es que un `admin` autenticado puede usar el `409` como oráculo para saber si un correo está ocupado en cualquier tenant y puede acaparar una dirección global si la alta o el cambio de correo reserva la dirección antes de que su titular la confirme. Se acepta el oráculo residual con estas mitigaciones obligatorias: (a) todo `409` de alta o cambio de correo por `admin`, tanto por chequeo previo como por colisión concurrente del constraint, escribe un evento de auditoría durable con `actor_user_id`, `actor_tenant_id`, operación, resultado, origen y request/trace id, sin guardar el correo consultado; la respuesta al cliente nunca incluye tenant, user id ni estado. La auditoría del 409 sobrevive al rollback de la mutación; si no puede escribirse, la operación falla cerrada; (b) `axioma_config` define `admin.users.email_attempts.max_per_hour`, tope de intentos por administrador y hora compartido entre altas y cambios de correo. Se identifica al actor por `user_id`; cada intento, incluido 409, incrementa en una tabla durable de contadores en base de datos dentro de la transacción serializada, nunca en memoria por worker. El límite uniforme impide nuevas altas y cambios de correo; si falta la clave o no tiene valor válido y finito, esas operaciones quedan denegadas; (c) la alta por `admin` crea una invitación pendiente, no una fila en `jax_users`, y no reserva `UNIQUE(email)` hasta que el destinatario confirma control del buzón y el tenant que acepta. El cambio de correo por `admin` tampoco reserva el nuevo correo ni lo aplica antes de esa confirmación. No se envía reset-link ni credencial; solo se envía el enlace de verificación necesario para que el titular complete explícitamente la aceptación. El `superadmin` conserva el comportamiento actual de alta y entrega de enlace, sujeto a la protección de cuenta de §3.1.

El flujo de pendientes se aloja en una tabla dedicada, sin índice único global sobre correo: admite varias altas/cambios pendientes para el mismo correo en distintos tenants y no expone si el correo ya pertenece a otro tenant. Cada registro guarda `invite_id`, tipo (`create` o `change_email`), tenant destino, rol ordinario permitido o `target_user_id`, correo normalizado cifrado, hash del token, actor y expiración; el token en claro solo se entrega al correo y nunca se guarda en logs. `POST /api/admin/users` devuelve al admin el enlace opaco/`invite_id`, que este comparte con el destinatario por un canal acordado fuera del correo no confirmado. El destinatario abre ese enlace; `POST /api/public/invitations/{invite_id}/verification` recibe el correo y solicita la verificación del buzón con respuesta/tiempo uniformes. Solo entonces se envía el enlace de verificación. Ese enlace muestra el tenant y exige una acción explícita «acepto este tenant» en `POST /api/public/invitations/{invite_id}/confirm` antes de confirmar. Para varias invitaciones al mismo correo, solo gana la primera confirmación explícita y válida; confirma únicamente la invitación/tenant seleccionado y revoca las demás invitaciones pendientes para ese correo. En un cambio de correo, la confirmación vuelve a autorizar y bloquear al actor, tenant y usuario ordinario; verifica que la cuenta no sea la protegida, cambia el correo, incrementa `token_version`, revoca sesiones y audita en la misma transacción. Antes de mutar, la misma transacción revalida que el emisor siga activo, conserve el permiso exacto para la operación y que el tenant destino siga activo. Si el emisor fue degradado/desactivado, perdió autoridad o el tenant quedó inactivo, revoca la invitación y no crea/cambia cuenta. La mutación de rol/estado del emisor revoca también sus invitaciones pendientes en esa transacción. No se libera ni modifica el correo vigente hasta ese commit. El token es de un solo uso, tiene TTL máximo de 24 horas, y puede revocarlo el admin emisor mientras siga pendiente. Los endpoints públicos aplican límite configurable y fail-closed por IP y por hash de correo; los intentos y revocaciones quedan auditados sin registrar el correo. Confirmar en carrera bloquea las invitaciones del correo en orden estable, verifica aún no existir una cuenta con ese correo, materializa la cuenta en `jax_users` para `create` o aplica el cambio descrito para `change_email`, y consume/revoca todas las invitaciones en una transacción; un conflicto devuelve respuesta neutra y no deja reservas. El límite por correo no permite descubrir cuentas existentes.

| Operación | Actor y ruta | Contrato |
|---|---|---|
| Crear pendiente | `admin`: `POST /api/admin/users` | Registra solo `operator`/`viewer` para su tenant y devuelve un `invite_id` opaco; no crea usuario ni reserva correo. |
| Iniciar verificación | Público: `POST /api/public/invitations/{invite_id}/verification` | Recibe correo; respuesta y tiempo uniformes; solo envía verificación si coincide con pendiente vigente. Límite por IP y hash de correo. |
| Confirmar alta/cambio | Público: `POST /api/public/invitations/{invite_id}/confirm` | Token de un uso más aceptación explícita del tenant mostrado; atomiza alta o cambio de correo con auditoría, consume invitación y revoca duplicadas del correo. |

El chequeo previo solo mejora el mensaje. El constraint de DB sigue siendo la autoridad ante altas concurrentes, también entre tenants, y todo duplicado recibe el mismo 409. Los intentos y auditoría del 409 se confirman aun cuando la inserción falla: la inserción de cuenta se aísla con savepoint y, ante colisión, se revierte solo a ese savepoint; contador y auditoría se conservan y se confirman en la transacción exterior. Si no se puede registrar ambos, la operación falla cerrada y no devuelve el 409. El alta de `admin` no devuelve ni registra cuál tenant ya usa el correo. Las reglas actuales de baja y liberación de correo se conservan. La invitación pendiente no envía correo por iniciativa del admin: el destinatario debe iniciar la solicitud pública de verificación con el identificador de invitación que recibió por un canal acordado. Solo esa solicitud envía el enlace de verificación; no se envían invitaciones ni mensajes a una dirección no confirmada.

## 5. Columna `role` y migración

`jax_users.role` es `VARCHAR(20) NOT NULL`, no enum. La migración acepta exactamente `superadmin`, `system_admin`, `admin`, `operator`, `viewer`, fija collation binaria y CHECK. `tenant_id` es NULL solo para los dos roles globales; los tres roles tenantales lo requieren y referencian un tenant existente, cuya vigencia se comprueba en cada operación. El esquema y middleware validan ambas combinaciones.

Antes del DDL se cuentan roles por bytes, NULL y longitudes inválidas. Cualquier rol no canónico, valor nulo o mayor de 20 bytes aborta mostrando valor escapado/hex y conteo; no recorta ni convierte. Se vuelve a validar inmediatamente antes del cambio. La medición recibida del 2026-10-06 reportó superadmin (1) y operator (2) en un tenant; no establece que superadmin pertenezca a ese tenant. Antes de migrar se registra el id protegido bajo bootstrap root, se verifica que identifica una sola fila y se asigna `tenant_id=NULL`; ante ambigüedad se aborta. La medición debe repetirse inmediatamente antes de migrar y no se toma como estado actual. Se conserva viewer aunque no apareciera en esa medición. `verificar_sesion` debe rechazar roles desconocidos en HTTP, refresh, WS/SSE/CLI; JAX deja de normalizar con `.lower()` y acepta solo los cinco valores exactos. Downgrade no puede eliminar roles ya asignados sin reversión explícita autorizada.

## 6. Auditoría de cambios de rol

Cada asignación, promoción, degradación o remoción efectiva de rol deja evidencia atómica con la mutación. Se reutiliza `user_admin_audit`, ampliándola con `actor_role_at_action`, `actor_tenant_id` y `target_tenant_id`, y un índice que permita consultar por tenant y objetivo. El evento conserva como mínimo:

- timestamp UTC;
- actor y rol/tenant vigentes al actuar;
- usuario objetivo y su tenant;
- `from_role` y `to_role` (incluido `null` solo para alta, si el contrato del evento así lo representa);
- IP derivada del proxy confiable;
- request/trace id obligatorio, generado en el punto de entrada si no llega uno válido y propagado sin perderlo hasta la transacción y el evento;
- transporte de entrada (`HTTP`, `WS` o `CLI`) y punto de entrada/operación que inició el cambio.

No se reconstruye un rol histórico desde la cuenta actual. Para filas anteriores, los campos no disponibles se guardan como `NULL`/`legacy_unknown`, nunca fabricados. Toda ruta de lectura valida alcance: los roles globales consultan entre tenants; `admin` solo auditoría de usuarios ordinarios de su tenant. Si en el historial o en un evento de baja el actor corresponde a cualquier cuenta `admin`, `system_admin` o `superadmin`, al `admin` se le muestra «administración»; no se revela correo ni otro dato identificable, aunque sea del mismo tenant. La misma ocultación aplica a los eventos de bajas. El cambio, incremento de `token_version`, sincronización JAX y evento se confirman juntos; si uno falla, ninguno queda aplicado.

## 7. Resolución tenant y ownership de datos

Para `admin`, una fila pertenece a su tenant solo si la fila autoritativa del dueño es `operator`/`viewer` activo en ese tenant. Filas de `admin`, `system_admin` o `superadmin` quedan ocultas a admin aunque compartan tenant. `system_admin` puede leer contenido de superadmin para operación con auditoría, pero nunca mutarlo (§2.3). Si un hecho referencia usuario y proyecto, ambas autoridades deben coincidir; filas huérfanas o inconsistentes se ocultan a admin, generan alerta de integridad y no se reparan implícitamente.

Un pipeline se autoriza por su fila completa (id, owner, scope y tenant), no por prefijo ni ruta aportada por cliente. `documents/{pipeline_id[:8]}_{NN}_{facet}.md` es solo convención de nombre: el prefijo de ocho caracteres no es clave de autorización y puede colisionar; antes de leer/borrar se resuelve el pipeline/artefacto por identificador íntegro y asociación persistida. `images/` almacena artefactos atribuibles al job/pipeline creador y requiere metadata de owner/scope; `missions/` resuelve titular y actor de cada turno desde misión/bitácora; `pipelines/` usa el id íntegro de pipeline; `documents/` resuelve pipeline/proyecto dueño. Una allowlist de carpetas no concede acceso. Los archivos operativos globales se identifican como `PLATFORM` y se administran por permiso global; todo archivo sin metadata de ownership falla cerrado para actores tenantales.

Las consultas filtran scope, tenant y rol del dueño antes de ordenar, paginar, agregar o devolver resultados. Mutaciones y lotes comprueban owner vigente bajo bloqueo/transacción; toda salida/acción registra el tenant objetivo derivado del recurso, o NULL solo para un recurso realmente global.

## 8. Dependencias entre repositorios y despliegue

No hay bandera en base de datos/config que habilite autoridad entre tenants. El permiso se resuelve por rol vigente, objetivo y superficie, y el rechazo ocurre antes de todo efecto. Cada mutación registra actor, rol/tenant del actor cuando aplique, tenant objetivo derivado del recurso (NULL solo para objeto global), recurso, acción, resultado y request/trace id durable. Lotes son todo-o-nada.

Antes de habilitar `admin` o `system_admin`, plataforma y JAX despliegan y auditan el mismo contrato versionado: roles exactos y globales sin tenant; autoridad desde fila vigente, nunca alias/membresía; scope platform-owned separado del tenant; controles para memoria/proyectos/pipelines/repo/misiones/tareas; auditoría, candados y orden de §3.3; y pruebas de autorización en ambas direcciones. Divergencia o indisponibilidad rechaza; no hay fallback a semántica previa.

### 8.1 Proyectos globales del superadmin en JAX

Los proyectos de Fernando viven en el almacén canónico de proyectos/autoridad de JAX como scope `PLATFORM`, con `owner_user_id` igual al id protegido y `tenant_id IS NULL`. No se insertan en `jax_project_scope` como proyecto tenant ni se crea tenant sintético. JAX amplía el schema/contrato para separar `PLATFORM` de `TENANT(tenant_id)` a través de claves foráneas, consultas, documentos, membresías, eventos y auditoría; owner/scope se resuelven en transacción desde autoridad vigente, nunca del cliente.

`sync_tenant_admin_memberships_in_transaction` solo concede OWNER sobre proyectos del tenant exacto administrado. `TenantAdminMembershipProtected` protege membresías tenantales, pero no otorga autoridad a system_admin sobre scope PLATFORM. La lectura operativa de system_admin es capacidad global read-only separada; mutar esos proyectos requiere superadmin. Promover, degradar o sincronizar un admin nunca agrega OWNER ni adopta un proyecto PLATFORM. Una invitación explícita a colaboradores ordinarios no permite a admin/system_admin cambiar contenido, propietario o membresías elevadas.

La titularidad de memoria, proyectos y pipelines conserva principal y scope (`PLATFORM` o tenant) de forma consultable. Lecturas de system_admin sobre datos del superadmin dejan auditoría sin crear membresías. No se habilita admin hasta implementar/auditar la matriz en ambos repos; tampoco system_admin hasta desplegar y probar el límite de datos/agencia y scope de plataforma (Principio IX). La UI refleja controles ya aplicados por backend.

## 9. Criterios verificables de aceptación

- Matriz por ruta para los cinco roles, incluidos los límites de sistema y fallos cerrados; filtro tenant antes de agregación/paginación.
- Existe exactamente una cuenta superadmin activa, global, protegida y sin tenant. Ningún rol/claim/membresía crea autoridad global ni tenant sintético.
- Solo superadmin crea/promueve system_admin. system_admin no puede modificar superadmin ni mutar sus datos/agencia; lecturas operativas quedan auditadas.
- Por cada superficie del §2.3, pruebas positivas de lectura de system_admin y negativas de edición/ejecución; incluye continuar misión, cambiar memoria/proyecto, continuar pipeline y escribir/borrar artefactos.
- El proyecto scope PLATFORM de superadmin no aparece en proyectos tenantales y nunca recibe membresía OWNER por sincronización de admin. Un ScopeContext tenant fabricado no autoriza a system_admin.
- `continuar()` vuelve a validar en transacción owner/scope de misión y registra el autor del turno; un system_admin no continúa misiones de superadmin.
- API y Ejecutor usan usuarios SQL separados y allowlist de entorno para el runner; no hereda credenciales API. Bajo ambas identidades SQL, pruebas comprueban rechazo de INSERT de una segunda cuenta superadmin, UPDATE que promueve otra fila a ese rol, UPDATE/DELETE de la fila protegida y DDL/alteración de triggers o registro de identidad.
- Intentos anónimos bloquean solo IP/origen por tiempo acotado; nunca bloquean la cuenta protegida completa.
- Recuperación valida TOTP contra relevo atem-ai con acción ligada, nonce/TTL y un solo uso; caída o discrepancia falla cerrado. Se declara root de Hall9000 como límite de confianza.
- `documents/{pipeline_id[:8]}_{NN}_{facet}.md` resuelve autorización con el pipeline completo; `images/` y `missions/` tienen owner/scope/lecturas/escrituras explícitas; sin metadata se falla cerrado.
- Confirmar invitación revalida emisor activo y autorizado y tenant activo; si no, revoca/no materializa. Degradar/desactivar emisor también revoca invitaciones pendientes en la misma transacción.
- `FRONTEND_ORIGIN` solo se obtiene del entorno y nunca tiene copia autoritativa en `axioma_config`.
- Prueba de Telegram demuestra disparo post-commit; transporte caído deja estado/reintento durable y fallo declarado.
- El valor histórico 163 corresponde a pruebas Vitest frontend pasadas, medido el 2026-09-14 al cerrar PR-L ronda 2 (`.github/workflows/policy.yml`); no es un piso actual. Si se propone modificar ese piso, primero se mide el conteo vigente con el comando de CI y se registra SHA, entorno, comando, passed/skipped/failed y artefacto de resultado. Este cambio de spec no altera ningún piso ni afirma una medición nueva.
- Crear tenant tiene actor global autorizado, validación, auditoría durable y transacción; no crea usuarios/membresías implícitos.
- Ningún flujo de perfil/sesión degrada a Fernando; cambiar el rol superadmin no es autoservicio.
- Test conductual de regresión para la línea 206 de migración falla si vuelve la condición errónea.
- `EXPLAIN` confirma índices de filtros tenant; autorización y concurrencia se prueban con casos positivos/negativos, deadlock y rollback.

## 10. Trazabilidad de hallazgos de ronda 3 y decisiones de ronda 4

| Hallazgo | Cierre en la spec |
|---|---|
| BLOCK-1: admin dueño de proyectos de Fernando | Decisiones iniciales: roles globales sin tenant. §8.1 separa scope PLATFORM y excluye sincronización tenant-admin. §2.3 define lectura operacional system_admin y prohíbe mutación. |
| MAJOR-1: id/compuerta escribibles por jax_user; credenciales DB heredadas por Ejecutor | §3.1 reserva id a bootstrap root/migración; §8 elimina bandera editable. API/Ejecutor usan usuarios SQL separados, triggers impiden insertar o promover otra fila superadmin y modificar/eliminar la protegida, sin ser modificables por runtime ni heredar credenciales API el runner. Pruebas bajo cada identidad cubren esos DML y alteración del trigger/registro. |
| MAJOR-2: `continuar()` no valida dueño ni registra autor | §2.3 prohíbe a system_admin continuar misión de superadmin; §9 exige validar owner/scope bajo transacción y registrar autor del turno. |
| MAJOR-3: intentos anónimos bloquean para siempre a Fernando | §3.1 bloquea por IP/origen y duración finita, nunca por cuenta. |
| MAJOR-4: recuperación TOTP no especifica relevo ni límite de confianza | §3.1 define validación por relevo atem-ai, acción/nonce/TTL, un uso, fallo cerrado, auditoría y root de Hall9000 como límite. |
| MAJOR-5: repo/file no clasifica ubicación real, prefijo ambiguo, images/missions | §7/§9 clasifican documentos, imágenes y misiones por owner/scope; prefijo de 8 chars no autoriza ni identifica pipeline. |
| MAJOR-6: invitaciones no revalidan emisor/tenant | §9 exige revalidar ambos al confirmar o revocar al degradar/desactivar emisor en misma transacción. |
| MAJOR-7: “datos/agencia del superadmin” imprecisos | §2.3 define superficies, operaciones de lectura permitidas y lista de mutaciones/ejecución prohibidas para system_admin. |
| MINOR-1: filas 47–51/76–83 incoherentes con §4 | Matriz queda subordinada al overlay §2.3; aceptación comprueba cada permiso efectivo por superficie y §4 se aplica a las filas afectadas. |
| MINOR-2: `FRONTEND_ORIGIN` solo en entorno | §3.1/§9 prohíben copia en `axioma_config`. |
| MINOR-3: Telegram no declara fallo ni prueba disparo | §3.1/§9 exigen evento, notificación post-commit, reintento durable y prueba de transporte caído. |
| MINOR-4: prueba de línea 206 podría no fallar | §9 requiere regresión conductual que falle ante la condición equivocada. |
| MINOR-5: orden de candados actor/dueño | §3.3 exige orden global → tenants ascendentes → conjunto actor/dueño/objetivo por id ascendente → recursos; owner validado bajo el mismo bloqueo. |
| MINOR-6: medición 163 | §9 identifica 163 como conteo histórico de Vitest frontend del 2026-09-14, no como piso actual; cualquier cambio de piso exige una medición vigente reproducible. |
| MINOR-7: contrato de crear tenants | §9 define actor, validación, auditoría, transacción y no creación implícita de cuentas/membresías. |
| MINOR-8: Fernando podría degradarse por accidente | §3.1 prohíbe autoservicio de rol y §9 requiere regresión sobre flujos de perfil/sesión. |
| MINOR-9: discrepancias restantes de matriz/invitaciones | §2.3 es overlay obligatorio para todas las filas; §5 define cinco roles/scope; §7/§8.1 define ownership y carpetas; §9 cubre revalidación de invitaciones y permisos por ruta. |

La decisión cooperativa de Fernando del 2026-10-06 reemplaza cualquier redacción previa incompatible con tenants como clientes, superadmin tenant-scoped o administración global solo por superadmin sin continuidad. Esta spec no habilita implementación, producción, despliegue o integración.
