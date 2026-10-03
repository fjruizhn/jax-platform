"""Tests puros (sin DB ni backend) de los helpers de `proyectos_e2a.py` (E2a, T12).

`python3 -m pytest loadtest/test_proyectos_e2a.py -q`
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import proyectos_e2a as pe  # noqa: E402



def _tipos_del_backend():
    """La lista que publica el backend (`GET /api/proyectos/documentos/limites` la saca de aca),
    cargada por ruta: este job no instala las dependencias del backend y `tipos.py` no tiene."""
    import importlib.util
    ruta = Path(__file__).resolve().parent.parent / "backend" / "proyectos_documentos" / "tipos.py"
    spec = importlib.util.spec_from_file_location("tipos_del_backend", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


TIPOS = _tipos_del_backend()
# Con punto, como las usa `plan_de_lote` (la API las publica sin punto; `medir` les agrega el punto).
EXT = frozenset("." + e for e in TIPOS.EXTENSIONES_ACEPTADAS)
CRED = "c" * 43


# --- Tamanos de referencia: solo stat, nunca el contenido -----------------------------------
def test_tamanos_de_referencia_usa_solo_stat_y_no_lee_el_contenido(tmp_path):
    (tmp_path / "sub").mkdir()
    a, b = tmp_path / "a.pdf", tmp_path / "sub" / "b.html"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"y" * 25)
    os.chmod(a, 0o000)   # si algo intentara abrirlo, fallaria: stat no necesita permiso de lectura
    os.chmod(b, 0o000)
    try:
        assert sorted(pe.tamanos_de_referencia(tmp_path)) == [10, 25]
    finally:
        os.chmod(a, 0o600)
        os.chmod(b, 0o600)


def test_tamanos_de_referencia_no_sigue_enlaces(tmp_path):
    real = tmp_path / "real.bin"
    real.write_bytes(b"z" * 7)
    (tmp_path / "enlace").symlink_to(real)
    assert pe.tamanos_de_referencia(tmp_path) == [7]


# --- Plan del lote: tamanos reales, extensiones aceptadas, nombres unicos --------------------
def test_plan_de_lote_conserva_cantidad_y_total_y_solo_extensiones_aceptadas():
    tamanos = [1, 5, 5, 9000, 12, 0 + 3]
    plan = pe.plan_de_lote(tamanos, EXT)
    assert len(plan) == len(tamanos)
    assert sum(b for _, b in plan) == sum(tamanos)
    assert sorted(b for _, b in plan) == sorted(tamanos)
    assert all(Path(n).suffix in EXT for n, _ in plan)
    assert len({n for n, _ in plan}) == len(plan)


def test_las_extensiones_de_la_prueba_son_las_que_publica_el_backend():
    assert EXT == {"." + e for e in TIPOS.EXTENSIONES_ACEPTADAS}
    assert ".xlsm" in EXT and ".csv" not in EXT and ".txt" not in EXT    # la compuerta de jax vigente
    plan = pe.plan_de_lote(list(range(1, 30)), EXT)
    assert all(TIPOS.tipo_de(n) is not None for n, _ in plan)        # todo el lote lo aceptaria la API


def test_plan_de_lote_es_determinista():
    assert pe.plan_de_lote([3, 1, 2], EXT) == pe.plan_de_lote([3, 1, 2], EXT)


def test_plan_de_lote_rechaza_si_no_hay_extensiones():
    with pytest.raises(ValueError):
        pe.plan_de_lote([1], frozenset())


def test_escribir_lote_hace_archivos_del_tamano_exacto_y_distintos(tmp_path):
    plan = [("a.pdf", 1000), ("b.txt", 0 + 70_000), ("c.png", 1)]
    rutas = pe.escribir_lote(tmp_path, plan)
    assert [r.stat().st_size for r in rutas] == [1000, 70_000, 1]
    assert rutas[0].read_bytes() != rutas[1].read_bytes()[:1000]


# --- Fases del chat -------------------------------------------------------------------------
def test_por_fase_reparte_las_muestras_por_su_instante_de_inicio():
    muestras = [(1.0, 10.0), (4.9, 11.0), (5.0, 50.0), (7.9, 60.0), (8.0, 20.0), (11.9, 21.0), (12.0, 9.0)]
    fases = pe.por_fase(muestras, subidas=[(5.0, 8.0)], procesos=[(8.0, 12.0)])
    assert fases == {"antes": [10.0, 11.0], "subida": [50.0, 60.0], "procesamiento": [20.0, 21.0],
                     "reposo": [], "despues": [9.0]}


def test_por_fase_con_varias_subidas_deja_el_hueco_entre_ellas_como_reposo():
    muestras = [(1.0, 1.0), (5.5, 2.0), (7.0, 3.0), (9.0, 4.0), (10.5, 5.0), (13.0, 6.0), (20.0, 7.0)]
    fases = pe.por_fase(muestras, subidas=[(5.0, 6.0), (10.0, 11.0)], procesos=[(6.0, 8.0), (11.0, 14.0)])
    assert fases["antes"] == [1.0]
    assert fases["subida"] == [2.0, 5.0]
    assert fases["procesamiento"] == [3.0, 6.0]
    assert fases["reposo"] == [4.0]
    assert fases["despues"] == [7.0]


def test_por_fase_sin_subidas_todo_es_antes():
    assert pe.por_fase([(1.0, 1.0)], subidas=[], procesos=[]) == {
        "antes": [1.0], "subida": [], "procesamiento": [], "reposo": [], "despues": []}


# --- Instantes de la cola (sondeo de la base) -----------------------------------------------------
# muestras: (t, total_de_filas, en_cola, terminales)
SONDEO = [(1.0, 40, 40, 0), (2.0, 120, 30, 0), (3.0, 120, 0, 0), (4.0, 120, 0, 70), (5.0, 120, 0, 120),
          (6.0, 240, 100, 120), (7.0, 240, 0, 130), (8.0, 240, 0, 240)]


def test_instante_sin_en_cola_exige_el_lote_completo_registrado():
    assert pe.instante_en_que(SONDEO, desde=0.0, esperadas=120, que="sin_en_cola") == 3.0
    assert pe.instante_en_que(SONDEO, desde=5.5, esperadas=240, que="sin_en_cola") == 7.0


def test_instante_terminal_exige_todas_las_filas_terminales():
    assert pe.instante_en_que(SONDEO, desde=0.0, esperadas=120, que="terminal") == 5.0
    assert pe.instante_en_que(SONDEO, desde=5.5, esperadas=240, que="terminal") == 8.0


def test_instante_que_no_llega_es_none():
    assert pe.instante_en_que(SONDEO[:3], desde=0.0, esperadas=120, que="terminal") is None
    with pytest.raises(ValueError):
        pe.instante_en_que(SONDEO, desde=0.0, esperadas=120, que="otra")


# --- Planes de EXPLAIN ------------------------------------------------------------------------
def test_alertas_de_plan_busca_filesort_y_temporary_en_cualquier_fila():
    limpio = [{"Extra": "Using where; Using index"}, {"Extra": None}]
    sucio = [{"Extra": "Using where"}, {"Extra": "Using temporary; Using filesort"}]
    assert pe.alertas_de_plan(limpio) == []
    assert pe.alertas_de_plan(sucio) == ["Using filesort", "Using temporary"]


# --- RSS ----------------------------------------------------------------------------------------
def test_rss_kb_lee_vmrss_y_vmhwm_del_status():
    texto = "Name:\tuvicorn\nVmHWM:\t  123456 kB\nVmRSS:\t   99000 kB\nThreads:\t4\n"
    assert pe.leer_rss_kb(texto) == {"rss_kb": 99000, "pico_kb": 123456}
    assert pe.leer_rss_kb("Name:\tx\n") == {"rss_kb": None, "pico_kb": None}


# --- LAS MANOS falso ----------------------------------------------------------------------------
def _pedir(url, *, metodo="GET", cuerpo=None, cred=CRED):
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    req = urllib.request.Request(url, data=datos, method=metodo,
                                 headers={"Content-Type": "application/json", **({pe.ENCABEZADO_CREDENCIAL: cred} if cred else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


@pytest.fixture
def manos():
    srv = pe.ManosFalsas(("127.0.0.1", 0), credencial=CRED, retraso_s=0.4)
    srv.arrancar()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    finally:
        srv.parar()


def test_manos_falsas_acepta_el_trabajo_y_lo_completa_tras_el_retraso(manos):
    url, srv = manos
    rutas = ["proyectos/u/entrada/l/a.pdf", "proyectos/u/entrada/l/b.pdf"]
    st, cuerpo = _pedir(f"{url}/procesamiento/trabajos", metodo="POST",
                        cuerpo={"project_uuid": "u", "rutas": rutas, "usuario": "x@y"})
    assert st == 202 and isinstance(cuerpo["job_id"], str) and cuerpo["job_id"]
    st, antes = _pedir(f"{url}/procesamiento/trabajos/{cuerpo['job_id']}")
    assert st == 200 and antes["estado"] == "running" and antes["resultados"] == []
    time.sleep(0.6)
    st, despues = _pedir(f"{url}/procesamiento/trabajos/{cuerpo['job_id']}")
    assert st == 200 and despues["estado"] == "completed"
    assert [r["archivo"] for r in despues["resultados"]] == rutas
    assert all(r["estado"] == "ok" and r["carpeta_procesado"] for r in despues["resultados"])
    assert srv.trabajos_recibidos == 1 and srv.rutas_recibidas == 2


def test_manos_falsas_exige_la_credencial_y_responde_404_a_un_trabajo_desconocido(manos):
    url, _ = manos
    assert _pedir(f"{url}/procesamiento/trabajos", metodo="POST", cuerpo={}, cred=None)[0] == 401
    assert _pedir(f"{url}/procesamiento/trabajos", metodo="POST", cuerpo={}, cred="mala" * 12)[0] == 401
    assert _pedir(f"{url}/procesamiento/trabajos/no-existe")[0] == 404


def test_manos_falsas_rechaza_un_pedido_mal_formado_o_con_demasiadas_rutas(manos):
    url, _ = manos
    assert _pedir(f"{url}/procesamiento/trabajos", metodo="POST", cuerpo={"project_uuid": "u"})[0] == 422
    st, cuerpo = _pedir(f"{url}/procesamiento/trabajos", metodo="POST",
                        cuerpo={"project_uuid": "u", "rutas": [f"r{i}" for i in range(51)], "usuario": "x"})
    assert st == 422 and cuerpo["detail"].startswith("demasiadas rutas")


# --- Seguridad de la corrida --------------------------------------------------------------------
def test_la_corrida_no_puede_apuntar_a_produccion():
    for puerto in (7777, 8080, 5173, 3306, 18080, 18180, 15173):
        assert puerto not in {pe.BACKEND_PORT, pe.VITE_PORT, pe.MANOS_PORT}
    assert pe.PREFIJO.startswith("carga-e2a-")


def test_workspace_de_la_corrida_nunca_es_el_real(tmp_path):
    real = Path.home() / "jax-workspace"
    with pytest.raises(RuntimeError):
        pe.verificar_workspace_propio(real)
    with pytest.raises(RuntimeError):
        pe.verificar_workspace_propio(real / "proyectos")
    pe.verificar_workspace_propio(tmp_path)   # no lanza


# --- Ronda final, menor 11: los resultados de `medir` se escriben FUERA del repo ---------------
def test_resultados_van_fuera_del_repo_por_defecto(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ruta = pe.ruta_de_resultados({})
    assert ruta == tmp_path / ".cache" / "jax-loadtest" / "_resultados_proyectos_e2a.json"
    assert ruta.parent.is_dir()
    assert pe.LOADTEST_DIR.resolve() not in ruta.resolve().parents


def test_resultados_en_la_carpeta_que_diga_el_entorno(tmp_path):
    destino = tmp_path / "otra" / "carpeta"
    ruta = pe.ruta_de_resultados({pe.VARIABLE_RESULTADOS: str(destino)})
    assert ruta == destino / "_resultados_proyectos_e2a.json" and destino.is_dir()


# --- Escenario «Reprocesar» (jax-platform#186) --------------------------------------------------
def test_plan_de_fuente_reparte_todo_sin_repetir_y_con_niveles_profundos():
    rutas = pe.plan_de_fuente(20_000, 2_000)
    assert len(rutas) == 20_000 and len(set(rutas)) == 20_000
    assert len({r.split("/")[0] for r in rutas}) == 2_000
    assert any(r.count("/") >= 6 for r in rutas)                       # alguna carpeta cuelga de 5 niveles mas
    assert not any(r.startswith("zzz/") for r in rutas)


def test_plan_de_fuente_con_resto_y_validaciones():
    rutas = pe.plan_de_fuente(10, 3)
    assert len(rutas) == 10 and len(set(rutas)) == 10
    for malo in ((0, 1), (5, 0), (3, 5)):
        with pytest.raises(ValueError):
            pe.plan_de_fuente(*malo)


def test_el_objetivo_va_al_final_del_orden_y_hondo_con_un_nombre_que_no_se_parece():
    r = pe.ruta_del_objetivo(7)
    assert r == "zzz/n1/n2/n3/n4/n5/n6/n7/scan-07.pdf" and r.count("/") == 8
    assert all(r > x for x in pe.plan_de_fuente(100, 10))
    assert "Informe-7" not in r
    with pytest.raises(ValueError):
        pe.ruta_del_objetivo(1, 0)


def test_escribir_fuente_escribe_del_tamano_pedido_con_bytes_distintos(tmp_path):
    rutas = ["a/x.pdf", "a/y.pdf", "b/c/z.pdf"]
    assert pe.escribir_fuente(tmp_path, rutas, 64) == 3
    contenidos = [(tmp_path / r).read_bytes() for r in rutas]
    assert all(len(c) == 64 for c in contenidos) and len(set(contenidos)) == 3


def test_peticiones_por_segundo():
    assert pe.peticiones_por_segundo(150, 60) == 2.5
    assert pe.peticiones_por_segundo(5, 0) == 0.0


def test_resumen_de_reprocesar_cuenta_codigos_y_separa_las_202():
    muestras = [{"status": 202, "ms": 100.0}, {"status": 202, "ms": 300.0}, {"status": 429, "ms": 5.0},
                {"status": 429, "ms": 6.0}, {"status": 503, "ms": 9.0}]
    r = pe.resumen_de_reprocesar(muestras, 10.0)
    assert r["codigos"] == {"202": 2, "429": 2, "503": 1}
    assert (r["peticiones"], r["rps_total"], r["rps_202"]) == (5, 0.5, 0.2)
    assert r["latencia_202_ms"]["n"] == 2 and r["latencia_202_ms"]["p50_ms"] == 100.0
    assert r["latencia_todas_ms"]["n"] == 5
    assert pe.resumen_de_reprocesar([], 10.0)["latencia_202_ms"]["n"] == 0


def test_contar_descriptores_de_este_proceso_ve_uno_nuevo(tmp_path):
    antes = pe.contar_descriptores(os.getpid())
    with open(tmp_path / "x", "w"):
        assert pe.contar_descriptores(os.getpid()) == antes + 1
