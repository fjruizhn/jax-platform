import { useI18n } from '../../i18n/index.jsx'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
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
//
// Ronda 2026-09-22: cada cluster es `{ids, superviviente_id}` (antes, una
// lista de ids a secas) -- el backend declara quién sobrevive
// (_elegir_superviviente: el verificado gana al más reciente); esta pantalla
// ya NO asume "el primero de la lista", que era exactamente el hallazgo de
// Fernando (una síntesis sin verificar podía superar a un hecho verificado).
//
// Ronda 146 (revisión adversarial de jax-platform PR 146, D5): el cluster
// también trae `superviviente_verificado`/`superviviente_texto` -- se
// arrastran hasta `Memoria.jsx` para armar el motivo y el texto de la
// ConfirmacionSuma con ESTOS datos, no con `hechosPorId` (que sólo tiene los
// primeros 500 hechos que cargó GET /hechos; un cluster puede traer ids que
// ese cap dejó afuera).
function agruparParaRenderizar(grupo) {
  const clusterDeId = new Map()
  for (const cluster of grupo.casi_duplicados || []) {
    for (const id of cluster.ids) clusterDeId.set(id, cluster)
  }
  const renderizados = new Set()
  const items = []
  for (const id of grupo.hechos) {
    const cluster = clusterDeId.get(id)
    if (!cluster) { items.push({ tipo: 'individual', id }); continue }
    const clave = cluster.ids.join(',')
    if (renderizados.has(clave)) continue
    renderizados.add(clave)
    items.push({
      tipo: 'cluster',
      ids: grupo.hechos.filter((x) => cluster.ids.includes(x)),
      supervivienteId: cluster.superviviente_id,
      supervivienteVerificado: cluster.superviviente_verificado,
      supervivienteTexto: cluster.superviviente_texto,
    })
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
  // El backend arma `grupo.hechos` con TODOS los miembros activos del tema
  // (backend/api/admin/memoria.py::agrupar_por_tema, sin el cap de 500 de
  // GET /hechos) y ya trae `sin_verificar` contado sobre esos miembros
  // completos -- es la cuenta real del grupo, no la de lo que esta pantalla
  // alcanzó a cargar. `sinVerificarIds.length` (arriba) sigue siendo lo que
  // gobierna qué se puede seleccionar/aprobar en lote, porque sólo se puede
  // accionar sobre lo que SÍ está cargado.
  const sinVerificarReal = grupo.sin_verificar ?? sinVerificarIds.length

  const items = agruparParaRenderizar(grupo)

  return (
    <section data-testid={`grupo-${indice}`} className="rounded-xl border border-borde bg-fondo p-4 space-y-3">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-texto-fuerte break-words">{grupo.tema}</h2>
          <p className="text-xs text-texto-tenue mt-0.5">
            {sinVerificarReal === 0
              ? t.memoria.grupoTodosVerificados
              : sinVerificarIds.length === sinVerificarReal
                ? t.memoria.totalSinVerificar(sinVerificarReal)
                : t.memoria.totalSinVerificarSubconjunto(sinVerificarIds.length, sinVerificarReal)}
          </p>
        </div>
        {sinVerificarIds.length > 0 && (
          <div className="flex items-center gap-2 flex-shrink-0">
            <button type="button" onClick={() => onSeleccionarTodos(sinVerificarIds)}
              className="text-xs min-h-6 text-acento-texto hover:underline">
              {t.memoria.seleccionarTodos}
            </button>
            <button type="button" onClick={() => onSeleccionarNinguno(sinVerificarIds)}
              className="text-xs min-h-6 text-texto-tenue hover:underline">
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
          // M3 (revisión adversarial de jax-platform PR 146, tercera
          // vuelta): el aviso "Estos N hechos..." cuenta TODOS los
          // `item.ids` que trajo el backend, no sólo los que este cap de
          // 500 llegó a cargar (`miembros`) -- si hay diferencia, se dice
          // explícito cuántos faltan.
          const totalCluster = item.ids.length
          const noCargadosCluster = totalCluster - miembros.length
          const textoCasiDuplicados = noCargadosCluster > 0
            ? t.memoria.casiDuplicadosSubconjunto(totalCluster, noCargadosCluster)
            : t.memoria.casiDuplicados(totalCluster)
          return (
            <div key={item.ids.join(',')} className="rounded-lg border-2 border-dashed border-aviso-borde p-3 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-semibold text-aviso">{textoCasiDuplicados}</p>
                <button
                  type="button"
                  disabled={ocupadoCluster}
                  onClick={() => onAbrirFundir(
                    item.ids, item.supervivienteId, item.supervivienteVerificado, item.supervivienteTexto,
                  )}
                  className={`${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors disabled:opacity-50 disabled:pointer-events-none`}
                >
                  {t.memoria.fundir}
                </button>
              </div>
              {miembros.map((hecho) => (
                <FichaDeHecho
                  key={hecho.id}
                  hecho={hecho}
                  resaltado
                  esSuperviviente={hecho.id === item.supervivienteId}
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
