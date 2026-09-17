"""La ruta vieja del freno sigue frenando, del lado del ESCRITOR (frente B,
Task H, requisito del controlador principal 2026-09-17; rulings R11-R15).

La plataforma decide sus escrituras mirando SÓLO el archivo que ella escribe
(`pausa_presente`), e INFORMA con el freno completo (`interruptor_activo`,
que incluye la ruta heredada) más el campo `heredada`. Nunca borra la ruta
heredada: no le pertenece.

Puro: la transacción y la difusión se sustituyen (mismo arnés que
test_kill_switch.py). La ruta heredada se apunta a un tmp_path; el conftest
ya la desvía a un temporal inexistente en cada test."""
import importlib.util
import logging
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

import interruptor
import kill_switch
from tests.test_kill_switch import ADMIN, OPERADOR, entorno  # noqa: F401 -- fixture compartido

LITERAL_VIEJO = "/etc/jax/" + "PAUSE"
ES_ROOT = os.geteuid() == 0


@pytest.fixture
def vieja(monkeypatch, tmp_path):
    ruta = tmp_path / "vieja" / "PAUSE"
    ruta.parent.mkdir()
    # raising=False: contra el código viejo el atributo no existe y el test
    # tiene que caer en su assert, no en el monkeypatch.
    monkeypatch.setattr(interruptor, "RUTA_HEREDADA", ruta, raising=False)
    return ruta


def test_la_constante_es_la_ruta_que_la_gente_usa():
    spec = importlib.util.spec_from_file_location("interruptor_fresco", interruptor.__file__)
    fresco = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresco)
    assert fresco.RUTA_HEREDADA == Path(LITERAL_VIEJO)


def test_el_conftest_aisla_la_ruta_heredada():
    assert str(getattr(interruptor, "RUTA_HEREDADA", LITERAL_VIEJO)) != LITERAL_VIEJO


async def test_la_mesa_responde_423_con_solo_la_heredada(entorno, vieja):
    assert await kill_switch.exigir_mesa_libre(OPERADOR) is OPERADOR
    vieja.write_text("")
    with pytest.raises(HTTPException) as frenada:
        await kill_switch.exigir_mesa_libre(OPERADOR)
    assert (frenada.value.status_code, frenada.value.detail) == (423, "kill_switch_activo")


async def test_reanudar_con_las_dos_quita_la_nueva_audita_y_sigue_activo(entorno, vieja):
    ruta, eventos, base = entorno
    interruptor.escribir_pausa(ruta, "{}")
    vieja.write_text("")
    assert await kill_switch.reanudar(ADMIN) == {"activo": True, "cambio": True, "heredada": True}
    assert not ruta.exists()
    assert vieja.exists()
    assert base.filas == [("reanudar", 7)]
    # El freno sigue puesto: avisar "liberado" a las pestañas sería mentir.
    assert eventos == []


async def test_reanudar_con_solo_la_heredada_no_audita_ni_la_toca(entorno, vieja):
    ruta, eventos, base = entorno
    vieja.write_text("")
    assert await kill_switch.reanudar(ADMIN) == {"activo": True, "cambio": False, "heredada": True}
    assert vieja.exists()
    assert base.filas == [] and base.commits == 0 and eventos == []


async def test_activar_con_solo_la_heredada_escribe_la_nueva_y_audita(entorno, vieja):
    ruta, eventos, base = entorno
    vieja.write_text("")
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True, "heredada": True}
    assert ruta.exists()
    assert base.filas == [("activar", 7)]
    assert eventos == [("kill_switch_activated", {"activo": True})]


async def test_activar_con_solo_la_heredada_y_fsync_roto_cuenta_el_cambio_propio(entorno, vieja, monkeypatch):
    """R10 + R13: el "antes" de activar mira SÓLO el archivo propio. Con la
    heredada puesta, el link del archivo nuevo surte efecto y el fsync del
    directorio explota: esta llamada SÍ puso su freno (R9), así que es un
    cambio real con difusión y auditoría. Si "antes" contara la heredada, se
    respondería 503 "nada cambió" con el archivo recién puesto y sin
    auditar."""
    ruta, eventos, base = entorno
    vieja.write_text("")

    def _fsync_roto(_directorio):
        raise OSError("fsync roto")

    monkeypatch.setattr(interruptor, "_sincronizar_directorio", _fsync_roto)
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True, "heredada": True}
    assert ruta.exists()
    assert base.filas == [("activar", 7)]
    assert eventos == [("kill_switch_activated", {"activo": True})]


async def test_activar_y_reanudar_sin_heredada_la_informan_en_false(entorno):
    assert await kill_switch.activar(ADMIN) == {"activo": True, "cambio": True, "heredada": False}
    assert await kill_switch.reanudar(ADMIN) == {"activo": False, "cambio": True, "heredada": False}


