"""
Front-matter schema validation (issue #13).

The schema lives in `mdtools.toml`, not in a separate JSON Schema file. The
needs are modest — is this key present, is it the right kind of thing, is its
value one of these — and a second file in a second language to express them
would be more machinery than the question deserves. It also keeps the project
to one config format and no new dependency.

Off unless configured. A project without a `[frontmatter]` table is not
failing a check it never asked for, so with no schema this does nothing at
all — not even "every document should have front matter".

**Where the span comes from.** The IR says where the front matter is; PyYAML's
composer says which line each key is on. Both are asked rather than guessed,
so a finding points at the offending key and not at the block. Front matter is
YAML, not Markdown, so reading it here is not the boundary dialect-policy §2
draws — that one is about Markdown grammar, which still lives only in mdfix.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:                              # pragma: no cover
    yaml = None                                  # type: ignore

# What each declared type accepts. `date` covers what a YAML loader hands back
# for an unquoted `2026-08-13`, and a string that a reader would parse the
# same way — a quoted date is still a date to a human.
_MATCHES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "date": lambda v: isinstance(v, (datetime.date, datetime.datetime)),
    "any": lambda v: True,
}


def _describe(value: Any) -> str:
    """What the value actually is, in the schema's vocabulary."""
    for name in ("bool", "number", "string", "list", "date"):
        if _MATCHES[name](value):
            return name
    if value is None:
        return "empty"
    return type(value).__name__


def _key_lines(text: str, first_line: int) -> Dict[str, int]:
    """
    Line number of each top-level key, from PyYAML's own marks.

    Scanning for `^key:` would be close and occasionally wrong — a key inside
    a nested block, or one whose name appears in a value, would both fool it.
    The composer already knows, so ask it.

    `first_line` is the IR's line for the front-matter block: the opening
    `---`, so the YAML body starts one line later.
    """
    lines: Dict[str, int] = {}
    if yaml is None:
        return lines
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return lines
    if node is None or not hasattr(node, "value"):
        return lines
    for pair in getattr(node, "value", []):
        if isinstance(pair, tuple) and len(pair) == 2:
            key = pair[0]
            lines[str(key.value)] = first_line + 1 + key.start_mark.line
    return lines


def validate(text: str, schema: Dict[str, Any],
             first_line: int) -> List[Tuple[str, str, int, str]]:
    """
    Findings for one document's front matter.

    Returns `(rule, severity, line, message)` tuples; the caller owns spans and
    paths. Empty when there is no schema, which is the common case.
    """
    if not schema or not schema.get("fields") and schema.get("unknown") == "allow":
        return []
    if yaml is None:
        return [("check.frontmatter-unreadable", "error", first_line,
                 "PyYAML is required to validate front matter "
                 "(pip install pyyaml)")]

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # One line, not the loader's multi-line dump: this goes on a
        # diagnostics stream where a newline would corrupt the record.
        detail = " ".join(str(exc).split())
        return [("check.frontmatter-invalid", "error", first_line,
                 f"front matter is not valid YAML: {detail}")]

    if data is None:
        data = {}
    if not isinstance(data, dict):
        return [("check.frontmatter-invalid", "error", first_line,
                 f"front matter must be a mapping, not a "
                 f"{_describe(data)}")]

    lines = _key_lines(text, first_line)
    fields: Dict[str, Any] = schema.get("fields", {})
    out: List[Tuple[str, str, int, str]] = []

    for name, spec in sorted(fields.items()):
        if name not in data:
            if spec["required"]:
                out.append(("check.frontmatter-missing", "error", first_line,
                            f"front matter is missing required field "
                            f"{name!r}"))
            continue
        where = lines.get(name, first_line)
        value = data[name]
        if not _MATCHES[spec["type"]](value):
            out.append(("check.frontmatter-type", "error", where,
                        f"front matter field {name!r} should be a "
                        f"{spec['type']}, not a {_describe(value)}"))
            continue        # a wrong type makes one_of meaningless
        if spec["one_of"] is not None and value not in spec["one_of"]:
            allowed = ", ".join(repr(v) for v in spec["one_of"])
            out.append(("check.frontmatter-value", "error", where,
                        f"front matter field {name!r} is {value!r}; "
                        f"expected one of {allowed}"))

    unknown = schema.get("unknown", "allow")
    if unknown != "allow":
        severity = "error" if unknown == "error" else "warning"
        for name in sorted(set(data) - set(fields)):
            out.append(("check.frontmatter-unknown", severity,
                        lines.get(name, first_line),
                        f"front matter has no schema for field {name!r}"))

    out.sort(key=lambda row: (row[2], row[0]))
    return out
