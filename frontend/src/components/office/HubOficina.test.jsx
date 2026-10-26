// OP 358 pieza 3 (hub 2D): la galería de salas muestra las siete verticales
// con su estado, la Agencia es la única con captura real, y el clic abre el
// detalle en Dialogo (única ventana propia del ecosistema para modales).
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'
import HubOficina from './HubOficina'

const NOMBRES = [
  'Junta Directiva',
  'Agencia · Susan Kare Studio',
  'Bufete Jurídico',
  'Despacho de Arquitectura e Ingeniería',
  'Despacho Contable',
  'Banca de Inversión y Finanzas',
  'Biblioteca',
]

describe('Hub de salas de la oficina', () => {
  it('muestra las siete salas con sus estados y la captura de la agencia', () => {
    render(<I18nProvider><HubOficina /></I18nProvider>)
    expect(screen.getByRole('heading', { name: 'Salas de la oficina' })).toBeInTheDocument()
    NOMBRES.forEach((nombre) => expect(screen.getByRole('button', { name: new RegExp(nombre) })).toBeInTheDocument())
    const foto = screen.getByRole('img', { name: 'Vista de la oficina 3D del estudio' })
    expect(foto).toHaveAttribute('src', '/salas/agencia.jpg')
    expect(screen.getByText('Disponible')).toBeInTheDocument()
    expect(screen.getAllByText('Próximamente')).toHaveLength(5)
    expect(screen.getByText('Planeada')).toBeInTheDocument()
  })

  it('abre el detalle de la sala al clic y lo cierra desde su botón', () => {
    render(<I18nProvider><HubOficina /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Banca de Inversión/ }))
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveTextContent('Banca de Inversión y Finanzas')
    expect(dialogo).toHaveTextContent('Análisis de crédito y modelos financieros.')
    expect(dialogo).toHaveTextContent('Próximamente')
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
