"""Task 1 del plan 2026-09-15-cola-durable-uso: el respaldo en disco de las
filas de uso que no pudieron entrar a `axioma_usage`.

El respaldo es un DIRECTORIO con un archivo por fila, no un archivo con
todas las líneas: hay TRES procesos que depositan (jax-platform, jacobs y
motor_registry) y uno solo que drena. Con un archivo único, el que drena
tendría que reescribirlo sin las filas ya insertadas, y esa reescritura pisa
lo que otro proceso agregó entremedio -- se perderían filas justo en el
mecanismo que existe para no perderlas.

Tests PUROS: el módulo no conoce la base ni FastAPI, y el fixture autouse
manda `JAX_USAGE_SPOOL_DIR` a `tmp_path`, así que ni una corrida de la suite
roza `/srv/jax-data/usage-spool/`. Corren igual con y sin `JAX_CI_NO_DB=1`.
"""
import ast
import asyncio
import json
import os
import stat
import sys

import pytest

from uso import cola


FILA = {
    "tenant_id": 1,
    "user_id": 7,
    "facet": "hyde",
    "model": "qwen3-coder",
    "tokens_in": 100,
    "tokens_out": 250,
    "cost_usd": 0.0031,
    "request_type": "chat",
    "origen": "platform",
}


@pytest.fixture(autouse=True)
def respaldo_aislado(tmp_path, monkeypatch):
    """Cada test parte de un respaldo propio y de contadores en cero."""
    directorio = tmp_path / "usage-spool"
    monkeypatch.setenv("JAX_USAGE_SPOOL_DIR", str(directorio))
    monkeypatch.delenv("JAX_USAGE_SPOOL_MAX_FILAS", raising=False)
    cola.reset_estado()
    yield directorio
    cola.reset_estado()


def _archivos(directorio):
    return sorted(p.name for p in directorio.glob("*.json"))


# --- el directorio y la configuración --------------------------------------

def test_el_directorio_sale_del_entorno_y_se_crea_si_falta(respaldo_aislado):
    assert not respaldo_aislado.exists()

    directorio = cola.directorio_del_respaldo()

    assert directorio == respaldo_aislado
    assert directorio.is_dir()


def test_el_default_no_esta_hardcodeado_en_el_llamador(monkeypatch):
    monkeypatch.delenv("JAX_USAGE_SPOOL_DIR", raising=False)
    assert str(cola.DIRECTORIO_POR_DEFECTO) == "/srv/jax-data/usage-spool"


def test_el_tope_sale_del_entorno_con_default(monkeypatch):
    assert cola.MAX_FILAS_POR_DEFECTO == 50000
    assert cola.max_filas() == 50000
    monkeypatch.setenv("JAX_USAGE_SPOOL_MAX_FILAS", "3")
    assert cola.max_filas() == 3
    monkeypatch.setenv("JAX_USAGE_SPOOL_MAX_FILAS", "no-es-un-numero")
    assert cola.max_filas() == 50000


