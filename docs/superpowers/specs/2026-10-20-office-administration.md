# Virtual Office · administración y ejecución multiusuario

2026-10-20 · OP#343/345 · Codex. DECISIÓN: Fernando autorizó comenzar ambos desarrollos. Fuente: conversación actual; arquitectura Sol platform_contract con corrección de alcance aceptada. Estado: implementación en rama propia; no producción.

## Autoridad y compatibilidad

OFFICE_SUPERUSER cruza organizaciones/proyectos únicamente de Virtual Office, sin membresía ni grant. No hay singleton obligatorio ni autoridad genérica sobre Axioma/Faro. El bootstrap identifica explícitamente un user_id verificado y registra actor y fundamento; ninguna semilla usa nombre/email ni infiere superusuario del admin heredado. GENERAL_ADMIN administra según delegaciones explícitas, no recibe contenido implícito; PROJECT_ADMIN es grant de proyecto. COLLABORATOR es rol global por defecto. Cliente es relación y perfil de acceso, no quinto rol.

JAX core posee namespace CLASSIC/OFFICE_CONFIDENTIAL y creación atómica. JXP posee identidades, roles y grants de oficina. Todo consumidor clásico rechaza namespace confidencial antes de roles heredados. EXTERNAL_CLIENT solo accede a sesión/enrolamiento y API de oficina; barrera HTTP/WS/SSE en todos los orígenes. Datos de oficina no van a índices/eventos/memoria clásica.

## Persistencia y autorización

Reutilizar db.connection.get_pool y db.transaccion; DDL propio en módulo office, invocado por db/migrations.py establecido. No otra base de credenciales ni runner/pool. account_class reside directamente en jax_users: INTERNAL por defecto / EXTERNAL_CLIENT con CHECK. Borrar perfil de oficina nunca elimina la barrera externa.

Tablas office_account_profile (user_id PK/FK, global_role, status, version); office_admin_delegation (capacidad administrativa explícita y actor); office_project_grant (project/user, PROJECT_ADMIN|COLLABORATOR, INTERNAL|CLIENT_PUBLISHED, status/version/granted_by); office_recipient_group (un CLIENT por proyecto V1); office_external_relation (project/user/group activa); office_professional_grant (project/user/capability/basis/evidence_ref); office_authorization_event append-only con actor, operación, proyecto/tenant, resultado y correlación. Todos los identificadores corresponden a FKs reales y consultas de alcance indexadas. Audit no incluye secretos/documentos.

Resolvedor async authorize_office(pool, actor, operation, project_id=None, recipient_group_id=None, require_recent_mfa=False) devuelve decisión inmutable. Relee usuario/tenant/perfil activos y namespace; ausente/desconocido niega. Usuario interno necesita membresía JAX vigente Y grant office. Externo necesita clase EXTERNAL_CLIENT, relación activa, grupo receptor y grant CLIENT_PUBLISHED. Superusuario office omite membresías/grants, no namespace ni identidad activa. General admin no recibe contenido por rol. PROJECT_ADMIN no promueve roles globales, no restablece MFA global, no concede autoridad profesional. Mutaciones reautorizan bajo bloqueos en su misma transacción y auditan atómicamente; el resultado de un request anterior no autoriza uno nuevo.

OfficeNamespaceAuthorizer bloquea actor y perfil en el cursor de JAX: INTERNAL activo, OFFICE_SUPERUSER activo, operación CREATE_OFFICE_PROJECT y namespace OFFICE_CONFIDENTIAL. Audita tenant destino y actor en la misma transacción. POST /api/virtual-office/projects fija namespace en servidor, requiere MFA reciente e idempotencia; GET dedicado filtra namespace. Root ve todas las oficinas, los demás solo grants válidos. Inicialización de grupo posterior es idempotente y falla dejando proyecto confidencial inaccesible, jamás clásico. par_jax.py verifica contrato conjunto antes de habilitar identidades/proyectos office.

## MFA e invitación

TOTP mediante librería verificada, secreto cifrado con infraestructura existente; códigos de recuperación hash y uso único. Obligatorio para superusuario/general admin/project admin/aprobadores. mfa_at y mfa_version firmados, revalidados en DB; refresh preserva mfa_at. Enrolar/recuperar/deshabilitar invalida sesiones (token_version y mfa_version). Step-up reciente en miembros/grants/conectores/aprobaciones. Invitación por token aleatorio almacenado solo como hash, expiración/uso único/email/rol/proyecto y outbox con recibo de entrega; crear fila no acredita correo. Sin signup público.

## Experiencia y trabajo durable

React reutiliza Dialogo, sesión, componentes de proyectos y i18n ES/EN; todos los colores mediante tokens dark/light. Selector de proyectos, Personas y permisos, invitación, administración global y entrada oficina 3D existente montada/desmontada sin rediseño. API valida toda autorización independientemente de controles UI.

Artefactos/revisiones/publicación conservan actor y hash, visibilidad y grupo receptor. Jobs durables con idempotencia, límites de gasto/concurrencia y conectores de proyecto; actor/project/group/op son procedencia, permisos se revalidan antes de lecturas y efectos y tras respuesta LLM. Revocación durante ejecución cancela/descarta, CANCELLED_REVOKED, sin publicación. Propuesta geométrica estructurada usa paquete virtual_office_spatial versionado y reparación acotada; informe real del validador precede exportación, sin ejecución arbitraria de Python.

Importar Omoa con backup restaurado y mapeo explícito; historial local permanece no autenticado, OP334 no duplicado y borrador sin aprobar. Producción exige activos allowlist (no prompts/Python), CSP/CSRF/rate/size/no-store, restauración, EXPLAIN real, carga peor caso y auditoría SHA exacto. Despliegue por artefactos y DNS/HTTPS comprobado, nunca editar /srv.

## Cuatro controles de rendimiento

Índices: EXPLAIN de consultas reales de resolver/lista/invitación/jobs con volumen representativo; sin filesort/temporary en camino caliente. Caché: compartir pool/clientes; no nuevo caché sin medición ni permisos TTL que prolonguen revocación. Async: aiomysql, trabajo pesado a worker; nada bloqueante en request. Carga: concurrencia real, RPS/p95/degradación, incluyendo revocación y cola llena, antes de producción.

## Matriz de aceptación

Dos tenants/dos proyectos; acceso total office root sin asignación; admin clásico/OWNER sin grant no lee; general admin sin delegación no administra; project admin no escala; externo no accede HTTP/WS/SSE clásico; borrar perfil externo no abre rutas; grupo ajeno y objeto adivinado denegados; revocación corta jobs/descargas/eventos; invitación expirada/reutilizada falla; MFA replay/recuperación/refresh no renueva recencia; historial Omoa fiel; ES/EN, dark/light, teclado/móvil; pruebas reales MariaDB y auditoría independiente.
