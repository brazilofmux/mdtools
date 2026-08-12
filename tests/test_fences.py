from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from prosevary.segment import LineKind, parse


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "fences"
MDFIX = ROOT / "mdfix" / "mdfix"


# FenceGrammarTests and the indent_columns cases that lived here tested
# prosevary's own fence grammar directly. That grammar is gone: block
# structure comes from `mdfix --emit-ir`, and the same rules are asserted
# against mdfix in MdfixTabFenceTests below and in tests/test_ir_schema.py.


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

    def test_tab_closed_fence_exposes_nothing(self) -> None:
        source = "```sh\ncode\n\t```\nAfter prose.\n"
        doc = parse(source)
        self.assertTrue(all(l.kind is LineKind.FENCE for l in doc.lines))
        self.assertEqual(
            [s.text for r in doc.regions for s in r.sentences], []
        )
        self.assertEqual(doc.reconstruct({}), source)


class MdfixTabFenceTests(unittest.TestCase):
    def test_mdfix_does_not_fix_prose_after_a_tab_delimiter(self) -> None:
        # mdfix reported an arrow rewrite on a line that is still code.
        source = "```sh\ncode\n\t```\nAfter A → B.\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.md"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [str(MDFIX), "-n", "-v", "--editorial", str(path)],
                capture_output=True, text=True,
            )
            self.assertNotIn("arrow aside", result.stdout + result.stderr)

    def test_mdfix_still_fixes_prose_after_a_real_closer(self) -> None:
        source = "```sh\ncode\n```\nAfter A → B.\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.md"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                # --editorial: the arrow-aside fix is the probe for "prose
                # reached the fixer here", and it became opt-in in #60.
                [str(MDFIX), "-n", "-v", "--editorial", str(path)],
                capture_output=True, text=True,
            )
            self.assertIn("arrow aside", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
