"""
Shadow validation (REFORMAS-v3 Fase 2 Sub-proyecto 2) — corre después de
que la Mesa web ya respondió al usuario. Mide, no bloquea: cada claim se
valida contra policy/governance/validator.py, cada bloque de
analysis/judgment se barre contra el vocabulario cerrado.

Importa policy/governance/ directo desde ~/jax (sys.path) — mismo patrón
que api/chat.py ya usa para jax.memory.db.MemoryDB. No hay puente HTTP:
ambos repos viven en el mismo host, y validator.py ya asume ese layout
(sus propios imports insertan REPO_ROOT en sys.path).

authority de todo claim es SIEMPRE "INFERIDO", fijado acá — nunca lo
declara el modelo (ver spec, sección 1a: P08 aplicado a metadata).
Resultado esperado: 100% AUTHORITY_INVALID esta ronda, porque chat.py no
tiene grounding cableado al mecanismo de claims. No es un bug.

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
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

JAX_REPO = Path(os.path.expanduser("~/jax"))
if str(JAX_REPO) not in sys.path:
    sys.path.insert(0, str(JAX_REPO))
if str(JAX_REPO / "policy" / "governance") not in sys.path:
    sys.path.insert(0, str(JAX_REPO / "policy" / "governance"))

import claims as governance_claims  # noqa: E402
import loaders as governance_loaders  # noqa: E402
import validator as governance_validator  # noqa: E402
import vocab_sweep as governance_vocab_sweep  # noqa: E402

from api.chat import ContractResult  # noqa: E402
from db.connection import get_pool  # noqa: E402


@lru_cache(maxsize=1)
def _validation_context():
    # Config estática cacheada por proceso — mismo criterio que
    # _load_config() en chat.py (Lección operativa #6, jax-platform/CLAUDE.md):
    # un cambio real requiere reiniciar el proceso, no releer en cada request.
    vocabulary = governance_loaders.load_vocabulary()
    ctx = governance_validator.load_validation_context(
        JAX_REPO, vocabulary.config_paths
    )
    predicates = governance_loaders.load_predicates()
    return ctx, predicates, vocabulary.term_categories


async def _insert_shadow_message(cur, conv_uuid, shadow_message_id, facet, contract):
    await cur.execute(
        "INSERT INTO shadow_messages "
        "(conv_uuid, shadow_message_id, facet, contract_parsed, degradation_reason, "
        "has_claim, has_analysis, has_judgment) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            conv_uuid, shadow_message_id, facet, contract.contract_parsed,
            contract.degradation_reason,
            bool(contract.claims), bool(contract.analysis), bool(contract.judgment),
        ),
    )


async def _mark_validated(cur, shadow_message_id):
    await cur.execute(
        "UPDATE shadow_messages SET validated_at = NOW() WHERE shadow_message_id = %s",
        (shadow_message_id,),
    )


async def _insert_claim_verdict(cur, conv_uuid, shadow_message_id, verdict, args):
    await cur.execute(
        "INSERT INTO shadow_claim_verdicts "
        "(conv_uuid, shadow_message_id, predicate, status, detail, args) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (conv_uuid, shadow_message_id, verdict.predicate, verdict.status, verdict.detail,
         json.dumps(args)),
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
) -> None:
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
                await _insert_shadow_message(cur, conv_uuid, shadow_message_id, facet, contract)

                ctx, predicates, term_categories = _validation_context()

                for raw_claim in contract.claims:
                    claim = governance_claims.Claim(
                        predicate=raw_claim["predicate"],
                        args={k: str(v) for k, v in raw_claim["args"].items()},
                        authority="INFERIDO",
                        provenance_ref=facet,
                        evidence_pointer=f"{conv_uuid}:{shadow_message_id}",
                        scope="mesa_web",
                    )
                    verdict = governance_validator.validate(claim, predicates, ctx)
                    await _insert_claim_verdict(
                        cur, conv_uuid, shadow_message_id, verdict, raw_claim["args"]
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
