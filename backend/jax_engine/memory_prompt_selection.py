"""Bounded representative selection over already authorized B9 envelopes.

Selection does not resolve authority, alter memory or confer factual truth.
The existing B9 renderer determines cost and remains the only formatter.
"""
from collections import Counter
from dataclasses import dataclass
from itertools import islice
import os
from typing import Iterable, Mapping

from jax.memory.b9 import MemoryEnvelope, ObjectKind, PromptMemoryContext


@dataclass(frozen=True)
class MemoryPromptLimits:
    candidates: int = 100
    entries: int = 20
    rendered_chars: int = 32000
    facts: int = 10
    decisions: int = 5
    actions: int = 5

    def __post_init__(self):
        for value,maximum in ((self.candidates,100),(self.entries,100),(self.rendered_chars,256000)):
            if type(value) is not int or not 1<=value<=maximum:
                raise ValueError('memory prompt budget outside bounds')
        for quota in (self.facts,self.decisions,self.actions):
            if type(quota) is not int or not 0<=quota<=100:
                raise ValueError('memory prompt quota outside bounds')
        if self.entries>self.candidates or self.facts+self.decisions+self.actions>self.entries:
            raise ValueError('inconsistent memory prompt budgets')


def limits_from_environment(env: Mapping[str,str] | None = None) -> MemoryPromptLimits:
    env=os.environ if env is None else env
    fields=(('candidates','JAX_MEMORY_PROMPT_CANDIDATES',100),
            ('entries','JAX_MEMORY_PROMPT_ENTRIES',20),
            ('rendered_chars','JAX_MEMORY_PROMPT_RENDERED_CHARS',32000),
            ('facts','JAX_MEMORY_PROMPT_FACT_QUOTA',10),
            ('decisions','JAX_MEMORY_PROMPT_DECISION_QUOTA',5),
            ('actions','JAX_MEMORY_PROMPT_ACTION_QUOTA',5))
    values={}
    for field,name,default in fields:
        raw=env.get(name,str(default))
        if not isinstance(raw,str) or not raw.isascii() or not raw.isdigit():
            raise ValueError('invalid memory prompt configuration: '+name)
        values[field]=int(raw)
    return MemoryPromptLimits(**values)


def select_memory_context(envelopes: Iterable[MemoryEnvelope], limits: MemoryPromptLimits) -> PromptMemoryContext:
    candidates=tuple(islice(envelopes,limits.candidates))
    if any(not isinstance(entry,MemoryEnvelope) for entry in candidates):
        raise TypeError('memory selection requires B9 envelopes')
    costs=tuple(len(PromptMemoryContext((entry,)).render()) for entry in candidates)
    selected=set()
    rendered_chars=0
    def admit(index):
        nonlocal rendered_chars
        if index in selected or len(selected)>=limits.entries or costs[index]==0:
            return False
        cost=costs[index]+(2 if selected else 0)
        if rendered_chars+cost>limits.rendered_chars:
            return False
        selected.add(index)
        rendered_chars+=cost
        return True
    # Reserve representative FACT slots before newer adoption categories can
    # consume the entire entry/character budget. Output order is still recency.
    for kind,quota in ((ObjectKind.FACT,limits.facts),
                       (ObjectKind.DECISION_MEMORY,limits.decisions),
                       (ObjectKind.ACTION_ITEM,limits.actions)):
        count=0
        for index,entry in enumerate(candidates):
            if count>=quota:
                break
            if entry.identity.kind is kind and admit(index):
                count+=1
    # Missing categories leave available slots for the newest remaining whole
    # envelopes. Oversized records are omitted, never truncated or rewritten.
    for index in range(len(candidates)):
        admit(index)
    return PromptMemoryContext(tuple(candidates[index] for index in sorted(selected)))


def selected_kind_counts(context: PromptMemoryContext) -> dict[str,int]:
    """Payload-free diagnostics from B9 identity, never inferred from text."""
    return dict(Counter(entry.identity.kind.value for entry in context.entries))
