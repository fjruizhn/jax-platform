"""Vida de los tokens: constantes SIN efectos al importarse (2026-09-17).

Viven aparte de auth/jwt.py porque auth/jwt.py exige JAX_JWT_SECRET al
importarse (fail-closed de la app: `uvicorn main:app` no arranca sin él), y
quien solo necesita la vida del access token -- ajustes.py, y por él
db/migrations.py -- no firma ni verifica nada. Las migraciones se corren sin
el secreto (job jacobs-gobernanza-db de jax).
"""

ACCESS_EXPIRE_SECONDS = 15 * 60
