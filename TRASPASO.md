# Traspaso PR #199

- Objetivo: cerrar hallazgos ronda 2 de atomicidad del contrato de dispatch.
- Hecho: el endpoint usa `db.transaccion.transaccion(AISLAMIENTO_ADMIN)`; se añadió una regresión de rollback por fallo real del INSERT de auditoría y se adaptaron pruebas unitarias del helper.
- Falta: rebasear sobre `origin/master` actual, medir/actualizar el piso acumulado, ejecutar CI con MariaDB efímera y auditoría escalón 3. La DB local de MariaDB está en una instancia de producción y no se usó.
- Decisión: no tocar producción ni `PENDIENTES.md`, según el encargo. CI será el verificador de DB real.
- Siguiente comando: `git fetch origin master && git rebase origin/master`
