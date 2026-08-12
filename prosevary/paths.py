"""
Project-aware path defaults for prosevary.

Mutable state must not live inside the installed package tree. Glossary
discovery starts from the input document, not only cwd.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional


def _walk_up(start: Path) -> Iterator[Path]:
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    while True:
        yield cur
        if cur == cur.parent:
            break
        cur = cur.parent


def project_root_for(input_path: Optional[Path] = None) -> Optional[Path]:
    """
    Nearest directory that looks like a project root for the input (or cwd).

    Markers: .git, mdtools.toml, glossary_terms.yaml.
    """
    starts: list[Path] = []
    if input_path is not None:
        starts.append(input_path)
    starts.append(Path.cwd())
    seen: set[Path] = set()
    for start in starts:
        for d in _walk_up(start):
            if d in seen:
                continue
            seen.add(d)
            if (
                (d / ".git").exists()
                or (d / "mdtools.toml").is_file()
                or (d / "glossary_terms.yaml").is_file()
            ):
                return d
    return None


def default_glossary_path(start: Optional[Path] = None) -> Optional[Path]:
    """
    Resolve glossary_terms.yaml.

    Order:
      1. $PROSEVARY_GLOSSARY
      2. Walk up from start (input file/dir), then from cwd
    """
    env = os.environ.get("PROSEVARY_GLOSSARY")
    if env:
        return Path(env)

    starts: list[Path] = []
    if start is not None:
        starts.append(start)
    starts.append(Path.cwd())
    seen: set[Path] = set()
    for root in starts:
        for d in _walk_up(root):
            if d in seen:
                continue
            seen.add(d)
            candidate = d / "glossary_terms.yaml"
            if candidate.is_file():
                return candidate
    return None


def default_db_path(input_path: Optional[Path] = None) -> Path:
    """
    SQLite path for run/synonym/embedding state.

    Order:
      1. $PROSEVARY_DB
      2. <project>/.prosevary/prosevary.sqlite (near input or cwd markers)
      3. $XDG_STATE_HOME/prosevary/prosevary.sqlite
      4. ~/.local/state/prosevary/prosevary.sqlite

    Never defaults inside the installed package tree.
    """
    env = os.environ.get("PROSEVARY_DB")
    if env:
        return Path(env)

    root = project_root_for(input_path)
    if root is not None:
        return root / ".prosevary" / "prosevary.sqlite"

    if input_path is not None:
        base = input_path.resolve()
        if base.is_file():
            base = base.parent
        return base / ".prosevary" / "prosevary.sqlite"

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "prosevary" / "prosevary.sqlite"
    return Path.home() / ".local" / "state" / "prosevary" / "prosevary.sqlite"
