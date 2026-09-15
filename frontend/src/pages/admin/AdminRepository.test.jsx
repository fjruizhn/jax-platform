import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// I-1 (revisión final PR 2, 2026-09-14): los encabezados de la tabla
// ('Nombre', 'Tamaño', 'Modificado') y el locale de la fecha ('es-HN')
// estaban fijos, sin pasar por i18n -- en inglés la tabla salía en español.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), delete: vi.fn() } }))

// Task 1 (2026-09-15): borrar pasa por ConfirmacionSuma, que necesita
// addToast del store para el toast de error (mismo patrón que AdminUsers).
const addToastMock = vi.fn()
vi.mock('../../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ addToast: addToastMock }),
}))

import api from '../../api/client'
import AdminRepository from './AdminRepository'
import { I18nProvider } from '../../i18n/index.jsx'

// Resuelve la suma al azar del diálogo, igual que en AdminUsers.test.jsx.
function resolverSuma(dialogo) {
  const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
  fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
}

const ARCHIVO = { path: 'documents/informe.pdf', name: 'informe.pdf', size: 2048, modified: '2026-03-14T18:30:00Z' }
const FOLDERS = { missions: [], pipelines: [], documents: [ARCHIVO], images: [] }

function renderRepo() {
  return render(<I18nProvider><AdminRepository /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { folders: FOLDERS } })
  api.delete.mockReset()
  addToastMock.mockReset()
  localStorage.clear()
})

describe('AdminRepository -- i18n (I-1)', () => {
  it('en inglés, los encabezados de la tabla salen en inglés (no fijos en español)', async () => {
    const { default: es } = await import('../../i18n/es.js')
    const { default: en } = await import('../../i18n/en.js')
    expect(es.adminRepoColName).not.toBe(en.adminRepoColName)

    localStorage.setItem('jax_lang', 'en')
    renderRepo()
    await screen.findByText('informe.pdf')
    expect(screen.getByText(en.adminRepoColName)).toBeInTheDocument()
    expect(screen.getByText(en.adminRepoColSize)).toBeInTheDocument()
    expect(screen.getByText(en.adminRepoColModified)).toBeInTheDocument()
    expect(screen.queryByText('Nombre')).not.toBeInTheDocument()
    expect(screen.queryByText('Modificado')).not.toBeInTheDocument()
  })

  it('la fecha sigue el idioma activo (en-US en inglés, no es-HN fijo)', async () => {
    localStorage.setItem('jax_lang', 'en')
    renderRepo()
    await screen.findByText('informe.pdf')
    const esperado = new Date(ARCHIVO.modified).toLocaleString('en-US')
    const fijoEsHN = new Date(ARCHIVO.modified).toLocaleString('es-HN')
    await waitFor(() => expect(screen.getByText(esperado)).toBeInTheDocument())
    if (esperado !== fijoEsHN) {
      expect(screen.queryByText(fijoEsHN)).not.toBeInTheDocument()
    }
  })
})

// M-3 (revisión final PR 2, 2026-09-14): prose-invert es de
// @tailwindcss/typography, que no está instalado (plugins: [] en
// tailwind.config.js) -- hoy no hace nada, pero forzaría texto claro en el
// tema claro si el plugin se agregara. El texto del preview debe pintar con
// el token text-texto, no con una clase muerta de un plugin ausente.
describe('AdminRepository -- preview markdown sin prose-invert (M-3)', () => {
  it('el contenedor del preview markdown no lleva prose ni prose-invert, y usa text-texto', async () => {
    api.get.mockImplementation((url) => {
      if (url.startsWith('/admin/repo/file')) {
        return Promise.resolve({ data: { type: 'markdown', content: '# Hola' } })
      }
      return Promise.resolve({ data: { folders: FOLDERS } })
    })
    renderRepo()
    fireEvent.click(await screen.findByText('Preview'))
    const encabezado = await screen.findByRole('heading', { name: 'Hola' })
    const contenedor = encabezado.parentElement
    expect(contenedor.className).not.toMatch(/(^|\s)prose(-\S+)?(\s|$)/)
    expect(contenedor.className).toMatch(/(^|\s)text-texto(\s|$)/)
  })
})

// Task 1 (2026-09-15): la confirmación de borrado pasa por ConfirmacionSuma,
// con el mismo patrón que la baja en AdminUsers -- window.confirm queda fuera
// de todo src (grep en el reporte de la tarea).
describe('AdminRepository -- borrar pasa por ConfirmacionSuma, no por window.confirm', () => {
  it('borrar exige la suma antes de llamar a la API', async () => {
    api.delete.mockResolvedValue({})
    render(<I18nProvider><AdminRepository /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Eliminar' }))
    const dialogo = screen.getByRole('dialog', { name: 'Eliminar informe.pdf' })
    const confirmar = within(dialogo).getByRole('button', { name: 'Eliminar' })
    expect(confirmar).toBeDisabled()
    resolverSuma(dialogo)
    expect(api.delete).not.toHaveBeenCalled()
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/admin/repo/file?path=documents%2Finforme.pdf'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('Cancelar no borra', async () => {
    render(<I18nProvider><AdminRepository /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Eliminar' }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Cancelar' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('Escape no borra', async () => {
    render(<I18nProvider><AdminRepository /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Eliminar' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('si hay error, el diálogo sigue abierto y avisa traducido', async () => {
    api.delete.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminRepository /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Eliminar' }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Eliminar' }))
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'error', message: 'No se pudo completar la acción.',
    }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
