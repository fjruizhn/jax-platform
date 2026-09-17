import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Continuar un pipeline detenido (spec 2026-09-17 §6.2): pasos reusados como
// listos, pasos a correr con selector de faceta (mismas opciones y clean-room
// que PipelineModal), costo máximo del pre-vuelo de continue y la misma
// confirmación en ventana propia. Formas reales del backend (adenda Task 10):
// `motivo` es un dict con `code`, una reasignación inválida llega como 200
// continuable:false y el veredicto viene aunque no sea continuable cuando el
// motivo es prevuelo_rechazado o limite_de_activos.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import ContinuarPipelineModal from './ContinuarPipelineModal'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import { formatearUsd } from '../../lib/moneda'
import { textoDeViolacion, textoDeMotivoDeCosto } from '../../api/errores'

// toHaveTextContent colapsa los espacios del DOM (incluido el espacio duro de
// Intl en es-HN) pero no los del esperado.
const texto = (s) => s.replace(/\s+/g, ' ')
const usd = (monto) => texto(formatearUsd(monto, 'es'))

const PASOS = [
  { step_index: 0, facet: 'hipatia', capability: 'research', depends_on: [], status: 'completed' },
  { step_index: 1, facet: 'ada', capability: 'design', depends_on: [0], status: 'completed' },
  { step_index: 2, facet: 'jekyll', capability: 'critique', depends_on: [0, 1], status: 'completed' },
  { step_index: 3, facet: 'ada', capability: 'reconcile', depends_on: [1, 2], status: 'completed' },
  { step_index: 4, facet: 'kimi', capability: 'generate', depends_on: [3], status: 'failed' },
  { step_index: 5, facet: 'thot', capability: 'validate_consistency', depends_on: [0, 2, 3, 4], status: 'pending' },
]
const VEREDICTO = {
  ok: true, violaciones: [], costo_max_usd: '0.30', umbral_usd: '0.50', requiere_confirmacion: false,
  pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.25', motivo: 'acotado' }, { paso: 5, faceta: 'thot', usd_max: '0.05', motivo: 'acotado' }],
}
const CONTINUABLE = { continuable: true, motivo: null, pasos_a_correr: [4, 5], pasos_reusados: [0, 1, 2, 3], veredicto: VEREDICTO }
const CARO = { ...CONTINUABLE, veredicto: { ...VEREDICTO, requiere_confirmacion: true } }
const PIPELINE = { pipeline_id: 'p-1', name: 'leyes de energía', status: 'aborted' }
const CONTINUADO = { pipeline_id: 'p-1', status: 'running', run_epoch: 2, pasos_a_correr: [4, 5], pasos_reusados: [0, 1, 2, 3] }

const esPrevio = (url) => url.endsWith('/continue/preflight')
const llamadasA = (url) => api.post.mock.calls.filter(([u]) => u === url)
const PREVIO = '/pipelines/p-1/continue/preflight'
const CONTINUAR = '/pipelines/p-1/continue'

function mockPrevio(previo) {
  api.post.mockImplementation((url) => Promise.resolve({ data: esPrevio(url) ? previo : CONTINUADO }))
}

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  api.get.mockImplementation((url) => Promise.resolve({
    data: url === '/motors/capabilities'
      ? { capabilities: [{ key: 'generate', allowed_motors: ['kimi', 'jax_local'] }], motors: [] }
      : { pipeline: { pipeline_id: 'p-1' }, steps: PASOS },
  }))
  mockPrevio(CONTINUABLE)
})

function abrir() {
  const onClose = vi.fn()
  render(<I18nProvider><ContinuarPipelineModal pipeline={PIPELINE} onClose={onClose} /></I18nProvider>)
  return { onClose }
}

const selectDe = (n, cap) => screen.getByLabelText(es.continuarPasoACorrer(n, cap))
const botonContinuar = () => screen.getByRole('button', { name: es.continuarBoton })

async function listo() {
  const r = abrir()
  await screen.findByText(es.continuarPasoReusado(1, 'hipatia'))
  await waitFor(() => expect(botonContinuar()).not.toBeDisabled())
  return r
}

