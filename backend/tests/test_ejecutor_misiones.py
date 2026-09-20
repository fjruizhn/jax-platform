# backend/tests/test_ejecutor_misiones.py
"""Modo Ejecutor (SP2, 2026-09-17): lanzar una misión, ver su estado, su resultado y su
bitácora, retomarla por id, y pausar al Ejecutor. Contra jax_memory_test.

El runner REAL (`python -m jax.ejecutor.mision_servicio` del repo jax) nunca corre acá: el
conftest fija JAX_EJECUTOR_PYTHON a un intérprete que no existe y cada test reemplaza
`misiones._runner` por un proceso falso que habla el MISMO protocolo (pedido por stdin, una
línea JSON por evento, `resultado` al final). Lo que se prueba es la plataforma: la compuerta,
la exclusión de un turno a la vez, la pausa, la persistencia y la lectura del stream."""
import json
import os
import sys
import textwrap
import time
import uuid

import pytest

from ejecutor import misiones, pausa
from tests.identidades import auth, cabeceras, sql, token_para

BASE = "/api/ejecutor"
ES_ROOT = os.geteuid() == 0
AFIRMACION = {"maquina": "t-sp2-vm", "comando": "ssh -tt -p 58291 axioma@192.0.2.50 free -h",
              "linea": "Mem:           1.9Gi       180Mi", "dato": "1.9Gi", "proposito": "memoria total"}


# --- el runner falso ------------------------------------------------------------------------

RUNNER_FALSO = textwrap.dedent('''
    import json, os, sys, time
    guion = json.load(open(os.environ["GUION_EJECUTOR"]))
    pedido = sys.stdin.read()
    open(os.environ["GUION_EJECUTOR"] + ".pedido." + str(json.loads(pedido)["n"]), "w").write(pedido)
    for linea in guion.get("stderr", []):
        print(linea, file=sys.stderr, flush=True)
    for linea in guion.get("lineas", []):
        if linea == "@esperar":
            while not os.path.exists(os.environ["GUION_EJECUTOR"] + ".seguir"):
                time.sleep(0.02)
            continue
        print(linea if isinstance(linea, str) else json.dumps(linea), flush=True)
    sys.exit(guion.get("rc", 0))
''')


def _ev(evento, turno=1, **datos):
    return {"evento": evento, "turno": turno, "datos": datos}


def _resultado(estado="completado", codigo=None, turno=1, **extra):
    datos = {"estado": estado, "codigo": codigo, "rechazo": [], "sesion_iniciada": True,
             "afirmaciones": [AFIRMACION] if estado == "completado" else [], "descartadas": [],
             "crudas": [{"maquina": "t-sp2-vm", "comando": AFIRMACION["comando"],
                         "salida": AFIRMACION["linea"] + "\n", "truncada": False}],
             "verificacion": {"registro_cuadra": True, "cadena_ok": True, "pausa_puesta": False,
                              "auditor_pauso": False, "auditor_legible": True}}
    datos.update(extra)
    return {"evento": "resultado", "turno": turno, "datos": datos}


GUION_BUENO = {"lineas": [_ev("turno_lanzado", reanudar=False), _ev("arranque_verificado"), _ev("vigia_late"),
                          _ev("afirmacion_entregada", **AFIRMACION), _ev("turno_completado", codigo=None),
                          _resultado()]}


@pytest.fixture
def runner(tmp_path, monkeypatch):
    script = tmp_path / "runner_falso.py"
    script.write_text(RUNNER_FALSO)
    guion = tmp_path / "guion.json"
    monkeypatch.setenv("GUION_EJECUTOR", str(guion))
    monkeypatch.setattr(misiones, "_runner", lambda: ([sys.executable, str(script)], str(tmp_path), dict(os.environ)))

    class R:
        def guion(self, doc):
            guion.write_text(json.dumps(doc))

        def pedido(self, n=1):
            return json.loads((tmp_path / f"guion.json.pedido.{n}").read_text())

        def seguir(self):
            (tmp_path / "guion.json.seguir").write_text("")
    return R()


@pytest.fixture
def maquinas(client):
    filas = [("t-sp2-vm", "192.0.2.50", 58291, "desarrollo", False, True),
             ("t-sp2-clientes", "192.0.2.10", 58291, "clientes", True, True),
             ("t-sp2-apagada", "192.0.2.11", 58291, "desarrollo", False, False)]
    for f in filas:
        client.portal.call(sql, "DELETE FROM ejecutor_host WHERE nombre = %s", (f[0],))
        client.portal.call(sql, "INSERT INTO ejecutor_host (nombre, ip, puerto, rol, con_datos_de_clientes, activo) "
                                "VALUES (%s, %s, %s, %s, %s, %s)", f)
    # Sin esto, la elegibilidad de 't-sp2-clientes' dependería de si ESTA base ya tiene el
    # binding real de 'auditor_local' (sembrado si JAX_OLLAMA_CPU_URL está en el entorno de
    # la sesión) -- no determinista, ajeno a este test. Se guarda y se restaura: no se
    # ASUME ausencia, se la fuerza acá y se devuelve lo que había al terminar.
    previo = client.portal.call(
        sql, "SELECT facet_key, provider_id, model_id, role FROM facet_binding WHERE facet_key = 'auditor_local'",
        (), True)
    client.portal.call(sql, "DELETE FROM facet_binding WHERE facet_key = 'auditor_local'")
    yield
    for f in filas:
        client.portal.call(sql, "DELETE FROM ejecutor_host WHERE nombre = %s", (f[0],))
    client.portal.call(sql, "DELETE FROM facet_binding WHERE facet_key = 'auditor_local'")
    for fila in previo:
        client.portal.call(sql, "INSERT INTO facet_binding (facet_key, provider_id, model_id, role) "
                                "VALUES (%s, %s, %s, %s)", fila)


