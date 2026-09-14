import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Director Jacobs (DEUDA.md, anotados de la ronda b8f80733, 2026-09-12):
// "Aprobar" y "Cancelar pipeline" tragaban el error con un console.error.
// Si /resume o /cancel fallaba, en la interfaz no pasaba nada y el pipeline
// seguía esperando sin que nadie supiera por qué. El fallo se muestra.
vi.mock('../../api/client', () => ({ default: { post: vi.fn() } }))

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
  localStorage.clear()
  useJaxStore.setState({ activePipelines: EN_ESPERA })
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
