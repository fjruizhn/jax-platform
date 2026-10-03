import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import '@testing-library/jest-dom'
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import tailwindConfig from '../../tailwind.config.js'

// Piso de 44px en los controles de Proyectos (E1, T10, decisión del
// controlador). El defecto que esto atrapa: en Tailwind 3.4, `min-h-6` (de
// TAMANO_BOTON_ACCION) y `min-h-11` en la MISMA clase dan 24px, porque el CSS
// emite `.min-h-11` ANTES que `.min-h-6` y gana el último. Un test que solo
// lea las clases no lo ve; éste COMPILA el CSS con el Tailwind del proyecto y
// resuelve, por orden de emisión, qué `min-h-*` gana en cada control
// RENDERIZADO (jsdom no aplica Tailwind, así que no se puede medir un
// getBoundingClientRect).
//
// LÍMITE: mide la altura mínima declarada (min-h-*), no el alto final; un
// control con padding vertical grande y sin min-h-* (la tarjeta de proyecto,
// py-3 sobre dos líneas) queda en EXCEPCIONES, con su motivo.

vi.mock('../api/proyectos', () => ({
  listarProyectos: vi.fn(), crearProyecto: vi.fn(), verProyecto: vi.fn(), renombrarProyecto: vi.fn(),
  cambiarEstado: vi.fn(), listarMiembros: vi.fn(), invitarMiembro: vi.fn(), cambiarPapel: vi.fn(),
  quitarMiembro: vi.fn(), buscarCandidatos: vi.fn(),
  listarDocumentos: vi.fn(), ocultarDocumento: vi.fn(), restaurarDocumento: vi.fn(),
  limitesDeDocumentos: vi.fn(), subirDocumentos: vi.fn(),
}))

import * as api from '../api/proyectos'
import Proyectos from '../pages/Proyectos'
import ProyectoDetalle from '../pages/ProyectoDetalle'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'

const T = es.proyectos
const INICIAL = useJaxStore.getState()
const MINIMO_PX = 44

// Tarjeta de la lista: enlace de bloque con py-3 sobre nombre + descripción.
const EXCEPCIONES = [/\bblock\b.*\bpy-3\b/]

async function reglasMinH(clases) {
  const css = (await postcss([tailwindcss({ ...tailwindConfig, content: [{ raw: clases.join(' ') }] })])
    .process('@tailwind utilities;', { from: undefined })).css
  const orden = []
  for (const m of css.matchAll(/\.(min-h-[\w.\\]+)\s*\{\s*min-height:\s*([^;}]+)/g)) orden.push([m[1].replace(/\\/g, ''), m[2].trim()])
  return orden
}

function aPx(valor) {
  if (valor.endsWith('rem')) return parseFloat(valor) * 16
  if (valor.endsWith('px')) return parseFloat(valor)
  return NaN
}

// min-height efectivo = el ÚLTIMO `.min-h-*` emitido entre los presentes.
function efectivo(clase, orden) {
  const presentes = new Set(clase.split(/\s+/))
  const ganadoras = orden.filter(([c]) => presentes.has(c))
  return ganadoras.length ? aPx(ganadoras[ganadoras.length - 1][1]) : null
}

async function controlesBajoElPiso(contenedor) {
  const controles = [...contenedor.querySelectorAll('button, a[href], [role="tab"]')]
  const orden = await reglasMinH(controles.map((c) => c.getAttribute('class') || ''))
  return controles
    .map((c) => ({ c, clase: c.getAttribute('class') || '' }))
    .filter(({ clase }) => !EXCEPCIONES.some((re) => re.test(clase)))
    .map(({ c, clase }) => ({ texto: (c.textContent || c.getAttribute('aria-label') || '').trim(), px: efectivo(clase, orden) }))
    .filter(({ px }) => !(px >= MINIMO_PX))
}

beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1, email: 'yo@x.com', role: 'operator' } }, true)
  Object.values(api).forEach((f) => f.mockReset())
})

describe('Proyectos: todo control mide al menos 44px (CSS compilado)', () => {
  it('el detector detecta la combinación rota (control de prueba)', async () => {
    const orden = await reglasMinH(['min-h-6 min-h-11'])
    expect(efectivo('min-h-6 min-h-11', orden)).toBe(24)
    expect(efectivo('min-h-11', orden)).toBe(44)
  })

  it('lista y modal de crear', async () => {
    api.listarProyectos.mockResolvedValue({ proyectos: [{ id: 1, uuid: 'u1', nombre: 'Alfa', descripcion: 'd', estado: 'ACTIVE', papel: 'OWNER' }], siguiente: 9 })
    const { container } = render(
      <I18nProvider><MemoryRouter initialEntries={['/proyectos']}>
        <Routes><Route path="/proyectos" element={<Proyectos />} /></Routes>
      </MemoryRouter></I18nProvider>)
    await screen.findByText('Alfa')
    fireEvent.click(screen.getByRole('button', { name: T.nuevo }))
    await screen.findByRole('dialog')
    expect(await controlesBajoElPiso(document.body)).toEqual([])
    expect(container.querySelectorAll('button, a[href]').length).toBeGreaterThan(4)
  })

  it('detalle: enlace volver, pestañas, documentos, miembros y ajustes', async () => {
    api.verProyecto.mockResolvedValue({ id: 7, uuid: 'u7', nombre: 'Alfa', descripcion: 'd', estado: 'ACTIVE', papel: 'OWNER' })
    api.listarMiembros.mockResolvedValue({ miembros: [
      { user_id: 1, email: 'yo@x.com', papel: 'OWNER', origen: 'DIRECT' }, { user_id: 3, email: 'lec@x.com', papel: 'VIEWER', origen: 'DIRECT' }] })
    api.listarDocumentos.mockResolvedValue({ documentos: [
      { id: 1, nombre: 'informe.pdf', bytes: 1024, tipo: 'pdf', estado: 'listo', error: null, subido_por_email: 'yo@x.com', creado: '2026-10-01T12:00:00', oculto: false }], siguiente: 1 })
    api.limitesDeDocumentos.mockResolvedValue({ max_bytes_archivo: 1e8, max_archivos_lote: 250, max_bytes_lote: 1e9, extensiones: ['pdf'] })
    render(
      <I18nProvider><MemoryRouter initialEntries={['/proyectos/7']}>
        <Routes><Route path="/proyectos/:id" element={<ProyectoDetalle />} /></Routes>
      </MemoryRouter></I18nProvider>)
    await screen.findByRole('heading', { name: 'Alfa' })
    // Pestaña Documentos (la de inicio): Agregar, Ver ocultos, Ocultar por fila y Cargar más.
    await waitFor(() => expect(screen.getAllByText('informe.pdf').length).toBeGreaterThan(0))
    expect(await controlesBajoElPiso(document.body)).toEqual([])
    const pestanas = screen.getAllByRole('tab')
    fireEvent.click(pestanas[1])
    await waitFor(() => expect(screen.getByText('lec@x.com')).toBeInTheDocument())
    expect(await controlesBajoElPiso(document.body)).toEqual([])
    fireEvent.click(pestanas[pestanas.length - 1])
    expect(await controlesBajoElPiso(document.body)).toEqual([])
  })
})
