import json
import logging
import os
import re
import sys
import tomllib
import unicodedata
import uuid
from collections import OrderedDict
from functools import lru_cache
from datetime import datetime
from typing import NamedTuple
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx
from http_client import get_http_client
from auth.middleware import get_current_user
from auth.models import AuthUser
from jax_engine.schemas import JAXEvent
from jax_engine.events import event_bus
from jax_engine.state import engine_state
from api.admin.usage import record_usage
from db.connection import get_pool

router = APIRouter(prefix="/api")

CONFIG_PATH = os.path.expanduser("~/jax/config/config.toml")

# Carga el .env de JAX una vez al importar el módulo
def _load_jax_env():
    try:
        with open("/etc/jax/.env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_jax_env()

# --- Memoria semántica COMPARTIDA con el REPL (MISMA MariaDB jax_memory) ----
# Reutiliza la clase MemoryDB del núcleo (~/jax) — no duplica memoria ni lógica.
# Degrada elegante: si no carga o la base cae, el chat sigue SIN memoria.
sys.path.insert(0, os.path.expanduser("~/jax"))
try:
    from jax.memory.db import MemoryDB
except Exception:
    MemoryDB = None

_memory = None              # instancia única (lazy)
_memory_ready = False
# "user_id:project_id" -> conversation_uuid. OrderedDict como LRU: sin cota,
# cada par (usuario, proyecto) que alguna vez chateó quedaba abierto acá para
# siempre. Al superar MAX_TRACKED_CONVERSATIONS se cierra (end_conversation)
# la conversación menos recientemente activa antes de sacarla del dict —
# nunca se abandona una conversación abierta sin cerrarla en la DB.
_conv_uuids: OrderedDict[str, str] = OrderedDict()
MAX_TRACKED_CONVERSATIONS = 500


async def _ensure_memory() -> bool:
    """Conecta (lazy) a la MISMA jax_memory del REPL. False si falla (no rompe)."""
    global _memory, _memory_ready
    if MemoryDB is None:
        return False
    if _memory_ready and _memory and _memory.is_connected:
        return True
    if _memory is None:
        _memory = MemoryDB()
    try:
        _memory_ready = await _memory.connect(
            host=os.getenv("JAX_DB_HOST", "localhost"),
            user=os.getenv("JAX_DB_USER", ""),
            password=os.getenv("JAX_DB_PASSWORD", ""),
            database=os.getenv("JAX_DB_NAME", "jax_memory"),
        )
    except Exception:
        _memory_ready = False
    return _memory_ready


async def _evict_oldest_conversation_if_over_cap():
    if len(_conv_uuids) <= MAX_TRACKED_CONVERSATIONS:
        return
    # popitem() ANTES del await: dos evicciones concurrentes (dos conversaciones
    # nuevas distintas empujando el cap al mismo tiempo) no deben leer la misma
    # "más vieja" y cerrarla dos veces en la DB dejando el cap sin bajar nunca
    # — popitem() saca la entrada del dict de forma síncrona (sin punto de
    # yield), así que la segunda llamada concurrente ve el dict ya reducido y
    # saca la SIGUIENTE más vieja, no la misma.
    oldest_key, oldest_uuid = _conv_uuids.popitem(last=False)
    try:
        await _memory.end_conversation(oldest_uuid)
    except Exception:
        pass  # best-effort: queda abierta en la DB, pero ya no se trackea acá


async def _get_conv_uuid(user_id: int, tenant_id, project_id) -> str | None:
    """Conversación por (usuario, proyecto). Lazy. None si la memoria está caída.
    project_id NOT NULL -> memoria de proyecto (compartida); NULL -> individual."""
    if not await _ensure_memory():
        return None
    key = f"{user_id}:{project_id}"
    u = _conv_uuids.get(key)
    if u:
        _conv_uuids.move_to_end(key)
        return u
    source = "axioma-web-proyecto" if project_id is not None else "axioma-web"
    u = await _memory.start_conversation(source=source, user_id=user_id,
                                         tenant_id=tenant_id, project_id=project_id)
    if u:
        _conv_uuids[key] = u
        await _evict_oldest_conversation_if_over_cap()
    return u


async def _semantic_context(user_text: str, user_id: int, project_id) -> list[dict]:
    """Recupera contexto de sesiones pasadas (replica jax/core/main.py:514-533)
    con scope de dos niveles: memoria del proyecto + memoria individual del user."""
    if not await _ensure_memory():
        return []
    try:
        similares = await _memory.search_similar_messages(
            user_text, limit=5, user_id=user_id, project_id=project_id)
    except Exception:
        return []
    relevantes = [r for r in similares if r["distancia"] < 0.8]
    if not relevantes:
        return []
    lineas = []
    for r in relevantes:
        fecha = r["started_at"].strftime("%Y-%m-%d") if r.get("started_at") else "?"
        rol = "user" if r["role"] == "user" else "jax"
        lineas.append(f"[{fecha}] {rol}: {r['content']}")
    contexto = ("Conversaciones relevantes de sesiones anteriores:\n" + "\n".join(lineas))
    return [
        {"role": "user", "content": "[memoria de sesiones anteriores]"},
        {"role": "assistant", "content": contexto},
    ]


async def flush_open_conversations() -> int:
    """Cierra (end_conversation) las conversaciones web abiertas para que el
    worker de facts las destile. Se llama en el shutdown de la app. Best-effort:
    nunca lanza. Devuelve cuántas cerró."""
    global _memory_ready
    if not (_memory and _memory_ready):
        return 0
    n = 0
    for uuid_ in list(_conv_uuids.values()):
        try:
            await _memory.end_conversation(uuid_)
            n += 1
        except Exception:
            pass
    _conv_uuids.clear()
    try:
        await _memory.close()
    except Exception:
        pass
    _memory_ready = False
    return n
# ---------------------------------------------------------------------------

# Historial de conversación en memoria: user_id → lista de {role, content}.
# OrderedDict como LRU: el usuario más recientemente activo queda al final;
# al superar MAX_TRACKED_USERS se descarta el más antiguo — si no, cada
# user_id que alguna vez chateó queda en memoria para siempre.
_conversations: OrderedDict[str, list[dict]] = OrderedDict()
MAX_TURNS = 20  # 20 turnos = 40 mensajes (user+assistant) por usuario
MAX_TRACKED_USERS = 500  # usuarios distintos en memoria simultáneamente

logger = logging.getLogger(__name__)


def _sin_tildes(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Keywords por faceta — misma lógica que router.py de consola.
# Hyde NO es destino del auto-routing: es ejecutor, no conversador.
_KIMI_KW = frozenset((
    "codigo", "programar", "programa", "script", "funcion", "clase", "metodo",
    "modulo", "libreria", "api", "endpoint", "backend", "frontend",
    "implementar", "implementa", "construir", "refactor", "refactorizar",
    "refactoriza", "debug", "depurar", "bug", "traceback", "excepcion",
    "compilar", "test", "tests", "pytest", "variable", "bucle", "array",
    "regex", "fastapi", "react", "typescript", "javascript", "python", "sql",
    "docker", "nginx", "commit", "branch", "merge",
))
_KIMI_STRONG = frozenset((
    "refactor", "refactoriza", "implementar", "debug", "depurar", "pytest",
    "fastapi", "docker", "nginx", "endpoint",
))

_HIPATIA_KW = frozenset((
    "busca", "buscar", "investiga", "investigar", "verifica", "verificar",
    "fuentes", "fuente", "citas", "referencias", "noticias", "noticia",
    "actualidad", "reciente", "ultima", "ultimo", "vigente", "precio",
    "precios", "cotizacion", "mercado", "ley", "regulacion", "normativa",
    "paper", "papers", "estudio", "informe", "estadistica", "lanzamiento",
    "version actual", "quien es",
))
_HIPATIA_STRONG = frozenset((
    "busca", "buscar", "investiga", "investigar", "noticias", "fuentes",
    "version actual",
))

_JEKYLL_KW = frozenset((
    "poesia", "poema", "cuento", "novela", "literatura", "ensayo", "arte",
    "pintura", "musica", "filosofia", "etica", "estetica", "humanidades",
    "barroco", "renacimiento", "romanticismo", "mito", "mitologia", "simbolo",
    "simbolismo", "metafora", "narrativa", "personaje", "estilo",
    "interpretacion", "sentido", "significado", "reflexion", "reflexiona",
    "contempla", "humanista", "cultura", "historia del arte", "historia cultural",
))
_JEKYLL_STRONG = frozenset((
    "poema", "poesia", "filosofia", "literatura", "mitologia",
    "historia del arte", "barroco",
))

_THOT_KW = frozenset((
    "audita", "auditar", "auditoria", "critica", "criticar", "criticamente",
    "cuestiona", "cuestionar", "adversarial", "abogado del diablo", "riesgo",
    "riesgos", "falla", "fallas", "debilidad", "debilidades", "vulnerabilidad",
    "vulnerabilidades", "amenaza", "amenazas", "threat model",
    "modelo de amenazas", "ataque", "donde se rompe", "punto ciego",
    "supuesto", "supuestos", "contraargumento", "refuta", "refutar",
    "no-go", "revisa criticamente",
))
_THOT_STRONG = frozenset((
    "audita", "auditar", "auditoria", "vulnerabilidad", "vulnerabilidades",
    "threat model", "adversarial", "refuta",
))

_ADA_KW = frozenset((
    "formaliza", "formalizar", "formalizacion", "modelo formal", "pseudocodigo",
    "logica", "demuestra", "demostrar", "demostracion", "prueba formal",
    "teorema", "lema", "corolario", "axioma", "proposicion", "invariante",
    "invariantes", "precondicion", "postcondicion", "maquina de estados",
    "automata", "complejidad", "big o", "o(n)", "estructura de datos",
    "grafo", "arbol", "matriz", "vector", "ecuacion", "optimizacion",
    "funcion objetivo", "matematica", "calculo", "algebra", "probabilidad",
    "determinista", "induccion", "algoritmo",
))
_ADA_STRONG = frozenset((
    "formaliza", "formalizar", "demuestra", "demostrar", "teorema",
    "invariante", "invariantes", "precondicion", "postcondicion",
    "complejidad", "maquina de estados",
))

_WEB_KW_SETS = {
    "kimi":    (_KIMI_KW,    _KIMI_STRONG),
    "hipatia": (_HIPATIA_KW, _HIPATIA_STRONG),
    "jekyll":  (_JEKYLL_KW,  _JEKYLL_STRONG),
    "thot":    (_THOT_KW,    _THOT_STRONG),
    "ada":     (_ADA_KW,     _ADA_STRONG),
}
_WEB_TIEBREAK = ("hipatia", "thot", "ada", "kimi", "jekyll")


def _auto_route(message: str) -> str:
    """Scoring multi-keyword con umbral. Sin clasificador LLM (fase 1).

    Regla:
    - score[f] = n° de keywords de f que matchean.
    - top = faceta con mayor score (desempate: _WEB_TIEBREAK).
    - score >= 2 → enrutar a top.
    - score == 1 y keyword STRONG → enrutar a top.
    - else → jax_local (fallback; en fase 2 se evaluará clasificador LLM).
    """
    text = _sin_tildes(message.lower().strip())
    scores: dict[str, int] = {}
    hit_strong: dict[str, bool] = {}

    for faceta, (kws, strong) in _WEB_KW_SETS.items():
        score = 0
        is_strong = False
        for kw in kws:
            if " " in kw:
                hit = kw in text
            else:
                hit = bool(re.search(rf"\b{re.escape(kw)}\b", text))
            if hit:
                score += 1
                if kw in strong:
                    is_strong = True
        scores[faceta] = score
        hit_strong[faceta] = is_strong

    max_score = max(scores.values())
    faceta_elegida = "jax_local"
    via = "default"

    if max_score > 0:
        top: str | None = None
        for f in _WEB_TIEBREAK:
            if scores[f] == max_score:
                top = f
                break

        if top is not None:
            if max_score >= 2:
                faceta_elegida = top
                via = "keyword_score"
            elif max_score == 1 and hit_strong[top]:
                faceta_elegida = top
                via = "keyword_strong"

    logger.info(
        "auto_route | msg=%.80s | faceta=%s | score=%d | via=%s",
        message, faceta_elegida, max_score, via,
    )
    return faceta_elegida


class ChatRequest(BaseModel):
    message: str
    facet: str | None = None
    project_id: int | None = None   # None = memoria individual; set = memoria de proyecto


class ChatResponse(BaseModel):
    facet: str
    response: str
    timestamp: str
    # True cuando _parse_contract_response no pudo parsear el JSON de
    # contrato de una respuesta real de LLM (degradación auditada) — False
    # para respuestas enlatadas (is_canned) y para el intercept de hyde,
    # que nunca pasan por el parseo de contrato.
    contract_degraded: bool = False


@lru_cache(maxsize=1)
def _load_config() -> dict:
    # config.toml no tiene ningún escritor en runtime (el modelo activo vive
    # en la tabla facet_models, no acá — ver CLAUDE.md) — seguro cachear por
    # el ciclo de vida del proceso en vez de releerlo en cada request de chat.
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


async def _resolve_active_model(facet: str, fallback: str) -> str:
    """Modelo activo de la faceta segun la tabla facet_models (fuente de verdad
    editada desde el panel admin). Cae a `fallback` (el model_default de
    config.toml) si la faceta no tiene fila activa o si la DB no responde,
    para que el chat nunca se rompa si la tabla queda vacia o la DB esta caida."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT model_name FROM facet_models "
                    "WHERE facet = %s AND is_active = TRUE LIMIT 1",
                    (facet,),
                )
                row = await cur.fetchone()
    except Exception:
        return fallback
    return row[0] if row else fallback


class ContractResult(NamedTuple):
    contract_parsed: bool
    claims: list[dict]
    analysis: str
    judgment: str | None
    degradation_reason: str | None
    raw_text: str


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _degraded(raw_text: str, reason: str) -> ContractResult:
    return ContractResult(
        contract_parsed=False, claims=[], analysis=raw_text, judgment=None,
        degradation_reason=reason, raw_text=raw_text,
    )


def _parse_contract_response(raw_text: str) -> ContractResult:
    candidate = _strip_markdown_fence(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        return _degraded(raw_text, f"JSON no parsea: {e}")

    if not isinstance(data, dict):
        return _degraded(raw_text, f"JSON parseado no es un objeto (es {type(data).__name__})")

    if "analysis" not in data:
        return _degraded(raw_text, "falta la clave 'analysis' en el JSON")

    raw_claims = data.get("claim", [])
    if not isinstance(raw_claims, list):
        return _degraded(raw_text, f"'claim' no es una lista (es {type(raw_claims).__name__})")

    parsed_claims = []
    for item in raw_claims:
        if not isinstance(item, dict) or "predicate" not in item or "args" not in item:
            return _degraded(raw_text, f"claim mal formado: {item!r}")
        if not isinstance(item["predicate"], str) or not isinstance(item["args"], dict):
            return _degraded(raw_text, f"claim con tipos inválidos: {item!r}")
        parsed_claims.append({"predicate": item["predicate"], "args": item["args"]})

    analysis = data["analysis"]
    if not isinstance(analysis, str):
        return _degraded(raw_text, f"'analysis' no es string (es {type(analysis).__name__})")

    judgment = data.get("judgment")
    if judgment is not None and not isinstance(judgment, str):
        return _degraded(raw_text, f"'judgment' no es string ni null (es {type(judgment).__name__})")

    return ContractResult(
        contract_parsed=True, claims=parsed_claims, analysis=analysis,
        judgment=judgment, degradation_reason=None, raw_text=raw_text,
    )


def _build_display_response(contract: ContractResult) -> tuple[str, bool]:
    if not contract.contract_parsed:
        return contract.raw_text, True
    if contract.judgment:
        return f"{contract.analysis}\n\n**{contract.judgment}**", False
    return contract.analysis, False


# NOTA DE ALCANCE (SP2 Task 3, ver task-3-report.md): el brief original
# asume que _invoke_facet ya devuelve tuple[str, UsageInfo | None] y que
# "usage is None" distingue respuesta enlatada de llamada real al LLM —
# esa señal solo existe en infra/facetas-bloque-d (commit 448a707), que
# NO es ancestro de esta rama (SP2 está basada en master). Por ruling del
# coordinador, se sustituyó esa señal por un flag posicional explícito
# (is_canned, devuelto por _invoke_facet) en vez de comparar response_text
# contra los strings enlatados conocidos — evita que un futuro edit de esos
# strings rompa la señal en silencio. _CONTRACT_PROMPT_SUFFIX SÍ está
# conectado al system_prompt real dentro de _invoke_facet (ver esa función).
# Cuando infra/facetas-bloque-d se mergee, is_canned se reemplaza por
# "usage is None" — swap de flag, no heurística a desandar.
_CONTRACT_PROMPT_SUFFIX = """

FORMATO DE RESPUESTA OBLIGATORIO — respondé ÚNICAMENTE con un objeto JSON, sin texto antes ni después, sin fences de markdown:

{"claim": [{"predicate": "NOMBRE", "args": {"clave": "valor"}}], "analysis": "tu razonamiento en texto libre", "judgment": "tu conclusión, o null si no aplica"}

- "claim": lista de afirmaciones verificables (puede ir vacía: []). Cada una es {"predicate": "...", "args": {...}} — SOLO estos dos campos, nada más.
- "analysis": tu análisis en texto libre. Obligatorio, aunque sea corto.
- "judgment": tu conclusión o recomendación, o null si no aplica.

No incluyas ningún otro campo. No expliques el formato, solo respondé el JSON."""


def _build_messages(system_prompt: str, history: list[dict], message: str) -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(history)
    msgs.append({"role": "user", "content": message})
    return msgs


async def _call_ollama(system_prompt: str, history: list[dict], message: str, config: dict, model: str) -> str:
    url = config["personalities"]["jax_local"]["api_url"]
    messages = _build_messages(system_prompt, history, message)
    client = await get_http_client()
    r = await client.post(
        url,
        json={"model": model, "messages": messages, "stream": False, "keep_alive": -1},
        timeout=180.0,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


async def _call_openai_compat(
    base_url: str, api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
) -> str:
    messages = _build_messages(system_prompt, history, message)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    client = await get_http_client()
    r = await client.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages},
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def _call_gemini(
    api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [{"googleSearch": {}}],
    }
    client = await get_http_client()
    r = await client.post(url, json=body, timeout=120.0)
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


_MODEL_IDENTITY_WORDS = ("modelo", "model")
_MODEL_IDENTITY_SELF_REF = (
    "sos", "eres", "estas", "corres", "corriendo", "ejecuta", "ejecutas",
    "usas", "tenes", "tienes", "corre", "corriste",
    "are", "running", "use", "using",
)


def _is_model_identity_question(message: str) -> bool:
    """Detecta preguntas sobre que modelo ejecuta a la faceta ('que modelo
    sos', 'con que modelo estas corriendo'). Estas se resuelven con el dato
    real de _resolve_active_model, nunca con la respuesta del LLM: el modelo
    confabula su propia identidad incluso cuando el dato correcto ya esta en
    su contexto (REGLA DE EVIDENCIA — ver config.toml)."""
    text = _sin_tildes(message.lower().strip())
    has_model_word = any(re.search(rf"\b{kw}\b", text) for kw in _MODEL_IDENTITY_WORDS)
    if not has_model_word:
        return False
    return any(re.search(rf"\b{v}\b", text) for v in _MODEL_IDENTITY_SELF_REF)


_MODEL_IDENTITY_HOSTING = {
    "jax_local": "vía Ollama local en hall9000",
    "jekyll": "vía la API de DeepSeek",
    "hipatia": "vía la API de Gemini (Google)",
    "thot": "vía la API de OpenAI",
    "kimi": "vía la API de Moonshot",
    "ada": "vía la API de Zhipu (GLM)",
}


def _model_identity_reply(model: str, facet: str) -> str:
    hosting = _MODEL_IDENTITY_HOSTING.get(facet, "vía la API configurada para esta faceta")
    return (
        f"Corro con '{model}' {hosting} — dato leído en vivo del selector de "
        f"modelos activo, no de memoria."
    )


async def _invoke_facet(
    facet: str, config: dict, user_id: str, message: str,
    semantic_context: list[dict] | None = None,
) -> tuple[str, bool]:
    """Devuelve (texto, is_canned). is_canned=True en cada punto que
    responde sin llamar a un transporte real (hoy: solo las respuestas
    enlatadas de _model_identity_reply) — señal posicional sustituta de
    la UsageInfo-based ("usage is None") que asume el brief de SP2 Task 3,
    porque _invoke_facet en esta rama (base master) no tiene esa
    infraestructura (vive en infra/facetas-bloque-d, no mergeada acá; ver
    task-3-report.md). Cuando ese branch se mergee, is_canned se
    reemplaza por "usage is None" — swap de flag, no hay que desandar
    esta heurística."""
    history = _conversations.get(user_id, [])
    if semantic_context:
        # Contexto de sesiones pasadas SOLO para este turno (no entra al hilo RAM).
        history = semantic_context + history
    personality = config["personalities"].get(facet, config["personalities"]["jax_local"])
    system_prompt = personality.get("system_prompt", "Sos JAX.") + _CONTRACT_PROMPT_SUFFIX

    if facet == "jax_local":
        model = await _resolve_active_model(
            "jax_local", personality.get("model_default", "qwen3:14b"))

        if _is_model_identity_question(message):
            return _model_identity_reply(model, facet), True

        # Bug 3: jax_local no sabia con que modelo corre y confabulaba su
        # identidad. Le damos el dato real (el resuelto desde la DB) como
        # contexto informativo, no como algo que deba soltar sin que le pregunten.
        ident = (
            f"\n\nDato tecnico (para tu propia referencia, no lo repitas sin que "
            f"te pregunten): el modelo que te ejecuta en este momento es "
            f"'{model}', via Ollama local en hall9000."
        )
        return await _call_ollama(system_prompt + ident, history, message, config, model), False

    if facet == "jekyll":
        model = await _resolve_active_model("jekyll", personality.get("model_default", "deepseek-v4-flash"))
        if _is_model_identity_question(message):
            return _model_identity_reply(model, facet), True
        return await _call_openai_compat(
            "https://api.deepseek.com/v1",
            os.getenv("DEEPSEEK_API_KEY", ""),
            model, system_prompt, history, message,
        ), False

    if facet == "hipatia":
        model = await _resolve_active_model("hipatia", personality.get("model_default", "gemini-2.5-flash"))
        if _is_model_identity_question(message):
            return _model_identity_reply(model, facet), True
        return await _call_gemini(
            os.getenv("GEMINI_API_KEY", ""), model, system_prompt, history, message,
        ), False

    if facet == "thot":
        model = await _resolve_active_model("thot", personality.get("model_default", "gpt-4o"))
        if _is_model_identity_question(message):
            return _model_identity_reply(model, facet), True
        return await _call_openai_compat(
            "https://api.openai.com/v1",
            os.getenv("OPENAI_API_KEY", ""),
            model, system_prompt, history, message,
        ), False

    if facet == "kimi":
        api_url = personality.get("api_url", "https://api.moonshot.ai/v1/chat/completions")
        # Normalizar a base URL sin /chat/completions
        base_url = api_url[:-len("/chat/completions")] if api_url.endswith("/chat/completions") else api_url
        model = await _resolve_active_model("kimi", personality.get("model_default", "kimi-k2.7-code"))
        if _is_model_identity_question(message):
            return _model_identity_reply(model, facet), True
        return await _call_openai_compat(
            base_url, os.getenv("KIMI_API_KEY", ""), model, system_prompt, history, message,
        ), False

    if facet == "ada":
        api_url = personality.get("api_url", "https://api.z.ai/api/paas/v4/chat/completions")
        base_url = api_url[:-len("/chat/completions")] if api_url.endswith("/chat/completions") else api_url
        model = await _resolve_active_model("ada", personality.get("model_default", "glm-5.2"))
        if _is_model_identity_question(message):
            return _model_identity_reply(model, facet), True
        return await _call_openai_compat(
            base_url, os.getenv("ZAI_API_KEY", ""), model, system_prompt, history, message,
        ), False

    # fallback
    model = await _resolve_active_model(facet, personality.get("model_default", "qwen3:14b"))
    if _is_model_identity_question(message):
        return _model_identity_reply(model, facet), True
    return await _call_ollama(system_prompt, history, message, config, model), False


def _update_history(user_id: str, user_msg: str, assistant_msg: str):
    history = _conversations.get(user_id, [])
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})
    # Mantener máximo MAX_TURNS turnos (2 mensajes por turno)
    if len(history) > MAX_TURNS * 2:
        history = history[-(MAX_TURNS * 2):]
    _conversations[user_id] = history
    _conversations.move_to_end(user_id)
    if len(_conversations) > MAX_TRACKED_USERS:
        _conversations.popitem(last=False)


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: AuthUser = Depends(get_current_user)):
    config = _load_config()
    facet = req.facet if req.facet else _auto_route(req.message)
    tenant_id = user.tenant_id
    user_id = user.user_id
    timestamp = datetime.utcnow().isoformat() + "Z"

    # --- Memoria semántica (misma jax_memory que el REPL) — best-effort -----
    # user_id/tenant_id vienen del JWT; project_id del request (None=individual).
    try:
        mem_uid = int(user_id)
        mem_tid = int(tenant_id)
    except (TypeError, ValueError):
        mem_uid = mem_tid = None
    mem_pid = req.project_id
    conv_uuid = None
    if mem_uid is not None:
        conv_uuid = await _get_conv_uuid(mem_uid, mem_tid, mem_pid)
        if conv_uuid:
            _memory.save_message(conv_uuid, "user", req.message)  # fire-and-forget
    # -----------------------------------------------------------------------

    # Respuestas especiales (sin llamada a LLM) — nunca pasan por el parseo
    # de contrato, igual que is_canned=True dentro de _invoke_facet.
    if facet == "hyde":
        resp = "Hyde opera en modo tarea autónoma — usá el modo Comando para ejecutar tareas técnicas."
        await _fire_completed(facet, tenant_id, user_id, resp)
        return ChatResponse(facet=facet, response=resp, timestamp=timestamp, contract_degraded=False)

    # Señal: faceta pensando
    await engine_state.set_facet_status(facet, "thinking", tenant_id, user_id, req.message[:100])

    # Retrieval semántico ANTES del LLM (scope: proyecto + individual del user).
    semantic_context: list[dict] = []
    if mem_uid is not None:
        semantic_context = await _semantic_context(req.message, mem_uid, mem_pid)

    try:
        response_text, is_canned = await _invoke_facet(facet, config, user_id, req.message, semantic_context)
    except httpx.HTTPStatusError as e:
        detail = f"Error HTTP {e.response.status_code} en {facet}: {e.response.text[:200]}"
        await engine_state.set_facet_status(facet, "error", tenant_id, user_id, detail[:100])
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        detail = f"Error en {facet}: {str(e)[:200]}"
        await engine_state.set_facet_status(facet, "error", tenant_id, user_id, str(e)[:100])
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=502, detail=detail)

    # shadow_message_id: calculado acá pero no persistido todavía — lo
    # consume Task 5 (queda sin usar a propósito, ver task-3-report.md).
    shadow_message_id = str(uuid.uuid4())

    # Contrato {claim/analysis/judgment}: solo se intenta parsear cuando
    # hubo una llamada real al LLM (not is_canned) — is_canned es la señal
    # posicional sustituta de "usage is None" (ver nota en _invoke_facet).
    contract = _parse_contract_response(response_text) if not is_canned else None
    if contract is not None:
        display_text, contract_degraded = _build_display_response(contract)
    else:
        display_text, contract_degraded = response_text, False

    _update_history(user_id, req.message, display_text)

    # Registrar uso (best-effort)
    personality = config["personalities"].get(facet, {})
    model_name = personality.get("model_default", facet)
    await record_usage(user_id, tenant_id, facet, model_name, 0, 0, "chat")

    # Guardar la respuesta de la faceta en la MISMA memoria (fire-and-forget).
    if conv_uuid:
        _memory.save_message(conv_uuid, facet, display_text,
                             facet=facet, model=model_name)

    await _fire_completed(facet, tenant_id, user_id, display_text)
    await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)

    return ChatResponse(
        facet=facet, response=display_text, timestamp=timestamp,
        contract_degraded=contract_degraded,
    )


async def _fire_completed(facet: str, tenant_id: str, user_id: str, response_text: str):
    event = JAXEvent(
        event_type="facet_response_completed",
        tenant_id=tenant_id,
        user_id=user_id,
        payload={
            "facet": facet,
            "content": response_text,
            "message_preview": response_text[:100],
        },
    )
    await event_bus.publish(event)
