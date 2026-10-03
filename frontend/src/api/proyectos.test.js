import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import causasDeReferencia from './causas_de_error.json'

// Cliente de /api/proyectos (E1, T6). `api` se simula como en client.test.js:
// la prueba mira método, ruta, cuerpo, query y cabeceras, no la red.
const api = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}
vi.mock('./client', () => ({ default: api }))

import es from '../i18n/es.js'
import en from '../i18n/en.js'

let c

beforeEach(async () => {
  for (const f of Object.values(api)) f.mockReset().mockResolvedValue({ data: 'DATA' })
  c = await import('./proyectos.js')
})

describe('cliente de proyectos', () => {
  it('listarProyectos manda vista, antes_de y limite como query y devuelve data', async () => {
    api.get.mockResolvedValue({ data: { proyectos: [], siguiente: null } })
    const r = await c.listarProyectos({ vista: 'archivados', antesDe: 9, limite: 20 })
    expect(api.get).toHaveBeenCalledWith('/proyectos', {
      params: { vista: 'archivados', antes_de: 9, limite: 20 },
    })
    expect(r).toEqual({ proyectos: [], siguiente: null })
  })

  it('listarProyectos usa vista=activos, antes_de=null y limite=50 por defecto', async () => {
    await c.listarProyectos({})
    expect(api.get).toHaveBeenCalledWith('/proyectos', {
      params: { vista: 'activos', antes_de: null, limite: 50 },
    })
  })

  it('crearProyecto hace POST con la cabecera Idempotency-Key', async () => {
    const r = await c.crearProyecto({ nombre: 'A', descripcion: null }, 'k-1')
    expect(api.post).toHaveBeenCalledWith(
      '/proyectos',
      { nombre: 'A', descripcion: null },
      { headers: { 'Idempotency-Key': 'k-1' } },
    )
    expect(r).toBe('DATA')
  })

  it('verProyecto: GET /proyectos/{id}', async () => {
    expect(await c.verProyecto(3)).toBe('DATA')
    expect(api.get).toHaveBeenCalledWith('/proyectos/3')
  })

  it('renombrarProyecto: PUT con las dos claves, descripcion puede ser null', async () => {
    await c.renombrarProyecto(3, { nombre: 'N', descripcion: null })
    expect(api.put).toHaveBeenCalledWith('/proyectos/3', { nombre: 'N', descripcion: null })
  })

  it('renombrarProyecto manda descripcion null aunque no se pase', async () => {
    await c.renombrarProyecto(3, { nombre: 'N' })
    expect(api.put).toHaveBeenCalledWith('/proyectos/3', { nombre: 'N', descripcion: null })
  })

  it('cambiarEstado: POST /proyectos/{id}/estado', async () => {
    await c.cambiarEstado(3, 'ARCHIVED')
    expect(api.post).toHaveBeenCalledWith('/proyectos/3/estado', { estado: 'ARCHIVED' })
  })

  it('listarMiembros: GET', async () => {
    await c.listarMiembros(3)
    expect(api.get).toHaveBeenCalledWith('/proyectos/3/miembros')
  })

  it('invitarMiembro: POST con email y papel', async () => {
    await c.invitarMiembro(3, { email: 'a@b.co', papel: 'VIEWER' })
    expect(api.post).toHaveBeenCalledWith('/proyectos/3/miembros', { email: 'a@b.co', papel: 'VIEWER' })
  })

  it('cambiarPapel: PUT /miembros/{user_id} con {papel}', async () => {
    await c.cambiarPapel(3, 7, 'OWNER')
    expect(api.put).toHaveBeenCalledWith('/proyectos/3/miembros/7', { papel: 'OWNER' })
  })

  it('quitarMiembro: DELETE', async () => {
    await c.quitarMiembro(3, 7)
    expect(api.delete).toHaveBeenCalledWith('/proyectos/3/miembros/7')
  })

  it('buscarCandidatos: GET con q', async () => {
    await c.buscarCandidatos(3, 'ana')
    expect(api.get).toHaveBeenCalledWith('/proyectos/3/candidatos', { params: { q: 'ana' } })
  })
})

const CODIGOS = [
  'proyecto_no_encontrado', 'miembro_no_encontrado', 'papel_insuficiente', 'estado_no_permite',
  'ultimo_dueno', 'admin_protegido', 'ya_es_miembro', 'usuario_no_elegible', 'idempotencia_conflicto',
  'idempotencia_invalida', 'reintentar', 'datos_invalidos', 'proyecto_id_reservado', 'proyectos_error',
  'tenant_scope_required', 'generico',
]

