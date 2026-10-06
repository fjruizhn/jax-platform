"""El aviso del freno de incertidumbre contra un Telegram simulado, con reloj falso (ronda 5 de #203).

Se simula el camino REAL: `_despachar` -> `_enviar_aviso_freno_incertidumbre` -> `_enviar_telegram_con_desenlace`
-> `http_client`, con un cliente HTTP falso que cuenta los POST. Nada de esto toca la red ni la base: el reloj es una
lista y las 'filas en incertidumbre' son el dict del modulo.

Cada prueba mata un mutante concreto (ver su docstring). Las pruebas que solo usan el cliente HTTP no nombran
simbolos nuevos, asi se pueden correr tal cual contra el despachador de la ronda 4.
"""
import asyncio
import logging
import sys

import httpx
import pytest

import ajustes
import catalogo_modelos_ejecutor as ejecutor
import http_client
from proyectos_documentos import despachador

_ESTADO_INICIAL = {
    "_freno_incertidumbre_activo": False,
    "_ultimo_aviso_freno_incertidumbre": None,
    "_aviso_freno_incertidumbre_pendiente": False,
    "_supresion_freno_incertidumbre_registrada": False,
    "_aviso_freno_incidente_entregado": False,
    "_fallos_aviso_freno_incertidumbre": 0,
    "_ultimo_fallo_aviso_freno_incertidumbre": None,
    "_ultima_actividad_freno_incertidumbre": None,
    "_pausa_previa_al_incidente": None,
    "_clave_de_lectura_logueada": None,
    "_avisos_del_incidente": 0,
    "_inicio_incidente_civil": None,
}


async def _pasada_vacia(*_args):
    return None


class _Resp:
    def __init__(self, status, cuerpo):
        self.status_code = status
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


class _Telegram:
    """Cliente HTTP falso. `comportamiento(n, t)` -> 'ok' | 'read_timeout' | 'connect_error' | 'http_500'."""

    def __init__(self, reloj, comportamiento):
        self.reloj = reloj
        self.comportamiento = comportamiento
        self.tiempos = []  # segundos desde el inicio de la simulacion, uno por POST
        self.textos = []  # el texto de cada aviso

    async def post(self, url, **kwargs):
        n = len(self.tiempos) + 1
        self.textos.append(kwargs["data"]["text"])
        self.tiempos.append(self.reloj[0] - self.reloj[1])
        que = self.comportamiento(n, self.tiempos[-1])
        if que == "ok":
            return _Resp(200, {"ok": True})
        if que == "read_timeout":
            raise httpx.ReadTimeout("sin respuesta")
        if que == "connect_error":
            raise httpx.ConnectError("rechazada")
        if que == "http_500":
            return _Resp(500, {"ok": False})
        if que == "runtime_otro":
            raise RuntimeError("fallo interno del transporte")
        if que == "proxy_error":
            raise httpx.ProxyError("el proxy no responde")
        raise AssertionError(que)


async def _esperar_avisos():
    if despachador._avisos_freno_incertidumbre:
        await asyncio.gather(*tuple(despachador._avisos_freno_incertidumbre))


