"""Task 15 R12(a) (2026-09-16): la carga H dio NO-GO. `_ultimas_20_lineas`
recorria los 50 MB del audit en Python dentro de `to_thread`; con 10 lectores
el p95 de /api/health subio a 15,7-19,5x el de reposo (limite 5x) porque los
hilos le disputan el GIL al loop. Ahora lee desde el final del archivo, en
bloques hacia atras, hasta juntar 20 lineas no vacias: el costo es O(cola),
no O(archivo).

Tambien: el `exists()` del handler era un stat bloqueante en el loop y una
carrera (el archivo puede desaparecer entre el stat y el open, y eso daba 503).
Ahora un archivo ausente se resuelve dentro del hilo y es `[]`.

Puros: sin `client` ni DB.
"""
import asyncio
import builtins
import json
from pathlib import Path

import pytest

import api.audit as audit_mod
from auth.models import AuthUser

USUARIO = AuthUser(user_id="1", tenant_id="1", role="superadmin")


def _ev(i, relleno=0):
    return json.dumps({"event": f"E{i}", "r": "x" * relleno})


def _nombres(lineas):
    return [json.loads(l)["event"] for l in lineas]


def test_devuelve_las_ultimas_20_en_orden_de_archivo(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("".join(_ev(i) + "\n" for i in range(100)))
    assert _nombres(audit_mod._ultimas_20_lineas(ruta)) == [f"E{i}" for i in range(80, 100)]


def test_las_lineas_vacias_no_cuentan_entre_las_20(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("".join(_ev(i) + "\n\n  \n" for i in range(50)) + "\n\n")
    assert _nombres(audit_mod._ultimas_20_lineas(ruta)) == [f"E{i}" for i in range(30, 50)]


def test_archivo_sin_salto_final_incluye_la_ultima_linea(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("\n".join(_ev(i) for i in range(30)))
    assert _nombres(audit_mod._ultimas_20_lineas(ruta)) == [f"E{i}" for i in range(10, 30)]


def test_archivo_con_menos_de_20_lineas_las_devuelve_todas(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text(_ev(0) + "\n\n" + _ev(1) + "\n" + _ev(2))
    assert _nombres(audit_mod._ultimas_20_lineas(ruta)) == ["E0", "E1", "E2"]


def test_lineas_mas_largas_que_un_bloque(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    # 200 KB por linea: cada una cruza varios bloques de lectura.
    ruta.write_text("".join(_ev(i, relleno=200_000) + "\n" for i in range(25)))
    lineas = audit_mod._ultimas_20_lineas(ruta)
    assert _nombres(lineas) == [f"E{i}" for i in range(5, 25)]
    assert all(len(json.loads(l)["r"]) == 200_000 for l in lineas)


def test_multibyte_partido_en_el_borde_de_bloque_no_rompe(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    # Caracteres de 3 bytes en todo el archivo: algun borde de bloque cae
    # a mitad de uno; solo se decodifican lineas completas.
    ruta.write_text("".join(json.dumps({"event": f"E{i}", "r": "€" * 30_001}, ensure_ascii=False) + "\n"
                            for i in range(30)), encoding="utf-8")
    assert _nombres(audit_mod._ultimas_20_lineas(ruta)) == [f"E{i}" for i in range(10, 30)]


def test_archivo_vacio(tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("")
    assert audit_mod._ultimas_20_lineas(ruta) == []


def test_archivo_ausente_es_lista_vacia_dentro_del_hilo(tmp_path):
    assert audit_mod._ultimas_20_lineas(tmp_path / "no-existe.jsonl") == []


def test_carrera_el_archivo_desaparece_despues_del_stat_no_es_503(monkeypatch, tmp_path):
    """Un Path cuyo exists() dice True pero el archivo ya no esta: con el stat
    en el loop, el open del hilo lanzaba FileNotFoundError -> OSError -> 503."""
    class Fantasma(type(tmp_path)):
        def exists(self, *a, **k):
            return True

    monkeypatch.setattr(audit_mod, "AUDIT_LOG", Fantasma(tmp_path / "se-borro.jsonl"))
    assert asyncio.run(audit_mod.get_audit(user=USUARIO)) == {"events": []}


def test_lee_solo_la_cola_no_el_archivo_entero(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("".join(_ev(i, relleno=400) + "\n" for i in range(20_000)))  # ~8 MB
    tam = ruta.stat().st_size
    leidos = []
    open_real = builtins.open

    class Contador:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def __getattr__(self, nombre):
            return getattr(self._f, nombre)

        def read(self, *a):
            datos = self._f.read(*a)
            leidos.append(len(datos))
            return datos

        def __iter__(self):
            for linea in self._f:
                leidos.append(len(linea))
                yield linea

    monkeypatch.setattr(audit_mod, "open", lambda *a, **k: Contador(open_real(*a, **k)), raising=False)
    assert len(audit_mod._ultimas_20_lineas(ruta)) == 20
    assert sum(leidos) < 256 * 1024 < tam, (sum(leidos), tam)


def test_bytes_invalidos_en_la_cola_siguen_siendo_503(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_bytes(b'{"event": "X"}\n\xff\xfe\xfa\n')
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", ruta)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(audit_mod.get_audit(user=USUARIO))
    assert exc.value.status_code == 503


def test_el_handler_no_hace_stat_en_el_loop(monkeypatch, tmp_path):
    llamadas = []
    real = Path.exists

    def espia(self, *a, **k):
        llamadas.append(self)
        return real(self, *a, **k)

    ruta = tmp_path / "audit.jsonl"
    ruta.write_text(_ev(1) + "\n")
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", ruta)
    monkeypatch.setattr(Path, "exists", espia)
    assert _nombres([json.dumps(e) for e in asyncio.run(audit_mod.get_audit(user=USUARIO))["events"]]) == ["E1"]
    assert ruta not in llamadas
