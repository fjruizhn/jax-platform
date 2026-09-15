import { render, screen, waitFor } from '@testing-library/react'
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
