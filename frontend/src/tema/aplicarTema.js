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
  return resolverTema(almacen.getItem(CLAVE_ELECCION), almacen.getItem(CLAVE_PREDETERMINADO))
}

// data-tema="claro" es lo que leen los tokens. La clase light-mode es la de la
// capa vieja de index.css: convive hasta el PR 4 del rollout, que la quita.
export function aplicarTema(tema, raiz = document.documentElement) {
  if (tema === 'light') {
    raiz.setAttribute('data-tema', 'claro')
    raiz.classList.add('light-mode')
  } else {
    raiz.removeAttribute('data-tema')
    raiz.classList.remove('light-mode')
  }
}
