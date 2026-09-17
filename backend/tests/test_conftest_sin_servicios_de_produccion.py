"""La suite no sondea servicios de producción al arrancar la app (2026-09-17).

Defecto encontrado en la carga del frente B (Task 10): el fixture `client`
arranca el lifespan de `main.py`, que lanza `_poll_las_manos` (GET
`{LAS_MANOS_URL}/health` cada 30 s) y `_poll_pipelines`; y el tablero de
admin sondea `JAX_PLATFORM_URL`. El conftest cargaba `/etc/jax/.env` con
`setdefault`, así que en hall9000 esas URLs eran LAS MANOS (:7777) y la
plataforma (:8080) de PRODUCCIÓN.

El control corre el conftest en un proceso aparte con el entorno apuntando
explícitamente a producción: si el conftest usara `setdefault` (o no tocara
la variable), el valor de producción ganaría y el test da rojo -- también
en el runner, donde no existe `/etc/jax/.env`."""
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]

PRODUCCION = {
    "LAS_MANOS_URL": "http://127.0.0.1:7777",
    "JACOBS_URL": "http://127.0.0.1:7777/jacobs",
    "JAX_PLATFORM_URL": "http://127.0.0.1:8080",
}

_SONDA = """
import json, os
import tests.conftest  # noqa: F401  (fija el entorno, como en la suite)
import jax_engine.state as state
print(json.dumps({
    "env": {k: os.environ.get(k) for k in %r},
    "LAS_MANOS_URL": state.LAS_MANOS_URL,
}))
""" % (sorted(PRODUCCION),)


def _destino_invalido(url: str) -> bool:
    partes = urlsplit(url)
    return partes.hostname == "127.0.0.1" and partes.port == 9


def test_el_conftest_desvia_las_urls_de_servicio_aunque_el_entorno_apunte_a_produccion():
    entorno = {**os.environ, **PRODUCCION}
    salida = subprocess.run(
        [sys.executable, "-c", _SONDA], cwd=BACKEND, env=entorno,
        capture_output=True, text=True, timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    datos = json.loads(salida.stdout.strip().splitlines()[-1])
    for nombre, valor in datos["env"].items():
        assert valor is not None and _destino_invalido(valor), (nombre, valor)
    assert _destino_invalido(datos["LAS_MANOS_URL"]), datos["LAS_MANOS_URL"]


def test_en_esta_sesion_ningun_modulo_apunta_a_produccion():
    import api.pipelines
    import jax_engine.state

    assert _destino_invalido(jax_engine.state.LAS_MANOS_URL)
    assert _destino_invalido(api.pipelines.JACOBS_URL)
    assert _destino_invalido(os.environ["JAX_PLATFORM_URL"])