async def _simular(monkeypatch, *, duracion_s, hay_incidente, comportamiento, enfriamiento, reintento, tick_s=10,
                   textos=None):
    """Corre `_despachar` cada `tick_s` segundos falsos. `hay_incidente(t)` dice si en ese segundo hay 100 filas
    en incertidumbre (se llenan juntas y vencen juntas). Devuelve los segundos de cada POST a Telegram."""
    for nombre, valor in _ESTADO_INICIAL.items():
        monkeypatch.setattr(despachador, nombre, valor, raising=False)
    reloj = [1000.0, 1000.0]  # [ahora, inicio]
    telegram = _Telegram(reloj, comportamiento)

    async def leer(clave):
        if clave == ajustes.DOC_RUTAS_POR_TRABAJO:
            return 50
        if clave == ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S:
            return enfriamiento
        if clave == ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S:
            return reintento
        raise AssertionError(f"ajuste inesperado: {clave}")

    monkeypatch.setattr(despachador, "_reloj", lambda: reloj[0])
    monkeypatch.setattr(despachador.ajustes, "valor", leer)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)
    monkeypatch.setenv(ejecutor.TELEGRAM_TOKEN_ENV, "123456:token-de-prueba")
    monkeypatch.setenv(ejecutor.TELEGRAM_CHAT_ID_ENV, "-100999")
    original = http_client._client
    http_client._client = telegram
    despachador._en_incertidumbre.clear()
    try:
        for paso in range(duracion_s // tick_s):
            t = paso * tick_s
            if hay_incidente(t):
                if not despachador._en_incertidumbre:
                    despachador._en_incertidumbre.update({10**21 + i: reloj[0] + 10**7 for i in range(100)})
            else:
                despachador._en_incertidumbre.clear()
            await despachador._despachar(None)
            await _esperar_avisos()
            reloj[0] += tick_s
    finally:
        http_client._client = original
        despachador._en_incertidumbre.clear()
        await _esperar_avisos()
    if textos is not None:
        textos.extend(telegram.textos)
    return telegram.tiempos


def _oscila(t):
    """100 filas que vencen juntas: 200 s de incidente y 100 s de calma, cada 300 s."""
    return t % 300 < 200


def _siempre(_t):
    return True


def _huecos(tiempos):
    return [b - a for a, b in zip(tiempos, tiempos[1:])]


# ------------------------------------------------------------------ desenlace desconocido + incidente que oscila

async def test_read_timeout_con_incidentes_que_oscilan_da_a_lo_sumo_un_aviso_por_enfriamiento(monkeypatch):
    """24 h, 100 filas que vencen juntas cada 300 s, enfriamiento 3600, reintento 60, Telegram en ReadTimeout (el
    aviso pudo haber llegado). El desenlace desconocido cuenta como entregado: <= 24 avisos en 24 h. En la ronda
    4 el contador se reiniciaba al terminar cada incidente y esto daba un reintento por incidente (cientos)."""
    tiempos = await _simular(monkeypatch, duracion_s=24 * 3600, hay_incidente=_oscila,
                             comportamiento=lambda n, t: "read_timeout", enfriamiento=3600, reintento=60)
    assert 20 <= len(tiempos) <= 24, (len(tiempos), tiempos[:30])
    assert all(h >= 3600 for h in _huecos(tiempos)), _huecos(tiempos)


async def test_read_timeout_no_se_reintenta_dentro_del_mismo_incidente(monkeypatch):
    tiempos = await _simular(monkeypatch, duracion_s=1800, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "read_timeout", enfriamiento=3600, reintento=60)
    assert tiempos == [0.0], tiempos


# ------------------------------------------------------------------ fallo cierto: reintenta con espera creciente

async def test_fallo_cierto_con_incidentes_que_oscilan_sigue_reintentando_con_espera_creciente(monkeypatch):
    """Mata M3 (reiniciar el contador al terminar el incidente): con ConnectError y el mismo escenario de 24 h, la
    espera sigue 60, 120, 240... aunque el incidente termine y vuelva a empezar dentro del enfriamiento. Si el
    contador se reinicia en cada incidente, hay un envio cada ~300 s (cientos)."""
    tiempos = await _simular(monkeypatch, duracion_s=24 * 3600, hay_incidente=_oscila,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=3600, reintento=60)
    assert len(tiempos) >= 8, "dejo de reintentar"
    assert len(tiempos) <= 30, (len(tiempos), tiempos[:40])
    for i, hueco in enumerate(_huecos(tiempos), start=1):  # tras el fallo i, espera min(60 * 2**(i-1), 3600)
        assert hueco >= min(60 * 2 ** (i - 1), 3600), (i, hueco, tiempos[:12])


async def test_http_500_es_fallo_cierto_y_reintenta_con_espera_creciente(monkeypatch):
    tiempos = await _simular(monkeypatch, duracion_s=3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "http_500", enfriamiento=3600, reintento=60)
    assert tiempos[:4] == [0.0, 60.0, 180.0, 420.0], tiempos


async def test_una_excepcion_que_se_escapa_del_envio_es_fallo_cierto_con_espera_creciente(monkeypatch, caplog):
    """Una excepcion que escapa de `_enviar_telegram_con_desenlace` ANTES de enviar (aqui, el import interno de
    `redaccion` revienta) no rompe el ciclo y se trata como fallo cierto: espera creciente, nunca 'entregado'.
    Mata el mutante que devuelve DESCONOCIDO (aviso dado por entregado, sin reintento) en ese `except`."""
    monkeypatch.setitem(sys.modules, "redaccion", None)  # `from redaccion import ...` -> ImportError
    with pytest.raises(ImportError):
        await ejecutor._enviar_telegram_con_desenlace("hola")  # la excepcion SI escapa de la funcion
    with caplog.at_level(logging.ERROR):
        tiempos = await _simular(monkeypatch, duracion_s=3600, hay_incidente=_siempre,
                                 comportamiento=lambda n, t: "ok", enfriamiento=3600, reintento=60)
    assert tiempos == [], "no debia llegar a enviar nada"
    assert despachador._fallos_aviso_freno_incertidumbre >= 4
    assert "falló el envío del aviso de incertidumbre (ModuleNotFoundError)" in caplog.text


async def test_la_excepcion_que_se_escapa_reintenta_con_espera_creciente_y_el_ciclo_sigue(monkeypatch):
    async def revienta(_mensaje):
        raise RuntimeError("fallo inesperado antes de enviar")

    envios = []
    reloj_ref = {}

    async def contador(mensaje):
        envios.append(reloj_ref["t"]())
        await revienta(mensaje)

    monkeypatch.setattr(despachador, "_enviar_telegram_con_desenlace", contador)
    # el reloj de _simular es interno: se lee a traves del propio modulo parcheado dentro de la simulacion
    reloj_ref["t"] = lambda: despachador._reloj() - 1000.0
    await _simular(monkeypatch, duracion_s=3600, hay_incidente=_siempre,
                   comportamiento=lambda n, t: "ok", enfriamiento=3600, reintento=60)
    assert envios[:4] == [0.0, 60.0, 180.0, 420.0], envios


# ------------------------------------------------------------------ el contador SI se reinicia

async def test_la_entrega_confirmada_reinicia_el_contador(monkeypatch):
    """Mata 'sin reinicio tras entrega confirmada'. Dos fallos y una entrega en el incidente A; el incidente B
    empieza 60 s despues de que termine A (menos que el enfriamiento: no es el reinicio por pausa) y falla una
    vez: su reintento llega a los 10 s (1x), no a los 40 s (4x) que da un contador que arrastra los dos fallos."""
    def comportamiento(n, t):
        return {1: "connect_error", 2: "connect_error", 3: "ok", 4: "connect_error"}.get(n, "ok")

    tiempos = await _simular(monkeypatch, duracion_s=300, hay_incidente=lambda t: t < 60 or 120 <= t < 250,
                             comportamiento=comportamiento, enfriamiento=100, reintento=10)
    assert tiempos[:3] == [0.0, 10.0, 30.0], tiempos  # A: fallo, fallo (+10), entregado (+20)
    assert tiempos[3:5] == [130.0, 140.0], tiempos  # B (al vencer el enfriamiento): fallo y reintento tras 10 s


async def test_un_enfriamiento_completo_sin_incidente_reinicia_el_contador(monkeypatch):
    """Mata 'sin reinicio tras un enfriamiento completo sin incidente'. Cinco fallos en A; B empieza 210 s despues
    (>= 100 de enfriamiento): su espera arranca de nuevo en 10 s. Sin el reinicio, B arrastra el contador y espera
    100 s (tope)."""
    tiempos = await _simular(monkeypatch, duracion_s=700, hay_incidente=lambda t: t < 200 or 400 <= t < 600,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=100, reintento=10)
    assert tiempos[:5] == [0.0, 10.0, 30.0, 70.0, 150.0], tiempos  # A: 10, 20, 40, 80
    assert tiempos[5:8] == [400.0, 410.0, 430.0], tiempos  # B desde cero: 10, 20


async def test_el_desconocido_no_reinicia_el_contador_de_fallos(monkeypatch):
    """Mata D2 (DESCONOCIDO tambien reinicia el contador): solo una entrega CONFIRMADA reinicia. Dos fallos ciertos y
    un ReadTimeout en A (el contador sigue en 2); B empieza 60 s despues de A (menos que el enfriamiento) y su primer
    envio falla: el contador pasa a 3 y el reintento llega a los 40 s (4x), no a los 10 s (1x) de un contador
    reiniciado."""
    def comportamiento(n, t):
        return {1: "connect_error", 2: "connect_error", 3: "read_timeout", 4: "connect_error"}.get(n, "ok")

    tiempos = await _simular(monkeypatch, duracion_s=300, hay_incidente=lambda t: t < 60 or 110 <= t < 250,
                             comportamiento=comportamiento, enfriamiento=100, reintento=10)
    assert tiempos[:3] == [0.0, 10.0, 30.0], tiempos
    assert tiempos[3:5] == [130.0, 170.0], tiempos


# ------------------------------------------------------------------ desconocido: nunca silencio con el freno activo

async def test_read_timeout_con_el_freno_continuo_recuerda_cada_enfriamiento(monkeypatch):
    """24 h con el freno activo sin pausa y ReadTimeout: el desconocido cuenta como entregado SOLO para el
    enfriamiento. Entre 1 y 24 avisos, nunca menos de uno por enfriamiento, y el ultimo dentro del ultimo
    enfriamiento: nada de un aviso y silencio por dias."""
    tiempos = await _simular(monkeypatch, duracion_s=24 * 3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "read_timeout", enfriamiento=3600, reintento=60)
    assert 1 <= len(tiempos) <= 24, (len(tiempos), tiempos)
    assert all(3600 <= h <= 3600 + 10 for h in _huecos(tiempos)), _huecos(tiempos)
    assert tiempos[-1] >= 24 * 3600 - 3600 - 10, tiempos[-3:]


async def test_la_entrega_confirmada_tambien_recuerda_cada_enfriamiento(monkeypatch):
    tiempos = await _simular(monkeypatch, duracion_s=6 * 3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "ok", enfriamiento=3600, reintento=60)
    assert tiempos == [0.0, 3600.0, 7200.0, 10800.0, 14400.0, 18000.0], tiempos


async def test_un_enfriamiento_ilegible_incluido_el_cero_falla_cerrado_sin_enviar(monkeypatch):
    """El 0 ya no existe (minimo 60 s): un 0 guardado se lee como ilegible (ajustes.valor lanza AjusteIlegible) y el
    aviso NO sale, ni un aviso por ciclo ni uno solo; el freno sigue cerrado."""
    for nombre, valor in _ESTADO_INICIAL.items():
        monkeypatch.setattr(despachador, nombre, valor, raising=False)

    async def leer(clave):
        if clave == ajustes.DOC_RUTAS_POR_TRABAJO:
            return 50
        if clave == ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S:
            return ajustes.DEFINICIONES[clave].interpretar("0")  # lanza ValorInvalido como la lectura real
        return 60

    monkeypatch.setattr(despachador.ajustes, "valor", leer)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)
    enviados = []

    async def no_debe_enviar(*_a, **_k):
        enviados.append(1)
        return Desenlace.ENTREGADO

    monkeypatch.setattr(despachador, "_enviar_telegram_con_desenlace", no_debe_enviar)
    despachador._en_incertidumbre.update({10**24 + i: despachador._reloj() + 10**6 for i in range(100)})
    try:
        for _ in range(30):
            await despachador._despachar(None)
            await _esperar_avisos()
    finally:
        despachador._en_incertidumbre.clear()
    assert enviados == []


