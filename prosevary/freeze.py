"""
Freeze-term extraction: tokens that must survive any candidate rewrite.

Checks are multiset-aware: protected content must appear the same number of
times in the candidate as in the original. Presence-only checks allowed a
rewrite to drop one of two ``LLVM`` tokens or one of two identical code spans.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


# Paths and home-relative roots used in this series
_PATHISH = re.compile(
    r"(?:~/[\w./+-]+|/Users/[\w./+-]+|/(?:tmp|var|usr|home)/[\w./+-]+)"
)
# Bare hex-ish commit SHAs (7–40 hex): a digit, or 8+ characters.
#
# Requiring a digit alone kept English words like "defaced" from freezing, but
# also dropped the all-letter sentinels this corpus is full of — deadbeef,
# cafebabe, feedface were frozen before and would not have been. The system
# dictionary has only two words that are valid 7+ char hex (deedeed,
# fabaceae), so the length arm costs almost nothing and buys back the
# constants a compiler/linker book actually quotes.
_SHA = re.compile(r"\b(?=[0-9a-f]{8,}\b|[0-9a-f]*\d)[0-9a-f]{7,40}\b")
# ALL-CAPS tech tokens of length >= 2 (DBT, LLVM, MMIO, W^X is special).
# Must end on an alphanumeric: the old class allowed a trailing separator, so
# `MMIO-based` yielded the term `MMIO-`, which then counted zero against its
# own source text and was silently skipped by check().
_SHOUT = re.compile(r"\b[A-Z][A-Z0-9](?:[A-Z0-9+^_-]*[A-Z0-9])?\b")
# SLOW-32 and similar product names
_PRODUCT = re.compile(r"\bSLOW-?\d+\b", re.IGNORECASE)
# Structural Markdown/Pandoc inline forms: freeze the whole match so a rewrite
# cannot drop a destination, image target, citation, attribute, or footnote ref.
# Destination-aware patterns keep the full construct, not just the label text.
# Label may nest one level of brackets so an image inside a link
# (`[![alt](img)](url)`) is captured whole rather than as a broken fragment.
_LABEL = r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]"
# Destination may contain one level of balanced parens, as Wikipedia URLs do
# (`.../Foo_(bar)`); `[^)]+` stopped at the inner paren and left the trailing
# `)` outside the frozen span, where a rewrite could drop it.
_DEST = r"\((?:[^()\s]|\([^()]*\))*(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)"
_INLINE_IMAGE = re.compile(r"!" + _LABEL + _DEST)
_INLINE_LINK = re.compile(r"(?<!!)" + _LABEL + _DEST)
_INLINE_REF_LINK = re.compile(r"(?<!!)" + _LABEL + r"\[[^\]]*\]")
# Shortcut reference link `[foo]` — no destination, no second bracket pair.
# Its definition is frozen as a block, so leaving the label rewritable would
# break the link and orphan a definition promised to stay byte-identical.
_SHORTCUT_REF = re.compile(r"(?<!!)\[[^\]\n]+\](?![\(\[:])")
_AUTOLINK = re.compile(r"<https?://[^>\s]+>|<mailto:[^>\s]+>", re.IGNORECASE)
# Raw inline HTML tags: <b>, </b>, <img src=…>, <br/>. Only the tag is frozen,
# never the text it wraps, so prose inside <b>…</b> can still vary while the
# markup around it cannot be dropped or reworded.
#
# The tag name must be followed by whitespace, "/" or ">", which keeps this
# from also matching an autolink like <https://x> (":" follows the name there)
# and from firing on comparisons such as "a < b".
_INLINE_HTML_TAG = re.compile(r"</?[a-zA-Z][\w-]*(?:\s[^<>]*)?/?>")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_CITATION = re.compile(r"(?<![A-Za-z0-9])@[\w:-]+")
_PANDOC_ATTR = re.compile(r"\{[#.][^}]*\}")
# Glossary / shout tokens that look like identifiers: match on token boundaries
# so "run" does not freeze inside "runtime".
_IDENT_TERM = re.compile(r"^[\w][\w'-]*$", re.UNICODE)


@dataclass
class FreezeSet:
    """Terms and spans that must survive unchanged in an accepted candidate."""

    terms: Set[str] = field(default_factory=set)
    # Exact substrings extracted from *this sentence* (code spans, paths, …).
    # A list, not a set: each occurrence is protected separately.
    spans: List[str] = field(default_factory=list)

    def check(self, original: str, candidate: str) -> Optional[str]:
        """
        Return None if candidate preserves freezes, else a short reason string.

        Multiset rule: protected content must appear in the candidate at least
        as often as in the *original*. Presence-only checks let a rewrite drop
        one of two identical tokens and still pass.

        Two details keep this honest:

        Counts come from `original`, never from the length of `self.spans`.
        Patterns overlap — `_FOOTNOTE_REF` and `_SHORTCUT_REF` both match
        `[^1]`, a shortcut ref is a substring of a full link, `/tmp/x` is a
        prefix of `/tmp/x/y` — so the extraction list holds duplicates and
        nested pieces. Counting those objects made `check(original, original)`
        fail, which rejected *every* candidate for any sentence containing a
        footnote reference and left the run silently doing nothing.

        Counts must match exactly. Dropping a protected token is the loss this
        guards against, but duplicating a code span or link is its own damage,
        so an added occurrence is rejected too.
        """
        for span in {s for s in self.spans if s}:
            need = original.count(span)
            got = candidate.count(span)
            if got != need:
                return f"span {span!r}: need {need}, have {got}"

        for term in self.terms:
            n_orig = count_term_occurrences(original, term)
            if n_orig == 0:
                # Extraction and counting disagreed about this term. Never
                # skip silently — that is how a droppable `LLVM's` slipped
                # through. Fall back to substring counts so protection holds
                # even when the two rules diverge.
                if term in original and candidate.count(term) != original.count(term):
                    return (
                        f"term {term!r}: need {original.count(term)}, "
                        f"have {candidate.count(term)}"
                    )
                continue
            n_cand = count_term_occurrences(candidate, term)
            if n_cand != n_orig:
                return f"term {term!r}: need {n_orig}, have {n_cand}"
        return None


def count_term_occurrences(text: str, term: str) -> int:
    """Count protected term occurrences under glossary boundary rules."""
    if not term:
        return 0
    return len(list(_term_finditer(text, term)))


def _term_finditer(text: str, term: str):
    """
    Yield match objects for term in text.

    Identifier-like terms (letters/digits/underscore/hyphen/apostrophe) use
    token boundaries so a glossary entry `run` does not match inside `runtime`.
    Multi-word or punctuated terms match as exact substrings (case-sensitive).
    """
    if _IDENT_TERM.match(term):
        # Only word characters block a match, which is what the extraction
        # patterns already use. Treating `'` as a boundary made `LLVM` count
        # zero inside `LLVM's` — the term was extracted, counted 0, and got
        # skipped, so a candidate could drop it entirely. Possessives are
        # everywhere in this corpus.
        #
        # `-` stays permissive, so `run` still matches inside `re-run`. That
        # over-freezes (a paraphrase dropping only the hyphen is rejected),
        # which is the safe direction; making `-` a boundary instead would
        # lose `MMIO` inside `MMIO-based`, which is the unsafe one.
        #
        # Inflections are deliberately *not* matched: `relocation` does not
        # cover `relocations`. Use the glossary's `aliases` for those, so the
        # freeze set stays something an editor declares rather than something
        # this regex guesses.
        pat = re.compile(
            r"(?<!\w)" + re.escape(term) + r"(?!\w)",
            re.UNICODE,
        )
        return pat.finditer(text)
    return re.finditer(re.escape(term), text)


def extract_inline_code_spans(text: str) -> List[str]:
    """
    Inline code spans with matching backtick-run lengths (CommonMark-style).

    An opener of n backticks closes only on a later run of exactly n backticks.
    Shorter or longer runs inside the content do not close the span. The old
    regex `` `+[^`]+`+ `` could not express that and mis-parsed nested ticks.
    """
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "`":
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        open_len = j - i
        # Scan for a closer of the same length.
        k = j
        found = False
        while k < n:
            if text[k] != "`":
                k += 1
                continue
            end = k
            while end < n and text[end] == "`":
                end += 1
            close_len = end - k
            if close_len == open_len:
                out.append(text[i:end])
                i = end
                found = True
                break
            # Different length — content, keep scanning past this run.
            k = end
        if not found:
            # Unclosed opener: skip it so we do not freeze a lone tick run.
            i = j
    return out


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
    for span in extract_inline_code_spans(text):
        fs.spans.append(span)
    # Structural inlines before looser token patterns so destinations stay whole.
    for pat in (
        _INLINE_IMAGE,
        _INLINE_LINK,
        _INLINE_REF_LINK,
        _AUTOLINK,
        _INLINE_HTML_TAG,
        _FOOTNOTE_REF,
        _PANDOC_ATTR,
        _CITATION,
        # Last: only labels no earlier pattern already claimed as a full
        # link, image, ref-link, or footnote reference.
        _SHORTCUT_REF,
    ):
        for m in pat.finditer(text):
            fs.spans.append(m.group(0))
    for m in _PATHISH.finditer(text):
        fs.spans.append(m.group(0))
    for m in _SHA.finditer(text):
        fs.spans.append(m.group(0))
    for m in _PRODUCT.finditer(text):
        fs.spans.append(m.group(0))

    # Glossary: only forms that appear on token boundaries (case-sensitive).
    for term in glossary:
        if term and count_term_occurrences(text, term) > 0:
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
