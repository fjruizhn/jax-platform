"""Contrato de dispatch de un modelo del catálogo (`model`) -- 2026-09-14.

Qué datos de la fila de `model` necesita el dispatch para armar el request, y
los validadores que los exigen FAIL-CLOSED. Vivían en api/chat.py; se movieron
acá (sin cambiar su lógica) para que los usen DOS lectores con la MISMA regla:

  - api/chat.py::_call_openai_compat, al despachar (como siempre).
  - api/admin/models.py (aprobar una propuesta) y api/admin/facet_bindings.py
    (PUT de un binding), ANTES de escribir facet_binding.

Por qué los admins: el 2026-09-12 se aprobó la propuesta #11 (drift
deepseek-v4-flash -> deepseek-flash) y jekyll quedó apuntando a una fila sin
max_tokens_param ni max_output_tokens: la faceta se cayó recién en el primer
uso (502 ModelDispatchConfigError). La aprobación nunca debió aceptarse. Con el
mismo validador en la escritura, un binding que el dispatch rechazaría no llega
a existir.

No se importa api.chat desde los admins: api.chat -> api.admin.usage arrastra
api/admin/__init__.py, que importa .models y .facet_bindings (ciclo verificado,
ver el import diferido de approve_proposal). Este módulo no importa nada de
la app.
"""
import logging

logger = logging.getLogger(__name__)

# Transportes cuyo dispatch en la Mesa web lee el contrato de la fila de
# `model`. Hoy uno solo: http_openai_compat manda `{max_tokens_param:
# max_output_tokens}` en el body (_call_openai_compat). Los otros NO lo leen:
# http_gemini (_call_gemini) arma generateContent sin límite de salida desde la
# fila; ollama y subprocess no pasan por el catálogo de límites; motor_registry
# no se despacha en la Mesa web. Si un transporte empieza a leer una columna
# de `model` al despachar, entra en este conjunto en el mismo commit.
TRANSPORTS_CON_CONTRATO_DE_DISPATCH = frozenset({"http_openai_compat"})

class ModelDispatchConfigError(RuntimeError):
    """El catálogo (`model`) no declara un dato que el dispatch NECESITA para
    armar el request. FAIL-CLOSED y RUIDOSO: nunca se asume un valor por
    defecto — un default silencioso es exactamente lo que convierte el
    próximo modelo nuevo en un incidente sin síntoma."""


# Nombres válidos del parámetro de límite de salida. Es el mismo conjunto que
# el ENUM de model.max_tokens_param (db/migrations.py) — se replica acá para
# que un valor imposible en la DB (ej. una migración a mano que se saltó el
# ENUM) no termine armando una clave arbitraria en el JSON que va a la API.
_MAX_TOKENS_PARAM_NAMES = ("max_tokens", "max_completion_tokens")

# El límite de salida se manda SIEMPRE explícito: sin él, un modelo de
# razonamiento (reasoning_content compitiendo por el mismo budget que content)
# puede agotarlo y cortar la respuesta antes de escribirla — mismo bug ya
# diagnosticado y corregido en motor_registry/worker.py::_call_kimi (017ba2f,
# 2026-08-10). Lo que dejó de ser universal es el VALOR: acá vivía la constante
# 131072 (la misma que jax/muscles/base.py) hasta que gpt-5.6-terra la rechazó
# con HTTP 400 ("max_tokens is too large: 131072. This model supports at most
# 128000 completion tokens"). Ahora sale de model.max_output_tokens, fila por
# fila. Ver _max_output_tokens_value().


def _max_tokens_field(model: str, max_tokens_param: str | None) -> str:
    """Devuelve el NOMBRE del parámetro de límite de salida que exige la API de
    `model`, tal como lo declara el catálogo (`model.max_tokens_param`).

    Por qué es un dato del catálogo y no una constante: 'max_tokens' fue el
    nombre único durante años, pero OpenAI lo rechaza con HTTP 400
    ("Unsupported parameter: 'max_tokens' is not supported with this model.
    Use 'max_completion_tokens' instead") en su generación nueva — el que tumbó
    a thot/gpt-5.6-terra por 3 días (2026-08-24). Cambiar la constante al
    nombre nuevo arregla la instancia y rompe la clase: deepseek-v4-flash
    (jekyll) y glm-5.3 (ada) siguen exigiendo el viejo. Es una propiedad
    estable POR MODELO, del mismo eje que supports_tool_use /
    supports_structured_output / context_window, y vive en la misma fila.

    NULL falla ruidoso a propósito (decisión del dueño, 2026-08-27): si el
    default fuera el parámetro viejo, el próximo modelo nuevo se rompería igual
    que thot pero en silencio y sin nadie mirando. Preferimos que un modelo sin
    valor falle con un mensaje que un operador pueda ejecutar."""
    if max_tokens_param is None:
        # Sin log acá (2026-09-14, PR-J ronda 1): este validador lo usan
        # también los admins, donde NO se aborta ningún dispatch. El ERROR
        # "dispatch abortado" (con este mensaje completo, que trae el UPDATE:
        # el 502 que ve el usuario trunca a 200 chars) lo escribe el camino de
        # dispatch en api/chat.py::_invoke_facet; el admin escribe su WARNING.
        raise ModelDispatchConfigError(
            f"modelo '{model}': la fila de `model` no declara max_tokens_param, "
            f"así que no se sabe si su API exige 'max_tokens' o "
            f"'max_completion_tokens' y NO se asume ninguno. Sembrala: "
            f"UPDATE model SET max_tokens_param='max_tokens' "  # o 'max_completion_tokens'
            f"WHERE model_id='{model}';  -- agregá AND provider_id='<provider>' "
            f"si ese model_id existe para más de un proveedor. Usá "
            f"'max_completion_tokens' para los modelos que rechazan el viejo "
            f"con HTTP 400 (generación nueva de OpenAI), 'max_tokens' para el resto."
        )
    if max_tokens_param not in _MAX_TOKENS_PARAM_NAMES:
        raise ModelDispatchConfigError(
            f"modelo '{model}': max_tokens_param={max_tokens_param!r} no es un "
            f"nombre de parámetro conocido {_MAX_TOKENS_PARAM_NAMES}. Corregí la "
            f"fila de `model` — no se manda una clave arbitraria a la API."
        )
    return max_tokens_param


