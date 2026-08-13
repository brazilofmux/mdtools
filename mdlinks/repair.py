"""
Suggest repairs for broken links, and turn confident ones into edits.

**No Markdown grammar.** The byte span of every destination comes from the IR
(`destinationStart` / `destinationEnd`), so repairing a link never means
working out where its text stops and its destination starts — that is
mdfix's job, and the reason those fields exist.

Two failures are repairable, and they are the two issue #14 names: a heading
was renamed, or a file was moved. Everything else is reported and left alone.

The governing rule is the issue's: *ambiguous targets require human choice*.
A suggestion is made only when exactly one candidate survives, and even then
nothing is written — `--edits` prints an edit list, and `mdfix --apply-edits`
is what touches the file, after validating it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from mdquery.slug import slugify

from .graph import Document, Finding, _split

# Repairable rules. `links.undefined-reference` is deliberately absent: a
# reference with no definition is usually a *missing definition*, and renaming
# the label to whichever one is closest would silently point the text at
# another destination entirely. That is not a repair, it is a guess with a
# convincing diff.
REPAIRABLE = frozenset({"links.broken-anchor", "links.missing-file"})

# Bare destinations cannot carry these without <> or escapes (balanced ()
# are allowed in CommonMark, so parentheses are not in this set).
_UNWRITABLE = set(" \t\"'<>\\")


@dataclass
class Suggestion:
    """One finding's candidates, and the edit if there is exactly one."""
    finding: Finding
    candidates: List[str]
    replacement: Optional[str] = None    # the whole new destination
    reason: str = ""
    # True when a unique candidate exists but cannot be written bare.
    unwritable: bool = False
    # "high" for an exact match, "medium" for a nearest-neighbour one. The
    # split is exact-versus-fuzzy and nothing finer: an edit-distance winner
    # is a different *kind* of answer from an identifier that matched, and
    # there is no ratio between them worth inventing a number for.
    confidence: str = "high"

    @property
    def confident(self) -> bool:
        return self.replacement is not None

    def to_edit(self) -> dict:
        # severity / confidence / explanation are issue #12's edit model.
        # mdfix does not act on them — it applies a "medium" edit exactly as
        # it applies a "high" one — but `mdfix --apply-edits --diff` shows
        # them, so a reviewer sees the judgement rather than a byte range.
        target = self.finding.target
        return {
            "start": target.start,
            "end": target.end,
            "replacement": self.replacement,
            "rule": self.finding.rule,
            "expect": target.destination,
            "severity": self.finding.severity,
            "confidence": self.confidence,
            "explanation": self.reason,
        }


