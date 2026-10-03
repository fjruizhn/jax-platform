import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/proyectos', () => ({
  listarDocumentos: vi.fn(),
  ocultarDocumento: vi.fn(),
  restaurarDocumento: vi.fn(),
  reprocesarDocumento: vi.fn(),
  limitesDeDocumentos: vi.fn(),
  subirDocumentos: vi.fn(),
}))

import * as api from '../../api/proyectos'
import Documentos from './Documentos'
import { puedeVerOcultos, puedeModificarDocumentos } from './permisos'
import { I18nProvider, useI18n } from '../../i18n/index.jsx'
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
  localStorage.clear() // el idioma elegido en una prueba no pasa a la siguiente
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

  it('un documento con error muestra la causa traducida desde su código', async () => {
    api.listarDocumentos.mockResolvedValue({
      documentos: [doc(1, { estado: 'error', error: 'procesamiento_fallido' })], siguiente: null,
    })
    montar()
    expect(await screen.findByText(T.estados.error)).toBeInTheDocument()
    expect(screen.getByText(T.causa(T.causas.procesamiento_fallido))).toBeInTheDocument()
  })

  it('un correo largo se recorta con puntos suspensivos y lo completo queda en el title; la fecha no se va', async () => {
    const largo = 'una.persona.con.un.correo.larguisimo.de.verdad@subdominio.empresa-de-ejemplo.com'
    api.listarDocumentos.mockResolvedValue({ documentos: [doc(1, { subido_por_email: largo })], siguiente: null })
    montar()
    const correo = await screen.findByTitle(largo)
    expect(correo).toHaveClass('truncate')
    expect(correo).toHaveTextContent(largo)
    // la fecha es hermana del correo en la misma línea y no se recorta
    const linea = correo.parentElement
    expect(linea).toHaveClass('flex')
    expect([...linea.children].at(-1)).toHaveClass('shrink-0')
  })

  it('un código desconocido (o texto crudo) muestra el genérico, nunca el texto tal cual', async () => {
    api.listarDocumentos.mockResolvedValue({
      documentos: [doc(1, { estado: 'error', error: '[Errno 28] /srv/jax/proyectos/x.pdf' })], siguiente: null,
    })
    montar()
    expect(await screen.findByText(T.causa(T.causas.desconocida))).toBeInTheDocument()
    expect(screen.queryByText(/Errno 28/)).toBeNull()
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

  it('Cargar más: un sondeo que corre con la página en vuelo no deja el botón deshabilitado', async () => {
    const base = servidor(60, (n) => (n === 60 ? { estado: 'procesando' } : {}))
    let soltar
    let retener = false
    api.listarDocumentos.mockImplementation((id, args) => {
      if (retener && args.antesDe === 11) return new Promise((res) => { soltar = () => res(base(id, args)) })
      return base(id, args)
    })
    vi.useFakeTimers()
    montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    retener = true
    fireEvent.click(screen.getByRole('button', { name: T.cargarMas }))
    expect(screen.getByRole('button', { name: T.cargarMas })).toBeDisabled()
    retener = false
    // el sondeo corre mientras la página sigue en vuelo
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    await act(async () => { soltar(); await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('button', { name: T.cargarMas })).toBeEnabled()
  })

  it('el cambio de idioma no reinicia la lista ni la vuelve a pedir', async () => {
    function Idioma() { const { setLang } = useI18n(); return <button type="button" onClick={() => setLang('en')}>en</button> }
    render(<I18nProvider><Idioma /><Documentos proyecto={PROY} /></I18nProvider>)
    await screen.findByText('doc1.pdf')
    const llamadas = api.listarDocumentos.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'en' }))
    expect(await screen.findByRole('button', { name: 'Add documents' })).toBeInTheDocument()
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
    expect(api.listarDocumentos.mock.calls.length).toBe(llamadas)
    expect(screen.queryByText(T.cargando)).not.toBeInTheDocument()
  })

  it('más de 100 filas visibles: el sondeo las re-pide en varios pedidos y no pierde ninguna', async () => {
    api.listarDocumentos.mockImplementation(servidor(130, (n) => (n === 130 ? { estado: 'procesando' } : {})))
    vi.useFakeTimers()
    montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    for (let i = 0; i < 2; i++) {
      fireEvent.click(screen.getByRole('button', { name: T.cargarMas }))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    }
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
    api.listarDocumentos.mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(api.listarDocumentos.mock.calls.map((c) => [c[1].antesDe, c[1].limite])).toEqual([[null, 100], [31, 30]])
    expect(screen.getByText('doc130.pdf')).toBeInTheDocument()
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
  })

  it('tope del sondeo: con más de 300 filas re-pide 300 y conserva las de más abajo', async () => {
    api.listarDocumentos.mockImplementation(servidor(320, (n) => (n === 320 ? { estado: 'procesando' } : {})))
    vi.useFakeTimers()
    montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    for (let i = 0; i < 6; i++) {
      fireEvent.click(screen.getByRole('button', { name: T.cargarMas }))
      await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    }
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
    api.listarDocumentos.mockClear()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(api.listarDocumentos.mock.calls.map((c) => c[1].limite)).toEqual([100, 100, 100])
    expect(screen.getByText('doc320.pdf')).toBeInTheDocument()
    expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(320)
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

describe('Documentos: anuncio de transiciones (role=status)', () => {
  it('anuncia una transición una sola vez y calla cuando un sondeo no cambia nada', async () => {
    vi.useFakeTimers()
    api.listarDocumentos
      .mockResolvedValueOnce({ documentos: [doc(1, { estado: 'procesando' }), doc(2, { estado: 'procesando' })], siguiente: null })
      .mockResolvedValueOnce({ documentos: [doc(1, { estado: 'procesando' }), doc(2, { estado: 'procesando' })], siguiente: null })
      .mockResolvedValueOnce({ documentos: [doc(1), doc(2, { estado: 'procesando' })], siguiente: null })
      .mockResolvedValue({ documentos: [doc(1), doc(2, { estado: 'procesando' })], siguiente: null })
    montar()
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const region = screen.getByRole('status')
    expect(region).toHaveTextContent('')
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(region).toHaveTextContent('') // sondeo sin cambios: nada
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(region).toHaveTextContent(T.anuncio('doc1.pdf', T.estados.listo))
    expect(region.textContent).not.toMatch(/doc2/)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(region).toHaveTextContent('') // otro sondeo sin cambios: ya no repite
  })

  it('la carga inicial y las filas nuevas no se anuncian', async () => {
    api.listarDocumentos.mockResolvedValue({ documentos: [doc(1)], siguiente: null })
    montar()
    await screen.findByText('doc1.pdf')
    expect(screen.getByRole('status')).toHaveTextContent('')
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


describe('Documentos: reprocesar', () => {
  const EXT = ['pdf', 'docx', 'xlsx', 'jpeg']
  const lista = (...docs) => api.listarDocumentos.mockResolvedValue({ documentos: docs, siguiente: null })
  const limites = (extensiones = EXT) => api.limitesDeDocumentos.mockResolvedValue({
    max_bytes_archivo: 1e8, max_archivos_lote: 250, max_bytes_lote: 1e9, extensiones,
  })
  const boton = (nombre) => screen.queryByRole('button', { name: T.reprocesarDe(nombre) })

  it('aparece en las filas sin_extractor y error de un tipo con extractor, y en ninguna otra', async () => {
    limites()
    lista(doc(1, { estado: 'sin_extractor', tipo: 'pdf' }), doc(2, { estado: 'error', tipo: 'jpeg', error: null }),
      doc(3, { estado: 'listo' }), doc(4, { estado: 'procesando' }), doc(5, { estado: 'en_cola' }),
      doc(6, { estado: 'parcial' }), doc(7, { estado: 'cancelado' }), doc(8, { estado: 'pendiente' }))
    montar()
    expect(await screen.findByRole('button', { name: T.reprocesarDe('doc1.pdf') })).toBeInTheDocument()
    expect(boton('doc2.pdf')).toBeInTheDocument()
    for (const n of [3, 4, 5, 6, 7, 8]) expect(boton(`doc${n}.pdf`)).toBeNull()
  })

  it('no aparece si el tipo no tiene extractor', async () => {
    limites()
    lista(doc(1, { estado: 'sin_extractor', tipo: 'txt' }), doc(2, { estado: 'sin_extractor', tipo: 'pdf' }))
    montar()
    await screen.findByRole('button', { name: T.reprocesarDe('doc2.pdf') })
    expect(boton('doc1.pdf')).toBeNull()
  })

  it('el tipo se compara sin distinguir mayúsculas', async () => {
    limites()
    lista(doc(1, { estado: 'sin_extractor', tipo: 'PDF' }))
    montar()
    expect(await screen.findByRole('button', { name: T.reprocesarDe('doc1.pdf') })).toBeInTheDocument()
  })

  it('no aparece para un VIEWER ni en un proyecto archivado, y ni siquiera pide los límites', async () => {
    limites()
    lista(doc(1, { estado: 'sin_extractor', tipo: 'pdf' }))
    for (const proyecto of [{ ...PROY, papel: 'VIEWER' }, { ...PROY, estado: 'ARCHIVED' }]) {
      const { unmount } = montar(proyecto)
      await screen.findByText('doc1.pdf')
      expect(boton('doc1.pdf')).toBeNull()
      unmount()
    }
    expect(api.limitesDeDocumentos).not.toHaveBeenCalled()
  })

  it('sin poder saber los tipos (los límites fallan) no se ofrece', async () => {
    api.limitesDeDocumentos.mockRejectedValue(error(500, 'x'))
    lista(doc(1, { estado: 'sin_extractor', tipo: 'pdf' }))
    montar()
    await screen.findByText('doc1.pdf')
    await waitFor(() => expect(api.limitesDeDocumentos).toHaveBeenCalled())
    expect(boton('doc1.pdf')).toBeNull()
  })

  it('no aparece en la vista de ocultos', async () => {
    limites()
    api.listarDocumentos.mockImplementation(async (_id, { vista }) => ({
      documentos: [doc(1, { estado: 'sin_extractor', tipo: 'pdf', oculto: vista === 'ocultos' })], siguiente: null }))
    montar()
    await screen.findByRole('button', { name: T.reprocesarDe('doc1.pdf') })
    fireEvent.click(screen.getByRole('button', { name: T.verOcultos }))
    await screen.findByRole('button', { name: T.restaurarDe('doc1.pdf') })
    expect(boton('doc1.pdf')).toBeNull()
  })

  it('llama a la API (sin diálogo del navegador) y la fila vuelve a En espera y se sigue con el sondeo', async () => {
    limites()
    let reprocesado = false
    api.listarDocumentos.mockImplementation(async () => ({
      documentos: [doc(1, reprocesado ? { estado: 'en_cola' } : { estado: 'sin_extractor', tipo: 'pdf' })],
      siguiente: null }))
    api.reprocesarDocumento.mockImplementation(async () => { reprocesado = true; return { id: 1, estado: 'en_cola' } })
    montar()
    fireEvent.click(await screen.findByRole('button', { name: T.reprocesarDe('doc1.pdf') }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(api.reprocesarDocumento).toHaveBeenCalledWith(7, 1))
    const fila = (await screen.findByText('doc1.pdf')).closest('li')
    await waitFor(() => expect(within(fila).getByText(T.estados.en_cola)).toBeInTheDocument())
    expect(boton('doc1.pdf')).toBeNull()
    expect(espias.every((e) => e.mock.calls.length === 0)).toBe(true)
  })

  it.each(['no_reprocesable', 'original_no_encontrado', 'fuente_ilegible', 'proyecto_no_activo', 'kill_switch_activo'])(
    'un 409/423 %s se muestra traducido y la fila sigue ahí', async (code) => {
      limites()
      lista(doc(1, { estado: 'sin_extractor', tipo: 'pdf' }))
      api.reprocesarDocumento.mockRejectedValue(error(409, code))
      montar()
      fireEvent.click(await screen.findByRole('button', { name: T.reprocesarDe('doc1.pdf') }))
      expect(await screen.findByText(T.errores[code])).toBeInTheDocument()
      expect(screen.getByText('doc1.pdf')).toBeInTheDocument()
    })

  it('el botón queda deshabilitado mientras corre una acción (una mutación a la vez)', async () => {
    limites()
    lista(doc(1, { estado: 'sin_extractor', tipo: 'pdf' }))
    let soltar
    api.reprocesarDocumento.mockImplementation(() => new Promise((r) => { soltar = r }))
    montar()
    const b = await screen.findByRole('button', { name: T.reprocesarDe('doc1.pdf') })
    fireEvent.click(b)
    await waitFor(() => expect(b).toBeDisabled())
    fireEvent.click(b)
    expect(api.reprocesarDocumento).toHaveBeenCalledTimes(1)
    soltar({})
  })
})

describe('Documentos: motivo del error', () => {
  const sinMotivo = () => api.listarDocumentos.mockResolvedValue({
    documentos: [doc(1, { estado: 'error', error: null })], siguiente: null })

  it('un error sin motivo guardado, que se puede reprocesar, dice que se reprocese', async () => {
    sinMotivo()
    montar()
    expect(await screen.findByText(T.sinMotivoReprocesable)).toBeInTheDocument()
    expect(T.sinMotivoReprocesable).toBe('Motivo no registrado: reprocesalo para obtenerlo')
    expect(T.sinMotivo).toBe('Motivo no registrado')
    expect(screen.queryByText(T.sinMotivo)).toBeNull()
  })

  it('sin permiso de escritura solo dice que el motivo no está registrado', async () => {
    sinMotivo()
    montar({ ...PROY, papel: 'VIEWER' })
    expect(await screen.findByText(T.sinMotivo)).toBeInTheDocument()
    expect(screen.queryByText(T.sinMotivoReprocesable)).toBeNull()
  })

  it('en un proyecto archivado tampoco ofrece reprocesar', async () => {
    sinMotivo()
    montar({ ...PROY, estado: 'ARCHIVED' })
    expect(await screen.findByText(T.sinMotivo)).toBeInTheDocument()
    expect(screen.queryByText(T.sinMotivoReprocesable)).toBeNull()
  })

  it('con un tipo sin extractor solo dice que el motivo no está registrado', async () => {
    api.listarDocumentos.mockResolvedValue({ documentos: [doc(1, { estado: 'error', error: null, tipo: 'txt' })], siguiente: null })
    montar()
    await waitFor(() => expect(api.limitesDeDocumentos).toHaveBeenCalled())
    expect(await screen.findByText(T.sinMotivo)).toBeInTheDocument()
    expect(screen.queryByText(T.sinMotivoReprocesable)).toBeNull()
  })

  it.each(['ocr_sin_texto', 'ocr_confianza_baja'])('el código %s se muestra traducido', async (codigo) => {
    api.listarDocumentos.mockResolvedValue({ documentos: [doc(1, { estado: 'error', error: codigo })], siguiente: null })
    montar()
    expect(await screen.findByText(T.causa(T.causas[codigo]))).toBeInTheDocument()
  })
})
