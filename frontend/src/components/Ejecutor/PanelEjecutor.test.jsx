import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../../api/client'
import PanelEjecutor from './PanelEjecutor'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import { useEjecutor, INTERVALO_POLLING_MS } from '../../store/useEjecutor'
import { textoDeErrorEjecutor } from './textos'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

// Panel del modo Ejecutor (SP2, 2026-09-17). Un test por estado visible.
const JAX_INICIAL = useJaxStore.getState()
const INICIAL = useEjecutor.getState()
const tx = es.ejecutor

const MAQ_OK = { nombre: 'ejecutor-prueba', rol: 'desarrollo', con_datos_de_clientes: false, activo: true, elegible: true, motivo_no_elegible: null }
const MAQ_CLIENTES = { nombre: 'atemai', rol: 'produccion', con_datos_de_clientes: true, activo: true, elegible: false, motivo_no_elegible: 'maquina_con_datos_de_clientes' }
const MAQ_INACTIVA = { nombre: 'vieja', rol: 'desarrollo', con_datos_de_clientes: false, activo: false, elegible: false, motivo_no_elegible: 'maquina_inactiva' }
const PAUSA_SUELTA = { puesta: false, origen: null, motivo: null, paso: null, momento: null, legible: true }

// Una línea con espacios repetidos y larga: se tiene que ver literal y entera.
const LINEA = 'Mem:           1.9Gi       180Mi       1.2Gi       1.0Mi       538Mi       1.6Gi   ' + 'x'.repeat(300)

function estado(extra = {}) {
  return { pausa: PAUSA_SUELTA, compuerta_datos_de_clientes: 'cerrada', maquinas: [MAQ_OK, MAQ_CLIENTES, MAQ_INACTIVA], turno_en_curso: null, ...extra }
}

function turno(extra = {}) {
  return {
    n: 1, instruccion: 'ver memoria', estado: 'completado', codigo: null,
    iniciado_at: '2026-09-17T10:00:00Z', terminado_at: '2026-09-17T10:01:00Z',
    rechazo: [], afirmaciones: [], descartadas: [], crudas: [],
    verificacion: { registro_cuadra: true, cadena_ok: true, pausa_puesta: false, auditor_pauso: false },
    ...extra,
  }
}

