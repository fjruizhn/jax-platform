import { codigoDe } from '../../api/errores'

// Errores de la administración de usuarios (2026-09-15, admin usuarios etapa 3;
// etapa 4 agrega el fallback a t.smtpErrors, C4-2). El backend responde un
// código estable en `detail` (string, o {code, server} cuando hay una
// respuesta de un servidor externo que mostrar); el código lo saca `codigoDe`
// (api/errores.js, compartido). Un código de "Enviar enlace" puede venir de
// las rutas SMTP compartidas (admin/smtp): si no está en t.adminErrors se
// busca en t.smtpErrors antes del mensaje genérico, sin duplicar esos textos.
// Nunca se muestra el código crudo.
export function mensajeDeError(t, err) {
  const code = codigoDe(err)
  const base = (code && (t.adminErrors[code] || t.smtpErrors[code])) || t.adminErrorGeneric
  const servidor = err?.response?.data?.detail?.server
  return servidor ? `${base} ${t.smtpServerSaid(servidor)}` : base
}
