import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'

const agents = vi.hoisted(() => ({ list: vi.fn(), engines: vi.fn(), create: vi.fn(), bind: vi.fn(), run: vi.fn() }))
vi.mock('../../api/virtualOffice', () => ({ officeAgentsApi: agents }))
import OfficeAgentPanel, { officeAgentRunIntentStorageKey } from './OfficeAgentPanel.jsx'

const engine = { engine_key: 'glm', display_name: 'GLM', model_id: 'glm-5', allowed_data_classifications: ['INTERNAL_CONFIDENTIAL'] }
const existing = { agent_id: 7, agent_key: 'resumen', display_name: 'Resumen', engine_key: 'glm', model_id: 'glm-5', data_classification: 'INTERNAL_CONFIDENTIAL', version: 1, status: 'ACTIVE' }

function show({ canManage = false, projectId = 12 } = {}) {
  return render(<I18nProvider><OfficeAgentPanel projectId={projectId} canManage={canManage} onForbidden={vi.fn()} /></I18nProvider>)
}

beforeEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  sessionStorage.clear()
  useJaxStore.setState({ user: { user_id: 41 } })
  agents.list.mockResolvedValue({ data: { items: [existing] } })
  agents.engines.mockResolvedValue({ data: { items: [engine] } })
})

describe('OfficeAgentPanel', () => {
  it('runs an existing private agent and renders output as text', async () => {
    agents.run.mockResolvedValue({ data: { run_id: 'run-7', status: 'SUCCEEDED', output: '<b>texto no confiable</b>', model_id: 'glm-5' } })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Resumen' }))
    fireEvent.change(screen.getByLabelText('Mensaje para Resumen'), { target: { value: 'Resumí esto' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    await waitFor(() => expect(agents.run).toHaveBeenCalledWith(12, 7, expect.objectContaining({ input: 'Resumí esto' })))
    expect(await screen.findByText('<b>texto no confiable</b>')).toBeInTheDocument()
    expect(document.querySelector('b')).toBeNull()
  })

  it('lets an internal member run agents without loading the admin engine catalogue', async () => {
    agents.engines.mockRejectedValue({ response: { status: 403 } })
    agents.run.mockResolvedValue({ data: { run_id: 'run-8', status: 'DISPATCHING', output: null, model_id: 'glm-5' } })
    show()
    expect(await screen.findByRole('button', { name: 'Resumen' })).toBeInTheDocument()
    expect(agents.engines).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Mensaje para Resumen'), { target: { value: 'seguí' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    expect(await screen.findByText('El agente sigue procesando este mensaje. No lo envíes de nuevo.')).toBeInTheDocument()
  })

  it('reuses the same run key after a reload following network uncertainty', async () => {
    agents.run.mockRejectedValueOnce({ response: { status: 503 } }).mockResolvedValueOnce({ data: { run_id: 'run-9', status: 'SUCCEEDED', output: 'listo', model_id: 'glm-5' } })
    const first = show()
    fireEvent.change(await screen.findByLabelText('Mensaje para Resumen'), { target: { value: 'continuá' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    await waitFor(() => expect(agents.run).toHaveBeenCalledTimes(1))
    const firstKey = agents.run.mock.calls[0][2].idempotency_key
    first.unmount()

    show()
    fireEvent.change(await screen.findByLabelText('Mensaje para Resumen'), { target: { value: 'continuá' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    await waitFor(() => expect(agents.run).toHaveBeenCalledTimes(2))
    expect(agents.run.mock.calls[1][2].idempotency_key).toBe(firstKey)
  })

  it('ignores malformed saved run intent data and creates a fresh key', async () => {
    const storageKey = await officeAgentRunIntentStorageKey({ userId: 41, projectId: 12, agentId: 7, input: 'continuá' })
    sessionStorage.setItem(storageKey, '{malformed')
    vi.stubGlobal('crypto', { subtle: crypto.subtle, randomUUID: vi.fn(() => 'fresh-run-key') })
    agents.run.mockResolvedValue({ data: { run_id: 'run-10', status: 'SUCCEEDED', output: 'listo', model_id: 'glm-5' } })
    show()
    fireEvent.change(await screen.findByLabelText('Mensaje para Resumen'), { target: { value: 'continuá' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    await waitFor(() => expect(agents.run).toHaveBeenCalledTimes(1))
    expect(agents.run.mock.calls[0][2].idempotency_key).toBe('fresh-run-key')
  })

  it('does not reuse a project run intent after the project changes', async () => {
    agents.run.mockResolvedValue({ data: { run_id: 'run-11', status: 'DISPATCHING', output: null, model_id: 'glm-5' } })
    const view = show({ projectId: 12 })
    fireEvent.change(await screen.findByLabelText('Mensaje para Resumen'), { target: { value: 'continuá' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    await waitFor(() => expect(agents.run).toHaveBeenCalledTimes(1))
    const firstKey = agents.run.mock.calls[0][2].idempotency_key
    expect(await screen.findByText('El agente sigue procesando este mensaje. No lo envíes de nuevo.')).toBeInTheDocument()

    view.rerender(<I18nProvider><OfficeAgentPanel projectId={13} onForbidden={vi.fn()} /></I18nProvider>)
    fireEvent.change(await screen.findByLabelText('Mensaje para Resumen'), { target: { value: 'continuá' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ejecutar agente' }))
    await waitFor(() => expect(agents.run).toHaveBeenCalledTimes(2))
    expect(agents.run.mock.calls[1][2].idempotency_key).not.toBe(firstKey)
  })

  it('allows a project administrator to create a private agent and change its engine', async () => {
    agents.create.mockResolvedValue({ data: { ...existing, agent_id: 8, version: 1 } })
    agents.bind.mockResolvedValue({ data: { ...existing, engine_key: 'local', model_id: 'local-1', version: 2 } })
    agents.engines.mockResolvedValue({ data: { items: [engine, { engine_key: 'local', display_name: 'Local', model_id: 'local-1', allowed_data_classifications: ['INTERNAL_CONFIDENTIAL'] }] } })
    show({ canManage: true })
    fireEvent.click(await screen.findByRole('button', { name: 'Crear agente privado' }))
    fireEvent.change(screen.getByLabelText('Clave del agente'), { target: { value: 'redactor' } })
    fireEvent.change(screen.getByLabelText('Nombre del agente'), { target: { value: 'Redactor' } })
    fireEvent.change(screen.getByLabelText('Instrucciones'), { target: { value: 'Redactá con claridad.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear agente' }))
    await waitFor(() => expect(agents.create).toHaveBeenCalledWith(12, { agent_key: 'redactor', display_name: 'Redactor', engine_key: 'glm', data_classification: 'INTERNAL_CONFIDENTIAL', instructions: 'Redactá con claridad.' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Resumen' })[0])
    fireEvent.change(await screen.findByLabelText('Motor de Resumen'), { target: { value: 'local' } })
    await waitFor(() => expect(agents.bind).toHaveBeenCalledWith(12, 7, { engine_key: 'local', expected_version: 1 }))
  })
})
