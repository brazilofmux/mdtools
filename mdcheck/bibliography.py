"""
Which citation keys a document could resolve to (issue #13).

Off unless a bibliography is named. A document with citations and no
bibliography is not making a mistake — it may be assembled later, or cited
into a system mdtools knows nothing about — so with no source this reports
nothing at all.

Sources:

    front matter `references:`   inline CSL, extra keys (merged with files)
    front matter `bibliography:` a path, or a list of them
    mdtools.toml `bibliography`  the project default

A document that names `bibliography:` is saying what it cites against, and
the project default is not used — including an explicit empty list, which
is named-but-empty rather than not named. `references:` adds keys; it does
not replace a bibliography file.

Formats are BibTeX/BibLaTeX (`.bib`), CSL JSON and CSL YAML. Only the *keys*
are read — nothing here cares what a reference says, only which names exist,
so none of this is a citation formatter and none of it needs to become one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

try:
    import yaml
except ImportError:                              # pragma: no cover
    yaml = None                                  # type: ignore

# `@article{key,` — the entry types that are not entries are excluded, since
# `@string{...}` defines an abbreviation and `@comment` holds prose.
_BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s}]+)\s*,", re.M)
_NOT_ENTRIES = {"string", "comment", "preamble"}


def _bib_keys(text: str) -> Set[str]:
    return {key for kind, key in _BIB_ENTRY.findall(text)
            if kind.lower() not in _NOT_ENTRIES}


def _csl_keys(items: object) -> Set[str]:
    """`id` from each entry of a CSL list."""
    out: Set[str] = set()
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and "id" in item:
                out.add(str(item["id"]))
    return out


def read_keys(path: Path) -> Tuple[Set[str], Optional[str]]:
    """
    Every key in one bibliography file, and why not if it could not be read.

    An unreadable bibliography is reported rather than treated as empty. Empty
    would mean *every* citation is unresolved, which buries the real finding
    under one per citation in the document.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return set(), f"{path}: {exc.strerror or exc}"
    except UnicodeDecodeError:
        return set(), f"{path}: not valid UTF-8"

    suffix = path.suffix.lower()
    if suffix == ".bib":
        return _bib_keys(text), None
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return set(), f"{path}: not valid JSON ({exc.msg})"
        return _read_csl(data, path)
    if suffix in (".yaml", ".yml"):
        if yaml is None:
            return set(), f"{path}: PyYAML is required to read a CSL YAML file"
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            detail = " ".join(str(exc).split())
            return set(), f"{path}: not valid YAML ({detail})"
        return _read_csl(data, path)
    return set(), (f"{path}: unknown bibliography format {suffix!r}; "
                   "expected .bib, .json, .yaml or .yml")


def _read_csl(data: object, path: Path) -> Tuple[Set[str], Optional[str]]:
    """A CSL list, or a mapping wrapping one under `references`."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get("references"), list):
        items = data["references"]
    else:
        return set(), (f"{path}: not a CSL bibliography; "
                       "expected a list or a mapping with a references list")
    return _csl_keys(items), None


def _as_paths(value: object) -> Optional[List[str]]:
    """A path or a list of them, or None when the value is some other kind."""
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, list):
        if not all(isinstance(v, (str, Path)) for v in value):
            return None
        return [str(v) for v in value]
    return None


def resolve(front: object, configured: Optional[object],
            document: Path) -> Tuple[Optional[Set[str]], List[str]]:
    """
    The keys this document can cite, and any problems reading them.

    Returns `(None, errors)` when no bibliography is named at all — which is
    different from an empty one. None means "do not check"; an empty set means
    "checked, and nothing matches".
    """
    errors: List[str] = []
    keys: Set[str] = set()
    named = False
    # A document that names bibliography: — including [] — is saying what
    # it cites against. references: only adds keys.
    own_files = False

    if isinstance(front, dict):
        if front.get("references") is not None:
            named = True
            keys |= _csl_keys(front.get("references"))
        if front.get("bibliography") is not None:
            named = True
            own_files = True
            paths = _as_paths(front.get("bibliography"))
            if paths is None:
                errors.append(
                    f"{document}: bibliography must be a path or a list of paths")
            else:
                for name in paths:
                    found, error = read_keys(document.parent / name)
                    keys |= found
                    if error:
                        errors.append(error)

    if not own_files and configured:
        for name in _as_paths(configured) or []:
            named = True
            found, error = read_keys(Path(name))
            keys |= found
            if error:
                errors.append(error)

    return (keys if named else None), errors
