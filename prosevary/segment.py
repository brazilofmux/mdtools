"""
Markdown-aware segmentation for prosevary.

Goal: identify spans that may be paraphrased (prose sentences inside
paragraphs) and leave everything else byte-identical on reassembly.

This is deliberately conservative. Ambiguous structures stay frozen.
Block classification is multi-line-aware (tables, HTML, setext, indented
code, reference definitions) so structural regions never reach generation.
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
    INDENTED_CODE = auto()
    REFERENCE = auto()  # link/image ref defs, footnote defs
    TEXT = auto()  # ordinary paragraph line — candidate region


_HEADING = re.compile(r"^#{1,6}\s")
# Openers accept any indentation: a fence inside an ordered list item sits at
# content column 4+ and is a real fence. Capping it here classified the block
# as TEXT and handed shell code to the paraphraser. Closers are stricter —
# see _is_fence_closer.
_FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")
_TABLE_LEADING = re.compile(r"^\|")
_BLOCKQUOTE = re.compile(r"^>\s?")
_LIST = re.compile(r"^(\s*)([-*+]|\d+\.)\s+")
_HR = re.compile(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$")
# HTML block openers (CommonMark types 1–7, conservative).
_HTML_OPEN = re.compile(r"^ {0,3}</?[a-zA-Z]")
_HTML_COMMENT = re.compile(r"^ {0,3}<!--")
_HTML_DECL = re.compile(r"^ {0,3}<![A-Z]")
_HTML_CDATA = re.compile(r"^ {0,3}<!\[CDATA\[")
_HTML_PI = re.compile(r"^ {0,3}<\?")
# Complete single-line HTML comment.
_HTML_COMMENT_FULL = re.compile(r"^ {0,3}<!--.*?-->\s*$")
# Setext underlines: 0–3 spaces, then only = or only - (CM allows 1+; we
# require 3+ dashes so a lone "-" in prose is not an underline).
_SETEXT_EQ = re.compile(r"^ {0,3}=+\s*$")
_SETEXT_DASH = re.compile(r"^ {0,3}-{3,}\s*$")
# Link/image reference definitions and Pandoc footnote definitions.
_REF_DEF = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s+\S")
_FOOTNOTE_DEF = re.compile(r"^ {0,3}\[\^[^\]\n]+\]:")
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
    """
    Whether text is a valid closer for fence.

    Indentation may exceed the opener's by at most 3, the CommonMark rule
    relative to the container. Unlike the opener this must stay strict: a
    more deeply indented delimiter inside the block is content, and accepting
    it as a closer would truncate the block.
    """
    line = text.rstrip("\r\n")
    limit = fence.indent + 3
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    if i > limit:
        return False
    start = i
    while i < len(line) and line[i] == fence.marker:
        i += 1
    if i - start < fence.length:
        return False
    return not line[i:].strip(" \t")


def _is_setext_underline(line: str) -> bool:
    return bool(_SETEXT_EQ.match(line) or _SETEXT_DASH.match(line))


def _setext_text_ok(line: str) -> bool:
    """A line that may be the text of a setext heading (not blank/structural)."""
    if not line.strip():
        return False
    if line.lstrip().startswith("#"):
        return False
    if _BLOCKQUOTE.match(line) or _LIST.match(line):
        return False
    return True


def _is_table_separator(line: str) -> bool:
    """
    GFM table delimiter row: dashes/colons separated by pipes.

    Accepts both `| --- | --- |` and `--- | ---` forms.
    """
    s = line.strip()
    if "|" not in s or "-" not in s:
        return False
    # Strip outer pipes for the cell check.
    core = s
    if core.startswith("|"):
        core = core[1:]
    if core.endswith("|"):
        core = core[:-1]
    cells = core.split("|")
    if not cells:
        return False
    for cell in cells:
        if not re.fullmatch(r":?-{3,}:?", cell.strip()):
            return False
    return True


def _looks_like_table_row(line: str) -> bool:
    """Any non-empty line containing a pipe — only used after a separator context."""
    return bool(line.strip()) and "|" in line


def _is_html_block_start(line: str) -> bool:
    return bool(
        _HTML_COMMENT.match(line)
        or _HTML_DECL.match(line)
        or _HTML_CDATA.match(line)
        or _HTML_PI.match(line)
        or _HTML_OPEN.match(line)
    )


def _is_html_block_complete(line: str) -> bool:
    """Single-line HTML that should not open a multi-line HTML region."""
    if _HTML_COMMENT_FULL.match(line):
        return True
    # Self-contained one-line tag with a closer on the same line, e.g. <br/>.
    if re.match(r"^ {0,3}<[^>]+/>\s*$", line):
        return True
    if re.match(r"^ {0,3}</?[a-zA-Z][^>]*>\s*$", line) and line.count("<") == 1:
        # Single open or close tag alone — still start a block if it's an open
        # container; only treat pure close tags as complete.
        if re.match(r"^ {0,3}</", line):
            return True
    return False


def _is_indented_code(line: str) -> bool:
    """Four spaces or a tab at the start — CommonMark indented code signal."""
    if not line:
        return False
    if line.startswith("\t"):
        return True
    return line.startswith("    ")


def _is_reference_def(line: str) -> bool:
    return bool(_FOOTNOTE_DEF.match(line) or _REF_DEF.match(line))


def classify_lines(raw_lines: Sequence[str]) -> List[Line]:
    """
    Multi-line-aware classification. Non-TEXT kinds never enter generation.

    Order matters: fence and front-matter state first, then setext look-ahead
    (so `---` after a title is a heading underline, not a thematic break),
    then GFM tables, then the remaining single-line forms.
    """
    lines: List[Line] = []
    fence: Optional[FenceState] = None
    in_fm = False
    in_html = False
    i = 0
    n = len(raw_lines)

    def emit(kind: LineKind, raw: str) -> None:
        lines.append(Line(kind=kind, text=raw, raw=raw))

    while i < n:
        raw = raw_lines[i]
        stripped = raw.rstrip("\r\n")

        # ── YAML front matter ──
        if fence is None and not in_html:
            if i == 0 and stripped.strip() == "---":
                emit(LineKind.FRONT_MATTER, raw)
                in_fm = True
                i += 1
                continue
            if in_fm:
                emit(LineKind.FRONT_MATTER, raw)
                if stripped.strip() == "---":
                    in_fm = False
                i += 1
                continue

        # ── Fenced code (delimiter-aware) ──
        if fence is not None:
            if _is_fence_closer(stripped, fence):
                emit(LineKind.FENCE, raw)
                fence = None
            else:
                emit(LineKind.FENCE, raw)
            i += 1
            continue

        # ── HTML block continuation (blank line ends the block) ──
        if in_html:
            if not stripped.strip():
                in_html = False
                emit(LineKind.BLANK, raw)
            else:
                emit(LineKind.HTML, raw)
            i += 1
            continue

        # ── Blank ──
        if not stripped.strip():
            emit(LineKind.BLANK, raw)
            i += 1
            continue

        # ── Fence opener ──
        opener = _fence_opener(stripped)
        if opener is not None:
            emit(LineKind.FENCE, raw)
            fence = opener
            i += 1
            continue

        # ── Setext heading (text + underline) ──
        if (
            i + 1 < n
            and _setext_text_ok(stripped)
            and _is_setext_underline(raw_lines[i + 1].rstrip("\r\n"))
        ):
            emit(LineKind.HEADING, raw)
            emit(LineKind.HEADING, raw_lines[i + 1])
            i += 2
            continue

        # ── ATX heading ──
        if _HEADING.match(stripped):
            emit(LineKind.HEADING, raw)
            i += 1
            continue

        # ── GFM table: header row + delimiter row, then body rows ──
        if (
            i + 1 < n
            and _looks_like_table_row(stripped)
            and _is_table_separator(raw_lines[i + 1].rstrip("\r\n"))
        ):
            while i < n:
                row = raw_lines[i]
                row_s = row.rstrip("\r\n")
                if not row_s.strip():
                    break
                if not (
                    _looks_like_table_row(row_s) or _is_table_separator(row_s)
                ):
                    break
                emit(LineKind.TABLE, row)
                i += 1
            continue

        # Leading-pipe table row without a separator yet (partial/broken tables
        # stay frozen rather than becoming prose).
        if _TABLE_LEADING.match(stripped):
            emit(LineKind.TABLE, raw)
            i += 1
            continue

        # ── Thematic break ──
        if _HR.match(stripped.strip()):
            emit(LineKind.HR, raw)
            i += 1
            continue

        # ── Blockquote / list marker lines ──
        if _BLOCKQUOTE.match(stripped):
            emit(LineKind.BLOCKQUOTE, raw)
            i += 1
            continue
        if _LIST.match(stripped):
            emit(LineKind.LIST, raw)
            i += 1
            continue

        # ── Reference and footnote definitions ──
        if _is_reference_def(stripped):
            emit(LineKind.REFERENCE, raw)
            i += 1
            continue

        # ── HTML block start ──
        if _is_html_block_start(stripped):
            emit(LineKind.HTML, raw)
            if not _is_html_block_complete(stripped):
                in_html = True
            i += 1
            continue

        # ── Indented code (4 spaces or tab) ──
        if _is_indented_code(stripped):
            while i < n and _is_indented_code(raw_lines[i].rstrip("\r\n")):
                emit(LineKind.INDENTED_CODE, raw_lines[i])
                i += 1
            continue

        # ── Ordinary prose ──
        emit(LineKind.TEXT, raw)
        i += 1

    return lines


def classify_line(
    text: str,
    fence: Optional[FenceState],
    in_front_matter: bool,
    line_no: int,
) -> Tuple[LineKind, Optional[FenceState], bool]:
    """
    Single-line classifier for simple callers and tests.

    Multi-line constructs (setext pairs, GFM tables, HTML block bodies)
    need classify_lines / parse — this helper cannot see look-ahead.
    """
    stripped = text.rstrip("\r\n")

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
    if _is_setext_underline(stripped):
        # Without the preceding text line this is a thematic break when dashes,
        # or an inert underline when equals — freeze either way.
        if _SETEXT_EQ.match(stripped):
            return LineKind.HEADING, None, False
        return LineKind.HR, None, False
    if _HR.match(stripped.strip()):
        return LineKind.HR, None, False
    if _TABLE_LEADING.match(stripped) or _is_table_separator(stripped):
        return LineKind.TABLE, None, False
    if _BLOCKQUOTE.match(stripped):
        return LineKind.BLOCKQUOTE, None, False
    if _LIST.match(stripped):
        return LineKind.LIST, None, False
    if _is_reference_def(stripped):
        return LineKind.REFERENCE, None, False
    if _is_html_block_start(stripped):
        return LineKind.HTML, None, False
    if _is_indented_code(stripped):
        return LineKind.INDENTED_CODE, None, False
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

    lines = classify_lines(raw_lines)

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
