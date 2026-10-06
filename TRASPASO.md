# Traspaso PR #199

- Objetivo: cerrar hallazgos ronda 2 de atomicidad del contrato de dispatch.
- Hecho: el endpoint usa `db.transaccion.transaccion(AISLAMIENTO_ADMIN)`; se añadió una regresión de rollback por fallo real del INSERT de auditoría y se adaptaron pruebas unitarias del helper.
- Hecho adicional: rebase sobre `origin/master` `d283d900`; pisos acumulados de esta etapa fijados en 3832/2360 según la medición base de CI 3830/2358 y el encargo.
- Falta: CI con MariaDB efímera y auditoría escalón 3.
- Decisión: no tocar producción ni `PENDIENTES.md`, según el encargo. CI será el verificador de DB real.
- Siguiente comando: `git fetch origin master && git rebase origin/master`
- Verificación local final: `pytest tests/test_contrato_dispatch_transaction.py tests/test_contrato_dispatch_admin.py` en `JAX_CI_NO_DB=1` → 2 passed, 22 skipped (requieren DB); py_compile y diff --check pasan.
