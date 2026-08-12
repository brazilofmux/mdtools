"""
L1 encoding validation (issue #53, architecture invariant I1.1).

mdtools expects UTF-8. Malformed input used to be accepted silently and copied
into the IR, so `--emit-ir` produced JSON no parser could read — I4.1 was false
for a reason unrelated to Markdown, and every consumer inherited it.

Rejection rather than U+FFFD substitution is deliberate: replacement changes
byte lengths and would invalidate I1.3, and silently repairing an author's
encoding is the wrong default for a tool that edits manuscripts.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"


class EncodingTestCase(unittest.TestCase):
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

    def _write(self, data: bytes, name: str = "t.md") -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(MDFIX), *argv], capture_output=True, text=True)


class RejectionTests(EncodingTestCase):
    """Every class RFC 3629 excludes, plus NUL."""

    CASES = {
        "unexpected continuation byte": b"# a\x80\n",
        "overlong two-byte sequence": b"# a\xc0\x80\n",
        "overlong three-byte sequence": b"# a\xe0\x80\x80\n",
        "overlong four-byte sequence": b"# a\xf0\x80\x80\x80\n",
        "UTF-16 surrogate (U+D800..U+DFFF)": b"# a\xed\xa0\x80\n",
        "codepoint above U+10FFFF": b"# a\xf4\x90\x80\x80\n",
        "truncated three-byte sequence": b"# a\xe2\x82\n",
        "truncated two-byte sequence": b"# a\xc3\n",
        "invalid lead byte": b"# a\xf5\n",
    }

    def test_each_class_is_rejected_with_its_reason(self) -> None:
        for reason, data in self.CASES.items():
            with self.subTest(case=reason):
                path = self._write(data)
                result = self._run("--emit-ir", str(path))
                self.assertEqual(result.returncode, 1)
                self.assertIn(reason, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_the_byte_offset_is_named(self) -> None:
        # A diagnostic that says "somewhere in this file" is not actionable.
        path = self._write(b"# ok\n\nbad \xff here\n")
        result = self._run("--emit-ir", str(path))
        self.assertIn("byte offset 10", result.stderr)
        self.assertIn("line 3", result.stderr)

    def test_nul_is_rejected_rather_than_truncating(self) -> None:
        # The regression this closes: every fixer is strlen-bounded, so a NUL
        # ended the line and the remainder was dropped on output. A 36-byte
        # file came back 22 bytes with `AFTER` gone, silently.
        path = self._write(b"# Title\n\nBefore\x00AFTER\n\nEnd.\n")
        result = self._run("--emit-ir", str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("NUL", result.stderr)

    def test_no_ir_is_emitted_for_a_rejected_file(self) -> None:
        path = self._write(b"# a\xff\n")
        self.assertEqual(self._run("--emit-ir", str(path)).stdout, "")

    def test_emitted_ir_is_always_decodable(self) -> None:
        # I4.1. This is what the whole issue was about.
        for data in self.CASES.values():
            with self.subTest(data=data):
                path = self._write(data)
                out = subprocess.run(
                    [str(MDFIX), "--emit-ir", str(path)], capture_output=True
                ).stdout
                out.decode("utf-8")  # must not raise

    def test_valid_ir_still_decodes_for_good_input(self) -> None:
        path = self._write("# 漢字 Ελληνικά 🎉\n".encode("utf-8"))
        out = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)], capture_output=True, check=True
        ).stdout
        for line in out.decode("utf-8").splitlines():
            json.loads(line)


class NoWriteOnRejectionTests(EncodingTestCase):
    """A rejected file must not be partially written or backed up."""

    BAD = b"# a\xff\n"

    def test_explicit_output_is_not_created(self) -> None:
        src = self._write(self.BAD, "in.md")
        out = self.dir / "out.md"
        result = self._run("-q", str(src), str(out))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(out.exists())

    def test_in_place_leaves_the_file_alone(self) -> None:
        src = self._write(self.BAD, "in.md")
        result = self._run("-q", "-i", str(src))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(src.read_bytes(), self.BAD)
        self.assertFalse((self.dir / "in.md.bak").exists())

    def test_other_files_still_process(self) -> None:
        good1 = self._write(b"# Good\n", "a.md")
        bad = self._write(self.BAD, "b.md")
        good2 = self._write(b"# Also good\n", "c.md")
        result = self._run("--emit-ir", str(good1), str(bad), str(good2))
        self.assertEqual(result.returncode, 1)
        sources = [json.loads(line)["source"]
                   for line in result.stdout.splitlines()
                   if json.loads(line)["kind"] == "document"]
        self.assertEqual([Path(s).name for s in sources], ["a.md", "c.md"])


class BomTests(EncodingTestCase):
    """
    A BOM belongs to the file, not to the first heading.

    Pandoc strips it — `\\xEF\\xBB\\xBF# Title` is a Header with identifier
    `title` — while mdfix classified the line by its first byte and saw no
    heading at all, mis-parsing the whole document.
    """

    BOM = b"\xef\xbb\xbf"

    def test_bom_no_longer_hides_the_heading(self) -> None:
        path = self._write(self.BOM + b"# Title\n\nBody.\n")
        records = [json.loads(line) for line
                   in self._run("--emit-ir", str(path)).stdout.splitlines()]
        content = [r for r in records[1:] if r["kind"] != "gap"]
        self.assertEqual([r["kind"] for r in content], ["heading", "paragraph"])
        self.assertEqual(content[0]["text"], "Title")

    def test_spans_still_address_the_file_on_disk(self) -> None:
        # I1.3: skipping the BOM must not shift offsets off the real bytes.
        data = self.BOM + b"# Title\n\nBody.\n"
        path = self._write(data)
        for record in [json.loads(line) for line in
                       self._run("--emit-ir", str(path)).stdout.splitlines()][1:]:
            if record["kind"] == "gap":
                continue
            segment = data[record["start"]:record["end"]]
            self.assertFalse(segment.startswith(self.BOM))
        self.assertEqual(data[3:10], b"# Title")

    def test_bom_is_dropped_from_output(self) -> None:
        src = self._write(self.BOM + b"# Title\n", "in.md")
        out = self.dir / "out.md"
        self._run("-q", str(src), str(out))
        self.assertFalse(out.read_bytes().startswith(self.BOM))

    def test_a_bom_mid_file_is_not_stripped(self) -> None:
        # Only a leading BOM is a byte-order mark; elsewhere U+FEFF is a
        # zero-width no-break space and belongs to the text.
        data = b"# Title\n\n" + self.BOM + b"Body.\n"
        path = self._write(data)
        result = self._run("--emit-ir", str(path))
        self.assertEqual(result.returncode, 0)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        para = [r for r in records if r["kind"] == "paragraph"][0]
        self.assertEqual(data[para["start"]:para["end"]], self.BOM + b"Body.")


class ValidInputIsUntouchedTests(EncodingTestCase):
    """Validation must not change behaviour for well-formed input."""

    def test_four_byte_sequences_survive(self) -> None:
        text = "# 漢字 Ελληνικά 🎉 𝕏 ℝ\n\nEmoji 👩‍💻 and math ∀x∈ℝ.\n"
        src = self._write(text.encode("utf-8"), "in.md")
        out = self.dir / "out.md"
        self._run("-q", str(src), str(out))
        self.assertEqual(out.read_bytes(), text.encode("utf-8"))

    def test_repository_markdown_is_unchanged(self) -> None:
        # The strongest regression check available: real files, real profiles.
        for name in ("README.md", "docs/architecture.md", "docs/ir-schema.md"):
            path = ROOT / name
            if not path.is_file():
                continue
            with self.subTest(document=name):
                result = self._run("--emit-ir", str(path))
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertTrue(result.stdout)


if __name__ == "__main__":
    unittest.main()
