"""Multiset freeze checks and matching-run inline code (issue #6)."""

from __future__ import annotations

import unittest

from prosevary.freeze import (
    count_term_occurrences,
    extract_inline_code_spans,
    sentence_freeze,
)


class MultisetSpanTests(unittest.TestCase):
    def test_two_identical_shout_terms_require_both(self) -> None:
        original = "LLVM and again LLVM in one sentence."
        fs = sentence_freeze(original, set())
        self.assertIn("LLVM", fs.terms)
        # Dropping one occurrence must fail.
        self.assertIsNotNone(
            fs.check(original, "LLVM and again something in one sentence.")
        )
        # Duplicating beyond the original count must fail.
        self.assertIsNotNone(
            fs.check(original, "LLVM and again LLVM plus LLVM in one sentence.")
        )
        # Preserving both is fine even if wording around them changes.
        self.assertIsNone(
            fs.check(original, "LLVM appears twice: LLVM in one sentence.")
        )

    def test_two_identical_code_spans_require_both(self) -> None:
        original = "Use `x` and also `x` again."
        fs = sentence_freeze(original, set())
        self.assertEqual(fs.spans.count("`x`"), 2)
        self.assertIsNotNone(fs.check(original, "Use `x` and also y again."))
        self.assertIsNotNone(fs.check(original, "Use `x` and also `x` plus `x`."))
        self.assertIsNone(fs.check(original, "Keep `x` twice: `x` still."))

    def test_two_html_openers_require_both(self) -> None:
        # set-not-multiset was the documented hole for <b>…</b> twice.
        original = "Use <b>a</b> and <b>c</b> here."
        fs = sentence_freeze(original, set())
        self.assertEqual(fs.spans.count("<b>"), 2)
        self.assertEqual(fs.spans.count("</b>"), 2)
        bad = "Use <b>a</b> and c here."
        self.assertIsNotNone(fs.check(original, bad))
        good = "Use <b>a</b> plus <b>c</b> here."
        self.assertIsNone(fs.check(original, good))


class InlineCodeRunTests(unittest.TestCase):
    def test_matching_run_lengths(self) -> None:
        text = "Code ``a ` b`` here."
        spans = extract_inline_code_spans(text)
        self.assertEqual(spans, ["``a ` b``"])
        fs = sentence_freeze(text, set())
        self.assertIn("``a ` b``", fs.spans)
        # Breaking the outer run fails.
        self.assertIsNotNone(fs.check(text, "Code `a ` b` here."))

    def test_single_tick_code(self) -> None:
        text = "Use `foo` please."
        self.assertEqual(extract_inline_code_spans(text), ["`foo`"])

    def test_unclosed_opener_not_frozen(self) -> None:
        text = "A lone ` tick is not code."
        self.assertEqual(extract_inline_code_spans(text), [])

    def test_two_separate_code_spans(self) -> None:
        text = "Both `a` and `b` matter."
        self.assertEqual(extract_inline_code_spans(text), ["`a`", "`b`"])


class GlossaryBoundaryTests(unittest.TestCase):
    def test_term_does_not_match_inside_longer_word(self) -> None:
        text = "The runtime system runs well."
        fs = sentence_freeze(text, {"run"})
        # "run" must not be frozen solely because of "runtime".
        self.assertNotIn("run", fs.terms)
        # "runs" is a different token.
        self.assertEqual(count_term_occurrences(text, "run"), 0)

    def test_whole_word_glossary_term_is_frozen(self) -> None:
        text = "We run the fixup pass next."
        fs = sentence_freeze(text, {"fixup", "run"})
        self.assertIn("fixup", fs.terms)
        self.assertIn("run", fs.terms)
        self.assertIsNotNone(fs.check(text, "We execute the adjustment pass next."))
        self.assertIsNone(fs.check(text, "We still run the fixup pass next."))

    def test_glossary_is_case_sensitive(self) -> None:
        text = "OpenCL is not opencl."
        fs = sentence_freeze(text, {"OpenCL"})
        self.assertIn("OpenCL", fs.terms)
        # Lowercase-only candidate loses the cased form.
        self.assertIsNotNone(fs.check(text, "opencl is not opencl."))

    def test_multi_word_glossary_term_is_substring(self) -> None:
        text = "The relocation table is large."
        fs = sentence_freeze(text, {"relocation table"})
        self.assertIn("relocation table", fs.terms)
        self.assertIsNotNone(fs.check(text, "The reloc map is large."))


class StructuralStillFrozenTests(unittest.TestCase):
    def test_link_destination_still_protected(self) -> None:
        original = "See [docs](https://example.com/path) for detail."
        fs = sentence_freeze(original, set())
        self.assertIsNotNone(
            fs.check(original, "See [docs](https://evil.example/) for detail.")
        )
        self.assertIsNone(
            fs.check(original, "See the [docs](https://example.com/path) for more.")
        )


if __name__ == "__main__":
    unittest.main()