function mision(estadoMision, turnos, extra = {}) {
  return { id: 'm1', objetivo: 'ver memoria', maquinas: ['ejecutor-prueba'], estado: estadoMision, puede_continuar: false, created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:01:00Z', turnos, ...extra }
}

let respuestas
function servir({ est = estado(), misiones = [], detalle = null, eventos = [] } = {}) {
  respuestas = { est, misiones, detalle, eventos }
  api.get.mockImplementation((url) => {
    const r = respuestas
    if (url === '/ejecutor/estado') return r.est instanceof Error || r.est?.response ? Promise.reject(r.est) : Promise.resolve({ data: r.est })
    if (url === '/ejecutor/misiones') return Promise.resolve({ data: { misiones: r.misiones } })
    if (url === '/ejecutor/misiones/m1') return Promise.resolve({ data: typeof r.detalle === 'function' ? r.detalle() : r.detalle })
    if (url === '/ejecutor/misiones/m1/bitacora') return Promise.resolve({ data: { eventos: r.eventos } })
    return Promise.reject(new Error(`GET inesperado ${url}`))
  })
}

function pintar() {
  return render(<I18nProvider><PanelEjecutor /></I18nProvider>)
}

async function pintarConMision(detalle, eventos = []) {
  servir({ misiones: [{ id: 'm1', objetivo: 'ver memoria', maquinas: ['ejecutor-prueba'], estado: detalle.estado, turnos: detalle.turnos.length, created_at: '2026-09-17T10:00:00Z', updated_at: '2026-09-17T10:00:00Z' }], detalle, eventos })
  const vista = pintar()
  fireEvent.click(await screen.findByRole('button', { name: /ver memoria/ }))
  await screen.findByRole('region', { name: tx.detalleTitulo })
  return vista
}

beforeEach(() => {
  useJaxStore.setState({ ...JAX_INICIAL, token: 'tok', user: { user_id: '1', role: 'superadmin' } }, true)
  useEjecutor.getState().reiniciar()
  useEjecutor.setState(INICIAL, true)
  api.get.mockReset()
  api.post.mockReset()
  localStorage.clear()
})

afterEach(() => {
  useEjecutor.getState().detenerPolling()
  vi.useRealTimers()
})

describe('Ejecutor -- máquinas y compuerta', () => {
  it('muestra TODAS las máquinas: elegibles seleccionables, no elegibles deshabilitadas con su motivo', async () => {
    servir()
    pintar()
    const ok = await screen.findByRole('checkbox', { name: /ejecutor-prueba/ })
    expect(ok).toBeEnabled()
    const clientes = screen.getByRole('checkbox', { name: /atemai/ })
    expect(clientes).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /vieja/ })).toBeDisabled()
    expect(screen.getByText(tx.motivosNoElegible.maquina_con_datos_de_clientes)).toBeInTheDocument()
    expect(screen.getByText(tx.motivosNoElegible.maquina_inactiva)).toBeInTheDocument()
    fireEvent.click(ok)
    expect(useEjecutor.getState().seleccion).toEqual(['ejecutor-prueba'])
  })

  it('la compuerta de datos de clientes cerrada se ve rotulada', async () => {
    servir()
    pintar()
    expect(await screen.findByText(tx.compuertaRotulo)).toBeInTheDocument()
    expect(screen.getByText(tx.compuertaEstados.cerrada)).toBeInTheDocument()
  })

  it('sin máquinas elegibles lo dice', async () => {
    servir({ est: estado({ maquinas: [MAQ_CLIENTES] }) })
    pintar()
    expect(await screen.findByText(tx.sinElegibles)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /atemai/ })).toBeDisabled()
  })

  it('un error de /estado se muestra traducido', async () => {
    servir({ est: { response: { status: 503, data: { detail: 'ejecutor_sin_configurar' } } } })
    pintar()
    expect(await screen.findByText(tx.errores.ejecutor_sin_configurar())).toBeInTheDocument()
  })
})

describe('Ejecutor -- pausa del Ejecutor', () => {
  it('sin pausa: dice que no está puesta y el botón aclara que no es la pausa global', async () => {
    servir()
    api.post.mockResolvedValue({ data: { ...PAUSA_SUELTA, puesta: true, origen: 'plataforma', motivo: 'pausa_manual' } })
    pintar()
    expect(await screen.findByText(tx.pausaNoPuesta)).toBeInTheDocument()
    expect(tx.pausarAclaracion).toMatch(/kill switch/i)
    expect(screen.getByText(tx.pausarAclaracion)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: tx.pausar }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/ejecutor/pausa/poner'))
    expect(await screen.findByText(tx.pausaPuesta)).toBeInTheDocument()
  })

  it('puesta: muestra origen, motivo traducido, paso y momento', async () => {
    servir({ est: estado({ pausa: { puesta: true, origen: 'c5', motivo: 'vigia_caido', paso: 3, momento: '2026-09-17T10:00:00Z', legible: true } }) })
    pintar()
    expect(await screen.findByText(tx.pausaPuesta)).toBeInTheDocument()
    const caja = screen.getByRole('region', { name: tx.pausaTitulo })
    expect(within(caja).getByText(tx.origenes.c5)).toBeInTheDocument()
    expect(within(caja).getByText(tx.motivosPausa.vigia_caido)).toBeInTheDocument()
    expect(within(caja).getByText('3')).toBeInTheDocument()
  })

  it('ilegible (legible:false) se trata como puesta y lo dice', async () => {
    servir({ est: estado({ pausa: { puesta: false, origen: null, motivo: null, paso: null, momento: null, legible: false } }) })
    pintar()
    expect(await screen.findByText(tx.pausaPuesta)).toBeInTheDocument()
    expect(screen.getByText(tx.pausaIlegible)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: tx.pausar })).not.toBeInTheDocument()
  })

  it('quitar la pausa exige la suma y muestra el motivo; con la suma bien, quita', async () => {
    servir({ est: estado({ pausa: { puesta: true, origen: 'plataforma', motivo: 'repuesta_sin_auditoria', paso: 2, momento: null, legible: true } }) })
    api.post.mockResolvedValue({ data: PAUSA_SUELTA })
    pintar()
    fireEvent.click(await screen.findByRole('button', { name: tx.quitarPausa }))
    const dialogo = screen.getByRole('dialog', { name: tx.quitarPausaTitulo })
    expect(dialogo).toHaveTextContent(tx.motivosPausa.repuesta_sin_auditoria)
    const confirmar = within(dialogo).getByRole('button', { name: tx.quitarPausaConfirmar })
    expect(confirmar).toBeDisabled()
    expect(api.post).not.toHaveBeenCalled()
    const etiqueta = dialogo.querySelector('label[for="confirmacion-suma-respuesta"]').textContent
    const [, a, b] = etiqueta.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByLabelText(etiqueta), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/ejecutor/pausa/quitar'))
    expect(await screen.findByText(tx.pausaNoPuesta)).toBeInTheDocument()
  })

  it('un 503 al poner la pausa se muestra traducido', async () => {
    servir()
    api.post.mockRejectedValue({ response: { status: 503, data: { detail: 'ejecutor_pausa_no_escribible' } } })
    pintar()
    fireEvent.click(await screen.findByRole('button', { name: tx.pausar }))
    expect(await screen.findByRole('alert')).toHaveTextContent(tx.errores.ejecutor_pausa_no_escribible())
  })
})

