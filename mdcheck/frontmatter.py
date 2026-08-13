"""
Front-matter schema check.

The IR locates the block; PyYAML reads the YAML. No schema means no check.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:                              # pragma: no cover
    yaml = None                                  # type: ignore


def _iso_date(text: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _is_date(value: Any) -> bool:
    if isinstance(value, datetime.date):
        return True
    return isinstance(value, str) and _iso_date(value) is not None


def _comparable(value: Any) -> Any:
    """one_of is stored JSON-safe; YAML dates become ISO strings."""
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


# What each declared type accepts. `date` covers a YAML timestamp *and* a
# quoted ISO string — many styles quote the date to stop YAML inventing one.
_MATCHES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "date": _is_date,
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


def _as_written(text: str, data: Dict[Any, Any],
                first_line: int) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """
    Keys as they appear in the YAML, not as `safe_load` constructed them.

    `2024: recap` loads as an int key; YAML 1.1 `on: true` loads as a bool
    key. Schema lookup, unknown detection, and line numbers all need the
    scalar text, or mixed extra keys crash `sorted` and miss the schema.
    """
    named: Dict[str, Any] = {}
    lines: Dict[str, int] = {}
    pairs = []
    if yaml is not None:
        try:
            node = yaml.compose(text)
        except yaml.YAMLError:
            node = None
        if node is not None:
            pairs = list(getattr(node, "value", []) or [])

    remaining = dict(data)
    for pair in pairs:
        if not (isinstance(pair, tuple) and len(pair) == 2):
            continue
        key_node = pair[0]
        name = str(getattr(key_node, "value", key_node))
        lines[name] = first_line + 1 + key_node.start_mark.line
        constructed: Any = name
        try:
            constructed = yaml.safe_load(name)
        except yaml.YAMLError:
            pass
        if constructed in remaining:
            named[name] = remaining.pop(constructed)
        elif name in remaining:
            named[name] = remaining.pop(name)

    for key, value in remaining.items():
        name = str(key)
        named[name] = value
        lines.setdefault(name, first_line)
    return named, lines


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
        raise RuntimeError("PyYAML is required to validate front matter")

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

    data, lines = _as_written(text, data, first_line)
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
        if (spec["one_of"] is not None
                and _comparable(value) not in spec["one_of"]):
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
