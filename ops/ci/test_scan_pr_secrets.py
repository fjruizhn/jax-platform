"""Pruebas unitarias del escáner server-side de secretos.

No usan el repositorio ni scrypt real: ejercitan el mismo límite y la misma
asociación token->rutas, sin hacer que la suite ordinaria pague su KDF.
"""
import importlib.util
import sys
from pathlib import Path

import pytest


RUTA = Path(__file__).with_name("scan-pr-secrets.py")


def cargar():
    spec = importlib.util.spec_from_file_location("scan_pr_secrets_prueba", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def escaner():
    return cargar()


class _Digest:
    def __init__(self, valor):
        self.valor = valor

    def hex(self):
        return self.valor


def _diff(*rutas_y_tokens):
    partes = []
    for ruta, token in rutas_y_tokens:
        partes.extend((f"diff --git a/{ruta} b/{ruta}", f"+++ b/{ruta}", "@@ -0,0 +1 @@", f"+{token}"))
    return "\n".join(partes)


def _ejecutar(monkeypatch, escaner, archivos, diff, entradas):
    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return "\n".join(archivos)
        if args[:2] == ["diff", "--unified=0"]:
            return diff
        raise AssertionError(f"git no esperado: {args}")

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "cargar", lambda: entradas)
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    return escaner.main()


def test_duplica_token_una_vez_por_entrada_y_reporta_todas_las_rutas(monkeypatch, capsys, escaner):
    llamadas = []

    def scrypt_falso(token, **_kwargs):
        llamadas.append(token.decode())
        return _Digest("coincide" if token == b"token-seguro" else "no")

    monkeypatch.setattr(escaner.hashlib, "scrypt", scrypt_falso)
    entradas = [(b"a" * 16, "coincide", "canario-prueba"), (b"b" * 16, "no", "otra")]
    with pytest.raises(SystemExit) as salida:
        _ejecutar(monkeypatch, escaner, ["uno.txt", "dos.txt"],
                  _diff(("uno.txt", "token-seguro"), ("dos.txt", "token-seguro")), entradas)
    assert salida.value.code == 1
    assert llamadas.count("token-seguro") == len(entradas)
    texto = capsys.readouterr().out
    assert "file=uno.txt" in texto
    assert "file=dos.txt" in texto


def test_rechaza_techo_de_tokens_antes_de_hacer_kdf(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner, "MAX_TOKENS", 1)
    llamadas = []
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda *_a, **_k: llamadas.append(1))
    with pytest.raises(SystemExit) as salida:
        _ejecutar(monkeypatch, escaner, ["a.txt"], _diff(("a.txt", "token-uno token-dos")),
                  [(b"a" * 16, "nunca", "entrada")])
    assert salida.value.code == 1
    assert not llamadas
    assert "techo de 1 tokens" in capsys.readouterr().out


def test_rechaza_techo_de_trabajo_antes_de_hacer_kdf(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner, "MAX_WORK_UNITS", 1)
    llamadas = []
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda *_a, **_k: llamadas.append(1))
    with pytest.raises(SystemExit) as salida:
        _ejecutar(monkeypatch, escaner, ["a.txt"], _diff(("a.txt", "token-unico")),
                  [(b"a" * 16, "nunca", "uno"), (b"b" * 16, "nunca", "dos")])
    assert salida.value.code == 1
    assert not llamadas
    assert "techo de 1 comparaciones" in capsys.readouterr().out


def test_binario_sin_hunk_tambien_se_compara(monkeypatch, capsys, escaner):
    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return "imagen.bin"
        if args[:2] == ["diff", "--unified=0"]:
            return "diff --git a/imagen.bin b/imagen.bin\nBinary files differ"
        if args[0] == "show":
            return b"\x00prefix token-binario suffix\xff"
        raise AssertionError(f"git no esperado: {args}")

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "cargar", lambda: [(b"a" * 16, "coincide", "binario")])
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-binario" else "no"))
    with pytest.raises(SystemExit):
        escaner.main()
    assert "file=imagen.bin" in capsys.readouterr().out


def test_lista_malformada_falla_cerrado(monkeypatch, tmp_path, capsys, escaner):
    lista = tmp_path / "lista.txt"
    lista.write_text("esto no es una entrada\n", encoding="utf-8")
    monkeypatch.setattr(escaner, "LISTA", str(lista))
    with pytest.raises(SystemExit) as salida:
        escaner.cargar()
    assert salida.value.code == 1
    assert "lista no parsea (FAIL-CLOSED)" in capsys.readouterr().out
