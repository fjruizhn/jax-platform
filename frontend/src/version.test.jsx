import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
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

  it('la pantalla de inicio la muestra, en es y en', () => {
    render(<LeftPanel />)
    expect(screen.getByText(`AXIOMA V${VERSION}`)).toBeInTheDocument()
    globalThis.__idiomaVersionTest.actual = en
    expect(en.platformLabel(VERSION)).toBe(`AXIOMA V${VERSION}`)
  })

  it('la Administración la muestra', () => {
    render(<MemoryRouter><AdminSidebar /></MemoryRouter>)
    expect(screen.getByText(`Axioma v${VERSION}`)).toBeInTheDocument()
  })
})

// Guarda: nadie vuelve a escribir la versión fija en src/. Mira «AXIOMA V0.2»,
// «Axioma v0.1.0» y similares (la palabra Axioma seguida de un número de versión)
// y también un literal v0.1.0 suelto. Los tests y este archivo quedan fuera.
export function versionesFijas(texto) {
  const hallazgos = []
  const re = /axioma\s*v?\s*\d+\.\d+(\.\d+)?|['"`]v\d+\.\d+\.\d+['"`]/gi
  let m
  while ((m = re.exec(texto)) !== null) hallazgos.push(m[0])
  return hallazgos
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

describe('guarda: ninguna versión fija en src/', () => {
  it('el detector se dispara con las formas que había (control negativo)', () => {
    expect(versionesFijas("platformLabel: 'AXIOMA V0.2',")).toHaveLength(1)
    expect(versionesFijas('Axioma v0.1.0')).toHaveLength(1)
    expect(versionesFijas("const v = 'v0.3.1'")).toHaveLength(1)
    expect(versionesFijas('AXIOMA V${v}')).toHaveLength(0)
  })

  it('ningún archivo de src/ (salvo tests) escribe una versión de Axioma', () => {
    const dir = join(process.cwd(), 'src')
    const sucios = archivosFuente(dir)
      .map((f) => [f, versionesFijas(readFileSync(f, 'utf8'))])
      .filter(([, h]) => h.length > 0)
    expect(sucios).toEqual([])
  })
})