async def test_el_informe_mira_la_heredada_una_sola_vez(entorno, vieja, monkeypatch):
    """Revisión final del frente B (hallazgo 9): `_informe` miraba la ruta
    heredada dos veces (`activo()` y `_heredada_activa()`). Un solo stat, y
    el resultado no cambia."""
    ruta, _, _ = entorno
    vieja.write_text("")
    miradas = []
    real = interruptor.pausa_presente

    def contar(objetivo):
        if str(objetivo) == str(vieja):
            miradas.append(objetivo)
        return real(objetivo)

    monkeypatch.setattr(interruptor, "pausa_presente", contar)
    assert kill_switch._informe(False) == {"activo": True, "cambio": False, "heredada": True}
    assert len(miradas) == 1


async def test_estado_incluye_heredada(entorno, vieja, monkeypatch):
    class _Cur:
        async def execute(self, *a):
            return None

        async def fetchone(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Conn(_Cur):
        def cursor(self):
            return _Cur()

    class _Pool:
        def acquire(self):
            return _Conn()

    async def pool():
        return _Pool()

    monkeypatch.setattr(kill_switch, "get_pool", pool)
    assert await kill_switch.estado() == {"activo": False, "heredada": False, "ultimo": None}
    vieja.write_text("")
    assert await kill_switch.estado() == {"activo": True, "heredada": True, "ultimo": None}


@pytest.mark.skipif(ES_ROOT, reason="root atraviesa cualquier permiso")
async def test_heredada_ilegible_frena_la_mesa(entorno, vieja):
    vieja.write_text("")
    vieja.parent.chmod(0)
    try:
        with pytest.raises(HTTPException) as frenada:
            await kill_switch.exigir_mesa_libre(OPERADOR)
    finally:
        vieja.parent.chmod(0o700)
    assert (frenada.value.status_code, frenada.value.detail) == (423, "kill_switch_activo")


async def test_el_warning_nombra_las_dos_rutas_una_vez(entorno, vieja, caplog):
    ruta, _, _ = entorno
    caplog.set_level(logging.WARNING)
    vieja.write_text("")
    assert kill_switch.activo() is True
    assert kill_switch.activo() is True
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1
    assert str(vieja) in avisos[0].getMessage() and str(ruta) in avisos[0].getMessage()


# --- Barrera de sesión sobre el freno de producción (revisión final del
# frente B, 2026-09-17). Antes miraba sólo el directorio del interruptor; la
# ruta heredada es un ARCHIVO suelto y un test que la creara o la borrara
# pasaba en verde. Sólo tmp_path: la barrera real nunca se ejercita contra /etc.

def test_la_barrera_vigila_la_ruta_heredada_real():
    import conftest
    assert conftest.HEREDADA_DE_PRODUCCION == LITERAL_VIEJO
    assert conftest.FRENO_DE_PRODUCCION == "/etc/jax/interruptor"


def _pasado(*rutas):
    import time
    viejo = time.time() - 100
    for r in rutas:
        os.utime(r, (viejo, viejo))


def test_barrera_sin_nada_en_disco_son_cero_cambios(tmp_path):
    import conftest
    import time
    assert conftest.cambios_del_freno_de_produccion(
        tmp_path / "no-dir", tmp_path / "no-PAUSE", time.time() - 1, False) == []


def test_barrera_detecta_la_heredada_que_aparece(tmp_path):
    import conftest
    import time
    inicio = time.time() - 1
    archivo = tmp_path / "PAUSE"
    archivo.write_text("")
    _pasado(archivo)  # aunque el mtime mienta: no existía al empezar
    assert conftest.cambios_del_freno_de_produccion(
        tmp_path / "no-dir", archivo, inicio, False) == [str(archivo)]


def test_barrera_detecta_la_heredada_que_cambia_o_desaparece(tmp_path):
    import conftest
    import time
    inicio = time.time() - 1
    archivo = tmp_path / "PAUSE"
    archivo.write_text("")
    _pasado(archivo)
    assert conftest.cambios_del_freno_de_produccion(tmp_path / "no-dir", archivo, inicio, True) == []
    os.utime(archivo, (inicio + 10, inicio + 10))
    assert conftest.cambios_del_freno_de_produccion(
        tmp_path / "no-dir", archivo, inicio, True) == [str(archivo)]
    archivo.unlink()
    assert conftest.cambios_del_freno_de_produccion(
        tmp_path / "no-dir", archivo, inicio, True) == [f"{archivo} (desapareció)"]


def test_barrera_detecta_altas_en_el_directorio(tmp_path):
    import conftest
    import time
    directorio = tmp_path / "interruptor"
    directorio.mkdir()
    viejo = directorio / "viejo"
    viejo.write_text("")
    _pasado(viejo, directorio)
    inicio = time.time() - 1
    assert conftest.cambios_del_freno_de_produccion(directorio, tmp_path / "no-PAUSE", inicio, False) == []
    (directorio / "PAUSE").write_text("")
    cambios = conftest.cambios_del_freno_de_produccion(directorio, tmp_path / "no-PAUSE", inicio, False)
    assert str(directorio / "PAUSE") in cambios
