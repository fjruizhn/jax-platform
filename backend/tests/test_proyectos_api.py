"""API /api/proyectos (Proyectos E1, T5, 2026-10-02).

Cada prueba usa su propio tenant (etiqueta unica -> id numerico determinista) y
sus propios usuarios, asi que ninguna ve lo que dejo otra. Las identidades son
filas reales de jax_users (tests/identidades.py): la autoridad la relee B9.
"""
import uuid

import pytest

from tests.identidades import cabeceras, sql, uid

P = "/api/proyectos"


class Mundo:
    """Un tenant de prueba y los usuarios que se le piden."""

    def __init__(self, client, tenant=None):
        self.client = client
        self.tenant = tenant or f"proy-{uuid.uuid4().hex}"
        self._h = {}

    def usuario(self, etiqueta, role="operator"):
        h = cabeceras(self.client, f"{self.tenant}-{etiqueta}", role=role, tenant_id=self.tenant)
        self._h[etiqueta] = h
        return h

    def id_de(self, etiqueta, role="operator"):
        return int(uid(self.client, f"{self.tenant}-{etiqueta}", role, self.tenant))

    def email_de(self, etiqueta, role="operator"):
        filas = self.client.portal.call(sql, "SELECT email FROM jax_users WHERE user_id=%s",
                                        (self.id_de(etiqueta, role),), True)
        return filas[0][0]

    def crear(self, h, nombre="Proyecto", descripcion=None, llave=None):
        return self.client.post(P, headers={**h, "Idempotency-Key": llave or str(uuid.uuid4())},
                                json={"nombre": nombre, "descripcion": descripcion})


def _code(r):
    return r.json()["detail"]["code"]


def test_crear_y_listar(client):
    m = Mundo(client)
    h = m.usuario("a")
    r = m.crear(h, "Alfa", "desc")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["nombre"] == "Alfa" and body["descripcion"] == "desc"
    assert body["papel"] == "OWNER" and body["estado"] == "ACTIVE"
    lista = client.get(P, headers=h).json()
    assert [p["id"] for p in lista["proyectos"]] == [body["id"]]
    assert lista["proyectos"][0]["papel"] == "OWNER" and lista["proyectos"][0]["estado"] == "ACTIVE"
    assert lista["siguiente"] is None


def test_descripcion_vacia_se_guarda_como_null(client):
    m = Mundo(client)
    h = m.usuario("a")
    r = m.crear(h, "Alfa", "   ")
    assert r.status_code == 201, r.text
    assert r.json()["descripcion"] is None
    r2 = client.put(f"{P}/{r.json()['id']}", headers=h, json={"nombre": "Beta", "descripcion": ""})
    assert r2.status_code == 200, r2.text
    assert r2.json()["descripcion"] is None and r2.json()["nombre"] == "Beta"


def test_crear_repetido_con_la_misma_llave_devuelve_el_mismo(client):
    m = Mundo(client)
    h = m.usuario("a")
    llave = str(uuid.uuid4())
    r1 = m.crear(h, "Alfa", llave=llave)
    r2 = m.crear(h, "Alfa", llave=llave)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    r3 = m.crear(h, "Otro nombre", llave=llave)
    assert r3.status_code == 409 and _code(r3) == "idempotencia_conflicto"


def test_crear_sin_llave_422(client):
    m = Mundo(client)
    h = m.usuario("a")
    r = client.post(P, headers=h, json={"nombre": "Alfa", "descripcion": None})
    assert r.status_code == 422


def test_llave_no_uuid_422(client):
    m = Mundo(client)
    h = m.usuario("a")
    r = m.crear(h, "Alfa", llave="no-es-uuid")
    assert r.status_code == 422 and _code(r) == "idempotencia_invalida"


def test_otro_tenant_da_404_igual_que_inexistente(client):
    a = Mundo(client)
    b = Mundo(client)
    pid = a.crear(a.usuario("a"), "Alfa").json()["id"]
    hb = b.usuario("b")
    ajeno = client.get(f"{P}/{pid}", headers=hb)
    inexistente = client.get(f"{P}/999999999", headers=hb)
    assert ajeno.status_code == inexistente.status_code == 404
    assert ajeno.json() == inexistente.json() == {"detail": {"code": "proyecto_no_encontrado"}}


