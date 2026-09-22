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
import Toast from '../components/Notifications/Toast'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'
import en from '../i18n/en.js'

// Store REAL, no mockeado: los avisos de éxito/error se verifican leyendo lo
// que <Toast/> pinta de verdad -- mockear el store entero lo dejaría sin
// `toasts`/`dismissToast` y rompería el árbol (medido: TypeError en
// Toast.jsx al mockear sólo addToast/user).
//
// Corrección (2026-09-20): Memoria.jsx YA NO monta su propio <Toast/> --
// ahora es una ruta anidada de Admin.jsx, que lo monta una sola vez para
// todas sus pantallas (ver el comentario de módulo en Memoria.jsx). Este
// test simula esa parte del caparazón montando <Toast/> junto a <Memoria/>,
// igual que hace Admin.jsx de verdad.
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

// Los tres (136/138/139) arrancan sin verificar (ver arriba): con la regla
// "el verificado gana, si no hay ninguno gana el más reciente"
// (_elegir_superviviente, backend/api/admin/memoria.py), el superviviente
// sigue siendo 139 -- el más nuevo de los tres.
const GRUPOS_DOS_TEMAS = {
  grupos: [
    {
      tema: HECHO_139.texto, hechos: [139, 138, 136], sin_verificar: 3,
      casi_duplicados: [{
        ids: [136, 138, 139], superviviente_id: 139,
        superviviente_verificado: false, superviviente_texto: HECHO_139.texto,
      }],
    },
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

// MAJOR B (revisión adversarial de jax-platform PR 146, ronda 4): fixtures
// SIN cluster (a diferencia de GRUPOS_DOS_TEMAS, donde 136/138/139 arrancan
// sin_verificar pero YA excluidos del lote por estar en casi_duplicados) --
// necesarias para poder deseleccionar a mano y comprobar que la recarga no
// los vuelve a marcar.
function hechoDePrueba(id, extra = {}) {
  return {
    id, texto: `hecho de prueba ${id}`, tipo: 'technical', confianza: 0.8,
    verificado: false, verificado_por: null, verificado_at: null, vence_at: null, vencido: false,
    creado_at: '2026-09-22T10:00:00', superado_por: null,
    procedencia: { mensaje_id: null, faceta: null },
    ...extra,
  }
}
const GRUPOS_PRESERVAR = {
  grupos: [
    { tema: 'tema A (sin cluster)', hechos: [401, 402, 403, 404, 405], sin_verificar: 5, casi_duplicados: [] },
    { tema: 'tema B (gatillo)', hechos: [410], sin_verificar: 1, casi_duplicados: [] },
  ],
}
const HECHOS_PRESERVAR = {
  hechos: [401, 402, 403, 404, 405, 410].map((id) => hechoDePrueba(id)),
  total: 6,
}

function renderMemoria() {
  return render(<I18nProvider><MemoryRouter><Memoria /><Toast /></MemoryRouter></I18nProvider>)
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

// SOSPECHA (revisión adversarial de jax-platform PR 146, ronda 4): varios
// tests disparaban una acción que recarga (M2: aprobar/caducar/corregir/
// fundir TODOS llaman a `cargar()` de nuevo) y terminaban sin esperar a que
// ESA recarga completara -- dejaban una cadena de promesas pendiente que
// podía resolverse durante el test SIGUIENTE, después de que `beforeEach`
// ya hubiera limpiado los mocks. `api.get.mockReset()` sólo limpia el
// historial de llamadas -- no cancela una promesa ya en vuelo de un render
// que sigue montado (RTL no desmonta entre tests sin `cleanup()` explícito
// en este archivo). Este helper espera la recarga hasta el final, con la
// MISMA señal que ya usaba el test M2 (el conteo de llamadas a /grupos).
async function esperarRecargaCompleta(vecesEsperadas = 2) {
  await waitFor(() => {
    const llamadas = api.get.mock.calls.filter(([url]) => url === '/admin/memoria/grupos').length
    expect(llamadas).toBe(vecesEsperadas)
  })
}

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

  it('M3: el aviso y la confirmacion de fundir cuentan TODOS los ids del cluster, no solo los cargados', async () => {
    // El cluster (y el grupo.hechos que lo contiene -- backend/api/admin/
    // memoria.py::agrupar_por_tema no tiene el cap de 500) trae un cuarto
    // id (999) que GET /hechos NO cargó (ni HECHOS_DOS_TEMAS lo tiene) --
    // el caso real que motiva M3, no un fixture inválido (cluster.ids
    // siempre es subconjunto de grupo.hechos en el backend real).
    servirGet(
      {
        grupos: [{
          tema: HECHO_139.texto, hechos: [139, 138, 136, 999], sin_verificar: 4,
          casi_duplicados: [{
            ids: [136, 138, 139, 999], superviviente_id: 139,
            superviviente_verificado: false, superviviente_texto: HECHO_139.texto,
          }],
        }],
      },
      HECHOS_DOS_TEMAS,
    )
    api.post.mockResolvedValue({ data: { superados: 3 } })
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    expect(within(grupo).getByText(es.memoria.casiDuplicadosSubconjunto(4, 1))).toBeInTheDocument()
    expect(within(grupo).queryByText(es.memoria.casiDuplicados(3))).not.toBeInTheDocument()

    fireEvent.click(within(grupo).getByRole('button', { name: es.memoria.fundir }))
    const confirmacion = await screen.findByRole('dialog')
    expect(confirmacion).toHaveTextContent(es.memoria.casiDuplicadosNoCargados(1))
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
    // SOSPECHA: esperar a que la recarga que dispara `aprobar()` (M2)
    // termine, para no dejar una promesa pendiente que resuelva en el test
    // siguiente.
    await esperarRecargaCompleta()
  })

  it('aprobar un hecho suelto no abre ninguna ventana', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockResolvedValue({ data: { aprobados: 1 } })
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-136')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.aprobar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/memoria/hechos/aprobar', { ids: [136] }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    // SOSPECHA: ídem -- esperar la recarga completa antes de terminar.
    await esperarRecargaCompleta()
  })

  it('M2: aprobar vuelve a pedir /grupos (is_verified decide quien sobrevive en un cluster)', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockResolvedValue({ data: { aprobados: 1 } })
    renderMemoria()
    await screen.findByTestId('hecho-136')
    const llamadasAGruposAntes = api.get.mock.calls.filter(([url]) => url === '/admin/memoria/grupos').length
    expect(llamadasAGruposAntes).toBe(1) // la carga inicial
    const ficha = await screen.findByTestId('hecho-136')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.aprobar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/memoria/hechos/aprobar', { ids: [136] }))
    await waitFor(() => {
      const llamadas = api.get.mock.calls.filter(([url]) => url === '/admin/memoria/grupos').length
      expect(llamadas).toBe(2) // la carga inicial + la recarga tras aprobar
    })
  })

  // MAJOR B (revisión adversarial de jax-platform PR 146, ronda 4): la
  // recarga (M2) reconstruía `seleccionados` incondicionalmente, así que
  // aprobar un hecho de OTRO grupo -- o cualquier otra acción -- volvía a
  // marcar hechos que Fernando ya había desmarcado a mano. Los ids que ya
  // estaban en pantalla tienen que CONSERVAR su estado; sólo los ids NUEVOS
  // reciben el default.
  it('la recarga preserva la seleccion que Fernando ya desmarco a mano (no vuelve a marcar ids conocidos)', async () => {
    let aprobado410 = false
    api.get.mockImplementation((url, config) => {
      if (url === '/admin/memoria/grupos') return Promise.resolve({ data: GRUPOS_PRESERVAR })
      if (url === '/admin/memoria/hechos') {
        const params = config?.params || {}
        if (params.incluir_vencidos) return Promise.resolve({ data: { hechos: [], total: 0 } })
        if (params.verificado === false && params.limite === 1) {
          return Promise.resolve({ data: { hechos: [], total: aprobado410 ? 5 : 6 } })
        }
        const hechos = HECHOS_PRESERVAR.hechos.map((h) => (
          h.id === 410 && aprobado410 ? { ...h, verificado: true } : h
        ))
        return Promise.resolve({ data: { hechos, total: hechos.length } })
      }
      return Promise.reject(new Error(`url no mockeada: ${url}`))
    })
    api.post.mockImplementation((url) => {
      if (url === '/admin/memoria/hechos/aprobar') { aprobado410 = true; return Promise.resolve({ data: { aprobados: 1 } }) }
      return Promise.reject(new Error(`post no mockeado: ${url}`))
    })
    renderMemoria()
    const grupoA = await screen.findByTestId('grupo-0')
    // Los 5 arrancan seleccionados (sin verificar, sin cluster -- default).
    for (const id of [401, 402, 403, 404, 405]) {
      expect(within(within(grupoA).getByTestId(`hecho-${id}`)).getByRole('checkbox')).toBeChecked()
    }
    // Fernando desmarca 404 y 405 a mano.
    fireEvent.click(within(within(grupoA).getByTestId('hecho-404')).getByRole('checkbox'))
    fireEvent.click(within(within(grupoA).getByTestId('hecho-405')).getByRole('checkbox'))
    expect(within(within(grupoA).getByTestId('hecho-404')).getByRole('checkbox')).not.toBeChecked()
    expect(within(within(grupoA).getByTestId('hecho-405')).getByRole('checkbox')).not.toBeChecked()
    expect(within(grupoA).getByRole('button', { name: es.memoria.aprobarSeleccionados(3) })).toBeInTheDocument()

    // Aprueba el hecho 410 -- de OTRO grupo -- que dispara la recarga (M2).
    const grupoB = await screen.findByTestId('grupo-1')
    fireEvent.click(within(within(grupoB).getByTestId('hecho-410')).getByRole('button', { name: es.memoria.aprobar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/memoria/hechos/aprobar', { ids: [410] }))
    await waitFor(() => {
      const llamadas = api.get.mock.calls.filter(([url]) => url === '/admin/memoria/grupos').length
      expect(llamadas).toBe(2)
    })

    // 404 y 405 SIGUEN desmarcados tras la recarga -- no se re-seleccionaron.
    const grupoATrasRecarga = screen.getByTestId('grupo-0')
    expect(within(within(grupoATrasRecarga).getByTestId('hecho-401')).getByRole('checkbox')).toBeChecked()
    expect(within(within(grupoATrasRecarga).getByTestId('hecho-402')).getByRole('checkbox')).toBeChecked()
    expect(within(within(grupoATrasRecarga).getByTestId('hecho-403')).getByRole('checkbox')).toBeChecked()
    expect(within(within(grupoATrasRecarga).getByTestId('hecho-404')).getByRole('checkbox')).not.toBeChecked()
    expect(within(within(grupoATrasRecarga).getByTestId('hecho-405')).getByRole('checkbox')).not.toBeChecked()
    expect(within(grupoATrasRecarga).getByRole('button', { name: es.memoria.aprobarSeleccionados(3) })).toBeInTheDocument()
  })

  // MAJOR B (revisión adversarial de jax-platform PR 146, ronda 4): la
  // recarga posterior a una acción no puede tapar la lista con la pantalla
  // de "Cargando…" inicial -- Fernando pierde de vista lo que estaba
  // revisando cada vez que aprueba/caduca/corrige/funde algo.
  it('la recarga tras una accion no reemplaza la lista por la pantalla completa de carga', async () => {
    let llamadasGrupos = 0
    let resolverSegundaLlamada
    api.get.mockImplementation((url, config) => {
      if (url === '/admin/memoria/grupos') {
        llamadasGrupos += 1
        if (llamadasGrupos === 1) return Promise.resolve({ data: GRUPOS_PRESERVAR })
        return new Promise((resolve) => { resolverSegundaLlamada = resolve })
      }
      if (url === '/admin/memoria/hechos') {
        const params = config?.params || {}
        if (params.incluir_vencidos) return Promise.resolve({ data: { hechos: [], total: 0 } })
        if (params.verificado === false && params.limite === 1) return Promise.resolve({ data: { hechos: [], total: 6 } })
        return Promise.resolve({ data: HECHOS_PRESERVAR })
      }
      return Promise.reject(new Error(`url no mockeada: ${url}`))
    })
    api.post.mockResolvedValue({ data: { aprobados: 1 } })
    renderMemoria()
    await screen.findByTestId('grupo-0')
    const grupoB = screen.getByTestId('grupo-1')
    fireEvent.click(within(within(grupoB).getByTestId('hecho-410')).getByRole('button', { name: es.memoria.aprobar }))

    // Mientras la segunda llamada a /grupos sigue pendiente (recarga en
    // curso): la lista sigue en pantalla, y NO aparece el "Cargando…" de
    // pantalla completa.
    await waitFor(() => expect(llamadasGrupos).toBe(2))
    expect(screen.getByTestId('grupo-0')).toBeInTheDocument()
    expect(screen.queryByText(es.memoria.cargando)).not.toBeInTheDocument()
    expect(screen.getByText(es.memoria.actualizando)).toBeInTheDocument()

    resolverSegundaLlamada({ data: GRUPOS_PRESERVAR })
    await waitFor(() => expect(screen.queryByText(es.memoria.actualizando)).not.toBeInTheDocument())
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
    // SOSPECHA: esperar la recarga que dispara `confirmarCorreccion()`.
    await esperarRecargaCompleta()
  })

  it('caducar, confirmado, llama al endpoint con una fecha (no null) y recarga /grupos', async () => {
    // M2 (revisión adversarial de jax-platform PR 146, tercera vuelta):
    // ahora que caducar recarga, el hecho SALE del grupo (GET /grupos
    // excluye vencidos, backend/api/admin/memoria.py::SQL_ACTIVOS_CON_
    // VECTOR) y pasa a la sección Vencidos -- el mock refleja ese cambio de
    // estado real en la SEGUNDA vuelta de cada endpoint, no la primera.
    let caducado = false
    api.get.mockImplementation((url, config) => {
      if (url === '/admin/memoria/grupos') {
        return Promise.resolve({
          data: { grupos: caducado ? [] : [{ tema: HECHO_201.texto, hechos: [201], sin_verificar: 0, casi_duplicados: [] }] },
        })
      }
      if (url === '/admin/memoria/hechos') {
        const params = config?.params || {}
        if (params.incluir_vencidos) {
          return Promise.resolve({ data: { hechos: [{ ...HECHO_201, vencido: caducado }], total: 1 } })
        }
        return Promise.resolve({ data: caducado ? { hechos: [], total: 0 } : { hechos: [HECHO_201], total: 1 } })
      }
      return Promise.reject(new Error(`url no mockeada: ${url}`))
    })
    api.post.mockImplementation((url) => {
      if (url === '/admin/memoria/hechos/201/caducar') { caducado = true; return Promise.resolve({ data: { ok: true } }) }
      return Promise.reject(new Error(`post no mockeado: ${url}`))
    })
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
    // MINOR 6 (revisión adversarial de jax-platform PR 146, ronda 4): antes
    // del arreglo de MAJOR B, esta aserción podía pasar por la razón
    // EQUIVOCADA -- `cargando` ocultaba TODA la lista durante cualquier
    // recarga (incluida ésta), así que "grupo-0 no está" podía ser cierto
    // por estar en la pantalla de "Cargando…" y no porque el dato real ya
    // hubiera cambiado. Con el arreglo (la recarga usa `recargando`, que NO
    // oculta la lista -- ver Memoria.jsx), la única forma de que grupo-0
    // deje de estar es que el dato real (sin el hecho, ya vencido) haya
    // llegado de verdad.
    //
    // El grupo (con el único hecho ahora vencido) desaparece de la lista de
    // grupos activos tras la recarga...
    await waitFor(() => expect(screen.queryByTestId('grupo-0')).not.toBeInTheDocument())
    // ...y el hecho no se borró: sigue viéndose, en la sección Vencidos
    // (SeccionVencidos.jsx, no la ficha de un grupo).
    expect(await screen.findByTestId('vencido-201')).toBeInTheDocument()
  })

  // Ronda de arreglo jax-platform#147, hallazgo MINOR 6 (i18n): el backend
  // (backend/api/admin/memoria.py) rechaza una fecha sin zona con 400
  // vence_at_sin_zona -- esta pantalla NUNCA la manda (Memoria.jsx usa
  // `new Date().toISOString()`, siempre con 'Z'), pero el codigo de error
  // tiene que traducirse igual que cualquier otro, no caer al generico.
  it('un error de zona horaria ausente al caducar se avisa por toast, con el codigo traducido', async () => {
    servirGet(
      { grupos: [{ tema: HECHO_201.texto, hechos: [201], sin_verificar: 0, casi_duplicados: [] }] },
      { hechos: [HECHO_201], total: 1 },
    )
    api.post.mockRejectedValue({ response: { status: 400, data: { detail: 'vence_at_sin_zona' } } })
    renderMemoria()
    const ficha = await screen.findByTestId('hecho-201')
    fireEvent.click(within(ficha).getByRole('button', { name: es.memoria.caducar }))
    const confirmacion = await screen.findByRole('dialog')
    fireEvent.change(within(confirmacion).getByLabelText(/=/), { target: { value: sumaCorrecta(confirmacion) } })
    fireEvent.click(within(confirmacion).getByRole('button', { name: es.memoria.caducarConfirmar }))
    expect(await screen.findByText(es.memoria.errores.vence_at_sin_zona)).toBeInTheDocument()
  })

  it('fundir pide confirmacion en ventana propia y llama SOLO al endpoint de fusion', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    api.post.mockResolvedValue({ data: { superados: 2 } })
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    fireEvent.click(within(grupo).getByRole('button', { name: es.memoria.fundir }))
    const confirmacion = await screen.findByRole('dialog')
    // Ninguno de los tres esta verificado (ver GRUPOS_DOS_TEMAS): el motivo
    // es "el mas reciente", no "el verificado" -- y el mensaje se arma con
    // el texto/verificado que trae el CLUSTER (D5), no con hechosPorId.
    expect(confirmacion).toHaveTextContent(es.memoria.fundirMensaje(HECHO_139.texto, false))
    fireEvent.change(within(confirmacion).getByLabelText(/=/), { target: { value: sumaCorrecta(confirmacion) } })
    fireEvent.click(within(confirmacion).getByRole('button', { name: es.memoria.fundirConfirmar }))
    // grupo.hechos = [139, 138, 136] (creado_at DESC); superviviente_id=139
    // viene del backend (GRUPOS_DOS_TEMAS), esta pantalla no lo recalcula.
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/admin/memoria/hechos/fundir', { superviviente_id: 139, absorbidos: [138, 136] },
    ))
    // Una sola llamada (ronda 2026-09-22): ya no hay un /hechos/aprobar
    // previo -- la ventana entre dos llamadas era justo lo que dejaba
    // "fundir a medias" posible.
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(es.memoria.fundido)).toBeInTheDocument()
    // SOSPECHA: esperar la recarga que dispara `confirmarFundir()`.
    await esperarRecargaCompleta()
  })

  it('fundir muestra cual sobrevive y por que cuando el superviviente esta verificado', async () => {
    // #138 esta verificado (aunque no sea el mas nuevo): el backend lo elige
    // como superviviente (_elegir_superviviente) -- esta pantalla lo
    // muestra, no lo recalcula ni asume "el primero de la lista".
    //
    // D5 (revision adversarial de jax-platform 146, MAYOR 3): el texto del
    // cluster (`superviviente_texto`) es a proposito DISTINTO del texto de
    // HECHO_138 en `hechosPorId` -- si Memoria.jsx armara el mensaje leyendo
    // hechosPorId en vez del cluster, este test veria el texto VIEJO
    // (HECHO_138.texto) y fallaria. Simula el caso real: un cluster puede
    // traer un superviviente que el cap de 500 de GET /hechos dejo afuera.
    const TEXTO_DEL_CLUSTER = 'JAX carece de capacidad nativa para consultas SQL. (via el cluster, no hechosPorId)'
    const HECHO_138_VERIFICADO = { ...HECHO_138, verificado: true, verificado_por: 2, verificado_at: '2026-09-05T00:00:00' }
    servirGet(
      {
        grupos: [{
          tema: HECHO_139.texto, hechos: [139, 138, 136], sin_verificar: 2,
          casi_duplicados: [{
            ids: [136, 138, 139], superviviente_id: 138,
            superviviente_verificado: true, superviviente_texto: TEXTO_DEL_CLUSTER,
          }],
        }],
      },
      { hechos: [HECHO_136, HECHO_138_VERIFICADO, HECHO_139], total: 3 },
    )
    api.post.mockResolvedValue({ data: { superados: 2 } })
    renderMemoria()
    const grupo = await screen.findByTestId('grupo-0')
    // La ficha del superviviente lleva la marca "Sobrevive"; las otras dos, no.
    const ficha138 = within(grupo).getByTestId('hecho-138')
    expect(within(ficha138).getByText(es.memoria.sobrevive)).toBeInTheDocument()
    const ficha139 = within(grupo).getByTestId('hecho-139')
    expect(within(ficha139).queryByText(es.memoria.sobrevive)).not.toBeInTheDocument()

    fireEvent.click(within(grupo).getByRole('button', { name: es.memoria.fundir }))
    const confirmacion = await screen.findByRole('dialog')
    expect(confirmacion).toHaveTextContent(es.memoria.fundirTitulo(138))
    expect(confirmacion).toHaveTextContent(es.memoria.fundirMensaje(TEXTO_DEL_CLUSTER, true))
    fireEvent.change(within(confirmacion).getByLabelText(/=/), { target: { value: sumaCorrecta(confirmacion) } })
    fireEvent.click(within(confirmacion).getByRole('button', { name: es.memoria.fundirConfirmar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/admin/memoria/hechos/fundir', { superviviente_id: 138, absorbidos: [139, 136] },
    ))
    // SOSPECHA: esperar la recarga que dispara `confirmarFundir()`.
    await esperarRecargaCompleta()
  })

  it('la seccion Vencidos no aparece cuando no hay hechos vencidos', async () => {
    servirGet(GRUPOS_DOS_TEMAS, HECHOS_DOS_TEMAS)
    renderMemoria()
    await screen.findByTestId('grupo-0')
    expect(screen.queryByTestId(/^vencido-/)).not.toBeInTheDocument()
  })

  it('la seccion Vencidos lista los hechos vencidos, cerrada por defecto, deja quitarles la caducidad y recarga /grupos', async () => {
    servirGetConVencidos({ grupos: [] }, { hechos: [], total: 0 }, { hechos: [HECHO_VENCIDO], total: 1 })
    api.post.mockResolvedValue({ data: { ok: true } })
    renderMemoria()
    const fila = await screen.findByTestId('vencido-301')
    expect(within(fila).getByText(HECHO_VENCIDO.texto)).toBeInTheDocument()
    const detalle = fila.closest('details')
    expect(detalle).not.toHaveAttribute('open')
    const llamadasAGruposAntes = api.get.mock.calls.filter(([url]) => url === '/admin/memoria/grupos').length
    // Accesibilidad: varios "Quitar caducidad" en la lista se distinguen por
    // el hecho al que corresponden (aria-label), no sólo por el texto visible.
    fireEvent.click(within(fila).getByRole('button', { name: es.memoria.quitarCaducidadDe(301) }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/admin/memoria/hechos/301/caducar', { vence_at: null },
    ))
    expect(await screen.findByText(es.memoria.caducidadQuitada)).toBeInTheDocument()
    // M2: un hecho reactivado puede volver a aparecer en un grupo -- recarga
    // /grupos, igual que aprobar/caducar/corregir/fundir.
    await waitFor(() => {
      const llamadas = api.get.mock.calls.filter(([url]) => url === '/admin/memoria/grupos').length
      expect(llamadas).toBe(llamadasAGruposAntes + 1)
    })
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
