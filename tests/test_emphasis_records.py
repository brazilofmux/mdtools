"""
Inline emphasis and strong in the IR (issue #88).

The last of the "inline constructs Pandoc knows and the IR does not" family,
and additive under the schema's stability rule: new kinds, no version bump.

**The contract is under-reporting.** Emphasis is the hairiest corner of the
dialect, and Pandoc's markdown reader is not CommonMark's delimiter algorithm
— it reads `*a *b* c*` as one emphasis over `a ` and leaves `b*` and `c*`
literal, which no stack discipline reproduces. So a pair is claimed only when
nothing else could have paired with either half: matching runs of one or two
delimiters, with no other run of the same marker between them or still open
around them.

Measured against `pandoc -t json` over 511 files of manuscript: **96.7% of
Emph and 95.0% of Strong**, and the records that are emitted agree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")


class EmphasisTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        source = ROOT / "mdfix" / "mdfix.c"
        if source.is_file() and source.stat().st_mtime > MDFIX.stat().st_mtime:
            raise AssertionError(
                f"{MDFIX} is older than {source} — rebuild with `make -C mdfix`"
            )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _records(self, text: str) -> list:
        path = self.dir / "a.md"
        path.write_text(text, encoding="utf-8")
        result = subprocess.run([str(MDFIX), "--emit-ir", str(path)],
                                capture_output=True, text=True, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def _emph(self, text: str) -> list:
        return [(r["kind"], r["text"])
                for r in self._records(text)
                if r["kind"] in ("emphasis", "strong")]

    def _spans(self, text: str) -> list:
        data = text.encode("utf-8")
        out = []
        for r in self._records(text):
            if r["kind"] in ("emphasis", "strong"):
                out.append((r["kind"],
                            data[r["start"]:r["end"]].decode("utf-8"),
                            data[r["textStart"]:r["textEnd"]].decode("utf-8")))
        return out

    def _pandoc_kinds(self, text: str) -> list:
        # -smart because this compares structure, not typography: the reader's
        # curly quotes would otherwise make every text comparison fail.
        result = subprocess.run([PANDOC, "-f", "markdown-smart", "-t", "json"],
                                input=text, capture_output=True, text=True,
                                check=True)
        found = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("t") in ("Emph", "Strong"):
                    found.append(node["t"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(json.loads(result.stdout))
        return found


class RecordTests(EmphasisTestCase):
    def test_the_plain_forms(self) -> None:
        self.assertEqual(self._emph("Some *emph* here.\n"),
                         [("emphasis", "emph")])
        self.assertEqual(self._emph("Some **strong** here.\n"),
                         [("strong", "strong")])
        self.assertEqual(self._emph("Some _emph_ here.\n"),
                         [("emphasis", "emph")])
        self.assertEqual(self._emph("Some __strong__ here.\n"),
                         [("strong", "strong")])

    def test_the_span_covers_the_delimiters_and_the_text_does_not(self) -> None:
        # The convention link destinations already use: an outer span for
        # "where is this construct", an inner one for "what would I rewrite".
        self.assertEqual(self._spans("a *emph* b\n"),
                         [("emphasis", "*emph*", "emph")])
        self.assertEqual(self._spans("a **strong** b\n"),
                         [("strong", "**strong**", "strong")])

    def test_two_pairs_on_one_line(self) -> None:
        self.assertEqual(self._emph("*one* and *two*\n"),
                         [("emphasis", "one"), ("emphasis", "two")])

    def test_a_later_pair_survives_a_skipped_inner_start(self) -> None:
        # find_emphasis records the inner pair; emit_inline skips the
        # enclosing [..] as one construct. Drain those starts so a later
        # simple pair on the same line is still emitted.
        self.assertEqual(
            self._emph("See [the *foo* guide][id] and *bar*.\n\n[id]: x.md\n"),
            [("emphasis", "bar")])
        self.assertEqual(
            self._emph("See [the *foo* guide] and *bar*.\n"),
            [("emphasis", "bar")])

    def test_records_arrive_in_source_order(self) -> None:
        kinds = [r["kind"] for r in self._records("*a* `c` **b**\n")
                 if r["kind"] in ("emphasis", "strong", "code_span")]
        self.assertEqual(kinds, ["emphasis", "code_span", "strong"])

    def test_they_are_not_protected(self) -> None:
        # Emphasis is prose with markers, not a verbatim construct: the
        # scanner rewrites inside it and the record must not claim otherwise.
        record = next(r for r in self._records("*emph text*\n")
                      if r["kind"] == "emphasis")
        self.assertFalse(record["protected"])

    def test_a_marker_in_code_is_not_a_delimiter(self) -> None:
        self.assertEqual(self._emph("Use `*args*` here.\n"), [])

    def test_an_escaped_marker_opens_nothing(self) -> None:
        self.assertEqual(self._emph(r"Literal \*stars\* here." + "\n"), [])

    def test_intraword_underscores_stay_literal(self) -> None:
        # +intraword_underscores, pinned in dialect-policy §3.
        self.assertEqual(self._emph("a_b_c and file_name_here\n"), [])

    def test_emphasis_in_a_heading_is_recorded(self) -> None:
        self.assertEqual(self._emph("# A *title*\n"), [("emphasis", "title")])

    def test_emphasis_in_a_list_item_is_recorded(self) -> None:
        self.assertEqual(self._emph("- an *item*\n"), [("emphasis", "item")])


class UnderReportTests(EmphasisTestCase):
    """What is deliberately left out, and why each one is not guessable."""

    def test_a_run_of_three_is_two_constructs_and_is_left_out(self) -> None:
        # Pandoc reads `***a***` as Strong [ Emph … ] — one delimiter run,
        # two constructs, with the inner pair inside the outer. Emitting one
        # record would be wrong about both.
        self.assertEqual(self._emph("***both***\n"), [])
        if PANDOC:
            self.assertEqual(self._pandoc_kinds("***both***\n"),
                             ["Strong", "Emph"])

    def test_a_run_of_four_is_literal_to_pandoc_and_left_out(self) -> None:
        self.assertEqual(self._emph("****four****\n"), [])
        if PANDOC:
            self.assertEqual(self._pandoc_kinds("****four****\n"), [])

    def test_nested_same_marker_pairs_are_left_out(self) -> None:
        # The case that made the rule this narrow. Pandoc pairs the *first*
        # two delimiters and leaves the rest literal, so both the outer pair
        # and the inner one are wrong readings of the source.
        self.assertEqual(self._emph("*a *b* c*\n"), [])
        if PANDOC:
            self.assertEqual(self._pandoc_kinds("*a *b* c*\n"), ["Emph"])

    def test_strong_inside_emphasis_is_left_out(self) -> None:
        # Pandoc reads both; mdfix reads neither, because the same-marker
        # runs in between are what make the outer pair unclaimable.
        self.assertEqual(self._emph("*a **b** c*\n"), [])
        if PANDOC:
            self.assertEqual(self._pandoc_kinds("*a **b** c*\n"),
                             ["Emph", "Strong"])

    def test_emphasis_inside_link_text_is_left_out(self) -> None:
        # The recursive inline tree, which is the rest of #88.
        self.assertEqual(self._emph("See [a *link*](x.md) here.\n"), [])

    def test_a_pair_across_a_line_break_is_left_out(self) -> None:
        # emit_inline walks one line at a time, with that line's offset, so
        # no record can span a newline. Pandoc joins the lines and reads one.
        self.assertEqual(self._emph("*wrapped\nemphasis*\n"), [])
        if PANDOC:
            self.assertEqual(self._pandoc_kinds("*wrapped\nemphasis*\n"),
                             ["Emph"])

    def test_an_unclosed_marker_records_nothing(self) -> None:
        self.assertEqual(self._emph("*unclosed here\n"), [])


@unittest.skipUnless(PANDOC, "pandoc not installed")
class OracleTests(EmphasisTestCase):
    """Every record emitted is one Pandoc agrees with."""

    CASES = (
        "Plain *emph* and **strong**.\n",
        "*Leading* and trailing *emph*.\n",
        "_under_ and __double__.\n",
        "A *phrase with spaces* here.\n",
        "**Bold: colon inside** and *dash-inside*.\n",
        "Punctuation *emph!* and *emph?*.\n",
        "# Heading with *emph*\n",
        "- item with **strong**\n",
        "> quote with *emph*\n",
        "| a | *emph* |\n|---|---|\n| 1 | 2 |\n",
        "Unicode *漢字* and *Ελληνικά*.\n",
        "*emph* `code` **strong** [link](x.md)\n",
    )

    def test_nothing_is_reported_that_pandoc_does_not_read(self) -> None:
        for source in self.CASES:
            with self.subTest(source=source):
                mine = self._emph(source)
                theirs = self._pandoc_kinds(source)
                self.assertLessEqual(
                    sum(1 for k, _ in mine if k == "emphasis"),
                    theirs.count("Emph"), source)
                self.assertLessEqual(
                    sum(1 for k, _ in mine if k == "strong"),
                    theirs.count("Strong"), source)

    def test_the_simple_forms_are_not_merely_under_reported(self) -> None:
        # An under-report contract is satisfied by emitting nothing, so pin
        # the coverage that makes the records worth having.
        for source in self.CASES:
            with self.subTest(source=source):
                self.assertEqual(len(self._emph(source)),
                                 len(self._pandoc_kinds(source)), source)


class KnownDivergenceTests(EmphasisTestCase):
    """
    `**%@**` is a `strong` record Pandoc does not read (2 in 511 files).

    Not an emphasis bug: `+citations` makes Pandoc read `@*` as a citation
    whose key is `*`, which eats the closing delimiter and leaves the whole
    thing literal. mdfix's citation keys start with an alphanumeric, so it
    sees no citation there and the emphasis stands.

    Pinned rather than guessed at — closing it means matching Pandoc's
    citation-key grammar, not narrowing this one further.
    """

    def test_the_shape_still_diverges(self) -> None:
        self.assertEqual(self._emph("- **%@** (caller) is the object.\n"),
                         [("strong", "%@")])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_reads_a_citation_there(self) -> None:
        result = subprocess.run(
            [PANDOC, "-f", "markdown-smart", "-t", "native"],
            input="- **%@** (caller) is the object.\n",
            capture_output=True, text=True, check=True)
        self.assertIn("Cite", result.stdout)
        self.assertNotIn("Strong", result.stdout)

    def test_a_normal_citation_inside_strong_is_fine(self) -> None:
        self.assertEqual(self._emph("**@smith2020** says so.\n"),
                         [("strong", "@smith2020")])


if __name__ == "__main__":
    unittest.main()
