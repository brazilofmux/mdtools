"""
Editorial metrics for prosevary runs.

Advisory only. Nothing here feeds the accept path — the gates stay
freeze + tau + judge, and a human still reads the diff. These numbers exist
so tau, k, and the generator prompt can be tuned against something other
than vibes.

Framing matters. Repetition and lexical variety move in the direction a
paraphraser pushes *by construction*: swapping "however" for "even so"
raises type-token ratio whether or not the sentence got better. Optimizing
on them rewards churn, so they are reported as telemetry (direction NEUTRAL).

The load-bearing metrics are the ones where a regression is unambiguous:

  shape_stdev / opener_variety
      LLM paraphrase homogenizes rhythm — everything drifts toward the same
      mid-length declarative. A *drop* here is the characteristic damage.
  readability
      A large swing either way means the register moved.
  semantic
      Mean cosine over accepted rewrites. Only meaningful on a semantic
      embedder; reported as n/a on the hash fallback rather than as a
      number that looks like evidence.

Prose only: text is run through segment.parse() so fenced code, tables, and
blockquotes never reach the counters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .segment import parse

HIGHER_BETTER = "higher-better"
LOWER_BETTER = "lower-better"
NEUTRAL = "neutral"

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
# Window for moving-average TTR. Plain TTR is length-sensitive, which would
# make any length change look like a vocabulary change.
_MATTR_WINDOW = 50


@dataclass
class Metric:
    name: str
    before: Optional[float]
    after: Optional[float]
    direction: str
    note: str = ""

    @property
    def delta(self) -> Optional[float]:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before

    @property
    def regressed(self) -> bool:
        d = self.delta
        if d is None or self.direction == NEUTRAL:
            return False
        if abs(d) < 1e-9:
            return False
        return d < 0 if self.direction == HIGHER_BETTER else d > 0


def prose_sentences(text: str) -> List[str]:
    """Sentences from paragraph regions only — never code, tables, or quotes."""
    doc = parse(text)
    return [s.text for reg in doc.regions for s in reg.sentences]


def _words(text: str) -> List[str]:
    return _WORD.findall(text)


def _syllables(word: str) -> int:
    """Vowel-group heuristic. Approximate by design; only used for Flesch."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    # Silent terminal 'e' ("shape"), but not "-le" ("table") or "-ee" ("free").
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee", "ye", "oe")):
        n -= 1
    return max(1, n)


def repeated_ngram_rate(words: Sequence[str], n: int = 3) -> Optional[float]:
    """Fraction of n-gram instances that are not the first use of that n-gram."""
    if len(words) < n:
        return None
    grams = [tuple(w.lower() for w in words[i : i + n]) for i in range(len(words) - n + 1)]
    return (len(grams) - len(set(grams))) / len(grams)


def mattr(words: Sequence[str], window: int = _MATTR_WINDOW) -> Optional[float]:
    """Moving-average type-token ratio; falls back to plain TTR when short."""
    lower = [w.lower() for w in words]
    if not lower:
        return None
    if len(lower) < window:
        return len(set(lower)) / len(lower)
    ratios = [
        len(set(lower[i : i + window])) / window
        for i in range(len(lower) - window + 1)
    ]
    return sum(ratios) / len(ratios)


def _stdev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def flesch_reading_ease(sentences: Sequence[str]) -> Optional[float]:
    words: List[str] = []
    for s in sentences:
        words.extend(_words(s))
    if not sentences or not words:
        return None
    syllables = sum(_syllables(w) for w in words)
    return (
        206.835
        - 1.015 * (len(words) / len(sentences))
        - 84.6 * (syllables / len(words))
    )


def compute(text: str) -> Dict[str, Optional[float]]:
    """Raw metric values for one document."""
    sentences = prose_sentences(text)
    words: List[str] = []
    lengths: List[float] = []
    openers: List[str] = []
    for s in sentences:
        w = _words(s)
        if not w:
            continue
        words.extend(w)
        lengths.append(float(len(w)))
        openers.append(w[0].lower())

    return {
        "sentences": float(len(lengths)) if lengths else None,
        "words": float(len(words)) if words else None,
        "repetition": repeated_ngram_rate(words),
        "lexical_variety": mattr(words),
        "shape_stdev": _stdev(lengths),
        "mean_length": (sum(lengths) / len(lengths)) if lengths else None,
        "opener_variety": (len(set(openers)) / len(openers)) if openers else None,
        "readability": flesch_reading_ease(sentences),
    }


def compare(
    before_text: str,
    after_text: str,
    *,
    cosines: Optional[Sequence[float]] = None,
    semantic: bool = True,
    embed_model: str = "",
) -> List[Metric]:
    """Before/after metrics for a run. cosines are the accepted rewrites'."""
    b = compute(before_text)
    a = compute(after_text)

    metrics = [
        Metric("repetition (3-gram)", b["repetition"], a["repetition"], NEUTRAL,
               "telemetry: paraphrase lowers this by construction"),
        Metric("lexical variety (MATTR)", b["lexical_variety"], a["lexical_variety"],
               NEUTRAL, "telemetry: gameable by churn"),
        Metric("sentence shape (stdev)", b["shape_stdev"], a["shape_stdev"],
               HIGHER_BETTER, "a drop means rhythm homogenized"),
        Metric("opener variety", b["opener_variety"], a["opener_variety"],
               HIGHER_BETTER, "a drop means sentences start alike"),
        Metric("mean sentence length", b["mean_length"], a["mean_length"], NEUTRAL),
        Metric("readability (Flesch)", b["readability"], a["readability"],
               NEUTRAL, "large swing either way = register moved"),
    ]

    if not semantic:
        metrics.append(
            Metric("semantic preservation", None, None, HIGHER_BETTER,
                   f"n/a — {embed_model or 'this embedder'} is not semantic")
        )
    elif cosines:
        mean_cos = sum(cosines) / len(cosines)
        metrics.append(
            Metric("semantic preservation", None, mean_cos, HIGHER_BETTER,
                   f"mean cosine over {len(cosines)} accepted")
        )
    else:
        metrics.append(
            Metric("semantic preservation", None, None, HIGHER_BETTER,
                   "n/a — nothing accepted")
        )
    return metrics


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.4f}" if abs(v) < 100 else f"{v:.1f}"


def format_report(metrics: Sequence[Metric], title: str = "") -> str:
    lines: List[str] = []
    head = "editorial metrics" + (f" — {title}" if title else "")
    lines.append(head)
    lines.append("  " + "-" * (len(head) + 2))
    width = max(len(m.name) for m in metrics)
    for m in metrics:
        d = m.delta
        if d is None:
            delta = "     —"
        else:
            delta = f"{d:+.4f}" if abs(d) < 100 else f"{d:+.1f}"
        flag = "  <-- REGRESSION" if m.regressed else ""
        lines.append(
            f"  {m.name:<{width}}  {_fmt(m.before):>9} -> {_fmt(m.after):>9}"
            f"  {delta:>9}{flag}"
        )
        if m.note:
            lines.append(f"  {'':<{width}}  ({m.note})")
    regressions = [m for m in metrics if m.regressed]
    if regressions:
        lines.append(
            f"  {len(regressions)} regression(s) — inspect the diff before committing."
        )
    return "\n".join(lines)
