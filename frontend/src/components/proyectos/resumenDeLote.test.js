import { describe, it, expect } from 'vitest'
import { resumirLote, formatoPeso } from './resumenDeLote'

const MB = 1024 * 1024
const LIMITES = {
  max_bytes_archivo: 100 * MB,
  max_archivos_lote: 250,
  max_bytes_lote: 1024 * MB,
  extensiones: ['pdf', 'xlsx', 'png'],
}
const f = (name, size = 10, webkitRelativePath = '') => ({ name, size, webkitRelativePath })

describe('resumirLote', () => {
  it('acepta lo admitido, suma el peso y cuenta por tipo', () => {
    const r = resumirLote([f('a.pdf', 100), f('b.PDF', 50), f('c.xlsx', 25)], LIMITES)
    expect(r.aceptados).toHaveLength(3)
    expect(r.ignorados).toEqual([])
    expect(r.totalBytes).toBe(175)
    expect(r.porTipo).toEqual({ pdf: 2, xlsx: 1 })
    expect(r.excedeLote).toBeNull()
  })

  it('ignora un tipo no admitido o sin extensión, con su motivo', () => {
    const r = resumirLote([f('a.exe'), f('LEEME'), f('.pdf'), f('ok.pdf')], LIMITES)
    expect(r.aceptados.map((x) => x.name)).toEqual(['ok.pdf'])
    expect(r.ignorados).toEqual([
      { nombre: 'a.exe', motivo: 'tipo_no_admitido' },
      { nombre: 'LEEME', motivo: 'tipo_no_admitido' },
      { nombre: '.pdf', motivo: 'tipo_no_admitido' },
    ])
  })

  it('ignora lo que supera el tope por archivo; el justo en el tope entra', () => {
    const r = resumirLote([f('justo.pdf', 100 * MB), f('grande.pdf', 100 * MB + 1)], LIMITES)
    expect(r.aceptados.map((x) => x.name)).toEqual(['justo.pdf'])
    expect(r.ignorados).toEqual([{ nombre: 'grande.pdf', motivo: 'demasiado_grande' }])
  })

  it('ignora el duplicado por nombre y tamaño dentro del lote; mismo nombre y otro tamaño no lo es', () => {
    const r = resumirLote([f('a.pdf', 10), f('a.pdf', 10), f('a.pdf', 11)], LIMITES)
    expect(r.aceptados).toHaveLength(2)
    expect(r.ignorados).toEqual([{ nombre: 'a.pdf', motivo: 'duplicado' }])
  })

  it('en una carpeta el nombre es la ruta relativa: el mismo nombre en otra subcarpeta no es duplicado', () => {
    const r = resumirLote([f('a.pdf', 10, 'x/a.pdf'), f('a.pdf', 10, 'y/a.pdf')], LIMITES)
    expect(r.aceptados).toHaveLength(2)
    expect(r.ignorados).toEqual([])
  })

  it('251 archivos aceptables exceden el lote por cantidad', () => {
    const muchos = Array.from({ length: 251 }, (_, i) => f(`d${i}.pdf`, 1))
    expect(resumirLote(muchos, LIMITES).excedeLote).toBe('archivos')
    expect(resumirLote(muchos.slice(0, 250), LIMITES).excedeLote).toBeNull()
  })

  it('1 GB + 1 byte exceden el lote por peso; 1 GB justo no', () => {
    const base = Array.from({ length: 10 }, (_, i) => f(`g${i}.pdf`, 100 * MB))
    const justo = [...base, f('resto.pdf', 24 * MB)]
    expect(resumirLote(justo, LIMITES).excedeLote).toBeNull()
    expect(resumirLote([...base, f('resto.pdf', 24 * MB + 1)], LIMITES).excedeLote).toBe('bytes')
  })

  it('lo ignorado no cuenta para los topes del lote', () => {
    const r = resumirLote([f('a.pdf', 1), ...Array.from({ length: 300 }, (_, i) => f(`x${i}.exe`))], LIMITES)
    expect(r.excedeLote).toBeNull()
  })

  it('un tope ausente no deja pasar todo: sin max_bytes_archivo nada se admite', () => {
    const { max_bytes_archivo, ...sinTopeArchivo } = LIMITES // eslint-disable-line no-unused-vars
    const r = resumirLote([f('a.pdf', 10)], sinTopeArchivo)
    expect(r.aceptados).toEqual([])
    expect(r.ignorados).toHaveLength(1)
    const { max_bytes_lote, ...sinTopeLote } = LIMITES // eslint-disable-line no-unused-vars
    expect(resumirLote([f('a.pdf', 10)], sinTopeLote).aceptados).toEqual([])
  })

  it('sin límites cargados no inventa topes: todo se ignora por tipo', () => {
    const r = resumirLote([f('a.pdf')], null)
    expect(r.aceptados).toEqual([])
    expect(r.ignorados).toEqual([{ nombre: 'a.pdf', motivo: 'tipo_no_admitido' }])
  })
})

describe('formatoPeso', () => {
  it('elige la unidad y no deja ceros de más', () => {
    expect(formatoPeso(0, 'en')).toBe('0 byte')
    expect(formatoPeso(512, 'en')).toBe('512 byte')
    expect(formatoPeso(1536, 'en')).toBe('1.5 kB')
    expect(formatoPeso(5 * MB, 'en')).toBe('5 MB')
    expect(formatoPeso(1024 * MB, 'en')).toBe('1 GB')
  })
})
