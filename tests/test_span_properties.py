"""
Property tests for sentence-span attribution and reconstruction.

Three separate bugs have been fixed in split_sentences by hand, each found
case-by-case and two of them only after review:

  * the final span ran to end-of-region, so a rewrite ate the trailing newline
  * a list-indented paragraph carried its indent, so a rewrite ate the indent
    and the paragraph escaped its list item
  * a trailing closing quote sat inside the span, so a rewrite deleted it and
    unbalanced the document

All three are instances of one question — *what does a sentence span own?* —
so they are asserted here as properties over generated documents rather than
as more examples. Each property below fails on at least one of those bugs.

Deterministic: fixed seeds, no network, ~0.2s.
"""

from __future__ import annotations

import random
import unittest
from pathlib import Path

from prosevary.freeze import sentence_freeze
from prosevary.segment import (
    Document,
    _restore_trailing_closers,
    _trailing_closer_run,
    parse,
    split_sentences,
)

# Fragments chosen to exercise the structures that own characters a span must
# not swallow: quotes, brackets, indentation, wrapped lines, block markers.
FRAGMENTS = [
    "Prose here.",
    "Another sentence follows.",
    ' He said "Hello."',
    " She replied “Quoted.”",
    " A note (see this.)",
    " A ref [see this.]",
    " Step 1.) Do the thing.",
    " It ended.",
    " Really?",
    " Stop!",
    "\n",
    "\n\n",
    "  indented continuation\n",
    "- list item\n",
    "> quote\n",
    "# Heading\n",
    "| a | b |\n",
    "```sh\n",
    "```\n",
    "    code line\n",
]

WHITESPACE = " \t\r\n"


def _documents(seed: int, count: int) -> list[str]:
    rnd = random.Random(seed)
    out = []
    for _ in range(count):
        n = rnd.randint(1, 14)
        out.append("".join(rnd.choice(FRAGMENTS) for _ in range(n)))
    return out


def _region_offset(doc: Document, line_start: int) -> int:
    """Character offset of a region's first line within the whole document."""
    return sum(len(line.text) for line in doc.lines[:line_start])


class SpanShapeProperties(unittest.TestCase):
    """What a span may contain. Whitespace at either edge belongs to the gap."""

    def test_spans_never_carry_edge_whitespace(self) -> None:
        # Fails on the trailing-newline bug and on the list-indent bug: both
        # put structural whitespace inside the span, where a rewrite ate it.
        for source in _documents(seed=1, count=2000):
            for region in parse(source).regions:
                for sent in region.sentences:
                    self.assertEqual(
                        sent.text,
                        sent.text.strip(WHITESPACE),
                        msg=f"span carries edge whitespace: {sent.text!r}",
                    )

    def test_spans_are_ordered_disjoint_and_in_bounds(self) -> None:
        for source in _documents(seed=2, count=2000):
            for region in parse(source).regions:
                prev_end = 0
                for sent in region.sentences:
                    self.assertGreaterEqual(sent.start, prev_end)
                    self.assertLessEqual(sent.end, len(region.text))
                    self.assertLessEqual(sent.start, sent.end)
                    prev_end = sent.end

    def test_span_offsets_agree_with_span_text(self) -> None:
        # Offsets are what reconstruct() splices on; text is what the model
        # rewrites. They must describe the same bytes.
        for source in _documents(seed=3, count=2000):
            for region in parse(source).regions:
                for sent in region.sentences:
                    self.assertEqual(region.text[sent.start : sent.end], sent.text)

    def test_split_sentences_agrees_with_its_own_offsets(self) -> None:
        for source in _documents(seed=4, count=1500):
            for start, end, text in split_sentences(source):
                self.assertEqual(source[start:end], text)


