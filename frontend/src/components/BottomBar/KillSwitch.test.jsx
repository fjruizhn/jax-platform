import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Kill switch real (2026-09-16, frente B). Antes el botón ponía
// killSwitchActive en true ANTES de llamar a un endpoint que no existía y se
// tragaba el 404: decía "detenido" sin detener nada.
vi.mock('../../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../../api/client'
import KillSwitch from './KillSwitch'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const INICIAL = useJaxStore.getState()

function pintar() {
  return render(<I18nProvider><KillSwitch /></I18nProvider>)
}

function como(role, killSwitchActive = false) {
  useJaxStore.setState({ user: { user_id: '7', role }, killSwitchActive })
}

beforeEach(() => {
  useJaxStore.setState(INICIAL, true)
  api.post.mockReset()
  api.get.mockReset()
  localStorage.clear()
})

describe('KillSwitch -- quién lo ve', () => {
  it('un operator no ve el botón con el freno suelto', () => {
    como('operator')
    pintar()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('un operator ve el aviso con el freno puesto, sin Reanudar', () => {
    como('operator', true)
    pintar()
    expect(screen.getByText(es.killSwitchActive)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: es.killResumeButton })).not.toBeInTheDocument()
  })
})

describe('KillSwitch -- activar', () => {
  it('abre un Dialogo propio y no llama al backend hasta confirmar', () => {
    como('superadmin')
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    expect(screen.getByRole('dialog', { name: es.killConfirmTitle })).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('confirmar activa y el estado sale de la respuesta', async () => {
    como('superadmin')
    api.post.mockResolvedValue({ data: { activo: true, cambio: true } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/kill-switch/activar'))
    await waitFor(() => expect(useJaxStore.getState().killSwitchActive).toBe(true))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('si falla, lo dice con su código y no finge que se detuvo', async () => {
    como('superadmin')
    api.post.mockRejectedValue({ response: { status: 503, data: { detail: 'kill_switch_no_escribible' } } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorNoEscribible)
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
  })

  it('un error sin código muestra el genérico de activar', async () => {
    como('superadmin')
    api.post.mockRejectedValue(new Error('red caída'))
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorActivar)
  })
})

describe('KillSwitch -- reanudar con suma', () => {
  it('pide la suma y recién ahí reanuda', async () => {
    como('superadmin', true)
    api.post.mockResolvedValue({ data: { activo: false, cambio: true } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    const etiqueta = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]').textContent
    const [, a, b] = etiqueta.match(/(\d+) \+ (\d+)/)
    const confirmar = within(dialogo).getByRole('button', { name: es.killResumeConfirm })
    expect(confirmar).toBeDisabled()
    fireEvent.change(within(dialogo).getByLabelText(etiqueta), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/kill-switch/reanudar'))
    await waitFor(() => expect(useJaxStore.getState().killSwitchActive).toBe(false))
  })

  it('con la suma mal no reanuda', () => {
    como('superadmin', true)
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    const etiqueta = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]').textContent
    fireEvent.change(within(dialogo).getByLabelText(etiqueta), { target: { value: '-1' } })
    expect(within(dialogo).getByRole('button', { name: es.killResumeConfirm })).toBeDisabled()
    expect(api.post).not.toHaveBeenCalled()
  })
})

describe('KillSwitch -- textos', () => {
  const NUEVAS = ['killConfirmTitle', 'killConfirmMessage', 'killResumeButton', 'killResumeTitle',
    'killResumeMessage', 'killResumeConfirm', 'killSwitchReleasedToast', 'killSwitchErrorActivar',
    'killSwitchErrorReanudar', 'killSwitchErrorNoEscribible', 'killSwitchErrorAuditoria']

  it('cada clave nueva existe en es y en, sin vacíos', () => {
    for (const clave of NUEVAS) {
      expect(typeof es[clave] === 'string' && es[clave].trim() !== '', `es.${clave}`).toBe(true)
      expect(typeof en[clave] === 'string' && en[clave].trim() !== '', `en.${clave}`).toBe(true)
    }
  })

  // Ruling R4 (2026-09-17): el texto del 423 vive con los demás errores de la
  // Mesa (erroresMesa, A-51), como función sin datos.
  it('erroresMesa.kill_switch_activo existe en es y en, sin vacíos', () => {
    for (const [idioma, dic] of [['es', es], ['en', en]]) {
      expect(typeof dic.erroresMesa.kill_switch_activo, `${idioma}.erroresMesa.kill_switch_activo`).toBe('function')
      const texto = dic.erroresMesa.kill_switch_activo()
      expect(typeof texto === 'string' && texto.trim() !== '', `${idioma}.erroresMesa.kill_switch_activo()`).toBe(true)
    }
    expect(es.kill_switch_activo).toBeUndefined()
    expect(en.kill_switch_activo).toBeUndefined()
  })

  it('el botón y su confirmación no comparten rótulo (un lector de pantalla los confunde)', () => {
    expect(es.killResumeButton).not.toBe(es.killResumeConfirm)
    expect(en.killResumeButton).not.toBe(en.killResumeConfirm)
  })

  it('las claves del botón viejo ya no existen', () => {
    for (const clave of ['killConfirm', 'killSwitchStoppedToast']) {
      expect(es[clave]).toBeUndefined()
      expect(en[clave]).toBeUndefined()
    }
  })

  it('el botón conserva el rótulo KILL', () => {
    expect(es.killButton).toBe('KILL')
    expect(en.killButton).toBe('KILL')
  })
})
