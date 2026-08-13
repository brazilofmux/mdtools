"""
mdquery — structural queries over Markdown (issue #15).

Read-only by construction: it runs `mdfix --emit-ir`, and every answer is a
span into the file on disk. It contains no Markdown grammar, which is the point
— see docs/dialect-policy.md §2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from mdtools_cli.config import ConfigError
from mdtools_cli.contract import (
    FINDINGS, OK, USAGE, add_common, fail, resolve_config, resolve_mdfix,
)

from .ir import IRError, load
from .query import (
    annotate,
    filter_blocks,
    hidden_nested_blocks,
    outline,
    section_span,
)

KINDS = (
    "frontmatter", "heading", "paragraph", "list", "block_quote",
    "code_fence", "code_indented", "table", "line_block", "raw_html",
    "thematic_break", "reference_def", "footnote_def",
)
TABLE_FORMS = ("pipe", "simple", "grid", "multiline")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mdquery",
        description="Structural queries and extraction for Markdown.",
        epilog="Spans are byte offsets into the file on disk. "
               "See docs/mdquery.md and docs/ir-schema.md.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="emit JSONL (one object per result) instead of human output",
    )
    add_common(parser)
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress the under-reporting warning for nested containers",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_outline = sub.add_parser("outline", help="heading tree with spans")
    p_outline.add_argument("files", nargs="+", type=Path)
    p_outline.add_argument(
        "--max-level", type=int, default=6, metavar="N",
        help="omit headings deeper than N (default: 6)",
    )

    p_blocks = sub.add_parser("blocks", help="every block, filterable")
    p_blocks.add_argument("files", nargs="+", type=Path)
    p_blocks.add_argument(
        "--kind", action="append", choices=KINDS, metavar="KIND",
        help=f"only this kind; repeatable. One of: {', '.join(KINDS)}",
    )
    p_blocks.add_argument(
        "--form", action="append", choices=TABLE_FORMS, metavar="FORM",
        help=f"only tables of this form; repeatable. One of: {', '.join(TABLE_FORMS)}",
    )
    p_blocks.add_argument(
        "--under", metavar="SLUG",
        help="only blocks inside this heading's section",
    )
    protection = p_blocks.add_mutually_exclusive_group()
    protection.add_argument(
        "--protected", action="store_true",
        help="only blocks mdfix reproduces byte for byte",
    )
    protection.add_argument(
        "--unprotected", action="store_true",
        help="only blocks a prose pass may rewrite",
    )

    p_section = sub.add_parser(
        "section", help="print the source text of one section")
    p_section.add_argument("file", type=Path)
    p_section.add_argument("--id", required=True, metavar="SLUG",
                           help="heading identifier, as `outline` reports it")

    p_stats = sub.add_parser("stats", help="block counts by kind")
    p_stats.add_argument("files", nargs="+", type=Path)

    return parser


def _warn_hidden(documents, quiet: bool) -> None:
    """
    Say so when a container hides fenced blocks from every query.

    Silence here would make `blocks --kind code_fence` look exhaustive when it
    is not. docs/ir-schema.md, "Not in schema 1".
    """
    if quiet:
        return
    for document in documents:
        for block in hidden_nested_blocks(document):
            print(
                f"warning: {document.path}:{block.line}: this {block.kind} "
                "contains a fenced block that schema 1 cannot report "
                "separately; queries over code blocks and protection "
                "under-report here",
                file=sys.stderr,
            )


def _emit(rows: Sequence[dict], as_json: bool, human) -> None:
    if as_json:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
    else:
        human()


def cmd_outline(args, documents) -> int:
    rows = []
    for document in documents:
        for block in outline(document):
            if (block.level or 1) > args.max_level:
                continue
            rows.append(block)

    def human() -> None:
        for block in rows:
            indent = "  " * ((block.level or 1) - 1)
            print(f"{block.source}:{block.line}: {indent}"
                  f"{'#' * (block.level or 1)} {block.text}  [{block.slug}]")

    _emit([b.to_dict() for b in rows], args.json, human)
    return OK


def cmd_blocks(args, documents) -> int:
    protected: Optional[bool] = None
    if args.protected:
        protected = True
    elif args.unprotected:
        protected = False

    rows: List = []
    for document in documents:
        rows.extend(filter_blocks(
            document.blocks,
            kinds=args.kind,
            under=args.under,
            protected=protected,
            forms=args.form,
        ))

    def human() -> None:
        for block in rows:
            extra = f" {block.form}" if block.form else ""
            flag = "frozen" if block.protected else "prose"
            where = "/".join(block.ancestors) or "-"
            print(f"{block.source}:{block.line}-{block.end_line}: "
                  f"{block.kind}{extra} ({flag}) "
                  f"[{block.start}:{block.end}] under {where}")

    _emit([b.to_dict() for b in rows], args.json, human)
    return OK


def cmd_section(args, documents) -> int:
    document = documents[0]
    span = section_span(document, args.id)
    if span is None:
        available = ", ".join(b.slug or "" for b in outline(document)) or "none"
        print(f"mdquery: no heading with id {args.id!r} in {document.path}\n"
              f"available: {available}", file=sys.stderr)
        return FINDINGS
    start, end = span
    data = document.path.read_bytes()
    if args.json:
        print(json.dumps({
            "path": str(document.path),
            "id": args.id,
            "start": start,
            "end": end,
            "text": data[start:end].decode("utf-8", errors="replace"),
        }, ensure_ascii=False))
    else:
        sys.stdout.write(data[start:end].decode("utf-8", errors="replace"))
    return OK


def cmd_stats(args, documents) -> int:
    rows = []
    for document in documents:
        counts: dict = {}
        for block in document.blocks:
            counts[block.kind] = counts.get(block.kind, 0) + 1
        rows.append({
            "path": str(document.path),
            "bytes": document.byte_length,
            "lines": document.line_count,
            "blocks": len(document.blocks),
            "kinds": counts,
        })

    def human() -> None:
        for row in rows:
            print(f"{row['path']}: {row['blocks']} blocks, "
                  f"{row['lines']} lines, {row['bytes']} bytes")
            for kind, n in sorted(row["kinds"].items()):
                print(f"  {n:5d}  {kind}")

    _emit(rows, args.json, human)
    return OK


COMMANDS = {
    "outline": cmd_outline,
    "blocks": cmd_blocks,
    "section": cmd_section,
    "stats": cmd_stats,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    paths = [args.file] if args.command == "section" else args.files

    missing = [p for p in paths if not p.is_file()]
    if missing:
        for path in missing:
            print(f"mdquery: {path}: not a file", file=sys.stderr)
        return USAGE

    try:
        config = resolve_config(args.config, paths[0] if paths else None)
    except ConfigError as exc:
        return fail("mdquery", str(exc))

    try:
        documents = [annotate(d)
                     for d in load(paths, resolve_mdfix(args.mdfix, config))]
    except IRError as exc:
        return fail("mdquery", str(exc))

    if not documents:
        return OK

    _warn_hidden(documents, args.quiet)
    return COMMANDS[args.command](args, documents)


if __name__ == "__main__":
    sys.exit(main())
