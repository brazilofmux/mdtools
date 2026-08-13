"""
Ordered-list marker forms (issue #90).

dialect-policy §3 pins `+fancy_lists`, `+startnum` and `+example_lists`, so
Pandoc reads `1)`, `a.`, `i.`, `@lab.` and `(@lab)` all as `OrderedList`.
mdfix recognized only `N. `, and a list read as a paragraph is a list the
prose passes rewrite.

This closes the decimal form with zero collisions: `1)`. Alpha, roman, and
example-list spellings stay unrecognized. The first two collide with
hard-wrapped prose; the last two are also mid-prose citations. Closing those
needs Pandoc's rule — a list cannot interrupt a paragraph — not a wider
predicate.
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
RECOGNIZED = ("1. x", "23. x", "1) x")

# Recognized by Pandoc, deliberately not by mdfix. See the module docstring.
DEFERRED = ("a. x", "A) x", "i. x", "iv) x")
EXAMPLE = ("@lab. x", "@. x", "(@lab) x", "(@) x")


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
        for marker in RECOGNIZED:
            with self.subTest(marker=marker):
                nested = [r for r in self._records(marker + "\n")
                          if r.get("depth")]
                self.assertEqual([r["kind"] for r in nested], ["paragraph"])

    def test_a_marker_needs_a_following_space(self) -> None:
        self.assertEqual(self._top_kinds("1.x\n"), ["paragraph"])
        self.assertEqual(self._top_kinds("1)x\n"), ["paragraph"])


class DeferredFormTests(MarkerTestCase):
    """
    Alpha, roman, and example-list markers, pinned as *not* recognized.

    This is a divergence from Pandoc, and it is deliberate — closing it fails
    this test, which is the point. It cannot be closed by widening a predicate;
    it needs the context rule, or hard-wrapped prose (and mid-prose citations)
    become lists.
    """

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_pandoc_reads_them_as_lists(self) -> None:
        for marker in DEFERRED + EXAMPLE:
            with self.subTest(marker=marker):
                self.assertEqual(self._blocks(marker + "\n"), ["OrderedList"])

    def test_mdfix_reads_them_as_prose(self) -> None:
        for marker in DEFERRED + EXAMPLE:
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
        # Not a list to mdfix, so R2 and R3 must not invent one. Three lines
        # so a false LT_ORDERED would fire blank-after-list on `more`.
        for marker in EXAMPLE:
            with self.subTest(marker=marker):
                source = "para text\n" + marker + "\nmore\n"
                self.assertEqual(self._fix(source), source)
                self.assertEqual(self._top_kinds(source), ["paragraph"])

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
        for marker in ("- x", "1. x", "1) x"):
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
