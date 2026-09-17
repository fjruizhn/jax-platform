"""Almacén de adjuntos por referencia (RD2, 2026-09-17; Principal Ruling del
mismo día y Ruling R23-2/R23-3).

Un adjunto subido se guarda en JAX_ADJUNTOS_DIR con un id aleatorio
(secrets.token_urlsafe) y un sidecar JSON atado a su DUEÑO. Todo lo que no es
"el dueño pide un id vigente" -- id desconocido, ajeno, vencido o malformado
(traversal incluido) -- es la MISMA excepción con el MISMO cuerpo 404. Puros:
sin base, sin app."""
import asyncio
import json
import os
import stat
import time
from datetime import datetime, timedelta, timezone

import pytest

from adjuntos import almacen
from adjuntos.errores import CODIGOS
from adjuntos.limites import LimitesDeAdjuntosInvalidos
from auth.models import AuthUser

DUENIO = AuthUser(user_id="5", tenant_id="1", role="operator")
AJENO = AuthUser(user_id="6", tenant_id="1", role="operator")
OTRO_TENANT = AuthUser(user_id="5", tenant_id="2", role="operator")
AHORA = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def directorio(tmp_path, monkeypatch):
    d = tmp_path / "adjuntos"
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    monkeypatch.setenv("JAX_ADJUNTOS_TTL_HORAS", "24")
    almacen.preparar_directorio()
    return d


def _guardar_imagen(directorio, user=DUENIO, datos=b"\x89PNG\r\n\x1a\nimagen", ahora=AHORA, ttl=24):
    temporal = directorio / almacen.nombre_temporal()
    temporal.write_bytes(datos)
    return almacen.guardar_imagen(directorio, temporal, user=user, mime="image/png", nombre="f.png",
                                  bytes_=len(datos), ttl_horas=ttl, ahora=ahora)


def _guardar_texto(directorio, user=DUENIO, texto="hola", ahora=AHORA, ttl=24):
    return almacen.guardar_texto(directorio, texto, user=user, origen="texto", nombre="n.md",
                                 bytes_=len(texto.encode()), recortado=False, ttl_horas=ttl, ahora=ahora)


def _no_encontrado(corutina):
    try:
        asyncio.run(corutina)
    except almacen.AdjuntoNoEncontrado as e:
        return e
    raise AssertionError("se esperaba AdjuntoNoEncontrado")


# ------------------------------------------------------------------ ids

def test_el_id_tiene_al_menos_128_bits_y_alfabeto_urlsafe():
    ids = {almacen.nuevo_id() for _ in range(200)}
    assert len(ids) == 200
    for i in ids:
        assert almacen.id_valido(i)
        assert len(i) == almacen.LARGO_ID
    # token_urlsafe(n) son n bytes aleatorios: 8*n bits.
    assert almacen.BYTES_DE_ENTROPIA * 8 >= 128


@pytest.mark.parametrize("malo", [
    "../x", "/etc/passwd", "..%2F..%2Fetc", "%2e%2e%2f", "a" * 31, "a" * 33, "a" * 31 + "=",
    "a" * 31 + "/", "a" * 31 + ".", "", None, 12345, "a" * 30 + "\x00b", "ñ" * 32,
])
def test_ids_malformados_no_son_validos(malo):
    assert not almacen.id_valido(malo)


# ----------------------------------------------------- guardar + obtener

def test_imagen_guardada_se_lee_por_su_duenio(directorio):
    meta = _guardar_imagen(directorio)
    assert meta["tipo"] == "imagen" and meta["mime"] == "image/png"
    assert meta["user_id"] == "5" and meta["tenant_id"] == "1"
    assert meta["creado"] == "2026-09-17T12:00:00Z" and meta["vence"] == "2026-09-18T12:00:00Z"
    leido, datos = asyncio.run(almacen.leer(meta["id"], DUENIO, ahora=AHORA))
    assert datos == b"\x89PNG\r\n\x1a\nimagen"
    assert leido == meta


def test_texto_guardado_guarda_el_texto_extraido_no_el_original(directorio):
    meta = _guardar_texto(directorio, texto="año ✓")
    assert (meta["tipo"], meta["origen"], meta["caracteres"], meta["recortado"]) == ("texto", "texto", 5, False)
    _, datos = asyncio.run(almacen.leer(meta["id"], DUENIO, ahora=AHORA))
    assert datos.decode("utf-8") == "año ✓"


