// Idioma de la interfaz (frente C, 2026-09-16). Sin React: lo usan
// I18nProvider, el store de apariencia y useJaxStore (fuera de React).
export const IDIOMAS = ['es', 'en']
export const IDIOMA_DE_RESPALDO = 'es'
export const CLAVE_ELECCION_IDIOMA = 'jax_lang' // lo que eligió el usuario
export const CLAVE_IDIOMA_PREDETERMINADO = 'jax_lang_default' // último lang_default conocido del sistema

export function esIdioma(valor) {
  return IDIOMAS.includes(valor)
}

// Elección del usuario > último predeterminado del sistema > español.
// `almacen` es inyectable (no siempre globalThis.localStorage: I18nProvider
// vive dentro de React y usa su propio store, pero useJaxStore no puede usar
// hooks) -- por eso mantiene su propio try/catch en vez de leer/escribir de
// almacenamiento.js (que sólo conoce localStorage).
export function idiomaInicial(almacen = globalThis.localStorage) {
  try {
    const eleccion = almacen.getItem(CLAVE_ELECCION_IDIOMA)
    if (esIdioma(eleccion)) return eleccion
    const predeterminado = almacen.getItem(CLAVE_IDIOMA_PREDETERMINADO)
    if (esIdioma(predeterminado)) return predeterminado
  } catch {
    // fail-soft: almacenamiento bloqueado -- idioma de respaldo
  }
  return IDIOMA_DE_RESPALDO
}
