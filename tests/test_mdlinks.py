"""mdlinks checks the Markdown link graph from mdfix IR (issue #14)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mdlinks import __main__ as cli
from mdlinks.graph import check, read
from mdquery.ir import raw_records

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PKG = ROOT / "mdlinks"


class LinksTestCase(unittest.TestCase):
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

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _check(self, *paths: Path):
        return check([read(p) for p in paths])

    def _rules(self, *paths: Path):
        return sorted({f.rule for f in self._check(*paths)})


class BoundaryTests(unittest.TestCase):
    """dialect-policy §2, as for mdquery and mdterms."""

    FORBIDDEN = ("```", "~~~", "^#", "|---", "+---", "<!--", "$$",
                 "](", "[^")

    def test_no_markdown_grammar(self) -> None:
        found = []
        for path in sorted(PKG.glob("*.py")):
            code = "\n".join(
                line.split("#", 1)[0]
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.strip().startswith("#"))
            for marker in self.FORBIDDEN:
                if marker in code:
                    found.append(f"{path.name}: {marker!r}")
        self.assertFalse(found, f"mdlinks must consume the IR: {found}")

    def test_structure_comes_from_mdfix(self) -> None:
        self.assertIn("raw_records", (PKG / "graph.py").read_text(encoding="utf-8"))


class AnchorTests(LinksTestCase):
    def test_a_good_anchor_is_silent(self) -> None:
        path = self._write("a.md", "# Title Here\n\nSee [x](#title-here).\n")
        self.assertEqual(self._check(path), [])

    def test_a_broken_anchor_is_reported(self) -> None:
        path = self._write("a.md", "# Title\n\nSee [x](#nope).\n")
        self.assertEqual(self._rules(path), ["links.broken-anchor"])

    def test_anchors_match_pandoc(self) -> None:
        # The property that makes the checker worth trusting.
        path = self._write("a.md",
                           "# Punctuation: colons, commas!\n\n"
                           "See [x](#punctuation-colons-commas).\n")
        self.assertEqual(self._check(path), [])

    def test_duplicate_headings_get_pandoc_suffixes(self) -> None:
        path = self._write("a.md", "# Dup\n\n# Dup\n\nSee [x](#dup-1).\n")
        self.assertEqual(self._check(path), [])

    def test_percent_escapes_are_decoded(self) -> None:
        path = self._write("a.md", "# A B\n\nSee [x](#a%2Db).\n")
        # #a%2Db decodes to #a-b, which is the anchor for "A B".
        self.assertEqual(self._check(path), [])

    def test_a_link_in_a_heading_is_checked(self) -> None:
        path = self._write("a.md", "# See [x](#nope)\n")
        self.assertEqual(self._rules(path), ["links.broken-anchor"])

    def test_a_link_in_a_table_is_checked(self) -> None:
        # Inline records cover table cells; without that these were invisible.
        path = self._write("a.md",
                           "# T\n\n| a | [x](#nope) |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual(self._rules(path), ["links.broken-anchor"])

    def test_a_link_in_a_list_item_is_checked(self) -> None:
        path = self._write("a.md", "# T\n\n- see [x](#nope)\n")
        self.assertEqual(self._rules(path), ["links.broken-anchor"])


class ReferenceTests(LinksTestCase):
    def test_undefined_reference(self) -> None:
        path = self._write("a.md", "See [x][missing].\n")
        self.assertIn("links.undefined-reference", self._rules(path))

    def test_defined_reference_is_silent(self) -> None:
        path = self._write("a.md", "See [x][id].\n\n[id]: http://y\n")
        self.assertEqual([f.rule for f in self._check(path)], [])

    def test_shortcut_reference(self) -> None:
        path = self._write("a.md", "See [id] here.\n\n[id]: http://y\n")
        self.assertEqual(self._check(path), [])

    def test_undefined_shortcut_is_an_error(self) -> None:
        # Policy: bare [brackets] without a definition are broken refs,
        # not plain text — so accidental editorial markers stay visible.
        path = self._write("a.md", "See [sic] in the prose.\n")
        self.assertEqual(self._rules(path), ["links.undefined-reference"])

    def test_labels_are_case_insensitive(self) -> None:
        path = self._write("a.md", "See [x][ID].\n\n[id]: http://y\n")
        self.assertEqual(self._check(path), [])

    def test_labels_collapse_whitespace(self) -> None:
        path = self._write("a.md", "See [x][foo  bar].\n\n[foo bar]: http://y\n")
        self.assertEqual(self._check(path), [])

    def test_unused_definition_is_a_warning(self) -> None:
        path = self._write("a.md", "Text.\n\n[id]: http://y\n")
        findings = self._check(path)
        self.assertEqual([f.rule for f in findings], ["links.unused-definition"])
        self.assertEqual(findings[0].severity, "warning")

    def test_collapsed_reference_uses_link_text(self) -> None:
        path = self._write("a.md", "See [id][] here.\n\n[id]: http://y\n")
        self.assertEqual(self._check(path), [])

    def test_reference_destination_missing_file(self) -> None:
        path = self._write("a.md", "See [x][id].\n\n[id]: ./nope.md\n")
        self.assertEqual(self._rules(path), ["links.missing-file"])

    def test_reference_cross_file_anchor(self) -> None:
        b = self._write("b.md", "# Other\n")
        a = self._write("a.md", "See [x][id].\n\n[id]: ./b.md#nope\n")
        self.assertEqual(self._rules(a, b), ["links.broken-anchor"])

    def test_reference_cross_file_anchor_that_exists(self) -> None:
        b = self._write("b.md", "# Other\n")
        a = self._write("a.md", "See [x][id].\n\n[id]: ./b.md#other\n")
        self.assertEqual(self._check(a, b), [])

    def test_reference_def_carries_label_and_destination(self) -> None:
        path = self._write("a.md", "[id]: ./b.md#frag \"Title\"\n")
        defs = [r for r in raw_records([path]) if r["kind"] == "reference_def"]
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0]["label"], "id")
        self.assertEqual(defs[0]["destination"], "./b.md#frag")


class FootnoteTests(LinksTestCase):
    def test_undefined_footnote(self) -> None:
        path = self._write("a.md", "Text[^1].\n")
        self.assertIn("links.undefined-footnote", self._rules(path))

    def test_defined_footnote_is_silent(self) -> None:
        path = self._write("a.md", "Text[^1].\n\n[^1]: Note.\n")
        self.assertEqual(self._check(path), [])

    def test_unused_footnote_is_a_warning(self) -> None:
        path = self._write("a.md", "Text.\n\n[^1]: Note.\n")
        self.assertEqual(self._rules(path), ["links.unused-footnote"])


class FileTests(LinksTestCase):
    def test_missing_relative_file(self) -> None:
        path = self._write("a.md", "See [x](./nope.md).\n")
        self.assertEqual(self._rules(path), ["links.missing-file"])

    def test_existing_relative_file_is_silent(self) -> None:
        self._write("b.md", "# Other\n")
        path = self._write("a.md", "See [x](./b.md).\n")
        self.assertEqual(self._check(path), [])

    def test_title_on_destination_is_stripped(self) -> None:
        self._write("b.md", "# Other\n")
        path = self._write("a.md", 'See [x](./b.md "Title").\n')
        self.assertEqual(self._check(path), [])

    def test_angle_bracket_destination(self) -> None:
        self._write("b.md", "# Other\n")
        path = self._write("a.md", "See [x](<./b.md>).\n")
        self.assertEqual(self._check(path), [])

    def test_angle_bracket_reference_destination(self) -> None:
        self._write("b.md", "# Other\n")
        path = self._write("a.md", "See [x][id].\n\n[id]: <./b.md>\n")
        self.assertEqual(self._check(path), [])

    def test_cross_file_anchor(self) -> None:
        b = self._write("b.md", "# Other\n")
        a = self._write("a.md", "See [x](./b.md#nope).\n")
        self.assertEqual(self._rules(a, b), ["links.broken-anchor"])

    def test_cross_file_anchor_that_exists(self) -> None:
        b = self._write("b.md", "# Other\n")
        a = self._write("a.md", "See [x](./b.md#other).\n")
        self.assertEqual(self._check(a, b), [])

    def test_a_file_outside_the_run_is_not_judged(self) -> None:
        # Its anchors are unknown, so claiming the link is broken would be a
        # false positive — the expensive kind for a link checker.
        self._write("b.md", "# Other\n")
        a = self._write("a.md", "See [x](./b.md#unknown).\n")
        self.assertEqual(self._check(a), [])

    def test_external_urls_are_not_fetched(self) -> None:
        path = self._write("a.md",
                           "See [x](http://example.com/nope#frag) and "
                           "<http://example.com>.\n")
        self.assertEqual(self._check(path), [])


class CliTests(LinksTestCase):
    def _run(self, *argv: str):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_errors_exit_one(self) -> None:
        path = self._write("a.md", "# T\n\n[x](#nope)\n")
        self.assertEqual(self._run(str(path))[0], 1)

    def test_warnings_alone_exit_zero_by_default(self) -> None:
        path = self._write("a.md", "Text.\n\n[id]: http://y\n")
        self.assertEqual(self._run(str(path))[0], 0)

    def test_warnings_flag_makes_them_fail(self) -> None:
        path = self._write("a.md", "Text.\n\n[id]: http://y\n")
        self.assertEqual(self._run("--warnings", str(path))[0], 1)

    def test_diagnostics_are_jsonl(self) -> None:
        path = self._write("a.md", "# T\n\n[x](#nope)\n")
        rc, out, _ = self._run("--diagnostics", str(path))
        self.assertEqual(rc, 1)
        for line in out.splitlines():
            row = json.loads(line)
            self.assertEqual(row["kind"], "diagnostic")
            self.assertTrue(row["rule"].startswith("links."))

    def test_graph_output(self) -> None:
        path = self._write("a.md", "# T\n\nSee [x](#t).\n\nText[^1].\n\n[^1]: N.\n")
        rc, out, _ = self._run("--graph", str(path))
        self.assertEqual(rc, 0)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(rows[0]["anchors"], ["t"])
        self.assertEqual(rows[0]["footnotes"], ["1"])
        kinds = [r["kind"] for r in rows]
        self.assertIn("link", kinds)
        self.assertIn("footnote_ref", kinds)

    def test_repository_documentation_has_no_broken_links(self) -> None:
        docs = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
        rc, out, _ = self._run(*[str(p) for p in docs])
        self.assertEqual(rc, 0, msg=out)


if __name__ == "__main__":
    unittest.main()
