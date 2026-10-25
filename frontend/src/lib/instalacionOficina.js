// OP 358 pieza 1 — la raíz del dominio de la Oficina Virtual recibe el login
// de la oficina, no la Mesa de Axioma. Este mismo build se sirve en ambos
// dominios: la lista de hosts de la oficina se declara AL CONSTRUIR
// (VITE_OFFICE_HOSTS, separados por coma), así el mismo artefacto sirve la
// instalación de Axioma (lista vacía u host ausente) y la de la oficina sin
// dominios hardcodeados — pieza «des-axiomar» (on-prem compila con su host).
const HOSTS = (import.meta.env.VITE_OFFICE_HOSTS ?? '')
  .split(',').map((host) => host.trim().toLowerCase()).filter(Boolean)

// Comparación por host EXACTO: ni subdominios ni sufijos — un host malicioso
// que contenga el nombre no hereda el modo oficina.
export const esInstalacionDeOficina = (host = window.location.hostname) =>
  HOSTS.includes(String(host).toLowerCase())
