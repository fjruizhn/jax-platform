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
// Etapa 5: tras guardar un correo, AdminUsers avisa al store (actualizarMiEmail
// decide si es el usuario logueado; lo prueba useJaxStore.test.js).
const actualizarMiEmailMock = vi.fn()
// Task 1 (2026-09-15, decisión de Fernando, revierte Ruling F7): la fila
// propia no ofrece auto-acciones. `usuario` es el logueado del store; por
// defecto no coincide con ningún user_id de fixture (2, 3, 5) para no afectar
// los tests existentes. Mismo patrón que BarraUsuario.test.jsx.
let usuario = { user_id: 1, email: 'admin@axioma-ia.io', role: 'superadmin' }
vi.mock('../../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ addToast: addToastMock, actualizarMiEmail: actualizarMiEmailMock, user: usuario }),
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
  actualizarMiEmailMock.mockReset()
  api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); api.delete.mockReset()
  servirGet([USUARIO])
  usuario = { user_id: 1, email: 'admin@axioma-ia.io', role: 'superadmin' }
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

// Etapa 5 (2026-09-15): eliminar = dar de baja, detrás de ConfirmacionSuma
// (nunca window.confirm ni DELETE); el alta traduce los códigos estables.
describe('AdminUsers — baja y alta', () => {
  beforeEach(() => servirGet([SUPERADMIN]))

  it('dar de baja exige la suma y recién ahí llama al backend', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Dar de baja' }))
    const dialogo = screen.getByRole('dialog', { name: 'Dar de baja a b@x.io' })
    const confirmar = within(dialogo).getByRole('button', { name: 'Dar de baja' })
    expect(confirmar).toBeDisabled()
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
    expect(api.post).not.toHaveBeenCalled()
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/baja'))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({ type: 'success', message: 'b@x.io fue dado de baja.' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('el alta con un correo repetido muestra el error traducido', async () => {
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: 'email_ya_existe' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: '+ Nuevo usuario' }))
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'b@x.io' } })
    fireEvent.change(screen.getByLabelText('Contraseña temporal'), { target: { value: 'clave-larga-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'Ya existe un usuario con ese correo.',
    }))
  })

  // Ruling U25/U28: "Dar de baja" entra a la exclusión mutua de los modales.
  it('abrir "Dar de baja" con Editar abierto deja un solo diálogo', async () => {
    renderUsers()
    const botonEditar = await screen.findByRole('button', { name: 'Editar' })
    const botonBaja = screen.getByRole('button', { name: 'Dar de baja' })
    fireEvent.click(botonEditar)
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    fireEvent.click(botonBaja)
    const dialogos = screen.getAllByRole('dialog')
    expect(dialogos).toHaveLength(1)
    expect(dialogos[0]).toHaveAttribute('aria-labelledby', 'confirmacion-suma-titulo')
  })

  it('guardar un correo nuevo avisa al store con el user_id y el correo guardado', async () => {
    api.put.mockResolvedValue({ data: { ok: true } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText('Email'), { target: { value: ' nuevo@x.io ' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/users/2', { email: 'nuevo@x.io' }))
    await waitFor(() => expect(actualizarMiEmailMock).toHaveBeenCalledWith(2, 'nuevo@x.io'))
  })
})

// Fix round 1 de la Task 4 (2026-09-15, review de 05b2200).
function resolverSuma(dialogo) {
  const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
  fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
}

describe('AdminUsers — baja: foco, error, exclusión y correo propio (fix round 1)', () => {
  beforeEach(() => servirGet([SUPERADMIN]))

  // Ruling U35 (WCAG 2.4.3): Dialogo devuelve el foco al botón de la fila, y
  // load() borra esa fila -- el foco caía a body. Va a "+ Nuevo usuario".
  it('tras una baja exitosa el foco queda en "+ Nuevo usuario", no en body', async () => {
    let dadoDeBaja = false
    api.get.mockImplementation((url) => Promise.resolve(
      url === '/admin/users' ? { data: { users: dadoDeBaja ? [] : [SUPERADMIN] } } : { data: { entries: HISTORIAL } }))
    api.post.mockImplementation(() => { dadoDeBaja = true; return Promise.resolve({ data: { ok: true } }) })
    renderUsers()
    const disparador = await screen.findByRole('button', { name: 'Dar de baja' })
    disparador.focus()
    fireEvent.click(disparador)
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(screen.queryByText('b@x.io')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: '+ Nuevo usuario' })).toHaveFocus())
    expect(document.activeElement).not.toBe(document.body)
  })

  it('si /baja falla (409 ultimo_superadmin) el diálogo sigue abierto, con el foco adentro, y el error sale traducido', async () => {
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: 'ultimo_superadmin' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Dar de baja' }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'No se puede: tiene que quedar al menos un superadmin activo.',
    }))
    const sigue = screen.getByRole('dialog', { name: 'Dar de baja a b@x.io' })
    await waitFor(() => expect(within(sigue).getByRole('button', { name: 'Dar de baja' })).toBeEnabled())
    expect(sigue.contains(document.activeElement)).toBe(true)
  })

  it.each([
    ['Editar', 'editar-usuario-titulo'],
    ['Historial', 'historial-titulo'],
    ['+ Nuevo usuario', null],
  ])('con la baja abierta, abrir "%s" la cierra y deja un solo diálogo', async (boton, idTitulo) => {
    renderUsers()
    const botonBaja = await screen.findByRole('button', { name: 'Dar de baja' })
    const otro = screen.getByRole('button', { name: boton })
    fireEvent.click(botonBaja)
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    fireEvent.click(otro)
    await waitFor(() => {
      const dialogos = screen.getAllByRole('dialog')
      expect(dialogos).toHaveLength(1)
      expect(dialogos[0]).not.toHaveAttribute('aria-labelledby', 'confirmacion-suma-titulo')
      if (idTitulo) expect(dialogos[0]).toHaveAttribute('aria-labelledby', idTitulo)
    })
  })

  it('si el PUT del correo falla, el store no se entera (user.email no cambia)', async () => {
    api.put.mockRejectedValue({ response: { status: 409, data: { detail: 'email_ya_existe' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByLabelText('Email'), { target: { value: 'otro@x.io' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'Ya existe un usuario con ese correo.',
    }))
    expect(actualizarMiEmailMock).not.toHaveBeenCalled()
  })
})

// Tanda del review final de la etapa 5 (m2, m3; Ruling U36).
const OTRO = {
  user_id: 5, email: 'y@x.io', role: 'operator', status: 'active', created_at: null,
  last_login: null, failed_attempts: 0, locked_until: null, is_locked: false,
}

describe('AdminUsers — baja: revisión final (m2/m3)', () => {
  it('m2: un 404 usuario_no_encontrado cierra el diálogo, avisa y recarga la lista', async () => {
    servirGet([SUPERADMIN])
    api.post.mockRejectedValue({ response: { status: 404, data: { detail: 'usuario_no_encontrado' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Dar de baja' }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'El usuario no existe.',
    }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // load() en el mount + load() tras el 404: dos llamadas.
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
  })

  it('m2: un 409 (el existente, ultimo_superadmin) sigue dejando el diálogo abierto', async () => {
    servirGet([SUPERADMIN])
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: 'ultimo_superadmin' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Dar de baja' }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'No se puede: tiene que quedar al menos un superadmin activo.',
    }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('m3: una confirmación en vuelo no cierra ni le roba el foco al diálogo de baja de OTRO usuario', async () => {
    servirGet([SUPERADMIN, OTRO])
    let resolverX
    const promesaX = new Promise((resolve) => { resolverX = resolve })
    api.post.mockImplementation((url) => (url === '/admin/users/2/baja' ? promesaX : Promise.resolve({ data: { ok: true } })))
    renderUsers()

    const botonesBaja = await screen.findAllByRole('button', { name: 'Dar de baja' })
    fireEvent.click(botonesBaja[0]) // fila de b@x.io (X)
    const dialogoX = screen.getByRole('dialog', { name: 'Dar de baja a b@x.io' })
    resolverSuma(dialogoX)
    fireEvent.click(within(dialogoX).getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/baja'))

    // X sigue esperando la respuesta -- Cancelar/Escape siguen habilitados.
    fireEvent.click(within(dialogoX).getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Dar de baja a b@x.io' })).not.toBeInTheDocument())

    const botonBajaY = screen.getAllByRole('button', { name: 'Dar de baja' })[1]
    fireEvent.click(botonBajaY)
    const dialogoY = screen.getByRole('dialog', { name: 'Dar de baja a y@x.io' })
    resolverSuma(dialogoY)

    // Recién ahora responde la confirmación de X, ya cancelada en pantalla.
    resolverX({ data: { ok: true } })
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'b@x.io fue dado de baja.',
    }))

    expect(screen.getByRole('dialog', { name: 'Dar de baja a y@x.io' })).toBeInTheDocument()
    expect(dialogoY.contains(document.activeElement)).toBe(true)
  })
})

// Fijar contraseña (2026-09-15, decisiones de Fernando que revierten U2): entra
// a la misma exclusión mutua (U25/U28) y su éxito, como la baja, sólo cierra el
// diálogo si sigue siendo el del mismo usuario.
describe('AdminUsers — fijar contraseña', () => {
  function fijarEn(dialogo, clave) {
    fireEvent.change(within(dialogo).getByLabelText('Nueva contraseña'), { target: { value: clave } })
    fireEvent.change(within(dialogo).getByLabelText('Confirmar contraseña'), { target: { value: clave } })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Fijar contraseña' }))
  }

  it('fija, cierra el modal y avisa traducido', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Fijar contraseña' }))
    fijarEn(screen.getByRole('dialog'), 'clave-fijada-789')
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/password', { new_password: 'clave-fijada-789' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Contraseña fijada para op@axioma-ia.io. Tendrá que cambiarla al entrar.',
    }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('un error deja el modal abierto y avisa traducido', async () => {
    api.post.mockRejectedValue({ response: { status: 403, data: { detail: 'auto_accion_prohibida' } } })
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Fijar contraseña' }))
    fijarEn(screen.getByRole('dialog'), 'clave-fijada-789')
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'No podés cambiar tu propio rol ni tu estado, ni darte de baja. Tu contraseña se cambia en "Mi cuenta".',
    }))
    expect(screen.getByRole('dialog', { name: 'Fijar la contraseña de op@axioma-ia.io' })).toBeInTheDocument()
  })

  it('abrir "Fijar contraseña" cierra la edición (un modal a la vez)', async () => {
    renderUsers()
    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Fijar contraseña' }))
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: /Fijar la contraseña/ })).toBeInTheDocument()
  })

  it.each([
    ['Editar', 'editar-usuario-titulo'],
    ['Historial', 'historial-titulo'],
    ['+ Nuevo usuario', null],
    ['Dar de baja', 'confirmacion-suma-titulo'],
  ])('con "Fijar contraseña" abierto, abrir "%s" lo cierra y deja un solo diálogo', async (boton, idTitulo) => {
    renderUsers()
    const botonFijar = await screen.findByRole('button', { name: 'Fijar contraseña' })
    const otro = screen.getByRole('button', { name: boton })
    fireEvent.click(botonFijar)
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    fireEvent.click(otro)
    await waitFor(() => {
      const dialogos = screen.getAllByRole('dialog')
      expect(dialogos).toHaveLength(1)
      expect(dialogos[0]).not.toHaveAttribute('aria-labelledby', 'fijar-password-titulo')
      if (idTitulo) expect(dialogos[0]).toHaveAttribute('aria-labelledby', idTitulo)
    })
  })

  // Fix round 1 (review de dd47d82): el sentido "abrir Fijar cierra el otro"
  // sólo tenía el caso de Editar.
  it.each([
    ['Dar de baja', 'confirmacion-suma-titulo'],
    ['Historial', 'historial-titulo'],
    ['+ Nuevo usuario', null],
  ])('con "%s" abierto, abrir "Fijar contraseña" lo cierra y deja un solo diálogo', async (boton) => {
    renderUsers()
    const botonFijar = await screen.findByRole('button', { name: 'Fijar contraseña' })
    fireEvent.click(screen.getByRole('button', { name: boton }))
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    fireEvent.click(botonFijar)
    await waitFor(() => {
      const dialogos = screen.getAllByRole('dialog')
      expect(dialogos).toHaveLength(1)
      expect(dialogos[0]).toHaveAttribute('aria-labelledby', 'fijar-password-titulo')
    })
  })

  it('un éxito tardío no cierra el "Fijar contraseña" de OTRO usuario', async () => {
    servirGet([SUPERADMIN, OTRO])
    let resolverX
    const promesaX = new Promise((resolve) => { resolverX = resolve })
    api.post.mockImplementation((url) => (url === '/admin/users/2/password' ? promesaX : Promise.resolve({ data: { ok: true } })))
    renderUsers()

    fireEvent.click((await screen.findAllByRole('button', { name: 'Fijar contraseña' }))[0]) // b@x.io (X)
    const dialogoX = screen.getByRole('dialog', { name: 'Fijar la contraseña de b@x.io' })
    fijarEn(dialogoX, 'clave-fijada-789')
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/users/2/password', { new_password: 'clave-fijada-789' }))

    // X sigue esperando -- Cancelar sigue habilitado.
    fireEvent.click(within(dialogoX).getByRole('button', { name: 'Cancelar' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    fireEvent.click(screen.getAllByRole('button', { name: 'Fijar contraseña' })[1]) // y@x.io (Y)
    expect(screen.getByRole('dialog', { name: 'Fijar la contraseña de y@x.io' })).toBeInTheDocument()

    resolverX({ data: { ok: true } })
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Contraseña fijada para b@x.io. Tendrá que cambiarla al entrar.',
    }))
    expect(screen.getByRole('dialog', { name: 'Fijar la contraseña de y@x.io' })).toBeInTheDocument()
  })
})

