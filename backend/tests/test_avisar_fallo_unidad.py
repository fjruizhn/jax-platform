"""ops/avisar-fallo-unidad.py -- aviso mínimo de fallo de una unidad systemd,
SOLO biblioteca estándar (MAJOR-2, segunda auditoría adversarial, 2026-09-27).

Tercera auditoría adversarial (2026-09-27): ya NO lee el journal (vía de fuga
-- jaxsvc no tiene permiso de leerlo) y sólo avisa cuando
`MONITOR_SERVICE_RESULT` (una de las cuatro variables que systemd exporta a
toda unidad de `OnFailure=`) es DISTINTO de `exit-code` -- con `exit-code` el
propio ejecutor (catalogo_modelos_ejecutor.py::main()) ya avisó desde adentro.

Se carga por ruta (no es un paquete de `backend/`, vive en `ops/`, y se
ejecuta con `/usr/bin/python3 -I`, aislado del resto del repo a propósito).
Todo se prueba con `urllib` FAKEADO -- nunca pega a la Telegram real.
"""
import ast
import importlib.util
from pathlib import Path

RUTA_SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "avisar-fallo-unidad.py"


def _cargar_modulo():
    spec = importlib.util.spec_from_file_location("avisar_fallo_unidad", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def setup_module(module):
    if not RUTA_SCRIPT.is_file():
        raise AssertionError(f"falta el script: {RUTA_SCRIPT}")


aviso = _cargar_modulo()


class _FakeHTTPResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_solo_importa_biblioteca_estandar():
    """El script no puede depender de nada de jax-platform ni de paquetes de
    terceros -- es el último recurso cuando el .venv de la app está roto.
    `subprocess` ya NO está permitido (tercera auditoría adversarial,
    2026-09-27): ya no corre journalctl ni ningún otro proceso externo."""
    permitidos = {"__future__", "os", "sys", "urllib",
                  "urllib.error", "urllib.parse", "urllib.request"}
    arbol = ast.parse(RUTA_SCRIPT.read_text(encoding="utf-8"))
    nombres = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres += [n.name for n in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres.append(nodo.module)
    assert nombres, "el escaneo no encontró ningún import -- ¿cambió el archivo?"
    assert set(nombres) <= permitidos, f"imports no permitidos: {set(nombres) - permitidos}"
    assert "subprocess" not in nombres, "ya no debería invocar journalctl ni ningún otro proceso"


def test_no_lee_el_journal():
    """El código fuente no debe mencionar journalctl en absoluto -- tercera
    auditoría adversarial (2026-09-27): jaxsvc no tiene permiso de leerlo, y
    dejar el camino armado (aunque hoy falle en silencio) es una vía de fuga
    si algún día se le diera ese permiso."""
    fuente = RUTA_SCRIPT.read_text(encoding="utf-8")
    assert "journalctl" not in fuente


def test_enviar_telegram_ok(monkeypatch):
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")
    capturado = {}

    def fake_urlopen(peticion, timeout=None):
        capturado["url"] = peticion.full_url
        capturado["data"] = peticion.data
        capturado["method"] = peticion.get_method()
        return _FakeHTTPResponse(200)
    monkeypatch.setattr(aviso.urllib.request, "urlopen", fake_urlopen)

    assert aviso._enviar_telegram("hola") is True
    assert "123456:token-de-prueba" in capturado["url"]  # va en el PATH, forma real de la API
    assert capturado["method"] == "POST"
    assert b"hola" in capturado["data"]
    assert b"-100999" in capturado["data"]


def test_enviar_telegram_false_si_urlopen_falla(monkeypatch):
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")

    def fake_urlopen(peticion, timeout=None):
        raise aviso.urllib.error.URLError("refused")
    monkeypatch.setattr(aviso.urllib.request, "urlopen", fake_urlopen)

    assert aviso._enviar_telegram("hola") is False


def test_enviar_telegram_false_si_status_no_es_200(monkeypatch):
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda peticion, timeout=None: _FakeHTTPResponse(403))

    assert aviso._enviar_telegram("hola") is False


def test_enviar_telegram_false_sin_variables_de_entorno(monkeypatch):
    monkeypatch.delenv(aviso.TELEGRAM_TOKEN_ENV, raising=False)
    monkeypatch.delenv(aviso.TELEGRAM_CHAT_ID_ENV, raising=False)
    llamado = []
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda *a, **k: llamado.append(1))

    assert aviso._enviar_telegram("hola") is False
    assert llamado == []  # nunca debería intentar la llamada de red


