import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/proyectos', () => ({
  listarDocumentos: vi.fn(),
  ocultarDocumento: vi.fn(),
  restaurarDocumento: vi.fn(),
  limitesDeDocumentos: vi.fn(),
  subirDocumentos: vi.fn(),
}))

import * as api from '../../api/proyectos'
import Documentos from './Documentos'
import { puedeVerOcultos, puedeModificarDocumentos } from './permisos'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'

const T = es.proyectos.documentos
const PROY = { id: 7, estado: 'ACTIVE', papel: 'OWNER' }

function doc(id, extra = {}) {
  return {
    id, nombre: `doc${id}.pdf`, bytes: 2048, tipo: 'pdf', estado: 'listo', error: null,
    subido_por_email: 'ana@x.com', creado: '2026-10-01T12:30:00', oculto: false, ...extra,
  }
}

// Backend simulado con `n` documentos (ids n..1) y paginación por cursor `antes_de`.
function servidor(n, extra = () => ({})) {
  return async (_id, { antesDe, limite }) => {
    const todos = Array.from({ length: n }, (_, i) => doc(n - i, extra(n - i)))
    const desde = todos.filter((d) => antesDe === null || d.id < antesDe)
    const pagina = desde.slice(0, limite)
    return { documentos: pagina, siguiente: desde.length > limite ? pagina[pagina.length - 1].id : null }
  }
}

function error(status, code) {
  return Object.assign(new Error('x'), { response: { status, data: { detail: { code } } } })
}

function montar(proyecto = PROY) {
  return render(
    <I18nProvider>
      <Documentos proyecto={proyecto} />
    </I18nProvider>,
  )
}

let espias
beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset())
  api.listarDocumentos.mockResolvedValue({ documentos: [doc(1), doc(2)], siguiente: null })
  api.limitesDeDocumentos.mockResolvedValue({
    max_bytes_archivo: 1e8, max_archivos_lote: 250, max_bytes_lote: 1e9, extensiones: ['pdf'],
  })
  espias = ['confirm', 'alert', 'prompt'].map((m) => vi.spyOn(window, m).mockImplementation(() => true))
})
afterEach(() => {
  espias.forEach((e) => e.mockRestore())
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('permisos compartidos', () => {
  it('escriben CONTRIBUTOR, REVIEWER y OWNER; VIEWER no', () => {
    for (const papel of ['CONTRIBUTOR', 'REVIEWER', 'OWNER']) expect(puedeVerOcultos({ papel })).toBe(true)
    expect(puedeVerOcultos({ papel: 'VIEWER' })).toBe(false)
    expect(puedeVerOcultos(null)).toBe(false)
  })
  it('modificar exige además el proyecto ACTIVE', () => {
    expect(puedeModificarDocumentos({ papel: 'OWNER', estado: 'ACTIVE' })).toBe(true)
    expect(puedeModificarDocumentos({ papel: 'OWNER', estado: 'ARCHIVED' })).toBe(false)
    expect(puedeModificarDocumentos({ papel: 'VIEWER', estado: 'ACTIVE' })).toBe(false)
  })
})

describe('Documentos: lista y permisos', () => {
  it('muestra nombre, tamaño legible, quién lo subió, fecha y estado', async () => {
    montar()
    const fila = (await screen.findByText('doc1.pdf')).closest('li')
    expect(within(fila).getByText(/2 KB/)).toBeInTheDocument()
    expect(within(fila).getByText(/ana@x\.com/)).toBeInTheDocument()
    expect(within(fila).getByText(T.estados.listo)).toBeInTheDocument()
    expect(within(fila).getByText(/2026/)).toBeInTheDocument()
    expect(api.listarDocumentos).toHaveBeenCalledWith(7, { vista: 'visibles', antesDe: null, limite: 50 })
  })

  it('un VIEWER ve la lista sin Agregar, Ocultar ni Ver ocultos', async () => {
    montar({ ...PROY, papel: 'VIEWER' })
    await screen.findByText('doc1.pdf')
    expect(screen.queryByRole('button', { name: T.agregar })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: new RegExp(T.ocultar) })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: T.verOcultos })).not.toBeInTheDocument()
  })

  it.each(['CONTRIBUTOR', 'REVIEWER', 'OWNER'])('un %s ve Agregar y Ocultar', async (papel) => {
    montar({ ...PROY, papel })
    const fila = (await screen.findByText('doc1.pdf')).closest('li')
    expect(screen.getByRole('button', { name: T.agregar })).toBeInTheDocument()
    expect(within(fila).getByRole('button', { name: T.ocultarDe('doc1.pdf') })).toBeInTheDocument()
  })

  it('un proyecto ARCHIVED muestra la lista sin acciones de escritura', async () => {
    montar({ ...PROY, estado: 'ARCHIVED' })
    await screen.findByText('doc1.pdf')
    expect(screen.queryByRole('button', { name: T.agregar })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: new RegExp(T.ocultar) })).not.toBeInTheDocument()
  })

  it('lista vacía muestra el texto de vacío', async () => {
    api.listarDocumentos.mockResolvedValue({ documentos: [], siguiente: null })
    montar()
    expect(await screen.findByText(T.vacio)).toBeInTheDocument()
  })

  it('un documento con error muestra la causa', async () => {
    api.listarDocumentos.mockResolvedValue({
      documentos: [doc(1, { estado: 'error', error: 'PDF corrupto' })], siguiente: null,
    })
    montar()
    expect(await screen.findByText(T.estados.error)).toBeInTheDocument()
    expect(screen.getByText(T.causa('PDF corrupto'))).toBeInTheDocument()
  })

  it('Cargar más pide la página siguiente con el cursor y la agrega', async () => {
    api.listarDocumentos.mockImplementation(servidor(60))
    montar()
    await screen.findByText('doc60.pdf')
    expect(screen.queryByText('doc10.pdf')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: T.cargarMas }))
    expect(await screen.findByText('doc10.pdf')).toBeInTheDocument()
    expect(screen.getByText('doc60.pdf')).toBeInTheDocument()
    expect(api.listarDocumentos).toHaveBeenLastCalledWith(7, { vista: 'visibles', antesDe: 11, limite: 50 })
    expect(screen.queryByRole('button', { name: T.cargarMas })).not.toBeInTheDocument()
  })
})

