import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import es from './i18n/es.js'
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

vi.mock('./api/client', () => ({ default: { get: vi.fn() } }))

import api from './api/client'
import { reiniciarVersion } from './store/useVersion'
import { useJaxStore } from './store/useJaxStore'
import LeftPanel from './components/LeftPanel/LeftPanel'
import AdminSidebar from './components/admin/AdminSidebar'
import { useApariencia } from './store/useApariencia'

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
  globalThis.__idiomaVersionTest.actual = es
  reiniciarVersion()
  useJaxStore.setState({ token: 'tok' })
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { version: '9.9.9' } })
})

// La versión se pide en tiempo de ejecución a GET /api/version (el backend la
// lee de VERSION al arrancar): cambiar VERSION no recompila el frontend. Las
// pruebas simulan la respuesta con 9.9.9, que NO es la real: un número escrito
// a mano en un componente no aparecería en pantalla.
describe('versión de Axioma: una sola fuente, el archivo VERSION', () => {
  it('VERSION tiene un número del tipo X.Y', () => {
    expect(VERSION).toMatch(/^\d+\.\d+(\.\d+)?$/)
  })

  it('inicio muestra la versión que responde /api/version', async () => {
    render(<LeftPanel />)
    expect(await screen.findByText(`${es.brandName} V9.9.9`)).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/version')
  })

  it('Administración muestra la versión que responde /api/version', async () => {
    render(<MemoryRouter><AdminSidebar /></MemoryRouter>)
    expect(await screen.findByText(`${es.brandName} v9.9.9`)).toBeInTheDocument()
  })

  it('con nombre configurado: plantilla i18n + nombre + versión', async () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<LeftPanel />)
    expect(await screen.findByText('Hal V9.9.9')).toBeInTheDocument()
  })

  it('sin sesión no pide la versión; al haber token, la pide', async () => {
    useJaxStore.setState({ token: null })
    render(<LeftPanel />)
    expect(api.get).not.toHaveBeenCalled()
    expect(screen.getByText(es.brandName)).toBeInTheDocument()
    act(() => useJaxStore.setState({ token: 'tok' }))
    expect(await screen.findByText(`${es.brandName} V9.9.9`)).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('mientras carga, solo el nombre, sin número', () => {
    api.get.mockReturnValue(new Promise(() => {}))
    render(<LeftPanel />)
    expect(screen.getByText(es.brandName)).toBeInTheDocument()
    expect(screen.queryByText(/\d+\.\d+/)).not.toBeInTheDocument()
  })

  it('si falla, solo el nombre, sin número de respaldo', async () => {
    api.get.mockRejectedValue(new Error('red'))
    render(<MemoryRouter><AdminSidebar /></MemoryRouter>)
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.getByText(es.brandName)).toBeInTheDocument()
    expect(screen.queryByText(/\d+\.\d+/)).not.toBeInTheDocument()
  })

  it('se pide una sola vez aunque se monten varios componentes', async () => {
    render(<LeftPanel />)
    render(<MemoryRouter><AdminSidebar /></MemoryRouter>)
    await screen.findAllByText(/9\.9\.9/)
    expect(api.get).toHaveBeenCalledTimes(1)
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

describe('guarda: package.json no lleva versión (cuarto lugar)', () => {
  it('ni package.json ni package-lock.json declaran "version" del paquete', () => {
    for (const f of ['package.json', 'package-lock.json']) {
      const j = JSON.parse(readFileSync(join(process.cwd(), f), 'utf8'))
      expect(j.version, f).toBeUndefined()
      if (j.packages?.['']) expect(j.packages[''].version, f).toBeUndefined()
    }
  })
})

describe('guarda: la versión no se compila dentro del frontend', () => {
  it('ningún archivo de src/ ni vite.config.js usan __APP_VERSION__', () => {
    const archivos = [...archivosFuente(SRC), join(process.cwd(), 'vite.config.js')]
    const sucios = archivos.filter((f) => readFileSync(f, 'utf8').includes('__APP_VERSION__'))
    expect(sucios).toEqual([])
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
