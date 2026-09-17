import { createContext, useContext, useState } from 'react'
import es from './es.js'
import en from './en.js'

const LANGS = { es, en }
const I18nContext = createContext(null)

// I-1 (revisión final PR 2, 2026-09-14): AdminRepository y AdminUsers
// fijaban 'es-HN' en toLocaleString sin importar el idioma activo. El
// locale de fechas sale del idioma de la interfaz, no de un valor fijo.
const LOCALES = { es: 'es-HN', en: 'en-US' }
export function localeFor(lang) {
  return LOCALES[lang] || LOCALES.es
}

// Idioma guardado y su diccionario (A-29, 2026-09-16): una sola regla para el
// proveedor y para el store, que no es un componente y no puede usar el hook.
export function idiomaGuardado() {
  // Una sola lectura; Object.hasOwn: `constructor` no es un idioma (ronda final M9).
  const guardado = localStorage.getItem('jax_lang')
  return typeof guardado === 'string' && Object.hasOwn(LANGS, guardado) ? guardado : 'es'
}

export function diccionarioActivo() {
  return LANGS[idiomaGuardado()]
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => idiomaGuardado())

  function setLang(l) {
    setLangState(l)
    localStorage.setItem('jax_lang', l)
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
