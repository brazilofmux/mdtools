"""
Build and check the link graph from mdfix IR — no Markdown grammar.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from mdquery.ir import raw_records
from mdquery.slug import assign_slugs


@dataclass
class Target:
    """
    Where a destination sits in the file, so a repair can replace it alone.

    Straight from the IR's `destinationStart` / `destinationEnd`. Locating the
    destination inside a link's span means knowing where its text stops,
    skipping an optional title and honouring escapes — Markdown grammar,
    which lives in mdfix (dialect-policy §2). This is that work already done.

    `None` where the IR emitted no span: an empty destination, or a reference
    form whose destination lives in its definition instead.
    """
    start: int
    end: int
    destination: str


@dataclass
class Link:
    path: Path
    line: int
    start: int
    end: int
    kind: str            # "link" or "image"
    form: str            # inline | autolink | reference | shortcut
    destination: str     # "" for reference forms
    label: str
    text: str = ""       # link text; used for collapsed [text][]
    target: Optional[Target] = None


@dataclass
class Definition:
    line: int
    destination: str
    target: Optional[Target] = None


@dataclass
class Document:
    path: Path
    anchors: List[str] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    definitions: Dict[str, Definition] = field(default_factory=dict)
    footnotes: Dict[str, int] = field(default_factory=dict)
    footnote_refs: List = field(default_factory=list)


@dataclass
class Finding:
    path: str
    rule: str
    severity: str
    line: int
    start: int
    end: int
    message: str
    # Set only when the finding names a destination a repair could replace.
    target: Optional[Target] = None

    def to_diagnostic(self) -> dict:
        return {"kind": "diagnostic", "path": self.path, "rule": self.rule,
                "severity": self.severity, "line": self.line,
                "start": self.start, "end": self.end, "message": self.message}


def _normalize_label(label: str) -> str:
    """CommonMark label: collapse whitespace, then case-fold."""
    return " ".join(label.split()).casefold()


def _normalize_destination(destination: str) -> str:
    """Bare URL if IR still carries <> or a trailing title (defensive)."""
    s = destination.strip()
    if s.startswith("<") and ">" in s:
        inner, _, rest = s[1:].partition(">")
        if not rest.strip() or rest.lstrip()[:1] in "\"'(":
            return inner.strip()
    # Unquoted destination ends at the first unescaped space (title follows).
    out = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
            continue
        if s[i] in " \t":
            break
        out.append(s[i])
        i += 1
    return "".join(out)


def _target(record: dict) -> Optional[Target]:
    """The destination's span, when the IR emitted one."""
    start = record.get("destinationStart")
    end = record.get("destinationEnd")
    if start is None or end is None:
        return None
    return Target(start=start, end=end,
                  destination=record.get("destination", "") or "")


def read(path: Path, mdfix: Optional[str] = None) -> Document:
    doc = Document(path=path)
    headings: List[str] = []
    records = list(raw_records([path], mdfix))
    def_starts = {r["start"] for r in records if r.get("kind") == "footnote_def"}
    for record in records:
        kind = record.get("kind")
        if kind == "heading":
            headings.append(record.get("plain") or record.get("text") or "")
        elif kind in ("link", "image"):
            doc.links.append(Link(
                path=path, line=record["line"],
                start=record["start"], end=record["end"],
                kind=kind, form=record.get("form", "inline"),
                destination=record.get("destination", "") or "",
                label=record.get("label", "") or "",
                text=record.get("text", "") or "",
                target=_target(record),
            ))
        elif kind == "reference_def":
            key = _normalize_label(record.get("label", "") or "")
            if key and key not in doc.definitions:
                dest = _normalize_destination(
                    record.get("destination", "") or "")
                doc.definitions[key] = Definition(
                    line=record["line"], destination=dest,
                    target=_target(record))
        elif kind == "footnote_def":
            doc.footnotes.setdefault(record.get("label", ""), record["line"])
        elif kind == "footnote_ref":
            in_body = record.get("parent") in def_starts
            doc.footnote_refs.append((record.get("label", ""), record["line"],
                                      record["start"], record["end"], in_body))
    doc.anchors = assign_slugs(headings)
    return doc


