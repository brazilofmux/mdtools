"""
mdcheck — repository-aware Markdown validation (issue #13).

Read-only, offline, deterministic. Composes the link graph and dialect
diagnostics, then adds the checks nothing else performs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from mdquery.ir import IRError
from mdtools_cli.config import ConfigError
from mdtools_cli.contract import (
    FINDINGS, OK, USAGE, add_common, add_verbs, fail, resolve_config,
    resolve_mdfix, sarif,
)

from .checks import Finding, run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdcheck",
        description="Repository-aware Markdown validation.",
        epilog="Read-only and offline: no network, no model. A validator that "
               "cannot run in a build gate is not one.",
    )
    parser.add_argument("paths", nargs="+", type=Path,
                        help="files, or directories to walk for *.md")
    add_common(parser)
    # No --fix or --diff: mdcheck reports things a tool cannot decide how to
    # repair (a missing asset, an anchor two files disagree about). --check is
    # still accepted so a script can pass the verb without knowing which tool
    # it is driving.
    add_verbs(parser, fixable=False)
    parser.add_argument("--suppress", action="append", default=[],
                        metavar="RULE",
                        help="rule id to ignore; a trailing * matches a "
                             "prefix. Repeatable")
    parser.add_argument("--warnings", action="store_true",
                        help="fail on warnings as well as errors")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--diagnostics", action="store_true",
                     help="JSONL, per docs/diagnostics.md")
    out.add_argument("--sarif", action="store_true",
                     help="SARIF 2.1.0 on stdout")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)

    missing = [p for p in args.paths if not p.exists()]
    for path in missing:
        print(f"mdcheck: {path}: does not exist", file=sys.stderr)
    if missing:
        return USAGE

    try:
        # A present but unusable project policy must not look like success.
        config = resolve_config(args.config, args.paths[0])
    except ConfigError as exc:
        return fail("mdcheck", str(exc))
    suppress: List[str] = list(args.suppress) + list(config.suppress)

    try:
        findings = run(args.paths, resolve_mdfix(args.mdfix, config),
                       suppress, config.frontmatter)
    except IRError as exc:
        return fail("mdcheck", str(exc))

    if args.sarif:
        print(json.dumps(sarif("mdcheck", findings), indent=2))
    elif args.diagnostics:
        for finding in findings:
            print(json.dumps(finding.to_diagnostic(), ensure_ascii=False))
    else:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: "
                  f"{finding.severity}: {finding.message} [{finding.rule}]")
        errors = sum(1 for f in findings if f.severity == "error")
        warnings = len(findings) - errors
        if findings:
            print(f"\n{errors} error(s), {warnings} warning(s)",
                  file=sys.stderr)

    if any(f.severity == "error" for f in findings):
        return FINDINGS
    return FINDINGS if (args.warnings and findings) else OK


if __name__ == "__main__":
    sys.exit(main())
