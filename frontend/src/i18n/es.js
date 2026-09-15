export default {
  // Top bar
  logout: 'Salir',

  // Left panel
  facets: 'Facetas',
  // Nombre propio del componente (como killButton/eyeLasManosDown): igual en los dos idiomas.
  lasManos: 'LAS MANOS',
  alive: 'vivo',
  down: 'caído',
  // M-2 (revisión final PR 3, 2026-09-14): wsStatus (useJaxStore.js,
  // api/websocket.js) se mostraba crudo, sin traducir. Un valor que el
  // diccionario no conoce cae en el mismo dato crudo (LeftPanel.jsx).
  wsStatusLabels: {
    connected: 'Conectado',
    disconnected: 'Desconectado',
    reconnecting: 'Reconectando',
  },

  // FacetCard status
  statusIdle: 'En reposo',
  statusThinking: 'Pensando…',
  statusError: 'Error',
  statusOffline: 'Offline',

  // Right panel
  tabDirectorJacobs: 'Director Jacobs',
  tabAudit: 'Audit',
  stepsLabel: 'pasos',
  noPipelinesActive: 'Sin pipelines activos',
  // M-2 (revisión final PR 3, 2026-09-14): activePipeline.status
  // (jax_engine/schemas.py::PipelineStatus) se mostraba crudo. Un valor que
  // el backend agregue y el diccionario no conozca cae en el dato crudo
  // (RightPanel.jsx).
  pipelineStatusLabels: {
    pending: 'Pendiente',
    running: 'En curso',
    waiting_gate: 'Esperando aprobación',
    completed: 'Completado',
    failed: 'Fallido',
  },
  pipelinesAdditional: (n) => `+${n} pipeline(s) adicional(es)`,
  approve: '✓ Aprobar',
  cancelling: 'Cancelando…',
  cancelPipeline: 'Cancelar pipeline',
  approveError: 'No se pudo aprobar el paso. Probá de nuevo.',
  cancelError: 'No se pudo cancelar el pipeline. Probá de nuevo.',
  // Nombre guardado del pipeline: el objetivo, recortado a 50 caracteres.
  pipelineName: (objetivo) => `Pipeline: ${objetivo.slice(0, 50)}`,

  // Audit log
  auditLog: 'Audit Log',
  loading: 'Cargando…',
  noEventsYet: 'Sin eventos aún',

  // Step card
  clickToSeeResult: 'Click para ver resultado en chat',

  // Bottom bar — modes
  modeChat: 'Chat',
  modeComando: 'Comando',
  modePipeline: 'Pipeline',
  modeImagen: 'Imagen',
  placeholderChat: (label) => `Mensaje a ${label}… Enter para enviar, Shift+Enter para nueva línea`,
  placeholderComando: () => 'Describe la tarea para Hyde… (autónomo)',
  placeholderPipeline: () => 'Describe el objetivo del pipeline…',
  placeholderImagen: () => 'Describe la imagen que querés generar…',
  hydeHint: 'Hyde ejecutará la tarea de forma autónoma en background',
  jacobsHint: 'Jacobs orquestará múltiples facetas en pipeline',
  imagenHint: 'DALL-E 3 generará la imagen a partir de tu descripción',
  errorImagen: 'No se pudo generar la imagen.',
  generatingImage: 'Generando…',
  configure: 'Configurar',
  send: 'Enviar',
  errorFacet: 'No se pudo conectar con la faceta.',
  taskInitializing: '_Iniciando tarea autónoma…_',
  taskStarted: (id) => `_Tarea iniciada — \`${id}\`_\n\nHyde está ejecutando en background…`,
  errorTask: 'No se pudo iniciar la tarea.',
  commandNoResult: '(sin resultado)',
  pipelineStarted: (id, mode, steps) =>
    `Pipeline iniciado — \`${id}\`\nModo: **${mode}** · ${steps} steps\n\nSiguiendo progreso en panel derecho…`,
  errorPipeline: 'No se pudo crear el pipeline.',
  pipelineStepHeader: (facet, capability) => `● **${facet}** — ${capability}`,
  pipelineCompleted: (done, total, secs) =>
    `**Pipeline completado** — ${done} de ${total} steps${secs ? `, ${Math.round(secs)}s totales` : ''}`,
  pipelineSources: 'Fuentes',
  pipelineNoResult: '_(sin resultado)_',

  // Kill switch
  killSwitchActive: 'KILL SWITCH ACTIVO',
  killConfirm: '¿Confirmar?',
  killConfirmYes: 'SÍ, DETENER TODO',
  // Término del producto (como eyeKillSwitch): igual en los dos idiomas.
  killButton: 'KILL',
  // Sigla técnica (WebSocket), igual en los dos idiomas.
  wsLabel: 'WS',
  cancel: 'Cancelar',
  killTitle: 'Kill Switch — detiene todos los procesos',
  // killSwitchActive: badge persistente en la UI. killSwitchToast: toast al
  // recibir el evento de WS (otro usuario/proceso lo activó). killSwitchStoppedToast:
  // toast tras activarlo uno mismo desde este cliente (activateKillSwitch).
  killSwitchToast: 'KILL SWITCH ACTIVADO',
  killSwitchStoppedToast: 'KILL SWITCH ACTIVADO — todos los procesos detenidos',

  // Store event toasts (WS handleEvent)
  humanGateRequestedToast: (id) => `Jacobs espera aprobación — pipeline ${id}`,
  pipelineResultsError: (id) => `No se pudieron cargar los resultados del pipeline ${id}`,

  // Pipeline modal
  newPipelineTitle: 'Nuevo Pipeline · Jacobs',
  objectiveLabel: 'Objetivo',
  modeLabel: 'Modo',
  // I-1 (revisión final PR 3, 2026-09-14): las etiquetas de modo del pipeline
  // estaban hardcodeadas en inglés dentro de PipelineModal.jsx.
  pipelineModeSupervised: '👁 Supervisado',
  pipelineModeAutonomous: '⚡ Autónomo',
  pipelineModeDryRun: '🧪 Prueba en seco',
  facetsLabel: 'Facetas',
  starting: 'Iniciando…',
  planAndExecute: 'Planificar y ejecutar',
  descJaxLocal: 'Razonamiento local (Qwen3)',
  descHipatia: 'Investigación web',
  descJekyll: 'Análisis reflexivo',
  descThot: 'Auditoría crítica',
  descKimi: 'Implementación técnica',
  descAda: 'Análisis y rigor',
  autoMotor: 'Auto (por competencia)',
  facetUngoverned: 'Sin gobernanza de Motor Registry — no se valida contra capabilities reales.',
  catalogLoadingHint: 'Cargando catálogo de motores…',
  catalogFailedHint: 'No se pudo cargar el catálogo de motores — no se puede planificar hasta que cargue.',
  errorPipelinePrefix: 'Error pipeline',
  layoutLabel: 'Forma',
  layoutChain: 'En cadena',
  layoutParallel: 'En paralelo',
  chainLabel: 'Cadena',
  chainHint: 'Cada paso recibe la salida de los pasos anteriores que necesita. Si uno falla, la cadena se detiene.',
  chainRoles: {
    research: 'Investigar',
    plan: 'Maquetar y planificar',
    critique: 'Criticar el plan',
    unify: 'Unificar plan y crítica',
    produce: 'Producir',
    audit: 'Auditar',
  },
  chainCleanroomWarning: (role, facet, depRole) =>
    `${role}: ${facet} no puede auditar lo que produjo en «${depRole}». Elige otra faceta.`,
  chainInvalidFacet: (role) => `${role}: la faceta elegida no está permitida para este paso según el catálogo.`,
  // Instrucciones que recibe cada modelo. Van en el idioma de la interfaz.
  chainInstructions: {
    research:
      'Rol: investigador. Investiga a fondo el objetivo con fuentes verificables y cita cada una. ' +
      'Separa los hechos verificados de los supuestos y declara explícitamente lo que no pudiste verificar.',
    plan:
      'Rol: arquitecto. Con la investigación recibida, maqueta y planifica: estructura, módulos, ' +
      'orden de construcción y por qué empezar por ahí. Todo dato que uses debe venir de la investigación; ' +
      'lo que falte, decláralo como incógnita en vez de suponerlo.',
    critique:
      'Rol: crítico. Critica el plan contra la investigación: huecos, riesgos, supuestos sin respaldo ' +
      'y orden equivocado. Numera cada hallazgo, indica a qué parte del plan se refiere y con qué evidencia. ' +
      'No reescribas el plan.',
    unify:
      'Rol: unificador. Produce el plan final incorporando la crítica. Para cada hallazgo numerado de la ' +
      'crítica, di si lo aceptas y qué cambia, o si lo rechazas y por qué.',
    produce:
      'Rol: productor. Con el plan unificado, produce el entregable completo. Síguelo; si te apartas de él, ' +
      'di dónde y por qué.',
    audit:
      'Rol: auditor independiente. Tu única fuente de verdad es la investigación y el objetivo: no aceptes ' +
      'como fuente el plan, la crítica ni el producto. ' +
      '1) Marca como NO VERIFICADA toda afirmación del producto que no esté respaldada por la investigación, ' +
      'y toda cita que no aparezca en ella. ' +
      '2) Señala las contradicciones entre el producto y el plan unificado. ' +
      '3) Medición contra la crítica original (no contra lo que el plan dice de ella): para cada hallazgo ' +
      'numerado de la crítica, di si llegó al producto, si el plan unificado lo rechazó con una razón, o si se ' +
      'perdió sin explicación. Cierra con el conteo de cada caso.',
  },

  // Center panel
  platformLabel: 'AXIOMA V0.2',
  inMemoryOf: 'En memoria de Jairo Urbina.',
  inHonorOf: 'En honor al Prof. Raúl Jacobs.',

  // Login
  brandName: 'Axioma',
  brandTagline: 'Infraestructura Cognitiva Personal',
  loginTitle: 'Axioma',
  loginTagline: 'En memoria de Jairo Urbina',
  emailLabel: 'Email',
  passwordLabel: 'Contraseña',
  loginError: 'Usuario o contraseña incorrectos',
  loggingIn: 'Iniciando…',
  loginButton: 'Entrar a Axioma',
  showPassword: 'Mostrar contraseña',
  hidePassword: 'Ocultar contraseña',
  // Motivo del cierre de sesión (2026-09-14, Task 4b): antes el interceptor
  // de api/client.js borraba la sesión en silencio ante un refresh fallido.
  sesion_invalida: 'Tu sesión se cerró: se inició sesión en otro lugar, o tu acceso cambió. Iniciá sesión de nuevo.',
  sesion_expirada: 'Tu sesión venció. Iniciá sesión de nuevo.',
  accountLocked: 'Cuenta bloqueada. Revisa tu correo.',
  accountLockedMinutes: (min) => `Cuenta bloqueada. Intenta de nuevo en ${min} minuto(s).`,
  tooManyAttempts: 'Demasiados intentos. Espera un momento y vuelve a intentarlo.',
  tooManyAttemptsSeconds: (s) => `Demasiados intentos. Vuelve a intentarlo en ${s} segundo(s).`,
  forgotPassword: '¿Olvidaste tu contraseña?',
  forgotPasswordTitle: 'Recuperar contraseña',
  forgotPasswordDesc: 'Ingresá tu correo y te enviaremos las instrucciones.',
  forgotPasswordSent: 'Si el correo existe, recibirás las instrucciones en breve.',
  forgotPasswordSend: 'Enviar instrucciones',
  forgotPasswordSending: 'Enviando…',
  backToLogin: 'Volver al login',
  emailPlaceholder: 'nombre@empresa.com',
  resetPasswordTitle: 'Nueva contraseña',
  resetPasswordDesc: 'Ingresá tu nueva contraseña.',
  resetPasswordLabel: 'Nueva contraseña',
  resetPasswordConfirm: 'Confirmar contraseña',
  resetPasswordMismatch: 'Las contraseñas no coinciden',
  resetPasswordShort: 'Mínimo 8 caracteres',
  resetPasswordSuccess: 'Contraseña actualizada. Ya podés iniciar sesión.',
  resetPasswordSubmit: 'Cambiar contraseña',
  resetPasswordSubmitting: 'Guardando…',
  resetPasswordInvalid: 'El enlace es inválido o ya fue usado.',
  resetPasswordUsed: 'Este enlace ya fue usado. Solicitá uno nuevo.',
  resetPasswordExpired: 'El enlace expiró. Solicitá uno nuevo.',
  resetPasswordLong: 'La contraseña es demasiado larga (máximo 72 bytes; los acentos ocupan 2).',

  // Message
  userLabel: 'Usuario',
  contractDegradedNote: 'La respuesta no cumplió el formato esperado.',
  // I-1 (revisión final PR 3, 2026-09-14): alt de <img> hardcodeado en
  // español, sin pasar por i18n (se veía en la interfaz en inglés).
  altGeneratedImage: 'imagen generada',
  altAttachment: 'adjunto',

  // Theme / language
  lightMode: 'Modo claro',
  darkMode: 'Modo oscuro',
  switchLanguage: (idioma) => `Cambiar idioma a ${idioma}`,
  adminPanel: 'Administración',

  // File attachments
  attachFile: 'Adjuntar archivo',
  attachTooltip: 'Adjuntar imagen, PDF o texto',
  attachRemove: 'Quitar adjunto',
  attachUploading: 'Subiendo…',
  attachError: 'Error al subir archivo',
  attachTooLarge: 'Archivo demasiado grande (máx 10MB)',
  attachTypes: 'Imágenes, PDF, texto, código',
  attachedFile: (name) => `Adjunto: ${name}`,
  attachReady: '✓ listo',

  // Admin module
  adminTitle: 'Administración',
  adminNav: 'Admin',
  adminDashboard: 'Dashboard',
  adminFacetsModels: 'Facetas & Modelos',
  adminUsers: 'Usuarios',
  adminRepo: 'Repositorio',
  adminSettings: 'Configuración',
  adminCosts: 'Costos',
  adminBack: 'Volver a Axioma',

  // Admin dashboard
  adminServicesTitle: 'Estado de Servicios',
  adminStatsTitle: 'Estadísticas de Hoy',
  adminEventsTitle: 'Últimos Eventos',
  serviceAlive: 'activo',
  serviceDown: 'caído',
  serviceConnected: 'conectado',
  serviceError: 'error',
  statMessages: 'Mensajes',
  statPipelines: 'Pipelines',
  statImages: 'Imágenes',
  statUsersActive: 'Usuarios activos',
  statUsersLocked: 'Bloqueados',
  statApiKeys: (c, t) => `API Keys: ${c}/${t}`,
  statRam: (pct) => `RAM: ${pct}%`,
  // I-1 (revisión final PR 2, 2026-09-14): AdminDashboard.jsx tenía
  // label="API Keys" fijo. "API Keys" es el mismo término en los dos
  // idiomas (nombre técnico), como brandName o eyeKillSwitch.
  statApiKeysLabel: 'API Keys',

  // Admin API keys
  adminKeyProvider: 'Proveedor',
  adminKeyModel: 'Modelo',
  adminKeyFacet: 'Faceta',
  adminKeyValue: 'Key',
  adminKeyStatus: 'Estado',
  adminKeyTest: 'Probar',
  adminKeyRotate: 'Rotar key',
  adminKeyRevoke: 'Revocar',
  adminKeyRevoking: 'Revocando…',
  adminKeyRevokeConfirmTitle: 'Revocar todas las credenciales activas',
  adminKeyRevokeConfirmBody: 'Esto corta el acceso de inmediato, sin ventana de gracia. ¿Confirmás?',
  adminKeyActiveCount: (n) => `${n} activa${n === 1 ? '' : 's'}`,
  adminKeyLastVerified: 'Última verificación',
  adminKeyNoActive: 'Sin credencial activa',
  adminKeyTesting: 'Probando…',
  adminKeyOk: 'OK',
  adminKeyFail: 'Error',
  adminKeyMissing: 'Sin key',
  adminKeyNewValue: 'Nueva API key',
  adminKeySave: 'Guardar',
  adminKeyEnter: 'Ingresá la nueva key para',
  adminKeyLatency: (ms) => `${ms}ms`,
  adminKeyAddModel: 'Agregar modelo',
  adminKeyModelProvider: 'Provider ID',
  adminKeyModelName: 'Nombre del modelo',
  adminKeyModelAdd: 'Agregar',
  adminKeyModelDelete: 'Eliminar modelo',
  adminKeyModelDeleteActive: 'No se puede eliminar el modelo activo',
  adminKeyModelDeleteConfirmTitle: (name) => `Confirmá la eliminación de "${name}"`,
  adminKeyModelDeleteConfirmSum: (a, b) => `Resolvé ${a} + ${b} = ? para confirmar`,
  adminKeyModelDeleteConfirmPlaceholder: 'Resultado',
  adminKeyModelDeleteConfirmButton: 'Eliminar',
  adminKeyModelDeleteConfirmWrong: 'Resultado incorrecto',

  // Admin — pestañas Bloque D (catálogo de modelos y facetas/bindings)
  adminTabProviders: 'Proveedores y Credenciales',
  adminTabModels: 'Catálogo de modelos',
  adminTabBindings: 'Facetas y Bindings',

  adminModelsTitle: 'Catálogo de modelos',
  adminModelsSync: 'Sincronizar',
  adminModelsSyncing: 'Sincronizando…',
  adminModelsSyncError: 'Error al sincronizar',
  adminModelsProvider: 'Proveedor',
  adminModelsModelId: 'Modelo',
  adminModelsAlias: 'Alias',
  adminModelsStatus: 'Estado',
  adminModelsSource: 'Origen',
  adminModelsContext: 'Contexto',
  adminModelsPrice: 'Precio (in/out por 1M)',
  adminModelsSourceCheckedAt: 'Verificado',
  adminModelsNoData: '—',
  adminModelsStatusAvailable: 'Disponible',
  adminModelsStatusDegraded: 'Degradado',
  adminModelsStatusDeprecated: 'Deprecado',
  adminModelsStatusGone: 'Retirado',
  adminModelsSourceProviderApi: 'API del proveedor',
  adminModelsSourceModelsDev: 'models.dev',
  adminModelsSourceManual: 'Manual',
  adminModelsSourceObserved: 'Observado en vivo',

  adminProposalsTitle: 'Cambios propuestos',
  adminProposalsEmpty: 'Sin propuestas pendientes.',
  adminProposalsFacet: 'Faceta',
  adminProposalsCurrent: 'Modelo actual',
  adminProposalsProposed: 'Modelo propuesto',
  adminProposalsReason: 'Motivo',
  adminProposalsDetail: 'Detalle',
  adminProposalsApprove: 'Aprobar',
  adminProposalsReject: 'Rechazar',
  adminProposalsApproving: 'Aprobando…',
  adminProposalsRejecting: 'Rechazando…',
  adminProposalReasonNewModel: 'Modelo nuevo disponible',
  adminProposalReasonDrift: 'Drift detectado',
  adminProposalReasonDeprecation: 'Aviso de deprecación',

  adminBindingsTitle: 'Facetas y Bindings',
  adminBindingsFacet: 'Faceta',
  adminBindingsTransport: 'Transporte',
  adminBindingsModel: 'Modelo activo',
  adminBindingsCapability: 'Contrato',
  adminBindingsCapabilityOk: '✓ Cumple',
  adminBindingsCapabilityWarning: '⚠ No cumple',
  adminBindingsCapabilityUnknown: '? Sin datos',
  adminBindingsNoBinding: 'Sin binding',
  adminBindingsEdit: 'Editar',
  adminBindingsSave: 'Guardar',
  adminBindingsCancel: 'Cancelar',
  adminBindingsSaving: 'Guardando…',
  adminBindingsSaveError: (detail) => `No se pudo guardar: ${detail}`,
  adminBindingsSelectModel: 'Elegí un modelo del catálogo',
  // 409 de aprobar una propuesta o guardar un binding (2026-09-14, PR-J): el
  // modelo destino no declara lo que el dispatch de esa faceta necesita.
  modelo_sin_contrato_de_dispatch: (modelo, campos) =>
    `El modelo ${modelo} no declara ${campos} en el catálogo, así que esta faceta no podría usarlo. Hay que completar esa fila del catálogo antes. No se cambió nada.`,
  adminProposalsDecideError: 'No se pudo completar la decisión sobre la propuesta.',
  // 409 de la ronda 1 (2026-09-14): el binding quedaría con un proveedor que
  // no es el del modelo, y la faceta mandaría el modelo al servicio equivocado.
  modelo_de_otro_proveedor: (modelo, proveedorModelo, proveedorBinding) =>
    `El modelo ${modelo} es de ${proveedorModelo}, pero esta faceta quedaría configurada con ${proveedorBinding}: no podría usarlo. Elegí un modelo de ${proveedorBinding}. No se cambió nada.`,
  // PR-L (2026-09-14): declarar el contrato de dispatch de una fila del
  // catálogo desde el admin (antes era un UPDATE a mano).
  adminContratoTitulo: (modelo) => `Contrato de dispatch de ${modelo}`,
  adminContratoParam: 'Nombre del parámetro de límite de salida',
  adminContratoTope: 'Tope de tokens de salida',
  adminContratoAyuda: 'Lo que la API de este modelo exige al despachar: cómo se llama su parámetro de límite de salida y cuántos tokens de salida acepta como máximo. El tope sale de la documentación del proveedor o de su propio error HTTP 400; no es la ventana de contexto. Queda registrado quién lo declaró y el valor anterior.',
  adminContratoElegir: 'Elegí uno',
  adminContratoGuardar: 'Guardar contrato',
  adminContratoGuardando: 'Guardando…',
  adminContratoCancelar: 'Cancelar',
  adminContratoGuardado: 'Contrato declarado. Ya se puede volver a aprobar la propuesta.',
  adminContratoError: 'No se pudo declarar el contrato.',
  contrato_dispatch_invalido: (campos) =>
    `El valor de ${campos} no es válido para el dispatch de este modelo. No se cambió nada.`,
  adminContratoDeclarar: 'Declarar contrato',
  adminModelsContrato: 'Contrato de dispatch',
  adminModelsContratoSinDeclarar: 'Sin declarar',
  adminProposalsUltimoRechazo: (fecha) => `Último intento de aprobación rechazado (${fecha}):`,
  // PR-L ronda 1: el último rechazo registrado para una faceta, en Bindings.
  adminBindingsUltimoRechazo: (fecha) => `Último cambio rechazado (${fecha}):`,
  // Ronda 2: en Bindings no hay propuesta; el paso siguiente es volver a guardar el binding.
  adminBindingsContratoGuardado: 'Contrato declarado. Ya se puede volver a guardar el binding de la faceta.',

  // Admin — pestaña Motores (R4 Task 9): alta de motor/capability sin SQL a mano
  adminTabMotors: 'Motores',
  adminMotorsTitle: 'Motores',
  adminMotorsCreate: '+ Nuevo motor',
  adminMotorsCreateTitle: 'Dar de alta un motor',
  adminMotorsKey: 'Clave',
  adminMotorsKeyPlaceholder: 'ej. gemini_flash',
  adminMotorsSelectProvider: 'Elegí un proveedor',
  adminMotorsSelectModel: 'Elegí un modelo',
  adminMotorsMaxTokens: 'Max tokens (0 = sin límite)',
  adminMotorsTimeout: 'Timeout (segundos)',
  adminMotorsSupportsReasoning: 'Soporta razonamiento (reasoning_content)',
  adminMotorsSandboxOnly: 'Solo sandbox',
  adminMotorsCapabilities: 'Capabilities que puede atender',
  adminMotorsPriority: 'Prioridad (menor = primera opción)',
  adminMotorsSave: 'Crear motor',
  adminMotorsSaveError: (detail) => `No se pudo crear el motor: ${detail}`,
  adminMotorsDispatchable: 'Despacho',
  adminMotorsDispatchableYes: '✓ Implementado',
  adminMotorsDispatchableNo: '⚠ Sin dispatcher aún',
  adminMotorsNoCapabilities: 'Sin capability asignada',
  adminMotorsEmpty: 'No hay motores dados de alta todavía.',
  adminMotorsLimitationNote: 'Limitación conocida: hoy solo los transportes "http_openai_compat" y "ollama" tienen dispatcher implementado en las_manos/motor_registry/worker.py. Un motor con otro transporte (http_gemini, subprocess, motor_registry) queda dado de alta pero un job real fallará hasta sumar su dispatcher — deuda con nombre, no bloqueante. Además: un motor recién dado de alta se escribe en la DB de inmediato, pero no es despachable vía jax-las-manos.service hasta que ese servicio se reinicie — carga su catálogo de motores una sola vez al arrancar.',

  // Admin users
  adminUsersTitle: 'Gestión de Usuarios',
  adminUserCreate: 'Nuevo usuario',
  adminUserEmail: 'Email',
  adminUserRole: 'Rol',
  adminUserStatus: 'Estado',
  adminUserLastLogin: 'Último acceso',
  adminUserActions: 'Acciones',
  adminUserActive: 'activo',
  adminUserInactive: 'inactivo',
  adminUserLocked: 'bloqueado',
  adminUserUnlock: 'Desbloquear',
  adminUserResetPwd: 'Reset pwd',
  adminUserChangeRole: 'Cambiar rol',
  adminCreateTitle: 'Crear Usuario',
  adminCreatePassword: 'Contraseña temporal',
  adminCreateSubmit: 'Crear',
  adminCreateSubmitting: 'Creando…',
  adminCreateCancel: 'Cancelar',
  // I-1 (revisión final PR 2, 2026-09-14): AdminUsers.jsx:100 tenía
  // "({n} intentos)" escrito directo en el JSX.
  adminUserFailedAttempts: (n) => `(${n} intentos)`,

  // Admin repository
  adminRepoTitle: 'Repositorio de Artefactos',
  adminRepoMissions: 'Misiones',
  adminRepoPipelines: 'Pipelines',
  adminRepoDocuments: 'Documentos',
  adminRepoImages: 'Imágenes',
  adminRepoEmpty: 'Sin archivos',
  // I-1 (revisión final PR 2, 2026-09-14): encabezados de la tabla del
  // repositorio (AdminRepository.jsx:79), antes fijos en español.
  adminRepoColName: 'Nombre',
  adminRepoColSize: 'Tamaño',
  adminRepoColModified: 'Modificado',
  adminRepoDelete: 'Eliminar',
  adminRepoDownload: 'Descargar',
  adminRepoPreview: 'Preview',
  adminRepoDeleteTitle: (name) => `Eliminar ${name}`,
  adminRepoDeleteMessage: 'El archivo se borra del repositorio y no se puede deshacer.',
  adminRepoSize: (bytes) => bytes < 1024 ? `${bytes}B` : bytes < 1024*1024 ? `${(bytes/1024).toFixed(1)}KB` : `${(bytes/1024/1024).toFixed(1)}MB`,

  // Admin settings
  adminSettingsTitle: 'Configuración del Sistema',
  adminSettingsSave: 'Guardar',
  adminSettingsSaved: 'Guardado',
  adminSettingsSaveError: 'No se pudo guardar la configuración.',
  adminSettingsLoadError: 'No se pudo cargar la configuración.',
  config_clave_reservada: 'Una de las claves está reservada y no se puede cambiar desde esta pantalla. No se guardó nada.',
  config_collation_desconocida: 'La base no permitió verificar las claves reservadas, así que no se guardó nada.',
  adminSettingsLang: 'Idioma por defecto',
  adminSettingsTheme: 'Tema por defecto',
  adminSettingsTimeout: 'Timeout sesión (min)',
  adminSettingsMaxPipelines: 'Max pipelines simultáneos',
  adminSettingsRetention: 'Retención web-tasks (días)',
  adminSettingsSystemName: 'Nombre del sistema',
  adminSettingsWsNotif: 'Notificaciones WS',
  adminSettingsDark: 'Oscuro',
  adminSettingsLight: 'Claro',

  // Admin costs
  adminCostsTitle: 'Monitor de Costos',
  adminCostsFacet: 'Faceta',
  adminCostsModel: 'Modelo',
  adminCostsTokensIn: 'Tokens entrada',
  adminCostsTokensOut: 'Tokens salida',
  adminCostsCost: 'Costo USD',
  adminCostsRequests: 'Requests',
  adminCostsPeriod: 'Período',
  adminCostsDay: 'Hoy',
  adminCostsWeek: 'Semana',
  adminCostsMonth: 'Mes',
  adminCostsTotal: 'Total',
  adminCostsChart: 'Requests por faceta (últimos 7 días)',
  adminCostsNoData: 'Sin datos aún',
  adminCostsNoPricing: 'Sin precio',
  adminCostsPartialMarker: '*',
  adminCostsPartialNote: '* Total parcial — hay modelos sin precio cargado en el catálogo, no están incluidos en la suma.',

  // HAL Eye
  eyeIdle: 'reposo',
  // M5 (revisión de código, 2026-09-14, fix vivo): getEyeState() traía
  // estas etiquetas escritas a mano dentro de la función. Son nombres
  // propios/técnicos de componentes y estados de JAX -- mismo texto en es y
  // en, como ya hacen killSwitchActive/jacobsHint/imagenHint (sólo la prosa
  // alrededor se traduce, el nombre no).
  eyeKillSwitch: 'KILL SWITCH',
  eyeDallE3: 'DALL-E 3',
  eyeLasManosDown: 'LAS MANOS DOWN',
  eyeGate: 'GATE',
  eyeJacobs: 'Jacobs',
  halEyeAriaLabel: (label) => `Ojo HAL — ${label}`,

  // Correo saliente (SMTP) — AdminSmtp.jsx (2026-09-12, admin usuarios etapa 1)
  adminSmtp: 'Correo (SMTP)',
  smtpTitle: 'Correo saliente (SMTP)',
  smtpDesc: 'Servidor con el que Axioma envía los enlaces de recuperación de contraseña.',
  smtpHost: 'Servidor',
  smtpPort: 'Puerto',
  smtpEncryption: 'Cifrado',
  smtpEncTls: 'STARTTLS',
  smtpEncSsl: 'SSL/TLS',
  smtpEncNone: 'Sin cifrado',
  smtpUser: 'Usuario',
  smtpPassword: 'Contraseña',
  smtpPasswordHint: 'Hay una contraseña guardada. Dejá la máscara para conservarla o escribí una nueva.',
  smtpFromName: 'Nombre del remitente',
  smtpFromEmail: 'Correo del remitente',
  smtpSave: 'Guardar',
  smtpSaving: 'Guardando…',
  smtpSaved: 'Configuración SMTP guardada.',
  smtpTestConnection: 'Probar conexión',
  smtpTesting: 'Probando…',
  smtpConnectionOk: 'Conexión y autenticación verificadas.',
  smtpSendTest: 'Enviar correo de prueba',
  smtpSending: 'Enviando…',
  smtpTestSent: (to) => `Correo de prueba enviado a ${to}.`,
  smtpTestToDefault: 'Destinatario de prueba por defecto',
  smtpTestToHint: 'Opcional. Si lo dejás vacío, la prueba se envía a tu correo.',
  smtpTestModalTitle: 'Enviar correo de prueba',
  smtpTestRecipient: 'Destinatario',
  smtpTestSendButton: 'Enviar',
  smtpCancel: 'Cancelar',
  smtpCorruptBanner: (motivo) => `La configuración guardada está dañada: ${motivo}. El envío de correos está deshabilitado. Volvé a escribir la contraseña y guardá.`,
  smtpReloadFailed: 'No se pudo volver a cargar la configuración: recargá la página para verla.',
  smtpEncNoneWarning: 'Sin cifrado, la contraseña y los correos viajan en claro por la red. Usalo solo en una red de confianza.',
  smtpMotivoPasswordIlegible: 'la contraseña guardada no se puede descifrar (¿cambió FERNET_KEY?)',
  smtpMotivoClaveAusente: (clave) => `falta el valor ${clave}`,
  smtpMotivoValorInvalido: (clave) => `el valor ${clave} no es válido`,
  smtpServerSaid: (texto) => `Respuesta del servidor: ${texto}`,
  smtpErrorGeneric: 'No se pudo completar la operación.',
  smtpErrors: {
    smtp_exige_contrasena: 'Escribí la contraseña: no hay una guardada que se pueda usar.',
    smtp_from_email_invalido: 'El correo del remitente no es válido.',
    smtp_sin_contrasena: 'No hay contraseña SMTP guardada: escribila para probar.',
    smtp_password_ilegible: 'La contraseña guardada no se puede descifrar: escribila de nuevo.',
    smtp_sin_clave_de_cifrado: 'El servidor no tiene FERNET_KEY: no se puede guardar la contraseña cifrada.',
    smtp_no_configurado: 'El correo saliente no está configurado.',
    smtp_config_corrupta: 'La configuración SMTP está dañada: el envío está deshabilitado.',
    smtp_envio_fallido: 'El servidor SMTP no aceptó el correo.',
    smtp_demasiadas_pruebas: 'Demasiadas pruebas seguidas. Esperá unos minutos.',
    smtp_conexion_fallida: 'No se pudo conectar con el servidor. Revisá servidor, puerto y cifrado.',
    smtp_saludo_inesperado: 'El servidor respondió un saludo inesperado.',
    smtp_ehlo_fallido: 'El servidor rechazó el saludo EHLO.',
    smtp_starttls_no_disponible: 'El servidor no ofrece STARTTLS en ese puerto.',
    smtp_tls_fallido: 'Falló la negociación TLS (certificado inválido o nombre que no coincide).',
    smtp_auth_rechazada: 'El servidor rechazó el usuario o la contraseña.',
    smtp_auth_no_soportada: 'El servidor no admite autenticación en esa conexión.',
    smtp_reescribir_contrasena_al_cambiar_servidor: 'Cambiaste servidor, puerto, cifrado o usuario: volvé a escribir la contraseña (la guardada no se envía a otro servidor).',
    smtp_password_no_ascii: 'La contraseña solo puede tener caracteres ASCII (sin tildes ni ñ): el protocolo SMTP no admite otros.',
    smtp_usuario_no_ascii: 'El usuario solo puede tener caracteres ASCII (sin tildes ni ñ): el protocolo SMTP no admite otros.',
    smtp_campo_invalido: 'Servidor, usuario, nombre o correo del remitente tienen caracteres no permitidos (saltos de línea o de control).',
    smtp_destinatario_invalido: 'El destinatario no es un correo válido.',
  },

  // Administración de usuarios — etapa 3 (guardas, sesiones, historial, 2026-09-15)
  adminUserEdit: 'Editar',
  adminUserEditTitle: (email) => `Editar ${email}`,
  adminUserSave: 'Guardar',
  adminUserSaved: 'Usuario actualizado.',
  adminUserRevokeSessions: 'Cerrar sesiones',
  adminSessionsRevoked: (email) => `Se cerraron todas las sesiones de ${email}.`,
  adminUserHistory: 'Historial',
  adminHistoryTitle: (email) => `Historial de ${email}`,
  adminHistoryEmpty: 'Sin acciones registradas.',
  adminHistoryClose: 'Cerrar',
  adminHistoryBy: (actor) => `por ${actor}`,
  adminErrorGeneric: 'No se pudo completar la acción.',
  adminErrors: {
    ultimo_superadmin: 'No se puede: tiene que quedar al menos un superadmin activo.',
    auto_accion_prohibida: 'No podés cambiar tu propio rol ni tu estado, ni darte de baja. Tu contraseña se cambia en "Mi cuenta".',
    usuario_no_encontrado: 'El usuario no existe.',
    rol_invalido: 'Rol inválido.',
    estado_invalido: 'Estado inválido.',
    sesion_invalida: 'Tu sesión ya no es válida. Volvé a entrar.',
    usuario_no_activo: 'El usuario no está activo: no se le puede enviar un enlace.',
    password_corta: 'La contraseña debe tener al menos 8 caracteres.',
    password_larga: 'La contraseña es demasiado larga (máximo 72 bytes; los acentos ocupan 2).',
    smtp_password_no_ascii: 'La contraseña SMTP guardada tiene caracteres no ASCII (tildes o ñ) y el servidor no la acepta. Volvé a escribirla en Administración → Correo (SMTP).',
    email_invalido: 'El correo no es válido.',
    email_ya_existe: 'Ya existe un usuario con ese correo.',
    password_igual_a_la_actual: 'La nueva contraseña tiene que ser distinta de la actual.',
    cambio_de_password_requerido: 'Tenés que cambiar tu contraseña antes de seguir.',
  },
  adminAuditActions: {
    create: 'Alta',
    update_email: 'Cambio de correo',
    update_role: 'Cambio de rol',
    update_status: 'Cambio de estado',
    reset_link_sent: 'Enlace de recuperación enviado',
    unlock: 'Desbloqueo',
    sessions_revoked: 'Sesiones cerradas',
    baja: 'Baja',
    password_changed_self: 'Cambió su contraseña',
    password_reset_completed: 'Restableció su contraseña',
    password_set_by_admin: 'Contraseña fijada por un admin',
  },
  adminAuditUnknown: 'Acción desconocida',

  // Mi cuenta y enlace de recuperación — etapa 4 (2026-09-12)
  myAccount: 'Mi cuenta',
  myAccountChangePassword: 'Cambiar mi contraseña',
  myAccountCurrent: 'Contraseña actual',
  myAccountSubmit: 'Cambiar contraseña',
  myAccountSubmitting: 'Guardando…',
  myAccountDone: 'Contraseña cambiada. Tus otras sesiones se cerraron.',
  myAccountWrongCurrent: 'La contraseña actual no es correcta.',
  myAccountTooMany: 'Demasiados intentos. Esperá y volvé a intentarlo.',
  myAccountError: 'No se pudo cambiar la contraseña.',
  adminUserSendResetLink: 'Enviar enlace',
  adminResetLinkSent: (email) => `Enlace de recuperación enviado a ${email}.`,

  // Editar y dar de baja — etapa 5 (2026-09-15)
  adminUserBaja: 'Dar de baja',
  adminBajaTitle: (email) => `Dar de baja a ${email}`,
  adminBajaMessage: 'La cuenta queda inutilizable, sale de la lista y libera el correo. Su historial se conserva.',
  adminBajaDone: (email) => `${email} fue dado de baja.`,
  confirmSumLabel: (a, b) => `Resolvé ${a} + ${b} = ?`,
  confirmSumHint: 'Escribí el resultado para habilitar el botón.',

  // Fijar contraseña por admin y cambio obligatorio (2026-09-15, U34)
  adminUserSetPassword: 'Fijar contraseña',
  adminSetPasswordTitle: (email) => `Fijar la contraseña de ${email}`,
  adminSetPasswordHint: 'Sus sesiones abiertas se cierran y tendrá que cambiarla en su próximo inicio de sesión.',
  adminSetPasswordSubmitting: 'Guardando…',
  adminPasswordSetDone: (email) => `Contraseña fijada para ${email}. Tendrá que cambiarla al entrar.`,
  forcedChangeTitle: 'Cambiá tu contraseña',
  forcedChangeIntro: 'Un administrador fijó tu contraseña. Para seguir, elegí una nueva.',
  forcedChangeDone: 'Contraseña cambiada. Ya podés seguir.',
  forcedChangeLogout: 'Cerrar sesión',
  myAccountSameAsCurrent: 'La nueva contraseña tiene que ser distinta de la que te dieron.',

  // Restaurar tareas pendientes (useJaxStore.js)
  taskRestoring: (id) => `_Tarea \`${id}\` — verificando estado…_`,
}
