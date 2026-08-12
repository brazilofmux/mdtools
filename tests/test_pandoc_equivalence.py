"""
mdfix output must be reliably consumable by Pandoc (issues #10, #11).

Pandoc is the output target: whatever dialect came in, what mdfix writes has
to parse in Pandoc and mean the same thing. These tests assert that against
the real binary rather than against our own reading of the spec — which is
how a leak that let mdfix rewrite an indented code block was found.

Skipped when pandoc is absent, so the offline suite still runs everywhere.
A container works too: `podman run --rm -i pandoc/core`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")

# Blocks whose presence and count must survive a fix run. Inline punctuation
# is deliberately normalized, but the block skeleton is structure.
_BLOCK = re.compile(
    r"^\[?(Para|Plain|Header|CodeBlock|BulletList|OrderedList|Table"
    r"|BlockQuote|RawBlock|Div|HorizontalRule|DefinitionList)",
    re.MULTILINE,
)
_CODEBLOCK = re.compile(r'CodeBlock \([^)]*\) "((?:[^"\\]|\\.)*)"')

# What an AI actually emits: GFM tables, fenced code with a language, nested
# bullets, footnotes, inline code, links, an em-dash, indented code.
AI_DOCUMENT = """# Report

Here's a summary of the findings[^1] — the linker requires three passes.

## Results

| Stage | Time |
|-------|------|
| parse | 1.2s |
| emit  | 0.8s |

Key points:
* First item
* Second item with `inline_code` and a [link](https://example.com)
  * Nested item

```python
def main():
    return {"a": 1}
```

    indented code → here

[^1]: The footnote body.
"""


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PandocEquivalenceTests(unittest.TestCase):
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

    def _native(self, path: Path) -> str:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "native", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"pandoc failed to parse {path.name}:\n{result.stderr}",
        )
        return result.stdout

    def _fix(self, source: str, *flags: str) -> tuple[Path, Path]:
        src = self.dir / "in.md"
        out = self.dir / "out.md"
        if out.exists():
            out.unlink()
        src.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", *(flags or ("--canonical",)), str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return src, out

    def _assert_pandoc_safe(self, source: str, *flags: str) -> None:
        src, out = self._fix(source, *flags)
        before, after = self._native(src), self._native(out)

        self.assertEqual(
            _BLOCK.findall(before), _BLOCK.findall(after),
            "block structure changed",
        )
        # Code is never mdfix's to edit, in any dialect or profile.
        self.assertEqual(
            _CODEBLOCK.findall(before), _CODEBLOCK.findall(after),
            "a code block's contents changed",
        )

    def test_ai_document_survives_canonical(self) -> None:
        self._assert_pandoc_safe(AI_DOCUMENT)

    def test_ai_document_survives_technical(self) -> None:
        self._assert_pandoc_safe(AI_DOCUMENT, "--technical")

    def test_repo_markdown_survives_canonical(self) -> None:
        for path in sorted(ROOT.rglob("*.md")):
            if ".git" in path.parts:
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                self._assert_pandoc_safe(
                    path.read_text(encoding="utf-8"),
                    "--canonical", "--no-arrow-aside",
                )

    def test_list_context_does_not_leak_past_a_fence(self) -> None:
        # The bug this file found: a list set the indented-code threshold to
        # its content column, a fenced block did not clear it, and the next
        # four-column code block was measured against the stale threshold and
        # rewritten as prose.
        source = (
            "- item\n\n```python\nx = 1\n```\n\n    indented code → here\n"
        )
        self._assert_pandoc_safe(source)

    def test_fence_inside_a_list_item_keeps_the_list_context(self) -> None:
        source = (
            "- item\n\n      ```python\n      x = 1\n      ```\n"
            "\n      code → here\n"
        )
        self._assert_pandoc_safe(source)

    def test_output_is_idempotent_under_pandoc(self) -> None:
        _, once = self._fix(AI_DOCUMENT)
        first = once.read_text(encoding="utf-8")
        _, twice = self._fix(first)
        self.assertEqual(twice.read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
