"""--skip-section leaves a heading's whole section verbatim; --forbid rejects
a candidate that introduces a pattern the original lacks."""

from __future__ import annotations

import re
import unittest

from prosevary.freeze import check_forbidden
from prosevary.pipeline import active_gates
from prosevary.segment import parse

DOC = (
    "# Chapter\n\n"
    "Body one. Body two.\n\n"
    "## Sources\n\n"
    "Ledger `a1b2c3d`, entry one.\n\n"
    "### Intervals\n\n"
    "Counted on 2026-09-20, 3 days.\n\n"
    "## What transferred\n\n"
    "Body three.\n"
)


class SkipSectionTests(unittest.TestCase):
    def test_no_patterns_is_unchanged(self) -> None:
        doc = parse(DOC)
        self.assertEqual(len(doc.regions), 4)
        self.assertEqual(doc.skipped_regions, 0)

    def test_section_and_subsections_are_skipped(self) -> None:
        doc = parse(DOC, skip_sections=[re.compile(r"^Sources$")])
        texts = [r.text.strip() for r in doc.regions]
        self.assertEqual(texts, ["Body one. Body two.", "Body three."])
        self.assertEqual(doc.skipped_regions, 2)

    def test_next_same_level_heading_resumes(self) -> None:
        doc = parse(DOC, skip_sections=[re.compile(r"^Sources$")])
        self.assertIn("Body three.", [r.text.strip() for r in doc.regions])

    def test_subsection_pattern_skips_only_itself(self) -> None:
        doc = parse(DOC, skip_sections=[re.compile(r"Intervals")])
        texts = [r.text.strip() for r in doc.regions]
        self.assertEqual(
            texts,
            ["Body one. Body two.", "Ledger `a1b2c3d`, entry one.", "Body three."],
        )
        self.assertEqual(doc.skipped_regions, 1)

    def test_reconstruct_is_exact_with_skips(self) -> None:
        doc = parse(DOC, skip_sections=[re.compile(r"^Sources$")])
        self.assertEqual(doc.reconstruct({}), DOC)
        # A rewrite of a live region leaves the skipped bytes untouched.
        out = doc.reconstruct({(doc.regions[0].region_id, 0): "Body ONE."})
        self.assertIn("Body ONE. Body two.", out)
        self.assertIn("Ledger `a1b2c3d`, entry one.", out)
        self.assertIn("Counted on 2026-09-20, 3 days.", out)


class ForbidTests(unittest.TestCase):
    FIRST_PERSON = re.compile(r"\b(I|my|we|our)\b")

    def test_introducing_a_forbidden_token_rejects(self) -> None:
        reason = check_forbidden(
            "The translator records the exit.",
            "I record the exit in my translator.",
            [self.FIRST_PERSON],
        )
        self.assertIsNotNone(reason)
        self.assertIn("introduces 2", reason)

    def test_existing_occurrence_may_stay(self) -> None:
        original = 'The commit says "we keep parity" and stops.'
        self.assertIsNone(
            check_forbidden(
                original, 'The commit states "we keep parity" and ends.',
                [self.FIRST_PERSON],
            )
        )
        # But adding a second one is still rejected.
        self.assertIsNotNone(
            check_forbidden(
                original, 'We think the commit says "we keep parity".',
                [re.compile(r"\b(I|my|we|We|our)\b")],
            )
        )

    def test_no_patterns_never_rejects(self) -> None:
        self.assertIsNone(check_forbidden("a", "I am b", []))

    def test_gate_is_listed_only_when_configured(self) -> None:
        class E:
            semantic = False
            model_id = "hash"

        class J:
            enforcing = False
            model_id = "null"

        self.assertEqual(active_gates(E(), J()), ["freeze"])
        self.assertEqual(
            active_gates(E(), J(), [self.FIRST_PERSON]), ["freeze", "forbid"]
        )


if __name__ == "__main__":
    unittest.main()