function forma(obj, prefijo = '') {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === 'object' ? forma(v, `${prefijo}${k}.`) : [`${prefijo}${k}:${typeof v}`])
}

describe('i18n proyectos', () => {
  it('es y en tienen las mismas claves en profundidad', () => {
    expect(forma(en.proyectos).sort()).toEqual(forma(es.proyectos).sort())
  })

  it('cada valor es string o función', () => {
    for (const d of [es, en]) {
      for (const f of forma(d.proyectos)) expect(['string', 'function']).toContain(f.split(':')[1])
    }
  })

  it('tiene todos los códigos de error de la API y generico', () => {
    for (const d of [es, en]) {
      for (const k of CODIGOS) expect(typeof d.proyectos.errores[k], k).toBe('string')
    }
  })

  it('vistas, papeles (cuatro, con REVIEWER) y estados tienen sus claves', () => {
    for (const d of [es, en]) {
      expect(Object.keys(d.proyectos.vistas).sort()).toEqual(['activos', 'archivados', 'ocultos'])
      expect(Object.keys(d.proyectos.papeles).sort()).toEqual(['CONTRIBUTOR', 'OWNER', 'REVIEWER', 'VIEWER'])
      expect(Object.keys(d.proyectos.estados).sort()).toEqual(['ACTIVE', 'ARCHIVED', 'HIDDEN'])
    }
  })
})

// ---- Documentos del proyecto (E2a, T8) ----

describe('cliente de documentos', () => {
  it('limitesDeDocumentos: GET /proyectos/documentos/limites', async () => {
    expect(await c.limitesDeDocumentos()).toBe('DATA')
    expect(api.get).toHaveBeenCalledWith('/proyectos/documentos/limites')
  })

  it('subirDocumentos: POST con FormData, campo archivos repetido y nombre de carpeta', async () => {
    const a = new File(['a'], 'a.pdf')
    const b = new File(['b'], 'b.txt')
    Object.defineProperty(b, 'webkitRelativePath', { value: 'carpeta/sub/b.txt' })
    const r = await c.subirDocumentos(7, [a, b])
    expect(r).toBe('DATA')
    const [url, cuerpo, opts] = api.post.mock.calls[0]
    expect(url).toBe('/proyectos/7/documentos')
    expect(cuerpo).toBeInstanceOf(FormData)
    const partes = cuerpo.getAll('archivos')
    expect(partes).toHaveLength(2)
    expect(partes.map((p) => p.name)).toEqual(['a.pdf', 'carpeta/sub/b.txt'])
    // Sin Content-Type a mano: el navegador pone el boundary.
    expect(opts.headers).toBeUndefined()
  })

  it('subirDocumentos: onProgreso recibe la fracción 0..1', async () => {
    const vistos = []
    api.post.mockImplementation(async (_u, _c, opts) => {
      opts.onUploadProgress({ loaded: 50, total: 200 })
      opts.onUploadProgress({ loaded: 200, total: 200 })
      opts.onUploadProgress({ loaded: 10 }) // sin total: no se inventa una fracción
      return { data: 'DATA' }
    })
    await c.subirDocumentos(7, [new File(['a'], 'a.pdf')], { onProgreso: (f) => vistos.push(f) })
    expect(vistos).toEqual([0.25, 1])
  })

  it('subirDocumentos sin onProgreso no revienta al llegar progreso', async () => {
    api.post.mockImplementation(async (_u, _c, opts) => {
      opts.onUploadProgress({ loaded: 1, total: 2 })
      return { data: 'DATA' }
    })
    await expect(c.subirDocumentos(7, [new File(['a'], 'a.pdf')])).resolves.toBe('DATA')
  })

  it('listarDocumentos: query con vista, antes_de y limite, y por defecto visibles/null/50', async () => {
    await c.listarDocumentos(4, { vista: 'ocultos', antesDe: 9, limite: 20 })
    expect(api.get).toHaveBeenCalledWith('/proyectos/4/documentos', {
      params: { vista: 'ocultos', antes_de: 9, limite: 20 },
    })
    await c.listarDocumentos(4)
    expect(api.get).toHaveBeenLastCalledWith('/proyectos/4/documentos', {
      params: { vista: 'visibles', antes_de: null, limite: 50 },
    })
  })

  it('ocultarDocumento y restaurarDocumento: POST a su ruta', async () => {
    await c.ocultarDocumento(4, 11)
    expect(api.post).toHaveBeenLastCalledWith('/proyectos/4/documentos/11/ocultar')
    await c.restaurarDocumento(4, 11)
    expect(api.post).toHaveBeenLastCalledWith('/proyectos/4/documentos/11/restaurar')
  })

  it('reprocesarDocumento: POST a su ruta', async () => {
    await c.reprocesarDocumento(4, 11)
    expect(api.post).toHaveBeenLastCalledWith('/proyectos/4/documentos/11/reprocesar')
  })
})

