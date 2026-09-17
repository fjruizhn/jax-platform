import { localeFor } from '../i18n/index.jsx'

// Montos en USD del pre-vuelo (spec 2026-09-17 §6.2): el backend los manda
// como string decimal; se muestran con el locale del idioma activo. Hasta 4
// decimales: un paso barato cuesta centavos de centavo.
// Revisión final (menor 6a): hacia ARRIBA (`roundingMode: 'ceil'`, Intl
// NumberFormat v3: Node 20+, Chrome 106+, Firefox 116+, Safari 15.4+). Son
// costos MÁXIMOS que el usuario consiente: mostrar menos que el real sería
// aparentar un tope más bajo, y "$0.00" para 0.000040 lo hacía.
export const OPCIONES_USD = {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4, roundingMode: 'ceil',
}
// El menor monto que se ve con maximumFractionDigits = 4.
const MINIMO_VISIBLE = 0.0001

export function formatearUsd(monto, lang) {
  if (monto === null || monto === undefined || monto === '') return null
  const numero = Number(monto)
  if (!Number.isFinite(numero)) return null
  // Un navegador sin roundingMode lo ignora y redondea al más cercano: un
  // monto positivo por debajo del mínimo visible se muestra como el mínimo,
  // nunca como $0.
  const visible = numero > 0 && numero < MINIMO_VISIBLE ? MINIMO_VISIBLE : numero
  return new Intl.NumberFormat(localeFor(lang), OPCIONES_USD).format(visible)
}
