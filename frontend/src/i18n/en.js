export default {
  // Top bar
  logout: 'Sign out',

  // Left panel
  facets: 'Facets',
  // Component's proper name (like killButton/eyeLasManosDown): same value in both languages.
  lasManos: 'LAS MANOS',
  alive: 'online',
  down: 'offline',
  // M-2 (final review PR 3, 2026-09-14): wsStatus (useJaxStore.js,
  // api/websocket.js) was shown raw, untranslated. An unknown value falls
  // back to the raw value (LeftPanel.jsx).
  wsStatusLabels: {
    connected: 'Connected',
    disconnected: 'Disconnected',
    reconnecting: 'Reconnecting',
  },

  // FacetCard status
  statusIdle: 'Idle',
  statusThinking: 'Thinking…',
  statusError: 'Error',
  statusOffline: 'Offline',

  // Right panel
  tabDirectorJacobs: 'Director Jacobs',
  tabAudit: 'Audit',
  stepsLabel: 'steps',
  noPipelinesActive: 'No active pipelines',
  // M-2 (final review PR 3, 2026-09-14): activePipeline.status
  // (jax_engine/schemas.py::PipelineStatus) was shown raw. A value the
  // backend adds that the dictionary doesn't know falls back to the raw
  // value (RightPanel.jsx).
  pipelineStatusLabels: {
    pending: 'Pending',
    running: 'Running',
    waiting_gate: 'Waiting for approval',
    completed: 'Completed',
    failed: 'Failed',
  },
  pipelinesAdditional: (n) => `+${n} additional pipeline(s)`,
  approve: '✓ Approve',
  cancelling: 'Cancelling…',
  cancelPipeline: 'Cancel pipeline',
  approveError: 'The step could not be approved. Try again.',
  cancelError: 'The pipeline could not be cancelled. Try again.',
  // Nombre guardado del pipeline: el objetivo, recortado a 50 caracteres.
  pipelineName: (objetivo) => `Pipeline: ${objetivo.slice(0, 50)}`,

  // Audit log
  auditLog: 'Audit Log',
  loading: 'Loading…',
  noEventsYet: 'No events yet',
  auditoria_ilegible: 'The audit log could not be read',
  auditLogError: 'Could not load the audit log',
  auditoriaSoloSuperadmin: 'Only a superadmin can view the LAS MANOS audit log.',

  // Step card
  clickToSeeResult: 'Click to see result in chat',

  // Bottom bar — modes
  modeChat: 'Chat',
  modeComando: 'Command',
  modePipeline: 'Pipeline',
  modeImagen: 'Image',
  placeholderChat: (label) => `Message to ${label}… Enter to send, Shift+Enter for new line`,
  placeholderComando: () => 'Describe the task for Hyde… (autonomous)',
  placeholderPipeline: () => 'Describe the pipeline objective…',
  placeholderImagen: () => 'Describe the image you want to generate…',
  hydeHint: 'Hyde will execute the task autonomously in background',
  jacobsHint: 'Jacobs will orchestrate multiple facets in a pipeline',
  imagenHint: 'DALL-E 3 will generate the image from your description',
  errorImagen: 'Could not generate the image.',
  generatingImage: 'Generating…',
  configure: 'Configure',
  send: 'Send',
  errorFacet: 'Could not connect to the facet.',
  // Front A (2026-09-16): Mesa errors and notices with stable codes.
  errorPrefix: 'Error',
  respuestaDelServicio: (texto) => `Service response: ${texto}`,
  erroresMesa: {
    faceta_desconocida: (d) => `The facet "${d.facet}" does not exist.`,
    proveedor_error_http: (d) => `The provider for ${d.facet} returned error ${d.status}.`,
    faceta_error: (d) => `${d.facet} could not respond.`,
    credencial_no_disponible: (d) => `There is no valid credential configured for ${d.provider}.`,
    imagen_error_http: (d) => `The image service returned error ${d.status}.`,
    imagen_error: () => 'The image could not be generated.',
    task_id_invalido: () => 'The task id is not valid.',
    tarea_no_encontrada: () => 'The task does not exist.',
    limite_de_pipelines: (d) => `${d.max} pipelines are already running: wait for one to finish.`,
    pipeline_id_invalido: () => 'The pipeline id is not valid.',
    pipeline_no_encontrado: () => 'The pipeline does not exist.',
    jacobs_rechazo: (d) => `Jacobs rejected the pipeline (${d.status}).`,
    jacobs_no_responde: () => 'Jacobs did not respond.',
    archivo_demasiado_grande: (d) => `The file exceeds the ${Math.round(d.max_bytes / 1048576)} MB maximum.`,
    pdf_ilegible: () => 'The PDF could not be read.',
  },
  avisosChat: {
    faceta_sin_binding: (p) => `⚠️ ${p.facet} is not available: no active binding configured.`,
    faceta_no_autorizada: (p) => `⚠️ ${p.facet} is not available: access not authorized.`,
    transporte_no_soportado: (p) => `⚠️ ${p.facet} is not available: transport '${p.transport}' is not supported in the web Mesa.`,
    identidad_del_modelo: (p, hosting) => `I run on '${p.model}' ${hosting} — read live from the active model selector, not from memory.`,
    hyde_usa_modo_comando: () => 'Hyde works as an autonomous task runner — use Command mode for technical tasks.',
  },
  hostingDeProveedor: {
    ollama: 'via local Ollama on hall9000',
    deepseek: 'via the DeepSeek API',
    gemini: 'via the Gemini API (Google)',
    openai: 'via the OpenAI API',
    moonshot: 'via the Moonshot API',
    zhipu: 'via the Zhipu API (GLM)',
  },
  hostingGenerico: 'via the API configured for this facet',
  avisoDesconocido: 'The facet replied with a notice this version does not know.',
  commandFailed: (motivo) => `Error running the task: ${motivo}`,
  commandDryRun: (mision) => `[Dry run] Task registered:\n\n${mision}`,
  taskInitializing: '_Starting autonomous task…_',
  taskStarted: (id) => `_Task started — \`${id}\`_\n\nHyde is running in background…`,
  errorTask: 'Could not start the task.',
  commandNoResult: '(no result)',
  pipelineStarted: (id, mode, steps) =>
    `Pipeline started — \`${id}\`\nMode: **${mode}** · ${steps} steps\n\nTracking progress in right panel…`,
  errorPipeline: 'Could not create the pipeline.',
  pipelineStepHeader: (facet, capability) => `● **${facet}** — ${capability}`,
  pipelineCompleted: (done, total, secs) =>
    `**Pipeline completed** — ${done} of ${total} steps${secs ? `, ${Math.round(secs)}s total` : ''}`,
  pipelineSources: 'Sources',
  pipelineNoResult: '_(no result)_',

  // Kill switch
  killSwitchActive: 'KILL SWITCH ACTIVE',
  killConfirm: 'Confirm?',
  killConfirmYes: 'YES, STOP ALL',
  // Product term (like eyeKillSwitch): same value in both languages.
  killButton: 'KILL',
  // Technical abbreviation (WebSocket), same value in both languages.
  wsLabel: 'WS',
  cancel: 'Cancel',
  killTitle: 'Kill Switch — stops all processes',
  killSwitchToast: 'KILL SWITCH ACTIVATED',
  killSwitchStoppedToast: 'KILL SWITCH ACTIVATED — all processes stopped',

  // Store event toasts (WS handleEvent)
  humanGateRequestedToast: (id) => `Jacobs is waiting for approval — pipeline ${id}`,
  pipelineResultsError: (id) => `Could not load the results for pipeline ${id}`,

  // Pipeline modal
  newPipelineTitle: 'New Pipeline · Jacobs',
  objectiveLabel: 'Objective',
  modeLabel: 'Mode',
  // I-1 (final review PR 3, 2026-09-14): pipeline mode labels were
  // hardcoded in English inside PipelineModal.jsx.
  pipelineModeSupervised: '👁 Supervised',
  pipelineModeAutonomous: '⚡ Autonomous',
  pipelineModeDryRun: '🧪 Dry run',
  facetsLabel: 'Facets',
  starting: 'Starting…',
  planAndExecute: 'Plan and execute',
  descJaxLocal: 'Local reasoning (Qwen3)',
  descHipatia: 'Web research',
  descJekyll: 'Reflective analysis',
  descThot: 'Critical audit',
  descKimi: 'Technical implementation',
  descAda: 'Analysis & rigor',
  autoMotor: 'Auto (by competence)',
  facetUngoverned: 'No Motor Registry governance — not validated against real capabilities.',
  catalogLoadingHint: 'Loading motor catalog…',
  catalogFailedHint: 'Could not load the motor catalog — planning is blocked until it loads.',
  errorPipelinePrefix: 'Pipeline error',
  layoutLabel: 'Shape',
  layoutChain: 'Chained',
  layoutParallel: 'In parallel',
  chainLabel: 'Chain',
  chainHint: 'Each step receives the output of the earlier steps it needs. If one fails, the chain stops.',
  chainRoles: {
    research: 'Research',
    plan: 'Sketch and plan',
    critique: 'Critique the plan',
    unify: 'Merge plan and critique',
    produce: 'Produce',
    audit: 'Audit',
  },
  chainCleanroomWarning: (role, facet, depRole) =>
    `${role}: ${facet} cannot audit what it produced in “${depRole}”. Pick another facet.`,
  chainInvalidFacet: (role) => `${role}: the selected facet is not allowed for this step by the catalog.`,
  // Instructions each model receives. They follow the interface language.
  chainInstructions: {
    research:
      'Role: researcher. Research the objective thoroughly with verifiable sources and cite each one. ' +
      'Separate verified facts from assumptions and state explicitly what you could not verify.',
    plan:
      'Role: architect. Using the research you received, sketch and plan: structure, modules, build order ' +
      'and why to start there. Every fact you use must come from the research; declare anything missing as ' +
      'an unknown instead of assuming it.',
    critique:
      'Role: critic. Critique the plan against the research: gaps, risks, unsupported assumptions and wrong ' +
      'ordering. Number each finding, say which part of the plan it refers to and with what evidence. ' +
      'Do not rewrite the plan.',
    unify:
      'Role: merger. Produce the final plan incorporating the critique. For each numbered finding of the ' +
      'critique, say whether you accept it and what changes, or reject it and why.',
    produce:
      'Role: producer. Using the merged plan, produce the complete deliverable. Follow it; if you deviate, ' +
      'say where and why.',
    audit:
      'Role: independent auditor. Your only source of truth is the research and the objective: do not accept ' +
      'the plan, the critique or the product as sources. ' +
      '1) Mark as NOT VERIFIED every claim in the product that the research does not support, and every ' +
      'quote that does not appear in it. ' +
      '2) Point out contradictions between the product and the merged plan. ' +
      '3) Measurement against the original critique (not against what the plan says about it): for each ' +
      'numbered critique finding, say whether it reached the product, whether the merged plan rejected it ' +
      'with a reason, or whether it was lost without explanation. Close with the count of each case.',
  },

  // Center panel
  platformLabel: 'AXIOMA V0.2',
  inMemoryOf: 'In memory of Jairo Urbina.',
  inHonorOf: 'In honor of Prof. Raúl Jacobs.',

  // Login
  brandName: 'Axioma',
  brandTagline: 'Personal Cognitive Infrastructure',
  loginTitle: 'Axioma',
  loginTagline: 'In memory of Jairo Urbina',
  emailLabel: 'Email',
  passwordLabel: 'Password',
  loginError: 'Invalid username or password',
  loggingIn: 'Signing in…',
  loginButton: 'Enter Axioma',
  showPassword: 'Show password',
  hidePassword: 'Hide password',
  // Session-close reason (2026-09-14, Task 4b): the interceptor in
  // api/client.js used to silently clear the session on a failed refresh.
  sesion_invalida: 'Your session was closed: someone signed in somewhere else, or your access changed. Please sign in again.',
  sesion_expirada: 'Your session expired. Please sign in again.',
  accountLocked: 'Account locked. Check your email.',
  accountLockedMinutes: (min) => `Account locked. Try again in ${min} minute(s).`,
  tooManyAttempts: 'Too many attempts. Wait a moment and try again.',
  tooManyAttemptsSeconds: (s) => `Too many attempts. Try again in ${s} second(s).`,
  forgotPassword: 'Forgot your password?',
  forgotPasswordTitle: 'Password recovery',
  forgotPasswordDesc: 'Enter your email and we will send you instructions.',
  forgotPasswordSent: 'If the email exists, you will receive instructions shortly.',
  forgotPasswordSend: 'Send instructions',
  forgotPasswordSending: 'Sending…',
  backToLogin: 'Back to login',
  emailPlaceholder: 'name@company.com',
  resetPasswordTitle: 'New password',
  resetPasswordDesc: 'Enter your new password.',
  resetPasswordLabel: 'New password',
  resetPasswordConfirm: 'Confirm password',
  resetPasswordMismatch: 'Passwords do not match',
  resetPasswordShort: 'Minimum 8 characters',
  resetPasswordSuccess: 'Password updated. You can now sign in.',
  resetPasswordSubmit: 'Change password',
  resetPasswordSubmitting: 'Saving…',
  resetPasswordInvalid: 'The link is invalid or has already been used.',
  resetPasswordUsed: 'This link has already been used. Request a new one.',
  resetPasswordExpired: 'The link has expired. Request a new one.',
  resetPasswordLong: 'The password is too long (maximum 72 bytes; accented letters take 2).',

  // Message
  userLabel: 'User',
  contractDegradedNote: 'The response did not meet the expected format.',
  // I-1 (final review PR 3, 2026-09-14): <img> alt text was hardcoded in
  // Spanish, not going through i18n (it showed up in Spanish in English UI).
  altGeneratedImage: 'generated image',
  altAttachment: 'attachment',

  // Theme / language
  lightMode: 'Light mode',
  darkMode: 'Dark mode',
  switchLanguage: (idioma) => `Switch language to ${idioma}`,
  adminPanel: 'Administration',

  // File attachments
  attachTooltip: 'Attach image, PDF or text',
  attachRemove: 'Remove attachment',
  attachUploading: 'Uploading…',
  attachError: 'Error uploading file',
  attachReady: '✓ ready',

  // Admin module
  adminTitle: 'Administration',
  adminDashboard: 'Dashboard',
  adminFacetsModels: 'Facets & Models',
  adminUsers: 'Users',
  adminRepo: 'Repository',
  adminSettings: 'Settings',
  adminCosts: 'Costs',
  adminBack: 'Back to Axioma',

  // Admin dashboard
  adminServicesTitle: 'Service Status',
  adminStatsTitle: "Today's Statistics",
  serviceAlive: 'online',
  serviceDown: 'offline',
  serviceConnected: 'connected',
  serviceError: 'error',
  serviceNotConfigured: 'Not configured',
  statMessages: 'Messages',
  statPipelines: 'Pipelines',
  statImages: 'Images',
  statUsersActive: 'Active users',
  statUsersLocked: 'Locked',
  statRam: 'RAM',
  // I-1 (final review PR 2, 2026-09-14): AdminDashboard.jsx had a fixed
  // label="API Keys". "API Keys" is the same term in both languages
  // (technical name), like brandName or eyeKillSwitch.
  statApiKeysLabel: 'API Keys',

  // Admin API keys
  adminKeyProvider: 'Provider',
  adminKeyValue: 'Key',
  adminKeyStatus: 'Status',
  adminKeyTest: 'Test',
  adminKeyRotate: 'Rotate key',
  adminKeyRevoke: 'Revoke',
  adminKeyRevoking: 'Revoking…',
  adminKeyRevokeConfirmTitle: 'Revoke all active credentials',
  adminKeyRevokeConfirmBody: 'This cuts access immediately, no grace window. Confirm?',
  adminKeyActiveCount: (n) => `${n} active`,
  adminKeyLastVerified: 'Last verified',
  adminKeyNoActive: 'No active credential',
  adminKeyTesting: 'Testing…',
  adminKeyOk: 'OK',
  adminKeyFail: 'Error',
  adminKeyMissing: 'No key',
  adminKeyNewValue: 'New API key',
  adminKeySave: 'Save',
  adminKeyEnter: 'Enter the new key for',
  adminKeyLatency: (ms) => `${ms}ms`,
  adminKeyTestError: 'The credential could not be tested.',
  adminKeyRotateError: 'The credential could not be rotated.',
  adminKeyRevokeError: 'The credential could not be revoked.',

  // Admin — Bloque D tabs (model catalog and facets/bindings)
  adminTabProviders: 'Providers & Credentials',
  adminTabModels: 'Model Catalog',
  adminTabBindings: 'Facets & Bindings',

  adminModelsTitle: 'Model Catalog',
  adminModelsSync: 'Sync',
  adminModelsSyncing: 'Syncing…',
  adminModelsSyncError: 'Sync failed',
  sync_con_errores: (lista) => `Sync incomplete, failed: ${lista}`,
  adminModelsProvider: 'Provider',
  adminModelsModelId: 'Model',
  adminModelsAlias: 'Alias',
  adminModelsStatus: 'Status',
  adminModelsSource: 'Source',
  adminModelsContext: 'Context',
  adminModelsPrice: 'Price (in/out per 1M)',
  adminModelsSourceCheckedAt: 'Checked',
  adminModelsNoData: '—',
  adminModelsStatusAvailable: 'Available',
  adminModelsStatusDegraded: 'Degraded',
  adminModelsStatusDeprecated: 'Deprecated',
  adminModelsStatusGone: 'Gone',
  adminModelsSourceProviderApi: 'Provider API',
  adminModelsSourceModelsDev: 'models.dev',
  adminModelsSourceManual: 'Manual',
  adminModelsSourceObserved: 'Observed live',

  adminProposalsTitle: 'Proposed changes',
  adminProposalsEmpty: 'No pending proposals.',
  adminProposalsFacet: 'Facet',
  adminProposalsProposed: 'Proposed model',
  adminProposalsReason: 'Reason',
  adminProposalsDetail: 'Detail',
  adminProposalsApprove: 'Approve',
  adminProposalsReject: 'Reject',
  adminProposalsApproving: 'Approving…',
  adminProposalsRejecting: 'Rejecting…',
  adminProposalReasonNewModel: 'New model available',
  adminProposalReasonDrift: 'Drift detected',
  adminProposalReasonDeprecation: 'Deprecation warning',

  adminBindingsTitle: 'Facets & Bindings',
  adminBindingsFacet: 'Facet',
  adminBindingsTransport: 'Transport',
  adminBindingsModel: 'Active model',
  adminBindingsCapability: 'Contract',
  adminBindingsCapabilityOk: '✓ Meets',
  adminBindingsCapabilityWarning: '⚠ Does not meet',
  adminBindingsCapabilityUnknown: '? No data',
  adminBindingsNoBinding: 'No binding',
  adminBindingsEdit: 'Edit',
  adminBindingsSave: 'Save',
  adminBindingsCancel: 'Cancel',
  adminBindingsSaving: 'Saving…',
  adminBindingsSaveError: (detail) => `Couldn't save: ${detail}`,
  adminBindingsSelectModel: 'Pick a model from the catalog',
  // 409 when approving a proposal or saving a binding (2026-09-14, PR-J): the
  // target model does not declare what this facet's dispatch needs.
  modelo_sin_contrato_de_dispatch: (modelo, campos) =>
    `Model ${modelo} does not declare ${campos} in the catalog, so this facet could not use it. That catalog row must be completed first. Nothing was changed.`,
  adminProposalsDecideError: 'The decision on the proposal could not be completed.',
  // Round 1 409 (2026-09-14): the binding would keep a provider that is not
  // the model's, and the facet would send the model to the wrong service.
  modelo_de_otro_proveedor: (modelo, proveedorModelo, proveedorBinding) =>
    `Model ${modelo} belongs to ${proveedorModelo}, but this facet would be configured with ${proveedorBinding}, so it could not use it. Pick a ${proveedorBinding} model. Nothing was changed.`,
  // PR-L (2026-09-14): declare a catalog row's dispatch contract from the
  // admin (it used to be a hand-written UPDATE).
  adminContratoTitulo: (modelo) => `Dispatch contract for ${modelo}`,
  adminContratoParam: 'Output limit parameter name',
  adminContratoTope: 'Max output tokens',
  adminContratoAyuda: "What this model's API requires at dispatch: the name of its output limit parameter and the maximum number of output tokens it accepts. The limit comes from the provider's documentation or its own HTTP 400 error; it is not the context window. Who declared it and the previous value are recorded.",
  adminContratoElegir: 'Pick one',
  adminContratoGuardar: 'Save contract',
  adminContratoGuardando: 'Saving…',
  adminContratoCancelar: 'Cancel',
  adminContratoGuardado: 'Contract declared. The proposal can be approved again.',
  adminContratoError: 'The contract could not be declared.',
  contrato_dispatch_invalido: (campos) =>
    `The value of ${campos} is not valid for this model's dispatch. Nothing was changed.`,
  adminContratoDeclarar: 'Declare contract',
  adminModelsContrato: 'Dispatch contract',
  adminModelsContratoSinDeclarar: 'Not declared',
  adminProposalsUltimoRechazo: (fecha) => `Last approval attempt rejected (${fecha}):`,
  // PR-L round 1: a facet's last recorded rejection, on the Bindings screen.
  adminBindingsUltimoRechazo: (fecha) => `Last change rejected (${fecha}):`,
  // Round 2: Bindings has no proposal; the next step is saving the binding again.
  adminBindingsContratoGuardado: "Contract declared. The facet's binding can be saved again.",

  // Admin — Motors tab (R4 Task 9): register a motor/capability without hand-written SQL
  adminTabMotors: 'Motors',
  adminMotorsTitle: 'Motors',
  adminMotorsCreate: '+ New motor',
  adminMotorsCreateTitle: 'Register a motor',
  adminMotorsKey: 'Key',
  adminMotorsKeyPlaceholder: 'e.g. gemini_flash',
  adminMotorsSelectProvider: 'Pick a provider',
  adminMotorsSelectModel: 'Pick a model',
  adminMotorsMaxTokens: 'Max tokens (0 = no limit)',
  adminMotorsTimeout: 'Timeout (seconds)',
  adminMotorsSupportsReasoning: 'Supports reasoning (reasoning_content)',
  adminMotorsSandboxOnly: 'Sandbox only',
  adminMotorsCapabilities: 'Capabilities it can serve',
  adminMotorsPriority: 'Priority (lower = first choice)',
  adminMotorsSave: 'Create motor',
  adminMotorsSaveError: (detail) => `Couldn't create the motor: ${detail}`,
  adminMotorsDispatchable: 'Dispatch',
  adminMotorsDispatchableYes: '✓ Implemented',
  adminMotorsDispatchableNo: '⚠ No dispatcher yet',
  adminMotorsNoCapabilities: 'No capability assigned',
  adminMotorsEmpty: 'No motors registered yet.',
  adminMotorsLimitationNote: 'Known limitation: today only the "http_openai_compat" and "ollama" transports have a dispatcher implemented in las_manos/motor_registry/worker.py. A motor with a different transport (http_gemini, subprocess, motor_registry) gets registered fine but a real job will fail until its dispatcher is added — named debt, not blocking. Also: a newly registered motor is written to the DB immediately, but is not dispatchable via jax-las-manos.service until that service is restarted — it loads its motor catalog once at startup.',

  // Admin users
  adminUsersTitle: 'User Management',
  adminUserCreate: 'New user',
  adminUserEmail: 'Email',
  adminUserRole: 'Role',
  adminUserStatus: 'Status',
  adminUserLastLogin: 'Last login',
  adminUserActions: 'Actions',
  adminUserActive: 'active',
  adminUserInactive: 'inactive',
  adminUserLocked: 'locked',
  adminUserUnlock: 'Unlock',
  adminCreateTitle: 'Create User',
  adminCreatePassword: 'Temporary password',
  adminCreateSubmit: 'Create',
  adminCreateSubmitting: 'Creating…',
  adminCreateCancel: 'Cancel',
  // I-1 (final review PR 2, 2026-09-14): AdminUsers.jsx:100 had
  // "({n} intentos)" written directly in Spanish in the JSX.
  adminUserFailedAttempts: (n) => `(${n} attempts)`,

  // Admin repository
  adminRepoTitle: 'Artifact Repository',
  adminRepoMissions: 'Missions',
  adminRepoPipelines: 'Pipelines',
  adminRepoDocuments: 'Documents',
  adminRepoImages: 'Images',
  adminRepoEmpty: 'No files',
  // I-1 (final review PR 2, 2026-09-14): repository table headers
  // (AdminRepository.jsx:79), previously fixed in Spanish.
  adminRepoColName: 'Name',
  adminRepoColSize: 'Size',
  adminRepoColModified: 'Modified',
  adminRepoDelete: 'Delete',
  adminRepoDownload: 'Download',
  adminRepoPreview: 'Preview',
  adminRepoDeleteTitle: (name) => `Delete ${name}`,
  adminRepoDeleteMessage: 'The file is removed from the repository and cannot be undone.',
  adminRepoSize: (bytes) => bytes < 1024 ? `${bytes}B` : bytes < 1024*1024 ? `${(bytes/1024).toFixed(1)}KB` : `${(bytes/1024/1024).toFixed(1)}MB`,

  // Admin settings
  adminSettingsTitle: 'System Configuration',
  adminSettingsSave: 'Save',
  adminSettingsSaved: 'Saved',
  adminSettingsSaveError: 'The configuration could not be saved.',
  adminSettingsLoadError: 'The configuration could not be loaded.',
  config_clave_reservada: 'One of the keys is reserved and cannot be changed from this screen. Nothing was saved.',
  config_collation_desconocida: 'The database could not verify the reserved keys, so nothing was saved.',
  adminSettingsLang: 'Default language',
  adminSettingsTheme: 'Default theme',
  adminSettingsTimeout: 'Session timeout (min)',
  adminSettingsMaxPipelines: 'Max simultaneous pipelines',
  adminSettingsRetention: 'Web-task retention (days)',
  adminSettingsSystemName: 'System name',
  adminSettingsDark: 'Dark',
  adminSettingsLight: 'Light',

  // Admin costs
  adminCostsTitle: 'Cost Monitor',
  adminCostsFacet: 'Facet',
  adminCostsModel: 'Model',
  adminCostsTokensIn: 'Input tokens',
  adminCostsTokensOut: 'Output tokens',
  adminCostsCost: 'Cost USD',
  adminCostsRequests: 'Requests',
  adminCostsDay: 'Today',
  adminCostsWeek: 'Week',
  adminCostsMonth: 'Month',
  adminCostsTotal: 'Total',
  adminCostsChart: 'Requests by facet (last 7 days)',
  adminCostsNoData: 'No data yet',
  adminCostsNoPricing: 'No pricing',
  adminCostsPartialMarker: '*',
  adminCostsPartialNote: '* Partial total — some models have no price loaded in the catalog and are not included in the sum.',
  // Task 4a (2026-09-15, durable usage queue): really LOST -- the row never
  // reached the DB and could not be parked in the on-disk backup either.
  adminCostsRegistrosPerdidos: (n, num) => `incomplete total: ${num} record${n === 1 ? '' : 's'} lost`,
  // Also lost, but from a different cause with a different fix for the admin.
  adminCostsPerdidasPorDesborde: (n, num) => `incomplete total: the backup filled up and ${num} old record${n === 1 ? ' was' : 's were'} dropped`,
  // Task 10 (2026-09-16, the poison row): a third cause of loss. The database
  // rejects the row's DATA and after three attempts it lands in quarantine.
  adminCostsRechazadas: (n, num) => `incomplete total: the database rejected ${num} record${n === 1 ? '' : 's'}, now quarantined`,
  // PENDING, not lost: the total is incomplete but completes on its own.
  adminCostsEnCola: (n, num) => `${num} record${n === 1 ? ' is' : 's are'} waiting to be retried; the total will complete on its own.`,
  adminCostsUltimoReintento: (cuando) => `Last retry: ${cuando}`,

  // HAL Eye
  eyeIdle: 'idle',
  // M5 (code review, 2026-09-14, fix vivo): getEyeState() used to hardcode
  // these labels in the function body. Proper/technical names of JAX
  // components and states -- same wording in es and en, like the existing
  // killSwitchActive/jacobsHint/imagenHint keys already do (only the prose
  // around them translates, not the name itself).
  eyeKillSwitch: 'KILL SWITCH',
  eyeDallE3: 'DALL-E 3',
  eyeLasManosDown: 'LAS MANOS DOWN',
  eyeGate: 'GATE',
  eyeJacobs: 'Jacobs',
  halEyeAriaLabel: (label) => `HAL Eye — ${label}`,

  // Outgoing email (SMTP) — AdminSmtp.jsx (2026-09-12, user admin stage 1)
  adminSmtp: 'Email (SMTP)',
  smtpTitle: 'Outgoing email (SMTP)',
  smtpDesc: 'Server Axioma uses to send password recovery links.',
  smtpHost: 'Server',
  smtpPort: 'Port',
  smtpEncryption: 'Encryption',
  smtpEncTls: 'STARTTLS',
  smtpEncSsl: 'SSL/TLS',
  smtpEncNone: 'No encryption',
  smtpUser: 'Username',
  smtpPassword: 'Password',
  smtpPasswordHint: 'A password is stored. Leave the mask to keep it or type a new one.',
  smtpFromName: 'Sender name',
  smtpFromEmail: 'Sender email',
  smtpSave: 'Save',
  smtpSaving: 'Saving…',
  smtpSaved: 'SMTP settings saved.',
  smtpTestConnection: 'Test connection',
  smtpTesting: 'Testing…',
  smtpConnectionOk: 'Connection and authentication verified.',
  smtpSendTest: 'Send test email',
  smtpSending: 'Sending…',
  smtpTestSent: (to) => `Test email sent to ${to}.`,
  smtpTestToDefault: 'Default test recipient',
  smtpTestToHint: 'Optional. If left empty, the test is sent to your email.',
  smtpTestModalTitle: 'Send test email',
  smtpTestRecipient: 'Recipient',
  smtpTestSendButton: 'Send',
  smtpCancel: 'Cancel',
  smtpCorruptBanner: (motivo) => `The stored settings are damaged: ${motivo}. Sending email is disabled. Type the password again and save.`,
  smtpReloadFailed: 'The settings could not be reloaded: refresh the page to see them.',
  smtpEncNoneWarning: 'Without encryption, the password and the emails travel in clear text over the network. Use it only on a trusted network.',
  smtpMotivoPasswordIlegible: 'the stored password cannot be decrypted (did FERNET_KEY change?)',
  smtpMotivoClaveAusente: (clave) => `the value ${clave} is missing`,
  smtpMotivoValorInvalido: (clave) => `the value ${clave} is not valid`,
  smtpServerSaid: (texto) => `Server response: ${texto}`,
  smtpErrorGeneric: 'The operation could not be completed.',
  smtpErrors: {
    smtp_exige_contrasena: 'Type the password: there is no stored one that can be used.',
    smtp_from_email_invalido: 'The sender email is not valid.',
    smtp_sin_contrasena: 'No SMTP password is stored: type it to test.',
    smtp_password_ilegible: 'The stored password cannot be decrypted: type it again.',
    smtp_sin_clave_de_cifrado: 'The server has no FERNET_KEY: the password cannot be stored encrypted.',
    smtp_no_configurado: 'Outgoing email is not configured.',
    smtp_config_corrupta: 'The SMTP settings are damaged: sending is disabled.',
    smtp_envio_fallido: 'The SMTP server did not accept the email.',
    smtp_demasiadas_pruebas: 'Too many tests in a row. Wait a few minutes.',
    smtp_conexion_fallida: 'Could not connect to the server. Check server, port and encryption.',
    smtp_saludo_inesperado: 'The server answered with an unexpected greeting.',
    smtp_ehlo_fallido: 'The server rejected the EHLO greeting.',
    smtp_starttls_no_disponible: 'The server does not offer STARTTLS on that port.',
    smtp_tls_fallido: 'TLS negotiation failed (invalid certificate or name mismatch).',
    smtp_auth_rechazada: 'The server rejected the username or password.',
    smtp_auth_no_soportada: 'The server does not allow authentication on that connection.',
    smtp_reescribir_contrasena_al_cambiar_servidor: 'You changed server, port, encryption or username: type the password again (the stored one is not sent to another server).',
    smtp_password_no_ascii: 'The password can only contain ASCII characters (no accents): SMTP does not allow others.',
    smtp_usuario_no_ascii: 'The username can only contain ASCII characters (no accents): SMTP does not allow others.',
    smtp_campo_invalido: 'Server, username, sender name or sender email contain characters that are not allowed (line breaks or control characters).',
    smtp_destinatario_invalido: 'The recipient is not a valid email address.',
  },

  // User administration — stage 3 (guards, sessions, history, 2026-09-15)
  adminUserEdit: 'Edit',
  adminUserEditTitle: (email) => `Edit ${email}`,
  adminUserSave: 'Save',
  adminUserSaved: 'User updated.',
  adminUserRevokeSessions: 'Sign out everywhere',
  adminSessionsRevoked: (email) => `All sessions of ${email} were closed.`,
  adminUserHistory: 'History',
  adminHistoryTitle: (email) => `History of ${email}`,
  adminHistoryEmpty: 'No recorded actions.',
  adminHistoryClose: 'Close',
  adminHistoryBy: (actor) => `by ${actor}`,
  adminErrorGeneric: 'The action could not be completed.',
  adminErrors: {
    ultimo_superadmin: 'Not allowed: at least one active superadmin must remain.',
    auto_accion_prohibida: 'You cannot change your own role or status, or remove yourself. Change your password in "My account".',
    usuario_no_encontrado: 'The user does not exist.',
    rol_invalido: 'Invalid role.',
    estado_invalido: 'Invalid status.',
    sesion_invalida: 'Your session is no longer valid. Sign in again.',
    usuario_no_activo: 'The user is not active: a link cannot be sent.',
    password_corta: 'The password must be at least 8 characters.',
    password_larga: 'The password is too long (maximum 72 bytes; accented letters take 2).',
    smtp_password_no_ascii: 'The saved SMTP password has non-ASCII characters (accents or ñ) and the server does not accept it. Re-enter it in Administration → Email (SMTP).',
    email_invalido: 'The email is not valid.',
    email_ya_existe: 'A user with that email already exists.',
    password_igual_a_la_actual: 'The new password must be different from the current one.',
    cambio_de_password_requerido: 'You have to change your password before continuing.',
  },
  adminAuditActions: {
    create: 'Created',
    update_email: 'Email changed',
    update_role: 'Role changed',
    update_status: 'Status changed',
    reset_link_sent: 'Recovery link sent',
    unlock: 'Unlocked',
    sessions_revoked: 'Sessions closed',
    baja: 'Removed',
    password_changed_self: 'Changed their password',
    password_reset_completed: 'Reset their password',
    password_set_by_admin: 'Password set by an admin',
  },
  adminAuditUnknown: 'Unknown action',

  // My account and recovery link — stage 4 (2026-09-12)
  myAccount: 'My account',
  myAccountChangePassword: 'Change my password',
  myAccountCurrent: 'Current password',
  myAccountSubmit: 'Change password',
  myAccountSubmitting: 'Saving…',
  myAccountDone: 'Password changed. Your other sessions were closed.',
  myAccountWrongCurrent: 'The current password is not correct.',
  myAccountTooMany: 'Too many attempts. Wait and try again.',
  myAccountError: 'The password could not be changed.',
  adminUserSendResetLink: 'Send link',
  adminResetLinkSent: (email) => `Recovery link sent to ${email}.`,

  // Edit and remove — stage 5 (2026-09-15)
  adminUserBaja: 'Remove',
  adminBajaTitle: (email) => `Remove ${email}`,
  adminBajaMessage: 'The account becomes unusable, leaves the list and frees the email. Its history is kept.',
  adminBajaDone: (email) => `${email} was removed.`,
  confirmSumLabel: (a, b) => `Solve ${a} + ${b} = ?`,
  confirmSumHint: 'Type the result to enable the button.',

  // Password set by an admin and forced change (2026-09-15, U34)
  adminUserSetPassword: 'Set password',
  adminSetPasswordTitle: (email) => `Set the password of ${email}`,
  adminSetPasswordHint: 'Their open sessions are closed and they will have to change it at their next sign-in.',
  adminSetPasswordSubmitting: 'Saving…',
  adminPasswordSetDone: (email) => `Password set for ${email}. They will have to change it when signing in.`,

  // Deleted users' history visible from the UI (2026-09-15, Task 2, DEUDA U36)
  adminUserShowBajas: 'Show deleted users',
  adminBajaListEmail: 'Original email',
  adminBajaListDeletedAt: 'Deleted on',
  adminBajaListDeletedBy: 'Deleted by',

  forcedChangeTitle: 'Change your password',
  forcedChangeIntro: 'An administrator set your password. To continue, choose a new one.',
  forcedChangeDone: 'Password changed. You can continue.',
  forcedChangeLogout: 'Sign out',
  myAccountSameAsCurrent: 'The new password must be different from the one you were given.',

  // Restoring pending tasks (useJaxStore.js)
  taskRestoring: (id) => `_Task \`${id}\` — checking status…_`,
}
