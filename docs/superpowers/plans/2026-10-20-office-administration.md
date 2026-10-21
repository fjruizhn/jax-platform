# Implementación · Virtual Office

Spec: docs/superpowers/specs/2026-10-20-office-administration.md
Fecha: 2026-10-20 · OP#343/345 · rama codex/virtual-office-20261020.

## Global Constraints

Reutilizar identidad/pool/runner/UI existentes. OFFICE_SUPERUSER es global solo dentro de Virtual Office. No inferir rol del email ni admin clásico. account_class en jax_users. EXTERNAL_CLIENT y OFFICE_INTERNAL denegados por defecto fuera de sesión/MFA/oficina. Namespace OFFICE_CONFIDENTIAL no accesible desde superficies clásicas. Reautorizar mutaciones y jobs; auditoría atómica. Sin producción durante implementación. ES/EN y dark/light, modal propio. Índices/EXPLAIN, async, no caché de permisos, carga antes de producción. Trabajadores no cometen/pushean; root verifica y conserva commits chicos, exact-SHA Sol entre tareas.

## Task 1: Esquema, resolvedor y auditoría de oficina

Own backend/virtual_office/{__init__,models,capabilities,authority,repository,audit,migrations}.py, backend/db/migrations.py integración mínima y backend/tests/test_office_{authority,migrations}.py. Todavía no modificar auth/middleware, main.py ni exponer rutas. Crear tablas/clase de cuenta y constraints/FKs/índices según spec, con upgrade idempotente. Resolvedor puro de entrada estricta y consultas DB, operación tipada, resultado inmutable; variante en cursor para mutaciones/bloqueos. Usuario/grants/revocados/missing/unknown fail closed. GENERAL_ADMIN solo delegaciones explícitas. Evento append-only con triggers y actor/scope, sin secretos. Probar RED/GREEN realMariaDB, matriz dos tenants/proyectos/roles y cambios de revocación; EXPLAIN consultas reales. No bootstrap automático ni creación de cuentas externas todavía. Extensión tras BLOCK Sol: implementar separación migrador/runtime y guard conductual según docs/superpowers/specs/2026-10-20-office-db-runtime.md; main.py/db/migrate.py/db/runtime_contract.py, units/restore y fixtures afectados tienen un solo dueño secuencial. Verificar con principal DML real, sin excepción de producción.

## Task 2: Barreras de sesión y par JAX

Own backend/auth/models.py, auth/middleware.py, api/auth.py, api/admin/users.py, virtual_office/identity_security.py, main.py y par_jax.py, tests específicos. Instalar también guard central de mutaciones clásicas sobre targetsOffice (password/resetlink/email/rol/reactivar/unlock/baja) según refinamiento office-mfa-invitations.md; soloOfficeroot+MFAreciente, sin cambiarselfservice/revokesesiones/deactivation. MientrasTask3nofactor real, guarddeniegaoperacionesOffice sensibles. Cargar account_class de DB, HTTP allowlist estricta para EXTERNAL_CLIENT y OFFICE_INTERNAL/sessionenrollment/MFA, WS clásico rechaza, SSE cubierto. Adaptador transaccional namespace autorizado y contrato de par al arranque. Incluir backend/proyectos_documentos/repositorio.py: sus consultas directas a jax_project_scope (despacho, predicado de owner, INSERT/reencolar/diagnóstico y jobs abiertos) deben filtrar CLASSIC; prueba real de filas Office con membresía heredada y trabajo en cola que no se despacha/lee/modifica desde ruta clásica. Consumir JAX solo tras gate independiente de namespace.

## Task 3: MFA y administración API

Own backend/virtual_office/{mfa,invitations}.py, backend/api/virtual_office/, schemas/proyectos/personas, constraints/requirements, tests. Integrar rutas auth/main secuencialmente. TOTP/cifrado/recovery/stepup/versiones, proyectos dedicados, grants/delegaciones y auditoría, invitación/outbox auditable. Bootstrap explícito verificado en comando propio no migración. Ningún envío real durante pruebas.

### Task 3 delivery slices

3A: persisted MFA primitives/schema/versioned readiness and direct service tests, no new routes/session issuance. Reuse crypto and existing pools/transactions. Strict credential/challenge/TOTP/recovery contracts; consume uniqueness and security events in same transaction, no plaintext fallback.

3B: auth/token/session integration and dedicated MFA endpoints, central privileged-role/recency enforcement, root target-identity proof. Existing classic no-factor login preserved; MFA challenge grants no session. All WS/SSE version cuts and refresh absolute expiration/recency tests required.

3C: administrative projects/people/delegations/grants, immutable invitation issuance proof/atomic acceptance+typed core adapter, explicit bootstrap and durable outbox. No real email/account/bootstrap in tests. Human target-account question affects activation, not service development. Independent exact-SHA review between slices; no external identities/Office execution enabled until all applicable gates.

## Task 4: UI administrativa

Own frontend/src/api/virtualOffice.js, pages/VirtualOffice*, components/virtual-office/, App y traducciones secuencialmente. Reutilizar Dialogo/tema/auth, selector proyectos, globaladmin/personas/permisos/invitación/MFA. Vitest y navegador ambos idiomas/temas/móvil/teclado; cuenta externa no carga datos clásicos.

## Task 5: Persistencia de trabajo y ejecución

Tablas office de artefactos/revisiones/publicación y jobs; worker durable con idempotencia, revocación y presupuesto/conectores por proyecto. Reutilizar contratos actuales y paquete espacial versionado; estructurar propuesta->validación->reparación limitada->presentación con evidencia. Integrar escena3D con montaje/desmontaje seguro. Matriz API/events/worker adversarial y carga.

## Task 6: Migración reversible y publicación web

Respaldar/restaurar Omoa, importar historial fiel sin aprobar ni duplicarOP334. Artefacto reproducible, activos allowlist, reverseproxy/HTTPS/DNS, cabeceras/límites/monitoreo. Auditoría independiente SHA exacto y gates constitucionales antes de integración/despliegue. Registrar pruebas EXPLAIN/carga/restauración y URL real operativa. No afirmar producción por tener código local.
