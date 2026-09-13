import { render } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// El ojo de HAL pasa del centro a la esquina superior izquierda (2026-09-12,
// pedido de Fernando): el centro queda entero para la conversación.
import LeftPanel from './LeftPanel/LeftPanel'
import CenterPanel from './CenterPanel/CenterPanel'
import { I18nProvider } from '../i18n/index.jsx'

const conI18n = (ui) => render(<I18nProvider>{ui}</I18nProvider>)

beforeEach(() => {
  localStorage.clear()
  // jsdom no implementa scrollIntoView y CenterPanel lo llama al montar (baja
  // al último mensaje): sin esto el test falla por el entorno, no por el ojo.
  Element.prototype.scrollIntoView = () => {}
})

describe('ubicación del ojo de HAL', () => {
  it('está en el panel izquierdo, arriba de FACETAS', () => {
    const { container } = conI18n(<LeftPanel />)
    const ojo = container.querySelector('svg.hal-eye-svg')
    expect(ojo).toBeInTheDocument()
    const facetas = [...container.querySelectorAll('h2')].find((h) => /facetas/i.test(h.textContent))
    expect(facetas).toBeTruthy()
    // el ojo va ANTES que el título de facetas en el documento
    expect(ojo.compareDocumentPosition(facetas) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('ya no está en el panel central', () => {
    const { container } = conI18n(<CenterPanel />)
    expect(container.querySelector('svg.hal-eye-svg')).not.toBeInTheDocument()
  })
})
