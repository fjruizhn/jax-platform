"""La sesión de tests nunca apunta al sello REAL de facet_resolver.

2026-09-12: con el sello del catálogo de motores, run_migrations estampa el
sello al terminar. El fixture `client` es de SESIÓN y arranca la app (y con
ella run_migrations) ANTES que el aislamiento por función
(`_sello_de_facets_aislado`): correr la suite en hall9000 estampó
/srv/jax-data/facet-cache-seal a las 14:37:46 -- el archivo que vigilan
Jacobs, el REPL y LAS MANOS en producción.

El aislamiento tiene que existir antes de que se importe facet_resolver, que
lee JAX_FACET_SEAL_PATH al importarse.
"""
import os

_SELLO_REAL = "/srv/jax-data/facet-cache-seal"


def test_la_sesion_no_apunta_al_sello_real():
    ruta = os.environ.get("JAX_FACET_SEAL_PATH")
    assert ruta, "JAX_FACET_SEAL_PATH no está fijado para la sesión de tests"
    assert ruta != _SELLO_REAL and not ruta.startswith("/srv/"), ruta


def test_el_valor_por_defecto_del_modulo_tampoco():
    """FACET_SEAL_PATH se lee al importar: si el env llegó tarde, el módulo
    ya quedó apuntando al real y el fixture de sesión lo usaría."""
    import facet_resolver
    default = facet_resolver.os.getenv("JAX_FACET_SEAL_PATH", _SELLO_REAL)
    assert not default.startswith("/srv/"), default
