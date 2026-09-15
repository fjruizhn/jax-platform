import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// PR-J (2026-09-14): aprobar una propuesta hacia un modelo que no declara el
// contrato de dispatch de la faceta devuelve 409 `modelo_sin_contrato_de_dispatch`.
// El catch de decide() estaba vacío: el rechazo parecía un click que no hizo
// nada. Ahora se dice qué falta, en los dos idiomas.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminModelCatalog from './AdminModelCatalog'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const PROPUESTA = {
  id: 11, facet_key: 'jekyll', current_model_ref: 3, proposed_model_ref: 2111,
  reason: 'drift_detected', detail: 'renombrado', status: 'pending',
}

function rechazo(status, detail) {
  return { response: { status, data: { detail } } }
}

function renderCatalogo() {
  api.get.mockImplementation(url => Promise.resolve(
    url.startsWith('/admin/models/proposals') ? { data: { proposals: [PROPUESTA] } } : { data: { models: [] } },
  ))
  return render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset()
  localStorage.clear()
})

describe('AdminModelCatalog -- una aprobación rechazada se ve', () => {
  it('los textos existen en los dos idiomas', () => {
    for (const clave of ['modelo_sin_contrato_de_dispatch', 'adminProposalsDecideError']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
    }
    expect(es.modelo_sin_contrato_de_dispatch('m', 'x')).toContain('x')
    expect(en.modelo_sin_contrato_de_dispatch('m', 'x')).toContain('x')
  })

  it('el 409 de contrato nombra el modelo y las columnas que faltan', async () => {
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_sin_contrato_de_dispatch', model_id: 'deepseek-flash',
      campos: ['max_tokens_param', 'max_output_tokens'], message: '...',
    }))
    renderCatalogo()
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      es.modelo_sin_contrato_de_dispatch('deepseek-flash', 'max_tokens_param, max_output_tokens'),
    )
  })

  it('otro error cae en el texto genérico, no en silencio', async () => {
    api.post.mockRejectedValue(rechazo(500, 'lo_que_sea'))
    renderCatalogo()
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.adminProposalsDecideError)
  })
})
