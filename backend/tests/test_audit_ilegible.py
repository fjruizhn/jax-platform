"""Task 3 (2026-09-15), clase (b): GET /api/audit con un audit.jsonl que no se
puede leer respondia `{"events": []}` -- lo mismo que "no hubo eventos". Un
fallo del camino de AUDITORIA se disfrazaba de estado sano. Ahora es 503 con
el codigo estable `auditoria_ilegible`; el archivo vacio o ausente sigue
siendo `{"events": []}`.

Puros: llaman al handler directo, sin `client` ni DB.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException

import api.audit as audit_mod
from auth.models import AuthUser

USUARIO = AuthUser(user_id="1", tenant_id="1", role="operator")


def _leer(monkeypatch, ruta):
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", ruta)
    return asyncio.run(audit_mod.get_audit(user=USUARIO))


def test_audit_ilegible_por_io_es_503_no_lista_vacia(monkeypatch, tmp_path):
    # Un directorio en lugar del archivo: exists() es True y read_text lanza
    # OSError (IsADirectoryError), sin depender de permisos ni de ser root.
    ruta = tmp_path / "audit.jsonl"
    ruta.mkdir()
    with pytest.raises(HTTPException) as exc:
        _leer(monkeypatch, ruta)
    assert exc.value.status_code == 503
    assert exc.value.detail == "auditoria_ilegible"


def test_audit_con_bytes_que_no_son_utf8_es_503(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_bytes(b'{"event": "X"}\n\xff\xfe\xfa\n')
    with pytest.raises(HTTPException) as exc:
        _leer(monkeypatch, ruta)
    assert exc.value.status_code == 503
    assert exc.value.detail == "auditoria_ilegible"


def test_audit_vacio_sigue_siendo_sin_eventos(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("")
    assert _leer(monkeypatch, ruta) == {"events": []}


def test_audit_ausente_sigue_siendo_sin_eventos(monkeypatch, tmp_path):
    assert _leer(monkeypatch, tmp_path / "no-existe.jsonl") == {"events": []}


def test_audit_legible_devuelve_los_eventos_mas_nuevos_primero(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text("\n".join(json.dumps({"event": f"E{i}"}) for i in range(3)) + "\n")
    assert [e["event"] for e in _leer(monkeypatch, ruta)["events"]] == ["E2", "E1", "E0"]