describe('ContinuarPipelineModal -- lo que muestra', () => {
  it('es un diálogo con nombre; Escape lo cierra', async () => {
    const { onClose } = await listo()
    expect(screen.getByRole('dialog')).toHaveAccessibleName(es.continuarTitulo)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('muestra los pasos reusados como listos, los que faltan con selector y el costo máximo', async () => {
    abrir()
    expect(await screen.findByText(es.continuarPasoReusado(1, 'hipatia'))).toBeInTheDocument()
    expect(screen.getByText(es.continuarPasoReusado(4, 'ada'))).toBeInTheDocument()
    expect(selectDe(5, 'generate')).toHaveValue('kimi')
    expect(selectDe(6, 'validate_consistency')).toHaveValue('thot')
    expect(screen.queryByLabelText(es.continuarPasoACorrer(1, 'research'))).not.toBeInTheDocument()
    const dialogo = screen.getByRole('dialog')
    await waitFor(() => expect(dialogo).toHaveTextContent(texto(es.continuarCostoMax(formatearUsd('0.30', 'es')))))
    expect(dialogo).toHaveTextContent(texto(es.confirmarCostoPaso(VEREDICTO.pasos_costo[0], formatearUsd('0.25', 'es'))))
  })

  it('las opciones de faceta son las de PipelineModal para esa capability', async () => {
    await listo()
    const opciones = Array.from(selectDe(5, 'generate').options).map((o) => o.value)
    expect(opciones).toEqual(['hipatia', 'jekyll', 'thot', 'ada', 'jax_local', 'kimi'])
  })

  it('un paso sin costo acotado se avisa con su motivo traducido, nunca crudo', async () => {
    const paso = { paso: 4, faceta: 'kimi', usd_max: null, motivo: 'herramientas_sin_tope' }
    mockPrevio({ ...CONTINUABLE, veredicto: { ...VEREDICTO, pasos_costo: [paso] } })
    await listo()
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveTextContent(es.confirmarCostoNoAcotado)
    expect(dialogo).toHaveTextContent(textoDeMotivoDeCosto(es, 'herramientas_sin_tope'))
    expect(dialogo).not.toHaveTextContent('herramientas_sin_tope')
  })

  it('si no se pueden cargar los pasos, lo dice y no deja continuar', async () => {
    api.get.mockRejectedValue(new Error('502'))
    abrir()
    expect(await screen.findByText(es.continuarErrorCarga)).toBeInTheDocument()
    expect(botonContinuar()).toBeDisabled()
  })

  it('si el pre-vuelo de continue falla, el error traducido se ve y no deja continuar', async () => {
    api.post.mockRejectedValue({ response: { status: 503, data: { detail: { code: 'prevuelo_no_disponible' } } } })
    abrir()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.prevuelo_no_disponible({}))
    expect(botonContinuar()).toBeDisabled()
  })
})

describe('ContinuarPipelineModal -- reasignar facetas', () => {
  it('cambiar la faceta de un paso vuelve a pedir el pre-vuelo con la reasignación', async () => {
    await listo()
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'ada' } })
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(PREVIO, { reasignar: { 4: 'ada' } }))
    expect(Object.keys(api.post.mock.calls.at(-1)[1].reasignar)).toEqual(['4'])
  })

  it('volver a la faceta original la saca de la reasignación', async () => {
    await listo()
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'ada' } })
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(PREVIO, { reasignar: { 4: 'ada' } }))
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'kimi' } })
    await waitFor(() => expect(api.post.mock.calls.at(-1)).toEqual([PREVIO, { reasignar: {} }]))
    await waitFor(() => expect(botonContinuar()).not.toBeDisabled())
    fireEvent.click(botonContinuar())
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(CONTINUAR, { reasignar: {} }))
  })

  it('un auditor igual a quien produjo avisa y bloquea Continuar', async () => {
    await listo()
    fireEvent.change(selectDe(6, 'validate_consistency'), { target: { value: 'ada' } })
    expect(await screen.findByText(es.continuarCleanroom(6, 'ada', 4))).toBeInTheDocument()
    expect(botonContinuar()).toBeDisabled()
  })

  it('una respuesta vieja del pre-vuelo no pisa la de la reasignación vigente', async () => {
    let resolverViejo
    api.post.mockImplementation((url, cuerpo) => {
      if (!esPrevio(url)) return Promise.resolve({ data: CONTINUADO })
      const faceta = cuerpo.reasignar['4']
      if (faceta === 'ada') {
        return new Promise((r) => { resolverViejo = () => r({ data: { ...CONTINUABLE, veredicto: { ...VEREDICTO, costo_max_usd: '9.99' } } }) })
      }
      const costo = faceta === 'jax_local' ? '0.40' : '0.30'
      return Promise.resolve({ data: { ...CONTINUABLE, veredicto: { ...VEREDICTO, costo_max_usd: costo } } })
    })
    await listo()
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'ada' } })
    await waitFor(() => expect(resolverViejo).toBeTypeOf('function'))
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'jax_local' } })
    const dialogo = screen.getByRole('dialog')
    await waitFor(() => expect(dialogo).toHaveTextContent(usd('0.40')))
    await act(async () => { resolverViejo() })
    expect(dialogo).toHaveTextContent(usd('0.40'))
    expect(dialogo).not.toHaveTextContent(usd('9.99'))
    expect(botonContinuar()).not.toBeDisabled()
  })

  it('una reasignación inválida se ve en línea con el detalle por paso y se puede corregir', async () => {
    const invalida = {
      continuable: false, pasos_a_correr: [], pasos_reusados: [0, 1, 2, 3], veredicto: null,
      motivo: { code: 'reasignacion_invalida', detalle: [{ paso: 4, faceta: 'ada', motivo: 'no admite generate' }] },
    }
    api.post.mockImplementation((url, cuerpo) => Promise.resolve({
      data: esPrevio(url) && cuerpo.reasignar['4'] === 'ada' ? invalida : CONTINUABLE,
    }))
    await listo()
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'ada' } })
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(es.erroresMesa.reasignacion_invalida({}))
    expect(alerta).toHaveTextContent(es.detalleDePaso({ paso: 4, faceta: 'ada', motivo: 'no admite generate' }))
    expect(botonContinuar()).toBeDisabled()
    expect(selectDe(5, 'generate')).not.toBeDisabled()
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'kimi' } })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    await waitFor(() => expect(botonContinuar()).not.toBeDisabled())
  })
})

