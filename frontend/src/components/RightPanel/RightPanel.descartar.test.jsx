import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 5 (2026-09-22, spec descartar-pipelines §5): botón Descartar en
// "Detenidos". Confirmación en ventana propia (Dialogo), nunca confirm().
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import RightPanel from './RightPanel'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const PIPELINE = { pipeline_id: 'p1', name: 'Plan A', status: 'aborted', causa: { tipo: 'expirado' } }

function renderPanel() {
  return render(<I18nProvider><RightPanel /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  api.get.mockImplementation((url) => Promise.resolve({
    data: url === '/pipelines' ? { pipelines: [PIPELINE] } : { events: [] },
  }))
  useJaxStore.setState({ activePipelines: {} })
})

describe('RightPanel -- descartar un pipeline detenido (Task 5)', () => {
  it('los textos de descartar existen en los dos idiomas', () => {
    for (const clave of ['descartarPipeline', 'descartarTitulo', 'descartarMensaje', 'descartarConfirmar', 'descartarError', 'cancelar']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
    }
  })

  it('cada tarjeta de Detenidos tiene un botón Descartar', async () => {
    expect(es.descartarPipeline).toBeTruthy()
    renderPanel()
    expect(await screen.findByRole('button', { name: es.descartarPipeline })).toBeInTheDocument()
  })

  it('al pulsarlo abre un diálogo con el nombre del pipeline, sin llamar a la API todavía', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveTextContent('Plan A')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('al confirmar llama a discard, la tarjeta desaparece y se vuelve a pedir /pipelines', async () => {
    // El backend real (GET /pipelines sin filtro) excluye discarded/hidden --
    // el mock imita esa verdad: tras el discard, la lista ya no trae a p1.
    let lista = [PIPELINE]
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/pipelines' ? { pipelines: lista } : { events: [] },
    }))
    api.post.mockImplementation(async (url) => {
      if (url === '/pipelines/p1/discard') lista = lista.filter((p) => p.pipeline_id !== 'p1')
      return { data: { pipeline_id: 'p1', status: 'discarded' } }
    })
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    const dialogo = screen.getByRole('dialog')
    const llamadasAntes = api.get.mock.calls.filter((c) => c[0] === '/pipelines').length

    fireEvent.click(within(dialogo).getByRole('button', { name: es.descartarConfirmar }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/p1/discard'))
    await waitFor(() => expect(screen.queryByText('Plan A')).not.toBeInTheDocument())
    await waitFor(() => expect(
      api.get.mock.calls.filter((c) => c[0] === '/pipelines').length
    ).toBeGreaterThan(llamadasAntes))
  })

  it('si falla, aparece el error traducido y la tarjeta sigue', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'pipeline_no_encontrado' } } })
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    const dialogo = screen.getByRole('dialog')

    fireEvent.click(within(dialogo).getByRole('button', { name: es.descartarConfirmar }))

    expect(await within(dialogo).findByText(es.erroresMesa.pipeline_no_encontrado())).toBeInTheDocument()
    expect(screen.getByText('Plan A')).toBeInTheDocument()
  })

  it('Escape cierra el diálogo sin llamar a la API', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  // MINOR-1 (fix round 1, revisión adversarial): errorDescartar sobrevivía
  // al cierre del diálogo y contaminaba la próxima apertura.
  it('el error se limpia al cancelar y no reaparece al volver a abrir', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'pipeline_no_encontrado' } } })
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    let dialogo = screen.getByRole('dialog')
    fireEvent.click(within(dialogo).getByRole('button', { name: es.descartarConfirmar }))
    await within(dialogo).findByText(es.erroresMesa.pipeline_no_encontrado())

    fireEvent.click(within(dialogo).getByRole('button', { name: es.cancelar }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: es.descartarPipeline }))
    dialogo = screen.getByRole('dialog')
    expect(within(dialogo).queryByText(es.erroresMesa.pipeline_no_encontrado())).not.toBeInTheDocument()
  })

  it('el error se limpia al cerrar con Escape y no reaparece al volver a abrir', async () => {
    api.post.mockRejectedValue({ response: { data: { detail: 'pipeline_no_encontrado' } } })
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    let dialogo = screen.getByRole('dialog')
    fireEvent.click(within(dialogo).getByRole('button', { name: es.descartarConfirmar }))
    await within(dialogo).findByText(es.erroresMesa.pipeline_no_encontrado())

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: es.descartarPipeline }))
    dialogo = screen.getByRole('dialog')
    expect(within(dialogo).queryByText(es.erroresMesa.pipeline_no_encontrado())).not.toBeInTheDocument()
  })

  // MINOR-5 (fix round 1): un único mecanismo de baja -- la recarga contra
  // el backend, no una quita optimista aparte. Prueba que DISTINGUE las dos
  // versiones de verdad (la primera, sólo con `api.get` siempre devolviendo
  // el pipeline, no distinguía nada -- la recarga corría última en los dos
  // casos y pisaba el resultado de la baja optimista igual): la recarga
  // queda A PROPÓSITO pendiente después de que el discard ya resolvió. Con
  // una baja optimista aparte, la tarjeta desaparecería ACÁ, antes de que la
  // recarga resuelva -- con un único mecanismo, se queda hasta que la
  // recarga responde de verdad.
  it('la tarjeta no desaparece antes de que la recarga resuelva -- sin baja optimista aparte', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'p1', status: 'discarded' } })
    let primeraLlamada = true
    let resolverRecarga
    api.get.mockImplementation((url) => {
      if (url !== '/pipelines') return Promise.resolve({ data: { events: [] } })
      if (primeraLlamada) {
        primeraLlamada = false
        return Promise.resolve({ data: { pipelines: [PIPELINE] } })
      }
      return new Promise((resolve) => { resolverRecarga = () => resolve({ data: { pipelines: [] } }) })
    })
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: es.descartarPipeline }))
    const dialogo = screen.getByRole('dialog')

    fireEvent.click(within(dialogo).getByRole('button', { name: es.descartarConfirmar }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/p1/discard'))
    await waitFor(() => expect(resolverRecarga).toBeTruthy())
    // El discard ya resolvió y la recarga ya se pidió, pero TODAVÍA no
    // resolvió -- la tarjeta tiene que seguir ahí.
    expect(screen.getByText('Plan A')).toBeInTheDocument()

    resolverRecarga()
    await waitFor(() => expect(screen.queryByText('Plan A')).not.toBeInTheDocument())
  })
})
