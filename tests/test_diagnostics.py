"""
Diagnostics (issue #12, architecture ID.1–ID.3).

    ID.1  located    — path, byte span, line
    ID.2  identified — a stable rule id, so a consumer never matches English
    ID.3  machine-readable — JSONL on a stream separate from the document

The third is the one with a sharp edge. `--emit-ir` and `--apply-edits` both
write the document to stdout, so diagnostics take stderr — and they have to
own it, because a human progress line interleaved with the JSONL makes the
stream unparseable. A consumer cannot skip what it cannot recognize.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
SOURCE = ROOT / "mdfix" / "mdfix.rl"

# Rule ids are API: a consumer gates or suppresses on them. Pinned so that
# renaming one is a deliberate change rather than a silent break.
EXPECTED_RULES = {
    "list.bullet-style", "list.blank-before", "list.blank-after",
    "heading.emphasis", "whitespace.trailing", "emphasis.bold-colon",
    "punct.arrow-aside", "blockquote.space", "chicago.emdash-spacing",
    "chicago.ellipsis", "chicago.sentence-space",
    "chicago.space-before-punct", "chicago.space-after-punct",
    "chicago.quote-terminal-punct", "chicago.abbrev-comma",
    "chicago.etal-period", "footnote.ref-format", "footnote.def-format",
    "heading.atx-space", "heading.canonical", "fence.canonical",
    "link.autolink-bare", "heading.scrivener-split",
    # lint-only, not fix categories
    "chicago.number-style", "chicago.serial-comma",
}


class DiagnosticsTestCase(unittest.TestCase):
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

    def _file(self, text: str, name: str = "d.md") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _diagnose(self, text: str, *flags: str) -> tuple[list[dict], str]:
        path = self._file(text)
        result = subprocess.run(
            [str(MDFIX), "-n", "--diagnostics", *flags, str(path)],
            capture_output=True, text=True,
        )
        rows = [json.loads(line) for line in result.stderr.splitlines()]
        return rows, result.stdout


class StreamTests(DiagnosticsTestCase):
    """ID.3."""

    def test_stderr_is_only_jsonl(self) -> None:
        # Every line must parse. Anything human on this stream — a progress
        # line, a summary — would make the whole thing unusable.
        path = self._file("#Title\n\nIntro:\n- one\nAfter.\n")
        result = subprocess.run(
            [str(MDFIX), "-n", "--diagnostics", "--canonical", str(path)],
            capture_output=True, text=True,
        )
        self.assertTrue(result.stderr.strip())
        for line in result.stderr.splitlines():
            self.assertIsInstance(json.loads(line), dict)

    def test_verbose_does_not_pollute_the_stream(self) -> None:
        # -v normally prints a line per fix and a "Read N lines" banner.
        path = self._file("#Title\n\nIntro:\n- one\nAfter.\n")
        result = subprocess.run(
            [str(MDFIX), "-n", "-v", "--diagnostics", "--canonical", str(path)],
            capture_output=True, text=True,
        )
        for line in result.stderr.splitlines():
            json.loads(line)

    def test_document_stream_stays_clean(self) -> None:
        # --emit-ir writes the document to stdout; a diagnostic mixed in
        # would corrupt it.
        path = self._file("#Title\n\nBody.\n")
        result = subprocess.run(
            [str(MDFIX), "--diagnostics", "--emit-ir", str(path)],
            capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            self.assertNotEqual(json.loads(line).get("kind"), "diagnostic")

    def test_applier_stream_stays_clean(self) -> None:
        path = self._file("# T\n\nThe quick fox.\n")
        result = subprocess.run(
            [str(MDFIX), "--diagnostics", "--apply-edits", str(path)],
            input="", capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout, "# T\n\nThe quick fox.\n")


class LocatedTests(DiagnosticsTestCase):
    """ID.1."""

    def test_every_diagnostic_carries_a_location(self) -> None:
        rows, _ = self._diagnose("#Title\n\nIntro:\n- one\nAfter.\n",
                                 "--canonical")
        self.assertTrue(rows)
        for row in rows:
            for field in ("path", "line", "start", "end"):
                self.assertIn(field, row)
            self.assertLessEqual(row["start"], row["end"])

    def test_the_span_slices_the_reported_line(self) -> None:
        text = "#Title\n\nIntro:\n- one\nAfter.\n"
        data = text.encode("utf-8")
        rows, _ = self._diagnose(text, "--canonical")
        by_rule = {r["rule"]: r for r in rows}
        self.assertEqual(data[by_rule["heading.atx-space"]["start"]:
                              by_rule["heading.atx-space"]["end"]], b"#Title")

    def test_the_path_is_the_file(self) -> None:
        rows, _ = self._diagnose("#Title\n", "--canonical")
        self.assertTrue(all(r["path"].endswith("d.md") for r in rows))


class IdentifiedTests(DiagnosticsTestCase):
    """ID.2."""

    def test_rules_come_from_the_pinned_set(self) -> None:
        text = ("#Title\n\nIntro:\n- one\nAfter.\n\n"
                "He paused . . . then spoke.\n\n"
                "We bought apples, oranges and pears.\n")
        rows, _ = self._diagnose(text, "--canonical", "--serial-comma-lint",
                                 "--chicago-number-lint")
        found = {r["rule"] for r in rows}
        self.assertTrue(found)
        unknown = found - EXPECTED_RULES
        self.assertFalse(unknown, f"unpinned rule ids: {unknown}")

    def test_severity_distinguishes_fixes_from_warnings(self) -> None:
        rows, _ = self._diagnose(
            "We bought apples, oranges and pears.\n", "--serial-comma-lint")
        self.assertEqual([r["severity"] for r in rows], ["warning"])
        rows, _ = self._diagnose("#Title\n", "--canonical")
        self.assertTrue(all(r["severity"] == "fix" for r in rows))

    def test_a_consumer_can_gate_on_one_rule(self) -> None:
        # The point of ID.2: select without matching English.
        text = "#Title\n\nIntro:\n- one\nAfter.\n"
        rows, _ = self._diagnose(text, "--canonical")
        headings = [r for r in rows if r["rule"].startswith("heading.")]
        self.assertEqual(len(headings), 1)


class SourceConsistencyTests(unittest.TestCase):
    """
    fix_rules[], fix_labels[] and enum fixcat are three parallel arrays in C,
    and nothing in the language keeps them in step. A mismatch would report
    the wrong rule id for a fix, which is worse than reporting none.
    """

    def _block(self, name: str) -> list[str]:
        text = SOURCE.read_text(encoding="utf-8")
        body = re.search(rf"{name}\[\] = \{{(.*?)\n\}};", text, re.S)
        self.assertIsNotNone(body, f"{name} not found")
        return re.findall(r'"((?:[^"\\]|\\.)*)"', body.group(1))

    def test_rules_and_labels_are_the_same_length(self) -> None:
        rules = self._block("static const char \\*fix_rules")
        labels = self._block("static const char \\*fix_labels")
        self.assertEqual(len(rules), len(labels))

    def test_rule_count_matches_the_enum(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        enum = re.search(r"enum fixcat \{(.*?)\n\};", text, re.S).group(1)
        members = [m for m in re.findall(r"\n\s+(FIX_\w+)", enum)]
        self.assertEqual(len(self._block("static const char \\*fix_rules")),
                         len(members))

    def test_every_rule_id_is_pinned(self) -> None:
        for rule in self._block("static const char \\*fix_rules"):
            with self.subTest(rule=rule):
                self.assertIn(rule, EXPECTED_RULES)

    def test_rule_ids_look_like_identifiers(self) -> None:
        for rule in self._block("static const char \\*fix_rules"):
            with self.subTest(rule=rule):
                self.assertRegex(rule, r"^[a-z]+(?:\.[a-z0-9-]+)+$")


if __name__ == "__main__":
    unittest.main()