// Los códigos, estados y motivos SALEN DEL BACKEND: se leen del .py para que un código
// nuevo sin texto rompa esta prueba (un código sin texto se vería como el genérico).
const RAIZ_BACKEND = resolve(dirname(fileURLToPath(import.meta.url)), '../../../backend')
const leer = (ruta) => readFileSync(resolve(RAIZ_BACKEND, ruta), 'utf8')
const unicos = (xs) => [...new Set(xs)].sort()
const comillas = (texto) => [...texto.matchAll(/["'](\w+)["']/g)].map((m) => m[1])

// Argumentos de cada llamada `nombre(...)` del .py, con el paréntesis balanceado (sirve para
// ternarios y llamadas partidas en dos líneas). Se salta la definición (`def nombre(`).
function llamadas(texto, nombre) {
  const sitios = []
  for (const m of texto.matchAll(new RegExp(`(?<!def )\\b${nombre}\\(`, 'g'))) {
    let nivel = 1
    let k = m.index + m[0].length
    for (; k < texto.length && nivel > 0; k++) nivel += texto[k] === '(' ? 1 : texto[k] === ')' ? -1 : 0
    sitios.push(texto.slice(m.index + m[0].length, k - 1))
  }
  return sitios
}
const ACTUALIZAR = 'actualizar es/en y esta prueba'

function codigosDelBackend() {
  const docs = leer('api/proyectos_documentos.py')
  const tabla = new Map([...leer('api/proyectos.py').matchAll(/\((\w+), \d+, "(\w+)"\)/g)].map((m) => [m[1], m[2]]))
  const codigos = []
  // Control de cuenta: cada llamada a `_error(` y a `_http(` tiene que dar al menos un código
  // visible; si no (una constante, otra forma), falla en voz alta en vez de quedar sin texto.
  const sitiosError = llamadas(docs, '_error')
  expect(sitiosError.length, `sin llamadas a _error: el extractor ya no ve nada, ${ACTUALIZAR}`).toBeGreaterThan(0)
  for (const a of sitiosError) {
    const expr = a.match(/^\s*\d+,\s*([\s\S]*?)(?:,\s*\w+=|$)/)?.[1] ?? ''
    const vistos = comillas(expr)
    expect(vistos.length, `_error(${a.slice(0, 60)}...) no tiene un código literal: ${ACTUALIZAR}`).toBeGreaterThan(0)
    codigos.push(...vistos)
  }
  const sitiosHttp = llamadas(docs, '_http')
  expect(sitiosHttp.length, `sin llamadas a _http: ${ACTUALIZAR}`).toBeGreaterThan(0)
  for (const a of sitiosHttp) {
    const clases = [...a.matchAll(/\b([A-Z]\w+)\(/g)].map((m) => m[1])
    expect(clases.length, `_http(${a.slice(0, 60)}...) sin clase de error visible: ${ACTUALIZAR}`).toBeGreaterThan(0)
    for (const c of clases) {
      expect(tabla.has(c), `${c} no está en _HTTP_DE_ERROR: ${ACTUALIZAR}`).toBe(true)
      codigos.push(tabla.get(c))
    }
  }
  const freno = leer('kill_switch.py').match(/^KILL_SWITCH_ACTIVO = "(\w+)"/m)[1]
  return unicos([...codigos, freno])
}

function estadosDelBackend() {
  const repo = leer('proyectos_documentos/repositorio.py')
  const resultado = repo.match(/ESTADOS_DE_RESULTADO = frozenset\(\{([^}]*)\}\)/)[1]
  const abiertos = repo.match(/_ESTADOS_ABIERTOS = \(([^)]*)\)/)[1]
  return unicos([...comillas(resultado), ...comillas(abiertos), 'en_cola'])
}

// Códigos estables de `project_documents.error`: la referencia es `causas_de_error.json`, que una prueba
// del backend (test_proyectos_documentos_despachador.py) exige igual a `sorted(CAUSAS_DE_ERROR)`. Aquí ya no
// se lee el .py con una regex: no depende de cómo esté escrito.
const causasDelBackend = () => causasDeReferencia

function motivosDelBackend() {
  const docs = leer('api/proyectos_documentos.py')
  return unicos([...docs.matchAll(/"motivo":\s*([^\n]*?)\}\)/g)].flatMap((m) => comillas(m[1])))
}

