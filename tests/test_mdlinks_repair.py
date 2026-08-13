"""
Link repair (issue #14): suggest, never guess, never write.

The issue sets the rule this file exists to hold to:

    Repair uses structured edits and never renames or rewrites implicitly.
    Ambiguous targets require human choice.

So the tests come in pairs. For every repair that lands there is one asserting
that the same shape, made ambiguous, produces nothing — because a link fixer
that is usually right is worse than one that is sometimes silent. A wrong
repair is a plausible diff pointing at the wrong section, and nobody re-reads
a diff the tool was confident about.

Nothing here writes a file except through `mdfix --apply-edits`, which is the
point of emitting edits rather than rewriting: the tool that decides is never
the tool that writes, and the applier validates before it splices.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from mdlinks.graph import check, read
from mdlinks.repair import edits_for, suggest

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"


class RepairTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _suggest(self, *paths: Path):
        docs = [read(p, str(MDFIX)) for p in paths]
        return suggest(docs, check(docs))

    def _one(self, *paths: Path):
        confident = [s for s in self._suggest(*paths) if s.confident]
        self.assertEqual(len(confident), 1,
                         f"expected one repair, got "
                         f"{[s.replacement for s in confident]}")
        return confident[0]

    def _apply(self, target: Path, *paths: Path) -> str:
        """Run the real pipeline: mdlinks emits, mdfix applies."""
        edits = edits_for(self._suggest(*paths), target)
        if not edits:
            return target.read_text(encoding="utf-8")
        stream = [json.dumps({"kind": "edits", "schema": "mdtools-edits-1",
                              "source": str(target),
                              "bytes": target.stat().st_size})]
        stream += [json.dumps(e) for e in edits]
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", "-i", str(target)],
            input="\n".join(stream) + "\n",
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return target.read_text(encoding="utf-8")


class AnchorRepairTests(RepairTestCase):
    def test_heading_text_written_where_an_identifier_belongs(self) -> None:
        # The deterministic tier, and the most common hand-written mistake.
        page = self._write("a.md", "# Installation Guide\n\n"
                                   "See [x](#Installation-Guide).\n")
        self.assertEqual(self._one(page).replacement, "#installation-guide")

    def test_a_typo_resolves_to_the_nearest_anchor(self) -> None:
        page = self._write("a.md", "# Configuration Reference\n\n"
                                   "See [x](#configuration-refrence).\n")
        self.assertEqual(self._one(page).replacement,
                         "#configuration-reference")

    def test_an_anchor_in_another_file_is_repaired(self) -> None:
        a = self._write("a.md", "See [x](b.md#overvew).\n")
        b = self._write("b.md", "# B\n\n## Overview\n")
        self.assertEqual(self._one(a, b).replacement, "b.md#overview")

    def test_two_equally_close_anchors_are_not_repaired(self) -> None:
        # 'cat' is one edit from both. The tool has done the search and must
        # stop there.
        page = self._write("a.md", "# Bat\n\n# Hat\n\nSee [x](#cat).\n")
        suggestions = self._suggest(page)
        self.assertEqual([s.replacement for s in suggestions], [None])
        self.assertEqual(suggestions[0].candidates, ["bat", "hat"])

    def test_a_distant_anchor_is_not_offered_at_all(self) -> None:
        page = self._write("a.md", "# Completely Different\n\n"
                                   "See [x](#zzz).\n")
        self.assertEqual(self._suggest(page)[0].candidates, [])

    def test_an_anchor_in_an_unknown_file_is_left_alone(self) -> None:
        # mdlinks does not judge a file outside the run, so it must not
        # repair against one either.
        a = self._write("a.md", "See [x](b.md#anything).\n")
        self._write("b.md", "# B\n")
        self.assertEqual(self._suggest(a), [])


class MovedFileRepairTests(RepairTestCase):
    def test_a_moved_file_is_found_by_name(self) -> None:
        a = self._write("a.md", "See [x](old/notes.md).\n")
        notes = self._write("notes.md", "# Notes\n")
        self.assertEqual(self._one(a, notes).replacement, "notes.md")

    def test_the_anchor_is_carried_across(self) -> None:
        # Repairing the path must not silently drop the fragment; the anchor
        # is then checked on the next pass.
        a = self._write("a.md", "See [x](old/b.md#overview).\n")
        b = self._write("b.md", "# B\n\n## Overview\n")
        self.assertEqual(self._one(a, b).replacement, "b.md#overview")

    def test_a_subdirectory_target_gets_a_relative_path(self) -> None:
        a = self._write("a.md", "See [x](notes.md).\n")
        notes = self._write("docs/notes.md", "# Notes\n")
        self.assertEqual(self._one(a, notes).replacement, "docs/notes.md")

    def test_two_files_with_the_same_name_are_not_repaired(self) -> None:
        a = self._write("a.md", "See [x](old/page.md).\n")
        one = self._write("x/page.md", "# One\n")
        two = self._write("y/page.md", "# Two\n")
        suggestions = self._suggest(a, one, two)
        self.assertEqual([s.replacement for s in suggestions], [None])
        self.assertEqual(suggestions[0].candidates, ["x/page.md", "y/page.md"])


class ReferenceDefinitionTests(RepairTestCase):
    """The destination lives in the definition, so that is what gets edited."""

    def test_the_definition_is_edited_not_the_link(self) -> None:
        a = self._write("a.md", "See [x][spec].\n\n[spec]: old/b.md\n")
        b = self._write("b.md", "# B\n")
        text = self._apply(a, a, b)
        self.assertEqual(text, "See [x][spec].\n\n[spec]: b.md\n")

    def test_a_definition_used_twice_yields_one_edit(self) -> None:
        # Both links are broken, so there are two findings — but one
        # destination, so one edit. Before this was handled, the two identical
        # edits looked like an overlapping pair and the repair was dropped.
        a = self._write("a.md",
                        "See [x][spec] and [y][spec].\n\n[spec]: old/b.md\n")
        b = self._write("b.md", "# B\n")
        self.assertEqual(len(edits_for(self._suggest(a, b), a)), 1)
        self.assertEqual(self._apply(a, a, b),
                         "See [x][spec] and [y][spec].\n\n[spec]: b.md\n")


class AppliedOutputTests(RepairTestCase):
    def test_only_the_destination_changes(self) -> None:
        a = self._write("a.md",
                        "# Installation Guide\n\n"
                        'Text before [label](#instalation-guide "a title") '
                        "and after.\n")
        b = self._write("b.md", "# B\n")
        text = self._apply(a, a, b)
        self.assertEqual(
            text,
            "# Installation Guide\n\n"
            'Text before [label](#installation-guide "a title") '
            "and after.\n")

    def test_a_destination_in_angle_brackets_keeps_them(self) -> None:
        a = self._write("a.md", "# Overview\n\nSee [x](<#overvew>).\n")
        self.assertEqual(self._apply(a, a), "# Overview\n\nSee [x](<#overview>).\n")

    def test_a_link_in_a_table_cell_is_repaired(self) -> None:
        a = self._write("a.md", "# Overview\n\n"
                                "| a | b |\n|---|---|\n"
                                "| [x](#overvew) | 2 |\n")
        self.assertIn("[x](#overview)", self._apply(a, a))

    def test_repair_converges(self) -> None:
        # A moved file whose anchor is also wrong needs two passes: the anchor
        # cannot be checked until the file resolves. Worth pinning, because
        # "run it twice" is a real part of how this is used.
        a = self._write("a.md", "See [x](old/b.md#overvew).\n")
        b = self._write("b.md", "# B\n\n## Overview\n")
        for _ in range(4):
            self._apply(a, a, b)
            docs = [read(p, str(MDFIX)) for p in (a, b)]
            if not check(docs):
                break
        self.assertEqual(a.read_text(encoding="utf-8"),
                         "See [x](b.md#overview).\n")
        self.assertEqual(check([read(p, str(MDFIX)) for p in (a, b)]), [])

    def test_nothing_is_written_without_edits(self) -> None:
        a = self._write("a.md", "# A\n\nSee [x](#zzz).\n")
        before = a.read_bytes()
        self.assertEqual(edits_for(self._suggest(a), a), [])
        self.assertEqual(a.read_bytes(), before)


class RefusalTests(RepairTestCase):
    def test_an_undefined_reference_is_never_repaired(self) -> None:
        # Renaming the label would point the text at a different destination.
        # That is not a repair; it is a guess with a convincing diff.
        a = self._write("a.md", "See [x][spce].\n\n[spec]: b.md\n")
        self._write("b.md", "# B\n")
        self.assertEqual([s for s in self._suggest(a) if s.confident], [])

    def test_a_candidate_needing_escapes_is_refused(self) -> None:
        # The angle-bracket form is what makes this a real test: without it
        # the space ends the destination and no candidate is found at all, so
        # the guard would pass while never running. Here the candidate *is*
        # found and is still refused, because writing it back would need
        # brackets or escapes mdlinks cannot know how to spell.
        a = self._write("a.md", "See [x](<old/my notes.md>).\n")
        spaced = self._write("my notes.md", "# Notes\n")
        suggestions = self._suggest(a, spaced)
        self.assertEqual([s.candidates for s in suggestions],
                         [["my notes.md"]])
        self.assertTrue(all(not s.confident for s in suggestions))
        self.assertEqual(edits_for(suggestions, a), [])

    def test_external_links_are_untouched(self) -> None:
        a = self._write("a.md", "See [x](https://example.com/gone#frag).\n")
        self.assertEqual(self._suggest(a), [])


class EditShapeTests(RepairTestCase):
    def test_the_edit_carries_expect_and_a_rule(self) -> None:
        # `expect` is the applier's staleness guard; `rule` is what makes the
        # change legible in a diff review.
        a = self._write("a.md", "# Overview\n\nSee [x](#overvew).\n")
        edit = edits_for(self._suggest(a), a)[0]
        self.assertEqual(edit["expect"], "#overvew")
        self.assertEqual(edit["replacement"], "#overview")
        self.assertEqual(edit["rule"], "links.broken-anchor")

    def test_the_span_slices_exactly_the_destination(self) -> None:
        a = self._write("a.md", "# Overview\n\nSee [x](#overvew) here.\n")
        data = a.read_bytes()
        edit = edits_for(self._suggest(a), a)[0]
        self.assertEqual(data[edit["start"]:edit["end"]], b"#overvew")

    def test_a_stale_edit_is_refused_by_the_applier(self) -> None:
        # The half that makes emitting edits safe rather than merely tidy.
        a = self._write("a.md", "# Overview\n\nSee [x](#overvew).\n")
        edits = edits_for(self._suggest(a), a)
        size = a.stat().st_size
        a.write_text("# Overview\n\nSee [x](#something-else).\n",
                     encoding="utf-8")
        stream = [json.dumps({"kind": "edits", "schema": "mdtools-edits-1",
                              "source": str(a), "bytes": size})]
        stream += [json.dumps(e) for e in edits]
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", "-i", str(a)],
            input="\n".join(stream) + "\n", capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(a.read_text(encoding="utf-8"),
                         "# Overview\n\nSee [x](#something-else).\n")


if __name__ == "__main__":
    unittest.main()
