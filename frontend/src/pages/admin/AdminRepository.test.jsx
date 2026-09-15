import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// I-1 (revisión final PR 2, 2026-09-14): los encabezados de la tabla
// ('Nombre', 'Tamaño', 'Modificado') y el locale de la fecha ('es-HN')
// estaban fijos, sin pasar por i18n -- en inglés la tabla salía en español.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), delete: vi.fn() } }))

import api from '../../api/client'
import AdminRepository from './AdminRepository'
import { I18nProvider } from '../../i18n/index.jsx'

const ARCHIVO = { path: 'documents/informe.pdf', name: 'informe.pdf', size: 2048, modified: '2026-03-14T18:30:00Z' }
const FOLDERS = { missions: [], pipelines: [], documents: [ARCHIVO], images: [] }

function renderRepo() {
  return render(<I18nProvider><AdminRepository /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { folders: FOLDERS } })
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
