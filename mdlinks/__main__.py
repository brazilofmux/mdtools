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
from typing import List, Optional, Sequence

from mdquery.ir import IRError

from .graph import check, read


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
    parser.add_argument("--warnings", action="store_true",
                        help="also fail on warnings (unused definitions)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

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
                "definitions": sorted(doc.definitions),
            }, ensure_ascii=False))
            for link in doc.links:
                print(json.dumps({
                    "kind": link.kind, "path": str(doc.path),
                    "line": link.line, "start": link.start, "end": link.end,
                    "form": link.form, "destination": link.destination,
                    "label": link.label,
                }, ensure_ascii=False))
        return 0

    findings = check(docs)
    if args.diagnostics:
        for finding in findings:
            print(json.dumps(finding.to_diagnostic(), ensure_ascii=False))
    else:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: "
                  f"{finding.severity}: {finding.message}")

    errors = [f for f in findings if f.severity == "error"]
    if errors:
        return 1
    return 1 if (args.warnings and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
