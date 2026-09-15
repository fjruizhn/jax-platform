import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'

// U34 (2026-09-15): con must_change_password la app NO se monta (ni Dashboard,
// ni su WS, ni sus polls): sólo el cambio obligatorio.
let estado
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector(estado),
}))

import RequireAuth from './RequireAuth'
import { I18nProvider } from '../i18n/index.jsx'

function arbol(hijos = <p>la app</p>) {
  return <I18nProvider><MemoryRouter><RequireAuth>{hijos}</RequireAuth></MemoryRouter></I18nProvider>
}

let root
beforeEach(() => {
  root = document.createElement('div')
  root.id = 'root'
  document.body.appendChild(root)
  localStorage.clear()
  estado = { token: 't', sessionRestoring: false, cambiarMiPassword: vi.fn(), logout: vi.fn(), addToast: vi.fn(), user: { user_id: 5 } }
})
afterEach(() => {
  root.remove()
})

describe('RequireAuth', () => {
  it('sin la marca muestra la app', () => {
    render(arbol())
    expect(screen.getByText('la app')).toBeInTheDocument()
  })

  it('con la marca muestra sólo el cambio obligatorio', () => {
    estado.user = { user_id: 5, must_change_password: true }
    render(arbol())
    expect(screen.queryByText('la app')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Cambiá tu contraseña' })).toBeInTheDocument()
  })

  // Tras el cambio el diálogo se desmonta y Dialogo devolvería el foco a lo
  // que lo tenía al abrir -- al entrar a la app, body. El foco va al punto de
  // entrada de la pantalla que se monta ([data-foco-inicial]: el campo del
  // chat en "/", el <main> de Admin).
  it('al terminar el cambio, el foco va al [data-foco-inicial] de la app', () => {
    estado.user = { user_id: 5, must_change_password: true }
    const hijos = <><button>otro</button><textarea aria-label="chat" data-foco-inicial /></>
    const { rerender } = render(arbol(hijos), { container: root })
    estado = { ...estado, user: { user_id: 5, must_change_password: false } }
    rerender(arbol(hijos))
    expect(screen.getByLabelText('chat')).toHaveFocus()
  })

  it('sin haber pasado por el cambio obligatorio no mueve el foco', () => {
    render(arbol(<textarea aria-label="chat" data-foco-inicial />), { container: root })
    expect(screen.getByLabelText('chat')).not.toHaveFocus()
  })
})
