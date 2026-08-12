from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from prosevary.segment import (
    LineKind,
    _fence_opener,
    _is_fence_closer,
    indent_columns,
    parse,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "fences"
MDFIX = ROOT / "mdfix" / "mdfix"


class FenceGrammarTests(unittest.TestCase):
    def test_closer_must_match_marker_and_opening_length(self) -> None:
        fence = _fence_opener("````python")
        self.assertIsNotNone(fence)
        assert fence is not None

        self.assertFalse(_is_fence_closer("```", fence))
        self.assertFalse(_is_fence_closer("~~~~", fence))
        self.assertFalse(_is_fence_closer("```` trailing", fence))
        self.assertTrue(_is_fence_closer("````", fence))
        self.assertTrue(_is_fence_closer("  `````  \t", fence))
        self.assertFalse(_is_fence_closer("    ````", fence))

    def test_backtick_info_string_cannot_contain_backticks(self) -> None:
        self.assertIsNone(_fence_opener("```bad`info"))
        self.assertIsNotNone(_fence_opener("~~~bad`info"))

    def test_opener_accepts_list_item_indentation(self) -> None:
        # A fence inside an ordered list item sits at content column 4+.
        # Capping opener indent classified the block as prose and fed shell
        # code to the wrapper/paraphraser.
        fence = _fence_opener("    ```sh")
        self.assertIsNotNone(fence)
        assert fence is not None
        self.assertEqual(fence.indent, 4)

    def test_closer_indent_is_relative_to_its_opener(self) -> None:
        # Closers stay strict, but strict *relative to the opener* — else an
        # indented block could never be closed.
        indented = _fence_opener("    ```sh")
        assert indented is not None
        self.assertTrue(_is_fence_closer("    ```", indented))
        self.assertTrue(_is_fence_closer("       ```", indented))
        self.assertFalse(_is_fence_closer("        ```", indented))

        flush = _fence_opener("```sh")
        assert flush is not None
        self.assertFalse(_is_fence_closer("    ```", flush))


class SharedFenceFixtureTests(unittest.TestCase):
    def test_prosevary_exposes_only_prose_outside_fences(self) -> None:
        source = (FIXTURES / "input.md").read_text(encoding="utf-8")
        doc = parse(source)

        self.assertEqual(
            [sentence.text for region in doc.regions for sentence in region.sentences],
            ["After → prose.", "Tail → prose."],
        )
        self.assertEqual(doc.reconstruct({}), source)

    def test_mdfix_preserves_fenced_content_and_long_delimiters(self) -> None:
        source = (FIXTURES / "input.md").read_text(encoding="utf-8")
        expected = (FIXTURES / "mdfix-canonical.md").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.md"
            output_path = Path(tmp) / "output.md"
            input_path.write_text(source, encoding="utf-8")
            subprocess.run(
                [str(MDFIX), "--canonical", str(input_path), str(output_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(output_path.read_text(encoding="utf-8"), expected)

    def test_mdfix_flushes_wrapped_prose_before_fence(self) -> None:
        source = (
            "This paragraph is deliberately long enough to enter the wrapping buffer.\n"
            "````text\n"
            "code stays after prose\n"
            "````\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.md"
            output_path = Path(tmp) / "output.md"
            input_path.write_text(source, encoding="utf-8")
            subprocess.run(
                [str(MDFIX), "--wrap=40", str(input_path), str(output_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            output = output_path.read_text(encoding="utf-8")

        self.assertLess(output.index("This paragraph"), output.index("````text"))
        self.assertIn("````text\ncode stays after prose\n````\n", output)


class IndentedFenceTests(unittest.TestCase):
    LIST_FENCE = (
        "1. Item:\n"
        "\n"
        "    ```sh\n"
        '    git log --pretty=format:"%h %an" --since=2024-01-01 --author=x --all\n'
        "    ```\n"
        "\n"
        "2. Next.\n"
    )

    def test_mdfix_does_not_reflow_fence_inside_list_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.md"
            dst = Path(tmp) / "out.md"
            src.write_text(self.LIST_FENCE, encoding="utf-8")
            subprocess.run(
                [str(MDFIX), "-q", "--technical", str(src), str(dst)],
                check=True, capture_output=True, text=True,
            )
            # --technical enables --wrap=78; the long command must survive whole.
            self.assertEqual(dst.read_text(encoding="utf-8"), self.LIST_FENCE)

    def test_prosevary_does_not_expose_fence_inside_list_item(self) -> None:
        doc = parse(self.LIST_FENCE)
        sentences = [s.text for r in doc.regions for s in r.sentences]
        self.assertFalse([s for s in sentences if "git log" in s or "```" in s])
        self.assertEqual(doc.reconstruct({}), self.LIST_FENCE)


class UnterminatedFenceTests(unittest.TestCase):
    def test_canonical_lint_fails_on_mismatched_closer(self) -> None:
        # Opened with backticks, "closed" with tildes: the old toggle balanced
        # these, so the gate reported clean while skipping the rest of the file.
        source = (
            "```sh\n"
            "code here\n"
            "~~~\n"
            "\n"
            "More -- prose that canonical would fix.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.md"
            src.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [str(MDFIX), "--canonical-lint", str(src)],
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unterminated code fence", result.stderr)

    def test_unterminated_fence_counter_resets_between_files(self) -> None:
        # process_file must zero unterminated_fence_warnings per file. Without
        # that reset, multi-file --canonical-lint fails every subsequent file
        # after the first unmatched fence, even when those files are clean.
        bad = "```sh\ncode\n~~~\n"
        good = "Clean prose with no fences.\n"
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.md"
            good_path = Path(tmp) / "good.md"
            bad_path.write_text(bad, encoding="utf-8")
            good_path.write_text(good, encoding="utf-8")

            multi = subprocess.run(
                [str(MDFIX), "-q", "--canonical-lint", str(bad_path), str(good_path)],
                capture_output=True, text=True,
            )
            alone = subprocess.run(
                [str(MDFIX), "-q", "--canonical-lint", str(good_path)],
                capture_output=True, text=True,
            )

        # Multi-file: overall exit is nonzero because bad.md fails, but the
        # clean file must not inherit the stale warning count.
        self.assertNotEqual(multi.returncode, 0)
        self.assertEqual(alone.returncode, 0, msg=alone.stderr + alone.stdout)

        # Run good.md after bad.md in one process and assert good's summary
        # is clean (not "unterminated code fence  1" carried over).
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.md"
            good_path = Path(tmp) / "good.md"
            bad_path.write_text(bad, encoding="utf-8")
            good_path.write_text(good, encoding="utf-8")
            result = subprocess.run(
                [str(MDFIX), "--canonical-lint", str(bad_path), str(good_path)],
                capture_output=True, text=True,
            )
        # good.md is second; its summary line must report clean, not a leak.
        self.assertIn(f"{good_path}: clean. Nothing to fix.", result.stdout)
        # And the unterminated warning must only be attributed once (bad.md).
        self.assertEqual(result.stdout.count("unterminated code fence"), 1)


class IndentColumnTests(unittest.TestCase):
    """Indentation is columns, not characters (issue #29)."""

    def test_tab_advances_to_the_next_multiple_of_four(self) -> None:
        for text, columns, chars in [
            ("    x", 4, 4),
            ("\tx", 4, 1),
            (" \tx", 4, 2),
            ("   \tx", 4, 4),
            ("\t\tx", 8, 2),
            ("x", 0, 0),
            ("", 0, 0),
        ]:
            with self.subTest(text=text):
                self.assertEqual(indent_columns(text), (columns, chars))

    def test_tab_indented_delimiter_does_not_close_a_fence(self) -> None:
        # One tab is four columns, past the three-column allowance, so this
        # is fence content. Counting characters made it a closer and exposed
        # the rest of the block — including the real closer — as prose.
        fence = _fence_opener("```sh")
        assert fence is not None
        self.assertFalse(_is_fence_closer("\t```", fence))
        self.assertTrue(_is_fence_closer("   ```", fence))
        self.assertFalse(_is_fence_closer("    ```", fence))

    def test_tab_closed_fence_exposes_nothing(self) -> None:
        source = "```sh\ncode\n\t```\nAfter prose.\n"
        doc = parse(source)
        self.assertTrue(all(l.kind is LineKind.FENCE for l in doc.lines))
        self.assertEqual(
            [s.text for r in doc.regions for s in r.sentences], []
        )
        self.assertEqual(doc.reconstruct({}), source)

    def test_tab_indented_opener_allows_a_tab_indented_closer(self) -> None:
        # The allowance is relative: an opener at four columns tolerates a
        # closer at four through seven.
        fence = _fence_opener("\t```sh")
        assert fence is not None
        self.assertEqual(fence.indent, 4)
        self.assertTrue(_is_fence_closer("\t```", fence))
        self.assertTrue(_is_fence_closer("       ```", fence))
        self.assertFalse(_is_fence_closer("\t    ```", fence))


class MdfixTabFenceTests(unittest.TestCase):
    def test_mdfix_does_not_fix_prose_after_a_tab_delimiter(self) -> None:
        # mdfix reported an arrow rewrite on a line that is still code.
        source = "```sh\ncode\n\t```\nAfter A → B.\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.md"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [str(MDFIX), "-n", "-v", str(path)],
                capture_output=True, text=True,
            )
            self.assertNotIn("arrow aside", result.stdout + result.stderr)

    def test_mdfix_still_fixes_prose_after_a_real_closer(self) -> None:
        source = "```sh\ncode\n```\nAfter A → B.\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.md"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [str(MDFIX), "-n", "-v", str(path)],
                capture_output=True, text=True,
            )
            self.assertIn("arrow aside", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