def test_el_nombre_del_cliente_nunca_esta_en_una_ruta(directorio):
    temporal = directorio / almacen.nombre_temporal()
    temporal.write_bytes(b"x")
    almacen.guardar_imagen(directorio, temporal, user=DUENIO, mime="image/png", nombre="secreto.png",
                           bytes_=1, ttl_horas=1, ahora=AHORA)
    assert all("secreto" not in p.name for p in directorio.iterdir())


def test_modos_archivos_0600_y_directorio_0700(directorio):
    meta = _guardar_imagen(directorio)
    _guardar_texto(directorio)
    assert stat.S_IMODE(directorio.stat().st_mode) == 0o700
    archivos = list(directorio.iterdir())
    assert len(archivos) == 4 and meta["id"] + ".json" in {p.name for p in archivos}
    for p in archivos:
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name


def test_no_quedan_temporales_tras_guardar(directorio):
    _guardar_imagen(directorio)
    _guardar_texto(directorio)
    assert [p.name for p in directorio.iterdir() if p.name.startswith(".")] == []


# ------------------------------------------- todos los "no" son el mismo 404

def test_ajeno_vencido_desconocido_y_malformado_son_el_mismo_404(directorio):
    meta = _guardar_imagen(directorio)
    casos = {
        "ajeno": almacen.obtener(meta["id"], AJENO, ahora=AHORA),
        "otro_tenant": almacen.obtener(meta["id"], OTRO_TENANT, ahora=AHORA),
        "vencido": almacen.obtener(meta["id"], DUENIO, ahora=AHORA + timedelta(hours=24)),
        "desconocido": almacen.obtener(almacen.nuevo_id(), DUENIO, ahora=AHORA),
        "traversal": almacen.obtener("../" + meta["id"], DUENIO, ahora=AHORA),
        "absoluto": almacen.obtener(str(directorio / meta["id"]), DUENIO, ahora=AHORA),
        "codificado": almacen.obtener("..%2F" + meta["id"][5:], DUENIO, ahora=AHORA),
        "largo": almacen.obtener(meta["id"] + "a", DUENIO, ahora=AHORA),
        "alfabeto": almacen.obtener(meta["id"][:-1] + ".", DUENIO, ahora=AHORA),
    }
    errores = {k: _no_encontrado(c) for k, c in casos.items()}
    cuerpos = {(e.status, json.dumps(e.detail), str(e), e.args) for e in errores.values()}
    assert cuerpos == {(404, '{"code": "adjunto_no_encontrado"}', "adjunto_no_encontrado",
                        ("adjunto_no_encontrado",))}
    # El dueño, vigente, sí.
    assert asyncio.run(almacen.obtener(meta["id"], DUENIO, ahora=AHORA))["id"] == meta["id"]


def test_el_codigo_nuevo_esta_declarado():
    assert "adjunto_no_encontrado" in CODIGOS


def test_un_symlink_con_nombre_de_id_que_sale_del_directorio_es_404(directorio, tmp_path):
    fuera = tmp_path / "fuera.json"
    fuera.write_text(json.dumps({"user_id": "5", "tenant_id": "1", "vence": "2999-01-01T00:00:00Z"}))
    falso = "A" * almacen.LARGO_ID
    os.symlink(fuera, directorio / f"{falso}.json")
    _no_encontrado(almacen.obtener(falso, DUENIO, ahora=AHORA))


def test_sidecar_corrupto_es_404(directorio):
    meta = _guardar_imagen(directorio)
    (directorio / f"{meta['id']}.json").write_text("{no es json")
    _no_encontrado(almacen.obtener(meta["id"], DUENIO, ahora=AHORA))


