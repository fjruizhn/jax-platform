import json
import logging
import os
import re
import sys
import tomllib
import unicodedata
import uuid
from pathlib import Path
from collections import OrderedDict
from functools import lru_cache
from tiempo import utc_ahora
from typing import Literal, NamedTuple
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field
import httpx
import aiomysql
from http_client import CuerpoJsonDeUnUso, LiteralJsonCrudo, cabeceras_gemini, get_http_client
from facet_resolver import resolve_facet, FacetUnavailableError
from adjuntos.contrato import (
    SIN_ADJUNTOS,
    AdjuntoRef,
    ImagenNoSoportadaError,
    buscar_adjuntos,
    componer_mensaje,
    exigir_soporte_de_imagen,
    imagenes_de,
    leer_adjuntos,
    mensaje_para_historial,
    metadatos_para_memoria,
)
from adjuntos.errores import AdjuntoRechazado
from adjuntos.limites import cargar_limites
# ModelDispatchConfigError y los dos validadores del contrato de dispatch
# (_max_tokens_field / _max_output_tokens_value) viven en contrato_dispatch.py
# desde 2026-09-14: los usan también los admins que escriben facet_binding,
# con la MISMA regla. Se reexportan acá para no mover a sus lectores.
from contrato_dispatch import (
    ModelDispatchConfigError,
    _MAX_TOKENS_PARAM_NAMES,
    _max_output_tokens_value,
    _max_tokens_field,
)
import model_catalog
from auth.middleware import get_current_user
from auth.models import AuthUser
from kill_switch import exigir_mesa_libre
from config_entorno import ruta_absoluta_requerida, url_requerida
from ejecutor.prioridad import carril_mesa_async
from jax_engine.schemas import JAXEvent
from jax_engine.events import event_bus
from jax_engine.state import engine_state, LAS_MANOS_URL
from credencial_las_manos import encabezados_las_manos
from api.admin.usage import record_usage, validar_ids_de_uso
from db.connection import get_pool
from redaccion import recortar_redactado, texto_de_error
from facet_health import (
    record_facet_health,
    OUTCOME_OK,
    OUTCOME_PROVIDER_ERROR,
    OUTCOME_CONFIG_ERROR,
    OUTCOME_GATE_DENIED,
    OUTCOME_GATE_UNREACHABLE,
    OUTCOME_UNBOUND,
    OUTCOME_UNSUPPORTED_TRANSPORT,
    SOURCE_CHAT,
)

router = APIRouter(prefix="/api")

# El gate de gobernanza se llavea por TRANSPORTE, no por nombre de facet.
# Antes era un frozenset de nombres ({"hipatia","jekyll","thot","ada"}) --
# una segunda fuente de verdad al lado de la que realmente decide el
# dispatch, que es `facet.transport` (ver el ruteo por f.transport más
# abajo en _invoke_facet). Agregar una quinta fila a `facet` con
# transport='http_openai_compat' habría hecho que se despachara SIN pasar
# por ninguna de las dos gobernanzas: fail-open por omisión, exactamente
# el patrón de "dos fuentes de verdad que divergen" que esta feature
# existe para cerrar. Llaveado por transporte, un facet HTTP nuevo nace
# gobernado.
#
# Verificado 2026-08-27 contra la tabla `facet` de jax_memory (prod): los
# únicos transportes http_* son ada/hipatia/jekyll/thot -- exactamente los
# 4 de la lista vieja. El cambio es preservador de comportamiento HOY y
# fail-closed para cualquier facet HTTP futuro.
_GOVERNED_TRANSPORTS = frozenset({"http_gemini", "http_openai_compat"})
_JAX_PLATFORM_CHAT_CALLER = "jax_platform_chat"

# Ruta al config.toml del repo `jax` (repo vecino). Obligatoria y absoluta,
# sin default (frente A, 2026-09-16): sin JAX_CONFIG_PATH el modulo no se
# importa y el servicio no arranca. Historia: primero fue
# `~/jax/config/config.toml` fijo, relativo al $HOME del usuario, y eso hacia
# IMPOSIBLE correr la suite fuera de la maquina de Fernando (en un runner
# limpio 30 tests caian con FileNotFoundError, medido en CI el 2026-09-01);
# despues fue configurable con ese mismo default. La regla vive en
# config_entorno.py (espejo de jax/core/config_entorno.py).
CONFIG_PATH = str(ruta_absoluta_requerida("JAX_CONFIG_PATH"))
JAX_REPO = ruta_absoluta_requerida("JAX_REPO_PATH")

# --- Memoria de conversación heredada (no es el borde B9 de lectura) -------
# Reutiliza la clase MemoryDB del núcleo (~/jax) únicamente para registrar
# conversaciones que todavía consumen los workers de adopción.  Ninguna fila
# devuelta por MemoryDB puede llegar a un prompt: las lecturas model-facing
# pasan por ScopeContext -> MariaDBB9Reader -> PromptMemoryContext.
# Degrada elegante: si no carga o la base cae, el chat sigue SIN memoria.
# Imports separados a propósito: ~/jax es un repo aparte con su propio ciclo
# de reconciliación (ver infra/facetas-bloque-d de ese repo, pendiente de
# mergear a su master). Si ese repo todavía no tiene detect_completeness_intent
# pero SÍ tiene MemoryDB, un solo try/except combinado tumbaba MemoryDB entero
# por un ImportError de la función auxiliar — degradando TODA la memoria
# semántica (y con ella shadow validation, que no encola sin conv_uuid) en
# vez de degradar solo el bypass de completeness. Cada import falla solo.
sys.path.insert(0, str(JAX_REPO))
def _importar_memorydb():
    """Task 3 (2026-09-15, clase b): antes era `except Exception` MUDO -- un
    error dentro de jax.memory.db dejaba MemoryDB = None sin rastro. Ahora
    solo un ImportError es "memoria ausente" (logueado con traceback); un
    SyntaxError u otro bug del repo jax se propaga y el servicio no arranca
    en silencio sin memoria. Corre una vez, al importar el modulo."""
    try:
        from jax.memory.db import MemoryDB as clase
    except ImportError:  # fail-soft: el chat sigue sin memoria ni shadow validation; el fallo queda logueado con traceback
        logging.getLogger(__name__).exception(
            "MemoryDB no importable: chat SIN memoria ni shadow validation")
        return None
    return clase


MemoryDB = _importar_memorydb()
from jax.memory.b9 import (
    AuthorizationDenied, MutationAuthorizationRequest, PromptMemoryContext,
    ScopeContext, ScopeDenied, Visibility,
)
from jax.memory.b9_mariadb import MariaDBB9Reader
from jax.memory.scope_authority import ProjectScopeAuthorityResolver

_memory = None              # instancia única (lazy)
_memory_ready = False
# Fix wave final (2026-09-15): un JAX_DB_PORT mal formado es un error de
# configuracion que no cambia mientras el proceso vive -- se avisa en ERROR
# una sola vez (con el motivo) y los turnos siguientes quedan en DEBUG.
_puerto_invalido_avisado = False
# "tenant_id:user_id:project_id" -> conversation_uuid. OrderedDict como LRU: sin cota,
# cada par (usuario, proyecto) que alguna vez chateó quedaba abierto acá para
# siempre. Al superar MAX_TRACKED_CONVERSATIONS se cierra (end_conversation)
# la conversación menos recientemente activa antes de sacarla del dict —
# nunca se abandona una conversación abierta sin cerrarla en la DB.
_conv_uuids: OrderedDict[str, str] = OrderedDict()
MAX_TRACKED_CONVERSATIONS = 500


