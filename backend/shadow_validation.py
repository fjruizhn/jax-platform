"""
Shadow validation (REFORMAS-v3 Fase 2 Sub-proyecto 2) — corre después de
que la Mesa web ya respondió al usuario. Mide, no bloquea: cada claim se
valida contra policy/governance/validator.py, cada bloque de
analysis/judgment se barre contra el vocabulario cerrado.

Importa policy/governance/ directo desde ~/jax (sys.path) — mismo patrón
que api/chat.py ya usa para jax.memory.db.MemoryDB. No hay puente HTTP:
ambos repos viven en el mismo host, y validator.py ya asume ese layout
(sus propios imports insertan REPO_ROOT en sys.path).

authority de todo claim la DERIVA EL SERVIDOR acreditando el claim contra el
snapshot que se inyectó en ese turno (grounding.py, SP3, 2026-09-03): OBSERVADO
si citó una línea del snapshot y los args coinciden, INFERIDO en cualquier
otro caso. Nunca lo declara el modelo (P08).

Lo que este docstring afirmaba hasta el 2026-09-01 —"Resultado esperado:
100% AUTHORITY_INVALID esta ronda"— describía un estado previsto que
nunca ocurrió, y se leyó como si fuera el medido. Lo medido contra la
MariaDB real ese día, catorce días después del despliegue: 22 de 22
mensajes en `shadow_messages` con `has_claim = 0`, en 5 facetas, y
`shadow_claim_verdicts` con CERO filas. No hubo un 100% de nada: no hubo
claims. El cuello de botella estaba un paso antes —la emisión, no la
autoridad— y la causa era que `_CONTRACT_PROMPT_SUFFIX` no nombraba
ninguno de los ocho predicados del vocabulario cerrado. Corregido en
api/chat.py: el prompt se genera desde predicates.yaml.

Verificado en vivo el mismo día, contra el servicio corriendo y las cinco
facetas que producen tráfico (jax_local, jekyll, hipatia, ada, thot):

- Con el prompt nuevo, pedido explícito de claims: 2 de 3 facetas
  probadas emitieron claims válidos del vocabulario cerrado
  (FACET_EXISTS, CAPABILITY_AVAILABLE) con los args exactos, ninguno
  inventado. `shadow_claim_verdicts` pasó de 0 filas a 4, todas
  AUTHORITY_INVALID por §3.1.4 — la predicción de arriba, medida por
  primera vez en lugar de supuesta.
- Con una pregunta orgánica sobre el estado del sistema: las 5 facetas
  siguieron devolviendo []. No es el prompt: sus personas se niegan
  —correctamente— a afirmar sin evidencia, y no hay grounding cableado
  que se las dé. thot mantuvo esa negativa incluso ante el pedido
  explícito.

O sea: nombrar el vocabulario era NECESARIO y no es SUFICIENTE. El
bloqueo que queda es el grounding, que es exactamente el objeto de
Sub-proyecto 3 — y ahora tiene su primer dato medido en vez de una
predicción.

`ClosedVocabulary.term_categories` y la firma de `sweep(text,
term_categories) -> list[tuple[str, frozenset[str]]]` vienen de la Tarea
1 de este mismo plan (repo `jax`, commit 5428d62), mergeada a `jax`
master en `ed25258` (2026-08-18). Antes de ese merge este módulo traía
un bridge local que releía closed_vocabulary.yaml a mano para no
depender de una rama sin mergear — duplicaba lógica que `loaders.py`
existe específicamente para poseer (hash fail-closed contra
policy/VERSION, única fuente de verdad de la estructura de categorías).
Con el merge hecho, se removió: `loaders.load_vocabulary()` es ahora la
única fuente de `term_categories`, sin duplicación.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Ruta al repo `jax` (repo vecino, de donde salen policy/governance/). Igual
# que CONFIG_PATH en api/chat.py: configurable por entorno, con el MISMO
# default de siempre. Era `~/jax` y nada mas -- una ruta hardcodeada a otro
# repo, relativa al $HOME del usuario, que hacia imposible correr esta parte
# de la suite fuera de la maquina de Fernando (9 tests con
# ModuleNotFoundError: No module named 'claims', medido en un contenedor
# limpio el 2026-09-01).
JAX_REPO = Path(os.getenv("JAX_REPO_PATH", os.path.expanduser("~/jax")))
if str(JAX_REPO) not in sys.path:
    sys.path.insert(0, str(JAX_REPO))
if str(JAX_REPO / "policy" / "governance") not in sys.path:
    sys.path.insert(0, str(JAX_REPO / "policy" / "governance"))

import claims as governance_claims  # noqa: E402
import grounding as governance_grounding  # noqa: E402
import validator as governance_validator  # noqa: E402
import vocab_sweep as governance_vocab_sweep  # noqa: E402

from api.chat import ContractResult  # noqa: E402
from db.connection import get_pool  # noqa: E402

# El contexto vive en governance_context.py desde SP3 (lo comparte chat.py).
# Se conserva el nombre _validation_context en este módulo a propósito:
# tests/test_shadow_validation.py lo parchea por nombre.
from governance_context import validation_context as _validation_context  # noqa: E402


def _grounding_columns(grounding_result) -> tuple[str, str]:
    """(grounding_snapshot, grounding_snapshot_sha256). Tres estados a
    propósito (spec §5.4): 'ERROR' con el motivo cuando el snapshot falló;
    64 hex con el JSON canónico cuando existe. NULL no sale de acá nunca:
    NULL = fila anterior a SP3."""
    if isinstance(grounding_result, governance_grounding.SnapshotError):
        return json.dumps({"error": grounding_result.reason}, ensure_ascii=False), "ERROR"
    return grounding_result.canonical_json, grounding_result.sha256


async def _insert_shadow_message(cur, conv_uuid, shadow_message_id, facet, contract, grounding_result):
    # Defensa en profundidad (finding 1 de la revisión final): api/chat.py
    # ya valida facet contra la whitelist de config["personalities"] antes
    # de llegar acá, pero este módulo es importable/invocable por
    # cualquier otro caller de run_shadow_validation — clampear a 30
    # caracteres acá asegura que este INSERT (el primero de la función,
    # ver comentario en run_shadow_validation) nunca falle con
    # "Data too long" por esta columna específicamente, sin importar quién
    # llame. shadow_messages.facet es VARCHAR(30) (db/migrations.py).
    snapshot_json, sha = _grounding_columns(grounding_result)
    await cur.execute(
        "INSERT INTO shadow_messages "
        "(conv_uuid, shadow_message_id, facet, contract_parsed, degradation_reason, "
        "has_claim, has_analysis, has_judgment, grounding_snapshot, grounding_snapshot_sha256) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            conv_uuid, shadow_message_id, facet[:30], contract.contract_parsed,
            contract.degradation_reason,
            bool(contract.claims), bool(contract.analysis), bool(contract.judgment),
            snapshot_json, sha,
        ),
    )


async def _mark_validated(cur, shadow_message_id):
    await cur.execute(
        "UPDATE shadow_messages SET validated_at = NOW() WHERE shadow_message_id = %s",
        (shadow_message_id,),
    )


_POINTER_COLUMN_WIDTH = 100  # shadow_claim_verdicts.evidence_pointer VARCHAR(100)
_DETAIL_COLUMN_WIDTH = 60000  # shadow_claim_verdicts.detail es TEXT (65535 bytes);
# margen bajo el límite real para dejar espacio a caracteres multibyte.


async def _insert_claim_verdict(cur, conv_uuid, shadow_message_id, verdict, raw_claim, accreditation):
    # authority: SIEMPRE la derivada por el servidor (spec §9.1). Si el
    # modelo mandó un campo authority, no entra acá: va al detail.
    detail = verdict.detail
    declared = raw_claim.get("authority")
    if declared is not None:
        detail += f" | el modelo declaró authority={declared!r} (ignorado: la autoridad la deriva el servidor)."
    # evidence_pointer: tal como se recibió, truncado al ancho de la columna;
    # si se truncó, el original completo va al detail (spec §9.1b).
    # accreditation.detail puede ya traer el puntero (preview de 120 o completo);
    # acá se garantiza el original íntegro sin depender de qué rama lo produjo.
    pointer = accreditation.evidence_pointer_raw
    pointer_db = None
    if pointer is not None:
        as_text = pointer if isinstance(pointer, str) else repr(pointer)
        pointer_db = as_text[:_POINTER_COLUMN_WIDTH]
        if len(as_text) > _POINTER_COLUMN_WIDTH:
            detail += f" | evidence_pointer truncado a {_POINTER_COLUMN_WIDTH}; original: {as_text}"
    # defensa en profundidad (mismo argumento que el clamp de `facet`): con
    # sql_mode=STRICT_TRANS_TABLES un detail que exceda la columna hace que el
    # INSERT falle a mitad del loop y se pierdan los claims restantes del turno.
    detail = detail[:_DETAIL_COLUMN_WIDTH]
    await cur.execute(
        "INSERT INTO shadow_claim_verdicts "
        "(conv_uuid, shadow_message_id, predicate, status, detail, args, authority, evidence_pointer) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (conv_uuid, shadow_message_id, verdict.predicate, verdict.status, detail,
         json.dumps(raw_claim["args"]), accreditation.authority, pointer_db),
    )


async def _insert_vocab_hit(cur, conv_uuid, shadow_message_id, channel, term, category):
    await cur.execute(
        "INSERT INTO shadow_vocab_hits "
        "(conv_uuid, shadow_message_id, channel, term, category) "
        "VALUES (%s, %s, %s, %s, %s)",
        (conv_uuid, shadow_message_id, channel, term, category),
    )


async def run_shadow_validation(
    conv_uuid: str | None,
    shadow_message_id: str,
    facet: str,
    contract: "ContractResult | None",
    grounding: "governance_grounding.Snapshot | governance_grounding.SnapshotError",
) -> None:
    """`grounding` es OBLIGATORIO y sin default a propósito (spec §9.2): es
    lo que garantiza que ninguna fila nueva de shadow_messages quede con
    grounding_snapshot_sha256 NULL. Un caller que lo omita falla al llamar,
    no produce una fila ambigua."""
    if conv_uuid is None or contract is None:
        return

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # La fila se inserta PRIMERO — antes de tocar el validador de
                # claims, el barrido de vocabulario, O SIQUIERA CARGAR la
                # config estática de gobernanza (_validation_context() abajo).
                # load_vocabulary()/load_predicates()/load_validation_context()
                # pueden lanzar (YAML mal formado, guard fail-closed propio de
                # loaders.py, config.toml de las_manos ilegible) — si eso
                # pasara ANTES de este insert, la función saldría sin haber
                # tocado shadow_messages: cero fila, no una fila con
                # validated_at NULL. Eso es exactamente la pérdida silenciosa
                # que esta tabla existe para volver visible. El pool corre con
                # autocommit=True (db/connection.py), así que este INSERT ya
                # quedó durablemente escrito en cuanto el `await` retorna: si
                # el proceso muere en cualquier punto de acá en adelante,
                # queda una fila con validated_at NULL — visible, no
                # silenciosa (garantía fail-closed, ver spec sección 3).
                await _insert_shadow_message(cur, conv_uuid, shadow_message_id, facet, contract, grounding)

                ctx, predicates, term_categories = _validation_context()

                for raw_claim in contract.claims:
                    # 1) acreditar contra el snapshot del turno (grounding.py,
                    #    puro): de acá salen authority y provenance_ref.
                    accreditation = governance_grounding.accredit(raw_claim, grounding)
                    claim = governance_claims.Claim(
                        predicate=raw_claim["predicate"],
                        args=governance_grounding.normalize_args(raw_claim["args"]),
                        authority=accreditation.authority,
                        provenance_ref=accreditation.provenance_ref,
                        evidence_pointer=(
                            accreditation.evidence_pointer_raw
                            if isinstance(accreditation.evidence_pointer_raw, str) else ""
                        ),
                        scope="mesa_web",
                    )
                    # 2) veredicto en el orden normativo del spec §4.1.
                    verdict = governance_validator.validate(claim, predicates, ctx, accreditation=accreditation)
                    await _insert_claim_verdict(
                        cur, conv_uuid, shadow_message_id, verdict, raw_claim, accreditation
                    )

                for channel, text in (("analysis", contract.analysis), ("judgment", contract.judgment)):
                    if not text:
                        continue
                    hits = governance_vocab_sweep.sweep(text, term_categories)
                    for term, categories in hits:
                        for category in sorted(categories):
                            await _insert_vocab_hit(
                                cur, conv_uuid, shadow_message_id, channel, term, category
                            )

                await _mark_validated(cur, shadow_message_id)
            await conn.commit()
    except Exception:
        # validated_at IS NULL ya dice QUE se perdió una corrida — esto deja
        # registrado POR QUÉ, con los tres identificadores para poder
        # correlacionar la fila en shadow_messages. No se traga la excepción
        # (BackgroundTasks de Starlette debe verla) — logging.exception y
        # luego re-raise, nunca uno sin el otro.
        logger.exception(
            "shadow validation falló para shadow_message_id=%s conv_uuid=%s facet=%s",
            shadow_message_id, conv_uuid, facet,
        )
        raise
