// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { hallazgosEnFuente } from './botonesSinAreaDeToque.js'

// WCAG 2.2 §2.5.8 (Target Size · Minimum, 24×24px): familia "ícono o glifo
// suelto sin ninguna clase de tamaño". Ver el comentario de cabecera de
// botonesSinAreaDeToque.js para el alcance exacto (qué cubre y qué no).
describe('detector de botones de ícono/glifo sin área de toque', () => {
  it('marca un botón cuyo único hijo es un glifo suelto ("×") sin ninguna clase de tamaño', () => {
    // Fixture EXACTA del código real de AdminRepository.jsx antes del
    // arreglo (git show 0865e87, verificado con `git show` antes de escribir
    // este test) -- no una aproximación: si esto no da rojo, el detector no
    // cazaba el defecto real que motivó este archivo.
    const codigo = '<button onClick={() => setPreview(null)} aria-label={t.adminHistoryClose} className="text-texto-tenue hover:text-texto text-lg font-bold">×</button>'
    const hallazgos = hallazgosEnFuente(codigo, 'AdminRepository.jsx')
    expect(hallazgos).toHaveLength(1)
    expect(hallazgos[0]).toContain('AdminRepository.jsx')
  })

  it('marca el mismo patrón con leading-none (Toast.jsx real, antes del arreglo)', () => {
    const codigo = [
      '<button',
      '  onClick={() => dismissToast(toast.id)}',
      '  className="flex-shrink-0 opacity-60 hover:opacity-100 text-lg leading-none"',
      '>',
      '  ×',
      '</button>',
    ].join('\n')
    expect(hallazgosEnFuente(codigo, 'Toast.jsx')).toHaveLength(1)
  })

  it('marca "✕" (FileAttachment.jsx y DetallePipeline.jsx reales, antes del arreglo)', () => {
    const fileAttachment = '<button type="button" onClick={onRemove} title={t.attachRemove} className="flex-shrink-0 text-texto-tenue hover:text-peligro transition-colors text-sm font-bold">×</button>'
    const detallePipeline = '<button type="button" onClick={onClose} aria-label={t.historialCloseDetail} title={t.historialCloseDetail} className="text-xs text-texto-tenue hover:text-texto transition-colors">✕</button>'
    expect(hallazgosEnFuente(fileAttachment, 'FileAttachment.jsx')).toHaveLength(1)
    expect(hallazgosEnFuente(detallePipeline, 'DetallePipeline.jsx')).toHaveLength(1)
  })

  it('marca el ojito de PasswordInput.jsx real (ternario de dos <svg>) sin ninguna clase de tamaño', () => {
    // Fixture EXACTA (git show 0865e87). Es el caso que prueba la precisión
    // del tokenizador: `top-1/2` y `right-2.5` NO son clases de padding/ancho
    // pero CONTIENEN la subcadena "p-" -- una búsqueda de subcadena cruda
    // habría dejado esto sin marcar (falso negativo real, no hipotético).
    const codigo = [
      '<button',
      '  type="button"',
      '  onClick={() => setVisible((v) => !v)}',
      '  aria-label={etiqueta}',
      '  aria-pressed={visible}',
      '  title={etiqueta}',
      '  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-texto-tenue hover:text-texto transition-colors"',
      '>',
      '  {visible ? (',
      '    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">',
      '      <path d="M10 3" />',
      '    </svg>',
      '  ) : (',
      '    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">',
      '      <path d="M3.28 2.22" />',
      '    </svg>',
      '  )}',
      '</button>',
    ].join('\n')
    const hallazgos = hallazgosEnFuente(codigo, 'PasswordInput.jsx')
    expect(hallazgos).toHaveLength(1)
  })

  it('marca un ícono directo (<svg> o Icono*) sin ninguna clase de tamaño', () => {
    expect(hallazgosEnFuente('<button onClick={f}><svg className="text-texto"><path/></svg></button>', 'a.jsx')).toHaveLength(1)
    expect(hallazgosEnFuente('<button onClick={f}><IconoBasura className="text-peligro" /></button>', 'b.jsx')).toHaveLength(1)
  })

  it('NO marca si el className trae alguna clase de tamaño (padding, ancho o alto)', () => {
    // BarraUsuario real: `p-1.5` + ícono `w-4 h-4` = 28px. No hace falta
    // saber que suma 28: alcanza con que haya ALGUNA clase de tamaño (ver
    // "Límite conocido" en botonesSinAreaDeToque.js -- no se calcula la
    // caja completa).
    expect(hallazgosEnFuente('<button className="flex items-center gap-1 p-1.5 rounded"><IconoSol /></button>', 'c.jsx')).toEqual([])
    // AttachButton real: ancho y alto explícitos.
    expect(hallazgosEnFuente('<button className="flex-shrink-0 w-8 h-8 flex items-center justify-center">+</button>', 'd.jsx')).toEqual([])
    expect(hallazgosEnFuente('<button className="min-h-6 min-w-6 inline-flex items-center justify-center">×</button>', 'e.jsx')).toEqual([])
  })

  it('NO marca por una coincidencia de subcadena con clases de posición (top-*, right-*) que no son de tamaño', () => {
    // Si el className NO tuviera ninguna otra clase de tamaño real, el
    // detector tiene que seguir marcando -- `top-1/2` no cuenta como si
    // fuera `p-1` o `pt-1`.
    const hallazgos = hallazgosEnFuente('<button className="absolute right-2.5 top-1/2 -translate-y-1/2">×</button>', 'f.jsx')
    expect(hallazgos).toHaveLength(1)
  })

  it('NO marca cuando el archivo IMPORTA TAMANO_MINIMO_TOQUE/TAMANO_BOTON_ACCION de tema/botones.js', () => {
    const g = [
      "import { TAMANO_MINIMO_TOQUE } from '../../tema/botones';",
      ';<button className={`${TAMANO_MINIMO_TOQUE} text-lg font-bold`}>×</button>',
    ].join('\n')
    const h = [
      "import { TAMANO_BOTON_ACCION } from '../tema/botones';",
      ';<button className={`ml-2 ${TAMANO_BOTON_ACCION} rounded`}>×</button>',
    ].join('\n')
    expect(hallazgosEnFuente(g, 'g.jsx')).toEqual([])
    expect(hallazgosEnFuente(h, 'h.jsx')).toEqual([])
  })

  it('SIGUE MARCANDO cuando el nombre coincide pero NO viene de un import real de tema/botones.js (hallazgo de revisión, reproducido)', () => {
    // Antes: se confiaba en el NOMBRE del identificador. Una constante
    // local con el mismo nombre que un token de tema/botones.js, pero sin
    // importarlo de ahí, pasaba igual -- 0 hallazgos, con el archivo sin
    // importar tema/botones.js para nada. Ahora se resuelve por CONTENIDO:
    // esta "TAMANO_MINIMO_TOQUE" de mentira no trae ninguna clase de tamaño
    // real, y se marca.
    const codigo = [
      "const TAMANO_MINIMO_TOQUE = 'text-lg font-bold';",
      ';<button className={`${TAMANO_MINIMO_TOQUE} rounded`}>×</button>',
    ].join('\n')
    expect(hallazgosEnFuente(codigo, 'trampa.jsx')).toHaveLength(1)
  })

  it('un token NUEVO agregado a tema/botones.js queda cubierto sin tocar este detector', () => {
    // No se prueba agregando un tercer export de verdad (ensuciaría
    // tema/botones.js sólo para el test) -- se prueba que CUALQUIER export
    // de tipo string del módulo real funciona igual, no sólo los dos que
    // existían cuando se escribió la primera versión de este detector: no
    // hay una lista de nombres a mano que mantener.
    for (const nombre of ['TAMANO_BOTON_ACCION', 'TAMANO_MINIMO_TOQUE']) {
      const codigo = [
        `import { ${nombre} } from '../tema/botones';`,
        `;<button className={${nombre}}>×</button>`,
      ].join('\n')
      expect(hallazgosEnFuente(codigo, 'cualquiera.jsx'), nombre).toEqual([])
    }
  })

  it('NO marca un botón con ícono Y texto visible (BarraUsuario real: no es "sólo ícono")', () => {
    const codigo = [
      '<button className={BOTON_NEUTRO}>',
      '  <IconoGlobo />',
      '  <span className="text-xs font-bold">{lang.toUpperCase()}</span>',
      '</button>',
    ].join('\n')
    expect(hallazgosEnFuente(codigo, 'BarraUsuario.jsx')).toEqual([])
  })

  it('NO marca un botón con etiqueta de texto normal (no es un glifo de un carácter)', () => {
    expect(hallazgosEnFuente('<button className="text-xs">Guardar</button>', 'i.jsx')).toEqual([])
    expect(hallazgosEnFuente('<button className="text-xs">{t.guardar}</button>', 'j.jsx')).toEqual([])
  })

  it('NO marca className={UNA_CONSTANTE} importada que no puede resolver (límite conocido, documentado)', () => {
    expect(hallazgosEnFuente('<button className={BOTON_CUALQUIERA}>×</button>', 'k.jsx')).toEqual([])
  })

  it('resuelve una constante del MISMO archivo, incluso encadenada (caso real: BarraUsuario.jsx)', () => {
    // Fixture real: BOTON trae `p-1.5` (tamaño). BOTON_NEUTRO lo referencia
    // encadenado, y el botón de salir referencia BOTON directo con más
    // clases al lado -- exactamente la forma que el escaneo de todo src
    // encontró como falso positivo al construir este detector (verificado,
    // no hipotético: ver el commit que agrega esta resolución).
    const codigo = [
      "const BOTON = 'flex items-center gap-1 p-1.5 rounded text-texto-tenue';",
      "const BOTON_NEUTRO = `${BOTON} hover:text-texto`;",
      ';<button className={`${BOTON} hover:text-peligro disabled:opacity-50`}><IconoSalir /></button>',
    ].join('\n')
    expect(hallazgosEnFuente(codigo, 'BarraUsuario.jsx')).toEqual([])
  })

  it('SIGUE marcando si la constante del mismo archivo referenciada tampoco trae tamaño', () => {
    const codigo = [
      "const SIN_TAMANO = 'text-texto-tenue hover:text-texto';",
      ';<button className={`${SIN_TAMANO} text-lg font-bold`}>×</button>',
    ].join('\n')
    expect(hallazgosEnFuente(codigo, 'x.jsx')).toHaveLength(1)
  })

  it('marca un glifo como EXPRESIÓN, `{\'×\'}`, no sólo como texto literal (hueco declarado, corregido)', () => {
    expect(hallazgosEnFuente("<button className=\"text-lg\">{'×'}</button>", 'expr.jsx')).toHaveLength(1)
  })

  it('con className duplicado, usa el ÚLTIMO (el que JSX aplica de verdad), no el primero', () => {
    // Antes: .find() se quedaba con el primero. Acá el primero SÍ trae
    // tamaño y el último (el que gana en el DOM real) no -- si el detector
    // mirara el primero, no marcaría; con el último, sí.
    const codigo = '<button className="min-h-6 min-w-6" className="text-lg font-bold">×</button>'
    expect(hallazgosEnFuente(codigo, 'dup.jsx')).toHaveLength(1)
  })

  it('una clase de tamaño SÓLO en variante (sm:/hover:/disabled:) no cuenta -- es condicional (hueco declarado, corregido)', () => {
    expect(hallazgosEnFuente('<button className="sm:p-2 text-lg">×</button>', 'variante1.jsx')).toHaveLength(1)
    expect(hallazgosEnFuente('<button className="hover:p-2 text-lg">×</button>', 'variante2.jsx')).toHaveLength(1)
    // Con la clase SIN variante presente TAMBIÉN, sí cuenta (la de variante
    // no hace falta que aporte nada).
    expect(hallazgosEnFuente('<button className="p-2 sm:p-4 text-lg">×</button>', 'variante3.jsx')).toEqual([])
  })

  it('un valor de CERO (p-0, w-0) no cuenta como clase de tamaño (hueco declarado, corregido)', () => {
    expect(hallazgosEnFuente('<button className="p-0 w-0 text-lg">×</button>', 'cero.jsx')).toHaveLength(1)
    // p-0.5 SÍ cuenta (no es cero, aunque no alcance el mínimo -- ver el
    // "Límite conocido" sobre no calcular la caja completa).
    expect(hallazgosEnFuente('<button className="p-0.5 text-lg">×</button>', 'nocero.jsx')).toEqual([])
  })

  it('`<button {...props}>` no se marca "sin className": el spread podría traerlo (hueco declarado, corregido)', () => {
    expect(hallazgosEnFuente('<button {...props}>×</button>', 'spread.jsx')).toEqual([])
  })

  it('una interpolación irresoluble (ternario) invalida TODO el className -- no se marca en falso (hueco declarado, corregido)', () => {
    const codigo = '<button className={`${activo ? "a" : "b"} text-lg`}>×</button>'
    expect(hallazgosEnFuente(codigo, 'ternario.jsx')).toEqual([])
  })

  it('una constante importada de OTRO archivo (no tema/botones.js) dentro de un template invalida TODO el className, no sólo esa parte (MINOR corregido)', () => {
    // Antes: la interpolación irresoluble se trataba como texto vacío y el
    // resto SÍ se evaluaba -- si el único tamaño real viniera de esa
    // constante no resuelta, marcaba en falso. Ahora, igual que un
    // Identifier suelto irresoluble, TODO el className queda irresoluble.
    const codigo = [
      "import { OTRO } from './otroArchivo';",
      ';<button className={`${OTRO} rounded`}>×</button>',
    ].join('\n')
    expect(hallazgosEnFuente(codigo, 'importada.jsx')).toEqual([])
  })

  it('un archivo que no se puede parsear es una violación, no un salto', () => {
    const hallazgos = hallazgosEnFuente('const = {', 'roto.jsx')
    expect(hallazgos).toHaveLength(1)
    expect(hallazgos[0]).toMatch(/no se pudo analizar/i)
  })
})

describe('todo src sin botones de ícono/glifo bajo el mínimo', () => {
  const raiz = new URL('../', import.meta.url)
  const archivos = readdirSync(raiz, { recursive: true })
    .filter((r) => /\.(jsx|js)$/.test(r) && !/\.test\./.test(r))

  it('ningún .jsx/.js de src tiene un botón de ícono/glifo sin ninguna clase de tamaño', () => {
    expect(archivos.length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const hallazgos = archivos.flatMap((r) => hallazgosEnFuente(readFileSync(new URL(r, raiz), 'utf8'), r))
    expect(hallazgos).toEqual([])
  })
})
