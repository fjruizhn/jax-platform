"""Fix wave final, item 4 (2026-09-15): los tests de chat no dejan filas en
`axioma_usage` de jax_memory_test.

Cada turno de chat con respuesta del LLM llama a record_usage, que inserta
una fila real. Nadie la borraba: la tabla crecia con cada corrida de la suite.
El fixture autouse `_uso_de_chat_limpio` (conftest.py) anota el id EXACTO de
cada fila que escribe api.chat.record_usage durante el test y borra esos ids
al terminar -- ni por tenant ni por ventana de tiempo, asi que no toca filas
de otra sesion concurrente (la prueba de carga usa tenants >= 900000).

Los dos tests van en este orden (el de definicion, pytest no reordena): el
primero escribe y anota su fila; el segundo verifica que ya no esta.
"""
from unittest.mock import patch

import pytest

import http_client
from tests.identidades import sql, token_de, uid

pytestmark = pytest.mark.usefixtures("chat_sin_memoria")

ETIQUETA = "test-uso-de-chat-limpio"
_ANOTADO: dict[str, list[int]] = {}


class _Resp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _LLMFalso:
    async def post(self, url, **kwargs):
        if "/motor/authorize-facet" in url:
            return _Resp({"allowed": True, "reason": "OK"})
        return _Resp({
            "choices": [{"message": {"content": '{"claim": [], "analysis": "ok", "judgment": null}'}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        })


def test_1_un_turno_de_chat_escribe_su_fila_de_uso(client):
    user_id = int(uid(client, ETIQUETA))
    token = token_de(client, ETIQUETA, "operator", "1")
    ((antes,),) = client.portal.call(sql, "SELECT COALESCE(MAX(id), 0) FROM axioma_usage", (), True)
    original = http_client._client
    http_client._client = _LLMFalso()
    try:
        with patch("jax_engine.background.add_safe_task"):
            resp = client.post("/api/chat", json={"message": "hola", "facet": "jekyll"},
                               headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original
    assert resp.status_code == 200, resp.text
    filas = client.portal.call(
        sql, "SELECT id FROM axioma_usage WHERE user_id = %s AND tenant_id = 1 AND id > %s",
        (user_id, antes), True)
    assert len(filas) == 1, "el turno tiene que escribir su fila (si no, el test 2 no prueba nada)"
    _ANOTADO["ids"] = [f[0] for f in filas]


def test_2_al_terminar_el_test_de_chat_su_fila_ya_no_esta(client):
    ids = _ANOTADO.get("ids")
    assert ids, "corre despues de test_1 en el mismo archivo"
    marcas = ", ".join(["%s"] * len(ids))
    restantes = client.portal.call(
        sql, f"SELECT id FROM axioma_usage WHERE id IN ({marcas})", tuple(ids), True)
    assert list(restantes) == [], f"quedaron filas del test de chat: {restantes}"
