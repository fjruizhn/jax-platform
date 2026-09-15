"""Task 6 S3 (2026-09-15): GET /api/audit (el log forense de LAS MANOS:
hosts, capacidades, que politica dijo que no y por que, stdout/stderr de lo
que ejecutan las facetas) solo exigia sesion. Un viewer lo leia entero.
Ahora exige require_superadmin: viewer y operator reciben 403.

Pasa por la autenticacion real (get_current_user lee el rol de jax_users),
asi que necesita jax_memory_test; sin DB se saltea.
"""
import json

import pytest

import api.audit as audit_mod
from tests.identidades import cabeceras


@pytest.fixture
def audit_de_prueba(monkeypatch, tmp_path):
    ruta = tmp_path / "audit.jsonl"
    ruta.write_text(json.dumps({"event": "EXECUTION", "stdout_tail": "salida-forense"}) + "\n")
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", ruta)
    return ruta


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_audit_es_403_para_quien_no_es_superadmin(client, audit_de_prueba, role):
    resp = client.get("/api/audit", headers=cabeceras(client, "t6-audit", role))
    assert resp.status_code == 403, resp.text
    assert "salida-forense" not in resp.text


def test_audit_es_200_para_el_superadmin(client, audit_de_prueba):
    resp = client.get("/api/audit", headers=cabeceras(client, "t6-audit", "superadmin"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["events"][0]["stdout_tail"] == "salida-forense"


def test_audit_sin_sesion_es_401_o_403(client, audit_de_prueba):
    resp = client.get("/api/audit")
    assert resp.status_code in (401, 403), resp.text
    assert "salida-forense" not in resp.text
