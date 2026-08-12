"""
Segment a Markdown document into paraphrasable prose and everything else.

**This module contains no Markdown grammar.** Block structure comes from
`mdfix --emit-ir`, which is the boundary in docs/dialect-policy.md §2. What
remains here is prose logic — splitting a paragraph into sentences, and
putting back a closing quote a rewrite dropped — which is not Markdown and
belongs on this side.

It used to contain 685 lines of block grammar restating `mdfix.rl`: fence
tracking, setext detection, raw-HTML block kinds, the four table forms,
indented code, list content columns. Every structural bug arrived in pairs,
and `tests/test_tool_parity.py` existed only because neither copy could be
trusted to agree with the other. Two rules had already drifted by the time the
IR could replace them — prosevary accepted an indented setext underline that
Pandoc reads as a paragraph, and treated `[id]:x` as paraphrasable prose.

Only `paragraph` records are offered for rewriting. Headings, lists, block
quotes, tables, code, raw HTML, front matter and link/footnote definitions are
reproduced exactly.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from mdquery.ir import raw_records


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
    LINE_BLOCK = auto()
    REFERENCE = auto()  # link/image ref defs, footnote defs
    TEXT = auto()  # ordinary paragraph line — the only candidate region


# IR block kind -> LineKind. Anything unknown is protected: a kind this
# version does not recognize is opaque-but-located per the schema's stability
# rules, and guessing would be how prose leaks into a construct.
_KIND_TO_LINE = {
    "frontmatter": LineKind.FRONT_MATTER,
    "heading": LineKind.HEADING,
    "paragraph": LineKind.TEXT,
    "list": LineKind.LIST,
    "block_quote": LineKind.BLOCKQUOTE,
    "code_fence": LineKind.FENCE,
    "code_indented": LineKind.INDENTED_CODE,
    "table": LineKind.TABLE,
    "line_block": LineKind.LINE_BLOCK,
    "raw_html": LineKind.HTML,
    "thematic_break": LineKind.HR,
    "reference_def": LineKind.REFERENCE,
    "footnote_def": LineKind.REFERENCE,
}

# The one kind whose text may be rewritten.
PROSE_KIND = "paragraph"


# ── Sentence splitting: prose logic, not Markdown ──────────────────────────

# Sentence end: .!? then optional closing quotes, then whitespace, then the
# next sentence-ish token. Closing quotes are matched so the split can fire,
# but they belong to the *preceding* sentence (see split_sentences) — leaving
# them in the separator produced `He said "Hello.` / gap `"` / `Next…` and a
# balanced rewrite reconstructed as `He replied "Hi."" Next…`.
# Conservative; abbrev false splits (e.g. "Dr. Smith") are acceptable for v0.
_SENT_SPLIT = re.compile(
    r'(?<=[.!?])["\'”’)\]}]*\s+(?=["\'“‘]?[A-Z0-9])'
)
# Closers that trail a terminator and must stay inside the sentence span when
# the splitter matches them as part of the separator. These sit *after* the
# terminator — `He said "Hello."`, `(See the note.)`, `[See note.]` — not
# before it.
_SENT_TRAILING_CLOSERS = frozenset("\"'”’)]}")


def _trailing_closer_run(text: str) -> str:
    """The run of closing delimiters at the end of text, possibly empty."""
    j = len(text)
    while j > 0 and text[j - 1] in _SENT_TRAILING_CLOSERS:
        j -= 1
    return text[j:]


# Closers with a distinct opener. Straight quotes are their own opener, so
# they are judged by parity instead.
_CLOSER_OPENERS = {")": "(", "]": "[", "}": "{", "”": "“", "’": "‘"}
_SYMMETRIC_CLOSERS = frozenset("\"'")


def _unmatched_trailing_closers(text: str) -> str:
    """
    The part of text's trailing closer run that text does not open itself.

    A trailing closer is only this module's concern when it belongs to a
    construct *larger* than the sentence — a quotation spanning a sentence
    boundary, say, where the closer lands inside the second sentence's span
    and a rewrite would delete it.

    A closer the sentence opens itself is ordinary content. Restoring those
    duplicates punctuation, which is what turned a rewrite of
    `He said ("Hello.")` into `He replied (Hi.)")`.

    Parity is a heuristic for straight quotes, since an apostrophe is the same
    character; it only misreads a sentence that both ends in a quote and
    contains an odd number of them, where the cost is an append the candidate
    already satisfies.
    """
    run = _trailing_closer_run(text)
    if not run:
        return ""
    unmatched = []
    for ch in run:
        if ch in _SYMMETRIC_CLOSERS:
            if text.count(ch) % 2 == 1:
                unmatched.append(ch)
        else:
            opener = _CLOSER_OPENERS.get(ch)
            if opener is not None and text.count(opener) < text.count(ch):
                unmatched.append(ch)
    return "".join(unmatched)


def _is_enumeration_label(prose: str, term_end: int, closer_end: int) -> bool:
    """
    Whether a terminator + bracket run is an enumeration label like `1.)`.

    Admitting `)]}` to the separator class made `Step 1.) Do the thing.` split
    into the fragment `Step 1.)` plus a sentence, and the fragment then went to
    the paraphraser on its own. These reach TEXT at all only because _LIST
    requires whitespace after `\\d+.`, so `1.)` is not read as a list marker.

    Scoped narrowly: bracket-only run, `.` terminator, digit before it. A
    letter before the terminator (`See the note.)`) still splits, which is the
    case this separator class was widened for.
    """
    run = prose[term_end:closer_end]
    if not run or any(c not in ")]}" for c in run):
        return False
    if term_end == 0 or prose[term_end - 1] != ".":
        return False
    return term_end >= 2 and prose[term_end - 2].isdigit()


def _restore_trailing_closers(original: str, candidate: str) -> str:
    """
    Ensure a rewritten sentence keeps the closers the original ended with.

    Attributing the closer to the sentence (rather than the inter-sentence gap)
    fixes duplication — a balanced candidate no longer yields `"Hi.""`. But it
    moves the closer *inside* rewritable text, where a candidate that drops it
    silently unbalances the document. That is the worse failure: duplication is
    obvious in a diff, a missing quote is not.

    Only closers the original does not open itself are restored — see
    _unmatched_trailing_closers. Restoring the rest duplicated punctuation.

    Only ever appends, and never when the candidate already ends with the
    final closer of the run: a candidate that kept part of it has dealt with
    the delimiter, and appending would double it. Under-restoring is the safe
    direction, since freeze and the judge still inspect the candidate.
    """
    needed = _unmatched_trailing_closers(original)
    if not needed or candidate.endswith(needed):
        return candidate
    if candidate.endswith(needed[-1]):
        return candidate
    return candidate + needed


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
        # m.start() is the first character after the terminator — often a
        # closing quote that the regex consumed as part of the separator.
        # Those closers belong to this sentence, not the inter-sentence gap.
        end = m.start()
        while end < m.end() and prose[end] in _SENT_TRAILING_CLOSERS:
            end += 1
        if _is_enumeration_label(prose, m.start(), end):
            continue
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
        # Whitespace at either edge belongs to the gap between sentences, not
        # to the sentence, and reconstruct() re-emits anything outside a span
        # verbatim.
        #
        # Trailing: the final span runs to end-of-region, so leaving the
        # newline inside it means an accepted rewrite silently eats it.
        #
        # Leading: a paragraph indented inside a list item carries its indent
        # at the head of the first span. That indent is load-bearing — drop it
        # and a second paragraph escapes its list item, splitting one list into
        # two with a stray paragraph between.
        lead = len(chunk) - len(chunk.lstrip())
        trimmed = chunk.strip()
        out.append((a + lead, a + lead + len(trimmed), trimmed))
    return out


# ── Document model ─────────────────────────────────────────────────────────


@dataclass
class Line:
    kind: LineKind
    text: str  # includes trailing newline if present in source
    raw: str


@dataclass
class Sentence:
    """One paraphrasable sentence, located inside a prose region."""

    text: str
    start: int  # char offset into the region's text
    end: int
    region_id: int


@dataclass
class Region:
    """One prose block, the only thing prosevary may rewrite."""

    region_id: int
    line_start: int  # inclusive index into lines[]
    line_end: int  # exclusive
    text: str
    sentences: List[Sentence] = field(default_factory=list)
    # Byte span in the source, so a caller can emit mdfix edits directly.
    byte_start: int = 0
    byte_end: int = 0


@dataclass
class Document:
    lines: List[Line]
    regions: List[Region]
    # Every record's (kind, text) in source order. The IR is total, so
    # concatenating these reproduces the file — which is what makes
    # reconstruct() exact rather than approximate.
    _pieces: List[Tuple[str, str, Optional[int]]] = field(default_factory=list)

    def reconstruct(self, replacements: Dict[Tuple[int, int], str]) -> str:
        """
        Rebuild the file. Keys are (region_id, sentence_index) -> new text.

        Untouched pieces are the source's own bytes, so a document with no
        replacements comes back byte for byte.
        """
        bodies: Dict[int, str] = {}
        for region in self.regions:
            if not region.sentences:
                bodies[region.region_id] = region.text
                continue
            parts: List[str] = []
            cursor = 0
            for i, sentence in enumerate(region.sentences):
                if sentence.start > cursor:
                    parts.append(region.text[cursor:sentence.start])
                new = replacements.get((region.region_id, i), sentence.text)
                if new is not sentence.text:
                    new = _restore_trailing_closers(sentence.text, new)
                parts.append(new)
                cursor = sentence.end
            if cursor < len(region.text):
                parts.append(region.text[cursor:])
            bodies[region.region_id] = "".join(parts)

        out: List[str] = []
        for kind, text, region_id in self._pieces:
            if region_id is not None:
                out.append(bodies[region_id])
            else:
                out.append(text)
        return "".join(out)


def parse(source: str) -> Document:
    """
    Segment `source` using mdfix's structural IR.

    The IR reader wants a path, so the text is written to a temp file. That is
    the price of not carrying a second parser, and it is a cheap one.
    """
    data = source.encode("utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.md"
        path.write_bytes(data)
        records = [r for r in raw_records([path]) if r.get("kind") != "document"]

    text = data.decode("utf-8", errors="replace")
    pieces: List[Tuple[str, str, Optional[int]]] = []
    regions: List[Region] = []

    for record in records:
        kind = record["kind"]
        span = text_slice(data, record["start"], record["end"])
        if kind == PROSE_KIND:
            region = Region(
                region_id=len(regions),
                line_start=record["line"] - 1,
                line_end=record["endLine"],
                text=span,
                byte_start=record["start"],
                byte_end=record["end"],
            )
            region.sentences = [
                Sentence(text=t, start=a, end=b, region_id=region.region_id)
                for a, b, t in split_sentences(span)
            ]
            regions.append(region)
            pieces.append((kind, span, region.region_id))
        else:
            pieces.append((kind, span, None))

    lines = _lines_from(data, records)
    return Document(lines=lines, regions=regions, _pieces=pieces)


def text_slice(data: bytes, start: int, end: int) -> str:
    return data[start:end].decode("utf-8", errors="replace")


def _lines_from(data: bytes, records: Sequence[dict]) -> List[Line]:
    """
    One Line per source line, carrying the kind of the record covering it.

    A blank line lives inside a `gap`, so it reports BLANK; a line inside a
    fence reports FENCE. This is a view for callers that think in lines, not
    a second classification — every kind comes from the IR.
    """
    text = data.decode("utf-8", errors="replace")
    raw_lines = text.splitlines(keepends=True)
    kinds: List[LineKind] = [LineKind.BLANK] * len(raw_lines)
    for record in records:
        line_kind = _KIND_TO_LINE.get(record["kind"])
        if line_kind is None:
            continue
        for i in range(record["line"] - 1, min(record["endLine"], len(raw_lines))):
            kinds[i] = line_kind
    return [Line(kind=k, text=t, raw=t) for k, t in zip(kinds, raw_lines)]


def iter_sentences(doc: Document) -> Iterator[Tuple[Region, int, Sentence]]:
    for region in doc.regions:
        for i, sentence in enumerate(region.sentences):
            yield region, i, sentence
