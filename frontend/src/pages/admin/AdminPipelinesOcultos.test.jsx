import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 7 (2026-09-22, spec descartar-pipelines §5): Administración →
// Pipelines ocultos, sólo superadmin (la guardia del ROL la pone /admin/*
// en App.jsx -- RequireAuth + RequireSuperadmin -- esta pantalla no repite
// esa comprobación). Restaurar es reversible: sin confirmación.
//
// 2026-09-22 (cierre de los dos huecos de la revisión final de Descartar
// Pipelines, punto 1): pestañas "Descartados"/"Ocultos" -- antes esta
// pantalla sólo mostraba los ocultos; el superadmin no tenía forma de VER
// (ni por lo tanto de volver a ocultar) el descartado de OTRO usuario que
// él mismo hubiera restaurado. "Descartados" es la pestaña por defecto
// (arranca ahí sin clic) porque es el hueco nuevo que esta tarea cierra.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminPipelinesOcultos from './AdminPipelinesOcultos'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

const OCULTO = {
  pipeline_id: 'h1', name: 'plan oculto', user_id: 'u-9', tenant_id: 't-1',
  descartado_por: 'admin-1', descartado_at: 1758000700, created_at: 1758000000,
}

const DESCARTADO = {
  pipeline_id: 'd1', name: 'plan descartado', user_id: 'u-7', tenant_id: 't-1',
  descartado_por: 'u-7', descartado_at: 1758000600, created_at: 1758000000,
}

function renderPantalla() {
  return render(<I18nProvider><AdminPipelinesOcultos /></I18nProvider>)
}

function mockGet({ ocultos = [OCULTO], hayMasOcultos = false, descartados = [DESCARTADO], hayMasDescartados = false } = {}) {
  api.get.mockImplementation((url, config) => {
    if (url === '/admin/pipelines/ocultos') return Promise.resolve({ data: { pipelines: ocultos, has_more: hayMasOcultos } })
    if (url === '/admin/pipelines/descartados') return Promise.resolve({ data: { pipelines: descartados, has_more: hayMasDescartados } })
    return Promise.reject(new Error(`url inesperada: ${url}`))
  })
}

async function irAPestanaOcultos() {
  fireEvent.click(screen.getByRole('button', { name: es.pestanaOcultos }))
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/admin/pipelines/ocultos', { params: { limite: 50, offset: 0 } }))
}

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  mockGet()
})

