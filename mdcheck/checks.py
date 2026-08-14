"""
Repository-aware validation.

mdlinks knows the link graph; mdfix knows the dialect. mdcheck composes those
and adds the checks nothing else does, then applies one policy.

Everything here reads. Nothing writes, and no check needs a network or a
model — a validator that cannot run offline is not a gate.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from mdquery.ir import find_mdfix, raw_records

from .bibliography import resolve as resolve_bibliography
from .frontmatter import validate as validate_frontmatter
from mdquery.slug import assign_slugs
from mdlinks.graph import (
    _is_external,
    _normalize_label,
    check as link_check,
    read as link_read,
)

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


def _front_matter_data(data: bytes, record: dict) -> Optional[dict]:
    """The front matter as a mapping, or None if it is not one."""
    try:
        import yaml
    except ImportError:                          # pragma: no cover
        return None
    span = data[record["start"]:record["end"]].decode("utf-8", "replace")
    body = "\n".join(span.splitlines()[1:-1])
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError:
        return None                              # the schema check reports it
    return parsed if isinstance(parsed, dict) else None


def check_document(path: Path, mdfix: Optional[str] = None,
                   frontmatter: Optional[dict] = None,
                   bibliography: Optional[Sequence[str]] = None) -> List[Finding]:
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
    saw_frontmatter = False
    front_data: Optional[dict] = None
    citations: List[dict] = []
    previous_level: Optional[int] = None
    for record in records:
        kind = record["kind"]

        if kind == "heading":
            level = record.get("level")
            # The IR emits no heading inside a quote or list item.
            if isinstance(level, int):
                # The first heading has nothing to descend from, so a
                # chapter that opens at `##` is not a finding.
                if previous_level is not None and level > previous_level + 1:
                    add("check.heading-skip", "warning", record,
                        f"h{previous_level} is followed by h{level}; "
                        f"h{previous_level + 1} is missing")
                previous_level = level

        elif kind == "image":
            destination = record.get("destination", "")
            if not record.get("text"):
                add("check.image-alt", "warning", record,
                    "image has no alt text")
            if destination and not _is_external(destination):
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
            label = _normalize_label(record.get("label", "") or "")
            if label in labels:
                add("check.duplicate-definition", "error", record,
                    f"[{label}] is already defined on line {labels[label]}")
            else:
                labels[label] = record["line"]

        elif kind == "frontmatter":
            saw_frontmatter = True
            front_data = _front_matter_data(data, record)
            if not frontmatter:
                continue
            # The whole block is the span; the line is the offending key's,
            # from PyYAML's marks. A schema error points at the field, not at
            # the document.
            span = data[record["start"]:record["end"]].decode("utf-8", "replace")
            body = "\n".join(span.splitlines()[1:-1])
            for rule, severity, line, message in validate_frontmatter(
                    body, frontmatter, record["line"]):
                findings.append(Finding(
                    path=str(path), rule=rule, severity=severity, line=line,
                    start=record["start"], end=record["end"], message=message))

        elif kind == "citation":
            citations.append(record)

        elif kind == "paragraph":
            span = data[record["start"]:record["end"]].decode("utf-8", "replace")
            for marker, rule, message in LOSSY_HINTS:
                if marker in span:
                    add(rule, "warning", record, message)
                    break

    # A schema with required fields is not satisfied by having no front
    # matter at all. Reporting only when a block exists would mean deleting
    # the block silently passes the gate, which is how a gate stops being one.
    if frontmatter and not saw_frontmatter:
        required = sorted(name for name, spec
                          in frontmatter.get("fields", {}).items()
                          if spec["required"])
        if required:
            findings.append(Finding(
                path=str(path), rule="check.frontmatter-missing",
                severity="error", line=1, start=0, end=0,
                message=("document has no front matter; required field(s) "
                         + ", ".join(repr(r) for r in required))))

    # Citations, once the whole document has been walked: the bibliography
    # may be named in front matter, which is read before any citation is.
    known, problems = resolve_bibliography(front_data, bibliography, path)
    for problem in problems:
        findings.append(Finding(
            path=str(path), rule="check.bibliography-unreadable",
            severity="error", line=1, start=0, end=0, message=problem))
    if known is not None and not problems:
        # Not while a source failed to load: an unreadable bibliography looks
        # like an empty one, and reporting every citation as unresolved buries
        # the finding that actually matters.
        for record in citations:
            if record["key"] not in known:
                findings.append(Finding(
                    path=str(path), rule="check.unresolved-citation",
                    severity="error", line=record["line"],
                    start=record["start"], end=record["end"],
                    message=f"no bibliography entry for @{record['key']}"))

    return findings


def check_repository(paths: Sequence[Path],
                     mdfix: Optional[str] = None) -> List[Finding]:
    """
    Cross-file checks.

    Duplicate anchors only make sense repository-wide: within one file Pandoc
    disambiguates with -1/-2 suffixes. Every cross-file slug collision is
    reported (not only when something links to it).
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
    """
    mdfix diagnostics at default settings.

    mdfix severity `fix` means a required repair would change the file
    (not canonical) → error. Lint-only rows stay warnings.
    """
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
        mdfix_sev = row.get("severity", "warning")
        severity = "error" if mdfix_sev == "fix" else "warning"
        message = row.get("message", "")
        if severity == "error":
            message = f"not canonical: {message}"
        out.append(Finding(
            path=row.get("path", str(path)), rule=f"dialect.{row['rule']}",
            severity=severity, line=row.get("line", 0),
            start=row.get("start", 0), end=row.get("end", 0),
            message=message))
    return out


def run(paths: Sequence[Path], mdfix: Optional[str] = None,
        suppress: Iterable[str] = (),
        frontmatter: Optional[dict] = None,
        bibliography: Optional[Sequence[str]] = None) -> List[Finding]:
    files = discover(paths)
    findings: List[Finding] = []
    for path in files:
        findings.extend(check_document(path, mdfix, frontmatter, bibliography))
        findings.extend(dialect_findings(path, mdfix))
    findings.extend(check_repository(files, mdfix))

    docs = [link_read(p, mdfix) for p in files]
    for link_finding in link_check(docs):
        findings.append(Finding(
            path=link_finding.path, rule=link_finding.rule,
            severity=link_finding.severity, line=link_finding.line,
            start=link_finding.start, end=link_finding.end,
            message=link_finding.message))

    blocked = set(suppress)

    def is_blocked(rule: str) -> bool:
        return (rule in blocked
                or any(rule.startswith(b.rstrip("*"))
                       for b in blocked if b.endswith("*")))

    # Prefer the more specific rule when two tools flag the same span.
    assets = {(f.path, f.start, f.end) for f in findings
              if f.rule == "check.missing-asset" and not is_blocked(f.rule)}
    findings = [f for f in findings
                if not (f.rule == "links.missing-file"
                        and (f.path, f.start, f.end) in assets)]

    # Unterminated fences: IR check owns the error; drop the dialect twin
    # only when the check rule will still be reported.
    if not is_blocked("check.unterminated-fence"):
        open_fences = {f.path for f in findings
                       if f.rule == "check.unterminated-fence"}
        findings = [f for f in findings
                    if not (f.rule == "dialect.fence.unterminated"
                            and f.path in open_fences)]

    findings = [f for f in findings if not is_blocked(f.rule)]
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings
