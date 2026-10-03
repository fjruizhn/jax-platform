import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { readFileSync } from 'node:fs'

vi.mock('../../api/proyectos', () => ({
  limitesDeDocumentos: vi.fn(),
  subirDocumentos: vi.fn(),
}))

import * as api from '../../api/proyectos'
import SelectorDeDocumentos from './SelectorDeDocumentos'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import { formatoPeso } from './resumenDeLote'
import { localeFor } from '../../i18n/index.jsx'

const T = es.proyectos.documentos
const MB = 1024 * 1024
const LIMITES = {
  max_bytes_archivo: 100 * MB,
  max_archivos_lote: 3,
  max_bytes_lote: 1024 * MB,
  extensiones: ['pdf', 'xlsx'],
}
const archivo = (nombre, bytes = 1024) => new File([new Uint8Array(bytes)], nombre)
const errorHttp = (status, code) => Object.assign(new Error('x'), { response: { status, data: { detail: { code } } } })

async function montar(props = {}) {
  const onTerminado = vi.fn()
  const onCerrar = vi.fn()
  const r = render(
    <I18nProvider>
      <SelectorDeDocumentos proyectoId={7} onTerminado={onTerminado} onCerrar={onCerrar} {...props} />
    </I18nProvider>,
  )
  const botonArchivos = screen.getByRole('button', { name: T.elegirArchivos })
  await waitFor(() => expect(botonArchivos).toBeEnabled())
  const entradas = r.container.querySelectorAll('input[type="file"]')
  return { ...r, onTerminado, onCerrar, botonArchivos, entradas, multiple: entradas[0], carpeta: entradas[1] }
}

function elegir(entrada, archivos) {
  fireEvent.change(entrada, { target: { files: archivos } })
}

beforeEach(() => {
  api.limitesDeDocumentos.mockReset().mockResolvedValue(LIMITES)
  api.subirDocumentos.mockReset().mockResolvedValue({ lote: 'L', aceptados: [], ignorados: [] })
})

