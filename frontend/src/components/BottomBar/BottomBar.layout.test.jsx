import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))

import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

function renderBar() {
  return render(
    <I18nProvider>
      <BottomBar />
    </I18nProvider>
  )
}

// Pedido de Fernando (2026-09-22): los botones de modo van en su PROPIA fila,
// arriba de la caja de escritura -- no comparten contenedor con el textarea.
// Antes vivían los dos en el mismo `div.items-end` (la fila de la caja), lo
// que le robaba ancho a la caja al crecer.
describe('BottomBar -- los modos van en su propia fila, sobre la caja', () => {
  it('el grupo de modos no comparte contenedor con el textarea', () => {
    const { container } = renderBar()
    const filaCaja = container.querySelector('[data-testid="fila-caja"]')
    const botonChat = screen.getByRole('button', { name: es.modeChat })
    expect(filaCaja).not.toBeNull()
    expect(filaCaja.contains(botonChat)).toBe(false)
  })

  it('el grupo de modos precede al textarea en el documento', () => {
    const { container } = renderBar()
    const filaModos = container.querySelector('[data-testid="fila-modos"]')
    const textarea = container.querySelector('textarea')
    expect(filaModos).not.toBeNull()
    // eslint-disable-next-line no-bitwise
    expect(filaModos.compareDocumentPosition(textarea) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('la fila de la caja sigue trayendo adjuntar, textarea, enviar y kill', () => {
    const { container } = renderBar()
    const filaCaja = container.querySelector('[data-testid="fila-caja"]')
    expect(filaCaja.querySelector('textarea')).not.toBeNull()
    expect(filaCaja.querySelector('input[type="file"]')).not.toBeNull()
    expect(filaCaja).toContainElement(screen.getByRole('button', { name: 'Enviar' }))
  })
})