describe('Documentos: ocultar y restaurar', () => {
  it('ocultar saca la fila (sin diálogo) y aparece en Ver ocultos, donde se restaura', async () => {
    api.ocultarDocumento.mockResolvedValue({})
    api.restaurarDocumento.mockResolvedValue({})
    let oculto = false
    api.listarDocumentos.mockImplementation(async (_id, { vista }) => {
      if (vista === 'ocultos') return { documentos: oculto ? [doc(1, { oculto: true })] : [], siguiente: null }
      return { documentos: oculto ? [doc(2)] : [doc(1), doc(2)], siguiente: null }
    })
    api.ocultarDocumento.mockImplementation(async () => { oculto = true })
    api.restaurarDocumento.mockImplementation(async () => { oculto = false })
    montar()
    const fila = (await screen.findByText('doc1.pdf')).closest('li')
    fireEvent.click(within(fila).getByRole('button', { name: T.ocultarDe('doc1.pdf') }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('doc1.pdf')).not.toBeInTheDocument())
    expect(api.ocultarDocumento).toHaveBeenCalledWith(7, 1)

    fireEvent.click(screen.getByRole('button', { name: T.verOcultos }))
    const oculta = (await screen.findByText('doc1.pdf')).closest('li')
    expect(screen.queryByText('doc2.pdf')).not.toBeInTheDocument()
    fireEvent.click(within(oculta).getByRole('button', { name: T.restaurarDe('doc1.pdf') }))
    await waitFor(() => expect(api.restaurarDocumento).toHaveBeenCalledWith(7, 1))
    expect(await screen.findByText(T.sinOcultos)).toBeInTheDocument()
  })

  it.each([
    [409, 'proyecto_no_activo'],
    [423, 'kill_switch_activo'],
    [404, 'documento_no_encontrado'],
    [403, 'papel_insuficiente'],
  ])('un %s %s se muestra traducido sin romper la lista', async (status, code) => {
    api.ocultarDocumento.mockRejectedValue(error(status, code))
    montar()
    const fila = (await screen.findByText('doc1.pdf')).closest('li')
    fireEvent.click(within(fila).getByRole('button', { name: T.ocultarDe('doc1.pdf') }))
    expect(await screen.findByText(es.proyectos.documentos.errores[code])).toBeInTheDocument()
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
  })

  it('un error al listar se muestra traducido y ofrece reintentar', async () => {
    api.listarDocumentos.mockRejectedValueOnce(error(423, 'kill_switch_activo'))
    montar()
    expect(await screen.findByText(T.errores.kill_switch_activo)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: T.reintentar }))
    expect(await screen.findByText('doc1.pdf')).toBeInTheDocument()
  })
})

