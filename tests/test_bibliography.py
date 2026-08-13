"""
Unresolved citations (issue #13's last bullet).

Off unless a bibliography is named. A document with citations and no
bibliography is not making a mistake — it may be assembled later, or cited
into a system mdtools knows nothing about — so with no source nothing fires.

The distinction that shapes the whole module is **named-but-empty versus not
named**. `None` means do not check; an empty set means checked and nothing
matched. Collapsing the two would make every citation in every unconfigured
document an error, which is the fastest way to have the check switched off.

The same reasoning governs a bibliography that will not load: an unreadable
file *looks* empty, so reporting one unresolved citation per citation would
bury the finding that matters under noise. One finding names the file.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
    HAVE_TOML = True
except ImportError:
    HAVE_TOML = False

try:
    import yaml
    HAVE_YAML = True
except ImportError:                              # pragma: no cover
    HAVE_YAML = False

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
SCRIPTS = ROOT / "scripts"

BIB = """\
@article{smith2020, title = {A Paper}, year = 2020}
@book{jones1999, title = {A Book}}
@string{jrn = "Journal of Things"}
@comment{this is not an entry}
"""


@unittest.skipUnless(HAVE_YAML, "front matter needs PyYAML")
class BibliographyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "refs.bib").write_text(BIB, encoding="utf-8")

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
        return subprocess.run([str(SCRIPTS / "mdcheck"), *args],
                              capture_output=True, text=True, env=self._env(),
                              cwd=str(self.dir))

    def _rows(self, name: str) -> list:
        result = self._run("--diagnostics", name)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        return [r for r in rows
                if r["rule"] in ("check.unresolved-citation",
                                 "check.bibliography-unreadable")]


class OffByDefaultTests(BibliographyTestCase):
    def test_no_bibliography_means_no_check(self) -> None:
        self._write("a.md", "Cites [@ghost] and @phantom freely.\n")
        result = self._run("a.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    def test_front_matter_without_a_bibliography_key_is_still_off(self) -> None:
        self._write("a.md", "---\ntitle: X\n---\n\nCites [@ghost].\n")
        self.assertEqual(self._rows("a.md"), [])


class FrontMatterSourceTests(BibliographyTestCase):
    def test_a_named_bib_file_resolves(self) -> None:
        self._write("a.md", "---\nbibliography: refs.bib\n---\n\n"
                            "[@smith2020] and @jones1999 and [@ghost].\n")
        rows = self._rows("a.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.unresolved-citation"])
        self.assertIn("@ghost", rows[0]["message"])

    def test_a_list_of_files_is_merged(self) -> None:
        self._write("more.bib", "@misc{extra, title = {X}}\n")
        self._write("a.md", "---\nbibliography:\n  - refs.bib\n  - more.bib\n"
                            "---\n\n[@smith2020] [@extra] [@ghost].\n")
        rows = self._rows("a.md")
        self.assertEqual(len(rows), 1)
        self.assertIn("@ghost", rows[0]["message"])

    def test_inline_csl_references_resolve(self) -> None:
        self._write("a.md", "---\nreferences:\n  - id: inline1\n"
                            "    title: X\n---\n\n[@inline1] and [@missing].\n")
        rows = self._rows("a.md")
        self.assertEqual(len(rows), 1)
        self.assertIn("@missing", rows[0]["message"])

    def test_a_path_is_relative_to_the_document(self) -> None:
        self._write("sub/a.md", "---\nbibliography: ../refs.bib\n---\n\n"
                                "[@smith2020].\n")
        self.assertEqual(self._rows("sub/a.md"), [])


@unittest.skipUnless(HAVE_TOML, "project config needs tomllib (3.11+)")
class ProjectSourceTests(BibliographyTestCase):
    def test_the_config_supplies_a_default(self) -> None:
        self._write("mdtools.toml", '[mdtools]\nbibliography = "refs.bib"\n')
        self._write("a.md", "[@smith2020] and [@absent].\n")
        rows = self._rows("a.md")
        self.assertEqual(len(rows), 1)
        self.assertIn("@absent", rows[0]["message"])

    def test_front_matter_beats_the_project_default(self) -> None:
        # The document is saying what it cites against; the config is only a
        # default for documents that do not.
        #
        # Lives here rather than with the other front-matter sources because
        # it needs the config to be *readable*: on 3.10 there is no tomllib,
        # mdcheck refuses a config it cannot read, and the run exits 2 before
        # any citation is looked at.
        self._write("mdtools.toml", '[mdtools]\nbibliography = "refs.bib"\n')
        self._write("own.bib", "@misc{onlyhere, title = {X}}\n")
        self._write("a.md", "---\nbibliography: own.bib\n---\n\n"
                            "[@onlyhere] and [@smith2020].\n")
        rows = self._rows("a.md")
        self.assertEqual(len(rows), 1)
        self.assertIn("@smith2020", rows[0]["message"])

    def test_a_bad_setting_is_a_usage_error(self) -> None:
        self._write("mdtools.toml", "[mdtools]\nbibliography = 3\n")
        self._write("a.md", "[@a].\n")
        result = self._run("a.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("bibliography", result.stderr)


class FormatTests(BibliographyTestCase):
    def test_csl_json(self) -> None:
        self._write("refs.json", json.dumps([{"id": "j1", "title": "X"}]))
        self._write("a.md", "---\nbibliography: refs.json\n---\n\n"
                            "[@j1] and [@nope].\n")
        self.assertEqual(len(self._rows("a.md")), 1)

    def test_csl_yaml_with_a_references_wrapper(self) -> None:
        self._write("refs.yaml", "references:\n  - id: y1\n    title: X\n")
        self._write("a.md", "---\nbibliography: refs.yaml\n---\n\n"
                            "[@y1] and [@nope].\n")
        self.assertEqual(len(self._rows("a.md")), 1)

    def test_bibtex_non_entries_are_not_keys(self) -> None:
        # `@string` defines an abbreviation and `@comment` holds prose;
        # neither is something a citation can resolve to.
        self._write("a.md", "---\nbibliography: refs.bib\n---\n\n[@jrn].\n")
        rows = self._rows("a.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.unresolved-citation"])

    def test_an_unknown_extension_is_reported(self) -> None:
        self._write("refs.txt", "smith2020\n")
        self._write("a.md", "---\nbibliography: refs.txt\n---\n\n[@a].\n")
        rows = self._rows("a.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.bibliography-unreadable"])
        self.assertIn("unknown bibliography format", rows[0]["message"])


class UnreadableTests(BibliographyTestCase):
    """An unreadable bibliography looks exactly like an empty one."""

    def test_a_missing_file_is_one_finding_not_one_per_citation(self) -> None:
        self._write("a.md", "---\nbibliography: gone.bib\n---\n\n"
                            "[@a] [@b] [@c] [@d].\n")
        rows = self._rows("a.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.bibliography-unreadable"])
        self.assertIn("gone.bib", rows[0]["message"])

    def test_malformed_json_is_reported_not_treated_as_empty(self) -> None:
        self._write("refs.json", "{not json")
        self._write("a.md", "---\nbibliography: refs.json\n---\n\n[@a] [@b].\n")
        rows = self._rows("a.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.bibliography-unreadable"])

    def test_a_genuinely_empty_bibliography_does_report(self) -> None:
        # Named, readable and empty is a different thing from unreadable, and
        # every citation really is unresolved.
        self._write("empty.bib", "% nothing here\n")
        self._write("a.md", "---\nbibliography: empty.bib\n---\n\n[@a] [@b].\n")
        rows = self._rows("a.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.unresolved-citation"] * 2)


class LocationTests(BibliographyTestCase):
    def test_the_span_is_the_citation(self) -> None:
        path = self._write("a.md", "---\nbibliography: refs.bib\n---\n\n"
                                   "Text [@ghost] here.\n")
        data = path.read_bytes()
        row = self._rows("a.md")[0]
        self.assertEqual(data[row["start"]:row["end"]], b"@ghost")

    def test_the_line_is_the_citation_s(self) -> None:
        self._write("a.md", "---\nbibliography: refs.bib\n---\n\n"
                            "Line five.\n\n[@ghost] on seven.\n")
        self.assertEqual(self._rows("a.md")[0]["line"], 7)

    def test_a_citation_in_a_footnote_resolves_too(self) -> None:
        # The footnote-body scan in #88 is what makes this reachable.
        self._write("a.md", "---\nbibliography: refs.bib\n---\n\n"
                            "Text[^1]\n\n[^1]: See [@ghost].\n")
        self.assertEqual(len(self._rows("a.md")), 1)


class OutputTests(BibliographyTestCase):
    def test_the_rules_can_be_suppressed(self) -> None:
        self._write("a.md", "---\nbibliography: refs.bib\n---\n\n[@ghost].\n")
        result = self._run("--suppress", "check.unresolved-citation", "a.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    def test_the_rule_reaches_sarif(self) -> None:
        self._write("a.md", "---\nbibliography: refs.bib\n---\n\n[@ghost].\n")
        doc = json.loads(self._run("--sarif", "a.md").stdout)
        rules = [r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]]
        self.assertIn("check.unresolved-citation", rules)


if __name__ == "__main__":
    unittest.main()
