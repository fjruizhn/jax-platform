# backend/tests/test_tope_devoluciones_en_ajustes.py
"""El tope de devoluciones del árbitro se administra desde la pantalla, no a mano.

El 2026-09-20 la fila `jacobs.tope_devoluciones` se sembró (jax-platform#121),
pero vivía SOLO en la base: no estaba en `ajustes.CLAVES`, así que no salía en
la pantalla de Configuración y la única forma de cambiarla era un UPDATE a mano
-- que `config_audit` prohíbe y el detector
`test_ningun_otro_modulo_escribe_axioma_config` hace cumplir. Resultado: un
número de gobernanza que nadie podía tocar sin un PR.

Sobre el tope máximo: el spec 2026-09-18-arbitro-devuelve-design §3.3 fija el
valor inicial en 2 y explica el porqué del tope --«sin tope, dos modelos pueden
discutir toda la noche gastando dinero real»--, pero NO fija un máximo. El 5 de
acá es una baranda elegida en esta rama, no un número del spec, y se declara
como tal. El 0 sí tiene que seguir siendo válido: es el estado fail-closed que
`store.get_tope_devoluciones()` impone cuando falta la fila, y significa "el
árbitro no devuelve" -- un valor legítimo, no un error.
"""
import pytest

import ajustes


def test_la_clave_esta_entre_los_ajustes_que_mandan():
    assert ajustes.TOPE_DEVOLUCIONES == "jacobs.tope_devoluciones"
    assert ajustes.TOPE_DEVOLUCIONES in ajustes.CLAVES


def test_tiene_definicion_con_limites_para_la_pantalla():
    """Sin entrada en DEFINICIONES la pantalla no sabe qué control dibujar ni
    contra qué validar, y `ajustes.interpretar` reventaría con KeyError."""
    assert ajustes.TOPE_DEVOLUCIONES in ajustes.DEFINICIONES
    limites = ajustes.limites()[ajustes.TOPE_DEVOLUCIONES]
    assert limites == {"min": 0, "max": ajustes.TOPE_DEVOLUCIONES_MAX}


@pytest.mark.parametrize("texto, esperado", [("0", 0), ("2", 2), ("5", 5)])
def test_acepta_los_valores_validos(texto, esperado):
    assert ajustes.interpretar(ajustes.TOPE_DEVOLUCIONES, texto) == esperado


def test_el_cero_es_valido_no_un_error():
    """Es el estado fail-closed de store.get_tope_devoluciones(): el árbitro
    puede objetar pero no devolver. Rechazarlo obligaría a un PR para volver a
    ese estado, que es justo lo que esta rama viene a evitar."""
    assert ajustes.interpretar(ajustes.TOPE_DEVOLUCIONES, "0") == 0


@pytest.mark.parametrize("texto", ["-1", "6", "2.5", "dos", "", " ", "1e1"])
def test_rechaza_lo_invalido(texto):
    with pytest.raises(ajustes.ValorInvalido):
        ajustes.interpretar(ajustes.TOPE_DEVOLUCIONES, texto)


def test_el_valor_sembrado_cae_dentro_de_los_limites():
    """Control cruzado: si alguien sube el valor inicial de la migración por
    encima del máximo de la pantalla, el arranque sembraría algo que el propio
    admin no podría guardar de vuelta."""
    from db.migrations import VALOR_INICIAL_TOPE_DEVOLUCIONES
    valor = ajustes.interpretar(ajustes.TOPE_DEVOLUCIONES, VALOR_INICIAL_TOPE_DEVOLUCIONES)
    assert 0 <= valor <= ajustes.TOPE_DEVOLUCIONES_MAX


def test_la_clave_de_ajustes_y_la_de_la_migracion_son_LA_MISMA():
    """Dos literales iguales hoy pueden separarse mañana. El nombre canónico lo
    fija `jacobs/store.py::_CONFIG_KEY_TOPE_DEVOLUCIONES` en el repo jax; acá se
    atan las dos copias de este repo entre sí."""
    from db.migrations import CLAVE_TOPE_DEVOLUCIONES
    assert CLAVE_TOPE_DEVOLUCIONES == ajustes.TOPE_DEVOLUCIONES
