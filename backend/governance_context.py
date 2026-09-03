"""
Contexto de gobernanza compartido (REFORMAS Fase 2 SP3).

Antes vivía como shadow_validation._validation_context(). Se mueve acá
porque chat.py también lo necesita (para construir el snapshot del turno,
spec §5.1) y chat.py NO puede importar shadow_validation a nivel de módulo:
shadow_validation importa `from api.chat import ContractResult` -- ciclo.

Config estática cacheada por proceso, mismo criterio que _load_config() en
chat.py (Lección operativa #6, jax-platform/CLAUDE.md): un cambio real
requiere reiniciar el proceso. Consecuencia declarada en el spec §9.4: el
snapshot y el resolver de CAPABILITY_AVAILABLE leen el MISMO objeto, así que
FACT_MISMATCH no puede dispararse por drift para ese predicado hoy.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

JAX_REPO = Path(os.getenv("JAX_REPO_PATH", os.path.expanduser("~/jax")))
if str(JAX_REPO) not in sys.path:
    sys.path.insert(0, str(JAX_REPO))
if str(JAX_REPO / "policy" / "governance") not in sys.path:
    sys.path.insert(0, str(JAX_REPO / "policy" / "governance"))

import loaders as governance_loaders  # noqa: E402
import validator as governance_validator  # noqa: E402


@lru_cache(maxsize=1)
def validation_context():
    vocabulary = governance_loaders.load_vocabulary()
    ctx = governance_validator.load_validation_context(JAX_REPO, vocabulary.config_paths)
    predicates = governance_loaders.load_predicates()
    return ctx, predicates, vocabulary.term_categories
