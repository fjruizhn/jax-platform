import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { act } from 'react'
import { I18nProvider } from '../i18n/index.jsx'
import es from '../i18n/es.js'
import { useApariencia } from '../store/useApariencia'
import TituloDePagina from './TituloDePagina'

// 2026-10-02 (Principio IV): el título de la pestaña y la meta description se
// arman en tiempo de ejecución con el nombre configurado; el HTML es solo el
// respaldo inicial.
function descripcion() {
  return document.querySelector('meta[name="description"]')?.getAttribute('content')
}

beforeEach(() => {
  localStorage.clear()
  document.title = 'viejo'
  document.head.innerHTML = '<meta name="description" content="viejo">'
  useApariencia.setState({ systemName: null, langDefault: null })
})

describe('TituloDePagina', () => {
  it('con nombre configurado, título y descripción llevan ese nombre', () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<I18nProvider><TituloDePagina /></I18nProvider>)
    expect(document.title).toBe(es.tituloPagina('Hal'))
    expect(document.title).toContain('Hal')
    expect(descripcion()).toBe(es.metaDescripcion('Hal'))
    expect(descripcion()).toContain('Hal')
  })

  it('sin nombre configurado usa brandName de i18n', () => {
    render(<I18nProvider><TituloDePagina /></I18nProvider>)
    expect(document.title).toContain(es.brandName)
  })

  it('un solo escritor: fijar() del store llega al título solo a través del componente', () => {
    render(<I18nProvider><TituloDePagina /></I18nProvider>)
    act(() => useApariencia.getState().fijar({ system_name: 'Lab' }))
    expect(document.title).toBe(es.tituloPagina('Lab'))
    const fuente = readFileSync(join(process.cwd(), 'src/store/useApariencia.js'), 'utf8')
    expect(fuente).not.toMatch(/document\.title/)
  })

  it('sigue al nombre cuando cambia', () => {
    render(<I18nProvider><TituloDePagina /></I18nProvider>)
    act(() => useApariencia.setState({ systemName: 'Lab' }))
    expect(document.title).toContain('Lab')
  })
})

describe('index.html: solo respaldo inicial', () => {
  const html = readFileSync(join(process.cwd(), 'index.html'), 'utf8')
  it('el <title> es la marca de respaldo (brandName), no otro texto', () => {
    expect(html.match(/<title>([^<]*)<\/title>/)[1]).toBe(es.brandName)
  })
  it('la meta description no lleva la marca escrita', () => {
    const d = html.match(/<meta name="description" content="([^"]*)"/)[1]
    expect(d).not.toMatch(/axioma/i)
  })
})
