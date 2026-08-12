"""
Find terminology violations in prose, and turn them into edits.

**This module contains no Markdown grammar.** Prose spans come from
`mdfix --emit-ir`, and fixes go back as byte-span edits for
`mdfix --apply-edits`. mdterms never sees a list marker or a fence.

Only prose is searched. A forbidden spelling inside a code block, a table
cell, a link definition or front matter is left alone, because the IR says
those are not paragraphs — which is the whole reason the boundary exists.
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
PROSE_KINDS = frozenset({"paragraph", "heading"})


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
    left = r"(?<![\w-])" if re.match(r"[\w]", spelling[0]) else ""
    right = r"(?![\w-])" if re.search(r"[\w]$", spelling) else ""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(left + re.escape(spelling) + right, flags)


def _code_spans(text: str) -> List[tuple]:
    """
    Backtick-delimited runs within a prose span.

    The one inline concession in this package, and it is a safety measure
    rather than a feature: a term inside `` `SLOW32` `` is a code span, and
    rewriting it would change a literal. Matches inside one are reported and
    never auto-fixed. Proper handling needs inline records in the IR; until
    then this errs toward leaving text alone.
    """
    spans = []
    for match in re.finditer(r"`+", text):
        run = match.group(0)
        closer = text.find(run, match.end())
        if closer < 0:
            break
        spans.append((match.start(), closer + len(run)))
    merged: List[tuple] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
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
        code = _code_spans(chunk)

        for term in terms:
            for bad in term.forbidden:
                for match in _word_pattern(bad, term.case_sensitive).finditer(chunk):
                    # Byte offsets, not character offsets: the IR speaks bytes
                    # and so does the applier.
                    prefix = chunk[:match.start()].encode("utf-8")
                    found = match.group(0)
                    start = record["start"] + len(prefix)
                    fixable = not _in_span(match.start(), code)
                    findings.append(Finding(
                        path=str(path),
                        rule="terms.forbidden",
                        severity="error",
                        line=record["line"] + chunk[:match.start()].count("\n"),
                        start=start,
                        end=start + len(found.encode("utf-8")),
                        found=found,
                        expected=term.term,
                        fixable=fixable,
                        message=(f"{found!r} should be {term.term!r}"
                                 + ("" if fixable else " (inside a code span; "
                                    "not fixed automatically)")),
                    ))
    findings.sort(key=lambda f: f.start)
    return findings


def edits_for(findings: Iterable[Finding]) -> List[dict]:
    """
    Edits for the findings that can be applied unambiguously.

    Overlaps are dropped rather than resolved: the applier refuses an
    overlapping list, and silently picking a winner would make the result
    depend on glossary order.
    """
    out: List[dict] = []
    last_end = -1
    for finding in findings:
        if not finding.fixable or finding.start < last_end:
            continue
        out.append(finding.to_edit())
        last_end = finding.end
    return out
