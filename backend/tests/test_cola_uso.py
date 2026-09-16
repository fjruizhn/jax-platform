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
import threading
import time

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


# --- el costo del camino degradado (Task 9) --------------------------------

def _scandir_contado(monkeypatch):
    """Envuelve `os.scandir` y devuelve la lista de rutas recorridas.

    Contar los recorridos, y no el tiempo, es lo que hace que este control no
    se caiga en una máquina lenta ni pase de casualidad en una rápida.
    """
    recorridos = []
    scandir_real = os.scandir

    def scandir(ruta, *args, **kwargs):
        recorridos.append(str(ruta))
        return scandir_real(ruta, *args, **kwargs)

    monkeypatch.setattr(cola.os, "scandir", scandir)
    return recorridos


async def test_encolar_recorre_el_directorio_una_sola_vez(respaldo_aislado, monkeypatch):
    # `encolar` corre en el `except` de `record_usage`: camino del turno del
    # usuario, y sólo con la base caída -- justo cuando la cola está profunda.
    # Dos recorridos por llamada hacen que el turno k pague O(k): el costo es
    # cuadrático sobre la caída. Este control existe para que nadie vuelva a
    # meter la segunda pasada sin enterarse.
    await cola.encolar(dict(FILA))

    recorridos = _scandir_contado(monkeypatch)
    spool_id = await cola.encolar(dict(FILA))

    assert spool_id is not None
    assert len(recorridos) == 1, (
        f"encolar recorrió el directorio {len(recorridos)} veces: {recorridos}"
    )


async def test_encolar_no_ordena_ni_hace_stat_por_archivo_en_el_camino_normal(
    respaldo_aislado, monkeypatch,
):
    # El `stat()` por archivo y el `sort` son el otro medio del costo: se pagan
    # sólo cuando hay que elegir la más vieja para descartar.
    await cola.encolar(dict(FILA))

    llamadas = []
    monkeypatch.setattr(cola, "_listar", lambda directorio: llamadas.append(directorio) or [])

    assert await cola.encolar(dict(FILA)) is not None
    assert llamadas == []


async def test_el_conteo_del_tope_sigue_saliendo_del_disco(respaldo_aislado):
    # jacobs y motor_registry depositan en el MISMO directorio sin pasar por
    # este proceso: un contador en memoria mentiría, y con el tope de por medio
    # mentiría justo cuando se descarta.
    respaldo_aislado.mkdir(parents=True)
    ajeno = {**FILA, "origen": "jacobs",
             "spool_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
             "created_at": "2026-09-15T10:00:00.000+00:00"}
    (respaldo_aislado / f"{ajeno['spool_id']}.json").write_text(
        json.dumps(ajeno), encoding="utf-8")

    await cola.encolar(dict(FILA))

    assert cola.estadisticas()["en_cola"] == 2


async def test_el_recorrido_barato_no_cuenta_lo_que_no_es_una_fila_pendiente(
    respaldo_aislado,
):
    await cola.encolar(dict(FILA))
    (respaldo_aislado / cola.SUBDIRECTORIO_CORRUPTOS).mkdir(parents=True, exist_ok=True)
    (respaldo_aislado / cola.SUBDIRECTORIO_CORRUPTOS / "viejo.json").write_text(
        "{}", encoding="utf-8")
    (respaldo_aislado / ".a-medio-escribir.json.tmp").write_text("{}", encoding="utf-8")
    (respaldo_aislado / "no-es-una-fila.txt").write_text("nada", encoding="utf-8")

    await cola.encolar(dict(FILA))

    assert cola.estadisticas()["en_cola"] == 2


# --- el lock: sólo alrededor del tope (Task 11) -----------------------------

