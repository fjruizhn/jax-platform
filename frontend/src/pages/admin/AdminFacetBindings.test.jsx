import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// PR-J (2026-09-14): PUT /admin/facet-bindings/{key} hacia un modelo sin el
// contrato de dispatch de la faceta devuelve 409 con `detail` OBJETO
// (`modelo_sin_contrato_de_dispatch`). La pantalla lo interpolaba tal cual
// ("[object Object]"); ahora lo traduce. Un detail de texto sigue igual.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), put: vi.fn() } }))

import api from '../../api/client'
import AdminFacetBindings from './AdminFacetBindings'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

const BINDING = {
  facet_key: 'jekyll', display_name: 'Jekyll', transport: 'http_openai_compat',
  provider_id: 'deepseek', model_ref: 3, model_id: 'deepseek-v4-flash', capability_check: 'ok',
}
const MODELO = { id: 2111, provider_id: 'deepseek', model_id: 'deepseek-flash' }

function rechazo(status, detail) {
  return { response: { status, data: { detail } } }
}

async function guardarHacia(modelId) {
  api.get.mockImplementation(url => Promise.resolve(
    url.startsWith('/admin/facet-bindings') ? { data: { bindings: [BINDING] } } : { data: { models: [MODELO] } },
  ))
  render(<I18nProvider><AdminFacetBindings /></I18nProvider>)
  fireEvent.click(await screen.findByRole('button', { name: es.adminBindingsEdit }))
  await screen.findByRole('option', { name: 'deepseek/deepseek-flash' })
  fireEvent.change(screen.getByRole('combobox'), { target: { value: String(modelId) } })
  fireEvent.click(screen.getByRole('button', { name: es.adminBindingsSave }))
}

beforeEach(() => {
  api.get.mockReset(); api.put.mockReset()
  localStorage.clear()
})

describe('AdminFacetBindings -- el rechazo por contrato de dispatch se traduce', () => {
  it('el 409 de contrato nombra el modelo y la columna que falta', async () => {
    api.put.mockRejectedValue(rechazo(409, {
      code: 'modelo_sin_contrato_de_dispatch', model_id: 'deepseek-flash',
      campos: ['max_tokens_param'], message: '...',
    }))
    await guardarHacia(MODELO.id)
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(es.modelo_sin_contrato_de_dispatch('deepseek-flash', 'max_tokens_param'))
    expect(alerta).not.toHaveTextContent('[object Object]')
  })

  it('un detail de texto se sigue mostrando como antes', async () => {
    api.put.mockRejectedValue(rechazo(400, 'model_ref 2111 no existe en el catalogo'))
    await guardarHacia(MODELO.id)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      es.adminBindingsSaveError('model_ref 2111 no existe en el catalogo'),
    )
  })
})
