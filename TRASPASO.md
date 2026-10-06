# Traspaso: loops de fondo fuera de pytest

- **Objetivo:** impedir que el despachador inmediato de documentos y la limpieza periódica de adjuntos arranquen en pruebas automáticamente.
- **Hecho:** ambos escritores consultan una detección estructural (`PYTEST_CURRENT_TEST` o `pytest` ya importado), registran aviso y retornan; `forzado=True` permite ejercitarlos deliberadamente. Pruebas RED-GREEN añadidas.
- **Falta:** el pendiente también nombra helpers y módulo común de `loadtest/binding_escritores_orquestar.py`; esa implementación está en el worktree ajeno `/home/fruiz/worktrees/jax-carga-binding` y no se tocó a la espera de coordinación. No se midió `pool maxsize` en producción porque este encargo prohíbe tocar producción.
- **Decisiones:** se mantienen juntas las dos guardias por ser el mismo contrato de arranque bajo pytest. No se cambió el pool.
- **Siguiente comando exacto:** `PYTHONPATH=backend JAX_CI_NO_DB=1 JAX_REPO_PATH=/home/fruiz/jax JAX_CONFIG_PATH=/home/fruiz/jax/config/config.toml python3 -m pytest -q backend/tests/test_adjuntos_almacen.py backend/tests/test_proyectos_documentos_despachador.py`
