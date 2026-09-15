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

// tokens.css -> { oscuro, claro }. Oscuro es `:root { }` y claro
// `:root[data-tema="claro"] { }`. Si falta un bloque, tira: un tema a medias
// no puede pasar en verde.
export function parsearTokens(css) {
  const oscuro = css.match(/:root\s*\{([^}]*)\}/)
  const claro = css.match(/:root\[data-tema="claro"\]\s*\{([^}]*)\}/)
  if (!oscuro || !claro) throw new Error('tokens.css sin el bloque :root o sin :root[data-tema="claro"]')
  return { oscuro: variables(oscuro[1]), claro: variables(claro[1]) }
}
