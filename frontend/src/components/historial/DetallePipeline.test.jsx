import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): hoy el resultado de
// un pipeline se vuelca al chat y se pierde hacia arriba. DetallePipeline
// muestra, por paso, lo que Fernando pidió ver: faceta, MODELO REAL, PROMPT
// EXACTO, salida completa, duración y de qué pasos dependía -- los tres
// primeros recién los expone jax/jacobs/routes.py::get_pipeline_results tras
// el commit b72002e (prompt/modelo_real/depends_on, antes fuera de la lista
// blanca del dict).
vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import DetallePipeline from './DetallePipeline'
import { I18nProvider } from '../../i18n/index.jsx'

const RESULTADO = {
  pipeline_id: 'p1',
  name: 'plan de leyes',
  status: 'completed',
  total_duration_seconds: 4.5,
  steps: [
    {
      step_index: 0, facet: 'thot', capability: 'analysis', name: 'thot — analysis',
      status: 'completed', prompt: 'resumí este documento', modelo_real: 'glm-4.6',
      depends_on: [], result: 'acá va un resumen', sources: [], duration_seconds: 1.5,
      error: null, result_unavailable: false,
    },
    {
      step_index: 1, facet: 'kimi', capability: 'implementation', name: 'kimi — implementation',
      status: 'completed', prompt: 'implementá lo que dijo el paso 0', modelo_real: 'kimi-k2.7-code',
      depends_on: [0], result: 'listo', sources: [{ title: 'doc', url: 'https://x' }], duration_seconds: 3.0,
      error: null, result_unavailable: false,
    },
  ],
}

function renderDetalle(props = {}) {
  return render(<I18nProvider><DetallePipeline pipelineId="p1" {...props} /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
})