describe('ContinuarPipelineModal -- no continuable', () => {
  it('un pipeline en otro estado dice por qué con su texto y no deja continuar', async () => {
    mockPrevio({
      continuable: false, pasos_a_correr: [], pasos_reusados: [], veredicto: null,
      motivo: { code: 'estado_no_continuable', status: 'completed', mensaje: 'status completed' },
    })
    abrir()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.estado_no_continuable({ status: 'completed' }))
    expect(botonContinuar()).toBeDisabled()
  })

  it('sin motivo legible cae en el texto genérico, nunca en el código crudo', async () => {
    mockPrevio({ continuable: false, pasos_a_correr: [], pasos_reusados: [], veredicto: null, motivo: { code: 'constructor' } })
    abrir()
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(es.continuarNoContinuable)
    expect(alerta).not.toHaveTextContent('constructor')
    expect(botonContinuar()).toBeDisabled()
  })

  it('prevuelo_rechazado trae veredicto: muestra las violaciones y el costo aunque no sea continuable', async () => {
    const v = { paso: 4, faceta: 'kimi', regla: 'tope_insuficiente', detalle: '' }
    mockPrevio({
      continuable: false, pasos_a_correr: [4, 5], pasos_reusados: [0, 1, 2, 3],
      motivo: { code: 'prevuelo_rechazado', detalle: '' },
      veredicto: { ...VEREDICTO, ok: false, violaciones: [v] },
    })
    abrir()
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toHaveTextContent(texto(es.continuarCostoMax(formatearUsd('0.30', 'es'))))
    expect(botonContinuar()).toBeDisabled()
  })
})