@pytest.fixture
def superadmin(client, usuarios):
    user_id, _ = usuarios(role="superadmin")
    yield user_id, auth(token_para(user_id, role="superadmin"))
    client.portal.call(_limpiar_turnos_colgados)


async def _limpiar_turnos_colgados():
    # Un test que falla a mitad deja un turno en_curso y bloquearía a los siguientes (409).
    await sql("UPDATE ejecutor_turno SET estado = 'interrumpido', codigo = 'plataforma_reiniciada' "
              "WHERE estado = 'en_curso'")


def _esperar(client, h, mision_id, hasta=lambda d: d["estado"] != "en_curso", tope_s=15):
    limite = time.monotonic() + tope_s
    while time.monotonic() < limite:
        d = client.get(f"{BASE}/misiones/{mision_id}", headers=h).json()
        if hasta(d):
            return d
        time.sleep(0.05)
    raise AssertionError(f"la misión no terminó: {d}")


def _crear(client, h, objetivo="memoria de la VM", maquinas=("t-sp2-vm",)):
    return client.post(f"{BASE}/misiones", headers=h, json={"objetivo": objetivo, "maquinas": list(maquinas)})


# --- barreras -------------------------------------------------------------------------------

def test_la_suite_no_puede_lanzar_el_runner_real():
    with pytest.raises(misiones.SinConfigurar):
        misiones._runner()


@pytest.mark.parametrize("variable, valor", [("JAX_EJECUTOR_PYTHON", ""), ("JAX_EJECUTOR_PYTHON", "python3"),
                                             ("JAX_REPO_PATH", ""), ("JAX_REPO_PATH", "relativo")])
def test_runner_sin_configurar(monkeypatch, variable, valor):
    monkeypatch.setenv("JAX_EJECUTOR_PYTHON", sys.executable)
    monkeypatch.setenv("JAX_REPO_PATH", "/tmp")
    monkeypatch.setenv(variable, valor)
    with pytest.raises(misiones.SinConfigurar):
        misiones._runner()


