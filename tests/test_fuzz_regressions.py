"""
Six bugs a generated corpus found, pinned as the inputs that found them.

`tests/test_fuzz.py` generates documents and checks properties over them. That
sweep is what *found* these; this file is what *keeps* them, because a seed
number is not a regression test. Change the generator and seed 630 becomes a
different document, and the case that mattered is gone with no test failing.

So each shrunk input is written out here in full. They are small, and they
read as what they are — the smallest document that broke a rule.

Four were `--wrap` mis-deciding where a line ends. The fifth was mdfix not
being a fixed point at all, which the other four were partly hiding. The sixth
came from a different property — block structure against Pandoc — and would
never have surfaced as an idempotence failure, because the damage it did was
stable.
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


class WrapRegressionTestCase(unittest.TestCase):
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

    def _fix(self, data: bytes, *flags: str) -> bytes:
        src = self.dir / "in.md"
        out = self.dir / "out.md"
        src.write_bytes(data)
        if out.exists():
            out.unlink()
        result = subprocess.run([str(MDFIX), "-q", *flags, str(src), str(out)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_bytes()

    def _stable(self, data: bytes, *flags: str) -> bytes:
        """Assert one pass is already a fixed point, and return it."""
        once = self._fix(data, *flags)
        twice = self._fix(once, *flags)
        self.assertEqual(twice, once,
                         f"{' '.join(flags)} needed a second pass:\n"
                         f"  once:  {once!r}\n  twice: {twice!r}")
        return once

    def _blocks(self, data: bytes) -> list:
        result = subprocess.run([PANDOC, "-f", "markdown", "-t", "json"],
                                input=data, capture_output=True, check=True)
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]


class JoinDecisionTests(WrapRegressionTestCase):
    """
    The join decision has to be a fixed point, so it is made per segment.

    It used to be made per line, from that line's width. Joining two lines
    makes a longer line, which next time is wide enough to join with what
    follows — so `--wrap` walked one line further on every run instead of
    settling.
    """

    def test_a_long_line_then_a_short_one_settles_in_one_pass(self) -> None:
        self._stable(b"A paragraph of prose number 0 with words.\n"
                     b"--dash\ncode 3\n", "--canonical", "--wrap=60")

    def test_a_paragraph_of_short_lines_is_left_alone(self) -> None:
        # The behaviour the heuristic exists for: nothing here looks
        # machine-wrapped, so the author's breaks stand.
        source = b"one short\ntwo short\nthree short\n"
        self.assertEqual(self._fix(source, "--wrap=60"), source)

    def test_a_mixed_paragraph_reflows_whole(self) -> None:
        # The deliberate behaviour change. One near-width line means the
        # paragraph is a wrapped one somebody edited, and --wrap is a request
        # to wrap it — so it is reflowed entire rather than in pieces.
        source = (b"This line is long enough to look machine wrapped indeed\n"
                  b"short\n")
        out = self._stable(source, "--wrap=60")
        self.assertEqual(out.count(b"\n"), 2)
        self.assertTrue(out.startswith(b"This line is long enough"))


class WrapBoundaryTests(WrapRegressionTestCase):
    """Where a line ends also decides what the next line begins with."""

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_wrapping_never_invents_a_list(self) -> None:
        # The one that mattered. "…then spoke 2." wrapped so that `2.` began
        # a line; mdfix's own blank-before-list repair then separated it, and
        # one Para became a Para and an OrderedList. I2.1, broken by --wrap,
        # and invisible to the transform matrix because no document in its
        # corpus ends a sentence with a number.
        source = ("Zero​width 1\nHe paused . . . then spoke 2.\n"
                  "code 6\n").encode("utf-8")
        before = self._blocks(source)
        out = self._stable(source, "--wrap=40")
        self.assertEqual(self._blocks(out), before)
        self.assertEqual(before, ["Para"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_a_sentence_ending_in_a_number_survives_every_width(self) -> None:
        # The general shape, swept rather than pinned at one width: prose
        # ending "... section 2." is ordinary, and some width puts the number
        # at the start of a line.
        source = (b"We discussed the matter at length in section 2. "
                  b"The following text continues the same paragraph here.\n")
        before = self._blocks(source)
        for width in range(20, 80, 3):
            with self.subTest(width=width):
                out = self._stable(source, f"--wrap={width}")
                self.assertEqual(self._blocks(out), before)

    def test_a_wrapped_line_has_no_trailing_whitespace(self) -> None:
        # A break landing inside a run of spaces left one on the emitted
        # line. Two would have been read back as a hard break nobody wrote.
        source = (b"Really?  Two spaces o spaces 1.\n"
                  b"Really?  Two spaces 3.\n")
        out = self._stable(source, "--wrap=40")
        for line in out.split(b"\n"):
            self.assertEqual(line, line.rstrip(b" \t"), out)

    def test_a_line_runs_long_rather_than_break_into_a_marker(self) -> None:
        # When every candidate break would start a block, none is taken. An
        # over-wide line is cosmetic; inventing a list is semantic.
        source = b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1. bbb\n"
        out = self._stable(source, "--wrap=40")
        self.assertNotIn(b"\n1.", out)


class JoinWhitespaceTests(WrapRegressionTestCase):
    def test_a_continuation_indent_is_dropped_on_join(self) -> None:
        # Lazy-continuation indent is Markdown's, not the author's. Carried
        # into the join it became a run of spaces mid-sentence, which the
        # sentence-spacing fix then collapsed on the next pass.
        source = b"A paragraph of prose number 1 with words.\n    indented 3"
        out = self._stable(source, "--canonical", "--wrap=60")
        self.assertNotIn(b".    ", out)
        self.assertIn(b"words. indented 3", out)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class ConstructPreservationTests(WrapRegressionTestCase):
    """An optional transform may not change what a block *is* (I3.1)."""

    def test_a_definition_list_survives_canonical(self) -> None:
        # `--chicago-punct-2` closed the gap in `: ./file.md`, reading the `.`
        # of a relative path as sentence punctuation. The result is stable and
        # idempotent — and a DefinitionList that has become a Para.
        # dialect-policy §3 pins `+definition_lists`.
        source = b'[term]\n: ./t5.md "Title"\n'
        self.assertEqual(self._blocks(source), ["DefinitionList"])
        for flags in (("--canonical",), ("--chicago-punct-2",),
                      ("--technical",)):
            with self.subTest(flags=flags):
                out = self._stable(source, *flags)
                self.assertEqual(self._blocks(out), ["DefinitionList"])

    def test_space_before_real_punctuation_is_still_closed(self) -> None:
        # The rule still does its job; it just asks what follows the mark
        # before deciding the mark ends a word.
        out = self._stable(b"He said the word , and then .\n", "--canonical")
        self.assertEqual(out, b"He said the word, and then.\n")

    def test_a_relative_path_keeps_its_space(self) -> None:
        out = self._stable(b"See the file ./notes.md for more.\n",
                           "--canonical")
        self.assertIn(b" ./notes.md", out)


class ConvergenceTests(WrapRegressionTestCase):
    """
    mdfix renders until the document stops changing, and these are why.

    Each is a fixer changing what a line *is*, where the repair that cares
    about that ran on a classification taken beforehand. Reordering would fix
    each one and leave the next to be discovered.
    """

    def test_stripping_a_trailing_space_can_create_a_list_marker(self) -> None:
        # `2. ` is not a list item; `2.` is. -w decides that after the
        # blank-line repair has already looked.
        self._stable(b"1. one 2\n2. ", "-w")

    def test_closing_a_gap_can_destroy_a_list_marker(self) -> None:
        # The mirror: `1. . one` is a list item, `1.. one` is not, so the
        # bullet after it acquires a need for a blank line.
        self._stable(b"1. . one 2\n- item 3b\n", "--canonical")

    def test_one_heading_fix_can_expose_another(self) -> None:
        # The ATX-space fix moves the `*` where the emphasis fix can see it,
        # and the emphasis fix has already run.
        self._stable(b"#*# Sub 3 #\n", "--canonical")

    def test_joining_changes_what_follows_a_paragraph(self) -> None:
        source = (b"Prose 2 with [a link](http://x/2).\n"
                  b"    indented code 4\n2. two 5")
        self._stable(source, "--technical")

    def test_convergence_does_not_change_a_settled_document(self) -> None:
        # The cost of the loop is one extra render on a clean file, and no
        # change to it. Worth pinning: a bug here would rewrite every file
        # mdfix touched.
        for name in ("README.md", "docs/architecture.md", "docs/writing.md"):
            with self.subTest(document=name):
                data = (ROOT / name).read_bytes()
                self.assertEqual(self._fix(data, "--canonical"),
                                 self._fix(self._fix(data, "--canonical"),
                                           "--canonical"))

    def test_counts_describe_the_input_not_the_passes(self) -> None:
        # Diagnostics carry byte spans into the file on disk (ID.1), so a
        # second pass is looking at a buffer no consumer has. Its findings
        # must not be reported as if they were the file's.
        src = self.dir / "c.md"
        src.write_bytes(b"1. one 2\n2. ")
        result = subprocess.run(
            [str(MDFIX), "-n", "--diagnostics", "-w", str(src)],
            capture_output=True, text=True)
        rows = [json.loads(line) for line in result.stderr.splitlines()]
        data = src.read_bytes()
        for row in rows:
            self.assertLessEqual(row["end"], len(data), row)
            self.assertEqual(row["path"], str(src))


@unittest.skipUnless(PANDOC, "pandoc not installed")
class KnownDivergenceTests(WrapRegressionTestCase):
    """
    What the sweep is told to ignore, asserted so it cannot be ignored quietly.

    `fuzz.KNOWN_DIVERGENCES` filters this shape out of the sweep. If it were
    only filtered, fixing it would leave a pin nobody removed and a filter
    quietly hiding the next instance. So the divergence is pinned here too:
    fix it and this test fails, which is the signal to delete both.
    """

    SOURCE = (b"A paragraph of prose number 1 with words.\n"
              b"    indented continuation\n2. two 4\n")

    def test_the_repair_still_depends_on_the_continuation(self) -> None:
        # mdfix's blank-before-list repair fires when an ordered marker
        # directly follows paragraph text, and not when a lazy continuation
        # sits between them. `--wrap` joins that continuation away, so the
        # same document gets two block structures depending on the flag.
        plain = self._blocks(self._stable(self.SOURCE))
        wrapped = self._blocks(self._stable(self.SOURCE, "--wrap=40"))
        self.assertEqual(plain, ["Para"])
        self.assertEqual(wrapped, ["Para", "OrderedList"])

    def test_pandoc_reads_no_ordered_marker_as_interrupting(self) -> None:
        # The fact that makes this a decision rather than a bug: R2 is not
        # restoring a list Pandoc would have seen, it is creating one. That
        # is allowed — I2.1 names the exception — but it should be on purpose.
        for marker in (b"1.", b"2.", b"1)", b"3)"):
            with self.subTest(marker=marker):
                self.assertEqual(
                    self._blocks(b"para text\n" + marker + b" item\n"),
                    ["Para"])

    def test_the_direct_case_does_fire(self) -> None:
        self.assertEqual(
            self._blocks(self._stable(b"para text\n2. two\n")),
            ["Para", "OrderedList"])


if __name__ == "__main__":
    unittest.main()
