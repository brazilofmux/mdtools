"""
Optional transforms may not break required guarantees (issue #10, I3.1).

architecture.md states three invariants that hold across every optional
transform, and until now they were a promise rather than a check:

    I2.1  the Pandoc AST is preserved — output means what the input meant
    I2.2  typography mdtools *emits* renders the same with and without `smart`
    I3.2  applying a transform twice equals applying it once

This is the sweep: every transform against every document, which is what turns
"we believe these compose" into something that fails when they stop.

Three of the known violations in dialect-policy §7 are invisible in ASCII, so
the corpus carries Greek, CJK, mathematics, hard breaks and typography.

Known violations are pinned exactly. A *new* violation fails, and so does a
*fixed* one — the second is the point, because it means closing a §7 gap
forces this file and the policy to be updated together.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")

# Inline nodes that carry structure rather than wording. A prose transform may
# freely change Str and Space — that is its job — but losing a LineBreak or a
# Link means the document no longer means what it meant.
STRUCTURAL_INLINE = frozenset({
    "LineBreak", "Link", "Image", "Code", "Math", "Note", "RawInline", "Cite",
})

# Every optional transform mdfix offers. `--technical` and `--canonical` are
# profiles, included because I3.1 covers "alone and in every shipped profile".
TRANSFORMS = (
    "-w",
    "--chicago-punct",
    "--chicago-punct-2",
    "--chicago-abbrev",
    "--canonical",
    "--footnote-canonical",
    "--heading-canonical",
    "--fence-canonical",
    "--pandoc-safe-links",
    "--scrivener-repair",
    "--spaced-emdash",
    "--wrap=78",
    "--technical",
)

CORPUS = {
    "ascii": "# Title\n\nA paragraph of words.\n\n- one\n- two\n\n```\ncode\n```\n",
    "greek": "# Θεολογία\n\nΚείμενο μὲ πολυτονικά καὶ λέξεις.\n",
    "cjk": "# 漢字\n\n日本語のテキストです。括弧（かっこ）。\n",
    "math": "Symbols ∀x ∈ ℝ, x² ≥ 0 and $a+b$ here.\n",
    "hardbreak": "line one  \nline two\n\npara\n",
    "typography": "A “quoted” thing — an ellipsis… here.\n",
    "ellipsis": "He paused . . . then spoke.\n",
    "links": "See [a link](http://x) and ![img](i.png).\n",
    "table": "| a | b |\n|---|---|\n| 1 | 2 |\n",
    "footnote": "Text[^1].\n\n[^1]: Note.\n",
    "nested": "- item\n\n  ```\n  code\n  ```\n\n- next\n",
    "longpara": "word " * 40 + "\n",
}

# (document, transform) -> which invariant it breaks, and why.
#
# Both are dialect-policy §7 gaps. The sweep found each to be broader than the
# policy recorded: hard breaks are destroyed by --wrap and --technical too, not
# only by -w and --canonical.
KNOWN_VIOLATIONS = {
    ("hardbreak", "-w"): "I2.1",
    ("hardbreak", "--canonical"): "I2.1",
    ("hardbreak", "--wrap=78"): "I2.1",
    ("hardbreak", "--technical"): "I2.1",
    ("ellipsis", "--chicago-punct"): "I2.2",
    ("ellipsis", "--chicago-punct-2"): "I2.2",
    ("ellipsis", "--canonical"): "I2.2",
    ("ellipsis", "--technical"): "I2.2",
}


class MatrixTestCase(unittest.TestCase):
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

    def _fix(self, text: str, transform: str) -> str:
        src = self.dir / "in.md"
        out = self.dir / "out.md"
        src.write_text(text, encoding="utf-8")
        if out.exists():
            out.unlink()
        result = subprocess.run(
            [str(MDFIX), "-q", transform, str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0,
                         msg=f"{transform}: {result.stderr}")
        return out.read_text(encoding="utf-8")


class IdempotenceTests(MatrixTestCase):
    """I3.2, and it needs no oracle — so it runs everywhere."""

    def test_every_transform_is_idempotent(self) -> None:
        for name, text in CORPUS.items():
            for transform in TRANSFORMS:
                with self.subTest(document=name, transform=transform):
                    once = self._fix(text, transform)
                    self.assertEqual(self._fix(once, transform), once)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class NonInterferenceTests(MatrixTestCase):
    """I3.1: the sweep that would have caught #49 and §7 gap 5."""

    def _structure(self, text: str) -> tuple[list[str], Counter]:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json"],
            input=text, capture_output=True, text=True, check=True,
        )
        document = json.loads(result.stdout)
        blocks: list[str] = []
        inline: Counter = Counter()

        def walk(node) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict) and "t" in node:
                if node["t"] in STRUCTURAL_INLINE:
                    inline[node["t"]] += 1
                walk(node.get("c"))

        for block in document["blocks"]:
            blocks.append(block["t"])
            walk(block.get("c"))
        return blocks, inline

    def _render(self, text: str, fmt: str) -> str:
        return subprocess.run(
            [PANDOC, "-f", fmt, "-t", "html"],
            input=text, capture_output=True, text=True, check=True,
        ).stdout

    def _violations(self) -> dict:
        found = {}
        for name, text in CORPUS.items():
            before = self._structure(text)
            for transform in TRANSFORMS:
                after_text = self._fix(text, transform)
                after = self._structure(after_text)
                if before[0] != after[0] or before[1] != after[1]:
                    found[(name, transform)] = "I2.1"
                    continue
                # I2.2 applies to typography mdtools *emits*. If the transform
                # left the file alone, any smart-dependence is the author's
                # and not ours — dialect-policy §4 as refined in architecture.
                if after_text != text:
                    if (self._render(after_text, "markdown")
                            != self._render(after_text, "markdown-smart")):
                        found[(name, transform)] = "I2.2"
        return found

    def test_violations_are_exactly_the_known_ones(self) -> None:
        found = self._violations()
        new = {k: v for k, v in found.items() if k not in KNOWN_VIOLATIONS}
        fixed = {k: v for k, v in KNOWN_VIOLATIONS.items() if k not in found}
        self.assertFalse(
            new,
            "new I3.1 violations — an optional transform broke a required "
            f"guarantee: {new}",
        )
        self.assertFalse(
            fixed,
            "these violations are gone, which is good news: remove them from "
            "KNOWN_VIOLATIONS and from dialect-policy §7 in the same change: "
            f"{fixed}",
        )

    def test_the_sweep_is_actually_running(self) -> None:
        # Without this, a corpus or transform list that silently emptied would
        # make the assertion above pass while checking nothing.
        self.assertGreaterEqual(len(CORPUS) * len(TRANSFORMS), 120)

    def test_most_combinations_are_clean(self) -> None:
        # The pins are meant to be a short list, not a way of life.
        total = len(CORPUS) * len(TRANSFORMS)
        self.assertLess(len(KNOWN_VIOLATIONS), total * 0.1)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class KnownViolationDetailTests(MatrixTestCase):
    """
    The two gaps, pinned individually so the failure explains itself.

    A matrix cell that goes red says "hardbreak/-w"; these say what broke.
    """

    def test_trailing_space_collapse_destroys_a_hard_break(self) -> None:
        # §7 gap 5. Two trailing spaces are a hard break under the pinned
        # profile; -w collapses any run to one and the LineBreak disappears.
        source = "line one  \nline two\n"
        self.assertIn("LineBreak", self._native(source))
        self.assertNotIn("LineBreak", self._native(self._fix(source, "-w")))

    def test_chicago_emits_ascii_ellipsis(self) -> None:
        # §7 gap 6. The mark mdtools emits must not be smart-dependent;
        # U+2026 is the target.
        out = self._fix("He paused . . . then spoke.\n", "--canonical")
        self.assertIn("...", out)
        self.assertNotIn("…", out)

    def test_an_already_ascii_ellipsis_is_left_alone(self) -> None:
        # The distinction I2.2 turns on: passing the author's shorthand
        # through is not the same as emitting it.
        source = "He paused... then spoke.\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def _native(self, text: str) -> str:
        return subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "native"],
            input=text, capture_output=True, text=True, check=True,
        ).stdout


if __name__ == "__main__":
    unittest.main()
