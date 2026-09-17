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
    return d


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
    return sorted(p.name for p in d.iterdir())


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
    (directorio / f"{'A' * 32}.json").write_bytes(b"{no es json")
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

    def lento(*a, **k):
        en_curso[0] += 1
        pico[0] = max(pico[0], en_curso[0])
        try:
            time.sleep(0.05)
            return real(*a, **k)
        finally:
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
