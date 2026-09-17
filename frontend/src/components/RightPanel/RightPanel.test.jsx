import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Director Jacobs (DEUDA.md, anotados de la ronda b8f80733, 2026-09-12):
// "Aprobar" y "Cancelar pipeline" tragaban el error con un console.error.
// Si /resume o /cancel fallaba, en la interfaz no pasaba nada y el pipeline
// seguía esperando sin que nadie supiera por qué. El fallo se muestra.
vi.mock('../../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../../api/client'
import RightPanel from './RightPanel'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const EN_ESPERA = {
  p1: { pipeline_id: 'p1-0000-0000', name: 'probar', status: 'waiting_gate', steps: [] },
}

function renderPanel() {
  return render(<I18nProvider><RightPanel /></I18nProvider>)
}

beforeEach(() => {
  api.post.mockReset()
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { events: [] } })
  localStorage.clear()
  useJaxStore.setState({ activePipelines: EN_ESPERA })
})

// Task 6 S3 (2026-09-15): /api/audit es solo para superadmin. La pestaña de
// auditoría no se le ofrece a nadie más (como el engranaje de Administración).
describe('RightPanel -- la pestaña de auditoría es solo para superadmin', () => {
  for (const role of ['viewer', 'operator']) {
    it(`un ${role} no ve la pestaña ni dispara /audit`, () => {
      useJaxStore.setState({ user: { user_id: '7', role } })
      renderPanel()
      expect(screen.queryByRole('button', { name: es.tabAudit })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: es.tabDirectorJacobs })).toBeInTheDocument()
      // 2026-09-17: el panel pide GET /pipelines (detenidos); /audit sigue sin pedirse.
      expect(api.get).not.toHaveBeenCalledWith('/audit')
    })
  }

  it('sin usuario en el store tampoco aparece', () => {
    useJaxStore.setState({ user: null })
    renderPanel()
    expect(screen.queryByRole('button', { name: es.tabAudit })).not.toBeInTheDocument()
  })

  it('un superadmin ve la pestaña y al abrirla carga la auditoría', async () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.tabAudit }))
    expect(await screen.findByText(es.auditLog)).toBeInTheDocument()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/audit'))
  })

  // Fix round 1 (review de 3bed155): la vuelta a Pipelines no tenía test.
  it('si el rol deja de ser superadmin con la auditoría abierta, vuelve a Pipelines', async () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.tabAudit }))
    expect(await screen.findByText(es.auditLog)).toBeInTheDocument()

    act(() => { useJaxStore.setState({ user: { user_id: '1', role: 'viewer' } }) })

    expect(screen.queryByText(es.auditLog)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: es.tabAudit })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.approve })).toBeInTheDocument()
  })
})