describe('Ejecutor -- detalle de misión', () => {
  it('completada con afirmaciones: dato, máquina, comando y la línea citada literal y completa', async () => {
    const afirmacion = { proposito: 'memoria total', dato: '1.9Gi', maquina: 'ejecutor-prueba', comando: 'ssh -tt -p 58291 axioma@192.168.122.50 free -h', linea: LINEA }
    const { container } = await pintarConMision(mision('completada', [turno({ afirmaciones: [afirmacion], crudas: [{ maquina: 'ejecutor-prueba', comando: 'free -h', salida: 'salida cruda', truncada: false }] })]))
    const lista = screen.getByRole('list', { name: tx.afirmaciones })
    expect(within(lista).getByText('1.9Gi')).toBeInTheDocument()
    expect(within(lista).getByText(afirmacion.comando)).toBeInTheDocument()
    expect(within(lista).getByText(tx.rotuloDato)).toBeInTheDocument()
    expect(within(lista).getByText(tx.rotuloMaquina)).toBeInTheDocument()
    expect(within(lista).getByText(tx.rotuloComando)).toBeInTheDocument()
    expect(within(lista).getByText(tx.rotuloLinea)).toBeInTheDocument()
    const pre = container.querySelector('[data-linea-citada]')
    expect(pre.textContent).toBe(LINEA)
    expect(pre.className).toMatch(/whitespace-pre/)
    expect(pre.className).not.toMatch(/truncate|line-clamp|text-ellipsis/)
    expect(screen.getByText(tx.estadosMision.completada)).toBeInTheDocument()
    expect(screen.getAllByText(tx.estadosTurno.completado).length).toBeGreaterThan(0)
    expect(screen.getByText('salida cruda')).toBeInTheDocument()
  })

  it('completada SIN afirmaciones: las salidas crudas se ven igual, con la marca de truncada', async () => {
    const salida = 'total 0\n  drwx  a\n'
    const { container } = await pintarConMision(mision('completada', [turno({ crudas: [{ maquina: 'ejecutor-prueba', comando: 'ls -la', salida, truncada: true }] })]))
    expect(screen.getByText(tx.sinAfirmaciones)).toBeInTheDocument()
    const cruda = container.querySelector('[data-salida-cruda]')
    expect(cruda.textContent).toBe(salida)
    expect(screen.getByText(tx.truncada)).toBeInTheDocument()
    expect(screen.getByText('ls -la')).toBeInTheDocument()
  })

  it('rechazada: contrato y código traducidos, con los datos visibles', async () => {
    await pintarConMision(mision('rechazada', [turno({ estado: 'rechazado', codigo: 'arranque_rechazado', verificacion: null, rechazo: [{ contrato: 'c5', codigo: 'auditor_no_admite_datos_de_clientes', datos: { host: 'atemai' } }] })]))
    expect(screen.getByText(tx.contratos.c5)).toBeInTheDocument()
    expect(screen.getByText(tx.codigos.auditor_no_admite_datos_de_clientes)).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: tx.rechazo })).getByText('host: atemai')).toBeInTheDocument()
    expect(screen.getByText(tx.estadosMision.rechazada)).toBeInTheDocument()
    expect(screen.getByText(tx.codigosTurno.arranque_rechazado)).toBeInTheDocument()
  })

  it('fallida e interrumpida: el código del turno traducido y la verificación visible', async () => {
    await pintarConMision(mision('interrumpida', [
      turno({ n: 1, estado: 'fallido', codigo: 'cadena_rota', verificacion: { registro_cuadra: true, cadena_ok: false, pausa_puesta: false, auditor_pauso: false } }),
      turno({ n: 2, estado: 'interrumpido', codigo: 'plataforma_reiniciada' }),
    ]))
    expect(screen.getByText(tx.codigosTurno.cadena_rota)).toBeInTheDocument()
    expect(screen.getByText(tx.codigosTurno.plataforma_reiniciada)).toBeInTheDocument()
    expect(screen.getByText(tx.estadosTurno.fallido)).toBeInTheDocument()
    expect(screen.getAllByText(tx.verificacion.cadena_ok).length).toBeGreaterThan(0)
  })

  it('descartadas: estado y código traducidos', async () => {
    await pintarConMision(mision('completada', [turno({ descartadas: [{ estado: 'sin_respaldo', codigo: 'linea_no_esta', datos: {}, proposito: 'p', dato: 'd', maquina: 'ejecutor-prueba', comando: 'c', linea: 'l' }] })]))
    const lista = screen.getByRole('list', { name: tx.descartadas })
    expect(within(lista).getByText(tx.codigos.sin_respaldo)).toBeInTheDocument()
    expect(within(lista).getByText(tx.codigos.linea_no_esta)).toBeInTheDocument()
  })

  it('descartada retenida por el auditor (auditor_ilegible) y verificación con auditor_legible', async () => {
    await pintarConMision(mision('completada', [turno({
      verificacion: { registro_cuadra: true, cadena_ok: true, pausa_puesta: false, auditor_pauso: false, auditor_legible: false },
      descartadas: [{ estado: 'retenida_por_auditor', codigo: 'auditor_ilegible', datos: {}, proposito: 'p', dato: 'd', maquina: 'ejecutor-prueba', comando: 'c', linea: 'l' }],
    })]))
    const lista = screen.getByRole('list', { name: tx.descartadas })
    expect(within(lista).getByText(tx.codigos.retenida_por_auditor)).toBeInTheDocument()
    expect(within(lista).getByText(tx.codigos.auditor_ilegible)).toBeInTheDocument()
    const item = screen.getByText(tx.verificacion.auditor_legible).closest('li')
    expect(item).toHaveTextContent(tx.no)
  })

  it('un código que el diccionario no conoce se muestra crudo', async () => {
    await pintarConMision(mision('fallida', [turno({ estado: 'fallido', codigo: 'codigo_marciano', rechazo: [{ contrato: 'c9', codigo: 'otro_raro', datos: {} }] })]))
    expect(screen.getByText('codigo_marciano')).toBeInTheDocument()
    expect(screen.getByText('c9')).toBeInTheDocument()
    expect(screen.getByText('otro_raro')).toBeInTheDocument()
  })

  it('bitácora: eventos en orden, traducidos, con turno y hora', async () => {
    await pintarConMision(mision('completada', [turno()]), [
      { id: 1, turno: 1, evento: 'mision_creada', datos: {}, at: '2026-09-17T10:00:00Z' },
      { id: 2, turno: 1, evento: 'arranque_verificado', datos: { paso: 1 }, at: '2026-09-17T10:00:05Z' },
      { id: 3, turno: 1, evento: 'evento_nuevo', datos: {}, at: '2026-09-17T10:00:06Z' },
    ])
    const items = within(screen.getByRole('list', { name: tx.bitacora })).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent(tx.eventos.mision_creada)
    expect(items[1]).toHaveTextContent(tx.eventos.arranque_verificado)
    expect(items[1]).toHaveTextContent('paso')
    expect(items[2]).toHaveTextContent('evento_nuevo')
    expect(items[0]).toHaveTextContent(new Date('2026-09-17T10:00:00Z').toLocaleString('es-HN'))
  })

  it('bitácora: codigo, motivo, estado y fallos dentro de datos van traducidos', async () => {
    await pintarConMision(mision('completada', [turno()]), [
      { id: 1, turno: 1, evento: 'turno_interrumpido', datos: { codigo: 'plataforma_reiniciada' }, at: '2026-09-17T10:00:00Z' },
      { id: 2, turno: 1, evento: 'pausa_detectada', datos: { motivo: 'repuesta_sin_auditoria' }, at: '2026-09-17T10:00:01Z' },
      { id: 3, turno: 1, evento: 'afirmacion_descartada', datos: { estado: 'retenida_por_auditor', codigo: 'auditor_ilegible', dato: '1.9Gi' }, at: '2026-09-17T10:00:02Z' },
      { id: 4, turno: 1, evento: 'arranque_rechazado', datos: { fallos: [{ contrato: 'c5', codigo: 'auditor_no_admite_datos_de_clientes' }, { contrato: 'c9', codigo: 'raro' }] }, at: '2026-09-17T10:00:03Z' },
      { id: 5, turno: 1, evento: 'auditor_pauso', datos: { motivo: 'motivo_marciano' }, at: '2026-09-17T10:00:04Z' },
    ])
    const items = within(screen.getByRole('list', { name: tx.bitacora })).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent(tx.codigosTurno.plataforma_reiniciada)
    expect(items[0]).not.toHaveTextContent('plataforma_reiniciada')
    expect(items[1]).toHaveTextContent(tx.motivosPausa.repuesta_sin_auditoria)
    expect(items[2]).toHaveTextContent(tx.codigos.retenida_por_auditor)
    expect(items[2]).toHaveTextContent(tx.codigosTurno.auditor_ilegible)
    expect(items[2]).toHaveTextContent('1.9Gi')
    expect(items[3]).toHaveTextContent(tx.contratos.c5)
    expect(items[3]).toHaveTextContent(tx.codigos.auditor_no_admite_datos_de_clientes)
    expect(items[3]).toHaveTextContent('c9')
    expect(items[3]).toHaveTextContent('raro')
    expect(items[3]).not.toHaveTextContent('[object Object]')
    expect(items[4]).toHaveTextContent('motivo_marciano')
  })

  it('en_curso: el polling corre y se detiene al terminar', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let estadoActual = 'en_curso'
    servir({
      misiones: [{ id: 'm1', objetivo: 'ver memoria', maquinas: [], estado: 'en_curso', turnos: 1 }],
      detalle: () => mision(estadoActual, [turno({ estado: estadoActual === 'en_curso' ? 'en_curso' : 'completado', verificacion: null })]),
    })
    pintar()
    fireEvent.click(await screen.findByRole('button', { name: /ver memoria/ }))
    expect(await screen.findByText(tx.estadosMision.en_curso, { selector: '[data-estado-mision]' })).toBeInTheDocument()
    const detalles = () => api.get.mock.calls.filter(([u]) => u === '/ejecutor/misiones/m1').length
    const n = detalles()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS)
    expect(detalles()).toBe(n + 1)
    estadoActual = 'completada'
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS)
    expect(await screen.findByText(tx.estadosMision.completada, { selector: '[data-estado-mision]' })).toBeInTheDocument()
    const alTerminar = detalles()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS * 4)
    expect(detalles()).toBe(alTerminar)
  })

  it('desmontar el panel corta el polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    servir({ misiones: [{ id: 'm1', objetivo: 'ver memoria', maquinas: [], estado: 'en_curso', turnos: 1 }], detalle: mision('en_curso', [turno({ estado: 'en_curso' })]) })
    const { unmount } = pintar()
    fireEvent.click(await screen.findByRole('button', { name: /ver memoria/ }))
    await screen.findByRole('region', { name: tx.detalleTitulo })
    const detalles = () => api.get.mock.calls.filter(([u]) => u === '/ejecutor/misiones/m1').length
    const antes = detalles()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS)
    expect(detalles()).toBe(antes + 1) // el polling corría: si no, el resto no prueba nada
    unmount()
    const n = api.get.mock.calls.length
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS * 3)
    expect(api.get.mock.calls.length).toBe(n)
  })
})

