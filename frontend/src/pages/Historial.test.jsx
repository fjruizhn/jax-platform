import { render, screen, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import '@testing-library/jest-dom'

// Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): "debería haber una
// forma de ver el resultado del pipeline... un lugar donde se listen los
// pipelines que se hicieron" (pedido textual de Fernando). GET /api/pipelines
// ya existe completo (Task 7): esta pantalla sólo lo lista y, por fila, abre
// el detalle completo (DetallePipeline).
//
// Ronda de arreglo 1 (2026-09-18): el detalle tiene que ser una ruta de
// verdad, /historial/:pipelineId -- los avisos de fin de pipeline (correo y
// Telegram, otras dos tareas de la misma ronda) arman el enlace como
// {origen}/historial/{pipeline_id} y esperan que ABRA ese pipeline. Con
// estado interno (versión anterior), ese enlace caía en la lista sin decir
// cuál era: el mismo problema ("terminó y no supe qué hacer") con más pasos.
vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../api/client'
import Historial from './Historial'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'

const INICIAL = useJaxStore.getState()

// Arnés de rutas: las dos rutas reales de App.jsx (mismo componente,
// Historial.jsx lee :pipelineId con useParams), más un botón que dispara
// navigate(-1) -- lo que el botón "atrás" del navegador también dispara vía
// popstate -- para probar que volver funciona como se espera, no sólo el
// botón "Cerrar" propio de la pantalla.
function BotonVolverDelNavegador() {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>simular-atrás-del-navegador</button>
}
function MuestraRuta() {
  return <div data-testid="ruta">{useLocation().pathname}</div>
}

function renderHistorial(rutaInicial = '/historial') {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/historial', rutaInicial]} initialIndex={1}>
        <BotonVolverDelNavegador />
        <MuestraRuta />
        <Routes>
          <Route path="/historial" element={<Historial />} />
          <Route path="/historial/:pipelineId" element={<Historial />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>
  )
}

const PIPELINES = [
  { pipeline_id: 'p1', name: 'plan de leyes', status: 'completed', created_at: 1758000000, updated_at: 1758000010, causa: null, duracion_s: 10.2, costo_usd: null },
  { pipeline_id: 'p2', name: 'otro plan', status: 'running', created_at: 1758000100, updated_at: 1758000100, causa: null, duracion_s: null, costo_usd: null },
]

const RESULTADO_P1 = { pipeline_id: 'p1', name: 'plan de leyes', status: 'completed', total_duration_seconds: 10.2, steps: [] }

beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't' }, true)
  api.get.mockReset()
})

