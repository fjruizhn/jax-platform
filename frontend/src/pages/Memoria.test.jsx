import { readFileSync } from 'fs'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Pantalla de Memoria (Task 6, plan 2026-09-20-memoria-admin.md). Ver el
// comentario de módulo en Memoria.jsx para el porqué de cada decisión: lote
// desde el grupo, casi-duplicados marcados y sin preseleccionar, aprobar sin
// ventana propia, corregir/caducar con ConfirmacionSuma.
vi.mock('../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../api/client'
import Memoria from './Memoria'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'
import en from '../i18n/en.js'

// Store REAL, no mockeado: Memoria.jsx monta su propio <Toast/> (como
// Admin.jsx), y los avisos de éxito/error se verifican leyendo lo que ese
// Toast pinta de verdad -- mockear el store entero dejaría <Toast/> sin
// `toasts`/`dismissToast` y rompería el árbol (medido: TypeError en
// Toast.jsx al mockear sólo addToast/user).
const usuario = { user_id: 7, email: 'fernando@rich-hn.com', role: 'superadmin' }

// Hechos 136/138/139: el ejemplo real del spec §1 -- "JAX no tiene capacidad
// nativa para SQL" dicho tres veces, del 4 de septiembre, hoy falsos.
const HECHO_136 = {
  id: 136, texto: 'JAX no tiene capacidad nativa para SQL.', tipo: 'technical', confianza: 0.8,
  verificado: false, verificado_por: null, verificado_at: null, vence_at: null, vencido: false,
  creado_at: '2026-09-04T10:00:00', superado_por: null,
  procedencia: { mensaje_id: 501, faceta: 'hyde' },
}
const HECHO_138 = {
  id: 138, texto: 'JAX carece de capacidad nativa para consultas SQL.', tipo: 'technical', confianza: 0.75,
  verificado: false, verificado_por: null, verificado_at: null, vence_at: null, vencido: false,
  creado_at: '2026-09-04T11:00:00', superado_por: null,
  procedencia: { mensaje_id: 502, faceta: 'jekyll' },
}
// A propósito SIN procedencia: cubre "vacía y marcada, nunca omitida".
const HECHO_139 = {
  id: 139, texto: 'No hay soporte nativo de SQL en JAX.', tipo: 'technical', confianza: 0.7,
  verificado: false, verificado_por: null, verificado_at: null, vence_at: null, vencido: false,
  creado_at: '2026-09-04T12:00:00', superado_por: null,
  procedencia: { mensaje_id: null, faceta: null },
}
const HECHO_201 = {
  id: 201, texto: 'Fernando prefiere reuniones cortas.', tipo: 'preference', confianza: 0.9,
  verificado: true, verificado_por: 2, verificado_at: '2026-09-10T09:00:00', vence_at: null, vencido: false,
  creado_at: '2026-09-09T08:00:00', superado_por: null,
  procedencia: { mensaje_id: 900, faceta: 'thot' },
}
// Un hecho caducado en una sesión anterior: sólo sale de `incluir_vencidos=true`
// (sección Vencidos), no del listado por defecto ni de ningún grupo.
const HECHO_VENCIDO = {
  id: 301, texto: 'JAX corre sólo en hall9000.', tipo: 'technical', confianza: 0.6,
  verificado: true, verificado_por: 3, verificado_at: '2026-08-01T09:00:00',
  vence_at: '2026-08-15T00:00:00', vencido: true,
  creado_at: '2026-07-01T08:00:00', superado_por: null,
  procedencia: { mensaje_id: 700, faceta: 'thot' },
}

const GRUPOS_DOS_TEMAS = {
  grupos: [
    { tema: HECHO_139.texto, hechos: [139, 138, 136], sin_verificar: 3, casi_duplicados: [[136, 138, 139]] },
    { tema: HECHO_201.texto, hechos: [201], sin_verificar: 0, casi_duplicados: [] },
  ],
}
const HECHOS_DOS_TEMAS = { hechos: [HECHO_136, HECHO_138, HECHO_139, HECHO_201], total: 4 }

// Escenario a escala, mismo patrón que la base de carga de 10.000 hechos
// (docs/carga-memoria-2026-09-20.md): un grupo cuyo `sin_verificar` real
// (contado por el backend sobre TODOS sus miembros activos, sin el cap de
// GET /hechos) es mucho mayor que lo que esta pantalla llegó a cargar --
// exactamente el defecto medido por Fernando (la cabecera decía "467 sin
// verificar" con la base en 8.000).
const GRUPO_A_ESCALA = {
  grupos: [{ tema: HECHO_136.texto, hechos: [136], sin_verificar: 5000, casi_duplicados: [] }],
}
// `total` de cada respuesta es el que SQL_CONTAR calcula server-side, no el
// largo de `hechos`: 9.000 vigentes en total, 9.500 vigentes+vencidos --
// Memoria.jsx resta las dos para sacar 500 vencidos reales, sin pedir un
// tercer endpoint (ver el comentario de módulo en Memoria.jsx).
const HECHOS_A_ESCALA = { hechos: [HECHO_136], total: 9000 }
const VENCIDOS_A_ESCALA = { hechos: [HECHO_VENCIDO], total: 9500 }

function renderMemoria() {
  return render(<I18nProvider><MemoryRouter><Memoria /></MemoryRouter></I18nProvider>)
}

// GET por URL, como AdminUsers.test.jsx. Memoria.jsx pide /hechos dos veces
// (el listado activo y, aparte, `incluir_vencidos=true` para la sección
// Vencidos) -- por defecto las dos devuelven `hechos` (ningún fixture trae
// vencido:true), así que la sección Vencidos queda vacía salvo que el test
// use `servirGetConVencidos`.
function servirGet(grupos, hechos) {
  api.get.mockImplementation((url) => {
    if (url === '/admin/memoria/grupos') return Promise.resolve({ data: grupos })
    if (url === '/admin/memoria/hechos') return Promise.resolve({ data: hechos })
    return Promise.reject(new Error(`url no mockeada: ${url}`))
  })
}

function servirGetConVencidos(grupos, hechosActivos, hechosVencidos) {
  api.get.mockImplementation((url, config) => {
    if (url === '/admin/memoria/grupos') return Promise.resolve({ data: grupos })
    if (url === '/admin/memoria/hechos') {
      return Promise.resolve({ data: config?.params?.incluir_vencidos ? hechosVencidos : hechosActivos })
    }
    return Promise.reject(new Error(`url no mockeada: ${url}`))
  })
}

// Distingue las CUATRO llamadas de verdad que hace Memoria.jsx (a diferencia
// de los dos helpers de arriba, que sirven la misma data a cualquier GET
// /hechos): el listado activo, `incluir_vencidos=true`, y el conteo liviano
// `verificado=false&limite=1` que alimenta el total real de la cabecera.
// Sin esto no se puede probar la diferencia entre "lo cargado" y "lo real"
// -- que es exactamente lo que este defecto necesita, medido con la base de
// carga (10.000 hechos): la cabecera decía 467, la base tenía 8.000.
function servirGetEscala({ grupos, hechosActivos, hechosVencidos = { hechos: [], total: 0 }, totalSinVerificarReal }) {
  api.get.mockImplementation((url, config) => {
    if (url === '/admin/memoria/grupos') return Promise.resolve({ data: grupos })
    if (url === '/admin/memoria/hechos') {
      const params = config?.params || {}
      if (params.verificado === false && params.limite === 1) {
        return Promise.resolve({ data: { hechos: [], total: totalSinVerificarReal } })
      }
      if (params.incluir_vencidos) return Promise.resolve({ data: hechosVencidos })
      return Promise.resolve({ data: hechosActivos })
    }
    return Promise.reject(new Error(`url no mockeada: ${url}`))
  })
}

beforeEach(() => {
  api.get.mockReset()
  api.post.mockReset()
  useJaxStore.setState({ user: usuario, toasts: [] })
})

describe('Memoria', () => {
  it('cada hecho muestra su procedencia', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-136')
    expect(within(ficha).getByText(es.memoria.procedencia.mensaje)).toBeInTheDocument()
    expect(within(ficha).getByText(es.memoria.procedencia.faceta)).toBeInTheDocument()
    expect(within(ficha).getByText('501')).toBeInTheDocument()
    expect(within(ficha).getByText('hyde')).toBeInTheDocument()
  })

  it('la procedencia vacía se muestra marcada, nunca se omite', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-139')
    expect(within(ficha).getByText(es.memoria.procedencia.mensaje)).toBeInTheDocument()
    expect(within(ficha).getByText(es.memoria.procedencia.sinMensaje)).toBeInTheDocument()
    expect(within(ficha).getByText(es.memoria.procedencia.sinFaceta)).toBeInTheDocument()
  })

  it('caducar pide confirmacion en ventana propia, no del navegador', async () => {
    // Fixture de un solo hecho, como el ejemplo del plan: un único botón
    // "Caducar" en toda la pantalla.
    servirGet(
      { grupos: [{ tema: HECHO_201.texto, hechos: [201], sin_verificar: 0, casi_duplicados: [] }] },
      { hechos: [HECHO_201], total: 1 },
    )
    const confirmSpy = vi.spyOn(window, 'confirm')
    renderMemoria()
    fireEvent.click(await screen.findByRole('button', { name: es.memoria.caducar }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('ningun texto visible esta hardcodeado', () => {
    const fuente = readFileSync('src/pages/Memoria.jsx', 'utf8')
    expect(fuente).not.toMatch(/>[A-ZÁÉÍÓÚÑ][a-záéíóúñ ]{3,}</)
  })

  it('sin confirm/alert/prompt, ni desnudos ni con window.', () => {
    for (const archivo of ['src/pages/Memoria.jsx', 'src/components/Memoria/GrupoDeHechos.jsx', 'src/components/Memoria/FichaDeHecho.jsx', 'src/components/Memoria/SeccionVencidos.jsx']) {
      const fuente = readFileSync(archivo, 'utf8')
      expect(fuente).not.toMatch(/(^|[^.a-zA-Z])(confirm|alert|prompt)\(/)
    }
  })

  it('dos temas se muestran en grupos separados', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    renderMemoria()
    await screen.findByTestId('grupo-0')
    await screen.findByTestId('grupo-1')
    expect(screen.getByTestId('grupo-0')).toHaveTextContent(HECHO_139.texto)
    expect(screen.getByTestId('grupo-1')).toHaveTextContent(HECHO_201.texto)
  })

  it('los casi-duplicados salen juntos y marcados, sin preseleccionar para el lote', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    expect(within(grupo).getByText(es.memoria.casiDuplicados(3))).toBeInTheDocument()
    // Los tres checkboxes del cluster arrancan sin marcar (nota de módulo,
    // punto 3): aprobar el grupo de un solo click no debe aprobar contradicciones.
    for (const id of [136, 138, 139]) {
      const ficha = within(grupo).getByTestId(`hecho-${id}`)
      expect(within(ficha).getByRole('checkbox')).not.toBeChecked()
    }
  })

  it('aprobar en lote desde el grupo manda todos los seleccionados de una vez', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockResolvedValue({ data: { aprobados: 3 } })
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    fireEvent.click(within(grupo).getByRole('button', { name: es.memoria.seleccionarTodos }))
    fireEvent.click(within(grupo).getByRole('button', { name: es.memoria.aprobarSeleccionados(3) }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/memoria/hechos/aprobar', { ids: [139, 138, 136] }))
    expect(await screen.findByText(es.memoria.aprobados(3))).toBeInTheDocument()
  })

  it('aprobar un hecho suelto no abre ninguna ventana', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockResolvedValue({ data: { aprobados: 1 } })
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-136')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.aprobar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/memoria/hechos/aprobar', { ids: [136] }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('un error al aprobar se avisa por toast, con el codigo traducido', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockRejectedValue({ response: { status: 404, data: { detail: 'hecho_no_encontrado' } } })
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-136')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.aprobar }))
    expect(await screen.findByText(es.memoria.errores.hecho_no_encontrado)).toBeInTheDocument()
  })

  it('corregir abre un formulario y confirma con ConfirmacionSuma antes de escribir', async () => {
    servirGet(
      { grupos: [{ tema: HECHO_201.texto, hechos: [201], sin_verificar: 0, casi_duplicados: [] }] },
      { hechos: [HECHO_201], total: 1 },
    )
    api.post.mockResolvedValue({ data: { nuevo_id: 555 } })
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-201')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.corregir }))
    const formulario = await screen.findByRole('dialog')
    expect(within(formulario)).toBeTruthy()
    fireEvent.change(screen.getByLabelText(es.memoria.corregirTexto), { target: { value: 'texto corregido de prueba' } })
    fireEvent.click(within(formulario).getByRole('button', { name: es.memoria.corregirGuardar }))
    // Paso 2: ConfirmacionSuma, la escritura destructiva no salió todavía.
    expect(api.post).not.toHaveBeenCalled()
    const confirmacion = await screen.findByRole('dialog')
    fireEvent.change(within(confirmacion).getByLabelText(/=/), { target: { value: sumaCorrecta(confirmacion) } })
    fireEvent.click(within(confirmacion).getByRole('button', { name: es.memoria.corregirConfirmarBoton }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/memoria/hechos/201/corregir', { texto: 'texto corregido de prueba' }))
  })

  it('caducar, confirmado, llama al endpoint con una fecha (no null) y no borra la ficha', async () => {
    servirGet(
      { grupos: [{ tema: HECHO_201.texto, hechos: [201], sin_verificar: 0, casi_duplicados: [] }] },
      { hechos: [HECHO_201], total: 1 },
    )
    api.post.mockResolvedValue({ data: { ok: true } })
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-201')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.caducar }))
    const confirmacion = await screen.findByRole('dialog')
    fireEvent.change(within(confirmacion).getByLabelText(/=/), { target: { value: sumaCorrecta(confirmacion) } })
    fireEvent.click(within(confirmacion).getByRole('button', { name: es.memoria.caducarConfirmar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/admin/memoria/hechos/201/caducar',
      expect.objectContaining({ vence_at: expect.any(String) }),
    ))
    expect(await screen.findByTestId('hecho-201')).toBeInTheDocument()
    expect(await screen.findByText(es.memoria.vencido)).toBeInTheDocument()
  })

  it('fundir pide confirmacion en ventana propia y llama al endpoint de fusion, no a caducar', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockResolvedValue({ data: { aprobados: 1, superados: 2 } })
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    fireEvent.click(within(grupo).getByRole('button', { name: es.memoria.fundir }))
    const confirmacion = await screen.findByRole('dialog')
    fireEvent.change(within(confirmacion).getByLabelText(/=/), { target: { value: sumaCorrecta(confirmacion) } })
    fireEvent.click(within(confirmacion).getByRole('button', { name: es.memoria.fundirConfirmar }))
    // grupo.hechos = [139, 138, 136] (creado_at DESC): 139 es el más reciente.
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/admin/memoria/hechos/fundir', { superviviente_id: 139, absorbidos: [138, 136] },
    ))
    expect(api.post).not.toHaveBeenCalledWith(expect.stringMatching(/\/caducar$/), expect.anything())
    expect(await screen.findByText(es.memoria.fundido)).toBeInTheDocument()
  })

  it('la seccion Vencidos no aparece cuando no hay hechos vencidos', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    renderMemoria()
    await screen.findByTestId('grupo-0')
    expect(screen.queryByTestId(/^vencido-/)).not.toBeInTheDocument()
  })

  it('la seccion Vencidos lista los hechos vencidos, cerrada por defecto, y deja quitarles la caducidad', async () => {
    servirGetConVencidos({ grupos: [] }, { hechos: [], total: 0 }, { hechos: [HECHO_VENCIDO], total: 1 })
    api.post.mockResolvedValue({ data: { ok: true } })
    renderMemoria()
    const fila = await screen.findByTestId('vencido-301')
    expect(within(fila).getByText(HECHO_VENCIDO.texto)).toBeInTheDocument()
    const detalle = fila.closest('details')
    expect(detalle).not.toHaveAttribute('open')
    // Accesibilidad: varios "Quitar caducidad" en la lista se distinguen por
    // el hecho al que corresponden (aria-label), no sólo por el texto visible.
    fireEvent.click(within(fila).getByRole('button', { name: es.memoria.quitarCaducidadDe(301) }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/admin/memoria/hechos/301/caducar', { vence_at: null },
    ))
    expect(await screen.findByText(es.memoria.caducidadQuitada)).toBeInTheDocument()
  })

  // Defecto medido por Fernando en la base de carga (10.000 hechos, 8.000
  // sin verificar): la cabecera decía "467 sin verificar" -- contaba lo que
  // el cap de 500 de GET /hechos alcanzó a cargar, no lo que había. Los
  // tres tests de acá prueban que ESE número chico ya no puede salir a
  // secas cuando hay más de verdad.
  it('la cabecera dice la verdad: si hay más sin verificar de los que cargó, no muestra el número chico a secas', async () => {
    servirGetEscala({
      grupos: GRUPO_A_ESCALA,
      hechosActivos: HECHOS_A_ESCALA,
      totalSinVerificarReal: 8000,
    })
    renderMemoria()
    await screen.findByTestId('grupo-0')
    // Lo cargado (1 hecho, HECHO_136) no puede presentarse como si fuera
    // el total: tiene que decir explícitamente que es un subconjunto.
    expect(screen.queryByText(es.memoria.totalSinVerificar(1))).not.toBeInTheDocument()
    expect(screen.getByText(es.memoria.totalSinVerificarSubconjunto(1, 8000))).toBeInTheDocument()
  })

  it('el contador de un grupo no miente: si el grupo tiene más miembros sin verificar de los que se cargaron, lo dice', async () => {
    servirGetEscala({
      grupos: GRUPO_A_ESCALA,
      hechosActivos: HECHOS_A_ESCALA,
      totalSinVerificarReal: 8000,
    })
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    // El grupo trae sin_verificar:5000 del backend (Task 5, sobre TODOS sus
    // miembros activos), pero esta pantalla sólo cargó 1 -- ni el número
    // chico solo ni "Todos verificados" serían ciertos acá.
    expect(within(grupo).queryByText(es.memoria.totalSinVerificar(1))).not.toBeInTheDocument()
    expect(within(grupo).queryByText(es.memoria.grupoTodosVerificados)).not.toBeInTheDocument()
    expect(within(grupo).getByText(es.memoria.totalSinVerificarSubconjunto(1, 5000))).toBeInTheDocument()
  })

  it('cuando lo cargado coincide con lo real, la cabecera usa el texto simple de siempre', async () => {
    servirGetEscala({
      grupos: GRUPOS_DOS_TEMAS,
      hechosActivos: HECHOS_DOS_TEMAS,
      hechosVencidos: { hechos: [], total: HECHOS_DOS_TEMAS.total },
      totalSinVerificarReal: 3,
    })
    renderMemoria()
    await screen.findByTestId('grupo-0')
    // Aparece dos veces con el texto simple: la cabecera (3 de 3 reales) y
    // el grupo 0 (sin_verificar:3, los 3 cargados) -- ninguna de las dos
    // necesita la variante "mostrando X de Y".
    expect(screen.getAllByText(es.memoria.totalSinVerificar(3))).toHaveLength(2)
    expect(screen.queryByText(/mostrando/)).not.toBeInTheDocument()
  })

  it('la seccion Vencidos no se conforma con lo que cargó: si hay más vencidos reales, lo dice', async () => {
    servirGetEscala({
      grupos: { grupos: [] },
      hechosActivos: HECHOS_A_ESCALA,
      hechosVencidos: VENCIDOS_A_ESCALA,
      totalSinVerificarReal: 8000,
    })
    renderMemoria()
    // 9.500 (vigentes+vencidos) menos 9.000 (vigentes) = 500 vencidos
    // reales; la llamada trajo sólo 1 -- Memoria.jsx los resta sin pedir un
    // tercer endpoint (ver comentario de módulo).
    expect(await screen.findByText(es.memoria.vencidosResumenSubconjunto(1, 500))).toBeInTheDocument()
    expect(screen.queryByText(es.memoria.vencidosResumen(1))).not.toBeInTheDocument()
  })

  it('los textos nuevos de memoria tienen es y en', () => {
    for (const t of [es, en]) {
      expect(typeof t.memoria.titulo).toBe('string')
      expect(typeof t.memoria.casiDuplicados(3)).toBe('string')
      expect(typeof t.memoria.errores.hecho_no_encontrado).toBe('string')
      expect(typeof t.memoria.vencidosResumen(1)).toBe('string')
      expect(typeof t.memoria.vencidosResumenSubconjunto(1, 500)).toBe('string')
      expect(typeof t.memoria.totalSinVerificarSubconjunto(1, 8000)).toBe('string')
      expect(typeof t.memoria.vencidoDesde('2026-08-15')).toBe('string')
      expect(typeof t.memoria.quitarCaducidadDe(1)).toBe('string')
    }
  })
})

// ConfirmacionSuma pinta "Resolvé a + b = ?" vía t.confirmSumLabel(a, b); acá
// sólo hace falta sumar los dos números que aparecen en el label del campo.
function sumaCorrecta(dialogo) {
  const label = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]')?.textContent || ''
  const numeros = label.match(/-?\d+/g)?.map(Number) || []
  return String(numeros.reduce((a, b) => a + b, 0))
}
