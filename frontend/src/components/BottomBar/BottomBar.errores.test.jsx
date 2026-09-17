import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { readFileSync } from 'node:fs'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))

// El modal real no importa acá: se capturan sus props para llamar a onSubmit.
vi.mock('./PipelineModal', () => ({
  default: (props) => { globalThis.__propsDelModalDePipeline = props; return null },
}))

import api from '../../api/client'
import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()

function enviarChat(texto) {
  render(<I18nProvider><BottomBar /></I18nProvider>)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: texto } })
  fireEvent.click(screen.getByRole('button', { name: es.send }))
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, activeFacet: 'thot', messages: [] }, true)
  api.post.mockReset()
})

describe('BottomBar -- errores y avisos con código', () => {
  it('un 502 con código se traduce; el código no aparece crudo', async () => {
    api.post.mockRejectedValue({ response: { status: 502, data: { detail: { code: 'faceta_error', facet: 'thot', motivo: 'timeout' } } } })
    enviarChat('hola')
    await waitFor(() => expect(useJaxStore.getState().messages).toHaveLength(2))
    const { content } = useJaxStore.getState().messages[1]
    expect(content).toBe(`**${es.errorPrefix}:** ${es.erroresMesa.faceta_error({ facet: 'thot' })} ${es.respuestaDelServicio('timeout')}`)
  })

  it('una respuesta enlatada muestra el texto del aviso, no la marca', async () => {
    api.post.mockResolvedValue({ data: {
      facet: 'thot', response: '[faceta_sin_binding facet=thot]', timestamp: 't',
      aviso: { code: 'faceta_sin_binding', params: { facet: 'thot' } },
    } })
    enviarChat('hola')
    await waitFor(() => expect(useJaxStore.getState().messages).toHaveLength(2))
    expect(useJaxStore.getState().messages[1].content).toBe(es.avisosChat.faceta_sin_binding({ facet: 'thot' }))
  })

  // Frente B, Task 9 (2026-09-17, Ruling R4): con el freno puesto la Mesa
  // responde 423 kill_switch_activo; el chat lo dice traducido, nunca el código.
  it('un 423 del kill switch se muestra traducido, no el código', async () => {
    api.post.mockRejectedValue({ response: { status: 423, data: { detail: 'kill_switch_activo' } } })
    enviarChat('hola')
    await waitFor(() => expect(
      useJaxStore.getState().messages.some((m) => m.content.includes(es.erroresMesa.kill_switch_activo()))).toBe(true))
    expect(useJaxStore.getState().messages.some((m) => m.content.includes('kill_switch_activo'))).toBe(false)
  })

  it('sin prefijos literales ni mapa de lambdas', () => {
    // Indirección vía `base` (como Login.test.jsx): Vite reescribe
    // `new URL('./x', import.meta.url)` inline y no sirve un file:// en jsdom.
    const base = import.meta.url
    const fuente = readFileSync(new URL('./BottomBar.jsx', base), 'utf8')
    expect(fuente).not.toContain('**Error:**')
    expect(fuente).not.toContain('PLACEHOLDERS')
  })
})

describe('BottomBar -- crear pipeline (spec 2026-09-17 §6.2)', () => {
  it('si crear falla, relanza el error para el modal y no ensucia el chat', async () => {
    globalThis.__propsDelModalDePipeline = undefined
    render(<I18nProvider><BottomBar /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: es.modePipeline }))
    const caja = screen.getByRole('textbox')
    fireEvent.change(caja, { target: { value: 'investigar leyes' } })
    fireEvent.keyDown(caja, { key: 'Enter' })
    await waitFor(() => expect(globalThis.__propsDelModalDePipeline).toBeTruthy())
    const rechazo = { response: { status: 409, data: { detail: { code: 'confirmacion_de_costo' } } } }
    api.post.mockRejectedValue(rechazo)
    await expect(globalThis.__propsDelModalDePipeline.onSubmit({ steps: [], mode: 'autonomous' })).rejects.toBe(rechazo)
    expect(useJaxStore.getState().messages).toEqual([])
  })
})
