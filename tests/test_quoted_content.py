"""
A block quote is someone else's text (issue #125).

The Chicago passes applied to the *contents* of a `BlockQuote`, so a rule
meant for the author's prose was editing quoted material. `> …prints it ;`
became `> …prints it;` — the spaced semicolon is J. B. Baillie's 1910
typography, and a series that describes its quotations as verified verbatim
against the source stops being able to say so.

The verification harness downstream cannot catch it either, because it
normalizes punctuation before matching: the document drifts from the source
while the check still reports agreement.

**Exempt by default rather than behind a flag.** dialect-policy calls a
profile a bundle of things safe to apply without looking, and restyling
quoted text is not safe without looking. The asymmetry decides it: wrongly
editing a quotation falsifies evidence silently, while wrongly leaving a
callout alone costs a spaced dash in the author's own aside.

Measured across six corpora, 578 files: 150 block quotes whose bytes
`--canonical` changed, 103 of them in the Hegel and Foucault quotations of
`~/philosophers`.

What still runs: `blockquote.space`, which owns the marker rather than the
content, exactly as the bullet pass owns a list marker.
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

EM = "—"

# Verbatim from the report.
BAILLIE = ("> A quotation with a spaced em dash — exactly as the 1910 edition "
           "prints it ; and a spaced semicolon.\n")


class QuotedTestCase(unittest.TestCase):
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

    def _blocks(self, text: str) -> list:
        result = subprocess.run([PANDOC, "-f", "markdown", "-t", "json"],
                                input=text, capture_output=True, text=True,
                                check=True)
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]


class QuotedContentTests(QuotedTestCase):
    PROFILES = ("--chicago-punct", "--chicago-punct-2", "--canonical",
                "--technical", "--editorial")

    def test_the_reported_quotation_survives(self) -> None:
        for flags in self.PROFILES:
            with self.subTest(flags=flags):
                self.assertEqual(self._fix(BAILLIE, flags), BAILLIE)

    def test_the_shapes_each_pass_would_have_changed(self) -> None:
        for name, source in (
                ("space-before-punct", "> the source prints it ; like that\n"),
                ("emdash-spacing", "> an aside -- as printed\n"),
                ("sentence-space", "> One sentence.  Two spaces.\n"),
                ("abbrev-comma", "> a list e.g. this one\n"),
                ("arrow-aside", "> a mapping → as printed\n"),
                ("bold-colon", "> **Term**: as printed\n"),
                ("quote-terminal", '> he said "a thing".\n'),
        ):
            with self.subTest(rule=name):
                self.assertEqual(self._fix(source, "--canonical"), source)

    def test_a_lazy_continuation_is_still_the_quotation(self) -> None:
        # No `>` on the second line, and Pandoc still reads it as part of the
        # quote. A per-line test would have missed exactly this.
        source = ("> A quotation with a spaced semicolon ; and it continues\n"
                  "lazily onto this line ; without a marker.\n")
        self.assertEqual(self._fix(source, "--canonical"), source)
        if PANDOC:
            self.assertEqual(self._blocks(source), ["BlockQuote"])

    def test_a_nested_quote_is_quoted_too(self) -> None:
        source = "> > a quote inside a quote ; as printed\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_an_indented_quote_counts(self) -> None:
        source = "  > indented, and still a quotation ; here\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_the_quote_ends_at_a_blank_line(self) -> None:
        source = "> quoted ; here\n\nAuthor's own prose ; here.\n"
        out = self._fix(source, "--canonical")
        self.assertIn("> quoted ; here", out)
        self.assertIn("prose; here.", out)

    def test_the_quote_ends_at_a_heading(self) -> None:
        source = "> quoted ; here\n# A heading\n\nProse ; here.\n"
        out = self._fix(source, "--canonical")
        self.assertIn("> quoted ; here", out)
        self.assertIn("prose; here.", out.lower())

    def test_a_marker_inside_a_fence_is_not_a_quote(self) -> None:
        # The fence owns those bytes; nothing runs there either way, but the
        # marker must not start a quote that swallows the lines after it.
        source = ("```\n> not a quote ; here\n```\n\nProse ; here.\n")
        out = self._fix(source, "--canonical")
        self.assertIn("> not a quote ; here", out)
        self.assertIn("Prose; here.", out)


class MarkerTests(QuotedTestCase):
    """The pass that owns the construct still runs."""

    def test_the_marker_space_is_still_repaired(self) -> None:
        self.assertEqual(self._fix(">No space after the marker.\n",
                                   "--canonical"),
                         "> No space after the marker.\n")

    def test_the_marker_fix_does_not_touch_the_content(self) -> None:
        out = self._fix(">quoted ; as printed\n", "--canonical")
        self.assertEqual(out, "> quoted ; as printed\n")


class AuthorsProseTests(QuotedTestCase):
    """The exemption must not reach past the quotation."""

    def test_prose_around_a_quote_is_still_fixed(self) -> None:
        source = ("Before ; here.\n\n> Quoted ; here.\n\nAfter ; here.\n")
        out = self._fix(source, "--canonical")
        self.assertIn("Before; here.", out)
        self.assertIn("> Quoted ; here.", out)
        self.assertIn("After; here.", out)

    def test_a_list_item_is_not_a_quotation(self) -> None:
        source = "- an item ; here\n"
        self.assertEqual(self._fix(source, "--canonical"),
                         "- an item; here\n")

    def test_wrapping_still_leaves_quotes_alone(self) -> None:
        # Long-standing behaviour, pinned here because the new marking runs
        # beside `is_wrappable_at` and a regression would look like this.
        source = "> " + "word " * 40 + "end\n"
        self.assertEqual(self._fix(source, "--wrap=40"), source)


if __name__ == "__main__":
    unittest.main()
