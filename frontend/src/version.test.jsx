import { readFileSync, readdirSync, statSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import es from './i18n/es.js'
import { leerVersion } from '../leerVersion.js'
import en from './i18n/en.js'

// Versión de Axioma en UN solo lugar (Fernando, 2026-10-02): el archivo VERSION
// de la raíz del repo. El frontend la lee al compilar (vite.config.js ->
// __APP_VERSION__) y el backend al arrancar (backend/main.py). Estos tests
// leen el archivo con node, sin pasar por la ruta que se prueba.
// vitest corre con cwd = frontend/ (CI: working-directory: frontend).
const RAIZ = join(process.cwd(), '..')
const VERSION = readFileSync(join(RAIZ, 'VERSION'), 'utf8').trim()

vi.mock('./i18n/index.jsx', async () => {
  const real = await vi.importActual('./i18n/index.jsx')
  const idioma = { actual: es }
  globalThis.__idiomaVersionTest = idioma
  return { ...real, useI18n: () => ({ t: idioma.actual }) }
})

import LeftPanel from './components/LeftPanel/LeftPanel'
import AdminSidebar from './components/admin/AdminSidebar'
import { useApariencia } from './store/useApariencia'

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
  globalThis.__idiomaVersionTest.actual = es
})

describe('versión de Axioma: una sola fuente, el archivo VERSION', () => {
  it('VERSION tiene un número del tipo X.Y', () => {
    expect(VERSION).toMatch(/^\d+\.\d+(\.\d+)?$/)
  })

  it('__APP_VERSION__ es el contenido de VERSION', () => {
    expect(__APP_VERSION__).toBe(VERSION)
  })

  // Principio IV: ni el número ni el nombre van fijos en los textos. La plantilla
  // de i18n recibe (nombre, versión) y cada idioma decide cómo se dice.
  // Se inyecta una versión que NO es la real: un número escrito a mano en el
  // componente (aunque coincida con VERSION hoy) no aparecería en pantalla.
  it('inicio e Administración muestran la versión inyectada, no un número escrito a mano', () => {
    vi.stubGlobal('__APP_VERSION__', '9.9.9')
    try {
      const { unmount } = render(<LeftPanel />)
      expect(screen.getByText(`${es.brandName} V9.9.9`)).toBeInTheDocument()
      unmount()
      render(<MemoryRouter><AdminSidebar /></MemoryRouter>)
      expect(screen.getByText(`${es.brandName} v9.9.9`)).toBeInTheDocument()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('inicio: plantilla i18n + nombre del sistema + VERSION', () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<LeftPanel />)
    expect(screen.getByText(`Hal V${VERSION}`)).toBeInTheDocument()
  })

  it('inicio sin nombre configurado: usa la marca de i18n (brandName)', () => {
    render(<LeftPanel />)
    expect(screen.getByText(`${es.brandName} V${VERSION}`)).toBeInTheDocument()
  })

  it('Administración: plantilla i18n + nombre del sistema + VERSION', () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<MemoryRouter><AdminSidebar /></MemoryRouter>)
    expect(screen.getByText(`Hal v${VERSION}`)).toBeInTheDocument()
  })

  it('cada idioma tiene su plantilla, que recibe nombre y versión (sin número ni marca fijos)', () => {
    for (const [idioma, dic] of [['es', es], ['en', en]]) {
      expect(dic.platformLabel('X', '9.9'), idioma).toContain('X')
      expect(dic.platformLabel('X', '9.9'), idioma).toContain('9.9')
      expect(dic.adminVersion('X', '9.9'), idioma).toContain('X')
      expect(dic.adminVersion('X', '9.9'), idioma).toContain('9.9')
    }
  })
})

