"""
mdtools — one entry point for the toolkit (issue #17).

    mdtools fix     chapter.md      # mdfix
    mdtools query   outline chapter.md
    mdtools terms   chapter.md
    mdtools links   chapter.md
    mdtools vary    chapter.md      # prosevary
    mdtools config                  # the resolved configuration

The standalone commands keep working; this dispatches to exactly the same
code, adding project configuration and one consistent set of exit codes.

Exit codes, shared by every verb:

    0  clean
    1  findings — something the tool wants a human to look at
    2  usage or environment error: bad flags, missing file, bad config
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .config import ConfigError, Config, fix_flags, load

VERBS = ("fix", "query", "terms", "links", "vary", "config", "check")

USAGE = """\
usage: mdtools <command> [args...]

  fix      repair and format Markdown (mdfix)
  query    structural queries and extraction (mdquery)
  terms    glossary and terminology enforcement (mdterms)
  links    check the link graph (mdlinks)
  vary     controlled lexical variation (prosevary)
  config   print the resolved project configuration
  check    repository-aware validation (not implemented; see issue #13)

Every command exits 0 clean, 1 with findings, 2 on a usage or environment
error. `mdtools <command> --help` shows that command's own options.

Project settings come from mdtools.toml, discovered by walking up from the
input file. `mdtools config` shows what was resolved and from where.
"""


def _find_mdfix(config: Config) -> str:
    if config.mdfix:
        return config.mdfix
    sibling = Path(__file__).resolve().parent.parent / "mdfix" / "mdfix"
    if sibling.is_file():
        return str(sibling)
    return "mdfix"


def _run_module(module: str, argv: Sequence[str]) -> int:
    """Dispatch in-process, so a traceback points at the real code."""
    import importlib

    main = importlib.import_module(f"{module}.__main__").main
    return main(list(argv))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0 if args else 2
    verb, rest = args[0], args[1:]
    if verb not in VERBS:
        print(f"mdtools: unknown command {verb!r}\n", file=sys.stderr)
        sys.stderr.write(USAGE)
        return 2

    # Configuration is discovered from the first path-looking argument, so a
    # run against another repository picks up that repository's settings.
    start = next((Path(a) for a in rest if not a.startswith("-")), None)
    try:
        config = load(start)
    except ConfigError as exc:
        print(f"mdtools: {exc}", file=sys.stderr)
        return 2

    if verb == "config":
        print(json.dumps(config.resolved(), indent=2))
        return 0

    if verb == "check":
        print("mdtools: `check` is not implemented yet (issue #13). "
              "`mdtools links` and `mdterms` cover part of it today.",
              file=sys.stderr)
        return 2

    if verb == "fix":
        flags = [] if any(a.startswith("--") for a in rest) else fix_flags(config)
        result = subprocess.run([_find_mdfix(config), *flags, *rest])
        return result.returncode

    if verb == "query":
        return _run_module("mdquery", rest)
    if verb == "links":
        return _run_module("mdlinks", rest)
    if verb == "terms":
        extra: List[str] = []
        if config.glossary and not any(a == "--glossary" for a in rest):
            extra = ["--glossary", str(config.glossary)]
        return _run_module("mdterms", [*extra, *rest])
    if verb == "vary":
        return _run_module("prosevary", rest)
    return 2


if __name__ == "__main__":
    sys.exit(main())
