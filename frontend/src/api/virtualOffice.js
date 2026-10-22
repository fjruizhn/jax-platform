import api from './client'

export const mfaApi = {
  status: () => api.get('/auth/mfa/status'),
  loginVerify: (challenge_token, otp) => api.post('/auth/mfa/login/verify', { challenge_token, otp }),
  recover: (recovery_code) => api.post('/auth/mfa/recover', { recovery_code }),
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
}
