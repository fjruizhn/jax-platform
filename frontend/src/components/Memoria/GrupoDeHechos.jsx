import { useI18n } from '../../i18n/index.jsx'
import FichaDeHecho from './FichaDeHecho'

// Un grupo = un tema (agrupado por cercanía semántica, backend Task 5).
// Dos cosas que el spec pide explícitamente y que acá se resuelven juntas
// (§2.1, §2.2):
//   - "junte las cosas del mismo tema" -- ya viene agrupado; acá sólo se
//     pinta.
//   - "aprobar en lote... revisar 115 de a uno no lo hace nadie" -- el
//     checkbox de cada ficha alimenta ESTE botón, no uno por ficha.
// Los casi-duplicados (`grupo.casi_duplicados`, ids que a distancia coseno
// están dentro del mismo umbral que ya usa el corrector, ver
// backend/api/admin/memoria.py) se renderizan juntos dentro de un panel
// marcado -- "estos N hechos dicen lo mismo" -- en vez de mezclados con el
// resto en orden de fecha.
function agruparParaRenderizar(grupo) {
  const clusterDeId = new Map()
  for (const cluster of grupo.casi_duplicados || []) {
    for (const id of cluster) clusterDeId.set(id, cluster)
  }
  const renderizados = new Set()
  const items = []
  for (const id of grupo.hechos) {
    const cluster = clusterDeId.get(id)
    if (!cluster) { items.push({ tipo: 'individual', id }); continue }
    const clave = cluster.join(',')
    if (renderizados.has(clave)) continue
    renderizados.add(clave)
    // Mismo orden que grupo.hechos (creado_at DESC, backend) -- el primero
    // del cluster en ese orden es el más reciente.
    items.push({ tipo: 'cluster', ids: grupo.hechos.filter((x) => cluster.includes(x)) })
  }
  return items
}

export default function GrupoDeHechos({
  grupo, indice, hechosPorId, seleccionados, procesando,
  onToggleSeleccion, onSeleccionarTodos, onSeleccionarNinguno, onAprobarLote,
  onAprobar, onAbrirCorregir, onAbrirCaducar, onQuitarCaducidad, onAbrirFundir,
}) {
  const { t } = useI18n()
  const idsDelGrupo = grupo.hechos.filter((id) => hechosPorId[id])
  const sinVerificarIds = idsDelGrupo.filter((id) => !hechosPorId[id].verificado)
  const seleccionadosDelGrupo = sinVerificarIds.filter((id) => seleccionados.has(id))
  const loteOcupado = seleccionadosDelGrupo.some((id) => procesando.has(id))

  const items = agruparParaRenderizar(grupo)

  return (
    <section data-testid={`grupo-${indice}`} className="rounded-xl border border-borde bg-fondo p-4 space-y-3">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-texto-fuerte break-words">{grupo.tema}</h2>
          <p className="text-xs text-texto-tenue mt-0.5">
            {sinVerificarIds.length > 0 ? t.memoria.totalSinVerificar(sinVerificarIds.length) : t.memoria.grupoTodosVerificados}
          </p>
        </div>
        {sinVerificarIds.length > 0 && (
          <div className="flex items-center gap-2 flex-shrink-0">
            <button type="button" onClick={() => onSeleccionarTodos(sinVerificarIds)}
              className="text-xs text-acento-texto hover:underline">
              {t.memoria.seleccionarTodos}
            </button>
            <button type="button" onClick={() => onSeleccionarNinguno(sinVerificarIds)}
              className="text-xs text-texto-tenue hover:underline">
              {t.memoria.seleccionarNinguno}
            </button>
            <button
              type="button"
              disabled={seleccionadosDelGrupo.length === 0 || loteOcupado}
              onClick={() => onAprobarLote(seleccionadosDelGrupo)}
              className="text-xs px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color font-semibold transition-colors disabled:opacity-50 disabled:pointer-events-none"
            >
              {loteOcupado ? t.memoria.aprobando : t.memoria.aprobarSeleccionados(seleccionadosDelGrupo.length)}
            </button>
          </div>
        )}
      </header>

      <div className="space-y-3">
        {items.map((item) => {
          if (item.tipo === 'individual') {
            const hecho = hechosPorId[item.id]
            if (!hecho) return null
            return (
              <FichaDeHecho
                key={hecho.id}
                hecho={hecho}
                seleccionado={seleccionados.has(hecho.id)}
                onToggleSeleccion={() => onToggleSeleccion(hecho.id)}
                ocupado={procesando.has(hecho.id)}
                onAprobar={() => onAprobar(hecho.id)}
                onCorregir={() => onAbrirCorregir(hecho)}
                onCaducar={() => onAbrirCaducar(hecho)}
                onQuitarCaducidad={() => onQuitarCaducidad(hecho.id)}
              />
            )
          }
          const miembros = item.ids.map((id) => hechosPorId[id]).filter(Boolean)
          if (miembros.length < 2) return null
          const ocupadoCluster = item.ids.some((id) => procesando.has(id))
          return (
            <div key={item.ids.join(',')} className="rounded-lg border-2 border-dashed border-aviso-borde p-3 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-semibold text-aviso">{t.memoria.casiDuplicados(miembros.length)}</p>
                <button
                  type="button"
                  disabled={ocupadoCluster}
                  onClick={() => onAbrirFundir(item.ids)}
                  className="text-xs px-2 py-1 rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors disabled:opacity-50 disabled:pointer-events-none"
                >
                  {t.memoria.fundir}
                </button>
              </div>
              {miembros.map((hecho) => (
                <FichaDeHecho
                  key={hecho.id}
                  hecho={hecho}
                  resaltado
                  seleccionado={seleccionados.has(hecho.id)}
                  onToggleSeleccion={() => onToggleSeleccion(hecho.id)}
                  ocupado={procesando.has(hecho.id)}
                  onAprobar={() => onAprobar(hecho.id)}
                  onCorregir={() => onAbrirCorregir(hecho)}
                  onCaducar={() => onAbrirCaducar(hecho)}
                  onQuitarCaducidad={() => onQuitarCaducidad(hecho.id)}
                />
              ))}
            </div>
          )
        })}
      </div>
    </section>
  )
}
