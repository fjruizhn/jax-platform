import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// I-1 (revisión final PR 2, 2026-09-14): "({n} intentos)" y el locale de
// last_login ('es-HN') estaban fijos en español, sin pasar por i18n.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

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

function renderUsers() {
  return render(<I18nProvider><AdminUsers /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { users: [USUARIO] } })
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

// M-2 (revisión final PR 2, 2026-09-14): el select de rol tenía border-borde
// (1,41:1 sobre superficie, bajo el mínimo 3:1 de WCAG 1.4.11 para un
// control) y focus:outline-none sin ningún indicador de foco de reemplazo.
describe('AdminUsers -- borde y foco del select de rol (M-2)', () => {
  it('usa border-borde-control (par de 3:1 declarado) y focus:border-foco', async () => {
    api.get.mockResolvedValue({ data: { users: [USUARIO] } })
    renderUsers()
    await screen.findByText('op@axioma-ia.io')
    const select = screen.getByDisplayValue('operator')
    expect(select.className).toMatch(/(^|\s)border-borde-control(\s|$)/)
    expect(select.className).not.toMatch(/(^|\s)border-borde(\s|$)/)
    expect(select.className).toMatch(/(^|\s)focus:border-foco(\s|$)/)
  })
})

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
