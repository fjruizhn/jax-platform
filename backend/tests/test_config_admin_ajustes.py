"""PUT/GET /api/admin/config con los ajustes que mandan (spec 2026-09-16 §C):
rangos del SERVIDOR, la misma regla de igualdad que la PRIMARY KEY (collation
uca1400_ai_ci: "MAX_PIPELINES" ES la fila max_pipelines), invalidación del
caché en el mismo request y límites publicados para la pantalla."""
import ajustes
from tests.identidades import cabeceras

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