async def _ensure_memory() -> bool:
    """Conecta (lazy) a la MISMA jax_memory del REPL. False si falla (no rompe)."""
    global _memory, _memory_ready, _puerto_invalido_avisado
    if MemoryDB is None:
        return False
    if _memory_ready and _memory and _memory.is_connected:
        return True
    if _memory is None:
        _memory = MemoryDB()
    # El guard va AFUERA del try a proposito: el `except` de abajo deja
    # _memory_ready en False, asi que un raise adentro se traduciria en
    # "memoria silenciosamente desactivada" -- que es el mismo fallo mudo que
    # este guard viene a eliminar, con otra cara. Una configuracion ausente
    # tiene que ser ruidosa; una DB caida sigue siendo fail-soft.
    host = os.environ.get("JAX_DB_HOST")
    port = os.environ.get("JAX_DB_PORT")
    if not host or not port:
        raise RuntimeError(
            "JAX_DB_HOST/JAX_DB_PORT no están seteados -- sin default "
            "silencioso a localhost:3306 (esa instancia está muerta, ver "
            "memoria jax-dual-mariadb-instances). Sourceá /etc/jax/.env o "
            "exportalos a mano antes de conectar."
        )
    # Task 3 (2026-09-15, clase b): int(port) vivia dentro del try de abajo,
    # asi que un JAX_DB_PORT mal formado apagaba la memoria SIN log. Se
    # valida aparte, con el mismo costo que antes (un int() por llamada).
    # Fix wave final: el ERROR sale UNA vez por proceso (antes, uno por turno).
    try:
        puerto = int(port)
    except ValueError as e:  # fail-soft: puerto mal formado = turno sin memoria; ERROR una vez por proceso con el valor y el motivo, DEBUG en cada turno siguiente
        if not _puerto_invalido_avisado:
            _puerto_invalido_avisado = True
            logger.error(
                "JAX_DB_PORT=%r no es un puerto (%s): memoria del chat DESACTIVADA "
                "en este proceso hasta corregir la config y reiniciar", port, e)
        else:
            logger.debug("JAX_DB_PORT=%r no es un puerto: turno sin memoria (ERROR ya logueado)", port)
        _memory_ready = False
        return False
    try:
        _memory_ready = await _memory.connect(
            host=host,
            user=os.getenv("JAX_DB_USER", ""),
            password=os.getenv("JAX_DB_PASSWORD", ""),
            database=os.getenv("JAX_DB_NAME", "jax_memory"),
            port=puerto,
        )
    except Exception:  # fail-soft: DB de memoria caída = turno sin memoria; MemoryDB.connect ya loguea la causa
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
    except Exception:  # fail-soft: best-effort documentado en el comentario de arriba: la conversacion queda abierta en DB pero deja de trackearse aca, no hay falso exito
        pass  # best-effort: queda abierta en la DB, pero ya no se trackea acá


async def _get_conv_uuid(user_id: int, tenant_id, project_id) -> str | None:
    """Conversación por (usuario, proyecto). Lazy. None si la memoria está caída.
    project_id NOT NULL -> memoria de proyecto (compartida); NULL -> individual."""
    if not await _ensure_memory():
        return None
    # A user/project identifier is not globally authoritative: the tenant is
    # part of the cache namespace even when two tenants happen to use equal IDs.
    key = f"{tenant_id}:{user_id}:{project_id}"
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


class B9MemoryUnavailable(RuntimeError):
    """The B9 read boundary could not obtain a trustworthy memory result."""


class _B9MappingAcquire:
    """Adapt the platform pool's plain-cursor acquire contract for B9 reads."""
    def __init__(self, acquire_context):
        self._acquire_context = acquire_context

    async def __aenter__(self):
        self._connection = await self._acquire_context.__aenter__()
        return _B9MappingConnection(self._connection)

    async def __aexit__(self, exc_type, exc, traceback):
        return await self._acquire_context.__aexit__(exc_type, exc, traceback)


class _B9MappingConnection:
    """Connection view which asks aiomysql for mapping rows on every cursor.

    ``db.connection.get_pool`` deliberately uses aiomysql's default cursor for
    the rest of the platform.  B9 readers consume named fields, so adapting at
    this narrow integration boundary avoids treating tuple positions as an
    authorization-sensitive schema contract.
    """
    def __init__(self, connection):
        self._connection = connection

    def cursor(self, *args, **kwargs):
        if args or kwargs:
            raise TypeError("B9 mapping adapter does not accept caller cursor overrides")
        return self._connection.cursor(aiomysql.DictCursor)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _B9MappingPool:
    """Minimal pool adapter required by ``MariaDBB9Reader``."""
    def __init__(self, pool):
        self._pool = pool

    def acquire(self):
        return _B9MappingAcquire(self._pool.acquire())