describe('i18n proyectos.documentos', () => {
  const secciones = [['es', es], ['en', en]]

  it('es y en tienen las mismas claves en profundidad', () => {
    expect(forma(en.proyectos.documentos).sort()).toEqual(forma(es.proyectos.documentos).sort())
  })

  it('el extractor de códigos del backend encuentra lo esperado (la prueba no es vacía)', () => {
    const codigos = codigosDelBackend()
    for (const k of ['lote_demasiado_grande', 'sin_espacio', 'proyecto_no_activo', 'kill_switch_activo',
      'papel_insuficiente', 'proyecto_no_encontrado', 'almacen_error_escritura', 'insercion_incierta'])
      expect(codigos).toContain(k)
    expect(estadosDelBackend()).toHaveLength(8)
    expect(motivosDelBackend()).toEqual(
      ['demasiado_grande', 'duplicado', 'duplicado_oculto', 'nombre_invalido', 'tipo_no_admitido'])
  })

  it('el extractor ve tantas llamadas como hay en el .py (un código invisible falla en voz alta)', () => {
    const docs = leer('api/proyectos_documentos.py')
    const cuenta = (n) => (docs.match(new RegExp(`(?<!def )\\b${n}\\(`, 'g')) || []).length
    expect(llamadas(docs, '_error')).toHaveLength(cuenta('_error'))
    expect(llamadas(docs, '_http')).toHaveLength(cuenta('_http'))
    expect(cuenta('_error')).toBeGreaterThan(8)
    expect(cuenta('_http')).toBe(2)
  })

  it.each(secciones)('%s: todo código de error del backend tiene texto', (_n, d) => {
    for (const k of codigosDelBackend()) expect(typeof d.proyectos.documentos.errores[k], k).toBe('string')
  })

  it.each(secciones)('%s: todo estado del backend tiene texto', (_n, d) => {
    for (const k of estadosDelBackend()) expect(typeof d.proyectos.documentos.estados[k], k).toBe('string')
  })

  it('la referencia de causas no está vacía, está ordenada y no repite códigos', () => {
    expect(causasDeReferencia.length).toBeGreaterThan(10)
    expect(causasDeReferencia).toEqual([...new Set(causasDeReferencia)].sort())
    for (const k of ['procesamiento_fallido', 'ocr_sin_texto', 'formato_no_soportado', 'formato_gif_animado'])
      expect(causasDeReferencia).toContain(k)
  })

  it.each(secciones)('%s: toda causa de error del backend tiene texto, y hay un genérico', (_n, d) => {
    for (const k of [...causasDelBackend(), 'desconocida']) expect(typeof d.proyectos.documentos.causas[k], k).toBe('string')
  })

  it.each(secciones)('%s: todo motivo de ignorado del backend tiene texto', (_n, d) => {
    for (const k of motivosDelBackend()) expect(typeof d.proyectos.documentos.motivos[k], k).toBe('string')
  })

  it.each(secciones)('%s: las demás claves del brief existen', (_n, d) => {
    const x = d.proyectos.documentos
    for (const k of ['pestana', 'agregar', 'vacio', 'ocultar', 'restaurar', 'verOcultos', 'subiendo', 'sinPermiso', 'boton'])
      expect(typeof x[k], k).toBe('string')
    for (const k of ['titulo', 'archivos', 'peso', 'ignorados', 'confirmar', 'cancelar'])
      expect(['string', 'function'], k).toContain(typeof x.resumen[k])
    for (const k of ['titulo', 'texto', 'crear']) expect(typeof x.elegirProyecto[k], k).toBe('string')
  })
})