def test_lector_no_invita_403(client):
    m = Mundo(client)
    dueno, lector = m.usuario("dueno"), m.usuario("lector")
    pid = m.crear(dueno, "Alfa").json()["id"]
    r = client.post(f"{P}/{pid}/miembros", headers=dueno, json={"email": m.email_de("lector"), "papel": "VIEWER"})
    assert r.status_code == 201, r.text
    r = client.post(f"{P}/{pid}/miembros", headers=lector, json={"email": m.email_de("dueno"), "papel": "VIEWER"})
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"


def test_ultimo_dueno_no_se_rebaja(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa").json()["id"]
    r = client.put(f"{P}/{pid}/miembros/{m.id_de('dueno')}", headers=h, json={"papel": "VIEWER"})
    assert r.status_code == 409 and _code(r) == "ultimo_dueno"
    assert client.get(f"{P}/{pid}", headers=h).json()["papel"] == "OWNER"


def test_invitar_cambiar_quitar(client):
    m = Mundo(client)
    dueno, otro = m.usuario("dueno"), m.usuario("otro")
    pid = m.crear(dueno, "Alfa").json()["id"]
    oid = m.id_de("otro")
    r = client.post(f"{P}/{pid}/miembros", headers=dueno, json={"email": m.email_de("otro"), "papel": "VIEWER"})
    assert r.status_code == 201 and r.json() == {"user_id": oid}
    r = client.post(f"{P}/{pid}/miembros", headers=dueno, json={"email": m.email_de("otro"), "papel": "VIEWER"})
    assert r.status_code == 409 and _code(r) == "ya_es_miembro"
    miembros = client.get(f"{P}/{pid}/miembros", headers=dueno).json()["miembros"]
    por_id = {x["user_id"]: x for x in miembros}
    assert por_id[oid]["papel"] == "VIEWER" and por_id[oid]["email"] == m.email_de("otro")
    assert set(por_id[oid]) == {"user_id", "email", "papel", "origen"}
    r = client.put(f"{P}/{pid}/miembros/{oid}", headers=dueno, json={"papel": "CONTRIBUTOR"})
    assert r.status_code == 204 and r.content == b""
    assert client.get(f"{P}/{pid}", headers=otro).json()["papel"] == "CONTRIBUTOR"
    r = client.delete(f"{P}/{pid}/miembros/{oid}", headers=dueno)
    assert r.status_code == 204
    assert client.get(f"{P}/{pid}", headers=otro).status_code == 404
    r = client.delete(f"{P}/{pid}/miembros/{oid}", headers=dueno)
    assert r.status_code == 404 and _code(r) == "miembro_no_encontrado"


def test_papel_no_asignable_422(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa").json()["id"]
    r = client.post(f"{P}/{pid}/miembros", headers=h, json={"email": m.email_de("dueno"), "papel": "REVIEWER"})
    assert r.status_code == 422


def test_archivar_y_restaurar(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa").json()["id"]
    r = client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "ARCHIVED"})
    assert r.status_code == 200 and r.json()["estado"] == "ARCHIVED"
    assert client.get(P, headers=h).json()["proyectos"] == []
    arch = client.get(P, headers=h, params={"vista": "archivados"}).json()["proyectos"]
    assert [p["id"] for p in arch] == [pid]
    r = client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "ACTIVE"})
    assert r.status_code == 200 and r.json()["estado"] == "ACTIVE"


def test_estado_disabled_no_se_acepta(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa").json()["id"]
    r = client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "DISABLED"})
    assert r.status_code == 422


def test_ocultar_requiere_admin(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa").json()["id"]
    assert client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "ARCHIVED"}).status_code == 200
    r = client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "HIDDEN"})
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"


def test_admin_oculta_y_lo_ve_en_ocultos(client):
    m = Mundo(client)
    h = m.usuario("adm", role="admin")
    pid = m.crear(h, "Alfa").json()["id"]
    assert client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "ARCHIVED"}).status_code == 200
    r = client.post(f"{P}/{pid}/estado", headers=h, json={"estado": "HIDDEN"})
    assert r.status_code == 200 and r.json()["estado"] == "HIDDEN"
    ocultos = client.get(P, headers=h, params={"vista": "ocultos"}).json()["proyectos"]
    assert [p["id"] for p in ocultos] == [pid]


