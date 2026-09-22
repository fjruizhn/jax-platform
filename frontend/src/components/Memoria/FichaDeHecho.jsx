import { useI18n, localeFor } from '../../i18n/index.jsx'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

// Una ficha = un hecho, con su procedencia SIEMPRE visible (spec §2.1: «la
// memoria sin procedencia es otra forma de suposición») -- si `mensaje_id` o
// `faceta` vienen null, se muestra la marca de "vacía", nunca se omite el
// campo (pedido explícito de Fernando en el brief de esta tarea).
//
// Aprobar es NO destructivo: llama directo, sin ventana propia (spec §2.2 --
// "revisar 115 de a uno no lo hace nadie", cada fricción de más cuenta).
// Corregir y caducar SÍ lo son (Global Constraints del plan): el disparador
// de acá sólo abre el paso 1 (texto / confirmación); el padre decide la
// ventana.
const BADGE = 'inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wide'
const ACCION = `${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors disabled:opacity-50 disabled:pointer-events-none`
const ACCION_ACENTO = `${TAMANO_BOTON_ACCION} rounded bg-acento hover:bg-acento-hover text-sobre-color font-semibold transition-colors disabled:opacity-50 disabled:pointer-events-none`

function EstadoBadge({ hecho, t }) {
  if (hecho.vencido) return <span className={`${BADGE} bg-peligro-fondo text-peligro border border-peligro-borde`}>{t.memoria.vencido}</span>
  if (hecho.verificado) return <span className={`${BADGE} bg-exito-fondo text-exito border border-exito-borde`}>{t.memoria.verificado}</span>
  return <span className={`${BADGE} bg-aviso-fondo text-aviso border border-aviso-borde`}>{t.memoria.sinVerificar}</span>
}

export default function FichaDeHecho({
  hecho, resaltado, esSuperviviente, seleccionado, onToggleSeleccion,
  ocupado, onAprobar, onCorregir, onCaducar, onQuitarCaducidad,
}) {
  const { t, lang } = useI18n()
  const tipoLabel = t.memoria.tipos[hecho.tipo] || hecho.tipo
  const fecha = (iso) => (iso ? new Date(iso).toLocaleString(localeFor(lang)) : null)

  return (
    <div
      data-testid={`hecho-${hecho.id}`}
      className={`rounded-lg border bg-superficie p-3 space-y-2 ${resaltado ? 'border-aviso-borde' : 'border-borde'}`}
    >
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          aria-label={t.memoria.seleccionarHecho(hecho.id)}
          checked={seleccionado}
          onChange={onToggleSeleccion}
          className="mt-1 h-4 w-4 accent-acento"
        />
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-1">
            <span className="text-xs text-texto-tenue">#{hecho.id}</span>
            <span className={`${BADGE} bg-superficie-2 text-texto-suave`}>{tipoLabel}</span>
            <EstadoBadge hecho={hecho} t={t} />
            {esSuperviviente && (
              <span className={`${BADGE} bg-acento-fondo text-acento-texto`}>{t.memoria.sobrevive}</span>
            )}
          </div>
          <p className="text-sm text-texto-fuerte break-words">{hecho.texto}</p>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-texto-suave pl-6">
        <div>
          <dt className="text-texto-tenue">{t.memoria.procedencia.mensaje}</dt>
          <dd>{hecho.procedencia?.mensaje_id ?? t.memoria.procedencia.sinMensaje}</dd>
        </div>
        <div>
          <dt className="text-texto-tenue">{t.memoria.procedencia.faceta}</dt>
          <dd>{hecho.procedencia?.faceta ?? t.memoria.procedencia.sinFaceta}</dd>
        </div>
        <div>
          <dt className="text-texto-tenue">{t.memoria.confianza}</dt>
          <dd>{Math.round((hecho.confianza ?? 0) * 100)}%</dd>
        </div>
        <div>
          <dt className="text-texto-tenue">{t.memoria.creado}</dt>
          <dd>{fecha(hecho.creado_at) || '—'}</dd>
        </div>
        {hecho.verificado && (
          <div className="col-span-2">
            <dd>{t.memoria.verificadoPor(hecho.verificado_por)} · {fecha(hecho.verificado_at)}</dd>
          </div>
        )}
        <div className="col-span-2">
          <dd>{hecho.vence_at ? t.memoria.vence(fecha(hecho.vence_at)) : t.memoria.sinVencimiento}</dd>
        </div>
      </dl>

      <div className="flex flex-wrap gap-1.5 pl-6">
        {!hecho.verificado && (
          <button type="button" className={ACCION_ACENTO} disabled={ocupado} onClick={onAprobar}>
            {t.memoria.aprobar}
          </button>
        )}
        <button type="button" className={ACCION} disabled={ocupado} onClick={onCorregir}>
          {t.memoria.corregir}
        </button>
        {hecho.vencido ? (
          <button type="button" className={ACCION} disabled={ocupado} onClick={onQuitarCaducidad}>
            {t.memoria.quitarCaducidad}
          </button>
        ) : (
          <button type="button" className={ACCION} disabled={ocupado} onClick={onCaducar}>
            {t.memoria.caducar}
          </button>
        )}
      </div>
    </div>
  )
}
