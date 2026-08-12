"""Long-line reading and content-based canonical-lint (issue #8)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
# Must match mdfix.rl MAX_LINE - 1 (payload capacity after terminator strip).
MAX_CONTENT = 8191


def _require_fresh_binary() -> None:
    if not MDFIX.is_file():
        raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
    source = ROOT / "mdfix" / "mdfix.c"
    if source.is_file() and source.stat().st_mtime > MDFIX.stat().st_mtime:
        raise AssertionError(
            f"{MDFIX} is older than {source} — rebuild with `make -C mdfix`"
        )


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MDFIX), *args], capture_output=True, text=True
    )


class LongLineTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_fresh_binary()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_overlong_line_fails_clearly_not_silently_split(self) -> None:
        # Historical bug: fgets(8192) split a 9000-byte line into two "lines"
        # and wrote 9002 bytes while reporting clean.
        path = self.dir / "long.md"
        path.write_text("a" * 9000 + "\n", encoding="utf-8")
        out = self.dir / "out.md"
        result = _run(["-q", str(path), str(out)])
        self.assertEqual(result.returncode, 1)
        self.assertIn("refuses to silently split or truncate", result.stderr)
        self.assertIn("9000 bytes", result.stderr)
        self.assertFalse(out.exists())

    def test_line_at_limit_is_accepted(self) -> None:
        path = self.dir / "edge.md"
        # nread >= MAX_CONTENT fails; one less is fine.
        path.write_text("x" * (MAX_CONTENT - 1) + "\n", encoding="utf-8")
        out = self.dir / "out.md"
        result = _run(["-q", str(path), str(out)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), path.read_text(encoding="utf-8"))

    def test_line_at_limit_plus_one_fails(self) -> None:
        path = self.dir / "edge.md"
        path.write_text("x" * MAX_CONTENT + "\n", encoding="utf-8")
        result = _run(["-n", "-q", str(path)])
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(MAX_CONTENT), result.stderr)


class CanonicalLintContentTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_fresh_binary()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_clean_lf_file_passes(self) -> None:
        path = self.dir / "clean.md"
        path.write_text("- item\n", encoding="utf-8")
        result = _run(["--canonical-lint", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("clean", result.stderr)

    def test_crlf_fails_even_without_fix_counts(self) -> None:
        # Stripping CR is silent normalization: counters stay 0, content changes.
        path = self.dir / "crlf.md"
        path.write_bytes(b"- item\r\n")
        result = _run(["--canonical-lint", str(path)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("output differs from input", result.stderr)

    def test_missing_final_newline_fails(self) -> None:
        path = self.dir / "nonl.md"
        path.write_bytes(b"- item")
        result = _run(["--canonical-lint", str(path)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("output differs from input", result.stderr)

    def test_bullet_fix_fails_lint(self) -> None:
        path = self.dir / "star.md"
        path.write_text("* item\n", encoding="utf-8")
        result = _run(["--canonical-lint", str(path)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("failed", result.stderr)

    def test_expansion_detected(self) -> None:
        # A fix that lengthens the line must still fail the gate.
        path = self.dir / "arrow.md"
        path.write_text("A -> B pipeline.\n", encoding="utf-8")
        # Without --no-arrow-aside, → conversion may apply under canonical?
        # Canonical enables chicago punct etc. Use always-on * → -
        path.write_text("* expanded bullet that will change\n", encoding="utf-8")
        before = path.read_bytes()
        result = _run(["--canonical-lint", str(path)])
        self.assertEqual(result.returncode, 2)
        # Input file must not have been modified (lint is no-write).
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
