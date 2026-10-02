import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
vi.mock('../../api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})) } }))

import AdminSidebar from './AdminSidebar'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'
import { useApariencia } from '../../store/useApariencia'

function renderSidebar() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <AdminSidebar />
      </MemoryRouter>
    </I18nProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
})

// La versión se pide a /api/version y se ata en src/version.test.jsx.

// system_name (frente C, 2026-09-16): la cabecera y el enlace de vuelta
// llevan el nombre del sistema en vez de "Axioma" fijo.
describe('AdminSidebar', () => {
  it('la cabecera y el enlace de vuelta usan el nombre del sistema', () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<I18nProvider><MemoryRouter><AdminSidebar /></MemoryRouter></I18nProvider>)
    expect(screen.getByText('Hal')).toBeInTheDocument()
    expect(screen.getByText(es.adminBack('Hal'))).toBeInTheDocument()
  })
})

// Orden pedido por Fernando (2026-10-02, con captura). Sustituye al de
// 2026-09-20 (Memoria 2ª): ahora Memoria va 5ª y Proyectos 6ª.
const ORDEN = [
  'adminDashboard', 'adminFacetsModels', 'adminCosts', 'adminRepo', 'adminMemoria',
  'adminProyectos', 'adminUsers', 'adminPipelinesOcultos', 'adminSettings', 'adminSmtp',
]

describe('AdminSidebar -- orden del menú (2026-10-02)', () => {
  it('Dashboard · Facetas · Costos · Repositorio · Memoria · Proyectos · Usuarios · Pipelines ocultos · Configuración · Correo', () => {
    renderSidebar()
    // Los enlaces de navegación; el último (volver) no es del menú.
    const enlaces = screen.getAllByRole('link').slice(0, ORDEN.length).map((a) => a.textContent)
    ORDEN.forEach((clave, i) => {
      expect(enlaces[i], `posición ${i + 1}`).toContain(es[clave])
    })
    expect(screen.getAllByRole('link')).toHaveLength(ORDEN.length + 1)
  })

  it('las etiquetas de Proyectos existen en es y en', () => {
    expect(es.adminProyectos).toBe('Proyectos')
    expect(en.adminProyectos).toBe('Projects')
  })

  it('Memoria es una ruta relativa del caparazón (/admin/memoria), no absoluta', () => {
    renderSidebar()
    const enlaceMemoria = screen.getByRole('link', { name: new RegExp(es.adminMemoria) })
    expect(enlaceMemoria).toHaveAttribute('href', '/admin/memoria')
  })

  it('Proyectos lleva a la pantalla de proyectos que ya existe (/proyectos)', () => {
    renderSidebar()
    const enlace = screen.getByRole('link', { name: new RegExp(es.adminProyectos) })
    expect(enlace).toHaveAttribute('href', '/proyectos')
  })
})

// Fernando (2026-10-02): ⚙ y ✉ se veían grises (símbolos de texto, no emoji).
// Un glifo con presentación de emoji por defecto (Emoji_Presentation) sale de
// color sin depender de un selector de variación ni de la fuente.
describe('AdminSidebar -- íconos de color (2026-10-02)', () => {
  const claves = ['adminDashboard', 'adminSettings', 'adminSmtp', 'adminProyectos']
  for (const clave of claves) {
    it(`${clave} usa un emoji de color por defecto`, () => {
      renderSidebar()
      const enlace = screen.getAllByRole('link').find((a) => a.textContent.includes(es[clave]))
      const icono = enlace.querySelector('span').textContent
      expect(icono).toMatch(/^\p{Emoji_Presentation}$/u)
    })
  }

  it('el detector rechaza los símbolos monocromos que había', () => {
    for (const mono of ['\u2699', '\u2709']) {
      expect(mono).not.toMatch(/^\p{Emoji_Presentation}$/u)
    }
  })
})
