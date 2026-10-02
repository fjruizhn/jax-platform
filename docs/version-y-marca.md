# Versión y nombre del sistema

**Versión.** Vive en un solo lugar: el archivo `VERSION` de la raíz del repo (hoy `2.5`; será `3.0` con LAS VOCES).
- Backend: `backend/app_version.py` la lee al arrancar (valida `X.Y[.Z]`; si falta o es inválida, el arranque falla) para `FastAPI(version=...)`, y `GET /api/version` (exige sesión, como las demás rutas de usuario; `api/version.py`) devuelve esa misma versión sin tocar el disco por request.
- Frontend: la pide a `/api/version` en tiempo de ejecución, una vez y solo con sesión iniciada (no la pide antes del login), y la cachea (`store/useVersion.js`). No se compila: `vite.config.js` no lee `VERSION`, y `package.json`/lock no llevan `version`. Mientras carga o si falla, la etiqueta muestra solo el nombre, nunca un número de respaldo.
- Los textos de i18n no llevan número: `platformLabel(nombre, v)` y `adminVersion(nombre, v)` lo reciben.

**Al cambiar `VERSION`.** Se edita el archivo y se reinicia `jax-platform`. No hace falta reiniciar el frontend ni recompilar ni volver a publicar el sitio. Las pestañas ya abiertas muestran la versión vieja hasta que se recargan.

**Nombre del sistema.** Sale del ajuste `system_name` (Administración, Configuración), guardado en `localStorage` como `jax_system_name` y leído con `useNombreDelSistema`. Sin él, la marca de respaldo es `brandName` de i18n (única «Axioma» permitida en i18n). El título de la pestaña y la meta description los escribe únicamente `components/TituloDePagina.jsx` con las plantillas `tituloPagina` y `metaDescripcion` (el store no toca `document.title`); `index.html` es solo el respaldo inicial.

**Qué garantiza la guarda** (`frontend/src/version.test.jsx`, `components/TituloDePagina.test.jsx`, `backend/tests/test_version_unica.py`):
- Inicio y Administración muestran la versión que responde `/api/version`: la prueba simula `9.9.9` (distinta de la real) y exige verla, así que un número escrito a mano en el componente falla. Sin respuesta, solo el nombre.
- Backend: con un `VERSION` falso de `9.9.9`, `main.app.version` y `GET /api/version` devuelven `9.9.9`, y el endpoint da 401 sin token y 200 con sesión.
- No hay versión fija en `src/` ni `__APP_VERSION__`, ni «Axioma» en i18n (salvo `brandName`), `LeftPanel` o `AdminSidebar` (detector por texto, complementario: no cubre otros archivos ni otras marcas).
- `package.json`/lock sin `version`; el `<title>` de `index.html` es `brandName`.
- NO garantiza que `VERSION` tenga el valor «correcto», solo que todo lo lee de ahí y que el formato es válido (lo valida el backend al arrancar).
