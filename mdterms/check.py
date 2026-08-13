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
        # severity / confidence / explanation are issue #12's edit model, so
        # `mdfix --apply-edits --diff` can show why a span is being claimed.
        #
        # Confidence is always high and that is not a shrug: a forbidden
        # spelling is an exact string the glossary named, matched on word
        # boundaries. There is no nearest-neighbour step here to be unsure
        # about — the uncertain cases are the *protected* ones, and those are
        # never turned into edits at all.
        return {
            "start": self.start,
            "end": self.end,
            "replacement": self.expected,
            "rule": self.rule,
            "expect": self.found,
            "severity": self.severity,
            "confidence": "high",
            "explanation": f"the glossary prefers {self.expected!r}",
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


def _ends_as_words(head: str, wanted: str) -> bool:
    """True if `head` ends with `wanted` as whole words, not a suffix."""
    if not head.endswith(wanted):
        return False
    if len(head) == len(wanted):
        return True
    return not head[len(head) - len(wanted) - 1].isalnum()


def _expansion_then_term(text: str, start: int, expansion: str,
                         term: str, case_sensitive: bool) -> bool:
    """True if `text[start:]` is `expansion (TERM)` (this match starts it)."""
    i = start
    n = len(text)
    for word in expansion.split():
        while i < n and text[i].isspace():
            i += 1
        if text[i:i + len(word)].casefold() != word.casefold():
            return False
        i += len(word)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "(":
        return False
    i += 1
    got = text[i:i + len(term)]
    if case_sensitive:
        if got != term:
            return False
    elif got.casefold() != term.casefold():
        return False
    i += len(term)
    return i < n and text[i] == ")"


def _introduces(text: str, start: int, end: int, expansion: str,
                term: str, case_sensitive: bool) -> bool:
    """
    Is the occurrence at [start, end) part of an introduction?

    Two shapes, and only two:

        intermediate representation (IR)      expansion, then the term
        IR (intermediate representation)      the term, then the expansion

    The first match of the term may be a word *inside* the expansion
    (YAML / YAML Ain't Markup Language). That still counts as shape 1.
    """
    wanted = " ".join(expansion.split()).casefold()

    before = text[:start].rstrip()
    if before.endswith("("):
        head = " ".join(before[:-1].split()).casefold()
        if _ends_as_words(head, wanted):
            return True

    after = text[end:].lstrip()
    if after.startswith("("):
        close = after.find(")")
        if close > 0:
            inner = " ".join(after[1:close].split()).casefold()
            if inner == wanted:
                return True

    if _expansion_then_term(text, start, expansion, term, case_sensitive):
        return True
    return False


def scan(path: Path, terms: Sequence[Term],
         mdfix: str | None = None) -> List[Finding]:
    """Every terminology violation in the prose of `path`."""
    data = path.read_bytes()
    findings: List[Finding] = []
    terms = [t for t in terms if t.applies_to(path)]

    # First prose use of each term needing an introduction, in document
    # order, with whether that use introduced it. Collected during the walk
    # and judged after, because "first" is only knowable once the walk ends.
    first_use: dict = {}

    for record in raw_records([path], mdfix):
        if record.get("kind") not in PROSE_KINDS:
            continue
        chunk = data[record["start"]:record["end"]].decode("utf-8", "replace")
        protected = _protected_spans(chunk)

        for term in terms:
            if term.expansion and term.term not in first_use:
                for match in _word_pattern(
                        term.term, term.case_sensitive).finditer(chunk):
                    # A term inside inline code or a URL is not prose use, so
                    # it neither counts as a first use nor introduces one.
                    if _in_span(match.start(), protected):
                        continue
                    prefix = chunk[:match.start()].encode("utf-8")
                    first_use[term.term] = (
                        term,
                        record["start"] + len(prefix),
                        len(match.group(0).encode("utf-8")),
                        record["line"] + chunk[:match.start()].count("\n"),
                        _introduces(chunk, match.start(), match.end(),
                                    term.expansion, term.term,
                                    term.case_sensitive),
                    )
                    break

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
    for term, start, length, line, introduced in first_use.values():
        if introduced:
            continue
        findings.append(Finding(
            path=str(path),
            rule="terms.undefined-acronym",
            severity="warning",
            line=line,
            start=start,
            end=start + length,
            found=term.term,
            expected=term.term,
            # Never auto-fixed: introducing a term is a wording decision.
            fixable=False,
            message=(f"{term.term!r} is used before it is introduced; "
                     f"write {term.expansion + ' (' + term.term + ')'!r} "
                     f"at first use"),
        ))

    findings.sort(key=lambda f: (f.start, f.end))
    return findings


def usage(paths: Sequence[Path], terms: Sequence[Term],
          mdfix: str | None = None) -> List[dict]:
    """
    Which documents use which terms, and which introduce them (#16).

    The consistency question a per-file report cannot answer: a term
    introduced in one chapter and assumed in the next reads fine in isolation
    and badly in order. This says where each term appears and where its
    expansion was actually written out, so the gap is visible at a glance.

    Counts prose uses only — a term in a code span is not the reader meeting
    the word.
    """
    # One IR walk per file (not per term). introduced_in follows scan():
    # only the first prose use may count as the introduction.
    by_term = {
        t.term: {"used_in": [], "introduced_in": [], "expansion": t.expansion}
        for t in terms
    }
    for path in paths:
        applicable = [t for t in terms if t.applies_to(path)]
        if not applicable:
            continue
        data = path.read_bytes()
        hits = {t.term: 0 for t in applicable}
        first: dict = {}
        for record in raw_records([path], mdfix):
            if record.get("kind") not in PROSE_KINDS:
                continue
            chunk = data[record["start"]:record["end"]].decode(
                "utf-8", "replace")
            protected = _protected_spans(chunk)
            for term in applicable:
                pattern = _word_pattern(term.term, term.case_sensitive)
                for match in pattern.finditer(chunk):
                    if _in_span(match.start(), protected):
                        continue
                    hits[term.term] += 1
                    if term.term not in first:
                        first[term.term] = bool(
                            term.expansion
                            and _introduces(chunk, match.start(), match.end(),
                                            term.expansion, term.term,
                                            term.case_sensitive))
        for term in applicable:
            if hits[term.term]:
                by_term[term.term]["used_in"].append(str(path))
            if first.get(term.term):
                by_term[term.term]["introduced_in"].append(str(path))
    return [
        {"kind": "term-usage", "term": name,
         "expansion": row["expansion"],
         "used_in": row["used_in"],
         "introduced_in": row["introduced_in"]}
        for name, row in by_term.items()
    ]


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
