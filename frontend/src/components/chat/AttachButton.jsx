import { useRef } from 'react'

// Frente D (2026-09-16): `accept` y `title` vienen de BottomBar (política del
// servidor y texto i18n). Nada de listas de tipos fijas acá.
export default function AttachButton({ onFileSelected, disabled, accept, title }) {
  const inputRef = useRef(null)

  function handleChange(e) {
    const file = e.target.files?.[0]
    if (file) {
      onFileSelected(file)
      e.target.value = ''
    }
  }

  return (
    <>
      <input ref={inputRef} type="file" accept={accept} className="hidden" onChange={handleChange} disabled={disabled} />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        title={title}
        className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-superficie hover:bg-superficie-2 text-texto-suave hover:text-texto disabled:opacity-40 transition-colors border border-borde text-lg font-bold"
      >
        +
      </button>
    </>
  )
}
