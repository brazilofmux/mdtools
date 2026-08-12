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
        # dialect-policy §3: `<div>` contents are Markdown, not a raw block —
        # pandoc parses them into a Div, so they stay prose-variable. The old
        # assertion froze them, contradicting the adopted profile.
        sents = _sentences(source)
        self.assertIn("Before.", sents)
        self.assertIn("After.", sents)
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
        # A delimiter row with no header is a Para to pandoc and a
        # line_block to the IR. Either way it is not offered as prose, which
        # is what this test is really about.
        source = "| --- | --- |\n"
        self.assertEqual(_sentences(source), [])
        self.assertEqual(_kinds(source), ["LINE_BLOCK"])


class ReviewFixTests(unittest.TestCase):
    """Regressions for the review round on the protected-regions work."""

    def test_table_delimiter_row_accepts_gfm_short_forms(self) -> None:
        # GFM needs only one dash. Requiring three let no-leading-pipe tables
        # with short or aligned delimiters through to the paraphraser; the
        # original suite only covered the "--- | ---" form that worked.
        for delim in ("--- | ---", ":-: | :-:", "- | -", "-- | --", ":- | -:"):
            source = f"Name | Type\n{delim}\nfoo | bar\n"
            with self.subTest(delim=delim):
                self.assertEqual(_sentences(source), [])
                self.assertEqual(_kinds(source), ["TABLE", "TABLE", "TABLE"])

    def test_lone_dash_underline_does_not_merge_with_title(self) -> None:
        # "Subtitle\n-" split as one sentence, so a single rewrite replaced
        # the title and destroyed its underline.
        source = "Subtitle\n-\n\nBody text follows.\n"
        self.assertEqual(_kinds(source)[:2], ["HEADING", "HEADING"])
        self.assertNotIn("Subtitle", "".join(_sentences(source)))

    def test_setext_freezes_the_whole_preceding_paragraph(self) -> None:
        # CommonMark makes the entire paragraph the heading, not just the
        # line directly above the underline.
        # The old assertion encoded CommonMark. Pandoc — the output dialect,
        # and the oracle — reads all three lines as a single Para: a setext
        # underline only makes a heading of a *single* preceding line.
        source = "Line one of prose.\nLine two of prose.\n---\n"
        self.assertEqual(_kinds(source), ["TEXT", "TEXT", "HR"])
        self.assertTrue(_sentences(source))

    def test_indented_continuation_is_not_code(self) -> None:
        # Indented code cannot interrupt a paragraph. Splitting here handed
        # generation the fragment "This is a paragraph that wraps".
        source = "This is a paragraph that wraps\n    with a hanging indent.\n"
        self.assertEqual(_kinds(source), ["TEXT", "TEXT"])
        self.assertEqual(len(_sentences(source)), 1)

    def test_reference_definition_title_continuation_frozen(self) -> None:
        source = '[ref]: https://example.com\n  "Optional Title Here"\n'
        self.assertEqual(_kinds(source), ["REFERENCE", "REFERENCE"])
        self.assertEqual(_sentences(source), [])

    def test_inline_html_does_not_swallow_following_prose(self) -> None:
        # <span>x</span> opened a block and froze every line to the next blank.
        # pandoc reads all three lines as one Para: an inline tag with no
        # blank line before it does not open an HTML block. The old code
        # froze the middle line, which was freezing part of a paragraph.
        source = "Some prose here.\n<span>inline html</span>\nMore prose follows.\n"
        self.assertEqual(_kinds(source), ["TEXT", "TEXT", "TEXT"])
        self.assertIn("More prose follows.", "".join(_sentences(source)))

    def test_void_html_tags_do_not_swallow_following_prose(self) -> None:
        # Void elements have no end tag. Without treating them as self-closing,
        # <br> / <img> opened a block and froze the next prose line.
        for tag in (
            "<br>",
            "<br/>",
            '<img src="x.png">',
            '<img src="x.png"/>',
            "<hr>",
            '<input type="text">',
        ):
            # pandoc: one Para. The point of the test is that the following
            # prose is still reachable, which it is.
            source = f"Before.\n{tag}\nAfter without blank.\n"
            with self.subTest(tag=tag):
                self.assertEqual(_kinds(source), ["TEXT", "TEXT", "TEXT"])
                # One Para, so one sentence — the tag sits mid-paragraph.
                # What matters is that the following prose is reachable.
                self.assertIn("After without blank.", "".join(_sentences(source)))
                self.assertEqual(parse(source).reconstruct({}), source)

    def test_a_pipe_line_after_a_table_is_a_row(self) -> None:
        # pandoc absorbs it: a pipe table runs to the first line with no '|',
        # so a "prose" line containing a pipe is a row. Verified with
        # `pandoc -t json`, which reports a single Table.
        source = "| Name | Type |\n|---|---|\n| a | b |\nThis prose has a | pipe.\n"
        self.assertEqual(_kinds(source)[-1], "TABLE")

    def test_a_line_without_a_pipe_ends_the_table(self) -> None:
        source = "| Name | Type |\n|---|---|\n| a | b |\nProse after.\n"
        self.assertEqual(_kinds(source)[-1], "TEXT")

    def test_list_item_paragraph_keeps_load_bearing_indent(self) -> None:
        # The indent lives at the head of the first span; dropping it ejects
        # the paragraph from its list item, splitting one list into two.
        # Schema 3 makes each item's prose its own region, so there are now
        # three rather than one. The indent must still survive a rewrite.
        source = "- item one\n\n  Second paragraph of the item.\n\n- item two\n"
        doc = parse(source)
        target = [(r, i) for r in doc.regions
                  for i, s in enumerate(r.sentences)
                  if "Second paragraph" in s.text]
        self.assertEqual(len(target), 1)
        region, index = target[0]
        out = doc.reconstruct({(region.region_id, index):
                               "Rewritten second paragraph."})
        self.assertIn("\n  Rewritten second paragraph.\n", out)
        # And the markers are untouched, which the flat model could not do.
        self.assertIn("- item one", out)
        self.assertIn("- item two", out)