def test_obtener_valida_el_id_antes_de_tocar_el_disco(directorio, monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("no debería llegar al disco")
    monkeypatch.setattr(almacen, "_cargar_sidecar", prohibido)
    _no_encontrado(almacen.obtener("../../etc/passwd", DUENIO, ahora=AHORA))


def test_vencido_es_404_aunque_el_limpiador_no_haya_corrido(directorio):
    meta = _guardar_imagen(directorio, ttl=1)
    assert (directorio / f"{meta['id']}.json").exists()
    _no_encontrado(almacen.leer(meta["id"], DUENIO, ahora=AHORA + timedelta(hours=1, seconds=1)))


# ------------------------------------------------------------- concurrencia

def test_dos_guardados_concurrentes_dan_ids_distintos_y_archivos_correctos(directorio):
    async def correr():
        return await asyncio.gather(
            asyncio.to_thread(_guardar_imagen, directorio, DUENIO, b"\x89PNG\r\n\x1a\nuno"),
            asyncio.to_thread(_guardar_imagen, directorio, AJENO, b"\x89PNG\r\n\x1a\ndos"))
    a, b = asyncio.run(correr())
    assert a["id"] != b["id"]
    assert asyncio.run(almacen.leer(a["id"], DUENIO, ahora=AHORA))[1].endswith(b"uno")
    assert asyncio.run(almacen.leer(b["id"], AJENO, ahora=AHORA))[1].endswith(b"dos")


def test_un_lector_abierto_lee_completo_tras_el_borrado(directorio):
    cuerpo = b"\x89PNG\r\n\x1a\n" + b"z" * 100_000
    meta = _guardar_imagen(directorio, datos=cuerpo)
    with open(directorio / f"{meta['id']}.dato", "rb") as f:
        almacen.limpiar(directorio, ahora=AHORA + timedelta(days=2))
        assert not (directorio / f"{meta['id']}.dato").exists()
        assert f.read() == cuerpo
    _no_encontrado(almacen.obtener(meta["id"], DUENIO, ahora=AHORA))


# ---------------------------------------------------------------- limpieza

def _envejecer(ruta, segundos):
    viejo = time.time() - segundos
    os.utime(ruta, (viejo, viejo))


def test_limpiar_borra_vencidos_y_huerfanos_y_deja_los_vigentes(directorio):
    vigente = _guardar_imagen(directorio, ttl=48)
    vencido = _guardar_texto(directorio, ttl=1)
    # Huérfanos viejos: dato sin sidecar, temporales de subida y de escritura.
    huerfano = directorio / f"{almacen.nuevo_id()}.dato"
    huerfano.write_bytes(b"x")
    subida_vieja = directorio / almacen.nombre_temporal()
    subida_vieja.write_bytes(b"x")
    escritura_vieja = directorio / ".tmp-abandonado"
    escritura_vieja.write_bytes(b"x")
    corrupto = directorio / f"{almacen.nuevo_id()}.json"
    corrupto.write_text("{roto")
    for p in (huerfano, subida_vieja, escritura_vieja, corrupto):
        _envejecer(p, almacen.ORFANO_MAX_SEGUNDOS + 60)
    # Huérfanos RECIENTES (una subida en curso): se quedan.
    subida_en_curso = directorio / almacen.nombre_temporal()
    subida_en_curso.write_bytes(b"x")
    dato_sin_sidecar_aun = directorio / f"{almacen.nuevo_id()}.dato"
    dato_sin_sidecar_aun.write_bytes(b"x")
    ajeno_al_almacen = directorio / "LEEME.txt"
    ajeno_al_almacen.write_text("no es nuestro")

    borrados = almacen.limpiar(directorio, ahora=AHORA + timedelta(hours=2))

    quedan = {p.name for p in directorio.iterdir()}
    assert quedan == {f"{vigente['id']}.json", f"{vigente['id']}.dato", subida_en_curso.name,
                      dato_sin_sidecar_aun.name, "LEEME.txt"}
    assert borrados == 6  # vencido (2 archivos) + 4 huérfanos
    assert asyncio.run(almacen.obtener(vigente["id"], DUENIO, ahora=AHORA))["id"] == vigente["id"]


def test_limpiar_sin_directorio_no_revienta(tmp_path):
    assert almacen.limpiar(tmp_path / "no-existe", ahora=AHORA) == 0


def test_el_bucle_de_limpieza_corre_al_arrancar_y_sobrevive_a_un_fallo(directorio, monkeypatch):
    llamadas = []

    def limpiar_falso(d, ahora=None):
        llamadas.append(d)
        if len(llamadas) == 1:
            raise OSError("disco")
        return 0

    async def sleep_falso(segundos):
        assert segundos == almacen.INTERVALO_DE_LIMPIEZA_SEGUNDOS
        if len(llamadas) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(almacen, "limpiar", limpiar_falso)
    monkeypatch.setattr(almacen, "_dormir", sleep_falso)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(almacen.start_limpieza_de_adjuntos())
    assert llamadas == [directorio, directorio]


# ---------------------------------------------------------- baja de usuario

def test_borrar_de_usuario_borra_solo_lo_suyo(directorio):
    suyo_a = _guardar_imagen(directorio, user=DUENIO)
    suyo_b = _guardar_texto(directorio, user=OTRO_TENANT)  # mismo user_id, otro tenant: el user_id es único
    ajeno = _guardar_imagen(directorio, user=AJENO)
    assert almacen.borrar_de_usuario(directorio, "5") == 4
    assert {p.name for p in directorio.iterdir()} == {f"{ajeno['id']}.json", f"{ajeno['id']}.dato"}
    del suyo_a, suyo_b


# ------------------------------------------------------ configuración fail-closed

@pytest.mark.parametrize("valor", [None, "", "relativo/adjuntos", "./adjuntos"])
def test_directorio_ausente_o_relativo_falla_cerrado(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTOS_DIR", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTOS_DIR", valor)
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        almacen.cargar_directorio()
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        almacen.preparar_directorio()


def test_preparar_crea_el_directorio_0700(tmp_path, monkeypatch):
    d = tmp_path / "nuevo"
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    assert almacen.preparar_directorio() == d
    assert d.is_dir() and stat.S_IMODE(d.stat().st_mode) == 0o700


def test_preparar_rechaza_un_directorio_abierto_a_otros(tmp_path, monkeypatch):
    d = tmp_path / "abierto"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        almacen.preparar_directorio()


def test_preparar_rechaza_un_archivo_en_lugar_de_directorio(tmp_path, monkeypatch):
    f = tmp_path / "archivo"
    f.write_text("x")
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(f))
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        almacen.preparar_directorio()


