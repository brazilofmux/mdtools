"""
Citations in the IR (issue #88), pinned against Pandoc.

`+citations` is in the profile dialect-policy §3 fixes, so what counts as a
citation is Pandoc's question and not ours. Every rule here was **measured**
against pandoc 3.10 rather than read off the manual, and the sweep at the
bottom re-measures on every run: a document goes through both, and the keys
have to match.

That matters more than usual here, because the syntax has corners that no
summary would have warned about:

    @a..b   -> key `a`        punctuation does not end a key
    @a's    -> key `a`        an apostrophe does
    @a-     -> key `a`        nor does a trailing hyphen
    a@b     -> nothing        a word character before the @ makes it an email
    @lab. x -> nothing        that is an example list, not a citation

The last one cost the most to find. `@lab.` at the start of a block is
Pandoc's `+example_lists` marker and produces an `OrderedList` with no
citation in it at all — so a scanner that did not know about example lists
would hand mdcheck an unresolved citation for a list marker.

**Under-reporting is the safe direction.** A citation mdfix misses is one
mdcheck cannot check; a citation mdfix invents is a false unresolved-reference
report, and one of those is how a gate loses its reader. The divergences pinned
below are all misses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")

# Documents whose citations mdfix reports differently from Pandoc, with the
# reason. Every one is a *miss*, and every one is the same cause: an inline
# construct the scanner treats as opaque, which is the recursive inline tree
# #88 also names. Pinned rather than hidden — closing one fails this file.
KNOWN_MISSES = {
    "[@a](url)\n": "a citation inside link text",
    "[@a; @b](u)\n": "citations inside link text",
}


class CitationTestCase(unittest.TestCase):
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

    def _records(self, text: str) -> list:
        path = self.dir / "c.md"
        path.write_text(text, encoding="utf-8")
        result = subprocess.run([str(MDFIX), "--emit-ir", str(path)],
                                capture_output=True, text=True, check=True)
        return [r for r in map(json.loads, result.stdout.splitlines())
                if r.get("kind") == "citation"]

    def _keys(self, text: str) -> list:
        return [r["key"] for r in self._records(text)]

    def _pandoc_keys(self, text: str) -> list:
        result = subprocess.run([PANDOC, "-f", "markdown", "-t", "json"],
                                input=text, capture_output=True, text=True,
                                check=True)
        found: list = []

        def walk(node) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                if node.get("t") == "Cite":
                    found.extend(c["citationId"] for c in node["c"][0])
                walk(node.get("c"))

        walk(json.loads(result.stdout)["blocks"])
        return found


class KeyShapeTests(CitationTestCase):
    def test_the_ordinary_forms(self) -> None:
        for text, expected in (("[@smith2020]\n", ["smith2020"]),
                               ("@smith2020\n", ["smith2020"]),
                               ("[-@smith]\n", ["smith"]),
                               ("[@a; @b]\n", ["a", "b"]),
                               ("[see @a, p. 3]\n", ["a"])):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._keys(text), expected)

    def test_a_key_may_hold_internal_punctuation(self) -> None:
        for key in ("a-b", "a_b", "a:b", "a.b", "a/b", "a#b", "2020", "_x"):
            with self.subTest(key=key):
                self.assertEqual(self._keys(f"See @{key} here.\n"), [key])

    def test_a_key_never_ends_on_punctuation(self) -> None:
        # `@a.` at the end of a sentence is the key `a` and a full stop.
        for text, expected in (("Text @a.\n", ["a"]),
                               ("Text @a-\n", ["a"]),
                               ("Text @a..b\n", ["a"]),
                               ("Text @a's work\n", ["a"])):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._keys(text), expected)

    def test_an_email_address_is_not_a_citation(self) -> None:
        # A word character before the `@` is what separates the two.
        for text in ("email@example.com\n", "a@b\n", "see x@y today\n"):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._keys(text), [])

    def test_a_bare_at_is_not_a_citation(self) -> None:
        for text in ("text @ alone\n", "@\n", "@ @\n", "@-\n"):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._keys(text), [])


class ExampleListTests(CitationTestCase):
    """
    `@label.` at the start of a block is a list marker, not a citation.

    Pandoc reads it as an `OrderedList` with no citation at all. Emitting one
    would hand mdcheck an unresolved citation for a list marker — work
    invented out of nothing.
    """

    def test_a_marker_is_not_a_citation(self) -> None:
        for text in ("@good. First example\n", "@. Unlabelled\n",
                     "  @indented. Still a marker\n"):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._keys(text), [])

    def test_the_same_key_mid_line_is_a_citation(self) -> None:
        # The distinction is position, not spelling.
        self.assertEqual(self._keys("As shown in @good. Next.\n"), ["good"])

    def test_a_marker_without_a_following_space_is_a_citation(self) -> None:
        # `@good.text` is not a marker; Pandoc reads the whole thing as a key.
        self.assertEqual(self._keys("@good.text\n"), ["good.text"])


class ModeTests(CitationTestCase):
    def test_each_mode_is_reported(self) -> None:
        for text, mode in (("[@a]\n", "normal"),
                           ("@a\n", "in-text"),
                           ("[-@a]\n", "suppress-author"),
                           ("[x -@a]\n", "suppress-author")):
            with self.subTest(text=text.strip()):
                self.assertEqual([r["mode"] for r in self._records(text)],
                                 [mode])

    def test_modes_mix_inside_one_bracket(self) -> None:
        records = self._records("[@a; -@b]\n")
        self.assertEqual([(r["key"], r["mode"]) for r in records],
                         [("a", "normal"), ("b", "suppress-author")])


class SpanTests(CitationTestCase):
    def test_the_record_span_covers_the_at_and_key(self) -> None:
        text = "See @smith2020 here.\n"
        data = text.encode("utf-8")
        record = self._records(text)[0]
        self.assertEqual(data[record["start"]:record["end"]], b"@smith2020")

    def test_the_key_span_covers_the_key_alone(self) -> None:
        # Same shape as the destination spans in #14: a consumer that wants to
        # *rewrite* a key needs its bytes, not the sigil's.
        text = "See [@smith2020] here.\n"
        data = text.encode("utf-8")
        record = self._records(text)[0]
        self.assertEqual(data[record["keyStart"]:record["keyEnd"]],
                         b"smith2020")

    def test_spans_survive_a_multibyte_prefix(self) -> None:
        text = "Ελληνικά κείμενο @smith2020 here.\n"
        data = text.encode("utf-8")
        record = self._records(text)[0]
        self.assertEqual(data[record["keyStart"]:record["keyEnd"]],
                         b"smith2020")


class ProtectedTests(CitationTestCase):
    def test_code_never_holds_a_citation(self) -> None:
        for text in ("`@a`\n", "```\n@a\n```\n", "    @a\n"):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._keys(text), [])

    def test_a_link_destination_is_not_a_citation(self) -> None:
        self.assertEqual(self._keys("[text](@notacite)\n"), [])

    def test_a_footnote_body_is_scanned(self) -> None:
        # It is prose, so its citations count. Left unscanned, mdcheck would
        # call a bibliography entry unused because the only thing citing it
        # was a footnote.
        self.assertEqual(self._keys("[^1]: note @a\n\nText[^1]\n"), ["a"])

    def test_a_definition_label_is_not_a_footnote_reference(self) -> None:
        # Scanning the body must not scan the label: `[^1]` on the definition
        # line names the note, and counting it emitted a phantom second
        # footnote_ref for every definition.
        path = self.dir / "f.md"
        path.write_text("[^1]: note @a\n\nText[^1]\n", encoding="utf-8")
        result = subprocess.run([str(MDFIX), "--emit-ir", str(path)],
                                capture_output=True, text=True, check=True)
        kinds = [r["kind"] for r in map(json.loads, result.stdout.splitlines())]
        self.assertEqual(kinds.count("footnote_ref"), 1)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PandocAgreementTests(CitationTestCase):
    """
    The sweep. Pandoc is the authority, so the test re-asks it every run.
    """

    CORPUS = (
        "[@a]\n", "@a\n", "See [@a; @b] here.\n", "*emph @a*\n",
        "**bold @a**\n", "# Heading @a\n", "> quote @a\n", "- item @a\n",
        "| c @a | d |\n|---|---|\n| x | y |\n",
        "[^1]: note @a\n\nText[^1]\n",
        "```\n@a\n```\n", "    @a\n", "[@a](url)\n", "[link](u) and @a\n",
        "text\n@a more\n", "@a\n\n@b\n", "[@a\n", "[@a; @b](u)\n",
        "<div>@a</div>\n", "@a[@b]\n", "[@a]{.x}\n",
        "@good. example\n", "email@x.com\n", "Text @a.\n", "@a's work\n",
        "[see @a, p. 3; also @b]\n", "Ελληνικά @a\n",
    )

    def test_mdfix_agrees_with_pandoc(self) -> None:
        for text in self.CORPUS:
            with self.subTest(text=text.strip()[:40]):
                expected = sorted(self._pandoc_keys(text))
                got = sorted(self._keys(text))
                if text in KNOWN_MISSES:
                    self.assertNotEqual(
                        got, expected,
                        f"{KNOWN_MISSES[text]} now works — remove the pin")
                    self.assertTrue(set(got) <= set(expected),
                                    "a pinned divergence must stay a *miss*")
                else:
                    self.assertEqual(got, expected)

    def test_every_pin_is_still_needed(self) -> None:
        # A pin for a document the corpus no longer contains is a pin nobody
        # is checking.
        for text in KNOWN_MISSES:
            self.assertIn(text, self.CORPUS)

    def test_mdfix_never_invents_a_citation(self) -> None:
        # The direction that matters. A miss costs a check; an invention
        # costs the reader's trust in every report.
        for text in self.CORPUS:
            with self.subTest(text=text.strip()[:40]):
                self.assertTrue(
                    set(self._keys(text)) <= set(self._pandoc_keys(text)),
                    "mdfix reported a citation Pandoc does not see")


if __name__ == "__main__":
    unittest.main()
