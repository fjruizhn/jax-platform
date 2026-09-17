"""Una carpeta por usuario en JAX_ADJUNTOS_DIR (RD7, decisión del principal
2026-09-17): JAX_ADJUNTOS_DIR/<user_id>/, 0700. La cuota de un usuario lee
SOLO su carpeta, la búsqueda por id mira SOLO la carpeta del que pide, y el
limpiador y la baja recorren carpetas. Puros: sin base, sin app."""
import asyncio
import builtins
import io
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import api.upload as upload_mod
from adjuntos import almacen, cuota
from auth.models import AuthUser
from starlette.datastructures import Headers, UploadFile
from tests.adjuntos_muestras import PNG

DUENIO = AuthUser(user_id="5", tenant_id="1", role="operator")
AJENO = AuthUser(user_id="6", tenant_id="1", role="operator")
AHORA = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def directorio(tmp_path, monkeypatch):
    d = tmp_path / "adjuntos"
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    monkeypatch.setenv("JAX_ADJUNTOS_TTL_HORAS", "24")
    almacen.preparar_directorio()
    cuota._cuentas.clear()
    return d


def _texto(directorio, user=DUENIO, ttl=24, ahora=AHORA):
    return almacen.guardar_texto(directorio, "hola", user=user, origen="texto", nombre="n.md", bytes_=4,
                                 recortado=False, ttl_horas=ttl, ahora=ahora)


def _archivos(d: Path):
    return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())


class _Grabador:
    """Registra toda ruta que se lista o se abre, en cualquier hilo."""

    def __init__(self, monkeypatch):
        self.rutas: list[str] = []
        for modulo, nombre in ((os, "scandir"), (os, "listdir"), (os, "open"), (io, "open"),
                               (builtins, "open"), (os, "stat"), (os, "lstat")):
            real = getattr(modulo, nombre)
            monkeypatch.setattr(modulo, nombre, self._espia(real))

    def _espia(self, real):
        def espia(ruta=".", *a, **k):
            if isinstance(ruta, (str, bytes, os.PathLike)):
                self.rutas.append(os.fsdecode(os.fspath(ruta)))
            return real(ruta, *a, **k)
        return espia

    def tocadas_bajo(self, carpeta: Path):
        base = str(carpeta)
        return [r for r in self.rutas if r == base or r.startswith(base + os.sep)]


# ------------------------------------------------------------ disposición

def test_cada_adjunto_va_a_la_carpeta_de_su_duenio_0700(directorio):
    a = _texto(directorio)
    b = _texto(directorio, user=AJENO)
    assert _archivos(directorio) == sorted([f"5/{a['id']}.dato", f"5/{a['id']}.json",
                                            f"6/{b['id']}.dato", f"6/{b['id']}.json"])
    for carpeta in ("5", "6"):
        assert stat.S_IMODE((directorio / carpeta).stat().st_mode) == 0o700
    for p in directorio.rglob("*"):
        if p.is_file():
            assert stat.S_IMODE(p.stat().st_mode) == 0o600


@pytest.mark.parametrize("malo", ["", "0", "05", "-5", "5/../6", "..", "../5", "5 ", "abc", "5\x00",
                                  "1" * 21, None, 5])
def test_un_user_id_malformado_no_construye_rutas(directorio, malo):
    raro = AuthUser.model_construct(user_id=malo, tenant_id="1", role="operator")
    with pytest.raises(almacen.UsuarioInvalido):
        _texto(directorio, user=raro)
    with pytest.raises(almacen.UsuarioInvalido):
        almacen.uso_de_usuario(directorio, malo)
    with pytest.raises(almacen.AdjuntoNoEncontrado):
        asyncio.run(almacen.obtener(almacen.nuevo_id(), raro))
    assert _archivos(directorio) == []


# ---------------------------------------------- la cuota no lee lo de otros

