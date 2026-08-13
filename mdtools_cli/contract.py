"""
Shared CLI verbs and exit codes.

    --check   report; change nothing.  The default.
    --diff    show what would change; change nothing.
    --fix     apply repairs via `mdfix --apply-edits` (never write directly).

    0  clean
    1  findings (after --fix: what is still wrong)
    2  the tool could not run (usage, missing file, bad config, missing mdfix)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from mdquery.ir import IRError, find_mdfix

from .config import Config, ConfigError, load as load_config, load_file

OK = 0
FINDINGS = 1
USAGE = 2

EDITS_SCHEMA = "mdtools-edits-1"


def add_common(parser: argparse.ArgumentParser) -> None:
    """`--config` and `--mdfix`, spelled the same way by every tool."""
    parser.add_argument(
        "--config", metavar="PATH", type=Path,
        help="mdtools.toml to use (default: walk up from the first input)")
    parser.add_argument(
        "--mdfix", metavar="PATH",
        help="mdfix binary (default: $MDFIX, sibling build, then PATH)")


def add_verbs(parser: argparse.ArgumentParser, *, fixable: bool = True) -> None:
    """
    The `--check` / `--diff` / `--fix` group.

    `fixable=False` for a tool that only reports: it still takes `--check`, so
    a script can pass the flag uniformly without knowing which tool it has.
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check", action="store_true",
        help="report and change nothing (the default)")
    if fixable:
        group.add_argument(
            "--diff", action="store_true",
            help="show what --fix would change, and change nothing")
        group.add_argument(
            "--fix", action="store_true",
            help="apply the repairable findings via `mdfix --apply-edits`")


def resolve_config(explicit: Optional[Path],
                   start: Optional[Path]) -> Config:
    """
    The project config, from `--config` if given or discovery if not.

    An explicit path that does not exist is a usage error, not a fallback to
    discovery: a caller who names a config file and silently gets a different
    one has no way to notice.
    """
    if explicit is not None:
        return load_file(explicit)
    return load_config(start)


def resolve_mdfix(explicit: Optional[str], config: Config) -> Optional[str]:
    """`--mdfix` beats the config file, which beats discovery."""
    return explicit or config.mdfix


def write_edits(stream, path: Path, edits: Sequence[dict]) -> None:
    """An edit list on `stream`, header first (docs/edit-schema.md)."""
    if not edits:
        return
    print(json.dumps({"kind": "edits", "schema": EDITS_SCHEMA,
                      "source": str(path),
                      "bytes": path.stat().st_size}), file=stream)
    for edit in edits:
        print(json.dumps(edit, ensure_ascii=False), file=stream)


def _edit_stream(path: Path, edits: Sequence[dict]) -> str:
    head = json.dumps({"kind": "edits", "schema": EDITS_SCHEMA,
                       "source": str(path), "bytes": path.stat().st_size})
    return "\n".join([head] + [json.dumps(e, ensure_ascii=False)
                               for e in edits]) + "\n"


def apply_edits(path: Path, edits: Sequence[dict], *,
                mdfix: Optional[str] = None,
                diff: bool = False,
                quiet: bool = False) -> int:
    """
    Hand an edit list to `mdfix --apply-edits`. Returns its exit status.

    `diff=True` previews and writes nothing; otherwise the file is rewritten
    in place. Either way mdfix is the one that reads the document, checks the
    edits against it and decides whether to proceed — a Python tool that
    spliced bytes itself would be a second implementation of the write path,
    with its own ideas about encoding and atomicity.

    An empty list is not sent at all. It would be accepted and be a no-op
    (I5.1), but running the applier to do nothing still rewrites the file's
    mtime, and a `--fix` that reports "nothing to fix" should leave the tree
    exactly as it found it.
    """
    if not edits:
        return OK
    try:
        binary = mdfix or find_mdfix()
        argv = [binary, "--apply-edits"]
        if diff:
            argv.append("--diff")
        else:
            argv.append("-i")
        if quiet:
            argv.insert(1, "-q")
        result = subprocess.run(argv + [str(path)],
                                input=_edit_stream(path, edits), text=True)
        return result.returncode
    except (OSError, IRError) as exc:
        return fail("mdfix", str(exc))


def fail(program: str, message: str) -> int:
    """A usage/environment error: one line on stderr, exit 2."""
    print(f"{program}: {message}", file=sys.stderr)
    return USAGE


def guard(program: str, fn, *args, **kwargs) -> int:
    """
    Run `fn`, turning the two "cannot run" failures into exit 2.

    IRError means mdfix could not be found or would not parse the input;
    ConfigError means the project's own settings are unusable. Neither is a
    finding about the prose, and reporting them as one is how a gate goes
    green on a check that never ran.
    """
    try:
        return fn(*args, **kwargs)
    except (IRError, ConfigError) as exc:
        return fail(program, str(exc))


__all__ = [
    "OK", "FINDINGS", "USAGE", "EDITS_SCHEMA",
    "add_common", "add_verbs", "apply_edits", "fail", "guard",
    "resolve_config", "resolve_mdfix", "write_edits",
]
