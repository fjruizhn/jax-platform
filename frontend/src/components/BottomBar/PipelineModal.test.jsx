import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// R4 -- el picker de motor (kimi/jax_local) se puebla desde
// GET /api/motors/capabilities via la instancia axios de src/api/client.js
// (no fetch() crudo) -- es la unica forma en que el interceptor inyecta
// Authorization: Bearer <token> desde el store (el JWT vive solo en
// memoria, nunca en cookie -- ver useJaxStore.js). Mockear la instancia
// real en vez de global.fetch es deliberado: un mock de fetch no habria
// detectado que el componente usaba el camino sin auth.
vi.mock('../../api/client', () => ({
  default: {
    get: vi.fn(),
  },
}))

import api from '../../api/client'
import PipelineModal from './PipelineModal'
import { I18nProvider } from '../../i18n/index.jsx'

// La cadena es la forma por defecto desde 2026-09-12. Los tests del picker
// en paralelo (los de abajo) cambian de forma explícitamente al renderizar.
function renderModal(props = {}, { layout = 'parallel' } = {}) {
  const result = render(
    <I18nProvider>
      <PipelineModal
        objective="probar el picker de motor"
        onClose={() => {}}
        onSubmit={() => Promise.resolve()}
        {...props}
      />
    </I18nProvider>
  )
  if (layout === 'parallel') fireEvent.click(screen.getByText(/En paralelo/i))
  return result
}

