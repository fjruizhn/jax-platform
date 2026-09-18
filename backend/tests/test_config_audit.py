"""Auditoría de TODA escritura de configuración (2026-09-18).

Por qué: `PUT /api/admin/config` cambiaba claves de `axioma_config` sin dejar
rastro. El 2026-09-17 la compuerta `ejecutor.c5_auditor_admite_datos_de_clientes`
-- la que deja que el auditor de nube vea máquinas con datos de clientes --
apareció en `true` y por la base NO se podía saber quién la había puesto así.

Contra jax_memory_test (conftest.py). Cada test borra las filas de auditoría
que escribió: la tabla no tiene FK (a propósito, como user_admin_audit) y
nadie la limpia por nosotros.
"""
import ast
import asyncio
import pathlib

import pytest

import config_audit
from tests.identidades import cabeceras, sql, uid

ADMIN = "auditoria-config"


def _put(client, items):
    return client.put("/api/admin/config", json=items,
                      headers=cabeceras(client, ADMIN, role="superadmin"))


def _auditoria(client, clave):
    """Lo auditado para `clave`, de lo más nuevo a lo más viejo."""
    filas = client.portal.call(
        sql,
        "SELECT actor_user_id, config_key, valor_anterior, valor_nuevo, origen, ip "
        "FROM axioma_config_audit WHERE config_key = %s ORDER BY id DESC", (clave,), True)
    return [tuple(f) for f in filas]


@pytest.fixture
def auditoria_limpia(client):
    """Deja la auditoría de las claves que toca el test como la encontró."""
    claves = ["max_pipelines", "system_name", "theme_default",
              "smtp.host", "smtp.password", "smtp.port", "smtp.encryption",
              "smtp.user", "smtp.from_name", "smtp.from_email", "smtp.test_to"]
    marcadores = ", ".join(["%s"] * len(claves))
    borrar = f"DELETE FROM axioma_config_audit WHERE config_key IN ({marcadores})"
    client.portal.call(sql, borrar, claves)
    yield
    client.portal.call(sql, borrar, claves)


# ------------------------------------------------- el rastro que faltaba

def test_un_cambio_de_config_queda_auditado_con_quien_y_con_que(client, ajustes_en_db, auditoria_limpia):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    assert _put(client, [{"key": "max_pipelines", "value": "2"}]).status_code == 200
    assert ajustes_en_db.filas()["max_pipelines"] == "2"
    actor = int(uid(client, ADMIN, role="superadmin"))
    ((actor_visto, clave, anterior, nuevo, origen, _ip),) = _auditoria(client, "max_pipelines")
    assert (actor_visto, clave, anterior, nuevo, origen) == (actor, "max_pipelines", "3", "2", "config")


def test_una_clave_que_no_existia_se_audita_con_valor_anterior_nulo(client, ajustes_en_db, auditoria_limpia):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    ajustes_en_db.quitar("max_pipelines")
    assert _put(client, [{"key": "max_pipelines", "value": "2"}]).status_code == 200
    ((_actor, _clave, anterior, nuevo, _origen, _ip),) = _auditoria(client, "max_pipelines")
    assert (anterior, nuevo) == (None, "2")


def test_otra_grafia_audita_la_clave_que_la_base_pisa(client, ajustes_en_db, auditoria_limpia):
    """"MAX_PIPELINES" ES la fila max_pipelines (collation uca1400_ai_ci): la
    auditoría tiene que nombrar la fila REAL, no la grafía del pedido -- si no,
    el historial de una clave no muestra el cambio que la pisó."""
    ajustes_en_db.poner(**ajustes_en_db.validos)
    assert _put(client, [{"key": "MAX_PIPELINES", "value": "2"}]).status_code == 200
    ((_actor, clave, anterior, nuevo, _origen, _ip),) = _auditoria(client, "max_pipelines")
    assert (clave, anterior, nuevo) == ("max_pipelines", "3", "2")


def test_guardar_el_mismo_valor_no_escribe_nada(client, ajustes_en_db, auditoria_limpia):
    """Una fila por CAMBIO real (como kill_switch_audit). La pantalla manda
    TODAS las claves en cada guardado: sin esto, un cambio real quedaría
    enterrado entre diez filas idénticas."""
    ajustes_en_db.poner(**ajustes_en_db.validos)
    assert _put(client, [{"key": "max_pipelines", "value": "3"}]).status_code == 200
    assert _auditoria(client, "max_pipelines") == []


