import { useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { TAMANO_MINIMO_TOQUE } from '../tema/botones'

// Caja de contraseña con ojito para ver u ocultar lo escrito (2026-09-12,
// pedido de Fernando: toda caja de contraseña de la UI lo tiene).
//
// - Cada caja tiene SU estado: ver la nueva contraseña no destapa la de
//   confirmar (ResetPassword usaba un solo botón para las dos).
// - El botón es type="button": dentro de un <form> no lo envía.
// - Texto accesible desde i18n (aria-label + title), no un emoji.
// - Colores: tokens del tema (src/tema/tokens.css), sirven en los dos temas.
// - `className` va al <input> tal cual (cada pantalla conserva su estilo);
//   `wrapperClassName` al contenedor, para márgenes: un margen en el input
//   descentraría el ojito, que se posiciona contra el contenedor.
export default function PasswordInput({ className = '', wrapperClassName = '', ...props }) {
  const { t } = useI18n()
  const [visible, setVisible] = useState(false)
  const etiqueta = visible ? t.hidePassword : t.showPassword

  return (
    <div className={`relative ${wrapperClassName}`.trim()}>
      <input {...props} type={visible ? 'text' : 'password'} className={`${className} pr-10`.trim()} />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={etiqueta}
        aria-pressed={visible}
        title={etiqueta}
        className={`${TAMANO_MINIMO_TOQUE} absolute right-2.5 top-1/2 -translate-y-1/2 text-texto-tenue hover:text-texto transition-colors`}
      >
        {visible ? (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path d="M10 3C5 3 1.73 7.11 1 10c.73 2.89 4 7 9 7s8.27-4.11 9-7c-.73-2.89-4-7-9-7zm0 12a5 5 0 110-10 5 5 0 010 10zm0-8a3 3 0 100 6 3 3 0 000-6z" />
          </svg>
        ) : (
          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
            <path fillRule="evenodd" d="M3.28 2.22a.75.75 0 00-1.06 1.06l14.5 14.5a.75.75 0 101.06-1.06l-1.745-1.745a11.806 11.806 0 002.908-3.97C17.547 8.383 14.476 5 10 5a10.966 10.966 0 00-4.31.858L3.28 2.22zM5.94 7.16l1.39 1.39a3 3 0 004.12 4.12l1.39 1.39A5 5 0 015.94 7.16zM10 15c-1.42 0-2.737-.37-3.874-1.02l1.568-1.567A3 3 0 0010 13a3 3 0 003-3 3 3 0 00-.413-1.507l1.568-1.567A8.03 8.03 0 0118 10c-.973 2.6-4.027 5-8 5z" clipRule="evenodd" />
          </svg>
        )}
      </button>
    </div>
  )
}