describe('Ejecutor -- errores de los POST', () => {
  it('409/423 con código simple se muestran traducidos', async () => {
    servir()
    pintar()
    await screen.findByText(tx.pausaNoPuesta)
    useEjecutor.setState({ errorEnvio: { response: { status: 409, data: { detail: 'ejecutor_turno_en_curso' } } } })
    expect(await screen.findByRole('alert')).toHaveTextContent(tx.errores.ejecutor_turno_en_curso())
  })

  it('403 objeto {codigo, maquina, motivo}: texto traducido con la máquina y el motivo, nunca [object Object]', () => {
    for (const dic of [es, en]) {
      const texto = textoDeErrorEjecutor(dic, { response: { status: 403, data: { detail: { codigo: 'ejecutor_maquina_no_elegible', maquina: 'atemai', motivo: 'maquina_con_datos_de_clientes' } } } })
      expect(texto).toContain('atemai')
      expect(texto).toContain(dic.ejecutor.motivosNoElegible.maquina_con_datos_de_clientes)
      expect(texto).not.toContain('[object Object]')
    }
  })

  it('un objeto con código desconocido muestra el código y sus datos, sin [object Object]', () => {
    const texto = textoDeErrorEjecutor(es, { response: { status: 409, data: { detail: { codigo: 'algo_nuevo', maquina: 'x', extra: { a: 1 } } } } })
    expect(texto).toContain('algo_nuevo')
    expect(texto).toContain('x')
    expect(texto).not.toContain('[object Object]')
  })

  it('sin respuesta del servidor da el genérico de red', () => {
    expect(textoDeErrorEjecutor(es, new Error('red'))).toBe(tx.errorDeRed)
  })
})

