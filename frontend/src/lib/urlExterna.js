export function urlExternaSegura(valor) {
  if (typeof valor !== 'string') return null
  try {
    const url = new URL(valor)
    return url.protocol === 'https:' || url.protocol === 'http:' ? valor : null
  } catch {
    return null
  }
}
