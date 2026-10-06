"""El aviso del freno de incertidumbre contra un Telegram simulado, con reloj falso (ronda 5 de #203).

Se simula el camino REAL: `_despachar` -> `_enviar_aviso_freno_incertidumbre` -> `_enviar_telegram_con_desenlace`
-> `http_client`, con un cliente HTTP falso que cuenta los POST. Nada de esto toca la red ni la base: el reloj es una
lista y las 'filas en incertidumbre' son el dict del modulo.

Cada prueba mata un mutante concreto (ver su docstring). Las pruebas que solo usan el cliente HTTP no nombran
simbolos nuevos, asi se pueden correr tal cual contra el despachador de la ronda 4.
"""
import asyncio
import logging

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

    async def post(self, url, **kwargs):
        n = len(self.tiempos) + 1
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
        raise AssertionError(que)


async def _esperar_avisos():
    if despachador._avisos_freno_incertidumbre:
        await asyncio.gather(*tuple(despachador._avisos_freno_incertidumbre))


async def _simular(monkeypatch, *, duracion_s, hay_incidente, comportamiento, enfriamiento, reintento, tick_s=10):
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


# ------------------------------------------------------------------ el contador SI se reinicia

async def test_la_entrega_confirmada_reinicia_el_contador(monkeypatch):
    """Mata 'sin reinicio tras entrega confirmada'. Dos fallos y una entrega en el incidente A; el incidente B
    empieza 60 s despues de que termine A (menos que el enfriamiento: no es el reinicio por pausa) y falla una
    vez: su reintento llega a los 10 s (1x), no a los 40 s (4x) que da un contador que arrastra los dos fallos."""
    def comportamiento(n, t):
        return {1: "connect_error", 2: "connect_error", 3: "ok", 4: "connect_error"}.get(n, "ok")

    tiempos = await _simular(monkeypatch, duracion_s=700, hay_incidente=lambda t: t < 400 or 450 <= t < 650,
                             comportamiento=comportamiento, enfriamiento=100, reintento=10)
    assert tiempos[:3] == [0.0, 10.0, 30.0], tiempos  # A: fallo, fallo (+10), entregado (+20)
    assert tiempos[3:5] == [450.0, 460.0], tiempos  # B: fallo y reintento tras 10 s


async def test_un_enfriamiento_completo_sin_incidente_reinicia_el_contador(monkeypatch):
    """Mata 'sin reinicio tras un enfriamiento completo sin incidente'. Cinco fallos en A; B empieza 210 s despues
    (>= 100 de enfriamiento): su espera arranca de nuevo en 10 s. Sin el reinicio, B arrastra el contador y espera
    100 s (tope)."""
    tiempos = await _simular(monkeypatch, duracion_s=700, hay_incidente=lambda t: t < 200 or 400 <= t < 600,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=100, reintento=10)
    assert tiempos[:5] == [0.0, 10.0, 30.0, 70.0, 150.0], tiempos  # A: 10, 20, 40, 80
    assert tiempos[5:8] == [400.0, 410.0, 430.0], tiempos  # B desde cero: 10, 20


# ------------------------------------------------------------------ topes de la espera

async def test_la_espera_tiene_tope_en_el_enfriamiento(monkeypatch):
    """Mata M5 (sin tope): un incidente largo de 8 h con fallos ciertos sigue reintentando como maximo cada
    `enfriamiento`; sin tope la espera se duplica sin fin (3840, 7680...) y el aviso queda mudo horas."""
    tiempos = await _simular(monkeypatch, duracion_s=8 * 3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=3600, reintento=60)
    assert max(_huecos(tiempos)) <= 3600 + 10, _huecos(tiempos)
    assert tiempos[-1] >= 8 * 3600 - 3700, "dejo de reintentar antes de terminar el incidente"


async def test_con_enfriamiento_cero_la_espera_minima_sigue_rigiendo(monkeypatch):
    """Mata M6 (tope solo `enfriamiento`, sin el `max`): con enfriamiento 0 el tope daria espera 0 y se reenviaria
    en CADA ciclo. La espera nunca baja del reintento minimo (60 s)."""
    tiempos = await _simular(monkeypatch, duracion_s=3600, hay_incidente=_siempre,
                             comportamiento=lambda n, t: "connect_error", enfriamiento=0, reintento=60)
    assert len(tiempos) >= 20, "dejo de reintentar"
    assert all(h >= 60 for h in _huecos(tiempos)), _huecos(tiempos)
    assert len(tiempos) <= 61, len(tiempos)


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