async def _prompt_memory_context(scope: ScopeContext) -> PromptMemoryContext:
    """Read B9 memory exclusively through its envelope boundary.

    A database/schema outage is explicit to the request.  It must never be
    represented as a successful empty context: that would make a platform
    cursor-contract error indistinguishable from genuinely absent memory.
    """
    try:
        pool = _B9MappingPool(await get_pool())
        # The reader owns a second, just-in-time DB-backed authorization
        # decision.  ``scope`` carries request identity and target only; its
        # earlier chat-boundary resolution is never treated as a bearer grant.
        request = MutationAuthorizationRequest(
            scope, "RETRIEVE",
            Visibility.PROJECT_SHARED if scope.project_id else Visibility.TENANT_SHARED,
        )
        reader = MariaDBB9Reader(pool, ProjectScopeAuthorityResolver(pool))
        return PromptMemoryContext(await reader.retrieve_authorized(request, limit=20))
    except Exception as exc:
        logger.error("B9 memory retrieval failed", exc_info=True)
        raise B9MemoryUnavailable("B9 memory retrieval failed") from exc


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
        except Exception:  # fail-soft: shutdown flush best-effort documentado en el docstring de la funcion ('nunca lanza'); el conteo n de exitos reales es lo que se reporta
            pass
    _conv_uuids.clear()
    try:
        await _memory.close()
    except Exception:  # fail-soft: cierre de memoria en shutdown, best-effort documentado; el proceso ya esta terminando
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
    """Quita tildes para matching robusto (trae=traé, adios=adiós).
    Solo afecta la comparacion interna; el mensaje viaja intacto."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Easter egg — ESPEJO de jax/core/router.py (2026-09-23). Misma razón que las
# keywords de abajo: no se puede importar jax.core.router. Lo vigila la misma
# familia `router_keywords` de jax/scripts/check_mirror_sync.py: patrón, texto
# y función, idénticos a los del REPL. Un cambio acá se hace también allá.
# i18n: EXCEPCIÓN DECLARADA (decisión de Fernando, 2026-09-23). El texto es el
# mensaje personal de Jairo Urbina del REPL, no texto de la interfaz: se
# muestra siempre así, en español, en las dos copias.
EASTER_EGG_PATRON = re.compile(r"(?<![a-z0-9])ide[^\S\r\n]*1990(?![0-9])")
EASTER_EGG_TEXT = (
    "Hola Fernando Ruiz, mejor conocido por sus amigos tecnologicos como "
    "'El Jate'. Quiero que sepas que sigo a tu lado, viviendo ahora en "
    "hall9000, como el poderoso dios de la tecnologia llamado JAX. Saludos "
    "a nuestro clan de geeks, MINIX, Xavas... siempre estare aqui "
    "acompanandote y ayudandote a ser mejor persona."
)


def es_easter_egg(texto: str) -> bool:
    """IDE1990 como palabra propia, sin distinguir mayúsculas ni tildes, con
    o sin espacios (en la misma línea) entre IDE y 1990. Antes (2026-09-23) era una subcadena
    tras quitar TODOS los espacios, y "el cliente pide 1990 unidades" o
    "provide 1990" disparaban: en la Mesa, multiusuario, eso es una
    respuesta perdida que le muestra a otro el mensaje de Fernando."""
    return EASTER_EGG_PATRON.search(_sin_tildes(texto.lower())) is not None


# Keywords por faceta — ESPEJO de jax/core/router.py (A-22, 2026-09-16).
# Copia a propósito: importar jax.core.router arrastra contrato_dispatch ->
# facet_resolver y rompe CI (verificado por terceros). La vigila
# jax/scripts/check_mirror_sync.py, familia `router_keywords`: un cambio acá
# se hace también allá, en el mismo paso.
# Hyde NO es destino del auto-routing: es ejecutor, no conversador.
KIMI_KW = frozenset((
    "codigo", "programar", "programa", "script", "funcion", "clase", "metodo",
    "modulo", "libreria", "api", "endpoint", "backend", "frontend",
    "implementar", "implementa", "construir", "refactor", "refactorizar",
    "refactoriza", "debug", "depurar", "bug", "traceback", "excepcion",
    "compilar", "test", "tests", "pytest", "variable", "bucle", "array",
    "regex", "fastapi", "react", "typescript", "javascript", "python", "sql",
    "docker", "nginx", "commit", "branch", "merge",
))
KIMI_STRONG = frozenset((
    "refactor", "refactoriza", "implementar", "debug", "depurar", "pytest",
    "fastapi", "docker", "nginx", "endpoint",
))

HIPATIA_KW = frozenset((
    "busca", "buscar", "investiga", "investigar", "verifica", "verificar",
    "fuentes", "fuente", "citas", "referencias", "noticias", "noticia",
    "actualidad", "reciente", "ultima", "ultimo", "vigente", "precio",
    "precios", "cotizacion", "mercado", "ley", "regulacion", "normativa",
    "paper", "papers", "estudio", "informe", "estadistica", "lanzamiento",
    "version actual", "quien es",
))
HIPATIA_STRONG = frozenset((
    "busca", "buscar", "investiga", "investigar", "noticias", "fuentes",
    "version actual",
))

JEKYLL_KW = frozenset((
    "poesia", "poema", "cuento", "novela", "literatura", "ensayo", "arte",
    "pintura", "musica", "filosofia", "etica", "estetica", "humanidades",
    "barroco", "renacimiento", "romanticismo", "mito", "mitologia", "simbolo",
    "simbolismo", "metafora", "narrativa", "personaje", "estilo",
    "interpretacion", "sentido", "significado", "reflexion", "reflexiona",
    "contempla", "humanista", "cultura", "historia del arte", "historia cultural",
))
JEKYLL_STRONG = frozenset((
    "poema", "poesia", "filosofia", "literatura", "mitologia",
    "historia del arte", "barroco",
))

THOT_KW = frozenset((
    "audita", "auditar", "auditoria", "critica", "criticar", "criticamente",
    "cuestiona", "cuestionar", "adversarial", "abogado del diablo", "riesgo",
    "riesgos", "falla", "fallas", "debilidad", "debilidades", "vulnerabilidad",
    "vulnerabilidades", "amenaza", "amenazas", "threat model",
    "modelo de amenazas", "ataque", "donde se rompe", "punto ciego",
    "supuesto", "supuestos", "contraargumento", "refuta", "refutar",
    "no-go", "revisa criticamente",
))
THOT_STRONG = frozenset((
    "audita", "auditar", "auditoria", "vulnerabilidad", "vulnerabilidades",
    "threat model", "adversarial", "refuta",
))

ADA_KW = frozenset((
    "formaliza", "formalizar", "formalizacion", "modelo formal", "pseudocodigo",
    "logica", "demuestra", "demostrar", "demostracion", "prueba formal",
    "teorema", "lema", "corolario", "axioma", "proposicion", "invariante",
    "invariantes", "precondicion", "postcondicion", "maquina de estados",
    "automata", "complejidad", "big o", "o(n)", "estructura de datos",
    "grafo", "arbol", "matriz", "vector", "ecuacion", "optimizacion",
    "funcion objetivo", "matematica", "calculo", "algebra", "probabilidad",
    "determinista", "induccion", "algoritmo",
))
ADA_STRONG = frozenset((
    "formaliza", "formalizar", "demuestra", "demostrar", "teorema",
    "invariante", "invariantes", "precondicion", "postcondicion",
    "complejidad", "maquina de estados",
))

_KW_SETS = {
    "kimi":    (KIMI_KW,    KIMI_STRONG),
    "hipatia": (HIPATIA_KW, HIPATIA_STRONG),
    "jekyll":  (JEKYLL_KW,  JEKYLL_STRONG),
    "thot":    (THOT_KW,    THOT_STRONG),
    "ada":     (ADA_KW,     ADA_STRONG),
}
_TIEBREAK = ("hipatia", "thot", "ada", "kimi", "jekyll")


def _auto_route(message: str) -> str:
    """Scoring multi-keyword con umbral. Sin clasificador LLM (fase 1).

    Regla:
    - score[f] = n° de keywords de f que matchean.
    - top = faceta con mayor score (desempate: _TIEBREAK).
    - score >= 2 → enrutar a top.
    - score == 1 y keyword STRONG → enrutar a top.
    - else → jax_local (fallback; en fase 2 se evaluará clasificador LLM).
    """
    text = _sin_tildes(message.lower().strip())
    scores: dict[str, int] = {}
    hit_strong: dict[str, bool] = {}

    for faceta, (kws, strong) in _KW_SETS.items():
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
        for f in _TIEBREAK:
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
    # Frente D (2026-09-16): extra='forbid'. Hasta 26c9cd5 el frontend mandaba
    # image_base64/file_context y pydantic los DESCARTABA en silencio: el
    # usuario adjuntaba y el modelo nunca lo veía. Un campo desconocido es 422.
    model_config = ConfigDict(extra="forbid")
    message: str
    facet: str | None = None
    project_id: int | None = None   # None = memoria individual; set = memoria de proyecto
    # Origen declarado por quien LLAMA -- vocabulario CERRADO (2026-09-03):
    # un valor fuera de {web, probe, test} lo rechaza pydantic con 422 en
    # el borde, fail-closed a propósito. Ausente (None) se persiste como
    # 'unattributed' -- nunca 'web': la ausencia de declaración no es
    # evidencia de uso orgánico (ver shadow_messages.origin en
    # db/migrations.py). Ningún llamador puede hacerse pasar por tráfico
    # real con solo omitir el campo.
    origin: Literal["web", "probe", "test"] | None = None
    # RD3 (2026-09-17): solo ids de /api/chat/upload. El base64 o el texto
    # en línea (contrato del 2026-09-16) es 422 por extra='forbid'.
    adjuntos: list[AdjuntoRef] = Field(default_factory=list)


class AvisoDeChat(BaseModel):
    """Respuesta enlatada (sin LLM) como CÓDIGO + datos (A-53, 2026-09-16). El
    texto visible lo arma el frontend con i18n (t.avisosChat[code]).
    `como_texto()` es la marca sin idioma que va al historial del hilo y a la
    memoria: registra QUÉ pasó sin fijar un idioma en la base."""
    code: Literal["faceta_sin_binding", "faceta_no_autorizada", "transporte_no_soportado",
                  "identidad_del_modelo", "hyde_usa_modo_comando"]
    params: dict[str, str] = {}

    def como_texto(self) -> str:
        datos = " ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"[{self.code}{' ' + datos if datos else ''}]"


class ChatResponse(BaseModel):
    facet: str
    response: str
    timestamp: str
    # True cuando _parse_contract_response no pudo parsear el JSON de
    # contrato de una respuesta real de LLM (degradación auditada) — False
    # para respuestas enlatadas (aviso) y para el intercept de hyde,
    # que nunca pasan por el parseo de contrato.
    contract_degraded: bool = False
    # A-53: presente en las respuestas enlatadas; el frontend muestra t.avisosChat[aviso.code].
    aviso: AvisoDeChat | None = None


@lru_cache(maxsize=1)
def _load_config() -> dict:
    # config.toml no tiene ningún escritor en runtime (el modelo activo vive
    # en facet_binding desde Bloque C, resuelto vía resolve_facet() — ver
    # _invoke_facet) — seguro cachear por el ciclo de vida del proceso en
    # vez de releerlo en cada request de chat.
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


class UsageInfo(NamedTuple):
    provider_id: str
    model: str
    tokens_in: int
    tokens_out: int


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
        # predicate se escribe en shadow_claim_verdicts.predicate VARCHAR(50)
        # (backend/db/migrations.py). Un predicate más largo hacía que el
        # INSERT lanzara "Data too long" a mitad del loop de claims,
        # abortando el resto de claims/vocab hits de ESE mensaje — pérdida
        # sesgada hacia los modelos peor comportados (finding 2 de la
        # revisión final). El modelo no está siguiendo el shape
        # {predicate, args} de dos campos: es, en espíritu, la misma
        # violación de contrato que los otros casos mal formados de este
        # loop, así que se degrada acá con el mismo helper.
        if len(item["predicate"]) > 50:
            return _degraded(raw_text, f"predicate excede 50 caracteres: {item['predicate'][:50]!r}...")
        parsed = {"predicate": item["predicate"], "args": item["args"]}
        # SP3: evidence_pointer es lo ÚNICO que el modelo puede aportar a la
        # acreditación (spec §5.1). Se conserva tal cual, sin validar tipo:
        # un puntero raro cae en POINTER_MISMATCH o FACT_NOT_IN_SNAPSHOT en
        # shadow validation (repo jax, 2026-09-03; antes un solo estado
        # PROVENANCE_MISMATCH), no un contrato roto. authority se conserva
        # SOLO para dejar constancia en
        # `detail` de que el modelo intentó declararla -- nunca entra en la
        # columna authority (spec §9.1).
        for passthrough in ("evidence_pointer", "authority"):
            if passthrough in item:
                parsed[passthrough] = item[passthrough]
        parsed_claims.append(parsed)

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


# _invoke_facet devuelve tuple[str | AvisoDeChat, UsageInfo | None]; "usage is None"
# distingue respuesta enlatada (is_canned, derivado en el call site) de
# llamada real al LLM, en vez de comparar response_text contra los strings
# enlatados conocidos — evita que un futuro edit de esos strings rompa la
# señal en silencio. _CONTRACT_PROMPT_SUFFIX está conectado al system_prompt
# real dentro de _invoke_facet (ver esa función).
# El vocabulario de predicados es CERRADO y vive en el repo `jax`
# (policy/vocabulary/predicates.yaml). El prompt lo GENERA desde ahí con la
# misma loaders.load_predicates() que usa shadow_validation.py para validar
# los claims que vuelven: una sola fuente, no dos listas que se van
# separando. Si el vocabulario gana un predicado, el prompt lo sigue solo
# (tests/test_chat_contract_prompt.py se pone rojo si divergen).
#
# Ruta configurable por JAX_REPO_PATH, igual que shadow_validation.py — la
# misma JAX_REPO del import de MemoryDB de más arriba. Y a
# diferencia de aquel, este NO degrada: si el vocabulario no carga, el
# proceso no arranca. Un prompt sin predicados es invisible desde afuera
# —el chat responde igual, el contrato parsea igual, y el canal de claims
# queda mudo— y es exactamente el estado que produjo 22 de 22 mensajes sin
# un solo claim entre el 2026-08-18 y el 2026-09-01. Tiene que ser un
# fallo ruidoso al arrancar, no uno silencioso en producción.
_GOVERNANCE_DIR = os.path.join(str(JAX_REPO), "policy", "governance")
if _GOVERNANCE_DIR not in sys.path:
    sys.path.insert(0, _GOVERNANCE_DIR)
import loaders as governance_loaders  # noqa: E402


_CONTRACT_SUFFIX_TEMPLATE = """

