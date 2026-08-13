"""
mdcheck — repository-aware Markdown validation (issue #13).

Read-only, offline, deterministic. It composes what the other tools already
know — mdlinks has the link graph, mdfix has the dialect — and adds the checks
nothing else performs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from mdquery.ir import IRError

from .checks import Finding, run

try:
    from mdtools_cli.config import ConfigError, load as load_config
except ImportError:  # pragma: no cover
    load_config = None  # type: ignore


def _sarif(findings: Sequence[Finding]) -> dict:
    """SARIF 2.1.0, the shape CI systems already ingest."""
    rules = sorted({f.rule for f in findings})
    index = {rule: n for n, rule in enumerate(rules)}
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "mdcheck",
                "informationUri": "https://github.com/brazilofmux/mdtools",
                "rules": [{"id": rule} for rule in rules],
            }},
            "results": [{
                "ruleId": f.rule,
                "ruleIndex": index[f.rule],
                "level": "error" if f.severity == "error" else "warning",
                "message": {"text": f.message},
                "locations": [{"physicalLocation": {
                    "artifactLocation": {"uri": f.path},
                    "region": {"startLine": max(f.line, 1)},
                }}],
            } for f in findings],
        }],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdcheck",
        description="Repository-aware Markdown validation.",
        epilog="Read-only and offline: no network, no model. A validator that "
               "cannot run in a build gate is not one.",
    )
    parser.add_argument("paths", nargs="+", type=Path,
                        help="files, or directories to walk for *.md")
    parser.add_argument("--mdfix", metavar="PATH")
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
        return 2

    suppress: List[str] = list(args.suppress)
    if load_config is not None:
        try:
            config = load_config(args.paths[0])
            suppress.extend(config.raw.get("suppress", []) or [])
        except Exception:
            pass   # config problems are mdtools' to report, not mdcheck's

    try:
        findings = run(args.paths, args.mdfix, suppress)
    except IRError as exc:
        print(f"mdcheck: {exc}", file=sys.stderr)
        return 2

    if args.sarif:
        print(json.dumps(_sarif(findings), indent=2))
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
        return 1
    return 1 if (args.warnings and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
