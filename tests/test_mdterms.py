"""
mdterms — glossary and terminology enforcement (issue #16).

This is the first tool that *writes*. mdquery reads; prosevary rewrites in
process. mdterms finds violations and hands mdfix an edit list, so the tool
that decides what to change is never the tool that writes the file — the
applier validates the edits and refuses any that would break the dialect.

That round trip is the thing worth testing: no test outside mdfix's own suite
had exercised `--apply-edits` from an external producer.

As with mdquery, the load-bearing assertion is the boundary: mdterms holds no
Markdown grammar, so a forbidden spelling inside a fence or a table is left
alone because the *IR* says those are not prose.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from mdterms import __main__ as cli
from mdterms.check import edits_for, scan
from mdterms.glossary import GlossaryError, freeze_set, load

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PKG = ROOT / "mdterms"

GLOSSARY = """\
terms:
  - term: SLOW-32
    aliases: [Slow-32]
    forbidden: [SLOW32, slow32]
  - term: Pandoc
    forbidden: [pandoc]
"""

DOC = """\
# About SLOW32

The SLOW32 architecture is described here. We use pandoc to render.

- A list item mentioning SLOW32 too.

```
SLOW32 in code stays.
```

| SLOW32 | in a table |
|---|---|
| stays | put |

Inline `SLOW32` is a code span.
"""


class TermsTestCase(unittest.TestCase):
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
        (self.dir / "glossary_terms.yaml").write_text(GLOSSARY, encoding="utf-8")

    def _doc(self, text: str = DOC, name: str = "t.md") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _terms(self):
        return load(self.dir / "glossary_terms.yaml")

    def _run(self, *argv: str) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class BoundaryTests(unittest.TestCase):
    """dialect-policy §2, the same rule mdquery is held to."""

    FORBIDDEN = ("```", "~~~", "^#", "|---", "+---", "<!--", "$$")
    # Documented exception: inline protected-span helpers (code spans, links)
    # so a term inside `` `SLOW32` `` or a URL is reported but never auto-fixed.
    EXEMPT_FUNCS = ("_code_spans", "_protected_spans")

    def _code(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if path.name == "check.py":
            for name in self.EXEMPT_FUNCS:
                match = re.search(
                    rf"\ndef {name}\(.*?(?=\ndef |\nclass |\Z)", text, re.S)
                self.assertIsNotNone(match, f"{path.name}: {name} is gone")
                text = text.replace(match.group(0), "\n")
            # Module-level inline patterns are the same safety concession.
            text = re.sub(
                r"^_LABEL = .*?\n_STRUCTURAL_INLINE = \(.*?\n\)\n",
                "\n", text, count=1, flags=re.S | re.M)
        return "\n".join(
            line.split("#", 1)[0]
            for line in text.splitlines()
            if not line.strip().startswith("#")
        )

    def test_no_markdown_grammar(self) -> None:
        found = []
        for path in sorted(PKG.glob("*.py")):
            code = self._code(path)
            for marker in self.FORBIDDEN:
                if marker in code:
                    found.append(f"{path.name}: {marker!r}")
        self.assertFalse(
            found, f"mdterms must consume the IR, not re-derive it: {found}")

    def test_structure_comes_only_from_mdfix(self) -> None:
        self.assertIn("raw_records", (PKG / "check.py").read_text(encoding="utf-8"))
        for path in sorted(PKG.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertNotIn("subprocess", path.read_text(encoding="utf-8"))


class GlossaryTests(TermsTestCase):
    def test_freeze_set_is_term_plus_aliases(self) -> None:
        self.assertEqual(freeze_set(self._terms()),
                         ["SLOW-32", "Slow-32", "Pandoc"])

    def test_case_only_rules_are_legal(self) -> None:
        # `Pandoc` preferred with `pandoc` forbidden differs only in case,
        # which is the point of case_sensitive. An earlier version folded
        # case here and rejected the very rule the glossary exists to state.
        terms = self._terms()
        self.assertEqual(terms[1].term, "Pandoc")
        self.assertEqual(terms[1].forbidden, ["pandoc"])

    def test_a_spelling_cannot_be_alias_and_forbidden(self) -> None:
        path = self.dir / "bad.yaml"
        path.write_text("terms:\n  - term: A\n    aliases: [B]\n"
                        "    forbidden: [B]\n", encoding="utf-8")
        with self.assertRaises(GlossaryError):
            load(path)

    def test_duplicate_terms_are_refused(self) -> None:
        path = self.dir / "dup.yaml"
        path.write_text("terms:\n  - term: A\n  - term: a\n", encoding="utf-8")
        with self.assertRaises(GlossaryError):
            load(path)

    def test_entry_without_a_term_is_refused(self) -> None:
        path = self.dir / "no.yaml"
        path.write_text("terms:\n  - aliases: [x]\n", encoding="utf-8")
        with self.assertRaises(GlossaryError):
            load(path)

    def test_empty_forbidden_spelling_is_refused(self) -> None:
        path = self.dir / "empty.yaml"
        path.write_text(
            "terms:\n  - term: A\n    forbidden: ['']\n", encoding="utf-8")
        with self.assertRaises(GlossaryError):
            load(path)


class ScanTests(TermsTestCase):
    def test_prose_and_headings_are_checked(self) -> None:
        findings = scan(self._doc(), self._terms())
        lines = sorted({f.line for f in findings})
        self.assertEqual(lines, [1, 3, 5, 15])

    def test_code_blocks_and_tables_are_left_alone(self) -> None:
        # Not because mdterms knows what a fence is — because the IR does.
        findings = scan(self._doc(), self._terms())
        for finding in findings:
            with self.subTest(line=finding.line):
                self.assertNotIn(finding.line, (8, 11))

    def test_prose_inside_a_list_item_is_checked(self) -> None:
        # Schema 3. Before nested records this was unreachable.
        findings = scan(self._doc(), self._terms())
        self.assertIn(5, [f.line for f in findings])

    def test_inline_code_is_reported_but_not_fixable(self) -> None:
        findings = scan(self._doc(), self._terms())
        inline = [f for f in findings if f.line == 15]
        self.assertEqual(len(inline), 1)
        self.assertFalse(inline[0].fixable)

    def test_unclosed_ticks_do_not_hide_later_code_spans(self) -> None:
        # An unclosed opener must not make a later closed span look fixable.
        path = self._doc("before `` unfinished then `SLOW32` after.\n")
        findings = scan(path, self._terms())
        self.assertEqual(len(findings), 1)
        self.assertFalse(findings[0].fixable)

    def test_link_destination_is_not_auto_fixed(self) -> None:
        path = self._doc("[see SLOW32](https://example.com/SLOW32)\n")
        findings = scan(path, self._terms())
        self.assertTrue(findings)
        edits = edits_for(findings)
        data = path.read_bytes()
        for edit in edits:
            span = data[edit["start"]:edit["end"]]
            self.assertNotIn(b"example.com", data[max(0, edit["start"] - 20):
                                                   edit["end"] + 20])
            self.assertEqual(span, b"SLOW32")
        # Destination occurrence must not appear as a fixable edit.
        self.assertFalse(
            any(b"https://example.com/SLOW32" in
                data[max(0, e["start"] - 30):e["end"] + 30]
                and e["expect"] == "SLOW32"
                for e in edits))
        # At least the label hit may be fixable; URL must not be.
        url_hits = [f for f in findings
                    if f.start >= data.index(b"https://")]
        self.assertTrue(url_hits)
        self.assertTrue(all(not f.fixable for f in url_hits))

    def test_spans_are_byte_offsets_into_the_file(self) -> None:
        path = self._doc()
        data = path.read_bytes()
        for finding in scan(path, self._terms()):
            with self.subTest(line=finding.line):
                self.assertEqual(data[finding.start:finding.end].decode(),
                                 finding.found)

    def test_word_boundaries(self) -> None:
        path = self._doc("SLOW32 and SLOW32X and xSLOW32 here.\n")
        findings = scan(path, self._terms())
        self.assertEqual(len(findings), 1)

    def test_non_ascii_prose_offsets(self) -> None:
        # Byte offsets, not character offsets: a multibyte prefix would shift
        # every span if this were computed in characters.
        path = self._doc("Θεολογία καὶ SLOW32 καὶ φιλοσοφία.\n")
        data = path.read_bytes()
        finding = scan(path, self._terms())[0]
        self.assertEqual(data[finding.start:finding.end], b"SLOW32")


class EditTests(TermsTestCase):
    def test_only_fixable_findings_become_edits(self) -> None:
        findings = scan(self._doc(), self._terms())
        edits = edits_for(findings)
        # Overlaps are dropped as a cluster, so the count can be lower than
        # the fixable set when two patterns claim the same span.
        self.assertLessEqual(
            len(edits), len([f for f in findings if f.fixable]))
        for edit in edits:
            self.assertTrue(any(
                f.fixable and f.start == edit["start"] and f.end == edit["end"]
                for f in findings))

    def test_overlapping_findings_drop_the_whole_cluster(self) -> None:
        # Two forbidden patterns covering the same bytes: keep neither.
        path = self.dir / "ov.yaml"
        path.write_text(
            "terms:\n"
            "  - term: AB\n    forbidden: [ABC]\n"
            "  - term: BC\n    forbidden: [ABC]\n",
            encoding="utf-8")
        doc = self._doc("See ABC here.\n")
        findings = scan(doc, load(path))
        fixable = [f for f in findings if f.fixable]
        self.assertGreaterEqual(len(fixable), 2)
        self.assertEqual(edits_for(findings), [])

    def test_edits_carry_expect(self) -> None:
        # The staleness guard the applier checks.
        for edit in edits_for(scan(self._doc(), self._terms())):
            self.assertIn("expect", edit)
            self.assertIn("rule", edit)

    def test_round_trip_through_the_applier(self) -> None:
        # The thing nothing outside mdfix had tested: an external producer's
        # edit list, validated and applied.
        path = self._doc()
        rc, out, _ = self._run("--edits", str(path))
        self.assertEqual(rc, 1)
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path)],
            input=out, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        fixed = result.stdout
        self.assertIn("# About SLOW-32", fixed)
        self.assertIn("The SLOW-32 architecture", fixed)
        self.assertIn("We use Pandoc to render", fixed)
        self.assertIn("- A list item mentioning SLOW-32", fixed)
        # Untouched: code block, table, inline code.
        self.assertIn("SLOW32 in code stays.", fixed)
        self.assertIn("| SLOW32 | in a table |", fixed)
        self.assertIn("Inline `SLOW32` is a code span.", fixed)

    def test_applying_twice_is_a_no_op(self) -> None:
        path = self._doc()
        rc, out, _ = self._run("--edits", str(path))
        subprocess.run([str(MDFIX), "-q", "-i", "--apply-edits", str(path)],
                       input=out, capture_output=True, text=True, check=True)
        rc2, out2, _ = self._run("--edits", str(path))
        # Only the inline-code finding remains, and it is not fixable.
        self.assertEqual(rc2, 1)
        self.assertEqual(out2.strip(), "")

    def test_the_header_carries_the_file_size(self) -> None:
        path = self._doc()
        _, out, _ = self._run("--edits", str(path))
        header = json.loads(out.splitlines()[0])
        self.assertEqual(header["kind"], "edits")
        self.assertEqual(header["bytes"], path.stat().st_size)


class CliTests(TermsTestCase):
    def test_clean_document_exits_zero(self) -> None:
        rc, _, _ = self._run(str(self._doc("Nothing to see, using SLOW-32.\n")))
        self.assertEqual(rc, 0)

    def test_findings_exit_one(self) -> None:
        rc, _, _ = self._run(str(self._doc()))
        self.assertEqual(rc, 1)

    def test_diagnostics_are_jsonl(self) -> None:
        rc, out, _ = self._run("--diagnostics", str(self._doc()))
        self.assertEqual(rc, 1)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["kind"], "diagnostic")
            self.assertEqual(row["rule"], "terms.forbidden")
            self.assertEqual(row["severity"], "warning")
            for field in ("path", "line", "start", "end", "severity"):
                self.assertIn(field, row)

    def test_freeze_exports_the_set(self) -> None:
        # --freeze takes no files, so there is nothing to walk up from and
        # the glossary must be named.
        rc, out, _ = self._run(
            "--glossary", str(self.dir / "glossary_terms.yaml"), "--freeze")
        self.assertEqual(rc, 0)
        self.assertEqual(out.split(), ["SLOW-32", "Slow-32", "Pandoc"])

    def test_missing_glossary_is_an_error_not_a_pass(self) -> None:
        empty = Path(self.tmp.name) / "sub"
        empty.mkdir()
        doc = empty / "x.md"
        doc.write_text("SLOW32\n", encoding="utf-8")
        rc, _, err = self._run("--glossary", str(empty / "nope.yaml"), str(doc))
        self.assertEqual(rc, 2)
        self.assertIn("no such file", err)

    def test_edits_refuses_several_files(self) -> None:
        a, b = self._doc(name="a.md"), self._doc(name="b.md")
        rc, _, err = self._run("--edits", str(a), str(b))
        self.assertEqual(rc, 2)
        self.assertIn("one file at a time", err)

    def test_edits_refuses_multi_file_even_when_one_is_clean(self) -> None:
        dirty = self._doc(name="dirty.md")
        clean = self._doc("Only SLOW-32 here.\n", name="clean.md")
        rc, _, err = self._run("--edits", str(dirty), str(clean))
        self.assertEqual(rc, 2)
        self.assertIn("one file at a time", err)

    def test_edits_refuses_multi_file_when_both_clean(self) -> None:
        a = self._doc("Only SLOW-32.\n", name="a.md")
        b = self._doc("Also SLOW-32.\n", name="b.md")
        rc, _, err = self._run("--edits", str(a), str(b))
        self.assertEqual(rc, 2)
        self.assertIn("one file at a time", err)


if __name__ == "__main__":
    unittest.main()
