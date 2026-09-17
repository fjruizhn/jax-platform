import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Logotipo de texto de la esquina superior izquierda (2026-09-12, pedido de
// Fernando): reemplaza "JAX | Platform v0.1", que estaba escrito a mano en
// Dashboard.jsx, sin i18n. Marca: "Axioma · Infraestructura Cognitiva Personal",
// la misma de la portada de Six Impossible Things (IBM Plex Serif, dorado).
import LogoAxioma from './LogoAxioma'
import { I18nProvider } from '../i18n/index.jsx'
import { useApariencia } from '../store/useApariencia'

const renderCon = () => render(<I18nProvider><LogoAxioma /></I18nProvider>)

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
})

describe('LogoAxioma', () => {
  it('muestra la marca y el lema en español', () => {
    renderCon()
    expect(screen.getByText('Axioma')).toBeInTheDocument()
    expect(screen.getByText('Infraestructura Cognitiva Personal')).toBeInTheDocument()
    expect(screen.queryByText(/JAX|Platform v0\.1/)).not.toBeInTheDocument()
  })

  it('en inglés traduce el lema; el nombre de la marca no cambia', () => {
    localStorage.setItem('jax_lang', 'en')
    renderCon()
    expect(screen.getByText('Axioma')).toBeInTheDocument()
    expect(screen.getByText('Personal Cognitive Infrastructure')).toBeInTheDocument()
  })

  it('el punto separador es decorativo: oculto para lectores de pantalla', () => {
    const { container } = renderCon()
    const punto = [...container.querySelectorAll('span')].find((s) => s.textContent.trim() === '·')
    expect(punto).toBeTruthy()
    expect(punto).toHaveAttribute('aria-hidden', 'true')
  })

  it('con un nombre del sistema configurado, el logotipo lo muestra en lugar de la marca', () => {
    useApariencia.setState({ systemName: 'Hal' })
    renderCon()
    expect(screen.getByText('Hal')).toBeInTheDocument()
    expect(screen.queryByText('Axioma')).not.toBeInTheDocument()
    expect(screen.getByText('Infraestructura Cognitiva Personal')).toBeInTheDocument()
  })
})
