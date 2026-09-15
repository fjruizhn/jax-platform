import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import AuditLog from './AuditLog'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

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

// Task 3 (2026-09-15): un audit.jsonl ilegible respondía {events: []} y el
// panel decía "Sin eventos aún". Ahora el backend responde 503
// `auditoria_ilegible` y el panel lo dice, traducido, en vez de la lista vacía.
describe('AuditLog -- un audit ilegible no se ve como "sin eventos"', () => {
  it('los textos existen en los dos idiomas', () => {
    for (const clave of ['auditoria_ilegible', 'auditLogError']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
    }
  })

  it('el 503 auditoria_ilegible muestra su texto y no "sin eventos"', async () => {
    api.get.mockRejectedValue({ response: { status: 503, data: { detail: 'auditoria_ilegible' } } })
    renderLog()
    expect(await screen.findByText(es.auditoria_ilegible)).toBeInTheDocument()
    expect(screen.queryByText(es.noEventsYet)).not.toBeInTheDocument()
  })

  it('otro fallo muestra el error genérico, no "sin eventos"', async () => {
    api.get.mockRejectedValue(new Error('Network Error'))
    renderLog()
    expect(await screen.findByText(es.auditLogError)).toBeInTheDocument()
    expect(screen.queryByText(es.noEventsYet)).not.toBeInTheDocument()
  })

  it('un archivo vacío sigue siendo "sin eventos"', async () => {
    api.get.mockResolvedValue({ data: { events: [] } })
    renderLog()
    expect(await screen.findByText(es.noEventsYet)).toBeInTheDocument()
  })
})

// Task 6 S3 (2026-09-15): /api/audit pasó a require_superadmin. Si igual
// llega un 403 (rol cambiado con la pestaña abierta), el panel lo dice
// traducido en vez del error genérico o de una lista vacía.
describe('AuditLog -- el 403 de quien no es superadmin se dice, traducido', () => {
  const PROHIBIDO = { response: { status: 403, data: { detail: 'Solo superadmin' } } }

  it('el texto existe en los dos idiomas', () => {
    expect(es.auditoriaSoloSuperadmin).toBeTruthy()
    expect(en.auditoriaSoloSuperadmin).toBeTruthy()
  })

  it('en español, el 403 muestra su texto y no "sin eventos" ni el genérico', async () => {
    api.get.mockRejectedValue(PROHIBIDO)
    renderLog()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.auditoriaSoloSuperadmin)
    expect(screen.queryByText(es.noEventsYet)).not.toBeInTheDocument()
    expect(screen.queryByText(es.auditLogError)).not.toBeInTheDocument()
  })

  it('en inglés, el 403 muestra el texto en inglés', async () => {
    localStorage.setItem('jax_lang', 'en')
    api.get.mockRejectedValue(PROHIBIDO)
    renderLog()
    expect(await screen.findByRole('alert')).toHaveTextContent(en.auditoriaSoloSuperadmin)
  })
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
