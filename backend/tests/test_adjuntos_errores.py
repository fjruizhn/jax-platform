import pytest

from adjuntos.errores import CODIGOS, AdjuntoRechazado


def test_detail_lleva_el_codigo_y_los_extras():
    e = AdjuntoRechazado(413, "adjunto_demasiado_grande", max_bytes=10)
    assert e.status == 413
    assert e.detail == {"code": "adjunto_demasiado_grande", "max_bytes": 10}


def test_un_codigo_fuera_de_la_lista_no_se_puede_emitir():
    # La lista es el contrato con es.js/en.js (test_adjuntos_i18n_backend.py):
    # un código nuevo sin traducción no llega al usuario como texto crudo.
    with pytest.raises(ValueError):
        AdjuntoRechazado(422, "codigo_inventado")
    assert len(set(CODIGOS)) == len(CODIGOS)
