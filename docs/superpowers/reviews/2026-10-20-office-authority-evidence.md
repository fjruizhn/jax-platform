# Office · evidencia de autorización y ejecución restringida

2026-10-20 · OP#343/345 · Codex. No producción modificada. Fuente de autorización de producto: conversación con Fernando; roles y alta por invitación decididos por él.

Sol office_authority_review rechazó6e290bb7e61f63d351a83494d1386c4c3e780b15 por audiencia/rol, FKs incompletas, EXPLAIN/matriz y cuenta runtime con DDL capaz de truncar audit. Corrección por Tier2 office_authority_implement, contrato de separación revisado por Sol office_audit_privileges_contract. Nueva auditoría sobre el siguiente checkpoint todavía pendiente.

Raíz verificó:
`python3 /tmp/cv-office-run-tests.py jxp -q --tb=short --disable-warnings tests/test_office_authority.py tests/test_office_migrations.py tests/test_office_runtime_contract.py tests/test_auth.py tests/test_proyectos_api.py`
Resultado59passed437warnings1.63s.24Office+35regresiones auth/proyectos. WarningsDDL idempotente heredada. `bash -n ops/migration/restore-hall9000.sh` y `git diff --check` pasan.

## Qué se comprobó

Dos tenants/proyectos reales, root Office sin asignación entre tenants, generaladmin delegado sin contenido implícito ni crear proyectos, projectadmin sin promoción profesional/global, staff INTERNAL/OFFICE_INTERNAL requiere membership+grantINTERNAL, cliente soloCOLLABORATOR/CLIENT_PUBLISHED/grupoactivoexacto. Decisión conserva contexto de audiencia/grant/grupo. IDs solicitados se distinguen de proyecto/tenant canónicos y sus FKs. Denegación de objetivo inexistente o de otrotenant se registra sin inventar un scope.

Cuenta aleatoria DML en MariaDB12.3.3 desechable33991: guard de privilegios/revisión/canario y probes reales de UPDATE/DELETE con rollback y reread. DDL TRUNCATE/DROP/ALTER/DROP TRIGGER rechazado1142; CREATE ROLE1227; GRANT1045. Añadir DROP, global SELECT, GRANT OPTION, permiso de rutina o rol hace fallar el guard. Identidad DB ambigua se deniega. Secreto/password no registrado.

El usuario DML ve metadata de triggers pero no ACTION_STATEMENT: raíz lo midió con dos usuarios efímeros propios y los eliminó. El guard usa pruebas conductuales, no inspección del cuerpo ni privilegioTRIGGER. SYSTEM_FENCE representa al runner real, no usuario humano.

El runner canónico se ejecuta por db.migrate en proceso oneshot, y web no lo llama. Revisión por componente se invalida antes del trabajo de migración y solo se confirma al final; un fallo posterior forzado mantiene no-listo. Source units separan credenciales; no se instalaron ni reiniciaron servicios.

## Límites

La protección es frente al principal DB runtime. Administrador DB/migrador son de confianza; no se afirma inmutabilidad frente a ellos ni frente a un compromiso total del host. La suite heredada prepara DDL privilegiado y puede sustituir el guard en su fixture; no acredita seguridad Office por sí sola. La prueba de runtime usa su cuenta restringida real y el guard sin sustituirlo. MFA/HTTPWSbarreras/APIs/escena/colas aún son siguientes tareas; no clientes habilitados.

## Corrección y verificación · fixround2

CORRECCIÓN de la afirmación anterior sobre separación en unidades: Sol rechazó5c340e0 porque restore no creaba realmente los env, web heredaba secreto migrador, faltaba dependencia de oneshot y la cuenta compartida LAS MANOS conservabaDDL. Separar solo env tampoco basta cuando comparten uid/grupo. El contrato revisado exige web/migrador con identidadesOS/DB dedicadas y preserva legado confiable sin revocarlo. No se aplicó el cambio a servicios vivos.

CHECKdiscriminador audit impide SYSTEM_FENCE falso/AUTHORIZATIONsinactor; no reescribe historial y conserva constraint compatible; incompatible falla cerrado. Lifecycle existente actualizado al guard DB. Fuente: diff/backendtests, originalTier2 y sucesoropscontextofresco.

Raíz ejecutó fixture real `sudo -n ops/migration/tests/provision-db-runtime-disposable.sh`, exclusivamente contenedor propio cv-op343-db-20261020 ID7cdc8cc68ac3f4ff60d8e42d10f0ea70141b179a7504f843d2dd52da0414d307 publicado127.0.0.1:33991. Exit0: CREATE/grants exactos con loginCURRENT_USER, archivosroot-only temporales, segundoapplyidempotente, rechazo de permisos inseguros/secretomissingen cuentaexistente/extraGRANT. Limpieza solo de principalesaleatoriospropios. No useradd/systemctl ni escrituras/etc o/srv. OSidentidades/drop-ins efectivos y ordenstart/restart reales siguen Task6 antes de activación. Dos fallos iniciales de la prueba se resolvieron: nombre aleatorio excedía32caracteres y SHOWGRANTS reordena privilegios; ahora nombres válidos y comparación de conjunto exacto, no deordenSQL.

EXPLAINindependiente ejecutando las seis funciones reales del repository con/sin FORUPDATE:12planes, todos con keyelegida y sinALL/filesort/temporary. Volumen sintético medido:1256usuarios,1451scopes,1432memberships,461Officegrants,66relaciones,40delegaciones,19professionalgrants. ResultadoJSONsincredenciales en2026-10-20-office-authority-explain.json. No se infiere carga productiva de estos planes.

Raízprobó regresiónlifecycle puntualmente:1passed0.64s. Suitecombinada antes de quitar prueba de tokens deimplementación65passed451warnings1.75s; cifra final posterior se registra abajo. La eliminación conserva prueba conductual real del orden deprivilegios. Dictamen exactSHA todavía pendiente.

Verificaciónfinal raíz fixround2:64passed451warnings1.80s con--disable-warnings, Office/provisión/runtime/auth/proyectos/lifecycle. Bashsyntax tresguiones ygitdiffcheck pasan.