describe('RightPanel -- los fallos de Aprobar y Cancelar se ven', () => {
  it('los textos existen en los dos idiomas', () => {
    for (const clave of ['approveError', 'cancelError']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
    }
  })

  it('si /resume falla, aparece el aviso', async () => {
    api.post.mockRejectedValue(new Error('502'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.approve }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.approveError)
  })

  it('si /cancel falla, aparece el aviso', async () => {
    api.post.mockRejectedValue(new Error('502'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.cancelPipeline }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.cancelError)
  })

  it('si /resume responde bien, no hay aviso', async () => {
    api.post.mockResolvedValue({ data: { ok: true } })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.approve }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/p1-0000-0000/resume'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('el aviso de Aprobar desaparece si el pipeline ya no espera aprobación', async () => {
    api.post.mockRejectedValue(new Error('502'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.approve }))
    await screen.findByRole('alert')
    act(() => useJaxStore.setState({ activePipelines: { p1: { ...EN_ESPERA.p1, status: 'running' } } }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('el aviso no se muestra sobre otro pipeline', async () => {
    api.post.mockRejectedValue(new Error('502'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.cancelPipeline }))
    await screen.findByRole('alert')
    act(() => useJaxStore.setState({
      activePipelines: { p2: { pipeline_id: 'p2-0000-0000', name: 'otro', status: 'running', steps: [] } },
    }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('un aviso viejo se borra al reintentar con éxito', async () => {
    api.post.mockRejectedValueOnce(new Error('502')).mockResolvedValueOnce({ data: { ok: true } })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: es.approve }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: es.approve }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})

describe('RightPanel -- jerarquía visual del gate', () => {
  // Revisión de ddde201: Aprobar quedaba con bg-aviso-fondo, casi idéntico a
  // Cancelar (bg-superficie) en modo claro. Aprobar es la única acción
  // primaria de un pipeline pausado: vuelve a un CTA sólido con par
  // declarado (sobre-color/accion).
  it('Aprobar es el CTA sólido con bg-accion, distinto de Cancelar', () => {
    renderPanel()
    expect(screen.getByRole('button', { name: es.approve })).toHaveClass('bg-accion')
    expect(screen.getByRole('button', { name: es.cancelPipeline })).not.toHaveClass('bg-accion')
  })
})

// M-2 (revisión final PR 3, 2026-09-14): activePipeline.status
// (jax_engine/schemas.py::PipelineStatus) se mostraba crudo, sin traducir.
describe('RightPanel -- status del pipeline traducido (M-2)', () => {
  it('las claves de los 5 estados existen en es y en', () => {
    for (const clave of ['pending', 'running', 'waiting_gate', 'completed', 'failed']) {
      expect(es.pipelineStatusLabels[clave], `es.pipelineStatusLabels.${clave}`).toBeTruthy()
      expect(en.pipelineStatusLabels[clave], `en.pipelineStatusLabels.${clave}`).toBeTruthy()
    }
  })

  it('waiting_gate se muestra traducido, no el valor crudo del backend', () => {
    renderPanel()
    expect(screen.getByText(es.pipelineStatusLabels.waiting_gate)).toBeInTheDocument()
    expect(screen.queryByText('waiting_gate')).not.toBeInTheDocument()
  })

  // Ruling del ledger (asignado a la Task 10, 2026-09-17): el fallback era el
  // valor crudo, y `constructor` leía una función heredada de Object. Un
  // status que esta versión no conoce se muestra con un texto genérico.
  for (const status of ['a_new_backend_status', 'constructor', 'toString']) {
    it(`un status desconocido (${status}) muestra el texto genérico, nunca el crudo`, () => {
      useJaxStore.setState({
        activePipelines: { p1: { pipeline_id: 'p1-0000-0000', name: 'probar', status, steps: [] } },
      })
      renderPanel()
      expect(screen.getByText(es.pipelineStatusDesconocido)).toBeInTheDocument()
      expect(screen.queryByText(status)).not.toBeInTheDocument()
    })
  }
})

// M-3 (Ruling 37, revisión final PR 3, 2026-09-14): la pista de la barra de
// progreso usaba bg-superficie -- 1,05:1 contra bg-accion (el relleno) en
// claro, casi invisible. bg-borde sí se distingue en los dos temas
// (contraste.test.js::HalEye -- anillo del housing ya lo prueba para el
// mismo par borde/fondo).
describe('RightPanel -- pista de la barra de progreso (M-3)', () => {
  it('la pista usa bg-borde, no bg-superficie', () => {
    useJaxStore.setState({
      activePipelines: {
        p1: {
          pipeline_id: 'p1-0000-0000',
          name: 'probar',
          status: 'running',
          steps: [{ step_id: 's1', status: 'completed' }, { step_id: 's2', status: 'pending' }],
        },
      },
    })
    const { container } = renderPanel()
    const pista = container.querySelector('.h-1')
    expect(pista).toHaveClass('bg-borde')
    expect(pista).not.toHaveClass('bg-superficie')
  })
})

describe('RightPanel -- pipelines detenidos que se pueden continuar (spec 2026-09-17 §6.2)', () => {
  const CAUSA_FALLO = { tipo: 'fallo', paso: 4, detalle: 'Salida cortada' }
  const LISTA = { pipelines: [
    { pipeline_id: 'p-ab', name: 'leyes', status: 'aborted', causa: CAUSA_FALLO },
    { pipeline_id: 'p-ex', name: 'vencido', status: 'expired', causa: { tipo: 'expirado' } },
    { pipeline_id: 'p-ok', name: 'terminado', status: 'completed', causa: null },
  ] }

  beforeEach(() => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url === '/pipelines' ? LISTA : { events: [] } }))
  })

  it('un abortado muestra su causa y el botón Continuar; un completado no aparece', async () => {
    renderPanel()
    expect(await screen.findByText(`${es.causasDeAborto.fallo(CAUSA_FALLO)} ${es.respuestaDelServicio('Salida cortada')}`)).toBeInTheDocument()
    expect(screen.getByText(es.continuablesTitulo)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: es.continuarPipeline })).toHaveLength(2)
    expect(screen.queryByText('terminado')).not.toBeInTheDocument()
  })

  it('un vencido muestra la causa expirado', async () => {
    renderPanel()
    expect(await screen.findByText(es.causasDeAborto.expirado({ tipo: 'expirado' }))).toBeInTheDocument()
  })

  it('una causa de tipo desconocido no se muestra cruda', async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url === '/pipelines'
      ? { pipelines: [{ pipeline_id: 'p-x', name: 'raro', status: 'aborted', causa: { tipo: 'constructor' } }] }
      : { events: [] } }))
    renderPanel()
    expect(await screen.findByText(es.causasDeAborto.desconocida({}))).toBeInTheDocument()
  })

  it('si la lista no se puede cargar, lo dice', async () => {
    api.get.mockImplementation((url) => (url === '/pipelines' ? Promise.reject(new Error('502')) : Promise.resolve({ data: { events: [] } })))
    renderPanel()
    expect(await screen.findByText(es.continuablesError)).toBeInTheDocument()
  })

  it('un detenido que el store ya ve corriendo no se ofrece para continuar', async () => {
    useJaxStore.setState({ activePipelines: { 'p-ab': { pipeline_id: 'p-ab', name: 'leyes', status: 'running', steps: [] } } })
    renderPanel()
    await screen.findByText(es.causasDeAborto.expirado({}))
    expect(screen.getAllByRole('button', { name: es.continuarPipeline })).toHaveLength(1)
  })

  it('la lista se vuelve a pedir cuando cambia el estado de un pipeline del store', async () => {
    renderPanel()
    await screen.findByText(es.causasDeAborto.expirado({}))
    const antes = api.get.mock.calls.filter(([u]) => u === '/pipelines').length
    act(() => useJaxStore.setState({ activePipelines: { p1: { ...EN_ESPERA.p1, status: 'aborted' } } }))
    await waitFor(() => expect(api.get.mock.calls.filter(([u]) => u === '/pipelines').length).toBe(antes + 1))
  })

  it('Continuar abre la ventana de continuar', async () => {
    api.post.mockReturnValue(new Promise(() => {}))
    renderPanel()
    const [boton] = await screen.findAllByRole('button', { name: es.continuarPipeline })
    fireEvent.click(boton)
    expect(await screen.findByRole('dialog', { name: es.continuarTitulo })).toBeInTheDocument()
  })
})
