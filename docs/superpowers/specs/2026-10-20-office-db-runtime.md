# Contrato DB de ejecución y auditoría Office

2026-10-20 · OP#343/345 · Codex, arquitectura Sol office_audit_privileges_contract. Sustituye la suposición de que dos triggers bastan para impedir TRUNCATE. No cambia producción por sí mismo.

## Causa y evidencia

El runtime actual llama run_migrations con su propio pool y por ello recibe DDL. TRUNCATE exige DROP y no dispara triggers UPDATE/DELETE. Separar privilegios cierra esa causa. En MariaDB12.3.3 desechable, raíz confirmó que un usuario DML ve las dos filas information_schema.TRIGGERS, pero ACTION_STATEMENT es NULL en ambas. No conceder TRIGGER para inspeccionarlas: permitiría eliminarlas.

## Dos procesos, mismo runner y pool canónicos

- `python -m db.migrate`: proceso oneshot de despliegue, llama el run_migrations establecido y siempre close_pool. Recibe credencial de migración: SELECT,INSERT,UPDATE,DELETE,CREATE,DROP,ALTER,INDEX,TRIGGER,REFERENCES sobre esta base; sin privilegios globales, roles ni GRANT OPTION.
- Proceso web: un get_pool por eventloop como hoy, principal runtime con SELECT,INSERT,UPDATE,DELETE sobre esta base, sin DDL, roles, privilegios globales ni GRANT OPTION. No recibe credencial de migración.
- main.lifespan elimina run_migrations. Verifica contrato DB antes de clientes HTTP/tráfico y de lanzar trabajo dependiente. apply_office_migrations exige cursor del runner; no abre pool propio.
- `assert_runtime_db_contract(pool)` lee privilegios efectivos de conexión actual (USER/SCHEMA/TABLE/ROUTINE_PRIVILEGES y roles aplicables). Falla cerrado ante DDL, rol heredado o lectura incierta. No confía en nombre de usuario ni registra grants crudos.
- El runner registra revisión de esquema al final de todos sus pasos. Un proceso con esquema ausente/parcial/antiguo no sirve tráfico. Esta revisión no sustituye la prueba del freno.

## Evento de sistema verificable

office_authorization_event admite AUTHORIZATION y SYSTEM_FENCE. AUTHORIZATION requiere actor_user_id canónico con FK, actor_principal NULL y fence_key NULL; conserva ALLOWED/DENIED, no puede usar operación AUDIT_FENCE_CANARY.

SYSTEM_FENCE requiere exactamente actor_user_id NULL, actor_principal ascii_bin `system:office-schema-migrator`, fence_key ascii_bin `office-audit-fence-v1` no NULL, operation AUDIT_FENCE_CANARY, result RECORDED, reason_code office_audit_fence_v1; proyecto/tenant/correlación NULL. fence_key tiene índice UNIQUE nullable. Los CHECK incluyen IS NOT NULL explícito para evitar el valor desconocido SQL. No existe pseudousuario ni autoridad derivada de este evento.

El runner crea una vez el canario al migrar y verifica todos sus campos canónicos. Si la fila existente difiere, falla sin reescribirla. El writer de autorizaciones fija explícitamente AUTHORIZATION y campos de sistema NULL. IDs solicitados inválidos no se hacen pasar por IDs canónicos sujetos a FK.

## Guard conductual antes de servir

1. Exige exactamente el canario canónico y toma snapshot completo.
2. Exige metadata de los triggers no_update_office_authorization_event BEFORE UPDATE y no_delete_office_authorization_event BEFORE DELETE. No inspecciona ACTION_STATEMENT.
3. En una db.transaccion, intenta aumentar occurred_at del canario en1microsegundo. Solo acepta errno1644 y el mensaje append-only canónico. Si la sentencia tiene éxito, exige rowcount1 y lanza una excepción deliberada: el contexto hace ROLLBACK antes de negar el arranque.
4. Otra transacción prueba DELETE del mismo canario y aplica el mismo criterio.
5. Relee después de cada prueba y exige snapshot idéntico. Falta de permisos1142, CHECK, otros errores o éxito no cuentan como freno válido.

No hacer probes DDL en producción: tienen commit implícito. La capacidad runtime de añadir audit no le permite reescribir, borrar, truncar ni retirar sus triggers. Administrador DB y migrador siguen siendo principales de confianza; no se afirma inmutabilidad frente a ellos.

## Pruebas y despliegue

