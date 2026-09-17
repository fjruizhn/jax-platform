import { localeFor } from '../i18n/index.jsx'

// Montos en USD del pre-vuelo (spec 2026-09-17 §6.2): el backend los manda
// como string decimal; se muestran con el locale del idioma activo. Hasta 4
// decimales: un paso barato cuesta centavos de centavo.
export const OPCIONES_USD = { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 }

export function formatearUsd(monto, lang) {
  if (monto === null || monto === undefined || monto === '') return null
  const numero = Number(monto)
  if (!Number.isFinite(numero)) return null
  return new Intl.NumberFormat(localeFor(lang), OPCIONES_USD).format(numero)
}
