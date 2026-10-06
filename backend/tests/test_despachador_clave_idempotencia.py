"""`clave_de_idempotencia` del despachador de documentos (auditoria de E3, 2026-10-06). Funcion pura: sin DB.

La clave viaja en `Idempotency-Key` a `POST /procesamiento/trabajos` de LAS MANOS. Tiene que ser
ESTABLE (la misma tras un reinicio) y cambiar cuando cambia el trabajo logico que representa. Equivocarse
hacia «cambia de mas» solo cuesta un OCR duplicado; hacia «no cambia» devolveria un trabajo viejo y el
documento nunca se procesaria: por eso cada campo de la fila que define el intento esta cubierto.
"""
import datetime
import re

from proyectos_documentos import despachador

PROYECTO = "0192f1d2-7c3a-7b4e-9a10-3f5e2d1c0b9a"
T0 = datetime.datetime(2026, 10, 6, 12, 0, 0, 123456)


def _fila(id_=1, sha="a" * 64, ruta=None, actualizado=T0):
    return {"id": id_, "sha256": sha, "ruta_entrada": ruta or f"proyectos/{PROYECTO}/entrada/l1/{id_}.pdf",
            "actualizado_at": actualizado}


def test_la_clave_es_estable_y_tiene_el_formato_que_acepta_las_manos():
    filas = [_fila(1), _fila(2)]
    clave = despachador.clave_de_idempotencia(PROYECTO, filas)
    assert clave == despachador.clave_de_idempotencia(PROYECTO, [dict(f) for f in filas])
    assert re.fullmatch(r"[A-Za-z0-9._:\-]{16,128}", clave)


def test_no_depende_del_orden_de_las_filas():
    a, b = _fila(1), _fila(2)
    assert despachador.clave_de_idempotencia(PROYECTO, [a, b]) == despachador.clave_de_idempotencia(PROYECTO, [b, a])


def test_cambia_con_cada_dato_que_define_el_trabajo():
    base = despachador.clave_de_idempotencia(PROYECTO, [_fila(1)])
    variantes = {
        "otro proyecto": despachador.clave_de_idempotencia("0192f1d2-7c3a-7b4e-9a10-3f5e2d1c0b9b", [_fila(1)]),
        "otro documento": despachador.clave_de_idempotencia(PROYECTO, [_fila(2)]),
        "otro sha256": despachador.clave_de_idempotencia(PROYECTO, [_fila(1, sha="b" * 64)]),
        "otra ruta": despachador.clave_de_idempotencia(PROYECTO, [_fila(1, ruta=f"proyectos/{PROYECTO}/fuente/x.pdf")]),
        "otro intento": despachador.clave_de_idempotencia(PROYECTO, [_fila(1, actualizado=T0 + datetime.timedelta(microseconds=1))]),
        "otro conjunto": despachador.clave_de_idempotencia(PROYECTO, [_fila(1), _fila(2)]),
    }
    for nombre, clave in variantes.items():
        assert clave != base, nombre
    assert len(set(variantes.values())) == len(variantes)


def test_los_campos_no_se_pueden_confundir_entre_si():
    """Sin separador, (id=1, sha='2...') y (id=12, sha='...') darian el mismo texto."""
    a = despachador.clave_de_idempotencia(PROYECTO, [_fila(1, sha="2" + "a" * 63)])
    b = despachador.clave_de_idempotencia(PROYECTO, [_fila(12, sha="a" * 64)])
    assert a != b


def test_acepta_la_fecha_como_texto_o_como_datetime():
    como_texto = {**_fila(1), "actualizado_at": T0.isoformat(timespec="microseconds")}
    assert despachador.clave_de_idempotencia(PROYECTO, [como_texto]) == despachador.clave_de_idempotencia(PROYECTO, [_fila(1)])
