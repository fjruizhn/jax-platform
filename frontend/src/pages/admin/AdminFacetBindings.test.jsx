import { render, screen, fireEvent, within } from '@testing-library/react'
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

// PR-L ronda 1 (2026-09-14): el 409 del PUT ofrece "declarar contrato" hacia
// el mismo formulario/endpoint que el catálogo, y el último rechazo registrado
// para la faceta (model_catalog_audit) se ve en esta pantalla.
const RECHAZO_GUARDADO = {
  code: 'modelo_sin_contrato_de_dispatch', model_ref: 2111, model_id: 'deepseek-flash',
  provider_modelo: 'deepseek', campos: ['max_output_tokens'], performed_by: 1,
  performed_at: '2026-09-14 10:00:00',
}

function renderBindings(binding = BINDING) {
  api.get.mockImplementation(url => Promise.resolve(
    url.startsWith('/admin/facet-bindings')
      ? { data: { bindings: [binding] } }
      : { data: { models: [MODELO], max_tokens_param_opciones: ['max_tokens', 'max_completion_tokens'] } },
  ))
  render(<I18nProvider><AdminFacetBindings /></I18nProvider>)
}

describe('AdminFacetBindings -- declarar contrato desde el 409 y el rastro (PR-L)', () => {
  it('la clave nueva existe en es y en', async () => {
    const { default: en } = await import('../../i18n/en.js')
    expect(es.adminBindingsUltimoRechazo('x')).toContain('x')
    expect(en.adminBindingsUltimoRechazo('x')).toContain('x')
    for (const t of [es, en]) {
      expect(t.adminBindingsContratoGuardado, 'adminBindingsContratoGuardado').toBeTruthy()
      expect(t.adminBindingsContratoGuardado).not.toBe(t.adminContratoGuardado)
    }
  })

  it('el 409 de contrato del PUT ofrece declarar el contrato de esa fila', async () => {
    api.put.mockRejectedValueOnce(rechazo(409, {
      code: 'modelo_sin_contrato_de_dispatch', model_ref: MODELO.id, model_id: 'deepseek-flash',
      provider_modelo: 'deepseek', campos: ['max_tokens_param'], message: '...',
    }))
    await guardarHacia(MODELO.id)
    const alerta = await screen.findByRole('alert')
    fireEvent.click(within(alerta).getByRole('button', { name: es.adminContratoDeclarar }))
    expect(await screen.findByText(es.adminContratoTitulo('deepseek/deepseek-flash'))).toBeInTheDocument()
  })

  it('el 409 de otro proveedor no ofrece declarar contrato', async () => {
    api.put.mockRejectedValueOnce(rechazo(409, {
      code: 'modelo_de_otro_proveedor', model_ref: MODELO.id, model_id: 'deepseek-flash', campos: ['provider_id'],
      provider_modelo: 'deepseek', provider_binding: 'openai', message: '...',
    }))
    await guardarHacia(MODELO.id)
    const alerta = await screen.findByRole('alert')
    expect(within(alerta).queryByRole('button', { name: es.adminContratoDeclarar })).not.toBeInTheDocument()
  })

  it('el último rechazo guardado se ve y declara el contrato con el mismo endpoint', async () => {
    api.put.mockResolvedValue({ data: { ok: true } })
    renderBindings({ ...BINDING, ultimo_rechazo: RECHAZO_GUARDADO })
    expect(await screen.findByText(es.adminBindingsUltimoRechazo('2026-09-14 10:00'))).toBeInTheDocument()
    expect(screen.getByText(es.modelo_sin_contrato_de_dispatch('deepseek-flash', 'max_output_tokens'))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.adminContratoDeclarar }))
    fireEvent.change(await screen.findByLabelText(es.adminContratoParam), { target: { value: 'max_tokens' } })
    fireEvent.change(screen.getByLabelText(es.adminContratoTope), { target: { value: '4096' } })
    fireEvent.click(screen.getByRole('button', { name: es.adminContratoGuardar }))
    // Ronda 2: en Bindings no hay propuesta que reaprobar -- aviso propio.
    expect(await screen.findByText(es.adminBindingsContratoGuardado)).toBeInTheDocument()
    expect(screen.queryByText(es.adminContratoGuardado)).not.toBeInTheDocument()
    expect(api.put).toHaveBeenCalledWith('/admin/models/2111/contrato-dispatch', {
      max_tokens_param: 'max_tokens', max_output_tokens: 4096,
    })
  })

  it('un rechazo de un modelo que no está en la lista igual abre el formulario', async () => {
    renderBindings({ ...BINDING, ultimo_rechazo: { ...RECHAZO_GUARDADO, model_ref: 9999, model_id: 'otro-modelo' } })
    fireEvent.click(await screen.findByRole('button', { name: es.adminContratoDeclarar }))
    expect(await screen.findByText(es.adminContratoTitulo('deepseek/otro-modelo'))).toBeInTheDocument()
  })

  it('sin rechazo guardado no hay aviso ni botón', async () => {
    renderBindings()
    await screen.findByText('deepseek/deepseek-v4-flash')
    expect(screen.queryByRole('button', { name: es.adminContratoDeclarar })).not.toBeInTheDocument()
  })
})
