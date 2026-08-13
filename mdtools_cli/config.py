"""
Project configuration: `mdtools.toml`.

Discovery walks up from one start path (the input file, or cwd), stopping at
the first `mdtools.toml` or `.git`. If neither is found, the start directory
is the project root.

No mutable state is written into the installed package tree — resolved paths
are always relative to the project root, never to the package.

TOML parsing uses `tomllib`, which is stdlib from Python 3.11. On 3.10 a
config file is reported rather than silently ignored: quietly ignoring
configuration is how a tool ends up doing something the project did not ask
for. Everything works without a config file on every supported version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ImportError:  # pragma: no cover - 3.10 only
    tomllib = None  # type: ignore

CONFIG_NAME = "mdtools.toml"

# Markers that end an upward walk when no config file is found.
ROOT_MARKERS = (".git", CONFIG_NAME)


class ConfigError(RuntimeError):
    """The config file could not be read, or says something unusable."""


@dataclass
class Config:
    root: Path
    path: Optional[Path] = None          # the mdtools.toml itself, if any
    # "none" when no config: parity with bare mdfix. Set in mdtools.toml to
    # opt into a profile.
    profile: str = "none"
    wrap: int = 0                        # 0 = no wrapping
    editorial: bool = False
    glossary: Optional[Path] = None
    state_dir: Optional[Path] = None
    mdfix: Optional[str] = None
    suppress: List[str] = field(default_factory=list)  # mdcheck rule ids / prefixes
    # Front-matter schema (issue #13). Empty means no schema, which means the
    # check does not run at all — a project without one is not "failing" it.
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def resolved(self) -> Dict[str, Any]:
        """What every tool would use, with provenance for `mdtools config`."""
        return {
            "root": str(self.root),
            "config": str(self.path) if self.path else None,
            "profile": self.profile,
            "wrap": self.wrap,
            "editorial": self.editorial,
            "frontmatter": self.frontmatter,
            "glossary": str(self.glossary) if self.glossary else None,
            "state_dir": str(self.state_dir) if self.state_dir else None,
            "mdfix": self.mdfix,
            "suppress": list(self.suppress),
        }


def find_root(start: Optional[Path] = None) -> Path:
    here = (start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    walk = here
    while True:
        for marker in ROOT_MARKERS:
            if (walk / marker).exists():
                return walk
        if walk == walk.parent:
            return here
        walk = walk.parent


def find_config(start: Optional[Path] = None) -> Optional[Path]:
    root = find_root(start)
    candidate = root / CONFIG_NAME
    return candidate if candidate.is_file() else None


_ALLOWED = {
    "profile", "wrap", "editorial", "glossary", "state_dir", "mdfix", "suppress",
    "frontmatter",
}

# What a field may declare, and what a value may be.
_FIELD_KEYS = {"type", "required", "one_of"}
_TYPES = {"string", "number", "bool", "list", "date", "any"}
# What to do about a key the schema does not mention.
_UNKNOWN = {"allow", "warn", "error"}


def _read_frontmatter(path: Path, section: Any) -> Dict[str, Any]:
    """
    The `[frontmatter]` table, validated as strictly as it will validate.

    A schema nobody checked is worse than none: a typo in `requried` would
    silently stop requiring anything, and the gate would pass a document
    missing every field it was supposed to have. So every key, type name and
    shape here is refused if it is not one this understands.
    """
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [frontmatter] must be a table")

    unknown = section.get("unknown", "allow")
    if unknown not in _UNKNOWN:
        raise ConfigError(
            f"{path}: frontmatter.unknown must be one of "
            f"{sorted(_UNKNOWN)}, not {unknown!r}")

    raw_fields = section.get("fields", {})
    if not isinstance(raw_fields, dict):
        raise ConfigError(f"{path}: [frontmatter.fields] must be a table")

    extra = sorted(set(section) - {"unknown", "fields"})
    if extra:
        raise ConfigError(
            f"{path}: unknown frontmatter setting(s) {extra}. "
            "Known: ['fields', 'unknown']")

    fields: Dict[str, Any] = {}
    for name, spec in raw_fields.items():
        if not isinstance(spec, dict):
            raise ConfigError(
                f"{path}: [frontmatter.fields.{name}] must be a table")
        bad = sorted(set(spec) - _FIELD_KEYS)
        if bad:
            raise ConfigError(
                f"{path}: frontmatter field {name!r} has unknown key(s) {bad}. "
                f"Known: {sorted(_FIELD_KEYS)}")
        kind = spec.get("type", "any")
        if kind not in _TYPES:
            raise ConfigError(
                f"{path}: frontmatter field {name!r} has type {kind!r}; "
                f"expected one of {sorted(_TYPES)}")
        required = spec.get("required", False)
        if not isinstance(required, bool):
            raise ConfigError(
                f"{path}: frontmatter field {name!r}: required must be "
                "true or false")
        one_of = spec.get("one_of")
        if one_of is not None:
            if not isinstance(one_of, list) or not one_of:
                raise ConfigError(
                    f"{path}: frontmatter field {name!r}: one_of must be a "
                    "non-empty list")
            normalized = []
            for item in one_of:
                if isinstance(item, datetime.datetime):
                    item = item.isoformat()
                elif isinstance(item, datetime.date):
                    item = item.isoformat()
                if isinstance(item, bool) or isinstance(item, (int, float, str)):
                    normalized.append(item)
                else:
                    raise ConfigError(
                        f"{path}: frontmatter field {name!r}: one_of "
                        "values must be strings, numbers, bools, or dates")
            one_of = normalized
        fields[str(name)] = {"type": kind, "required": required,
                             "one_of": one_of}

    return {"unknown": unknown, "fields": fields}


def _resolve_mdfix(root: Path, value: str) -> str:
    """Absolute path, root-relative path, or bare command name on PATH."""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    # Path-like relative values are project-root relative.
    if "/" in value or "\\" in value:
        return str((root / value).resolve())
    return value


def load(start: Optional[Path] = None) -> Config:
    """The config discovered by walking up from `start`."""
    root = find_root(start)
    path = find_config(start)
    if path is None:
        return Config(root=root, path=None)
    return _read(path, root)


def load_file(path: Path) -> Config:
    """
    A config file named outright, wherever it lives.

    The project root is still discovered from the file's directory rather than
    taken to *be* it, because `glossary` and `state_dir` are root-relative and
    a config kept in `ci/` should not silently reroot them there.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"{path}: not a file")
    return _read(path, find_root(path))


