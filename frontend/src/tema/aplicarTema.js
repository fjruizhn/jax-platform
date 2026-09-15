// Tema claro/oscuro (spec 2026-09-14-tema-tokens §5). Una sola regla y una
// sola forma de aplicarlo al DOM. El script en línea de index.html es la copia
// mínima de temaInicial + aplicarTema; scriptTema.test.js exige que hagan lo mismo.

export const CLAVE_ELECCION = 'jax_theme' // lo que eligió el usuario (clave de siempre)
export const CLAVE_PREDETERMINADO = 'jax_theme_default' // último theme_default conocido
export const TEMA_DE_RESPALDO = 'dark' // = DEFAULT_CONFIG.theme_default del backend

export function esTema(valor) {
  return valor === 'dark' || valor === 'light'
}

// Si hay elección, gana la elección; si no, el predeterminado; si no, oscuro.
export function resolverTema(eleccion, predeterminado) {
  if (esTema(eleccion)) return eleccion
  if (esTema(predeterminado)) return predeterminado
  return TEMA_DE_RESPALDO
}

export function temaInicial(almacen = localStorage) {
  try {
    return resolverTema(almacen.getItem(CLAVE_ELECCION), almacen.getItem(CLAVE_PREDETERMINADO))
  } catch {
    // fail-soft: almacenamiento bloqueado (Safari con cookies bloqueadas,
    // iframe con sandbox) -- tema de respaldo, no pantalla en blanco (M-4,
    // revisión final del PR 1, 2026-09-14).
    return TEMA_DE_RESPALDO
  }
}

// data-tema="claro" es lo que leen los tokens (src/tema/tokens.css).
export function aplicarTema(tema, raiz = document.documentElement) {
  if (tema === 'light') raiz.setAttribute('data-tema', 'claro')
  else raiz.removeAttribute('data-tema')
}
