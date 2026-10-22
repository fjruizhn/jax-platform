# MFA en la autoridad Office

2026-10-21 · DECISIÓN técnica Codex con análisis de delivery_path (Sol), previa a Task3B. No habilita rutas ni permisos.

La autoridad recibe `mfa_proof_event_id` opcional, tomado por el adaptador del usuario autenticado y sus claims firmados, nunca del body/query. Recibe configuración MFA del servidor. El identificador es una referencia que debe verificarse en base de datos, no una prueba por sí mismo. No se introduce otro contexto autenticado ni un receipt aceptado como autoridad.

El servicio MFA reutiliza un loader privado exacto: usuario actual y evento VERIFIED, sujeto correcto, propósito LOGIN con PASSWORD_PREAUTH/actor nulo o STEP_UP con USER_SESSION/self, versiones token/MFA actuales y fecha no futura en UTC de base. Expone dos operaciones nombradas: `reload_current_self_mfa_verified_in_cursor` sin techo de edad y el existente `reload_current_self_mfa_proof_in_cursor` con la recencia configurada. No hay selector público bool/max_age en estas operaciones.

Después de resolver roles/grants canónicos, el resolver central deriva el requisito: toda operación distinta de VIEW_PROJECT necesita prueba reciente; VIEW_PROJECT necesita prueba verificada para OFFICE_SUPERUSER, GENERAL_ADMIN o grant PROJECT_ADMIN. Un colaborador ordinario puede ver con su grant y sin prueba. Falta/invalidez devuelve denegación, nunca autoridad por fallback. La variante in_cursor comprueba el evento en la misma transacción antes del allowed.

Ruling Codex sobre una recomendación intermedia de Sol: se conserva `require_recent_mfa` por compatibilidad con la firma auditada y el contrato previo. Solo endurece: requisito final es el más fuerte entre requisito derivado y RECENT cuando el flag es verdadero. False nunca desactiva un requisito derivado. Se descarta eliminar el parámetro porque añade ruptura sin mejorar esa garantía. Coste: matriz de pruebas explícita para ambos valores.

AuthUser/JWT incorpora mfa_proof_event_id, mfa_version y mfa_at derivados del servidor. El resolver confía en el reload durable, no en la antigüedad/versión declarada en claims. Los adaptadores interactivos de namespace pasan la referencia y configuración; sin ellas las mutaciones privilegiadas quedan denegadas.

Los callbacks de trabajo de Task5 no fabrican sesión de navegador: la admisión guarda `job_admission_authorization_event_id` y su prueba ligada; callback reautoriza la admisión/actor/grant/binding actuales mediante su contrato de workflow. Esto no es una excepción actor-only al resolver interactivo.

Pruebas obligatorias: evento de otro sujeto, DENIED, versiones antiguas y futuro deniegan; prueba antigua aún corriente permite VIEW privilegiado pero deniega mutación; roles root/general/projectadmin sin prueba deniegan VIEW; colaborador ordinario con grant permite VIEW sin prueba; flag false no rebaja y true endurece; namespace adapter no puede omitir prueba en mutación. No caché de permisos.
