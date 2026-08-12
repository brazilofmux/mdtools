"""Closing quotes belong inside sentence spans (issue #5)."""

from __future__ import annotations

import unittest

from prosevary.segment import parse, split_sentences


def _texts(source: str) -> list[str]:
    return [t for _, _, t in split_sentences(source)]


def _rewrite_first(source: str, replacement: str) -> str:
    doc = parse(source)
    for reg in doc.regions:
        if reg.sentences:
            return doc.reconstruct({(reg.region_id, 0): replacement})
    raise AssertionError("no sentence to rewrite")


class ClosingQuoteSpanTests(unittest.TestCase):
    def test_ascii_double_quote_stays_in_sentence(self) -> None:
        source = 'He said "Hello." Next came noon.'
        self.assertEqual(
            _texts(source),
            ['He said "Hello."', "Next came noon."],
        )

    def test_ascii_single_quote_stays_in_sentence(self) -> None:
        source = "He said 'Hello.' Next came noon."
        self.assertEqual(
            _texts(source),
            ["He said 'Hello.'", "Next came noon."],
        )

    def test_curly_quotes_stay_in_sentence(self) -> None:
        source = "He said “Hello.” Next came noon."
        self.assertEqual(
            _texts(source),
            ["He said “Hello.”", "Next came noon."],
        )
        source = "He said ‘Hello.’ Next came noon."
        self.assertEqual(
            _texts(source),
            ["He said ‘Hello.’", "Next came noon."],
        )

    def test_question_and_exclamation_with_quote(self) -> None:
        self.assertEqual(
            _texts('She asked "Why?" Then left.'),
            ['She asked "Why?"', "Then left."],
        )
        self.assertEqual(
            _texts('She yelled "Stop!" Then ran.'),
            ['She yelled "Stop!"', "Then ran."],
        )

    def test_rewrite_does_not_duplicate_closing_quote(self) -> None:
        # The original bug: gap kept the orphan `"`, so a balanced candidate
        # reconstructed as `He replied "Hi."" Next came noon.`
        source = 'He said "Hello." Next came noon.\n'
        out = _rewrite_first(source, 'He replied "Hi."')
        self.assertEqual(out, 'He replied "Hi." Next came noon.\n')
        self.assertNotIn('.""', out)

    def test_rewrite_preserves_inter_sentence_whitespace(self) -> None:
        source = 'He said "Hi."  Double space next.\n'
        out = _rewrite_first(source, 'He replied "Hi."')
        self.assertEqual(out, 'He replied "Hi."  Double space next.\n')

    def test_identity_reconstruct(self) -> None:
        for source in (
            'He said "Hello." Next came noon.\n',
            "He said 'Hello.' Next came noon.\n",
            "He said “Hello.” Next came noon.\n",
            "Simple one. Simple two.\n",
            "Call f(x). Next step.\n",
            "See [1]. Next step.\n",
        ):
            with self.subTest(source=source):
                self.assertEqual(parse(source).reconstruct({}), source)


class TrailingCloserTests(unittest.TestCase):
    def test_period_inside_parens_does_not_steal_closer(self) -> None:
        # "Call f(x). Next." — terminator is outside the paren; both sentences
        # stay clean.
        self.assertEqual(_texts("Call f(x). Next."), ["Call f(x).", "Next."])

    def test_paren_closer_after_terminator_stays_with_sentence(self) -> None:
        # Rare but real in technical notes: a closer after the stop.
        source = "It was fine.) Next sentence."
        self.assertEqual(_texts(source), ["It was fine.)", "Next sentence."])
        out = _rewrite_first(source + "\n", "It still works.)")
        self.assertEqual(out, "It still works.) Next sentence.\n")

    def test_bracket_closer_after_period(self) -> None:
        source = "It was fine.] Next sentence."
        self.assertEqual(_texts(source), ["It was fine.]", "Next sentence."])