def _read(path: Path, root: Path) -> Config:
    config = Config(root=root, path=path)
    if tomllib is None:
        raise ConfigError(
            f"{path} needs Python 3.11 or newer to read (tomllib). "
            "Ignoring it would risk doing something the project did not ask "
            "for, so this is an error rather than a warning."
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    section = data.get("mdtools", data)
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [mdtools] must be a table")

    unknown = sorted(set(section) - _ALLOWED)
    if unknown:
        raise ConfigError(
            f"{path}: unknown setting(s) {unknown}. "
            f"Known: {sorted(_ALLOWED)}")

    config.raw = section
    if "profile" in section:
        value = section["profile"]
        if value not in ("none", "canonical", "technical"):
            raise ConfigError(
                f"{path}: profile must be none, canonical or technical")
        config.profile = value
    if "wrap" in section:
        value = section["wrap"]
        # bool is a subclass of int; reject true/false as wrap widths.
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"{path}: wrap must be a non-negative integer")
        config.wrap = value
    if "editorial" in section:
        if not isinstance(section["editorial"], bool):
            raise ConfigError(f"{path}: editorial must be true or false")
        config.editorial = section["editorial"]
    for key in ("glossary", "state_dir"):
        if key in section:
            setattr(config, key, (root / str(section[key])).resolve())
    if "mdfix" in section:
        config.mdfix = _resolve_mdfix(root, str(section["mdfix"]))
    if "frontmatter" in section:
        config.frontmatter = _read_frontmatter(path, section["frontmatter"])
    if "suppress" in section:
        value = section["suppress"]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ConfigError(
                f"{path}: suppress must be a list of rule id strings")
        config.suppress = list(value)
    return config


def fix_flags(config: Config) -> List[str]:
    """mdfix flags implied by the configuration."""
    flags: List[str] = []
    if config.profile == "canonical":
        flags.append("--canonical")
    elif config.profile == "technical":
        flags.append("--technical")
    if config.editorial and config.profile == "none":
        flags.append("--editorial")
    if config.wrap:
        flags.append(f"--wrap={config.wrap}")
    return flags
