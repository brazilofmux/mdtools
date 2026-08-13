"""
First-use definitions and acronym introduction (issue #16).

    - first-use definitions
    - acronym introduction

These are one rule seen twice. An acronym is a term whose definition is the
words it stands for, and "define it the first time you use it" is the same
instruction either way. So the glossary gets one field, `expansion`, and the
check asks one question: at the first prose use of this term in this
document, are those words next to it?

Per *document*, not per repository. A reader reads one file, and a term
introduced in chapter 3 is not introduced for someone who opened chapter 7.
`--report` is the cross-file view, and it reports rather than judges.

Never auto-fixed. Rewriting a sentence to introduce a term is a wording
decision, and mdterms only makes changes that have exactly one right answer.
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
SCRIPTS = ROOT / "scripts"

GLOSSARY = """\
terms:
  - term: IR
    expansion: intermediate representation
  - term: SARIF
    expansion: Static Analysis Results Interchange Format
    exempt: ["CHANGELOG.md", "docs/legacy/*"]
  - term: Pandoc
    forbidden: [pandoc]
"""


class IntroductionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "glossary_terms.yaml").write_text(GLOSSARY,
                                                      encoding="utf-8")

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _env(self) -> dict:
        env = dict(os.environ)
        env["MDTOOLS_LIB"] = str(ROOT)
        env["MDFIX"] = str(MDFIX)
        return env

    def _run(self, *args: str):
        return subprocess.run([str(SCRIPTS / "mdterms"), *args],
                              capture_output=True, text=True, env=self._env(),
                              cwd=str(self.dir))

    def _rules(self, *args: str) -> list:
        result = self._run("--diagnostics", *args)
        return [json.loads(line) for line in result.stdout.splitlines()]


class AcronymTests(IntroductionTestCase):
    def test_an_unintroduced_term_is_reported(self) -> None:
        self._write("a.md", "# Doc\n\nThe IR is a stream of records.\n")
        rows = self._rules("a.md")
        self.assertEqual([r["rule"] for r in rows], ["terms.undefined-acronym"])
        self.assertEqual(rows[0]["severity"], "warning")

    def test_expansion_then_term_introduces_it(self) -> None:
        self._write("a.md", "# Doc\n\n"
                            "The intermediate representation (IR) is a "
                            "stream. Later the IR again.\n")
        self.assertEqual(self._rules("a.md"), [])

    def test_term_then_expansion_introduces_it(self) -> None:
        self._write("a.md", "# Doc\n\n"
                            "We emit IR (intermediate representation) here. "
                            "Then IR.\n")
        self.assertEqual(self._rules("a.md"), [])

    def test_a_term_inside_its_expansion_still_introduces(self) -> None:
        # Shape 1 when the expansion contains the term as a word.
        self._write("mine.yaml",
                    "terms:\n  - term: YAML\n"
                    "    expansion: YAML Ain't Markup Language\n")
        self._write("a.md", "# Doc\n\n"
                            "YAML Ain't Markup Language (YAML) is a "
                            "serialization format.\n")
        self.assertEqual(
            self._run("--glossary", "mine.yaml", "--diagnostics", "a.md")
            .stdout, "")

    def test_a_suffix_of_the_preceding_word_is_not_an_introduction(self) -> None:
        self._write("mine.yaml",
                    "terms:\n  - term: IR\n    expansion: representation\n")
        self._write("a.md", "# Doc\n\nmisrepresentation (IR) here.\n")
        rows = [json.loads(line) for line in
                self._run("--glossary", "mine.yaml", "--diagnostics", "a.md")
                .stdout.splitlines()]
        self.assertEqual([r["rule"] for r in rows], ["terms.undefined-acronym"])

    def test_the_expansion_may_be_capitalized_differently(self) -> None:
        # It may start a sentence. The term's own casing still follows
        # case_sensitive; only the expansion is compared loosely.
        self._write("a.md", "# Doc\n\n"
                            "Intermediate Representation (IR) is the "
                            "interface.\n")
        self.assertEqual(self._rules("a.md"), [])

    def test_introducing_later_is_still_a_finding(self) -> None:
        # The rule is *first* use. Introducing it in paragraph three does not
        # help the reader who met it in paragraph one.
        self._write("a.md", "# Doc\n\nThe IR is a stream.\n\n"
                            "The intermediate representation (IR) again.\n")
        rows = self._rules("a.md")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 3)

    def test_only_the_first_use_is_reported(self) -> None:
        # One finding per term per document, or a chapter using an acronym
        # forty times reports it forty times and buries everything else.
        self._write("a.md", "# Doc\n\n" + "The IR is a stream. " * 20 + "\n")
        self.assertEqual(len(self._rules("a.md")), 1)

    def test_a_term_in_an_inline_code_span_is_not_a_use(self) -> None:
        # `IR` in a code span is a literal, not the reader meeting the word.
        # This is the protected-span path, inside a prose record.
        self._write("a.md", "# Doc\n\nA `IR` code span here.\n")
        self.assertEqual(self._rules("a.md"), [])

    def test_a_term_in_a_fenced_block_is_not_a_use(self) -> None:
        # A different mechanism from the test above and worth its own case:
        # a fence is not a prose record at all, so the walk never sees it.
        # (The fence must open at the start of a line to be one — an earlier
        # version of this test put it mid-paragraph, where it is just text,
        # and the bare IR on the next line was a genuine prose use.)
        self._write("a.md", "# Doc\n\nSome prose.\n\n```\nIR\n```\n")
        self.assertEqual(self._rules("a.md"), [])

    def test_a_code_span_cannot_introduce_it_either(self) -> None:
        # The mirror of the test above, and the one that would break if
        # protected spans were skipped only for reporting.
        self._write("a.md", "# Doc\n\n"
                            "`intermediate representation (IR)` then IR.\n")
        rows = self._rules("a.md")
        self.assertEqual([r["rule"] for r in rows], ["terms.undefined-acronym"])

    def test_a_term_without_an_expansion_is_never_checked(self) -> None:
        # Pandoc has forbidden spellings but no expansion; it must not
        # acquire an introduction rule by accident.
        self._write("a.md", "# Doc\n\nWe use Pandoc for this.\n")
        self.assertEqual(self._rules("a.md"), [])

    def test_the_span_points_at_the_first_use(self) -> None:
        path = self._write("a.md", "# Doc\n\nThe IR is a stream.\n")
        row = self._rules("a.md")[0]
        self.assertEqual(path.read_bytes()[row["start"]:row["end"]], b"IR")

    def test_it_is_not_auto_fixed(self) -> None:
        # Rewriting the sentence is a wording decision. --fix must leave it,
        # and must still say so.
        path = self._write("a.md", "# Doc\n\nThe IR is a stream.\n")
        before = path.read_bytes()
        result = self._run("--fix", "a.md")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertIn("introduced", result.stdout)

    def test_a_word_boundary_is_respected(self) -> None:
        # IRQ is not IR.
        self._write("a.md", "# Doc\n\nThe IRQ line and IRs elsewhere.\n")
        self.assertEqual(self._rules("a.md"), [])


class ExemptionTests(IntroductionTestCase):
    """#16's domain-specific exceptions."""

    def test_an_exempt_file_is_skipped(self) -> None:
        self._write("CHANGELOG.md", "# Changes\n\nSARIF output added.\n")
        self.assertEqual(self._rules("CHANGELOG.md"), [])

    def test_a_name_pattern_matches_in_any_directory(self) -> None:
        # `CHANGELOG.md` with no separator is a name, so it holds wherever
        # the file lives — otherwise the pattern would silently stop working
        # the day someone passed `docs/CHANGELOG.md`.
        self._write("docs/CHANGELOG.md", "# Changes\n\nSARIF output added.\n")
        self.assertEqual(self._rules("docs/CHANGELOG.md"), [])

    def test_a_path_pattern_matches_only_that_path(self) -> None:
        self._write("docs/legacy/old.md", "# Old\n\nSARIF output.\n")
        self._write("docs/current/new.md", "# New\n\nSARIF output.\n")
        self.assertEqual(self._rules("docs/legacy/old.md"), [])
        self.assertEqual([r["rule"] for r in self._rules("docs/current/new.md")],
                         ["terms.undefined-acronym"])

    def test_a_name_star_does_not_cross_directories(self) -> None:
        self._write("mine.yaml",
                    "terms:\n  - term: SARIF\n"
                    "    expansion: Static Analysis Results Interchange Format\n"
                    '    exempt: ["draft*"]\n')
        self._write("draft/notes.md", "# Notes\n\nSARIF output.\n")
        result = self._run("--glossary", "mine.yaml", "--diagnostics",
                           "draft/notes.md")
        self.assertEqual([json.loads(line)["rule"]
                          for line in result.stdout.splitlines()],
                         ["terms.undefined-acronym"])

    def test_exemption_covers_forbidden_spellings_too(self) -> None:
        # An exemption is about the term, not about one rule: a changelog
        # quoting an old release note should not be corrected either.
        self._write("mine.yaml",
                    "terms:\n  - term: Pandoc\n    forbidden: [pandoc]\n"
                    '    exempt: ["CHANGELOG.md"]\n')
        self._write("CHANGELOG.md", "# Changes\n\nUses pandoc.\n")
        result = self._run("--glossary", "mine.yaml", "CHANGELOG.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)


class ReportTests(IntroductionTestCase):
    """#16's repository consistency report."""

    def test_it_shows_where_a_term_is_used_and_introduced(self) -> None:
        self._write("a.md", "# A\n\nThe IR is a stream.\n")
        self._write("b.md", "# B\n\nThe intermediate representation (IR).\n")
        rows = [json.loads(line) for line in
                self._run("--report", "--diagnostics", "a.md", "b.md")
                .stdout.splitlines()]
        by_term = {r["term"]: r for r in rows}
        self.assertEqual(by_term["IR"]["used_in"], ["a.md", "b.md"])
        self.assertEqual(by_term["IR"]["introduced_in"], ["b.md"])

    def test_it_reports_rather_than_judges(self) -> None:
        # Exit 0 even on an untidy corpus: it is a description, and a gate
        # that fires on every report is one nobody runs.
        self._write("a.md", "# A\n\nThe IR is a stream.\n")
        self.assertEqual(self._run("--report", "a.md").returncode, 0)

    def test_later_introduction_is_not_introduced_in(self) -> None:
        # Same first-use rule as scan(): a later expansion (TERM) does not
        # make the file look introduced in the report.
        self._write("a.md", "# A\n\nThe IR is a stream.\n\n"
                            "The intermediate representation (IR) again.\n")
        rows = [json.loads(line) for line in
                self._run("--report", "--diagnostics", "a.md")
                .stdout.splitlines()]
        ir = next(r for r in rows if r["term"] == "IR")
        self.assertEqual(ir["used_in"], ["a.md"])
        self.assertEqual(ir["introduced_in"], [])

    def test_it_refuses_to_be_combined_with_a_write_verb(self) -> None:
        # Checked before any output branch: further down, whichever came
        # first in the source would silently win and the other flag would
        # look accepted.
        self._write("a.md", "# A\n\nThe IR is a stream.\n")
        for other in ("--edits", "--fix", "--freeze", "--sarif", "--diff"):
            with self.subTest(flag=other):
                self.assertEqual(
                    self._run("--report", other, "a.md").returncode, 2)


class SarifTests(IntroductionTestCase):
    def test_sarif_is_well_formed(self) -> None:
        self._write("a.md", "# Doc\n\nThe IR is a stream.\n")
        result = self._run("--sarif", "a.md")
        self.assertEqual(result.returncode, 1)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["version"], "2.1.0")
        run = doc["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "mdterms")
        self.assertEqual([r["ruleId"] for r in run["results"]],
                         ["terms.undefined-acronym"])
        self.assertEqual(run["results"][0]["level"], "warning")

    def test_the_rule_index_points_at_the_right_rule(self) -> None:
        # ruleIndex into driver.rules is the part a SARIF viewer dereferences,
        # and an off-by-one there mislabels every finding.
        self._write("a.md", "# Doc\n\nThe IR is a stream and pandoc too.\n")
        doc = json.loads(self._run("--sarif", "a.md").stdout)
        run = doc["runs"][0]
        rules = [r["id"] for r in run["tool"]["driver"]["rules"]]
        for result in run["results"]:
            self.assertEqual(rules[result["ruleIndex"]], result["ruleId"])

    def test_both_tools_produce_the_same_shape(self) -> None:
        # They share one implementation now; this is what says so.
        self._write("a.md", "# Doc\n\nThe IR is a stream.\n")
        terms_doc = json.loads(self._run("--sarif", "a.md").stdout)
        check = subprocess.run(
            [str(SCRIPTS / "mdcheck"), "--sarif", "a.md"],
            capture_output=True, text=True, env=self._env(), cwd=str(self.dir))
        check_doc = json.loads(check.stdout)
        self.assertEqual(terms_doc["$schema"], check_doc["$schema"])
        self.assertEqual(sorted(terms_doc["runs"][0]["tool"]["driver"]),
                         sorted(check_doc["runs"][0]["tool"]["driver"]))


class GlossaryValidationTests(IntroductionTestCase):
    def test_a_self_expanding_term_is_refused(self) -> None:
        self._write("mine.yaml", "terms:\n  - term: IR\n    expansion: IR\n")
        self._write("a.md", "# A\n\nThe IR.\n")
        result = self._run("--glossary", "mine.yaml", "a.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("expands to itself", result.stderr)

    def test_a_self_expansion_is_refused_case_insensitively(self) -> None:
        self._write("mine.yaml", "terms:\n  - term: IR\n    expansion: ir\n")
        self._write("a.md", "# A\n\nThe IR.\n")
        self.assertEqual(
            self._run("--glossary", "mine.yaml", "a.md").returncode, 2)

    def test_an_empty_exempt_pattern_is_refused(self) -> None:
        self._write("mine.yaml",
                    'terms:\n  - term: IR\n    exempt: [""]\n')
        self._write("a.md", "# A\n\nThe IR.\n")
        self.assertEqual(
            self._run("--glossary", "mine.yaml", "a.md").returncode, 2)


if __name__ == "__main__":
    unittest.main()
