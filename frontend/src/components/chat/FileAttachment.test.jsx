import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import '@testing-library/jest-dom'
import FileAttachment from './FileAttachment'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

const pintar = (props) => render(<I18nProvider><FileAttachment onRemove={() => {}} {...props} /></I18nProvider>)

// RD4 (2026-09-17): el adjunto llega por referencia; la vista previa de la
// imagen es un object URL local (`previewUrl`), nunca mime+base64.
describe('FileAttachment -- vista previa local por id (RD4)', () => {
  it('imagen: vista previa desde el object URL local del compositor', () => {
    pintar({ attachment: { tipo: 'imagen', nombre: 'f.png', previewUrl: 'blob:compositor-1' } })
    expect(screen.getByAltText('f.png')).toHaveAttribute('src', 'blob:compositor-1')
  })

  it('texto: nombre, vista previa acotada y aviso de recortado', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'pdf', nombre: 'i.pdf', vista_previa: 'ventas de julio', recortado: true } })
    expect(screen.getByText('i.pdf')).toBeInTheDocument()
    expect(screen.getByText('ventas de julio')).toBeInTheDocument()
    expect(screen.getByText(es.adjuntoRecortado)).toBeInTheDocument()
  })

  it('sin recorte no muestra el aviso', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'texto', nombre: 'n.txt', vista_previa: 'algo', recortado: false } })
    expect(screen.queryByText(es.adjuntoRecortado)).not.toBeInTheDocument()
  })

  it('la vista previa de texto se muestra como texto plano, nunca como HTML', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'texto', nombre: 'n.txt', vista_previa: '<img src=x onerror="alert(1)">' } })
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument()
    expect(document.querySelector('img[onerror]')).toBeNull()
  })

  it('sin nombre muestra el texto i18n, no un vacío', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'texto', nombre: '', vista_previa: '' } })
    expect(screen.getByText(es.altAttachment)).toBeInTheDocument()
  })
})