def _limpiar_monitor_env(monkeypatch):
    for var in (
        aviso.MONITOR_SERVICE_RESULT_ENV, aviso.MONITOR_EXIT_CODE_ENV,
        aviso.MONITOR_EXIT_STATUS_ENV, aviso.MONITOR_INVOCATION_ID_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------
# Tercera auditoría adversarial (2026-09-27): sólo avisa si
# MONITOR_SERVICE_RESULT != "exit-code" -- con "exit-code" el propio
# ejecutor ya avisó desde adentro.
# --------------------------------------------------------------------------

def test_main_no_avisa_nada_si_el_resultado_es_exit_code(monkeypatch):
    """El caso central: `exit-code` significa que el proceso SÍ corrió y
    terminó solo -- eso ya lo avisa `catalogo_modelos_ejecutor.py::main()`
    desde adentro, con más contexto. Esta unidad se queda callada."""
    _limpiar_monitor_env(monkeypatch)
    monkeypatch.setenv(aviso.MONITOR_SERVICE_RESULT_ENV, "exit-code")
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")

    llamado = []
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda *a, **k: llamado.append(1))

    codigo = aviso.main(["prog", "jax-catalogo-modelos.service"])

    assert codigo == 0
    assert llamado == []  # nunca intentó mandar nada -- el ejecutor ya avisó


def test_main_avisa_si_el_resultado_es_timeout(monkeypatch):
    """El caso que sí le corresponde a esta unidad: `TimeoutStartSec` mató
    el proceso antes de que corriera su propio try/except."""
    _limpiar_monitor_env(monkeypatch)
    monkeypatch.setenv(aviso.MONITOR_SERVICE_RESULT_ENV, "timeout")
    monkeypatch.setenv(aviso.MONITOR_EXIT_CODE_ENV, "killed")
    monkeypatch.setenv(aviso.MONITOR_EXIT_STATUS_ENV, "SIGTERM")
    monkeypatch.setenv(aviso.MONITOR_INVOCATION_ID_ENV, "abc123")
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")

    capturado = {}

    def fake_urlopen(peticion, timeout=None):
        capturado["data"] = peticion.data
        return _FakeHTTPResponse(200)
    monkeypatch.setattr(aviso.urllib.request, "urlopen", fake_urlopen)

    codigo = aviso.main(["prog", "jax-catalogo-modelos.service"])

    assert codigo == 0
    cuerpo = capturado["data"].decode()
    assert "jax-catalogo-modelos.service" in cuerpo
    assert "timeout" in cuerpo
    assert "killed" in cuerpo
    assert "SIGTERM" in cuerpo
    assert "abc123" in cuerpo


def test_main_avisa_si_no_hay_variables_monitor_en_absoluto(monkeypatch):
    """Sin `MONITOR_SERVICE_RESULT` (systemd viejo, o corrido a mano) se
    trata como "no es exit-code" -- mejor un aviso de más que uno de menos
    en un camino que sólo se dispara cuando algo ya salió mal."""
    _limpiar_monitor_env(monkeypatch)
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda peticion, timeout=None: _FakeHTTPResponse(200))

    assert aviso.main(["prog", "jax-catalogo-modelos.service"]) == 0


def test_main_sin_unidad_imprime_uso_y_sale_2(capsys):
    assert aviso.main(["prog"]) == 2
    assert "uso" in capsys.readouterr().err.lower()


def test_main_devuelve_1_si_el_envio_falla(monkeypatch):
    _limpiar_monitor_env(monkeypatch)
    monkeypatch.setenv(aviso.MONITOR_SERVICE_RESULT_ENV, "signal")
    monkeypatch.delenv(aviso.TELEGRAM_TOKEN_ENV, raising=False)
    monkeypatch.delenv(aviso.TELEGRAM_CHAT_ID_ENV, raising=False)

    assert aviso.main(["prog", "jax-catalogo-modelos.service"]) == 1


def test_el_token_nunca_aparece_en_stdout_ni_stderr(monkeypatch, capsys):
    _limpiar_monitor_env(monkeypatch)
    monkeypatch.setenv(aviso.MONITOR_SERVICE_RESULT_ENV, "core-dump")
    token = "123456:token-secreto-de-verdad"
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, token)
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda peticion, timeout=None: _FakeHTTPResponse(200))

    aviso.main(["prog", "jax-catalogo-modelos.service"])

    salida = capsys.readouterr()
    assert token not in salida.out
    assert token not in salida.err
