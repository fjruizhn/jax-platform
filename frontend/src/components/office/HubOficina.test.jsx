// OP 358 pieza 4 (hub 2D): el hub ya no declara las salas — renderiza el
// registro del servidor. Sólo pintan las salas presentes en el registro Y en
// el mapa visual/i18n local; una sala activa abre su vertical, las demás
// siguen informativas en Dialogo (única ventana propia del ecosistema).
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'
import HubOficina from './HubOficina'

const REGISTRO = [
  { id: 'junta', estado: 'proximamente' },
  { id: 'agencia', estado: 'activa' },
  { id: 'bufete', estado: 'proximamente' },
  { id: 'arquitectura', estado: 'proximamente' },
  { id: 'contable', estado: 'proximamente' },
  { id: 'banca', estado: 'proximamente' },
  { id: 'biblioteca', estado: 'futura' },
  // Una sala anunciada por el servidor pero sin visual/i18n local nunca se
  // renderiza a medias: el backend puede ir adelantado al frontend.
  { id: 'desconocida', estado: 'activa' },
]

describe('Hub de salas de la oficina', () => {
  it('renderiza el registro del servidor: estados, foto de la agencia y omisiones', () => {
    render(<I18nProvider><HubOficina verticales={REGISTRO} /></I18nProvider>)
    expect(screen.getByRole('heading', { name: 'Salas de la oficina' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /desconocida/i })).not.toBeInTheDocument()
    const foto = screen.getByRole('img', { name: 'Vista de la oficina 3D del estudio' })
    expect(foto).toHaveAttribute('src', '/salas/agencia.jpg')
    expect(screen.getByText('Disponible')).toBeInTheDocument()
    expect(screen.getAllByText('Próximamente')).toHaveLength(5)
    expect(screen.getByText('Planeada')).toBeInTheDocument()
  })

  it('una sala activa abre su vertical en vez del detalle informativo', () => {
    const onAbrirVertical = vi.fn()
    render(<I18nProvider><HubOficina verticales={REGISTRO} onAbrirVertical={onAbrirVertical} /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Agencia · Susan Kare Studio/ }))
    expect(onAbrirVertical).toHaveBeenCalledWith('agencia')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('una sala no activa sigue informativa: detalle en Dialogo y cierre', () => {
    const onAbrirVertical = vi.fn()
    render(<I18nProvider><HubOficina verticales={REGISTRO} onAbrirVertical={onAbrirVertical} /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Banca de Inversión/ }))
    expect(onAbrirVertical).not.toHaveBeenCalled()
    const dialogo = screen.getByRole('dialog')
    expect(dialogo).toHaveTextContent('Banca de Inversión y Finanzas')
    expect(dialogo).toHaveTextContent('Análisis de crédito y modelos financieros.')
    expect(dialogo).toHaveTextContent('Próximamente')
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
