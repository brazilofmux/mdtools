"""
`!` is sentence-final only after a word (issue #102).

The Chicago space-after-punctuation rule keys on what *follows* the mark. That
is enough for `,` `;` `:` `.` and wrong for `!`: Khoisan orthography writes a
click consonant with a leading `!`, so a rule that fires on "`!` then a letter"
re-spells every San term it meets.

This is the worst kind of finding this repository can produce. It is a content
change to a proper noun in a language that uses the character phonemically; it
is invisible on the page, because `! Kung` reads as prose that happens to end a
sentence; and it shipped — *Evolution of the Sacred* went to print with it, and
`git log -S'! Kung'` names the commit `Ran mdfix.`

The fix is one condition: a letter or digit must precede the mark. Measured
over 511 files of manuscript, the `!` branch fired 4 times, all clicks, all
damage. Its only legitimate repair is `Wow!Next`, which still fires.
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

CHICAGO_FLAGS = ("--chicago-punct-2", "--canonical", "--technical",
                 "--editorial")

# Every preceding context from the report, verbatim. None of these is a
# sentence ending, and the mark carries meaning in all of them.
CLICKS = (
    "the !Kung San",
    "Ju/'hoansi (!Kung) San",
    "they entered *!kia* states",
    "they used **!kanna** for trance",
    'the "!Kung" people',
    "see [!kia] below",
    "!Kung opens the line",
    "a click ?something odd",
)

# What the rule exists for: a missing space after a real sentence ending.
TYPOS = (
    ("Wow!Next sentence.", "Wow! Next sentence."),
    ("Really?Yes.", "Really? Yes."),
    ("Stop!Go now.", "Stop! Go now."),
)


class ClickTestCase(unittest.TestCase):
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


class ClickConsonantTests(ClickTestCase):
    def test_a_click_survives_every_chicago_profile(self) -> None:
        for line in CLICKS:
            for flags in CHICAGO_FLAGS:
                with self.subTest(line=line, flags=flags):
                    source = line + "\n"
                    self.assertEqual(self._fix(source, flags), source)

    def test_the_real_repair_still_fires(self) -> None:
        for source, expected in TYPOS:
            with self.subTest(source=source):
                self.assertEqual(self._fix(source + "\n", "--canonical"),
                                 expected + "\n")

    def test_a_letter_in_any_script_ends_a_sentence(self) -> None:
        # `mdfix_is_word` answers "is this a letter", so the rule is not
        # limited to ASCII. Yorùbá and Greek end sentences too.
        for source, expected in (("Ọ̀ṣun!Next.", "Ọ̀ṣun! Next."),
                                 ("Ναί!Next.", "Ναί! Next.")):
            with self.subTest(source=source):
                self.assertEqual(self._fix(source + "\n", "--canonical"),
                                 expected + "\n")

    def test_a_digit_ends_a_sentence(self) -> None:
        self.assertEqual(self._fix("Chapter 12!Next.\n", "--canonical"),
                         "Chapter 12! Next.\n")

    def test_the_unicode_click_letters_stay_untouched(self) -> None:
        # U+01C0–U+01C3 are the correct spelling, and were never at risk —
        # pinned so that "fixing" the ASCII case never reaches them either.
        source = "ǃKung and ǀXam and ǂHoan and ǁGana.\n"
        for flags in CHICAGO_FLAGS:
            with self.subTest(flags=flags):
                self.assertEqual(self._fix(source, flags), source)

    def test_an_image_is_not_a_sentence_ending(self) -> None:
        # `![alt](src)` begins with the same byte. It was protected by the
        # bracket that follows; now it is protected by the space before it too.
        source = "Text ![alt](i.png) more.\n"
        for flags in CHICAGO_FLAGS:
            with self.subTest(flags=flags):
                self.assertEqual(self._fix(source, flags), source)

    def test_the_book_line_that_shipped(self) -> None:
        # Verbatim from religions/1-02-FireFeastFirstAltars.md:57.
        source = ("Wiessner's study of Ju/'hoansi (!Kung) San firelight "
                  "conversation.\n")
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_the_footnote_and_the_body_now_agree(self) -> None:
        # How it stayed invisible: footnote definitions are structural and
        # skip the prose scanner, so the same term survived in the note and
        # broke in the body two lines above. The exemption is deliberate and
        # unchanged; what is gone is the divergence it produced here.
        source = ("Body text with the !Kung here.\n"
                  "\n"
                  "[^1]: **A Note.** The !Kung San danced.\n")
        out = self._fix(source, "--canonical")
        self.assertEqual(out.count("!Kung"), 2)
        self.assertNotIn("! Kung", out)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class RenderedFormTests(ClickTestCase):
    """The mark reaches the reader unchanged, not just the file."""

    def _plain(self, text: str) -> str:
        return subprocess.run([PANDOC, "-f", "markdown", "-t", "plain"],
                              input=text, capture_output=True, text=True,
                              check=True).stdout

    def test_the_rendered_term_is_unchanged(self) -> None:
        source = "The !Kung San and their *!kia* dance.\n"
        self.assertEqual(self._plain(self._fix(source, "--canonical")),
                         self._plain(source))


if __name__ == "__main__":
    unittest.main()
