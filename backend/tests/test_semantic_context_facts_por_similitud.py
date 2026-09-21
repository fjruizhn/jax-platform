"""`_semantic_context` inyecta facts por similitud vectorial en CADA turno,
no solo cuando `detect_completeness_intent` reconoce una de sus seis
categorías por palabra clave.

POR QUE EXISTE (2026-09-20, decisión de Fernando tras un fallo real).
Fernando le preguntó a JAX "jax sabes a que me dedico?" y contestó que no
sabía, pese a tener el hecho GUARDADO Y VERIFICADO (fact #7). Causa medida:
`detect_completeness_intent("jax sabes a que me dedico?")` da `None` (no
matchea ninguna de las seis categorías por palabra clave), así que el turno
nunca llamaba a `get_facts()` NI a ninguna búsqueda de facts por similitud
— esta última ni existía. `search_similar_facts` (jax/memory/db.py) es el
método nuevo; este archivo cubre el CABLEADO en `_semantic_context`, no la
lógica de umbral/scope/superseded/vencido, que ya tiene su propia suite en
el repo `jax` (tests/test_memoria_busqueda_facts.py).

Mismo patrón que test_semantic_context_fail_soft.py: una `_MemoriaFalsa`
que implementa la interfaz mínima de `MemoryDB`, sin tocar una base real.
"""
import asyncio

import api.chat as chat_mod


class _MemoriaFalsa:
    def __init__(self, facts_completeness=None, facts_similares=None,
                 falla_similares=False):
        self.facts_completeness = facts_completeness or []
        self.facts_similares = facts_similares or []
        self.falla_similares = falla_similares
        self.llamadas_similares: list[dict] = []

    async def search_similar_messages(self, *_args, **_kwargs):
        return []

    async def get_facts(self, *_args, **_kwargs):
        return self.facts_completeness

    async def search_similar_facts(self, query, **kwargs):
        self.llamadas_similares.append({"query": query, **kwargs})
        if self.falla_similares:
            raise RuntimeError("Ollama caído (simulado)")
        return self.facts_similares


def _con_memoria(monkeypatch, memoria):
    async def _lista():
        return True

    monkeypatch.setattr(chat_mod, "_ensure_memory", _lista)
    monkeypatch.setattr(chat_mod, "_memory", memoria)


def _fact(id_, texto):
    return {"id": id_, "fact_text": texto, "fact_type": "user", "distancia": 0.3}


# ---------------------------------------------------------------------------
# 1. El caso real: una pregunta que NO matchea completeness SI trae facts
# ---------------------------------------------------------------------------

def test_una_pregunta_que_no_matchea_completeness_igual_trae_facts_por_similitud(monkeypatch):
    """El caso real del bug: "jax sabes a que me dedico?" no matchea ninguna
    categoría de `detect_completeness_intent` (ver ese detector en
    jax/memory/db.py), pero `search_similar_facts` SI puede traer el hecho
    de ocupación por similitud vectorial."""
    memoria = _MemoriaFalsa(
        facts_completeness=[],
        facts_similares=[_fact(7, "Fernando Ruiz es Licenciado en administración de empresas")])
    _con_memoria(monkeypatch, memoria)

    ctx = asyncio.run(chat_mod._semantic_context("jax sabes a que me dedico?", 1, None))

    assert ctx, "el hecho de ocupación no llegó al contexto del turno"
    texto = ctx[-1]["content"]
    assert "Fernando Ruiz es Licenciado en administración de empresas" in texto


def test_sin_facts_similares_ni_completeness_el_contexto_queda_vacio(monkeypatch):
    """Control: una pregunta sin nada guardado cerca no inventa un bloque de
    facts -- contrapositivo del test de arriba, mismo criterio que
    test_semantic_context_fail_soft.py."""
    memoria = _MemoriaFalsa(facts_completeness=[], facts_similares=[])
    _con_memoria(monkeypatch, memoria)

    assert asyncio.run(chat_mod._semantic_context("que clima hace hoy?", 1, None)) == []


# ---------------------------------------------------------------------------
# 2. No duplicar: completeness y similitud pueden traer el MISMO fact
# ---------------------------------------------------------------------------