describe('DetallePipeline', () => {
  it('pide /pipelines/{id}/results con el id recibido', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    await screen.findByText('thot')
    expect(api.get).toHaveBeenCalledWith('/pipelines/p1/results')
  })

  it('muestra el estado de carga mientras espera la respuesta', () => {
    api.get.mockReturnValue(new Promise(() => {})) // nunca resuelve
    renderDetalle()
    expect(screen.getByText('Cargando el detalle…')).toBeInTheDocument()
  })

  it('un fallo de red muestra el error, no una pantalla vacía', async () => {
    api.get.mockRejectedValue(new Error('network'))
    renderDetalle()
    expect(await screen.findByText('No se pudo cargar el detalle de este pipeline.')).toBeInTheDocument()
  })

  // Ronda de arreglo 1 (2026-09-18): el backend responde 404 A PROPÓSITO al
  // que no es dueño (_require_pipeline_owner, jax-platform/backend/api/
  // pipelines.py) -- para no confirmarle que el pipeline_id existe. Un id
  // ajeno y uno inexistente dan la MISMA respuesta, así que la pantalla
  // tiene que decir lo mismo para los dos: ni "no existe" ni "no es tuyo",
  // sólo "no aparece en tu historial". Distinto texto del error genérico
  // (que sí puede sugerir reintentar -- un 404 no se arregla reintentando).
  it('un 404 (ajeno o inexistente) muestra el estado vacío, no el error genérico', async () => {
    api.get.mockRejectedValue(Object.assign(new Error('not found'), { response: { status: 404 } }))
    renderDetalle()
    expect(await screen.findByText('Ese pipeline no aparece en tu historial.')).toBeInTheDocument()
    expect(screen.queryByText('No se pudo cargar el detalle de este pipeline.')).not.toBeInTheDocument()
  })

  it('muestra el modelo REAL de cada paso', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    expect(await screen.findByText('glm-4.6')).toBeInTheDocument()
    expect(screen.getByText('kimi-k2.7-code')).toBeInTheDocument()
  })

  it('modelo_real null se muestra como desconocido, no como null crudo', async () => {
    api.get.mockResolvedValue({
      data: { ...RESULTADO, steps: [{ ...RESULTADO.steps[0], modelo_real: null }] },
    })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.getByText('desconocido')).toBeInTheDocument()
    expect(screen.queryByText('null')).not.toBeInTheDocument()
  })

  it('muestra el prompt EXACTO de cada paso (colapsado por default, presente en el DOM)', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.getByText('resumí este documento')).toBeInTheDocument()
    expect(screen.getByText('implementá lo que dijo el paso 0')).toBeInTheDocument()
  })

  it('un paso sin prompt en input muestra el texto de "sin prompt", no undefined', async () => {
    api.get.mockResolvedValue({
      data: { ...RESULTADO, steps: [{ ...RESULTADO.steps[0], prompt: null }] },
    })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.getByText('(sin prompt)')).toBeInTheDocument()
  })

  it('muestra de qué pasos dependía cada paso', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.getByText('No depende de otro paso')).toBeInTheDocument()
    expect(screen.getByText('Depende de: Paso 1')).toBeInTheDocument()
  })

  it('el bloque del prompt y del resultado arrancan colapsados (details cerrado)', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    const { container } = renderDetalle()
    await screen.findByText('thot')
    const detalles = container.querySelectorAll('details')
    expect(detalles.length).toBeGreaterThan(0)
    for (const d of detalles) expect(d.open).toBe(false)
  })

  it('un paso con result_unavailable muestra el motivo, no un resultado vacío disfrazado de completo', async () => {
    api.get.mockResolvedValue({
      data: {
        ...RESULTADO,
        steps: [{ ...RESULTADO.steps[0], result: '', result_unavailable: true, error: 'el resultado de este paso no se pudo leer: boom' }],
      },
    })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.getByText('El resultado de este paso no se pudo leer: el resultado de este paso no se pudo leer: boom')).toBeInTheDocument()
  })

  // Menor del revisor (ronda de arreglo 1): antes se interpolaba el número
  // crudo (round(...,2)/round(...,3) del backend imprime "3s" para un 3.0
  // pero también podría imprimir "10.234s"). Un decimal siempre, mismo
  // criterio que StepCard.jsx.
  it('duración total y por paso se muestran con un decimal; sin datos, "desconocida"', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    expect(await screen.findByText('Duración total: 4.5s')).toBeInTheDocument()
    expect(screen.getByText('1.5s')).toBeInTheDocument()
    expect(screen.getByText('3.0s')).toBeInTheDocument()
  })

  it('sin total_duration_seconds, "Duración total: desconocida"', async () => {
    api.get.mockResolvedValue({ data: { ...RESULTADO, total_duration_seconds: null } })
    renderDetalle()
    expect(await screen.findByText('Duración total: desconocida')).toBeInTheDocument()
  })

  it('las fuentes de un paso se listan', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    const link = await screen.findByRole('link', { name: 'doc' })
    expect(link).toHaveAttribute('href', 'https://x')
  })

  it('onClose (si se pasa) se llama al hacer click en cerrar', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    const onClose = vi.fn()
    renderDetalle({ onClose })
    await screen.findByText('thot')
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar detalle' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('sin onClose no muestra el botón de cerrar', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.queryByRole('button', { name: 'Cerrar detalle' })).not.toBeInTheDocument()
  })

  it('cambiar pipelineId vuelve a pedir el detalle nuevo', async () => {
    api.get.mockResolvedValue({ data: RESULTADO })
    const { rerender } = renderDetalle()
    await screen.findByText('thot')
    api.get.mockResolvedValue({ data: { ...RESULTADO, pipeline_id: 'p2', name: 'otro', steps: [] } })
    rerender(<I18nProvider><DetallePipeline pipelineId="p2" /></I18nProvider>)
    await screen.findByText('Detalle — otro')
    expect(api.get).toHaveBeenLastCalledWith('/pipelines/p2/results')
  })
})
