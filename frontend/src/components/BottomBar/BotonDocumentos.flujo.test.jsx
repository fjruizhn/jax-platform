import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Ronda final de E2a, MAJOR-2: el botón 📄 con el selector REAL (BotonDocumentos.test.jsx lo
// reemplaza por un doble). Aquí importa a qué proyecto se sube de verdad, y que la ventana de
// elegir se cierre y devuelva el foco.
vi.mock('../../api/proyectos', () => ({
  listarProyectos: vi.fn(), verProyecto: vi.fn(), crearProyecto: vi.fn(),
  limitesDeDocumentos: vi.fn(), subirDocumentos: vi.fn(),
}))

import { verProyecto, limitesDeDocumentos, subirDocumentos } from '../../api/proyectos'
import BotonDocumentos from './BotonDocumentos'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const T = es.proyectos.documentos
const INICIAL = useJaxStore.getState()
const LIMITES = { max_bytes_archivo: 1e8, max_archivos_lote: 250, max_bytes_lote: 1e9, extensiones: ['pdf'] }
const archivo = (nombre) => new File([new Uint8Array(10)], nombre)

// Se monta dentro de un #root real, como la app: Dialogo marca ese nodo `inert` mientras está
// abierto (y vive fuera de él, por portal). Así la prueba ve lo mismo que el navegador.
function renderBoton() {
  const raiz = document.createElement('div')
  raiz.id = 'root'
  document.body.appendChild(raiz)
  return render(<I18nProvider><MemoryRouter><BotonDocumentos /></MemoryRouter></I18nProvider>, { container: raiz })
}

afterEach(() => { document.getElementById('root')?.remove() })

async function abrirPara(nombre) {
  const boton = screen.getByRole('button', { name: T.boton })
  await waitFor(() => expect(boton).toBeEnabled())
  boton.focus()
  fireEvent.click(boton)
  const dialogo = await screen.findByRole('dialog', { name: T.ventanaElegir(nombre) })
  await waitFor(() => expect(within(dialogo).getByRole('button', { name: T.elegirArchivos })).toBeEnabled())
  return { boton, dialogo }
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, proyectoActivo: null, toasts: [] }, true)
  verProyecto.mockReset().mockImplementation(async (id) => ({ id, estado: 'ACTIVE', papel: 'OWNER' }))
  limitesDeDocumentos.mockReset().mockResolvedValue(LIMITES)
  subirDocumentos.mockReset().mockResolvedValue({ lote: 'L', aceptados: [{ id: 1, nombre: 'a.pdf' }], ignorados: [] })
})

describe('BotonDocumentos con el selector real', () => {
  it('📄 abre una ventana con Archivos y Carpeta; Escape la cierra y el foco vuelve a 📄', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    renderBoton()
    const { boton, dialogo } = await abrirPara('Alfa')
    expect(within(dialogo).getByRole('button', { name: T.elegirCarpeta })).toBeInTheDocument()
    expect(document.getElementById('root')).toHaveAttribute('inert')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.getElementById('root')).not.toHaveAttribute('inert')
    expect(screen.queryByRole('button', { name: T.elegirArchivos })).toBeNull()   // nada queda fijo en la barra
    await waitFor(() => expect(boton).toHaveFocus())
  })

  it('con la ventana abierta el resto de la app (con 📄) está inerte; Cancelar la cierra y el foco vuelve a 📄', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    renderBoton()
    const { boton, dialogo } = await abrirPara('Alfa')
    const raiz = document.getElementById('root')
    expect(raiz).toHaveAttribute('inert')
    expect(raiz.contains(boton)).toBe(true)          // 📄 queda dentro de lo inerte: no se puede volver a pulsar
    expect(raiz.contains(dialogo)).toBe(false)       // la ventana vive fuera, por portal
    fireEvent.click(within(dialogo).getByRole('button', { name: T.resumen.cancelar }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(raiz).not.toHaveAttribute('inert')
    await waitFor(() => expect(boton).toHaveFocus())
  })

  it('el resumen se titula con el proyecto de destino', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    renderBoton()
    const { dialogo } = await abrirPara('Alfa')
    fireEvent.change(dialogo.querySelector('input[multiple]'), { target: { files: [archivo('a.pdf')] } })
    expect(await screen.findByRole('dialog', { name: T.resumen.tituloA('Alfa') })).toBeInTheDocument()
  })

  it('7→9 con el resumen abierto: el flujo se reinicia y la subida va a 9, nunca a 7', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    renderBoton()
    const { dialogo } = await abrirPara('Alfa')
    fireEvent.change(dialogo.querySelector('input[multiple]'), { target: { files: [archivo('a.pdf')] } })
    await screen.findByRole('dialog', { name: T.resumen.tituloA('Alfa') })
    act(() => useJaxStore.setState({ proyectoActivo: { id: 9, nombre: 'Nueve' } }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByRole('button', { name: T.resumen.confirmar })).toBeNull()
    const segundo = await abrirPara('Nueve')
    fireEvent.change(segundo.dialogo.querySelector('input[multiple]'), { target: { files: [archivo('b.pdf')] } })
    const resumen = await screen.findByRole('dialog', { name: T.resumen.tituloA('Nueve') })
    fireEvent.click(within(resumen).getByRole('button', { name: T.resumen.confirmar }))
    await screen.findByRole('dialog', { name: T.resultado.titulo })
    expect(subirDocumentos).toHaveBeenCalledTimes(1)
    expect(subirDocumentos.mock.calls[0][0]).toBe(9)
  })
})
