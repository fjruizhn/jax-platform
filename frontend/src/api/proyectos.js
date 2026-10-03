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

// ---- Documentos del proyecto (E2a, T8) ----
// Los topes y las extensiones aceptadas NO viven aquí: los publica el backend.

export async function limitesDeDocumentos() {
  const { data } = await api.get('/proyectos/documentos/limites')
  return data
}

// Una sola petición con el campo `archivos` repetido. Para una carpeta el nombre
// es la ruta relativa (`webkitRelativePath`); sin ella, el nombre del archivo.
// Sin Content-Type a mano: el navegador pone el boundary. `onProgreso` recibe
// 0..1 y solo cuando axios conoce el total (sin total no se inventa una fracción).
export async function subirDocumentos(id, archivos, { onProgreso } = {}) {
  const cuerpo = new FormData()
  for (const f of archivos) cuerpo.append('archivos', f, f.webkitRelativePath || f.name)
  const { data } = await api.post(`/proyectos/${id}/documentos`, cuerpo, {
    onUploadProgress: (e) => {
      if (onProgreso && e.total) onProgreso(Math.min(1, e.loaded / e.total))
    },
  })
  return data
}

export async function listarDocumentos(id, { vista = 'visibles', antesDe = null, limite = 50 } = {}) {
  const { data } = await api.get(`/proyectos/${id}/documentos`, {
    params: { vista, antes_de: antesDe, limite },
  })
  return data
}

export async function ocultarDocumento(id, doc) {
  const { data } = await api.post(`/proyectos/${id}/documentos/${doc}/ocultar`)
  return data
}

export async function restaurarDocumento(id, doc) {
  const { data } = await api.post(`/proyectos/${id}/documentos/${doc}/restaurar`)
  return data
}

// Vuelve a poner en la cola un documento sin_extractor o con error (el original ya está en el servidor).
export async function reprocesarDocumento(id, doc) {
  const { data } = await api.post(`/proyectos/${id}/documentos/${doc}/reprocesar`)
  return data
}
