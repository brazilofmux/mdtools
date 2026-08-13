"""Wrap output lines must be <= N display columns; unspaced CJK stays one line."""

from __future__ import annotations

import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
VENDOR = ROOT / "mdfix" / "vendor" / "utf_width.c"
VENDOR_H = ROOT / "mdfix" / "vendor" / "utf_width.h"


def columns(text: str) -> int:
    """The reference implementation: Python's own Unicode data."""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return total


class WrapWidthTests(unittest.TestCase):
    WIDTH = 78

    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        sources = [
            ROOT / "mdfix" / "mdfix.c",
            VENDOR,
            VENDOR_H,
        ]
        bin_mtime = MDFIX.stat().st_mtime
        for source in sources:
            if source.is_file() and source.stat().st_mtime > bin_mtime:
                raise AssertionError(
                    f"{MDFIX} is older than {source} — rebuild with `make -C mdfix`"
                )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _wrap(self, text: str) -> list[str]:
        src, out = self.dir / "in.md", self.dir / "out.md"
        src.write_text(text, encoding="utf-8")
        if out.exists():
            out.unlink()
        subprocess.run(
            [str(MDFIX), "-q", "--technical", str(src), str(out)],
            capture_output=True, check=True,
        )
        return out.read_text(encoding="utf-8").splitlines()

    CASES = {
        "ASCII": "word " * 60,
        "Greek": "Θεολογία καὶ φιλοσοφία " * 8,
        "Cyrillic": "Математика и философия " * 8,
        "CJK with spaces": "日本語 テキスト " * 14,
        "mixed scripts": "English Ελληνικά 漢字 " * 10,
        "combining marks": "échappé " * 20,
    }

    def test_no_line_exceeds_the_requested_width(self) -> None:
        for name, text in self.CASES.items():
            with self.subTest(script=name):
                for line in self._wrap(text.strip() + "\n"):
                    self.assertLessEqual(columns(line), self.WIDTH, line)

    def test_lines_actually_fill_the_width(self) -> None:
        # The bug was under-filling, so an upper bound alone would have
        # passed throughout. Every line but the last must be substantial.
        for name, text in self.CASES.items():
            with self.subTest(script=name):
                lines = self._wrap(text.strip() + "\n")
                for line in lines[:-1]:
                    self.assertGreater(columns(line), self.WIDTH // 2, line)

    def test_text_survives_wrapping(self) -> None:
        for name, text in self.CASES.items():
            with self.subTest(script=name):
                lines = self._wrap(text.strip() + "\n")
                joined = "".join(lines).replace(" ", "")
                self.assertEqual(joined, text.strip().replace(" ", ""))

    def test_ascii_behaviour_is_unchanged(self) -> None:
        lines = self._wrap("word " * 60 + "\n")
        self.assertTrue(all(len(line) == 74 for line in lines[:-1]))

    def test_a_combining_mark_costs_no_column(self) -> None:
        # "e" + U+0301 is one column, not two. A wrong width-1 charge would
        # under-fill NFD relative to NFC; pin both fill and parity.
        base = "échappé "
        nfc = unicodedata.normalize("NFC", base) * 20
        nfd = unicodedata.normalize("NFD", base) * 20
        nfc_lines = self._wrap(nfc.strip() + "\n")
        nfd_lines = self._wrap(nfd.strip() + "\n")
        self.assertEqual(len(nfc_lines), len(nfd_lines))
        for line in nfd_lines:
            self.assertLessEqual(columns(line), self.WIDTH, line)
        for line in nfd_lines[:-1]:
            self.assertGreater(columns(line), self.WIDTH // 2, line)
        # Same visual width per line once combining marks are ignored.
        for a, b in zip(nfc_lines, nfd_lines):
            self.assertEqual(columns(a), columns(b), (a, b))

    def test_unspaced_cjk_is_left_on_one_line(self) -> None:
        # Deliberate: `east_asian_line_breaks` is off in the pinned profile
        # (dialect-policy §3), so there is no break opportunity without a
        # space. Breaking anyway would invent one pandoc does not recognize.
        lines = self._wrap("日本語のテキストです" * 8 + "\n")
        self.assertEqual(len(lines), 1)

    def test_a_long_word_is_not_split(self) -> None:
        text = "short " + "x" * 200 + " tail\n"
        lines = self._wrap(text)
        self.assertIn("x" * 200, lines)


class VendorTests(unittest.TestCase):
    """The vendored table is generated; keep it recognizably so."""

    def test_provenance_is_recorded(self) -> None:
        head = VENDOR.read_text(encoding="utf-8")[:2500]
        self.assertIn("VENDORED, DO NOT EDIT", head)
        self.assertIn("github.com/brazilofmux/utf", head)
        self.assertIn("commit", head)

    def test_symbol_is_renamed_to_avoid_collision(self) -> None:
        # If mdtools ever links libutf, this copy must not clash with it.
        text = VENDOR.read_text(encoding="utf-8")
        self.assertIn("mdfix_display_width", text)
        self.assertNotIn("int co_console_width", text)

    def test_tables_are_static(self) -> None:
        text = VENDOR.read_text(encoding="utf-8")
        for name in ("tr_widths_itt", "tr_widths_sot", "tr_widths_sbt"):
            with self.subTest(table=name):
                self.assertIn(f"static const unsigned", text)
                self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
