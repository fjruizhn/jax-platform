import { describe, it, expect } from 'vitest'
import { mensajeDeError } from './erroresAdmin'

// Etapa 4 (2026-09-12, C4-2): un código que no está en t.adminErrors puede
// venir de las rutas SMTP compartidas (enviar el enlace de recuperación
// reusa /admin/smtp): mensajeDeError cae a t.smtpErrors antes del genérico.
const t = {
  adminErrorGeneric: 'No se pudo completar la acción.',
  adminErrors: {
    usuario_no_activo: 'El usuario no está activo: no se le puede enviar un enlace.',
  },
  smtpErrors: {
    smtp_no_configurado: 'El correo saliente no está configurado.',
  },
  smtpServerSaid: (texto) => `Respuesta del servidor: ${texto}`,
}

function err(detail) {
  return { response: { data: { detail } } }
}

describe('mensajeDeError', () => {
  it('un código de adminErrors se traduce ahí', () => {
    expect(mensajeDeError(t, err('usuario_no_activo'))).toBe(
      'El usuario no está activo: no se le puede enviar un enlace.',
    )
  })

  it('un código smtp_* ausente de adminErrors cae a t.smtpErrors', () => {
    expect(mensajeDeError(t, err('smtp_no_configurado'))).toBe(
      'El correo saliente no está configurado.',
    )
  })

  it('un código desconocido cae al mensaje genérico, nunca el código crudo', () => {
    expect(mensajeDeError(t, err('codigo_inventado'))).toBe('No se pudo completar la acción.')
  })
})
