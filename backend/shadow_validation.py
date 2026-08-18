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

NOTA DE ALCANCE — bridge de term_categories (verificado 2026-08-18):
El plan de este sub-proyecto agrega `ClosedVocabulary.term_categories` y
una nueva firma de `sweep()` (`sweep(text, term_categories) ->
list[tuple[str, frozenset[str]]]`) como su propia Tarea 1, EN EL REPO
`jax`. Esa tarea está commiteada en la rama `reformas-fase2-sp2-
integracion-real` del repo `jax` (commit 5428d62), pero esa rama todavía
NO está mergeada a `jax` master — `~/jax` (el checkout que usa este
mismo patrón de sys.path que ya usaba api/chat.py) sigue en master, con
la firma vieja: `ClosedVocabulary` sin `term_categories`, `sweep(text,
vocabulary: frozenset[str]) -> list[str]`. Verificado con `git log`/`git
diff` contra `~/jax/.worktrees/reformas-fase2-sp2-integracion-real`
antes de escribir este módulo — no es una suposición.

Hardcodear un sys.path hacia ese worktree sería más frágil que este
bridge (el worktree es un artefacto de desarrollo, se borra al mergear;
`~/jax` es la ruta estable). `_load_term_categories()` abajo relee
closed_vocabulary.yaml con el mismo algoritmo aditivo que esa Tarea 1
todavía no mergeada — cuando esa rama llegue a `jax` master, borrar este
helper y usar `governance_loaders.load_vocabulary().term_categories`
directo, igual que ya hace `_validation_context()` con `.flattened`.
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

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


def _load_term_categories() -> dict[str, frozenset[str]]:
    """Bridge temporal — ver nota de alcance arriba. Relee
    closed_vocabulary.yaml (misma fuente que governance_loaders.py) para
    reconstruir término → categorías, porque la ClosedVocabulary de
    ~/jax (master) todavía no trae ese campo."""
    import yaml

    data = yaml.safe_load(governance_loaders.VOCABULARY_FILE.read_text(encoding="utf-8"))
    term_categories: dict[str, set[str]] = {}
    for key, value in data.items():
        if key == "config_paths":
            continue
        if isinstance(value, dict):
            terms = value.keys()
        elif isinstance(value, list):
            terms = value
        else:
            continue
        for term in terms:
            term_categories.setdefault(term, set()).add(key)
    return {t: frozenset(cats) for t, cats in term_categories.items()}


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
    term_categories = _load_term_categories()
    return ctx, predicates, vocabulary.flattened, term_categories


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

    ctx, predicates, vocabulary_flattened, term_categories = _validation_context()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # La fila se inserta PRIMERO — antes de tocar el validador de
            # claims o el barrido de vocabulario. El pool corre con
            # autocommit=True (db/connection.py), así que este INSERT ya
            # quedó durablemente escrito en cuanto el `await` retorna: si
            # el proceso muere en cualquier punto de acá en adelante,
            # queda una fila con validated_at NULL — visible, no
            # silenciosa (garantía fail-closed, ver spec sección 3).
            await _insert_shadow_message(cur, conv_uuid, shadow_message_id, facet, contract)

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
                hits = governance_vocab_sweep.sweep(text, vocabulary_flattened)
                for term in hits:
                    for category in sorted(term_categories.get(term, frozenset())):
                        await _insert_vocab_hit(
                            cur, conv_uuid, shadow_message_id, channel, term, category
                        )

            await _mark_validated(cur, shadow_message_id)
        await conn.commit()
