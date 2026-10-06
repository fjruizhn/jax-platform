import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import '@testing-library/jest-dom'
import FileAttachment from './FileAttachment'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

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

describe('FileAttachment scanned PDF status', () => {
  it('announces the real OCR failure in the live status region', () => {
    pintar({ attachment: { tipo: 'pdf_procesando', nombre: 'scan.pdf', estado: 'error', error: 'ocr_sin_texto' } })
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByRole('status')).toHaveTextContent(es.proyectos.documentos.causas.ocr_sin_texto)
    expect(screen.getByRole('status')).not.toHaveTextContent('ocr_sin_texto')
    expect(screen.getByText(es.erroresMesa.pdf_procesando_selector_libre)).toBeInTheDocument()
  })

  it('never renders an unknown internal OCR error code', () => {
    pintar({ attachment: { tipo: 'pdf_procesando', nombre: 'scan.pdf', estado: 'error', error: 'future_private_code' } })
    expect(screen.getByRole('status')).toHaveTextContent(es.proyectos.documentos.causas.desconocida)
    expect(screen.getByRole('status')).not.toHaveTextContent('future_private_code')
  })
})

describe('FileAttachment scanned PDF -- estado en curso', () => {
  const ESTADOS_EN_CURSO = ['en_cola', 'pendiente', 'procesando']

  it.each(ESTADOS_EN_CURSO)('no dice «%s» listo para enviar: el PDF todavía no es un adjunto del chat', (estado) => {
    pintar({ attachment: { tipo: 'pdf_procesando', nombre: 'scan.pdf', estado } })
    expect(screen.queryByText(es.attachReady)).not.toBeInTheDocument()
    expect(screen.queryByText(es.erroresMesa.pdf_procesando_selector_libre)).not.toBeInTheDocument()
  })

  it('un adjunto de texto sí muestra «listo» (control positivo de la guarda)', () => {
    pintar({ attachment: { tipo: 'texto', origen: 'texto', nombre: 'n.txt', vista_previa: 'algo' } })
    expect(screen.getByText(es.attachReady)).toBeInTheDocument()
  })

  it.each(ESTADOS_EN_CURSO)('muestra el estado «%s» traducido, nunca el código crudo', (estado) => {
    pintar({ attachment: { tipo: 'pdf_procesando', nombre: 'scan.pdf', estado } })
    const texto = es.proyectos.documentos.estados[estado]
    expect(texto).not.toBe(estado)
    expect(screen.getByRole('status')).toHaveTextContent(`(${texto})`)
    expect(screen.getByRole('status')).not.toHaveTextContent(`(${estado})`)
  })

  it('un estado que este cliente no conoce se muestra con el texto traducido de «desconocido»', () => {
    pintar({ attachment: { tipo: 'pdf_procesando', nombre: 'scan.pdf', estado: 'estado_del_futuro' } })
    expect(screen.getByRole('status')).toHaveTextContent(
      `Estado del documento: ${es.proyectos.documentos.estados.desconocido}.`)
    expect(screen.getByRole('status')).not.toHaveTextContent('estado_del_futuro')
  })

  it('«desconocido» existe en español e inglés (no se cae al literal de respaldo)', () => {
    for (const idioma of [es, en]) {
      expect(typeof idioma.proyectos.documentos.estados.desconocido).toBe('string')
      expect(idioma.proyectos.documentos.estados.desconocido).not.toBe('')
    }
    expect(es.proyectos.documentos.estados.desconocido).not.toBe(en.proyectos.documentos.estados.desconocido)
  })
})