async def test_la_escritura_del_archivo_no_corre_bajo_el_lock(
    respaldo_aislado, monkeypatch,
):
    # Los dos `fsync` de `_escribir_atomico` son ~4,4 ms, y con el lock de todo
    # el proceso alrededor eran el piso ENTERO de 111 ms medido a c=25 con la
    # cola VACÍA: el turno k esperaba detrás de los k-1 anteriores aunque no
    # hubiera nada que contar. Ese piso es espera, no trabajo.
    #
    # Se puede sacar porque el lock no estaba protegiendo nada: cada fila va a
    # su propio `<spool_id>.json` con `spool_id` uuid4, por temporal OCULTO +
    # `os.replace`. Dos escrituras concurrentes no comparten ni el nombre
    # definitivo ni el temporal.
    tomado = []
    original = cola._escribir_atomico

    def espia(directorio, spool_id, fila):
        tomado.append(cola._lock.locked())
        return original(directorio, spool_id, fila)

    monkeypatch.setattr(cola, "_escribir_atomico", espia)

    assert await cola.encolar(dict(FILA)) is not None
    assert tomado == [False], (
        "la escritura del archivo (con sus dos fsync) corrió bajo `_lock`, que "
        "es de todo el proceso: eso serializa cada turno detrás de los demás"
    )


async def test_el_descarte_por_tope_sigue_corriendo_bajo_el_lock(
    respaldo_aislado, monkeypatch,
):
    # Lo único que necesita exclusión es el tope: contar y descartar la más
    # vieja tienen que ser atómicos ENTRE SÍ o dos llamadas concurrentes
    # descartan de más. `_hacer_lugar` re-lista adentro, así que con el lock
    # puesto el descarte es exacto.
    monkeypatch.setenv(cola.VARIABLE_MAX_FILAS, "1")
    assert await cola.encolar(dict(FILA)) is not None

    tomado = []
    original = cola._hacer_lugar

    def espia(directorio):
        tomado.append(cola._lock.locked())
        return original(directorio)

    monkeypatch.setattr(cola, "_hacer_lugar", espia)

    assert await cola.encolar(dict(FILA)) is not None
    assert tomado == [True], (
        "el descarte por desborde corrió sin `_lock`: dos llamadas concurrentes "
        "pueden descartar de más"
    )


async def test_en_cola_no_retrocede_por_una_llamada_vieja_que_termino_tarde(
    respaldo_aislado, monkeypatch,
):
    # Con la escritura fuera del lock, dos `encolar` pueden TERMINAR en orden
    # distinto al que MIDIERON el disco. La que midió primero y terminó última
    # no puede publicar su número viejo: `en_cola` es lo que mira Admin →
    # Costos justo durante una caída, y verlo retroceder haría creer que la
    # cola drena cuando en realidad crece.
    barrera = threading.Event()
    escrituras = []
    original = cola._escribir_atomico

    def espia(directorio, spool_id, fila):
        escrituras.append(spool_id)
        if len(escrituras) == 1:
            # 5 s de tope para que el código VIEJO (que escribe bajo el lock)
            # dé rojo por el número y no se cuelgue para siempre.
            barrera.wait(5)
        return original(directorio, spool_id, fila)

    monkeypatch.setattr(cola, "_escribir_atomico", espia)

    lenta = asyncio.create_task(cola.encolar({**FILA, "tokens_in": 0}))
    for _ in range(500):  # que la lenta llegue a la escritura (ya midió: 0)
        if escrituras:
            break
        await asyncio.sleep(0.002)
    assert escrituras, "la primera llamada nunca llegó a escribir"

    for i in range(1, 4):
        assert await cola.encolar({**FILA, "tokens_in": i}) is not None
    assert cola.estadisticas()["en_cola"] == 3

    barrera.set()
    assert await lenta is not None
    assert cola.estadisticas()["en_cola"] == 3, (
        "`en_cola` retrocedió: una llamada que midió el disco ANTES publicó su "
        "profundidad DESPUÉS de tres mediciones más nuevas"
    )


async def test_cien_encolados_concurrentes_dejan_cien_archivos(
    respaldo_aislado, monkeypatch,
):
    # Este test NO valida el cambio de la Task 11: con el lock grande de antes
    # también pasa, porque era MÁS estricto. Lo que valida el cambio es la
    # medición a c=25. Éste es la red: que sacar el lock de la escritura no
    # haya roto la unicidad de los nombres ni el contenido de las filas.
    monkeypatch.setenv(cola.VARIABLE_MAX_FILAS, "1000")

    ids = await asyncio.gather(
        *(cola.encolar({**FILA, "tokens_in": i}) for i in range(100))
    )

    assert None not in ids
    assert len(set(ids)) == 100
    assert _archivos(respaldo_aislado) == sorted(f"{i}.json" for i in ids)
    filas = await cola.leer_pendientes(200)
    assert sorted(f["tokens_in"] for f in filas) == list(range(100))


