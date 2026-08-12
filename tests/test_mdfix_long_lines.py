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
        # MAX_CONTENT is the capacity, so exactly that much must be accepted.
        # The guard used to reject it while the error message named it as the
        # limit, and this test had to compensate with MAX_CONTENT - 1.
        path = self.dir / "edge.md"
        path.write_text("x" * MAX_CONTENT + "\n", encoding="utf-8")
        out = self.dir / "out.md"
        result = _run(["-q", str(path), str(out)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), path.read_text(encoding="utf-8"))

    def test_line_at_limit_plus_one_fails(self) -> None:
        path = self.dir / "edge.md"
        path.write_text("x" * (MAX_CONTENT + 1) + "\n", encoding="utf-8")
        result = _run(["-n", "-q", str(path)])
        self.assertEqual(result.returncode, 1)
        # The message must name the largest accepted length, not the rejected one.
        self.assertIn(f"limit {MAX_CONTENT}", result.stderr)
        self.assertIn(str(MAX_CONTENT + 1), result.stderr)


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

    def test_line_lengthening_fixes_are_detected(self) -> None:
        # A fix that *lengthens* the line must still fail the gate. The earlier
        # version of this test wrote an arrow case and then overwrote the same
        # path with a `*` → `-` substitution, which is length-preserving — so
        # the stated invariant was never exercised, and the second write made
        # the first two lines dead code.
        #
        # These inputs matter beyond the gate: each drives an in-place fixer
        # that grows the line, which is exactly what overflowed a right-sized
        # line allocation. Run under `make asan` to check that directly.
        cases = {
            "heading.md": "#Title\n",            # -> "# Title"
            "quote.md": ">Quoted text here\n",   # -> "> Quoted text here"
            "footnote.md": "[^1]:note text\n",   # -> "[^1]: note text"
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                path = self.dir / name
                path.write_text(text, encoding="utf-8")
                before = path.read_bytes()

                result = _run(["--canonical-lint", str(path)])
                self.assertEqual(result.returncode, 2, msg=result.stderr)
                # Lint is no-write: the input must be untouched.
                self.assertEqual(path.read_bytes(), before)

                # And the fix really does lengthen the line.
                out = self.dir / (name + ".out")
                fixed = _run(["-q", "--canonical", str(path), str(out)])
                self.assertEqual(fixed.returncode, 0, msg=fixed.stderr)
                self.assertGreater(
                    len(out.read_bytes()), len(before),
                    msg=f"{name}: expected the canonical fix to grow the line",
                )


if __name__ == "__main__":
    unittest.main()
