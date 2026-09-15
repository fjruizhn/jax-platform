import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'

// I-1 (revisión final PR 2, 2026-09-14): "({n} intentos)" y el locale de
// last_login ('es-HN') estaban fijos en español, sin pasar por i18n.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

// Etapa 3 (2026-09-15): los errores del backend llegan como códigos y se
// muestran traducidos en un toast -- antes cada acción terminaba en
// `.catch(() => {})`. El mock del store vale para todo el archivo.
const addToastMock = vi.fn()
vi.mock('../../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ addToast: addToastMock }),
}))

import api from '../../api/client'
import AdminUsers from './AdminUsers'
import { I18nProvider } from '../../i18n/index.jsx'

const USUARIO = {
  user_id: 2, email: 'op@axioma-ia.io', role: 'operator', status: 'active',
  is_locked: false, failed_attempts: 3, last_login: '2026-03-14T18:30:00Z',
}

const USUARIO_BLOQUEADO = {
  user_id: 3, email: 'bloqueado@axioma-ia.io', role: 'viewer', status: 'active',
  is_locked: true, failed_attempts: 5, last_login: '2026-03-14T18:30:00Z',
}

const SUPERADMIN = {
  user_id: 2, email: 'b@x.io', role: 'superadmin', status: 'active', created_at: null,
  last_login: null, failed_attempts: 0, locked_until: null, is_locked: false,
}

const HISTORIAL = [{
  id: 9, ts: '2026-09-12T10:00:00', actor_user_id: 1, actor_email: 'fernando@rich-hn.com',
  action: 'update_role', detail: { from: 'superadmin', to: 'operator' }, ip: '203.0.113.5',
}]

function renderUsers() {
  return render(<I18nProvider><AdminUsers /></I18nProvider>)
}

// GET por URL: la lista de usuarios o el historial de uno.
function servirGet(usuarios) {
  api.get.mockImplementation((url) => Promise.resolve(
    url === '/admin/users' ? { data: { users: usuarios } } : { data: { entries: HISTORIAL } }))
}

beforeEach(() => {
  addToastMock.mockReset()
  api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); api.delete.mockReset()
  servirGet([USUARIO])
  localStorage.clear()
})

describe('AdminUsers -- i18n (I-1)', () => {
  it('en inglés, "intentos" sale como "attempts" (no fijo en español)', async () => {
    const { default: es } = await import('../../i18n/es.js')
    const { default: en } = await import('../../i18n/en.js')
    expect(es.adminUserFailedAttempts(3)).not.toBe(en.adminUserFailedAttempts(3))

    localStorage.setItem('jax_lang', 'en')
    renderUsers()
    await screen.findByText('op@axioma-ia.io')
    expect(screen.getByText(en.adminUserFailedAttempts(3))).toBeInTheDocument()
    expect(screen.queryByText(/intentos/)).not.toBeInTheDocument()
  })

  it('la fecha del último acceso sigue el idioma activo (en-US, no es-HN fijo)', async () => {
    localStorage.setItem('jax_lang', 'en')
    renderUsers()
    await screen.findByText('op@axioma-ia.io')
    const esperado = new Date(USUARIO.last_login).toLocaleString('en-US')
    const fijoEsHN = new Date(USUARIO.last_login).toLocaleString('es-HN')
    await waitFor(() => expect(screen.getByText(esperado)).toBeInTheDocument())
    if (esperado !== fijoEsHN) {
      expect(screen.queryByText(fijoEsHN)).not.toBeInTheDocument()
    }
  })
})

// M-2 (select de rol con border-borde-control + focus:border-foco) se mudó a
// EditarUsuarioModal.test.jsx el 2026-09-15 (etapa 3, Ruling U6): el rol ya
// no se edita en línea en la tabla sino en el modal Editar.

// M-1 (revisión final PR 2, 2026-09-14): "Desbloquear" era bg-aviso-fondo
// sobre una fila que también es bg-aviso-fondo cuando is_locked -- en reposo
// no se distinguía del fondo de la fila, en ningún tema.
describe('AdminUsers -- botón Desbloquear sobre fila bloqueada (M-1)', () => {
  it('el fondo del botón difiere del fondo de la fila (aviso-fondo) y tiene borde visible en reposo', async () => {
    api.get.mockResolvedValue({ data: { users: [USUARIO_BLOQUEADO] } })
    renderUsers()
    const boton = await screen.findByRole('button', { name: 'Desbloquear' })
    expect(boton.className).not.toMatch(/(^|\s)bg-aviso-fondo(\s|$)/)
    expect(boton.className).toMatch(/(^|\s)border-aviso-borde(\s|$)/)
  })
})

// Etapa 3 (2026-09-15): editar rol/estado, cerrar sesiones e historial.
describe('AdminUsers -- etapa 3: toasts, edición, sesiones e historial', () => {
  beforeEach(() => servirGet([SUPERADMIN]))

  it('un 409 ultimo_superadmin al editar aparece traducido en un toast', async () => {
    api.put.mockRejectedValue({ response: { status: 409, data: { detail: 'ultimo_superadmin' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText('Rol'), { target: { value: 'operator' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'No se puede: tiene que quedar al menos un superadmin activo.',
    }))
    expect(api.put).toHaveBeenCalledWith('/admin/users/2', { role: 'operator' })
  })

  it('cerrar sesiones llama al endpoint y avisa', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Cerrar sesiones' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/revoke-sessions'))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Se cerraron todas las sesiones de b@x.io.',
    }))
  })

  it('el historial muestra la acción traducida, el cambio y quién la hizo', async () => {
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Historial' }))
    const dialogo = await screen.findByRole('dialog')
    expect(await within(dialogo).findByText('Cambio de rol')).toBeInTheDocument()
    expect(within(dialogo).getByText('superadmin → operator')).toBeInTheDocument()
    expect(within(dialogo).getByText('por fernando@rich-hn.com')).toBeInTheDocument()
  })
})

