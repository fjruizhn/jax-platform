import api from './client'

export const mfaApi = {
  status: () => api.get('/auth/mfa/status'),
  loginVerify: (challenge_token, otp) => api.post('/auth/mfa/login/verify', { challenge_token, otp }),
  recover: (recovery_code) => api.post('/auth/mfa/recover', { recovery_code }),
  recoveryEnrollmentStart: (recovery_token) => api.post('/auth/mfa/recovery/enrollment/start', { recovery_token }),
  enrollmentStart: (password) => api.post('/auth/mfa/enrollment/start', { password }),
  reenrollmentStart: (password) => api.post('/auth/mfa/reenrollment/start', { password }),
  enrollmentConfirm: (enrollment_token, otp) => api.post('/auth/mfa/enrollment/confirm', { enrollment_token, otp }),
  stepUp: () => api.post('/auth/mfa/step-up'),
  stepUpVerify: (challenge_token, otp) => api.post('/auth/mfa/step-up/verify', { challenge_token, otp }),
  regenerateCodes: (password) => api.post('/auth/mfa/recovery-codes/regenerate', { password }),
  disable: (password) => api.post('/auth/mfa/disable', { password }),
}

// La respuesta 200 de esta ruta es la única señal de entrada para Office.
// Los datos de sesión del navegador sólo sirven para desechar una respuesta
// tardía de otra identidad; nunca para conceder acceso.
export const officeSessionApi = {
  read: () => api.get('/virtual-office/session'),
  bootstrap: (payload) => api.post('/virtual-office/bootstrap', payload),
}

export const officeProjectsApi = {
  list: ({ limit, after_id }) => api.get('/virtual-office/projects', { params: { limit, after_id } }),
  detail: (projectId) => api.get(`/virtual-office/projects/${projectId}`),
  create: (payload, idempotencyKey) => api.post('/virtual-office/projects', payload, { headers: { 'Idempotency-Key': idempotencyKey } }),
}

export const officePeopleApi = {
  list: ({ limit, after_user_id, tenant_id } = {}) => api.get('/virtual-office/people', { params: { limit, after_user_id, tenant_id } }),
}

export const officeMembersApi = {
  list: (projectId, { limit, cursor } = {}) => api.get(`/virtual-office/projects/${projectId}/members`, { params: { limit, cursor } }),
  grant: (projectId, userId, payload, idempotencyKey) => api.put(`/virtual-office/projects/${projectId}/members/${userId}`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }),
  changeRole: (projectId, userId, payload, idempotencyKey) => api.patch(`/virtual-office/projects/${projectId}/members/${userId}/role`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }),
  revoke: (projectId, userId, expectedVersion, idempotencyKey) => api.delete(`/virtual-office/projects/${projectId}/members/${userId}`, { params: { expected_version: expectedVersion }, headers: { 'Idempotency-Key': idempotencyKey } }),
}

// Los agentes viven dentro de un proyecto de Oficina. El navegador escoge
// únicamente claves del catálogo que el servidor entrega; nunca transportes,
// URLs ni credenciales de proveedores.
export const officeAgentsApi = {
  list: (projectId) => api.get(`/virtual-office/projects/${projectId}/agents`),
  engines: (projectId) => api.get(`/virtual-office/projects/${projectId}/engines`),
  create: (projectId, payload) => api.post(`/virtual-office/projects/${projectId}/agents`, payload),
  bind: (projectId, agentId, payload) => api.patch(`/virtual-office/projects/${projectId}/agents/${agentId}/binding`, payload),
  run: (projectId, agentId, payload) => api.post(`/virtual-office/projects/${projectId}/agents/${agentId}/runs`, payload),
}

// El token de aceptación sólo llega aquí desde el fragmento del navegador y
// viaja en el cuerpo del POST. Nunca se concatena a una ruta o query string.
export const officeInvitationsApi = {
  list: (projectId, { limit, cursor } = {}) => api.get(`/virtual-office/projects/${projectId}/invitations`, { params: { limit, cursor } }),
  issue: (projectId, payload, idempotencyKey) => api.post(`/virtual-office/projects/${projectId}/invitations`, payload, { headers: { 'Idempotency-Key': idempotencyKey } }),
  revoke: (projectId, invitationId, expectedVersion, idempotencyKey) => api.delete(`/virtual-office/projects/${projectId}/invitations/${invitationId}`, { params: { expected_version: expectedVersion }, headers: { 'Idempotency-Key': idempotencyKey } }),
  accept: (payload, { accessToken = null, signal } = {}) => api.post('/virtual-office/invitations/accept', payload, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    ...(accessToken ? {} : { _officeInvitationNoAuth: true }),
    ...(signal ? { signal } : {}),
  }),
}