def _max_output_tokens_value(model: str, max_output_tokens: int | None) -> int:
    """Devuelve el VALOR del límite de tokens de salida que acepta la API de
    `model`, tal como lo declara el catálogo (`model.max_output_tokens`).

    Par de _max_tokens_field(): aquel resuelve CÓMO se llama el parámetro, éste
    QUÉ VALOR admite. Arreglado el nombre (2026-08-27), la misma API contestó
    HTTP 400 por el valor: "max_tokens is too large: 131072. This model supports
    at most 128000 completion tokens, whereas you provided 131072". La constante
    131072 era universal mientras todos los modelos del camino la aceptaran;
    dejó de serlo, y el tope es una propiedad estable POR MODELO.

    NO se deriva de context_window: aquella es la ventana TOTAL (entrada+salida)
    y ésta el tope de completion. gpt-5.6-terra tiene context_window=1050000
    contra un tope de 128000 — verificado, no supuesto. Derivar uno del otro
    sería inventar el dato.

    NULL falla ruidoso a propósito (decisión del dueño, textual: "prefiero que
    un modelo sin valor falle ruidoso a que asuma"). Un default de 131072
    reproduciría este incidente contra el próximo modelo con tope más bajo; uno
    "conservador" truncaría respuestas de modelos de razonamiento en silencio,
    que es justo el bug que el límite explícito existe para prevenir."""
    if max_output_tokens is None:
        # Sin log acá: ver el comentario gemelo en _max_tokens_field.
        raise ModelDispatchConfigError(
            f"modelo '{model}': la fila de `model` no declara max_output_tokens, "
            f"así que no se sabe cuántos tokens de salida acepta su API y NO se "
            f"asume ninguno. Sembrala: "
            f"UPDATE model SET max_output_tokens=<tope de completion> "
            f"WHERE model_id='{model}';  -- agregá AND provider_id='<provider>' "
            f"si ese model_id existe para más de un proveedor. El tope sale de la "
            f"doc del proveedor o del propio HTTP 400 ('This model supports at "
            f"most N completion tokens'); NO es context_window, que es la ventana "
            f"total entrada+salida y suele ser mucho mayor."
        )
    if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens <= 0:
        # Defensa en profundidad contra un valor imposible en la DB (una
        # migración a mano, un 0 heredado de un backfill): un límite <= 0 haría
        # que la API devuelva vacío o un 400, con un modo de falla que se
        # confunde con un error real del proveedor.
        raise ModelDispatchConfigError(
            f"modelo '{model}': max_output_tokens={max_output_tokens!r} no es un "
            f"entero positivo. Corregí la fila de `model` — no se manda un límite "
            f"inválido a la API."
        )
    return max_output_tokens


def faltantes_del_contrato(
    transport: str, model: str, max_tokens_param: str | None, max_output_tokens: int | None,
) -> list[tuple[str, ModelDispatchConfigError]]:
    """Corre los MISMOS validadores que el dispatch sobre una fila de `model`
    y devuelve `(columna_faltante, error)` por cada uno que levantaría (vacío =
    el dispatch la aceptaría). La columna sale de QUÉ validador falló, no de
    parsear su mensaje.

    Solo exige el contrato si `transport` lo lee al despachar
    (TRANSPORTS_CON_CONTRATO_DE_DISPATCH): para ollama/subprocess/http_gemini
    esas columnas no significan nada y bloquear sería inventar un requisito.
    Junta los dos errores en vez de cortar en el primero: quien aprueba ve de
    una vez todo lo que falta sembrar."""
    if transport not in TRANSPORTS_CON_CONTRATO_DE_DISPATCH:
        return []
    errores = []
    try:
        _max_tokens_field(model, max_tokens_param)
    except ModelDispatchConfigError as e:
        errores.append(("max_tokens_param", e))
    try:
        _max_output_tokens_value(model, max_output_tokens)
    except ModelDispatchConfigError as e:
        errores.append(("max_output_tokens", e))
    return errores


