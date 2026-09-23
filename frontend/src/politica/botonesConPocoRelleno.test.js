// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { hallazgosEnFuente } from './botonesConPocoRelleno.js'

// WCAG 2.2 §2.5.8 (Target Size · Minimum, 24×24px): botones cuyo tamaño de
// texto + relleno vertical suma menos de 24px de alto, calculado tokenizando
// el className -- no una búsqueda de subcadena sensible al orden. Ver el
// comentario de cabecera de botonesConPocoRelleno.js para el alcance exacto.
describe('detector de botones con poco relleno vertical (< 24px calculado)', () => {
  it('marca ACCION_NEUTRA de AdminUsers.jsx real (20px: text-xs=16 + py-0.5=4)', () => {
    // Fixture EXACTA (git show 0865e87, antes del arreglo de esta familia).
    const codigo = [
      "const ACCION_NEUTRA = 'text-xs px-2 py-0.5 rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors';",
      ';<button className={ACCION_NEUTRA}>Editar</button>',
    ].join('\n')
    const hallazgos = hallazgosEnFuente(codigo, 'AdminUsers.jsx')
    expect(hallazgos).toHaveLength(1)
    expect(hallazgos[0]).toContain('20px')
  })

  it('marca el botón de PanelEjecutor.jsx real', () => {
    const codigo = '<button type="button" onClick={nuevaMision} className="px-2 py-0.5 rounded bg-superficie-2 text-texto hover:text-texto-fuerte text-xs font-semibold">{tx.nuevaMision}</button>'
    expect(hallazgosEnFuente(codigo, 'PanelEjecutor.jsx')).toHaveLength(1)
  })

  it('marca el chip de faceta de BottomBar.jsx real (className con interpolación de estado)', () => {
    const codigo = [
      '<button',
      '  key={f.id}',
      '  onClick={() => setActiveFacet(f.id)}',
      '  className={`px-2 py-0.5 rounded border bg-superficie text-xs font-semibold transition-colors ${',
      "    activeFacet === f.id ? '' : 'border-transparent text-texto-suave hover:text-texto'",
      '  }`}',
      '>',
      '  {f.label}',
      '</button>',
    ].join('\n')
    // La interpolación es un ConditionalExpression -- irresoluble, así que
    // el className COMPLETO queda irresoluble (mismo criterio que
    // resolverClassName.js) y esto NO se puede marcar sin adivinar. Se
    // documenta como límite: ver el test siguiente con la forma real que sí
    // se arregló (TAMANO_BOTON_ACCION, sin depender de la interpolación
    // para el tamaño).
    expect(hallazgosEnFuente(codigo, 'BottomBar.jsx')).toEqual([])
  })

  it('el orden de las clases NO importa (a diferencia de tamanoDeToque.test.js)', () => {
    const normal = hallazgosEnFuente('<button className="text-xs px-2 py-0.5">x</button>', 'a.jsx')
    const reordenado = hallazgosEnFuente('<button className="py-0.5 px-2 text-xs">x</button>', 'b.jsx')
    expect(normal).toHaveLength(1)
    expect(reordenado).toHaveLength(1)
    expect(normal[0].replace('a.jsx', '')).toBe(reordenado[0].replace('b.jsx', ''))
  })

  it('NO marca el patrón original `text-xs px-2 py-1` (24px, en el mínimo, no una violación)', () => {
    expect(hallazgosEnFuente('<button className="text-xs px-2 py-1">x</button>', 'c.jsx')).toEqual([])
  })

  it('NO marca cuando hay un h-N/min-h-N explícito que ya alcanza, aunque el padding solo no alcance', () => {
    expect(hallazgosEnFuente('<button className="text-xs py-0.5 min-h-6">x</button>', 'd.jsx')).toEqual([])
    expect(hallazgosEnFuente('<button className="text-xs py-0.5 h-8">x</button>', 'e.jsx')).toEqual([])
  })

  it('NO marca si no hay ninguna clase text-* reconocida (no se puede calcular la base)', () => {
    expect(hallazgosEnFuente('<button className="py-0.5 px-2">x</button>', 'f.jsx')).toEqual([])
  })

  it('SÍ marca un padding que existe SÓLO en variante: en la pantalla base no hay relleno (2026-09-23)', () => {
    // `sm:py-2` no garantiza nada: en un celular (bajo `sm`) el botón mide
    // 16px. Antes esto quedaba sin marcar, junto con todo botón sin padding.
    expect(hallazgosEnFuente('<button className="text-xs sm:py-2">x</button>', 'g.jsx')).toHaveLength(1)
  })

  it('SÍ marca un botón-enlace suelto sin padding ni alto (el caso de "Restaurar", 16px)', () => {
    const codigo = '<td><button className="text-xs font-semibold hover:underline">Restaurar</button></td>'
    expect(hallazgosEnFuente(codigo, 'ocultos.jsx')).toEqual([
      'ocultos.jsx:1: botón de 16px de alto calculado, bajo el mínimo de 24px (WCAG 2.2 2.5.8) -- dale TAMANO_BOTON_ACCION de tema/botones.js',
    ])
  })

  it('NO marca un botón sin padding que DECLARA la excepción "Inline" de WCAG con data-en-linea', () => {
    const codigo = '<p>Si no la recuerdas, <button data-en-linea className="text-xs hover:underline">pedí otra</button>.</p>'
    expect(hallazgosEnFuente(codigo, 'oracion.jsx')).toEqual([])
  })

  it('data-en-linea={false} (o cualquier valor que no sea true) NO exime', () => {
    expect(hallazgosEnFuente('<button data-en-linea={false} className="text-xs">x</button>', 'l.jsx')).toHaveLength(1)
    expect(hallazgosEnFuente('<button data-en-linea="no" className="text-xs">x</button>', 'm.jsx')).toHaveLength(1)
    expect(hallazgosEnFuente('<button data-en-linea={true} className="text-xs">x</button>', 'n.jsx')).toEqual([])
  })

  it('min-h-6 sin padding alcanza (24px): así se arreglaron los 9 botones-enlace', () => {
    expect(hallazgosEnFuente('<button className="text-xs min-h-6 hover:underline">x</button>', 'k.jsx')).toEqual([])
  })

  it('NO marca cuando p-N Y py-N/pt-N/pb-N aparecen juntos (ambiguo: no se adivina cuál gana)', () => {
    expect(hallazgosEnFuente('<button className="text-xs p-2 py-0.5">x</button>', 'h.jsx')).toEqual([])
  })

  it('calcula bien con pt-N + pb-N sueltos (sin py-N)', () => {
    // 0.5+0.5 = 1 (4px) de relleno vertical + 16 de línea = 20, bajo 24.
    expect(hallazgosEnFuente('<button className="text-xs pt-0.5 pb-0.5">x</button>', 'i.jsx')).toHaveLength(1)
  })

  it('NO marca p-1.5 (12px) + text-xs (16px) = 28px, ya cumple', () => {
    expect(hallazgosEnFuente('<button className="text-xs p-1.5">x</button>', 'j.jsx')).toEqual([])
  })

  it('un archivo que no se puede parsear es una violación, no un salto', () => {
    const hallazgos = hallazgosEnFuente('const = {', 'roto.jsx')
    expect(hallazgos).toHaveLength(1)
    expect(hallazgos[0]).toMatch(/no se pudo analizar/i)
  })
})