describe('PipelineModal -- cadena en línea', () => {
  it('la cadena es la forma por defecto y manda 6 pasos encadenados por depends_on', async () => {
    let submitted = null
    renderModal({ onSubmit: (p) => { submitted = p; return Promise.resolve() } }, { layout: 'chain' })
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    expect(submitted.steps.map(s => s.capability)).toEqual([
      'research', 'design', 'critique', 'reconcile', 'generate', 'validate_consistency',
    ])
    expect(submitted.steps.map(s => s.depends_on)).toEqual([[], [0], [0, 1], [1, 2], [3], [0, 2, 3, 4]])
    expect(submitted.max_steps).toBe(6)
    expect(submitted.steps[4]).toMatchObject({ facet: 'kimi', motor: 'kimi' })
    submitted.steps.forEach(s => expect(s).not.toHaveProperty('timeout_seconds'))
  })

  it('la cadena corre en autonomous por defecto; paralelo conserva supervised', async () => {
    let chain = null
    const r1 = renderModal({ onSubmit: (p) => { chain = p; return Promise.resolve() } }, { layout: 'chain' })
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await waitFor(() => expect(chain).not.toBeNull())
    expect(chain.mode).toBe('autonomous')
    r1.unmount()

    let parallel = null
    renderModal({ onSubmit: (p) => { parallel = p; return Promise.resolve() } })  // cambia a paralelo
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Razonamiento local/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await waitFor(() => expect(parallel).not.toBeNull())
    expect(parallel.mode).toBe('supervised')
  })

  it('si el usuario elige un modo, cambiar de forma no se lo pisa', async () => {
    let submitted = null
    renderModal({ onSubmit: (p) => { submitted = p; return Promise.resolve() } }, { layout: 'chain' })
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    fireEvent.click(screen.getByText(/Supervised/))
    fireEvent.click(screen.getByText(/En paralelo/i))
    fireEvent.click(screen.getByText(/En cadena/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    expect(submitted.mode).toBe('supervised')
  })

  it('si el auditor coincide con quien produjo, avisa y no deja enviar', async () => {
    renderModal({}, { layout: 'chain' })
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    fireEvent.change(screen.getByLabelText('Producir'), { target: { value: 'thot' } })

    expect(screen.getByText(/no puede auditar lo que produjo/i)).toBeInTheDocument()
    expect(screen.getByText(/Planificar y ejecutar/i)).toBeDisabled()
  })

  it('ofrece un motor en un paso solo si capability_motor lo permite', async () => {
    renderModal({}, { layout: 'chain' })
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    const research = screen.getByLabelText('Investigar')
    const produce = screen.getByLabelText('Producir')
    const values = (sel) => Array.from(sel.querySelectorAll('option')).map(o => o.value)
    expect(values(research)).not.toContain('kimi')
    expect(values(produce)).toContain('kimi')
  })
})

// T5 (2026-08-22, diagnóstico pipeline 19ad2c42-cdf): el mock ahora incluye
// "motors" -- el endpoint real (T1) lo agrega junto a "capabilities". kimi
// SIN has_tool_access, jax_local CON -- exactamente la asimetría real de
// jax_memory hoy.
function mockCatalog({ kimiHasTools = false, jaxLocalHasTools = true } = {}) {
  api.get.mockResolvedValue({
    data: {
      capabilities: [
        { key: 'file_write', allowed_motors: ['jax_local'] },
        { key: 'generate', allowed_motors: ['kimi', 'ada', 'jax_local'] },
      ],
      motors: [
        { key: 'ada', has_tool_access: false },
        { key: 'jax_local', has_tool_access: jaxLocalHasTools },
        { key: 'kimi', has_tool_access: kimiHasTools },
        { key: 'thot', has_tool_access: false },
      ],
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  mockCatalog()
})

describe('PipelineModal -- picker de motor (R4 + T5)', () => {
  it('pide el catalogo por la instancia axios autenticada, no fetch crudo', async () => {
    renderModal()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/motors/capabilities'))
  })

  it('muestra el <select> de motor para Kimi tras seleccionarlo, poblado desde /api/motors/capabilities', async () => {
    renderModal()
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Implementación técnica/i))

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument()
    })
  })

  it('no muestra select de motor para facetas no gobernadas (thot, seleccionado por default)', async () => {
    renderModal()

    // Selección inicial (hipatia/jekyll/thot) no incluye ninguna faceta
    // gobernada -- ningun <select> debe aparecer aunque el catalogo ya
    // haya cargado.
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/motors/capabilities'))

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  // T5: el bug real encontrado en la verificación de T1-T3 -- un step
  // etiquetado "jax_local" se ejecutó contra kimi porque motor quedaba sin
  // fijar y MotorPolicy._resolve_motor(None, cap) resuelve por prioridad
  // GLOBAL de capability_motor, ignorando el facet. El checkbox debe
  // garantizar el motor que dice, no delegar en la política de competencia.
  it('fija motor=facet automáticamente para facetas gobernadas sin que el usuario toque el select', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Razonamiento local/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const jaxLocalStep = submitted.steps.find(s => s.facet === 'jax_local')
    expect(jaxLocalStep.motor).toBe('jax_local')
  })

  // Pipeline b8f80733 (2026-09-12): cada step viajaba con
  // timeout_seconds=300 fijo desde acá, pisando el techo por capability de
  // la DB (capability.max_execution_minutes) -- la fuente única decidida el
  // 2026-09-01. Sin la clave, Jacobs aplica el valor de la DB.
  it('no fija timeout_seconds: el techo por capability lo pone la DB', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Implementación técnica/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    expect(submitted.steps.length).toBeGreaterThan(0)
    for (const step of submitted.steps) {
      expect(step).not.toHaveProperty('timeout_seconds')
    }
  })

  it('el select de motor, si el usuario elige explícito, sigue pisando el default', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Implementación técnica/i))
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ada' } })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const kimiStep = submitted.steps.find(s => s.facet === 'kimi')
    expect(kimiStep.motor).toBe('ada')
  })

  // T5 precisión 1: implementation/code_patch.v1 es un callejón sin salida
  // en este picker plano (sin depends_on, sin reconcile/assemble). La
  // capability real que se pide depende de has_tool_access, no de un mapa
  // fijo -- file_write si el motor puede ejecutar tools (autocontenido),
  // generate si no (no promete escribir nada que no puede).
  it('pide file_write para jax_local (tiene has_tool_access)', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Razonamiento local/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const step = submitted.steps.find(s => s.facet === 'jax_local')
    expect(step.capability).toBe('file_write')
  })

  it('pide generate para kimi (sin has_tool_access) -- no implementation, no promete un patch que nadie aplica', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Implementación técnica/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const step = submitted.steps.find(s => s.facet === 'kimi')
    expect(step.capability).toBe('generate')
  })

  it('si kimi gana has_tool_access en el futuro, pide file_write como cualquier motor gobernado', async () => {
    mockCatalog({ kimiHasTools: true })
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    fireEvent.click(screen.getByText(/Implementación técnica/i))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const step = submitted.steps.find(s => s.facet === 'kimi')
    expect(step.capability).toBe('file_write')
  })

  // T5: fail-closed -- si el catálogo no cargó, no se arma ningún plan.
  // Nada de fallback silencioso a un mapa hardcodeado (ese es el bug que
  // causó el incidente: PipelineModal pedía el dato real y lo descartaba).
  it('fail-closed: si /motors/capabilities falla, Planificar y ejecutar queda deshabilitado', async () => {
    api.get.mockRejectedValue(new Error('401'))
    renderModal()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    fireEvent.click(screen.getByText(/Razonamiento local/i))

    expect(screen.getByText(/Planificar y ejecutar/i).closest('button')).toBeDisabled()
  })

  it('fail-closed: mientras el catálogo está cargando, Planificar y ejecutar queda deshabilitado', () => {
    api.get.mockReturnValue(new Promise(() => {}))  // nunca resuelve
    renderModal()

    expect(screen.getByText(/Planificar y ejecutar/i).closest('button')).toBeDisabled()
  })

  it('con el catálogo cargado ok, Planificar y ejecutar se habilita con una faceta seleccionada', async () => {
    renderModal()
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    expect(screen.getByText(/Planificar y ejecutar/i).closest('button')).not.toBeDisabled()
  })
})