// Task 4, ronda 1 (2026-09-15): el camino 403. Guardarse a uno mismo un rol o
// estado responde auto_accion_prohibida: sale traducido y el modal NO se
// cierra (el admin ve qué intentó y puede cancelar o corregir).
describe('AdminUsers -- etapa 3: 403 auto_accion_prohibida', () => {
  beforeEach(() => servirGet([SUPERADMIN]))

  it('un 403 auto_accion_prohibida al editar aparece traducido y el modal sigue abierto', async () => {
    api.put.mockRejectedValue({ response: { status: 403, data: { detail: 'auto_accion_prohibida' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText('Estado'), { target: { value: 'inactive' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error',
      message: 'No podés cambiar tu propio rol ni tu estado, ni darte de baja. Tu contraseña se cambia en "Mi cuenta".',
    }))
    expect(api.put).toHaveBeenCalledWith('/admin/users/2', { status: 'inactive' })
    const sigue = screen.getByRole('dialog')
    await waitFor(() => expect(within(sigue).getByRole('button', { name: 'Guardar' })).not.toBeDisabled())
  })
})

describe('AdminUsers — enlace de recuperación', () => {
  it('sin SMTP configurado el 503 aparece traducido, no como éxito', async () => {
    api.post.mockRejectedValue({ response: { status: 503, data: { detail: 'smtp_no_configurado' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar enlace' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'El correo saliente no está configurado.',
    }))
    expect(api.post).toHaveBeenCalledWith('/admin/users/2/reset-link')
  })

  it('con éxito dice a qué correo se mandó', async () => {
    api.post.mockResolvedValue({ data: { ok: true, to: 'b@x.io' } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar enlace' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Enlace de recuperación enviado a b@x.io.',
    }))
  })

  // Fix round 1 (2026-09-15): el camino 502 (el servidor SMTP rechazó el
  // envío) no tenía test -- mensajeDeError agrega la respuesta del servidor
  // con smtpServerSaid, y nada probaba que "Enviar enlace" también la mostrara.
  it('un 502 smtp_envio_fallido muestra el mensaje y la respuesta del servidor SMTP', async () => {
    api.post.mockRejectedValue({
      response: { status: 502, data: { detail: { code: 'smtp_envio_fallido', server: 'Connection unexpectedly closed' } } },
    })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Enviar enlace' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error',
      message: 'El servidor SMTP no aceptó el correo. Respuesta del servidor: Connection unexpectedly closed',
    }))
  })
})

// Fix round 2 (2026-09-15, Ruling U25): con un modal abierto, el fondo (header
// + tabla) seguía alcanzable con Tab -- sin `inert` y sin trampa de foco --,
// así que un par de Tabs y Enter podían abrir Historial ENCIMA de Editar, y un
// solo Escape (los dos hooks escuchan en `document`) cerraba los dos a la vez,
// descartando en silencio una edición sin guardar.
// Ruling U27 (review final, 2026-09-15): el inert local (data-admin-contenido)
// dejaba vivo el AdminSidebar de Admin.jsx (I1). Ahora Dialogo marca #root
// entero: se renderiza dentro de un contenedor con id "root", como en
// index.html, para que el test mida lo real.
describe('AdminUsers -- #root inert detrás de un modal (Ruling U25/U27)', () => {
  let root
  beforeEach(() => {
    root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)
  })
  afterEach(() => root.remove())

  it.each(['Editar', 'Historial', '+ Nuevo usuario'])('con el modal de "%s" abierto #root queda inert; al cerrarlo, no', async (boton) => {
    render(<I18nProvider><AdminUsers /></I18nProvider>, { container: root })
    const disparador = await screen.findByRole('button', { name: boton })
    expect(root).not.toHaveAttribute('inert')

    disparador.focus()
    fireEvent.click(disparador)
    await waitFor(() => expect(root).toHaveAttribute('inert'))
    expect(root.contains(screen.getByRole('dialog'))).toBe(false)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(root).not.toHaveAttribute('inert')
    expect(disparador).toHaveFocus()
  })

  it('ya no hay un inert local: #root cubre también el sidebar', async () => {
    render(<I18nProvider><AdminUsers /></I18nProvider>, { container: root })
    await screen.findByText('op@axioma-ia.io')
    expect(document.querySelector('[data-admin-contenido]')).toBeNull()
  })
})

describe('AdminUsers -- un solo modal a la vez (Ruling U25)', () => {
  it('abrir Historial con Editar abierto deja un solo modal, y Escape lo cierra sin reabrir Editar', async () => {
    renderUsers()
    const botonEditar = await screen.findByRole('button', { name: 'Editar' })
    // Se toma la referencia ANTES de abrir Editar: una vez que el fondo queda
    // inert, un query por rol ya no la encontraría (queda fuera del árbol de
    // accesibilidad) -- fireEvent sobre el nodo ya obtenido no depende de eso.
    const botonHistorial = screen.getByRole('button', { name: 'Historial' })

    fireEvent.click(botonEditar)
    expect(screen.getAllByRole('dialog')).toHaveLength(1)

    fireEvent.click(botonHistorial)
    // HistorialUsuario pide su propio /audit al montar: se espera con waitFor
    // (envuelve en act) para no dejar esa resolución fuera de React.
    await waitFor(() => {
      const dialogos = screen.getAllByRole('dialog')
      expect(dialogos).toHaveLength(1)
      expect(dialogos[0]).toHaveAttribute('aria-labelledby', 'historial-titulo')
    })

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