describe('AdminPipelinesOcultos (Task 7 + cierre de huecos 2026-09-22)', () => {
  it('el título ahora nombra las dos vistas', async () => {
    renderPantalla()
    expect(await screen.findByText(es.pipelinesOcultosTitulo)).toBeInTheDocument()
    expect(es.pipelinesOcultosTitulo).toBe('Pipelines descartados y ocultos')
  })

  it('arranca en la pestaña Descartados, con nombre, dueño y fecha de TODOS los usuarios', async () => {
    renderPantalla()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/admin/pipelines/descartados', { params: { limite: 50, offset: 0 } }))
    expect(await screen.findByText('plan descartado')).toBeInTheDocument()
    expect(screen.getByText(es.ocultoDe('u-7'))).toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalledWith('/admin/pipelines/ocultos', expect.anything())
  })

  it('la primera visita a Ocultos trae su propia lista', async () => {
    renderPantalla()
    await screen.findByText('plan descartado')
    await irAPestanaOcultos()
    expect(await screen.findByText('plan oculto')).toBeInTheDocument()
    expect(screen.getByText(es.ocultoDe('u-9'))).toBeInTheDocument()
  })

  // BLOCK-1 (fix round 1, revisión adversarial de PR 151): el test que
  // antes vivía acá se llamaba "sin recargar Descartados" y por construcción
  // NO PODÍA detectar el defecto -- codificaba como comportamiento deseado
  // exactamente lo que el revisor encontró roto ("la lista está vacía" no
  // es la señal de "hay que recargar"). Escenario real que probó el
  // revisor: A y B descartados, C oculto. Se abre Descartados (carga A,B),
  // se va a Ocultos (carga C), se Restaura C, se vuelve a Descartados -> C
  // no aparecía, porque `listaDescartados` NO estaba vacía y la pestaña
  // nunca se marcaba para recargar. Las dos direcciones, cada una visitando
  // la pestaña destino DOS veces (la primera consume la carga inicial; si
  // la SEGUNDA visita no pidiera de nuevo, la aserción de más abajo fallaría
  // igual que fallaba antes del fix).
  it('Ocultar invalida Ocultos: al volver a esa pestaña se pide de nuevo y aparece el recién ocultado', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'hidden' } })
    renderPantalla()
    await screen.findByText('plan descartado')

    // Primera visita a Ocultos -- consume la carga inicial de esa pestaña.
    await irAPestanaOcultos()
    await screen.findByText('plan oculto')
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    const llamadasOcultosAntes = api.get.mock.calls.filter((c) => c[0] === '/admin/pipelines/ocultos').length

    // Ocultar d1 desde Descartados.
    fireEvent.click(screen.getByRole('button', { name: es.ocultarPipeline }))
    const dialogo = screen.getByRole('dialog')
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByLabelText(/Resolvé/), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.ocultarPipeline }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/hide'))

    // Segunda visita a Ocultos: tiene que pedir de nuevo (no confiar en que
    // la lista "ya tenía algo") y mostrar el recién ocultado.
    mockGet({ ocultos: [{ ...OCULTO, pipeline_id: 'd1', name: 'plan descartado', descartado_por: 'admin-1' }] })
    fireEvent.click(screen.getByRole('button', { name: es.pestanaOcultos }))
    await waitFor(() => {
      const llamadas = api.get.mock.calls.filter((c) => c[0] === '/admin/pipelines/ocultos').length
      expect(llamadas).toBe(llamadasOcultosAntes + 1)
    })
    expect(await screen.findByText('plan descartado')).toBeInTheDocument()
  })

  it('Restaurar invalida Descartados: al volver a esa pestaña se pide de nuevo y aparece el recién restaurado', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'h1', status: 'discarded' } })
    renderPantalla()
    await screen.findByText('plan descartado')
    await irAPestanaOcultos()
    await screen.findByText('plan oculto')

    const llamadasDescartadosAntes = api.get.mock.calls.filter((c) => c[0] === '/admin/pipelines/descartados').length

    // Restaurar h1 desde Ocultos -- sin confirmación (reversible).
    fireEvent.click(screen.getByRole('button', { name: es.restaurarPipeline }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/h1/restore'))

    // Segunda visita a Descartados: tiene que pedir de nuevo y mostrar el
    // recién restaurado -- el escenario EXACTO del revisor.
    mockGet({ descartados: [{ ...DESCARTADO, pipeline_id: 'h1', name: 'plan oculto', descartado_por: 'admin-1' }] })
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await waitFor(() => {
      const llamadas = api.get.mock.calls.filter((c) => c[0] === '/admin/pipelines/descartados').length
      expect(llamadas).toBe(llamadasDescartadosAntes + 1)
    })
    expect(await screen.findByText('plan oculto')).toBeInTheDocument()
  })

  // MINOR (fix round 1, revisión adversarial de PR 151): el nombre decía
  // "offset=50" y la aserción de abajo pedía `offset: 1` -- el mock inicial
  // sólo carga UNA fila (DESCARTADO), así que "Cargar más" pide desde
  // offset=length=1, no desde 50 (ese seria el `limite`, no el `offset`).
  it('en Descartados, "Cargar más" pide offset=1 (la cantidad ya cargada)', async () => {
    mockGet({ hayMasDescartados: true })
    renderPantalla()
    await screen.findByText('plan descartado')

    mockGet({ descartados: [{ ...DESCARTADO, pipeline_id: 'd2', name: 'segundo descartado' }] })
    fireEvent.click(screen.getByRole('button', { name: es.cargarMas }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/admin/pipelines/descartados', { params: { limite: 50, offset: 1 } }))
    expect(await screen.findByText('segundo descartado')).toBeInTheDocument()
    expect(screen.getByText('plan descartado')).toBeInTheDocument()
  })

  it('en Descartados, Recuperar llama a /recover y quita la fila, sin pedir confirmación', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'aborted' } })
    renderPantalla()
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/recover'))
    await waitFor(() => expect(screen.queryByText('plan descartado')).not.toBeInTheDocument())
  })

  it('en Descartados, Ocultar pide confirmación con ConfirmacionSuma antes de llamar a /hide', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'hidden' } })
    renderPantalla()
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.ocultarPipeline }))
    const dialogo = screen.getByRole('dialog')
    expect(api.post).not.toHaveBeenCalled()
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByLabelText(/Resolvé/), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.ocultarPipeline }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/hide'))
    await waitFor(() => expect(screen.queryByText('plan descartado')).not.toBeInTheDocument())
  })

  it('si recuperar falla, se muestra el error traducido y la fila se queda', async () => {
    api.post.mockRejectedValue({ response: { status: 403, data: { detail: 'recuperar_no_permitido' } } })
    renderPantalla()
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))

    expect(await screen.findByText(es.erroresMesa.recuperar_no_permitido())).toBeInTheDocument()
    expect(screen.getByText('plan descartado')).toBeInTheDocument()
  })

  it('sin descartados, muestra el estado vacío de la pestaña Descartados', async () => {
    mockGet({ descartados: [] })
    renderPantalla()
    expect(await screen.findByText(es.sinDescartados)).toBeInTheDocument()
  })

  it('si la API de descartados responde 403, se muestra el error traducido', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/admin/pipelines/descartados') return Promise.reject({ response: { status: 403, data: { detail: 'usuario_no_superadmin' } } })
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderPantalla()
    expect(await screen.findByText(es.descartadosError)).toBeInTheDocument()
  })

  it('en Ocultos, Restaurar llama a /restore y quita la fila, sin pedir confirmación', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'h1', status: 'discarded' } })
    renderPantalla()
    await screen.findByText('plan descartado')
    await irAPestanaOcultos()
    await screen.findByText('plan oculto')

    fireEvent.click(screen.getByRole('button', { name: es.restaurarPipeline }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/h1/restore'))
    await waitFor(() => expect(screen.queryByText('plan oculto')).not.toBeInTheDocument())
  })

  it('en Ocultos, si la API responde 403, se muestra el error traducido', async () => {
    api.get.mockImplementation((url) => {
      if (url === '/admin/pipelines/descartados') return Promise.resolve({ data: { pipelines: [], has_more: false } })
      if (url === '/admin/pipelines/ocultos') return Promise.reject({ response: { status: 403, data: { detail: 'usuario_no_superadmin' } } })
      return Promise.reject(new Error(`url inesperada: ${url}`))
    })
    renderPantalla()
    await irAPestanaOcultos()
    expect(await screen.findByText(es.pipelinesOcultosError)).toBeInTheDocument()
  })

  it('en Ocultos, sin ocultos, muestra el estado vacío', async () => {
    mockGet({ ocultos: [] })
    renderPantalla()
    await irAPestanaOcultos()
    expect(await screen.findByText(es.sinOcultos)).toBeInTheDocument()
  })
})
