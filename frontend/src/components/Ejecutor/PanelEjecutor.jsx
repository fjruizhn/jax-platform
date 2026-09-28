import { memo, useEffect, useState } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { useEjecutor } from '../../store/useEjecutor'
import Maquinas from './Maquinas'
import PausaEjecutor from './PausaEjecutor'
import DetalleMision from './DetalleMision'
import Bitacora from './Bitacora'
import { textoDeErrorEjecutor, traducir } from './textos'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

// Panel del modo Ejecutor (SP2, 2026-09-17). Ocupa el centro en lugar de los
// mensajes mientras el modo está activo (CenterPanel). Al montar lee /estado
// y las misiones; al desmontar corta el polling.
function PanelEjecutor() {
  const { t, lang } = useI18n()
  const tx = t.ejecutor
  const estado = useEjecutor((s) => s.estado)
  const errorEstado = useEjecutor((s) => s.errorEstado)
  const misiones = useEjecutor((s) => s.misiones)
  const errorMisiones = useEjecutor((s) => s.errorMisiones)
  const idActiva = useEjecutor((s) => s.idActiva)
  const misionActiva = useEjecutor((s) => s.misionActiva)
  const bitacora = useEjecutor((s) => s.bitacora)
  const errorMision = useEjecutor((s) => s.errorMision)
  const errorEnvio = useEjecutor((s) => s.errorEnvio)
  const cargarEstado = useEjecutor((s) => s.cargarEstado)
  const cargarMisiones = useEjecutor((s) => s.cargarMisiones)
  const abrirMision = useEjecutor((s) => s.abrirMision)
  const nuevaMision = useEjecutor((s) => s.nuevaMision)
  const detenerPolling = useEjecutor((s) => s.detenerPolling)
  // Tipo Código (Task 11, 2026-09-28): repo, PR y estado de entrega en vez de
  // máquinas. `tipoNueva` es de esta pantalla nada más -- el backend no lo
  // pide hasta que se lanza la misión.
  const repos = useEjecutor((s) => s.repos)
  const errorRepos = useEjecutor((s) => s.errorRepos)
  const cargarRepos = useEjecutor((s) => s.cargarRepos)
  const crearMision = useEjecutor((s) => s.crearMision)
  const enviando = useEjecutor((s) => s.enviando)
  const [tipoNueva, setTipoNueva] = useState('servidor')
  const [repoId, setRepoId] = useState('')
  const [objetivoCodigo, setObjetivoCodigo] = useState('')

  useEffect(() => {
    cargarEstado()
    cargarMisiones()
    return () => detenerPolling()
  }, [cargarEstado, cargarMisiones, detenerPolling])

  useEffect(() => {
    if (tipoNueva === 'codigo') cargarRepos()
  }, [tipoNueva, cargarRepos])

  async function lanzarCodigo() {
    const objetivo = objetivoCodigo.trim()
    if (!repoId || !objetivo) return
    const aceptada = await crearMision({ tipo: 'codigo', repoId: Number(repoId), objetivo })
    if (aceptada) {
      setRepoId('')
      setObjetivoCodigo('')
    }
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
      <h2 className="text-sm font-semibold text-texto-fuerte">{tx.titulo}</h2>

      {errorEnvio && <p role="alert" className="text-xs text-peligro">{textoDeErrorEjecutor(t, errorEnvio)}</p>}

      <div role="radiogroup" aria-label={tx.nuevaMision} className="flex items-center gap-4 text-xs">
        <label htmlFor="ejecutor-tipo-servidor" className="flex items-center gap-1.5 text-texto">
          <input
            id="ejecutor-tipo-servidor"
            type="radio"
            name="ejecutor-tipo-mision"
            checked={tipoNueva === 'servidor'}
            onChange={() => setTipoNueva('servidor')}
          />
          {tx.tipoServidor}
        </label>
        <label htmlFor="ejecutor-tipo-codigo" className="flex items-center gap-1.5 text-texto">
          <input
            id="ejecutor-tipo-codigo"
            type="radio"
            name="ejecutor-tipo-mision"
            checked={tipoNueva === 'codigo'}
            onChange={() => setTipoNueva('codigo')}
          />
          {tx.tipoCodigo}
        </label>
      </div>

      {errorEstado && <p className="text-xs text-peligro">{textoDeErrorEjecutor(t, errorEstado)}</p>}
      {!estado && !errorEstado && <p className="text-xs text-texto-suave">{tx.cargando}</p>}
      {estado && (
        <div className="grid gap-4 md:grid-cols-2">
          {tipoNueva === 'codigo' ? (
            <section aria-labelledby="ejecutor-repo" className="rounded-lg border border-borde bg-superficie p-3 space-y-2">
              <h3 id="ejecutor-repo" className="text-xs font-semibold text-texto-fuerte">{tx.repo}</h3>
              {errorRepos && <p className="text-xs text-peligro">{textoDeErrorEjecutor(t, errorRepos)}</p>}
              <select
                aria-label={tx.repo}
                value={repoId}
                onChange={(e) => setRepoId(e.target.value)}
                className="w-full bg-superficie border border-borde-control rounded px-2 py-1 text-xs text-texto"
              >
                <option value="">{tx.elegirRepo}</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>{r.owner_repo}</option>
                ))}
              </select>
              <textarea
                aria-label={tx.placeholderNueva}
                value={objetivoCodigo}
                onChange={(e) => setObjetivoCodigo(e.target.value)}
                placeholder={tx.placeholderNueva}
                rows={2}
                className="w-full bg-superficie border border-borde-control rounded px-2 py-1 text-xs text-texto placeholder-texto-tenue resize-none"
              />
              <button
                type="button"
                onClick={lanzarCodigo}
                disabled={!repoId || !objetivoCodigo.trim() || enviando}
                className={`${TAMANO_BOTON_ACCION} rounded bg-modo-ejecutor text-sobre-color font-semibold disabled:opacity-40`}
              >
                {tx.lanzar}
              </button>
            </section>
          ) : (
            <Maquinas estado={estado} />
          )}
          <PausaEjecutor pausa={estado.pausa} />
        </div>
      )}

      <section aria-labelledby="ejecutor-misiones" className="rounded-lg border border-borde bg-superficie p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 id="ejecutor-misiones" className="text-xs font-semibold text-texto-fuerte">{tx.misionesTitulo}</h3>
          <button type="button" onClick={nuevaMision} className={`${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte font-semibold`}>
            {tx.nuevaMision}
          </button>
        </div>
        {errorMisiones && <p className="text-xs text-peligro">{textoDeErrorEjecutor(t, errorMisiones)}</p>}
        {misiones.length === 0 ? (
          <p className="text-xs text-texto-suave">{tx.sinMisiones}</p>
        ) : (
          <ul className="space-y-1">
            {misiones.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  aria-current={m.id === idActiva ? 'true' : undefined}
                  onClick={() => abrirMision(m.id)}
                  className={`w-full text-left px-2 py-1 rounded border text-xs ${m.id === idActiva ? 'border-foco text-texto-fuerte' : 'border-transparent text-texto hover:border-borde'}`}
                >
                  <span className="font-semibold">{m.objetivo}</span>
                  <span className="text-texto-suave">
                    {' · '}{traducir(tx.estadosMision, m.estado)}
                    {' · '}{tx.turnosContados(m.turnos ?? 0)}
                    {m.created_at && <>{' · '}{new Date(m.created_at).toLocaleString(localeFor(lang))}</>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="ejecutor-detalle" className="space-y-3">
        <h3 id="ejecutor-detalle" className="text-xs font-semibold text-texto-fuerte">{tx.detalleTitulo}</h3>
        {errorMision && <p className="text-xs text-peligro">{textoDeErrorEjecutor(t, errorMision)}</p>}
        {misionActiva ? <DetalleMision mision={misionActiva} /> : !idActiva && <p className="text-xs text-texto-suave">{tx.sinMisionAbierta}</p>}
      </section>

      {idActiva && <Bitacora eventos={bitacora} />}
    </div>
  )
}

export default memo(PanelEjecutor)