async def test_proxy_error_permanente_reintenta_y_nunca_se_calla(monkeypatch):
    """ProxyError es fallo cierto: espera creciente con tope en el enfriamiento y, 24 h despues, sigue reintentando."""
    tiempos = await _simular(monkeypatch, duracion_s=24 * 3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "proxy_error", enfriamiento=3600, reintento=60)
    assert tiempos[:4] == [0.0, 60.0, 180.0, 420.0], tiempos
    assert max(_huecos(tiempos)) <= 3600 + 10, _huecos(tiempos)
    assert tiempos[-1] >= 24 * 3600 - 3600 - 10, tiempos[-3:]
    assert len(tiempos) >= 24, len(tiempos)


async def test_runtime_error_que_no_es_cliente_cerrado_recuerda_cada_enfriamiento(monkeypatch):
    """Un RuntimeError cualquiera es DESCONOCIDO (no se reintenta antes del enfriamiento), pero con el freno activo no
    queda en silencio: un recordatorio por enfriamiento."""
    tiempos = await _simular(monkeypatch, duracion_s=24 * 3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "runtime_otro", enfriamiento=3600, reintento=60)
    assert len(tiempos) == 24, (len(tiempos), tiempos[:5])
    assert all(3600 <= h <= 3600 + 10 for h in _huecos(tiempos)), _huecos(tiempos)


