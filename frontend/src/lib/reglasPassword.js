// Regla única de contraseña (2026-09-12, admin usuarios etapa 4): la misma de
// backend/auth/password_rules.py. Mínimo 8 CARACTERES contados como puntos de
// código ([...s].length, igual que len() de Python; s.length contaría un emoji
// como 2) y máximo 72 BYTES: bcrypt no admite más (una ñ ocupa 2).
export const MIN_CARACTERES = 8
export const BCRYPT_MAX_BYTES = 72

export function problemaDePassword(password) {
  if ([...password].length < MIN_CARACTERES) return 'corta'
  if (new TextEncoder().encode(password).length > BCRYPT_MAX_BYTES) return 'larga'
  return null
}