def test_si_la_auditoria_no_se_puede_escribir_el_cambio_no_se_aplica(client, ajustes_en_db,
                                                                    auditoria_limpia, monkeypatch):
    """Fail-closed: un cambio de configuración sin rastro es exactamente el
    defecto que esto vino a cerrar. La transacción es la garantía.

    El TestClient relanza la excepción del servidor (no hay handler genérico
    de 500): lo que importa es que la base quede como estaba."""
    ajustes_en_db.poner(**ajustes_en_db.validos)
    monkeypatch.setattr(config_audit, "SQL_AUDITORIA",
                        "INSERT INTO axioma_config_audit_que_no_existe (id) VALUES (1)")
    with pytest.raises(Exception):
        _put(client, [{"key": "max_pipelines", "value": "2"}])
    assert ajustes_en_db.filas()["max_pipelines"] == "3", "el cambio se aplicó sin auditoría"
    assert _auditoria(client, "max_pipelines") == []


def test_un_lote_que_falla_a_mitad_no_deja_ni_cambios_ni_rastro(client, ajustes_en_db,
                                                               auditoria_limpia, monkeypatch):
    """La primera clave del lote ya está escrita cuando la segunda revienta: la
    transacción revierte las dos, y también lo auditado de la primera."""
    ajustes_en_db.poner(**ajustes_en_db.validos)
    import aiomysql
    original = aiomysql.Cursor.execute

    async def falla_al_auditar_max_pipelines(self, query, args=None):
        if "axioma_config_audit" in query and args and args[1] == "max_pipelines":
            raise aiomysql.OperationalError(2013, "Lost connection (simulada)")
        return await original(self, query, args)

    monkeypatch.setattr(aiomysql.Cursor, "execute", falla_al_auditar_max_pipelines)
    with pytest.raises(aiomysql.OperationalError):
        _put(client, [{"key": "system_name", "value": "Otro"}, {"key": "max_pipelines", "value": "2"}])
    monkeypatch.setattr(aiomysql.Cursor, "execute", original)
    assert ajustes_en_db.filas()["system_name"] == "Axioma", "la primera clave del lote quedó escrita"
    assert ajustes_en_db.filas()["max_pipelines"] == "3"
    assert _auditoria(client, "system_name") == []


# -------------------------------------------------------------- SMTP

def test_la_pantalla_de_smtp_tambien_audita_y_redacta_la_contrasena(client, auditoria_limpia, monkeypatch):
    """smtp.* es configuración: la escribe otra pantalla, por otro camino, y
    también tiene que dejar rastro. El valor de smtp.password va CIFRADO en
    axioma_config: copiarlo a la auditoría sería una segunda copia del secreto
    en reposo, así que se guarda redactado en los dos lados."""
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key LIKE %s", ("smtp.%",))
    cabecera = cabeceras(client, ADMIN, role="superadmin")
    cuerpo = {"host": "mail.example.test", "port": 587, "encryption": "tls", "user": "u@example.test",
              "password": "secreto-1", "from_name": "Axioma", "from_email": "no-reply@example.test"}
    try:
        assert client.put("/api/admin/smtp", json=cuerpo, headers=cabecera).status_code == 200
        ((actor, _clave, anterior, nuevo, origen, _ip),) = _auditoria(client, "smtp.host")
        assert (actor, anterior, nuevo, origen) == (
            int(uid(client, ADMIN, role="superadmin")), None, "mail.example.test", "smtp")
        ((_a, _c, ant_pass, nuevo_pass, _o, _i),) = _auditoria(client, "smtp.password")
        assert (ant_pass, nuevo_pass) == (None, config_audit.REDACTADO)

        cuerpo2 = {**cuerpo, "host": "otro.example.test", "password": "secreto-2"}
        assert client.put("/api/admin/smtp", json=cuerpo2, headers=cabecera).status_code == 200
        assert _auditoria(client, "smtp.host")[0][2:4] == ("mail.example.test", "otro.example.test")
        # Ni el cifrado viejo ni el nuevo: los dos lados redactados.
        assert _auditoria(client, "smtp.password")[0][2:4] == (config_audit.REDACTADO, config_audit.REDACTADO)
    finally:
        client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key LIKE %s", ("smtp.%",))


# ----------------------------------------------- estructura y rendimiento

