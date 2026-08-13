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
    raw: Dict[str, Any] = field(default_factory=dict)

    def resolved(self) -> Dict[str, Any]:
        """What every tool would use, with provenance for `mdtools config`."""
        return {
            "root": str(self.root),
            "config": str(self.path) if self.path else None,
            "profile": self.profile,
            "wrap": self.wrap,
            "editorial": self.editorial,
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
}


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
