"""PUT/GET /api/admin/config con los ajustes que mandan (spec 2026-09-16 §C):
rangos del SERVIDOR, la misma regla de igualdad que la PRIMARY KEY (collation
uca1400_ai_ci: "MAX_PIPELINES" ES la fila max_pipelines), invalidación del
caché en el mismo request y límites publicados para la pantalla."""
import pytest

import ajustes
from tests.identidades import cabeceras, sql

ADMIN = "ajustes-config"


def _put(client, items):
    return client.put("/api/admin/config", json=items, headers=cabeceras(client, ADMIN, role="superadmin"))


def test_un_lote_con_un_valor_fuera_de_rango_no_escribe_nada(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = _put(client, [{"key": "system_name", "value": "Otro"}, {"key": "max_pipelines", "value": "4"}])
    assert (r.status_code, r.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "max_pipelines"}})
    assert ajustes_en_db.filas()["system_name"] == "Axioma"


def test_otra_grafia_de_la_misma_clave_tambien_se_valida(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = _put(client, [{"key": "MAX_PIPELINES", "value": "99"}])
    assert (r.status_code, r.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "max_pipelines"}})
    assert ajustes_en_db.filas()["max_pipelines"] == "3"


def test_un_put_bueno_se_ve_en_el_request_siguiente_sin_esperar_el_ttl(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    assert client.portal.call(ajustes.valor, "max_pipelines") == 3
    assert _put(client, [{"key": "max_pipelines", "value": "2"}]).status_code == 200
    assert client.portal.call(ajustes.valor, "max_pipelines") == 2


def test_get_publica_los_limites_del_servidor(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = client.get("/api/admin/config", headers=cabeceras(client, ADMIN, role="superadmin"))
    assert r.status_code == 200
    assert r.json()["limites"] == ajustes.limites()


def test_la_sesion_vigente_de_siete_dias_se_puede_guardar(client, ajustes_en_db):
    # Guarda (ya pasa con el código viejo, que no validaba): la pantalla vieja
    # topaba en 1440 y el backend nuevo NO puede rechazar el valor vigente.
    ajustes_en_db.poner(**{**ajustes_en_db.validos, "session_timeout_min": "60"})
    assert _put(client, [{"key": "session_timeout_min", "value": "10080"}]).status_code == 200
    assert ajustes_en_db.filas()["session_timeout_min"] == "10080"


# ---- MAJOR-1 de la auditoría de #205: la PK (PAD SPACE) ignora los espacios finales y
# WEIGHT_STRING no, así que "clave " saltaba la validación y escribía la fila real.

@pytest.mark.parametrize("clave,valor,fila", [
    ("max_pipelines ", "99", "max_pipelines"),
    ("ejecutor.c2_edad_max_s ", "1", "ejecutor.c2_edad_max_s"),
    (" max_pipelines", "99", "max_pipelines"),
    ("max_pipelines\t", "99", "max_pipelines"),
])
def test_una_clave_con_espacios_en_los_bordes_es_400_y_no_escribe(client, ajustes_en_db, clave, valor, fila):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    antes = client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = %s", (fila,), True)
    r = _put(client, [{"key": clave, "value": valor}])
    assert (r.status_code, r.json()) == (400, {"detail": "config_clave_invalida"})
    despues = client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = %s", (fila,), True)
    assert despues == antes


@pytest.mark.parametrize("clave", ["MAX_PIPELINES", "máx_pipelines", "​max_pipelines", "ＭＡＸ_pipelines"])
def test_las_variantes_que_la_base_iguala_siguen_validandose(client, ajustes_en_db, clave):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = _put(client, [{"key": clave, "value": "99"}])
    assert (r.status_code, r.json()) == (400, {"detail": {"code": "config_valor_invalido", "clave": "max_pipelines"}})
    assert ajustes_en_db.filas()["max_pipelines"] == "3"


def test_una_clave_con_espacio_final_en_un_lote_no_aplica_nada(client, ajustes_en_db):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    r = _put(client, [{"key": "system_name", "value": "Otro"}, {"key": "max_pipelines ", "value": "2"}])
    assert r.status_code == 400
    assert ajustes_en_db.filas()["system_name"] == "Axioma"
    assert ajustes_en_db.filas()["max_pipelines"] == "3"
