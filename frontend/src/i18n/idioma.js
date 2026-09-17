// Idioma de la interfaz (frente C, 2026-09-16). Sin React: lo usan
// I18nProvider, el store de apariencia y useJaxStore (fuera de React).
export const IDIOMAS = ['es', 'en']
export const IDIOMA_DE_RESPALDO = 'es'
export const CLAVE_ELECCION_IDIOMA = 'jax_lang' // lo que eligió el usuario
export const CLAVE_IDIOMA_PREDETERMINADO = 'jax_lang_default' // último lang_default conocido del sistema

export function esIdioma(valor) {
  return IDIOMAS.includes(valor)
}
