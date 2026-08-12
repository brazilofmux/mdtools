from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from prosevary.segment import _fence_opener, _is_fence_closer, parse


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


if __name__ == "__main__":
    unittest.main()
