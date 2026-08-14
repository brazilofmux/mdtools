"""
The L5 applier: `mdfix --apply-edits` (issue #12).

Architecture invariants:

    I4.2  incoming edits are validated — bounds, ordering, overlap, encoding
    I4.3  an edit that would break L2 is *rejected*, not silently repaired
    I5.1  an empty edit list reproduces the file byte for byte
    I5.2  a one-sentence change produces a one-sentence diff

I4.3 is the one worth stating plainly. Repairing a consumer's edit would touch
bytes the consumer never edited, which destroys the minimal-diff guarantee it
came for. Refusing keeps both guarantees and makes the failure visible.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
SCHEMA = "mdtools-edits-1"

SAMPLE = "# Title\n\nThe quick brown fox jumps.\n\nSecond paragraph here.\n"


class ApplyTestCase(unittest.TestCase):
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

    def _file(self, text: str = SAMPLE, name: str = "a.md") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _apply(self, path: Path, edits: list[dict], *flags: str,
               header: dict | None = None) -> subprocess.CompletedProcess:
        lines = []
        if header is not None:
            lines.append(json.dumps(header))
        lines += [json.dumps(e) for e in edits]
        return subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", *flags, str(path)],
            input="\n".join(lines) + ("\n" if lines else ""),
            capture_output=True, text=True,
        )


class IdentityTests(ApplyTestCase):
    def test_empty_edit_list_is_byte_identical(self) -> None:
        # I5.1, the cheapest correctness test the applier has.
        #
        # Compared as bytes, not text: subprocess text mode applies universal
        # newlines, so a CRLF file would compare equal to its LF twin and the
        # one case most worth checking would silently pass.
        for name, text in (
            ("lf", SAMPLE),
            ("crlf", "# A\r\n\r\npara\r\n"),
            ("cr only", "# A\r\rpara\r"),
            ("no final newline", "# A\n\npara"),
            ("unicode", "# 漢字\n\nΘεολογία — café…\n"),
            ("hard break", "line one  \nline two\n"),
            ("trailing blanks", "# A\n\n\n\n"),
        ):
            with self.subTest(case=name):
                data = text.encode("utf-8")
                path = self.dir / "a.md"
                path.write_bytes(data)
                result = subprocess.run(
                    [str(MDFIX), "-q", "--apply-edits", str(path)],
                    input=b"", capture_output=True)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertEqual(result.stdout, data)

    def test_crlf_survives_an_edit(self) -> None:
        # The applier is the first path that preserves line endings: the
        # fixer normalizes CRLF to LF by design, and splicing must not.
        data = "# A\r\n\r\nThe quick fox.\r\n".encode("utf-8")
        path = self.dir / "c.md"
        path.write_bytes(data)
        i = data.index(b"quick")
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path)],
            input=json.dumps({"start": i, "end": i + 5,
                              "replacement": "slow"}).encode() + b"\n",
            capture_output=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, data.replace(b"quick", b"slow"))

    def test_input_is_not_modified_when_writing_to_stdout(self) -> None:
        path = self._file()
        before = path.read_bytes()
        self._apply(path, [{"start": 2, "end": 7, "replacement": "X"}])
        self.assertEqual(path.read_bytes(), before)


class MinimalDiffTests(ApplyTestCase):
    def test_one_word_change_touches_one_line(self) -> None:
        # I5.2. Splicing rather than serializing is the whole reason.
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"quick")
        result = self._apply(path, [
            {"start": i, "end": i + 5, "replacement": "nimble",
             "rule": "prosevary.vary", "expect": "quick"},
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        before = SAMPLE.splitlines()
        after = result.stdout.splitlines()
        changed = [n for n, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn("nimble", after[changed[0]])

    def test_untouched_bytes_are_preserved_exactly(self) -> None:
        text = "# T\n\n\n\nmany   blanks  above\t\n\nlast\n"
        path = self._file(text)
        data = text.encode("utf-8")
        i = data.index(b"last")
        result = self._apply(path, [
            {"start": i, "end": i + 4, "replacement": "final"},
        ])
        self.assertEqual(result.stdout, text.replace("last", "final"))

    def test_several_edits_apply_in_order(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        result = self._apply(path, [
            {"start": data.index(b"Second"), "end": data.index(b"Second") + 6,
             "replacement": "Third"},
            {"start": data.index(b"quick"), "end": data.index(b"quick") + 5,
             "replacement": "slow"},
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("slow brown", result.stdout)
        self.assertIn("Third paragraph", result.stdout)

    def test_insertion_and_deletion(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"brown ")
        self.assertEqual(
            self._apply(path, [{"start": i, "end": i + 6, "replacement": ""}]).stdout,
            SAMPLE.replace("brown ", ""))
        self.assertEqual(
            self._apply(path, [{"start": i, "end": i, "replacement": "very "}]).stdout,
            SAMPLE.replace("brown", "very brown"))


class ValidationTests(ApplyTestCase):
    """I4.2. Every one of these is a way a consumer corrupts a manuscript."""

    def _expect_failure(self, edits, fragment, *, header=None):
        path = self._file()
        result = self._apply(path, edits, header=header)
        self.assertEqual(result.returncode, 1, msg=result.stdout)
        self.assertIn(fragment, result.stderr)
        return result

    def test_overlapping_edits_are_refused(self) -> None:
        self._expect_failure(
            [{"start": 0, "end": 5, "replacement": "x"},
             {"start": 3, "end": 9, "replacement": "y"}],
            "Overlapping edits are refused")

    def test_out_of_range_is_refused(self) -> None:
        self._expect_failure(
            [{"start": 0, "end": 99999, "replacement": "x"}], "outside the file")

    def test_negative_start_is_refused(self) -> None:
        self._expect_failure(
            [{"start": -1, "end": 2, "replacement": "x"}], "outside the file")

    def test_reversed_span_is_refused(self) -> None:
        self._expect_failure(
            [{"start": 9, "end": 4, "replacement": "x"}], "outside the file")

    def test_invalid_utf8_replacement_is_refused(self) -> None:
        path = self._file()
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path)],
            input=b'{"start":0,"end":1,"replacement":"\xff"}\n',
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"not valid UTF-8", result.stderr)

    def test_stale_expect_is_refused(self) -> None:
        # The staleness guard: the consumer says what it saw, and if the file
        # moved underneath it the spans point somewhere else entirely.
        self._expect_failure(
            [{"start": 2, "end": 7, "replacement": "x", "expect": "WRONG"}],
            "The file changed since the spans were computed")

    def test_correct_expect_is_accepted(self) -> None:
        path = self._file()
        result = self._apply(path, [
            {"start": 0, "end": 7, "replacement": "# Other", "expect": "# Title"},
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(result.stdout.startswith("# Other"))

    def test_stale_byte_count_in_the_header_is_refused(self) -> None:
        self._expect_failure(
            [{"start": 0, "end": 1, "replacement": "x"}],
            "re-run --emit-ir",
            header={"kind": "edits", "schema": SCHEMA, "bytes": 999})

    def test_unknown_schema_is_refused(self) -> None:
        self._expect_failure(
            [], "is not",
            header={"kind": "edits", "schema": "mdtools-edits-99"})

    def test_malformed_json_is_refused(self) -> None:
        path = self._file()
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path)],
            input="not json\n", capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a flat JSON object", result.stderr)

    def test_nested_values_are_refused_not_half_parsed(self) -> None:
        path = self._file()
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path)],
            input='{"start":0,"end":1,"replacement":"x","meta":{"a":1}}\n',
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)

    def test_invalid_utf8_input_file_is_refused(self) -> None:
        path = self.dir / "bad.md"
        path.write_bytes(b"# a\xff\n")
        result = self._apply(path, [])
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid UTF-8", result.stderr)

    def test_mid_codepoint_span_is_refused(self) -> None:
        # € is E2 82 AC — cutting after the first byte would corrupt UTF-8.
        data = b"# \xe2\x82\xac\n\npara\n"
        path = self.dir / "u.md"
        path.write_bytes(data)
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path)],
            input=b'{"start":2,"end":3,"replacement":"x"}\n',
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"multi-byte", result.stderr)
        self.assertEqual(path.read_bytes(), data)


class ValidateDoNotRepairTests(ApplyTestCase):
    """
    I4.3. Repairing would touch bytes the consumer never edited, so the edit
    is refused and the consumer fixes its own output.
    """

    def test_edit_needing_a_required_repair_is_refused(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"Second paragraph here.")
        result = self._apply(path, [
            {"start": i, "end": i + 22, "replacement": "Intro:\n- one\n- two"},
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("I4.3", result.stderr)
        self.assertIn("refused rather than silently fixed", result.stderr)

    def test_a_marker_run_edit_is_refused(self) -> None:
        # R4 is required. I4.3 must see it the same way it sees R2.
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"Second paragraph here.")
        result = self._apply(path, [
            {"start": i, "end": i + 22,
             "replacement": "A. First option\nB. Second option"},
        ])
        self.assertEqual(result.returncode, 1)
        self.assertIn("I4.3", result.stderr)

    def test_the_same_edit_written_correctly_is_accepted(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"Second paragraph here.")
        result = self._apply(path, [
            {"start": i, "end": i + 22, "replacement": "Intro:\n\n- one\n- two"},
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Intro:\n\n- one", result.stdout)

    def test_a_refused_edit_writes_nothing(self) -> None:
        path = self._file()
        before = path.read_bytes()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"Second paragraph here.")
        self._apply(path, [
            {"start": i, "end": i + 22, "replacement": "Intro:\n- one"},
        ], "-i")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.dir / "a.md.bak").exists())

    def test_empty_list_on_dirty_input_is_still_identity(self) -> None:
        # I5.1 is absolute: pre-existing L2 dirt must not block an empty apply.
        dirty = "#Title\n\nIntro:\n- one\n"
        path = self._file(dirty)
        result = self._apply(path, [])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, dirty)

    def test_no_required_does_not_disable_the_i43_gate(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"Second paragraph here.")
        result = self._apply(path, [
            {"start": i, "end": i + 22, "replacement": "Intro:\n- one\n- two"},
        ], "--no-required")
        self.assertEqual(result.returncode, 1)
        self.assertIn("I4.3", result.stderr)

    def test_same_offset_inserts_keep_input_order(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"quick")
        result = self._apply(path, [
            {"start": i, "end": i, "replacement": "A"},
            {"start": i, "end": i, "replacement": "B"},
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("ABquick", result.stdout)


class InPlaceTests(ApplyTestCase):
    def test_in_place_preserves_mode_and_makes_a_backup(self) -> None:
        # The applier reuses write_inplace_buf rather than reimplementing it,
        # because mode preservation, the .bak and the atomic rename were all
        # hard-won. A first version that duplicated the logic dropped 0644
        # to mkstemp's 0600.
        path = self._file()
        os.chmod(path, 0o644)
        data = SAMPLE.encode("utf-8")
        i = data.index(b"quick")
        result = self._apply(path, [
            {"start": i, "end": i + 5, "replacement": "swift"},
        ], "-i")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("swift", path.read_text(encoding="utf-8"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        backup = self.dir / "a.md.bak"
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), SAMPLE)

    def test_empty_in_place_is_identity(self) -> None:
        path = self._file()
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", "-i", str(path)],
            input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE)

    def test_output_file_form(self) -> None:
        path = self._file()
        out = self.dir / "out.md"
        data = SAMPLE.encode("utf-8")
        i = data.index(b"quick")
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path), str(out)],
            input=json.dumps({"start": i, "end": i + 5,
                              "replacement": "slow"}) + "\n",
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE)
        self.assertIn("slow", out.read_text(encoding="utf-8"))

    def test_output_file_form_refuses_if_exists(self) -> None:
        path = self._file()
        out = self.dir / "out.md"
        out.write_text("already\n", encoding="utf-8")
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", str(path), str(out)],
            input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), "already\n")

    def test_dry_run_does_not_write(self) -> None:
        path = self._file()
        data = SAMPLE.encode("utf-8")
        i = data.index(b"quick")
        # No -q: dry-run message goes to stderr when not quiet.
        result = subprocess.run(
            [str(MDFIX), "-n", "-i", "--apply-edits", str(path)],
            input=json.dumps({"start": i, "end": i + 5,
                              "replacement": "slow"}) + "\n",
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE)
        self.assertFalse((self.dir / "a.md.bak").exists())
        self.assertIn("dry run", result.stderr)

    def test_multi_file_in_place_is_refused(self) -> None:
        a = self._file(name="a.md")
        b = self._file(name="b.md")
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", "-i", str(a), str(b)],
            input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly one input file", result.stderr)


class ModeConflictTests(ApplyTestCase):
    def test_apply_and_emit_are_refused_together(self) -> None:
        path = self._file()
        result = subprocess.run(
            [str(MDFIX), "--apply-edits", "--emit-ir", str(path)],
            input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("opposite halves", result.stderr)

    def test_apply_and_lint_are_refused_together(self) -> None:
        path = self._file()
        result = subprocess.run(
            [str(MDFIX), "--apply-edits", "--canonical-lint", str(path)],
            input="", capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)


class RoundTripTests(ApplyTestCase):
    """emit-ir and apply-edits are the two halves; they must agree."""

    def test_editing_every_paragraph_via_ir_spans(self) -> None:
        text = ("# Title\n\nFirst para.\n\n```\ncode\n```\n\n"
                "Second para.\n\n- item\n\nThird para.\n")
        path = self._file(text)
        ir = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True, check=True)
        records = [json.loads(line) for line in ir.stdout.splitlines()]
        paras = [r for r in records
                 if r["kind"] == "paragraph" and not r.get("depth")]
        self.assertEqual(len(paras), 3)

        data = text.encode("utf-8")
        edits = [{"start": p["start"], "end": p["end"],
                  "replacement": "Rewritten.",
                  "expect": data[p["start"]:p["end"]].decode("utf-8")}
                 for p in paras]
        result = self._apply(path, edits)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.count("Rewritten."), 3)
        # Everything the IR called protected is untouched.
        self.assertIn("```\ncode\n```", result.stdout)
        self.assertIn("- item", result.stdout)

    def test_prose_nested_in_a_list_item_can_be_edited(self) -> None:
        # The capability schema 3 exists for: a consumer rewriting prose can
        # reach inside a list item without learning what a list marker is.
        text = "# T\n\n- first item prose\n- second item\n\nAfter.\n"
        path = self._file(text)
        ir = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True, check=True)
        nested = [json.loads(x) for x in ir.stdout.splitlines()
                  if json.loads(x).get("depth")]
        self.assertEqual(len(nested), 2)
        data = text.encode("utf-8")
        self.assertEqual(data[nested[0]["start"]:nested[0]["end"]],
                         b"first item prose")

        result = self._apply(path, [
            {"start": nested[0]["start"], "end": nested[0]["end"],
             "replacement": "rewritten prose", "expect": "first item prose"},
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("- rewritten prose\n", result.stdout)
        # The marker and every other byte are untouched.
        self.assertEqual(result.stdout,
                         text.replace("first item prose", "rewritten prose"))

    def test_protected_spans_can_be_replaced_verbatim(self) -> None:
        # Replacing a span with exactly what was there is a no-op, which is
        # the identity check applied per-block rather than per-file.
        path = self._file()
        ir = subprocess.run(
            [str(MDFIX), "--emit-ir", str(path)],
            capture_output=True, text=True, check=True)
        data = SAMPLE.encode("utf-8")
        edits = []
        for record in [json.loads(x) for x in ir.stdout.splitlines()]:
            if record["kind"] in ("document", "gap"):
                continue
            segment = data[record["start"]:record["end"]].decode("utf-8")
            edits.append({"start": record["start"], "end": record["end"],
                          "replacement": segment, "expect": segment})
        result = self._apply(path, edits)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, SAMPLE)


if __name__ == "__main__":
    unittest.main()
