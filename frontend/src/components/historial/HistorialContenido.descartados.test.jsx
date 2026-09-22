import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 6 (2026-09-22, spec descartar-pipelines §5): pestaña "Descartados" en
// el historial -- Recuperar por fila y, sólo para el superadmin, Borrar
// (ocultar) con ConfirmacionSuma. Los descartados NO comparten el store
// `historial` (spec §2: "van a ser muchos en el tiempo", vista propia).
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import HistorialContenido from './HistorialContenido'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()

const DESCARTADOS = [
  { pipeline_id: 'd1', name: 'plan descartado', status: 'discarded', descartado_at: 1758000500, costo_usd: 0.01 },
]

function renderCuerpo(extra = {}) {
  return render(
    <I18nProvider>
      <HistorialContenido pipelineId={null} nombreSeleccionado={null}
        onSelect={vi.fn()} onCloseDetail={vi.fn()} {...extra} />
    </I18nProvider>
  )
}

function mockGet({ todos = [], discarded = DESCARTADOS, hayMasDescartados = false } = {}) {
  api.get.mockImplementation((url, config) => {
    if (url !== '/pipelines') return Promise.reject(new Error(`url inesperada: ${url}`))
    if (config?.params?.estado === 'discarded') {
      return Promise.resolve({ data: { pipelines: discarded, has_more: hayMasDescartados } })
    }
    return Promise.resolve({ data: { pipelines: todos, has_more: false } })
  })
}

beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: '1', role: 'viewer' } }, true)
  api.get.mockReset()
  api.post.mockReset()
  mockGet()
})

