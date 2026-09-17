import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))

import api from '../../api/client'
import BottomBar from './BottomBar'
import CenterPanel from '../CenterPanel/CenterPanel'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import { useEjecutor } from '../../store/useEjecutor'
import es from '../../i18n/es.js'

// Modo Ejecutor en la barra (SP2, 2026-09-17): se AGREGA junto a Chat,
// Comando, Pipeline e Imagen; sólo lo ve un superadmin.
// jsdom no implementa scrollIntoView (CenterPanel baja al último mensaje).
Element.prototype.scrollIntoView = vi.fn()

const JAX_INICIAL = useJaxStore.getState()
const INICIAL = useEjecutor.getState()
const tx = es.ejecutor

function como(role) {
  useJaxStore.setState({ ...JAX_INICIAL, token: 'tok', user: { user_id: '1', role }, messages: [{ id: 'x', facet: 'user', content: 'mensaje del chat', timestamp: '2026-09-17T10:00:00Z' }] }, true)
}

function pintar() {
  return render(<I18nProvider><CenterPanel /><BottomBar /></I18nProvider>)
}

beforeEach(() => {
  useEjecutor.getState().reiniciar()
  useEjecutor.setState(INICIAL, true)
  api.post.mockReset()
  api.get.mockReset()
  api.get.mockImplementation(() => Promise.resolve({ data: {} }))
  localStorage.clear()
})

describe('BottomBar -- modo Ejecutor', () => {
  it('un operator no ve el modo Ejecutor; los cuatro modos de siempre siguen', () => {
    como('operator')
    pintar()
    expect(screen.queryByRole('button', { name: tx.modo })).not.toBeInTheDocument()
    for (const m of [es.modeChat, es.modeComando, es.modePipeline, es.modeImagen]) {
      expect(screen.getByRole('button', { name: m })).toBeInTheDocument()
    }
  })

  it('un superadmin lo ve junto a Comando (que no se renombra)', () => {
    como('superadmin')
    pintar()
    expect(screen.getByRole('button', { name: tx.modo })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.modeComando })).toBeInTheDocument()
  })

  it('al elegirlo, el centro muestra el panel del Ejecutor en vez de los mensajes y no hay adjuntos', async () => {
    como('superadmin')
    pintar()
    expect(screen.getByText('mensaje del chat')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: tx.modo }))
    expect(useEjecutor.getState().activo).toBe(true)
    expect(await screen.findByRole('heading', { name: tx.titulo })).toBeInTheDocument()
    expect(screen.queryByText('mensaje del chat')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(tx.placeholderNueva)).toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: es.modeChat }))
    expect(useEjecutor.getState().activo).toBe(false)
    expect(screen.getByText('mensaje del chat')).toBeInTheDocument()
  })

  it('enviar crea una misión con las máquinas elegidas, no un comando ni un chat', async () => {
    como('superadmin')
    api.post.mockResolvedValue({ data: { id: 'm1', objetivo: 'ver memoria', maquinas: ['ejecutor-prueba'], estado: 'completada', puede_continuar: true, turnos: [] } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: tx.modo }))
    useEjecutor.setState({ seleccion: ['ejecutor-prueba'] })
    fireEvent.change(screen.getByPlaceholderText(tx.placeholderNueva), { target: { value: 'ver memoria' } })
    fireEvent.click(screen.getByRole('button', { name: es.send }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/ejecutor/misiones', { objetivo: 'ver memoria', maquinas: ['ejecutor-prueba'] }))
    expect(api.post).not.toHaveBeenCalledWith('/command', expect.anything())
    expect(api.post).not.toHaveBeenCalledWith('/chat', expect.anything())
  })

  it('con una misión que puede continuar, el texto es el turno siguiente', async () => {
    como('superadmin')
    api.post.mockResolvedValue({ data: { id: 'm1', estado: 'en_curso', puede_continuar: false, turnos: [] } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: tx.modo }))
    useEjecutor.setState({ idActiva: 'm1', misionActiva: { id: 'm1', objetivo: 'o', maquinas: [], estado: 'completada', puede_continuar: true, turnos: [] } })
    fireEvent.change(await screen.findByPlaceholderText(tx.placeholderTurno), { target: { value: 'ahora el disco' } })
    fireEvent.click(screen.getByRole('button', { name: es.send }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/ejecutor/misiones/m1/turnos', { instruccion: 'ahora el disco' }))
    useEjecutor.getState().detenerPolling()
  })

  it('un 403 objeto se ve traducido en el panel y el texto vuelve a la caja', async () => {
    como('superadmin')
    api.post.mockRejectedValue({ response: { status: 403, data: { detail: { codigo: 'ejecutor_maquina_no_elegible', maquina: 'atemai', motivo: 'maquina_con_datos_de_clientes' } } } })
    pintar()
    fireEvent.click(screen.getByRole('button', { name: tx.modo }))
    const caja = screen.getByPlaceholderText(tx.placeholderNueva)
    fireEvent.change(caja, { target: { value: 'ver memoria' } })
    fireEvent.click(screen.getByRole('button', { name: es.send }))
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent('atemai')
    expect(alerta).toHaveTextContent(tx.motivosNoElegible.maquina_con_datos_de_clientes)
    expect(alerta).not.toHaveTextContent('[object Object]')
    expect(caja).toHaveValue('ver memoria')
  })

  it('si deja de ser superadmin, el modo se apaga y vuelven los mensajes', async () => {
    como('superadmin')
    pintar()
    fireEvent.click(screen.getByRole('button', { name: tx.modo }))
    expect(useEjecutor.getState().activo).toBe(true)
    como('operator')
    await waitFor(() => expect(useEjecutor.getState().activo).toBe(false))
    expect(screen.getByText('mensaje del chat')).toBeInTheDocument()
  })
})