async def detalle_si_rompe_el_contrato(
    cur, facet_key: str, model_ref: int, provider_id: str | None = None,
) -> dict | None:
    """Lo que chequean los dos escritores de facet_binding ANTES de escribir:
    ¿el dispatch de `facet_key` aceptaría la fila `model_ref`?

    `provider_id`: el que quedará en facet_binding.provider_id. El PUT lo
    manda en el request; approve no lo toca (solo cambia model_ref), así que
    pasa None y se usa el que el binding ya tiene.

    None = sí (o no hay nada que decidir acá: faceta o modelo inexistentes los
    rechaza el propio endpoint con su 404/FK de siempre). Si no, el `detail`
    del 409 -- un código estable que el frontend traduce (mismo estilo que
    AdminSmtp: objeto con `code` y datos), qué columna falla y el mensaje.

    Qué se exige, MEDIDO contra TODOS los lectores que despachan con
    facet_binding.model_ref (2026-09-14, PR-J ronda 1; jax @fb8a8a1, solo
    lectura):

    1. max_tokens_param + max_output_tokens, solo si el transporte los lee
       (TRANSPORTS_CON_CONTRATO_DE_DISPATCH). Único lector:
       jax-platform api/chat.py::_call_openai_compat. Ningún otro lector los
       lee: jax/core/facet_resolver.py:229-233 y las_manos/facet_resolver.py:
       231-232 solo traen m.model_id; jacobs/executor.py:318 (openai-compat)
       manda {"model","messages","stream"} sin límite; las_manos/
       motor_registry/worker.py:152-153 manda motor.max_tokens (tabla motor,
       no model; 0 = no lo manda); jax/muscles/base.py:230,269 y
       jacobs/plan.py:577 mandan "max_tokens": 131072 FIJO sin leer `model`.
       Ninguno rompe si esas columnas faltan; ningún otro campo de `model`
       es exigido por ninguno (model_id es NOT NULL).
    2. provider_id del binding == provider_id del modelo, en TODO transporte.
       Los resolvers sacan base_url y credencial de b.provider_id y el modelo
       de b.model_ref (jax-platform facet_resolver.py:274-279,
       jax/core/facet_resolver.py:229-233, las_manos/facet_resolver.py:
       231-232), mientras que motor_registry saca el proveedor del MODELO
       (las_manos/motor_registry/catalog.py:183-189) y el REPL también
       (jax/core/registro_facetas.py:17-19, que documenta el desalineo). Un
       binding con proveedor distinto al del modelo manda model_id de un
       proveedor a la URL y credencial de otro, y los lectores divergen
       entre sí. approve cambia model_ref sin tocar provider_id: una
       propuesta hacia un modelo de otro proveedor lo producía."""
    await cur.execute("SELECT transport FROM facet WHERE `key`=%s", (facet_key,))
    facet_row = await cur.fetchone()
    await cur.execute(
        "SELECT provider_id, model_id, max_tokens_param, max_output_tokens FROM model WHERE id=%s",
        (model_ref,),
    )
    model_row = await cur.fetchone()
    if facet_row is None or model_row is None:
        return None
    (transport,) = facet_row
    model_provider, model_id, max_tokens_param, max_output_tokens = model_row
    if provider_id is None:
        await cur.execute(
            "SELECT provider_id FROM facet_binding WHERE facet_key=%s AND role='primary'",
            (facet_key,),
        )
        binding_row = await cur.fetchone()
        provider_id = binding_row[0] if binding_row else model_provider

    detalle = None
    faltantes = faltantes_del_contrato(transport, model_id, max_tokens_param, max_output_tokens)
    if faltantes:
        detalle = {
            "code": "modelo_sin_contrato_de_dispatch",
            "facet_key": facet_key,
            "transport": transport,
            "model_id": model_id,
            "campos": [campo for campo, _ in faltantes],
            "message": " | ".join(str(e) for _, e in faltantes),
        }
    elif provider_id != model_provider:
        detalle = {
            "code": "modelo_de_otro_proveedor",
            "facet_key": facet_key,
            "transport": transport,
            "model_id": model_id,
            "campos": ["provider_id"],
            "provider_binding": provider_id,
            "provider_modelo": model_provider,
            "message": (
                f"modelo '{model_id}' es del proveedor '{model_provider}' y el "
                f"binding de '{facet_key}' quedaría con provider_id='{provider_id}': "
                f"el dispatch mandaría el modelo a la URL y credencial de otro "
                f"proveedor. Elegí un modelo de '{provider_id}' o cambiá el "
                f"binding con PUT /api/admin/facet-bindings/{facet_key} "
                f"(provider_id='{model_provider}')."
            ),
        }
    if detalle is not None:
        # WARNING y no ERROR: no se abortó ningún dispatch, se rechazó una
        # escritura. El ERROR "dispatch abortado" es del camino de chat.py.
        logger.warning(
            f"escritura de facet_binding rechazada: modelo sin contrato de dispatch "
            f"facet_key={facet_key!r} model={model_id!r} code={detalle['code']} "
            f"campos={detalle['campos']}"
        )
    return detalle
