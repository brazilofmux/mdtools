"""
Required transforms run by default (issue #55, architecture I2.3).

docs/transforms.md classifies every transform, and the test for "required" is
executable: omitting it leaves Pandoc reading the document as something other
than what the author wrote.

The classification is asserted in both directions. Required transforms must
repair their construct with no flags at all, and the transforms deemed optional
must genuinely be optional — Pandoc reading the construct identically with and
without them is what makes them a matter of taste.
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
DOC = ROOT / "docs" / "transforms.md"
PANDOC = shutil.which("pandoc")

# construct -> (blocks Pandoc sees unfixed, blocks it should see after mdfix)
REQUIRED_REPAIRS = {
    "blank line before a list": (
        "Intro:\n- one\n- two\n", ["Para"], ["Para", "BulletList"],
    ),
    "blank line after a list": (
        "- one\n- two\nAfter.\n", ["BulletList"], ["BulletList", "Para"],
    ),
    "space after the ATX marker": (
        "#Title\n\nBody.\n", ["Para", "Para"], ["Header", "Para"],
    ),
}

# Constructs docs/transforms.md calls optional. Pandoc must read each the same
# way with and without the transform — that is what "optional" means here.
NOT_REPAIRS = {
    "bullet marker style": "* one\n* two\n",
    "emphasis in a heading": "# **Bold** Title\n",
    "block quote without a space": ">Quoted text\n",
    "bare URL": "See http://example.com here.\n",
    "trailing hashes": "# Title ###\n",
    "fence width": "~~~~\ncode\n~~~~\n",
    "footnote spacing": "Text[^1].\n\n[^1]: Note.\n",
}


class RequiredTestCase(unittest.TestCase):
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
        result = subprocess.run(
            [str(MDFIX), "-q", *flags, str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def _blocks(self, text: str) -> list[str]:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json"],
            input=text, capture_output=True, text=True, check=True,
        )
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]


@unittest.skipUnless(PANDOC, "pandoc not installed")
class RequiredByDefaultTests(RequiredTestCase):
    def test_each_repair_happens_with_no_flags(self) -> None:
        # I2.3. Before this, the ATX case needed --heading-canonical, so a
        # bare run left a heading reading as a paragraph and called it fixed.
        for name, (source, unfixed, fixed) in REQUIRED_REPAIRS.items():
            with self.subTest(construct=name):
                self.assertEqual(self._blocks(source), unfixed,
                                 "the construct is no longer misread — "
                                 "update docs/transforms.md")
                self.assertEqual(self._blocks(self._fix(source)), fixed)

    def test_repairs_hold_together_in_one_document(self) -> None:
        source = "#Title\n\nIntro:\n- one\nAfter.\n"
        self.assertEqual(self._blocks(self._fix(source)),
                         ["Header", "Para", "BulletList", "Para"])

    def test_no_required_disables_them(self) -> None:
        source = "#Title\n\nIntro:\n- one\nAfter.\n"
        self.assertEqual(self._blocks(self._fix(source, "--no-required")),
                         ["Para", "Para"])

    def test_no_required_is_documented_as_inspection_only(self) -> None:
        # usage() writes to stderr, so read both.
        run = subprocess.run([str(MDFIX), "-h"], capture_output=True, text=True)
        help_text = run.stdout + run.stderr
        self.assertIn("--no-required", help_text)
        self.assertIn("Pandoc-readable", help_text)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class OptionalIsGenuinelyOptionalTests(RequiredTestCase):
    """
    The other half of the classification. If Pandoc read one of these
    differently without its transform, it would belong in the required set.
    """

    def test_pandoc_reads_these_the_same_either_way(self) -> None:
        for name, source in NOT_REPAIRS.items():
            with self.subTest(construct=name):
                before = self._blocks(source)
                after = self._blocks(self._fix(source, "--canonical"))
                self.assertEqual(
                    before, after,
                    f"{name}: block structure changed, so this may be a "
                    "required repair rather than an optional transform",
                )

    def test_wrapping_a_bare_url_adds_a_link(self) -> None:
        # Why --pandoc-safe-links is optional despite the name: it does not
        # repair a misread, it adds a Link that was not in the document.
        source = "See http://example.com here.\n"
        native = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "native"],
            input=source, capture_output=True, text=True, check=True).stdout
        self.assertNotIn("Link", native)
        fixed = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "native"],
            input=self._fix(source, "--pandoc-safe-links"),
            capture_output=True, text=True, check=True).stdout
        self.assertIn("Link", fixed)


class ClassificationDocumentTests(unittest.TestCase):
    """The document is the classification; keep the two in step."""

    def test_document_exists_and_lists_the_required_set(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        for phrase in ("Blank line **before** a list",
                       "Blank line **after** a list",
                       "Space after the ATX marker"):
            with self.subTest(row=phrase):
                self.assertIn(phrase, text)

    def test_the_required_set_stays_small(self) -> None:
        # Three repairs. A fourth belongs here only with an I2.1 or I2.2
        # justification, which is what the document is for.
        self.assertEqual(len(REQUIRED_REPAIRS), 3)

    def test_still_on_by_default_is_recorded(self) -> None:
        # I3.3 is not yet true; the document says so rather than implying the
        # classification is finished.
        self.assertIn("Still on by default", DOC.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