def _split(destination: str) -> tuple:
    """(target, anchor) with percent-escapes decoded in the anchor."""
    if "#" in destination:
        target, _, anchor = destination.partition("#")
        return target, urllib.parse.unquote(anchor)
    return destination, ""


def _is_external(target: str) -> bool:
    scheme = urllib.parse.urlparse(target).scheme
    return bool(scheme) or target.startswith("//")


def check(docs: Sequence[Document]) -> List[Finding]:
    """Every finding across the given documents, in source order."""
    by_path = {d.path.resolve(): d for d in docs}
    findings: List[Finding] = []

    def add(doc, link, rule, message, severity="error", target=None):
        findings.append(Finding(
            path=str(doc.path), rule=rule, severity=severity,
            line=link.line, start=link.start, end=link.end, message=message,
            target=target))

    def check_destination(doc, link, destination: str, target=None) -> None:
        destination = _normalize_destination(destination)
        if not destination or _is_external(destination):
            return
        path_part, anchor = _split(destination)
        if not path_part:
            if anchor and anchor not in doc.anchors:
                add(doc, link, "links.broken-anchor",
                    f"no heading with anchor #{anchor}", target=target)
            return
        resolved = (doc.path.parent / path_part).resolve()
        if not resolved.exists():
            add(doc, link, "links.missing-file",
                f"{path_part} does not exist", target=target)
            return
        if anchor:
            other = by_path.get(resolved)
            if other is None and resolved.suffix == ".md":
                return   # not in scope; cannot judge its anchors
            if other is not None and anchor not in other.anchors:
                add(doc, link, "links.broken-anchor",
                    f"{path_part} has no anchor #{anchor}", target=target)

    for doc in docs:
        used = set()
        for link in doc.links:
            if link.form in ("reference", "shortcut"):
                # Collapsed [text][]: IR label is empty; CommonMark uses text.
                label = link.label
                if link.form == "reference" and not label:
                    label = link.text
                key = _normalize_label(label)
                used.add(key)
                if key not in doc.definitions:
                    shown = label or link.label or link.text
                    if link.form != "shortcut":
                        # Full/collapsed spelling is unambiguous.
                        add(doc, link, "links.undefined-reference",
                            f"no definition for [{shown}]")
                    elif doc.definitions:
                        # A shortcut is a warning only when this file defines a reference.
                        add(doc, link, "links.undefined-reference",
                            f"no definition for [{shown}]",
                            severity="warning")
                    continue
                definition = doc.definitions[key]
                # The destination lives in the definition, so that is what a
                # repair must edit — not the link that reached it. One bad
                # definition used twice is one edit, not two.
                check_destination(doc, link, definition.destination,
                                  target=definition.target)
                continue
            if link.form == "autolink" or _is_external(link.destination):
                continue
            check_destination(doc, link, link.destination,
                              target=link.target)

        for label, definition in doc.definitions.items():
            if label not in used:
                findings.append(Finding(
                    path=str(doc.path), rule="links.unused-definition",
                    severity="warning", line=definition.line, start=0, end=0,
                    message=f"[{label}] is defined but never used"))

        seen_notes = {label for label, *_ in doc.footnote_refs}
        first_use: Dict[str, int] = {}
        for label, line, start, end, in_body in doc.footnote_refs:
            if label not in doc.footnotes:
                findings.append(Finding(
                    path=str(doc.path), rule="links.undefined-footnote",
                    severity="error", line=line, start=start, end=end,
                    message=f"no definition for footnote {label}"))
                continue
            # A token inside a definition body is not a document reference.
            if in_body:
                continue
            # Pandoc has no reuse syntax: a second document reference clones
            # the note body and renumbers every note after it.
            if label in first_use:
                findings.append(Finding(
                    path=str(doc.path), rule="links.reused-footnote",
                    severity="error", line=line, start=start, end=end,
                    message=f"footnote {label} is already referenced on line "
                            f"{first_use[label]}; Pandoc will print the note "
                            f"twice and renumber the rest"))
            else:
                first_use[label] = line
        for label, line in doc.footnotes.items():
            if label not in seen_notes:
                findings.append(Finding(
                    path=str(doc.path), rule="links.unused-footnote",
                    severity="warning", line=line, start=0, end=0,
                    message=f"footnote {label} is defined but never referenced"))

    findings.sort(key=lambda f: (f.path, f.line, f.start))
    return findings