def test_vista_ocultos_403_para_no_admin(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    r = client.get(P, headers=h, params={"vista": "ocultos"})
    assert r.status_code == 403 and _code(r) == "papel_insuficiente"


@pytest.mark.parametrize("valor", ["abc", "0", "-3"])
def test_cursor_invalido_422(client, valor):
    m = Mundo(client)
    r = client.get(P, headers=m.usuario("a"), params={"antes_de": valor})
    assert r.status_code == 422


@pytest.mark.parametrize("valor", [0, 101])
def test_limite_fuera_de_rango_422(client, valor):
    m = Mundo(client)
    r = client.get(P, headers=m.usuario("a"), params={"limite": valor})
    assert r.status_code == 422


def test_paginacion_con_cursor(client):
    m = Mundo(client)
    h = m.usuario("a")
    ids = [m.crear(h, f"P{i}").json()["id"] for i in range(3)]
    p1 = client.get(P, headers=h, params={"limite": 2}).json()
    assert [p["id"] for p in p1["proyectos"]] == sorted(ids, reverse=True)[:2]
    assert p1["siguiente"] == p1["proyectos"][-1]["id"]
    p2 = client.get(P, headers=h, params={"limite": 2, "antes_de": p1["siguiente"]}).json()
    assert [p["id"] for p in p2["proyectos"]] == [min(ids)] and p2["siguiente"] is None


def test_limite_100_con_101_proyectos_da_siguiente(client):
    m = Mundo(client)
    h = m.usuario("a")
    for i in range(101):
        assert m.crear(h, f"P{i}").status_code == 201
    r = client.get(P, headers=h, params={"limite": 100})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert len(cuerpo["proyectos"]) == 100
    assert cuerpo["siguiente"] is not None
    assert cuerpo["siguiente"] == cuerpo["proyectos"][-1]["id"]


def test_renombrar_valida_nombre(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa", "d").json()["id"]
    r = client.put(f"{P}/{pid}", headers=h, json={"nombre": "", "descripcion": None})
    assert r.status_code == 422
    r = client.put(f"{P}/{pid}", headers=h, json={"nombre": "   ", "descripcion": None})
    assert r.status_code == 422 and _code(r) == "datos_invalidos"
    r = client.put(f"{P}/{pid}", headers=h, json={"nombre": "Beta", "descripcion": None})
    assert r.status_code == 200 and r.json()["nombre"] == "Beta" and r.json()["descripcion"] is None


def test_put_sin_descripcion_422(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa", "conservame").json()["id"]
    r = client.put(f"{P}/{pid}", headers=h, json={"nombre": "Beta"})
    assert r.status_code == 422
    assert client.get(f"{P}/{pid}", headers=h).json()["descripcion"] == "conservame"


def test_candidatos_minimo_dos_letras(client):
    m = Mundo(client)
    dueno = m.usuario("dueno")
    m.usuario("zz")
    pid = m.crear(dueno, "Alfa").json()["id"]
    assert client.get(f"{P}/{pid}/candidatos", headers=dueno, params={"q": "t"}).json() == {"candidatos": []}
    email = m.email_de("zz")
    r = client.get(f"{P}/{pid}/candidatos", headers=dueno, params={"q": email[:12]})
    assert r.status_code == 200
    assert {"user_id": m.id_de("zz"), "email": email} in r.json()["candidatos"]


def test_ningun_500_filtra_mensaje_interno(client, monkeypatch):
    import api.proyectos as proyectos

    async def falla(*a, **k):
        raise RuntimeError("secreto")

    monkeypatch.setattr(proyectos, "list_projects_for_user", falla)
    m = Mundo(client)
    r = client.get(P, headers=m.usuario("a"))
    assert r.status_code == 500
    assert "secreto" not in r.text
    assert r.json() == {"detail": {"code": "proyectos_error"}}


def test_sin_sesion_401(client):
    assert client.get(P).status_code in (401, 403)


def test_patch_ya_no_existe_405(client):
    m = Mundo(client)
    h = m.usuario("dueno")
    pid = m.crear(h, "Alfa").json()["id"]
    assert client.patch(f"{P}/{pid}", headers=h, json={"nombre": "B", "descripcion": None}).status_code == 405
    r = client.patch(f"{P}/{pid}/miembros/{m.id_de('dueno')}", headers=h, json={"papel": "VIEWER"})
    assert r.status_code == 405


def test_cors_permite_idempotency_key_y_put():
    from fastapi.middleware.cors import CORSMiddleware
    from main import app

    opciones = next(mw.kwargs for mw in app.user_middleware if mw.cls is CORSMiddleware)
    assert "Idempotency-Key" in opciones["allow_headers"]
    assert "PUT" in opciones["allow_methods"] and "PATCH" not in opciones["allow_methods"]