# ------------------------------------------------------------------ topes de la espera

async def test_la_espera_tiene_tope_en_el_enfriamiento(monkeypatch):
    """Mata M5 (sin tope): un incidente largo de 8 h con fallos ciertos sigue reintentando como maximo cada
    `enfriamiento`; sin tope la espera se duplica sin fin (3840, 7680...) y el aviso queda mudo horas."""
    tiempos = await _simular(monkeypatch, duracion_s=8 * 3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=3600, reintento=60)
    assert max(_huecos(tiempos)) <= 3600 + 10, _huecos(tiempos)
    assert tiempos[-1] >= 8 * 3600 - 3700, "dejo de reintentar antes de terminar el incidente"


async def test_con_reintento_mayor_que_el_enfriamiento_la_espera_minima_sigue_rigiendo(monkeypatch):
    """Mata M6 (tope solo `enfriamiento`, sin el `max`): con el enfriamiento en su minimo (60 s) y un reintento de
    600 s, el tope daria 60 s y se reenviaria 10 veces mas seguido. La espera nunca baja del reintento minimo."""
    tiempos = await _simular(monkeypatch, duracion_s=3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=60, reintento=600)
    assert len(tiempos) >= 4, "dejo de reintentar"
    assert all(h >= 600 for h in _huecos(tiempos)), _huecos(tiempos)
    assert len(tiempos) <= 7, len(tiempos)


