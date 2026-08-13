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
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--mdfix", metavar="PATH",
                        help="mdfix binary (default: $MDFIX, sibling, PATH)")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--diagnostics", action="store_true",
                     help="report as JSONL (see docs/diagnostics.md)")
    out.add_argument("--graph", action="store_true",
                     help="print the link graph as JSONL instead of checking")
    out.add_argument("--edits", metavar="FILE", type=Path,
                     help="write an edit list repairing FILE for "
                          "`mdfix --apply-edits`; other paths are the scope "
                          "repairs draw on (FILE is added if not listed)")
    parser.add_argument("--warnings", action="store_true",
                        help="also fail on warnings (unused definitions)")
    return parser


def _exit_code(findings, warnings: bool) -> int:
    if any(f.severity == "error" for f in findings):
        return 1
    return 1 if (warnings and findings) else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

    # --edits is per document; other positionals are search scope. Include
    # the target automatically so `mdlinks --edits a.md b.md` works — and so
    # `mdlinks --edits a.md` alone works too, which is why `files` is optional
    # rather than required.
    files = list(args.files)
    if not files and args.edits is None:
        print("mdlinks: no input files", file=sys.stderr)
        return 2
    if args.edits is not None:
        if not args.edits.is_file():
            print(f"mdlinks: {args.edits}: not a file", file=sys.stderr)
            return 2
        if args.edits.resolve() not in {p.resolve() for p in files}:
            files = [args.edits, *files]

    missing = [p for p in files if not p.is_file()]
    for path in missing:
        print(f"mdlinks: {path}: not a file", file=sys.stderr)
    if missing:
        return 2

    try:
        docs = [read(p, args.mdfix) for p in files]
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
            elif note.unwritable and len(note.candidates) == 1:
                print(f"{where}found {note.candidates[0]!r} but cannot "
                      f"write it bare (needs <> or escapes)")
            else:
                # Ambiguity is reported, never resolved — issue #14.
                shown = ", ".join(repr(c) for c in note.candidates)
                n = len(note.candidates)
                noun = "candidate" if n == 1 else "candidates"
                print(f"{where}{n} {noun}, not repaired: {shown}")
        if any(s.confident for s in suggestions):
            target = next(s.finding.path for s in suggestions if s.confident)
            # Flush stdout so this stderr hint cannot overtake findings.
            sys.stdout.flush()
            others = " ".join(str(p) for p in files if p.resolve() != Path(target).resolve())
            scope = f" {others}" if others else ""
            print(f"\nRepair the confident ones with:\n"
                  f"  mdlinks --edits {target}{scope}"
                  f" | mdfix --apply-edits -i {target}", file=sys.stderr)

    return _exit_code(findings, args.warnings)


if __name__ == "__main__":
    sys.exit(main())
