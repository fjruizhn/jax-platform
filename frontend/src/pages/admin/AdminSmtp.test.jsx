import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
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
  test_to: '', email_sesion: 'fernando@rich-hn.com',
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
    const dialogo = await screen.findByRole('dialog')
    fireEvent.click(within(dialogo).getByRole('button', { name: es.smtpTestSendButton }))
    expect(await within(dialogo).findByText('El correo saliente no está configurado.')).toBeInTheDocument()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.smtpTestSendButton }))
    expect(await screen.findByText('Correo de prueba enviado a fernando@rich-hn.com.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
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
    expect(estado.className).toContain('text-exito')
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
    const nuevos = ['smtp_reescribir_contrasena_al_cambiar_servidor', 'smtp_password_no_ascii', 'smtp_campo_invalido', 'smtp_usuario_no_ascii']
    for (const t of [es, en]) {
      for (const code of nuevos) expect(typeof t.smtpErrors[code]).toBe('string')
      expect(typeof t.smtpReloadFailed).toBe('string')
      expect(typeof t.smtpEncNoneWarning).toBe('string')
      expect(t.smtpCorruptBanner('x')).toContain('x')
    }
    // smtp_campo_invalido también salta por el correo del remitente (item H).
    expect(es.smtpErrors.smtp_campo_invalido).toMatch(/correo del remitente/)
    expect(en.smtpErrors.smtp_campo_invalido).toMatch(/sender email/)
  })

  // ------- puerto por cifrado y destinatario de la prueba (2026-09-13)

  it('cambiar el cifrado pone el puerto de ese cifrado: none 25, tls 587, ssl 465', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    const puerto = screen.getByLabelText(es.smtpPort)
    for (const [cifrado, esperado] of [['none', 25], ['ssl', 465], ['tls', 587]]) {
      fireEvent.change(screen.getByLabelText(es.smtpEncryption), { target: { value: cifrado } })
      expect(puerto).toHaveValue(esperado)
    }
  })

  it('un puerto personalizado se conserva hasta que se cambia el cifrado', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    const puerto = screen.getByLabelText(es.smtpPort)
    fireEvent.change(puerto, { target: { value: '2525' } })
    fireEvent.change(screen.getByLabelText(es.smtpUser), { target: { value: 'otro@axioma-ia.io' } })
    expect(puerto).toHaveValue(2525)
    fireEvent.change(screen.getByLabelText(es.smtpEncryption), { target: { value: 'ssl' } })
    expect(puerto).toHaveValue(465)
    fireEvent.change(puerto, { target: { value: '8465' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/smtp', expect.objectContaining({ port: 8465, encryption: 'ssl' })))
  })

  it('al cargar no toca el puerto guardado (2525 con STARTTLS se ve 2525)', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, port: 2525, encryption: 'tls' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    expect(screen.getByLabelText(es.smtpPort)).toHaveValue(2525)
  })

  it('el destinatario por defecto es opcional y se guarda con Guardar', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, test_to: 'pruebas@rich-hn.com' } })
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSmtp()
    const campo = await screen.findByLabelText(es.smtpTestToDefault)
    expect(campo).toHaveValue('pruebas@rich-hn.com')
    expect(campo).toHaveAttribute('type', 'email')
    expect(campo).not.toBeRequired()
    expect(screen.getByText(es.smtpTestToHint)).toBeInTheDocument()
    fireEvent.change(campo, { target: { value: 'otro@rich-hn.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/smtp', expect.objectContaining({ test_to: 'otro@rich-hn.com' })))
  })

  it('el botón abre un diálogo prellenado con el destinatario por defecto', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, test_to: 'pruebas@rich-hn.com' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    const dialogo = await screen.findByRole('dialog')
    expect(dialogo).toHaveAttribute('aria-modal', 'true')
    expect(dialogo).toHaveAccessibleName(es.smtpTestModalTitle)
    expect(within(dialogo).getByLabelText(es.smtpTestRecipient)).toHaveValue('pruebas@rich-hn.com')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('el diálogo de prueba es el Dialogo común: portal fuera del formulario y el fondo no cierra (A-23)', async () => {
    api.get.mockResolvedValue({ data: { ...GUARDADA, test_to: 'pruebas@rich-hn.com' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    const dialogo = await screen.findByRole('dialog')
    expect(dialogo.closest('form[class]')).toBeNull()
    fireEvent.click(dialogo.parentElement)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('sin destinatario por defecto, el diálogo se prellena con el correo de la sesión', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    const dialogo = await screen.findByRole('dialog')
    expect(within(dialogo).getByLabelText(es.smtpTestRecipient)).toHaveValue('fernando@rich-hn.com')
  })

  it('Enviar manda el destinatario escrito y, con éxito, cierra y dice a quién', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post.mockResolvedValue({ data: { ok: true, to: 'otra@rich-hn.com' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    const dialogo = await screen.findByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText(es.smtpTestRecipient), { target: { value: 'otra@rich-hn.com' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.smtpTestSendButton }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/smtp/test', { to: 'otra@rich-hn.com' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('status')).toHaveTextContent(es.smtpTestSent('otra@rich-hn.com'))
  })

  it('un error se muestra DENTRO del diálogo, que sigue abierto con el destinatario', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post.mockRejectedValue({ response: { status: 502, data: { detail: { code: 'smtp_envio_fallido', server: '550 5.1.1 User unknown' } } } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    const dialogo = await screen.findByRole('dialog')
    fireEvent.click(within(dialogo).getByRole('button', { name: es.smtpTestSendButton }))
    const alerta = await within(dialogo).findByRole('alert')
    expect(alerta).toHaveTextContent(es.smtpErrors.smtp_envio_fallido)
    expect(alerta).toHaveTextContent(es.smtpServerSaid('550 5.1.1 User unknown'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(within(dialogo).getByLabelText(es.smtpTestRecipient)).toHaveValue('fernando@rich-hn.com')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('Cancelar y Escape cierran el diálogo sin enviar', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: es.smtpCancel }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.smtpSendTest }))
    fireEvent.keyDown(await screen.findByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('los textos nuevos del destinatario tienen es y en', () => {
    for (const t of [es, en]) {
      for (const k of ['smtpTestToDefault', 'smtpTestToHint', 'smtpTestModalTitle', 'smtpTestRecipient', 'smtpTestSendButton', 'smtpCancel']) {
        expect(typeof t[k]).toBe('string')
      }
      expect(typeof t.smtpErrors.smtp_destinatario_invalido).toBe('string')
    }
  })

  it('al cerrar el diálogo (Cancelar, Escape o envío exitoso) el foco vuelve al botón que lo abrió', async () => {
    api.get.mockResolvedValue({ data: GUARDADA })
    api.post.mockResolvedValue({ data: { ok: true, to: 'fernando@rich-hn.com' } })
    renderSmtp()
    await screen.findByDisplayValue('mail.axioma-ia.io')
    const disparador = screen.getByRole('button', { name: es.smtpSendTest })
    // Dialogo captura el foco al montar (previo = document.activeElement):
    // en un navegador real un clic ya deja el botón enfocado, pero
    // fireEvent.click no simula esa parte, así que se enfoca a mano (mismo
    // patrón que Dialogo.test.jsx y AdminUsers.test.jsx).
    disparador.focus()
    fireEvent.click(disparador)
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: es.smtpCancel }))
    expect(disparador).toHaveFocus()
    disparador.focus()
    fireEvent.click(disparador)
    fireEvent.keyDown(await screen.findByRole('dialog'), { key: 'Escape' })
    expect(disparador).toHaveFocus()
    disparador.focus()
    fireEvent.click(disparador)
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: es.smtpTestSendButton }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(disparador).toHaveFocus())
  })
})
