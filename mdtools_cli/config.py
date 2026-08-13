"""
Project configuration: `mdtools.toml`.

Discovery walks up from the input file, then the working directory, stopping
at the first `mdtools.toml`. That is the same rule mdterms uses for the
glossary, so a repository behaves the same way whichever tool is invoked.

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
    profile: str = "canonical"           # mdfix profile for `mdtools fix`
    wrap: int = 0                        # 0 = no wrapping
    editorial: bool = False
    glossary: Optional[Path] = None
    state_dir: Optional[Path] = None
    mdfix: Optional[str] = None
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


_ALLOWED = {"profile", "wrap", "editorial", "glossary", "state_dir", "mdfix"}


def load(start: Optional[Path] = None) -> Config:
    root = find_root(start)
    path = find_config(start)
    config = Config(root=root, path=path)
    if path is None:
        return config

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
        if not isinstance(section["wrap"], int) or section["wrap"] < 0:
            raise ConfigError(f"{path}: wrap must be a non-negative integer")
        config.wrap = section["wrap"]
    if "editorial" in section:
        if not isinstance(section["editorial"], bool):
            raise ConfigError(f"{path}: editorial must be true or false")
        config.editorial = section["editorial"]
    for key in ("glossary", "state_dir"):
        if key in section:
            setattr(config, key, (root / str(section[key])).resolve())
    if "mdfix" in section:
        config.mdfix = str(section["mdfix"])
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
