import { createContext, useContext, useState } from 'react'
import es from './es.js'
import en from './en.js'
import { CLAVE_ELECCION_IDIOMA, IDIOMA_DE_RESPALDO, esIdioma, idiomaInicial } from './idioma'
import { useApariencia } from '../store/useApariencia'
import { leer, escribir } from '../store/almacenamiento'

const LANGS = { es, en }
const I18nContext = createContext(null)

// I-1 (revisión final PR 2, 2026-09-14): AdminRepository y AdminUsers
// fijaban 'es-HN' en toLocaleString sin importar el idioma activo. El
// locale de fechas sale del idioma de la interfaz, no de un valor fijo.
const LOCALES = { es: 'es-HN', en: 'en-US' }
export function localeFor(lang) {
  return LOCALES[lang] || LOCALES.es
}

// Diccionario activo fuera de React (A-29, 2026-09-16): el store no es un
// componente y no puede usar el hook. Frente C (2026-09-16): usa la misma
// regla que el proveedor, en un solo lugar (idioma.js::idiomaInicial):
// elección de la persona > lang_default del sistema > español.
export function diccionarioActivo() {
  return LANGS[idiomaInicial()]
}

function eleccionGuardada() {
  const valor = leer(CLAVE_ELECCION_IDIOMA)
  return esIdioma(valor) ? valor : null
}

export function I18nProvider({ children }) {
  // Frente C (2026-09-16): la ELECCIÓN de la persona (jax_lang) gana; si no
  // eligió, manda lang_default del sistema (useApariencia), y si todavía no se
  // conoce, español. Solo setLang escribe una elección.
  const [eleccion, setEleccion] = useState(eleccionGuardada)
  const langDefault = useApariencia((s) => s.langDefault)
  const lang = eleccion ?? (esIdioma(langDefault) ? langDefault : IDIOMA_DE_RESPALDO)

  function setLang(l) {
    setEleccion(l)
    escribir(CLAVE_ELECCION_IDIOMA, l)
  }

  return (
    <I18nContext.Provider value={{ lang, setLang, t: LANGS[lang] || LANGS.es }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  return useContext(I18nContext)
}
