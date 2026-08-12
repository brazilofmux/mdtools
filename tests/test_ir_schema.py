"""
The structural IR emitted by `mdfix --emit-ir` (issue #15, schema mdtools-ir-1).

This is the reader half of the boundary in docs/dialect-policy.md §2: consumers
locate and edit Markdown through these spans instead of re-deriving the
grammar. The guarantee that makes that possible is byte-exactness — a span must
slice the original file, not a normalized copy — so most of this file is spent
on that rather than on the block taxonomy.

docs/ir-schema.md is the contract; the divergence table at the bottom of it is
pinned here so closing a gap is a deliberate change rather than a surprise.
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
SCHEMA_DOC = ROOT / "docs" / "ir-schema.md"
PANDOC = shutil.which("pandoc")
SCHEMA = "mdtools-ir-1"

SAMPLE = """\
---
title: Test
---

# Heading One

A paragraph with
two lines.

## Sub ##

- item one
- item two
  continued

```python
code = 1
```

| a | b |
|---|---|
| 1 | 2 |

| line block
| second line

+---+---+
| x | y |
+---+---+

> quoted
> more

    indented code

<!-- a comment -->

---

Final.
"""


class IRTestCase(unittest.TestCase):
    """Shared plumbing: a fresh binary, and IR from bytes."""

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

    def _write(self, data: bytes | str, name: str = "t.md") -> Path:
        path = self.dir / name
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def _ir(self, data: bytes | str, name: str = "t.md") -> list[dict]:
        path = self._write(data, name)
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def _kinds(self, data: bytes | str) -> list[str]:
        return [r["kind"] for r in self._ir(data)[1:]]


class SpanGuaranteeTests(IRTestCase):
    """
    Guarantee 1–3. These are the properties a consumer builds on, so they are
    checked against raw bytes rather than decoded text.
    """

    CASES = {
        "lf": b"# A\n\npara\n",
        "crlf": b"# A\r\n\r\npara\r\n",
        "cr-only": b"# A\r\rpara\r",
        "no final newline": b"# A\n\npara",
        "utf-8": "# Héading →\n\ntext — here\n".encode("utf-8"),
        "blank lines": b"\n\n\n# A\n\n\n\npara\n\n\n",
        "tabs": b"# A\n\n\tcode\n",
    }

    def test_spans_slice_the_source_exactly(self) -> None:
        for name, data in self.CASES.items():
            with self.subTest(case=name):
                for record in self._ir(data)[1:]:
                    start, end = record["start"], record["end"]
                    self.assertLessEqual(0, start)
                    self.assertLessEqual(start, end)
                    self.assertLessEqual(end, len(data))
                    segment = data[start:end]
                    # Guarantee 2: the terminator is never inside the span.
                    self.assertFalse(segment.endswith(b"\n"), record)
                    self.assertFalse(segment.endswith(b"\r"), record)

    def test_crlf_offsets_are_not_lf_offsets(self) -> None:
        # The regression that motivated tracking offsets at read time: mdfix
        # strips terminators and normalizes CRLF, so lines[] alone cannot
        # locate anything. A CRLF file must report wider spacing than its LF
        # twin, not identical offsets.
        lf = self._ir(b"# A\n\npara\n")[1:]
        crlf = self._ir(b"# A\r\n\r\npara\r\n")[1:]
        self.assertEqual([r["kind"] for r in lf], [r["kind"] for r in crlf])
        self.assertEqual(lf[1]["start"], 5)
        self.assertEqual(crlf[1]["start"], 7)

    def test_records_are_ordered_and_disjoint(self) -> None:
        for name, data in self.CASES.items():
            with self.subTest(case=name):
                prev = 0
                for record in self._ir(data)[1:]:
                    self.assertGreaterEqual(record["start"], prev, record)
                    prev = record["end"]

    def test_line_numbers_agree_with_offsets(self) -> None:
        data = SAMPLE.encode("utf-8")
        for record in self._ir(data)[1:]:
            with self.subTest(kind=record["kind"], line=record["line"]):
                before = data[: record["start"]]
                self.assertEqual(before.count(b"\n") + 1, record["line"])

    def test_header_record_comes_first(self) -> None:
        records = self._ir(SAMPLE)
        self.assertEqual(records[0]["kind"], "document")
        self.assertEqual(records[0]["schema"], SCHEMA)
        self.assertEqual(records[0]["bytes"], len(SAMPLE.encode("utf-8")))
        self.assertEqual(records[0]["source"], str(self.dir / "t.md"))

    def test_byte_count_is_the_file_size(self) -> None:
        for name, data in self.CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self._ir(data)[0]["bytes"], len(data))

    def test_empty_file_emits_only_a_header(self) -> None:
        self.assertEqual(len(self._ir(b"")), 1)

    def test_control_bytes_do_not_break_the_json(self) -> None:
        # heading.text is copied out of the source, so a raw C0 byte would
        # emit a JSON document no parser accepts.
        record = self._ir(b"# a\x01b\x02c\n")[1]
        self.assertEqual(record["text"], "a\x01b\x02c")

    def test_quotes_and_backslashes_in_heading_text(self) -> None:
        record = self._ir('# a "b" \\ c\n')[1]
        self.assertEqual(record["text"], 'a "b" \\ c')

    def test_long_heading_survives(self) -> None:
        # ir_emit_heading copies into a MAX_LINE buffer; this is the input
        # that would overflow it. `make asan` covers the same ground.
        text = "x" * 8000
        self.assertEqual(self._ir(f"# {text}\n")[1]["text"], text)

    def test_output_is_one_json_object_per_line(self) -> None:
        path = self._write(SAMPLE)
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True, check=True,
        )
        self.assertTrue(result.stdout.endswith("\n"))
        for line in result.stdout.splitlines():
            self.assertIsInstance(json.loads(line), dict)


class ReadOnlyTests(IRTestCase):
    """Guarantee 5. A read-only mode that writes is worse than no mode."""

    def test_input_is_untouched(self) -> None:
        path = self._write(SAMPLE)
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        subprocess.run([str(MDFIX), "--emit-ir", str(path)],
                       capture_output=True, check=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_no_stray_files_are_created(self) -> None:
        self._write(SAMPLE)
        before = sorted(p.name for p in self.dir.iterdir())
        subprocess.run([str(MDFIX), "--emit-ir", str(self.dir / "t.md")],
                       capture_output=True, check=True)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), before)

    def test_no_summary_pollutes_the_stream(self) -> None:
        # Without -q, every other mode prints a summary. On stdout it would
        # corrupt the JSONL, so --emit-ir must suppress it unasked.
        path = self._write("#Heading\n\nA  sentence.\n")
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            json.loads(line)

    def test_rejects_in_place(self) -> None:
        path = self._write(SAMPLE)
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", "-i", str(path)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only reads", result.stderr)

    def test_rejects_canonical_lint(self) -> None:
        path = self._write(SAMPLE)
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", "--canonical-lint", str(path)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_several_files_share_one_stream(self) -> None:
        a = self._write("# A\n", "a.md")
        b = self._write("# B\n", "b.md")
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", str(a), str(b)],
            capture_output=True, text=True, check=True,
        )
        records = [json.loads(line) for line in result.stdout.splitlines()]
        headers = [r for r in records if r["kind"] == "document"]
        self.assertEqual([Path(h["source"]).name for h in headers],
                         ["a.md", "b.md"])


class BlockKindTests(IRTestCase):
    def test_sample_document_segmentation(self) -> None:
        self.assertEqual(self._kinds(SAMPLE), [
            "frontmatter", "heading", "paragraph", "heading", "list",
            "code_fence", "table", "line_block", "table", "block_quote",
            "code_indented", "raw_html", "thematic_break", "paragraph",
        ])

    def test_heading_level_and_text(self) -> None:
        records = self._ir("# One\n\n### Three ###\n\n# C#\n")[1:]
        self.assertEqual([(r["level"], r["text"]) for r in records],
                         [(1, "One"), (3, "Three"), (1, "C#")])

    def test_heading_plain_strips_inline_markup(self) -> None:
        # `plain` is the text Pandoc's identifier pass sees. Exactly three
        # constructs need handling; the rest already agree once a consumer
        # drops non-identifier characters.
        cases = {
            "[link](http://x)": "link",
            "![img](i.png)": "img",
            "a [b](c) d": "a b d",
            "[a **b** c](u)": "a b c",
            "*star*": "star",
            "_under_": "under",
            "**strong**": "strong",
            "<span>html</span>": "html",
            "mix *a* and _b_": "mix a and b",
            # Residual closer: consume min(opener, closer), leave the rest.
            "_a___": "a__",
            "___a_": "__a",
        }
        for text, expected in cases.items():
            with self.subTest(heading=text):
                self.assertEqual(self._ir(f"# {text}\n")[1]["plain"], expected)

    def test_heading_plain_leaves_the_rest_alone(self) -> None:
        # Under-report rather than mis-report. Reference links especially:
        # Pandoc computes identifiers before resolving them, so raw is right.
        # Spaced `] (` is not a tight inline link under pandoc markdown.
        # Bracketed spans use `{`, not `(`, after `]`.
        for text in ("[text][id]", "[shortcut]", "note[^1]", "<http://auto>",
                     "`code` span", "a * b", "a_b_c", "_unclosed",
                     "*unclosed", "trailing_", "intra_word_score",
                     "[link] (http://x)", "[text]{.class}"):
            with self.subTest(heading=text):
                self.assertEqual(self._ir(f"# {text}\n")[1]["plain"], text)

    def test_heading_plain_is_unicode_clean(self) -> None:
        # Greek, CJK and Hangul pass through untouched, and an underscore
        # against a multibyte letter stays literal — `isalnum` is byte-based,
        # so this read as emphasis and deleted the underscores.
        for text in ("\u6f22\u5b57_\u306e_\u5f37\u8abf",
                     "\u0398\u03b5\u03bf\u03bb\u03bf\u03b3\u03af\u03b1",
                     "\ud55c\uad6d\uc5b4 \uc81c\ubaa9",
                     "\u2200x \u2208 \u211d, x\u00b2 \u2265 0"):
            with self.subTest(heading=text):
                self.assertEqual(self._ir(f"# {text}\n")[1]["plain"], text)

    def test_deeply_nested_link_text_does_not_crash(self) -> None:
        # Recursing with two MAX_LINE buffers per frame segfaulted here at
        # around 1200 levels. The scanner now works on ranges and caps depth.
        text = "x"
        while len(text) < 7000:
            text = f"[{text}](u)"
        record = self._ir(f"# {text[:7000]}\n")[1]
        self.assertEqual(record["kind"], "heading")
        self.assertIn("plain", record)

    def test_table_forms(self) -> None:
        cases = {
            "pipe": "| a | b |\n|---|---|\n| 1 | 2 |\n",
            "grid": "+---+---+\n| a | b |\n+---+---+\n",
            "simple": "Right  Left\n-----  ----\n12     34\n",
            "multiline": "-------\n A   B\n---- ----\n 1   2\n-------\n",
        }
        for form, source in cases.items():
            with self.subTest(form=form):
                tables = [r for r in self._ir(source)[1:] if r["kind"] == "table"]
                self.assertEqual([t["form"] for t in tables], [form])

    def test_line_block_is_not_a_table(self) -> None:
        # The delimiter row is the only difference; see docs/ir-schema.md.
        self.assertEqual(self._kinds("| a | b |\n| 1 | 2 |\n"), ["line_block"])
        self.assertEqual(self._kinds("| a | b |\n|---|---|\n| 1 | 2 |\n"),
                         ["table"])

    def test_raw_html_kinds(self) -> None:
        cases = {
            "comment": "<!-- c -->\n",
            "cdata": "<![CDATA[ x ]]>\n",
            "processing-instruction": "<?php ?>\n",
            "declaration": "<!DOCTYPE html>\n",
            "element": "<script>\nx()\n</script>\n",
        }
        for kind, source in cases.items():
            with self.subTest(html=kind):
                records = [r for r in self._ir(source)[1:]
                           if r["kind"] == "raw_html"]
                self.assertEqual([r["htmlKind"] for r in records], [kind])

    def test_div_is_not_raw_html(self) -> None:
        # dialect-policy §3: <div> contents are Markdown, so they must stay
        # visible as prose rather than disappear into a raw block.
        self.assertNotIn("raw_html", self._kinds("<div>\n\n*emph*\n\n</div>\n"))

    def test_unterminated_fence_is_flagged(self) -> None:
        closed = self._ir("```\nx\n```\n")[1]
        self.assertFalse(closed["unterminated"])
        open_ = self._ir("```\nx\n")[1]
        self.assertTrue(open_["unterminated"])
        self.assertEqual(open_["endLine"], 2)

    def test_indented_code_keeps_interior_blanks(self) -> None:
        records = self._ir("para\n\n    one\n\n    two\n\nafter\n")[1:]
        code = [r for r in records if r["kind"] == "code_indented"]
        self.assertEqual(len(code), 1)
        self.assertEqual((code[0]["line"], code[0]["endLine"]), (3, 5))

    def test_frontmatter_only_at_the_top(self) -> None:
        self.assertEqual(self._kinds("---\na: 1\n---\n"), ["frontmatter"])
        # A later `---` is a thematic break, not a second front matter.
        self.assertEqual(self._kinds("para\n\n---\n\nmore\n"),
                         ["paragraph", "thematic_break", "paragraph"])


class ProtectedFlagTests(IRTestCase):
    """
    `protected` makes dialect-policy §7 machine-readable. A consumer reads it
    to know whether a fixer will rewrite inside the block.
    """

    def test_verbatim_constructs_are_protected(self) -> None:
        for name, source in (
            ("fence", "```\nx\n```\n"),
            ("indented", "    x\n"),
            ("grid", "+---+\n| a |\n+---+\n"),
            ("raw html", "<!-- c -->\n"),
            ("frontmatter", "---\na: 1\n---\n"),
            # Mid-document so "---" is not YAML front matter.
            ("hr dashes", "Para.\n\n---\n\nAfter.\n"),
            ("hr stars tight", "Para.\n\n***\n\nAfter.\n"),
            ("hr stars spaced", "Para.\n\n* * *\n\nAfter.\n"),
            ("hr underscores", "Para.\n\n_ _ _\n\nAfter.\n"),
            ("hr dashes spaced", "Para.\n\n- - -\n\nAfter.\n"),
        ):
            with self.subTest(case=name):
                records = self._ir(source)[1:]
                if name.startswith("hr "):
                    hrs = [r for r in records if r["kind"] == "thematic_break"]
                    self.assertEqual(len(hrs), 1, msg=records)
                    self.assertTrue(hrs[0]["protected"])
                else:
                    self.assertTrue(all(r["protected"] for r in records))

    def test_prose_constructs_are_not_protected(self) -> None:
        for name, source in (
            ("paragraph", "text\n"),
            ("heading", "# text\n"),
            ("list", "- text\n"),
            ("block quote", "> text\n"),
            # Plus is not a thematic-break marker; this is a list.
            ("plus list", "+ + +\n"),
        ):
            with self.subTest(case=name):
                self.assertFalse(any(r["protected"] for r in self._ir(source)[1:]))

    def test_pipe_table_is_reported_unprotected(self) -> None:
        # §7 gap 4: structure survives but cell punctuation is rewritten. The
        # IR must not claim a protection mdfix does not provide.
        table = self._ir("| a | b |\n|---|---|\n| 1 | 2 |\n")[1]
        self.assertEqual(table["form"], "pipe")
        self.assertFalse(table["protected"])

    def test_spaced_star_hr_is_not_a_list(self) -> None:
        # The honesty bug: "* * *" matched find_bullet and process rewrote
        # the first marker while the IR claimed a protected thematic_break.
        records = self._ir("* * *\n\nPara.\n")[1:]
        self.assertEqual(records[0]["kind"], "thematic_break")
        self.assertTrue(records[0]["protected"])
        self.assertNotEqual(records[0]["kind"], "list")

    def test_protected_flag_matches_what_mdfix_does(self) -> None:
        # The flag is a claim about behavior, so check it against behavior:
        # run the fixer and confirm protected spans came through untouched.
        arrow = "→"
        source = (
            f"Prose {arrow} here.\n\n"
            f"```\ncode {arrow} kept\n```\n\n"
            f"+-------+\n| {arrow} cell |\n+-------+\n\n"
            # A pipe table belongs here precisely because it is *not*
            # protected: claiming it were would leave this arrow rewritten
            # inside a span the IR promised was untouched.
            f"| a | b |\n|---|---|\n| {arrow} | 2 |\n\n"
            f"<!-- {arrow} comment -->\n\n"
            f"    indented {arrow} code\n\n"
            f"* * *\n\n"
            f"***\n\n"
            f"- - -\n\n"
            f"_ _ _\n"
        )
        src = self._write(source, "in.md")
        out = self.dir / "out.md"
        subprocess.run(
            [str(MDFIX), "-q", "--technical", str(src), str(out)],
            capture_output=True, check=True,
        )
        fixed = out.read_bytes()
        raw = source.encode("utf-8")
        for record in self._ir(source, "in.md")[1:]:
            if not record["protected"]:
                continue
            with self.subTest(kind=record["kind"], start=record["start"]):
                self.assertIn(raw[record["start"]:record["end"]], fixed)
        # Explicit pin: spaced star HR must not become "- * *".
        self.assertIn(b"* * *", fixed)
        self.assertNotIn(b"- * *", fixed)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PandocOracleTests(IRTestCase):
    """The IR claims to describe Markdown; Pandoc decides whether it does."""

    # Front matter is metadata rather than a block, so it has no counterpart.
    EQUIVALENT = {
        "heading": {"Header"},
        "paragraph": {"Para", "Plain"},
        "list": {"BulletList", "OrderedList"},
        "block_quote": {"BlockQuote"},
        "code_fence": {"CodeBlock"},
        "code_indented": {"CodeBlock"},
        "table": {"Table"},
        "line_block": {"LineBlock"},
        "raw_html": {"RawBlock"},
        "thematic_break": {"HorizontalRule"},
    }

    def _pandoc_blocks(self, path: Path) -> list[str]:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]

    def test_sample_agrees_block_for_block(self) -> None:
        path = self._write(SAMPLE)
        ours = [r for r in self._ir(SAMPLE)[1:] if r["kind"] != "frontmatter"]
        theirs = self._pandoc_blocks(path)
        self.assertEqual(len(ours), len(theirs))
        for mine, other in zip(ours, theirs):
            with self.subTest(kind=mine["kind"]):
                self.assertIn(other, self.EQUIVALENT[mine["kind"]])

    def test_repository_documentation_agrees(self) -> None:
        # A corpus nobody wrote for this test.
        for name in ("README.md", "docs/dialect-policy.md", "docs/ir-schema.md"):
            path = ROOT / name
            if not path.is_file():
                continue
            with self.subTest(document=name):
                data = path.read_bytes()
                ours = [r for r in self._ir(data, Path(name).name)[1:]
                        if r["kind"] != "frontmatter"]
                theirs = self._pandoc_blocks(path)
                self.assertEqual(
                    len(ours), len(theirs),
                    f"{name}: {len(ours)} IR blocks vs {len(theirs)} pandoc",
                )
                for mine, other in zip(ours, theirs):
                    self.assertIn(other, self.EQUIVALENT[mine["kind"]],
                                  f"{name}:{mine['line']} {mine['kind']}")


@unittest.skipUnless(PANDOC, "pandoc not installed")
class KnownDivergenceTests(IRTestCase):
    """
    docs/ir-schema.md lists where the IR under-reports structure. Pinning them
    keeps the list honest: closing a gap fails here and the doc gets updated,
    rather than the table quietly describing a version that no longer exists.

    Every divergence is in the same direction — the IR says `paragraph` where
    Pandoc sees more. It never invents structure Pandoc does not see.
    """

    DIVERGENCES = {
        "setext heading": ("Title\n=====\n", "Header"),
        "definition list": ("Term\n:   Def\n", "DefinitionList"),
        "pipe table without leading bar": ("a | b\n--|--\n1 | 2\n", "Table"),
        "display math": ("$$\nx\n$$\n", "Para"),
        "raw latex": ("\\begin{verbatim}\nx\n\\end{verbatim}\n", "RawBlock"),
    }

    def test_divergences_are_still_paragraphs(self) -> None:
        for name, (source, pandoc_block) in self.DIVERGENCES.items():
            with self.subTest(case=name):
                path = self._write(source)
                result = subprocess.run(
                    [PANDOC, "-f", "markdown", "-t", "json", str(path)],
                    capture_output=True, text=True, check=True,
                )
                blocks = [b["t"] for b in json.loads(result.stdout)["blocks"]]
                self.assertEqual(blocks, [pandoc_block])
                self.assertEqual(self._kinds(source), ["paragraph"])

    def test_divergences_are_documented(self) -> None:
        text = SCHEMA_DOC.read_text(encoding="utf-8")
        section = text.split("## Known divergences", 1)
        self.assertEqual(len(section), 2, "schema doc lost its divergence list")
        for name in ("Setext", "Definition list", "Display math", "Raw LaTeX"):
            with self.subTest(row=name):
                self.assertIn(name, section[1])


if __name__ == "__main__":
    unittest.main()
