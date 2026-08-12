"""
mdfix and prosevary must agree about verbatim regions.

The two tools protect different things on purpose — prosevary refuses to
paraphrase blockquotes, headings, list markers and tables, while mdfix still
normalizes punctuation inside them. That divergence is by design.

What is *not* negotiable is the verbatim set: fenced code, indented code, and
raw HTML blocks. Both tools must leave those byte-identical, and both must
leave ordinary prose reachable.

This harness exists because #27 shipped with three C-side holes that the
Python side did not have. The regexes were correct; hand-porting them to C
dropped a `\\b`, truncated `</name\\s*>` to a prefix, and reused LT_TEXT for
raw lines. Nothing compared the two implementations on the same input, so
review had to catch it. #28 adds another dual implementation — Pandoc table
grammar in both languages — which is the same trap.

Every content line carries an arrow, which mdfix converts to an em-dash when
it believes the line is prose. That makes "did mdfix consider this prose?"
directly observable.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from prosevary.segment import LineKind, _raw_html_terminator, parse


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
ARROW = "→"

# Kinds both tools must pass through untouched.
#
# TABLE is here for the Pandoc grid and simple forms, where column position
# carries the structure — a fix that shortens a cell moves every column after
# it. GFM pipe tables also land on LineKind.TABLE and mdfix does normalize
# punctuation inside their cells, so those are excluded from the corpus rather
# than from the contract.
_VERBATIM_KINDS = {LineKind.FENCE, LineKind.INDENTED_CODE, LineKind.TABLE}

CORPUS = {
    "fenced code": (
        f"Prose A {ARROW} B.\n\n```sh\nfenced A {ARROW} B\n```\n\n"
        f"Tail A {ARROW} B.\n"
    ),
    "tab-closed fence": (
        f"Prose A {ARROW} B.\n\n```sh\nfenced A {ARROW} B\n\t```\nstill A {ARROW} B\n```\n"
    ),
    "indented code": (
        f"Prose A {ARROW} B.\n\n    indented A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "tab-indented code": (
        f"Prose A {ARROW} B.\n\n\ttabbed A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "list-nested code": (
        f"- item A {ARROW} B\n\n      nested A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "script block": (
        f"Prose A {ARROW} B.\n\n<script>\n\nraw A {ARROW} B\n</script>\n\n"
        f"Tail A {ARROW} B.\n"
    ),
    "comment block": (
        f"Prose A {ARROW} B.\n\n<!--\n\nnote A {ARROW} B\n\n-->\n\nTail A {ARROW} B.\n"
    ),
    "style block": (
        f"Prose A {ARROW} B.\n\n<style>\n\n.a {{ content: 'A {ARROW} B' }}\n"
        f"</style>\n\nTail A {ARROW} B.\n"
    ),
    "prefix lookalike": (
        f"<scripture>\ninside A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "end-tag inside a string": (
        f'<script>\nvar s = "</script";\nraw A {ARROW} B\n</script>\n\n'
        f"Tail A {ARROW} B.\n"
    ),
    "code after a raw block": (
        f"<script>\nx()\n</script>\n\n    code A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    # No blank line after the closer. Raw lines that set prev_content_type to
    # LT_TEXT made this fail mdfix's "indented code cannot interrupt a
    # paragraph" guard, so the code block was rewritten as prose.
    "code immediately after a raw block": (
        f"<script>\nx()\n</script>\n    code A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "code immediately after a fence": (
        f"```sh\nx\n```\n    code A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "simple table": (
        f"Prose A {ARROW} B.\n\nRight     Left\n-------   -------\n"
        f"12        A {ARROW} B\n\nTail A {ARROW} B.\n"
    ),
    "grid table": (
        f"Prose A {ARROW} B.\n\n+------+------+\n| a    | b    |\n"
        f"+======+======+\n| A {ARROW} B | c |\n+------+------+\n\nTail A {ARROW} B.\n"
    ),
    "fence inside a list": (
        f"- item A {ARROW} B\n\n  ```sh\n  fenced A {ARROW} B\n  ```\n\n"
        f"Tail A {ARROW} B.\n"
    ),
}


def _verbatim_lines(source: str) -> set[str]:
    """
    Lines prosevary treats as verbatim: fenced code, indented code, and any
    line inside a raw HTML block.

    Raw-ness is recomputed here rather than read off LineKind, because
    LineKind.HTML covers both raw kinds and <div>, whose contents Pandoc
    parses as markdown and which mdfix should still fix.
    """
    doc = parse(source)
    out: set[str] = set()
    raw_end = None
    for line in doc.lines:
        stripped = line.text.rstrip("\r\n")
        if raw_end is not None:
            out.add(stripped)
            if raw_end.search(stripped):
                raw_end = None
            continue
        if line.kind is LineKind.HTML:
            terminator = _raw_html_terminator(stripped)
            if terminator is not None:
                out.add(stripped)
                if not terminator.search(stripped[stripped.index("<") + 1:]):
                    raw_end = terminator
            continue
        if line.kind in _VERBATIM_KINDS:
            out.add(stripped)
    return {s for s in out if s.strip()}


def _prose_lines(source: str) -> set[str]:
    """Lines prosevary treats as ordinary paragraph text."""
    doc = parse(source)
    return {
        line.text.rstrip("\r\n")
        for line in doc.lines
        if line.kind is LineKind.TEXT and line.text.strip()
    }


class ToolParityTests(unittest.TestCase):
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

    def _mdfix(self, source: str) -> str:
        src, out = self.dir / "p.md", self.dir / "p_out.md"
        if out.exists():
            out.unlink()
        src.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", "--canonical", str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def test_verbatim_regions_survive_mdfix(self) -> None:
        # If prosevary refuses to touch it as code, mdfix must not rewrite it.
        for name, source in CORPUS.items():
            with self.subTest(case=name):
                output = self._mdfix(source)
                out_lines = set(output.splitlines())
                for line in _verbatim_lines(source):
                    self.assertIn(
                        line, out_lines,
                        msg=(
                            f"{name}: prosevary treats this as verbatim but "
                            f"mdfix rewrote it: {line!r}"
                        ),
                    )

    def test_prose_stays_reachable_in_both(self) -> None:
        # The other direction: a line prosevary offers for paraphrase must not
        # be frozen by mdfix. This is what catches an over-eager raw opener —
        # <scripture> swallowing the rest of the file.
        for name, source in CORPUS.items():
            with self.subTest(case=name):
                output = self._mdfix(source)
                out_lines = set(output.splitlines())
                for line in _prose_lines(source):
                    if ARROW not in line:
                        continue
                    self.assertNotIn(
                        line, out_lines,
                        msg=(
                            f"{name}: prosevary offers this as prose but mdfix "
                            f"left it untouched: {line!r}"
                        ),
                    )

    def test_corpus_actually_exercises_both_sides(self) -> None:
        # A parity harness whose fixtures contain no verbatim regions, or no
        # prose, would pass while testing nothing.
        for name, source in CORPUS.items():
            with self.subTest(case=name):
                self.assertTrue(
                    _verbatim_lines(source) or "lookalike" in name,
                    msg=f"{name}: no verbatim lines to check",
                )
                self.assertTrue(
                    any(ARROW in l for l in _prose_lines(source)),
                    msg=f"{name}: no fixable prose to check",
                )


if __name__ == "__main__":
    unittest.main()
