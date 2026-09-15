import { codigoDe } from '../../api/errores'

// Errores de la administración de usuarios (2026-09-15, admin usuarios etapa 3).
// El backend responde un código estable en `detail` (string, o {code, server}
// cuando hay una respuesta de un servidor externo que mostrar); el código lo
// saca `codigoDe` (api/errores.js, compartido). Acá solo se traduce: un código
// sin traducción cae en el mensaje genérico, nunca se muestra crudo.
export function mensajeDeError(t, err) {
  const code = codigoDe(err)
  const base = (code && t.adminErrors[code]) || t.adminErrorGeneric
  const servidor = err?.response?.data?.detail?.server
  return servidor ? `${base} ${t.smtpServerSaid(servidor)}` : base
}