describe('Documentos: Agregar abre el Selector y recarga al terminar', () => {
  it('Agregar muestra el Selector; al terminar la subida cierra y vuelve a pedir la lista', async () => {
    api.subirDocumentos.mockResolvedValue({ aceptados: [{ id: 3, nombre: 'a.pdf' }], ignorados: [] })
    const { container } = montar()
    await screen.findByText('doc1.pdf')
    expect(api.limitesDeDocumentos).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: T.agregar }))
    await waitFor(() => expect(api.limitesDeDocumentos).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: T.agregar })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: T.elegirArchivos })).toBeEnabled())

    const entrada = container.querySelector('input[type="file"]:not([webkitdirectory])')
    fireEvent.change(entrada, { target: { files: [new File([new Uint8Array(10)], 'a.pdf')] } })
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    const cargas = api.listarDocumentos.mock.calls.length
    fireEvent.click(await screen.findByRole('button', { name: T.resultado.cerrar }))
    await waitFor(() => expect(api.listarDocumentos.mock.calls.length).toBe(cargas + 1))
    expect(await screen.findByRole('button', { name: T.agregar })).toBeInTheDocument()
  })

  it('cancelar el Selector vuelve al botón Agregar', async () => {
    const { container } = montar()
    await screen.findByText('doc1.pdf')
    fireEvent.click(screen.getByRole('button', { name: T.agregar }))
    await waitFor(() => expect(screen.getByRole('button', { name: T.elegirArchivos })).toBeEnabled())
    fireEvent.change(container.querySelector('input[type="file"]:not([webkitdirectory])'),
      { target: { files: [new File([new Uint8Array(10)], 'a.pdf')] } })
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.cancelar }))
    expect(await screen.findByRole('button', { name: T.agregar })).toBeInTheDocument()
    expect(api.subirDocumentos).not.toHaveBeenCalled()
  })
})

describe('Documentos: sondeo cada 5 s', () => {
  const activo = (id) => doc(id, { estado: 'procesando' })

  async function montarConTemporizadores(...respuestas) {
    vi.useFakeTimers()
    respuestas.forEach((r) => api.listarDocumentos.mockResolvedValueOnce(r))
    const r = montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    return r
  }

  it('vuelve a pedir cada 5 s mientras haya filas no terminales y se detiene al terminar todas', async () => {
    await montarConTemporizadores(
      { documentos: [activo(1)], siguiente: null },
      { documentos: [activo(1)], siguiente: null },
      { documentos: [doc(1)], siguiente: null },
    )
    expect(api.listarDocumentos).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(4900) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(3)
    expect(screen.getByText(T.estados.listo)).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(3)
  })

  it.each(['en_cola', 'pendiente', 'procesando'])('una fila %s activa el sondeo', async (estado) => {
    await montarConTemporizadores({ documentos: [doc(1, { estado })], siguiente: null })
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(api.listarDocumentos.mock.calls.length).toBeGreaterThan(1)
  })

  it.each(['listo', 'parcial', 'error', 'sin_extractor', 'cancelado'])('con todo %s no sondea', async (estado) => {
    await montarConTemporizadores({ documentos: [doc(1, { estado })], siguiente: null })
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(1)
  })

  it('al desmontar cancela el temporizador', async () => {
    const { unmount } = await montarConTemporizadores({ documentos: [activo(1)], siguiente: null })
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(1)
  })

  it('el sondeo no pierde las páginas cargadas: re-pide lo que se ve y reemplaza por id', async () => {
    let terminado = false
    api.listarDocumentos.mockImplementation(servidor(60, (n) => (n === 5 && !terminado ? { estado: 'procesando' } : {})))
    vi.useFakeTimers()
    montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    fireEvent.click(screen.getByRole('button', { name: T.cargarMas }))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('doc5.pdf')).toBeInTheDocument()
    expect(screen.getByText(T.estados.procesando)).toBeInTheDocument()
    terminado = true
    api.listarDocumentos.mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    // 60 filas vistas → se re-piden 60 de una vez (≤ 100, el máximo del backend), sin perder ninguna
    expect(api.listarDocumentos.mock.calls.map((c) => [c[1].antesDe, c[1].limite])).toEqual([[null, 60]])
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
    expect(screen.queryByText(T.estados.procesando)).not.toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(api.listarDocumentos).toHaveBeenCalledTimes(1)
  })

  it('cambiar de proyecto cancela el sondeo del anterior', async () => {
    vi.useFakeTimers()
    api.listarDocumentos.mockResolvedValue({ documentos: [activo(1)], siguiente: null })
    const { rerender } = montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    api.listarDocumentos.mockClear()
    api.listarDocumentos.mockResolvedValue({ documentos: [doc(1)], siguiente: null })
    rerender(<I18nProvider><Documentos proyecto={{ ...PROY, id: 8 }} /></I18nProvider>)
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(api.listarDocumentos.mock.calls.every((c) => c[0] === 8)).toBe(true)
  })

  it('un error transitorio al sondear muestra el aviso y conserva la lista', async () => {
    await montarConTemporizadores({ documentos: [activo(1)], siguiente: null })
    api.listarDocumentos.mockRejectedValueOnce(error(423, 'kill_switch_activo'))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText(T.errores.kill_switch_activo)).toBeInTheDocument()
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
  })
})

describe('Documentos: sin diálogos del navegador', () => {
  it('ninguna acción llama a confirm, alert ni prompt', async () => {
    api.ocultarDocumento.mockResolvedValue({})
    montar()
    const fila = (await screen.findByText('doc1.pdf')).closest('li')
    fireEvent.click(within(fila).getByRole('button', { name: T.ocultarDe('doc1.pdf') }))
    await waitFor(() => expect(api.ocultarDocumento).toHaveBeenCalled())
    espias.forEach((e) => expect(e).not.toHaveBeenCalled())
  })
})