def test_la_cuota_no_lista_ni_abre_la_carpeta_de_otro_usuario(directorio, monkeypatch):
    ajenos = [_texto(directorio, user=AJENO) for _ in range(50)]
    rutas_ajenas = {str(p) for p in directorio.rglob("*") if p.is_file()}
    assert len(rutas_ajenas) == 100
    _texto(directorio)
    grabador = _Grabador(monkeypatch)
    assert almacen.uso_de_usuario(directorio, "5") == 4
    tocadas = set(grabador.rutas)
    assert tocadas.isdisjoint(rutas_ajenas)
    assert grabador.tocadas_bajo(directorio / "6") == []
    assert str(directorio) not in tocadas  # ni siquiera lista la raíz
    del ajenos


def test_una_subida_entera_no_toca_la_carpeta_de_otro_usuario(directorio, monkeypatch):
    for _ in range(20):
        _texto(directorio, user=AJENO)
    monkeypatch.setenv("JAX_ADJUNTOS_CUOTA_BYTES_USUARIO", str(1024 * 1024))
    grabador = _Grabador(monkeypatch)
    r = asyncio.run(upload_mod.upload_file(
        file=UploadFile(io.BytesIO(PNG), filename="f.png", headers=Headers({"content-type": "image/png"})),
        user=DUENIO))
    assert r["tipo"] == "imagen"
    assert grabador.tocadas_bajo(directorio / "6") == []


def test_uso_de_un_usuario_sin_carpeta_es_cero(directorio):
    assert almacen.uso_de_usuario(directorio, "5") == 0


# -------------------------------------------- la búsqueda mira solo lo propio

def test_un_id_ajeno_no_se_busca_en_la_carpeta_del_otro(directorio, monkeypatch):
    ajeno = _texto(directorio, user=AJENO)
    grabador = _Grabador(monkeypatch)
    for leer in (almacen.obtener, almacen.leer, almacen.leer_imagen_en_base64):
        try:
            asyncio.run(leer(ajeno["id"], DUENIO, ahora=AHORA))
        except almacen.AdjuntoNoEncontrado as e:
            assert e.detail == {"code": "adjunto_no_encontrado"}
        else:
            raise AssertionError("un id ajeno se encontró")
    assert grabador.tocadas_bajo(directorio / "6") == []
    monkeypatch.undo()
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(directorio))
    assert asyncio.run(almacen.obtener(ajeno["id"], AJENO, ahora=AHORA))["id"] == ajeno["id"]


def test_un_sidecar_propio_copiado_a_otra_carpeta_no_se_le_da_al_otro(directorio):
    """El sidecar sigue atado al dueño: aunque aparezca en la carpeta de
    otro (copia manual, error), el otro recibe el 404 de siempre."""
    meta = _texto(directorio, user=AJENO)
    (directorio / "5").mkdir(mode=0o700)
    for sufijo in (".json", ".dato"):
        (directorio / "5" / f"{meta['id']}{sufijo}").write_bytes(
            (directorio / "6" / f"{meta['id']}{sufijo}").read_bytes())
    with pytest.raises(almacen.AdjuntoNoEncontrado):
        asyncio.run(almacen.obtener(meta["id"], DUENIO, ahora=AHORA))


def test_una_carpeta_de_usuario_que_es_symlink_es_404_y_no_se_escribe(directorio, tmp_path):
    fuera = tmp_path / "fuera"
    fuera.mkdir(mode=0o700)
    meta = _texto(directorio, user=AJENO)
    os.symlink(directorio / "6", directorio / "5")
    with pytest.raises(almacen.AdjuntoNoEncontrado):
        asyncio.run(almacen.obtener(meta["id"], AuthUser(user_id="5", tenant_id="1", role="operator"),
                                    ahora=AHORA))
    os.unlink(directorio / "5")
    os.symlink(fuera, directorio / "5")
    with pytest.raises(OSError):
        _texto(directorio)
    assert list(fuera.iterdir()) == []


# ---------------------------------------------------------------- limpieza

