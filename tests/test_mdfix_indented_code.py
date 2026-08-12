"""Indented code blocks are protected regions in mdfix (issue #30)."""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
ARROW = "→"


def _require_fresh_binary() -> None:
    if not MDFIX.is_file():
        raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
    source = ROOT / "mdfix" / "mdfix.c"
    if source.is_file() and source.stat().st_mtime > MDFIX.stat().st_mtime:
        raise AssertionError(
            f"{MDFIX} is older than {source} — rebuild with `make -C mdfix`"
        )


class IndentedCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_fresh_binary()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _fixes(self, source: str, *flags: str) -> str:
        """Verbose fix report for source."""
        path = self.dir / "in.md"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-n", "-v", *flags, str(path)],
            capture_output=True, text=True,
        )
        return result.stdout + result.stderr

    def _canonical(self, source: str, *flags: str) -> str:
        src = self.dir / "c_in.md"
        out = self.dir / "c_out.md"
        if out.exists():
            out.unlink()
        src.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", *(flags or ("--canonical",)), str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    # --- the reported cases -------------------------------------------------

    def test_four_space_code_is_not_rewritten(self) -> None:
        self.assertNotIn("arrow aside", self._fixes(f"    A {ARROW} B\n"))

    def test_tab_indented_code_is_not_rewritten(self) -> None:
        self.assertNotIn("arrow aside", self._fixes(f"\tA {ARROW} B\n"))

    def test_code_after_a_paragraph_and_blank_is_protected(self) -> None:
        source = f"Intro.\n\n    A {ARROW} B\n"
        self.assertNotIn("arrow aside", self._fixes(source))
        self.assertEqual(self._canonical(source), source)

    # --- the boundaries that must NOT become code ---------------------------

    def test_indented_code_cannot_interrupt_a_paragraph(self) -> None:
        # CommonMark: an indented line directly after paragraph text is a lazy
        # continuation, not code. Freezing it would stop fixing wrapped prose.
        self.assertIn("arrow aside", self._fixes(f"Intro line.\n    A {ARROW} B\n"))

    def test_list_continuation_is_not_code(self) -> None:
        # `- item` puts content at column 2, so a two-space continuation is
        # prose. Measuring from the margin instead would freeze it.
        self.assertIn("arrow aside", self._fixes(f"- item\n  more A {ARROW} B\n"))

    def test_plain_prose_is_still_fixed(self) -> None:
        self.assertIn("arrow aside", self._fixes(f"Prose A {ARROW} B here.\n"))

    # --- list-nested code ---------------------------------------------------

    def test_code_nested_in_a_list_item_is_protected(self) -> None:
        # Content column 2, so code starts at 6.
        source = f"- item\n\n      A {ARROW} B\n"
        self.assertNotIn("arrow aside", self._fixes(source))
        self.assertEqual(self._canonical(source), source)

    def test_code_nested_in_an_ordered_item_is_protected(self) -> None:
        # `1. ` puts content at column 3, so code starts at 7.
        source = f"1. item\n\n       A {ARROW} B\n"
        self.assertNotIn("arrow aside", self._fixes(source))

    def test_threshold_is_relative_to_the_list_marker(self) -> None:
        # Four spaces inside a `- item` is continuation prose, not code:
        # it is only two columns past the item's content column.
        self.assertIn("arrow aside", self._fixes(f"- item\n\n    A {ARROW} B\n"))

    # --- wrapping and round trips -------------------------------------------

    def test_technical_does_not_reflow_indented_code(self) -> None:
        long_line = (
            "    git log --pretty=format:\"%h %an %s\" "
            "--since=2024-01-01 --author=someone --all\n"
        )
        source = f"Intro.\n\n{long_line}"
        self.assertEqual(self._canonical(source, "--technical"), source)

    def test_code_bytes_are_preserved_exactly(self) -> None:
        # Trailing whitespace and interior tabs are significant in code, so
        # even the whitespace normalizer must keep its hands off.
        source = "Intro.\n\n    code with trailing   \n\ttabbed\t line\n"
        self.assertEqual(self._canonical(source), source)

    def test_blank_lines_inside_a_code_block_do_not_end_it(self) -> None:
        source = f"Intro.\n\n    first {ARROW} line\n\n    second {ARROW} line\n"
        self.assertNotIn("arrow aside", self._fixes(source))
        self.assertEqual(self._canonical(source), source)

    def test_prose_after_a_code_block_is_still_fixed(self) -> None:
        source = f"Intro.\n\n    code {ARROW} here\n\nAfter A {ARROW} B.\n"
        report = self._fixes(source)
        self.assertIn("arrow aside", report)
        # Exactly one *line* fixed: the prose, not the code. Count the
        # per-line reports only — the phrase also appears in the summary
        # tally, so a bare .count() reads 2 for a single fix.
        per_line = re.findall(r"line (\d+): arrow aside", report)
        self.assertEqual(per_line, ["5"], msg=report)

    def test_canonical_is_idempotent_over_mixed_content(self) -> None:
        source = (
            f"#Title\n\nProse A {ARROW} B.\n\n    code A {ARROW} B\n"
            f"\n- item\n\n      nested A {ARROW} B\n"
        )
        once = self._canonical(source)
        self.assertEqual(self._canonical(once), once)
        # The arrows inside code survive; the prose arrow does not.
        self.assertIn(f"    code A {ARROW} B", once)
        self.assertIn(f"      nested A {ARROW} B", once)
        self.assertNotIn(f"Prose A {ARROW} B.", once)


if __name__ == "__main__":
    unittest.main()
