import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Pantalla "Correo (SMTP)" (2026-09-12, etapa 1): copia de AteneaERP. La
// contraseña nunca vuelve del backend (llega una máscara), el estado corrupto
// se nombra, y cada error es un código que se traduce.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminSmtp from './AdminSmtp'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const MASCARA = '••••••••'
const GUARDADA = {
  host: 'mail.axioma-ia.io', port: 587, encryption: 'tls', user: 'no-reply@axioma-ia.io',
  password: MASCARA, from_name: 'Axioma', from_email: 'no-reply@axioma-ia.io',
  configurado: true, corrupta: false, motivo: null,
}

function renderSmtp() {
  return render(<I18nProvider><AdminSmtp /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset(); api.put.mockReset(); api.post.mockReset()
  localStorage.clear()
})

describe('AdminSmtp', () => {
  it('muestra la máscara y la manda tal cual al guardar (conserva la guardada)', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.put.mockResolvedValue({ data: { ok: true } })
    const { container } = renderSmtp()
    await waitFor(() => expect(container.querySelector('input[type="password"]')).toHaveValue(MASCARA))
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/smtp', expect.objectContaining({ password: MASCARA, port: 587 })))
    expect(await screen.findByText('Configuración SMTP guardada.')).toBeInTheDocument()
  })

  it('estado corrupto: nombra el motivo y pide volver a escribir la contraseña', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, password: '', corrupta: true, motivo: 'password_ilegible' } })
    renderSmtp()
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(/no se puede descifrar/)
    expect(alerta).toHaveTextContent(/Volvé a escribir la contraseña/)
  })

  it('probar conexión muestra el paso exacto traducido y lo que respondió el servidor', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post.mockRejectedValue({ response: { status: 422, data: { detail: { code: 'smtp_auth_rechazada', server: '535 5.7.8 Authentication failed' } } } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: 'Probar conexión' }))
    const estado = await screen.findByRole('status')
    expect(estado).toHaveTextContent('El servidor rechazó el usuario o la contraseña.')
    expect(estado).toHaveTextContent('Respuesta del servidor: 535 5.7.8 Authentication failed')
  })

  it('guardar sin contraseña la primera vez muestra el error traducido', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, host: 'mail.axioma-ia.io', password: '', configurado: false } })
    api.put.mockRejectedValue({ response: { status: 422, data: { detail: 'smtp_exige_contrasena' } } })
    renderSmtp()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(await screen.findByText('Escribí la contraseña: no hay una guardada que se pueda usar.')).toBeInTheDocument()
  })

  it('correo de prueba: sin SMTP traduce el 503; con éxito dice a quién se envió', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post
      .mockRejectedValueOnce({ response: { status: 503, data: { detail: 'smtp_no_configurado' } } })
      .mockResolvedValueOnce({ data: { ok: true, to: 'fernando@rich-hn.com' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: 'Enviar correo de prueba' }))
    expect(await screen.findByText('El correo saliente no está configurado.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Enviar correo de prueba' }))
    expect(await screen.findByText('Correo de prueba enviado a fernando@rich-hn.com.')).toBeInTheDocument()
  })

  // ---------------- fix wave de la revisión final (2026-09-13)

  it('guardado bien y recarga fallida: dice que se guardó y avisa que no se pudo recargar', async () => {
    api.get.mockResolvedValueOnce({ data: GUARDADA }).mockRejectedValueOnce({ response: { status: 500 } })
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    const estado = await screen.findByRole('status')
    await waitFor(() => expect(estado).toHaveTextContent(es.smtpSaved))
    expect(estado).toHaveTextContent(es.smtpReloadFailed)
    expect(estado.className).toContain('text-green-400')
  })

  it('el banner de estado corrupto es una sola frase de i18n, sin puntuación agregada', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, password: '', corrupta: true, motivo: 'password_ilegible' } })
    renderSmtp()
    const alerta = await screen.findByRole('alert')
    expect(alerta.textContent).toBe(es.smtpCorruptBanner(es.smtpMotivoPasswordIlegible))
  })

  it('con cifrado "none" advierte que la contraseña viaja sin cifrar', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    expect(screen.queryByText(es.smtpEncNoneWarning)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(es.smtpEncryption), { target: { value: 'none' } })
    expect(screen.getByText(es.smtpEncNoneWarning)).toBeInTheDocument()
  })

  it('los códigos nuevos del backend tienen texto en es y en', () => {
    const nuevos = ['smtp_reescribir_contrasena_al_cambiar_servidor', 'smtp_password_no_ascii', 'smtp_campo_invalido']
    for (const t of [es, en]) {
      for (const code of nuevos) expect(typeof t.smtpErrors[code]).toBe('string')
      expect(typeof t.smtpReloadFailed).toBe('string')
      expect(typeof t.smtpEncNoneWarning).toBe('string')
      expect(t.smtpCorruptBanner('x')).toContain('x')
    }
  })
})