describe('todo src sin botones con menos de 24px calculados', () => {
  const raiz = new URL('../', import.meta.url)
  const archivos = readdirSync(raiz, { recursive: true })
    .filter((r) => /\.(jsx|js)$/.test(r) && !/\.test\./.test(r))

  it('ningún .jsx/.js de src tiene un botón con menos de 24px calculados', () => {
    expect(archivos.length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const hallazgos = archivos.flatMap((r) => hallazgosEnFuente(readFileSync(new URL(r, raiz), 'utf8'), r))
    expect(hallazgos).toEqual([])
  })

  // Registro de la excepción "Inline" (auditoría 2026-09-23): `data-en-linea`
  // saca un botón del detector, así que cada uso tiene que verse. La lista
  // exacta vive acá; agregar uno obliga a tocar este test, y el diff lo revisa
  // una persona. Se excluye SOLO el detector (lo nombra en su código), por
  // nombre exacto: un prefijo dejaba pasar politicaDePrivacidad.jsx o
  // cualquier otro archivo de politica/ (auditoría 2026-09-23, ronda 2).
  const EXCEPCIONES_EN_LINEA = []
  it('la excepción data-en-linea sólo aparece donde está registrada', () => {
    const usos = archivos
      .filter((r) => r.replaceAll('\\', '/') !== 'politica/botonesConPocoRelleno.js')
      .flatMap((r) => readFileSync(new URL(r, raiz), 'utf8').split('\n')
        .map((linea, i) => (linea.includes('data-en-linea') ? `${r}:${i + 1}` : null))
        .filter(Boolean))
    expect(usos).toEqual(EXCEPCIONES_EN_LINEA)
  })
})
