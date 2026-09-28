import { useId, useState } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe } from '../../api/errores'
import Dialogo from '../../components/Dialogo'
import AlertaError from '../../components/AlertaError'

// Modal de programación del sync (2026-09-27, pedido de Fernando): encender
// /apagar y elegir cada cuánto corre, sobre el Dialogo único del repo --
// NUNCA confirm()/alert()/prompt() (decisión de Fernando, 2026-09-15).
const UNIDADES = ['horas', 'dias', 'semanas', 'meses']
const UNIDAD_KEY = {
  horas: 'adminModelsProgramacionUnidadHoras',
  dias: 'adminModelsProgramacionUnidadDias',
  semanas: 'adminModelsProgramacionUnidadSemanas',
  meses: 'adminModelsProgramacionUnidadMeses',
}

export default function DialogoProgramacionSync({ config, onGuardado, onCerrar }) {
  const { t, lang } = useI18n()
  const idHabilitado = useId()
  const idValor = useId()
  const idUnidad = useId()

  // MINOR-3 (cuarta ronda de la auditoría adversarial, 2026-09-28): un
  // `<select>` CONTROLADO con un `value` que no matchea NINGÚN `<option>`
  // (una `cada_unidad` corrupta, fuera de UNIDADES) cae al PRIMER `<option>`
  // en pantalla ("horas") -- pero el estado de React sigue teniendo el
  // valor corrupto. Mostraba "horas" mintiendo: parecía una config válida
  // (y guardable tal cual) cuando no lo era. Con la config inválida o la
  // unidad desconocida, el estado arranca VACÍO -- el `<select>` lo
  // refleja con un placeholder sin elegir, y Guardar queda deshabilitado
  // hasta que se elija de verdad.
  const configUtilizable = config.valida !== false && UNIDADES.includes(config.cada_unidad)

  const [habilitado, setHabilitado] = useState(config.habilitado)
  const [cadaValor, setCadaValor] = useState(configUtilizable ? String(config.cada_valor) : '')
  const [cadaUnidad, setCadaUnidad] = useState(configUtilizable ? config.cada_unidad : '')
  const [guardando, setGuardando] = useState(false)
  const [error, setError] = useState(null)

  async function guardar(e) {
    e.preventDefault()
    setGuardando(true)
    setError(null)
    try {
      const { data } = await api.put('/admin/models/sync/config', {
        habilitado,
        cada_valor: Number(cadaValor),
        cada_unidad: cadaUnidad,
      })
      onGuardado(data)
    } catch (err) {
      setError(err)
    } finally {
      setGuardando(false)
    }
  }

  function textoDeError(err) {
    if (codigoDe(err) === 'catalogo_sync_config_invalida') {
      return t.catalogo_sync_config_invalida(err.response.data.detail.campo)
    }
    return t.adminModelsProgramacionError
  }

  function fechaLocal(iso) {
    return new Date(iso).toLocaleString(localeFor(lang))
  }

  return (
    <Dialogo idTitulo="programacion-sync-titulo" titulo={t.adminModelsProgramacionTitulo} onCerrar={onCerrar}>
      <form onSubmit={guardar}>
        {/* MINOR-2 (tercera ronda de la auditoría adversarial, 2026-09-27):
            GET /sync/config ya no da 500 ante una fila corrupta -- manda
            `valida: false` para que un operador la vea y la repare desde
            acá (el PUT sabe reparar: audita el "antes" tal cual, marcado
            inválido, y aplica el "después" ya validado). */}
        {config.valida === false && (
          <p role="alert" className="text-xs text-aviso mb-4">{t.adminModelsProgramacionConfigInvalida}</p>
        )}

        <label htmlFor={idHabilitado} className="flex items-center gap-2 mb-4 text-sm text-texto">
          <input
            id={idHabilitado}
            type="checkbox"
            role="checkbox"
            aria-label={t.adminModelsProgramacionActivo}
            checked={habilitado}
            onChange={e => setHabilitado(e.target.checked)}
            className="h-4 w-4"
          />
          {t.adminModelsProgramacionActivo}
        </label>

        <div className="flex items-end gap-3 mb-4">
          <label htmlFor={idValor} className="flex flex-col gap-1 text-xs text-texto-suave">
            {t.adminModelsProgramacionCada}
            <input
              id={idValor}
              type="number"
              min="1"
              step="1"
              value={cadaValor}
              onChange={e => setCadaValor(e.target.value)}
              disabled={!habilitado}
              className="bg-superficie border border-borde-control rounded px-2 py-1 text-sm text-texto w-24 disabled:opacity-50"
            />
          </label>
          <label htmlFor={idUnidad} className="flex flex-col gap-1 text-xs text-texto-suave">
            {t.adminModelsProgramacionUnidadEtiqueta}
            <select
              id={idUnidad}
              value={cadaUnidad}
              onChange={e => setCadaUnidad(e.target.value)}
              disabled={!habilitado}
              className="bg-superficie border border-borde-control rounded px-2 py-1 text-sm text-texto disabled:opacity-50"
            >
              {cadaUnidad === '' && (
                <option value="" disabled>{t.adminModelsProgramacionUnidadElegir}</option>
              )}
              {UNIDADES.map(u => <option key={u} value={u}>{t[UNIDAD_KEY[u]]}</option>)}
            </select>
          </label>
        </div>

        <p className="text-xs text-texto-tenue mb-4">
          {!habilitado
            ? t.adminModelsProgramacionApagado
            : config.proxima_corrida_estimada
              ? t.adminModelsProgramacionProximaCorrida(fechaLocal(config.proxima_corrida_estimada))
              : t.adminModelsProgramacionProximaCorridaNinguna}
        </p>

        {/* MINOR-4 (auditoría adversarial, 2026-09-27): apagar el sync
            también apaga los avisos de facetas en riesgo y de modelos
            nuevos (esos avisos sólo salen de una corrida real -- sin
            corridas, no hay de dónde sacarlos). */}
        {!habilitado && (
          <p role="alert" className="text-xs text-aviso mb-4">{t.adminModelsProgramacionApagadoAvisos}</p>
        )}

        {error && <AlertaError className="text-xs mb-3">{textoDeError(error)}</AlertaError>}

        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={guardando || cadaValor === '' || cadaUnidad === ''}
            className="text-xs px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color font-semibold disabled:opacity-50 transition-colors"
          >
            {guardando ? t.adminModelsProgramacionGuardando : t.adminModelsProgramacionGuardar}
          </button>
          <button
            type="button"
            onClick={onCerrar}
            className="text-xs px-3 py-1.5 rounded-lg text-texto-suave hover:text-texto transition-colors"
          >
            {t.adminModelsProgramacionCancelar}
          </button>
        </div>
      </form>
    </Dialogo>
  )
}