def test_el_modulo_no_depende_de_nada_fuera_de_la_stdlib():
    """El repo jax va a llevar una copia literal (Task 7) y se compara por AST,
    como ya se hizo con redaccion.py. Un import del backend rompería eso."""
    fuente = ast.parse((cola.__file__ and open(cola.__file__, encoding="utf-8").read()))
    raices = set()
    for nodo in ast.walk(fuente):
        if isinstance(nodo, ast.Import):
            raices.update(a.name.split(".")[0] for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
            raices.add(nodo.module.split(".")[0])
    fuera = sorted(r for r in raices if r not in sys.stdlib_module_names)
    assert fuera == [], f"imports fuera de la stdlib: {fuera}"


# --- encolar ---------------------------------------------------------------

async def test_encolar_escribe_un_archivo_por_fila_con_el_formato_compartido(
    respaldo_aislado,
):
    spool_id = await cola.encolar(dict(FILA))

    assert isinstance(spool_id, str) and len(spool_id) == 36
    assert _archivos(respaldo_aislado) == [f"{spool_id}.json"]
    guardada = json.loads((respaldo_aislado / f"{spool_id}.json").read_text("utf-8"))
    assert set(guardada) == set(cola.CAMPOS)
    for campo, valor in FILA.items():
        assert guardada[campo] == valor
    assert guardada["spool_id"] == spool_id
    assert guardada["created_at"].endswith("+00:00")


async def test_el_contrato_nombra_los_trece_campos_y_los_tres_origenes():
    assert cola.CAMPOS == (
        "spool_id", "created_at", "tenant_id", "user_id", "facet", "model",
        "tokens_in", "tokens_out", "cost_usd", "request_type", "origen",
        "status", "job_id",
    )
    assert cola.ORIGENES == frozenset({"platform", "jacobs", "motor_registry"})


async def test_status_y_job_id_son_opcionales_para_el_llamador():
    """Sólo `motor_registry` los tiene. Que estén en el CONTRATO no los hace
    obligatorios para quien encola: van al archivo en `null`."""
    assert set(cola.CAMPOS) - set(cola.CAMPOS_OBLIGATORIOS) == {
        "spool_id", "created_at", "status", "job_id",
    }


async def test_una_fila_sin_status_ni_job_id_sale_con_los_dos_en_null(
    respaldo_aislado,
):
    spool_id = await cola.encolar(dict(FILA))

    guardada = json.loads((respaldo_aislado / f"{spool_id}.json").read_text("utf-8"))
    assert set(guardada) == set(cola.CAMPOS)
    assert guardada["status"] is None
    assert guardada["job_id"] is None


async def test_una_fila_con_status_y_job_id_los_conserva(respaldo_aislado):
    """Sin esto, la fila recuperada entra a `axioma_usage` con las dos columnas
    en NULL y la reconciliación contra `motor_jobs.jsonl` (T3) no la puede
    emparejar: se recupera el cobro y se pierde la trazabilidad."""
    trabajo = "11111111-2222-3333-4444-555555555555"

    spool_id = await cola.encolar(
        {**FILA, "origen": "motor_registry", "status": "ok", "job_id": trabajo}
    )

    guardada = json.loads((respaldo_aislado / f"{spool_id}.json").read_text("utf-8"))
    assert guardada["status"] == "ok"
    assert guardada["job_id"] == trabajo


async def test_cost_usd_puede_venir_en_null(respaldo_aislado):
    spool_id = await cola.encolar({**FILA, "cost_usd": None})

    guardada = json.loads((respaldo_aislado / f"{spool_id}.json").read_text("utf-8"))
    assert guardada["cost_usd"] is None


async def test_una_fila_sin_todos_los_campos_no_se_encola(respaldo_aislado):
    incompleta = dict(FILA)
    del incompleta["cost_usd"]

    assert await cola.encolar(incompleta) is None
    assert not respaldo_aislado.exists() or _archivos(respaldo_aislado) == []
    assert cola.estadisticas()["ultimo_error_de_respaldo"] is not None


async def test_un_origen_desconocido_no_se_encola(respaldo_aislado):
    assert await cola.encolar({**FILA, "origen": "el-vecino"}) is None
    assert cola.estadisticas()["ultimo_error_de_respaldo"] is not None


async def test_encolar_respeta_el_spool_id_y_la_hora_que_ya_traiga_la_fila(
    respaldo_aislado,
):
    # la hora es la del TURNO, no la del reintento: si no, una caída de dos
    # horas movería el costo al día siguiente.
    previo = {**FILA, "spool_id": "11111111-2222-3333-4444-555555555555",
              "created_at": "2026-09-15T10:00:00.000+00:00"}

    spool_id = await cola.encolar(previo)

    guardada = json.loads((respaldo_aislado / f"{spool_id}.json").read_text("utf-8"))
    assert spool_id == previo["spool_id"]
    assert guardada["created_at"] == "2026-09-15T10:00:00.000+00:00"


async def test_encolar_es_fail_soft_si_el_disco_falla(respaldo_aislado, monkeypatch):
    def _no_se_puede(*a, **k):
        raise OSError("disco lleno")

    monkeypatch.setattr(cola, "_escribir_atomico", _no_se_puede)

    assert await cola.encolar(dict(FILA)) is None
    assert "disco lleno" in cola.estadisticas()["ultimo_error_de_respaldo"]


# --- atomicidad ------------------------------------------------------------

async def test_encolar_usa_un_temporal_del_mismo_directorio_con_replace_y_fsync(
    respaldo_aislado, monkeypatch
):
    reemplazos = []
    real_replace = os.replace
    monkeypatch.setattr(
        os, "replace",
        lambda src, dst: reemplazos.append((str(src), str(dst))) or real_replace(src, dst),
    )
    sincronizados = []
    real_fsync = os.fsync
    monkeypatch.setattr(
        os, "fsync",
        lambda fd: sincronizados.append(os.fstat(fd).st_mode) or real_fsync(fd),
    )

    spool_id = await cola.encolar(dict(FILA))

    assert len(reemplazos) == 1
    origen, destino = reemplazos[0]
    # el temporal tiene que estar en el MISMO directorio: si no, `replace`
    # cruzaría sistemas de archivos y dejaría de ser atómico.
    assert os.path.dirname(origen) == os.path.dirname(destino) == str(respaldo_aislado)
    assert destino.endswith(f"{spool_id}.json")
    # fsync del archivo Y del directorio: sin el del directorio, un corte de
    # luz puede dejar el rename sin persistir.
    assert any(stat.S_ISREG(m) for m in sincronizados)
    assert any(stat.S_ISDIR(m) for m in sincronizados)


async def test_si_falla_a_mitad_no_queda_el_temporal(respaldo_aislado, monkeypatch):
    def _explota(src, dst):
        raise OSError("se corto la luz")

    monkeypatch.setattr(os, "replace", _explota)

    assert await cola.encolar(dict(FILA)) is None
    assert list(respaldo_aislado.iterdir()) == []


# --- leer ------------------------------------------------------------------

async def test_leer_pendientes_devuelve_los_mas_viejos_primero(respaldo_aislado):
    ids = []
    for i in range(3):
        ids.append(await cola.encolar({**FILA, "tokens_in": i}))
        # la edad la da la fecha del archivo: se separan para que el orden sea
        # el de llegada y no el del nombre (un uuid4 no ordena por tiempo).
        os.utime(respaldo_aislado / f"{ids[-1]}.json", (1000 + i, 1000 + i))

    filas = await cola.leer_pendientes(10)

    assert [f["tokens_in"] for f in filas] == [0, 1, 2]
    assert [f["spool_id"] for f in filas] == ids


async def test_leer_pendientes_respeta_el_limite(respaldo_aislado):
    for i in range(5):
        spool_id = await cola.encolar({**FILA, "tokens_in": i})
        os.utime(respaldo_aislado / f"{spool_id}.json", (1000 + i, 1000 + i))

    filas = await cola.leer_pendientes(2)

    assert [f["tokens_in"] for f in filas] == [0, 1]


async def test_leer_pendientes_sin_directorio_es_lista_vacia(respaldo_aislado):
    assert await cola.leer_pendientes(10) == []
    # preguntar no crea nada: el default es una ruta de producción
    assert not respaldo_aislado.exists()


async def test_un_directorio_ilegible_es_un_error_visible(respaldo_aislado):
    if os.geteuid() == 0:
        pytest.skip("como root los permisos no frenan la lectura")
    respaldo_aislado.mkdir(parents=True)
    await cola.encolar(dict(FILA))
    os.chmod(respaldo_aislado, 0o000)
    try:
        with pytest.raises(OSError):
            await cola.leer_pendientes(10)
    finally:
        os.chmod(respaldo_aislado, 0o700)

    assert cola.estadisticas()["ultimo_error_de_respaldo"] is not None


# --- archivos corruptos ----------------------------------------------------

async def test_un_archivo_corrupto_no_rompe_la_cola_y_se_va_a_corruptos(
    respaldo_aislado, caplog
):
    bueno = await cola.encolar(dict(FILA))
    (respaldo_aislado / "roto.json").write_text("{esto no es json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        filas = await cola.leer_pendientes(10)

    assert [f["spool_id"] for f in filas] == [bueno]
    assert cola.estadisticas()["corruptos"] == 1
    assert any("corrupto" in r.getMessage().lower() for r in caplog.records)
    # movido, no borrado: el dato no se tira, pero deja de taparle el paso al
    # ciclo siguiente.
    assert (respaldo_aislado / cola.SUBDIRECTORIO_CORRUPTOS / "roto.json").exists()
    assert not (respaldo_aislado / "roto.json").exists()


async def test_el_corrupto_no_se_vuelve_a_contar_en_el_ciclo_siguiente(
    respaldo_aislado,
):
    await cola.encolar(dict(FILA))
    (respaldo_aislado / "roto.json").write_text("{esto no es json", encoding="utf-8")

    await cola.leer_pendientes(10)
    await cola.leer_pendientes(10)

    assert cola.estadisticas()["corruptos"] == 1


async def test_un_archivo_de_once_campos_va_a_corruptos_nombrando_los_que_faltan(
    respaldo_aislado, caplog
):
    """Fail-closed a propósito: un archivo escrito por una copia VIEJA del
    módulo (once campos) no entra a medias -- entraría con `status`/`job_id` en
    NULL y sin manera de saber que faltaban."""
    respaldo_aislado.mkdir(parents=True)
    viejo = {c: FILA.get(c) for c in cola.CAMPOS if c not in ("status", "job_id")}
    viejo["spool_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    viejo["created_at"] = "2026-09-15T10:00:00.000+00:00"
    (respaldo_aislado / f"{viejo['spool_id']}.json").write_text(
        json.dumps(viejo), encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert await cola.leer_pendientes(10) == []

    assert cola.estadisticas()["corruptos"] == 1
    assert (respaldo_aislado / cola.SUBDIRECTORIO_CORRUPTOS
            / f"{viejo['spool_id']}.json").exists()
    assert "status" in caplog.text and "job_id" in caplog.text


async def test_un_json_valido_sin_spool_id_es_corrupto(respaldo_aislado):
    respaldo_aislado.mkdir(parents=True)
    (respaldo_aislado / "sin-id.json").write_text(json.dumps({"facet": "hyde"}), "utf-8")

    assert await cola.leer_pendientes(10) == []
    assert cola.estadisticas()["corruptos"] == 1


async def test_lo_que_esta_en_corruptos_no_cuenta_como_pendiente(respaldo_aislado):
    await cola.encolar(dict(FILA))
    (respaldo_aislado / "roto.json").write_text("{roto", encoding="utf-8")
    await cola.leer_pendientes(10)

    assert await cola.contar_pendientes() == 1
    assert cola.estadisticas()["en_cola"] == 1


# --- el tope ---------------------------------------------------------------

async def test_pasado_el_tope_se_descarta_el_mas_viejo(respaldo_aislado, monkeypatch):
    monkeypatch.setenv("JAX_USAGE_SPOOL_MAX_FILAS", "3")

    ids = []
    for i in range(5):
        ids.append(await cola.encolar({**FILA, "tokens_in": i}))
        os.utime(respaldo_aislado / f"{ids[-1]}.json", (1000 + i, 1000 + i))

    filas = await cola.leer_pendientes(10)
    assert [f["tokens_in"] for f in filas] == [2, 3, 4]
    assert cola.estadisticas()["perdidas_por_desborde"] == 2
    assert cola.estadisticas()["en_cola"] == 3


# --- quitar ----------------------------------------------------------------

async def test_quitar_borra_esos_archivos(respaldo_aislado):
    ids = [await cola.encolar({**FILA, "tokens_in": i}) for i in range(3)]

    quitadas = await cola.quitar({ids[0], ids[2]})

    assert quitadas == 2
    assert _archivos(respaldo_aislado) == [f"{ids[1]}.json"]
    assert cola.estadisticas()["en_cola"] == 1


async def test_quitar_algo_que_ya_no_esta_no_explota(respaldo_aislado):
    spool_id = await cola.encolar(dict(FILA))
    (respaldo_aislado / f"{spool_id}.json").unlink()

    # otro ciclo pudo ganarle: no es un error, y no cuenta como quitada
    assert await cola.quitar({spool_id, "no-existe"}) == 0


async def test_quitar_sin_ids_es_cero(respaldo_aislado):
    assert await cola.quitar(set()) == 0


async def test_quitar_no_acepta_un_id_con_separador_de_ruta(respaldo_aislado):
    otro = respaldo_aislado.parent / "afuera.json"
    respaldo_aislado.mkdir(parents=True)
    otro.write_text("no me toques", encoding="utf-8")

    assert await cola.quitar({"../afuera"}) == 0
    assert otro.exists()


# --- concurrencia ----------------------------------------------------------

async def test_diez_encolados_concurrentes_no_se_pisan(respaldo_aislado):
    ids = await asyncio.gather(
        *(cola.encolar({**FILA, "tokens_in": i}) for i in range(10))
    )

    assert len(set(ids)) == 10
    assert len(_archivos(respaldo_aislado)) == 10
    filas = await cola.leer_pendientes(50)
    assert sorted(f["tokens_in"] for f in filas) == list(range(10))


async def test_encolar_y_quitar_concurrentes_no_se_pisan(respaldo_aislado):
    primero = await cola.encolar({**FILA, "tokens_in": 0})

    quitadas, nuevo = await asyncio.gather(
        cola.quitar({primero}),
        cola.encolar({**FILA, "tokens_in": 1}),
    )

    assert quitadas == 1
    assert _archivos(respaldo_aislado) == [f"{nuevo}.json"]


# --- estadísticas ----------------------------------------------------------

async def test_estadisticas_publica_lo_que_mira_admin(respaldo_aislado):
    assert cola.estadisticas() == {
        "en_cola": 0,
        "perdidas_por_desborde": 0,
        "corruptos": 0,
        "ultimo_error_de_respaldo": None,
    }

    await cola.encolar(dict(FILA))

    assert cola.estadisticas()["en_cola"] == 1


async def test_contar_pendientes_ve_lo_que_dejo_otro_proceso(respaldo_aislado):
    # jacobs y motor_registry depositan en el MISMO directorio sin pasar por
    # este proceso: la profundidad se cuenta del disco, no de un contador.
    respaldo_aislado.mkdir(parents=True)
    ajeno = {**FILA, "origen": "jacobs", "spool_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
             "created_at": "2026-09-15T10:00:00.000+00:00"}
    (respaldo_aislado / f"{ajeno['spool_id']}.json").write_text(
        json.dumps(ajeno), encoding="utf-8")

    assert await cola.contar_pendientes() == 1
    assert cola.estadisticas()["en_cola"] == 1
