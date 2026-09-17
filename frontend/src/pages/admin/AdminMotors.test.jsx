import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminMotors from './AdminMotors'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

beforeEach(() => {
  localStorage.clear()
  api.get.mockImplementation((url) => Promise.resolve({ data: url === '/admin/motors'
    ? { motors: [], transport_values: ['ollama'], dispatchable_transports: ['ollama'] }
    : url === '/admin/models' ? { models: [] } : { capabilities: [] } }))
})

describe('AdminMotors -- crear motor en un Dialogo (A-23)', () => {
  it('abre un diálogo modal con nombre; Escape lo cierra; un clic en el fondo no', async () => {
    render(<I18nProvider><AdminMotors /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminMotorsCreate }))
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveAttribute('aria-modal', 'true')
    expect(dialogo).toHaveAccessibleName(es.adminMotorsCreateTitle)
    fireEvent.click(dialogo.parentElement)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