En MariaDB desechable, preparar esquema con principal admin separado y usuario runtime aleatorio DML. Probar guard real y mutaciones con pool canónico/transaccion; INSERTaudit permitido, UPDATE/DELETE/REPLACE/ON DUPLICATE UPDATE rechazados, TRUNCATE/DROP/ALTER/DROP TRIGGER denegados con errno real, filas intactas. Otorgar DROP al usuario desechable hace fallar el guard. Trigger ausente, canario ausente y revisión vieja niegan arranque sin cambiar filas. Rol heredado también niega.

La suite heredada puede usar fixture admin explícito para su DDL y monkeypatch del guard, pero eso no es evidencia de seguridad Office. Ninguna prueba Office de integración omite los guards reales y la cuenta restringida. No hay variable de producción para saltarse el guard.

El despliegue ejecuta primero migración oneshot con credencial separada y luego web DML; actualizar units y restore para quitar GRANT ALL runtime. No cambiar servicios ni credenciales ahora. Respaldo/restauración, regresiones clásicas con DML, auditoría SHA exacto y carga preceden activación. Un DDL inesperado en request se mueve al runner, no se amplían privilegios.

## Aprovisionamiento y aislamiento del proceso · refinamiento 2026-10-20

DECISIÓN TÉCNICA Codex basada en revisión Sol office_private_access_review. Separar solo archivos env no basta: web y LAS MANOS comparten actualmente uidjaxsvc, y el web podría leer la credencial legado DDL. La implementación preparará tres identidades OS y tres principalesDB; la activación real queda en Task6 con pruebas independientes.

- LAS MANOS conserva su identidad/principal/env legado sin ALTER/REVOKE de su cuenta. Es principal confiable con DDL de startup, no el runtime web.
- Web usa identidad dedicada `jaxweb`, migrador `jaxmigrate`, ambas distintas del legado `jaxsvc`. Valores de despliegue en configuración, sin inferirlos del actor humano. Env propios root:root0600 que systemd carga: el proceso web no pertenece al grupo que puede leer secretos legados/migrador. Accesos compartidos de datos se conceden por grupos de propósito sin credenciales.
- Web carga únicamente runtime.env completo: configuración necesaria filtrada sin claves de credenciales DB legadas/migrador/bootstrap, con un solo conjunto efectivo JAX_DB_HOST/PORT/NAME/USER/PASSWORD del runtime. Migrador carga solo migrate.env mínimo: esos cinco campos y JAX_REPO_PATH/JAX_EJECUTOR_INVENTARIO si runner requiere. Nada de `.env` común más override. Rechazar duplicados/claves malformadas; no source/eval de env como root; escribir atómicamente sin imprimir secretos.
- PrincipalesDB nuevos explícitos y distintos entre sí y del legado. Cuenta ausente: contraseña aleatoria>=256bits, archivo secreto persistido root-only, CREATE+GRANT, verificación de login y privilegios exactos. Cuenta existente: exige archivo coincidente, login válido y privilegios exactos; de lo contrario aborta sin ALTER/REVOKE/DROP. Archivo existente/cuenta ausente puede converger; cuenta existente/archivo ausente falla cerrado. Ninguna mutación ciega sobre una cuenta desconocida.
- Runtime DML exacto y migrador DML+CREATE/DROP/ALTER/INDEX/TRIGGER/REFERENCES solo esta base, USAGE global, cero roles/defaultroles/grants de tabla/columna/rutina/GRANTOPTION. Verificar de verdad, no asumir que GRANT quita permisos anteriores.
- Host grantee explícito verificado en despliegue, no inferido de DB_HOST. Sol midió backendDocker127.0.0.1:3308 con origencliente172.30.5.1; confirmar nuevamente antes de crear cuentas, no fijar `%`. Canal admin canónico del contenedor/secret montado, no asumir sudo mariadb del host. Ningún secreto en argv/logs.
- Unidades apuntan al artefacto `/srv/jax-prod`, no checkout `/home/fruiz`. Web Requires+After=migrate; oneshot sin RemainAfterExit. Actualizar/verificar drop-ins efectivos, incluido cuenta-de-servicio.conf que puede prevalecer sobre User base. Startup fallido del migrador impide web; cada start/restart real lo ejecuta de nuevo.

Pruebas del código: parser/saneamiento env, provisión idempotente y rechazo de principales existentes incompatibles, concesión DML real y preflightmigrador con grants extra, templates de unidad sin secretoscompartidos/paths viejos, regresiones clásicas y EXPLAIN con volumen representativo. Task6 verifica identidades/entornos/permisos efectivos con systemctl cat/show, denegación real de lectura de archivos y /proc ajenos comojaxweb, orden y repetición del runner, fallo de migrador sin web, LAS MANOS sin regresión. No afirmar esas pruebas de producción por probar templates.