describe('Historial', () => {
  it('al montar, pide la lista (offset 0) y la muestra', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    expect(await screen.findByText('plan de leyes')).toBeInTheDocument()
    expect(screen.getByText('otro plan')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/pipelines', { params: { offset: 0 } })
  })

  it('muestra el estado traducido de cada pipeline, no el valor crudo', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')
    expect(screen.getByText('Completado')).toBeInTheDocument()
    expect(screen.getByText('En curso')).toBeInTheDocument()
  })

  // Ronda `feat/estado-disputed` (2026-09-18): un pipeline `disputed`
  // terminó con una objeción del árbitro SIN RESOLVER -- ni aprobado ni
  // fallido, pide la decisión de Fernando. Antes de esta ronda no tenía
  // entrada propia en pipelineStatusLabels y caía indistinguible de
  // "Completado" (backend/jax_engine/state.py, antes de mapear disputed a
  // su propio status). Tiene que leerse distinto de los dos, y nunca como
  // el texto genérico "Estado desconocido".
  it('un pipeline disputed se muestra con su propia etiqueta, no como completado ni como desconocido', async () => {
    const disputado = { ...PIPELINES[0], pipeline_id: 'p-disputed', name: 'plan con objeción', status: 'disputed' }
    api.get.mockResolvedValue({ data: { pipelines: [disputado], has_more: false } })
    renderHistorial()
    await screen.findByText('plan con objeción')
    expect(screen.getByText(es.pipelineStatusLabels.disputed)).toBeInTheDocument()
    expect(screen.queryByText('Completado')).not.toBeInTheDocument()
    expect(screen.queryByText(es.pipelineStatusDesconocido)).not.toBeInTheDocument()
    expect(screen.queryByText('disputed')).not.toBeInTheDocument()
  })

  it('duracion_s null se muestra como "desconocido", nunca como 0', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('otro plan')
    const fila = screen.getByText('otro plan').closest('tr')
    const celdaDuracion = fila.querySelector('[data-campo="duracion"]')
    expect(celdaDuracion).toHaveTextContent('desconocido')
    expect(within(fila).queryByText('0')).not.toBeInTheDocument()
  })

  // Menor del revisor (ronda de arreglo 1): duracion_s se imprimía crudo
  // ("10.234s" si el backend manda 3 decimales, round(u-c,3) en
  // pipelines.py). Un decimal, mismo criterio que StepCard.jsx.
  it('duracion_s se formatea a un decimal', async () => {
    api.get.mockResolvedValue({ data: { pipelines: [{ ...PIPELINES[0], duracion_s: 10.234 }], has_more: false } })
    renderHistorial()
    const fila = (await screen.findByText('plan de leyes')).closest('tr')
    const celdaDuracion = fila.querySelector('[data-campo="duracion"]')
    expect(celdaDuracion).toHaveTextContent('10.2s')
  })

  // Menor 5 (revisión final, 2026-09-18): costo_usd null YA NO es "limitación
  // de esquema conocida" -- desde la Task 7b (misma rama) es real cuando hay
  // uso cargado por pipeline_id, y null sólo para pipelines viejos sin ese
  // dato. El fixture (PIPELINES[0]) es uno de esos viejos.
  it('costo_usd sale "desconocido" cuando es null (pipeline sin dato de costo, no se muestra como $0)', async () => {
    api.get.mockResolvedValue({ data: { pipelines: [PIPELINES[0]], has_more: false } })
    renderHistorial()
    const fila = (await screen.findByText('plan de leyes')).closest('tr')
    const celdaCosto = fila.querySelector('[data-campo="costo"]')
    expect(celdaCosto).toHaveTextContent('desconocido')
  })

  it('costo_usd real se muestra en dólares con seis decimales, no como "desconocido"', async () => {
    api.get.mockResolvedValue({ data: { pipelines: [{ ...PIPELINES[0], costo_usd: 0.001234 }], has_more: false } })
    renderHistorial()
    const fila = (await screen.findByText('plan de leyes')).closest('tr')
    const celdaCosto = fila.querySelector('[data-campo="costo"]')
    expect(celdaCosto).toHaveTextContent('$0.001234')
  })

  // Menor 10 (revisión final, 2026-09-18): 's' y '$' salían escritos a mano
  // en el componente (`${...}s`, `$${...}`) en vez de vivir en i18n --
  // único lugar de la pantalla que rompía la regla de cero texto fuera de
  // i18n (el detalle ya usaba detalleStepDuration/detalleTotalDuration).
  it('la duración y el costo pasan por t.historialDuration/t.historialCost, no por un literal armado en el componente', async () => {
    // Espiar la función del diccionario (no sólo comparar el texto renderizado):
    // un literal hardcodeado en el componente puede coincidir por casualidad
    // con lo que la función de i18n devolvería -- lo que prueba que el
    // componente NO tiene el texto escrito a mano es que la llamó.
    const spyDuracion = vi.spyOn(es, 'historialDuration')
    const spyCosto = vi.spyOn(es, 'historialCost')
    api.get.mockResolvedValue({
      data: { pipelines: [{ ...PIPELINES[0], duracion_s: 10.2, costo_usd: 0.001234 }], has_more: false },
    })
    renderHistorial()
    await screen.findByText('plan de leyes')

    expect(spyDuracion).toHaveBeenCalledWith('10.2')
    expect(spyCosto).toHaveBeenCalledWith('0.001234')
    spyDuracion.mockRestore()
    spyCosto.mockRestore()
  })

  it('sin pipelines, muestra el estado vacío', async () => {
    api.get.mockResolvedValue({ data: { pipelines: [], has_more: false } })
    renderHistorial()
    expect(await screen.findByText('Todavía no corriste ningún pipeline.')).toBeInTheDocument()
  })

  it('un fallo de red muestra el error, con un botón para reintentar', async () => {
    api.get.mockRejectedValue(new Error('network'))
    renderHistorial()
    expect(await screen.findByText('No se pudo cargar el historial. Probá de nuevo.')).toBeInTheDocument()

    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    expect(await screen.findByText('plan de leyes')).toBeInTheDocument()
  })

  it('con has_more, "Cargar más" pide la página siguiente y agrega filas', async () => {
    api.get.mockResolvedValueOnce({ data: { pipelines: PIPELINES, has_more: true } })
    renderHistorial()
    await screen.findByText('plan de leyes')

    const pagina2 = [{ pipeline_id: 'p3', name: 'tercero', status: 'completed', created_at: 1758000200, updated_at: 1758000210, causa: null, duracion_s: 10, costo_usd: null }]
    api.get.mockResolvedValueOnce({ data: { pipelines: pagina2, has_more: false } })
    fireEvent.click(screen.getByRole('button', { name: 'Cargar más' }))

    expect(await screen.findByText('tercero')).toBeInTheDocument()
    expect(screen.getByText('plan de leyes')).toBeInTheDocument() // no se pierde lo anterior
    expect(api.get).toHaveBeenLastCalledWith('/pipelines', { params: { offset: 2 } })
  })

  it('sin has_more, no aparece el botón "Cargar más"', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')
    expect(screen.queryByRole('button', { name: 'Cargar más' })).not.toBeInTheDocument()
  })

  it('"Ver detalle" en una fila navega a /historial/:id -- la URL refleja la selección', async () => {
    api.get.mockResolvedValueOnce({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')
    expect(screen.getByTestId('ruta')).toHaveTextContent('/historial')

    api.get.mockResolvedValueOnce({ data: RESULTADO_P1 })
    // 2026-09-22: DetallePipeline pide un TERCER endpoint (auditoría de
    // descarte) junto con /results -- sin este `once`, esa llamada cae en
    // el mock sin configurar (undefined) y `.then` explota.
    api.get.mockResolvedValueOnce({ data: { eventos: [] } })
    const fila = screen.getByText('plan de leyes').closest('tr')
    fireEvent.click(within(fila).getByRole('button', { name: 'Ver detalle' }))

    expect(await screen.findByText('Detalle — plan de leyes')).toBeInTheDocument()
    expect(screen.getByTestId('ruta')).toHaveTextContent('/historial/p1')
    expect(api.get).toHaveBeenCalledWith('/pipelines/p1/results')
  })

  it('entrar directo por la URL con :pipelineId abre ese detalle sin pasar por la lista', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/pipelines') return Promise.resolve({ data: { pipelines: PIPELINES, has_more: false } })
      if (url === '/pipelines/p1/results') return Promise.resolve({ data: RESULTADO_P1 })
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderHistorial('/historial/p1')
    expect(await screen.findByText('Detalle — plan de leyes')).toBeInTheDocument()
    // La lista sigue ahí debajo -- no es una pantalla aparte.
    expect(await screen.findByText('otro plan')).toBeInTheDocument()
  })

  it('cerrar el detalle vuelve a /historial sin recargar la lista', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/pipelines') return Promise.resolve({ data: { pipelines: PIPELINES, has_more: false } })
      if (url === '/pipelines/p1/results') return Promise.resolve({ data: RESULTADO_P1 })
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderHistorial('/historial/p1')
    await screen.findByText('Detalle — plan de leyes')
    const llamadasAntes = api.get.mock.calls.filter((c) => c[0] === '/pipelines').length

    fireEvent.click(screen.getByRole('button', { name: 'Cerrar detalle' }))

    expect(screen.queryByText('Detalle — plan de leyes')).not.toBeInTheDocument()
    expect(screen.getByText('plan de leyes')).toBeInTheDocument()
    expect(screen.getByTestId('ruta')).toHaveTextContent('/historial')
    expect(api.get.mock.calls.filter((c) => c[0] === '/pipelines').length).toBe(llamadasAntes)
  })

  it('el botón "atrás" del navegador (popstate/navigate(-1)) vuelve a la lista', async () => {
    api.get.mockResolvedValueOnce({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')

    api.get.mockResolvedValueOnce({ data: RESULTADO_P1 })
    // 2026-09-22: tercer endpoint que pide DetallePipeline (ver el test de
    // arriba, "Ver detalle").
    api.get.mockResolvedValueOnce({ data: { eventos: [] } })
    const fila = screen.getByText('plan de leyes').closest('tr')
    fireEvent.click(within(fila).getByRole('button', { name: 'Ver detalle' }))
    await screen.findByText('Detalle — plan de leyes')

    fireEvent.click(screen.getByRole('button', { name: 'simular-atrás-del-navegador' }))

    expect(screen.getByTestId('ruta')).toHaveTextContent('/historial')
    expect(screen.queryByText('Detalle — plan de leyes')).not.toBeInTheDocument()
  })

  // Recomendado 3 (revisión final, 2026-09-18): los avisos de fin de pipeline
  // (correo, Telegram) enlazan a /historial/:id, y el detalle se renderizaba
  // DESPUÉS de la tabla completa -- con 600 pipelines había que bajar 50
  // filas para ver el detalle al que el enlace mandaba. El Ruling 15 dice
  // "un aviso que no lleva al resultado repite el problema con más pasos":
  // técnicamente llevaba, en la práctica no. El detalle pasa a renderizarse
  // ANTES de la tabla -- se ve sin scrollear con cualquier volumen, y la
  // lista sigue "ahí debajo" (el comportamiento que los tests de arriba ya
  // fijan, sin pantalla aparte).
  it('el detalle se renderiza ANTES de la tabla, no después: no hace falta bajar para verlo', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/pipelines') return Promise.resolve({ data: { pipelines: PIPELINES, has_more: false } })
      if (url === '/pipelines/p1/results') return Promise.resolve({ data: RESULTADO_P1 })
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderHistorial('/historial/p1')
    const detalle = await screen.findByText('Detalle — plan de leyes')
    const filaDeLaLista = await screen.findByText('otro plan')

    // eslint-disable-next-line no-bitwise
    expect(detalle.compareDocumentPosition(filaDeLaLista) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('un pipelineId ajeno o inexistente (404 del backend) muestra un estado vacío decente, no rompe la pantalla', async () => {
    const error404 = Object.assign(new Error('not found'), { response: { status: 404 } })
    api.get.mockImplementation((url) => {
      if (url === '/pipelines') return Promise.resolve({ data: { pipelines: PIPELINES, has_more: false } })
      if (url === '/pipelines/ajeno/results') return Promise.reject(error404)
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderHistorial('/historial/ajeno')
    expect(await screen.findByText('Ese pipeline no aparece en tu historial.')).toBeInTheDocument()
    // No confirma ni niega que exista -- mismo texto para "no existe" y "es de otro dueño".
    expect(screen.queryByText('No se pudo cargar el detalle de este pipeline.')).not.toBeInTheDocument()
    // La lista de al lado sigue funcionando.
    expect(await screen.findByText('plan de leyes')).toBeInTheDocument()
  })
})
