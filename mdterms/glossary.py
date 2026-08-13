"""
The glossary file.

Extends the schema prosevary already reads, so one file serves both:

    terms:
      - term: SLOW-32              # the preferred spelling
        aliases: [Slow-32]         # acceptable; frozen, never rewritten
        forbidden: [SLOW32, slow32]  # rewritten to `term`
        case_sensitive: true       # default; false ignores case when matching
        expansion: SLOW 32-bit ISA # must introduce the term at first use
        exempt: ["CHANGELOG.md"]   # glob patterns this term does not apply to

`aliases` and `forbidden` differ in intent and that difference is the point.
An alias is a spelling you tolerate — prosevary freezes it so a paraphrase
cannot touch it. A forbidden variant is one you want gone, and mdterms will
rewrite it.

`expansion` is issue #16's first-use definition and acronym introduction,
which are the same rule seen twice: the first time a document uses the term,
the words it stands for must be next to it. `exempt` is #16's domain-specific
exceptions — a changelog quoting old release notes should not be told to
introduce an acronym it is only citing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by the import guard test
    yaml = None  # type: ignore


class GlossaryError(RuntimeError):
    """The glossary could not be read, or does not say what it must."""


@dataclass
class Term:
    term: str
    aliases: List[str] = field(default_factory=list)
    forbidden: List[str] = field(default_factory=list)
    case_sensitive: bool = True
    # The words the term stands for. When set, a document must introduce the
    # term the first time it uses it.
    expansion: str = ""
    # Glob patterns, matched against the path as given, where this term's
    # rules do not apply.
    exempt: List[str] = field(default_factory=list)

    def applies_to(self, path) -> bool:
        """
        False where the term is exempt (#16's domain-specific exceptions).

        Matched against the path as written on the command line, and also
        against its name, so `exempt: ["CHANGELOG.md"]` works whether the file
        was named as `CHANGELOG.md` or `docs/CHANGELOG.md`. A pattern with a
        separator in it is matched only against the whole path, because that
        is someone being specific about where.
        """
        import fnmatch
        text = str(path)
        for pattern in self.exempt:
            if fnmatch.fnmatch(text, pattern):
                return False
            if "/" not in pattern and fnmatch.fnmatch(Path(text).name, pattern):
                return False
        return True

    @property
    def frozen(self) -> List[str]:
        """Spellings prosevary must not paraphrase: the term and its aliases."""
        return [self.term, *self.aliases]


def load(path: Path) -> List[Term]:
    if not path.is_file():
        raise GlossaryError(f"{path}: no such file")
    if yaml is None:
        raise GlossaryError(
            "PyYAML is required to read a glossary (pip install pyyaml)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise GlossaryError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GlossaryError(f"{path}: expected a mapping at the top level")

    out: List[Term] = []
    seen: dict = {}
    for n, entry in enumerate(data.get("terms") or [], 1):
        if not isinstance(entry, dict):
            raise GlossaryError(f"{path}: entry {n} is not a mapping")
        name = entry.get("term")
        if name is None or str(name) == "":
            raise GlossaryError(f"{path}: entry {n} has no `term`")
        name = str(name)
        if name.lower() in seen:
            raise GlossaryError(
                f"{path}: `{name}` is defined twice (also line-ish entry "
                f"{seen[name.lower()]})")
        seen[name.lower()] = n
        aliases: List[str] = []
        for a in entry.get("aliases") or []:
            s = str(a)
            if not s:
                raise GlossaryError(
                    f"{path}: entry {n} has an empty alias spelling")
            aliases.append(s)
        forbidden: List[str] = []
        for f in entry.get("forbidden") or []:
            s = str(f)
            if not s:
                raise GlossaryError(
                    f"{path}: entry {n} has an empty forbidden spelling")
            forbidden.append(s)
        exempt: List[str] = []
        for g in entry.get("exempt") or []:
            s_g = str(g)
            if not s_g:
                raise GlossaryError(
                    f"{path}: entry {n} has an empty exempt pattern")
            exempt.append(s_g)
        expansion = str(entry.get("expansion") or "")
        term = Term(
            term=name,
            aliases=aliases,
            forbidden=forbidden,
            case_sensitive=bool(entry.get("case_sensitive", True)),
            expansion=expansion,
            exempt=exempt,
        )
        if expansion and expansion == name:
            raise GlossaryError(
                f"{path}: `{name}` expands to itself, so no introduction "
                "could ever satisfy it")
        # A spelling cannot be both tolerated and forbidden; that would make
        # the fix non-deterministic, and mdterms only applies unambiguous ones.
        #
        # Compared the way the term itself is matched. Enforcing
        # capitalization is the point of `case_sensitive: true` — `Pandoc`
        # preferred with `pandoc` forbidden differs only in case, and folding
        # here would reject the very rule the glossary exists to express.
        fold = (lambda x: x) if term.case_sensitive else str.lower
        clash = {fold(a) for a in term.aliases} & {fold(f) for f in term.forbidden}
        if clash:
            raise GlossaryError(
                f"{path}: `{name}` lists {sorted(clash)} as both an alias and "
                "forbidden")
        if fold(term.term) in {fold(f) for f in term.forbidden}:
            raise GlossaryError(
                f"{path}: `{name}` forbids its own preferred spelling")
        out.append(term)
    return out


def freeze_set(terms: Sequence[Term]) -> List[str]:
    """Every spelling that must survive a paraphrase, for prosevary."""
    out: List[str] = []
    for term in terms:
        for spelling in term.frozen:
            if spelling not in out:
                out.append(spelling)
    return out


def find(path: Optional[Path], start: Optional[Path] = None) -> Optional[Path]:
    """Explicit path, else `glossary_terms.yaml` walking up from `start`."""
    if path is not None:
        return path
    here = (start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    while True:
        candidate = here / "glossary_terms.yaml"
        if candidate.is_file():
            return candidate
        if here == here.parent:
            return None
        here = here.parent
