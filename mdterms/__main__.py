"""
mdterms — glossary and terminology enforcement (issue #16).

Read-only by default. `--edits` writes an edit list for `mdfix --apply-edits`,
so the tool that decides what to change is never the tool that writes the
file — mdfix validates the edits and refuses any that would break the dialect.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from mdquery.ir import IRError

from .check import edits_for, scan
from .glossary import GlossaryError, find, freeze_set, load


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdterms",
        description="Glossary and terminology enforcement for Markdown.",
        epilog="Only prose is checked: a forbidden spelling inside a code "
               "block, table or link definition is left alone.",
    )
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument(
        "--glossary", type=Path, metavar="PATH",
        help="glossary_terms.yaml (default: walk up from the first input)",
    )
    parser.add_argument(
        "--mdfix", metavar="PATH",
        help="mdfix binary (default: $MDFIX, sibling build, then PATH)",
    )
    out = parser.add_mutually_exclusive_group()
    out.add_argument(
        "--diagnostics", action="store_true",
        help="report findings as JSONL on stdout (see docs/diagnostics.md)",
    )
    out.add_argument(
        "--edits", action="store_true",
        help="write an edit list for `mdfix --apply-edits` on stdout",
    )
    out.add_argument(
        "--freeze", action="store_true",
        help="print the freeze set — every spelling prosevary must preserve",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

    start = args.files[0] if args.files else None
    glossary_path = find(args.glossary, start)
    if glossary_path is None:
        print("mdterms: no glossary_terms.yaml found; pass --glossary",
              file=sys.stderr)
        return 2
    try:
        terms = load(glossary_path)
    except GlossaryError as exc:
        print(f"mdterms: {exc}", file=sys.stderr)
        return 2

    if args.freeze:
        for spelling in freeze_set(terms):
            print(spelling)
        return 0

    if not args.files:
        print("mdterms: no input files", file=sys.stderr)
        return 2
    # One file at a time for --edits: the applier reads a single document and
    # its `bytes` header is that document's size. Refuse before scanning so
    # clean files and unfixable-only results are not a silent multi-file path.
    if args.edits and len(args.files) != 1:
        print("mdterms: --edits takes one file at a time, because "
              "`mdfix --apply-edits` applies to one document",
              file=sys.stderr)
        return 2
    missing = [p for p in args.files if not p.is_file()]
    for path in missing:
        print(f"mdterms: {path}: not a file", file=sys.stderr)
    if missing:
        return 2

    findings = []
    try:
        for path in args.files:
            findings.extend(scan(path, terms, args.mdfix))
    except IRError as exc:
        print(f"mdterms: {exc}", file=sys.stderr)
        return 2

    if args.edits:
        edits = edits_for(findings)
        if edits:
            path = Path(args.files[0])
            print(json.dumps({"kind": "edits", "schema": "mdtools-edits-1",
                              "source": str(path),
                              "bytes": path.stat().st_size}))
            for edit in edits:
                print(json.dumps(edit, ensure_ascii=False))
        return 1 if findings else 0

    if args.diagnostics:
        for finding in findings:
            print(json.dumps(finding.to_diagnostic(), ensure_ascii=False))
        return 1 if findings else 0

    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.message}")
    if findings:
        fixable = sum(1 for f in findings if f.fixable)
        print(f"\n{len(findings)} finding(s), {fixable} fixable. "
              f"Apply with:\n"
              f"  mdterms --edits {findings[0].path} | "
              f"mdfix --apply-edits -i {findings[0].path}",
              file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
