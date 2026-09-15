// Aviso de error de una pantalla (2026-09-14). Un solo lugar para su rol y su
// color (token peligro, src/tema/tokens.css).
export default function AlertaError({ children, className = '' }) {
  return (
    <div role="alert" className={`text-peligro ${className}`.trim()}>
      {children}
    </div>
  )
}