def test_runner_configurado_lanza_el_modulo_del_repo_jax(monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_PYTHON", sys.executable)
    monkeypatch.setenv("JAX_REPO_PATH", "/tmp")
    argv, cwd, env = misiones._runner()
    assert argv == [sys.executable, "-m", "jax.ejecutor.mision_servicio"] and cwd == "/tmp"
    assert env["PYTHONPATH"] == "/tmp:/tmp/las_manos" and env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_solo_un_superadmin(client):
    h = cabeceras(client, "ejecutor-operador")
    for metodo, ruta in (("get", "/estado"), ("post", "/pausa/poner"), ("post", "/pausa/quitar"),
                         ("get", "/misiones"), ("post", "/misiones"), ("get", f"/misiones/{uuid.uuid4()}"),
                         ("get", f"/misiones/{uuid.uuid4()}/bitacora"), ("post", f"/misiones/{uuid.uuid4()}/turnos")):
        assert getattr(client, metodo)(BASE + ruta, headers=h).status_code == 403, ruta
    assert client.get(BASE + "/estado").status_code in (401, 403)
    assert not pausa.pausa_puesta(pausa.ruta_de_la_pausa())


# --- estado: la compuerta se ve, no se esconde ----------------------------------------------

def test_estado_lista_todas_las_maquinas_con_su_elegibilidad(client, superadmin, maquinas):
    _, h = superadmin
    d = client.get(f"{BASE}/estado", headers=h).json()
    por_nombre = {m["nombre"]: m for m in d["maquinas"]}
    assert por_nombre["t-sp2-vm"] == {"nombre": "t-sp2-vm", "rol": "desarrollo", "con_datos_de_clientes": False,
                                      "activo": True, "elegible": True, "motivo_no_elegible": None}
    assert (por_nombre["t-sp2-clientes"]["elegible"], por_nombre["t-sp2-clientes"]["motivo_no_elegible"]) == \
        (False, "maquina_con_datos_de_clientes")
    assert (por_nombre["t-sp2-apagada"]["elegible"], por_nombre["t-sp2-apagada"]["motivo_no_elegible"]) == \
        (False, "maquina_inactiva")
    assert d["compuerta_datos_de_clientes"] == "cerrada"
    assert d["pausa"]["puesta"] is False and d["turno_en_curso"] is None


def test_compuerta_ilegible_es_cerrada():
    assert misiones.compuerta_abierta(None) is False
    assert misiones.compuerta_abierta("TRUE ") is False
    assert misiones.compuerta_abierta("true") is True


# --- crear: validaciones antes de lanzar nada -----------------------------------------------

def _misiones_de(client, user_id):
    return client.portal.call(sql, "SELECT id FROM ejecutor_mision WHERE user_id = %s", (user_id,), True)


def test_maquina_con_datos_de_clientes_no_es_elegible(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    r = _crear(client, h, maquinas=("t-sp2-vm", "t-sp2-clientes"))
    assert (r.status_code, r.json()["detail"]) == (403, {"codigo": "ejecutor_maquina_no_elegible",
                                                          "maquina": "t-sp2-clientes",
                                                          "motivo": "maquina_con_datos_de_clientes"})
    assert _misiones_de(client, user_id) == ()


# --- auditor local (spec 2026-09-18-auditor-local-opcion.md §4) ----------------------------

@pytest.fixture
def auditor_local_bindeado(client):
    """Faceta 'auditor_local' → un proveedor sintético con `is_local`. La faceta la trae la
    migración (siempre); acá sólo se agrega el binding de prueba -- se retira al final, la
    faceta se deja (la migración la vuelve a sembrar con INSERT IGNORE de cualquier forma)."""
    def _armar(*, is_local: bool):
        client.portal.call(sql, "DELETE FROM facet_binding WHERE facet_key = 'auditor_local'")
        client.portal.call(sql, "DELETE FROM provider WHERE id = 't-sp2-auditor'")
        client.portal.call(sql, "INSERT INTO provider (id, display_name, auth_type, is_local) "
                                "VALUES ('t-sp2-auditor', 'auditor de prueba', 'none', %s)", (is_local,))
        client.portal.call(sql, "INSERT INTO facet_binding (facet_key, provider_id, model_id, role) "
                                "VALUES ('auditor_local', 't-sp2-auditor', 'modelo-x', 'primary')")
    yield _armar
    client.portal.call(sql, "DELETE FROM facet_binding WHERE facet_key = 'auditor_local'")
    # `model` antes que `provider`: ver el comentario homónimo en
    # test_ejecutor_auditor_local.py::_limpiar -- _seed_models_and_backfill puede haber
    # derivado una fila de catálogo mientras este binding sintético estaba vivo.
    client.portal.call(sql, "DELETE FROM model WHERE provider_id = 't-sp2-auditor'")
    client.portal.call(sql, "DELETE FROM provider WHERE id = 't-sp2-auditor'")


def test_con_auditor_local_disponible_la_maquina_de_clientes_es_elegible(
        client, superadmin, maquinas, runner, auditor_local_bindeado):
    """El corazón de la decisión: la compuerta deja de ser el ÚNICO camino -- un auditor
    local REAL (provider.is_local=1) también habilita una máquina con datos de clientes,
    sin tocar la compuerta (sigue cerrada)."""
    auditor_local_bindeado(is_local=True)
    assert (_estado(client, superadmin)["compuerta_datos_de_clientes"]) == "cerrada"
    assert _estado(client, superadmin)["auditor_local_disponible"] is True
    user_id, h = superadmin
    r = _crear(client, h, maquinas=("t-sp2-vm", "t-sp2-clientes"))
    assert r.status_code == 202
    assert _misiones_de(client, user_id) != ()


def test_peor_caso_auditor_local_mal_bindeado_a_un_proveedor_de_nube_no_abre_nada(
        client, superadmin, maquinas, runner, auditor_local_bindeado):
    """Freno sin prueba no es freno (Principio VII): si 'auditor_local' quedó bindeado a un
    proveedor que NO es local (is_local=0 -- un error de configuración, o alguien apuntó la
    clave al proveedor equivocado), la compuerta sigue siendo la única puerta. Con la
    compuerta cerrada, la misión se rechaza igual que sin ningún auditor local."""
    auditor_local_bindeado(is_local=False)
    assert _estado(client, superadmin)["auditor_local_disponible"] is False
    user_id, h = superadmin
    r = _crear(client, h, maquinas=("t-sp2-vm", "t-sp2-clientes"))
    assert (r.status_code, r.json()["detail"]) == (403, {"codigo": "ejecutor_maquina_no_elegible",
                                                          "maquina": "t-sp2-clientes",
                                                          "motivo": "maquina_con_datos_de_clientes"})
    assert _misiones_de(client, user_id) == ()


def _estado(client, superadmin):
    _, h = superadmin
    return client.get(f"{BASE}/estado", headers=h).json()


@pytest.mark.parametrize("cuerpo, estado, detalle", [
    ({"objetivo": "  ", "maquinas": ["t-sp2-vm"]}, 422, "ejecutor_objetivo_vacio"),
    ({"objetivo": "x", "maquinas": []}, 422, "ejecutor_sin_maquinas"),
    ({"objetivo": "x", "maquinas": ["t-sp2-vm", "t-sp2-vm"]}, 422, "ejecutor_sin_maquinas"),
    ({"objetivo": "x", "maquinas": ["no-existe"]}, 422, {"codigo": "ejecutor_maquina_desconocida", "maquina": "no-existe"}),
    # Un elemento NO HASHEABLE es dato mal tipado del cliente, no una falla del servidor:
    # `len(set(pedidas))` lo evaluaba antes del `isinstance` y reventaba con TypeError -> 500.
    ({"objetivo": "x", "maquinas": [{"host": "t-sp2-vm"}]}, 422, "ejecutor_sin_maquinas"),
    ({"objetivo": "x", "maquinas": [["t-sp2-vm"]]}, 422, "ejecutor_sin_maquinas"),
    ({"objetivo": "x", "maquinas": ["t-sp2-vm", {"host": "t-sp2-vm"}]}, 422, "ejecutor_sin_maquinas"),
])
def test_pedidos_invalidos(client, superadmin, maquinas, runner, cuerpo, estado, detalle):
    user_id, h = superadmin
    r = client.post(f"{BASE}/misiones", headers=h, json=cuerpo)
    assert (r.status_code, r.json()["detail"]) == (estado, detalle)
    assert _misiones_de(client, user_id) == ()


# --- texto del cliente que salia como 500 (auditoria adversarial 2026-09-20) -----------------
#
# Las tres entradas de abajo daban 500, no 422: `objetivo` e `instruccion` van a columnas
# TEXT (65.535 bytes) sin tope declarado, y un surrogate solitario -- que `json.loads`
# acepta -- revienta al codificar a utf-8 en el driver. Es el MISMO defecto que el 422 de
# `maquinas`: dato del cliente contado como falla del servidor.

LARGO = "A" * 70000

#: JSON CRUDO en ASCII: `json.loads` del servidor lo convierte en un surrogate
#: solitario. No se puede mandar con `json=` porque httpx no logra codificarlo
#: del lado del cliente -- y un cliente que no sea httpx lo manda sin esfuerzo.
CUERPO_SURROGATE = b'{"objetivo": "hola \\ud800 mundo", "maquinas": ["t-sp2-vm"]}'
TURNO_SURROGATE = b'{"instruccion": "hola \\ud800 mundo"}'
JSON = {"content-type": "application/json"}


def test_objetivo_mas_largo_que_la_columna_es_422(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    r = client.post(f"{BASE}/misiones", headers=h, json={"objetivo": LARGO, "maquinas": ["t-sp2-vm"]})
    assert (r.status_code, r.json()["detail"]) == (422, "ejecutor_objetivo_largo")
    assert _misiones_de(client, user_id) == ()


def test_objetivo_con_surrogate_solitario_es_422(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    r = client.post(f"{BASE}/misiones", headers={**h, **JSON}, content=CUERPO_SURROGATE)
    assert (r.status_code, r.json()["detail"]) == (422, "ejecutor_objetivo_ilegible")
    assert _misiones_de(client, user_id) == ()


def test_instruccion_mas_larga_que_la_columna_es_422(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion(GUION_BUENO)
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers=h, json={"instruccion": LARGO})
    assert (r.status_code, r.json()["detail"]) == (422, "ejecutor_instruccion_larga")


def test_instruccion_con_surrogate_solitario_es_422(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion(GUION_BUENO)
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers={**h, **JSON}, content=TURNO_SURROGATE)
    assert (r.status_code, r.json()["detail"]) == (422, "ejecutor_instruccion_ilegible")


# --- los dos MINOR de la auditoria adversarial del 2026-09-20 --------------------------------


def test_el_nombre_de_maquina_del_error_va_recortado(client, superadmin, maquinas, runner):
    """El 422 no se hace eco del pedido entero.

    Con un nombre de 5.000 caracteres la respuesta medida era del tamano del
    pedido: el endpoint devolvia `nombre` tal cual. No es un 500 y exige
    superadmin, pero un error no tiene por que repetir lo que le mandaron.
    """
    _, h = superadmin
    r = client.post(f"{BASE}/misiones", headers=h, json={"objetivo": "x", "maquinas": ["z" * 5000]})
    assert r.status_code == 422
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "ejecutor_maquina_desconocida"
    assert len(detalle["maquina"]) <= misiones.LIMITE_ECO, len(detalle["maquina"])


@pytest.mark.parametrize("objetivo", ["hola\x00mundo", "hola\x07mundo", "hola\x1bmundo"])
def test_objetivo_con_caracteres_de_control_es_422(client, superadmin, maquinas, runner, objetivo):
    """Un `\x00` se aceptaba (202), se guardaba, volvia en el detalle y viajaba
    al runner por stdin. No es inyeccion -- va como JSON, nunca por un shell --
    pero es basura que ensucia la bitacora y la interfaz."""
    user_id, h = superadmin
    r = client.post(f"{BASE}/misiones", headers=h, json={"objetivo": objetivo, "maquinas": ["t-sp2-vm"]})
    assert r.status_code == 422, f"se acepto un objetivo con control: {r.status_code} {r.text[:200]}"
    assert r.json()["detail"] == "ejecutor_objetivo_ilegible"
    assert _misiones_de(client, user_id) == ()


def test_el_salto_de_linea_y_el_tab_siguen_siendo_texto_valido(client, superadmin, maquinas, runner):
    """El control de arriba no puede pasarse de listo: un objetivo de varias
    lineas es legitimo y tiene que seguir entrando."""
    _, h = superadmin
    runner.guion(GUION_BUENO)
    r = _crear(client, h, objetivo="primera linea\nsegunda\tcon tab")
    assert r.status_code == 202, r.json()


def test_instruccion_con_caracteres_de_control_es_422(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion(GUION_BUENO)
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers=h,
                    json={"instruccion": "sigue\x00ahora"})
    assert (r.status_code, r.json()["detail"]) == (422, "ejecutor_instruccion_ilegible")


def test_con_la_pausa_puesta_no_se_lanza(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    pausa.poner_pausa(pausa.ruta_de_la_pausa(), {"origen": "c5", "motivo": "prohibido"})
    r = _crear(client, h)
    assert (r.status_code, r.json()["detail"]) == (423, "ejecutor_pausado")
    assert _misiones_de(client, user_id) == ()


def test_sin_runner_configurado_503_y_nada_escrito(client, superadmin, maquinas):
    user_id, h = superadmin
    r = _crear(client, h)
    assert (r.status_code, r.json()["detail"]) == (503, "ejecutor_sin_configurar")
    assert _misiones_de(client, user_id) == ()


# --- el ciclo completo ----------------------------------------------------------------------

def test_mision_completa_con_resultado_bitacora_y_sesion(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    runner.guion(GUION_BUENO)
    r = _crear(client, h)
    assert r.status_code == 202
    creada = r.json()
    assert creada["estado"] == "en_curso" and creada["maquinas"] == ["t-sp2-vm"]
    d = _esperar(client, h, creada["id"])
    assert d["estado"] == "completada" and d["puede_continuar"] is True
    (t,) = d["turnos"]
    assert (t["n"], t["estado"], t["codigo"], t["instruccion"]) == (1, "completado", None, "memoria de la VM")
    assert t["afirmaciones"] == [AFIRMACION] and t["crudas"][0]["salida"] == AFIRMACION["linea"] + "\n"
    assert t["verificacion"]["registro_cuadra"] is True and t["terminado_at"]
    pedido = runner.pedido()
    assert pedido == {"mision_id": creada["id"], "n": 1, "sesion": pedido["sesion"], "objetivo": "memoria de la VM",
                      "instruccion": "memoria de la VM", "hosts": ["t-sp2-vm"]}
    assert str(uuid.UUID(pedido["sesion"])) == pedido["sesion"]
    eventos = client.get(f"{BASE}/misiones/{creada['id']}/bitacora", headers=h).json()["eventos"]
    assert [e["evento"] for e in eventos] == ["mision_creada", "turno_lanzado", "arranque_verificado", "vigia_late",
                                              "afirmacion_entregada", "turno_completado"]
    assert eventos[4]["datos"] == AFIRMACION and eventos[4]["turno"] == 1 and eventos[0]["at"].endswith("+00:00")
    desde = eventos[2]["id"]
    parcial = client.get(f"{BASE}/misiones/{creada['id']}/bitacora?desde={desde}", headers=h).json()["eventos"]
    assert [e["id"] for e in parcial] == [e["id"] for e in eventos[3:]]
    lista = client.get(f"{BASE}/misiones", headers=h).json()["misiones"]
    (mia,) = [m for m in lista if m["id"] == creada["id"]]
    assert (mia["estado"], mia["turnos"], mia["objetivo"]) == ("completada", 1, "memoria de la VM")

    # El turno siguiente retoma la MISMA sesión.
    runner.guion({"lineas": [_ev("turno_lanzado", 2, reanudar=True), _resultado(turno=2)]})
    r2 = client.post(f"{BASE}/misiones/{creada['id']}/turnos", headers=h, json={"instruccion": "y el disco"})
    assert r2.status_code == 202
    d2 = _esperar(client, h, creada["id"])
    assert [t["n"] for t in d2["turnos"]] == [1, 2]
    p2 = runner.pedido(2)
    assert (p2["n"], p2["sesion"], p2["instruccion"], p2["objetivo"]) == (2, pedido["sesion"], "y el disco",
                                                                          "memoria de la VM")


def test_un_turno_a_la_vez(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion({"lineas": [_ev("turno_lanzado"), "@esperar", _resultado()]})
    creada = _crear(client, h).json()
    estado = client.get(f"{BASE}/estado", headers=h).json()
    assert estado["turno_en_curso"] == {"mision_id": creada["id"], "n": 1}
    r = _crear(client, h)
    assert (r.status_code, r.json()["detail"]) == (409, "ejecutor_turno_en_curso")
    r = client.post(f"{BASE}/misiones/{creada['id']}/turnos", headers=h, json={"instruccion": "otra"})
    assert (r.status_code, r.json()["detail"]) == (409, "ejecutor_turno_en_curso")
    d = client.get(f"{BASE}/misiones/{creada['id']}", headers=h).json()
    assert d["estado"] == "en_curso" and d["puede_continuar"] is False
    runner.seguir()
    assert _esperar(client, h, creada["id"])["estado"] == "completada"


def test_rechazo_del_arranque_llega_con_sus_codigos(client, superadmin, maquinas, runner):
    _, h = superadmin
    rechazo = [{"contrato": "c4", "codigo": "freno_sin_latido", "datos": {}}]
    runner.guion({"lineas": [_ev("turno_lanzado"), _ev("arranque_rechazado", fallos=rechazo),
                             _ev("turno_rechazado", codigo="arranque_rechazado"),
                             _resultado("rechazado", "arranque_rechazado", rechazo=rechazo, sesion_iniciada=False,
                                        crudas=[], verificacion={})], "rc": 1})
    d = _esperar(client, h, _crear(client, h).json()["id"])
    assert d["estado"] == "rechazada" and d["puede_continuar"] is False
    (t,) = d["turnos"]
    assert (t["estado"], t["codigo"], t["rechazo"]) == ("rechazado", "arranque_rechazado", rechazo)


def test_runner_que_muere_sin_resultado_es_fallo_con_codigo(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion({"lineas": [_ev("turno_lanzado"), "esto no es json"], "rc": 3})
    mision_id = _crear(client, h).json()["id"]
    d = _esperar(client, h, mision_id)
    (t,) = d["turnos"]
    assert (d["estado"], t["estado"], t["codigo"]) == ("fallida", "fallido", "runner_sin_cierre")
    assert t["crudas"] == [] and t["afirmaciones"] == []
    eventos = [e["evento"] for e in client.get(f"{BASE}/misiones/{mision_id}/bitacora", headers=h).json()["eventos"]]
    assert eventos == ["mision_creada", "turno_lanzado", "runner_salida_invalida", "turno_fallido"]


def test_resultado_con_estado_desconocido_no_se_toma_por_bueno(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion({"lineas": [_resultado("fantastico")]})
    d = _esperar(client, h, _crear(client, h).json()["id"])
    assert (d["turnos"][0]["estado"], d["turnos"][0]["codigo"]) == ("fallido", "runner_salida_invalida")


def test_sin_configurar_del_runner_llega_como_codigo(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion({"lineas": [{"evento": "resultado", "turno": 1,
                              "datos": {"estado": "fallido", "codigo": "sin_configurar",
                                        "detalle": "JAX_EJECUTOR_TURNO_TOPE_S"}}], "rc": 2})
    d = _esperar(client, h, _crear(client, h).json()["id"])
    assert (d["turnos"][0]["estado"], d["turnos"][0]["codigo"]) == ("fallido", "sin_configurar")


def test_continuar_exige_sesion_iniciada(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion({"lineas": [_resultado("fallido", "vigia_no_latio", sesion_iniciada=False)], "rc": 1})
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers=h, json={"instruccion": "otra"})
    assert (r.status_code, r.json()["detail"]) == (409, "ejecutor_mision_sin_sesion")


def test_continuar_valida_la_instruccion_la_mision_y_la_pausa(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion(GUION_BUENO)
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    r = client.post(f"{BASE}/misiones/{uuid.uuid4()}/turnos", headers=h, json={"instruccion": "x"})
    assert (r.status_code, r.json()["detail"]) == (404, "ejecutor_mision_inexistente")
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers=h, json={"instruccion": " "})
    assert (r.status_code, r.json()["detail"]) == (422, "ejecutor_instruccion_vacia")
    pausa.poner_pausa(pausa.ruta_de_la_pausa(), {"origen": "c5", "motivo": "prohibido"})
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers=h, json={"instruccion": "x"})
    assert (r.status_code, r.json()["detail"]) == (423, "ejecutor_pausado")
    assert client.get(f"{BASE}/misiones/{uuid.uuid4()}", headers=h).status_code == 404


def test_continuar_revalida_la_compuerta(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion(GUION_BUENO)
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    client.portal.call(sql, "UPDATE ejecutor_host SET con_datos_de_clientes = TRUE WHERE nombre = 't-sp2-vm'")
    r = client.post(f"{BASE}/misiones/{mision_id}/turnos", headers=h, json={"instruccion": "x"})
    assert (r.status_code, r.json()["detail"]["codigo"]) == (403, "ejecutor_maquina_no_elegible")


def test_al_arrancar_un_turno_en_curso_queda_interrumpido(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    runner.guion({"lineas": ["@esperar"]})
    mision_id = _crear(client, h).json()["id"]
    n = client.portal.call(misiones.reconciliar_al_arrancar)
    assert n >= 1
    d = client.get(f"{BASE}/misiones/{mision_id}", headers=h).json()
    assert (d["estado"], d["turnos"][0]["codigo"]) == ("interrumpida", "plataforma_reiniciada")
    eventos = client.get(f"{BASE}/misiones/{mision_id}/bitacora", headers=h).json()["eventos"]
    assert eventos[-1]["evento"] == "turno_interrumpido" and eventos[-1]["datos"] == {"codigo": "plataforma_reiniciada"}
    runner.seguir()
    time.sleep(0.3)
    # El runner viejo termina sin resultado: no pisa el estado reconciliado.
    assert client.get(f"{BASE}/misiones/{mision_id}", headers=h).json()["estado"] == "interrumpida"


# --- el kill switch del modo -----------------------------------------------------------------

async def _auditoria_pausa(user_id):
    filas = await sql("SELECT accion FROM ejecutor_pausa_audit WHERE user_id = %s ORDER BY id", (user_id,), True)
    return [f[0] for f in filas]


def test_poner_y_quitar_la_pausa_con_auditoria(client, superadmin):
    user_id, h = superadmin
    r = client.post(f"{BASE}/pausa/poner", headers=h)
    assert r.status_code == 200 and r.json()["puesta"] is True and r.json()["origen"] == "plataforma"
    assert r.json()["motivo"] == "pausa_manual"
    assert pausa.pausa_puesta(pausa.ruta_de_la_pausa())
    assert client.post(f"{BASE}/pausa/poner", headers=h).json()["puesta"] is True  # no pisa ni re-audita
    assert client.get(f"{BASE}/estado", headers=h).json()["pausa"]["puesta"] is True
    r = client.post(f"{BASE}/pausa/quitar", headers=h)
    assert (r.status_code, r.json()["puesta"]) == (200, False)
    assert client.post(f"{BASE}/pausa/quitar", headers=h).json()["puesta"] is False
    assert client.portal.call(_auditoria_pausa, user_id) == ["poner", "quitar"]


def test_quitar_una_pausa_de_c5_la_audita(client, superadmin):
    user_id, h = superadmin
    pausa.poner_pausa(pausa.ruta_de_la_pausa(), {"origen": "c5", "motivo": "fuera_de_mision", "paso": 2})
    assert client.get(f"{BASE}/estado", headers=h).json()["pausa"]["motivo"] == "fuera_de_mision"
    assert client.post(f"{BASE}/pausa/quitar", headers=h).json()["puesta"] is False
    assert client.portal.call(_auditoria_pausa, user_id) == ["quitar"]


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_pausa_no_escribible_503_sin_auditoria(client, superadmin):
    user_id, h = superadmin
    carpeta = pausa.ruta_de_la_pausa().parent
    carpeta.chmod(0o500)
    try:
        r = client.post(f"{BASE}/pausa/poner", headers=h)
    finally:
        carpeta.chmod(0o700)
    assert (r.status_code, r.json()["detail"]) == (503, "ejecutor_pausa_no_escribible")
    assert client.portal.call(_auditoria_pausa, user_id) == []


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
def test_quitar_sin_permiso_deja_la_pausa_y_no_audita(client, superadmin):
    user_id, h = superadmin
    ruta = pausa.ruta_de_la_pausa()
    pausa.poner_pausa(ruta, {"origen": "c5", "motivo": "prohibido"})
    ruta.parent.chmod(0o500)
    try:
        r = client.post(f"{BASE}/pausa/quitar", headers=h)
    finally:
        ruta.parent.chmod(0o700)
    assert (r.status_code, r.json()["detail"]) == (503, "ejecutor_pausa_no_escribible")
    assert pausa.pausa_puesta(ruta) and client.portal.call(_auditoria_pausa, user_id) == []


def test_auditoria_fallida_al_quitar_repone_la_pausa(client, superadmin, monkeypatch):
    _, h = superadmin
    ruta = pausa.ruta_de_la_pausa()
    pausa.poner_pausa(ruta, {"origen": "c5", "motivo": "prohibido"})
    monkeypatch.setattr(misiones, "SQL_AUDITAR_PAUSA", "INSERT INTO tabla_que_no_existe (x) VALUES (%s, %s)")
    r = client.post(f"{BASE}/pausa/quitar", headers=h)
    assert (r.status_code, r.json()["detail"]) == (500, "ejecutor_pausa_auditoria_fallida")
    assert pausa.pausa_puesta(ruta)


def test_si_la_transaccion_cae_despues_de_quitar_la_pausa_se_repone(client, superadmin, monkeypatch):
    """La auditoría y el borrado van en la misma transacción; si algo cae DESPUÉS de borrar
    (p.ej. el commit), la auditoría se revierte y la pausa se vuelve a poner: ante la duda, frenado."""
    _, h = superadmin
    ruta = pausa.ruta_de_la_pausa()
    pausa.poner_pausa(ruta, {"origen": "c5", "motivo": "prohibido"})
    real = pausa.quitar_pausa

    def quita_y_cae(r):
        real(r)
        raise RuntimeError("se_cayo_la_base")
    monkeypatch.setattr(pausa, "quitar_pausa", quita_y_cae)
    r = client.post(f"{BASE}/pausa/quitar", headers=h)
    assert (r.status_code, r.json()["detail"]) == (500, "ejecutor_pausa_auditoria_fallida")
    leida = pausa.leer_pausa(ruta)
    assert (leida["puesta"], leida["origen"], leida["motivo"]) == (True, "plataforma", "repuesta_sin_auditoria")


def test_auditoria_fallida_al_poner_deja_la_pausa_puesta(client, superadmin, monkeypatch):
    _, h = superadmin
    monkeypatch.setattr(misiones, "SQL_AUDITAR_PAUSA", "INSERT INTO tabla_que_no_existe (x) VALUES (%s, %s)")
    r = client.post(f"{BASE}/pausa/poner", headers=h)
    assert (r.status_code, r.json()["detail"]) == (500, "ejecutor_pausa_auditoria_fallida")
    assert pausa.pausa_puesta(pausa.ruta_de_la_pausa())


# --- LAS CUATRO DEL RENDIMIENTO: las consultas del sondeo usan su índice --------------------

def _plan(client, consulta, args):
    async def medir():
        for tabla in ("ejecutor_mision", "ejecutor_turno", "ejecutor_bitacora"):
            await sql(f"ANALYZE TABLE {tabla}", (), True)
        return await sql("EXPLAIN " + consulta, args, True)
    return client.portal.call(medir)


async def _volumen(user_id, misiones_n=30, eventos_por_mision=40):
    """Un plan sobre tablas casi vacías no dice nada (el optimizador recorre la PRIMARY): se
    mide con volumen, como el EXPLAIN del kill switch."""
    for _ in range(misiones_n):
        otra = str(uuid.uuid4())
        await sql("INSERT INTO ejecutor_mision (id, user_id, objetivo, maquinas, sesion_id, created_at, updated_at) "
                  "VALUES (%s, %s, 'volumen', '[]', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                  (otra, user_id, str(uuid.uuid4())))
        await sql("INSERT INTO ejecutor_turno (mision_id, n, instruccion, estado, iniciado_at) "
                  "VALUES (%s, 1, 'volumen', 'completado', UTC_TIMESTAMP(6))", (otra,))
        await sql("INSERT INTO ejecutor_bitacora (mision_id, turno, evento, datos, at) "
                  "SELECT %s, 1, 'paso', '{}', UTC_TIMESTAMP(6) FROM seq_1_to_" + str(eventos_por_mision), (otra,))


def test_las_consultas_del_sondeo_usan_indice_sin_filesort(client, superadmin, maquinas, runner):
    user_id, h = superadmin
    runner.guion(GUION_BUENO)
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    client.portal.call(_volumen, user_id)
    for consulta, args, tabla, indice in (
        (misiones.SQL_BITACORA, (mision_id, 0), "ejecutor_bitacora", "idx_ejecutor_bitacora_mision"),
        (misiones.SQL_TURNO_EN_CURSO, (), "ejecutor_turno", "idx_ejecutor_turno_estado"),
        (misiones.SQL_TURNOS, (mision_id,), "ejecutor_turno", "uk_ejecutor_turno_mision_n"),
    ):
        plan = _plan(client, consulta, args)
        fila = [f for f in plan if f[2] == tabla][0]
        assert fila[5] == indice, plan
        assert all("filesort" not in (f[9] or "") and "temporary" not in (f[9] or "") for f in plan), plan
    for consulta, args, tabla, indice in (
        (misiones.SQL_LISTAR, (20,), "ejecutor_mision", "idx_ejecutor_mision_actualizada"),
        (misiones.SQL_ULTIMO_TURNO, (mision_id,), "t", "uk_ejecutor_turno_mision_n"),
    ):
        plan = _plan(client, consulta, args)
        assert [f for f in plan if f[2] == tabla][0][5] == indice, plan
        assert all("filesort" not in (f[9] or "") and "temporary" not in (f[9] or "") for f in plan), plan


def test_el_evento_de_cierre_va_con_el_cierre_del_turno(client, superadmin, maquinas, runner, monkeypatch):
    """Carrera real vista en CI (2026-09-17, PR #105 de otra sesión): el turno se cerraba en una
    transacción y el evento de cierre se anotaba DESPUÉS del commit. Quien leía en esa ventana veía
    la misión ya terminal con la bitácora un evento atrás, y la comparación de la lista completa
    fallaba. El cierre y su evento van juntos: si el evento no se puede escribir, el turno no queda
    cerrado a medias."""
    import ejecutor.misiones as M
    runner.guion({"lineas": [_ev("turno_lanzado"), "esto no es json"], "rc": 3})
    vistos = []
    cerrar = M._cerrar_turno

    async def espia(mision_id, n, estado_t, codigo, resultado, evento=None):
        # Al volver de cerrar, el estado y la bitácora ya tienen que estar de acuerdo.
        ok = await cerrar(mision_id, n, estado_t, codigo, resultado, evento)
        vistos.append((ok, evento))
        return ok
    monkeypatch.setattr(M, "_cerrar_turno", espia)
    mision_id = _crear(client, h_de(superadmin)).json()["id"]
    d = _esperar(client, h_de(superadmin), mision_id)
    eventos = [e["evento"] for e in client.get(f"{BASE}/misiones/{mision_id}/bitacora",
                                               headers=h_de(superadmin)).json()["eventos"]]
    assert d["estado"] == "fallida"
    assert eventos == ["mision_creada", "turno_lanzado", "runner_salida_invalida", "turno_fallido"]
    assert vistos and vistos[0][1] == ("turno_fallido", {"codigo": "runner_sin_cierre"})


def h_de(superadmin):
    return superadmin[1]


# --- El stderr del runner no se tira a la basura (2026-09-20) ----------------------------------
# `_correr_turno` lanzaba el runner con `stderr=asyncio.subprocess.DEVNULL`. Cuando el
# runner muere sin resultado, el turno queda en `runner_sin_cierre` y NO hay una sola
# linea para investigar. Mismo defecto que el vigia (jax#231) y misma familia que las dos
# veces anteriores del mismo dia.
#
# Igual que alli, NO alcanza con cambiar DEVNULL por PIPE: el stdout ya se drena en el
# bucle, pero un stderr sin drenar llena el pipe (~64 KB) y cuelga al runner. Se drena en
# continuo, con tope, conservando la COLA -- la traza esta al final.

def test_el_stderr_del_runner_llega_a_la_bitacora_cuando_no_hay_cierre(client, superadmin, maquinas, runner):
    _, h = superadmin
    runner.guion({"lineas": [_ev("turno_lanzado")],
                  "stderr": ["ModuleNotFoundError: No module named 'jax.ejecutor'"], "rc": 1})
    mision_id = _crear(client, h).json()["id"]
    d = _esperar(client, h, mision_id)
    (t,) = d["turnos"]
    assert t["codigo"] == "runner_sin_cierre"
    eventos = client.get(f"{BASE}/misiones/{mision_id}/bitacora", headers=h).json()["eventos"]
    err = [e for e in eventos if e["evento"] == "runner_stderr"]
    assert err, [e["evento"] for e in eventos]
    assert "ModuleNotFoundError" in err[0]["datos"].get("stderr", "")


def test_el_stderr_NO_ensucia_la_bitacora_cuando_el_turno_cierra_bien(client, superadmin, maquinas, runner):
    """Un log que siempre grita es un log que nadie lee."""
    runner.guion({"lineas": [_ev("turno_lanzado"), _ev("turno_completado"), _resultado()],
                  "stderr": ["INFO: ruido que no importa"], "rc": 0})
    _, h = superadmin
    mision_id = _crear(client, h).json()["id"]
    _esperar(client, h, mision_id)
    eventos = [e["evento"] for e in client.get(f"{BASE}/misiones/{mision_id}/bitacora", headers=h).json()["eventos"]]
    assert "runner_stderr" not in eventos, eventos


def test_un_runner_que_escribe_MUCHO_en_stderr_no_se_cuelga(client, superadmin, maquinas, runner):
    """La prueba del diseno: sin drenaje el runner se bloquea al llenar el pipe y el
    turno no termina nunca."""
    runner.guion({"lineas": [_ev("turno_lanzado"), _ev("turno_completado"), _resultado()],
                  "stderr": ["INFO:httpx:HTTP Request: POST ... 200 OK"] * 4000, "rc": 0})
    _, h = superadmin
    d = _esperar(client, h, _crear(client, h).json()["id"])
    (t,) = d["turnos"]
    assert t["estado"] == "completado", t
