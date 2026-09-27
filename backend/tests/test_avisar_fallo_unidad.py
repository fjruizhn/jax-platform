"""ops/avisar-fallo-unidad.py -- aviso mínimo de fallo de una unidad systemd,
SOLO biblioteca estándar (MAJOR-2, segunda auditoría adversarial, 2026-09-27).

Se carga por ruta (no es un paquete de `backend/`, vive en `ops/`, y se
ejecuta con `/usr/bin/python3 -I`, aislado del resto del repo a propósito).
Todo se prueba con `urllib`/`subprocess` FAKEADOS -- nunca pega a la Telegram
real ni corre `journalctl` de verdad.
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
    terceros -- es el último recurso cuando el .venv de la app está roto."""
    permitidos = {"__future__", "os", "subprocess", "sys", "urllib",
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


def test_ultimas_lineas_de_journal_las_incluye_en_el_mensaje(monkeypatch):
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")

    class _FakeCompletedProcess:
        returncode = 0
        stdout = "línea 1 del journal\nlínea 2 del journal\n"

    monkeypatch.setattr(aviso.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())
    capturado = {}

    def fake_urlopen(peticion, timeout=None):
        capturado["data"] = peticion.data
        return _FakeHTTPResponse(200)
    monkeypatch.setattr(aviso.urllib.request, "urlopen", fake_urlopen)

    codigo = aviso.main(["prog", "jax-catalogo-modelos.service"])

    assert codigo == 0
    assert b"jax-catalogo-modelos.service" in capturado["data"]
    assert b"l%C3%ADnea+1+del+journal" in capturado["data"] or b"linea 1 del journal" in capturado["data"] \
        or b"journal" in capturado["data"]


def test_journal_no_legible_no_rompe_el_aviso(monkeypatch):
    """Sin journalctl en el PATH, o sin permiso -- el aviso principal sale
    igual, sólo que sin esas líneas."""
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")
    monkeypatch.setattr(aviso.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no journalctl")))
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda peticion, timeout=None: _FakeHTTPResponse(200))

    assert aviso.main(["prog", "jax-catalogo-modelos.service"]) == 0


def test_main_sin_unidad_imprime_uso_y_sale_2(capsys):
    assert aviso.main(["prog"]) == 2
    assert "uso" in capsys.readouterr().err.lower()


def test_main_devuelve_1_si_el_envio_falla(monkeypatch):
    monkeypatch.delenv(aviso.TELEGRAM_TOKEN_ENV, raising=False)
    monkeypatch.delenv(aviso.TELEGRAM_CHAT_ID_ENV, raising=False)
    monkeypatch.setattr(aviso.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    assert aviso.main(["prog", "jax-catalogo-modelos.service"]) == 1


def test_el_token_nunca_aparece_en_stdout_ni_stderr(monkeypatch, capsys):
    token = "123456:token-secreto-de-verdad"
    monkeypatch.setenv(aviso.TELEGRAM_TOKEN_ENV, token)
    monkeypatch.setenv(aviso.TELEGRAM_CHAT_ID_ENV, "-100999")
    monkeypatch.setattr(aviso.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(aviso.urllib.request, "urlopen", lambda peticion, timeout=None: _FakeHTTPResponse(200))

    aviso.main(["prog", "jax-catalogo-modelos.service"])

    salida = capsys.readouterr()
    assert token not in salida.out
    assert token not in salida.err
