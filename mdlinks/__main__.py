"""
mdlinks — build and check the Markdown link graph (issue #14).

Read-only. Anchors are Pandoc's, taken from mdquery's slug rules, so a link
mdlinks calls good is one Pandoc will resolve.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from mdquery.ir import IRError

from .graph import check, read
from .repair import edits_for, suggest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdlinks",
        description="Build and check the Markdown link graph.",
        epilog="Anchors follow Pandoc's auto_identifiers, so a link this "
               "accepts is one Pandoc resolves.",
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--mdfix", metavar="PATH",
                        help="mdfix binary (default: $MDFIX, sibling, PATH)")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--diagnostics", action="store_true",
                     help="report as JSONL (see docs/diagnostics.md)")
    out.add_argument("--graph", action="store_true",
                     help="print the link graph as JSONL instead of checking")
    out.add_argument("--edits", metavar="FILE", type=Path,
                     help="write an edit list repairing FILE for "
                          "`mdfix --apply-edits`; FILE must be among the "
                          "files given, which are the scope repairs draw on")
    parser.add_argument("--warnings", action="store_true",
                        help="also fail on warnings (unused definitions)")
    return parser


def _exit_code(findings, warnings: bool) -> int:
    if any(f.severity == "error" for f in findings):
        return 1
    return 1 if (warnings and findings) else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

    if args.edits is not None:
        # The applier reads one document and its `bytes` header is that
        # document's size, so edits are per file. The rest of the run is not
        # noise: a moved file is only findable because the other files were
        # named, which is the same scope rule the checker already uses.
        given = {p.resolve() for p in args.files}
        if args.edits.resolve() not in given:
            print(f"mdlinks: --edits {args.edits}: not among the files given",
                  file=sys.stderr)
            return 2

    missing = [p for p in args.files if not p.is_file()]
    for path in missing:
        print(f"mdlinks: {path}: not a file", file=sys.stderr)
    if missing:
        return 2

    try:
        docs = [read(p, args.mdfix) for p in args.files]
    except IRError as exc:
        print(f"mdlinks: {exc}", file=sys.stderr)
        return 2

    if args.graph:
        for doc in docs:
            print(json.dumps({
                "kind": "document", "path": str(doc.path),
                "anchors": doc.anchors,
                "definitions": {
                    label: definition.destination
                    for label, definition in sorted(doc.definitions.items())
                },
                "footnotes": sorted(doc.footnotes),
            }, ensure_ascii=False))
            for link in doc.links:
                print(json.dumps({
                    "kind": link.kind, "path": str(doc.path),
                    "line": link.line, "start": link.start, "end": link.end,
                    "form": link.form, "destination": link.destination,
                    "label": link.label, "text": link.text,
                }, ensure_ascii=False))
            for label, line, start, end in doc.footnote_refs:
                print(json.dumps({
                    "kind": "footnote_ref", "path": str(doc.path),
                    "line": line, "start": start, "end": end,
                    "label": label,
                }, ensure_ascii=False))
        return 0

    findings = check(docs)
    suggestions = suggest(docs, findings)

    if args.edits is not None:
        edits = edits_for(suggestions, args.edits)
        if edits:
            print(json.dumps({"kind": "edits", "schema": "mdtools-edits-1",
                              "source": str(args.edits),
                              "bytes": args.edits.stat().st_size}))
            for edit in edits:
                print(json.dumps(edit, ensure_ascii=False))
        return _exit_code(findings, args.warnings)

    if args.diagnostics:
        for finding in findings:
            print(json.dumps(finding.to_diagnostic(), ensure_ascii=False))
    else:
        notes = {id(s.finding): s for s in suggestions}
        for finding in findings:
            print(f"{finding.path}:{finding.line}: "
                  f"{finding.severity}: {finding.message}")
            note = notes.get(id(finding))
            if note is None or not note.candidates:
                continue
            where = f"{finding.path}:{finding.line}: note: "
            if note.confident:
                print(f"{where}did you mean {note.replacement!r}? "
                      f"({note.reason})")
            else:
                # Ambiguity is reported, never resolved — issue #14. Showing
                # the candidates is the whole value: the tool has done the
                # search, and the choice is the part it cannot make.
                shown = ", ".join(repr(c) for c in note.candidates)
                print(f"{where}{len(note.candidates)} candidates, "
                      f"not repaired: {shown}")
        if any(s.confident for s in suggestions):
            target = next(s.finding.path for s in suggestions if s.confident)
            # The report is on stdout and this hint is on stderr; without the
            # flush the hint overtakes the findings it refers to whenever
            # stdout is a pipe.
            sys.stdout.flush()
            print(f"\nRepair the confident ones with:\n"
                  f"  mdlinks --edits {target} {' '.join(map(str, args.files))}"
                  f" | mdfix --apply-edits -i {target}", file=sys.stderr)

    return _exit_code(findings, args.warnings)


if __name__ == "__main__":
    sys.exit(main())
