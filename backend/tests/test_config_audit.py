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
import re

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


def test_auditar_redacta_por_su_cuenta_sin_depender_de_quien_la_llama():
    """MINOR-4 (cuarta ronda de la auditoría adversarial, 2026-09-27):
    `auditar()` aplica `_visible()` ELLA MISMA -- se prueba llamándola
    DIRECTO (no a través de `escribir()`), con un cursor falso que sólo
    graba los parámetros del INSERT, para confirmar que la redacción no
    depende de que el llamador se acuerde de aplicarla antes."""
    llamadas = []

    class _CursorFalso:
        async def execute(self, _sql, params):
            llamadas.append(params)

    asyncio.run(config_audit.auditar(
        _CursorFalso(), actor_user_id=1, config_key="smtp.password",
        valor_anterior="secreto-viejo", valor_nuevo="secreto-nuevo",
        origen="smtp"))

    ((_actor, _clave, valor_anterior, valor_nuevo, _origen, _ip),) = llamadas
    assert valor_anterior == config_audit.REDACTADO
    assert valor_nuevo == config_audit.REDACTADO


# ------------------------------------------------------------- detector

RAIZ = pathlib.Path(__file__).resolve().parent.parent

#
# MINOR-5 (quinta ronda de la auditoría adversarial, 2026-09-27): una sola
# regex de escritores, COMPARTIDA por `axioma_config` y `axioma_config_audit`
# -- antes eran dos juegos de patrones casi idénticos (uno armado en la
# cuarta ronda para `_audit`, el original de `axioma_config` sin
# actualizar) con el mismo riesgo de divergir: un arreglo en uno que se
# olvida del otro. `_patrones_de_escritura(tabla)` arma los patrones para
# CUALQUIER nombre de tabla; `_escribe_tabla(texto, tabla)` es el único
# escáner. El `(?![A-Z0-9_])` al final del nombre de tabla (en vez de `\b`)
# sigue siendo necesario -- una comilla invertida de cierre inmediatamente
# seguida de un espacio no es un "borde de palabra" para `\b`, pero sí
# tiene que contar como "la tabla termina acá" para este detector; `\b`
# fallaba justo en ese caso (comillas invertidas).
#
# Formas que ahora se ven, además del nombre pelado con `INSERT INTO`:
#   - comillas invertidas: `` `tabla` ``;
#   - prefijo de base: `mibase.tabla`, con o sin comillas invertidas en
#     cada parte;
#   - `INTO` opcional en INSERT/REPLACE (la gramática de MariaDB/MySQL no
#     lo exige: `INSERT tabla (...)` es SQL válido);
#   - modificadores opcionales de INSERT/REPLACE (`LOW_PRIORITY`,
#     `DELAYED`, `HIGH_PRIORITY`, `IGNORE`) y de UPDATE/DELETE
#     (`LOW_PRIORITY`, `IGNORE`, y `QUICK` sólo en DELETE) entre el verbo y
#     el nombre de la tabla -- p.ej. `UPDATE LOW_PRIORITY tabla SET ...` o
#     `INSERT LOW_PRIORITY IGNORE INTO tabla ...`;
#   - `DELETE` multi-tabla, DOS formas: la tabla pegada a `DELETE` --
#     `DELETE tabla, otra FROM tabla JOIN otra ON ...` -- y la tabla pegada
#     a `FROM`, en CUALQUIER posición de la sentencia (no sólo inmediata
#     después de `DELETE`) -- `DELETE FROM tabla, otra USING ...` y también
#     `DELETE alias FROM tabla alias WHERE ...` (MINOR-3, sexta ronda de la
#     auditoría adversarial, 2026-09-27: la versión anterior de este
#     comentario decía que las "dos formas" ya estaban cubiertas, pero el
#     patrón exigía que `FROM` viniera INMEDIATAMENTE después de `DELETE` --
#     un alias en el medio, como en `DELETE alias FROM tabla alias`, no
#     matcheaba. Ahora el patrón de FROM busca la tabla en cualquier punto
#     posterior a `DELETE`, no sólo pegada);
#   - `UPDATE` multi-tabla: `UPDATE otra JOIN tabla ON ... SET tabla.x = ...`
#     (la tabla como el JOIN, no la primera) y la sintaxis vieja
#     `UPDATE otra, tabla SET tabla.x = ...` (lista separada por comas, sin
#     JOIN) -- las dos formas dejan escribir una tabla que NO es la primera
#     nombrada después de `UPDATE`;
#   - `TRUNCATE [TABLE] tabla` -- borra todas las filas, tan escritura como
#     un DELETE sin WHERE;
#   - `LOAD DATA ... INTO TABLE tabla` -- carga masiva, un tercer camino de
#     escritura además de INSERT/REPLACE.
_MODIFICADORES_INSERT = r"(?:(?:LOW_PRIORITY|DELAYED|HIGH_PRIORITY|IGNORE)\s+)*"
_MODIFICADORES_UPDATE = r"(?:(?:LOW_PRIORITY|IGNORE)\s+)*"
_MODIFICADORES_DELETE = r"(?:(?:LOW_PRIORITY|QUICK|IGNORE)\s+)*"


def _patron_de_tabla(tabla: str) -> str:
    return rf"`?(?:[A-Z0-9_$]+`?\.`?)?{tabla}(?![A-Z0-9_])"


def _patrones_de_escritura(tabla: str) -> tuple:
    t = _patron_de_tabla(tabla)
    return tuple(re.compile(p) for p in (
        rf"INSERT\s+{_MODIFICADORES_INSERT}(?:INTO\s+)?{t}",
        rf"REPLACE\s+{_MODIFICADORES_INSERT}(?:INTO\s+)?{t}",
        # UPDATE: la tabla justo después del verbo (caso simple), O en
        # cualquier punto posterior a un JOIN o una coma (multi-tabla).
        rf"UPDATE\s+{_MODIFICADORES_UPDATE}{t}",
        rf"UPDATE\b.*?(?:JOIN|,)\s*{t}",
        # DELETE: la tabla pegada al verbo (listado multi-tabla), O en
        # cualquier punto posterior a un FROM -- no necesariamente el FROM
        # inmediato después de DELETE (cubre alias en el medio).
        rf"DELETE\s+{_MODIFICADORES_DELETE}{t}",
        rf"DELETE\b.*?FROM\s+{_MODIFICADORES_DELETE}{t}",
        rf"TRUNCATE\s+(?:TABLE\s+)?{t}",
        rf"LOAD\s+DATA\b.*?INTO\s+TABLE\s+{t}",
    ))


