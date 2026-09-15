import { render, screen, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// PR-J (2026-09-14): aprobar una propuesta hacia un modelo que no declara el
// contrato de dispatch de la faceta devuelve 409 `modelo_sin_contrato_de_dispatch`.
// El catch de decide() estaba vacío: el rechazo parecía un click que no hizo
// nada. Ahora se dice qué falta, en los dos idiomas.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))

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
  api.get.mockReset(); api.post.mockReset(); api.put.mockReset()
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

  it('el 409 de otro proveedor nombra los dos proveedores', async () => {
    expect(en.modelo_de_otro_proveedor('m', 'a', 'b')).toContain('b')
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_de_otro_proveedor', model_id: 'gpt-x', campos: ['provider_id'],
      provider_modelo: 'openai', provider_binding: 'deepseek', message: '...',
    }))
    renderCatalogo()
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      es.modelo_de_otro_proveedor('gpt-x', 'openai', 'deepseek'),
    )
  })

  it('otro error cae en el texto genérico, no en silencio', async () => {
    api.post.mockRejectedValue(rechazo(500, 'lo_que_sea'))
    renderCatalogo()
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.adminProposalsDecideError)
  })
})

// PR-L (2026-09-14, Ruling 33): el 409 ya no es un callejón sin salida. La
// propuesta muestra su último rechazo (rastro en model_catalog_audit) y tanto
// ese rastro como el 409 en vivo ofrecen "declarar contrato" sobre la fila.
const MODELO_SIN_CONTRATO = {
  id: 2111, provider_id: 'deepseek', model_id: 'deepseek-flash', status: 'available', source: 'provider_api',
  max_tokens_param: null, max_output_tokens: null,
}
const MODELO_CON_CONTRATO = {
  id: 3, provider_id: 'deepseek', model_id: 'deepseek-v4-flash', status: 'available', source: 'provider_api',
  max_tokens_param: 'max_tokens', max_output_tokens: 393216,
}
const RECHAZO_GUARDADO = {
  code: 'modelo_sin_contrato_de_dispatch', model_ref: 2111, model_id: 'deepseek-flash',
  campos: ['max_tokens_param', 'max_output_tokens'], performed_by: 1, performed_at: '2026-09-14 10:00:00',
}

function renderConModelos(propuesta = PROPUESTA) {
  api.get.mockImplementation(url => Promise.resolve(
    url.startsWith('/admin/models/proposals')
      ? { data: { proposals: [propuesta] } }
      : { data: { models: [MODELO_SIN_CONTRATO, MODELO_CON_CONTRATO], max_tokens_param_opciones: ['max_tokens', 'max_completion_tokens'] } },
  ))
  return render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
}

// Task 16b (Ruling 28): "deprecado" se ve distinto de "degradado" -- antes
// los dos pintaban text-aviso y sólo los distinguía la palabra.
const MODELO_DEPRECADO = {
  id: 4, provider_id: 'deepseek', model_id: 'deepseek-v3', status: 'deprecated', source: 'provider_api',
  max_tokens_param: 'max_tokens', max_output_tokens: 131072,
}
const MODELO_DEGRADADO = {
  id: 5, provider_id: 'deepseek', model_id: 'deepseek-v3-mini', status: 'degraded', source: 'provider_api',
  max_tokens_param: 'max_tokens', max_output_tokens: 131072,
}

describe('AdminModelCatalog -- "deprecado" se distingue de "degradado" (Ruling 28)', () => {
  it('deprecated pinta text-obsoleto y degraded se queda en text-aviso', async () => {
    api.get.mockImplementation(url => Promise.resolve(
      url.startsWith('/admin/models/proposals')
        ? { data: { proposals: [] } }
        : { data: { models: [MODELO_DEPRECADO, MODELO_DEGRADADO] } },
    ))
    render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
    expect(await screen.findByText(es.adminModelsStatusDeprecated)).toHaveClass('text-obsoleto')
    expect(screen.getByText(es.adminModelsStatusDegraded)).toHaveClass('text-aviso')
  })
})

describe('AdminModelCatalog -- declarar contrato de dispatch (PR-L)', () => {
  it('la propuesta muestra su último rechazo guardado y ofrece declarar el contrato', async () => {
    renderConModelos({ ...PROPUESTA, ultimo_rechazo: RECHAZO_GUARDADO })
    expect(await screen.findByText(es.adminProposalsUltimoRechazo('2026-09-14 10:00'), { exact: false })).toBeInTheDocument()
    expect(screen.getByText(
      es.modelo_sin_contrato_de_dispatch('deepseek-flash', 'max_tokens_param, max_output_tokens'), { exact: false },
    )).toBeInTheDocument()
    // La tabla de propuestas es la primera; la del catálogo tiene su propio
    // "declarar contrato" por fila.
    const [tablaPropuestas] = screen.getAllByRole('table')
    fireEvent.click(within(tablaPropuestas).getByRole('button', { name: es.adminContratoDeclarar }))
    expect(await screen.findByText(es.adminContratoTitulo('deepseek/deepseek-flash'))).toBeInTheDocument()
  })

  it('el 409 en vivo de approve ofrece declarar el contrato de esa fila', async () => {
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_sin_contrato_de_dispatch', model_ref: 2111, model_id: 'deepseek-flash',
      campos: ['max_output_tokens'], message: '...',
    }))
    renderConModelos()
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    const alerta = await screen.findByRole('alert')
    fireEvent.click(within(alerta).getByRole('button', { name: es.adminContratoDeclarar }))
    expect(await screen.findByText(es.adminContratoTitulo('deepseek/deepseek-flash'))).toBeInTheDocument()
  })

  it('el 409 de otro proveedor NO ofrece declarar contrato (no es el remedio)', async () => {
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_de_otro_proveedor', model_ref: 2111, model_id: 'deepseek-flash', campos: ['provider_id'],
      provider_modelo: 'openai', provider_binding: 'deepseek', message: '...',
    }))
    renderConModelos()
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    const alerta = await screen.findByRole('alert')
    expect(within(alerta).queryByRole('button', { name: es.adminContratoDeclarar })).not.toBeInTheDocument()
  })

  it('el catálogo muestra el contrato de cada fila y declara el de una desde la tabla', async () => {
    api.put.mockResolvedValue({ data: { ok: true } })
    renderConModelos({ ...PROPUESTA, proposed_model_ref: 3 })
    expect(await screen.findByText(es.adminModelsContrato)).toBeInTheDocument()
    expect(screen.getByText(es.adminModelsContratoSinDeclarar)).toBeInTheDocument()
    expect(screen.getByText('max_tokens · 393216')).toBeInTheDocument()
    const tablaCatalogo = screen.getAllByRole('table')[1]
    const botones = within(tablaCatalogo).getAllByRole('button', { name: es.adminContratoDeclarar })
    expect(botones).toHaveLength(2)
    fireEvent.click(botones[0])  // fila 2111, la primera del catálogo
    fireEvent.change(await screen.findByLabelText(es.adminContratoParam), { target: { value: 'max_tokens' } })
    fireEvent.change(screen.getByLabelText(es.adminContratoTope), { target: { value: '393216' } })
    fireEvent.click(screen.getByRole('button', { name: es.adminContratoGuardar }))
    expect(await screen.findByText(es.adminContratoGuardado)).toBeInTheDocument()
    expect(api.put).toHaveBeenCalledWith('/admin/models/2111/contrato-dispatch', {
      max_tokens_param: 'max_tokens', max_output_tokens: 393216,
    })
  })
})
