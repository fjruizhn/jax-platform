# Trabajos, artefactos y conversación Office · contrato previo

2026-10-20 · OP343/345 · PROPUESTA técnica Codex basada en arquitectura readonly Sol office_workflow_contract. Task5 se implementará después de barreras/MFA/API; este documento no acredita capacidad disponible. Dos refinamientos (atestación root y conversación cliente) pendientes de cerrar abajo antes de implementación.

## Ejecución y confidencialidad

Jacobs es el único ejecutor de modelos y fuente de épocas/cupo/preflight/catálogo/credenciales/uso. JXP conserva dominio Office: trabajos, inputs, artefactos, discusión, revisiones, publicaciones y eventos. No copiar cola del piloto ni procesamiento clásicoJSONL. La ruta Jacobs actual persiste input_ref y save_if_large sin alcance Office; no introducir prompts ni outputs confidenciales en sus tablas/archivos clásicos.

Nuevo contrato emparejado OFFICE_V1 / office-work.1: Jacobs persiste solo office_job_id,office_stage_id,project_id,client_request_id,request_sha256,facet. Un adapter materializa texto acotado solo en memoria y reemplaza salida cruda por recibo Office antes de save_if_large. No basta filtrar la UI clásica si el texto ya quedó en el storage clásico.

JXP internos POST/internal/virtual-office/stages/{stage_id}/{materialize,complete,fail}, autenticados como servicioJacobs con mecanismo existente X-Jax-Credencial-Servicio. Binding persistido stage/pipeline/run_epoch/step/correlation, nunca tenant/project/actor/grupo provenientes del body como autoridad. Materialize bloquea y reautoriza actor/operación/proyecto antes de leer referencias; complete reautoriza tras LLM y antes de guardar efecto, valida tamaño/digest. Revocación CANCELLED_REVOKED: descarta salida, conserva uso real ya generado, cero artefactos/publicaciones.

Complete idempotente por(stage,pipeline,epoch,step,outputsha); replay exacto devuelve mismo recibo, digest distinto409. Jacobs mantieneCASepoch, nunca escritura tardía de intento viejo. Creación pipeline debe ser idempotente en(invoked_by,tenant_id,client_request_id)+requestsha para reconciliar respuesta perdida sin duplicar gasto.

## Persistencia y estados

Tablas con tenant/project y FKcompuestas al namespaceOffice; índices segúnconsultas reales:

- office_work_item: definición inmutable/hash, discusiónfuente, estado/versionCAS.
- office_job: actor/operación/audiencia/grupo, idempotencia/digest, costomáximo aceptado, estado/epoch.
- office_job_stage: GENERATE/VALIDATE/REPAIR/EXPORT, bindingJacobs/epoch, estado, digests y salida privadano confiable.
- office_job_input_reference: IDcanónico/versión/hash/clasificación/orden; jamás URL/ruta elegida por navegador.
- office_artifact_version: artifact/versionúnico, bytesMEDIUMBLOB/sha/MIME/clase/procedencia/paquete exacto, inmutable.
- office_artifact_review: versión/hash exacto, actor, baseprofesional real, decisión/comentario append-only.
- office_publication: versión/revisión/gruporeceptor exactos, PUBLISHED/REVOKED; otra versión nunca hereda publicación.
- office_discussion_message: autorhumano/modelo/cliente, cuerpo, audienciaINTERNAL/CLIENT_GROUP, grupo obligatorio para cliente.
- office_project_event: secuenciadurable porproyecto/audiencia/grupo, payloadsolo IDs/estados.

Mutaciones idempotentes únicas(tenant,actor,operation,key)+requestsha, nunca readtheninsert. JobsQUEUED→RUNNING→SUCCEEDED|FAILED|CANCELLED|CANCELLED_REVOKED|OUTCOME_UNKNOWN. EtapasPREPARED→SUBMITTING→RUNNING→RESULT_RECEIVED→COMPLETED; SUBMISSION_UNKNOWN/FAILED/CANCELLED_REVOKED. Unknown se reconcilia por clave, no se relanza a ciegas. Reconciliador conlease/CAScoordinaetapas, noejecutamodelos ni crea otra cola.

