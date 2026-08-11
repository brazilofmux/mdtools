"""
Candidate generation: synonym swaps (offline) + LLM paraphrase (online).
"""

from __future__ import annotations

import random
import re
from typing import List, Optional, Set

from .freeze import FreezeSet
from .llm import Generator
from .store import Store

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def synonym_candidates(
    sentence: str,
    store: Store,
    freeze: FreezeSet,
    k: int = 4,
    rng: Optional[random.Random] = None,
) -> List[str]:
    """
    Produce up to k variants by replacing 1–2 non-frozen content words
    with curated synonyms. Deterministic if rng is seeded.
    """
    rng = rng or random.Random()
    protected: Set[str] = set(freeze.terms) | set(freeze.spans)

    # Word matches with positions
    # Character ranges covered by freeze spans (code, paths, etc.)
    span_ranges = []
    for span in freeze.spans:
        start = 0
        while True:
            idx = sentence.find(span, start)
            if idx < 0:
                break
            span_ranges.append((idx, idx + len(span)))
            start = idx + len(span)

    def _in_span(a: int, b: int) -> bool:
        return any(a >= s and b <= e for s, e in span_ranges)

    matches = list(_WORD.finditer(sentence))
    replaceable = []
    for m in matches:
        w = m.group(0)
        if w in protected or _in_span(m.start(), m.end()):
            continue
        if any(w == t or w in t for t in freeze.terms):
            # whole glossary term or shout-token — do not swap
            if w in freeze.terms:
                continue
        syns = store.synonyms_for(w.lower())
        if syns:
            replaceable.append((m, syns))

    if not replaceable:
        return []

    out: List[str] = []
    seen = {sentence}
    attempts = k * 6
    while len(out) < k and attempts > 0:
        attempts -= 1
        n_repl = 1 if len(replaceable) == 1 else rng.choice([1, 1, 2])
        picks = rng.sample(replaceable, k=min(n_repl, len(replaceable)))
        # Apply from right to left so offsets stay valid
        picks_sorted = sorted(picks, key=lambda p: p[0].start(), reverse=True)
        new = sentence
        for m, syns in picks_sorted:
            syn = rng.choice(syns)
            syn = _match_case(m.group(0), syn)
            new = new[: m.start()] + syn + new[m.end() :]
        if new not in seen:
            seen.add(new)
            out.append(new)
    return out


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        # title-ish
        return replacement[:1].upper() + replacement[1:]
    return replacement


def all_candidates(
    sentence: str,
    store: Store,
    freeze: FreezeSet,
    generator: Generator,
    k: int = 4,
    rng: Optional[random.Random] = None,
) -> List[str]:
    """LLM candidates first (if any), then fill with synonym swaps."""
    freeze_list = sorted(freeze.terms | set(freeze.spans))
    llm = generator.paraphrase(sentence, freeze_list, k=k)
    need = max(0, k - len(llm))
    syn = synonym_candidates(sentence, store, freeze, k=need or k, rng=rng) if need or not llm else []
    # Prefer LLM, then synonym; dedupe
    out: List[str] = []
    seen = {sentence}
    for c in llm + syn:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= k:
            break
    return out
