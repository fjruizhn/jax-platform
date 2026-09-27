"""Ejecutor programado del catálogo de modelos (2026-09-27).

`catalogo_modelos_ejecutor.py` corre `model_catalog.sync_all()` fuera del
click de un superadmin -- el hallazgo real que lo origina es que
POST /admin/models/sync solo se disparaba a mano, y desde que los servicios
corren como `jaxsvc` el sync de anthropic se saltaba en cada corrida sin que
nadie lo viera (nadie hacía click).

Estos tests son PUROS -- ninguno pide `client` ni toca la base real: el
propio `sync_all()` ya está cubierto por test_model_catalog_sync_all.py y
test_model_catalog_facetas_en_riesgo.py. Lo que se prueba acá es la capa de
arriba: código de salida, resumen, y el dedupe del aviso.
"""
import json

import catalogo_modelos_ejecutor as ejecutor


def _resultado_ok():
    return {
        "ok": True, "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": [], "enrich_fallido": False, "nuevos": {},
        "facetas_en_riesgo": [],
    }


def _resultado_con_problemas(**overrides):
    base = {
        "ok": False, "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": ["anthropic"], "enrich_fallido": False, "nuevos": {},
        "facetas_en_riesgo": [], "code": "sync_con_errores",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# main(): código de salida
# --------------------------------------------------------------------------

def _sin_aviso(monkeypatch):
    """Aísla main() del envío real de avisos -- eso lo prueban los tests de
    dedupe, más abajo, con `_avisar` directamente."""
    monkeypatch.setattr(ejecutor, "_avisar", lambda resultado: None)


def test_main_sale_0_cuando_ok(monkeypatch):
    async def _correr():
        return _resultado_ok()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    assert ejecutor.main() == 0


def test_main_sale_distinto_de_cero_cuando_hay_problemas(monkeypatch):
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    assert ejecutor.main() != 0


def test_main_imprime_un_resumen_con_los_campos_clave(monkeypatch, capsys):
    async def _correr():
        return _resultado_con_problemas(providers_fallidos=["openai"])
    monkeypatch.setattr(ejecutor, "_correr", _correr)
    _sin_aviso(monkeypatch)

    ejecutor.main()
    salida = capsys.readouterr().out
    assert "ok=False" in salida
    assert "openai" in salida
    assert "anthropic" in salida


def test_main_no_enmascara_el_codigo_de_salida_si_el_aviso_falla(monkeypatch):
    """Principio: un aviso roto (Telegram caído, disco lleno) NO puede
    convertir un job con problemas en un job que sale 0."""
    async def _correr():
        return _resultado_con_problemas()
    monkeypatch.setattr(ejecutor, "_correr", _correr)

    def _avisar_que_revienta(resultado):
        raise RuntimeError("Telegram no responde")
    monkeypatch.setattr(ejecutor, "_avisar", _avisar_que_revienta)

    # main() no debe dejar que el aviso reviente el proceso entero: el
    # código de salida tiene que seguir reflejando `ok`, no un traceback.
    try:
        codigo = ejecutor.main()
    except RuntimeError:
        raise AssertionError("un fallo de aviso no puede propagarse fuera de main()")
    assert codigo != 0


# --------------------------------------------------------------------------
# Dedupe del aviso
# --------------------------------------------------------------------------

def test_debe_avisar_primera_vez_es_true():
    assert ejecutor._debe_avisar(None, "firma-x", ahora=1000.0) is True


def test_debe_avisar_misma_firma_dentro_de_la_ventana_es_false():
    entrada = {"firma": "firma-x", "notificado_en": 1000.0}
    assert ejecutor._debe_avisar(entrada, "firma-x", ahora=1000.0 + 60) is False


def test_debe_avisar_firma_distinta_es_true_aunque_sea_reciente():
    entrada = {"firma": "firma-x", "notificado_en": 1000.0}
    assert ejecutor._debe_avisar(entrada, "firma-y", ahora=1000.0 + 60) is True


def test_debe_avisar_misma_firma_pasada_la_ventana_es_true():
    entrada = {"firma": "firma-x", "notificado_en": 1000.0}
    ahora = 1000.0 + ejecutor.VENTANA_REAVISO_SEGUNDOS + 1
    assert ejecutor._debe_avisar(entrada, "firma-x", ahora=ahora) is True


def test_avisar_no_repite_el_mismo_problema_dos_veces_seguidas(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = []
    monkeypatch.setattr(ejecutor, "_enviar_telegram", lambda msg: llamadas.append(msg))

    resultado = _resultado_con_problemas()
    ejecutor._avisar(resultado)
    ejecutor._avisar(resultado)  # mismo conjunto de problemas, corrida siguiente

    assert len(llamadas) == 1


def test_avisar_repite_cuando_el_problema_cambia(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = []
    monkeypatch.setattr(ejecutor, "_enviar_telegram", lambda msg: llamadas.append(msg))

    ejecutor._avisar(_resultado_con_problemas(providers_saltados=["anthropic"]))
    ejecutor._avisar(_resultado_con_problemas(providers_saltados=["ollama"]))  # problema DISTINTO

    assert len(llamadas) == 2


def test_avisar_reavisa_pasada_la_ventana_aunque_el_problema_sea_el_mismo(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = []
    monkeypatch.setattr(ejecutor, "_enviar_telegram", lambda msg: llamadas.append(msg))

    ahora = [1_000_000.0]
    monkeypatch.setattr(ejecutor.time, "time", lambda: ahora[0])

    resultado = _resultado_con_problemas()
    ejecutor._avisar(resultado)
    ahora[0] += ejecutor.VENTANA_REAVISO_SEGUNDOS + 1
    ejecutor._avisar(resultado)

    assert len(llamadas) == 2


def test_avisar_sin_problemas_no_manda_nada_y_limpia_el_estado(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = []
    monkeypatch.setattr(ejecutor, "_enviar_telegram", lambda msg: llamadas.append(msg))

    ejecutor._avisar(_resultado_con_problemas())
    assert len(llamadas) == 1

    ejecutor._avisar(_resultado_ok())
    assert len(llamadas) == 1  # nada nuevo: ok=True no es un problema

    # y si el MISMO problema de antes vuelve a aparecer, se vuelve a avisar
    # -- el estado de "ya avisado" se limpió cuando el catálogo estuvo sano.
    ejecutor._avisar(_resultado_con_problemas())
    assert len(llamadas) == 2


def test_avisar_informa_modelos_nuevos_una_sola_vez(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    llamadas = []
    monkeypatch.setattr(ejecutor, "_enviar_telegram", lambda msg: llamadas.append(msg))

    resultado = _resultado_ok()
    resultado["nuevos"] = {"anthropic": ["claude-opus-5-nuevo"]}
    ejecutor._avisar(resultado)
    ejecutor._avisar(resultado)

    assert len(llamadas) == 1
    assert "claude-opus-5-nuevo" in llamadas[0]


def test_avisar_persiste_el_estado_como_json_legible(monkeypatch, tmp_path):
    monkeypatch.setenv(ejecutor.ESTADO_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(ejecutor, "_enviar_telegram", lambda msg: None)

    ejecutor._avisar(_resultado_con_problemas())

    ruta = ejecutor._ruta_estado()
    assert ruta.is_file()
    estado = json.loads(ruta.read_text(encoding="utf-8"))
    assert "problemas" in estado
    assert "firma" in estado["problemas"]
    assert "notificado_en" in estado["problemas"]
