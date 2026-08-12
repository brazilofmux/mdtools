"""
mdquery — structural queries over Markdown (issue #15, part 2).

Two things are being tested. The obvious one is that the queries answer
correctly. The load-bearing one is the boundary from docs/dialect-policy.md §2:
mdquery must contain no Markdown grammar, because the moment a consumer
re-derives block structure we are back to the dual-implementation problem that
produced every structural bug in this repository.

Heading identifiers follow Pandoc's `auto_identifiers`, pinned by §3, so they
are checked against `pandoc -t json` rather than against my reading of the
manual.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import re
import tempfile
import unicodedata
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mdquery import __main__ as cli
from mdquery.ir import IRError, load
from mdquery.query import annotate, filter_blocks, outline, section_span
from mdquery.slug import assign_slugs, slugify

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
MDQUERY_PKG = ROOT / "mdquery"
PANDOC = shutil.which("pandoc")

SAMPLE = """\
# Top

Intro text.

## Install

Run it.

```bash
make
```

### Notes

| a | b |
|---|---|
| 1 | 2 |

## Usage

Use it.

# Second Top

Tail.
"""


class MdqueryTestCase(unittest.TestCase):
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

    def _doc(self, text: str = SAMPLE, name: str = "t.md"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return annotate(load([path])[0])

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class BoundaryTests(unittest.TestCase):
    """
    dialect-policy §2: Markdown grammar lives in exactly one implementation.

    mdquery is the first consumer, so it is where the rule either holds or
    quietly stops holding. These patterns are how grammar creeps in — a regex
    for fences here, a heading matcher there — and each would be a second
    implementation to keep in step with mdfix.rl.
    """

    # Markers that only appear in code that is parsing Markdown itself.
    FORBIDDEN = ("```", "~~~", "^#", r"\#", "|---", "+---", "<!--", "$$")

    # One documented exception, and only one: query.hidden_nested_blocks looks
    # for a fence marker to warn that schema 1 cannot report blocks nested in a
    # container. The real fix is nested IR records; until then, warning beats
    # letting `--kind code_fence` look exhaustive when it is not.
    #
    # The exemption is scoped to that function's body rather than to the file,
    # so a second use anywhere in query.py is still a failure. A file-wide
    # allow-list let exactly that through when it was tried.
    EXEMPT = {"query.py": "hidden_nested_blocks"}

    def _code(self, path: Path) -> str:
        """Executable text only: docstrings and comments name these legitimately."""
        text = path.read_text(encoding="utf-8")
        exempt = self.EXEMPT.get(path.name)
        if exempt:
            match = re.search(
                rf"\ndef {exempt}\(.*?(?=\ndef |\nclass |\Z)", text, re.S)
            self.assertIsNotNone(
                match, f"{path.name}: exempt function {exempt} is gone")
            text = text.replace(match.group(0), "\n")
        return "\n".join(
            line.split("#", 1)[0]
            for line in text.splitlines()
            if not line.strip().startswith("#")
        )

    def test_no_markdown_grammar_in_mdquery(self) -> None:
        found = []
        for path in sorted(MDQUERY_PKG.glob("*.py")):
            code = self._code(path)
            for marker in self.FORBIDDEN:
                if marker in code:
                    found.append(f"{path.name}: {marker!r}")
        self.assertFalse(
            found,
            "Markdown grammar found in mdquery — it must consume mdfix's IR "
            f"instead of re-deriving structure: {found}",
        )

    def test_the_scan_would_notice_a_leak(self) -> None:
        # The exemption is narrow enough that a fence marker outside
        # hidden_nested_blocks still trips the scan. Without this, the test
        # above could pass because the exemption swallowed the whole file.
        code = self._code(MDQUERY_PKG / "query.py")
        self.assertIn("def filter_blocks", code)
        self.assertNotIn("```", code)

    def test_structure_only_ever_arrives_from_mdfix(self) -> None:
        text = (MDQUERY_PKG / "ir.py").read_text(encoding="utf-8")
        self.assertIn("--emit-ir", text)
        # No other module may spawn a process: mdfix is the single source.
        for path in sorted(MDQUERY_PKG.glob("*.py")):
            if path.name in ("ir.py", "__init__.py"):
                continue
            with self.subTest(module=path.name):
                self.assertNotIn("subprocess", path.read_text(encoding="utf-8"))


class SlugTests(unittest.TestCase):
    def test_documented_examples(self) -> None:
        cases = {
            "Simple Heading": "simple-heading",
            "Punctuation: colons, commas!": "punctuation-colons-commas",
            "2. Numbers first": "numbers-first",
            "123": "section",
            "": "section",
            "Héading with accents": "héading-with-accents",
            "CamelCase": "camelcase",
            "under_score": "under_score",
            "dot.separated": "dot.separated",
            "Emoji 🎉 here": "emoji-here",
            "C#": "c",
            "With  Multiple   Spaces": "with-multiple-spaces",
        }
        for text, expected in cases.items():
            with self.subTest(heading=text):
                self.assertEqual(slugify(text), expected)

    def test_inline_markup_falls_out_of_the_filter(self) -> None:
        # Star emphasis and code spans agree with Pandoc without mdquery
        # knowing what they are: the markers are simply not slug characters.
        # Underscore emphasis does not — "_" is kept (see SlugOracleTests).
        self.assertEqual(slugify("*emphasis* inside"), "emphasis-inside")
        self.assertEqual(slugify("`code` inside"), "code-inside")
        self.assertEqual(slugify("**bold** head"), "bold-head")

    def test_ascii_shorthand_is_folded_first(self) -> None:
        # +smart is pinned by §3, so Pandoc folds these before slugging and
        # then drops them. §7 gap 6 means mdfix can still emit ASCII `...`.
        self.assertEqual(slugify("A--B"), "ab")
        self.assertEqual(slugify("A---B"), "ab")
        self.assertEqual(slugify("Ellipsis..."), "ellipsis")
        self.assertEqual(slugify("Ellipsis…"), "ellipsis")

    def test_duplicates_get_pandoc_suffixes(self) -> None:
        self.assertEqual(assign_slugs(["Dup", "Dup", "Dup"]),
                         ["dup", "dup-1", "dup-2"])

    def test_duplicate_numbering_is_document_order(self) -> None:
        self.assertEqual(assign_slugs(["A", "B", "A"]), ["a", "b", "a-1"])

    def test_decomposed_accents_follow_pandoc_and_lose_the_mark(self) -> None:
        # Pandoc does not normalize, so these two spellings of the same word
        # really do slug differently. Matching that matters more than being
        # principled: the anchor mdquery reports has to be the one Pandoc
        # emits. Confirmed with `pandoc -t json` on both spellings.
        precomposed = "H\u00e9ading"
        decomposed = unicodedata.normalize("NFD", precomposed)
        self.assertNotEqual(precomposed, decomposed)
        self.assertEqual(slugify(precomposed), "h\u00e9ading")
        self.assertEqual(slugify(decomposed), "heading")


@unittest.skipUnless(PANDOC, "pandoc not installed")
class SlugOracleTests(MdqueryTestCase):
    """Pandoc computes the real anchors; ours must be the same string."""

    HEADINGS = [
        "Simple Heading", "Punctuation: colons, commas!", "2. Numbers first",
        "123", "Héading with accents", "C#", "a/b", "*emphasis* inside",
        "`code` inside", "-- dashes --", "A--B", "a  b", "Dup", "Dup",
        "Em — dash", "Ellipsis...", "A---B", "x_y-z.w", "**bold** head",
        "semi;colon", "paren(s)", "plus+plus", "100% done",
    ]

    def _pandoc_ids(self, path: Path) -> list[str]:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return [b["c"][1][0]
                for b in json.loads(result.stdout)["blocks"]
                if b["t"] == "Header"]

    def test_slugs_match_pandoc(self) -> None:
        text = "".join(f"## {h}\n\n" for h in self.HEADINGS)
        path = self.dir / "s.md"
        path.write_text(text, encoding="utf-8")
        document = annotate(load([path])[0])
        self.assertEqual([b.slug for b in outline(document)],
                         self._pandoc_ids(path))

    def test_slugs_match_pandoc_on_repository_documentation(self) -> None:
        # A corpus nobody wrote for this test.
        for name in ("README.md", "docs/dialect-policy.md", "docs/ir-schema.md"):
            path = ROOT / name
            if not path.is_file():
                continue
            with self.subTest(document=name):
                document = annotate(load([path])[0])
                self.assertEqual([b.slug for b in outline(document)],
                                 self._pandoc_ids(path))

    def test_link_in_heading_is_the_known_divergence(self) -> None:
        # Pinned rather than hidden: fixing it needs inline structure in the
        # IR (#15 part 3), not a Markdown parser inside mdquery.
        path = self.dir / "d.md"
        path.write_text("## [link](http://x)\n", encoding="utf-8")
        document = annotate(load([path])[0])
        self.assertEqual([b.slug for b in outline(document)], ["linkhttpx"])
        self.assertEqual(self._pandoc_ids(path), ["link"])

    def test_underscore_emphasis_is_a_known_divergence(self) -> None:
        # Same class as links: raw IR text keeps "_", which is a slug character.
        # Pandoc strips emphasis markers before slugging. Do not "fix" by
        # parsing emphasis here.
        for text, ours, pandoc in (
            ("_emphasis_", "emphasis_", "emphasis"),
            ("__bold__", "bold__", "bold"),
        ):
            with self.subTest(heading=text):
                path = self.dir / "u.md"
                path.write_text(f"## {text}\n", encoding="utf-8")
                document = annotate(load([path])[0])
                self.assertEqual([b.slug for b in outline(document)], [ours])
                self.assertEqual(self._pandoc_ids(path), [pandoc])

class AncestryTests(MdqueryTestCase):
    def test_headings_carry_their_ancestors(self) -> None:
        document = self._doc()
        got = {b.slug: b.ancestors for b in outline(document)}
        self.assertEqual(got, {
            "top": [],
            "install": ["top"],
            "notes": ["top", "install"],
            "usage": ["top"],
            "second-top": [],
        })

    def test_blocks_inherit_the_enclosing_headings(self) -> None:
        document = self._doc()
        fence = [b for b in document.blocks if b.kind == "code_fence"][0]
        self.assertEqual(fence.ancestors, ["top", "install"])
        table = [b for b in document.blocks if b.kind == "table"][0]
        self.assertEqual(table.ancestors, ["top", "install", "notes"])

    def test_a_shallower_heading_pops_the_stack(self) -> None:
        document = self._doc()
        tail = document.blocks[-1]
        self.assertEqual(tail.kind, "paragraph")
        self.assertEqual(tail.ancestors, ["second-top"])

    def test_content_before_any_heading_has_no_ancestors(self) -> None:
        document = self._doc("Preamble.\n\n# Later\n")
        self.assertEqual(document.blocks[0].ancestors, [])

    def test_skipped_levels_do_not_invent_ancestors(self) -> None:
        document = self._doc("# One\n\n### Three\n")
        got = {b.slug: b.ancestors for b in outline(document)}
        self.assertEqual(got, {"one": [], "three": ["one"]})


class SectionTests(MdqueryTestCase):
    def test_section_runs_to_the_next_same_level_heading(self) -> None:
        document = self._doc()
        start, end = section_span(document, "install")
        text = (self.dir / "t.md").read_bytes()[start:end].decode()
        self.assertTrue(text.startswith("## Install"))
        self.assertIn("### Notes", text)      # a deeper heading stays inside
        self.assertNotIn("## Usage", text)    # a sibling ends it

    def test_deeper_section_stops_at_its_sibling(self) -> None:
        document = self._doc()
        start, end = section_span(document, "notes")
        text = (self.dir / "t.md").read_bytes()[start:end].decode()
        self.assertIn("| a | b |", text)
        self.assertNotIn("Use it.", text)

    def test_last_section_runs_to_end_of_file(self) -> None:
        document = self._doc()
        start, end = section_span(document, "second-top")
        self.assertEqual(end, document.byte_length)

    def test_unknown_id_is_none(self) -> None:
        self.assertIsNone(section_span(self._doc(), "nope"))

    def test_span_is_exact_against_the_file(self) -> None:
        document = self._doc()
        data = (self.dir / "t.md").read_bytes()
        start, end = section_span(document, "usage")
        self.assertEqual(data[start:end].decode(), "## Usage\n\nUse it.\n\n")


class FilterTests(MdqueryTestCase):
    def test_filter_by_kind(self) -> None:
        blocks = filter_blocks(self._doc().blocks, kinds=["heading"])
        self.assertEqual(len(blocks), 5)

    def test_filter_by_form(self) -> None:
        document = self._doc()
        self.assertEqual(len(filter_blocks(document.blocks, forms=["pipe"])), 1)
        self.assertEqual(len(filter_blocks(document.blocks, forms=["grid"])), 0)

    def test_filter_by_protection(self) -> None:
        document = self._doc()
        frozen = filter_blocks(document.blocks, protected=True)
        self.assertEqual([b.kind for b in frozen], ["code_fence"])

    def test_filter_under_includes_the_heading_itself(self) -> None:
        blocks = filter_blocks(self._doc().blocks, under="install")
        self.assertIn("install", [b.slug for b in blocks if b.slug])

    def test_filter_under_excludes_siblings(self) -> None:
        blocks = filter_blocks(self._doc().blocks, under="usage")
        for block in blocks:
            self.assertNotEqual(block.slug, "install")

    def test_filters_compose(self) -> None:
        blocks = filter_blocks(
            self._doc().blocks, kinds=["code_fence"], under="install")
        self.assertEqual(len(blocks), 1)


class CliTests(MdqueryTestCase):
    def _file(self, text: str = SAMPLE) -> Path:
        path = self.dir / "t.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_outline_human_output(self) -> None:
        rc, out, _ = self._run("outline", str(self._file()))
        self.assertEqual(rc, 0)
        self.assertIn("[install]", out)
        self.assertIn("## Install", out)

    def test_outline_json_is_jsonl(self) -> None:
        rc, out, _ = self._run("--json", "outline", str(self._file()))
        self.assertEqual(rc, 0)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["slug"] for r in rows],
                         ["top", "install", "notes", "usage", "second-top"])
        for row in rows:
            self.assertIn("start", row)
        # A top-level heading has no ancestors, so the key is absent rather
        # than an empty list; a nested one carries it.
        self.assertNotIn("ancestors", rows[0])
        self.assertEqual(rows[1]["ancestors"], ["top"])

    def test_outline_max_level(self) -> None:
        rc, out, _ = self._run("outline", "--max-level", "1", str(self._file()))
        self.assertEqual(rc, 0)
        self.assertNotIn("[install]", out)
        self.assertIn("[top]", out)

    def test_section_prints_source_text(self) -> None:
        rc, out, _ = self._run("section", str(self._file()), "--id", "usage")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "## Usage\n\nUse it.\n\n")

    def test_section_unknown_id_fails_with_the_list(self) -> None:
        rc, _, err = self._run("section", str(self._file()), "--id", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("available:", err)
        self.assertIn("install", err)

    def test_blocks_filters_from_the_cli(self) -> None:
        rc, out, _ = self._run(
            "--json", "blocks", str(self._file()), "--kind", "table")
        self.assertEqual(rc, 0)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["form"] for r in rows], ["pipe"])

    def test_stats_counts_by_kind(self) -> None:
        rc, out, _ = self._run("--json", "stats", str(self._file()))
        self.assertEqual(rc, 0)
        row = json.loads(out)
        self.assertEqual(row["kinds"]["heading"], 5)
        self.assertEqual(row["blocks"], len(self._doc().blocks))

    def test_missing_file_fails_cleanly(self) -> None:
        rc, _, err = self._run("outline", str(self.dir / "nope.md"))
        self.assertEqual(rc, 1)
        self.assertIn("not a file", err)

    def test_several_files_at_once(self) -> None:
        a = self.dir / "a.md"
        b = self.dir / "b.md"
        a.write_text("# A\n", encoding="utf-8")
        b.write_text("# B\n", encoding="utf-8")
        rc, out, _ = self._run("--json", "outline", str(a), str(b))
        self.assertEqual(rc, 0)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([Path(r["path"]).name for r in rows],
                         ["a.md", "b.md"])

    def test_reads_only(self) -> None:
        path = self._file()
        before, mtime = path.read_bytes(), path.stat().st_mtime_ns
        names = sorted(p.name for p in self.dir.iterdir())
        self._run("blocks", str(path))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), names)


class NestedContainerWarningTests(MdqueryTestCase):
    """
    Schema 1 is flat, so a fence inside a list item is part of the `list`
    record. Saying nothing would make `--kind code_fence` look exhaustive.
    """

    NESTED = "- item\n\n  ```\n  code\n  ```\n\n- next\n"

    def test_warning_names_the_container(self) -> None:
        path = self.dir / "n.md"
        path.write_text(self.NESTED, encoding="utf-8")
        rc, _, err = self._run("blocks", str(path), "--kind", "code_fence")
        self.assertEqual(rc, 0)
        self.assertIn("under-report", err)
        self.assertIn("n.md:1", err)

    def test_quiet_suppresses_it(self) -> None:
        path = self.dir / "n.md"
        path.write_text(self.NESTED, encoding="utf-8")
        rc, _, err = self._run("-q", "blocks", str(path))
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")

    def test_no_warning_when_nothing_is_hidden(self) -> None:
        rc, _, err = self._run("blocks", str(self._doc() and self.dir / "t.md"))
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")


class IRPlumbingTests(MdqueryTestCase):
    def test_schema_mismatch_is_refused(self) -> None:
        # Guessing at an unknown schema is how a consumer reports wrong spans
        # silently, so the header record exists to make it refusable.
        fake = self.dir / "fake-mdfix"
        fake.write_text(
            '#!/bin/sh\n'
            'echo \'{"kind":"document","schema":"mdtools-ir-99",'
            '"source":"x","bytes":0,"lines":0}\'\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        path = self.dir / "t.md"
        path.write_text("# A\n", encoding="utf-8")
        with self.assertRaises(IRError) as caught:
            load([path], mdfix=str(fake))
        self.assertIn("mdtools-ir-99", str(caught.exception))

    def test_mdfix_failure_is_reported_not_swallowed(self) -> None:
        fake = self.dir / "failing-mdfix"
        fake.write_text('#!/bin/sh\necho "boom" >&2\nexit 3\n', encoding="utf-8")
        fake.chmod(0o755)
        path = self.dir / "t.md"
        path.write_text("# A\n", encoding="utf-8")
        with self.assertRaises(IRError) as caught:
            load([path], mdfix=str(fake))
        self.assertIn("boom", str(caught.exception))

    def test_malformed_ir_is_reported(self) -> None:
        fake = self.dir / "garbage-mdfix"
        fake.write_text('#!/bin/sh\necho "not json"\n', encoding="utf-8")
        fake.chmod(0o755)
        path = self.dir / "t.md"
        path.write_text("# A\n", encoding="utf-8")
        with self.assertRaises(IRError):
            load([path], mdfix=str(fake))

    def test_mdfix_override_is_honoured(self) -> None:
        path = self.dir / "t.md"
        path.write_text("# A\n", encoding="utf-8")
        with unittest_env(MDFIX=str(MDFIX)):
            document = load([path])[0]
        self.assertEqual(document.schema, "mdtools-ir-1")

    def test_bad_mdfix_override_is_rejected(self) -> None:
        with unittest_env(MDFIX=str(self.dir / "nothing")):
            with self.assertRaises(IRError):
                load([self.dir / "t.md"])


class unittest_env:
    """Minimal os.environ patch, so the tests keep to the standard library."""

    def __init__(self, **values: str) -> None:
        self.values = values
        self.saved: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *exc: object) -> None:
        for key, old in self.saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


class LauncherTests(unittest.TestCase):
    def test_launcher_runs(self) -> None:
        launcher = ROOT / "scripts" / "mdquery"
        if not launcher.is_file():
            self.skipTest("launcher not present")
        result = subprocess.run([str(launcher), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Structural queries", result.stdout)


if __name__ == "__main__":
    unittest.main()