async def test_el_sobrepaso_del_tope_bajo_concurrencia_esta_acotado_y_se_corrige(
    respaldo_aislado, monkeypatch,
):
    # El tope sigue siendo EXACTO en el uso secuencial. Bajo concurrencia se
    # tolera un sobrepaso transitorio de a lo sumo (llamadas en vuelo - 1)
    # filas: cada `encolar` cuenta el disco antes de que las otras hayan
    # escrito, así que sólo la primera ve el desborde y descarta.
    #
    # Cuánto: con c=25, 24 filas sobre 50.000 = 0,048%. Por qué se acepta: la
    # alternativa es serializar la escritura -- exactamente el piso de 111 ms
    # que esta tarea saca -- para no tener 24 filas de más (~7 KB) que la
    # llamada siguiente ya borra. `_hacer_lugar` re-lista, así que NUNCA
    # descarta de más; el error es sólo hacia arriba y dura una llamada.
    monkeypatch.setenv(cola.VARIABLE_MAX_FILAS, "10")
    for i in range(10):
        assert await cola.encolar({**FILA, "tokens_in": i}) is not None
    assert len(_archivos(respaldo_aislado)) == 10

    en_vuelo = 8
    await asyncio.gather(
        *(cola.encolar({**FILA, "tokens_in": 100 + i}) for i in range(en_vuelo))
    )
    assert len(_archivos(respaldo_aislado)) <= 10 + en_vuelo - 1

    # y la llamada siguiente lo devuelve al tope exacto
    assert await cola.encolar(dict(FILA)) is not None
    assert len(_archivos(respaldo_aislado)) == 10


async def test_dos_encolar_no_recorren_el_directorio_a_la_vez(
    respaldo_aislado, monkeypatch,
):
    # EL HALLAZGO de la Task 11: sacar TAMBIÉN el conteo del lock es 7x PEOR,
    # no mejor. `_contar_barato` es un bucle de Python sobre todas las entradas
    # con el GIL tomado casi todo el tiempo: 25 a la vez sobre el mismo
    # directorio no cuestan 25 veces uno, cuestan 230 veces uno. Medido el
    # 2026-09-16 sobre XFS/NVMe con el cache caliente: 49.000 entradas, 7,98 ms
    # un hilo solo contra 1.833 ms 25 hilos juntos (10.000: 1,26 contra 372).
    # A c=25 eso daba 2.039 ms por turno contra los 291 ms del lock grande.
    #
    # Este control es lo único que impide que alguien lo vuelva a paralelizar
    # "para sacarle el lock", y falla en las DOS direcciones: si se paraleliza
    # el conteo cae `max(pico) == 1`, y si se vuelve a contar una vez por turno
    # cae `len(pico) < 25`.
    simultaneos = 0
    pico = []
    cerrojo = threading.Lock()
    original = cola._contar_barato

    def espia(directorio):
        nonlocal simultaneos
        with cerrojo:
            simultaneos += 1
            pico.append(simultaneos)
        try:
            time.sleep(0.01)  # ventana ancha, para que se pisen si pueden
            return original(directorio)
        finally:
            with cerrojo:
                simultaneos -= 1

    monkeypatch.setattr(cola, "_contar_barato", espia)

    ids = await asyncio.gather(*(cola.encolar(dict(FILA)) for _ in range(25)))

    assert None not in ids
    assert len(_archivos(respaldo_aislado)) == 25
    assert max(pico) == 1, (
        f"{max(pico)} recorridos del directorio A LA VEZ: el conteo se "
        f"paralelizó, y eso es 230x el costo de uno solo"
    )
    assert len(pico) < 25, (
        f"{len(pico)} recorridos para 25 turnos: cada turno volvió a recorrer "
        f"el directorio en vez de reusar la medición en curso"
    )
