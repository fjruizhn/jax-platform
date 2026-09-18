import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
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
    post: vi.fn(),
  },
}))

import api from '../../api/client'
import PipelineModal from './PipelineModal'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'
import { formatearUsd } from '../../lib/moneda'
import { textoDeViolacion, textoDeMotivoDeCosto } from '../../api/errores'

// toHaveTextContent colapsa los espacios del DOM (incluido el espacio duro que
// Intl pone entre "USD" y el número en es-HN) pero no los del esperado.
const usd = (monto) => formatearUsd(monto, 'es').replace(/\s+/g, ' ')
const texto = (s) => s.replace(/\s+/g, ' ')

// Pre-vuelo aprobado y barato: el camino de todos los tests viejos.
const VEREDICTO_OK = {
  ok: true, violaciones: [], costo_max_usd: '0.10', umbral_usd: '0.50', requiere_confirmacion: false,
  pasos_costo: [{ paso: 0, faceta: 'hipatia', usd_max: '0.10', motivo: 'acotado' }],
}

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
    // audit ya no depende de unify (paso 3, fix bloqueante 2026-09-18): ver
    // el comentario de CHAIN_ROLES en pipelineChain.js.
    expect(submitted.steps.map(s => s.depends_on)).toEqual([[], [0], [0, 1], [1, 2], [3], [0, 2, 4]])
    expect(submitted.max_steps).toBe(6)
    expect(submitted.steps[4]).toMatchObject({ facet: 'kimi', motor: 'kimi' })
    submitted.steps.forEach(s => expect(s).not.toHaveProperty('timeout_seconds'))
  })

  // DEUDA.md (anotados b8f80733): el nombre llevaba `Pipeline: ` escrito en
  // el componente. Sale de i18n, en las dos formas de armar el pipeline.
  it('el nombre del pipeline sale de i18n, no de un prefijo fijo', async () => {
    expect(en.pipelineName, 'en.pipelineName').toBeTypeOf('function')
    // Un spy y no una comparación de texto: el de es.js coincide con el viejo
    // prefijo fijo, así que comparar solo el texto pasaría con el código viejo.
    const spy = vi.spyOn(es, 'pipelineName').mockReturnValue('NOMBRE-DESDE-I18N')
    try {
      for (const layout of ['chain', 'parallel']) {
        let submitted = null
        const { unmount } = renderModal({ onSubmit: (p) => { submitted = p; return Promise.resolve() } }, { layout })
        await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
        fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
        await waitFor(() => expect(submitted).not.toBeNull())
        expect(submitted.name).toBe('NOMBRE-DESDE-I18N')
        unmount()
      }
      expect(spy).toHaveBeenCalledWith('probar el picker de motor')
    } finally {
      spy.mockRestore()
    }
  })

  // tanda A (2026-09-14): invoked_by es un rol que pone el backend
  // (api/pipelines.py); el cliente no lo declara, en ninguna de las dos formas.
  it('no manda invoked_by en ninguna de las dos formas', async () => {
    for (const layout of ['chain', 'parallel']) {
      let submitted = null
      const { unmount } = renderModal({ onSubmit: (p) => { submitted = p; return Promise.resolve() } }, { layout })
      await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
      fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
      await waitFor(() => expect(submitted).not.toBeNull())
      expect(submitted).not.toHaveProperty('invoked_by')
      unmount()
    }
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

    fireEvent.click(screen.getByText(es.pipelineModeSupervised))
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

    // El auditor por defecto es ada (2026-09-18: thot quedó reservado al
    // árbitro, ver el describe de abajo) -- para reproducir la colisión
    // vieja de cleanroom hace falta que Producir use la MISMA faceta.
    fireEvent.change(screen.getByLabelText('Producir'), { target: { value: 'ada' } })

    expect(screen.getByText(/no puede auditar lo que produjo/i)).toBeInTheDocument()
    expect(screen.getByText(/Planificar y ejecutar/i)).toBeDisabled()
  })

  it('la faceta árbitro (thot) no puede ser productora: avisa incondicionalmente y no deja enviar', async () => {
    // Bloqueante 2026-09-18: el servidor agrega thot SOLO, al final, como
    // árbitro -- si ya aparece como productor en cualquier rol, rechaza el
    // plan entero (422), sin importar capability ni depends_on. 'Investigar'
    // no audita nada y no depende de nadie: el cleanroom viejo lo dejaría
    // pasar. Esta regla no.
    renderModal({}, { layout: 'chain' })
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    fireEvent.change(screen.getByLabelText('Investigar'), { target: { value: 'thot' } })

    expect(screen.getByText(/está reservada para el árbitro/i)).toBeInTheDocument()
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
  api.post.mockResolvedValue({ data: VEREDICTO_OK })
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

  it('no muestra select de motor para facetas no gobernadas (ada, seleccionado por default)', async () => {
    renderModal()

    // Selección inicial (hipatia/jekyll/ada -- 2026-09-18: ya no thot, ver
    // el describe de la sala limpia del árbitro) no incluye ninguna faceta
    // gobernada -- ningun <select> debe aparecer aunque el catalogo ya
    // haya cargado.
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/motors/capabilities'))

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('en paralelo, elegir la faceta árbitro (thot) avisa y no deja enviar', async () => {
    // Mismo bloqueante que en cadena (ver describe de arriba), pero en
    // paralelo no hay roles ni depends_on: la faceta elegida ES el
    // productor directo, así que alcanza con marcarla.
    renderModal()
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    fireEvent.click(screen.getByText(/Auditoría crítica/i))  // desc de thot

    expect(screen.getByText(/está reservada para el árbitro/i)).toBeInTheDocument()
    expect(screen.getByText(/Planificar y ejecutar/i)).toBeDisabled()
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

// I-1 (revisión final PR 3, 2026-09-14): las etiquetas de modo (Supervised/
// Autonomous/Dry run) estaban hardcodeadas en inglés dentro del componente,
// sin pasar por i18n -- se veían en inglés aunque la interfaz estuviera en
// español.
describe('PipelineModal -- etiquetas de modo desde i18n (I-1)', () => {
  it('las claves existen, no están vacías y difieren entre es y en', () => {
    for (const clave of ['pipelineModeSupervised', 'pipelineModeAutonomous', 'pipelineModeDryRun']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(es[clave], `${clave} debería diferir entre es y en`).not.toBe(en[clave])
    }
  })

  it('en español muestra el texto de es.js, no el literal en inglés', async () => {
    renderModal({}, { layout: 'chain' })
    await waitFor(() => expect(api.get).toHaveBeenCalled())

    expect(screen.getByText(es.pipelineModeSupervised)).toBeInTheDocument()
    expect(screen.getByText(es.pipelineModeAutonomous)).toBeInTheDocument()
    expect(screen.getByText(es.pipelineModeDryRun)).toBeInTheDocument()
    expect(screen.queryByText('👁 Supervised')).not.toBeInTheDocument()
    expect(screen.queryByText('⚡ Autonomous')).not.toBeInTheDocument()
    expect(screen.queryByText('🧪 Dry run')).not.toBeInTheDocument()
  })
})

describe('PipelineModal -- es un Dialogo (A-23)', () => {
  it('diálogo modal con nombre; el clic en el fondo no cierra y Escape sí', async () => {
    api.get.mockResolvedValue({ data: { capabilities: [], motors: [] } })
    const onClose = vi.fn()
    render(<I18nProvider><PipelineModal objective="x" onClose={onClose} onSubmit={() => Promise.resolve()} /></I18nProvider>)
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveAccessibleName(es.newPipelineTitle)
    fireEvent.click(dialogo.parentElement)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

// Spec 2026-09-17 §6.2: nada se crea sin pre-vuelo; violaciones y rechazos se
// quedan DENTRO del modal; la confirmación de costo va en ventana propia.
describe('PipelineModal -- pre-vuelo y confirmación de costo', () => {
  const CARO = {
    ...VEREDICTO_OK, costo_max_usd: '0.60', requiere_confirmacion: true,
    pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.60', motivo: 'acotado' }],
  }

  async function listo(props = {}) {
    renderModal(props, { layout: 'chain' })
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
  }

  it('pregunta el pre-vuelo con los mismos pasos y, sin confirmación, crea sin costo_confirmado_usd', async () => {
    const onSubmit = vi.fn(() => Promise.resolve())
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const [url, cuerpo] = api.post.mock.calls[0]
    expect(url).toBe('/pipelines/preflight')
    expect(cuerpo.steps).toEqual(onSubmit.mock.calls[0][0].steps)
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('costo_confirmado_usd')
  })

  // Revisión final, crítico 1: Jacobs cuenta el objetivo en el costo; el
  // pre-vuelo tiene que llevar el MISMO objetivo que la creación.
  it('el pre-vuelo manda el mismo objective que la creación', async () => {
    const onSubmit = vi.fn(() => Promise.resolve())
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const [, cuerpo] = api.post.mock.calls[0]
    expect(cuerpo.objective).toBe('probar el picker de motor')
    expect(cuerpo.objective).toBe(onSubmit.mock.calls[0][0].objective)
  })

  it('con violaciones las muestra en el modal, no crea y no cierra', async () => {
    const v = { paso: 4, faceta: 'kimi', regla: 'tope_insuficiente', detalle: 'tope 8000 < 16384' }
    api.post.mockResolvedValue({ data: { ...VEREDICTO_OK, ok: false, violaciones: [v] } })
    const onSubmit = vi.fn()
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled()
  })

  it('por encima del umbral pide confirmación en ventana propia y crea con el costo confirmado', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn(() => Promise.resolve())
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(usd('0.60'))
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0].costo_confirmado_usd).toBe('0.60')
  })

  it('cancelar la confirmación no crea y deja el modal abierto', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn()
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.cancel }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
    expect(screen.getByRole('dialog', { name: es.newPipelineTitle })).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('Escape con la confirmación abierta cierra sólo la confirmación (DV-12)', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onClose = vi.fn()
    await listo({ onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: es.newPipelineTitle })).toBeInTheDocument()
  })

  // Adenda Task 9 ítems 1 y 2: lugar del paso con el mismo helper que las
  // violaciones, y el motivo traducido, nunca el código crudo.
  it('un paso sin costo acotado se avisa con su motivo traducido', async () => {
    const paso = { paso: 1, faceta: 'ada', usd_max: null, motivo: 'herramientas_sin_tope' }
    api.post.mockResolvedValue({ data: { ...CARO, pasos_costo: [paso] } })
    await listo()
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(es.confirmarCostoNoAcotado)
    expect(dialogo).toHaveTextContent(es.confirmarCostoPasoNoAcotado(paso))
    expect(dialogo).toHaveTextContent('Paso 2 (ada)')
    expect(dialogo).toHaveTextContent(textoDeMotivoDeCosto(es, 'herramientas_sin_tope'))
    expect(dialogo).not.toHaveTextContent('herramientas_sin_tope')
  })

  it('una faceta que no es texto no llega a la pantalla', async () => {
    const paso = { paso: 'x', faceta: { raro: 1 }, usd_max: '0.60', motivo: 'acotado' }
    api.post.mockResolvedValue({ data: { ...CARO, pasos_costo: [paso] } })
    await listo()
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(texto(es.confirmarCostoPaso(paso, formatearUsd('0.60', 'es'))))
    expect(dialogo).toHaveTextContent('Un paso')
    expect(dialogo).not.toHaveTextContent('[object Object]')
  })

  it('si la creación falla, el error se ve en el modal y se puede reintentar', async () => {
    const rechazo = { response: { status: 429, data: { detail: { code: 'limite_de_activos', detalle: 'tope' } } } }
    const onSubmit = vi.fn(() => Promise.reject(rechazo))
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.erroresMesa.limite_de_activos({}))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled()
  })

  // Adenda ítem 4: el costo subió entre el pre-vuelo y la creación.
  it('409 costo_supera_lo_aceptado reabre la confirmación con el costo nuevo y lo manda al confirmar', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const rechazo = { response: { status: 409, data: { detail: {
      code: 'costo_supera_lo_aceptado', costo_max_usd: '0.90', costo_max_aceptado_usd: '0.60',
      pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.90', motivo: 'acotado' }],
    } } } }
    const onSubmit = vi.fn().mockRejectedValueOnce(rechazo).mockResolvedValueOnce()
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    let dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    await waitFor(() => expect(dialogo).toHaveTextContent(usd('0.90')))
    expect(within(dialogo).getByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2))
    expect(onSubmit.mock.calls[1][0].costo_confirmado_usd).toBe('0.90')
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  // Revisión final, menor 6b.
  it('409 confirmacion_de_costo DESPUÉS de confirmar, con un costo mayor, reabre con el aviso de que subió', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const rechazo = { response: { status: 409, data: { detail: {
      code: 'confirmacion_de_costo', costo_max_usd: '0.90', umbral_usd: '0.50',
      pasos_costo: [{ paso: 4, faceta: 'kimi', usd_max: '0.90', motivo: 'acotado' }],
    } } } }
    const onSubmit = vi.fn().mockRejectedValueOnce(rechazo).mockResolvedValueOnce()
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    let dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    await waitFor(() => expect(dialogo).toHaveTextContent(usd('0.90')))
    expect(within(dialogo).getByRole('alert')).toHaveTextContent(es.erroresMesa.costo_supera_lo_aceptado({}))
  })

  it('409 confirmacion_de_costo al crear también abre la confirmación', async () => {
    const rechazo = { response: { status: 409, data: { detail: {
      code: 'confirmacion_de_costo', costo_max_usd: '0.70', umbral_usd: '0.50',
      pasos_costo: [{ paso: 0, faceta: 'hipatia', usd_max: '0.70', motivo: 'acotado' }],
    } } } }
    const onSubmit = vi.fn().mockRejectedValueOnce(rechazo)
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    expect(dialogo).toHaveTextContent(usd('0.70'))
  })

  it('422 prevuelo_rechazado al crear muestra las violaciones en el modal', async () => {
    const v = { paso: 0, faceta: 'hipatia', regla: 'faceta_caida', detalle: '' }
    const rechazo = { response: { status: 422, data: { detail: { code: 'prevuelo_rechazado', violaciones: [v] } } } }
    const onSubmit = vi.fn(() => Promise.reject(rechazo))
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
  })

  // Adenda ítem 5 (fix round 1 ítem 4): los dos clics dentro del MISMO act,
  // antes de que React vuelva a renderizar: el `disabled` por estado todavía
  // no llegó, sólo la guardia síncrona impide la segunda llamada.
  it('doble clic antes del re-render no manda dos pre-vuelos ni dos creaciones', async () => {
    const onSubmit = vi.fn(() => Promise.resolve())
    await listo({ onSubmit })
    const boton = screen.getByText(/Planificar y ejecutar/i)
    await act(async () => { boton.click(); boton.click() })
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it('doble clic en Confirmar antes del re-render no manda dos creaciones', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn(() => new Promise(() => {}))
    await listo({ onSubmit })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    const confirmar = within(dialogo).getByRole('button', { name: es.confirmarCostoBoton })
    await act(async () => { confirmar.click(); confirmar.click() })
    await waitFor(() => expect(confirmar).toBeDisabled())
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  // Fix round 1 ítem 1: Dialogo sólo vuelve inert a #root; los dos diálogos son
  // portales hermanos. Con la confirmación abierta, el modal padre no puede
  // volver a lanzar el pre-vuelo ni cambiar el cuerpo que se va a crear.
  it('con la confirmación abierta el modal padre queda inert y no cambia lo que se crea', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn(() => Promise.resolve())
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    const padre = screen.getByRole('dialog', { name: es.newPipelineTitle })
    const selector = within(padre).getAllByRole('combobox')[0]
    expect(selector.closest('[inert]')).not.toBeNull()
    const otra = Array.from(selector.options).map((o) => o.value).find((v) => v !== selector.value)
    fireEvent.change(selector, { target: { value: otra } })
    fireEvent.click(within(padre).getByText(/En paralelo/i))
    fireEvent.click(within(padre).getByText(/Planificar y ejecutar/i))
    fireEvent.click(within(padre).getByRole('button', { name: es.cancel }))
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0].steps).toEqual(api.post.mock.calls[0][1].steps)
  })

  // Fix round 1 ítem 2: mientras se crea, cancelar no puede cerrar la
  // confirmación (el pipeline se crearía igual).
  it('mientras se crea, Cancelar está deshabilitado y Escape no cierra la confirmación', async () => {
    api.post.mockResolvedValue({ data: CARO })
    const onSubmit = vi.fn(() => new Promise(() => {}))
    const onClose = vi.fn()
    await listo({ onSubmit, onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.confirmarCostoBoton }))
    await waitFor(() => expect(within(dialogo).getByRole('button', { name: es.cancel })).toBeDisabled())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: es.confirmarCostoTitulo })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  // Fix round 1 ítem 3: lo que dijo el pre-vuelo es de la forma que se probó;
  // al editarla, deja de valer.
  it('editar la forma o las facetas borra las violaciones y el error anteriores', async () => {
    const v = { paso: 4, faceta: 'kimi', regla: 'tope_insuficiente', detalle: '' }
    api.post.mockResolvedValueOnce({ data: { ...VEREDICTO_OK, ok: false, violaciones: [v] } })
    await listo()
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
    const selector = screen.getAllByRole('combobox')[0]
    const original = selector.value
    const otra = Array.from(selector.options).map((o) => o.value).find((x) => x !== original)
    fireEvent.change(selector, { target: { value: otra } })
    await waitFor(() => expect(screen.queryByText(textoDeViolacion(es, v))).not.toBeInTheDocument())
    // Volver a la cadena válida para poder enviar otra vez.
    fireEvent.change(selector, { target: { value: original } })
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())

    api.post.mockRejectedValueOnce(new Error('network'))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.errorPipeline)
    fireEvent.click(screen.getByText(/En paralelo/i))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  // Fix round 2 (a): cada dependencia del efecto que borra tiene su test.
  it('elegir otra faceta (en paralelo) borra las violaciones y el error anteriores', async () => {
    const v = { paso: 0, faceta: 'hipatia', regla: 'faceta_caida', detalle: '' }
    api.post.mockResolvedValueOnce({ data: { ...VEREDICTO_OK, ok: false, violaciones: [v] } })
    renderModal()
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
    fireEvent.click(screen.getByText(/Razonamiento local/i))
    await waitFor(() => expect(screen.queryByText(textoDeViolacion(es, v))).not.toBeInTheDocument())

    api.post.mockRejectedValueOnce(new Error('network'))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.errorPipeline)
    fireEvent.click(screen.getByText(/Razonamiento local/i))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('elegir otro motor borra las violaciones y el error anteriores', async () => {
    const v = { paso: 0, faceta: 'kimi', regla: 'faceta_caida', detalle: '' }
    renderModal()
    await waitFor(() => expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled())
    fireEvent.click(screen.getByText(/Implementación técnica/i))
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
    api.post.mockResolvedValueOnce({ data: { ...VEREDICTO_OK, ok: false, violaciones: [v] } })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByText(textoDeViolacion(es, v))).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } })
    await waitFor(() => expect(screen.queryByText(textoDeViolacion(es, v))).not.toBeInTheDocument())

    api.post.mockRejectedValueOnce(new Error('network'))
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.errorPipeline)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'kimi' } })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  // Fix round 2 (b): el mismo commit que abre la confirmación vuelve inert al
  // envoltorio del botón enfocado, así que Dialogo no tiene a quién devolver
  // el foco. Al cerrarse la confirmación con el modal abierto, el foco va al
  // botón de enviar, sin depender del inert de jsdom.
  for (const [nombre, cerrar] of [
    ['Cancelar', (d) => fireEvent.click(within(d).getByRole('button', { name: es.cancel }))],
    ['Escape', () => fireEvent.keyDown(document, { key: 'Escape' })],
  ]) {
    it(`al cerrar la confirmación con ${nombre} el foco vuelve a Planificar y ejecutar`, async () => {
      api.post.mockResolvedValue({ data: CARO })
      await listo()
      // Como en el navegador: al abrir la confirmación el botón ya quedó bajo
      // inert y Dialogo registra `body` como elemento previo (fireEvent.click
      // no enfoca, así que acá también es `body`).
      document.activeElement?.blur?.()
      fireEvent.click(screen.getByRole('button', { name: es.planAndExecute }))
      const dialogo = await screen.findByRole('dialog', { name: es.confirmarCostoTitulo })
      cerrar(dialogo)
      await waitFor(() => expect(screen.queryByRole('dialog', { name: es.confirmarCostoTitulo })).not.toBeInTheDocument())
      await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: es.planAndExecute })))
    })
  }

  it('si el pre-vuelo no responde, el error se ve en el modal y el botón vuelve', async () => {
    api.post.mockRejectedValue(new Error('network'))
    await listo()
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.errorPipeline)
    expect(screen.getByText(/Planificar y ejecutar/i)).not.toBeDisabled()
  })

  // Fix round 1 Task 10 ítem 1: mientras el pre-vuelo o la creación están en
  // vuelo (sin confirmación), ni Cancelar ni Escape cierran: el servidor crearía
  // el pipeline igual y el cierre tardío no tiene a quién pertenecer.
  for (const [nombre, enVuelo] of [
    ['el pre-vuelo', () => { api.post.mockReturnValue(new Promise(() => {})); return vi.fn(() => Promise.resolve()) }],
    ['la creación', () => vi.fn(() => new Promise(() => {}))],
  ]) {
    it(`con ${nombre} en vuelo, Cancelar está deshabilitado y ni Cancelar ni Escape cierran`, async () => {
      const onSubmit = enVuelo()
      const onClose = vi.fn()
      await listo({ onSubmit, onClose })
      fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
      const cancelar = screen.getByRole('button', { name: es.cancel })
      await waitFor(() => expect(cancelar).toBeDisabled())
      fireEvent.click(cancelar)
      await act(async () => { cancelar.click() })
      fireEvent.keyDown(document, { key: 'Escape' })
      expect(onClose).not.toHaveBeenCalled()
      expect(screen.getByRole('dialog', { name: es.newPipelineTitle })).toBeInTheDocument()
    })
  }

  // Fix round 2 Task 10 ítem 3: tras un pedido que falla el modal vuelve a cerrarse.
  it('tras un pre-vuelo que falla, Cancelar vuelve a estar habilitado y Escape cierra', async () => {
    api.post.mockRejectedValue(new Error('network'))
    const onClose = vi.fn()
    await listo({ onClose })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))
    await screen.findByRole('alert')
    await waitFor(() => expect(screen.getByRole('button', { name: es.cancel })).not.toBeDisabled())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