def test_tabla_e_indice(client):
    filas = client.portal.call(
        sql,
        "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'axioma_config_audit' "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX", (), True)
    assert [tuple(f) for f in filas] == [
        ("idx_axioma_config_audit_key_ts", 1, "config_key"),
        ("idx_axioma_config_audit_key_ts", 2, "ts"),
        ("PRIMARY", 1, "id"),
    ]


def test_el_historial_usa_el_indice_clave_ts(client):
    """LAS CUATRO, indexing: EXPLAIN sobre la consulta REAL. Un índice que
    existe no es un índice que se usa."""
    filas = client.portal.call(sql, "EXPLAIN " + config_audit.SQL_HISTORIAL, ("una.clave.inexistente", 50), True)
    plan = {f[2]: (f[3], f[5], f[9] or "") for f in filas}
    _tipo, clave, extra = plan["a"]
    assert clave == "idx_axioma_config_audit_key_ts", plan
    assert "filesort" not in extra and "temporary" not in extra, plan
    assert plan["u"][:2] == ("eq_ref", "PRIMARY"), plan


def test_historial_devuelve_el_email_de_quien_cambio(client, ajustes_en_db, auditoria_limpia):
    ajustes_en_db.poner(**ajustes_en_db.validos)
    assert _put(client, [{"key": "max_pipelines", "value": "2"}]).status_code == 200
    (entrada,) = client.portal.call(config_audit.historial, "max_pipelines")
    assert entrada["actor_email"] == f"test-ident-{ADMIN}-superadmin@example.invalid"
    assert (entrada["valor_anterior"], entrada["valor_nuevo"], entrada["origen"]) == ("3", "2", "config")
    assert entrada["ts"].endswith("+00:00"), "el ts se guarda y se lee en UTC (tiempo.iso_utc)"


def test_escribir_rechaza_un_origen_desconocido_antes_de_tocar_la_base():
    # cur=None a propósito: si llegara a ejecutar algo, reventaría con
    # AttributeError en vez del ValueError que se espera.
    with pytest.raises(ValueError, match="inventado"):
        asyncio.run(config_audit.escribir(None, {"k": "v"}, 1, "inventado"))


def test_escribir_exige_saber_quien_lo_hizo():
    """Una escritura de configuración sin actor es media auditoría: no se
    escribe nada."""
    with pytest.raises(ValueError):
        asyncio.run(config_audit.escribir(None, {"k": "v"}, None, "config"))


# ------------------------------------------------------------- detector

RAIZ = pathlib.Path(__file__).resolve().parent.parent
# Los DOS únicos archivos que pueden escribir axioma_config:
#   - config_audit.py: el escritor auditado (es el punto de esta rama);
#   - db/migrations.py: las semillas de arranque (INSERT IGNORE), que no son
#     el cambio de nadie y corren antes de que exista una sesión.
# Cualquier otro escritor nuevo vuelve a abrir el agujero que esto cerró.
ESCRITORES_PERMITIDOS = {"config_audit.py", "db/migrations.py"}
_ESCRITURAS = ("INSERT INTO AXIOMA_CONFIG", "INSERT IGNORE INTO AXIOMA_CONFIG",
               "UPDATE AXIOMA_CONFIG", "DELETE FROM AXIOMA_CONFIG")


def _escribe_config(texto: str) -> bool:
    """Mira los LITERALES de cadena del módulo (no el texto crudo): un
    comentario que nombre la tabla no cuenta, y una consulta partida en varias
    líneas sí (ast concatena las adyacentes)."""
    for nodo in ast.walk(ast.parse(texto)):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            aplanado = " ".join(nodo.value.upper().split())
            if any(e in aplanado for e in _ESCRITURAS):
                return True
    return False


def test_ningun_otro_modulo_escribe_axioma_config():
    culpables = set()
    for ruta in RAIZ.rglob("*.py"):
        relativa = ruta.relative_to(RAIZ).as_posix()
        if relativa.startswith(("tests/", ".venv/")):
            continue
        if _escribe_config(ruta.read_text(encoding="utf-8")):
            culpables.add(relativa)
    assert culpables == ESCRITORES_PERMITIDOS, (
        "escritor de axioma_config fuera del camino auditado (o un permitido que dejó de escribir): "
        f"{sorted(culpables ^ ESCRITORES_PERMITIDOS)}")


def test_el_detector_ve_una_escritura_nueva():
    """Un control que no falla no valida."""
    assert _escribe_config('"INSERT INTO axioma_config (config_key) VALUES (%s)"')
    assert _escribe_config('("UPDATE axioma_config SET config_value = %s "\n "WHERE config_key = %s")')
    assert not _escribe_config('"SELECT config_value FROM axioma_config WHERE config_key = %s"')
    assert not _escribe_config('# INSERT INTO axioma_config: esto es un comentario\nx = 1')
