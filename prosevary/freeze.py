"""
Freeze-term extraction: tokens that must survive any candidate rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


# Inline code, either `...` or ``...``
_INLINE_CODE = re.compile(r"`+[^`]+`+")
# Paths and home-relative roots used in this series
_PATHISH = re.compile(
    r"(?:~/[\w./+-]+|/Users/[\w./+-]+|/(?:tmp|var|usr|home)/[\w./+-]+)"
)
# Bare hex-ish commit SHAs (7–40 hex)
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
# ALL-CAPS tech tokens of length >= 2 (DBT, LLVM, MMIO, W^X is special)
_SHOUT = re.compile(r"\b[A-Z][A-Z0-9][A-Z0-9+^_-]*\b")
# SLOW-32 and similar product names
_PRODUCT = re.compile(r"\bSLOW-?\d+\b", re.IGNORECASE)


@dataclass
class FreezeSet:
    """Terms that must appear unchanged in an accepted candidate."""

    terms: Set[str] = field(default_factory=set)
    # Exact substrings extracted from *this sentence* (code spans, paths)
    spans: List[str] = field(default_factory=list)

    def check(self, original: str, candidate: str) -> Optional[str]:
        """
        Return None if candidate preserves freezes, else a short reason string.
        """
        for span in self.spans:
            if span and span not in candidate:
                return f"missing span: {span!r}"
        # Case-sensitive for spans; glossary terms allow the form present in original
        for term in self.terms:
            if term in original and term not in candidate:
                return f"missing term: {term!r}"
        return None


def load_glossary_terms(path: Path) -> Set[str]:
    """Load term + aliases from glossary_terms.yaml."""
    if not path.is_file():
        return set()
    if yaml is None:
        raise RuntimeError("PyYAML required to load glossary_terms.yaml (pip install pyyaml)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: Set[str] = set()
    for entry in data.get("terms") or []:
        if not isinstance(entry, dict):
            continue
        term = entry.get("term")
        if term:
            out.add(str(term))
        for alias in entry.get("aliases") or []:
            out.add(str(alias))
    return out


def sentence_freeze(text: str, glossary: Iterable[str]) -> FreezeSet:
    """Build a FreezeSet for one sentence against a global glossary."""
    fs = FreezeSet()
    for m in _INLINE_CODE.finditer(text):
        fs.spans.append(m.group(0))
    for m in _PATHISH.finditer(text):
        fs.spans.append(m.group(0))
    for m in _SHA.finditer(text):
        fs.spans.append(m.group(0))
    for m in _PRODUCT.finditer(text):
        fs.spans.append(m.group(0))

    # Glossary terms present as whole-ish substrings (case-sensitive first)
    for term in glossary:
        if term and term in text:
            fs.terms.add(term)

    # Shout-case tokens often load-bearing even if not in glossary yet
    for m in _SHOUT.finditer(text):
        tok = m.group(0)
        if tok not in {"I", "A", "OK"}:  # tiny allowlist
            fs.terms.add(tok)

    return fs


def default_glossary_path() -> Optional[Path]:
    """
    Resolve a glossary file if one is obvious.

    Order:
      1. PROSEVARY_GLOSSARY env
      2. ./glossary_terms.yaml (cwd)
      3. Walk parents of cwd looking for glossary_terms.yaml
    Returns None if nothing found (caller treats as empty freeze set).
    """
    import os
    env = os.environ.get("PROSEVARY_GLOSSARY")
    if env:
        return Path(env)
    cwd = Path.cwd()
    candidate = cwd / "glossary_terms.yaml"
    if candidate.is_file():
        return candidate
    for parent in cwd.parents:
        candidate = parent / "glossary_terms.yaml"
        if candidate.is_file():
            return candidate
        # stop at filesystem root-ish
        if parent == parent.parent:
            break
    return None