describe('HistorialContenido -- pestaña Descartados (Task 6)', () => {
  it('hay dos pestañas y "Todos" es la inicial', async () => {
    renderCuerpo()
    expect(screen.getByRole('button', { name: es.pestanaTodos })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.pestanaDescartados })).toBeInTheDocument()
    // "Todos" es la inicial: pide la lista normal (store historial), no la de descartados.
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', { params: { offset: 0 } }))
    expect(api.get).not.toHaveBeenCalledWith('/pipelines', { params: { estado: 'discarded', limite: 50, offset: 0 } })
  })

  it('"Descartados" pide GET /pipelines?estado=discarded&limite=50&offset=0 y lista con su fecha de descarte', async () => {
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', {
      params: { estado: 'discarded', limite: 50, offset: 0 },
    }))
    expect(await screen.findByText('plan descartado')).toBeInTheDocument()
    const fila = screen.getByText('plan descartado').closest('tr')
    const celdaFecha = within(fila).getByText((_, el) => el?.getAttribute('data-campo') === 'descartado')
    expect(celdaFecha).not.toHaveTextContent('—')
  })

  // Fix round 2 (MINOR-2 de la re-revisión): el nombre decía "pide offset=50"
  // pero la página sembrada acá tiene 1 elemento -- lo que de verdad prueba
  // es que el offset es la CANTIDAD YA CARGADA (1), no un 50 fijo. El caso
  // con una página completa de 50 (offset real = 50) está abajo, aparte.
  it('con has_more, "Cargar más" pide el offset = cantidad ya cargada, y agrega sin perder lo anterior', async () => {
    mockGet({ hayMasDescartados: true })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    mockGet({ discarded: [{ pipeline_id: 'd2', name: 'segundo', status: 'discarded', descartado_at: 1758000600, costo_usd: null }], hayMasDescartados: false })
    fireEvent.click(screen.getByRole('button', { name: es.cargarMas }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', {
      params: { estado: 'discarded', limite: 50, offset: 1 },
    }))
    expect(await screen.findByText('segundo')).toBeInTheDocument()
    expect(screen.getByText('plan descartado')).toBeInTheDocument() // no se pierde lo anterior
  })

  it('Recuperar llama a recover y quita la fila', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'aborted' } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/recover'))
    await waitFor(() => expect(screen.queryByText('plan descartado')).not.toBeInTheDocument())
  })

  it('Borrar aparece solo para el superadmin, y al confirmar llama a hide', async () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'hidden' } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.borrarPipeline }))
    const dialogo = screen.getByRole('dialog')
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByLabelText(/Resolvé/), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.borrarPipeline }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/hide'))
    await waitFor(() => expect(screen.queryByText('plan descartado')).not.toBeInTheDocument())
  })

  it('un usuario que no es superadmin no ve "Borrar"', async () => {
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')
    expect(screen.queryByRole('button', { name: es.borrarPipeline })).not.toBeInTheDocument()
  })

  it('la lista vacía muestra el estado vacío', async () => {
    mockGet({ discarded: [] })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    expect(await screen.findByText(es.sinDescartados)).toBeInTheDocument()
  })

  // MAJOR-1 (fix round 1, revisión adversarial): recuperar() sólo quitaba la
  // fila local -- el store `historial` ("Todos") sólo se pide una vez al
  // montar, así que un pipeline recién recuperado no aparecía ahí hasta
  // alguna otra recarga. cargarHistorial() dentro de recuperar() lo arregla.
  it('recuperar refresca "Todos" -- el pipeline aparece ahí después (MAJOR-1)', async () => {
    let todos = []
    api.get.mockImplementation((url, config) => {
      if (url !== '/pipelines') return Promise.reject(new Error(`url inesperada: ${url}`))
      if (config?.params?.estado === 'discarded') {
        return Promise.resolve({ data: { pipelines: DESCARTADOS, has_more: false } })
      }
      return Promise.resolve({ data: { pipelines: todos, has_more: false } })
    })
    api.post.mockImplementation(async () => {
      todos = [{ pipeline_id: 'd1', name: 'plan descartado', status: 'aborted', created_at: 1758000000, updated_at: 1758000000, causa: null, duracion_s: null, costo_usd: null }]
      return { data: { pipeline_id: 'd1', status: 'aborted' } }
    })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/recover'))

    fireEvent.click(screen.getByRole('button', { name: es.pestanaTodos }))
    expect(await screen.findByText('plan descartado')).toBeInTheDocument()
  })

  // MINOR-4: error de Recuperar traducido, con texto genérico PROPIO
  // (recuperarError, no descartarError) -- y la fila sigue en la lista.
  it('recuperar_no_permitido se muestra traducido y la fila sigue', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'recuperar_no_permitido' } } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))

    expect(await screen.findByText(es.erroresMesa.recuperar_no_permitido())).toBeInTheDocument()
    expect(screen.getByText('plan descartado')).toBeInTheDocument()
  })

  // Fix round 2 (MINOR-1 de la re-revisión): el test de arriba
  // (recuperar_no_permitido) usa un error CON código -- textoDeErrorDeMesa
  // lo traduce directo y nunca toca el fallback genérico, así que una
  // mutación `recuperarError -> descartarError` en el catch de recuperar()
  // pasaba desapercibida (13 passed igual). Este test usa un error SIN
  // código (como el de Borrar) para forzar el fallback genérico de verdad.
  it('un fallo de Recuperar SIN código muestra el texto genérico propio (recuperarError), no el de Descartar', async () => {
    api.post.mockRejectedValue(new Error('502'))
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))

    expect(await screen.findByText(es.recuperarError)).toBeInTheDocument()
    expect(screen.queryByText(es.descartarError)).not.toBeInTheDocument()
  })

  // MINOR-4: un fallo de Borrar muestra su propio texto genérico (borrarError).
  it('un fallo de Borrar muestra el error traducido con texto propio, no el de Descartar', async () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    api.post.mockRejectedValue(new Error('502'))
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.borrarPipeline }))
    const dialogo = screen.getByRole('dialog')
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByLabelText(/Resolvé/), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.borrarPipeline }))

    expect(await screen.findByText(es.borrarError)).toBeInTheDocument()
    expect(screen.queryByText(es.descartarError)).not.toBeInTheDocument()
  })

  // MINOR-1: errorAccion no puede sobrevivir al cambio de pestaña ni
  // contaminar la acción siguiente.
  it('el error de una acción se limpia al cambiar de pestaña', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'recuperar_no_permitido' } } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')
    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))
    await screen.findByText(es.erroresMesa.recuperar_no_permitido())

    fireEvent.click(screen.getByRole('button', { name: es.pestanaTodos }))
    // La sección "Descartados" (con su banner de error) desaparece del DOM
    // al cambiar de pestaña de cualquier forma -- lo que prueba que el
    // ESTADO se limpió (no sólo que la sección está oculta) es volver a
    // "Descartados" y comprobar que el error NO reaparece.
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))

    expect(screen.queryByText(es.erroresMesa.recuperar_no_permitido())).not.toBeInTheDocument()
  })

  it('el error de una acción se limpia al empezar otra', async () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    api.post.mockRejectedValue({ response: { data: { detail: 'recuperar_no_permitido' } } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')
    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))
    await screen.findByText(es.erroresMesa.recuperar_no_permitido())

    fireEvent.click(screen.getByRole('button', { name: es.borrarPipeline }))

    expect(screen.queryByText(es.erroresMesa.recuperar_no_permitido())).not.toBeInTheDocument()
  })

  // MINOR-6: el caso real del brief -- una página COMPLETA de 50, no 1.
  it('con una página completa de 50, "Cargar más" pide offset=50', async () => {
    const pagina = Array.from({ length: 50 }, (_, i) => ({
      pipeline_id: `d${i}`, name: `plan ${i}`, status: 'discarded', descartado_at: 1758000000 + i, costo_usd: null,
    }))
    mockGet({ discarded: pagina, hayMasDescartados: true })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan 0')

    mockGet({ discarded: [{ pipeline_id: 'd50', name: 'plan 50', status: 'discarded', descartado_at: 1758000900, costo_usd: null }], hayMasDescartados: false })
    fireEvent.click(screen.getByRole('button', { name: es.cargarMas }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', {
      params: { estado: 'discarded', limite: 50, offset: 50 },
    }))
    expect(await screen.findByText('plan 50')).toBeInTheDocument()
  })
})