describe('ContinuarPipelineModal -- continuar y confirmar el costo', () => {
  it('sin confirmación continúa sin costo_confirmado_usd, avisa y cierra', async () => {
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(llamadasA(CONTINUAR)).toEqual([[CONTINUAR, { reasignar: {} }]])
    expect(onClose).toHaveBeenCalled()
  })

  it('por encima del umbral confirma en ventana propia y continúa con el costo confirmado', async () => {
    mockPrevio(CARO)
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(llamadasA(CONTINUAR)).toHaveLength(0)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(CONTINUAR, { reasignar: {}, costo_confirmado_usd: '0.30' }))
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('la reasignación elegida viaja en continue', async () => {
    api.post.mockImplementation((url) => Promise.resolve({ data: esPrevio(url) ? CONTINUABLE : CONTINUADO }))
    await listo()
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'jax_local' } })
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(PREVIO, { reasignar: { 4: 'jax_local' } }))
    await waitFor(() => expect(botonContinuar()).not.toBeDisabled())
    fireEvent.click(botonContinuar())
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(CONTINUAR, { reasignar: { 4: 'jax_local' } }))
  })

  it('si continuar falla, el error se ve en la ventana y no se cierra', async () => {
    api.post.mockImplementation((url) => (esPrevio(url)
      ? Promise.resolve({ data: CONTINUABLE })
      : Promise.reject({ response: { status: 409, data: { detail: { code: 'costo_supera_lo_aceptado' } } } })))
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
    expect(onClose).not.toHaveBeenCalled()
    expect(botonContinuar()).not.toBeDisabled()
  })

  it('409 costo_supera_lo_aceptado reabre la confirmación con el costo nuevo y lo manda al confirmar', async () => {
    const rechazo = { response: { status: 409, data: { detail: {
      code: 'costo_supera_lo_aceptado', costo_max_usd: '0.90', costo_max_aceptado_usd: '0.30',
      pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.90', motivo: 'acotado' }],
    } } } }
    let intentos = 0
    api.post.mockImplementation((url) => {
      if (esPrevio(url)) return Promise.resolve({ data: CARO })
      intentos += 1
      return intentos === 1 ? Promise.reject(rechazo) : Promise.resolve({ data: CONTINUADO })
    })
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    let dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(intentos).toBe(1))
    dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    await waitFor(() => expect(dialogo).toHaveTextContent(usd('0.90')))
    expect(within(dialogo).getByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(intentos).toBe(2))
    expect(llamadasA(CONTINUAR)[1][1]).toEqual({ reasignar: {}, costo_confirmado_usd: '0.90' })
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('422 prevuelo_rechazado al continuar muestra las violaciones en la ventana', async () => {
    const v = { paso: 5, faceta: 'thot', regla: 'faceta_caida', detalle: '' }
    api.post.mockImplementation((url) => (esPrevio(url)
      ? Promise.resolve({ data: CONTINUABLE })
      : Promise.reject({ response: { status: 422, data: { detail: { code: 'prevuelo_rechazado', violaciones: [v] } } } })))
    await listo()
    fireEvent.click(botonContinuar())
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
  })

  it('cambiar una faceta borra las violaciones y el error anteriores', async () => {
    api.post.mockImplementation((url) => (esPrevio(url)
      ? Promise.resolve({ data: CONTINUABLE })
      : Promise.reject({ response: { status: 429, data: { detail: { code: 'limite_de_activos' } } } })))
    await listo()
    fireEvent.click(botonContinuar())
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.limite_de_activos({}))
    fireEvent.change(selectDe(5, 'generate'), { target: { value: 'ada' } })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})

// Lecciones de la Task 9 (adenda Task 10 regla 7): los dos diálogos son
// portales hermanos y Dialogo sólo vuelve inert a #root.
describe('ContinuarPipelineModal -- diálogo anidado', () => {
  it('doble clic en Continuar antes del re-render no manda dos continuaciones', async () => {
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CONTINUABLE }) : new Promise(() => {})))
    await listo()
    const boton = botonContinuar()
    await act(async () => { boton.click(); boton.click() })
    expect(llamadasA(CONTINUAR)).toHaveLength(1)
  })

  it('doble clic en Confirmar antes del re-render no manda dos continuaciones', async () => {
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CARO }) : new Promise(() => {})))
    await listo()
    fireEvent.click(botonContinuar())
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    const confirmar = within(dialogo).getByRole('button', { name: es.confirmarCostoBoton })
    await act(async () => { confirmar.click(); confirmar.click() })
    await waitFor(() => expect(confirmar).toBeDisabled())
    expect(llamadasA(CONTINUAR)).toHaveLength(1)
  })

  it('con la confirmación abierta la ventana de continuar queda inert y no cambia lo que se manda', async () => {
    mockPrevio(CARO)
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    const padre = screen.getByRole('dialog', { name: es.continuarTitulo })
    const selector = within(padre).getByLabelText(es.continuarPasoACorrer(5, 'generate'))
    expect(selector.closest('[inert]')).not.toBeNull()
    const previosAntes = llamadasA(PREVIO).length
    fireEvent.change(selector, { target: { value: 'ada' } })
    fireEvent.click(within(padre).getByRole('button', { name: es.continuarBoton }))
    fireEvent.click(within(padre).getByRole('button', { name: es.cancel }))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(llamadasA(CONTINUAR)).toHaveLength(1))
    expect(llamadasA(CONTINUAR)[0][1]).toEqual({ reasignar: {}, costo_confirmado_usd: '0.30' })
    expect(llamadasA(PREVIO)).toHaveLength(previosAntes)
  })

  it('mientras se continúa, Cancelar está deshabilitado y Escape no cierra la confirmación', async () => {
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CARO }) : new Promise(() => {})))
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(within(dialogo).getByRole('button', { name: es.cancel })).toBeDisabled())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: es.confirmarCostoTitulo })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('Escape con la confirmación abierta cierra sólo la confirmación', async () => {
    mockPrevio(CARO)
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
    expect(onClose).not.toHaveBeenCalled()
    expect(llamadasA(CONTINUAR)).toHaveLength(0)
  })

  for (const [nombre, cerrar] of [
    ['Cancelar', (d) => fireEvent.click(within(d).getByRole('button', { name: es.cancel }))],
    ['Escape', () => fireEvent.keyDown(document, { key: 'Escape' })],
  ]) {
    it(`al cerrar la confirmación con ${nombre} el foco vuelve a Continuar`, async () => {
      mockPrevio(CARO)
      await listo()
      document.activeElement?.blur?.()
      fireEvent.click(botonContinuar())
      const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
      cerrar(dialogo)
      await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
      await waitFor(() => expect(document.activeElement).toBe(botonContinuar()))
    })
  }
})