def test_un_fact_que_completeness_ya_trajo_no_se_repite_por_similitud(monkeypatch):
    """"que proyectos tenes activos?" matchea completeness (fact_type=project)
    Y el mismo fact puede además ser el más similar por embedding -- no
    puede aparecer dos veces en el prompt: eso es ruido y gasto de tokens
    doble por el MISMO hecho."""
    memoria = _MemoriaFalsa(
        facts_completeness=[_fact(55, "Fernando trabaja en ATENEAERP")],
        facts_similares=[_fact(55, "Fernando trabaja en ATENEAERP")])
    _con_memoria(monkeypatch, memoria)

    ctx = asyncio.run(chat_mod._semantic_context("que proyectos tenes activos?", 1, None))

    texto = ctx[-1]["content"]
    assert texto.count("Fernando trabaja en ATENEAERP") == 1, (
        f"el mismo fact aparece mas de una vez en el prompt:\n{texto}")


def test_un_fact_nuevo_por_similitud_se_suma_al_de_completeness(monkeypatch):
    """El contrapositivo del dedup: un fact que NO vino por completeness
    tiene que seguir apareciendo -- deduplicar no puede volverse "esconder
    todo lo que venga de similitud"."""
    memoria = _MemoriaFalsa(
        facts_completeness=[_fact(55, "Fernando trabaja en ATENEAERP")],
        facts_similares=[
            _fact(55, "Fernando trabaja en ATENEAERP"),
            _fact(52, "Fernando es el creador de ATENEAERP"),
        ])
    _con_memoria(monkeypatch, memoria)

    ctx = asyncio.run(chat_mod._semantic_context("que proyectos tenes activos?", 1, None))

    texto = ctx[-1]["content"]
    assert texto.count("Fernando trabaja en ATENEAERP") == 1
    assert "Fernando es el creador de ATENEAERP" in texto


# ---------------------------------------------------------------------------
# 3. Fail-soft: search_similar_facts caída no tumba el turno
# ---------------------------------------------------------------------------

def test_search_similar_facts_caida_no_tumba_el_turno(monkeypatch):
    """Mismo contrato que el resto del archivo: una búsqueda de memoria que
    falla dijea al turno responder SIN ese bloque, nunca con un 500."""
    memoria = _MemoriaFalsa(facts_completeness=[], falla_similares=True)
    _con_memoria(monkeypatch, memoria)

    ctx = asyncio.run(chat_mod._semantic_context("cualquier cosa", 1, None))
    assert ctx == []


def test_search_similar_facts_caida_no_tapa_el_bloque_de_completeness(monkeypatch):
    """El fail-soft es POR BLOQUE, no todo-o-nada: si completeness trajo algo
    y la búsqueda por similitud falla, el bloque de completeness igual
    llega."""
    memoria = _MemoriaFalsa(
        facts_completeness=[_fact(55, "Fernando trabaja en ATENEAERP")],
        falla_similares=True)
    _con_memoria(monkeypatch, memoria)

    ctx = asyncio.run(chat_mod._semantic_context("que proyectos tenes activos?", 1, None))
    assert ctx, "el bloque de completeness se perdio junto con la busqueda por similitud"
    assert "Fernando trabaja en ATENEAERP" in ctx[-1]["content"]


# ---------------------------------------------------------------------------
# 4. El scope y el historial se pasan tal cual a search_similar_facts
# ---------------------------------------------------------------------------

def test_pasa_user_id_project_id_y_recent_history_a_search_similar_facts(monkeypatch):
    memoria = _MemoriaFalsa(facts_completeness=[], facts_similares=[])
    _con_memoria(monkeypatch, memoria)
    historial = [{"role": "user", "content": "turno anterior"}]

    asyncio.run(chat_mod._semantic_context(
        "consulta", 42, 99, recent_history=historial))

    assert memoria.llamadas_similares, "no se llamo a search_similar_facts"
    llamada = memoria.llamadas_similares[0]
    assert llamada["query"] == "consulta"
    assert llamada["user_id"] == 42
    assert llamada["project_id"] == 99
    assert llamada["recent_history"] == historial