// Guarda: nadie vuelve a escribir la versión ni la marca fija (Principio IV).
// Dos detectores, sobre el código SIN comentarios:
//  - versionesFijas: «Axioma v0.2», «AXIOMA V0.1.0» o un literal 'v0.1.0' suelto,
//    en todo src/ salvo tests;
//  - marcaFija: la palabra «axioma» en los textos de i18n y en los componentes de
//    inicio y Administración. Única excepción: `brandName`, que ES la marca de
//    respaldo de i18n que useNombreDelSistema usa antes de conocer system_name.
export function sinComentarios(texto) {
  return texto.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

export function versionesFijas(texto) {
  const re = /axioma\s*v?\s*\d+\.\d+(\.\d+)?|['"`]v\d+\.\d+\.\d+['"`]/gi
  return sinComentarios(texto).match(re) ?? []
}

export function marcaFija(texto) {
  return sinComentarios(texto)
    .split('\n')
    .filter((l) => !/^\s*brandName:/.test(l))
    .flatMap((l) => l.match(/axioma/gi) ?? [])
}

function archivosFuente(dir) {
  const salida = []
  for (const nombre of readdirSync(dir)) {
    const ruta = join(dir, nombre)
    if (statSync(ruta).isDirectory()) { salida.push(...archivosFuente(ruta)); continue }
    if (!/\.(jsx?|css|html)$/.test(nombre)) continue
    if (/\.test\.jsx?$/.test(nombre)) continue
    salida.push(ruta)
  }
  return salida
}

const SRC = join(process.cwd(), 'src')

describe('guarda: ninguna versión fija en src/', () => {
  it('el detector se dispara con las formas que había (control negativo)', () => {
    expect(versionesFijas("platformLabel: 'AXIOMA V0.2',")).toHaveLength(1)
    expect(versionesFijas('Axioma v0.1.0')).toHaveLength(1)
    expect(versionesFijas("const v = 'v0.3.1'")).toHaveLength(1)
    expect(versionesFijas('AXIOMA V${v}')).toHaveLength(0)
    expect(versionesFijas('// Axioma v0.1.0 era el viejo')).toHaveLength(0)
  })

  it('ningún archivo de src/ (salvo tests) escribe una versión de Axioma', () => {
    const sucios = archivosFuente(SRC)
      .map((f) => [f, versionesFijas(readFileSync(f, 'utf8'))])
      .filter(([, h]) => h.length > 0)
    expect(sucios).toEqual([])
  })
})

describe('vite.config: valida VERSION al leerla', () => {
  it.each(['', '   \n', 'dos punto cinco', '2', '2.5.x', 'v2.5'])('rechaza %j con un mensaje claro', (contenido) => {
    const f = join(mkdtempSync(join(tmpdir(), 'ver-')), 'VERSION')
    writeFileSync(f, contenido)
    expect(() => leerVersion(f)).toThrow(/VERSION/)
  })

  it('acepta X.Y y X.Y.Z, con salto de línea', () => {
    const d = mkdtempSync(join(tmpdir(), 'ver-'))
    writeFileSync(join(d, 'a'), '2.5\n'); writeFileSync(join(d, 'b'), '3.0.1')
    expect(leerVersion(join(d, 'a'))).toBe('2.5')
    expect(leerVersion(join(d, 'b'))).toBe('3.0.1')
  })

  it('un archivo inexistente falla con mensaje claro', () => {
    expect(() => leerVersion('/no/existe/VERSION')).toThrow(/VERSION/)
  })
})

describe('guarda: package.json no lleva versión (cuarto lugar)', () => {
  it('ni package.json ni package-lock.json declaran "version" del paquete', () => {
    for (const f of ['package.json', 'package-lock.json']) {
      const j = JSON.parse(readFileSync(join(process.cwd(), f), 'utf8'))
      expect(j.version, f).toBeUndefined()
      if (j.packages?.['']) expect(j.packages[''].version, f).toBeUndefined()
    }
  })
})

describe('guarda: ninguna marca fija en i18n, inicio y Administración', () => {
  it('el detector se dispara con una marca fija y respeta brandName (control negativo)', () => {
    expect(marcaFija("platformLabel: (v) => `AXIOMA V${v}`")).toHaveLength(1)
    expect(marcaFija("adminVersion: () => 'Axioma v'")).toHaveLength(1)
    expect(marcaFija("  brandName: 'Axioma',")).toHaveLength(0)
    expect(marcaFija('// el nombre Axioma sale de system_name')).toHaveLength(0)
  })

  it('los textos de i18n y los componentes de inicio y Administración no llevan «Axioma»', () => {
    const archivos = [
      ...readdirSync(join(SRC, 'i18n')).filter((n) => /^[a-z]+\.js$/.test(n)).map((n) => join(SRC, 'i18n', n)),
      join(SRC, 'components/LeftPanel/LeftPanel.jsx'),
      join(SRC, 'components/admin/AdminSidebar.jsx'),
    ]
    expect(archivos.length).toBeGreaterThanOrEqual(4)
    const sucios = archivos
      .map((f) => [f, marcaFija(readFileSync(f, 'utf8'))])
      .filter(([, h]) => h.length > 0)
    expect(sucios).toEqual([])
  })
})
