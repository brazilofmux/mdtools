"""
The marker shapes mdfix must not resolve (issue #97).

#90 taught mdfix to read every marker form Pandoc reads. What was left is the
set of lines where *Pandoc's* reading and the *author's* intent can come
apart, and the tool cannot safely pick. Silence there is the failure mdfix was
built to end: Pandoc accepts the document, misunderstands it, and nothing says
so.

The split follows the stated priority — auto-resolve what is unambiguous, then
auto-fail what is not.

**R4 repairs the run.** `A. First` then `B. Second` is one paragraph to Pandoc
and a list to every reader, and the difference is one space nobody can see.
Two or more consecutive letters at the same indent is not ambiguous.

**A diagnostic reports the rest**, and never rewrites it: a lone `A. text`
(a list item, or a name abbreviated), `@key.` opening a block (an example
list to Pandoc, a citation to a reader), and a word that parses as a roman
numeral.

Noise budget, measured over 511 files of manuscript: **zero** occurrences of
any of them, and zero output changes. This is a trap for what arrives, not a
backlog to clear — which is what makes a hit worth reading.
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


class MarkerTestCase(unittest.TestCase):
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

    def _fix(self, text: str, *flags: str) -> str:
        src = self.dir / "in.md"
        out = self.dir / "out.md"
        src.write_text(text, encoding="utf-8")
        if out.exists():
            out.unlink()
        result = subprocess.run([str(MDFIX), "-q", *flags, str(src), str(out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def _rules(self, text: str, *flags: str) -> list:
        src = self.dir / "in.md"
        src.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-n", "--diagnostics", *flags, str(src)],
            capture_output=True, text=True)
        rows = []
        for line in result.stderr.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return [r["rule"] for r in rows]

    def _blocks(self, text: str) -> list:
        result = subprocess.run([PANDOC, "-f", "markdown", "-t", "json"],
                                input=text, capture_output=True, text=True,
                                check=True)
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]


class RunRepairTests(MarkerTestCase):
    """R4: the unambiguous half."""

    RUN = "A. First option\nB. Second option\n"

    def test_a_run_gets_its_second_column(self) -> None:
        self.assertEqual(self._fix(self.RUN),
                         "A.  First option\nB.  Second option\n")

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_the_repair_is_what_makes_pandoc_read_a_list(self) -> None:
        # The whole point, and the shape that started the tool: before the
        # repair Pandoc reads one paragraph and says nothing about it.
        self.assertEqual(self._blocks(self.RUN), ["Para"])
        self.assertEqual(self._blocks(self._fix(self.RUN)), ["OrderedList"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_a_run_after_prose_gets_the_blank_line_too(self) -> None:
        # The shape generated Markdown actually produces. R4 supplies the
        # column and R2 the blank, because a repaired run classifies as a
        # list — without that the columns land in a paragraph and change
        # nothing at all.
        source = "Here are the options:\nA. First option\nB. Second option\n"
        fixed = self._fix(source)
        self.assertEqual(self._blocks(source), ["Para"])
        self.assertEqual(self._blocks(fixed), ["Para", "OrderedList"])

    def test_a_run_of_three(self) -> None:
        self.assertEqual(self._fix("A. one\nB. two\nC. three\n"),
                         "A.  one\nB.  two\nC.  three\n")

    def test_the_letters_must_be_consecutive(self) -> None:
        # Two capitals that are not a sequence are two sentences that begin
        # with an initial, which is the case the two-column rule protects.
        source = "A. Smith wrote it.\nZ. Jones edited it.\n"
        self.assertEqual(self._fix(source), source)

    def test_the_indent_must_match(self) -> None:
        source = "A. one\n  B. two\n"
        self.assertEqual(self._fix(source), source)

    def test_a_singleton_is_never_repaired(self) -> None:
        source = "A. Smith wrote the book.\n"
        self.assertEqual(self._fix(source), source)

    def test_a_run_inside_a_fence_is_left_alone(self) -> None:
        source = "```\nA. one\nB. two\n```\n"
        self.assertEqual(self._fix(source), source)

    def test_a_run_inside_front_matter_is_left_alone(self) -> None:
        source = "---\nA. one\nB. two\n---\n\nProse.\n"
        self.assertEqual(self._fix(source), source)

    def test_a_run_inside_indented_code_is_left_alone(self) -> None:
        source = "    A. one\n    B. two\n"
        self.assertEqual(self._fix(source), source)

    def test_a_run_inside_raw_html_is_left_alone(self) -> None:
        source = "<!--\nA. one\nB. two\n-->\n"
        self.assertEqual(self._fix(source), source)

    def test_a_nested_run_under_a_list_is_still_repaired(self) -> None:
        source = "- item\n    A. one\n    B. two\n"
        self.assertEqual(self._fix(source),
                         "- item\n    A.  one\n    B.  two\n")

    def test_wrap_does_not_force_stale_run_lines(self) -> None:
        # marker_run_line is rebuilt at the start of every process(). A
        # wrap re-read that reused the first pass's bits would force a
        # wrap line to LT_ORDERED and R2 would split the paragraph.
        source = ("A long paragraph that will wrap under a narrow "
                  "width into several lines.\n\n"
                  "A. First option\nB. Second option\n")
        out = self._fix(source, "--wrap=40")
        self.assertIn("A.  First", out)
        self.assertEqual(out.count("\n\n"), 1)

    def test_no_required_disables_it(self) -> None:
        self.assertEqual(self._fix(self.RUN, "--no-required"), self.RUN)

    def test_it_reports_under_its_own_rule(self) -> None:
        self.assertEqual(self._rules(self.RUN),
                         ["list.marker-column", "list.marker-column"])

    def test_the_profiles_do_not_collapse_the_column_again(self) -> None:
        # The Chicago sentence-space rule sees a period and two spaces. The
        # block-type guard in apply_scanner is what stops it undoing R4.
        for profile in ("--canonical", "--technical"):
            with self.subTest(profile=profile):
                self.assertIn("A.  First", self._fix(self.RUN, profile))


class DiagnosticTests(MarkerTestCase):
    """The half that must not be resolved, only reported."""

    def test_a_lone_uppercase_marker(self) -> None:
        rules = self._rules("A. Smith wrote the book.\n")
        self.assertEqual(rules, ["list.marker-ambiguous"])

    def test_a_citation_opening_a_block(self) -> None:
        self.assertIn("list.marker-ambiguous",
                      self._rules("@smith2020. This is the claim.\n"))

    def test_a_word_that_parses_as_a_roman_numeral(self) -> None:
        for word in ("mix. of things\n", "cd. drive\n"):
            with self.subTest(word=word):
                self.assertIn("list.marker-ambiguous", self._rules(word))

    def test_nothing_is_rewritten_by_a_diagnostic(self) -> None:
        for source in ("A. Smith wrote the book.\n",
                       "@smith2020. This is the claim.\n",
                       "mix. of things\n"):
            with self.subTest(source=source):
                self.assertEqual(self._fix(source), source)


class SilenceTests(MarkerTestCase):
    """What must stay quiet, which is the whole noise budget."""

    QUIET = (
        # Ordinary markers: nothing is in doubt about these.
        "a. lower alpha\n",
        "1. decimal\n",
        "i. roman one\n",
        "iv. roman four\n",
        "xxx. roman thirty\n",
        "A.  already two columns\n",
        "@. an anonymous example list\n",
        "(@) another one\n",
        # Prose that merely contains the shapes.
        "A sentence about cc.fth and its callers.\n",
        "Prose mentioning mix. in the middle of a line.\n",
        "An email to a@b.com in prose.\n",
        # Not markers: no separator, or the wrong case.
        "did. not a numeral\n",
        "ll. not a numeral\n",
        "A.no space\n",
    )

    def test_the_quiet_set_is_quiet(self) -> None:
        for source in self.QUIET:
            with self.subTest(source=source):
                self.assertEqual(self._rules(source), [], source)

    def test_a_marker_shape_mid_paragraph_is_not_doubted(self) -> None:
        # Where a paragraph is open, Pandoc and the author agree the line is
        # prose. Reporting it would be pure noise — 2 occurrences in the
        # corpora, both genuine wrapped sentences.
        for tail in ("A. Smith wrote it.", "@smith2020. A claim.",
                     "mix. of things"):
            with self.subTest(tail=tail):
                self.assertEqual(self._rules("Wrapped prose ending a line\n"
                                             + tail + "\n"), [])

    def test_the_repository_and_the_docs_are_quiet(self) -> None:
        # The rule has to survive contact with real prose before it is worth
        # leaving on.
        for path in sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]:
            with self.subTest(path=path.name):
                result = subprocess.run(
                    [str(MDFIX), "-n", "--diagnostics", str(path)],
                    capture_output=True, text=True)
                rules = [json.loads(line)["rule"]
                         for line in result.stderr.splitlines()
                         if line.startswith("{")]
                self.assertNotIn("list.marker-ambiguous", rules)
                self.assertNotIn("list.marker-column", rules)


if __name__ == "__main__":
    unittest.main()