## Autoridad y presentación

Ampliar operación central con READ_PROJECT_WORK,EXECUTE_PROJECT_WORK,POST_PROJECT_DISCUSSION,REVIEW_ARTIFACT,PUBLISH_ARTIFACT,EXPORT_ARTIFACT,READ_PUBLISHED_ARTIFACT. No excepciones locales en rutas. Cliente solo contenido y mensajes de grupo canónico resuelto en servidor; ninguna lectura de artefactos/revisiones internos. Descarga pasa por API con autorización fresca, sin URLcuya vigencia sobreviva revocación. Review/publicación transaccionales ligadas a versión/hash y MFA reciente.

SSE/WS consulta evento durable filtrado por proyecto/audiencia/grupo y reverifica sesión; busclásicoporusuario no es verdad compartida deproyecto. La escena3D deriva trabajo de jobs/stages reales; presenciaambiental se identifica aparte. Montaje/desmontaje cancela fetch/subscripción y libera recursos. No modeloafirma estar trabajando por una animación.

## Geometría y reparación

MODEL_UNTRUSTED exacto → parser/schema estrictos → paquete espacial versión+hash → una reparación LLM máxima si falla → revalidacióncompleta → plan/report reales → SVG/DXF solo si exportable → revisión/publicación explícita de entregables. Preservar original/reparado como versiones ligadas. Ningún Python generado, make_valid o cambio silencioso. Segundo fallo FAILED_VALIDATION sinexportación. pass_with_warnings conserva revisiónprofesional requerida. Presupuesto total inicial debe cubrir reparación real; si no, AWAITING_COST_APPROVAL. No inventar cuota comercial.

## Proveedores y conectores

Navegador selecciona rol/capacidad, no secretos/proveedor/URL. Rol→facet configurado, Jacobs resuelve catálogo/credencial/salud/costo. Extraer cliente privado compartido del preflight existente en api/pipelines.py; no duplicar lógica ni usar sesionesCLI personales paraproducto. max_concurrent_jobs explícito porpolíticadeproyecto; sinconfig no ejecutar. Conectores guardan credential_ref/alcance/estado/versión, nunca secretos. Leer solo IDs/hash exactos después de reautorizar, cambio deversión INPUT_STALE.

F2D es WebChat-only con audienciauser, no publicación a grupodeproyecto: reutilizar ideasdeincertidumbre, no afirmar quesuoutbox sirveOffice. Publicarapp es insercióntransaccionalrevocable. Efectosexternos futuros requieren outboxpropio conUNKNOWN y noreenvíoambiguo.

## Omoa y verificaciones

Importar bytes/timestamps/hashes exactos como historiaLEGACY_LOCAL_OPERATOR_UNAUTHENTICATED, artefactosDRAFT_UNVERIFIED, cero reviews/publicaciones, OP334 referenciaúnicaexistente sin POST/duplicado/aprobación. Backup y restauraciónverificada previos.

Pruebas mínimas: doble submit/timeoutunknown/epochviejo, revocaciónantesytrasLLM, bindingajeno, fuga a storage/API clásico, clientegrupoajeno, publicaciónversión/hash/reviewincorrecta, eventoSSErevocado, artifactinmutable, geometríafallida/norepairbudget, referenciasstale, reglas deatestaciónroot yconversacióncliente. EXPLAINconvolumen y cargapeorcaso antes de producción.

Fuentes: jax/jacobs/{store,artifacts,executor,models}.py y JXPbackend/api/pipelines.py, webchat_f2d/transport.py; lecturaSol sobreworktreesnamespace22715d51 y JXP5c340e0+WIP, pendiente deverificaciónraíz deinterfacesalimplementar. No se atribuye auditfinal a esta propuesta.

