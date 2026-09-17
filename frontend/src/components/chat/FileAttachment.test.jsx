import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import '@testing-library/jest-dom'
import FileAttachment from './FileAttachment'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

const pintar = (props) => render(<I18nProvider><FileAttachment onRemove={() => {}} {...props} /></I18nProvider>)

describe('FileAttachment -- forma nueva del adjunto', () => {
  it('imagen: vista previa desde mime + base64', () => {
    pintar({ attachment: { tipo: 'imagen', nombre: 'f.png', mime: 'image/png', base64: 'QUJD' } })
    expect(screen.getByAltText('f.png')).toHaveAttribute('src', 'data:image/png;base64,QUJD')
  })
  it('texto recortado lo avisa con texto i18n', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'pdf', nombre: 'i.pdf', contenido: 'x', recortado: true } })
    expect(screen.getByText('i.pdf')).toBeInTheDocument()
    expect(screen.getByText(es.adjuntoRecortado)).toBeInTheDocument()
  })
  it('sin nombre muestra el texto i18n, no un vacío', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'texto', nombre: '', contenido: 'x', recortado: false } })
    expect(screen.getByText(es.altAttachment)).toBeInTheDocument()
  })
})
