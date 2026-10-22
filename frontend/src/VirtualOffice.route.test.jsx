import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from './i18n/index.jsx'
import { useJaxStore } from './store/useJaxStore'

vi.mock('./apariencia/sincronizarApariencia', () => ({ sincronizarApariencia: vi.fn(() => Promise.resolve()) }))
vi.mock('./api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) } }))

import App from './App.jsx'

describe('Virtual Office route', () => {
  beforeEach(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn()
    useJaxStore.setState({ token: 'office-token', user: { user_id: 8, email: 'client@example.test', account_class: 'EXTERNAL_CLIENT' }, sessionRestoring: false })
    window.history.pushState({}, '', '/virtual-office')
  })

  it('allows an Office-only account into the Office entry without mounting classic routes', async () => {
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/virtual-office')
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Otorgar permiso|Invitar persona|Elegir proyecto/ })).not.toBeInTheDocument()
  })

  it('does not expose the Office entry to a classic account through a direct URL', async () => {
    useJaxStore.setState({ token: 'classic-token', user: { user_id: 9, email: 'classic@example.test', account_class: 'INTERNAL' }, sessionRestoring: false })
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })
})