## Refinamientos cerrados por Codex tras segunda revisión Sol

Este bloque resuelve y sustituye los dos puntos pendientes de la propuesta anterior. Decisión técnica basada en requisitos explícitos de Fernando y segundo dictamen office_workflow_contract; implementación futura Task5.

### Acceso total y fundamento profesional

OFFICE_SUPERUSER puede revisar/aprobar/publicar/liberar cualquier versión sin grant de proyecto ni profesional. No agregar restricción funcional al acceso total solicitado. La review registra decisionAPPROVED|CHANGES_REQUESTED y approval_basisOFFICE_SUPERUSER|PROFESSIONAL_GRANT. Baseprofesional exige grantID/evidenciahash/snapshotexactos; baseOfficeSuperuser exige actorrootactivo y grantprofesionalNULL. Si root dispone además de grantprofesional válido puede elegir ese fundamento comprobado.

Publicación conserva release_classPROFESSIONAL_RELEASE|ADMINISTRATIVE_RELEASE según base real. Ambas se aprueban y publican; cliente ve la clase real. Roladministrativo no acredita título/licencia ni convierte validacióngeométrica en cumplimiento normativo.

### Conversación cliente con Dirección

La función de conversar con Dirección/agente dedicado es parte del requisito de producto y se implementa con el mismo adapterJacobs opaco, no con acceso a jobs internos. Operación REQUEST_CLIENT_RESPONSE, job_kindCLIENT_RESPONSE, stageGENERATE_CLIENT_REPLY. Cliente necesita claseEXTERNAL_CLIENT/COLLABORATOR activo, grantCOLLABORATOR/CLIENT_PUBLISHED activo, relación/grupoCLIENT activo del mismo proyectoOffice. Grupo se resuelve desde thread/relación en servidor. Insertarmensaje+crearjob son atómicos e idempotentes. Reautorizar al solicitar, materializar y completar; revocación descarta respuesta y CANCELLED_REVOKED.

Contexto únicamente mensajeactual, mensajesCLIENT_GROUP del mismo thread/grupo, versiones publicadasPUBLISHED para ese grupo y plantilla deatención configurada. Nunca INTERNAL, borradores/reviews/documentosnopublicados, memoriaclásica/tenant, conectores/herramientas, prompts deespecialistas ni otrosalcances. Entrada no confiable como datos, una llamada sin herramientas/bucles. Navegador no elige proveedor/modelo/facet/documentos/especialistas.

Configuración office_client_agent_config por tenant/proyecto: DIRECTOR|DEDICATED_CLIENT_AGENT,facet_key,prompt_template_version,status,execution_policy_ref. Política configurada habilita respuesta/tasa/concurrencia/contexto/salida/presupuesto; ausencia falla antes de gasto. Jacobsresuelvecatálogo y preflight. No inventar cuota comercial ni cobro implícito no aceptado.

Complete entrega office_discussion_message MODEL/CLIENT_GROUP/grupoexacto, delivery_basisCLIENT_REQUEST, job/stage/outputsha y professional_statusNOT_PROFESSIONALLY_REVIEWED. Es respuesta alrequestespecífico en suaudiencia, no office_publication, aprobación ni entregableprofesional. Artefactos mantienen review/publicaciónexplícita. Eventos soloIDs/estado/grupo; el cuerpo se obtiene con autorizaciónfresca. Citas solo de publicaciones ya accesibles.

### Reutilización de comprobación del informe

Antes de habilitar el worker productivo, extraer la comprobación estricta del informe hoy enpilot/spatial_api.py a un módulo puro del paquete virtual_office_spatial, preservando bytes/semántica y pruebas. API y worker consumen la misma función; ningún import depilot/FastAPI desde paquete ni copia de la lógica. La extracción tiene checkpoint/gate independiente y versión de paquete registrada. No afirmar que el worker aplica esa comprobación mientras solo exista en el adaptadorHTTP del piloto.
