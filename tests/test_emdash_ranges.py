"""
A range is not an aside (issue #119).

`chicago.emdash-spacing` turns ` -- ` into an em-dash, which is what an author
means by it in prose. Two things it got wrong, both from one crude predicate.

**A numeric range was converted.** `1--3` is a range, and Chicago 6.78 makes a
range an en-dash. Pandoc's `smart` already renders `--` as `–`, so the input
was correct and the pass made it wrong. All 19 numeric ranges in the corpora
were converted; the hit rate on the pattern was 100%. The spaced form is
worse still — an em-dash with spaces around it, between two digits, is a
construction Chicago has no use for.

**A definition bullet was skipped.** `- **shall** -- The behavior…` did not
convert, because the character before the dash is the `*` closing the bold
rather than a word. The aside two lines down did convert. The result is a
document less consistent than before the pass ran, which is what made the
reporter normalize the dashes outside mdfix instead.

Nothing protected either shape on purpose: `is_dash_join_char` has been
alphanumerics-and-closers since the initial import, and inline markup was
simply not in the list.

The one hazard in adding it: `*` also spells a bullet marker, and `* -- text`
is a list item whose text starts with a dash. A marker opening the line is not
the end of emphasis, and joining it would eat the marker.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")

EM = "—"
EN = "–"


class DashTestCase(unittest.TestCase):
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
        src, out = self.dir / "in.md", self.dir / "out.md"
        src.write_text(text, encoding="utf-8")
        if out.exists():
            out.unlink()
        result = subprocess.run([str(MDFIX), "-q", *flags, str(src), str(out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def _rendered(self, text: str) -> str:
        return subprocess.run([PANDOC, "-f", "markdown", "-t", "plain"],
                              input=text, capture_output=True, text=True,
                              check=True).stdout


class RangeTests(DashTestCase):
    RANGES = (
        "- **Part I** (Chapters 1--3) establishes the purpose.\n",
        "See pages 10--20 for the rest.\n",
        "The years 1914--1918 are the subject.\n",
    )

    def test_a_numeric_range_is_left_alone(self) -> None:
        for source in self.RANGES:
            for flags in ("--chicago-punct", "--canonical", "--technical"):
                with self.subTest(source=source[:26], flags=flags):
                    self.assertIn("--", self._fix(source, flags))
                    self.assertNotIn(EM, self._fix(source, flags))

    def test_the_spaced_profile_does_not_space_a_range(self) -> None:
        # The worse of the two forms in the report: an em-dash with spaces
        # around it, between two digits.
        out = self._fix(self.RANGES[0], "--chicago-punct", "--spaced-emdash")
        self.assertIn("1--3", out)

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_already_renders_the_range_correctly(self) -> None:
        # Why leaving it alone is right rather than merely safe: the input
        # was already correct, and `smart` gives the en-dash a range wants.
        self.assertIn(f"1{EN}3", self._rendered("Chapters 1--3 here.\n"))

    def test_an_aside_between_words_still_converts(self) -> None:
        out = self._fix("The core -- object model -- has converged.\n",
                        "--chicago-punct")
        self.assertNotIn("--", out)
        self.assertEqual(out.count(EM), 2)


class DefinitionBulletTests(DashTestCase):
    BULLETS = ("- **shall** -- The behavior is an absolute requirement.\n"
               "- **should** -- There may exist valid reasons to ignore it.\n")

    def test_a_definition_bullet_converts(self) -> None:
        out = self._fix(self.BULLETS, "--chicago-punct")
        self.assertNotIn("--", out)
        self.assertEqual(out.count(EM), 2)

    def test_the_spaced_profile_gives_the_form_the_manuscript_uses(self) -> None:
        # What the reporter had already normalized to by hand.
        out = self._fix(self.BULLETS, "--chicago-punct", "--spaced-emdash")
        self.assertIn(f"**shall** {EM} The behavior", out)

    def test_a_closing_code_span_also_joins(self) -> None:
        # Same shape, same reasoning: a backtick ends a word.
        out = self._fix("See `%M` -- NOT to `%R`.\n", "--chicago-punct")
        self.assertIn(f"`%M`{EM}NOT", out)

    def test_emphasis_on_the_far_side_joins_too(self) -> None:
        out = self._fix("The core -- **object model** -- converged.\n",
                        "--chicago-punct")
        self.assertNotIn("--", out)


class BulletMarkerTests(DashTestCase):
    """The hazard that came with treating `*` as the end of a word."""

    def test_a_star_bullet_is_not_eaten(self) -> None:
        source = "* -- a star bullet whose text starts with a dash\n"
        self.assertEqual(self._fix(source, "--chicago-punct"), source)

    def test_a_hyphen_bullet_is_not_eaten(self) -> None:
        source = "- -- a hyphen bullet likewise\n"
        self.assertEqual(self._fix(source, "--chicago-punct"), source)

    def test_an_indented_marker_is_still_a_marker(self) -> None:
        source = "  * -- nested, and still a bullet\n"
        self.assertEqual(self._fix(source, "--chicago-punct"), source)

    def test_emphasis_mid_line_is_not_a_marker(self) -> None:
        # The distinction is position, not the character: the same `*` in the
        # middle of a line closes emphasis and does join.
        out = self._fix("Text *emph* -- and more.\n", "--chicago-punct")
        self.assertIn(f"*emph*{EM}and", out)

    def test_a_quoted_star_bullet_is_not_eaten(self) -> None:
        source = "> * -- a quoted star bullet\n"
        for flags in ("--chicago-punct", "--canonical"):
            with self.subTest(flags=flags):
                self.assertEqual(self._fix(source, flags), source)

    def test_a_tight_quoted_star_bullet_is_not_eaten(self) -> None:
        source = ">* -- tight against the marker\n"
        self.assertEqual(self._fix(source, "--chicago-punct"), source)
        # --canonical adds the space after `>` first; the dash must stay.
        self.assertEqual(self._fix(source, "--canonical"),
                         "> * -- tight against the marker\n")

    def test_quoted_emphasis_still_joins(self) -> None:
        out = self._fix("> *emph* -- aside\n", "--chicago-punct")
        self.assertIn(f"*emph*{EM}aside", out)


class ProtectedTests(DashTestCase):
    def test_a_flag_in_a_code_span_is_untouched(self) -> None:
        # Corpus check: a complete code span, so the `--` action never
        # sees the bytes. Keep it so a later scanner change cannot eat
        # the README form.
        source = "Pass `--editorial --no-arrow-aside` to the tool.\n"
        for flags in ("--canonical", "--technical"):
            with self.subTest(flags=flags):
                self.assertIn("`--editorial", self._fix(source, flags))

    def test_an_unspaced_closer_does_not_join(self) -> None:
        # The spacing guard: a closer then unspaced `--` is not an aside.
        # This is the case that would move if `spaced &&` were dropped.
        source = "See `%M`--NOT to `%R`.\n"
        self.assertEqual(self._fix(source, "--chicago-punct"), source)

    def test_a_code_span_is_untouched(self) -> None:
        source = "Run `git log -- path/to/file` for that.\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_a_command_line_flag_is_untouched(self) -> None:
        # Space before, none after: the long-standing `--flag` guard.
        source = "Pass --canonical to the tool.\n"
        self.assertEqual(self._fix(source, "--chicago-punct"), source)


if __name__ == "__main__":
    unittest.main()
