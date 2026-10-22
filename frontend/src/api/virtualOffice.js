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