// Task 1 (2026-09-15, decisión de Fernando, revierte Ruling F7): la fila del
// usuario logueado no ofrece auto-acciones. El backend sigue rechazando la
// auto-acción con 403 auto_accion_prohibida como defensa en profundidad
// (ronda de "403 auto_accion_prohibida" arriba) -- esto es solo la UI.
describe('AdminUsers — fila propia sin auto-acciones (decisión de Fernando, revierte F7)', () => {
  it('la fila del usuario logueado no muestra "Fijar contraseña" ni "Dar de baja"', async () => {
    usuario = { user_id: 2, email: 'op@axioma-ia.io', role: 'operator' }
    renderUsers()
    await screen.findByText('op@axioma-ia.io')
    expect(screen.queryByRole('button', { name: 'Fijar contraseña' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Dar de baja' })).not.toBeInTheDocument()
    // Las demás acciones de la fila quedan como están.
    expect(screen.getByRole('button', { name: 'Editar' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cerrar sesiones' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enviar enlace' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Historial' })).toBeInTheDocument()
  })

  it('la fila de otro usuario sí muestra "Fijar contraseña" y "Dar de baja"', async () => {
    usuario = { user_id: 999, email: 'otro-admin@axioma-ia.io', role: 'superadmin' }
    renderUsers()
    await screen.findByText('op@axioma-ia.io')
    expect(screen.getByRole('button', { name: 'Fijar contraseña' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dar de baja' })).toBeInTheDocument()
  })

  it('con dos filas, solo la propia esconde las dos acciones -- la otra las conserva', async () => {
    usuario = { user_id: 2, email: 'op@axioma-ia.io', role: 'operator' }
    servirGet([USUARIO, OTRO])
    renderUsers()
    await screen.findByText('y@x.io')
    // "Fijar contraseña"/"Dar de baja" aparecen una sola vez: los de la fila OTRO.
    expect(screen.getAllByRole('button', { name: 'Fijar contraseña' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Dar de baja' })).toHaveLength(1)
  })
})
