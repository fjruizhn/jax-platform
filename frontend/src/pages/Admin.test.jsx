import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// U34 (fix round 1 del review de dd47d82): tras el cambio obligatorio de
// contraseña, RequireAuth pone el foco en [data-foco-inicial]. En Admin es el
// <main> (enfocable con tabIndex -1). Sin este test, quitar el marcador dejaba
// la suite verde.
vi.mock('../api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})) } }))

import Admin from './Admin'
import { I18nProvider } from '../i18n/index.jsx'

beforeEach(() => {
  localStorage.clear()
})

describe('Admin', () => {
  it('el <main> es el punto de entrada del foco ([data-foco-inicial], enfocable)', () => {
    render(<I18nProvider><MemoryRouter initialEntries={['/admin/dashboard']}><Admin /></MemoryRouter></I18nProvider>)
    const main = screen.getByRole('main')
    expect(main).toHaveAttribute('data-foco-inicial')
    expect(main).toHaveAttribute('tabindex', '-1')
  })
})
