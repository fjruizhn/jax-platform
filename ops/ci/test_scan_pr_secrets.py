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
            return ("\0".join(archivos) + "\0").encode()
        if args[0] == "show":
            if args[1].endswith(":ops/ci/known-secrets-ci.txt"):
                return Path(escaner.LISTA).read_bytes()
            return b""
        raise AssertionError(f"git no esperado: {args}")

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "lineas_diff", lambda _args: iter(
        line.encode("utf-8") for line in diff.splitlines()))
    monkeypatch.setattr(escaner, "cargar", lambda: entradas)
    monkeypatch.setattr(escaner, "parsear_lista", lambda _crudo: entradas)
    monkeypatch.setattr(escaner, "atributo_diff", lambda _ruta: b"unspecified")
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


def test_rechaza_techo_de_ubicaciones_antes_de_hacer_kdf(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner, "MAX_UBICACIONES", 1)
    llamadas = []
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda *_a, **_k: llamadas.append(1))
    with pytest.raises(SystemExit) as salida:
        _ejecutar(monkeypatch, escaner, ["uno.txt", "dos.txt"],
                  _diff(("uno.txt", "token-repetido"), ("dos.txt", "token-repetido")),
                  [(b"a" * 16, "nunca", "entrada")])
    assert salida.value.code == 1
    assert not llamadas
    assert "techo de 1 ubicaciones archivo-token" in capsys.readouterr().out


def test_binario_sin_hunk_tambien_se_compara(monkeypatch, capsys, escaner):
    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return b"imagen.bin\0"
        if args[0] == "show":
            if args[1].endswith(":ops/ci/known-secrets-ci.txt"):
                return Path(escaner.LISTA).read_bytes()
            return b"\x00prefix token-binario suffix\xff"
        raise AssertionError(f"git no esperado: {args}")

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "lineas_diff", lambda _args: iter(
        x.encode() for x in "diff --git a/imagen.bin b/imagen.bin\nBinary files differ".splitlines()))
    monkeypatch.setattr(escaner, "cargar", lambda: [(b"a" * 16, "coincide", "binario")])
    monkeypatch.setattr(escaner, "parsear_lista", lambda _crudo: [(b"a" * 16, "coincide", "binario")])
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


@pytest.mark.parametrize("contenido, esperado", [("", "lista esta vacia (FAIL-CLOSED)"),
                                                   (None, "no se pudo leer la lista (FAIL-CLOSED)")])
def test_lista_ausente_o_vacia_falla_cerrado(monkeypatch, tmp_path, capsys, escaner, contenido, esperado):
    lista = tmp_path / "lista.txt"
    if contenido is None:
        lista.unlink(missing_ok=True)
    else:
        lista.write_text(contenido, encoding="utf-8")
    monkeypatch.setattr(escaner, "LISTA", str(lista))
    with pytest.raises(SystemExit) as salida:
        escaner.cargar()
    assert salida.value.code == 1
    assert esperado in capsys.readouterr().out


def test_excepcion_del_worker_no_se_convierte_en_aprobacion(monkeypatch, escaner):
    def explotar(_entrada, _tokens):
        raise RuntimeError("fallo del KDF")

    monkeypatch.setattr(escaner, "comparar_entrada", explotar)
    with pytest.raises(RuntimeError, match="fallo del KDF"):
        _ejecutar(monkeypatch, escaner, ["a.txt"], _diff(("a.txt", "token-unico")),
                  [(b"a" * 16, "nunca", "entrada")])


