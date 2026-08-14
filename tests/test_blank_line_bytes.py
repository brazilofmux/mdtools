"""
A blank line may still have bytes in it (issue #116).

`process()` printed `"\\n"` for every line it classified as blank, so a
whitespace-only line was emptied on **every** run — including a bare one,
which is meant to perform the four required repairs and nothing else — and no
fix was recorded for it. That makes `-n` answer "clean. Nothing to fix." about
a file it is about to change, and a dry run that under-reports is worse than
no dry run, because it is trusted. The README tells people to run one before
letting a profile near their prose.

Pandoc reads `" \\n"` and `"\\n"` identically, so emptying the line is not a
required repair: it belongs to `-w`, where the fix is counted like every other
trailing-whitespace fix.

Found on a real file — `plan_volume1.md` in the *Evolution of the Sacred*
repository begins with such a line — and only because `--canonical-lint`
compares output against input rather than trusting the fix counter. That check
earning its keep is the good news in the report.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from mdtools_cli.contract import FINDINGS, OK, USAGE

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"

# Whitespace-only lines, in every position the report names.
LEADING = " \n# Title\n\nBody.\n"
INTERIOR = "# Title\n \nBody.\n"
TABBED = "# Title\n\t\nBody.\n"
WIDE = "# Title\n   \nBody.\n"


class BlankBytesTestCase(unittest.TestCase):
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

    def _dry_run(self, text: str, *flags: str) -> str:
        src = self.dir / "in.md"
        src.write_text(text, encoding="utf-8")
        result = subprocess.run([str(MDFIX), "-n", "-v", *flags, str(src)],
                                capture_output=True, text=True)
        return result.stdout + result.stderr


class RequiredOnlyTests(BlankBytesTestCase):
    """A bare run performs the required repairs and nothing else."""

    def test_a_whitespace_only_line_survives(self) -> None:
        for name, source in (("leading", LEADING), ("interior", INTERIOR),
                             ("tab", TABBED), ("wide", WIDE)):
            with self.subTest(case=name):
                self.assertEqual(self._fix(source), source)

    def test_the_dry_run_agrees_with_the_real_one(self) -> None:
        # The property the whole issue turns on, asserted directly rather
        # than through either half's wording.
        for name, source in (("leading", LEADING), ("interior", INTERIOR)):
            for flags in ((), ("-w",), ("--canonical",)):
                with self.subTest(case=name, flags=flags):
                    changed = self._fix(source, *flags) != source
                    reported = "clean. Nothing to fix." not in self._dry_run(
                        source, *flags)
                    self.assertEqual(changed, reported)


class TrailingWhitespaceTests(BlankBytesTestCase):
    """With `-w`, the line is emptied — and counted."""

    def test_the_line_is_emptied(self) -> None:
        self.assertEqual(self._fix(LEADING, "-w"), "\n# Title\n\nBody.\n")
        self.assertEqual(self._fix(INTERIOR, "-w"), "# Title\n\nBody.\n")

    def test_the_fix_is_reported(self) -> None:
        out = self._dry_run(LEADING, "-w")
        self.assertIn("trailing whitespace normalized", out)
        self.assertNotIn("clean. Nothing to fix.", out)

    def test_a_tab_only_line_is_emptied_too(self) -> None:
        # I expected the tab exception to apply and it does not, correctly.
        # mdfix refuses to touch trailing whitespace containing a tab on a
        # *content* line, because Pandoc's expansion decides whether it is a
        # hard break and that depends on a reader flag. A blank line has no
        # content in front of it, so it cannot be a break and there is
        # nothing to decide — `trailing_has_tab` says so in as many words.
        self.assertEqual(self._fix(TABBED, "-w"), "# Title\n\nBody.\n")
        self.assertEqual(self._fix(TABBED), TABBED)

    def test_an_already_empty_line_is_not_a_fix(self) -> None:
        source = "# Title\n\nBody.\n"
        self.assertEqual(self._fix(source, "-w"), source)
        self.assertIn("clean. Nothing to fix.", self._dry_run(source, "-w"))

    def test_it_is_idempotent(self) -> None:
        once = self._fix(LEADING, "--canonical")
        self.assertEqual(self._fix(once, "--canonical"), once)


class LintExitCodeTests(BlankBytesTestCase):
    """The gate ran; a non-canonical file is a finding."""

    def _lint(self, text: str) -> int:
        src = self.dir / "in.md"
        src.write_text(text, encoding="utf-8")
        return subprocess.run([str(MDFIX), "-q", "--canonical-lint", str(src)],
                              capture_output=True, text=True).returncode

    def test_a_clean_file_is_zero(self) -> None:
        self.assertEqual(self._lint("# Title\n\nBody.\n"), OK)

    def test_a_counted_fix_is_findings(self) -> None:
        self.assertEqual(self._lint("#Title\n\nBody.\n"), FINDINGS)

    def test_a_whitespace_only_line_is_findings(self) -> None:
        self.assertEqual(self._lint(LEADING), FINDINGS)

    def test_an_uncounted_normalization_is_findings(self) -> None:
        # CRLF and a missing final newline still change content with no fix
        # category to their name. That is a finding about the file, not a
        # failure of the tool.
        src = self.dir / "crlf.md"
        src.write_bytes(b"- item\r\n")
        result = subprocess.run(
            [str(MDFIX), "--canonical-lint", str(src)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, FINDINGS)
        self.assertIn("output differs from input", result.stderr)

    def test_a_missing_file_is_usage(self) -> None:
        result = subprocess.run(
            [str(MDFIX), "-q", "--canonical-lint", str(self.dir / "gone.md")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, USAGE)
        self.assertTrue(result.stderr.strip())

    def test_an_unreadable_file_is_usage(self) -> None:
        path = self.dir / "not-a-file"
        path.mkdir()
        result = subprocess.run(
            [str(MDFIX), "-q", "--canonical-lint", str(path)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, USAGE)

    def test_a_skipped_file_outranks_a_finding(self) -> None:
        dirty = self.dir / "dirty.md"
        dirty.write_text("#Title\n\nBody.\n", encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", "--canonical-lint",
             str(self.dir / "gone.md"), str(dirty)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, USAGE)


if __name__ == "__main__":
    unittest.main()
