"""
Build and check the link graph.

**No Markdown grammar.** Links, images and definitions come from
`mdfix --emit-ir` as records with destinations and labels; anchors come from
mdquery's Pandoc-compatible slugs. mdlinks never looks at a bracket.

What it checks:

  - an internal anchor that no heading provides
  - a reference or shortcut link with no definition
  - a definition nothing uses
  - a relative path that is not on disk, and its anchor if it names one
"""

from __future__ import annotations

import posixpath
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from mdquery.ir import raw_records
from mdquery.slug import assign_slugs


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


@dataclass
class Document:
    path: Path
    anchors: List[str] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    definitions: Dict[str, int] = field(default_factory=dict)   # label -> line
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

    def to_diagnostic(self) -> dict:
        return {"kind": "diagnostic", "path": self.path, "rule": self.rule,
                "severity": self.severity, "line": self.line,
                "start": self.start, "end": self.end, "message": self.message}


def read(path: Path, mdfix: Optional[str] = None) -> Document:
    doc = Document(path=path)
    headings: List[str] = []
    for record in raw_records([path], mdfix):
        kind = record.get("kind")
        if kind == "heading":
            headings.append(record.get("plain") or record.get("text") or "")
        elif kind in ("link", "image"):
            doc.links.append(Link(
                path=path, line=record["line"],
                start=record["start"], end=record["end"],
                kind=kind, form=record.get("form", "inline"),
                destination=record.get("destination", ""),
                label=record.get("label", ""),
            ))
        elif kind == "reference_def":
            doc.definitions.setdefault(record.get("label", "").lower(),
                                       record["line"])
        elif kind == "footnote_def":
            doc.footnotes.setdefault(record.get("label", ""), record["line"])
        elif kind == "footnote_ref":
            doc.footnote_refs.append((record.get("label", ""), record["line"],
                                      record["start"], record["end"]))
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

    def add(doc, link, rule, message, severity="error"):
        findings.append(Finding(
            path=str(doc.path), rule=rule, severity=severity,
            line=link.line, start=link.start, end=link.end, message=message))

    for doc in docs:
        used = set()
        for link in doc.links:
            if link.form in ("reference", "shortcut"):
                label = link.label.lower()
                used.add(label)
                if label not in doc.definitions:
                    add(doc, link, "links.undefined-reference",
                        f"no definition for [{link.label}]")
                continue
            if link.form == "autolink" or _is_external(link.destination):
                continue
            target, anchor = _split(link.destination)
            if not target:
                if anchor and anchor not in doc.anchors:
                    add(doc, link, "links.broken-anchor",
                        f"no heading with anchor #{anchor}")
                continue
            resolved = (doc.path.parent / target).resolve()
            if not resolved.exists():
                add(doc, link, "links.missing-file",
                    f"{target} does not exist")
                continue
            if anchor:
                other = by_path.get(resolved)
                if other is None and resolved.suffix == ".md":
                    continue   # not in scope; cannot judge its anchors
                if other is not None and anchor not in other.anchors:
                    add(doc, link, "links.broken-anchor",
                        f"{target} has no anchor #{anchor}")

        for label, line in doc.definitions.items():
            if label not in used:
                findings.append(Finding(
                    path=str(doc.path), rule="links.unused-definition",
                    severity="warning", line=line, start=0, end=0,
                    message=f"[{label}] is defined but never used"))

        seen_notes = {label for label, *_ in doc.footnote_refs}
        for label, line, start, end in doc.footnote_refs:
            if label not in doc.footnotes:
                findings.append(Finding(
                    path=str(doc.path), rule="links.undefined-footnote",
                    severity="error", line=line, start=start, end=end,
                    message=f"no definition for footnote {label}"))
        for label, line in doc.footnotes.items():
            if label not in seen_notes:
                findings.append(Finding(
                    path=str(doc.path), rule="links.unused-footnote",
                    severity="warning", line=line, start=0, end=0,
                    message=f"footnote {label} is defined but never referenced"))

    findings.sort(key=lambda f: (f.path, f.line, f.start))
    return findings
