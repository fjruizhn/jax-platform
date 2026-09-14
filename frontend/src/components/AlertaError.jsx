// Aviso de error de una pantalla (2026-09-14). Un solo lugar para su rol y su
// color: cuando llegue el tema claro/oscuro (DEUDA.md, frontend sin tema) se
// migra acá y no en cada pantalla que muestra un error.
export default function AlertaError({ children, className = '' }) {
  return (
    <div role="alert" className={`text-red-400 ${className}`.trim()}>
      {children}
    </div>
  )
}
