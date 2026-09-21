"""_semantic_context no puede tumbar el turno de chat por una fila de memoria
con distancia inutilizable.

Incidente real, 2026-09-11 02:37:59: `search_similar_messages` devolvio filas
con `distancia=None` (embeddings "vector cero" de `messages`, ver
jax/memory/db.py::_nonzero_embedding_sql en el repo jax), y la comparacion
`r["distancia"] < 0.8` -- afuera del try que envuelve la consulta -- lanzo
TypeError y el turno entero termino en 500, en cualquier faceta.

La memoria semantica es un plus del turno, no una condicion para responder:
la consulta ya era fail-soft (su `except` devuelve []), pero el filtro que
consume el resultado no. La causa se cierra en la fuente (repo jax); esto es
la segunda capa, para que una fila mala futura cueste UN candidato y no el
turno.
"""
import asyncio

import api.chat as chat_mod


class _MemoriaFalsa:
    def __init__(self, filas):
        self.filas = filas

    async def search_similar_messages(self, *_args, **_kwargs):
        return self.filas

    async def get_facts(self, *_args, **_kwargs):
        return []

    async def search_similar_facts(self, *_args, **_kwargs):
        return []


def _con_memoria(monkeypatch, filas):
    async def _lista():
        return True

    monkeypatch.setattr(chat_mod, "_ensure_memory", _lista)
    monkeypatch.setattr(chat_mod, "_memory", _MemoriaFalsa(filas))


def _fila(contenido, distancia):
    return {"content": contenido, "role": "user", "started_at": None, "distancia": distancia}


def test_filas_con_distancia_inutilizable_se_descartan_sin_tumbar_el_turno(monkeypatch):
    _con_memoria(monkeypatch, [
        _fila("fila con distancia None", None),
        _fila("fila con distancia NaN", float("nan")),
        _fila("recuerdo real", 0.2),
    ])

    ctx = asyncio.run(chat_mod._semantic_context("hola", 1, None))

    # Contrapositivo: la fila buena SIGUE llegando al contexto -- descartar
    # todo tambien "no tumbaria el turno", pero perderia la memoria.
    assert ctx, "la fila real se perdio junto con las malas"
    texto = ctx[-1]["content"]
    assert "recuerdo real" in texto
    assert "distancia None" not in texto
    assert "distancia NaN" not in texto


def test_solo_filas_inutilizables_da_contexto_vacio(monkeypatch):
    _con_memoria(monkeypatch, [_fila("fila con distancia None", None)])

    assert asyncio.run(chat_mod._semantic_context("hola", 1, None)) == []
