import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { I18nProvider } from './i18n/index.jsx'
import './index.css'

// Inter en el bundle (@fontsource, OFL), como IBM Plex Serif en LogoAxioma:
// sin Google Fonts, así ninguna visita le avisa a un tercero. Sólo el subset
// latin y los pesos que usa la UI: normal 400, font-medium 500,
// font-semibold 600, font-bold 700 (font-light es de la marca, Plex Serif).
// DECISIÓN de Fernando (2026-09-13, reafirmada con el spec 2026-09-14): Inter se queda aunque el hook de
// impeccable la marque como sobreusada (.impeccable/config.json).
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>
)
