# Versión y nombre del sistema

**Versión.** Vive en un solo lugar: el archivo `VERSION` de la raíz del repo (hoy `2.5`; será `3.0` con LAS VOCES).
- Frontend: `frontend/vite.config.js` la lee con `frontend/leerVersion.js` al compilar y define `__APP_VERSION__`. Si `VERSION` falta, está vacío o no es `X.Y[.Z]`, el build y el dev fallan con un mensaje claro. `package.json` y el lock no llevan `version` (el paquete es privado).
- Backend: `backend/app_version.py` la lee al arrancar para `FastAPI(version=...)`, con la misma validación.
- Los textos de i18n no llevan número: `platformLabel(nombre, v)` y `adminVersion(nombre, v)` lo reciben.

**Al cambiar `VERSION`.** Ambos lados la leen al arrancar/compilar, no en caliente: hay que reiniciar `jax-platform` y `jax-platform-frontend` y recompilar el sitio público (`npm run build` y el despliegue a mano).

**Nombre del sistema.** Sale del ajuste `system_name` (Administración, Configuración), guardado en `localStorage` como `jax_system_name` y leído con `useNombreDelSistema`. Sin él, la marca de respaldo es `brandName` de i18n (única «Axioma» permitida en i18n). El título de la pestaña y la meta description los escribe únicamente `components/TituloDePagina.jsx` con las plantillas `tituloPagina` y `metaDescripcion` (el store no toca `document.title`); `index.html` es solo el respaldo inicial.

**Qué garantiza la guarda** (`frontend/src/version.test.jsx`, `components/TituloDePagina.test.jsx`, `backend/tests/test_version_unica.py`):
- Inicio y Administración muestran la versión que reciben por `__APP_VERSION__`: la prueba inyecta `9.9.9` (distinta de la real) y exige verla, así que un número escrito a mano en el componente falla aunque coincida con `VERSION`. Igual en backend: con un `VERSION` falso de `9.9.9`, `main.app.version` tiene que ser `9.9.9`.
- No hay versión fija en `src/` ni «Axioma» en i18n (salvo `brandName`), `LeftPanel` o `AdminSidebar` (detector por texto, complementario: no cubre otros archivos ni otras marcas).
- `package.json`/lock sin `version`; el `<title>` de `index.html` es `brandName`.
- NO garantiza que `VERSION` tenga el valor «correcto», solo que todo lo lee de ahí y que el formato es válido.
