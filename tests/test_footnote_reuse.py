"""
Pandoc has no syntax for reusing a footnote (issue #115).

A reference used twice does not link twice to one note. It **duplicates the
note body** and renumbers every note after it, so a chapter that defines 24
prints 31.

Nothing about the source looks wrong. It is valid Markdown, every reference
resolves, `links.undefined-footnote` has nothing to say because the note *is*
defined, and the author's own read-through cannot catch it because the
duplication does not exist in the manuscript — only in the rendered book. It
reached print in two volumes of *Evolution of the Sacred*.

**Check-only, deliberately.** The repair is editorial and the tool cannot pick
it: dropping the second reference is right when two consecutive paragraphs
share a note, and wrong when the second sits mid-paragraph against a distinct
claim that would lose its citation. Both shapes occurred in the same chapter.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mdlinks.graph import check, read

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")

REUSED = ("First use.[^1] Later, again.[^1]\n"
          "\n"
          "Other.[^2]\n"
          "\n"
          "[^1]: The note body.\n"
          "\n"
          "[^2]: Second note.\n")


class FootnoteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _doc(self, text: str, name: str = "a.md") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _findings(self, *paths: Path) -> list:
        return check([read(p) for p in paths])

    def _rules(self, *paths: Path) -> list:
        return [f.rule for f in self._findings(*paths)]

    def _rendered_notes(self, text: str) -> list:
        html = subprocess.run([PANDOC, "-f", "markdown", "-t", "html"],
                              input=text, capture_output=True, text=True,
                              check=True).stdout
        return [line for line in html.splitlines() if '<li id="fn' in line]


@unittest.skipUnless(PANDOC, "pandoc not installed")
class OracleTests(FootnoteTestCase):
    """What Pandoc actually does, which is the whole reason for the rule."""

    def test_a_reused_reference_duplicates_the_note(self) -> None:
        notes = self._rendered_notes(REUSED)
        self.assertEqual(len(notes), 3)          # two defined, three printed
        self.assertEqual(sum("The note body." in n for n in notes), 2)

    def test_the_notes_after_it_are_renumbered(self) -> None:
        # `[^2]` was the second note in the source and is the third on the
        # page, which is what breaks a cross-reference to "note 2".
        notes = self._rendered_notes(REUSED)
        self.assertIn('id="fn3"', notes[2])
        self.assertIn("Second note.", notes[2])

    def test_one_reference_each_is_fine(self) -> None:
        single = REUSED.replace(" Later, again.[^1]", " Later, again.")
        self.assertEqual(len(self._rendered_notes(single)), 2)


class DetectionTests(FootnoteTestCase):
    def test_a_reused_footnote_is_reported(self) -> None:
        self.assertIn("links.reused-footnote", self._rules(self._doc(REUSED)))

    def test_it_is_an_error(self) -> None:
        # There is no reading in which the source is right: Pandoc has no
        # reuse syntax, so the rendered document is wrong however it is read.
        finding = next(f for f in self._findings(self._doc(REUSED))
                       if f.rule == "links.reused-footnote")
        self.assertEqual(finding.severity, "error")

    def test_it_points_at_the_second_reference(self) -> None:
        source = "One.[^a]\n\nTwo.[^a]\n\n[^a]: Body.\n"
        path = self._doc(source)
        finding = next(f for f in self._findings(path)
                       if f.rule == "links.reused-footnote")
        self.assertEqual(finding.line, 3)
        data = source.encode("utf-8")
        self.assertEqual(data[finding.start:finding.end], b"[^a]")
        self.assertIn("line 1", finding.message)

    def test_a_third_reference_is_reported_too(self) -> None:
        # Each extra reference is one more printed note and one more decision.
        source = "A.[^1] B.[^1] C.[^1]\n\n[^1]: Body.\n"
        rules = self._rules(self._doc(source))
        self.assertEqual(rules.count("links.reused-footnote"), 2)

    def test_a_single_reference_is_silent(self) -> None:
        source = "Only once.[^1]\n\n[^1]: Body.\n"
        self.assertEqual(self._rules(self._doc(source)), [])

    def test_distinct_labels_are_silent(self) -> None:
        source = "A.[^1] B.[^2]\n\n[^1]: One.\n\n[^2]: Two.\n"
        self.assertEqual(self._rules(self._doc(source)), [])

    def test_reuse_across_files_is_not_reuse(self) -> None:
        # Footnotes are per document; two chapters may both have a `[^1]`.
        a = self._doc("A.[^1]\n\n[^1]: One.\n", "a.md")
        b = self._doc("B.[^1]\n\n[^1]: Two.\n", "b.md")
        self.assertEqual(self._rules(a, b), [])

    def test_an_undefined_label_keeps_its_own_rule(self) -> None:
        # Twice-referenced and never defined is a missing note, not a reused
        # one — reporting both would be two complaints about one mistake.
        source = "A.[^x] B.[^x]\n\nNo definition here.\n"
        rules = self._rules(self._doc(source))
        self.assertEqual(set(rules), {"links.undefined-footnote"})

    def test_a_definition_is_not_a_reference(self) -> None:
        # `[^1]:` opening a definition must not count as a use of `[^1]`.
        source = "Once.[^1]\n\n[^1]: Body mentioning nothing.\n"
        self.assertEqual(self._rules(self._doc(source)), [])


class CompositionTests(FootnoteTestCase):
    """mdcheck composes mdlinks, so the gate inherits this for free."""

    def test_mdcheck_reports_it(self) -> None:
        from mdcheck.checks import run
        self._doc(REUSED)
        rules = [f.rule for f in run([self.dir])]
        self.assertIn("links.reused-footnote", rules)

    def test_the_cli_exits_with_findings(self) -> None:
        from mdtools_cli.contract import FINDINGS
        path = self._doc(REUSED)
        result = subprocess.run(
            [str(ROOT / "scripts" / "mdlinks"), "--check", str(path)],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                 "MDTOOLS_LIB": str(ROOT), "MDFIX": str(MDFIX)})
        self.assertEqual(result.returncode, FINDINGS, msg=result.stderr)
        # mdlinks' human format is `path:line: severity: message` — the rule
        # id appears in `--diagnostics` and in mdcheck's report, not here.
        self.assertIn("already referenced", result.stdout)


if __name__ == "__main__":
    unittest.main()
