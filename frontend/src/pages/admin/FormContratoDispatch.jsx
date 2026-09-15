import { useId, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe } from '../../api/errores'
import AlertaError from '../../components/AlertaError'

// PR-L (2026-09-14, Ruling 33): declarar el contrato de dispatch
// (max_tokens_param + max_output_tokens) de UNA fila de `model`. Es el remedio
// del 409 `modelo_sin_contrato_de_dispatch` de PR-J, que hasta hoy era un
// UPDATE a mano. El formulario no valida por su cuenta: el backend corre los
// MISMOS validadores que el dispatch y responde 422 `contrato_dispatch_invalido`
// con las columnas. Las opciones del parámetro vienen del backend (`opciones`).
export default function FormContratoDispatch({ modelo, opciones, onGuardado, onCancelar }) {
  const { t } = useI18n()
  const idParam = useId()
  const idTope = useId()
  const [param, setParam] = useState(modelo.max_tokens_param ?? '')
  const [tope, setTope] = useState(modelo.max_output_tokens != null ? String(modelo.max_output_tokens) : '')
  const [guardando, setGuardando] = useState(false)
  // El error crudo: se traduce al renderizar, así un cambio de idioma lo sigue.
  const [error, setError] = useState(null)

  async function guardar(e) {
    e.preventDefault()
    setGuardando(true)
    setError(null)
    try {
      await api.put(`/admin/models/${modelo.id}/contrato-dispatch`, {
        // Vacío va null, nunca 0 ni un default: que lo rechace el validador.
        max_tokens_param: param === '' ? null : param,
        max_output_tokens: tope.trim() === '' ? null : Number(tope),
      })
      onGuardado()
    } catch (err) {
      setError(err)
    } finally {
      setGuardando(false)
    }
  }

  function textoDeError(err) {
    if (codigoDe(err) === 'contrato_dispatch_invalido') {
      return t.contrato_dispatch_invalido((err.response.data.detail.campos || []).join(', '))
    }
    return t.adminContratoError
  }

  return (
    // noValidate: la validación nativa del navegador (min/step) sería una
    // segunda regla y taparía el 422 del validador del dispatch, que es la única.
    <form noValidate onSubmit={guardar} className="rounded-lg border border-borde bg-hundido p-4 mb-6">
      <h3 className="text-xs font-semibold text-texto mb-1">
        {t.adminContratoTitulo(`${modelo.provider_id}/${modelo.model_id}`)}
      </h3>
      <p className="text-xs text-texto-tenue mb-3">{t.adminContratoAyuda}</p>
      <div className="flex flex-wrap items-end gap-3 mb-3">
        <label htmlFor={idParam} className="flex flex-col gap-1 text-xs text-texto-suave">
          {t.adminContratoParam}
          <select
            id={idParam}
            value={param}
            onChange={e => setParam(e.target.value)}
            className="bg-superficie border border-borde-control rounded px-2 py-1 text-xs text-texto font-mono"
          >
            <option value="">{t.adminContratoElegir}</option>
            {opciones.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label htmlFor={idTope} className="flex flex-col gap-1 text-xs text-texto-suave">
          {t.adminContratoTope}
          <input
            id={idTope}
            type="number"
            min="1"
            step="1"
            value={tope}
            onChange={e => setTope(e.target.value)}
            className="bg-superficie border border-borde-control rounded px-2 py-1 text-xs text-texto font-mono"
          />
        </label>
        <button
          type="submit"
          disabled={guardando}
          className="text-xs px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color font-semibold disabled:opacity-50 transition-colors"
        >
          {guardando ? t.adminContratoGuardando : t.adminContratoGuardar}
        </button>
        <button
          type="button"
          onClick={onCancelar}
          className="text-xs px-3 py-1.5 rounded-lg text-texto-suave hover:text-texto transition-colors"
        >
          {t.adminContratoCancelar}
        </button>
      </div>
      {error && <AlertaError className="text-xs">{textoDeError(error)}</AlertaError>}
    </form>
  )
}