describe('ContinuarPipelineModal -- fix round 1', () => {
  // Ítem 1: un continue sin confirmación ya salió; cerrar la ventana no lo frena.
  it('con continue en vuelo sin confirmación, Cancelar está deshabilitado y ni Cancelar ni Escape cierran', async () => {
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CONTINUABLE }) : new Promise(() => {})))
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    const cancelar = screen.getByRole('button', { name: es.cancel })
    await waitFor(() => expect(cancelar).toBeDisabled())
    await act(async () => { cancelar.click() })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: es.continuarTitulo })).toBeInTheDocument()
  })

  // Ítem 2: tras un fallo de red el pipeline pudo haber continuado igual.
  it('si continue falla por red, dice que revises el panel antes de reintentar', async () => {
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CONTINUABLE }) : Promise.reject(new Error('timeout'))))
    await listo()
    fireEvent.click(botonContinuar())
    expect(await screen.findByRole('alert')).toHaveTextContent(es.continuarError)
    expect(screen.getByRole('alert')).not.toHaveTextContent(es.continuarErrorCarga)
  })

  // Ítems 3 y 4: el veredicto a la vista es de la reasignación que lo produjo.
  // Cambiar una faceta y pulsar Continuar en el mismo tick (antes de que React
  // vuelva a renderizar) no continúa con el veredicto de la faceta anterior.
  it('cambiar una faceta y pulsar Continuar enseguida no manda continue', async () => {
    api.post.mockImplementation((url, cuerpo) => (esPrevio(url)
      ? (Object.keys(cuerpo.reasignar).length ? new Promise(() => {}) : Promise.resolve({ data: CONTINUABLE }))
      : Promise.resolve({ data: CONTINUADO })))
    await listo()
    const selector = selectDe(5, 'generate')
    const boton = botonContinuar()
    await act(async () => {
      fireEvent.change(selector, { target: { value: 'ada' } })
      boton.click()
    })
    expect(llamadasA(CONTINUAR)).toHaveLength(0)
    expect(botonContinuar()).toBeDisabled()
  })

  // Ítem 3: siLibre en Continuar. Con la confirmación reabierta con un costo
  // nuevo, un clic en el padre (jsdom no respeta inert) no la reemplaza por el
  // veredicto viejo del pre-vuelo.
  it('con la confirmación reabierta, Continuar del padre no vuelve al costo viejo', async () => {
    const rechazo = { response: { status: 409, data: { detail: {
      code: 'costo_supera_lo_aceptado', costo_max_usd: '0.90', pasos_costo: [],
    } } } }
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CARO }) : Promise.reject(rechazo)))
    await listo()
    fireEvent.click(botonContinuar())
    let dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    await waitFor(() => expect(dialogo).toHaveTextContent(usd('0.90')))
    const padre = screen.getByRole('dialog', { name: es.continuarTitulo })
    await act(async () => { within(padre).getByRole('button', { name: es.continuarBoton }).click() })
    dialogo = screen.getByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(usd('0.90'))
    expect(within(dialogo).getByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
  })

  // Fix round 2 ítem 3: tras un pedido que falla la ventana vuelve a cerrarse.
  it('tras un continue que falla, Cancelar vuelve a estar habilitado y Escape cierra', async () => {
    api.post.mockImplementation((url) => (esPrevio(url) ? Promise.resolve({ data: CONTINUABLE }) : Promise.reject(new Error('timeout'))))
    const { onClose } = await listo()
    fireEvent.click(botonContinuar())
    await screen.findByRole('alert')
    await waitFor(() => expect(screen.getByRole('button', { name: es.cancel })).not.toBeDisabled())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
