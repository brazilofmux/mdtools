"""
Repository-aware validation.

Most of what #13 asks for already exists: mdlinks knows the link graph, mdfix
knows the dialect. mdcheck composes those and adds the checks nothing else
does, then applies one policy over the result.

Everything here reads. Nothing writes, and no check needs a network or a
model — a validator that cannot run offline is not a gate.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from mdquery.ir import find_mdfix, raw_records
from mdquery.slug import assign_slugs
from mdlinks.graph import check as link_check, read as link_read

# Constructs the IR reports as `paragraph` although Pandoc sees more. Writing
# them is not an error — they simply are not protected from prose passes, and
# a repository that gates on lossless round-tripping wants to know.
LOSSY_HINTS = (
    ("$$", "check.lossy-math", "display math is treated as prose"),
    ("\\begin{", "check.lossy-latex", "raw LaTeX is treated as prose"),
)


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


def discover(paths: Sequence[Path]) -> List[Path]:
    """Files to check: named files, and every .md under a named directory."""
    out: List[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(
                p for p in path.rglob("*.md")
                if ".git" not in p.parts))
        elif path.is_file():
            out.append(path)
    seen = set()
    unique = []
    for path in out:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _records(path: Path, mdfix: Optional[str]) -> List[dict]:
    return raw_records([path], mdfix)


def check_document(path: Path, mdfix: Optional[str] = None) -> List[Finding]:
    """The checks nothing else performs."""
    findings: List[Finding] = []
    data = path.read_bytes()
    records = _records(path, mdfix)

    def add(rule, severity, record, message):
        findings.append(Finding(
            path=str(path), rule=rule, severity=severity,
            line=record["line"], start=record["start"], end=record["end"],
            message=message))

    labels: Dict[str, int] = {}
    for record in records:
        kind = record["kind"]

        if kind == "image":
            destination = record.get("destination", "")
            if not record.get("text"):
                add("check.image-alt", "warning", record,
                    "image has no alt text")
            if destination and not urllib.parse.urlparse(destination).scheme:
                target = destination.split("#", 1)[0]
                if target and not (path.parent / target).exists():
                    add("check.missing-asset", "error", record,
                        f"{target} does not exist")

        elif kind == "code_fence":
            span = data[record["start"]:record["end"]].decode("utf-8", "replace")
            first = span.splitlines()[0] if span else ""
            info = first.lstrip("`~ \t")
            if not info.strip():
                add("check.fence-language", "warning", record,
                    "code fence has no language")
            if record.get("unterminated"):
                add("check.unterminated-fence", "error", record,
                    "code fence is never closed")

        elif kind == "reference_def":
            label = record.get("label", "").lower()
            if label in labels:
                add("check.duplicate-definition", "error", record,
                    f"[{label}] is already defined on line {labels[label]}")
            else:
                labels[label] = record["line"]

        elif kind == "paragraph":
            span = data[record["start"]:record["end"]].decode("utf-8", "replace")
            for marker, rule, message in LOSSY_HINTS:
                if marker in span:
                    add(rule, "warning", record, message)
                    break

    return findings


def check_repository(paths: Sequence[Path],
                     mdfix: Optional[str] = None) -> List[Finding]:
    """
    Cross-file checks.

    Duplicate anchors are the one that only makes sense here: within a file
    Pandoc disambiguates with -1 and -2 suffixes, so a collision is only a
    problem when two *files* claim the same anchor and something links to it
    by name across them.
    """
    findings: List[Finding] = []
    anchors: Dict[str, List[str]] = {}
    for path in paths:
        headings = [r for r in _records(path, mdfix) if r["kind"] == "heading"]
        slugs = assign_slugs([h.get("plain") or h.get("text") or ""
                              for h in headings])
        for heading, slug in zip(headings, slugs):
            anchors.setdefault(slug, []).append(f"{path}:{heading['line']}")
    for slug, places in sorted(anchors.items()):
        if len(places) > 1:
            first = places[0].rsplit(":", 1)
            findings.append(Finding(
                path=first[0], rule="check.anchor-collision",
                severity="warning", line=int(first[1]), start=0, end=0,
                message=(f"#{slug} is also a heading in "
                         f"{', '.join(places[1:])}")))
    return findings


def dialect_findings(path: Path, mdfix: Optional[str] = None) -> List[Finding]:
    """mdfix's own diagnostics, at default settings — the required repairs."""
    binary = mdfix or find_mdfix()
    result = subprocess.run(
        [binary, "-n", "--diagnostics", str(path)],
        capture_output=True, text=True)
    out: List[Finding] = []
    for line in result.stderr.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(Finding(
            path=row.get("path", str(path)), rule=f"dialect.{row['rule']}",
            severity="error", line=row.get("line", 0),
            start=row.get("start", 0), end=row.get("end", 0),
            message=f"not canonical: {row.get('message', '')}"))
    return out


def run(paths: Sequence[Path], mdfix: Optional[str] = None,
        suppress: Iterable[str] = ()) -> List[Finding]:
    files = discover(paths)
    findings: List[Finding] = []
    for path in files:
        findings.extend(check_document(path, mdfix))
        findings.extend(dialect_findings(path, mdfix))
    findings.extend(check_repository(files, mdfix))

    docs = [link_read(p, mdfix) for p in files]
    for link_finding in link_check(docs):
        findings.append(Finding(
            path=link_finding.path, rule=link_finding.rule,
            severity=link_finding.severity, line=link_finding.line,
            start=link_finding.start, end=link_finding.end,
            message=link_finding.message))

    # An image with a missing file is reported by both mdlinks (which sees an
    # image as a link with a destination) and by check.missing-asset. Two
    # diagnostics for one problem is how a gate loses trust, so the more
    # specific rule wins at the same span.
    assets = {(f.path, f.start, f.end) for f in findings
              if f.rule == "check.missing-asset"}
    findings = [f for f in findings
                if not (f.rule == "links.missing-file"
                        and (f.path, f.start, f.end) in assets)]

    blocked = set(suppress)
    findings = [f for f in findings
                if f.rule not in blocked
                and not any(f.rule.startswith(b.rstrip("*"))
                            for b in blocked if b.endswith("*"))]
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings
