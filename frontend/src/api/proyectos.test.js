import { describe, it, expect, vi, beforeEach } from 'vitest'

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

  it('vistas, papeles y estados tienen sus tres claves', () => {
    for (const d of [es, en]) {
      expect(Object.keys(d.proyectos.vistas).sort()).toEqual(['activos', 'archivados', 'ocultos'])
      expect(Object.keys(d.proyectos.papeles).sort()).toEqual(['CONTRIBUTOR', 'OWNER', 'VIEWER'])
      expect(Object.keys(d.proyectos.estados).sort()).toEqual(['ACTIVE', 'ARCHIVED', 'HIDDEN'])
    }
  })
})
