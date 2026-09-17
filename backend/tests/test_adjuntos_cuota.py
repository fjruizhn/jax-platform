"""Cuota por usuario y guarda de disco libre de /api/chat/upload (RD6,
2026-09-17; Principal Ruling "quota").

- JAX_ADJUNTOS_CUOTA_BYTES_USUARIO: la suma de `bytes` de los adjuntos
  VIGENTES del usuario más lo reservado por sus subidas en vuelo. Se mira
  antes de la copia y otra vez al confirmar. Pasarse: 413
  `adjuntos_cuota_excedida`, sin archivos.
- JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES: shutil.disk_usage del filesystem de
  JAX_ADJUNTOS_DIR, en un hilo, antes de la copia. Por debajo, o si medir
  falla: 507 `adjuntos_sin_espacio` (fail-closed).

Directos (sin DB): llaman a upload_file como test_adjuntos_upload.py."""
import asyncio
import io
import logging
import threading
import time
from collections import namedtuple
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

import api.upload as upload_mod
from adjuntos import almacen, cuota
from adjuntos.limites import LimitesDeAdjuntosInvalidos
from auth.models import AuthUser
from tests.adjuntos_muestras import PNG

USUARIO = AuthUser(user_id="5", tenant_id="1", role="operator")
OTRO = AuthUser(user_id="6", tenant_id="1", role="operator")
MIB = 1024 * 1024
Uso = namedtuple("Uso", "total used free")


@pytest.fixture
def directorio(tmp_path, monkeypatch):
    d = tmp_path / "adjuntos"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(MIB))
    monkeypatch.setenv("JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES", str(cuota.DISCO_LIBRE_MIN))
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(10 * MIB))
    # Estado de módulo: una fuga de un test no contamina al siguiente (cada
    # test que la puede causar la afirma él mismo).
    cuota._cuentas.clear()
    yield d
    cuota._cuentas.clear()


def _png(n: int) -> bytes:
    assert n >= len(PNG)
    return PNG + b"\x00" * (n - len(PNG))


def _archivo(datos, nombre="f.png"):
    return UploadFile(io.BytesIO(datos), filename=nombre, headers=Headers({"content-type": "image/png"}))


def _subir(datos, user=USUARIO, nombre="f.png"):
    return asyncio.run(upload_mod.upload_file(file=_archivo(datos, nombre), user=user))


def _rechazo(datos, user=USUARIO, nombre="f.png") -> HTTPException:
    try:
        _subir(datos, user, nombre)
    except HTTPException as e:
        return e
    raise AssertionError("se esperaba HTTPException")


def _archivos(d: Path):
    # RD7: los archivos viven en la carpeta de cada usuario.
    return sorted(p.name for p in d.rglob("*") if p.is_file())


# ------------------------------------------------------------- cuota exacta

def test_justo_en_la_cuota_pasa_y_un_byte_mas_es_413(directorio):
    r = _subir(_png(MIB))
    assert r["bytes"] == MIB
    e = _rechazo(b"a", nombre="uno.txt")
    assert (e.status_code, e.detail) == (413, {"code": "adjuntos_cuota_excedida", "cuota_bytes": MIB})
    assert _archivos(directorio) == sorted([f"{r['id']}.dato", f"{r['id']}.json"])


def test_en_dos_partes_hasta_la_cuota_pasa_y_la_siguiente_no(directorio):
    _subir(_png(MIB - 100))
    _subir(b"a" * 100, nombre="cien.txt")  # cuenta `bytes` subidos, no el texto guardado
    e = _rechazo(b"a", nombre="uno.txt")
    assert (e.status_code, e.detail["code"]) == (413, "adjuntos_cuota_excedida")
    assert len(_archivos(directorio)) == 4


def test_un_byte_sobre_la_cuota_en_una_sola_subida_es_413_sin_archivos(directorio):
    e = _rechazo(_png(MIB + 1))
    assert (e.status_code, e.detail) == (413, {"code": "adjuntos_cuota_excedida", "cuota_bytes": MIB})
    assert _archivos(directorio) == []


def test_la_cuota_es_por_usuario(directorio):
    _subir(_png(MIB))
    assert _subir(_png(MIB), user=OTRO)["bytes"] == MIB