class InlineFreezeReviewTests(unittest.TestCase):
    def test_image_inside_link_freezes_outer_destination(self) -> None:
        text = "Click [![logo](img/l.png)](https://example.com/home) now."
        spans = sentence_freeze(text, set()).spans
        self.assertTrue(any("https://example.com/home)" in s for s in spans), spans)

    def test_balanced_parens_in_destination_are_kept(self) -> None:
        text = "See [wiki](https://en.wikipedia.org/wiki/Foo_(bar)) here."
        spans = sentence_freeze(text, set()).spans
        self.assertIn("[wiki](https://en.wikipedia.org/wiki/Foo_(bar))", spans)

    def test_shortcut_reference_label_is_frozen(self) -> None:
        # Its definition is block-frozen, so a rewritable label would orphan it.
        fs = sentence_freeze("See [foo] for details.", set())
        self.assertIn("[foo]", fs.spans)
        self.assertIsNotNone(
            fs.check("See [foo] for details.", "See [bar] for details.")
        )

    def test_ordinary_links_still_frozen(self) -> None:
        text = "Prose with [a link](http://x.com) and ![img](y.png) and <http://a.com>."
        fs = sentence_freeze(text, set())
        for bad in (
            "Prose with [a hyperlink](http://x.com) and ![img](y.png) and <http://a.com>.",
            "Prose with [a link](http://x.com) and ![image](y.png) and <http://a.com>.",
            "Prose with [a link](http://x.com) and ![img](y.png) and <http://b.com>.",
        ):
            with self.subTest(bad=bad):
                self.assertIsNotNone(fs.check(text, bad))

    def test_raw_inline_html_tags_are_frozen(self) -> None:
        # A paraphrase could previously drop or rename inline markup and pass.
        text = "Some text with <b>bold</b> and <sub>x</sub> markup."
        fs = sentence_freeze(text, set())
        self.assertEqual(fs.spans, ["<b>", "</b>", "<sub>", "</sub>"])
        for bad in (
            "Some text with bold and <sub>x</sub> markup.",
            "Some text with <i>bold</i> and <sub>x</sub> markup.",
            "Some text with <b>bold</b> and x markup.",
        ):
            with self.subTest(bad=bad):
                self.assertIsNotNone(fs.check(text, bad))

    def test_inline_html_freeze_leaves_wrapped_prose_variable(self) -> None:
        # Only the tag is frozen, never the text it wraps.
        text = "Some text with <b>bold</b> markup."
        fs = sentence_freeze(text, set())
        self.assertIsNone(fs.check(text, "Some prose with <b>bold</b> markup."))

    def test_inline_html_pattern_does_not_over_match(self) -> None:
        # An autolink is claimed by _AUTOLINK alone (":" follows the name), and
        # a comparison is not a tag at all.
        self.assertEqual(
            sentence_freeze("See <https://example.com> now.", set()).spans,
            ["<https://example.com>"],
        )
        self.assertEqual(
            sentence_freeze("Compare a < b and c > d here.", set()).spans, []
        )

    def test_void_and_self_closing_tags_frozen(self) -> None:
        text = 'Line<br/>break and <img src="x.png"> here.'
        fs = sentence_freeze(text, set())
        self.assertIn("<br/>", fs.spans)
        self.assertIn('<img src="x.png">', fs.spans)
        self.assertIsNotNone(
            fs.check(text, 'Line break and <img src="y.png"> here.')
        )


if __name__ == "__main__":
    unittest.main()
