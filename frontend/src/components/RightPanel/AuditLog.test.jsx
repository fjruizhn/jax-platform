import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import AuditLog from './AuditLog'
import { I18nProvider } from '../../i18n/index.jsx'

const EVENTO = {
  event: 'ENVELOPE_ACCEPTED',
  layer: 'gate',
  reason: 'ok',
  '@timestamp': '2026-03-14T18:30:00Z',
}

function renderLog() {
  return render(<I18nProvider><AuditLog /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { events: [EVENTO] } })
  localStorage.clear()
})

// I-2 (revisión final PR 3, 2026-09-14): la hora del evento fijaba 'es-HN' en
// toLocaleTimeString sin importar el idioma activo (mismo defecto que
// Message.jsx). Ahora sale de localeFor(lang), como AdminUsers/AdminCosts/
// AdminRepository (Ruling 29).
describe('AuditLog -- hora del evento sigue el idioma activo, no es-HN fijo (I-2)', () => {
  it('en español (default), la hora usa es-HN', async () => {
    renderLog()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(await screen.findByText(new Date(EVENTO['@timestamp']).toLocaleTimeString('es-HN'))).toBeInTheDocument()
  })

  it('en inglés, la hora usa en-US, no es-HN fijo', async () => {
    localStorage.setItem('jax_lang', 'en')
    renderLog()
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    const esperado = new Date(EVENTO['@timestamp']).toLocaleTimeString('en-US')
    const fijoEsHN = new Date(EVENTO['@timestamp']).toLocaleTimeString('es-HN')
    expect(await screen.findByText(esperado)).toBeInTheDocument()
    if (esperado !== fijoEsHN) {
      expect(screen.queryByText(fijoEsHN)).not.toBeInTheDocument()
    }
  })
})
