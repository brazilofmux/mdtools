"""
Markdown-aware segmentation for prosevary.

Goal: identify spans that may be paraphrased (prose sentences inside
paragraphs) and leave everything else byte-identical on reassembly.

This is deliberately conservative. Ambiguous structures stay frozen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterator, List, Optional, Sequence, Tuple


class LineKind(Enum):
    BLANK = auto()
    HEADING = auto()
    FENCE = auto()
    TABLE = auto()
    BLOCKQUOTE = auto()
    LIST = auto()
    HR = auto()
    HTML = auto()
    FRONT_MATTER = auto()
    TEXT = auto()  # ordinary paragraph line — candidate region


_HEADING = re.compile(r"^#{1,6}\s")
_FENCE_OPEN = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")
_TABLE = re.compile(r"^\|")
_BLOCKQUOTE = re.compile(r"^>\s?")
_LIST = re.compile(r"^(\s*)([-*+]|\d+\.)\s+")
_HR = re.compile(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$")
_HTML = re.compile(r"^</?[a-zA-Z]")
# Sentence end: .!? optional close-quote, whitespace, next sentence-ish token.
# Conservative; abbrev false splits (e.g. "Dr. Smith") are acceptable for v0.
_SENT_SPLIT = re.compile(
    r'(?<=[.!?])["\'”’]?\s+(?=["\'“‘]?[A-Z0-9])'
)


@dataclass
class Line:
    kind: LineKind
    text: str  # includes trailing newline if present in source
    raw: str  # same as text for now; kept for future mdfix-style tracking


@dataclass
class Sentence:
    """One paraphrasable sentence, located inside a prose region."""

    text: str
    start: int  # char offset into the joined prose region
    end: int
    region_id: int


@dataclass(frozen=True)
class FenceState:
    """Delimiter information needed to recognize the matching closer."""

    marker: str
    length: int
    indent: int


@dataclass
class Region:
    """Contiguous run of TEXT lines that form one or more paragraphs."""

    region_id: int
    line_start: int  # inclusive index into lines[]
    line_end: int  # exclusive
    text: str  # joined content without final file-level concerns
    sentences: List[Sentence] = field(default_factory=list)


@dataclass
class Document:
    lines: List[Line]
    regions: List[Region]

    def reconstruct(self, replacements: dict[Tuple[int, int], str]) -> str:
        """
        Rebuild the file. replacements keys are (region_id, sent_index) → new text.
        Sentences not in replacements stay original.
        """
        # Build per-region new body text from sentences.
        region_bodies: dict[int, str] = {}
        for reg in self.regions:
            if not reg.sentences:
                region_bodies[reg.region_id] = reg.text
                continue
            parts: List[str] = []
            cursor = 0
            for i, sent in enumerate(reg.sentences):
                if sent.start > cursor:
                    parts.append(reg.text[cursor : sent.start])
                new = replacements.get((reg.region_id, i), sent.text)
                parts.append(new)
                cursor = sent.end
            if cursor < len(reg.text):
                parts.append(reg.text[cursor:])
            region_bodies[reg.region_id] = "".join(parts)

        out: List[str] = []
        i = 0
        region_by_start = {r.line_start: r for r in self.regions}
        while i < len(self.lines):
            if i in region_by_start:
                reg = region_by_start[i]
                body = region_bodies[reg.region_id]
                # Preserve how lines joined: we joined with '' after keeping
                # each line's original text (including newlines).
                out.append(body)
                i = reg.line_end
            else:
                out.append(self.lines[i].text)
                i += 1
        return "".join(out)


def _fence_opener(text: str) -> Optional[FenceState]:
    """Return a CommonMark/Pandoc fence descriptor, or None."""
    line = text.rstrip("\r\n")
    m = _FENCE_OPEN.fullmatch(line)
    if m is None:
        return None
    run = m.group("marker")
    # Backtick info strings cannot themselves contain a backtick. Without
    # this guard an inline-code-looking prose line can open a block forever.
    if run[0] == "`" and "`" in m.group("rest"):
        return None
    return FenceState(
        marker=run[0],
        length=len(run),
        indent=len(m.group("indent")),
    )


def _is_fence_closer(text: str, fence: FenceState) -> bool:
    """Whether text is a valid closer for fence."""
    line = text.rstrip("\r\n")
    i = 0
    while i < len(line) and i < 3 and line[i] == " ":
        i += 1
    if i < len(line) and line[i] == " ":
        return False
    start = i
    while i < len(line) and line[i] == fence.marker:
        i += 1
    if i - start < fence.length:
        return False
    return not line[i:].strip(" \t")


def classify_line(
    text: str,
    fence: Optional[FenceState],
    in_front_matter: bool,
    line_no: int,
) -> Tuple[LineKind, Optional[FenceState], bool]:
    """Return (kind, new fence state, new front-matter state)."""
    stripped = text.rstrip("\r\n")
    # YAML front matter: --- on line 0 opens, --- later closes.
    if line_no == 0 and stripped.strip() == "---":
        return LineKind.FRONT_MATTER, fence, True
    if in_front_matter:
        if stripped.strip() == "---":
            return LineKind.FRONT_MATTER, fence, False
        return LineKind.FRONT_MATTER, fence, True

    if fence is not None:
        if _is_fence_closer(stripped, fence):
            return LineKind.FENCE, None, False
        return LineKind.FENCE, fence, False

    opener = _fence_opener(stripped)
    if opener is not None:
        return LineKind.FENCE, opener, False
    if not stripped.strip():
        return LineKind.BLANK, None, False
    if _HEADING.match(stripped):
        return LineKind.HEADING, None, False
    if _HR.match(stripped.strip()):
        return LineKind.HR, None, False
    if _TABLE.match(stripped):
        return LineKind.TABLE, None, False
    if _BLOCKQUOTE.match(stripped):
        return LineKind.BLOCKQUOTE, None, False
    if _LIST.match(stripped):
        return LineKind.LIST, None, False
    if _HTML.match(stripped):
        return LineKind.HTML, None, False
    return LineKind.TEXT, None, False


def split_sentences(prose: str) -> List[Tuple[int, int, str]]:
    """
    Return list of (start, end, text) for sentences in prose.
    Conservative: if we can't split, one sentence = whole string (stripped of
    leading/trailing whitespace only at edges when alone).
    """
    if not prose or not prose.strip():
        return []

    # Work on the full string; keep offsets absolute.
    spans: List[Tuple[int, int]] = []
    last = 0
    for m in _SENT_SPLIT.finditer(prose):
        end = m.start()
        # include trailing close-quotes already in lookbehind area — m.start()
        # is at the whitespace after punct. Sentence ends at first whitespace.
        # Actually finditer position is start of whitespace after punct.
        # We want end after the punctuation/quote.
        end = m.start()
        if end > last:
            spans.append((last, end))
        last = m.end()  # skip the whitespace between sentences
    if last < len(prose):
        spans.append((last, len(prose)))

    out: List[Tuple[int, int, str]] = []
    for a, b in spans:
        chunk = prose[a:b]
        if not chunk.strip():
            continue
        # Trailing whitespace belongs to the gap between sentences, not to the
        # sentence. The final span runs to end-of-region, so leaving the
        # newline inside it means an accepted rewrite silently eats it.
        # reconstruct() re-emits anything outside a span verbatim.
        trimmed = chunk.rstrip()
        out.append((a, a + len(trimmed), trimmed))
    return out


def parse(source: str) -> Document:
    raw_lines = source.splitlines(keepends=True)
    if not raw_lines and source == "":
        return Document(lines=[], regions=[])

    lines: List[Line] = []
    fence: Optional[FenceState] = None
    in_fm = False
    for i, raw in enumerate(raw_lines):
        kind, fence, in_fm = classify_line(raw, fence, in_fm, i)
        lines.append(Line(kind=kind, text=raw, raw=raw))

    # Group contiguous TEXT lines into regions (broken by blank/other).
    regions: List[Region] = []
    i = 0
    rid = 0
    while i < len(lines):
        if lines[i].kind != LineKind.TEXT:
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].kind == LineKind.TEXT:
            i += 1
        end = i
        body = "".join(L.text for L in lines[start:end])
        reg = Region(region_id=rid, line_start=start, line_end=end, text=body)
        for s_i, (a, b, t) in enumerate(split_sentences(body)):
            reg.sentences.append(Sentence(text=t, start=a, end=b, region_id=rid))
        regions.append(reg)
        rid += 1

    return Document(lines=lines, regions=regions)


def iter_sentences(doc: Document) -> Iterator[Tuple[Region, int, Sentence]]:
    for reg in doc.regions:
        for i, sent in enumerate(reg.sentences):
            yield reg, i, sent
