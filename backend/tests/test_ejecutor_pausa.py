# backend/tests/test_ejecutor_pausa.py
"""La pausa del Ejecutor desde la plataforma (SP2, kill switch del modo Ejecutor). Sin DB.

`ejecutor/pausa.py` es copia de jax/ejecutor/contratos/pausa.py en lo que escribe y lee la
pausa (familia `pausa_ejecutor` de jax/scripts/check_mirror_sync.py): el proxy de C3 y el
arranque la leen con el canónico. La lectura del motivo y quitarla son de la plataforma."""
import json
import os

import pytest

from ejecutor import pausa as P

ES_ROOT = os.geteuid() == 0


def test_la_suite_no_usa_la_pausa_de_produccion():
    # /etc/jax/.env define JAX_EJECUTOR_PAUSA=/etc/jax/interruptor/...: un test que la pusiera
    # frenaría al Ejecutor de producción. El conftest la desvía a un temporal.
    ruta = P.ruta_de_la_pausa()
    assert not str(ruta).startswith("/etc/") and "jax-test-pausa-ejecutor-" in str(ruta)


def test_sin_variable_no_hay_ruta(monkeypatch):
    for valor in (None, "", "relativa/pausa"):
        if valor is None:
            monkeypatch.delenv(P.VARIABLE_RUTA, raising=False)
        else:
            monkeypatch.setenv(P.VARIABLE_RUTA, valor)
        with pytest.raises(P.PausaSinConfigurar):
            P.ruta_de_la_pausa()


def test_leer_sin_pausa(tmp_path):
    assert P.leer_pausa(tmp_path / "p") == {"puesta": False, "legible": True, "origen": None, "motivo": None,
                                            "paso": None, "momento": None}


def test_poner_leer_y_quitar(tmp_path):
    ruta = tmp_path / "p"
    assert P.poner_pausa(ruta, {"origen": "plataforma", "motivo": "manual", "user_id": 7}) is True
    assert P.poner_pausa(ruta, {"origen": "otro", "motivo": "x"}) is False  # el primer motivo no se pisa
    leida = P.leer_pausa(ruta)
    assert (leida["puesta"], leida["legible"], leida["origen"], leida["motivo"], leida["paso"]) == \
        (True, True, "plataforma", "manual", None)
    assert leida["momento"]
    assert P.quitar_pausa(ruta) is True and P.quitar_pausa(ruta) is False
    assert P.leer_pausa(ruta)["puesta"] is False


def test_la_pausa_de_c5_se_lee_con_su_paso(tmp_path):
    ruta = tmp_path / "p"
    ruta.write_text(json.dumps({"origen": "c5", "motivo": "fuera_de_mision", "paso": 3, "momento": "m"}))
    assert P.leer_pausa(ruta) == {"puesta": True, "legible": True, "origen": "c5", "motivo": "fuera_de_mision",
                                  "paso": 3, "momento": "m"}


@pytest.mark.parametrize("contenido", ["{roto", "[]", "42"])
def test_contenido_ilegible_es_puesta_fail_closed(tmp_path, contenido):
    ruta = tmp_path / "p"
    ruta.write_text(contenido)
    assert P.leer_pausa(ruta) == {"puesta": True, "legible": False, "origen": None, "motivo": None, "paso": None,
                                  "momento": None}


def test_tipos_inesperados_no_se_inventan(tmp_path):
    ruta = tmp_path / "p"
    ruta.write_text(json.dumps({"origen": 3, "motivo": ["x"], "paso": True, "momento": None}))
    assert P.leer_pausa(ruta) == {"puesta": True, "legible": True, "origen": None, "motivo": None, "paso": None,
                                  "momento": None}


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_directorio_ilegible_cuenta_como_puesta(tmp_path):
    carpeta = tmp_path / "d"
    carpeta.mkdir()
    carpeta.chmod(0o000)
    try:
        assert P.pausa_puesta(carpeta / "p") is True
        assert P.leer_pausa(carpeta / "p")["puesta"] is True
    finally:
        carpeta.chmod(0o700)


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_quitar_sin_permiso_lanza_y_la_pausa_sigue(tmp_path):
    carpeta = tmp_path / "d"
    carpeta.mkdir()
    P.poner_pausa(carpeta / "p", {"origen": "c5", "motivo": "prohibido"})
    carpeta.chmod(0o500)
    try:
        with pytest.raises(OSError):
            P.quitar_pausa(carpeta / "p")
    finally:
        carpeta.chmod(0o700)
    assert P.pausa_puesta(carpeta / "p")


def test_la_copia_comparte_con_jax_lo_que_escribe_y_lee():
    # Los símbolos de la familia `pausa_ejecutor` tienen que existir acá; la comparación
    # byte a byte la hace jax/scripts/check_mirror_sync.py en el CI de jax.
    for nombre in ("VARIABLE_RUTA", "PausaSinConfigurar", "pausa_puesta", "_sincronizar_directorio", "poner_pausa"):
        assert hasattr(P, nombre), nombre
    assert P.VARIABLE_RUTA == "JAX_EJECUTOR_PAUSA"