# ------------------------------------------------------------------ el log de lectura nombra la clave real

@pytest.mark.parametrize("clave_que_falla, como", [
    (ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S, "ilegible"),
    (ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S, "ilegible"),
    (ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S, "oserror"),
])
async def test_el_log_de_lectura_nombra_la_clave_que_fallo(monkeypatch, caplog, clave_que_falla, como):
    """La lectura de ajustes falla en una de DOS claves; el log nombra la que fallo, no siempre el enfriamiento."""
    for nombre, valor in _ESTADO_INICIAL.items():
        monkeypatch.setattr(despachador, nombre, valor, raising=False)

    async def leer(clave):
        if clave == ajustes.DOC_RUTAS_POR_TRABAJO:
            return 50
        if clave == clave_que_falla:
            raise ajustes.AjusteIlegible(clave, "invalido") if como == "ilegible" else OSError("no hay base")
        return 60

    monkeypatch.setattr(despachador.ajustes, "valor", leer)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)
    despachador._en_incertidumbre.clear()
    despachador._en_incertidumbre.update({10**22 + i: despachador._reloj() + 1000 for i in range(100)})
    try:
        with caplog.at_level(logging.ERROR):
            await despachador._despachar(None)
    finally:
        despachador._en_incertidumbre.clear()
    registros = [r.getMessage() for r in caplog.records if "no se pudo leer el ajuste" in r.getMessage()]
    assert len(registros) == 1, caplog.text
    otra = (ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S
            if clave_que_falla == ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S
            else ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S)
    assert clave_que_falla in registros[0] and otra not in registros[0], registros[0]