def _distance(a: str, b: str, cap: int) -> int:
    """
    Levenshtein, abandoned once every cell exceeds `cap`.

    The cap is not only speed. A repair is only offered inside a tight bound,
    so a distance past it never needs its exact value — and returning cap + 1
    keeps a far-away candidate from ever winning a comparison.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1,
                               current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def _near(broken: str, options: Sequence[str]) -> List[str]:
    """
    Candidates at the unique minimum edit distance, within a bound.

    The bound scales with length because a typo in `#faq` and a typo in
    `#configuration-reference` are not the same size of mistake, and a fixed
    threshold would either miss the second or fabricate matches for the first.
    """
    cap = max(1, len(broken) // 4)
    scored: List[Tuple[int, str]] = []
    for option in options:
        d = _distance(broken, option, cap)
        if d <= cap:
            scored.append((d, option))
    if not scored:
        return []
    best = min(d for d, _ in scored)
    return sorted(option for d, option in scored if d == best)


def _anchor_candidates(broken: str,
                       anchors: Sequence[str]) -> Tuple[List[str], str, str]:
    """
    Anchors that might be meant by `broken`, best tier first.

    Tier order matters more than the tiers themselves: an exact answer must
    never be outvoted by a fuzzy one. The first tier that produces anything
    wins outright, even if a later tier would have found more.
    """
    if not anchors:
        return [], "", "high"

    # 1. The author wrote the heading's *text* where its identifier belongs —
    #    `#Installation Guide` for `installation-guide`. Deterministic, and by
    #    far the most common way a hand-written anchor is wrong.
    slugged = slugify(broken)
    if slugged and slugged in anchors:
        return [slugged], "the heading's identifier for that text", "high"

    # 2. Case, which Pandoc's identifiers do not carry.
    folded = [a for a in anchors if a.casefold() == broken.casefold()]
    if folded:
        return sorted(set(folded)), "the same anchor, differently cased", "high"

    # 3. A typo, or a heading whose wording moved.
    near = _near(broken, anchors)
    if near:
        # Fuzzy. Unique at this distance, but a guess in a way the two tiers
        # above are not.
        return near, "the closest anchor in that file", "medium"
    return [], "", "high"


def _scope_files(docs: Sequence[Document]) -> List[Path]:
    """
    Files a moved target may have moved *to*.

    The documents in the run, plus everything sitting beside them. mdlinks
    already refuses to judge a file outside the run; searching the whole tree
    for a repair would break that symmetry in the more dangerous direction,
    since a repair rewrites the document while a check only complains.
    """
    found: Dict[Path, None] = {}
    for doc in docs:
        found[doc.path.resolve()] = None
    for directory in {d.path.resolve().parent for d in docs}:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file():
                found[entry.resolve()] = None
    return list(found)


def _relative(target: Path, source: Path) -> str:
    """
    Path from the linking file to the target, as a destination is written.

    Both sides are resolved first. Candidates arrive resolved and `source` does
    not, so on any tree reached through a symlink — macOS `/tmp`, a home
    directory on a network mount — the two disagree about where they are and
    `relpath` answers with a chain of `..` back to the root. It is a correct
    path and a useless one.
    """
    return Path(os.path.relpath(target.resolve(),
                                source.resolve().parent)).as_posix()


def suggest(docs: Sequence[Document],
            findings: Sequence[Finding]) -> List[Suggestion]:
    """Every repairable finding, with its candidates and, when unique, an edit."""
    by_path = {d.path.resolve(): d for d in docs}
    scope = _scope_files(docs)
    out: List[Suggestion] = []

    for finding in findings:
        if finding.rule not in REPAIRABLE or finding.target is None:
            continue
        doc = by_path.get(Path(finding.path).resolve())
        if doc is None:
            continue

        destination = finding.target.destination
        # Same split as check(): decode percent-escapes in the fragment.
        target, anchor = _split(destination)

        if finding.rule == "links.broken-anchor":
            other = doc
            if target:
                other = by_path.get((doc.path.parent / target).resolve())
                if other is None:
                    continue
            candidates, reason, confidence = _anchor_candidates(
                anchor, other.anchors)
            replacements = [f"{target}#{c}" for c in candidates]
        else:
            wanted = Path(target).name
            matches = [p for p in scope if p.name == wanted
                       and p != doc.path.resolve()]
            candidates = sorted(_relative(p, doc.path) for p in matches)
            reason = "a file with that name, elsewhere in the run"
            # Exact: the basename matched, and a second match would have made
            # this ambiguous and stopped it.
            confidence = "high"
            suffix = f"#{anchor}" if anchor else ""
            replacements = [c + suffix for c in candidates]

        replacement = None
        unwritable = False
        if len(replacements) == 1:
            if _UNWRITABLE & set(replacements[0]):
                unwritable = True
            else:
                replacement = replacements[0]
        out.append(Suggestion(finding=finding, candidates=candidates,
                              replacement=replacement, reason=reason,
                              unwritable=unwritable,
                              confidence=confidence))
    return out


def edits_for(suggestions: Sequence[Suggestion], path: Path) -> List[dict]:
    """
    Edits for one document, refusing any cluster that overlaps.

    Same rule as mdterms: overlapping edits are dropped together rather than
    resolved by taking the first, because an order-dependent winner makes the
    result depend on how the tool walked the file instead of on the file.
    In practice destinations never overlap; the check is here so that a future
    repair which edits something else cannot quietly introduce the problem.
    """
    resolved = Path(path).resolve()
    # One reference definition used by two links produces two findings — both
    # real, since both links are broken — but only one destination, and so
    # only one edit. Collapsing identical (span, replacement) pairs first
    # matters: without it the overlap rule below sees two edits at the same
    # span, calls them a conflict, and drops the repair entirely.
    seen: Dict[Tuple[int, int, str], Suggestion] = {}
    for s in suggestions:
        if not s.confident or Path(s.finding.path).resolve() != resolved:
            continue
        seen.setdefault(
            (s.finding.target.start, s.finding.target.end, s.replacement), s)
    confident = sorted(
        seen.values(),
        key=lambda s: (s.finding.target.start, s.finding.target.end),
    )
    drop: set = set()
    for i, a in enumerate(confident):
        for j in range(i + 1, len(confident)):
            if confident[j].finding.target.start >= a.finding.target.end:
                break
            drop.add(i)
            drop.add(j)
    return [s.to_edit() for i, s in enumerate(confident) if i not in drop]
