# Traspaso: identidad de facet_binding tras approve

- **Objetivo:** mantener `facet_binding.provider_id` y `model_id` alineados con el `model_ref` aprobado.
- **Hecho:** añadida una prueba unitaria DB-free que verifica que el escritor de aprobación copia los identificadores desde la fila bloqueada de `model`; añadida una aserción al test DB-backed existente; el endpoint llama el escritor dentro de la transacción de aprobación.
- **Falta:** DB-backed local no se ejecutó porque no se usó MariaDB de producción; se omitió bajo `JAX_CI_NO_DB=1`. Falta revisión de Hyde.
- **Decisiones:** conservar las columnas heredadas; actualizar en el mismo UPDATE vía JOIN sobre la fila `model`.
- **Siguiente comando exacto:** `PYTHONPATH=backend JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml python3 -m pytest -q backend/tests/test_approve_proposal_binding_columns.py backend/tests/test_binding_aplicado_auditado.py backend/tests/test_admin_models_endpoints.py`