@pytest.mark.skipif(os.geteuid() == 0, reason="root escribe en cualquier directorio")
def test_preparar_rechaza_un_directorio_sin_escritura(tmp_path, monkeypatch):
    d = tmp_path / "solo-lectura"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o500)
    monkeypatch.setenv("JAX_ADJUNTOS_DIR", str(d))
    try:
        with pytest.raises(LimitesDeAdjuntosInvalidos):
            almacen.preparar_directorio()
    finally:
        os.chmod(d, 0o700)


@pytest.mark.parametrize("valor", [None, "", "0", "-1", "169", "1.5", "abc", "24h"])
def test_ttl_ausente_o_fuera_de_rango_falla_cerrado(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("JAX_ADJUNTOS_TTL_HORAS", raising=False)
    else:
        monkeypatch.setenv("JAX_ADJUNTOS_TTL_HORAS", valor)
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        almacen.cargar_ttl_horas()


@pytest.mark.parametrize("valor,esperado", [("1", 1), ("24", 24), ("168", 168)])
def test_ttl_en_rango(monkeypatch, valor, esperado):
    monkeypatch.setenv("JAX_ADJUNTOS_TTL_HORAS", valor)
    assert almacen.cargar_ttl_horas() == esperado


def test_lifespan_valida_el_almacen_antes_de_la_base(monkeypatch):
    """Sin JAX_ADJUNTOS_DIR el servicio no arranca, y falla antes de abrir la
    base (mismo lugar que los otros límites de adjuntos)."""
    import main

    monkeypatch.delenv("JAX_ADJUNTOS_DIR", raising=False)

    async def prohibido():
        raise AssertionError("abrió la base sin almacén de adjuntos")

    monkeypatch.setattr(main, "get_pool", prohibido)

    async def correr():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(LimitesDeAdjuntosInvalidos):
        asyncio.run(correr())


def test_lifespan_valida_el_ttl(monkeypatch):
    import main

    monkeypatch.setenv("JAX_ADJUNTOS_TTL_HORAS", "0")

    async def prohibido():
        raise AssertionError("abrió la base con un TTL inválido")

    monkeypatch.setattr(main, "get_pool", prohibido)

    async def correr():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(LimitesDeAdjuntosInvalidos):
        asyncio.run(correr())


# ------------------------------------------------ RD2 fix round 1 (2026-09-17)

def _scandir_con_primero(monkeypatch, primeros):
    """scandir determinista: las entradas problemáticas van PRIMERO, así un
    fallo que aborte la pasada se nota siempre, no según el orden del FS."""
    real = almacen._listar

    def ordenado(d):
        return sorted(real(d), key=lambda e: e.name not in primeros)

    monkeypatch.setattr(almacen, "_listar", ordenado)


@pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000")
def test_una_entrada_rota_no_aborta_la_limpieza_de_los_demas(directorio, monkeypatch):
    vencido = _guardar_texto(directorio, ttl=1)
    carpeta_sidecar = directorio / f"{almacen.nuevo_id()}.json"
    carpeta_sidecar.mkdir()
    carpeta_dato = directorio / f"{almacen.nuevo_id()}.dato"
    carpeta_dato.mkdir()
    _envejecer(carpeta_dato, almacen.ORFANO_MAX_SEGUNDOS + 60)
    ilegible = directorio / f"{almacen.nuevo_id()}.json"
    ilegible.write_text("{}")
    os.chmod(ilegible, 0)
    _scandir_con_primero(monkeypatch, {carpeta_sidecar.name, carpeta_dato.name, ilegible.name})
    try:
        almacen.limpiar(directorio, ahora=AHORA + timedelta(hours=2))
        quedan = {p.name for p in directorio.iterdir()}
    finally:
        os.chmod(ilegible, 0o600)
    assert f"{vencido['id']}.json" not in quedan and f"{vencido['id']}.dato" not in quedan
    assert {carpeta_sidecar.name, carpeta_dato.name, ilegible.name} <= quedan


def test_borrar_de_usuario_sigue_si_un_borrado_falla(directorio, monkeypatch):
    roto = _guardar_texto(directorio)
    sano = _guardar_texto(directorio)
    (directorio / f"{roto['id']}.dato").unlink()
    (directorio / f"{roto['id']}.dato").mkdir()  # unlink -> IsADirectoryError
    _scandir_con_primero(monkeypatch, {f"{roto['id']}.json"})
    almacen.borrar_de_usuario(directorio, "5")
    quedan = {p.name for p in directorio.iterdir()}
    assert f"{sano['id']}.json" not in quedan and f"{sano['id']}.dato" not in quedan


@pytest.mark.parametrize("valor", ["0024", "024", "01"])
def test_ttl_con_ceros_a_la_izquierda_falla_cerrado(monkeypatch, valor):
    monkeypatch.setenv("JAX_ADJUNTOS_TTL_HORAS", valor)
    with pytest.raises(LimitesDeAdjuntosInvalidos):
        almacen.cargar_ttl_horas()


def test_un_sidecar_escrito_despues_del_scandir_salva_al_dato(directorio, monkeypatch):
    """Segunda guarda del limpiador: la foto de scandir no tiene el sidecar,
    pero el sidecar ya existe cuando se decide borrar el dato."""
    id_ = almacen.nuevo_id()
    dato = directorio / f"{id_}.dato"
    dato.write_bytes(b"x")
    _envejecer(dato, almacen.ORFANO_MAX_SEGUNDOS + 60)
    foto = list(os.scandir(directorio))
    (directorio / f"{id_}.json").write_text("{}")
    monkeypatch.setattr(almacen, "_listar", lambda d: foto)
    almacen.limpiar(directorio, ahora=AHORA)
    assert dato.exists()


def test_el_dato_renombrado_no_hereda_la_edad_de_la_subida(directorio):
    """El mtime de un .dato renombrado sería el del temporal (fin de la
    copia, antes de la cola): guardar_imagen lo refresca, así la ventana
    dato-sin-sidecar nunca parece vieja."""
    temporal = directorio / almacen.nombre_temporal()
    temporal.write_bytes(b"\x89PNG\r\n\x1a\nx")
    _envejecer(temporal, 10 * almacen.ORFANO_MAX_SEGUNDOS)
    meta = almacen.guardar_imagen(directorio, temporal, user=DUENIO, mime="image/png", nombre="f.png",
                                  bytes_=9, ttl_horas=1, ahora=AHORA)
    assert time.time() - (directorio / f"{meta['id']}.dato").stat().st_mtime < 60


def test_el_margen_de_huerfanos_cubre_la_cola_de_subidas():
    """Peor espera de cola razonable: tope 1, cada subida hasta el timeout
    máximo de pypdf (60 s) + copia/clasificación; ver el comentario de
    ORFANO_MAX_SEGUNDOS."""
    from adjuntos.limites import LIMITE_TIMEOUT_DE_PDF_SEGUNDOS
    assert almacen.ORFANO_MAX_SEGUNDOS >= 200 * (LIMITE_TIMEOUT_DE_PDF_SEGUNDOS + 30)
