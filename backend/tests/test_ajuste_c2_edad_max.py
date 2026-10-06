"""`ejecutor.c2_edad_max_s` en la pantalla Configuración.

La fila la siembra db/migrations.py::_ejecutor_reglas_v1 con 86400 y el Ejecutor
(repo jax, contratos/exportar.py) la lee por SQL directo y la exige entera > 0.
Hasta acá ningún ajuste la administraba: el superadmin no podía llevarla a 108000
(30 h, el diseño de C2). Este módulo fija su definición con rangos del servidor.
"""
import pytest

import ajustes
from tests.identidades import cabeceras, sql, uid

CLAVE = "ejecutor.c2_edad_max_s"
ADMIN = "ajuste-c2-edad"


def _put(client, items):
    return client.put("/api/admin/config", json=items, headers=cabeceras(client, ADMIN, role="superadmin"))


def test_la_clave_es_la_misma_que_siembra_la_migracion():
    assert ajustes.C2_EDAD_MAX_S == CLAVE
    assert CLAVE in ajustes.DEFINICIONES
    assert CLAVE in ajustes.CLAVES_SOLO_ADMINISTRADAS
    assert CLAVE not in ajustes.CLAVES  # esta clave no la lee ESTE servicio: ver el comentario en ajustes.py


def test_limites_publicados():
    assert ajustes.limites()[CLAVE] == {"min": 3600, "max": 7 * 86400}


@pytest.mark.parametrize("texto,esperado", [("3600", 3600), ("86400", 86400), ("108000", 108000), ("604800", 604800)])
def test_acepta_valores_en_rango(texto, esperado):
    assert ajustes.interpretar(CLAVE, texto) == esperado


@pytest.mark.parametrize("texto", ["0", "3599", "604801", "-1", "1e5", "108000.0", "treinta", "", " 108000", "0108000"])
def test_rechaza_lo_invalido(texto):
    with pytest.raises(ajustes.ValorInvalido):
        ajustes.interpretar(CLAVE, texto)


def test_el_valor_sembrado_cae_dentro_de_los_limites():
    """El 86400 que siembra la migración tiene que poder guardarse de vuelta."""
    assert ajustes.interpretar(CLAVE, "86400") == 86400


def _fila(client):
    return client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = %s", (CLAVE,), True)[0][0]


@pytest.fixture
def auditoria_limpia(client):
    """Deja la auditoría y la fila de la clave como las encontró (la fixture
    `ajustes_en_db` sólo repone ajustes.CLAVES, y esta no está ahí)."""
    borrar = "DELETE FROM axioma_config_audit WHERE config_key = %s"
    antes = client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = %s", (CLAVE,), True)
    client.portal.call(sql, borrar, (CLAVE,))
    yield
    client.portal.call(sql, borrar, (CLAVE,))
    client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key = %s", (CLAVE,))
    for (valor,) in antes:
        client.portal.call(sql, "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s)", (CLAVE, valor))


def test_put_valido_guarda_y_audita(client, ajustes_en_db, auditoria_limpia):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, CLAVE: "86400"})
    assert _put(client, [{"key": CLAVE, "value": "108000"}]).status_code == 200
    assert _fila(client) == "108000"
    actor = int(uid(client, ADMIN, role="superadmin"))
    filas = client.portal.call(
        sql, "SELECT actor_user_id, config_key, valor_anterior, valor_nuevo, origen "
             "FROM axioma_config_audit WHERE config_key = %s ORDER BY id DESC", (CLAVE,), True)
    assert [tuple(f) for f in filas] == [(actor, CLAVE, "86400", "108000", "config")]


@pytest.mark.parametrize("valor", ["3599", "604801", "absurdo", "99999999999"])
def test_put_fuera_de_limites_es_400_y_no_escribe(client, ajustes_en_db, auditoria_limpia, valor):
    ajustes_en_db.poner(**{**ajustes_en_db.validos, CLAVE: "86400"})
    r = _put(client, [{"key": CLAVE, "value": valor}])
    assert (r.status_code, r.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": CLAVE}})
    assert _fila(client) == "86400"
    assert client.portal.call(
        sql, "SELECT COUNT(*) FROM axioma_config_audit WHERE config_key = %s", (CLAVE,), True)[0][0] == 0
