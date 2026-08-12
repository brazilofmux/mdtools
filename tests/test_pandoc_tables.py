"""
Pandoc grid and simple tables are protected regions (issue #28).

Unlike a GFM pipe table, where `|` delimits the cells, these forms carry
their structure in *column position*. Converting an arrow to an em-dash
shortens a cell and moves every column after it, so both tools must treat
these lines as verbatim rather than merely unwrappable.

The grammar was pinned against `pandoc -t json` rather than read off the
spec, because the conditions are not obvious:

    Right Left / --- ---- / 12 34   -> Table
    Right Left / --- ----           -> Para Para       (no body row)
    --- ----   / 12 34              -> HorizontalRule  (no header line)
    Title      / -------            -> Header          (setext; no spaces)

Multiline tables are deliberately out of scope here — they span blank lines
and need different machinery. See the module-level note in test_tool_parity.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from prosevary.segment import LineKind, parse


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")
ARROW = "→"

SIMPLE = f"Right     Left\n-------   -------\n12        A {ARROW} B\n123       123\n\nAfter.\n"
GRID = (
    "+---------+---------+\n"
    "| Header  | Second  |\n"
    "+=========+=========+\n"
    f"| A {ARROW} B   | cell    |\n"
    "+---------+---------+\n"
    "\nAfter.\n"
)


def _kinds(source: str) -> list[str]:
    return [line.kind.name for line in parse(source).lines]


def _sentences(source: str) -> list[str]:
    return [s.text for r in parse(source).regions for s in r.sentences]


class ProsevaryTableTests(unittest.TestCase):
    def test_simple_table_is_protected(self) -> None:
        self.assertEqual(_sentences(SIMPLE), ["After."])
        self.assertEqual(_kinds(SIMPLE)[:4], ["TABLE"] * 4)
        self.assertEqual(parse(SIMPLE).reconstruct({}), SIMPLE)

    def test_grid_table_is_protected(self) -> None:
        self.assertEqual(_sentences(GRID), ["After."])
        self.assertEqual(_kinds(GRID)[:5], ["TABLE"] * 5)
        self.assertEqual(parse(GRID).reconstruct({}), GRID)

    def test_grid_borders_are_not_exposed_as_prose(self) -> None:
        # `+---------+---------+` used to arrive as a sentence.
        self.assertFalse([s for s in _sentences(GRID) if "+" in s])

    # --- the negatives, each verified against pandoc -----------------------

    def test_setext_underline_is_still_a_heading(self) -> None:
        self.assertEqual(_kinds("Title\n-------\n\nBody.\n")[:2],
                         ["HEADING", "HEADING"])

    def test_dash_row_without_a_header_is_a_thematic_break(self) -> None:
        self.assertEqual(_kinds("---    ----\n12     34\n")[0], "HR")

    def test_dash_row_without_a_body_row_is_not_a_table(self) -> None:
        kinds = _kinds("Right  Left\n---    ----\n\nBody.\n")
        self.assertNotIn("TABLE", kinds)

    def test_thematic_break_is_untouched(self) -> None:
        self.assertEqual(_kinds("Para one.\n\n-----\n\nPara two.\n")[2], "HR")

    def test_tab_separated_dash_row_is_a_table(self) -> None:
        # Pandoc expands tabs before parsing, so `----\t----` is a table
        # exactly as `----    ----` is — verified with `pandoc -t json` on
        # tabs in the dash row, the header, and the body. mdfix accepted
        # them and this side did not.
        source = f"Right\tLeft\n----\t----\n12\tA {ARROW} B\n\nAfter.\n"
        self.assertEqual(_sentences(source), ["After."])
        self.assertEqual(_kinds(source)[:3], ["TABLE"] * 3)
        self.assertEqual(parse(source).reconstruct({}), source)

    def test_mixed_space_and_tab_separators(self) -> None:
        self.assertEqual(
            _kinds(f"Right\tLeft\n---- \t ----\n12\t34\n")[:3], ["TABLE"] * 3
        )

    def test_single_dash_group_is_not_a_dash_row(self) -> None:
        # `--- - ---` has a one-character group; not a table on either side.
        self.assertNotIn("TABLE", _kinds("Head  Second\n--- - ---\n12  34\n"))

    def test_pipe_tables_still_work(self) -> None:
        source = f"| a | b |\n|---|---|\n| A {ARROW} B | 2 |\n\nAfter.\n"
        self.assertEqual(_sentences(source), ["After."])


class MdfixTableTests(unittest.TestCase):
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

    def _fix(self, source: str, *flags: str) -> str:
        src, out = self.dir / "t.md", self.dir / "t_out.md"
        if out.exists():
            out.unlink()
        src.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", *(flags or ("--technical",)), str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")

    def test_simple_table_survives_technical_byte_for_byte(self) -> None:
        self.assertEqual(self._fix(SIMPLE), SIMPLE)

    def test_grid_table_survives_technical_byte_for_byte(self) -> None:
        self.assertEqual(self._fix(GRID), GRID)

    def test_cell_width_is_never_changed(self) -> None:
        # The arrow is what a prose pass would rewrite; doing so would shorten
        # the cell and move every column after it.
        for source in (SIMPLE, GRID):
            with self.subTest(source=source[:14]):
                self.assertIn(f"A {ARROW} B", self._fix(source))

    def test_prose_after_a_table_is_still_fixed(self) -> None:
        source = SIMPLE.replace("After.", f"After A {ARROW} B.")
        self.assertNotIn(f"After A {ARROW} B.", self._fix(source))

    def test_negatives_are_still_treated_as_prose(self) -> None:
        for name, source in (
            ("setext", f"Title A {ARROW} B\n-------\n\nBody.\n"),
            ("thematic", f"Para A {ARROW} B.\n\n-----\n\nPara two.\n"),
            ("no body row", f"Right A {ARROW} B\n---    ----\n\nBody.\n"),
            ("plain prose", f"Prose A {ARROW} B here.\n"),
        ):
            with self.subTest(case=name):
                self.assertNotIn(f"A {ARROW} B", self._fix(source))

    def test_idempotent(self) -> None:
        for source in (SIMPLE, GRID):
            with self.subTest(source=source[:14]):
                once = self._fix(source)
                self.assertEqual(self._fix(once), once)

    def test_list_context_survives_a_nested_table(self) -> None:
        # Nested table must not wipe list_content_col. Content column for
        # `- item` is 2, so a four-space line is list continuation (prose)
        # and a six-space line is indented code.
        nested = "\n".join(
            ("  " + ln if ln else ln) for ln in GRID.strip("\n").split("\n")
        )
        source = (
            f"- item\n\n{nested}\n\n"
            f"    cont A {ARROW} C\n\n"
            f"      code {ARROW} here\n"
        )
        out = self._fix(source, "--canonical")
        self.assertNotIn(f"cont A {ARROW} C", out)
        self.assertIn(f"code {ARROW} here", out)
        # Table cells stay protected.
        self.assertIn(f"A {ARROW} B", out)

    def test_margin_table_ends_list_context(self) -> None:
        # A table at the margin is left of the item's content column, so it
        # ends the list and a later four-space block is margin indented code.
        source = (
            f"- item\n\n{GRID}\n"
            f"    code A {ARROW} B\n"
        )
        out = self._fix(source, "--canonical")
        self.assertIn(f"code A {ARROW} B", out)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PandocTableOracleTests(unittest.TestCase):
    """The grammar came from pandoc; assert the output still parses as a Table."""

    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _blocks(self, path: Path) -> list[str]:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]

    def test_table_block_survives_a_fix_run(self) -> None:
        for name, source in (("simple", SIMPLE), ("grid", GRID)):
            with self.subTest(case=name):
                src, out = self.dir / "a.md", self.dir / "b.md"
                if out.exists():
                    out.unlink()
                src.write_text(source, encoding="utf-8")
                subprocess.run(
                    [str(MDFIX), "-q", "--technical", str(src), str(out)],
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(self._blocks(src), self._blocks(out))
                self.assertIn("Table", self._blocks(out))


if __name__ == "__main__":
    unittest.main()
