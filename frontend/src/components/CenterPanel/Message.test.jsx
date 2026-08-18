import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import '@testing-library/jest-dom'
import Message from './Message'
import { I18nProvider } from '../../i18n/index.jsx'

function renderMessage(message) {
  return render(
    <I18nProvider>
      <Message message={message} />
    </I18nProvider>
  )
}

describe('Message contract degradation footnote', () => {
  it('muestra la nota cuando contract_degraded es true', () => {
    renderMessage({
      facet: 'jekyll',
      content: 'respuesta cruda sin parsear',
      contract_degraded: true,
    })
    expect(screen.getByText(/no cumplió el formato esperado/i)).toBeInTheDocument()
  })

  it('no muestra la nota cuando contract_degraded es false', () => {
    renderMessage({
      facet: 'jekyll',
      content: 'respuesta normal',
      contract_degraded: false,
    })
    expect(screen.queryByText(/no cumplió el formato esperado/i)).not.toBeInTheDocument()
  })

  it('no muestra la nota cuando contract_degraded no está presente (mensajes viejos)', () => {
    renderMessage({ facet: 'jekyll', content: 'mensaje de antes de este cambio' })
    expect(screen.queryByText(/no cumplió el formato esperado/i)).not.toBeInTheDocument()
  })
})
