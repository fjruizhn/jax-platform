import { useRef } from 'react'
import { useI18n } from '../../i18n/index.jsx'

const ACCEPT = [
  'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml',
  'application/pdf',
  'text/plain', 'text/markdown', 'text/csv', 'application/json',
  '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.toml', '.yml', '.yaml', '.sh',
].join(',')

export default function AttachButton({ onFileSelected, disabled }) {
  const { t } = useI18n()
  const inputRef = useRef(null)

  function handleClick() {
    inputRef.current?.click()
  }

  function handleChange(e) {
    const file = e.target.files?.[0]
    if (file) {
      onFileSelected(file)
      e.target.value = ''
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={handleChange}
        disabled={disabled}
      />
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        title={t.attachTooltip}
        className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 disabled:opacity-40 transition-colors border border-slate-700 text-lg font-bold"
      >
        +
      </button>
    </>
  )
}
