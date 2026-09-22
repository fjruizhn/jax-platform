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

  // Menor 4 (revisión final, 2026-09-18): un paso de ANTES de que la columna
  // `depends_on` existiera (store.py:1070-1075, agregada Task 1 de esta
  // ronda) no trae la clave -- `undefined`, no `[]`. `[]` es "paralelo
  // explícito" (el plan LO DECIDIÓ así); ausente es "no sé". Antes las dos
  // caían en el mismo texto ("No depende de otro paso"), afirmando un NO
  // sobre un dato que no existe -- el único campo del detalle que hacía
  // eso: modelo_real/costo_usd ausentes ya dicen "desconocido".
  it('depends_on AUSENTE (paso viejo, sin la columna) se muestra "desconocido", no como un NO', async () => {
    const { depends_on, ...pasoSinDependsOn } = RESULTADO.steps[0]
    api.get.mockResolvedValue({ data: { ...RESULTADO, steps: [pasoSinDependsOn] } })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.queryByText('No depende de otro paso')).not.toBeInTheDocument()
    expect(screen.getByText('Dependencias desconocidas')).toBeInTheDocument()
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

  // Ronda de arreglo 2 (2026-09-18): con ~30 fuentes (una investigación de
  // mercado real), la lista se salía del borde de la tarjeta y seguía hasta
  // fuera de la pantalla (captura de Fernando, paso 3). jsdom no hace layout
  // -- no puede medir un desborde real -- así que esto prueba el CONTRATO
  // (el contenedor declara que puede partir en varias líneas), no el pixel.
  // La verificación de que se ve bien se hizo a mano con el dev server (ver
  // REPORTE.md).
  it('con muchas fuentes, el contenedor declara que puede partir en varias líneas (flex-wrap), no una sola línea infinita', async () => {
    const muchas = Array.from({ length: 30 }, (_, i) => ({ title: '', url: `https://dominio${i}.com` }))
    api.get.mockResolvedValue({
      data: { ...RESULTADO, steps: [{ ...RESULTADO.steps[1], sources: muchas }] },
    })
    renderDetalle()
    const primerLink = await screen.findByRole('link', { name: 'https://dominio0.com' })
    const contenedor = primerLink.closest('div')
    expect(contenedor.className).toMatch(/flex-wrap/)
    // Las 30 siguen presentes, ninguna se pierde al envolver.
    expect(screen.getAllByRole('link')).toHaveLength(30)
  })

  // Una URL sin espacios es UN solo token: el wrap normal (que corta en
  // espacios) no la parte -- necesita `break-all` (corta dentro de la
  // palabra) para no desbordar. Sin esto, aunque el contenedor tenga
  // flex-wrap, ESTE ítem seguiría siendo más ancho que la tarjeta.
  it('una URL larguísima sin espacios se puede partir dentro de la palabra (break-all) y no se recorta el texto', async () => {
    const urlLarga = `https://${'sub.'.repeat(20)}dominio-muy-largo.com/una/ruta/tambien/larga/sin/espacios`
    api.get.mockResolvedValue({
      data: { ...RESULTADO, steps: [{ ...RESULTADO.steps[1], sources: [{ title: '', url: urlLarga }] }] },
    })
    renderDetalle()
    const link = await screen.findByRole('link', { name: urlLarga })
    expect(link.className).toMatch(/break-all/)
    // Nada de truncar con "…" ni recortar: el texto completo sigue en el DOM.
    expect(link.textContent).toBe(urlLarga)
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
    // 2026-09-22: ya no es la ÚLTIMA llamada -- desde que se agregó la
    // auditoría, cada cambio de pipelineId dispara DOS pedidos (/results y
    // /auditoria-descarte). Se comprueba que la de /results se hizo, no que
    // sea la más reciente.
    expect(api.get).toHaveBeenCalledWith('/pipelines/p2/results')
  })
})

// 2026-09-22 (cierre de los dos huecos de la revisión final de Descartar
// Pipelines, punto 2): sección de auditoría -- PIPELINE_DISCARDED/RECOVERED/
// HIDDEN/RESTORED en palabras, ya ordenados del más nuevo al más viejo por
// el backend. Si no hay eventos, no se muestra ninguna caja (ni título ni
// borde vacío) -- distinto de "Cargando…", que sólo tapa mientras la
// respuesta está en vuelo.
function mockAuditoria(eventos) {
  api.get.mockImplementation((url) => {
    if (url === '/pipelines/p1/results') return Promise.resolve({ data: RESULTADO })
    if (url === '/pipelines/p1/auditoria-descarte') return Promise.resolve({ data: { eventos } })
    return Promise.reject(new Error(`url inesperada: ${url}`))
  })
}

describe('DetallePipeline > auditoría de descarte', () => {
  it('pide /pipelines/{id}/auditoria-descarte junto con /results', async () => {
    mockAuditoria([])
    renderDetalle()
    await screen.findByText('thot')
    expect(api.get).toHaveBeenCalledWith('/pipelines/p1/auditoria-descarte')
  })

  it('sin eventos, no muestra ninguna caja de auditoría', async () => {
    mockAuditoria([])
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.queryByText('Historial de descarte')).not.toBeInTheDocument()
  })

  it('muestra los cuatro tipos en palabras humanas, con el user_id tal cual viene', async () => {
    mockAuditoria([
      { event_type: 'PIPELINE_RESTORED', user_id: 'admin-9', desde: 'hidden', a: 'discarded', ts: 1758000900 },
      { event_type: 'PIPELINE_HIDDEN', user_id: 'admin-9', desde: 'discarded', a: 'hidden', ts: 1758000800 },
      { event_type: 'PIPELINE_RECOVERED', user_id: 'u-42', desde: 'discarded', a: 'aborted', ts: 1758000700 },
      { event_type: 'PIPELINE_DISCARDED', user_id: 'u-42', desde: 'aborted', a: 'discarded', ts: 1758000600 },
    ])
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.getByText('Historial de descarte')).toBeInTheDocument()
    expect(screen.getByText(/^Restaurado por admin-9 el /)).toBeInTheDocument()
    expect(screen.getByText(/^Ocultado por admin-9 el /)).toBeInTheDocument()
    expect(screen.getByText(/^Recuperado por u-42 el /)).toBeInTheDocument()
    expect(screen.getByText(/^Descartado por u-42 el /)).toBeInTheDocument()
  })

  it('respeta el orden que manda el backend (más nuevo primero)', async () => {
    mockAuditoria([
      { event_type: 'PIPELINE_RECOVERED', user_id: 'u-1', desde: 'discarded', a: 'aborted', ts: 200 },
      { event_type: 'PIPELINE_DISCARDED', user_id: 'u-1', desde: 'aborted', a: 'discarded', ts: 100 },
    ])
    renderDetalle()
    await screen.findByText('thot')
    const items = screen.getAllByText(/^(Descartado|Recuperado|Ocultado|Restaurado) por /)
    expect(items.map((el) => el.textContent)).toEqual([
      expect.stringMatching(/^Recuperado por u-1 el /),
      expect.stringMatching(/^Descartado por u-1 el /),
    ])
  })

  it('si la auditoría falla, no rompe el resto del detalle ni muestra la caja', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/pipelines/p1/results') return Promise.resolve({ data: RESULTADO })
      if (url === '/pipelines/p1/auditoria-descarte') return Promise.reject(new Error('network'))
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderDetalle()
    await screen.findByText('thot')
    expect(screen.queryByText('Historial de descarte')).not.toBeInTheDocument()
  })
})
