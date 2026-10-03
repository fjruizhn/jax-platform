import api from './client'

// Cliente de /api/proyectos (E1, T6). El backend es la autoridad: aquí solo se
// arma la petición. Los errores llegan como `detail.code` y cada pantalla los
// traduce con `t.proyectos.errores` (ver `codigoDe` en ./errores).

export async function listarProyectos({ vista = 'activos', antesDe = null, limite = 50 } = {}) {
  const { data } = await api.get('/proyectos', {
    params: { vista, antes_de: antesDe, limite },
  })
  return data
}

export async function crearProyecto({ nombre, descripcion = null }, idempotencyKey) {
  const { data } = await api.post(
    '/proyectos',
    { nombre, descripcion },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  return data
}

export async function verProyecto(id) {
  const { data } = await api.get(`/proyectos/${id}`)
  return data
}

// PUT reemplaza nombre Y descripción: las dos claves se mandan siempre
// (descripcion null la borra; omitirla no es una opción en la API).
export async function renombrarProyecto(id, { nombre, descripcion = null }) {
  const { data } = await api.put(`/proyectos/${id}`, { nombre, descripcion })
  return data
}

export async function cambiarEstado(id, estado) {
  const { data } = await api.post(`/proyectos/${id}/estado`, { estado })
  return data
}

export async function listarMiembros(id) {
  const { data } = await api.get(`/proyectos/${id}/miembros`)
  return data
}

export async function invitarMiembro(id, { email, papel }) {
  const { data } = await api.post(`/proyectos/${id}/miembros`, { email, papel })
  return data
}

export async function cambiarPapel(id, userId, papel) {
  const { data } = await api.put(`/proyectos/${id}/miembros/${userId}`, { papel })
  return data
}

export async function quitarMiembro(id, userId) {
  const { data } = await api.delete(`/proyectos/${id}/miembros/${userId}`)
  return data
}

export async function buscarCandidatos(id, q) {
  const { data } = await api.get(`/proyectos/${id}/candidatos`, { params: { q } })
  return data
}