def _nodos_de_docstring(arbol: ast.AST) -> set:
    """Los `ast.Constant` que SON docstrings de verdad -- el PRIMER
    statement del módulo, o de una clase/función/función async, exactamente
    la definición de Python (`ast.get_docstring`). MINOR-3 (sexta ronda de
    la auditoría adversarial, 2026-09-27): una docstring que MENCIONA una
    tabla en su prosa -- "antes, X tenía su propio INSERT INTO
    axioma_config_audit crudo", por ejemplo, un patrón que este mismo
    árbol usa seguido para explicar POR QUÉ algo cambió -- no es una
    escritura real, y no tiene que contar como una. Se identifican por
    posición (primer statement de su scope), no por heurística de
    contenido: así no hace falta adivinar qué "parece" documentación."""
    nodos = set()
    scopes = [arbol] + [n for n in ast.walk(arbol)
                        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        if (scope.body and isinstance(scope.body[0], ast.Expr)
                and isinstance(scope.body[0].value, ast.Constant)
                and isinstance(scope.body[0].value.value, str)):
            nodos.add(id(scope.body[0].value))
    return nodos


def _escribe_tabla(texto: str, tabla: str) -> bool:
    """Mira los LITERALES de cadena del módulo que NO son docstrings (no el
    texto crudo): un comentario `#` nunca es un nodo del AST (no hace falta
    excluirlo aparte), una docstring de verdad se excluye explícitamente
    (`_nodos_de_docstring`), y una consulta partida en varias líneas sí
    cuenta (ast concatena los literales adyacentes en uno solo)."""
    patrones = _patrones_de_escritura(tabla)
    arbol = ast.parse(texto)
    docstrings = _nodos_de_docstring(arbol)
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            if id(nodo) in docstrings:
                continue
            aplanado = " ".join(nodo.value.upper().split())
            if any(patron.search(aplanado) for patron in patrones):
                return True
    return False


def _escribe_config(texto: str) -> bool:
    return _escribe_tabla(texto, "AXIOMA_CONFIG")


def _escribe_config_audit(texto: str) -> bool:
    return _escribe_tabla(texto, "AXIOMA_CONFIG_AUDIT")


# Los DOS únicos archivos que pueden escribir axioma_config:
#   - config_audit.py: el escritor auditado (es el punto de esta rama);
#   - db/migrations.py: las semillas de arranque (INSERT IGNORE), que no son
#     el cambio de nadie y corren antes de que exista una sesión.
# Cualquier otro escritor nuevo vuelve a abrir el agujero que esto cerró.
ESCRITORES_PERMITIDOS = {"config_audit.py", "db/migrations.py"}

# `config_audit.auditar()` (MINOR-6, tercera ronda) es el ÚNICO INSERT crudo
# a `axioma_config_audit` en todo el árbol -- `escribir()` la llama para su
# propio rastro, y `catalogo_sync_config.py` (su propia tabla tipada, no
# `axioma_config`) la llama directo.
ESCRITORES_PERMITIDOS_AUDITORIA = {"config_audit.py"}


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


def test_ningun_otro_modulo_escribe_axioma_config_audit():
    culpables = set()
    for ruta in RAIZ.rglob("*.py"):
        relativa = ruta.relative_to(RAIZ).as_posix()
        if relativa.startswith(("tests/", ".venv/")):
            continue
        if _escribe_config_audit(ruta.read_text(encoding="utf-8")):
            culpables.add(relativa)
    assert culpables == ESCRITORES_PERMITIDOS_AUDITORIA, (
        "escritor de axioma_config_audit fuera de config_audit.py (o config_audit.py dejó de "
        f"escribirla): {sorted(culpables ^ ESCRITORES_PERMITIDOS_AUDITORIA)}")


@pytest.mark.parametrize("escribe,tabla", [
    (_escribe_config, "axioma_config"),
    (_escribe_config_audit, "axioma_config_audit"),
])
class TestDetectorCompartido:
    """Un control que no falla no valida -- las MISMAS formas, para las DOS
    tablas, contra el MISMO detector (MINOR-5: la razón de compartirlo es
    justamente poder escribir el test una sola vez y correrlo dos veces)."""

    def test_forma_basica(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "INSERT INTO {tabla} (config_key) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = ("UPDATE {tabla} SET x = %s "\n "WHERE config_key = %s")')
        assert not escribe(f'SQL_DE_PRUEBA = "SELECT config_key FROM {tabla} WHERE config_key = %s"')
        assert not escribe(f'# INSERT INTO {tabla}: esto es un comentario\nx = 1')

    def test_comillas_invertidas(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "INSERT INTO `{tabla}` (config_key) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = "UPDATE `{tabla}` SET ip = %s WHERE id = %s"')

    def test_replace_con_y_sin_into(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "REPLACE INTO {tabla} (id, ts) VALUES (%s, %s)"')
        assert escribe(f'SQL_DE_PRUEBA = "REPLACE {tabla} (id, ts) VALUES (%s, %s)"')

    def test_insert_sin_into(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "INSERT {tabla} (config_key) VALUES (%s)"')

    def test_prefijo_de_base(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "INSERT INTO jax_memory.{tabla} (id) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = "INSERT INTO `jax_memory`.`{tabla}` (id) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = "INSERT INTO `jax_memory`.{tabla} (id) VALUES (%s)"')

    def test_modificadores_de_insert(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "INSERT LOW_PRIORITY INTO {tabla} (id) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = "INSERT DELAYED {tabla} (id) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = "INSERT HIGH_PRIORITY IGNORE INTO {tabla} (id) VALUES (%s)"')
        assert escribe(f'SQL_DE_PRUEBA = "INSERT IGNORE INTO {tabla} (id) VALUES (%s)"')

    def test_update_low_priority(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "UPDATE LOW_PRIORITY {tabla} SET x = %s WHERE id = %s"')
        assert escribe(f'SQL_DE_PRUEBA = "UPDATE LOW_PRIORITY IGNORE {tabla} SET x = %s WHERE id = %s"')

    def test_delete_con_modificadores(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "DELETE LOW_PRIORITY QUICK IGNORE FROM {tabla} WHERE id = %s"')

    def test_delete_multi_tabla(self, escribe, tabla):
        # La tabla pegada a DELETE, antes de cualquier FROM.
        assert escribe(f'SQL_DE_PRUEBA = "DELETE {tabla}, otra FROM {tabla} JOIN otra ON otra.id = {tabla}.id"')
        # La tabla pegada a FROM, en la lista de un DELETE ... USING.
        assert escribe(f'SQL_DE_PRUEBA = "DELETE FROM {tabla}, otra USING {tabla} JOIN otra ON otra.id = {tabla}.id"')

    def test_delete_con_alias_y_from_no_inmediato(self, escribe, tabla):
        """MINOR-3: `DELETE alias FROM tabla alias WHERE ...` -- el `FROM`
        NO viene inmediatamente después de `DELETE` (hay un alias en el
        medio), así que un patrón que exigiera esa adyacencia se lo perdía
        entero."""
        assert escribe(f'SQL_DE_PRUEBA = "DELETE a FROM {tabla} a WHERE a.config_key = %s"')

    def test_update_join(self, escribe, tabla):
        """MINOR-3: `UPDATE otra JOIN tabla ON ... SET tabla.x = ...` -- la
        tabla escrita es la del JOIN, no la primera después de UPDATE."""
        assert escribe(f'SQL_DE_PRUEBA = "UPDATE otra JOIN {tabla} ON otra.id = {tabla}.id SET {tabla}.x = %s"')

    def test_update_multi_tabla_con_coma(self, escribe, tabla):
        """MINOR-3: sintaxis vieja de UPDATE multi-tabla -- lista de tablas
        separada por comas, sin JOIN."""
        assert escribe(f'SQL_DE_PRUEBA = "UPDATE otra, {tabla} SET {tabla}.x = %s WHERE otra.id = {tabla}.id"')

    def test_truncate(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "TRUNCATE {tabla}"')
        assert escribe(f'SQL_DE_PRUEBA = "TRUNCATE TABLE {tabla}"')

    def test_load_data_into_table(self, escribe, tabla):
        assert escribe(f'SQL_DE_PRUEBA = "LOAD DATA LOCAL INFILE %s INTO TABLE {tabla}"')
        assert escribe(f'SQL_DE_PRUEBA = "LOAD DATA INFILE %s REPLACE INTO TABLE {tabla}"')

    def test_no_confunde_una_tabla_que_solo_comparte_el_prefijo(self, escribe, tabla):
        assert not escribe(f'SQL_DE_PRUEBA = "INSERT INTO `{tabla}_no_es_una_tabla_real` (id) VALUES (%s)"')
        assert not escribe(f'SQL_DE_PRUEBA = "INSERT INTO jax_memory.{tabla}_no_es_una_tabla_real (id) VALUES (%s)"')

    def test_una_docstring_que_menciona_la_tabla_no_cuenta_como_escritor(self, escribe, tabla):
        """MINOR-3: una docstring que EXPLICA algo citando SQL de ejemplo --
        patrón real y frecuente en este árbol ("antes, X tenía su propio
        INSERT INTO ... crudo") -- no es una escritura. Sólo cuenta si el
        mismo texto aparece como un LITERAL usado de verdad (no como
        docstring)."""
        modulo_con_docstring_nada_mas = (
            '"""Esto documenta algo.\n\n'
            f'Antes este modulo tenia su propio INSERT INTO {tabla} (id) VALUES (%s)\n'
            'crudo -- ya no: ahora llama a la funcion compartida.\n'
            '"""\n'
            'x = 1\n'
        )
        assert not escribe(modulo_con_docstring_nada_mas)

        def_con_docstring = (
            'def f():\n'
            f'    """INSERT INTO {tabla} (id) VALUES (%s) -- esto es solo un ejemplo en prosa."""\n'
            '    return 1\n'
        )
        assert not escribe(def_con_docstring)

        # Control positivo: el MISMO texto, pero como literal usado de
        # verdad (no el primer statement de una función) -- SÍ cuenta.
        def_con_literal_real = (
            'def f(cur):\n'
            '    x = 1\n'
            f'    return cur.execute("INSERT INTO {tabla} (id) VALUES (%s)")\n'
        )
        assert escribe(def_con_literal_real)


def test_el_detector_no_confunde_axioma_config_audit_con_axioma_config():
    """Falso positivo real, encontrado al agregar MAJOR-2
    (catalogo_sync_config.py escribe en axioma_config_audit, una tabla
    DISTINTA, reusando la MISMA tabla de auditoría genérica que ya usa
    config_audit.py) -- sin el `(?![A-Z0-9_])` al final del nombre, la
    comparación por substring de antes ("INSERT INTO AXIOMA_CONFIG" adentro
    de "INSERT INTO AXIOMA_CONFIG_AUDIT (...)") daba un falso positivo
    real, medido al agregar esa auditoría."""
    assert not _escribe_config(
        '"INSERT INTO axioma_config_audit (ts, actor_user_id) VALUES (%s, %s)"')
    assert not _escribe_config(
        '("INSERT INTO axioma_config_audit "\n "(ts, actor_user_id) VALUES (%s, %s)")')
    # Control positivo con la MISMA tabla real, sin el sufijo: sigue viéndose.
    assert _escribe_config('SQL_DE_PRUEBA = "INSERT INTO axioma_config_audit_no_es_una_tabla_real" '
                           '"; INSERT INTO axioma_config (k) VALUES (1)"')
