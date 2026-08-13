"""
The edit model and `--diff` (issue #12, architecture I4.2).

#12 asks an edit to carry "byte start/end, replacement, rule ID,
confidence/severity, explanation". The first three shipped with the applier;
this is the rest, plus the thing that makes them worth carrying — a preview
that shows a reviewer the producer's own judgement instead of a byte range.

mdfix never *acts* on severity or confidence. A "low" edit is applied exactly
like a "high" one, because sending it was the producer's decision and second-
guessing it here would put the same policy in two places. But the values are
validated (I4.2: accepted input is checked, not trusted), because a field that
is silently dropped when misspelled is worse than one that does not exist —
a review step filtering on `confidence` would go on passing everything.
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


class EditModelTestCase(unittest.TestCase):
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

    def _file(self, text: str, name: str = "e.md") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _stream(self, path: Path, *edits: dict) -> str:
        head = {"kind": "edits", "schema": "mdtools-edits-1",
                "source": str(path), "bytes": path.stat().st_size}
        return "\n".join([json.dumps(head)] + [json.dumps(e) for e in edits]) + "\n"

    def _run(self, path: Path, *edits: dict, flags=("--diff",)):
        return subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", *flags, str(path)],
            input=self._stream(path, *edits), capture_output=True, text=True,
        )


class VocabularyTests(EditModelTestCase):
    """I4.2 for the new fields."""

    def test_the_documented_values_are_accepted(self) -> None:
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        for severity in ("error", "warning", "info"):
            for confidence in ("high", "medium", "low"):
                with self.subTest(severity=severity, confidence=confidence):
                    result = self._run(path, {
                        "start": i, "end": i + 5, "replacement": "slow",
                        "rule": "t.t", "severity": severity,
                        "confidence": confidence, "explanation": "why",
                    })
                    self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_an_unknown_confidence_is_refused(self) -> None:
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        result = self._run(path, {"start": i, "end": i + 5,
                                  "replacement": "slow",
                                  "confidence": "certain"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("confidence", result.stderr)
        self.assertIn("high, medium, low", result.stderr)

    def test_an_unknown_severity_is_refused(self) -> None:
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        result = self._run(path, {"start": i, "end": i + 5,
                                  "replacement": "slow",
                                  "severity": "critical"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("error, warning, info", result.stderr)

    def test_a_refused_edit_writes_nothing(self) -> None:
        # The point of validating before splicing: a bad field costs an error
        # message, never a half-applied file.
        path = self._file("# T\n\nThe quick fox.\n")
        before = path.read_bytes()
        i = before.index(b"quick")
        result = self._run(path, {"start": i, "end": i + 5,
                                  "replacement": "slow",
                                  "confidence": "certain"},
                           flags=("-i",))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(path.read_bytes(), before)

    def test_the_fields_stay_optional(self) -> None:
        # Every existing producer omits them, and must keep working.
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        result = self._run(path, {"start": i, "end": i + 5,
                                  "replacement": "slow"})
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_an_explanation_must_be_valid_utf8(self) -> None:
        # It reaches a terminal and a diff; the same rule as every other
        # accepted string (I1.1).
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        head = json.dumps({"kind": "edits", "schema": "mdtools-edits-1",
                           "source": str(path),
                           "bytes": path.stat().st_size}).encode()
        # A raw 0xFF, which is never a valid UTF-8 byte. Building this as a
        # str and encoding it would produce U+00FF — perfectly valid, and the
        # test would pass while checking nothing.
        edit = (b'{"start":%d,"end":%d,"replacement":"slow",'
                b'"explanation":"bad \xff byte"}' % (i, i + 5))
        result = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", "--diff", str(path)],
            input=head + b"\n" + edit + b"\n", capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"UTF-8", result.stderr)


class DiffTests(EditModelTestCase):
    @staticmethod
    def _hunks(diff: str) -> int:
        # Count headers, not occurrences: `@@ file:3 @@` has two of them.
        return sum(1 for line in diff.splitlines() if line.startswith("@@"))

    def _diff(self, path: Path, *edits: dict) -> str:
        result = self._run(path, *edits)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return result.stdout

    def test_nothing_is_written(self) -> None:
        path = self._file("# T\n\nThe quick fox.\n")
        before = path.read_bytes()
        i = before.index(b"quick")
        self._diff(path, {"start": i, "end": i + 5, "replacement": "slow"})
        self.assertEqual(path.read_bytes(), before)

    def test_the_hunk_shows_before_and_after(self) -> None:
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        diff = self._diff(path, {"start": i, "end": i + 5,
                                 "replacement": "slow"})
        self.assertIn("- The quick fox.", diff)
        self.assertIn("+ The slow fox.", diff)

    def test_the_hunk_names_the_file_and_line(self) -> None:
        path = self._file("one\ntwo\nthree\n")
        i = path.read_bytes().index(b"three")
        diff = self._diff(path, {"start": i, "end": i + 5,
                                 "replacement": "3"})
        self.assertIn(f"@@ {path}:3 @@", diff)

    def test_the_rule_and_judgement_are_shown(self) -> None:
        # The whole reason this is not `git diff`: bytes are visible either
        # way, but only here does the reviewer learn which rule claimed them.
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        diff = self._diff(path, {
            "start": i, "end": i + 5, "replacement": "slow",
            "rule": "prosevary.vary", "severity": "info",
            "confidence": "low", "explanation": "a slower animal",
        })
        self.assertIn("prosevary.vary", diff)
        self.assertIn("[info]", diff)
        self.assertIn("confidence: low", diff)
        self.assertIn("a slower animal", diff)

    def test_two_edits_on_one_line_are_one_hunk(self) -> None:
        # Printing the line once per edit would show a state that never
        # exists: the line with only half the changes applied.
        path = self._file("alpha and beta\n")
        data = path.read_bytes()
        a, b = data.index(b"alpha"), data.index(b"beta")
        diff = self._diff(path,
                          {"start": a, "end": a + 5, "replacement": "ALPHA"},
                          {"start": b, "end": b + 4, "replacement": "BETA"})
        self.assertEqual(self._hunks(diff), 1)
        self.assertIn("+ ALPHA and BETA", diff)
        self.assertNotIn("+ ALPHA and beta", diff)

    def test_distant_edits_are_separate_hunks(self) -> None:
        path = self._file("one\n\n\n\nfive\n")
        data = path.read_bytes()
        a, b = data.index(b"one"), data.index(b"five")
        diff = self._diff(path,
                          {"start": a, "end": a + 3, "replacement": "1"},
                          {"start": b, "end": b + 4, "replacement": "5"})
        self.assertEqual(self._hunks(diff), 2)

    def test_whole_line_edit_does_not_pull_the_next_line(self) -> None:
        # Half-open end on the next line's first byte must still bound the
        # hunk to the line that was actually included.
        path = self._file("one\ntwo\n")
        data = path.read_bytes()
        # Replace "one\n" entirely: [0, 4).
        diff = self._diff(path, {"start": 0, "end": 4, "replacement": "ONE\n"})
        self.assertEqual(self._hunks(diff), 1)
        self.assertIn("- one", diff)
        self.assertIn("+ ONE", diff)
        self.assertNotIn("- two", diff)
        self.assertNotIn("+ two", diff)

    def test_consecutive_line_edits_are_separate_hunks(self) -> None:
        path = self._file("one\ntwo\n")
        data = path.read_bytes()
        # Whole first line including newline, then second line body.
        diff = self._diff(path,
                          {"start": 0, "end": 4, "replacement": "ONE\n"},
                          {"start": 4, "end": 7, "replacement": "TWO"})
        self.assertEqual(self._hunks(diff), 2)
        self.assertIn("@@ ", diff)
        self.assertIn(":1 @@", diff)
        self.assertIn(":2 @@", diff)

    def test_diff_requires_apply_edits(self) -> None:
        path = self._file("# T\n")
        result = subprocess.run(
            [str(MDFIX), "-q", "--diff", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("--apply-edits", result.stderr)

    def test_a_missing_final_newline_is_marked(self) -> None:
        path = self._file("one\ntwo")
        i = path.read_bytes().index(b"two")
        diff = self._diff(path, {"start": i, "end": i + 3,
                                 "replacement": "TWO"})
        self.assertEqual(diff.count("\\ No newline at end of file"), 2)

    def test_a_multiline_replacement_shows_every_line(self) -> None:
        path = self._file("alpha beta\ngamma\n")
        diff = self._diff(path, {"start": 0, "end": 10,
                                 "replacement": "alpha\n\nbeta"})
        self.assertIn("- alpha beta", diff)
        self.assertIn("+ alpha", diff)
        self.assertIn("+ beta", diff)

    def test_an_empty_edit_list_produces_no_hunks(self) -> None:
        path = self._file("# T\n\nBody.\n")
        result = self._run(path)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "")

    def test_the_diff_never_shows_a_refused_change(self) -> None:
        # --diff runs after the I4.3 check, so a preview cannot promise
        # something the applier would then refuse to do.
        path = self._file("# T\n\nIntro:\n\n- one\n")
        data = path.read_bytes()
        i = data.index(b"Intro:\n\n- one")
        result = self._run(path, {"start": i, "end": i + len(b"Intro:\n\n- one"),
                                  "replacement": "Intro:\n- one"})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("I4.3", result.stderr)

    def test_the_document_stream_is_not_used(self) -> None:
        # Without --diff, stdout is the spliced document. With it, stdout is
        # the diff and no document is emitted anywhere.
        path = self._file("# T\n\nThe quick fox.\n")
        i = path.read_bytes().index(b"quick")
        edit = {"start": i, "end": i + 5, "replacement": "slow"}
        self.assertIn("The slow fox.",
                      self._run(path, edit, flags=()).stdout)
        self.assertNotIn("# T\n\nThe slow fox.\n", self._diff(path, edit))


class ProducerTests(EditModelTestCase):
    """The fields are only worth having if the tools that emit edits set them."""

    def _env(self) -> dict:
        # Inherit rather than replace: a hand-built PATH loses whichever
        # interpreter has PyYAML, and the failure reads like a glossary bug.
        env = dict(os.environ)
        env["MDTOOLS_LIB"] = str(ROOT)
        env["MDFIX"] = str(MDFIX)
        return env

    def _edits(self, argv, cwd=None) -> list:
        result = subprocess.run(
            argv, capture_output=True, text=True, cwd=cwd, env=self._env(),
        )
        self.assertNotEqual(result.returncode, 2, msg=result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        return [r for r in rows if r.get("kind") != "edits"]

    def test_mdlinks_reports_an_exact_match_as_high(self) -> None:
        path = self._file("# Installation Guide\n\n"
                          "See [x](#Installation-Guide).\n", "a.md")
        edits = self._edits([str(ROOT / "scripts" / "mdlinks"),
                             "--edits", str(path)])
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["confidence"], "high")
        self.assertEqual(edits[0]["severity"], "error")
        self.assertTrue(edits[0]["explanation"])

    def test_mdlinks_reports_a_fuzzy_match_as_medium(self) -> None:
        # The distinction the field exists for: an identifier that matched is
        # not the same kind of answer as a nearest neighbour.
        path = self._file("# Configuration Reference\n\n"
                          "See [x](#configuration-refrence).\n", "a.md")
        edits = self._edits([str(ROOT / "scripts" / "mdlinks"),
                             "--edits", str(path)])
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["confidence"], "medium")

    def test_mdterms_edits_carry_the_model(self) -> None:
        (self.dir / "glossary_terms.yaml").write_text(
            "terms:\n  - term: Pandoc\n    forbidden: [pandoc]\n"
            "    case_sensitive: true\n", encoding="utf-8")
        self._file("# T\n\nWe use pandoc here.\n", "a.md")
        edits = self._edits([str(ROOT / "scripts" / "mdterms"),
                             "--edits", "a.md"], cwd=self.dir)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["confidence"], "high")
        self.assertEqual(edits[0]["severity"], "warning")

    def test_producer_output_survives_the_applier(self) -> None:
        # The end-to-end contract: what a producer writes, the applier takes.
        # Cheap, and it is what would break first if a vocabulary drifted.
        path = self._file("# Installation Guide\n\n"
                          "See [x](#Installation-Guide).\n", "a.md")
        produced = subprocess.run(
            [str(ROOT / "scripts" / "mdlinks"), "--edits", str(path)],
            capture_output=True, text=True, env=self._env(),
        ).stdout
        applied = subprocess.run(
            [str(MDFIX), "-q", "--apply-edits", "-i", str(path)],
            input=produced, capture_output=True, text=True,
        )
        self.assertEqual(applied.returncode, 0, msg=applied.stderr)
        self.assertIn("#installation-guide",
                      path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