async def test_la_lectura_rota_se_loguea_una_vez_por_incidente_y_por_clave(monkeypatch, caplog):
    """N ciclos con la lectura del enfriamiento rota dan UN log; otro incidente (el contador pasa por cero) lo
    vuelve a emitir; y un cambio de la clave que falla dentro del mismo incidente tambien."""
    for nombre, valor in _ESTADO_INICIAL.items():
        monkeypatch.setattr(despachador, nombre, valor, raising=False)
    rota = [ajustes.DOC_FRENO_INCERTIDUMBRE_ENFRIAMIENTO_S]

    async def leer(clave):
        if clave == ajustes.DOC_RUTAS_POR_TRABAJO:
            return 50
        if clave in rota:
            raise ajustes.AjusteIlegible(clave, "invalido")
        return 60

    monkeypatch.setattr(despachador.ajustes, "valor", leer)
    monkeypatch.setattr(despachador, "_pasada_de_despacho", _pasada_vacia)

    def logs():
        return [r for r in caplog.records if "no se pudo leer el ajuste" in r.getMessage()]

    def incidente():
        despachador._en_incertidumbre.clear()
        despachador._en_incertidumbre.update({10**23 + i: despachador._reloj() + 10**6 for i in range(100)})

    try:
        with caplog.at_level(logging.ERROR):
            incidente()
            for _ in range(20):
                await despachador._despachar(None)
            assert len(logs()) == 1, caplog.text
            rota[:] = [ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S]  # cambia la clave que falla
            for _ in range(5):
                await despachador._despachar(None)
            assert len(logs()) == 2 and ajustes.DOC_FRENO_INCERTIDUMBRE_REINTENTO_S in logs()[1].getMessage()
            despachador._en_incertidumbre.clear()
            await despachador._despachar(None)  # el incidente termina
            incidente()  # y empieza otro
            for _ in range(5):
                await despachador._despachar(None)
            assert len(logs()) == 3, caplog.text
    finally:
        despachador._en_incertidumbre.clear()
        await _esperar_avisos()


def _horas_que_avanzan(monkeypatch):
    """`_hora_civil` falsa que avanza 7 minutos en cada llamada: si el texto la recalculara por aviso, la hora de
    inicio cambiaria de un aviso al siguiente."""
    from datetime import datetime, timedelta, timezone
    llamadas = []

    def hora():
        llamadas.append(1)
        return datetime(2026, 10, 6, 10, 5, tzinfo=timezone.utc) + timedelta(minutes=7 * (len(llamadas) - 1))

    monkeypatch.setattr(despachador, "_hora_civil", hora)
    return llamadas


async def test_el_recordatorio_dice_aviso_2_con_la_misma_hora_de_inicio(monkeypatch):
    """El recordatorio se distingue: «aviso N» sube con cada aviso del incidente y «activo desde HH:MM» es la hora en
    que empezo el incidente sin pausa, la misma en todos sus avisos."""
    _horas_que_avanzan(monkeypatch)
    textos = []
    await _simular(monkeypatch, duracion_s=3 * 3600, hay_incidente=_siempre,
                   comportamiento=lambda n, t: "ok", enfriamiento=3600, reintento=60, textos=textos)
    assert len(textos) == 3, textos
    assert "aviso 1, activo desde 10:05" in textos[0], textos[0]
    assert "aviso 2, activo desde 10:05" in textos[1], textos[1]
    assert "aviso 3, activo desde 10:05" in textos[2], textos[2]


async def test_un_reintento_tras_fallo_cierto_conserva_el_numero_de_aviso(monkeypatch):
    """Un envio que fallo de verdad no cuenta como aviso dado: su reintento sigue siendo el «aviso 1»."""
    _horas_que_avanzan(monkeypatch)
    textos = []
    await _simular(monkeypatch, duracion_s=600, hay_incidente=_siempre,
                   comportamiento=lambda n, t: "connect_error" if n < 3 else "ok",
                   enfriamiento=3600, reintento=60, textos=textos)
    assert len(textos) == 3 and all("aviso 1," in t for t in textos), textos


async def test_un_incidente_nuevo_reinicia_el_numero_y_la_hora_de_inicio(monkeypatch):
    _horas_que_avanzan(monkeypatch)
    textos = []
    await _simular(monkeypatch, duracion_s=1200, hay_incidente=lambda t: t < 100 or 700 <= t < 800,
                   comportamiento=lambda n, t: "ok", enfriamiento=600, reintento=10, textos=textos)
    assert len(textos) == 2, textos
    assert "aviso 1, activo desde 10:05" in textos[0] and "aviso 1, activo desde 10:12" in textos[1], textos
