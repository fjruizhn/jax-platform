# Versión y nombre del sistema

**Versión.** Vive en un solo lugar: el archivo `VERSION` de la raíz del repo (hoy `2.5`; será `3.0` con LAS VOCES).
- Frontend: `frontend/vite.config.js` lo lee al compilar y define `__APP_VERSION__`. `package.json` y el lock no llevan `version` (el paquete es privado).
- Backend: `backend/app_version.py` lo lee al arrancar para `FastAPI(version=...)`; si falta o no es `X.Y[.Z]`, el arranque falla.
- Los textos de i18n no llevan número: `platformLabel(nombre, v)` y `adminVersion(nombre, v)` lo reciben.

**Nombre del sistema.** Sale del ajuste `system_name` (Administración, Configuración), guardado en `localStorage` como `jax_system_name` y leído con `useNombreDelSistema`. Sin él, la marca de respaldo es `brandName` de i18n (única «Axioma» permitida en i18n). El título de la pestaña y la meta description los arma `components/TituloDePagina.jsx` con las plantillas `tituloPagina` y `metaDescripcion`; `index.html` es solo el respaldo inicial.

**Quién lo vigila** (`frontend/src/version.test.jsx`, `components/TituloDePagina.test.jsx`, `backend/tests/test_version_unica.py`): falla si aparece una versión fija en `src/`, la palabra «Axioma» en i18n (salvo `brandName`), `LeftPanel` o `AdminSidebar`, una `version` en `package.json`/lock, si `app.version` no sale de `VERSION`, o si el `<title>` de `index.html` difiere de `brandName`.
