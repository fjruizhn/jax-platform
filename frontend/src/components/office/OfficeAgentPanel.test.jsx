import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'

const agents = vi.hoisted(() => ({ list: vi.fn(), engines: vi.fn(), create: vi.fn(), bind: vi.fn(), run: vi.fn() }))
vi.mock('../../api/virtualOffice', () => ({ officeAgentsApi: agents }))
import OfficeAgentPanel from './OfficeAgentPanel.jsx'

const engine = { engine_key: 'glm', display_name: 'GLM', model_id: 'glm-5', allowed_data_classifications: ['INTERNAL'] }
const existing = { agent_id: 7, agent_key: 'resumen', display_name: 'Resumen', engine_key: 'glm', model_id: 'glm-5', data_classification: 'INTERNAL', version: 1, status: 'ACTIVE' }

function show({ canManage = false } = {}) {
  return render(<I18nProvider><OfficeAgentPanel projectId={12} canManage={canManage} onForbidden={vi.fn()} /></I18nProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
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

  it('allows a project administrator to create a private agent and change its engine', async () => {
    agents.create.mockResolvedValue({ data: { ...existing, agent_id: 8, version: 1 } })
    agents.bind.mockResolvedValue({ data: { ...existing, engine_key: 'local', model_id: 'local-1', version: 2 } })
    agents.engines.mockResolvedValue({ data: { items: [engine, { engine_key: 'local', display_name: 'Local', model_id: 'local-1', allowed_data_classifications: ['INTERNAL'] }] } })
    show({ canManage: true })
    fireEvent.click(await screen.findByRole('button', { name: 'Crear agente privado' }))
    fireEvent.change(screen.getByLabelText('Clave del agente'), { target: { value: 'redactor' } })
    fireEvent.change(screen.getByLabelText('Nombre del agente'), { target: { value: 'Redactor' } })
    fireEvent.change(screen.getByLabelText('Instrucciones'), { target: { value: 'Redactá con claridad.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear agente' }))
    await waitFor(() => expect(agents.create).toHaveBeenCalledWith(12, { agent_key: 'redactor', display_name: 'Redactor', engine_key: 'glm', data_classification: 'INTERNAL', instructions: 'Redactá con claridad.' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Resumen' })[0])
    fireEvent.change(await screen.findByLabelText('Motor de Resumen'), { target: { value: 'local' } })
    await waitFor(() => expect(agents.bind).toHaveBeenCalledWith(12, 7, { engine_key: 'local', expected_version: 1 }))
  })
})