def test_nombre_de_archivo_vacio_tambien_se_escanea(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        _ejecutar(monkeypatch, escaner, ["directorio/token-conocido"], "",
                  [(b"a" * 16, "coincide", "nombre")])
    assert "file=directorio/token-conocido" in capsys.readouterr().out


def test_nombre_de_archivo_con_extension_extrae_token(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        _ejecutar(monkeypatch, escaner, ["directorio/token-conocido.txt"], "",
                  [(b"a" * 16, "coincide", "nombre")])
    assert "file=directorio/token-conocido.txt" in capsys.readouterr().out


def test_nombre_con_salto_de_linea_se_parsea_completo(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        _ejecutar(monkeypatch, escaner, ["directorio\nextra/token-conocido"], "",
                  [(b"a" * 16, "coincide", "nombre")])
    assert "token-conocido" in capsys.readouterr().out


def test_nul_forzado_textual_escanea_blob_completo(monkeypatch, capsys, escaner):
    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return b"archivo.bin\0"
        if args[0] == "show":
            if args[1].endswith(":ops/ci/known-secrets-ci.txt"):
                return Path(escaner.LISTA).read_bytes()
            return b"visible\0token-conocido\0"
        raise AssertionError(args)

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "lineas_diff", lambda _args: iter(
        x.encode() for x in _diff(("archivo.bin", "visible")).splitlines()))
    monkeypatch.setattr(escaner, "cargar", lambda: [(b"a" * 16, "coincide", "blob")])
    monkeypatch.setattr(escaner, "parsear_lista", lambda _crudo: [(b"a" * 16, "coincide", "blob")])
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        escaner.main()
    assert "file=archivo.bin" in capsys.readouterr().out


def test_blob_no_utf8_forzado_textual_escanea_bytes_crudos(monkeypatch, capsys, escaner):
    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return b"archivo.bin\0"
        if args[0] == "show":
            if args[1].endswith(":ops/ci/known-secrets-ci.txt"):
                return Path(escaner.LISTA).read_bytes()
            return b"visible\xfftoken-conocido\xff"
        raise AssertionError(args)

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "lineas_diff", lambda _args: iter(
        x.encode() for x in _diff(("archivo.bin", "visible")).splitlines()))
    monkeypatch.setattr(escaner, "atributo_diff", lambda _ruta: b"unspecified")
    monkeypatch.setattr(escaner, "cargar", lambda: [(b"a" * 16, "coincide", "blob")])
    monkeypatch.setattr(escaner, "parsear_lista", lambda _crudo: [(b"a" * 16, "coincide", "blob")])
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        escaner.main()
    assert "file=archivo.bin" in capsys.readouterr().out


def test_atributo_diff_forzado_escanea_blob_completo(monkeypatch, capsys, escaner):
    entradas = [(b"a" * 16, "coincide", "blob")]

    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return b"archivo.txt\0"
        if args[0] == "show":
            return b"lista" if args[1].endswith(":ops/ci/known-secrets-ci.txt") else b"visible token-conocido"
        raise AssertionError(args)

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "lineas_diff", lambda _args: iter(
        x.encode() for x in _diff(("archivo.txt", "visible")).splitlines()))
    monkeypatch.setattr(escaner, "cargar", lambda: entradas)
    monkeypatch.setattr(escaner, "parsear_lista", lambda _raw: entradas)
    monkeypatch.setattr(escaner, "atributo_diff", lambda _ruta: b"set")
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        escaner.main()
    assert "file=archivo.txt" in capsys.readouterr().out


def test_ascii_delimitado_por_emoji_en_hunk_se_detecta(monkeypatch, capsys, escaner):
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    with pytest.raises(SystemExit):
        _ejecutar(monkeypatch, escaner, ["archivo.txt"],
                  _diff(("archivo.txt", "🔒token-conocido🔒")),
                  [(b"a" * 16, "coincide", "unicode")])
    assert "file=archivo.txt" in capsys.readouterr().out


def test_secreto_historico_no_modificado_no_falla(monkeypatch, escaner):
    entradas = [(b"a" * 16, "coincide", "historico")]

    def git_falso(args, binario=False):
        if args[:2] == ["diff", "--name-only"]:
            return b"archivo.txt\0"
        if args[0] == "show":
            return b"lista" if args[1].endswith(":ops/ci/known-secrets-ci.txt") else b"token-conocido\nvisible nuevo\n"
        raise AssertionError(args)

    monkeypatch.setattr(escaner, "git", git_falso)
    monkeypatch.setattr(escaner, "lineas_diff", lambda _args: iter(
        x.encode() for x in _diff(("archivo.txt", "visible nuevo")).splitlines()))
    monkeypatch.setattr(escaner, "cargar", lambda: entradas)
    monkeypatch.setattr(escaner, "parsear_lista", lambda _raw: entradas)
    monkeypatch.setattr(escaner, "atributo_diff", lambda _ruta: b"unspecified")
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    monkeypatch.setattr(escaner.hashlib, "scrypt", lambda token, **_k: _Digest(
        "coincide" if token == b"token-conocido" else "no"))
    assert escaner.main() == 0


def test_lista_head_malformada_o_sobredimensionada_falla(monkeypatch, escaner):
    with pytest.raises(SystemExit):
        escaner.parsear_lista("aa" * 16 + " " + "bb" * 32 + " valida\nmalformada\n")
    monkeypatch.setattr(escaner, "MAX_ENTRIES", 1)
    with pytest.raises(SystemExit):
        escaner.parsear_lista("aa" * 16 + " " + "bb" * 32 + " uno\n" +
                              "cc" * 16 + " " + "dd" * 32 + " dos\n")


def test_lista_con_demasiadas_entradas_falla_aunque_no_haya_tokens(monkeypatch, tmp_path, escaner):
    lista = tmp_path / "lista.txt"
    lista.write_text("aa" * 16 + " " + "bb" * 32 + " entrada\n" +
                     "cc" * 16 + " " + "dd" * 32 + " otra\n", encoding="utf-8")
    monkeypatch.setattr(escaner, "LISTA", str(lista))
    monkeypatch.setattr(escaner, "MAX_ENTRIES", 1)
    with pytest.raises(SystemExit):
        escaner.cargar()


def test_lista_no_puede_quitar_entrada_conocida(monkeypatch, tmp_path, escaner):
    lista = tmp_path / "lista.txt"
    lista.write_text("aa" * 16 + " " + "bb" * 32 + " entrada\n", encoding="utf-8")
    monkeypatch.setattr(escaner, "LISTA", str(lista))
    monkeypatch.setattr(escaner, "git", lambda args, binario=False:
                        b"ops/ci/known-secrets-ci.txt\0" if args[:2] == ["diff", "--name-only"] else b"")
    monkeypatch.setattr(sys, "argv", [str(RUTA), "base", "head"])
    with pytest.raises(SystemExit):
        escaner.main()
