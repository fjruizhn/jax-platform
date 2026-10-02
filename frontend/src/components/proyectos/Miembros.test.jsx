import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/proyectos', () => ({
  buscarCandidatos: vi.fn(),
  invitarMiembro: vi.fn(),
  cambiarPapel: vi.fn(),
  quitarMiembro: vi.fn(),
}))

import * as api from '../../api/proyectos'
import Miembros from './Miembros'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const T = es.proyectos
const PROY = { id: 7, estado: 'ACTIVE', papel: 'OWNER' }
const U = (n) => ({ user_id: 100 + n, email: `u${n}@x.com` })
const CINCO = [U(1), U(2), U(3), U(4), U(5)]

function error(status, code) {
  return Object.assign(new Error('x'), { response: { status, data: { detail: { code } } } })
}

function montar(props = {}) {
  const onCambio = vi.fn().mockResolvedValue()
  const r = render(
    <I18nProvider>
      <Miembros proyecto={PROY} miembros={[]} onCambio={onCambio} {...props} />
    </I18nProvider>,
  )
  return { ...r, onCambio }
}

const casilla = (email) => screen.getByRole('checkbox', { name: email })
const agregar = (n) => screen.getByRole('button', { name: T.agregarSeleccionados(n) })

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset())
  api.buscarCandidatos.mockResolvedValue({ candidatos: CINCO })
  api.invitarMiembro.mockResolvedValue({})
})
afterEach(() => { vi.useRealTimers() })

