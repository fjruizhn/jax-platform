import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))
import api from '../../api/client'
import AdminAuditoriaDescarte from './AdminAuditoriaDescarte'
import { I18nProvider } from '../../i18n/index.jsx'

const fila = {
  id: 14, pipeline_id: 'pipe-14', pipeline_name: 'proyecto de prueba',
  event_type: 'PIPELINE_DISCARDED', actor: 'admin-1', motivo: 'duplicado', ts: 1791244800,
}

function renderPantalla() {
  return render(<I18nProvider><MemoryRouter><AdminAuditoriaDescarte /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  localStorage.clear()
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { eventos: [fila], has_more: false, cursor_siguiente: null } })
})

describe('AdminAuditoriaDescarte', () => {
  it('carga el registro y muestra fecha, evento, pipeline, actor y motivo', async () => {
    renderPantalla()
    expect(await screen.findByText('proyecto de prueba')).toBeInTheDocument()
    expect(screen.getByText('admin-1')).toBeInTheDocument()
    expect(screen.getByText('duplicado')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'proyecto de prueba' })).toHaveAttribute('href', '/historial/pipe-14')
  })

  it('manda filtros y cursor; cargar más agrega la página siguiente', async () => {
    api.get.mockResolvedValueOnce({ data: { eventos: [fila], has_more: false } })
      .mockResolvedValueOnce({ data: { eventos: [fila], has_more: true, cursor_siguiente: 'c-1' } })
      .mockResolvedValueOnce({ data: { eventos: [{ ...fila, id: 13, actor: 'admin-2' }], has_more: false } })
    renderPantalla()
    await screen.findByText('admin-1')
    fireEvent.change(screen.getByLabelText(/Desde|From/), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText(/Hasta|To/), { target: { value: '2026-10-06' } })
    fireEvent.click(screen.getByRole('button', { name: /Filtrar|Filter/ }))
    await waitFor(() => expect(api.get).toHaveBeenLastCalledWith('/admin/auditoria-descarte', expect.objectContaining({ params: expect.objectContaining({ desde: '2026-10-01', hasta: '2026-10-06' }) })))
    fireEvent.click(screen.getByRole('button', { name: /Cargar más|Load more/ }))
    expect(await screen.findByText('admin-2')).toBeInTheDocument()
    expect(api.get).toHaveBeenLastCalledWith('/admin/auditoria-descarte', expect.objectContaining({ params: expect.objectContaining({ cursor: 'c-1' }) }))
  })

  it('presenta estados vacío y error traducibles', async () => {
    api.get.mockResolvedValueOnce({ data: { eventos: [], has_more: false } })
    const { unmount } = renderPantalla()
    expect(await screen.findByText(/No hay eventos|No events/)).toBeInTheDocument()
    unmount()
    api.get.mockRejectedValueOnce(new Error('fallo'))
    renderPantalla()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
