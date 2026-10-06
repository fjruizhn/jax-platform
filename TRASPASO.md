# Traspaso: declarar contrato de dispatch atómico

- **Objetivo:** hacer atómico el `UPDATE model` y el `INSERT model_catalog_audit` de `PUT /api/admin/models/{id}/contrato-dispatch`.
- **Hecho:** añadidas pruebas unitarias sin DB que exigen `begin()` antes de la lectura/escritura y `rollback()` si falla la auditoría. El endpoint abre la transacción antes del `SELECT ... FOR UPDATE`, confirma tras ambas escrituras y revierte cualquier excepción.
- **Falta:** ejecutar la suite DB-backed en una base de test aislada; completar revisión, push y abrir PR para Hyde. Las pruebas existentes que usan `client` se saltaron bajo `JAX_CI_NO_DB=1`.
- **Decisiones:** Fernando indicó no usar la base de producción; no se leyó `/etc/jax/.env` ni se abrió ninguna conexión MariaDB. Se conservaron las columnas heredadas `facet_binding.provider_id/model_id`; no son parte de este pendiente.
- **Siguiente comando exacto:** `PYTHONPATH=backend JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml python3 -m pytest -q backend/tests/test_contrato_dispatch_transaction.py backend/tests/test_contrato_dispatch_admin.py backend/tests/test_contrato_dispatch_al_aprobar.py`
