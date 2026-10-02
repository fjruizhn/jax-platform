"""T16 (2026-10-02, decisión de Fernando D-5 del spec El Faro): /command se retiró.

El endpoint lanzaba `JAX_BIN --task` (el REPL de `jax`, con Claude y Bash) en el
host. La vía que lo reemplaza es el Ejecutor (`/api/ejecutor/misiones`). Estos
tests fallan contra el árbol anterior al retiro: ahí `api.command` existe y sus
rutas están registradas.

Puros: no piden `client` ni DB. Se mira el módulo y el esquema de rutas de la app.
"""
import importlib.util


def test_el_modulo_api_command_ya_no_existe():
    assert importlib.util.find_spec("api.command") is None


def test_la_app_no_registra_ninguna_ruta_de_command():
    import main
    rutas = main.app.openapi()["paths"]
    assert [r for r in rutas if r.startswith("/api/command")] == []


def test_el_evento_command_completed_ya_no_existe():
    import typing
    from jax_engine.schemas import EventType
    assert "command_completed" not in typing.get_args(EventType)


def test_el_kill_switch_ya_no_frena_una_ruta_que_no_existe():
    from kill_switch import RUTAS_FRENADAS
    assert ("POST", "/api/command") not in RUTAS_FRENADAS
