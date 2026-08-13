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
from mdtools_cli.contract import (
    FINDINGS, OK, USAGE, add_common, add_verbs, apply_edits, fail,
    resolve_config, resolve_mdfix, sarif, write_edits,
)
from mdtools_cli.config import ConfigError

from .check import edits_for, scan, usage
from .glossary import GlossaryError, find, freeze_set, load


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdterms",
        description="Glossary and terminology enforcement for Markdown.",
        epilog="Only prose is checked: a forbidden spelling inside a code "
               "block, table or link definition is left alone.",
    )
    parser.add_argument("files", nargs="*", type=Path)
    # Deliberately outside the output group below: --report is a different
    # question, not a different format, so `--report --diagnostics` composes
    # and gives the same table as JSONL.
    parser.add_argument(
        "--report", action="store_true",
        help="repository consistency: which files use each term, and which "
             "introduce it. Combines with --diagnostics for JSONL",
    )
    parser.add_argument(
        "--glossary", type=Path, metavar="PATH",
        help="glossary_terms.yaml (default: mdtools.toml, then walk up "
             "from the first input)",
    )
    add_common(parser)
    add_verbs(parser)
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
        "--sarif", action="store_true",
        help="SARIF 2.1.0 on stdout, for a CI system that ingests it",
    )
    out.add_argument(
        "--freeze", action="store_true",
        help="print the freeze set — every spelling prosevary must preserve",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

    if args.report and (args.edits or args.freeze or args.sarif
                        or args.fix or args.diff):
        # Checked before anything else runs: further down, whichever branch
        # comes first in the source would silently win and the other flag
        # would look accepted.
        return fail("mdterms",
                    "--report describes the corpus; it does not produce "
                    "edits, a freeze set, SARIF or repairs")

    start = args.files[0] if args.files else None
    try:
        config = resolve_config(args.config, start)
    except ConfigError as exc:
        return fail("mdterms", str(exc))
    mdfix = resolve_mdfix(args.mdfix, config)

    # --glossary beats mdtools.toml beats discovery. Explicit always wins:
    # a caller who names a glossary and silently gets another one has no way
    # to notice the check ran against the wrong vocabulary.
    glossary_path = find(args.glossary or config.glossary, start)
    if glossary_path is None:
        return fail("mdterms", "no glossary_terms.yaml found; pass --glossary")
    try:
        terms = load(glossary_path)
    except GlossaryError as exc:
        return fail("mdterms", str(exc))

    if args.freeze:
        for spelling in freeze_set(terms):
            print(spelling)
        return OK

    if not args.files:
        return fail("mdterms", "no input files")
    # One file at a time for --edits: the applier reads a single document and
    # its `bytes` header is that document's size. Refuse before scanning so
    # clean files and unfixable-only results are not a silent multi-file path.
    if args.edits and len(args.files) != 1:
        return fail("mdterms",
                    "--edits takes one file at a time, because "
                    "`mdfix --apply-edits` applies to one document. "
                    "Use --fix to repair several.")
    missing = [p for p in args.files if not p.is_file()]
    for path in missing:
        print(f"mdterms: {path}: not a file", file=sys.stderr)
    if missing:
        return USAGE

    findings = []
    try:
        for path in args.files:
            findings.extend(scan(path, terms, mdfix))
    except IRError as exc:
        return fail("mdterms", str(exc))

    if args.edits:
        write_edits(sys.stdout, Path(args.files[0]), edits_for(findings))
        return FINDINGS if findings else OK

    if args.fix or args.diff:
        # Never write the file here — build the edits and let mdfix apply
        # them, so the validation in docs/edit-schema.md is not something a
        # second write path can skip.
        for path in args.files:
            edits = edits_for([f for f in findings if f.path == str(path)])
            if not edits:
                continue
            rc = apply_edits(path, edits, mdfix=mdfix, diff=args.diff,
                             quiet=True)
            if rc != OK:
                return USAGE
        if args.diff:
            return FINDINGS if findings else OK
        # Re-scan: overlapping fixable clusters are dropped from the edit
        # list without being applied, and still-wrong prose is still a finding.
        remaining = []
        try:
            for path in args.files:
                remaining.extend(scan(path, terms, mdfix))
        except IRError as exc:
            return fail("mdterms", str(exc))
        for finding in remaining:
            print(f"{finding.path}:{finding.line}: {finding.message}")
        return FINDINGS if remaining else OK

    if args.report:
        # A report, not a gate: it describes the corpus rather than judging
        # it, so it exits 0 even when the picture is untidy.
        rows = usage(args.files, terms, mdfix)
        if args.diagnostics:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False))
        else:
            for row in rows:
                where = ", ".join(row["used_in"]) or "nowhere"
                print(f"{row['term']}: used in {where}")
                if row["expansion"]:
                    intro = ", ".join(row["introduced_in"]) or "nowhere"
                    print(f"  introduced in {intro}")
        return OK

    if args.sarif:
        print(json.dumps(sarif("mdterms", findings), indent=2))
        return FINDINGS if findings else OK

    if args.diagnostics:
        for finding in findings:
            print(json.dumps(finding.to_diagnostic(), ensure_ascii=False))
        return FINDINGS if findings else OK

    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.message}")
    fixable = sum(1 for f in findings if f.fixable)
    if fixable:
        # Only when there is something --fix would actually do. Offering it
        # against a file whose remaining findings are all inside protected
        # spans sends the reader to a command that changes nothing.
        #
        # The report is on stdout and this is on stderr; flush first, or the
        # hint overtakes the findings it refers to whenever stdout is a pipe.
        sys.stdout.flush()
        print(f"\n{len(findings)} finding(s), {fixable} fixable. Apply with:\n"
              f"  mdterms --fix {' '.join(str(p) for p in args.files)}",
              file=sys.stderr)
    return FINDINGS if findings else OK


if __name__ == "__main__":
    sys.exit(main())
