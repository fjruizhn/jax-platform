import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// PR-L (2026-09-14, Ruling 33): el 409 `modelo_sin_contrato_de_dispatch` de
// PR-J dejaba como único remedio un UPDATE a mano sobre `model`. Este
// formulario declara el contrato de UNA fila vía
// PUT /admin/models/{id}/contrato-dispatch; el backend valida con los mismos
// validadores del dispatch y responde 422 `contrato_dispatch_invalido`.
vi.mock('../../api/client', () => ({ default: { put: vi.fn() } }))

import api from '../../api/client'
import FormContratoDispatch from './FormContratoDispatch'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const MODELO = { id: 2111, provider_id: 'deepseek', model_id: 'deepseek-flash', max_tokens_param: null, max_output_tokens: null }
const OPCIONES = ['max_tokens', 'max_completion_tokens']

const CLAVES_NUEVAS = [
  'adminContratoTitulo', 'adminContratoParam', 'adminContratoTope', 'adminContratoAyuda',
  'adminContratoElegir', 'adminContratoGuardar', 'adminContratoGuardando', 'adminContratoCancelar',
  'adminContratoGuardado', 'adminContratoError', 'contrato_dispatch_invalido', 'adminContratoDeclarar',
  'adminModelsContrato', 'adminModelsContratoSinDeclarar', 'adminProposalsUltimoRechazo',
]

function rechazo(status, detail) {
  return { response: { status, data: { detail } } }
}

function renderForm(props = {}) {
  const onGuardado = vi.fn()
  const onCancelar = vi.fn()
  render(
    <I18nProvider>
      <FormContratoDispatch modelo={MODELO} opciones={OPCIONES} onGuardado={onGuardado} onCancelar={onCancelar} {...props} />
    </I18nProvider>,
  )
  return { onGuardado, onCancelar }
}

function completar(param, tope) {
  fireEvent.change(screen.getByLabelText(es.adminContratoParam), { target: { value: param } })
  fireEvent.change(screen.getByLabelText(es.adminContratoTope), { target: { value: tope } })
  fireEvent.click(screen.getByRole('button', { name: es.adminContratoGuardar }))
}

beforeEach(() => {
  api.put.mockReset()
  localStorage.clear()
})

describe('FormContratoDispatch', () => {
  it('todas las claves nuevas existen en es y en, y las funciones interpolan', () => {
    for (const clave of CLAVES_NUEVAS) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(typeof es[clave], `tipo de ${clave}`).toBe(typeof en[clave])
    }
    for (const t of [es, en]) {
      expect(t.adminContratoTitulo('deepseek/deepseek-flash')).toContain('deepseek/deepseek-flash')
      expect(t.contrato_dispatch_invalido('max_output_tokens')).toContain('max_output_tokens')
      expect(t.adminProposalsUltimoRechazo('2026-09-14 10:00')).toContain('2026-09-14 10:00')
    }
  })

  it('las opciones del parámetro salen del backend, no de una lista propia', () => {
    renderForm({ opciones: ['uno_del_backend'] })
    expect(screen.getByRole('option', { name: 'uno_del_backend' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'max_tokens' })).not.toBeInTheDocument()
  })

  it('guardar manda el par a la fila y avisa al padre', async () => {
    api.put.mockResolvedValue({ data: { ok: true } })
    const { onGuardado } = renderForm()
    expect(screen.getByText(es.adminContratoTitulo('deepseek/deepseek-flash'))).toBeInTheDocument()
    completar('max_tokens', '393216')
    await vi.waitFor(() => expect(onGuardado).toHaveBeenCalledTimes(1))
    expect(api.put).toHaveBeenCalledWith('/admin/models/2111/contrato-dispatch', {
      max_tokens_param: 'max_tokens', max_output_tokens: 393216,
    })
  })

  it('el 422 del validador se traduce y nombra la columna', async () => {
    api.put.mockRejectedValue(rechazo(422, {
      code: 'contrato_dispatch_invalido', campos: ['max_output_tokens'], message: '...',
    }))
    const { onGuardado } = renderForm()
    completar('max_tokens', '0')
    expect(await screen.findByRole('alert')).toHaveTextContent(es.contrato_dispatch_invalido('max_output_tokens'))
    expect(onGuardado).not.toHaveBeenCalled()
  })

  it('un tope vacío no se convierte en 0: va null y lo rechaza el backend', async () => {
    api.put.mockRejectedValue(rechazo(422, {
      code: 'contrato_dispatch_invalido', campos: ['max_output_tokens'], message: '...',
    }))
    renderForm()
    completar('max_tokens', '')
    await screen.findByRole('alert')
    expect(api.put).toHaveBeenCalledWith('/admin/models/2111/contrato-dispatch', {
      max_tokens_param: 'max_tokens', max_output_tokens: null,
    })
  })

  it('otro error cae en el genérico, no en silencio', async () => {
    api.put.mockRejectedValue(rechazo(500, 'lo_que_sea'))
    renderForm()
    completar('max_tokens', '4096')
    expect(await screen.findByRole('alert')).toHaveTextContent(es.adminContratoError)
  })

  it('cancelar avisa al padre sin escribir', () => {
    const { onCancelar } = renderForm()
    fireEvent.click(screen.getByRole('button', { name: es.adminContratoCancelar }))
    expect(onCancelar).toHaveBeenCalledTimes(1)
    expect(api.put).not.toHaveBeenCalled()
  })
})