def _envejecer(ruta, segundos):
    viejo = time.time() - segundos
    os.utime(ruta, (viejo, viejo))


def test_limpiar_recorre_las_carpetas_y_borra_las_que_quedan_vacias(directorio):
    vencido = _texto(directorio, ttl=1)
    vigente = _texto(directorio, user=AJENO, ttl=48)
    huerfano = directorio / "6" / almacen.nombre_temporal()
    huerfano.write_bytes(b"x")
    _envejecer(huerfano, almacen.ORFANO_MAX_SEGUNDOS + 60)
    (directorio / "LEEME.txt").write_text("no es nuestro")
    (directorio / "no-es-usuario").mkdir()
    borrados = almacen.limpiar(directorio, ahora=AHORA + timedelta(hours=2))
    assert borrados == 3
    assert not (directorio / "5").exists()  # quedó vacía: se borra
    assert _archivos(directorio) == sorted(["LEEME.txt", f"6/{vigente['id']}.dato", f"6/{vigente['id']}.json"])
    assert (directorio / "no-es-usuario").is_dir()
    del vencido


@pytest.mark.skipif(os.geteuid() == 0, reason="root lee directorios 000")
def test_una_carpeta_ilegible_no_aborta_la_limpieza_de_las_demas(directorio, caplog):
    vencido = _texto(directorio, ttl=1)
    _texto(directorio, user=AJENO, ttl=1)
    os.chmod(directorio / "5", 0)
    try:
        almacen.limpiar(directorio, ahora=AHORA + timedelta(hours=2))
    finally:
        os.chmod(directorio / "5", 0o700)
    assert not (directorio / "6").exists()
    assert (directorio / "5" / f"{vencido['id']}.json").exists()
    assert "salteó" in caplog.text


def test_una_subida_que_llega_justo_despues_de_borrar_su_carpeta_vacia_la_recrea(directorio):
    """Carrera limpiador/subida: la subida preparó su carpeta, el limpiador
    la encontró vacía y la borró; la subida igual escribe."""
    carpeta = almacen.preparar_carpeta(directorio, "5")
    almacen.limpiar(directorio, ahora=AHORA)
    assert not carpeta.exists()
    destino = carpeta / almacen.nombre_temporal()
    assert almacen.copiar_subida(io.BytesIO(PNG), destino, 10 * 1024 * 1024) == len(PNG)
    meta = almacen.guardar_imagen(directorio, destino, user=DUENIO, mime="image/png", nombre="f.png",
                                  bytes_=len(PNG), ttl_horas=24, ahora=AHORA)
    assert asyncio.run(almacen.leer(meta["id"], DUENIO, ahora=AHORA))[1] == PNG
    # Y lo mismo para el texto: la carpeta desaparece entre medio.
    almacen.borrar_de_usuario(directorio, "5")
    assert not carpeta.exists()
    assert _texto(directorio)["user_id"] == "5"


def test_limpiar_no_borra_una_carpeta_con_una_subida_en_curso(directorio):
    carpeta = almacen.preparar_carpeta(directorio, "5")
    (carpeta / almacen.nombre_temporal()).write_bytes(b"x")
    almacen.limpiar(directorio, ahora=AHORA)
    assert carpeta.is_dir()


# ---------------------------------------------------------- baja de usuario

def test_la_baja_borra_la_carpeta_del_usuario_y_nada_mas(directorio):
    _texto(directorio)
    _texto(directorio)
    (directorio / "5" / almacen.nombre_temporal()).write_bytes(b"x")
    ajeno = _texto(directorio, user=AJENO)
    assert almacen.borrar_de_usuario(directorio, "5") == 5
    assert not (directorio / "5").exists()
    assert _archivos(directorio) == sorted([f"6/{ajeno['id']}.dato", f"6/{ajeno['id']}.json"])


def test_la_baja_de_un_usuario_sin_carpeta_no_falla(directorio):
    assert almacen.borrar_de_usuario(directorio, "5") == 0