describe('Miembros: lista de elegibles con casillas', () => {
  it('la lista se carga sin escribir nada, con q vacío', async () => {
    montar()
    expect(await screen.findByRole('checkbox', { name: 'u1@x.com' })).toBeInTheDocument()
    expect(api.buscarCandidatos).toHaveBeenCalledWith(7, '')
    expect(screen.getAllByRole('checkbox')).toHaveLength(5)
  })

  it('el buscador filtra contra el backend con q, tras 300 ms y una sola vez por ráfaga (sin mínimo de letras)', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    api.buscarCandidatos.mockClear()
    vi.useFakeTimers()
    const campo = screen.getByLabelText(T.buscarPorEmail)
    fireEvent.change(campo, { target: { value: 'u' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(299) })
    expect(api.buscarCandidatos).not.toHaveBeenCalled()
    fireEvent.change(campo, { target: { value: 'u3' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(299) })
    expect(api.buscarCandidatos).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(2) })
    expect(api.buscarCandidatos).toHaveBeenCalledTimes(1)
    expect(api.buscarCandidatos).toHaveBeenCalledWith(7, 'u3')
  })

  it('descarta la respuesta tardía de una búsqueda anterior', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let resolverViejo
    api.buscarCandidatos
      .mockImplementationOnce(() => new Promise((r) => { resolverViejo = r }))
      .mockResolvedValueOnce({ candidatos: [{ user_id: 5, email: 'nueva@x.com' }] })
    const campo = screen.getByLabelText(T.buscarPorEmail)
    fireEvent.change(campo, { target: { value: 'vie' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    fireEvent.change(campo, { target: { value: 'nue' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    expect(await screen.findByRole('checkbox', { name: 'nueva@x.com' })).toBeInTheDocument()
    await act(async () => { resolverViejo({ candidatos: [{ user_id: 6, email: 'vieja@x.com' }] }) })
    expect(screen.queryByRole('checkbox', { name: 'vieja@x.com' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'nueva@x.com' })).toBeInTheDocument()
  })

  it('anuncia la cantidad seleccionada y deshabilita el botón con 0', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    expect(agregar(0)).toBeDisabled()
    fireEvent.click(casilla('u1@x.com'))
    fireEvent.click(casilla('u2@x.com'))
    expect(screen.getByText(T.seleccionados(2))).toHaveAttribute('aria-live', 'polite')
    expect(agregar(2)).toBeEnabled()
  })

  it('marcar 3 y agregar hace 3 llamadas, con el papel elegido, y recarga miembros y candidatos', async () => {
    const { onCambio } = montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    for (const n of [1, 3, 5]) fireEvent.click(casilla(`u${n}@x.com`))
    fireEvent.change(screen.getByLabelText(T.papel), { target: { value: 'CONTRIBUTOR' } })
    api.buscarCandidatos.mockClear()
    fireEvent.click(agregar(3))
    await waitFor(() => expect(api.invitarMiembro).toHaveBeenCalledTimes(3))
    expect(api.invitarMiembro.mock.calls.map((c) => c[1])).toEqual([
      { email: 'u1@x.com', papel: 'CONTRIBUTOR' },
      { email: 'u3@x.com', papel: 'CONTRIBUTOR' },
      { email: 'u5@x.com', papel: 'CONTRIBUTOR' },
    ])
    expect(api.invitarMiembro.mock.calls.every((c) => c[0] === 7)).toBe(true)
    await waitFor(() => expect(onCambio).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(api.buscarCandidatos).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(T.resumenAgregados(3))).toBeInTheDocument()
  })

  it('las llamadas van en secuencia, no en paralelo', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    const resolvers = []
    api.invitarMiembro.mockImplementation(() => new Promise((r) => { resolvers.push(r) }))
    fireEvent.click(casilla('u1@x.com'))
    fireEvent.click(casilla('u2@x.com'))
    fireEvent.click(agregar(2))
    await waitFor(() => expect(resolvers).toHaveLength(1))
    await act(async () => { await Promise.resolve() })
    expect(api.invitarMiembro).toHaveBeenCalledTimes(1)
    await act(async () => { resolvers[0]({}) })
    await waitFor(() => expect(resolvers).toHaveLength(2))
    await act(async () => { resolvers[1]({}) })
  })

  it('un fallo parcial muestra resumen con el email y el error traducido', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    api.invitarMiembro
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(error(409, 'ya_es_miembro'))
      .mockRejectedValueOnce(error(422, 'usuario_no_elegible'))
    for (const n of [1, 2, 3]) fireEvent.click(casilla(`u${n}@x.com`))
    fireEvent.click(agregar(3))
    expect(await screen.findByText(T.resumenAgregados(1))).toBeInTheDocument()
    expect(screen.getByText(`u2@x.com: ${T.errores.ya_es_miembro}`)).toBeInTheDocument()
    expect(screen.getByText(`u3@x.com: ${T.errores.usuario_no_elegible}`)).toBeInTheDocument()
    // lo que falló queda marcado; lo que entró, no
    expect(api.invitarMiembro).toHaveBeenCalledTimes(3)
  })

  it('un código desconocido cae en el texto genérico', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    api.invitarMiembro.mockRejectedValueOnce(error(500, 'inventado'))
    fireEvent.click(casilla('u1@x.com'))
    fireEvent.click(agregar(1))
    expect(await screen.findByText(`u1@x.com: ${T.errores.generico}`)).toBeInTheDocument()
  })

  it('un doble clic no duplica y el botón queda deshabilitado en vuelo', async () => {
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    let resolver
    api.invitarMiembro.mockImplementation(() => new Promise((r) => { resolver = r }))
    fireEvent.click(casilla('u1@x.com'))
    const boton = agregar(1)
    fireEvent.click(boton)
    fireEvent.click(boton)
    await waitFor(() => expect(boton).toBeDisabled())
    // un envío forzado del formulario (Enter, o un clic que se cuela) tampoco duplica
    fireEvent.submit(boton.closest('form'))
    expect(api.invitarMiembro).toHaveBeenCalledTimes(1)
    await act(async () => { resolver({}) })
    await waitFor(() => expect(api.invitarMiembro).toHaveBeenCalledTimes(1))
  })

  it('con 100 candidatos avisa que se muestran los primeros 100; con menos, no', async () => {
    api.buscarCandidatos.mockResolvedValue({ candidatos: Array.from({ length: 100 }, (_, i) => U(i + 1)) })
    const { unmount } = montar()
    expect(await screen.findByText(T.mostrandoPrimeros100)).toBeInTheDocument()
    unmount()
    api.buscarCandidatos.mockResolvedValue({ candidatos: CINCO })
    montar()
    await screen.findByRole('checkbox', { name: 'u1@x.com' })
    expect(screen.queryByText(T.mostrandoPrimeros100)).not.toBeInTheDocument()
  })

  it('solo un OWNER en proyecto ACTIVE ve la sección y se consulta', async () => {
    for (const proyecto of [{ ...PROY, papel: 'VIEWER' }, { ...PROY, papel: 'CONTRIBUTOR' }, { ...PROY, estado: 'ARCHIVED' }]) {
      const { unmount } = montar({ proyecto })
      expect(screen.queryByLabelText(T.buscarPorEmail)).not.toBeInTheDocument()
      expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
      unmount()
    }
    expect(api.buscarCandidatos).not.toHaveBeenCalled()
  })

  it('sin elegibles muestra el texto de vacío', async () => {
    api.buscarCandidatos.mockResolvedValue({ candidatos: [] })
    montar()
    expect(await screen.findByText(T.sinCandidatos)).toBeInTheDocument()
  })

  it('los textos nuevos existen en es y en', () => {
    for (const L of [es.proyectos, en.proyectos]) {
      expect(typeof L.agregarSeleccionados(2)).toBe('string')
      expect(typeof L.seleccionados(2)).toBe('string')
      expect(typeof L.resumenAgregados(2)).toBe('string')
      expect(typeof L.mostrandoPrimeros100).toBe('string')
    }
  })
})