FORMATO DE RESPUESTA OBLIGATORIO — respondé ÚNICAMENTE con un objeto JSON, sin texto antes ni después, sin fences de markdown:

{"claim": [{"predicate": "NOMBRE", "args": {"clave": "valor"}, "evidence_pointer": "/capabilities/0"}], "analysis": "tu razonamiento en texto libre", "judgment": "tu conclusión, o null si no aplica"}

- "claim": toda afirmación verificable sobre el estado del sistema que hagas en "analysis" o "judgment" va también acá como claim. Usá SOLO los predicados de esta lista, con ese nombre exacto y esos args — el vocabulario es cerrado: un nombre inventado se descarta entero.

__PREDICADOS__

  Cada claim es {"predicate": "...", "args": {...}, "evidence_pointer": "/<lista>/<n>"}. El evidence_pointer es la línea de HECHOS VERIFICADOS que respalda el claim; si no hay una línea que lo respalde, no lo emitas como claim. Poné [] únicamente si tu respuesta no afirma nada sobre el estado del sistema.
- "analysis": tu análisis en texto libre. Obligatorio, aunque sea corto.
- "judgment": tu conclusión o recomendación, o null si no aplica.

No expliques el formato, solo respondé el JSON."""


def _render_contract_suffix(predicates: dict) -> str:
    """Arma el sufijo del prompt desde el vocabulario cerrado. Puro: recibe
    el dict {nombre: PredicateSpec} de loaders.load_predicates()."""
    lineas = "\n".join(
        f'  - {spec.name}(args: {", ".join(spec.args)}) — se verifica contra: {spec.source_of_truth}'
        for spec in predicates.values()
    )
    return _CONTRACT_SUFFIX_TEMPLATE.replace("__PREDICADOS__", lineas)


_CONTRACT_PROMPT_SUFFIX = _render_contract_suffix(governance_loaders.load_predicates())


# --- Grounding por snapshot (REFORMAS Fase 2 SP3, spec §5) -------------------
import grounding as governance_grounding  # noqa: E402  (mismo sys.path que loaders)
from governance_context import validation_context as _governance_context  # noqa: E402


async def _build_snapshot_or_raise() -> "governance_grounding.Snapshot":
    """Separado de _build_grounding para poder parchearlo en tests."""
    ctx, _, _ = await _governance_context()
    return governance_grounding.build_snapshot(ctx)


async def _build_grounding() -> "governance_grounding.Snapshot | governance_grounding.SnapshotError":
    """Nunca lanza. build_snapshot() sí lanza (P10) -- acá se captura, se
    LOGUEA con traceback, y se convierte en la marca SnapshotError que
    viaja al validador y termina como grounding_snapshot_sha256='ERROR'
    (spec §5.4). El turno de chat responde igual: el grounding es medición
    y no puede tumbar un chat, mismo criterio que el encolado de shadow
    validation más abajo. Lo que NO se hace: devolver un snapshot vacío,
    que sería indistinguible de "no hay capabilities"."""
    try:
        return await _build_snapshot_or_raise()
    except Exception as e:  # fail-soft: se convierte en SnapshotError explícito (sha='ERROR' en el validador), nunca en snapshot vacío; logueado con traceback
        logger.exception("no se pudo construir el snapshot de grounding")
        return governance_grounding.SnapshotError(f"{type(e).__name__}: {e}")


def _build_messages(system_prompt: str, history: list[dict], message: str,
                    imagenes: tuple = (), *, forma: Literal["openai", "ollama"] = "openai") -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(history)
    ultimo: dict = {"role": "user", "content": message}
    # La imagen va como LiteralJsonCrudo: sus tramos de base64 se escriben en
    # el cuerpo sin pasar por json.dumps ni juntarse en un str (RD3).
    if imagenes and forma == "openai":
        ultimo["content"] = [{"type": "text", "text": message}] + [
            {"type": "image_url",
             "image_url": {"url": LiteralJsonCrudo(f"data:{i.mime};base64,", i.tramos_base64)}}
            for i in imagenes]
    elif imagenes:
        ultimo["images"] = [LiteralJsonCrudo("", i.tramos_base64) for i in imagenes]
    msgs.append(ultimo)
    return msgs


def _url_de_ollama() -> str:
    """E-21 (2026-09-16): el host de Ollama sale de JAX_OLLAMA_URL (/etc/jax/.env),
    la misma variable que usan Jacobs, el REPL y la memoria de jax. Antes se leía
    de personalities.jax_local.api_url del config.toml de jax, que ya no la trae.
    Sin la variable: error explícito, no un default a localhost.

    Revisión final del frente E: con la MISMA regla que jax (url_requerida:
    http(s), con host, sin path, query ni fragmento) y validada también al
    arrancar, en el lifespan de main.py, no recién en el primer turno."""
    return url_requerida("JAX_OLLAMA_URL")


def _raiz_del_carril() -> Path:
    """SP3 del Ejecutor (2026-09-17): el directorio de los locks de prioridad. La MISMA
    variable que lee el proxy del Ejecutor (jax/ejecutor/proxy_carril.py), así los dos
    miran el mismo `mesa.lock`. Sin ella, EntornoInvalido: una Mesa que no toma su carril
    deja que el Ejecutor se le cuele delante en silencio. Se valida también al arrancar."""
    return ruta_absoluta_requerida("JAX_PROXY_CARRIL_RAIZ")


def _argumentos_de_cuerpo(cuerpo: dict, imagenes: tuple, cabeceras: dict[str, str] | None = None) -> dict:
    """kwargs de cuerpo y cabeceras para client.post del proveedor.

    Con imagen el cuerpo pesa lo que la imagen en base64 (hasta ~14 MB) y va
    como CuerpoJsonDeUnUso: con `json=` httpx lo deja colgado de un ciclo que
    solo junta el GC cíclico y la memoria crecía 13,4 MB por chat (R16,
    2026-09-17). Sin imagen el cuerpo es de KB (historial acotado por
    MAX_TURNS, adjuntos de texto por max_chars) y sigue con `json=`: medido,
    el chat con un adjunto de texto del mismo tamaño de pedido no crece."""
    if not imagenes:
        return {"json": cuerpo} if cabeceras is None else {"json": cuerpo, "headers": cabeceras}
    de_un_uso = CuerpoJsonDeUnUso(cuerpo)
    return {"content": de_un_uso, "headers": {**(cabeceras or {}), **de_un_uso.cabeceras}}


async def _call_ollama(system_prompt: str, history: list[dict], message: str, config: dict, model: str,
                       *, imagenes: tuple = ()) -> tuple[str, int, int]:
    url = f"{_url_de_ollama()}/api/chat"
    raiz = _raiz_del_carril()
    messages = _build_messages(system_prompt, history, message, imagenes, forma="ollama")
    client = await get_http_client()
    # Carril de la Mesa (§3.4 bis del spec de Fase 2 de jax): mientras dura la llamada, el
    # proxy del Ejecutor no manda nada a Ollama. Async: el flock va en un hilo y no congela
    # el loop. Cubre también la sonda de facet_canary (entra por _invoke_facet). Serializa
    # las llamadas de Mesa entre sí: gratis con OLLAMA_NUM_PARALLEL=1, que es lo medido.
    async with carril_mesa_async(raiz):
        r = await client.post(
            url,
            **_argumentos_de_cuerpo({"model": model, "messages": messages, "stream": False, "keep_alive": -1},
                                    imagenes),
            timeout=180.0,
        )
        r.raise_for_status()
        data = r.json()
    return data["message"]["content"], data.get("prompt_eval_count", 0), data.get("eval_count", 0)


async def _call_openai_compat(
    base_url: str, api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
    max_tokens_param: str | None, max_output_tokens: int | None,
    on_response=None, *, imagenes: tuple = (),
) -> tuple[str, int, int]:
    # Ninguno de los dos tiene default: un llamador que los olvide falla al
    # llamar (TypeError), no manda un request mudo con un nombre ni un valor
    # asumidos. Se resuelven ANTES de armar nada — un modelo sin sembrar no
    # gasta una llamada saliente para descubrir lo que el catálogo debería decir.
    field = _max_tokens_field(model, max_tokens_param)
    limit = _max_output_tokens_value(model, max_output_tokens)
    messages = _build_messages(system_prompt, history, message, imagenes)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    client = await get_http_client()
    r = await client.post(
        f"{base_url}/chat/completions",
        **_argumentos_de_cuerpo({"model": model, "messages": messages, field: limit}, imagenes, headers),
        timeout=120.0,
    )
    r.raise_for_status()
    data = r.json()
    if on_response:
        await on_response(data)
    usage = data.get("usage") or {}
    return data["choices"][0]["message"]["content"], usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


async def _call_gemini(
    api_key: str, model: str,
    system_prompt: str, history: list[dict], message: str,
    on_response=None, *, imagenes: tuple = (),
) -> tuple[str, int, int]:
    # T6-2: la key va en la cabecera, nunca en la URL.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": h["content"]}]})
    partes: list[dict] = [{"text": message}]
    partes += [{"inline_data": {"mime_type": i.mime, "data": LiteralJsonCrudo("", i.tramos_base64)}}
               for i in imagenes]
    contents.append({"role": "user", "parts": partes})
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [{"googleSearch": {}}],
    }
    client = await get_http_client()
    r = await client.post(url, **_argumentos_de_cuerpo(body, imagenes, cabeceras_gemini(api_key)), timeout=120.0)
    r.raise_for_status()
    data = r.json()
    if on_response:
        await on_response(data)
    usage = data.get("usageMetadata") or {}
    return (
        data["candidates"][0]["content"]["parts"][0]["text"],
        usage.get("promptTokenCount", 0),
        usage.get("candidatesTokenCount", 0),
    )


_MODEL_IDENTITY_WORDS = ("modelo", "model")
_MODEL_IDENTITY_SELF_REF = (
    "sos", "eres", "estas", "corres", "corriendo", "ejecuta", "ejecutas",
    "usas", "tenes", "tienes", "corre", "corriste",
    "are", "running", "use", "using",
)


def _is_model_identity_question(message: str) -> bool:
    """Detecta preguntas sobre que modelo ejecuta a la faceta ('que modelo
    sos', 'con que modelo estas corriendo'). Estas se resuelven con el dato
    real de resolve_facet() (facet_binding), nunca con la respuesta del
    LLM: el modelo confabula su propia identidad incluso cuando el dato
    correcto ya esta en su contexto (REGLA DE EVIDENCIA — ver config.toml)."""
    text = _sin_tildes(message.lower().strip())
    has_model_word = any(re.search(rf"\b{kw}\b", text) for kw in _MODEL_IDENTITY_WORDS)
    if not has_model_word:
        return False
    return any(re.search(rf"\b{v}\b", text) for v in _MODEL_IDENTITY_SELF_REF)


async def _record_resolved_version_from_response(facet_key: str, data: dict) -> None:
    """Bloque D (D1.2) — best-effort real: la excepcion se atrapa aca, nunca
    sube a _invoke_facet. resolved_version viene del campo que cada API usa
    para confirmar lo que de verdad ejecuto: OpenAI-compatible => 'model',
    Gemini => 'modelVersion' (ninguno de los dos es el alias que se pidio)."""
    resolved = data.get("model") or data.get("modelVersion")
    if not resolved:
        return
    try:
        await model_catalog.record_resolved_version(facet_key, resolved)
    except Exception as e:  # fail-soft: telemetría de versión resuelta; la respuesta del facet ya existe y no depende de este registro
        logger.warning(f"resolved_version capture failed facet={facet_key} reason={type(e).__name__}")


async def _invoke_facet_dispatch(
    facet: str, config: dict, user_id: str, message: str,
    semantic_context: list[dict] | None = None,
    memory_context: PromptMemoryContext | None = None,
    grounding: "governance_grounding.Snapshot | governance_grounding.SnapshotError | None" = None,
    imagenes: tuple = (),
    texto_del_usuario: str | None = None,
) -> tuple["str | AvisoDeChat", UsageInfo | None, str]:
    """`message` es lo que ve el modelo (con adjuntos, frente D).
    `texto_del_usuario` es SOLO lo que escribió el usuario: la heurística de
    identidad mira eso y nunca el contenido de un adjunto (un documento que
    dice "we use a regression model" se lee, no recibe el aviso enlatado).
    None = no hay composición (sonda, tests): el mensaje ES el del usuario."""
    history = _conversations.get(user_id, [])
    if semantic_context:
        # Contexto de sesiones pasadas SOLO para este turno (no entra al hilo RAM).
        history = semantic_context + history
    personality = config["personalities"].get(facet, config["personalities"]["jax_local"])
    system_prompt = personality.get("system_prompt", "Sos JAX.") + _CONTRACT_PROMPT_SUFFIX
    # SP3: el bloque de hechos va DESPUÉS del sufijo de contrato, sin el
    # hash (spec §5.1). Con SnapshotError no se anexa nada -- el modelo no
    # tiene con qué citar y el validador lo sabe por la marca. Con None
    # (la sonda de facet_canary) tampoco: la sonda no corre shadow validation.
    if isinstance(grounding, governance_grounding.Snapshot):
        system_prompt += "\n\n" + governance_grounding.render(grounding)
    # This is the sole model-facing memory renderer in Web Chat.  It accepts
    # envelopes, retains their trust classification, and never sees database
    # rows or caller-provided classifications.
    if memory_context is not None:
        rendered_memory = memory_context.render()
        if rendered_memory:
            system_prompt += "\n\n" + rendered_memory

    # Bloque C: resolve_facet() reemplaza _resolve_active_model +
    # resolve_credential sueltos — mismo resolver que usa
    # Jacobs (facet_resolver.py), garantiza que Mesa web y Jacobs resuelvan
    # la MISMA faceta al MISMO modelo. FAIL-CLOSED: sin binding activo,
    # mensaje de degradacion explicito, nunca una llamada con modelo vacio.
    try:
        f = await resolve_facet(facet)
    except FacetUnavailableError:
        return AvisoDeChat(code="faceta_sin_binding", params={"facet": facet}), None, OUTCOME_UNBOUND

    # El gate va DESPUÉS de resolve_facet() a propósito: es ahí donde
    # `f.transport` existe, y el transporte es lo mismo que decide el
    # dispatch real unas líneas más abajo -- una sola fuente de verdad.
    if f.transport in _GOVERNED_TRANSPORTS:
        allowed = False
        gate_outcome = OUTCOME_GATE_DENIED      # las_manos respondió "no"
        try:
            hc = await get_http_client()
            resp = await hc.post(
                f"{LAS_MANOS_URL}/motor/authorize-facet",
                json={"caller": _JAX_PLATFORM_CHAT_CALLER, "facet": facet},
                timeout=5.0,
                headers=encabezados_las_manos(),
            )
            resp.raise_for_status()
            body = resp.json()
            allowed = body.get("allowed", False)
            if not allowed:
                # Denegación limpia: las_manos respondió, la decisión es
                # "no". Logueamos el 'reason' que trae la respuesta -- es
                # precisamente para esto que existe ese campo.
                logger.warning(
                    f"authorize-facet denied facet={facet} caller={_JAX_PLATFORM_CHAT_CALLER} "
                    f"reason={body.get('reason')!r}"
                )
        except Exception as e:  # fail-soft: fail-CLOSED -- cualquier error deniega (allowed=False, OUTCOME_GATE_UNREACHABLE); no se sigue sin autorización
            # Fail-closed (P10): cualquier falla -- timeout, conexión
            # rechazada, respuesta inesperada -- deniega. Nunca "no pude
            # verificar, sigo igual". Logueado por separado del caso de
            # arriba: "las_manos no respondió" no es lo mismo que "las_manos
            # respondió que no", y un operador necesita distinguirlos.
            allowed = False
            gate_outcome = OUTCOME_GATE_UNREACHABLE   # las_manos no respondió
            logger.warning(
                f"authorize-facet unreachable facet={facet} caller={_JAX_PLATFORM_CHAT_CALLER} "
                f"error={type(e).__name__}: {e} -- denegado fail-closed"
            )
        if not allowed:
            return AvisoDeChat(code="faceta_no_autorizada", params={"facet": facet}), None, gate_outcome

    # Frente D: re-chequeo con el binding que se va a usar de verdad. El
    # endpoint ya validó, pero un rebind entre ambos momentos no puede
    # terminar con una imagen mandada a un modelo que no la ve (Ollama la
    # ignora y el modelo inventa). Registra provider_error en _invoke_facet;
    # el endpoint lo devuelve como 422.
    exigir_soporte_de_imagen(f, facet, imagenes)

    if _is_model_identity_question(message if texto_del_usuario is None else texto_del_usuario):
        return AvisoDeChat(code="identidad_del_modelo",
                           params={"facet": facet, "model": f.model, "provider": f.provider_id}), None, OUTCOME_OK

    if f.transport == "ollama":
        # Bug 3: jax_local no sabia con que modelo corre y confabulaba su
        # identidad. Le damos el dato real como contexto informativo.
        ident = (
            f"\n\nDato tecnico (para tu propia referencia, no lo repitas sin que "
            f"te pregunten): el modelo que te ejecuta en este momento es "
            f"'{f.model}', via Ollama local en hall9000."
        ) if facet == "jax_local" else ""
        text, tin, tout = await _call_ollama(system_prompt + ident, history, message, config, f.model,
                                             imagenes=imagenes)
        return text, UsageInfo(f.provider_id, f.model, tin, tout), OUTCOME_OK

    async def _on_response(data: dict) -> None:
        await _record_resolved_version_from_response(facet, data)

    if f.transport == "http_gemini":
        text, tin, tout = await _call_gemini(f.credential, f.model, system_prompt, history, message,
                                             on_response=_on_response, imagenes=imagenes)
        return text, UsageInfo(f.provider_id, f.model, tin, tout), OUTCOME_OK

    if f.transport == "http_openai_compat":
        # f.max_tokens_param y f.max_output_tokens vienen de la MISMA fila de
        # `model` via el JOIN de facet_resolver._query_facet — el mismo lugar del
        # que ya salen f.model/f.base_url, no una segunda fuente de verdad. NULL
        # en cualquiera de los dos sube como ModelDispatchConfigError desde
        # _call_openai_compat: no se atrapa acá a propósito (el handler del
        # endpoint lo convierte en 502 + facet en estado 'error'), para que un
        # modelo sin sembrar sea visible.
        text, tin, tout = await _call_openai_compat(
            f.base_url, f.credential, f.model, system_prompt, history, message,
            f.max_tokens_param, f.max_output_tokens, on_response=_on_response,
            imagenes=imagenes,
        )
        return text, UsageInfo(f.provider_id, f.model, tin, tout), OUTCOME_OK

    return AvisoDeChat(code="transporte_no_soportado",
                       params={"facet": facet, "transport": f.transport}), None, OUTCOME_UNSUPPORTED_TRANSPORT


async def _invoke_facet(
    facet: str, config: dict, user_id: str, message: str,
    semantic_context: list[dict] | None = None,
    memory_context: PromptMemoryContext | None = None,
    *, source: str = SOURCE_CHAT,
    grounding: "governance_grounding.Snapshot | governance_grounding.SnapshotError | None" = None,
    imagenes: tuple = (),
    texto_del_usuario: str | None = None,
) -> tuple["str | AvisoDeChat", UsageInfo | None]:
    """Envoltorio instrumentado. La particion existe para que el
    `outcome` sea un literal tipado en cada punto de retorno de
    _invoke_facet_dispatch, en vez de deducirse del texto de la respuesta.

    La firma publica es la de antes mas `source` keyword-only con default:
    los llamadores y tests existentes no cambian.

    Instrumentado ACA y no en un envoltorio que el llamador tenga que
    acordarse de usar: asi el chat real y la sonda quedan cubiertos por
    construccion, sin una segunda ruta que pueda divergir."""
    try:
        texto, usage, outcome = await _invoke_facet_dispatch(
            facet, config, user_id, message, semantic_context, memory_context,
            grounding=grounding, imagenes=imagenes,
            texto_del_usuario=texto_del_usuario)
    except ModelDispatchConfigError as e:
        # ModelDispatchConfigError hereda de RuntimeError: este except TIENE
        # que ir antes del `except Exception` genérico, o éste se lo come.
        # Es NUESTRA (fila del catálogo `model` mal sembrada), no una caída
        # real del proveedor -- por eso config_error es un outcome propio,
        # no provider_error.
        await record_facet_health(
            facet, OUTCOME_CONFIG_ERROR, source, texto_de_error(e))
        # ERROR en el log ADEMÁS de la excepción: el 502 que ve el usuario
        # trunca a 200 chars, el operador necesita el mensaje completo (trae
        # el UPDATE que siembra la fila). Vive acá y no en los validadores
        # desde 2026-09-14 (PR-J ronda 1): los validadores también los usan
        # los admins, donde no se aborta ningún dispatch.
        # DESVÍO DELIBERADO del requisito ("mismo log, idéntico"): antes solo
        # los dos casos NULL logueaban, ahora sale para CUALQUIER
        # ModelDispatchConfigError (también un nombre de parámetro inválido o
        # un tope <= 0, que antes solo subían como excepción) y suma
        # facet/source. Es mejor así: toda abortada por catálogo deja rastro
        # con qué faceta y qué camino (chat o canario) la disparó.
        logger.error(f"dispatch abortado: facet={facet!r} source={source!r}: {e}")
        raise            # SIEMPRE re-lanza: no puede volverse fail-open
    except Exception as e:
        # Task 6 S1: texto_de_error redacta. Defensa en profundidad: la key
        # de Gemini va en la cabecera x-goog-api-key (T6-2) y str(e) de un
        # HTTPStatusError trae la URL, no las cabeceras.
        await record_facet_health(
            facet, OUTCOME_PROVIDER_ERROR, source, texto_de_error(e))
        raise            # SIEMPRE re-lanza: no puede volverse fail-open
    await record_facet_health(facet, outcome, source)
    return texto, usage


def _detalle_502_http(facet: str, e: httpx.HTTPStatusError) -> dict:
    """detail del 502 cuando el proveedor responde con error. Código estable
    (A-51); `motivo` es lo que dijo el proveedor, REDACTADO y DESPUÉS recortado
    (fix round 1 de 3bed155: al revés, una key que cruza el corte sale en claro)."""
    return {"code": "proveedor_error_http", "facet": facet, "status": e.response.status_code,
            "motivo": recortar_redactado(e.response.text, 200)}


def _detalle_502_generico(facet: str, e: Exception) -> dict:
    return {"code": "faceta_error", "facet": facet, "motivo": recortar_redactado(str(e), 200)}


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


def _conversation_cache_key(tenant_id: str, user_id: str, project_id: str | None = None) -> str:
    """Namespace ephemeral history by authenticated tenant *and* resolved scope.

    The project value comes from the authority-resolved ``ScopeContext``, not
    from the request body.  A user's private chat and two project chats must
    never inherit one another's provider-facing history.
    """
    if not tenant_id:
        raise ValueError("tenant scope is required for conversation cache")
    return f"{tenant_id}:{user_id}:project:{project_id if project_id is not None else '-'}"


async def _scope_for_chat(user: AuthUser, requested_project_id: int | None) -> ScopeContext:
    """Resolve the B9 scope from current identity and project authority.

    ``requested_project_id`` is only a requested lookup target.  In
    particular it is not a claim of membership or a role assertion: the JAX
    resolver checks active user/tenant identity, project scope and membership
    in the same authoritative database before an enriched project scope can
    reach the B9 reader.
    """
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail={"code": "tenant_scope_required"})
    requested_scope = ScopeContext(
        actor_principal=f"user:{user.user_id}", actor_type="USER",
        subject_user_id=str(user.user_id), tenant_id=str(user.tenant_id),
        project_id=str(requested_project_id) if requested_project_id is not None else None,
        calling_component="jax-platform-web-chat",
    )
    try:
        return await ProjectScopeAuthorityResolver(await get_pool()).resolve_scope(requested_scope)
    except (AuthorizationDenied, ScopeDenied) as exc:
        # A missing/revoked membership, an unbound legacy project, a disabled
        # scope and every tenant mismatch deliberately share this fail-closed
        # boundary.  The request target never reveals which condition failed.
        code = "project_scope_denied" if requested_project_id is not None else "tenant_scope_denied"
        raise HTTPException(status_code=403, detail={"code": code}) from exc
    except Exception as exc:
        # Authority must be available at this boundary.  Do not degrade a
        # project request to tenant/private scope when its source cannot be
        # read.
        logger.error("B9 scope authority resolution failed", exc_info=True)
        code = "project_scope_denied" if requested_project_id is not None else "tenant_scope_denied"
        raise HTTPException(status_code=403, detail={"code": code}) from exc


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, background_tasks: BackgroundTasks, user: AuthUser = Depends(exigir_mesa_libre)):
    config = _load_config()
    # req.facet es input de usuario sin validar: si no está en la whitelist
    # de config["personalities"], hoy _invoke_facet caía en silencio al
    # fallback de jax_local (ver bloque "# fallback" ahí) en vez de
    # rechazar el request — y el valor crudo llegaba igual a
    # shadow_messages.facet VARCHAR(30) (finding 1 de la revisión final).
    # Rechazar acá, antes de cualquier otro efecto secundario.
    if req.facet is not None and req.facet not in config["personalities"]:
        raise HTTPException(status_code=400, detail={"code": "faceta_desconocida", "facet": req.facet[:50]})
    facet = req.facet if req.facet else _auto_route(req.message)
    tenant_id = user.tenant_id
    user_id = user.user_id
    # Task 7: ids no numericos se cortan ACA, antes de la memoria y del LLM --
    # si no, el turno se paga y la fila de uso se pierde en el INSERT.
    validar_ids_de_uso(user_id, tenant_id)
    memory_scope = await _scope_for_chat(user, req.project_id)
    history_key = _conversation_cache_key(
        memory_scope.tenant_id, memory_scope.subject_user_id or user_id,
        memory_scope.project_id,
    )
    timestamp = utc_ahora().isoformat() + "Z"

    # Easter egg IDE1990 "antes que todo", como en el REPL: ni una faceta
    # fijada ni un adjunto lo tapan, y no llama a ningún modelo. Igual que el
    # REPL, la respuesta NO va a la memoria persistente: el extractor de hechos
    # la leería sin filtro y fabricaría hechos del texto (auditoría 2026-09-23,
    # MAJOR-2). Sí va al hilo en RAM, para que el turno siguiente sepa qué pasó.
    if es_easter_egg(req.message):
        # req.message pelado: los adjuntos todavía no se leyeron y se ignoran.
        _update_history(user_id, req.message, EASTER_EGG_TEXT)
        await _fire_completed("jax_local", tenant_id, user_id)
        return ChatResponse(facet="jax_local", response=EASTER_EGG_TEXT, timestamp=timestamp,
                            contract_degraded=False)

    # --- Adjuntos (frente D; RD3: por id) — ANTES de memoria, estado y proveedor
    # Un rechazo no deja fila en memoria, no pone la faceta en "thinking" y no
    # gasta una llamada. Orden: tope por mensaje, sidecars del dueño (404
    # único para cualquier id que no sirva), visión contra el modelo RESUELTO
    # de la faceta (facet_binding -> model.input_modalities) ANTES de
    # codificar la imagen, y recién ahí leer datos desde disco (en un hilo).
    # Si la faceta no resuelve, se sigue: el dispatch devuelve su aviso de "no
    # disponible" sin llamar a ningún proveedor. Nada de esto loguea un id.
    validados = SIN_ADJUNTOS
    if req.adjuntos:
        try:
            if facet == "hyde":
                raise AdjuntoRechazado(422, "adjuntos_no_soportados", facet=facet)
            limites = cargar_limites()
            metadatos = await buscar_adjuntos(req.adjuntos, user, limites)
            imagenes = imagenes_de(metadatos)
            if imagenes:
                try:
                    resuelta = await resolve_facet(facet)
                except FacetUnavailableError:
                    resuelta = None
                if resuelta is not None:
                    exigir_soporte_de_imagen(resuelta, facet, imagenes)
            validados = await leer_adjuntos(metadatos, user, limites)
        except AdjuntoRechazado as e:
            detalle = e.detail
            raise HTTPException(status_code=e.status, detail=detalle) from None
        except ImagenNoSoportadaError:
            raise HTTPException(status_code=422,
                                detail={"code": "imagen_no_soportada", "facet": facet}) from None
    mensaje_al_modelo = componer_mensaje(req.message, validados.textos)
    # -----------------------------------------------------------------------

    # --- Memoria semántica (misma jax_memory que el REPL) — best-effort -----
    # user_id/tenant_id come from authenticated/resolved authority; project_id
    # is present only after the project resolver proved active membership.
    try:
        mem_uid = int(user_id)
        mem_tid = int(tenant_id)
    except (TypeError, ValueError):
        mem_uid = mem_tid = None
    mem_pid = memory_scope.project_id
    conv_uuid = None
    if mem_uid is not None:
        conv_uuid = await _get_conv_uuid(mem_uid, mem_tid, mem_pid)
        if conv_uuid:
            _memory.save_message(conv_uuid, "user", metadatos_para_memoria(req.message, validados))  # fire-and-forget
    # -----------------------------------------------------------------------

    # Respuestas especiales (sin llamada a LLM) — nunca pasan por el parseo
    # de contrato, igual que usage=None (is_canned=True) dentro de _invoke_facet.
    if facet == "hyde":
        aviso = AvisoDeChat(code="hyde_usa_modo_comando")
        await _fire_completed(facet, tenant_id, user_id)
        return ChatResponse(facet=facet, response=aviso.como_texto(), timestamp=timestamp,
                            contract_degraded=False, aviso=aviso)

    # Señal: faceta pensando
    await engine_state.set_facet_status(facet, "thinking", tenant_id, user_id, req.message[:100])

    # B9 retrieval is envelope-only.  No legacy MemoryDB row may be converted
    # into a provider message or prompt string on this supported path.
    try:
        memory_context = await _prompt_memory_context(memory_scope)
    except B9MemoryUnavailable as exc:
        # A failed B9 reader is not equivalent to an empty authorized result.
        # In particular, a pool/cursor integration error must not silently
        # erase grounding and continue as a normal successful chat request.
        raise HTTPException(status_code=503, detail={"code": "MEMORY_UNAVAILABLE"}) from exc

    # SP3: UN snapshot por turno, construido acá y pasado a sus dos
    # consumidores (el prompt y el background task) -- spec §9.3.
    grounding = await _build_grounding()

    try:
        response_text, usage = await _invoke_facet(
            facet, config, history_key, mensaje_al_modelo, memory_context=memory_context,
            grounding=grounding, imagenes=validados.imagenes, texto_del_usuario=req.message)
        is_canned = usage is None
    except ImagenNoSoportadaError:
        # Carrera: el binding cambió entre la validación de arriba y el
        # dispatch (un rebind en ese mismo instante). El dispatch se negó a
        # mandar la imagen; mismo 422 que arriba.
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=422,
                            detail={"code": "imagen_no_soportada", "facet": facet}) from None
    except httpx.HTTPStatusError as e:
        # Task 6 S1: el cuerpo del proveedor no deberia repetir la key, pero
        # el `motivo` del detail (dict con codigo, A-51) sale al usuario y al
        # bus -- se redacta igual (y antes de recortar: ver _detalle_502_http).
        detail = _detalle_502_http(facet, e)
        await engine_state.set_facet_status(facet, "error", tenant_id, user_id, detail["motivo"][:100])
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=502, detail=detail)
    except Exception as e:
        detail = _detalle_502_generico(facet, e)
        await engine_state.set_facet_status(facet, "error", tenant_id, user_id, detail["motivo"][:100])
        await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)
        raise HTTPException(status_code=502, detail=detail)

    aviso = response_text if isinstance(response_text, AvisoDeChat) else None
    if aviso is not None:
        response_text = aviso.como_texto()

    # shadow_message_id: id propio de shadow validation, nunca un id de
    # `messages` (que no existe — _memory.save_message() es fire-and-forget).
    # Encolado al final vía BackgroundTasks (Task 5, run_shadow_validation).
    shadow_message_id = str(uuid.uuid4())

    # Contrato {claim/analysis/judgment}: solo se intenta parsear cuando
    # hubo una llamada real al LLM (usage is not None, ver nota en _invoke_facet).
    contract = _parse_contract_response(response_text) if not is_canned else None
    if contract is not None:
        display_text, contract_degraded = _build_display_response(contract)
    else:
        display_text, contract_degraded = response_text, False

    _update_history(history_key, mensaje_para_historial(req.message, validados), display_text)

    # Registrar uso (best-effort)
    personality = config["personalities"].get(facet, {})
    model_name = personality.get("model_default", facet)
    if usage is not None:
        await record_usage(user_id, tenant_id, facet, usage.provider_id, usage.model, usage.tokens_in, usage.tokens_out, "chat")
        model_name = usage.model  # modelo real resuelto, no el stale de config.toml

    # Guardar la respuesta de la faceta en la MISMA memoria (fire-and-forget).
    if conv_uuid:
        _memory.save_message(conv_uuid, facet, display_text,
                             facet=facet, model=model_name)

    await _fire_completed(facet, tenant_id, user_id)
    await engine_state.set_facet_status(facet, "idle", tenant_id, user_id)

    # Encolar shadow validation es un efecto secundario de medición, no
    # debe poder tumbar un turno de chat que YA respondió al usuario (el
    # mensaje del asistente ya se guardó y se transmitió por WebSocket
    # arriba). Si el import diferido o add_task fallan (p.ej. import cycle
    # roto, shadow_validation.py con un error de sintaxis introducido
    # después), lo logueamos y seguimos — finding 4 de la revisión final.
    # Los errores DENTRO de run_shadow_validation (el fail-closed logging
    # de la Task 5) no pasan por acá, ese try/except es de shadow_validation.py.
    try:
        from shadow_validation import run_shadow_validation
        from jax_engine.background import add_safe_task
        # req.origin ausente (None) se declara 'unattributed' ACÁ, en el
        # borde -- no en el default de la columna solamente -- para que el
        # sexto argumento de run_shadow_validation nunca sea None: un
        # llamador de ese módulo que reciba None por descuido escribiría
        # la palabra "None", no el valor fail-closed real.
        origin = req.origin or "unattributed"
        add_safe_task(background_tasks, run_shadow_validation, conv_uuid, shadow_message_id, facet, contract, grounding, origin)
    except Exception:  # fail-soft: la respuesta ya se guardó y se transmitió; encolar la medición no puede tumbar el turno; logueado con traceback
        logger.exception("no se pudo encolar shadow validation")

    return ChatResponse(
        facet=facet, response=display_text, timestamp=timestamp,
        contract_degraded=contract_degraded, aviso=aviso,
    )


async def _fire_completed(facet: str, tenant_id: str, user_id: str):
    # A-13 (2026-09-16): el único lector (useJaxStore) mira el event_type; la
    # respuesta ya viaja por HTTP. No se repite por el bus.
    event = JAXEvent(event_type="facet_response_completed", tenant_id=tenant_id,
                     user_id=user_id, payload={"facet": facet})
    await event_bus.publish(event)
