"""
Generate-then-gate pipeline over a Document.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .candidates import all_candidates
from .embed import Embedder, cosine
from .freeze import FreezeSet, load_glossary_terms, sentence_freeze
from .llm import Generator, Judge
from .segment import Document, iter_sentences
from .store import Store


@dataclass
class Decision:
    region_id: int
    sent_index: int
    global_index: int
    original: str
    candidate: Optional[str]
    cosine: Optional[float]
    status: str
    reason: str = ""


@dataclass
class PipelineResult:
    decisions: List[Decision] = field(default_factory=list)
    # (region_id, sent_index) → accepted new text
    replacements: Dict[Tuple[int, int], str] = field(default_factory=dict)
    run_id: Optional[int] = None

    @property
    def accepted(self) -> int:
        return sum(1 for d in self.decisions if d.status == "accepted")

    @property
    def kept(self) -> int:
        return sum(1 for d in self.decisions if d.status == "kept")


def active_gates(embedder: Embedder, judge: Judge) -> List[str]:
    """
    Names of the gates that actually assess a candidate on these backends.

    Offline fallbacks (HashEmbedder, NullJudge) are inert: reporting them as
    though they ran would overstate how much a rewrite was vetted.
    """
    gates = ["freeze"]
    if getattr(embedder, "semantic", True):
        gates.append("tau")
    if getattr(judge, "enforcing", True):
        gates.append("judge")
    return gates


def run_pipeline(
    doc: Document,
    store: Store,
    embedder: Embedder,
    generator: Generator,
    judge: Judge,
    *,
    glossary_terms: Optional[set] = None,
    tau: float = 0.92,
    k: int = 4,
    seed: Optional[int] = None,
    source_path: str = "",
    max_sentences: Optional[int] = None,
    notes: str = "",
) -> PipelineResult:
    rng = random.Random(seed)
    gates = active_gates(embedder, judge)
    passed_reason = "passed " + "+".join(gates)
    # A non-semantic embedder must not gate. Its cosine is a token-overlap
    # score, so it rates a one-word synonym swap ~0.92 and a genuine rewrite
    # ~0.24 — exactly backwards. Still computed and logged as telemetry.
    tau_enforced = "tau" in gates
    glossary = glossary_terms if glossary_terms is not None else set()
    # merge DB freeze terms
    glossary = set(glossary) | set(store.all_freeze_terms())

    run_id = store.start_run(
        source=source_path,
        tau=tau,
        k=k,
        model_gen=generator.model_id,
        model_judge=judge.model_id,
        model_embed=embedder.model_id,
        notes=notes,
    )
    result = PipelineResult(run_id=run_id)
    gidx = 0

    for reg, s_i, sent in iter_sentences(doc):
        if max_sentences is not None and gidx >= max_sentences:
            break
        original = sent.text
        freeze: FreezeSet = sentence_freeze(original, glossary)
        orig_vec = embedder.embed_cached(original, store)

        cands = all_candidates(
            original, store, freeze, generator, k=k, rng=rng
        )
        chosen: Optional[str] = None
        chosen_cos: Optional[float] = None
        last_status = "no-candidate"
        last_reason = "no candidates produced"
        last_cand: Optional[str] = None
        last_cos: Optional[float] = None

        for cand in cands:
            last_cand = cand
            fail = freeze.check(original, cand)
            if fail:
                last_status, last_reason = "reject-freeze", fail
                continue
            cvec = embedder.embed_cached(cand, store)
            cos = cosine(orig_vec, cvec)
            last_cos = cos
            if tau_enforced and cos < tau:
                last_status = "reject-tau"
                last_reason = f"cosine {cos:.4f} < tau {tau}"
                continue
            jr = judge.judge(original, cand)
            if not jr.accept:
                last_status = "reject-judge"
                last_reason = jr.reason or "judge rejected"
                continue
            # accepted
            chosen = cand
            chosen_cos = cos
            break

        if chosen is not None and chosen != original:
            result.replacements[(reg.region_id, s_i)] = chosen
            dec = Decision(
                region_id=reg.region_id,
                sent_index=s_i,
                global_index=gidx,
                original=original,
                candidate=chosen,
                cosine=chosen_cos,
                status="accepted",
                reason=passed_reason,
            )
        else:
            reason = (
                f"all candidates failed ({last_status}: {last_reason})"
                if cands
                else "no candidates produced"
            )
            dec = Decision(
                region_id=reg.region_id,
                sent_index=s_i,
                global_index=gidx,
                original=original,
                candidate=last_cand,
                cosine=last_cos,
                status="kept",
                reason=reason,
            )

        result.decisions.append(dec)
        store.log_decision(
            run_id,
            gidx,
            original,
            dec.candidate,
            dec.cosine,
            dec.status,
            dec.reason,
        )
        gidx += 1

    return result


def load_glossary(path) -> set:
    return load_glossary_terms(path)
