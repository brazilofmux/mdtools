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


if __name__ == "__main__":
    unittest.main()
