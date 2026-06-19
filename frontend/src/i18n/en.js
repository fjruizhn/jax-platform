export default {
  // Top bar
  logout: 'Sign out',

  // Left panel
  facets: 'Facets',
  alive: 'online',
  down: 'offline',

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
  pipelinesAdditional: (n) => `+${n} additional pipeline(s)`,
  approve: '✓ Approve',
  cancelling: 'Cancelling…',
  cancelPipeline: 'Cancel pipeline',

  // Audit log
  auditLog: 'Audit Log',
  loading: 'Loading…',
  noEventsYet: 'No events yet',

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
  taskInitializing: '_Starting autonomous task…_',
  taskStarted: (id) => `_Task started — \`${id}\`_\n\nHyde is running in background…`,
  errorTask: 'Could not start the task.',
  pipelineStarted: (id, mode, steps) =>
    `Pipeline started — \`${id}\`\nMode: **${mode}** · ${steps} steps\n\nTracking progress in right panel…`,
  errorPipeline: 'Could not create the pipeline.',

  // Kill switch
  killSwitchActive: 'KILL SWITCH ACTIVE',
  killConfirm: 'Confirm?',
  killConfirmYes: 'YES, STOP ALL',
  cancel: 'Cancel',
  killTitle: 'Kill Switch — stops all processes',

  // Pipeline modal
  newPipelineTitle: 'New Pipeline · Jacobs',
  objectiveLabel: 'Objective',
  modeLabel: 'Mode',
  facetsLabel: 'Facets',
  starting: 'Starting…',
  planAndExecute: 'Plan and execute',
  descJaxLocal: 'Local reasoning (Qwen3)',
  descHipatia: 'Web research',
  descJekyll: 'Reflective analysis',
  descThot: 'Critical audit',
  descKimi: 'Technical implementation',
  descAda: 'Final synthesis',

  // Center panel
  platformLabel: 'AXIOMA V0.2',
  inMemoryOf: 'In memory of Jairo Urbina.',
  inHonorOf: 'In honor of Prof. Raúl Jacobs.',

  // Login
  loginTitle: 'Axioma',
  loginTagline: 'In memory of Jairo Urbina',
  emailLabel: 'Email',
  passwordLabel: 'Password',
  loginError: 'Login failed',
  loggingIn: 'Signing in…',
  loginButton: 'Enter Axioma',

  // Message
  userLabel: 'User',

  // Theme / language
  lightMode: 'Light mode',
  darkMode: 'Dark mode',

  // File attachments
  attachFile: 'Attach file',
  attachTooltip: 'Attach image, PDF or text',
  attachRemove: 'Remove attachment',
  attachUploading: 'Uploading…',
  attachError: 'Error uploading file',
  attachTooLarge: 'File too large (max 10MB)',
  attachTypes: 'Images, PDF, text, code',
  attachedFile: (name) => `Attached: ${name}`,

  // Admin module
  adminTitle: 'Administration',
  adminNav: 'Admin',
  adminDashboard: 'Dashboard',
  adminApiKeys: 'API Keys',
  adminUsers: 'Users',
  adminRepo: 'Repository',
  adminSettings: 'Settings',
  adminCosts: 'Costs',
  adminBack: 'Back to Axioma',

  // Admin dashboard
  adminServicesTitle: 'Service Status',
  adminStatsTitle: "Today's Statistics",
  adminEventsTitle: 'Recent Events',
  serviceAlive: 'online',
  serviceDown: 'offline',
  serviceConnected: 'connected',
  serviceError: 'error',
  statMessages: 'Messages',
  statPipelines: 'Pipelines',
  statImages: 'Images',

  // Admin API keys
  adminKeyProvider: 'Provider',
  adminKeyModel: 'Model',
  adminKeyFacet: 'Facet',
  adminKeyValue: 'Key',
  adminKeyStatus: 'Status',
  adminKeyTest: 'Test',
  adminKeyRotate: 'Rotate key',
  adminKeyTesting: 'Testing…',
  adminKeyOk: 'OK',
  adminKeyFail: 'Error',
  adminKeyMissing: 'No key',
  adminKeyNewValue: 'New API key',
  adminKeySave: 'Save',
  adminKeyEnter: 'Enter the new key for',
  adminKeyLatency: (ms) => `${ms}ms`,

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
  adminUserDisable: 'Disable',
  adminUserEnable: 'Enable',
  adminUserDelete: 'Delete',
  adminUserResetPwd: 'Reset pwd',
  adminUserChangeRole: 'Change role',
  adminCreateTitle: 'Create User',
  adminCreatePassword: 'Temporary password',
  adminCreateSubmit: 'Create',
  adminCreateCancel: 'Cancel',
  adminDeleteConfirm: (email) => `Delete user ${email}?`,

  // Admin repository
  adminRepoTitle: 'Artifact Repository',
  adminRepoMissions: 'Missions',
  adminRepoPipelines: 'Pipelines',
  adminRepoDocuments: 'Documents',
  adminRepoImages: 'Images',
  adminRepoEmpty: 'No files',
  adminRepoDelete: 'Delete',
  adminRepoDownload: 'Download',
  adminRepoPreview: 'Preview',
  adminRepoDeleteConfirm: (name) => `Delete ${name}?`,
  adminRepoSize: (bytes) => bytes < 1024 ? `${bytes}B` : bytes < 1024*1024 ? `${(bytes/1024).toFixed(1)}KB` : `${(bytes/1024/1024).toFixed(1)}MB`,

  // Admin settings
  adminSettingsTitle: 'System Configuration',
  adminSettingsSave: 'Save',
  adminSettingsSaved: 'Saved',
  adminSettingsLang: 'Default language',
  adminSettingsTheme: 'Default theme',
  adminSettingsTimeout: 'Session timeout (min)',
  adminSettingsMaxPipelines: 'Max simultaneous pipelines',
  adminSettingsRetention: 'Web-task retention (days)',
  adminSettingsSystemName: 'System name',
  adminSettingsWsNotif: 'WS notifications',
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
  adminCostsPeriod: 'Period',
  adminCostsDay: 'Today',
  adminCostsWeek: 'Week',
  adminCostsMonth: 'Month',
  adminCostsTotal: 'Total',
  adminCostsChart: 'Requests by facet (last 7 days)',
  adminCostsNoData: 'No data yet',
}