def test_lo_vencido_no_cuenta(directorio, monkeypatch):
    _subir(_png(MIB))
    real = almacen._ahora
    monkeypatch.setattr(almacen, "_ahora", lambda: real() + timedelta(hours=25))
    assert _subir(_png(MIB))["bytes"] == MIB


def test_un_sidecar_corrupto_no_cuenta_ni_rompe(directorio):
    almacen.preparar_carpeta(directorio, USUARIO.user_id)
    (directorio / USUARIO.user_id / f"{'A' * 32}.json").write_bytes(b"{no es json")
    assert _subir(_png(MIB))["bytes"] == MIB


def test_max_bytes_se_mira_antes_que_la_cuota(directorio, monkeypatch):
    """Un archivo sobre el tope por archivo es SIEMPRE el 413 de tamaño,
    aunque además no entre en la cuota: ese es el motivo accionable (ni
    con la cuota vacía entraría)."""
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(MIB // 2))
    _subir(_png(MIB // 2))
    e = _rechazo(_png(MIB // 2 + 1))
    assert (e.status_code, e.detail) == (413, {"code": "adjunto_demasiado_grande", "max_bytes": MIB // 2})
    assert len(_archivos(directorio)) == 2


def test_max_bytes_se_rechaza_sin_copiar(directorio, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(MIB // 2))
    copias = []
    monkeypatch.setattr(almacen, "copiar_subida", lambda *a, **k: copias.append(1))
    e = _rechazo(_png(MIB // 2 + 1))
    assert (e.status_code, e.detail["code"]) == (413, "adjunto_demasiado_grande")
    assert copias == []


def test_la_cuota_se_rechaza_antes_de_copiar_y_de_medir_el_disco(directorio, monkeypatch):
    _subir(_png(MIB))
    copias, medidas = [], []
    monkeypatch.setattr(almacen, "copiar_subida", lambda *a, **k: copias.append(1))
    monkeypatch.setattr(cuota.shutil, "disk_usage", lambda p: medidas.append(p))
    e = _rechazo(_png(len(PNG)))
    assert e.detail["code"] == "adjuntos_cuota_excedida"
    assert (copias, medidas) == ([], [])


# --------------------------------------------------- concurrencia en el borde

def test_dos_subidas_que_juntas_se_pasan_solo_entra_una(directorio, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_SUBIDAS_EN_PROCESO", "2")
    tamano = 600 * 1024  # cada una entra sola (<= 1 MiB); juntas, no
    # Lectura del disco lenta y vigilada: sin el candado por usuario, las dos
    # leerían a la vez, las dos verían 0 usado y 0 reservado, y las dos
    # reservarían (mutación verificada en RD6).
    real, en_curso, pico = almacen.uso_de_usuario, [0], [0]
    contadores = threading.Lock()  # la lectura corre en hilos de to_thread

    def lento(*a, **k):
        with contadores:
            en_curso[0] += 1
            pico[0] = max(pico[0], en_curso[0])
        try:
            time.sleep(0.05)
            return real(*a, **k)
        finally:
            with contadores:
                en_curso[0] -= 1

    monkeypatch.setattr(almacen, "uso_de_usuario", lento)

    async def correr():
        return await asyncio.gather(
            upload_mod.upload_file(file=_archivo(_png(tamano), "a.png"), user=USUARIO),
            upload_mod.upload_file(file=_archivo(_png(tamano), "b.png"), user=USUARIO),
            return_exceptions=True)

    resultados = asyncio.run(correr())
    ok = [r for r in resultados if isinstance(r, dict)]
    malos = [r for r in resultados if isinstance(r, HTTPException)]
    assert len(ok) == 1 and len(malos) == 1, resultados
    assert (malos[0].status_code, malos[0].detail["code"]) == (413, "adjuntos_cuota_excedida")
    assert _archivos(directorio) == sorted([f"{ok[0]['id']}.dato", f"{ok[0]['id']}.json"])
    assert cuota._cuentas == {}  # nada reservado ni retenido al terminar
    assert pico[0] == 1


def test_la_confirmacion_vuelve_a_mirar_el_disco(directorio):
    """Si entre la reserva y el commit aparece un adjunto vigente del usuario
    que la reserva no vio, el commit tampoco se pasa de la cuota."""

    async def correr():
        async with cuota.reserva(directorio, USUARIO, 600 * 1024, MIB) as r:
            await asyncio.to_thread(
                almacen.guardar_texto, directorio, "x", user=USUARIO, origen="texto", nombre="n",
                bytes_=600 * 1024, recortado=False, ttl_horas=24)
            llamadas = []

            async def guardar():
                llamadas.append(1)
                return {}

            with pytest.raises(cuota.CuotaExcedida):
                await r.confirmar(600 * 1024, guardar)
            assert llamadas == []

    asyncio.run(correr())
    assert cuota._cuentas == {}


def test_la_reserva_se_libera_si_la_subida_falla(directorio, monkeypatch):
    def rompe(*a, **k):
        raise OSError("disco")

    monkeypatch.setattr(almacen, "_fsync_directorio", rompe)
    with pytest.raises(OSError):
        _subir(_png(MIB))
    assert cuota._cuentas == {}
    monkeypatch.undo()
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(directorio))
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(MIB))
    assert _subir(_png(MIB))["bytes"] == MIB  # nada quedó reservado ni contado


# ------------------------------------------------------------ guarda de disco

def test_disco_bajo_el_minimo_es_507_sin_archivos(directorio, monkeypatch):
    minimo = cuota.DISCO_LIBRE_MIN
    monkeypatch.setattr(cuota.shutil, "disk_usage", lambda p: Uso(10**12, 10**12 - minimo, minimo))
    e = _rechazo(_png(len(PNG)))
    assert (e.status_code, e.detail) == (507, {"code": "adjuntos_sin_espacio"})
    assert _archivos(directorio) == []
    assert cuota._cuentas == {}


def test_el_disco_tiene_que_quedar_sobre_el_minimo_despues_de_escribir(directorio, monkeypatch):
    minimo, n = cuota.DISCO_LIBRE_MIN, 1000
    monkeypatch.setattr(cuota.shutil, "disk_usage", lambda p: Uso(10**12, 0, minimo + n))
    assert _subir(_png(n))["bytes"] == n
    monkeypatch.setattr(cuota.shutil, "disk_usage", lambda p: Uso(10**12, 0, minimo + n - 1))
    assert _rechazo(_png(n)).status_code == 507


def test_si_medir_el_disco_falla_es_507_no_500(directorio, monkeypatch, caplog):
    def rompe(p):
        raise PermissionError("sin acceso")

    monkeypatch.setattr(cuota.shutil, "disk_usage", rompe)
    with caplog.at_level(logging.ERROR, logger="adjuntos.cuota"):
        e = _rechazo(_png(len(PNG)))
    assert (e.status_code, e.detail) == (507, {"code": "adjuntos_sin_espacio"})
    assert _archivos(directorio) == []
    assert "PermissionError" in caplog.text


def test_el_disco_se_mide_en_un_hilo_sobre_el_directorio(directorio, monkeypatch):
    vistos = []

    def espia(p):
        vistos.append((Path(p), threading.current_thread() is threading.main_thread()))
        return Uso(10**12, 0, 10**12)

    monkeypatch.setattr(cuota.shutil, "disk_usage", espia)
    _subir(_png(len(PNG)))
    assert vistos == [(directorio, False)]


# ------------------------------------------------------ entorno (fail-closed)

@pytest.mark.parametrize("variable,cargar", [
    ("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", cuota.cargar_cuota_por_usuario),
    ("JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES", cuota.cargar_disco_libre_minimo),
])
@pytest.mark.parametrize("valor", [None, "", "0", "-1", "diez", "1e9", " 1048576", "01048576", "1048576.0"])
def test_variable_ausente_o_no_entera_no_arranca(monkeypatch, variable, cargar, valor):
    if valor is None:
        monkeypatch.delenv(variable, raising=False)
    else:
        monkeypatch.setenv(variable, valor)
    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        cargar()
    assert variable in str(e.value)


@pytest.mark.parametrize("variable,cargar,minimo,maximo", [
    ("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", cuota.cargar_cuota_por_usuario, cuota.CUOTA_MIN, cuota.CUOTA_MAX),
    ("JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES", cuota.cargar_disco_libre_minimo,
     cuota.DISCO_LIBRE_MIN, cuota.DISCO_LIBRE_MAX),
])
def test_rango_de_las_variables(monkeypatch, variable, cargar, minimo, maximo):
    for fuera in (minimo - 1, maximo + 1):
        monkeypatch.setenv(variable, str(fuera))
        with pytest.raises(LimitesDeAdjuntosInvalidos):
            cargar()
    for dentro in (minimo, maximo):
        monkeypatch.setenv(variable, str(dentro))
        assert cargar() == dentro


def test_rangos_declarados():
    assert (cuota.CUOTA_MIN, cuota.CUOTA_MAX) == (MIB, 1024 ** 4)
    assert (cuota.DISCO_LIBRE_MIN, cuota.DISCO_LIBRE_MAX) == (1024 ** 3, 1024 ** 4)
    # Los valores de deploy del principal caen dentro.
    assert cuota.CUOTA_MIN <= 524288000 <= cuota.CUOTA_MAX
    assert cuota.DISCO_LIBRE_MIN <= 53687091200 <= cuota.DISCO_LIBRE_MAX


def test_cuota_menor_que_max_bytes_no_arranca(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(10 * MIB))
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(10 * MIB - 1))
    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        cuota.validar_configuracion()
    assert "JAX_ADJUNTOS_CUOTA_BYTES_USUARIO" in str(e.value)
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(10 * MIB))
    cuota.validar_configuracion()


@pytest.mark.parametrize("variable", ["JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", "JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES"])
def test_lifespan_valida_cuota_y_disco_antes_de_abrir_la_base(monkeypatch, variable):
    import main

    llamadas = []

    async def pool_espia():
        llamadas.append("pool")

    monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(main, "get_pool", pool_espia)

    async def arrancar():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(LimitesDeAdjuntosInvalidos) as e:
        asyncio.run(arrancar())
    assert variable in str(e.value)
    assert llamadas == []


def test_subir_sin_cuota_configurada_falla_cerrado(directorio, monkeypatch):
    monkeypatch.delenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO")
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        _subir(_png(len(PNG)))
    assert _archivos(directorio) == []


# ------------------------------------- la reserva se libera por todas las salidas
# Fix round 1 (RD6). Cada salida deja `_cuentas` vacío y la subida siguiente
# del mismo usuario llena la cuota entera (menos lo que SÍ quedó guardado).
# "repetida": la tarea se cancela una y otra vez hasta terminar -- un `await`
# en el finally de reserva() la dejaría a mitad de liberar, y un commit que no
# espera su hilo soltaría candado y reserva antes de que aparezca el sidecar.

_PUNTOS = {
    "copia": (almacen, "copiar_subida"),
    "lectura_de_cuota": (almacen, "uso_de_usuario"),
    "commit": (almacen, "guardar_imagen"),
}


def _frenar(monkeypatch, punto):
    """Sustituye la función del punto por una que avisa al entrar, espera
    `soltar` y recién ahí llama a la real; `termino` queda marcado cuando la
    real devolvió."""
    modulo, nombre = _PUNTOS[punto]
    real = getattr(modulo, nombre)
    entro, soltar, termino = threading.Event(), threading.Event(), threading.Event()
    primera = [True]
    candado = threading.Lock()

    def frenada(*a, **k):
        with candado:
            frenar, primera[0] = primera[0], False
        if frenar:
            entro.set()
            assert soltar.wait(10)
        try:
            return real(*a, **k)
        finally:
            if frenar:
                termino.set()

    monkeypatch.setattr(modulo, nombre, frenada)
    return entro, soltar, termino


def _usado(directorio):
    return almacen.uso_de_usuario(directorio, USUARIO.user_id)


@pytest.mark.parametrize("modo", ["una", "repetida"])
@pytest.mark.parametrize("punto", ["copia", "lectura_de_cuota", "commit"])
def test_cancelar_la_subida_libera_la_reserva(directorio, monkeypatch, punto, modo):
    entro, soltar, termino = _frenar(monkeypatch, punto)

    async def correr():
        tarea = asyncio.create_task(upload_mod.upload_file(file=_archivo(_png(len(PNG))), user=USUARIO))
        while not entro.is_set():
            await asyncio.sleep(0.005)
        if modo == "una":
            tarea.cancel()
            await asyncio.sleep(0.05)
            soltar.set()
        else:
            for i in range(10**6):
                if tarea.done():
                    break
                tarea.cancel()
                await asyncio.sleep(0)
                if i == 50:
                    soltar.set()
        soltar.set()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        if punto == "commit":
            # El hilo del commit no se interrumpe: la tarea no termina antes
            # que él (si no, candado y reserva se sueltan con el sidecar en
            # camino y otra subida cuenta de menos).
            assert termino.is_set()
        assert cuota._cuentas == {}

    asyncio.run(correr())
    monkeypatch.undo()
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(directorio))
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(MIB))
    monkeypatch.setenv("JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES", str(cuota.DISCO_LIBRE_MIN))
    usado = _usado(directorio)
    # Cancelado en el commit, el adjunto quedó guardado y cuenta; en los demás, nada.
    assert usado == (len(PNG) if punto == "commit" else 0)
    assert _subir(_png(MIB - usado))["bytes"] == MIB - usado
    assert cuota._cuentas == {}


@pytest.mark.parametrize("datos,status,code", [
    (b"MZ\x90\x00\x03\x00\x00\x00", 415, "adjunto_tipo_no_permitido"),
    (b"%PDF-1.4 basura" * 5, 422, "pdf_ilegible"),
])
def test_un_rechazo_despues_de_reservar_libera_la_reserva(directorio, datos, status, code):
    e = _rechazo(datos, nombre="x.bin")
    assert (e.status_code, e.detail) == (status, {"code": code})
    assert cuota._cuentas == {}
    assert _archivos(directorio) == []
    assert _subir(_png(MIB))["bytes"] == MIB


# ---------------------------------------- RD7 fix round (R29): commit acotado

def test_el_plazo_del_commit_sale_del_tope_por_archivo_con_piso(monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(10 * MIB))
    assert cuota.plazo_de_escritura_segundos() == cuota.PLAZO_DE_ESCRITURA_PISO_SEGUNDOS == 60
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", str(100 * MIB))
    assert cuota.plazo_de_escritura_segundos() == 100 * MIB // cuota.DISCO_LENTO_BYTES_POR_SEGUNDO == 400


@pytest.mark.parametrize("modo", ["sin_cancelar", "cancelada_repetida"])
def test_un_commit_colgado_suelta_candado_y_reserva_y_da_un_codigo(directorio, monkeypatch, caplog, modo):
    monkeypatch.setattr(cuota, "plazo_de_escritura_segundos", lambda: 0.3)
    entro, soltar, termino = _frenar(monkeypatch, "commit")

    async def correr():
        tarea = asyncio.create_task(upload_mod.upload_file(file=_archivo(_png(len(PNG))), user=USUARIO))
        while not entro.is_set():
            await asyncio.sleep(0.005)
        if modo == "cancelada_repetida":
            for _ in range(20):
                tarea.cancel()
                await asyncio.sleep(0.005)
        # Sin plazo la tarea no terminaría: se espera acotado para que eso
        # falle en vez de colgar la suite.
        await asyncio.wait({tarea}, timeout=5)
        if not tarea.done():
            soltar.set()
            raise AssertionError("el commit colgado no soltó dentro del plazo")
        if modo == "cancelada_repetida":
            with pytest.raises(asyncio.CancelledError):
                await tarea
        else:
            with pytest.raises(HTTPException) as e:
                await tarea
            assert (e.value.status_code, e.value.detail) == (503, {"code": "adjuntos_reintentar"})
        assert not termino.is_set()  # el hilo sigue colgado: igual se soltó
        assert cuota._cuentas == {}
        # El mismo usuario sube otra vez mientras el primer hilo sigue colgado.
        otra = await upload_mod.upload_file(file=_archivo(_png(len(PNG))), user=USUARIO)
        assert otra["bytes"] == len(PNG)
        soltar.set()
        while not termino.is_set():
            await asyncio.sleep(0.005)

    with caplog.at_level(logging.ERROR, logger="adjuntos.cuota"):
        asyncio.run(correr())
    assert "TimeoutError" in caplog.text
    assert cuota._cuentas == {}
