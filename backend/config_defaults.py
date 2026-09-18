"""Valores por defecto de configuración, SIN capa de API.

Los usan tres caminos que no deben depender entre sí: la pantalla de admin
(`api/admin/config_admin.py`), el respaldo de `GET /api/apariencia` y la semilla de
`db/migrations.py`. La migración importaba el módulo de la API y eso arrastraba
`auth/jwt`, que exige `JAX_JWT_SECRET`: el job de CI que sólo construye el esquema
moría con `RuntimeError` antes de crear una tabla (2026-09-18). Una migración no
depende de la API.
"""

DEFAULT_CONFIG = {
    "theme_default": "dark",
}