class WrappedAndAbbrevTests(unittest.TestCase):
    def test_wrapped_sentence_keeps_internal_newline(self) -> None:
        source = "A long sentence that\nwraps once. Next one."
        texts = _texts(source)
        self.assertEqual(len(texts), 2)
        self.assertIn("\n", texts[0])
        self.assertEqual(texts[1], "Next one.")

    def test_abbrev_false_split_still_documented(self) -> None:
        # Known v0 limitation — not fixed here, but must not regress into a
        # quote-corruption path if a quote follows an abbrev period.
        texts = _texts("See Dr. Smith today.")
        # Current regex splits on "Dr. S…"
        self.assertEqual(texts, ["See Dr.", "Smith today."])

    def test_quoted_abbrev_keeps_closing_quote(self) -> None:
        # Even when abbrev splits remain imperfect, a closing quote after a
        # real sentence end must not be orphaned into the gap.
        source = 'He said "Done." Next came noon.'
        self.assertEqual(
            _texts(source),
            ['He said "Done."', "Next came noon."],
        )
        out = _rewrite_first(source + "\n", 'She said "Done."')
        self.assertEqual(out, 'She said "Done." Next came noon.\n')


class MultiSentenceQuotationTests(unittest.TestCase):
    """
    Attributing the closer to the sentence fixes duplication but moves it into
    rewritable text. A candidate that drops it would silently unbalance the
    document — the worse failure, since duplication is visible in a diff.
    """

    SRC = 'He said, "This is one. This is two." Then he left.\n'

    def test_closer_is_inside_the_second_sentence(self) -> None:
        sents = [s.text for r in parse(self.SRC).regions for s in r.sentences]
        self.assertEqual(
            sents,
            ['He said, "This is one.', 'This is two."', "Then he left."],
        )

    def test_candidate_dropping_the_closer_does_not_unbalance(self) -> None:
        out = parse(self.SRC).reconstruct({(0, 1): "This is number two."})
        self.assertIn('This is number two."', out)
        self.assertEqual(out.count('"'), self.SRC.count('"'))

    def test_candidate_keeping_the_closer_is_not_doubled(self) -> None:
        out = parse(self.SRC).reconstruct({(0, 1): 'This is number two."'})
        self.assertNotIn('""', out)
        self.assertEqual(out.count('"'), self.SRC.count('"'))

    def test_wholly_different_candidate_still_balanced(self) -> None:
        out = parse(self.SRC).reconstruct({(0, 1): "Something else entirely"})
        self.assertEqual(out.count('"'), self.SRC.count('"'))

    def test_sentence_without_closer_is_untouched(self) -> None:
        src = "One sentence here. Two sentences here.\n"
        out = parse(src).reconstruct({(0, 0): "Rewritten one"})
        self.assertIn("Rewritten one Two sentences here.", out)


class EnumerationLabelTests(unittest.TestCase):
    def test_numbered_paren_label_does_not_split(self) -> None:
        # Admitting ")" to the separator class split "Step 1.)" off as a
        # standalone fragment and handed it to the paraphraser.
        self.assertEqual(
            [t for _, _, t in split_sentences("Step 1.) Do the thing. Step 2.) Do more.")],
            ["Step 1.) Do the thing.", "Step 2.) Do more."],
        )

    def test_letter_before_terminator_still_splits(self) -> None:
        # The case the separator class was widened for must keep working.
        self.assertEqual(
            [t for _, _, t in split_sentences("See the note.) Next came noon.")],
            ["See the note.)", "Next came noon."],
        )

    def test_parenthetical_ending_in_digit_stays_whole(self) -> None:
        # Conservative: under-splitting is safe, fragmenting is not.
        self.assertEqual(
            [t for _, _, t in split_sentences("(See note 1.) Next came noon.")],
            ["(See note 1.) Next came noon."],
        )


if __name__ == "__main__":
    unittest.main()