describe('SelectorDeDocumentos', () => {
  it('tiene dos entradas ocultas: una múltiple y otra de carpeta, y el botón Carpeta', async () => {
    const { multiple, carpeta } = await montar()
    expect(multiple).toHaveAttribute('multiple')
    expect(carpeta).toHaveAttribute('webkitdirectory')
    expect(screen.getByRole('button', { name: T.elegirCarpeta })).toBeInTheDocument()
  })

  it('los topes salen del backend: nada se elige hasta que llegan', async () => {
    let resolver
    api.limitesDeDocumentos.mockReturnValue(new Promise((r) => { resolver = r }))
    render(<I18nProvider><SelectorDeDocumentos proyectoId={7} onTerminado={vi.fn()} onCerrar={vi.fn()} /></I18nProvider>)
    expect(screen.getByRole('button', { name: T.elegirArchivos })).toBeDisabled()
    await act(async () => { resolver(LIMITES) })
    expect(screen.getByRole('button', { name: T.elegirArchivos })).toBeEnabled()
  })

  it('abre el resumen con cuántos, cuánto pesan, tipos e ignorados con su motivo', async () => {
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf', 1024), archivo('b.pdf', 1024), archivo('c.xlsx', 1024), archivo('virus.exe')])
    const dialogo = await screen.findByRole('dialog', { name: T.resumen.titulo })
    expect(within(dialogo).getByText(T.resumen.archivos(3))).toBeInTheDocument()
    expect(within(dialogo).getByText(T.resumen.peso(formatoPeso(3072, localeFor('es'))))).toBeInTheDocument()
    const fila = (ext) => within(dialogo).getByText(`.${ext}`).closest('tr')
    expect(within(fila('pdf')).getByText('2')).toBeInTheDocument()
    expect(within(fila('xlsx')).getByText('1')).toBeInTheDocument()
    expect(within(dialogo).getByText(T.resumen.ignorados(1))).toBeInTheDocument()
    expect(within(dialogo).getByText(T.motivos.tipo_no_admitido, { exact: false })).toBeInTheDocument()
    expect(within(dialogo).getByText('virus.exe')).toBeInTheDocument()
    expect(within(dialogo).getByRole('button', { name: T.resumen.confirmar })).toBeEnabled()
  })

  it('el foco inicial va a Subir y vuelve al botón que abrió el diálogo', async () => {
    const { multiple, botonArchivos } = await montar()
    botonArchivos.focus()
    elegir(multiple, [archivo('a.pdf')])
    const subir = await screen.findByRole('button', { name: T.resumen.confirmar })
    await waitFor(() => expect(subir).toHaveFocus())
    fireEvent.click(screen.getByRole('button', { name: T.resumen.cancelar }))
    await waitFor(() => expect(botonArchivos).toHaveFocus())
  })

  it('Escape cierra sin subir', async () => {
    const { multiple, onCerrar } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    await screen.findByRole('dialog')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.subirDocumentos).not.toHaveBeenCalled()
    expect(onCerrar).toHaveBeenCalledTimes(1)
  })

  it('Subir llama a subirDocumentos una sola vez aunque se haga doble clic, con solo lo aceptado', async () => {
    api.subirDocumentos.mockReturnValue(new Promise(() => {}))
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf'), archivo('virus.exe')])
    const subir = await screen.findByRole('button', { name: T.resumen.confirmar })
    // Dos clics SIN re-render intermedio: solo la guardia síncrona (ref) los frena;
    // el `disabled` del DOM todavía no se pintó.
    act(() => { subir.click(); subir.click() })
    expect(api.subirDocumentos).toHaveBeenCalledTimes(1)
    const [id, enviados] = api.subirDocumentos.mock.calls[0]
    expect(id).toBe(7)
    expect(enviados.map((f) => f.name)).toEqual(['a.pdf'])
  })

  it('la barra de avance es accesible y refleja onProgreso', async () => {
    let progreso
    api.subirDocumentos.mockImplementation((_id, _a, { onProgreso }) => { progreso = onProgreso; return new Promise(() => {}) })
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    const barra = await screen.findByRole('progressbar', { name: T.resumen.progreso })
    expect(barra).not.toHaveAttribute('aria-valuenow')
    act(() => progreso(0.5))
    expect(barra).toHaveAttribute('aria-valuenow', '50')
    expect(barra).toHaveAttribute('aria-valuemin', '0')
    expect(barra).toHaveAttribute('aria-valuemax', '100')
    expect(screen.getByText(T.subiendo, { exact: false })).toBeInTheDocument()
  })

  it('mientras sube dice por qué no se puede cerrar', async () => {
    api.subirDocumentos.mockReturnValue(new Promise(() => {}))
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    expect(screen.queryByText(T.resumen.noSeCierra)).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    expect(await screen.findByText(T.resumen.noSeCierra)).toBeInTheDocument()
  })

  it('si fallan los límites: texto propio, Reintentar los vuelve a pedir y al llegar se habilitan los botones', async () => {
    api.limitesDeDocumentos.mockReset().mockRejectedValueOnce(new Error('red')).mockResolvedValue(LIMITES)
    render(<I18nProvider><SelectorDeDocumentos proyectoId={7} onTerminado={vi.fn()} onCerrar={vi.fn()} /></I18nProvider>)
    expect(await screen.findByText(T.limites.error)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: T.elegirArchivos })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: T.limites.reintentar }))
    await waitFor(() => expect(screen.getByRole('button', { name: T.elegirArchivos })).toBeEnabled())
    expect(screen.queryByText(T.limites.error)).not.toBeInTheDocument()
    expect(api.limitesDeDocumentos).toHaveBeenCalledTimes(2)
  })

  it('con ~5.000 ignorados el DOM tiene a lo sumo 50 <li> por motivo y los conteos siguen completos', async () => {
    const { multiple } = await montar()
    const ignorados = Array.from({ length: 5000 }, (_, i) => archivo(`x${i}.exe`, 1))
    elegir(multiple, [archivo('a.pdf'), ...ignorados])
    const dialogo = await screen.findByRole('dialog')
    expect(within(dialogo).getByText(T.resumen.ignorados(5000))).toBeInTheDocument()
    const detalle = within(dialogo).getByText(T.motivos.tipo_no_admitido, { exact: false }).closest('details')
    expect(within(detalle).getByText('(5000)')).toBeInTheDocument()
    expect(detalle.querySelectorAll('li').length).toBeLessThanOrEqual(50)
    expect(within(detalle).getByText(T.resumen.yMas(4950))).toBeInTheDocument()
  })

  it('un motivo desconocido del servidor muestra un texto traducido, no el código crudo', async () => {
    api.subirDocumentos.mockResolvedValue({
      lote: 'L', aceptados: [], ignorados: [{ nombre: 'z.pdf', motivo: 'motivo_inventado_xyz' }],
    })
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    const dialogo = await screen.findByRole('dialog', { name: T.resultado.titulo })
    expect(within(dialogo).getByText(T.resumen.motivoDesconocido, { exact: false })).toBeInTheDocument()
    expect(dialogo).not.toHaveTextContent('motivo_inventado_xyz')
  })

  it('un nombre vacío del servidor se muestra con el texto traducido', async () => {
    api.subirDocumentos.mockResolvedValue({
      lote: 'L', aceptados: [], ignorados: [{ nombre: '', motivo: 'nombre_invalido' }],
    })
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    expect(await screen.findByText(T.resumen.sinNombre)).toBeInTheDocument()
  })

  it('si el padre desmonta con el resultado abierto, igual avisa con onTerminado una vez', async () => {
    const resultado = { lote: 'L', aceptados: [{ id: 1, nombre: 'a.pdf' }], ignorados: [] }
    api.subirDocumentos.mockResolvedValue(resultado)
    const { multiple, onTerminado, unmount } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    await screen.findByRole('dialog', { name: T.resultado.titulo })
    unmount()
    expect(onTerminado).toHaveBeenCalledTimes(1)
    expect(onTerminado).toHaveBeenCalledWith(resultado)
  })

  it('si ya lo entregó al cerrar, desmontar no avisa de nuevo', async () => {
    api.subirDocumentos.mockResolvedValue({ lote: 'L', aceptados: [], ignorados: [] })
    const { multiple, onTerminado, unmount } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    fireEvent.click(await screen.findByRole('button', { name: T.resultado.cerrar }))
    unmount()
    expect(onTerminado).toHaveBeenCalledTimes(1)
  })

  it('mientras sube, Escape no cierra', async () => {
    api.subirDocumentos.mockReturnValue(new Promise(() => {}))
    const { multiple, onCerrar } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(onCerrar).not.toHaveBeenCalled()
  })

  it('un 413 se muestra con su texto dentro del diálogo, sin perder el resumen, y permite reintentar', async () => {
    api.subirDocumentos.mockRejectedValueOnce(errorHttp(413, 'lote_demasiado_grande'))
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf'), archivo('virus.exe')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(T.errores.lote_demasiado_grande)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(T.resumen.archivos(1))).toBeInTheDocument()
    expect(screen.getByText('virus.exe')).toBeInTheDocument()
    const subir = screen.getByRole('button', { name: T.resumen.confirmar })
    expect(subir).toBeEnabled()
    fireEvent.click(subir)
    await waitFor(() => expect(api.subirDocumentos).toHaveBeenCalledTimes(2))
  })

  it.each([
    [409, 'proyecto_no_activo'], [423, 'kill_switch_activo'], [507, 'sin_espacio'], [500, 'insercion_incierta'],
  ])('un %i (%s) se muestra con su texto traducido', async (status, codigo) => {
    api.subirDocumentos.mockRejectedValueOnce(errorHttp(status, codigo))
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    expect(await screen.findByRole('alert')).toHaveTextContent(T.errores[codigo])
  })

  it('un error sin código conocido cae al texto genérico', async () => {
    api.subirDocumentos.mockRejectedValueOnce(new Error('red'))
    const { multiple } = await montar()
    elegir(multiple, [archivo('a.pdf')])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    expect(await screen.findByRole('alert')).toHaveTextContent(T.errores.generico)
  })

  it('Subir está deshabilitado si el lote excede el tope, y lo dice', async () => {
    const { multiple } = await montar()
    elegir(multiple, ['a', 'b', 'c', 'd'].map((n) => archivo(`${n}.pdf`, 100 + n.charCodeAt(0))))
    const subir = await screen.findByRole('button', { name: T.resumen.confirmar })
    expect(subir).toBeDisabled()
    expect(screen.getByText(T.resumen.excedeArchivos(3))).toBeInTheDocument()
    fireEvent.click(subir)
    expect(api.subirDocumentos).not.toHaveBeenCalled()
  })

  it('Subir está deshabilitado si no queda nada aceptado', async () => {
    const { multiple } = await montar()
    elegir(multiple, [archivo('virus.exe')])
    expect(await screen.findByRole('button', { name: T.resumen.confirmar })).toBeDisabled()
    expect(screen.getByText(T.resumen.nadaQueSubir)).toBeInTheDocument()
  })

  it('muchos ignorados de un motivo van en un desplegable con su cantidad', async () => {
    const { multiple } = await montar({})
    elegir(multiple, [archivo('a.pdf'), ...Array.from({ length: 8 }, (_, i) => archivo(`x${i}.exe`))])
    const dialogo = await screen.findByRole('dialog')
    const detalle = within(dialogo).getByText(T.motivos.tipo_no_admitido, { exact: false }).closest('details')
    expect(detalle).not.toBeNull()
    expect(detalle).not.toHaveAttribute('open')
    expect(within(detalle).getByText('x7.exe')).toBeInTheDocument()
  })

  it('al terminar muestra lo que respondió el servidor y avisa con el resultado al cerrar', async () => {
    const resultado = {
      lote: 'L',
      aceptados: [{ id: 1, nombre: 'a.pdf' }],
      ignorados: [{ nombre: 'b.pdf', motivo: 'duplicado' }],
    }
    api.subirDocumentos.mockResolvedValue(resultado)
    const { multiple, onTerminado } = await montar()
    elegir(multiple, [archivo('a.pdf'), archivo('b.pdf', 2048)])
    fireEvent.click(await screen.findByRole('button', { name: T.resumen.confirmar }))
    const dialogo = await screen.findByRole('dialog', { name: T.resultado.titulo })
    expect(within(dialogo).getByText(T.resultado.agregados(1))).toBeInTheDocument()
    expect(within(dialogo).getByText(T.motivos.duplicado, { exact: false })).toBeInTheDocument()
    expect(within(dialogo).getByText('b.pdf')).toBeInTheDocument()
    expect(onTerminado).not.toHaveBeenCalled()
    fireEvent.click(within(dialogo).getByRole('button', { name: T.resultado.cerrar }))
    expect(onTerminado).toHaveBeenCalledWith(resultado)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('el código del componente no usa confirm(, alert( ni prompt(', () => {
    for (const ruta of ['./SelectorDeDocumentos.jsx', './resumenDeLote.js']) {
      const fuente = readFileSync(new URL(ruta, import.meta.url), 'utf8')
      expect(fuente).not.toMatch(/\b(confirm|alert|prompt)\s*\(/)
    }
  })
})
