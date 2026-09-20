import { useI18n, localeFor } from '../../i18n/index.jsx'

// Sección «Vencidos» (decisión de Fernando, 2026-09-20). Hoy un hecho
// vencido desaparece de la pantalla y no se le puede quitar la caducidad:
// caducás algo por error, recargás, y ya no podés deshacerlo -- eso
// contradice la intención del spec (caducar no es borrar, el hecho "sigue,
// deja de pesar"), pero sin forma de VERLO, en la práctica SÍ era borrar.
//
// GET /hechos?incluir_vencidos=true (Task 3, ya soportado por el backend)
// trae vencidos Y activos juntos -- Memoria.jsx filtra acá sólo los vencidos.
//
// <details> arranca cerrado (mismo patrón que
// components/historial/DetallePipeline.jsx): no compite visualmente con la
// cola de revisión, que es la tarea principal, pero se llega sin buscarla.
export default function SeccionVencidos({ vencidos, totalReal, procesando, onQuitarCaducidad }) {
  const { t, lang } = useI18n()
  // `vencidos` es lo que trajo el cap de 500 de GET /hechos (Memoria.jsx
  // filtra ahí `vencido: true`); `totalReal` es la cuenta de verdad
  // (derivada en Memoria.jsx de los dos `total` que ya devuelve el
  // servidor). Medido contra la base de carga: con 500 hechos vigentes
  // ordenados antes que los vencidos, el cap dejaba afuera 200 de 500
  // vencidos reales -- mismo defecto que el contador de arriba.
  const real = totalReal ?? vencidos.length
  if (real === 0) return null
  const fecha = (iso) => (iso ? new Date(iso).toLocaleString(localeFor(lang)) : null)

  return (
    <details className="rounded-xl border border-borde bg-fondo p-4">
      <summary className="cursor-pointer text-sm font-semibold text-texto-suave select-none">
        {vencidos.length === real
          ? t.memoria.vencidosResumen(real)
          : t.memoria.vencidosResumenSubconjunto(vencidos.length, real)}
      </summary>
      <ul className="mt-3 space-y-2">
        {vencidos.map((hecho) => (
          <li
            key={hecho.id}
            data-testid={`vencido-${hecho.id}`}
            className="rounded-lg border border-borde bg-superficie p-3 flex flex-wrap items-center justify-between gap-2"
          >
            <div className="min-w-0">
              <p className="text-xs text-texto-tenue">#{hecho.id} · {t.memoria.vencidoDesde(fecha(hecho.vence_at))}</p>
              <p className="text-sm text-texto break-words">{hecho.texto}</p>
            </div>
            <button
              type="button"
              aria-label={t.memoria.quitarCaducidadDe(hecho.id)}
              disabled={procesando.has(hecho.id)}
              onClick={() => onQuitarCaducidad(hecho.id)}
              className="text-xs px-2 py-1 rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors disabled:opacity-50 disabled:pointer-events-none flex-shrink-0"
            >
              {t.memoria.quitarCaducidad}
            </button>
          </li>
        ))}
      </ul>
    </details>
  )
}
