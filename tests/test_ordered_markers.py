"""
Ordered-list marker forms (issue #90).

dialect-policy §3 pins `+fancy_lists`, `+startnum` and `+example_lists`, so
Pandoc reads `1)`, `a.`, `i.`, `@lab.` and `(@lab)` all as `OrderedList`.
mdfix recognized only `N. `, and a list read as a paragraph is a list the
prose passes rewrite.

**Classification and repair are separate questions, and only one of them is
answered here.** What a line *is* governs whether prose passes may touch it
and what the IR calls it. Whether a blank line should be inserted before it —
required repair R2 — is a different judgement, and #90 measured that it must
stay narrower: of 56 lines matching `a. ` after a prose line in the downstream
corpora, 52 are hard-wrapped sentences ("…eventually work" / "out. I want to
point at something else"). Recognizing those as markers fabricates lists.

So this closes the forms with **zero** measured collisions and leaves alpha
and roman to the context-sensitive work, where the fix is Pandoc's own rule:
a list cannot interrupt a paragraph, so a marker after prose is not a marker.
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

# Forms mdfix now recognizes, and what Pandoc calls each.
RECOGNIZED = ("1. x", "23. x", "1) x", "@lab. x", "@. x", "(@lab) x", "(@) x")

# Recognized by Pandoc, deliberately not by mdfix. See the module docstring.
DEFERRED = ("a. x", "A) x", "i. x", "iv) x")


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

    def test_item_prose_is_reachable_in_every_form(self) -> None:
        # The consequence that matters. Nested prose records are what let
        # mdterms and prosevary see inside an item; before this, `1.` had them
        # and `@lab.` did not — the same list, two answers, because the marker
        # rule was written out three times in three places.
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                nested = [r for r in self._records(marker + "\n")
                          if r.get("depth")]
                self.assertEqual([r["kind"] for r in nested], ["paragraph"])

    def test_a_marker_needs_a_following_space(self) -> None:
        for text in ("1.x\n", "@lab.x\n", "(@lab)x\n"):
            with self.subTest(text=text.strip()):
                self.assertEqual(self._top_kinds(text), ["paragraph"])


class DeferredFormTests(MarkerTestCase):
    """
    Alpha and roman markers, pinned as *not* recognized.

    This is a divergence from Pandoc, and it is deliberate — closing it fails
    this test, which is the point. It cannot be closed by widening a predicate;
    it needs the context rule, or hard-wrapped prose becomes lists.
    """

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_reads_them_as_lists(self) -> None:
        for marker in DEFERRED:
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks(marker + "\n"), ["OrderedList"])

    def test_mdfix_reads_them_as_prose(self) -> None:
        for marker in DEFERRED:
            with self.subTest(marker=marker):
                self.assertEqual(self._top_kinds(marker + "\n"), ["paragraph"])

    def test_wrapped_prose_is_why(self) -> None:
        # The shape that made this the safe choice, taken from slow32-book.
        # Recognizing `out. ` as a marker turns a sentence into a list, and
        # the blank-after-list repair then fires on the line after it.
        source = ("There's a version of this chapter where I look clever\n"
                  "out. I want to point at something else instead.\n")
        self.assertEqual(self._top_kinds(source), ["paragraph"])
        self.assertEqual(self._fix(source), source)


class RepairTests(MarkerTestCase):
    """R2 stays narrower than classification, on measured grounds."""

    def test_a_blank_is_inserted_before_the_decimal_forms(self) -> None:
        for marker in ("- x", "* x", "1. x", "1) x"):
            with self.subTest(marker=marker):
                out = self._fix("para text\n" + marker + "\n")
                self.assertEqual(out, "para text\n\n" + marker + "\n")

    def test_no_blank_is_inserted_before_the_deferred_forms(self) -> None:
        # Not because they are not lists — Pandoc says they are — but because
        # after a prose line they are overwhelmingly not.
        for marker in DEFERRED:
            with self.subTest(marker=marker):
                source = "para text\n" + marker + "\n"
                self.assertEqual(self._fix(source), source)

    def test_an_example_list_marker_after_prose_is_left_alone(self) -> None:
        # Zero occurrences in the corpora either way, so the conservative
        # answer costs nothing and cannot fabricate a list.
        source = "para text\n@lab. x\n"
        self.assertEqual(self._fix(source), source)

    def test_r2_still_stays_out_of_an_existing_list(self) -> None:
        source = ("1. First item whose text\n"
                  "   wraps onto a second line.\n"
                  "2. Second item.\n")
        self.assertEqual(self._fix(source), source)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PreservationTests(MarkerTestCase):
    """Recognizing a form must not change what it means."""

    def test_a_bare_run_preserves_block_structure(self) -> None:
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                source = marker + "\n"
                self.assertEqual(self._blocks(self._fix(source)),
                                 self._blocks(source))

    def test_a_lazy_continuation_is_split_off_every_list(self) -> None:
        # Pinned as a *pre-existing* divergence, found while writing the test
        # above and confirmed to predate this change — `- x` does it too.
        #
        # `@lab. x` then `second line` is one list item to Pandoc: the second
        # line is a lazy continuation. The blank-after-list repair separates
        # them, so `OrderedList` becomes `OrderedList` + `Para`.
        #
        # That is the same family as R2's divergence: a required repair
        # creating structure Pandoc did not read. Recorded on #90 rather than
        # fixed here, because deciding it means deciding what R3 is for.
        for marker in ("- x", "1. x", "@lab. x"):
            with self.subTest(marker=marker):
                source = marker + "\nsecond line\n"
                self.assertEqual(self._blocks(source), ["OrderedList"]
                                 if marker != "- x" else ["BulletList"])
                self.assertEqual(self._blocks(self._fix(source))[1:], ["Para"])

    def test_markers_survive_the_profiles(self) -> None:
        for marker in RECOGNIZED:
            for profile in ("--canonical", "--technical"):
                with self.subTest(marker=marker, profile=profile):
                    source = marker + "\n"
                    self.assertEqual(self._blocks(self._fix(source, profile)),
                                     self._blocks(source))


if __name__ == "__main__":
    unittest.main()
