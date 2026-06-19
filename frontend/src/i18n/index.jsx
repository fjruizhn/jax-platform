import { createContext, useContext, useState } from 'react'
import es from './es.js'
import en from './en.js'

const LANGS = { es, en }
const I18nContext = createContext(null)

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(
    () => localStorage.getItem('jax_lang') || 'es'
  )

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
