"""
Ordered-list marker forms (issue #90).

dialect-policy §3 pins `+fancy_lists`, `+startnum` and `+example_lists`, so
Pandoc reads `1)`, `a.`, `i.`, `@lab.` and `(@lab)` all as `OrderedList`.
mdfix used to recognize only `N. `, and a list read as a paragraph is a list
the prose passes rewrite.

What made the rest unsafe was never the spelling — it was the missing context.
Pandoc leaves `lists_without_preceding_blankline` off, so *nothing* opens a
list in the middle of a paragraph, and that is the whole of why a hard-wrapped
`C. They built a real toolchain.` stays prose. mdfix now carries the same rule,
and with it the forms Pandoc reads — including `#.`, `(1)` and `(a)`.
Lowercase `p.` followed by a digit is a page number, not a list.

Three sub-rules come with the fancy forms, and each one is Pandoc's, checked
against it rather than read off a spec:

* an uppercase marker ending in a period wants two columns after it, so
  `B. Russell wrote` is not a list and `B.  Russell wrote` is;
* that rule keys on the *value*, so `IV. ` is a list and `IVI. ` — which is
  5, spelled the long way — is not;
* roman numerals are Pandoc's loose kind: `iiii` and `ivi` parse, `did` and
  `ll` do not.
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

DECIMAL = ("1. x", "23. x", "1) x", "(1) x", "#. x", "#) x", "(#) x",
           "1.\tx")
ALPHA = ("a. x", "z) x", "A) x", "A.  x", "(a) x", "p. one", "p.  1", "p.\t1")
ROMAN = ("i. x", "iv) x", "IV. x", "mix. x", "I.  x", "(iv) x")
EXAMPLE = ("@lab. x", "@. x", "@lab) x", "(@lab) x", "(@) x")

# A marker and nothing else. Pandoc reads a one-item list; every form does it,
# and end of line satisfies the two-column rule that `A. x` fails.
EMPTY_ITEMS = ("1.", "1)", "(1)", "#.", "a.", "A.", "iv)", "@lab.", "(@lab)",
               "-", "*", "+", "- ")

# `-` under a paragraph is a setext underline, not an empty item, so it is not
# part of the mid-paragraph prose check below. Both tools already agree.
SETEXT_UNDERLINES = ("-", "- ")

FANCY = ALPHA + ROMAN + EXAMPLE
RECOGNIZED = DECIMAL + FANCY

# Marker-shaped but not markers, for the reason named in each case.
NOT_MARKERS = (
    "A. x",       # single uppercase + period wants two columns
    "I. x",       # I is 1, which a single letter spells
    "IVI. x",     # 5 the long way is still a one-letter value
    "did. x",     # d-i-d is not a roman numeral to Pandoc's parser
    "ll. x",      # nor is l-l
    "mixed. x",   # the numeral must run right up to the delimiter
    "é. x",       # alpha markers are ASCII
    "1.x",        # every form needs a separator
    "a.x",
    "p. 1",       # page number, not a list
)


class MarkerTestCase(unittest.TestCase):
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
        path = self.dir / "m.md"
        path.write_text(text, encoding="utf-8")
        result = subprocess.run([str(MDFIX), "--emit-ir", str(path)],
                                capture_output=True, text=True, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def _top_kinds(self, text: str) -> list:
        return [r["kind"] for r in self._records(text)
                if r["kind"] not in ("document", "gap") and not r.get("depth")]

    def _fix(self, text: str, *flags: str) -> str:
        src = self.dir / "in.md"
        out = self.dir / "out.md"
        src.write_text(text, encoding="utf-8")
        if out.exists():
            out.unlink()
        result = subprocess.run([str(MDFIX), "-q", *flags, str(src), str(out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def _blocks(self, text: str) -> list:
        result = subprocess.run([PANDOC, "-f", "markdown", "-t", "json"],
                                input=text, capture_output=True, text=True,
                                check=True)
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]


class ClassificationTests(MarkerTestCase):
    def test_every_recognized_form_is_a_list(self) -> None:
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                self.assertEqual(self._top_kinds(marker + "\n"), ["list"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_agrees_they_are_lists(self) -> None:
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks(marker + "\n"), ["OrderedList"])

    def test_the_near_misses_are_prose(self) -> None:
        for marker in NOT_MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(self._top_kinds(marker + "\n"), ["paragraph"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_agrees_the_near_misses_are_prose(self) -> None:
        for marker in NOT_MARKERS:
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks(marker + "\n"), ["Para"])

    def test_item_prose_is_reachable_in_every_form(self) -> None:
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                nested = [r for r in self._records(marker + "\n")
                          if r.get("depth")]
                self.assertEqual([r["kind"] for r in nested], ["paragraph"])

    def test_prose_under_an_empty_item_is_reachable(self) -> None:
        # list_content_column used to return -1 on `1.`, so emit_list_children
        # skipped the item and the wrapped line never became a nested paragraph.
        source = "1.\n   more\n"
        self.assertEqual(self._top_kinds(source), ["list"])
        nested = [r for r in self._records(source) if r.get("depth")]
        self.assertEqual([r["kind"] for r in nested], ["paragraph"])

    def test_a_tab_separates_as_two_columns(self) -> None:
        # Pandoc expands tabs before parsing, so `A.` then a tab reaches
        # column 4 — the two columns the initials rule wants.
        self.assertEqual(self._top_kinds("A.\tx\n"), ["list"])

    def test_an_example_label_is_unicode(self) -> None:
        # Pandoc's label alphabet is isAlphaNum, not ASCII. `mdfix_is_word`
        # answers this one, which is why the vendored table is linked in.
        for marker in ("@café. x", "@ЛАБ. x", "@a_b-2. x"):
            with self.subTest(marker=marker):
                self.assertEqual(self._top_kinds(marker + "\n"), ["list"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_agrees_about_unicode_labels(self) -> None:
        for marker in ("@café. x", "@ЛАБ. x", "@a_b-2. x"):
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks(marker + "\n"), ["OrderedList"])


class ContextTests(MarkerTestCase):
    """
    Pandoc pins `lists_without_preceding_blankline` off: no list interrupts a
    paragraph. That rule is why the fancy forms are safe to recognize at all.
    """

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_reads_none_of_them_mid_paragraph(self) -> None:
        for marker in RECOGNIZED + ("- x",):
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks("para text\n" + marker + "\n"),
                                 ["Para"])

    def test_mdfix_reads_the_fancy_forms_mid_paragraph_as_prose(self) -> None:
        for marker in FANCY:
            with self.subTest(marker=marker):
                self.assertEqual(self._top_kinds("para text\n" + marker + "\n"),
                                 ["paragraph"])

    def test_a_marker_opens_a_list_after_anything_but_a_paragraph(self) -> None:
        for before in ("", "# Heading\n", "1. item\n", "- item\n",
                       "para text\n\n", "```\ncode\n```\n",
                       "***\n", "---\n",
                       "| a | b |\n|---|---|\n| 1 | 2 |\n",
                       "---\ntitle: x\n---\n"):
            with self.subTest(before=before):
                kinds = self._top_kinds(before + "a. x\n")
                self.assertEqual(kinds[-1], "list")

    def test_an_empty_item_is_a_list_where_a_list_may_start(self) -> None:
        for marker in EMPTY_ITEMS:
            with self.subTest(marker=marker):
                self.assertEqual(self._top_kinds(marker + "\n"), ["list"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_agrees_an_empty_item_is_a_list(self) -> None:
        for marker in EMPTY_ITEMS:
            with self.subTest(marker=marker):
                self.assertEqual(len(self._blocks(marker + "\n")), 1)
                self.assertTrue(self._blocks(marker + "\n")[0].endswith("List"))

    def test_an_empty_item_mid_paragraph_is_prose(self) -> None:
        # The one part of the marker predicates that needs context, and the
        # reason it does is below: a wrapped year is not an empty list item.
        for marker in EMPTY_ITEMS:
            if marker in SETEXT_UNDERLINES:
                continue
            with self.subTest(marker=marker):
                self.assertEqual(
                    self._top_kinds("para text\n" + marker + "\n"),
                    ["paragraph"])

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_agrees_an_empty_item_mid_paragraph_is_prose(self) -> None:
        for marker in EMPTY_ITEMS:
            if marker in SETEXT_UNDERLINES:
                continue
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks("para text\n" + marker + "\n"),
                                 ["Para"])

    def test_a_lone_dash_under_a_paragraph_is_still_a_setext_heading(self) -> None:
        # The empty-item rule must not reach this: `-` there underlines the
        # line above. Both tools already read it that way, and the gate keeps
        # it that way.
        for marker in SETEXT_UNDERLINES:
            with self.subTest(marker=marker):
                source = "para text\n" + marker + "\n"
                self.assertEqual(self._top_kinds(source), ["heading"])
                if PANDOC:
                    self.assertEqual(self._blocks(source), ["Header"])

    def test_a_wrapped_year_is_not_an_empty_item(self) -> None:
        # From slow32-book, and the reason the empty-item rule is gated.
        # `--wrap` puts the year at the head of a line; reading `2003.` as a
        # marker there made R2 insert a blank and cut the sentence in two.
        source = ("Everything in this chapter is a consequence of what we\n"
                  "learned since 2003.\n")
        self.assertEqual(self._top_kinds(source), ["paragraph"])
        for profile in ("", "--canonical", "--technical", "--wrap=40"):
            with self.subTest(profile=profile):
                flags = (profile,) if profile else ()
                out = self._fix(source, *flags)
                self.assertNotIn("\n\n", out)

    def test_a_two_item_fancy_list_is_one_record(self) -> None:
        for source in ("a. first\nb. second\n",
                       "a. first\n\nb. second\n"):
            with self.subTest(source=source):
                self.assertEqual(self._top_kinds(source), ["list"])

    def test_a_wrapped_sibling_stays_one_list(self) -> None:
        source = ("a. First item whose text\n"
                  "   wraps onto a second line.\n"
                  "b. Second item.\n")
        self.assertEqual(self._top_kinds(source), ["list"])
        self.assertEqual(self._fix(source), source)

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_agrees_a_marker_line_ends_a_list_item(self) -> None:
        # Not a lazy continuation: `- item` then `a. x` is two lists, which is
        # why paragraph_is_open() is false after a marker line.
        self.assertEqual(self._blocks("- item\na. x\n"),
                         ["BulletList", "OrderedList"])
        self.assertEqual(self._top_kinds("- item\na. x\n"), ["list", "list"])

    def test_tab_and_empty_bullets_share_a_style(self) -> None:
        # list_style used to require a literal space, so `-` and `-\ty` were
        # style 0 and split off from `- x`.
        for source in ("- x\n-\n", "- x\n-\ty\n"):
            with self.subTest(source=source):
                self.assertEqual(self._top_kinds(source), ["list"])
                if PANDOC:
                    self.assertEqual(self._blocks(source), ["BulletList"])

    def test_a_tab_bullet_is_not_an_ordered_list(self) -> None:
        # Style 0 also disabled the style check, so `-\tx` then `1. y`
        # collapsed into one record.
        source = "-\tx\n1. y\n"
        self.assertEqual(self._top_kinds(source), ["list", "list"])
        if PANDOC:
            self.assertEqual(self._blocks(source),
                             ["BulletList", "OrderedList"])

    def test_wrapped_prose_is_why(self) -> None:
        # Verbatim from slow32-book, and the only marker-shaped line in 339
        # files across the corpora. `C. They built` is the third line of a
        # hard-wrapped sentence; reading it as a marker cuts the sentence in
        # half, and the blank-after-list repair then makes the cut permanent.
        source = ("An assembler, an archiver, a linker and a C compiler. "
                  "Written in Forth, on a bare\n"
                  "machine, hosted by an eight-hundred-line emulator. They "
                  "worked. They compiled real\n"
                  "C. They built a real toolchain.\n")
        self.assertEqual(self._top_kinds(source), ["paragraph"])
        self.assertEqual(self._fix(source), source)


class RepairTests(MarkerTestCase):
    def test_a_blank_is_inserted_before_a_list_after_prose(self) -> None:
        # R2, and the reason mdfix exists: Pandoc reads this as one paragraph,
        # the author meant a list, and nothing says so. See dialect-policy §7.
        for marker in ("- x", "* x", "1. x", "1) x"):
            with self.subTest(marker=marker):
                out = self._fix("para text\n" + marker + "\n")
                self.assertEqual(out, "para text\n\n" + marker + "\n")

    def test_no_blank_is_inserted_before_a_fancy_marker_after_prose(self) -> None:
        # Not a list there — to Pandoc or to mdfix — so there is nothing for
        # R2 to act on. This is the same rule as ContextTests, seen through
        # the repair: one predicate, not two.
        for marker in FANCY:
            with self.subTest(marker=marker):
                source = "para text\n" + marker + "\nmore\n"
                self.assertEqual(self._fix(source), source)

    def test_r2_still_stays_out_of_an_existing_list(self) -> None:
        source = ("1. First item whose text\n"
                  "   wraps onto a second line.\n"
                  "2. Second item.\n")
        self.assertEqual(self._fix(source), source)

    def test_a_prose_rule_may_not_collapse_a_marker_separator(self) -> None:
        # Found by the profile sweep below, and the reason apply_scanner()
        # compares the widest reading. `A.  x` is a list; the Chicago
        # sentence-space rule sees a sentence-ending period followed by two
        # spaces and collapses them, and `A. x` is a name being abbreviated.
        # Structure lost to a typography rule, silently, in the profile most
        # documents run.
        for profile in ("--canonical", "--technical"):
            for source in ("A.  First.\n", "I.  First.\n"):
                with self.subTest(profile=profile, source=source):
                    self.assertEqual(self._fix(source, profile), source)
                    self.assertEqual(self._top_kinds(source), ["list"])

    def test_a_fancy_item_keeps_its_content_column(self) -> None:
        # list_content_column() subtracts the one separator the marker length
        # carries and measures the rest, so a two-column marker still indents
        # its continuation correctly.
        source = "A.  First item whose text\n    wraps onto a second line.\n"
        self.assertEqual(self._fix(source), source)
        self.assertEqual(self._top_kinds(source), ["list"])

    def test_an_empty_item_resets_the_content_column(self) -> None:
        # `123456. foo` is content column 8; empty `2.` is column 3. Seven
        # spaces then is indented code (3+4), so --technical must leave
        # the arrow alone rather than rewrite it as prose. The width has
        # to live in the marker: extra spaces after `1.` collapse under
        # --technical before the column is measured.
        source = "123456. foo\n2.\n       code -> here\n"
        self.assertEqual(self._fix(source, "--technical"), source)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PreservationTests(MarkerTestCase):
    """Recognizing a form must not change what it means."""

    def test_a_bare_run_preserves_block_structure(self) -> None:
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                source = marker + "\n"
                self.assertEqual(self._blocks(self._fix(source)),
                                 self._blocks(source))

    def test_markers_survive_the_profiles(self) -> None:
        for marker in RECOGNIZED:
            for profile in ("--canonical", "--technical"):
                with self.subTest(marker=marker, profile=profile):
                    source = marker + "\n"
                    self.assertEqual(self._blocks(self._fix(source, profile)),
                                     self._blocks(source))

    def test_a_lazy_continuation_is_split_off_every_list(self) -> None:
        # Pinned as pre-existing and now decided rather than deferred: R3
        # separates a lazy continuation from its item, so `OrderedList`
        # becomes `OrderedList` + `Para`. That is a required repair making
        # structure explicit, which is what the required set is for
        # (dialect-policy §7). It predates the fancy forms — `- x` does it.
        for marker in ("- x", "1. x", "a. x", "@lab. x"):
            with self.subTest(marker=marker):
                source = marker + "\nsecond line\n"
                self.assertEqual(len(self._blocks(source)), 1)
                self.assertEqual(self._blocks(self._fix(source))[1:], ["Para"])

    def test_a_citation_opening_a_block_is_an_example_list_to_both(self) -> None:
        # `@smith2020. Claim.` at the start of a block is an example list to
        # Pandoc, whatever the author meant. mdfix now reads it the same way,
        # so this adds no divergence — but R3 then splits the next line off,
        # which settles the ambiguity in the direction the author may not have
        # meant. That is the case for a diagnostic; see issue #97.
        source = "@smith2020. Claim.\n"
        self.assertEqual(self._blocks(source), ["OrderedList"])
        self.assertEqual(self._top_kinds(source), ["list"])


if __name__ == "__main__":
    unittest.main()
