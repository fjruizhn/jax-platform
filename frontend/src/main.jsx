import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { I18nProvider } from './i18n/index.jsx'
import './index.css'

// Apply persisted theme before first render to avoid flash
;(function () {
  const theme = localStorage.getItem('jax_theme') || 'dark'
  if (theme === 'light') document.documentElement.classList.add('light-mode')
})()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>
)
