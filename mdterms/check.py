"""
Find terminology violations in prose, and turn them into edits.

**This module contains no Markdown grammar.** Prose spans come from
`mdfix --emit-ir`, and fixes go back as byte-span edits for
`mdfix --apply-edits`. mdterms never sees a list marker or a fence.

Only prose is searched. A forbidden spelling inside a code block, a table
cell, a link definition or front matter is left alone, because the IR says
those are not paragraphs — which is the whole reason the boundary exists.

Within a prose span, matches inside inline code, links, images, autolinks
and raw HTML tags are reported but never auto-fixed: rewriting those would
change literals or destinations. That is the one inline concession, scoped
here until the IR carries inline records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from mdquery.ir import raw_records

from .glossary import Term

# Kinds whose text is prose a consumer may rewrite. `paragraph` covers both
# top-level paragraphs and, since schema 3, prose nested in a list item.
#
# Headings are included: a heading is where a term is most visible, and
# leaving it out would pass a document titled with the forbidden spelling.
# Note that fixing one changes its anchor, so links to it must be updated —
# mdterms reports the finding either way and only rewrites on --edits.
#
# Block quotes are not searched: schema 3 does not emit nested quote prose,
# and the whole quote is one opaque IR span.
PROSE_KINDS = frozenset({"paragraph", "heading"})

# Structural inlines whose interiors must not be auto-fixed (destinations,
# markup). Same shapes prosevary freezes; listed here so mdterms does not
# depend on that package for a write path.
_LABEL = r"\[(?:[^\[\]]|\[[^\[\]]*\])*\]"
_DEST = r"\((?:[^()\s]|\([^()]*\))*(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)"
_STRUCTURAL_INLINE = (
    re.compile(r"!" + _LABEL + _DEST),
    re.compile(r"(?<!!)" + _LABEL + _DEST),
    re.compile(r"(?<!!)" + _LABEL + r"\[[^\]]*\]"),
    re.compile(r"<https?://[^>\s]+>|<mailto:[^>\s]+>", re.IGNORECASE),
    re.compile(r"</?[a-zA-Z][\w-]*(?:\s[^<>]*)?/?>"),
)


@dataclass
class Finding:
    path: str
    rule: str
    severity: str
    line: int
    start: int          # byte offset into the file
    end: int
    found: str
    expected: str
    fixable: bool
    message: str

    def to_diagnostic(self) -> dict:
        return {
            "kind": "diagnostic",
            "path": self.path,
            "rule": self.rule,
            "severity": self.severity,
            "line": self.line,
            "start": self.start,
            "end": self.end,
            "message": self.message,
        }

    def to_edit(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "replacement": self.expected,
            "rule": self.rule,
            "expect": self.found,
        }


def _word_pattern(spelling: str, case_sensitive: bool) -> re.Pattern:
    """
    Match a spelling on word boundaries, so `IR` does not match `IRQ`.

    `\\b` is wrong at a non-word edge — a term like `C++` or `.NET` ends or
    begins with punctuation — so the boundary is asserted only on the sides
    where the spelling itself is word-ish.
    """
    if not spelling:
        raise ValueError("empty spelling")
    left = r"(?<![\w-])" if re.match(r"[\w]", spelling[0]) else ""
    right = r"(?![\w-])" if re.search(r"[\w]$", spelling) else ""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(left + re.escape(spelling) + right, flags)


def _code_spans(text: str) -> List[tuple]:
    """
    Backtick-delimited runs with matching opener/closer lengths (CommonMark).

    An unclosed opener is skipped so later well-formed spans are still found.
    Matches inside these runs are reported and never auto-fixed.
    """
    spans: List[tuple] = []
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
        k = j
        found = False
        while k < n:
            if text[k] != "`":
                k += 1
                continue
            end = k
            while end < n and text[end] == "`":
                end += 1
            if end - k == open_len:
                spans.append((i, end))
                i = end
                found = True
                break
            k = end
        if not found:
            i = j
    return spans


def _protected_spans(text: str) -> List[tuple]:
    """Code spans plus link/image/autolink/HTML — report-only interiors."""
    spans = list(_code_spans(text))
    for pat in _STRUCTURAL_INLINE:
        for match in pat.finditer(text):
            spans.append((match.start(), match.end()))
    spans.sort()
    merged: List[tuple] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
            continue
        merged.append((start, end))
    return merged


def _in_span(pos: int, spans: Sequence[tuple]) -> bool:
    return any(a <= pos < b for a, b in spans)


def scan(path: Path, terms: Sequence[Term],
         mdfix: str | None = None) -> List[Finding]:
    """Every terminology violation in the prose of `path`."""
    data = path.read_bytes()
    findings: List[Finding] = []

    for record in raw_records([path], mdfix):
        if record.get("kind") not in PROSE_KINDS:
            continue
        chunk = data[record["start"]:record["end"]].decode("utf-8", "replace")
        protected = _protected_spans(chunk)

        for term in terms:
            for bad in term.forbidden:
                for match in _word_pattern(bad, term.case_sensitive).finditer(chunk):
                    # Byte offsets, not character offsets: the IR speaks bytes
                    # and so does the applier.
                    prefix = chunk[:match.start()].encode("utf-8")
                    found = match.group(0)
                    start = record["start"] + len(prefix)
                    fixable = not _in_span(match.start(), protected)
                    findings.append(Finding(
                        path=str(path),
                        rule="terms.forbidden",
                        severity="warning",
                        line=record["line"] + chunk[:match.start()].count("\n"),
                        start=start,
                        end=start + len(found.encode("utf-8")),
                        found=found,
                        expected=term.term,
                        fixable=fixable,
                        message=(f"{found!r} should be {term.term!r}"
                                 + ("" if fixable else
                                    " (inside a protected span; "
                                    "not fixed automatically)")),
                    ))
    findings.sort(key=lambda f: (f.start, f.end))
    return findings


def edits_for(findings: Iterable[Finding]) -> List[dict]:
    """
    Edits for findings that can be applied unambiguously.

    Any fixable finding that overlaps another fixable finding is dropped with
    its whole cluster — not resolved by keeping the first. Order-dependent
    winners would make the result depend on the glossary, not the document.
    """
    fixable = sorted(
        (f for f in findings if f.fixable),
        key=lambda f: (f.start, f.end),
    )
    drop: set[int] = set()
    for i, a in enumerate(fixable):
        for j in range(i + 1, len(fixable)):
            b = fixable[j]
            if b.start >= a.end:
                break
            if b.start < a.end:
                drop.add(i)
                drop.add(j)
    return [f.to_edit() for i, f in enumerate(fixable) if i not in drop]
