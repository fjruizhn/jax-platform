import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 7 (2026-09-22, spec descartar-pipelines §5): Administración →
// Pipelines ocultos, sólo superadmin (la guardia del ROL la pone /admin/*
// en App.jsx -- RequireAuth + RequireSuperadmin -- esta pantalla no repite
// esa comprobación). Restaurar es reversible: sin confirmación.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import AdminPipelinesOcultos from './AdminPipelinesOcultos'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

const OCULTO = {
  pipeline_id: 'h1', name: 'plan oculto', user_id: 'u-9', tenant_id: 't-1',
  descartado_por: 'admin-1', descartado_at: 1758000700, created_at: 1758000000,
}

function renderPantalla() {
  return render(<I18nProvider><AdminPipelinesOcultos /></I18nProvider>)
}

function mockGet({ ocultos = [OCULTO], hayMas = false } = {}) {
  api.get.mockImplementation((url) => {
    if (url !== '/admin/pipelines/ocultos') return Promise.reject(new Error(`url inesperada: ${url}`))
    return Promise.resolve({ data: { pipelines: ocultos, has_more: hayMas } })
  })
}

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  mockGet()
})

describe('AdminPipelinesOcultos (Task 7)', () => {
  it('lista paginada con nombre, dueño y fecha', async () => {
    renderPantalla()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/admin/pipelines/ocultos', { params: { limite: 50, offset: 0 } }))
    expect(await screen.findByText('plan oculto')).toBeInTheDocument()
    expect(screen.getByText(es.ocultoDe('u-9'))).toBeInTheDocument()
  })

  it('con has_more, "Cargar más" pide offset=50', async () => {
    mockGet({ hayMas: true })
    renderPantalla()
    await screen.findByText('plan oculto')

    mockGet({ ocultos: [{ ...OCULTO, pipeline_id: 'h2', name: 'segundo oculto' }], hayMas: false })
    fireEvent.click(screen.getByRole('button', { name: es.cargarMas }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/admin/pipelines/ocultos', { params: { limite: 50, offset: 1 } }))
    expect(await screen.findByText('segundo oculto')).toBeInTheDocument()
    expect(screen.getByText('plan oculto')).toBeInTheDocument()
  })

  it('Restaurar llama a /restore y quita la fila, sin pedir confirmación', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'h1', status: 'discarded' } })
    renderPantalla()
    await screen.findByText('plan oculto')

    fireEvent.click(screen.getByRole('button', { name: es.restaurarPipeline }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/h1/restore'))
    await waitFor(() => expect(screen.queryByText('plan oculto')).not.toBeInTheDocument())
  })

  it('si la API responde 403, se muestra el error traducido', async () => {
    api.get.mockRejectedValue({ response: { status: 403, data: { detail: 'usuario_no_superadmin' } } })
    renderPantalla()
    expect(await screen.findByText(es.pipelinesOcultosError)).toBeInTheDocument()
  })

  it('sin ocultos, muestra el estado vacío', async () => {
    mockGet({ ocultos: [] })
    renderPantalla()
    expect(await screen.findByText(es.sinOcultos)).toBeInTheDocument()
  })
})
