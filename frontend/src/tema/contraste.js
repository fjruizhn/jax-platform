// Contraste WCAG 2.x (luminancia relativa sRGB). Puro: lo usa el test de
// contraste; la app no lo carga en tiempo de ejecución.
function canal(c) {
  const s = c / 255
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
}

export function luminancia([r, g, b]) {
  return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)
}

export function contraste(a, b) {
  const [claro, oscuro] = [luminancia(a), luminancia(b)].sort((x, y) => y - x)
  return (claro + 0.05) / (oscuro + 0.05)
}

// `--nombre: R G B;` de un bloque de CSS -> { nombre: [R, G, B] }.
function variables(bloque) {
  const salida = {}
  for (const [, nombre, r, g, b] of bloque.matchAll(/--([a-z0-9-]+):\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;/g)) {
    salida[nombre] = [Number(r), Number(g), Number(b)]
  }
  return salida
}

// tokens.css -> { oscuro, claro, oficinaOscuro, oficinaClaro }. Oscuro es
// `:root { }` y claro `:root[data-tema="claro"] { }`; los de la instalación
// de la Oficina agregan data-instalacion="oficina" (OP 358). Si falta un
// bloque, tira: un tema a medias no puede pasar en verde.
export function parsearTokens(css) {
  const bloques = [
    ['oscuro', /:root\s*\{([^}]*)\}/],
    ['claro', /:root\[data-tema="claro"\]\s*\{([^}]*)\}/],
    ['oficinaOscuro', /:root\[data-instalacion="oficina"\]\s*\{([^}]*)\}/],
    ['oficinaClaro', /:root\[data-instalacion="oficina"\]\[data-tema="claro"\]\s*\{([^}]*)\}/],
  ]
  const temas = {}
  for (const [nombre, patron] of bloques) {
    const bloque = css.match(patron)
    if (!bloque) throw new Error(`tokens.css sin el bloque del tema ${nombre}`)
    temas[nombre] = variables(bloque[1])
  }
  return temas
}
