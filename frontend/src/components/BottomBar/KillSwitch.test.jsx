import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
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

// Fix round 1 de la Task 9 (2026-09-17). Un aviso de error describe un estado
// del freno: si el estado cambia por otro camino (WS, loadState, el 423 del
// interceptor), el aviso deja de aplicar. Y tras una acción propia que cambia
// de rama, el foco no se pierde en <body>.
function activarConFallo(rechazo) {
  api.post.mockRejectedValue(rechazo)
  fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
}

function sumaCorrecta(dialogo) {
  const etiqueta = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]').textContent
  const [, a, b] = etiqueta.match(/(\d+) \+ (\d+)/)
  fireEvent.change(within(dialogo).getByLabelText(etiqueta), { target: { value: String(Number(a) + Number(b)) } })
}

describe('KillSwitch -- el aviso pertenece al estado que describe', () => {
  it('activar falla (503) y otro admin lo activa: no queda "nada cambió" junto al badge', async () => {
    como('superadmin')
    pintar()
    activarConFallo({ response: { status: 503, data: { detail: 'kill_switch_no_escribible' } } })
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorNoEscribible)
    act(() => useJaxStore.getState().handleEvent({ event_type: 'kill_switch_activated', payload: {} }))
    expect(screen.getByText(es.killSwitchActive)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('activar con auditoría fallida: aviso en la rama activa; al liberarse, desaparece', async () => {
    como('superadmin')
    pintar()
    activarConFallo({ response: { status: 500, data: { detail: 'kill_switch_auditoria_fallida' } } })
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorAuditoria)
    expect(screen.getByText(es.killSwitchActive)).toBeInTheDocument()
    act(() => useJaxStore.getState().handleEvent({ event_type: 'kill_switch_released', payload: { activo: false } }))
    expect(screen.getByRole('button', { name: new RegExp(es.killButton) })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reanudar falla sin código: muestra el genérico de reanudar', async () => {
    como('superadmin', true)
    api.post.mockRejectedValue(new Error('red caída'))
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    sumaCorrecta(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.killResumeConfirm }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchErrorReanudar)
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })
})

// Task H (2026-09-17, requisito del controlador principal): la ruta vieja del
// freno sigue frenando. Reanudar quita el archivo propio pero, si la heredada
// sigue puesta, el backend responde activo: true, heredada: true. El badge
// sigue y se dice por qué, con el mismo aviso atado al estado.
describe('KillSwitch -- la ruta heredada sigue frenando', () => {
  function reanudarCon(data) {
    como('superadmin', true)
    api.post.mockResolvedValue({ data })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    sumaCorrecta(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.killResumeConfirm }))
  }

  it('reanudar con la heredada puesta: el badge sigue y se ve el aviso', async () => {
    reanudarCon({ activo: true, cambio: false, heredada: true })
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchHeredada)
    expect(screen.getByText(es.killSwitchActive)).toBeInTheDocument()
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })

  it('el aviso de la heredada desaparece cuando el freno se suelta por otro camino', async () => {
    reanudarCon({ activo: true, cambio: true, heredada: true })
    expect(await screen.findByRole('alert')).toHaveTextContent(es.killSwitchHeredada)
    act(() => useJaxStore.getState().handleEvent({ event_type: 'kill_switch_released', payload: { activo: false } }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reanudar sin heredada no muestra el aviso', async () => {
    reanudarCon({ activo: false, cambio: true, heredada: false })
    await waitFor(() => expect(useJaxStore.getState().killSwitchActive).toBe(false))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('killSwitchHeredada existe en es y en, nombra la ruta vieja y no está vacía', () => {
    for (const dic of [es, en]) {
      expect(typeof dic.killSwitchHeredada === 'string' && dic.killSwitchHeredada.trim() !== '').toBe(true)
      expect(dic.killSwitchHeredada).toContain('/etc/jax/PAUSE')
    }
  })
})

describe('KillSwitch -- foco tras la acción propia', () => {
  it('activar con éxito deja el foco en el aviso del freno, no en body', async () => {
    como('superadmin')
    api.post.mockResolvedValue({ data: { activo: true, cambio: true } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: new RegExp(es.killButton) }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: es.killConfirmYes }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(document.activeElement).toBe(screen.getByText(es.killSwitchActive).closest('[tabindex="-1"]')))
  })

  it('reanudar con éxito deja el foco en KILL', async () => {
    como('superadmin', true)
    api.post.mockResolvedValue({ data: { activo: false, cambio: true } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.killResumeButton }))
    const dialogo = screen.getByRole('dialog', { name: es.killResumeTitle })
    sumaCorrecta(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.killResumeConfirm }))
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: new RegExp(es.killButton) })))
  })

  it('un cambio externo del estado no mueve el foco', () => {
    como('superadmin')
    pintar()
    const kill = screen.getByRole('button', { name: new RegExp(es.killButton) })
    kill.blur()
    act(() => useJaxStore.getState().handleEvent({ event_type: 'kill_switch_activated', payload: {} }))
    expect(document.activeElement).toBe(document.body)
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