class ReconstructProperties(unittest.TestCase):
    """What reconstruct() may change: the replaced span, and nothing else."""

    def test_identity_without_replacements(self) -> None:
        for source in _documents(seed=5, count=2000):
            self.assertEqual(parse(source).reconstruct({}), source)

    def test_everything_outside_the_replaced_span_survives(self) -> None:
        # The core property. Fails on all three historical bugs: each one let
        # a rewrite consume bytes that lay outside the span it replaced.
        sentinel = "REWRITTEN"
        for source in _documents(seed=6, count=2000):
            doc = parse(source)
            for region in doc.regions:
                base = _region_offset(doc, region.line_start)
                for index, sent in enumerate(region.sentences):
                    out = parse(source).reconstruct(
                        {(region.region_id, index): sentinel}
                    )
                    expected_new = _restore_trailing_closers(sent.text, sentinel)
                    abs_start = base + sent.start
                    abs_end = base + sent.end
                    self.assertEqual(
                        out,
                        source[:abs_start] + expected_new + source[abs_end:],
                        msg=(
                            f"replacement leaked outside its span\n"
                            f"  source={source!r}\n  span={sent.text!r}"
                        ),
                    )

    def test_replacing_a_span_with_itself_is_identity(self) -> None:
        for source in _documents(seed=7, count=1500):
            doc = parse(source)
            for region in doc.regions:
                for index, sent in enumerate(region.sentences):
                    out = parse(source).reconstruct(
                        {(region.region_id, index): sent.text}
                    )
                    self.assertEqual(out, source)

    def test_trailing_closers_survive_any_candidate(self) -> None:
        # A quotation spanning two sentences puts the closer inside the second
        # span. Whatever the model returns, the delimiter must not vanish.
        for source in _documents(seed=8, count=1500):
            doc = parse(source)
            for region in doc.regions:
                for index, sent in enumerate(region.sentences):
                    run = _trailing_closer_run(sent.text)
                    if not run:
                        continue
                    out = parse(source).reconstruct(
                        {(region.region_id, index): "Something else entirely"}
                    )
                    self.assertIn("Something else entirely" + run, out)


class FreezeIdentityProperties(unittest.TestCase):
    """
    A sentence must always satisfy its own freeze set.

    Multiset counting was first derived from the extraction list rather than
    from the original text. Patterns overlap — two of them match `[^1]`, a
    shortcut ref is a substring of a full link, `/tmp/x` is a prefix of
    `/tmp/x/y` — so the list holds duplicates and nested pieces, and
    check(original, original) failed. Every candidate for such a sentence was
    then rejected, silently, while the run reported healthy reject-freeze
    counts.
    """

    MARKUP = [
        "Note [^1] here.",
        "See [foo] and [foo](https://x.com) here.",
        "Path /tmp/x and /tmp/x/y here.",
        "Use ``a `x` b`` and `x`.",
        "LLVM's IR is fine.",
        "MMIO-based access here.",
        "A link [docs](https://example.com/a_(b)) here.",
        "An image ![alt](i.png) and <b>bold</b>.",
        "Commit deadbeef and 1a2b3c4 landed.",
        "Cite @smith2020 with {#id} and [^2].",
        "Plain prose with no markup at all.",
        "SLOW-32 and DBT in one line.",
    ]

    def test_every_sentence_satisfies_its_own_freeze_set(self) -> None:
        rnd = random.Random(101)
        for _ in range(3000):
            text = " ".join(
                rnd.choice(self.MARKUP) for _ in range(rnd.randint(1, 4))
            )
            fs = sentence_freeze(text, {"run", "relocation", "SLOW-32"})
            self.assertIsNone(
                fs.check(text, text),
                msg=f"sentence rejects itself: {text!r}",
            )

    def test_identity_holds_for_repo_prose(self) -> None:
        for path in sorted(Path(".").rglob("*.md")):
            if ".git" in str(path):
                continue
            source = path.read_text(encoding="utf-8")
            for region in parse(source).regions:
                for sent in region.sentences:
                    fs = sentence_freeze(sent.text, set())
                    self.assertIsNone(
                        fs.check(sent.text, sent.text),
                        msg=f"{path}: {sent.text!r}",
                    )


if __name__ == "__main__":
    unittest.main()
