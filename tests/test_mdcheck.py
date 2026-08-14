"""
mdcheck — repository-aware validation (issue #13).

Most of what #13 asks for already existed: mdlinks knows the link graph, mdfix
knows the dialect. mdcheck composes those and adds the checks nothing else
performs, then applies one policy. So the tests worth writing are about the
seams — that composition does not double-report, that suppression works, and
that the cross-file checks see what a single-file tool cannot.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mdcheck import __main__ as cli
from mdcheck.checks import discover, run
from mdquery.ir import raw_records

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"


class CheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _doc(self, text: str, name: str = "a.md") -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _rules(self, *paths) -> list:
        return sorted(f.rule for f in run(list(paths) or [self.dir]))

    def _run(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class OwnChecksTests(CheckTestCase):
    def test_image_without_alt_text(self) -> None:
        (self.dir / "i.png").write_bytes(b"x")
        self._doc("# T\n\nAn ![](i.png) image.\n")
        self.assertIn("check.image-alt", self._rules())

    def test_image_with_alt_text_is_silent(self) -> None:
        (self.dir / "i.png").write_bytes(b"x")
        self._doc("# T\n\nAn ![alt](i.png) image.\n")
        self.assertNotIn("check.image-alt", self._rules())

    def test_missing_asset(self) -> None:
        self._doc("# T\n\n![alt](nope.png)\n")
        self.assertIn("check.missing-asset", self._rules())

    def test_remote_image_is_not_fetched(self) -> None:
        self._doc("# T\n\n![alt](http://example.com/x.png)\n")
        self.assertNotIn("check.missing-asset", self._rules())

    def test_protocol_relative_image_is_not_a_local_asset(self) -> None:
        self._doc("# T\n\n![alt](//cdn.example/x.png)\n")
        self.assertNotIn("check.missing-asset", self._rules())

    def test_fence_without_a_language(self) -> None:
        self._doc("# T\n\n```\ncode\n```\n")
        self.assertIn("check.fence-language", self._rules())

    def test_fence_with_a_language_is_silent(self) -> None:
        self._doc("# T\n\n```python\ncode\n```\n")
        self.assertNotIn("check.fence-language", self._rules())

    def test_unterminated_fence(self) -> None:
        self._doc("# T\n\n```python\ncode\n")
        self.assertIn("check.unterminated-fence", self._rules())

    def test_unterminated_fence_is_reported_once(self) -> None:
        self._doc("# T\n\n```python\ncode\n")
        rules = [f.rule for f in run([self.dir])]
        self.assertEqual(rules.count("check.unterminated-fence"), 1)
        self.assertNotIn("dialect.fence.unterminated", rules)

    def test_duplicate_reference_definition(self) -> None:
        self._doc("# T\n\nSee [x][id].\n\n[id]: http://a\n[id]: http://b\n")
        self.assertIn("check.duplicate-definition", self._rules())

    def test_duplicate_definition_normalizes_whitespace(self) -> None:
        self._doc("# T\n\nSee [x][foo bar].\n\n[foo  bar]: http://a\n"
                  "[foo bar]: http://b\n")
        self.assertIn("check.duplicate-definition", self._rules())

    def test_lossy_constructs_are_warnings(self) -> None:
        self._doc("# T\n\n$$\nx = 1\n$$\n")
        self.assertIn("check.lossy-math", self._rules())


class HeadingSkipTests(CheckTestCase):
    """
    `check.heading-skip` (issue #99): a level reached without passing through
    the one above it.

    Nothing is broken by a skip — which is how two chapters of *An Agnostic's
    Guide to the Bible* carried one through six volumes and a full editorial
    pass. The consequence is that the sections render a size smaller than
    their peers and nest a level deeper in the navigation: the document says
    a structure the author did not mean.

    There is no safe repair — the heading might want to be `##`, or the file
    might want a `##` above it — so this is a warning and stays one.
    """

    def test_a_skipped_level_is_reported(self) -> None:
        self._doc("# Title\n\n### Section\n\ntext\n")
        self.assertIn("check.heading-skip", self._rules())

    def test_a_descent_of_one_is_silent(self) -> None:
        self._doc("# Title\n\n## Section\n\n### Sub\n\n#### Deeper\n")
        self.assertNotIn("check.heading-skip", self._rules())

    def test_climbing_back_up_any_distance_is_silent(self) -> None:
        # `####` then `#` is not a skip: the level above has been passed
        # through already, and every book does this at a chapter boundary.
        self._doc("# T\n\n## A\n\n### B\n\n#### C\n\n# Next\n")
        self.assertNotIn("check.heading-skip", self._rules())

    def test_a_file_may_start_at_any_level(self) -> None:
        # A chapter file that opens at `##` has nothing to descend from. The
        # rule is about the sequence, not about where it starts — otherwise
        # every included fragment in a book reports one.
        for opener in ("## Section\n\n### Sub\n", "### Deep\n\n#### Deeper\n"):
            with self.subTest(opener=opener):
                self._doc(opener)
                self.assertNotIn("check.heading-skip", self._rules())

    def test_the_report_names_both_levels(self) -> None:
        self._doc("# Title\n\n### Section\n")
        finding = next(f for f in run([self.dir])
                       if f.rule == "check.heading-skip")
        self.assertEqual(finding.severity, "warning")
        self.assertEqual(finding.line, 3)
        self.assertIn("h1", finding.message)
        self.assertIn("h3", finding.message)

    def test_it_fires_once_per_run_of_skipped_headings(self) -> None:
        # The corpus shape: a chapter that uses `###` where its peers use
        # `##`. After the first, the level is no longer a skip — so the file
        # reports once rather than eleven times.
        self._doc("# Title\n\n### A\n\n### B\n\n### C\n")
        rules = [f.rule for f in run([self.dir])]
        self.assertEqual(rules.count("check.heading-skip"), 1)

    def test_the_sequence_does_not_run_between_files(self) -> None:
        # `#` then `###` would skip if previous_level leaked across files.
        self._doc("# One\n", name="a.md")
        self._doc("### Three\n", name="b.md")
        self.assertNotIn("check.heading-skip", self._rules())

    def test_setext_headings_take_part(self) -> None:
        self._doc("Title\n=====\n\n### Section\n")
        self.assertIn("check.heading-skip", self._rules())

    def test_every_heading_record_is_the_outline(self) -> None:
        # Why the sequence needs no depth filter: the IR emits no heading
        # record nested inside a block quote or a list item, so there is no
        # nested heading to exclude. Pinning the fact rather than writing a
        # guard nothing can exercise — if the IR ever descends, this fails and
        # whoever changed it decides what the outline means.
        path = self._doc("# T\n\n> ### Quoted\n\n- item\n\n  ### Indented\n")
        levels = [(r.get("level"), r.get("depth"))
                  for r in raw_records([path]) if r["kind"] == "heading"]
        self.assertEqual(levels, [(1, None)])
        self.assertNotIn("check.heading-skip", self._rules())


class CompositionTests(CheckTestCase):
    def test_dialect_findings_are_included(self) -> None:
        self._doc("#Title\n\nBody.\n")
        self.assertIn("dialect.heading.atx-space", self._rules())

    def test_dialect_required_repairs_are_errors(self) -> None:
        self._doc("#Title\n\nBody.\n")
        finding = next(f for f in run([self.dir])
                       if f.rule == "dialect.heading.atx-space")
        self.assertEqual(finding.severity, "error")

    def test_dialect_warnings_stay_warnings(self) -> None:
        # fence.unterminated is warning in mdfix; we drop its dialect twin when
        # check.unterminated-fence is present, so use a bare dialect path via
        # suppression of the check rule to observe mapped severity.
        self._doc("# T\n\n```python\ncode\n")
        findings = run([self.dir], suppress=["check.unterminated-fence"])
        dialect = [f for f in findings if f.rule == "dialect.fence.unterminated"]
        self.assertEqual(len(dialect), 1)
        self.assertEqual(dialect[0].severity, "warning")

    def test_link_findings_are_included(self) -> None:
        self._doc("# T\n\nSee [x](#nope).\n")
        self.assertIn("links.broken-anchor", self._rules())

    def test_a_missing_image_is_reported_once(self) -> None:
        # Prefer check.missing-asset over links.missing-file at the same span.
        self._doc("# T\n\n![alt](nope.png)\n")
        rules = [f.rule for f in run([self.dir])]
        self.assertEqual(rules.count("check.missing-asset"), 1)
        self.assertNotIn("links.missing-file", rules)

    def test_a_missing_linked_file_still_reports(self) -> None:
        # The dedupe must not swallow genuine link findings.
        self._doc("# T\n\nSee [x](./nope.md).\n")
        self.assertIn("links.missing-file", self._rules())


class RepositoryTests(CheckTestCase):
    def test_anchor_collision_across_files(self) -> None:
        # Only visible repository-wide: within one file pandoc disambiguates
        # with -1 and -2 suffixes.
        self._doc("# Shared\n", "a.md")
        self._doc("# Shared\n", "b.md")
        self.assertIn("check.anchor-collision", self._rules())

    def test_no_collision_when_anchors_differ(self) -> None:
        self._doc("# One\n", "a.md")
        self._doc("# Two\n", "b.md")
        self.assertNotIn("check.anchor-collision", self._rules())

    def test_duplicate_headings_in_one_file_are_not_a_collision(self) -> None:
        self._doc("# Dup\n\n# Dup\n")
        self.assertNotIn("check.anchor-collision", self._rules())

    def test_a_directory_is_walked(self) -> None:
        self._doc("# A\n", "nested/deep/a.md")
        found = discover([self.dir])
        self.assertEqual([p.name for p in found], ["a.md"])

    def test_git_is_not_walked(self) -> None:
        self._doc("# A\n", ".git/objects/x.md")
        self.assertEqual(discover([self.dir]), [])


class SuppressionTests(CheckTestCase):
    def test_an_exact_rule_can_be_suppressed(self) -> None:
        (self.dir / "i.png").write_bytes(b"x")
        self._doc("# T\n\n![](i.png)\n")
        rules = [f.rule for f in run([self.dir], suppress=["check.image-alt"])]
        self.assertNotIn("check.image-alt", rules)

    def test_a_prefix_can_be_suppressed(self) -> None:
        self._doc("# T\n\nSee [x](#nope) and [y](./nope.md).\n")
        rules = [f.rule for f in run([self.dir], suppress=["links.*"])]
        self.assertFalse([r for r in rules if r.startswith("links.")])

    def test_suppress_from_mdtools_toml(self) -> None:
        import sys
        if sys.version_info < (3, 11):
            self.skipTest("tomllib needs Python 3.11")
        (self.dir / ".git").mkdir()
        (self.dir / "mdtools.toml").write_text(
            '[mdtools]\nsuppress = ["links.*"]\n', encoding="utf-8")
        self._doc("# T\n\nSee [x](#nope).\n")
        rc, out, err = self._run(str(self.dir / "a.md"))
        self.assertEqual(rc, 0, msg=err + out)
        self.assertNotIn("links.broken-anchor", out)

    def test_bad_config_exits_two(self) -> None:
        import sys
        if sys.version_info < (3, 11):
            self.skipTest("tomllib needs Python 3.11")
        (self.dir / ".git").mkdir()
        (self.dir / "mdtools.toml").write_text(
            '[mdtools]\nfrobnicate = true\n', encoding="utf-8")
        self._doc("# T\n")
        rc, _, err = self._run(str(self.dir / "a.md"))
        self.assertEqual(rc, 2)
        self.assertIn("frobnicate", err)


class CliTests(CheckTestCase):
    def test_errors_exit_one(self) -> None:
        self._doc("# T\n\nSee [x](#nope).\n")
        self.assertEqual(self._run(str(self.dir))[0], 1)

    def test_warnings_alone_exit_zero(self) -> None:
        self._doc("# T\n\n```\ncode\n```\n")
        self.assertEqual(self._run(str(self.dir))[0], 0)

    def test_warnings_flag_makes_them_fail(self) -> None:
        self._doc("# T\n\n```\ncode\n```\n")
        self.assertEqual(self._run("--warnings", str(self.dir))[0], 1)

    def test_missing_path_is_two(self) -> None:
        self.assertEqual(self._run(str(self.dir / "nope"))[0], 2)

    def test_diagnostics_are_jsonl(self) -> None:
        self._doc("# T\n\nSee [x](#nope).\n")
        rc, out, _ = self._run("--diagnostics", str(self.dir))
        self.assertEqual(rc, 1)
        for line in out.splitlines():
            self.assertEqual(json.loads(line)["kind"], "diagnostic")

    def test_sarif_is_wellformed(self) -> None:
        self._doc("# T\n\nSee [x](#nope).\n")
        rc, out, _ = self._run("--sarif", str(self.dir))
        self.assertEqual(rc, 1)
        doc = json.loads(out)
        self.assertEqual(doc["version"], "2.1.0")
        run_block = doc["runs"][0]
        self.assertEqual(run_block["tool"]["driver"]["name"], "mdcheck")
        result = run_block["results"][0]
        self.assertIn("ruleId", result)
        self.assertIn(result["level"], ("error", "warning"))
        location = result["locations"][0]["physicalLocation"]
        self.assertIn("uri", location["artifactLocation"])
        self.assertGreaterEqual(location["region"]["startLine"], 1)

    def test_suppression_from_the_command_line(self) -> None:
        self._doc("# T\n\nSee [x](#nope).\n")
        rc, _, _ = self._run("--suppress", "links.*", str(self.dir))
        self.assertEqual(rc, 0)

    def test_repository_documentation_has_no_errors(self) -> None:
        rc, out, _ = self._run(str(ROOT / "README.md"), str(ROOT / "docs"))
        self.assertEqual(rc, 0, msg=out)


class DispatcherTests(CheckTestCase):
    def test_mdtools_check_reaches_mdcheck(self) -> None:
        launcher = ROOT / "scripts" / "mdtools"
        if not launcher.is_file():
            self.skipTest("launcher not present")
        self._doc("# T\n\nSee [x](#nope).\n")
        result = subprocess.run([str(launcher), "check", str(self.dir)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("links.broken-anchor", result.stdout)


if __name__ == "__main__":
    unittest.main()
