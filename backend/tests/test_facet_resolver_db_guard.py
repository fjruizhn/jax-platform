"""_db_conn NO cae en silencio a localhost:3306.

POR QUE EXISTE, con el incidente que lo motivo: hay dos instancias de MariaDB
en el historial de esta maquina, 3306 y 3308, y la de 3306 esta MUERTA (ver la
memoria jax-dual-mariadb-instances). El espejo de jax/core ya tenia este guard;
esta copia NO lo tenia y caia a `localhost:3306` por default silencioso --
mismo bug, arreglado en un espejo y no en el otro. Detectado 2026-09-01 por
scripts/check_facet_resolver_sync.py, que estaba en rojo en master y no corria
en ningun workflow.

El test que importa es el negativo: sin las variables, DEBE reventar ruidoso.
Un default silencioso a una instancia muerta no falla, se conecta a la nada.
"""
import os
import pytest

from facet_resolver import _db_conn


@pytest.mark.asyncio
@pytest.mark.parametrize("faltante", ["JAX_DB_HOST", "JAX_DB_PORT", "ambas"])
async def test_db_conn_falla_ruidoso_sin_host_o_puerto(monkeypatch, faltante):
    monkeypatch.setenv("JAX_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("JAX_DB_PORT", "3308")
    if faltante in ("JAX_DB_HOST", "ambas"):
        monkeypatch.delenv("JAX_DB_HOST", raising=False)
    if faltante in ("JAX_DB_PORT", "ambas"):
        monkeypatch.delenv("JAX_DB_PORT", raising=False)

    with pytest.raises(RuntimeError) as exc:
        await _db_conn()

    msg = str(exc.value)
    assert "JAX_DB_HOST" in msg and "JAX_DB_PORT" in msg
    # El mensaje nombra la causa, no solo el sintoma: sin esto alguien
    # vuelve a poner el default "por comodidad".
    assert "3306" in msg


@pytest.mark.asyncio
async def test_db_conn_no_tiene_default_a_3306_en_el_codigo():
    """Guard estructural: aunque alguien re-agregue un default, este test lo ve."""
    import inspect
    import facet_resolver
    src = inspect.getsource(facet_resolver._db_conn)
    assert 'os.getenv("JAX_DB_HOST", ' not in src, "volvio el default silencioso de host"
    assert 'os.getenv("JAX_DB_PORT", ' not in src, "volvio el default silencioso de puerto"
