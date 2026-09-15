// Nombres de los tokens de color y matriz de pares de contraste (spec
// 2026-09-14-tema-tokens-design.md §3-§4). Acá NO hay valores: viven sólo en
// tokens.css. Lo importan tailwind.config.js, el test de contraste y el código
// que pinta un color que viene de datos (colorToken).

export const TOKENS = [
  'fondo', 'superficie', 'superficie-2', 'hundido', 'borde', 'borde-control',
  'barra-pista', 'barra-pulgar',
  'texto-fuerte', 'texto', 'texto-suave', 'texto-tenue', 'sobre-color',
  'acento', 'acento-hover', 'acento-texto', 'acento-fondo',
  'accion', 'accion-hover', 'info', 'info-fondo', 'foco',
  'peligro', 'peligro-fondo', 'peligro-borde', 'peligro-solido', 'peligro-solido-hover',
  'exito', 'exito-fondo', 'exito-borde',
  'aviso', 'aviso-fondo', 'aviso-borde',
  'obsoleto',
  'oro', 'oro-claro', 'oro-oscuro',
  'burbuja-usuario', 'modo-comando',
  'faceta-jax-local', 'faceta-jekyll', 'faceta-hyde', 'faceta-hipatia',
  'faceta-thot', 'faceta-kimi', 'faceta-ada', 'faceta-jacobs', 'faceta-imagen',
]

// WCAG 2.x AA: 4,5 texto normal; 3 componentes de UI (1.4.11).
export const AA_TEXTO = 4.5
export const AA_UI = 3

const FACETAS = TOKENS.filter((t) => t.startsWith('faceta-'))
const TEXTOS_SOBRE_BASE = [
  'texto-fuerte', 'texto', 'texto-suave', 'texto-tenue', 'acento-texto',
  'info', 'peligro', 'exito', 'aviso', 'obsoleto', 'oro', 'oro-claro', ...FACETAS,
]
const FONDOS_BASE = ['fondo', 'superficie', 'hundido']

// [primer plano, fondo, mínimo]. 88 pares (spec §3.3). Lo que no está acá no
// se combina: p. ej. texto-suave o un color de estado sobre superficie-2.
export const PARES = [
  ...FONDOS_BASE.flatMap((f) => TEXTOS_SOBRE_BASE.map((t) => [t, f, AA_TEXTO])),
  ['texto-fuerte', 'superficie-2', AA_TEXTO],
  ['texto', 'superficie-2', AA_TEXTO],
  ...['acento', 'info', 'peligro', 'exito', 'aviso'].flatMap((e) => [
    [e === 'acento' ? 'acento-texto' : e, `${e}-fondo`, AA_TEXTO],
    ['texto', `${e}-fondo`, AA_TEXTO],
  ]),
  ...['acento', 'acento-hover', 'accion', 'accion-hover', 'peligro-solido', 'peligro-solido-hover', 'modo-comando']
    .map((f) => ['sobre-color', f, AA_TEXTO]),
  ['texto', 'burbuja-usuario', AA_TEXTO],
  ['texto-suave', 'burbuja-usuario', AA_TEXTO],
  ['fondo', 'texto-fuerte', AA_TEXTO],
  ...FONDOS_BASE.flatMap((f) => [['borde-control', f, AA_UI], ['foco', f, AA_UI]]),
]

// Tokens que se pueden usar como `text-*` sin ser primer plano de ningún par.
// Lista CERRADA: cada entrada con su motivo y los únicos archivos donde vale.
export const EXENTOS_TEXTO = {
  'oro-oscuro': {
    motivo: 'separador aria-hidden del logotipo: decorativo, WCAG 1.4.3 lo exime',
    archivos: ['components/LogoAxioma.jsx'],
  },
}

// Colores crudos (clase de paleta, hex, rgb) que se aceptan en un archivo
// migrado. Lista CERRADA, vacía a propósito: cada entrada lleva
// { archivo, texto, motivo } y la aprueba la revisión.
export const PERMITIDOS_CRUDOS = []

const RESPALDO = 'texto-suave'

// Color CSS de un token para un `style={{}}` (colores que vienen de datos).
// Un nombre desconocido cae en texto-suave, el gris de respaldo de hoy.
export function colorToken(nombre, alfa = 1) {
  const token = TOKENS.includes(nombre) ? nombre : RESPALDO
  return `rgb(var(--${token}) / ${alfa})`
}

// Clave de faceta del backend (jax_local, hyde, ...) -> su token de identidad.
// DALL·E no es una faceta: se pide 'faceta-imagen' directo. Una clave que el
// tema no conoce cae en el mismo respaldo que colorToken.
export function tokenDeFaceta(clave) {
  const token = `faceta-${String(clave).replaceAll('_', '-')}`
  return TOKENS.includes(token) ? token : RESPALDO
}
