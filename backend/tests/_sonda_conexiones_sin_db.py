"""Sonda de `test_conftest_ci_sin_db_no_conecta.py`: corre SOLO como subproceso, bajo
`JAX_CI_NO_DB=1`. El nombre no empieza con `test_` a proposito: pytest no la colecta sola."""
import asyncio

import pytest


@pytest.mark.parametrize("modulo", ["facet_resolver", "credential_resolver"])
def test_db_conn_directo_no_llega_a_la_red(modulo):
    import importlib
    resolver = importlib.import_module(modulo)
    with pytest.raises(ConnectionRefusedError, match="JAX_CI_NO_DB"):
        asyncio.run(resolver._db_conn())
