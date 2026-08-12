"""
Raw HTML blocks run to their own terminator (issue #27).

Pandoc keeps `<script>`, `<pre>`, `<style>`, `<textarea>`, comments,
processing instructions, declarations and CDATA as a RawBlock ending at a
kind-specific terminator — a blank line does not end them, and the contents
are passed through verbatim.

`<div>` and other block-level tags are deliberately *not* raw: Pandoc parses
those into a Div whose contents are markdown, so prose inside them stays
reachable. Both directions are asserted here.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from prosevary.segment import LineKind, parse


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
ARROW = "→"


def _sentences(source: str) -> list[str]:
    return [s.text for r in parse(source).regions for s in r.sentences]


def _kinds(source: str) -> list[str]:
    return [line.kind.name for line in parse(source).lines]


class ProsevaryRawHtmlTests(unittest.TestCase):
    def test_script_contents_never_reach_generation(self) -> None:
        source = f'<script>\n\nalert("A {ARROW} B");\n</script>\nAfter.\n'
        self.assertEqual(_sentences(source), ["After."])
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_comment_body_and_terminator_are_frozen(self) -> None:
        # The body *and* the `-->` used to become prose.
        source = f"<!--\n\nnote A {ARROW} B\n\n-->\nAfter.\n"
        self.assertEqual(_sentences(source), ["After."])
        self.assertNotIn("-->", "".join(_sentences(source)))

    def test_pre_style_and_textarea_are_raw(self) -> None:
        for tag in ("pre", "style", "textarea"):
            with self.subTest(tag=tag):
                source = f"<{tag}>\n\nA {ARROW} B\n</{tag}>\nAfter.\n"
                self.assertEqual(_sentences(source), ["After."])

    def test_raw_block_is_case_insensitive(self) -> None:
        source = f'<SCRIPT>\n\nalert("A {ARROW} B");\n</SCRIPT>\nAfter.\n'
        self.assertEqual(_sentences(source), ["After."])

    def test_fence_inside_a_script_does_not_open_a_fence(self) -> None:
        source = "<script>\n```\nnot a fence\n```\n</script>\nAfter.\n"
        self.assertEqual(_sentences(source), ["After."])
        self.assertNotIn(LineKind.FENCE, [l.kind for l in parse(source).lines])

    # --- one-liners must not swallow the document ---------------------------

    def test_single_line_comment_closes_immediately(self) -> None:
        source = "Before.\n\n<!-- note -->\n\nAfter.\n"
        self.assertEqual(_sentences(source), ["Before.", "After."])

    def test_single_line_script_closes_immediately(self) -> None:
        source = "Before.\n\n<script>x()</script>\n\nAfter.\n"
        self.assertEqual(_sentences(source), ["Before.", "After."])

    def test_doctype_closes_on_its_own_line(self) -> None:
        source = "<!DOCTYPE html>\n\nAfter.\n"
        self.assertEqual(_sentences(source), ["After."])

    # --- the other direction: Div contents are prose ------------------------

    def test_div_contents_stay_reachable(self) -> None:
        # Pandoc parses <div> into a Div holding markdown, so freezing its
        # prose would remove variation surface for no reason.
        source = f"<div>\n\ninner A {ARROW} B\n\n</div>\n\nAfter.\n"
        self.assertIn(f"inner A {ARROW} B", _sentences(source))

    def test_reconstruct_is_exact_for_every_shape(self) -> None:
        for source in (
            f'<script>\n\nalert("{ARROW}");\n</script>\nAfter.\n',
            f"<!--\n\nnote\n\n-->\nAfter.\n",
            "Before.\n\n<!-- note -->\n\nAfter.\n",
            f"<div>\n\ninner {ARROW}\n\n</div>\n\nAfter.\n",
        ):
            with self.subTest(source=source[:24]):
                self.assertEqual(parse(source).reconstruct({}), source)

    # --- delimiter rules the C side used to get wrong -----------------------

    def test_prefix_lookalikes_are_not_type1_raw(self) -> None:
        # <preview>/<scripture> must not open type-1 raw (which runs past
        # blanks). Generic HTML may freeze until a blank; after that blank,
        # prose is reachable again.
        for opener in ("<preview>", "<scripture>"):
            with self.subTest(opener=opener):
                source = f"{opener}\ninside\n\nAfter the blank.\n"
                self.assertIn("After the blank.", _sentences(source))

    def test_incomplete_end_tag_prefix_does_not_close_script(self) -> None:
        # JS string containing "</script" must not end the raw block early.
        source = (
            f'<script>\nvar s = "</script";\nalert("A {ARROW} B");\n'
            f"</script>\nAfter.\n"
        )
        self.assertEqual(_sentences(source), ["After."])
        self.assertEqual(parse(source).reconstruct({}), source)


class MdfixRawHtmlTests(unittest.TestCase):
    """mdfix was rewriting JavaScript and CSS as though it were prose."""

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

    def _report(self, source: str) -> str:
        path = self.dir / "in.md"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            # --editorial: the arrow-aside fix is the probe for "prose reached
            # the fixer here", and it became opt-in in #60.
            [str(MDFIX), "-n", "-v", "--editorial", str(path)],
            capture_output=True, text=True
        )
        return result.stdout + result.stderr

    def _canonical(self, source: str) -> str:
        src, out = self.dir / "c.md", self.dir / "c_out.md"
        if out.exists():
            out.unlink()
        src.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", "--canonical", str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def test_script_body_is_not_rewritten(self) -> None:
        source = f'<script>\n\nalert("A {ARROW} B");\n</script>\nAfter.\n'
        self.assertNotIn("arrow aside", self._report(source))
        self.assertEqual(self._canonical(source), source)

    def test_comment_body_is_not_rewritten(self) -> None:
        source = f"<!--\n\nnote A {ARROW} B\n\n-->\nAfter.\n"
        self.assertNotIn("arrow aside", self._report(source))

    def test_style_and_pre_are_not_rewritten(self) -> None:
        for tag in ("style", "pre"):
            with self.subTest(tag=tag):
                source = f"<{tag}>\n\nA {ARROW} B\n</{tag}>\nAfter.\n"
                self.assertNotIn("arrow aside", self._report(source))

    def test_prose_after_a_raw_block_is_still_fixed(self) -> None:
        source = f"<script>\nx()\n</script>\n\nAfter A {ARROW} B.\n"
        per_line = re.findall(r"line (\d+): arrow aside", self._report(source))
        self.assertEqual(per_line, ["5"])

    def test_one_line_raw_block_does_not_swallow_the_document(self) -> None:
        source = f"Before A {ARROW} B.\n\n<!-- note -->\n\nAfter A {ARROW} B.\n"
        per_line = re.findall(r"line (\d+): arrow aside", self._report(source))
        self.assertEqual(per_line, ["1", "5"])

    def test_div_prose_is_still_fixed(self) -> None:
        source = f"<div>\n\ninner A {ARROW} B\n\n</div>\n"
        self.assertIn("arrow aside", self._report(source))

    def test_prefix_lookalikes_do_not_open_raw_blocks(self) -> None:
        # Bare prefix matching treated <preview> as <pre> and froze the rest.
        source = f"<preview>\nprose A {ARROW} B\n\nAfter A {ARROW} B.\n"
        per_line = re.findall(r"line (\d+): arrow aside", self._report(source))
        self.assertEqual(per_line, ["2", "4"], msg=self._report(source))

    def test_incomplete_end_tag_does_not_close_script_early(self) -> None:
        # `"</script"` without '>' must leave later script lines protected.
        source = (
            f'<script>\nvar s = "</script";\nalert("A {ARROW} B");\n'
            f"</script>\n"
        )
        self.assertNotIn("arrow aside", self._report(source))
        self.assertEqual(self._canonical(source), source)

    def test_indented_code_immediately_after_raw_block_is_protected(self) -> None:
        # Raw HTML is a leaf block; indented code may follow with no blank.
        source = f"<script>\nx\n</script>\n    A {ARROW} B\n"
        self.assertNotIn("arrow aside", self._report(source))
        self.assertEqual(self._canonical(source), source)


if __name__ == "__main__":
    unittest.main()