describe('Ejecutor -- textos', () => {
  const CITA = ['respaldada', 'sin_respaldo', 'fuente_truncada', 'fuente_inexistente', 'dato_fuera_de_linea', 'verificador_caido', 'linea_vacia', 'maquina_vacia', 'dato_vacio', 'proposito_vacio', 'dato_no_entero', 'linea_no_esta', 'comando_no_corrido']
  const AUDITOR = ['fuera_de_mision', 'prohibido', 'solucion_temporal', 'hardcoding', 'cierre_sin_verificacion', 'vigia_caido']
  const ARRANQUE = ['instalado_distinto_del_repo', 'maquina_fuera_del_inventario', 'maquina_sin_contratos_remotos', 'freno_sin_latido', 'interruptor_puesto', 'freno_sin_cuenta', 'freno_no_habilitado', 'cron_abierto_para_la_cuenta', 'linger_activo', 'pausa_del_ejecutor_puesta', 'vigia_ya_activo', 'cuenta_inalcanzable', 'cerco_alcanza_maquina_sin_contratos', 'maquina_inalcanzable', 'exportacion_imposible', 'prueba_ausente', 'prueba_reventada', 'auditor_no_admite_datos_de_clientes']
  const TURNO = ['arranque_rechazado', 'pausa_puesta', 'auditor_pauso', 'vigia_no_latio', 'cerebro_fallo', 'registro_no_cuadra', 'cadena_rota', 'vigia_no_cerro', 'runner_salida_invalida', 'runner_sin_cierre', 'plataforma_reiniciada', 'sin_configurar', 'cerebro_tope_vencido', 'auditor_ilegible', 'turno_ilegible', 'runner_error', 'sin_afirmaciones']
  const EVENTOS = ['mision_creada', 'turno_lanzado', 'arranque_rechazado', 'arranque_verificado', 'vigia_late', 'vigia_no_latio', 'cerebro_termino', 'paso', 'afirmacion_entregada', 'afirmacion_descartada', 'auditor_pauso', 'vigia_cerrado', 'pausa_detectada', 'turno_completado', 'turno_fallido', 'turno_interrumpido', 'turno_rechazado', 'auditor_ilegible', 'runner_salida_invalida']
  const ERRORES = ['ejecutor_objetivo_vacio', 'ejecutor_sin_maquinas', 'ejecutor_maquina_desconocida', 'ejecutor_maquina_no_elegible', 'ejecutor_turno_en_curso', 'ejecutor_pausado', 'ejecutor_sin_configurar', 'ejecutor_mision_inexistente', 'ejecutor_instruccion_vacia', 'ejecutor_mision_sin_sesion', 'ejecutor_pausa_no_escribible', 'ejecutor_pausa_auditoria_fallida']
  const lleno = (v) => typeof v === 'string' && v.trim() !== ''

  it('todo código conocido del contrato tiene texto en es y en', () => {
    for (const [nombre, dic] of [['es', es], ['en', en]]) {
      const e = dic.ejecutor
      for (const c of [...CITA, ...AUDITOR, ...ARRANQUE]) expect(lleno(e.codigos[c]), `${nombre}.codigos.${c}`).toBe(true)
      for (const c of TURNO) expect(lleno(e.codigosTurno[c]), `${nombre}.codigosTurno.${c}`).toBe(true)
      for (const c of EVENTOS) expect(lleno(e.eventos[c]), `${nombre}.eventos.${c}`).toBe(true)
      for (const c of ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'arranque']) {
        expect(lleno(e.contratos[c]), `${nombre}.contratos.${c}`).toBe(true)
        // Con nombre propio, no sólo "Contrato C1".
        expect(e.contratos[c], `${nombre}.contratos.${c}`).not.toMatch(/^(Contrato|Contract) C\d$/)
      }
      for (const c of ['pausa_manual', 'repuesta_sin_auditoria', 'vigia_caido', 'fuera_de_mision', 'prohibido']) expect(lleno(e.motivosPausa[c]), `${nombre}.motivosPausa.${c}`).toBe(true)
      for (const c of ['c5', 'plataforma']) expect(lleno(e.origenes[c]), `${nombre}.origenes.${c}`).toBe(true)
      expect(e.origenes.vigia).toBeUndefined()
      expect(e.origenes.auditor).toBeUndefined()
      expect(lleno(e.codigos.retenida_por_auditor)).toBe(true)
      expect(lleno(e.codigos.auditor_ilegible)).toBe(true)
      expect(lleno(e.verificacion.auditor_legible)).toBe(true)
      for (const c of ['en_curso', 'completada', 'rechazada', 'fallida', 'interrumpida']) expect(lleno(e.estadosMision[c])).toBe(true)
      for (const c of ['en_curso', 'completado', 'rechazado', 'fallido', 'interrumpido']) expect(lleno(e.estadosTurno[c])).toBe(true)
      for (const c of ERRORES) expect(lleno(e.errores[c]({ maquina: 'x', motivo: 'y' })), `${nombre}.errores.${c}`).toBe(true)
    }
  })

  it('en inglés el panel habla inglés', async () => {
    localStorage.setItem('jax_lang', 'en')
    servir()
    pintar()
    expect(await screen.findByText(en.ejecutor.pausaNoPuesta)).toBeInTheDocument()
    expect(screen.getByText(en.ejecutor.motivosNoElegible.maquina_con_datos_de_clientes)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.ejecutor.pausar })).toBeInTheDocument()
    expect(en.ejecutor.pausar).not.toBe(es.ejecutor.pausar)
  })
})
