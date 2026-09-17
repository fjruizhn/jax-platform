import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Fix round 1 Task 10 ítem 1: el cierre que dispara una ventana de continuar
// (p. ej. cuando su continue termina tarde) sólo cierra la ventana de SU
// pipeline, nunca la de otro abierto después. La ventana se reemplaza por un
// doble que guarda sus props para poder llamar a un onClose viejo.
vi.mock('../../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))
const abiertas = []
vi.mock('./ContinuarPipelineModal', () => ({
  default: (props) => {
    abiertas.push(props)
    return <div role="dialog" aria-label={`continuar ${props.pipeline.pipeline_id}`} />
  },
}))

import api from '../../api/client'
import RightPanel from './RightPanel'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

beforeEach(() => {
  abiertas.length = 0
  api.get.mockReset()
  api.get.mockImplementation((url) => Promise.resolve({ data: url === '/pipelines' ? { pipelines: [
    { pipeline_id: 'p-a', name: 'A', status: 'aborted', causa: { tipo: 'expirado' } },
    { pipeline_id: 'p-b', name: 'B', status: 'aborted', causa: { tipo: 'expirado' } },
  ] } : { events: [] } }))
  useJaxStore.setState({ activePipelines: {} })
})

describe('RightPanel -- cierre de la ventana de continuar con alcance', () => {
  it('un onClose tardío de la ventana de A no cierra la de B', async () => {
    render(<I18nProvider><RightPanel /></I18nProvider>)
    const [botonA, botonB] = await screen.findAllByRole('button', { name: es.continuarPipeline })
    fireEvent.click(botonA)
    const cerrarA = abiertas.at(-1).onClose
    expect(abiertas.at(-1).pipeline.pipeline_id).toBe('p-a')
    act(() => cerrarA())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    fireEvent.click(botonB)
    expect(screen.getByRole('dialog', { name: 'continuar p-b' })).toBeInTheDocument()
    act(() => cerrarA())
    expect(screen.getByRole('dialog', { name: 'continuar p-b' })).toBeInTheDocument()
    act(() => abiertas.at(-1).onClose())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
