"""
mdtools — one entry point for the toolkit (issue #17).

    mdtools fix     chapter.md      # mdfix
    mdtools query   outline chapter.md
    mdtools terms   chapter.md
    mdtools links   chapter.md
    mdtools vary    chapter.md      # prosevary
    mdtools check   .               # mdcheck, over a whole repository
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

from .config import ConfigError, Config, fix_flags, load, load_file

VERBS = ("fix", "query", "terms", "links", "vary", "config", "check")

# mdquery subcommands — not path starts for config discovery.
_QUERY_COMMANDS = frozenset({"outline", "blocks", "section", "stats"})

USAGE = """\
usage: mdtools <command> [args...]

  fix      repair and format Markdown (mdfix)
  query    structural queries and extraction (mdquery)
  terms    glossary and terminology enforcement (mdterms)
  links    check the link graph (mdlinks)
  vary     controlled lexical variation (prosevary)
  config   print the resolved project configuration
  check    repository-aware validation (mdcheck)

Every command exits 0 clean, 1 with findings, 2 on a usage or environment
error. `mdtools <command> --help` shows that command's own options.

--check reports, --diff previews, --fix repairs. --check is the default, and
--fix hands its edits to mdfix rather than writing anything itself.

Project settings come from mdtools.toml, discovered by walking up from the
input file, or named with --config. `mdtools config` shows what was resolved
and from where. The full contract is in docs/cli.md.
"""


def _find_mdfix(config: Config) -> str:
    if config.mdfix:
        return config.mdfix
    sibling = Path(__file__).resolve().parent.parent / "mdfix" / "mdfix"
    if sibling.is_file():
        return str(sibling)
    return "mdfix"


def _config_start(verb: str, rest: Sequence[str]) -> Optional[Path]:
    """
    Walk start for mdtools.toml: the input file, not a subcommand name.

    Prefer an existing path among positionals so `query outline chapter.md`
    uses the file. Otherwise take the first path-like token (slash, backslash,
    or a suffix), skipping known query subcommands.
    """
    positionals = [a for a in rest if not a.startswith("-")]
    for a in positionals:
        try:
            p = Path(a)
            if p.exists():
                return p
        except OSError:
            continue
    for a in positionals:
        if verb == "query" and a in _QUERY_COMMANDS:
            continue
        if "/" in a or "\\" in a or "." in Path(a).name:
            return Path(a)
    return None


def _has_long_flag(rest: Sequence[str], name: str) -> bool:
    return any(a == name or a.startswith(name + "=") for a in rest)


def _with_mdfix(config: Config, rest: Sequence[str]) -> List[str]:
    if config.mdfix and not _has_long_flag(rest, "--mdfix"):
        return ["--mdfix", config.mdfix, *rest]
    return list(rest)


def _defers_config(rest: Sequence[str]) -> bool:
    """
    True when the user named a config for the tool itself.

    The dispatcher must then inject nothing. Its own discovered settings would
    arrive as *explicit* flags, which beat --config in the tool's precedence
    order — so `mdtools terms --config ci.toml` would silently run with the
    glossary from the discovered config instead of the named one. Every tool
    reads mdtools.toml on its own now, so deferring costs nothing.
    """
    return _has_long_flag(rest, "--config")


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

    # Discovered from the input file so another repository's settings apply.
    start = _config_start(verb, rest)
    try:
        config = load(start)
    except ConfigError as exc:
        print(f"mdtools: {exc}", file=sys.stderr)
        return 2

    if verb == "config":
        # `mdtools config --config ci.toml` inspects a named file, which is
        # the natural way to check what a CI config actually resolves to.
        for i, arg in enumerate(rest):
            named = None
            if arg == "--config" and i + 1 < len(rest):
                named = rest[i + 1]
            elif arg.startswith("--config="):
                named = arg.split("=", 1)[1]
            if named:
                try:
                    config = load_file(Path(named))
                except ConfigError as exc:
                    print(f"mdtools: {exc}", file=sys.stderr)
                    return 2
                break
        print(json.dumps(config.resolved(), indent=2))
        return 0

    if verb == "fix":
        # Only long options suppress the profile; short flags like -q/-i still
        # take project wrap/profile (common: `mdtools fix -i file.md`).
        flags = [] if any(a.startswith("--") for a in rest) else fix_flags(config)
        try:
            result = subprocess.run([_find_mdfix(config), *flags, *rest])
        except FileNotFoundError:
            print(f"mdtools: mdfix not found ({_find_mdfix(config)})",
                  file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"mdtools: {exc}", file=sys.stderr)
            return 2
        return result.returncode

    if _defers_config(rest):
        module = {"check": "mdcheck", "query": "mdquery",
                  "links": "mdlinks", "terms": "mdterms"}.get(verb)
        if module:
            return _run_module(module, rest)

    if verb == "check":
        return _run_module("mdcheck", _with_mdfix(config, rest))
    if verb == "query":
        return _run_module("mdquery", _with_mdfix(config, rest))
    if verb == "links":
        return _run_module("mdlinks", _with_mdfix(config, rest))
    if verb == "terms":
        extra: List[str] = []
        if config.glossary and not _has_long_flag(rest, "--glossary"):
            extra.extend(["--glossary", str(config.glossary)])
        return _run_module("mdterms", [*extra, *_with_mdfix(config, rest)])
    if verb == "vary":
        extra = []
        if config.glossary and not _has_long_flag(rest, "--glossary"):
            extra.extend(["--glossary", str(config.glossary)])
        if config.state_dir and not _has_long_flag(rest, "--db"):
            extra.extend(["--db", str(config.state_dir / "prosevary.sqlite")])
        return _run_module("prosevary", [*extra, *rest])
    return 2


if __name__ == "__main__":
    sys.exit(main())
