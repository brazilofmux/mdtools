"""Non-prose regions must never reach generation (issue #2)."""

from __future__ import annotations

import unittest

from prosevary.freeze import sentence_freeze
from prosevary.segment import LineKind, parse


def _sentences(source: str) -> list[str]:
    doc = parse(source)
    return [s.text for r in doc.regions for s in r.sentences]


def _kinds(source: str) -> list[str]:
    return [line.kind.name for line in parse(source).lines]


class ProtectedBlockTests(unittest.TestCase):
    def test_gfm_table_without_leading_pipe(self) -> None:
        source = (
            "a | b | c\n"
            "---|---|---\n"
            "x | y | z\n"
            "\n"
            "Prose after.\n"
        )
        doc = parse(source)
        self.assertEqual(_sentences(source), ["Prose after."])
        self.assertTrue(all(k == "TABLE" for k in _kinds(source)[:3]))
        self.assertEqual(doc.reconstruct({}), source)

    def test_gfm_table_with_leading_pipe(self) -> None:
        source = (
            "| a | b |\n"
            "| --- | --- |\n"
            "| x | y |\n"
            "\n"
            "Prose after.\n"
        )
        self.assertEqual(_sentences(source), ["Prose after."])
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_indented_code_not_exposed(self) -> None:
        source = (
            "Intro.\n"
            "\n"
            "    def foo():\n"
            "        return 1\n"
            "\n"
            "After.\n"
        )
        sents = _sentences(source)
        self.assertEqual(sents, ["Intro.", "After."])
        self.assertNotIn("def foo", " ".join(sents))
        self.assertIn(LineKind.INDENTED_CODE, [l.kind for l in parse(source).lines])
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_html_block_body_not_exposed(self) -> None:
        source = (
            "Before.\n"
            "\n"
            '<div class="x">\n'
            "  <p>raw markup stays</p>\n"
            "</div>\n"
            "\n"
            "After.\n"
        )
        sents = _sentences(source)
        self.assertEqual(sents, ["Before.", "After."])
        self.assertFalse(any("raw markup" in s for s in sents))
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_html_comment_not_exposed(self) -> None:
        source = "Before.\n\n<!-- editorial note -->\n\nAfter.\n"
        self.assertEqual(_sentences(source), ["Before.", "After."])
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_setext_heading_not_exposed(self) -> None:
        source = "Title here\n=========\n\nBody sentence.\n"
        self.assertEqual(_sentences(source), ["Body sentence."])
        kinds = _kinds(source)
        self.assertEqual(kinds[0], "HEADING")
        self.assertEqual(kinds[1], "HEADING")
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_setext_h2_not_thematic_break_only(self) -> None:
        # Underline must freeze the title line, not leave it as paraphraseable TEXT.
        source = "Subtitle\n---------\n\nBody.\n"
        self.assertEqual(_sentences(source), ["Body."])
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_reference_and_footnote_defs_not_exposed(self) -> None:
        source = (
            "See [x][ref] and note[^1].\n"
            "\n"
            "[ref]: https://example.com\n"
            "[^1]: footnote body\n"
        )
        sents = _sentences(source)
        self.assertEqual(len(sents), 1)
        self.assertIn("See", sents[0])
        self.assertFalse(any(s.startswith("[ref]:") for s in sents))
        self.assertFalse(any(s.startswith("[^1]:") for s in sents))
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_fenced_still_protected(self) -> None:
        source = "Prose.\n\n```sh\necho hi\n```\n\nMore.\n"
        self.assertEqual(_sentences(source), ["Prose.", "More."])
        self.assertEqual(parse(source).reconstruct({}), source)


class InlineFreezeTests(unittest.TestCase):
    def test_link_destination_must_survive(self) -> None:
        original = "See [docs](https://example.com/path) for detail."
        fs = sentence_freeze(original, set())
        self.assertTrue(any("https://example.com/path" in s for s in fs.spans))
        bad = "See [docs](https://evil.example/) for detail."
        self.assertIsNotNone(fs.check(original, bad))
        good = "See the [docs](https://example.com/path) for more detail."
        self.assertIsNone(fs.check(original, good))

    def test_image_destination_must_survive(self) -> None:
        original = "Logo: ![alt text](images/logo.png)."
        fs = sentence_freeze(original, set())
        bad = "Logo: ![alt text](images/other.png)."
        self.assertIsNotNone(fs.check(original, bad))

    def test_footnote_ref_and_citation_and_attr(self) -> None:
        original = "Claim[^1] per @smith2020 and a span{#id}."
        fs = sentence_freeze(original, set())
        for needle in ("[^1]", "@smith2020", "{#id}"):
            self.assertTrue(
                any(needle in s for s in fs.spans),
                msg=f"missing freeze for {needle}: {fs.spans}",
            )
        bad = "Claim[^2] per @other and a span{.x}."
        self.assertIsNotNone(fs.check(original, bad))


class AmbiguousDefaultsFrozenTests(unittest.TestCase):
    def test_pipe_heavy_separator_alone_is_table_not_prose(self) -> None:
        source = "| --- | --- |\n"
        self.assertEqual(_sentences(source), [])
        self.assertEqual(_kinds(source), ["TABLE"])


if __name__ == "__main__":
    unittest.main()
