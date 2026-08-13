"""
The structural IR emitted by `mdfix --emit-ir` (issue #15, schema mdtools-ir-2).

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
SCHEMA = "mdtools-ir-3"

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

    def _ir_raw(self, data: bytes | str, name: str = "t.md") -> list[dict]:
        """Every record, gaps included. Totality tests need these."""
        path = self._write(data, name)
        result = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def _ir(self, data: bytes | str, name: str = "t.md") -> list[dict]:
        """
        Header plus top-level content records.

        `gap` records carry the runs between blocks and are structure rather
        than content. Nested records (schema 3, `depth > 0`) live inside their
        parent's span, so including them here would double-count bytes and
        break every span and totality assertion below.
        """
        return [r for r in self._ir_raw(data, name)
                if r["kind"] != "gap" and not r.get("depth")]

    def _nested(self, data: bytes | str, name: str = "t.md") -> list[dict]:
        return [r for r in self._ir_raw(data, name) if r.get("depth")]

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


class TotalityTests(IRTestCase):
    """
    I5.3 groundwork (issue #56): every byte belongs to exactly one record.

    Schema 1 covered the blocks and nothing else — terminators, blank runs, a
    leading BOM and trailing bytes belonged to no record. A serializer built on
    that would silently normalize all of them: one blank line where the author
    left three, a lost hard break, a rewritten line ending.
    """

    CASES = {
        "lf": b"# A\n\npara\n",
        "crlf": b"# A\r\n\r\npara\r\n",
        "cr-only": b"# A\r\rpara\r",
        "no final newline": b"# A\n\npara",
        "bom": b"\xef\xbb\xbf# A\n\npara\n",
        "many blanks": b"\n\n\n# A\n\n\n\npara\n\n\n",
        "only blanks": b"\n\n\n",
        "empty": b"",
        "hard break": b"line one  \nline two\n",
        "trailing spaces": b"# A   \n\npara \n",
        "tabs": b"# A\n\n\tcode\n",
    }

    def test_records_reproduce_the_file(self) -> None:
        for name, data in self.CASES.items():
            with self.subTest(case=name):
                records = [r for r in self._ir_raw(data)[1:]
                           if not r.get("depth")]
                joined = b"".join(data[r["start"]:r["end"]] for r in records)
                self.assertEqual(joined, data)

    def test_records_are_contiguous(self) -> None:
        # Stronger than non-overlapping: no byte is skipped either.
        for name, data in self.CASES.items():
            with self.subTest(case=name):
                cursor = 0
                for record in self._ir_raw(data)[1:]:
                    if record.get("depth"):
                        continue
                    self.assertEqual(record["start"], cursor, record)
                    cursor = record["end"]
                self.assertEqual(cursor, len(data))

    def test_hard_breaks_are_inside_a_record(self) -> None:
        # dialect-policy §7 gap 5: two trailing spaces are a hard break, and
        # a serializer must be able to see them to preserve them.
        data = b"line one  \nline two\n"
        records = [r for r in self._ir_raw(data)[1:] if not r.get("depth")]
        joined = b"".join(data[r["start"]:r["end"]] for r in records)
        self.assertIn(b"  \n", joined)

    def test_blank_run_length_is_preserved(self) -> None:
        # One blank line versus three is a real difference; both must survive.
        for blanks in (1, 2, 5):
            with self.subTest(blanks=blanks):
                data = b"# A\n" + b"\n" * blanks + b"para\n"
                records = [r for r in self._ir_raw(data)[1:]
                           if not r.get("depth")]
                joined = b"".join(data[r["start"]:r["end"]] for r in records)
                self.assertEqual(joined, data)

    def test_gaps_are_not_protected(self) -> None:
        # mdfix's list-spacing fixes insert and remove blank lines, so a gap
        # is not reproduced byte for byte and must not claim to be.
        gaps = [r for r in self._ir_raw(b"# A\n\npara\n") if r["kind"] == "gap"]
        self.assertTrue(gaps)
        self.assertFalse(any(g["protected"] for g in gaps))

    def test_gap_line_numbers_stay_in_document_range(self) -> None:
        # Terminator-only gaps used to report line nlines+1.
        for data in (b"# A\npara\n", b"# A\n\npara\n", b"# A\n", b"\xef\xbb\xbf# A\n"):
            with self.subTest(data=data):
                records = self._ir_raw(data)
                nlines = records[0]["lines"]
                for r in records[1:]:
                    self.assertGreaterEqual(r["line"], 1)
                    self.assertLessEqual(r["endLine"], max(nlines, 1))
                    self.assertLessEqual(r["line"], r["endLine"])

    def test_a_leading_bom_is_its_own_gap(self) -> None:
        records = self._ir_raw(b"\xef\xbb\xbf# A\n")[1:]
        self.assertEqual(records[0]["kind"], "gap")
        self.assertEqual((records[0]["start"], records[0]["end"]), (0, 3))

    def test_schema_is_declared(self) -> None:
        self.assertEqual(self._ir_raw(b"# A\n")[0]["schema"], SCHEMA)


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


class NestedProseTests(IRTestCase):
    """Schema 3: plain list-item prose as depth-1 paragraphs."""

    def test_marker_is_excluded_from_the_span(self) -> None:
        source = "- first item\n- second\n"
        nested = self._nested(source)
        self.assertEqual(len(nested), 2)
        data = source.encode("utf-8")
        self.assertEqual(data[nested[0]["start"]:nested[0]["end"]], b"first item")
        self.assertEqual(data[nested[1]["start"]:nested[1]["end"]], b"second")
        self.assertEqual(nested[0]["depth"], 1)
        self.assertEqual(nested[0]["kind"], "paragraph")

    def test_parent_is_the_list_start(self) -> None:
        source = "- one\n- two\n"
        top = self._ir(source)
        lists = [r for r in top if r["kind"] == "list"]
        self.assertEqual(len(lists), 1)
        for child in self._nested(source):
            self.assertEqual(child["parent"], lists[0]["start"])

    def test_ordered_markers_work(self) -> None:
        source = "1. alpha\n2. beta\n"
        nested = self._nested(source)
        self.assertEqual(len(nested), 2)
        data = source.encode("utf-8")
        self.assertEqual(data[nested[0]["start"]:nested[0]["end"]], b"alpha")

    def test_loose_items_split_on_blank_lines(self) -> None:
        source = "- first\n\n  continued\n- next\n"
        nested = self._nested(source)
        # Two paragraphs in the first item, one in the second.
        self.assertEqual(len(nested), 3)
        data = source.encode("utf-8")
        self.assertEqual(data[nested[0]["start"]:nested[0]["end"]], b"first")
        self.assertIn(b"continued", data[nested[1]["start"]:nested[1]["end"]])

    def test_pipe_prose_is_still_nested(self) -> None:
        # A bare pipe in item text is not a table; under-report only real tables.
        source = "- use A | B here\n"
        nested = self._nested(source)
        self.assertEqual(len(nested), 1)
        data = source.encode("utf-8")
        self.assertEqual(data[nested[0]["start"]:nested[0]["end"]],
                         b"use A | B here")

    def test_non_prose_runs_are_not_nested(self) -> None:
        # Opacity is per blank-separated run: plain intro may still nest, but
        # the fence/quote/heading/table run must not become a depth-1 paragraph.
        cases = {
            "fence": "- intro\n\n  ```\n  code\n  ```\n",
            "heading": "- intro\n\n  # nested head\n",
            "blockquote": "- intro\n\n  > quoted\n",
            "table": "- intro\n\n  a | b\n  --|--\n  1 | 2\n",
        }
        for name, source in cases.items():
            with self.subTest(construct=name):
                nested = self._nested(source)
                data = source.encode("utf-8")
                for child in nested:
                    span = data[child["start"]:child["end"]]
                    self.assertNotIn(b"```", span)
                    self.assertNotIn(b"> quoted", span)
                    self.assertNotIn(b"# nested", span)
                    self.assertNotIn(b"--|--", span)
                # The plain intro is still reachable.
                self.assertTrue(
                    any(data[c["start"]:c["end"]] == b"intro" for c in nested),
                    msg=nested,
                )

    def test_blockquote_alone_in_item_is_opaque(self) -> None:
        # Mis-report would emit a paragraph spanning the `>` markers.
        source = "- > quoted line\n"
        self.assertEqual(self._nested(source), [])
        self.assertIn("list", self._kinds(source))

    def test_tight_quote_after_prose_keeps_whole_run_opaque(self) -> None:
        # No blank between intro and quote: one run, quote fails plain → none.
        self.assertEqual(self._nested("- intro\n  > quoted\n"), [])


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

    def test_pipe_table_without_a_leading_bar(self) -> None:
        # Pandoc reads `a | b` over `--|--` as a Table. Reporting it as a
        # paragraph handed table rows to any consumer editing prose (#65).
        self.assertEqual(self._kinds("a | b\n--|--\n1 | 2\n"), ["table"])
        self.assertEqual(self._kinds("a | b\n--|--\n"), ["table"])
        self.assertEqual(self._ir("a | b\n--|--\n1 | 2\n")[1]["form"], "pipe")

    def test_a_pipe_alone_is_not_a_table(self) -> None:
        # The delimiter row is the whole discriminator, so prose containing a
        # pipe must stay prose — the expensive direction to get wrong.
        for source in ("Either a | b is fine.\n",
                       "Either a | b.\nOr c | d.\n",
                       "--|--\n",
                       "a | b\n-----\n1 | 2\n"):
            with self.subTest(source=source):
                self.assertNotIn("table", self._kinds(source))

    def test_a_header_continuing_a_paragraph_is_not_a_table(self) -> None:
        # `Intro.` then `a | b` then `--|--` is one Para to pandoc: the header
        # is a lazy continuation, so no table starts.
        self.assertEqual(self._kinds("Intro.\na | b\n--|--\n1 | 2\n"),
                         ["paragraph"])

    def test_block_openers_are_not_headerless_table_headers(self) -> None:
        # Headerless recognition must not invent a Table over lines our
        # classifier takes as other openers (heading, list, quote, ref-def).
        # Inventing structure is the unsafe direction for the IR.
        for name, source in (
            ("heading", "# Name | Role\n---|---\nAlice | Eng\n"),
            ("bullet", "- a | b\n--|--\n1 | 2\n"),
            ("blockquote", "> a | b\n--|--\n1 | 2\n"),
            ("ref_def", "[a | b]: http://example.com\n--|--\n"),
        ):
            with self.subTest(opener=name):
                self.assertNotIn("table", self._kinds(source),
                                 msg=f"IR kinds: {self._kinds(source)}")

    def test_a_pipe_table_ends_at_the_first_line_without_a_pipe(self) -> None:
        self.assertEqual(self._kinds("a | b\n--|--\n1 | 2\nProse.\n"),
                         ["table", "paragraph"])

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

    def test_unclosed_front_matter_does_not_swallow_the_file(self) -> None:
        # Issue #64. `---` with no closer is a thematic break, and treating
        # it as an unterminated metadata block froze the whole document:
        # every block came back protected and mdfix reported the file clean.
        self.assertEqual(self._kinds("---\n\n# H\n\nBody.\n"),
                         ["thematic_break", "heading", "paragraph"])

    def test_a_delimiter_is_exactly_three_dashes(self) -> None:
        # The old test accepted `---` plus a space plus anything, so a Pandoc
        # dash row at line 1 opened front matter.
        # pandoc: HorizontalRule + Para. The point is that it is not front
        # matter — before #64 this opened a block that ran to EOF.
        self.assertEqual(self._kinds("---    ----\n12     34\n"),
                         ["thematic_break", "paragraph"])
        self.assertNotIn("frontmatter", self._kinds("----\ntitle: T\n----\n"))

    def test_trailing_whitespace_after_the_delimiter_is_allowed(self) -> None:
        self.assertEqual(self._kinds("---   \ntitle: T\n---\n\n# H\n"),
                         ["frontmatter", "heading"])

    def test_a_dot_delimiter_closes_front_matter(self) -> None:
        # Pandoc accepts `...` as a terminator; mdfix did not, so the block
        # was unclosed and ran to EOF.
        record = self._ir("---\ntitle: T\n...\n\n# H\n")[1]
        self.assertEqual(record["kind"], "frontmatter")
        self.assertEqual((record["line"], record["endLine"]), (1, 3))

    def test_dot_closer_does_not_freeze_the_fixer(self) -> None:
        # process() used to gate open/close on LT_FMATTER only, so a Pandoc
        # `...` closer left in_frontmatter set and the body was pass-through.
        # The L2 ATX-space repair must still run after a `...` block.
        source = "---\ntitle: T\n...\n\n#Title\n"
        path = self.dir / "fm.md"
        out = self.dir / "fm.out.md"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", str(path), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(out.read_text(encoding="utf-8"),
                         "---\ntitle: T\n...\n\n# Title\n")

    def test_empty_front_matter(self) -> None:
        self.assertEqual(self._kinds("---\n---\n\n# H\n"),
                         ["frontmatter", "heading"])

    def test_frontmatter_only_at_the_top(self) -> None:
        self.assertEqual(self._kinds("---\na: 1\n---\n"), ["frontmatter"])
        # A later `---` is a thematic break, not a second front matter.
        self.assertEqual(self._kinds("para\n\n---\n\nmore\n"),
                         ["paragraph", "thematic_break", "paragraph"])


class StructureNotParagraphTests(IRTestCase):
    """
    Constructs that Pandoc does not render as paragraphs, and that a prose
    pass must never be handed.

    These were reported as `paragraph` until prosevary needed to stop carrying
    its own classifier. Reporting a section heading or a link definition as
    prose is only safe for a reader; a tool that rewrites text would paraphrase
    them. Every rule here is pinned with `pandoc -t json`.
    """

    def test_setext_headings(self) -> None:
        for source, level in (("Title\n=====\n", 1), ("Title\n-----\n", 2)):
            with self.subTest(source=source):
                record = self._ir(source)[1]
                self.assertEqual(record["kind"], "heading")
                self.assertEqual(record["level"], level)
                self.assertEqual(record["style"], "setext")
                self.assertEqual(record["text"], "Title")
                self.assertEqual((record["line"], record["endLine"]), (1, 2))

    def test_atx_headings_say_so_too(self) -> None:
        self.assertEqual(self._ir("# Title\n")[1]["style"], "atx")

    def test_underline_must_start_at_column_zero(self) -> None:
        # CommonMark allows up to three spaces; pandoc's markdown reader does
        # not, and it is the output dialect. One space is already too far.
        self.assertEqual(self._kinds("Title\n ===\n"), ["paragraph"])
        self.assertEqual(self._kinds("Title\n===\n"), ["heading"])

    def test_setext_text_may_be_indented(self) -> None:
        self.assertEqual(self._kinds("   Title\n===\n"), ["heading"])

    def test_setext_text_is_a_single_line(self) -> None:
        self.assertEqual(self._kinds("Line one\nline two\n=====\n"),
                         ["paragraph"])

    def test_a_rule_under_a_rule_is_a_heading(self) -> None:
        # The text line may itself look like a thematic break; pandoc agrees.
        self.assertEqual(self._kinds("-----\n-----\n"), ["heading"])

    def test_a_rule_after_a_blank_is_still_a_rule(self) -> None:
        self.assertEqual(self._kinds("Para.\n\n-----\n\nBody.\n"),
                         ["paragraph", "thematic_break", "paragraph"])

    def test_simple_tables_still_win(self) -> None:
        self.assertEqual(self._kinds("Right  Left\n-----  ----\n12     34\n"),
                         ["table"])

    def test_reference_definitions(self) -> None:
        self.assertEqual(self._kinds("[id]: http://x\n\nBody.\n"),
                         ["reference_def", "paragraph"])

    def test_reference_definition_without_a_space(self) -> None:
        # `[id]:x` is a definition to pandoc, which emits no block for it.
        # prosevary's regex demanded whitespace and called this prose.
        self.assertEqual(self._kinds("[id]:x\n\nBody.\n"),
                         ["reference_def", "paragraph"])

    def test_reference_definition_takes_a_quoted_title(self) -> None:
        record = self._ir('[id]: http://x\n   "Title"\n\nBody.\n')[1]
        self.assertEqual(record["kind"], "reference_def")
        self.assertEqual((record["line"], record["endLine"]), (1, 2))

    def test_reference_definition_does_not_take_indented_prose(self) -> None:
        # An indented plain line after a definition is a code block.
        self.assertEqual(self._kinds("[id]: http://x\n    indented\n"),
                         ["reference_def", "code_indented"])

    def test_footnote_definitions(self) -> None:
        self.assertEqual(self._kinds("[^1]: Note.\n\nBody.\n"),
                         ["footnote_def", "paragraph"])

    def test_footnote_definition_spans_a_blank_line(self) -> None:
        record = self._ir("[^1]: One.\n\n    Two.\n\nBody.\n")[1]
        self.assertEqual(record["kind"], "footnote_def")
        self.assertEqual((record["line"], record["endLine"]), (1, 3))

    def test_a_bracket_without_a_colon_is_prose(self) -> None:
        self.assertEqual(self._kinds("[id] no colon\n"), ["paragraph"])

    def test_empty_label_is_not_a_definition(self) -> None:
        self.assertEqual(self._kinds("[]: http://x\n"), ["paragraph"])
        self.assertEqual(self._kinds("[^]: note\n"), ["paragraph"])

    def test_definition_line_is_not_setext_text(self) -> None:
        # Else the IR invents a Header pandoc does not emit.
        self.assertEqual(
            self._kinds("[id]: http://x\n====\n"),
            ["reference_def", "paragraph"],
        )
        self.assertEqual(
            self._kinds("[id]:x\n----\n"),
            ["reference_def", "thematic_break"],
        )


class InlineRecordTests(IRTestCase):
    """
    Inline structure: links, images, code spans, footnote references.

    Purely additive — new kinds at depth > 0, which schema 3 already excludes
    from totality. No consumer changed, which is why the schema name did not.
    """

    def _inline(self, source: str) -> list[dict]:
        return [r for r in self._ir_raw(source)
                if r["kind"] in ("link", "image", "code_span",
                                 "footnote_ref", "raw_inline")]

    def test_inline_link(self) -> None:
        record = self._inline("See [text](http://x) here.\n")[0]
        self.assertEqual(record["kind"], "link")
        self.assertEqual(record["form"], "inline")
        self.assertEqual(record["destination"], "http://x")
        self.assertEqual(record["text"], "text")

    def test_destination_strips_title_and_angle_brackets(self) -> None:
        titled = self._inline('See [text](./a.md "Title") here.\n')[0]
        self.assertEqual(titled["destination"], "./a.md")
        angled = self._inline("See [text](<./a.md>) here.\n")[0]
        self.assertEqual(angled["destination"], "./a.md")

    def test_image(self) -> None:
        record = self._inline("An ![alt](i.png) image.\n")[0]
        self.assertEqual(record["kind"], "image")
        self.assertEqual(record["destination"], "i.png")

    def test_autolink(self) -> None:
        record = self._inline("See <http://x> here.\n")[0]
        self.assertEqual((record["kind"], record["form"]), ("link", "autolink"))
        self.assertEqual(record["destination"], "http://x")

    def test_reference_and_shortcut_carry_a_label(self) -> None:
        # Not resolved here: the consumer already has the reference_def
        # records, and holding the link table in the emitter buys nothing.
        ref = self._inline("See [text][id].\n")[0]
        self.assertEqual((ref["form"], ref["label"], ref["text"]),
                         ("reference", "id", "text"))
        short = self._inline("See [id] here.\n")[0]
        self.assertEqual((short["form"], short["label"]), ("shortcut", "id"))

    def test_code_span_is_protected(self) -> None:
        record = self._inline("Use `code` here.\n")[0]
        self.assertEqual(record["kind"], "code_span")
        self.assertEqual(record["text"], "code")
        self.assertTrue(record["protected"])

    def test_double_backtick_code_span(self) -> None:
        record = self._inline("Use ``a `b` c`` here.\n")[0]
        self.assertEqual(record["text"], "a `b` c")

    def test_footnote_reference(self) -> None:
        records = self._inline("Text[^1].\n\n[^1]: Note.\n")
        self.assertEqual(records[0]["kind"], "footnote_ref")
        self.assertEqual(records[0]["label"], "1")

    def test_an_escaped_bracket_opens_nothing(self) -> None:
        self.assertEqual(self._inline("See \\[not a link](x) here.\n"), [])

    def test_spans_slice_the_source(self) -> None:
        source = "See [text](http://x) and `code` here.\n"
        data = source.encode("utf-8")
        for record in self._inline(source):
            with self.subTest(kind=record["kind"]):
                segment = data[record["start"]:record["end"]].decode()
                self.assertTrue(segment.startswith(("[", "`", "!", "<")))

    def test_inline_in_a_heading(self) -> None:
        records = self._inline("# See [text](http://x)\n")
        self.assertEqual(len(records), 1)

    def test_inline_in_a_setext_heading(self) -> None:
        records = self._inline("See [text](http://x)\n===\n")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "link")

    def test_crlf_multiline_inline_offsets(self) -> None:
        # Per-line bases: a construct on line 2 must slice the real file
        # bytes under CRLF, not a synthetic LF-joined chunk.
        data = b"first line\r\nSee [text](http://x) here.\r\n"
        records = [r for r in self._ir_raw(data)
                   if r["kind"] == "link"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(data[record["start"]:record["end"]],
                         b"[text](http://x)")
        self.assertEqual(record["line"], 2)

    def test_inline_nested_in_a_list_item(self) -> None:
        records = self._inline("- item with [a link](http://x)\n")
        self.assertEqual(len(records), 1)
        self.assertGreaterEqual(records[0]["depth"], 2)

    def test_inline_in_a_table_cell(self) -> None:
        # mdlinks would miss five of eleven links in this repository's own
        # architecture doc without this.
        records = self._inline("| a | [x](http://y) |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual(len(records), 1)

    def test_inline_in_a_block_quote(self) -> None:
        self.assertEqual(len(self._inline("> guarded by `ImportError` here\n")), 1)

    def test_nothing_inside_a_fence(self) -> None:
        # A fenced block is verbatim; a backtick pair inside it is content.
        self.assertEqual(self._inline("```\nUse `code` here.\n```\n"), [])

    def test_records_are_nested_not_top_level(self) -> None:
        # The additive property: totality still holds over depth-0 records.
        data = b"See [text](http://x) here.\n"
        top = [r for r in self._ir_raw(data)[1:] if not r.get("depth")]
        self.assertEqual(b"".join(data[r["start"]:r["end"]] for r in top), data)


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
            f"_ _ _\n\n"
            # Code after a ref def / setext must stay protected in process()
            # as well as in the IR (prev_content_type agreement).
            f"[id]: http://x\n"
            f"    after-def {arrow} code\n\n"
            f"Setext Title\n"
            f"====\n"
            f"    after-setext {arrow} code\n"
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
        self.assertIn(f"after-def {arrow} code".encode(), fixed)
        self.assertIn(f"after-setext {arrow} code".encode(), fixed)

    def test_process_protects_code_after_ref_def_and_setext(self) -> None:
        arrow = "→"
        cases = {
            "ref def": f"[id]: http://x\n    code {arrow} here\n",
            "setext": f"Title\n====\n    code {arrow} here\n",
            "footnote": f"[^1]: note\n    code {arrow} here\n",
        }
        for name, source in cases.items():
            with self.subTest(case=name):
                src = self._write(source, "p.md")
                out = self.dir / "p_out.md"
                if out.exists():
                    out.unlink()
                subprocess.run(
                    [str(MDFIX), "-q", "--technical", str(src), str(out)],
                    capture_output=True, check=True,
                )
                self.assertIn(f"code {arrow} here", out.read_text(encoding="utf-8"))


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PandocOracleTests(IRTestCase):
    """The IR claims to describe Markdown; Pandoc decides whether it does."""

    # Kinds Pandoc emits no block for: front matter is metadata, and link and
    # footnote definitions are definitions. Verified — `[id]: http://x` on its
    # own produces an empty block list.
    NO_PANDOC_BLOCK = frozenset({"frontmatter", "reference_def", "footnote_def"})

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
        ours = [r for r in self._ir(SAMPLE)[1:]
                if r["kind"] not in self.NO_PANDOC_BLOCK]
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
                        if r["kind"] not in self.NO_PANDOC_BLOCK]
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
        "definition list": ("Term\n:   Def\n", "DefinitionList"),
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
        for name in ("Definition list", "Display math", "Raw LaTeX"):
            with self.subTest(row=name):
                self.assertIn(name, section[1])


if __name__ == "__main__":
    unittest.main()
