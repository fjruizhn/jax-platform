# Traspaso PR #201

- Objetivo: cerrar hallazgos ronda 2 de loops de fondo bajo pytest.
- Hecho: detección compartida en `backend/pytest_guard.py`, cinco consumidores migrados, pruebas actualizadas y registro histórico corregido.
- Falta: rebasear sobre `origin/master` actual, medir/actualizar el piso acumulado, CI y auditoría escalón 3.
- Decisión: CARGA queda fuera de alcance, sin métricas; no tocar producción ni `PENDIENTES.md`.
- Siguiente comando: `git fetch origin master && git rebase origin/master`
