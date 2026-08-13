"""
Front-matter schema validation (issue #13).

The schema lives in `mdtools.toml`. The needs are modest — is this key here,
is it the right kind of thing, is its value one of these — and a second file
in a second schema language to express them would be more machinery than the
question deserves.

Two properties do most of the work in these tests:

**Off unless configured.** A project with no `[frontmatter]` table is not
failing a check it never asked for. Nothing fires, not even "you should have
front matter".

**A schema is validated as strictly as it validates.** A typo in `requried`
would silently stop requiring anything, and the gate would pass every document
missing every field it was meant to have. So the config loader refuses what it
does not understand, and several tests below are about the schema rather than
about any document.
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

SCHEMA = """\
[mdtools.frontmatter]
unknown = "warn"

[mdtools.frontmatter.fields.title]
type = "string"
required = true

[mdtools.frontmatter.fields.date]
type = "date"

[mdtools.frontmatter.fields.status]
one_of = ["draft", "review", "final"]

[mdtools.frontmatter.fields.tags]
type = "list"

[mdtools.frontmatter.fields.draft]
type = "bool"

[mdtools.frontmatter.fields.order]
type = "number"
"""

needs = unittest.skipUnless(
    HAVE_TOML and HAVE_YAML,
    "front-matter schemas need tomllib (3.11+) and PyYAML")


@needs
class SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "mdtools.toml").write_text(SCHEMA, encoding="utf-8")

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
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
        return [r for r in rows if r["rule"].startswith("check.frontmatter")]

    def _rules(self, name: str) -> list:
        return [r["rule"] for r in self._rows(name)]


class ValidDocumentTests(SchemaTestCase):
    def test_a_conforming_document_is_clean(self) -> None:
        self._write("ok.md", "---\ntitle: Good\ndate: 2026-08-13\n"
                             "status: draft\ntags: [a, b]\ndraft: true\n"
                             "order: 3\n---\n\nBody.\n")
        result = self._run("ok.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    def test_optional_fields_may_be_absent(self) -> None:
        self._write("ok.md", "---\ntitle: Only the required one\n---\n\nBody.\n")
        self.assertEqual(self._rules("ok.md"), [])

    def test_a_quoted_scalar_is_still_a_string(self) -> None:
        self._write("ok.md", '---\ntitle: "Quoted"\n---\n\nBody.\n')
        self.assertEqual(self._rules("ok.md"), [])

    def test_a_quoted_iso_date_is_a_date(self) -> None:
        self._write("ok.md",
                    '---\ntitle: Fine\ndate: "2026-08-13"\n---\n\nBody.\n')
        self.assertEqual(self._rules("ok.md"), [])


class RequiredFieldTests(SchemaTestCase):
    def test_a_missing_required_field_is_an_error(self) -> None:
        self._write("bad.md", "---\ndate: 2026-08-13\n---\n\nBody.\n")
        rows = self._rows("bad.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.frontmatter-missing"])
        self.assertEqual(rows[0]["severity"], "error")
        self.assertIn("title", rows[0]["message"])

    def test_no_front_matter_at_all_is_also_missing(self) -> None:
        # Reporting only when a block exists would mean deleting the block
        # silently passes the gate, which is how a gate stops being one.
        self._write("bare.md", "Just prose, no front matter.\n")
        rows = self._rows("bare.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.frontmatter-missing"])
        self.assertIn("no front matter", rows[0]["message"])

    def test_no_front_matter_is_fine_when_nothing_is_required(self) -> None:
        (self.dir / "mdtools.toml").write_text(
            '[mdtools.frontmatter]\nunknown = "error"\n'
            '[mdtools.frontmatter.fields.title]\ntype = "string"\n',
            encoding="utf-8")
        self._write("bare.md", "Just prose.\n")
        self.assertEqual(self._rules("bare.md"), [])


class TypeTests(SchemaTestCase):
    CASES = (
        ("title: [a, b]", "title", "string", "list"),
        ("title: Fine\ndate: not-a-date", "date", "date", "string"),
        ("title: Fine\ntags: one", "tags", "list", "string"),
        ("title: Fine\ndraft: yes-ish", "draft", "bool", "string"),
        ("title: Fine\norder: third", "order", "number", "string"),
    )

    def test_each_declared_type_is_checked(self) -> None:
        for body, field, wanted, got in self.CASES:
            with self.subTest(field=field):
                self._write("t.md", f"---\n{body}\n---\n\nBody.\n")
                rows = [r for r in self._rows("t.md")
                        if r["rule"] == "check.frontmatter-type"]
                self.assertEqual(len(rows), 1, rows)
                self.assertIn(f"should be a {wanted}", rows[0]["message"])
                self.assertIn(f"not a {got}", rows[0]["message"])

    def test_a_bool_is_not_a_number(self) -> None:
        # YAML `true` is an int in Python's eyes if you are careless; the
        # number matcher excludes bool on purpose.
        self._write("t.md", "---\ntitle: Fine\norder: true\n---\n\nBody.\n")
        rows = [r for r in self._rows("t.md")
                if r["rule"] == "check.frontmatter-type"]
        self.assertEqual(len(rows), 1)
        self.assertIn("not a bool", rows[0]["message"])

    def test_an_untyped_field_accepts_anything(self) -> None:
        # `status` declares only one_of, so its type is `any`.
        self._write("t.md", "---\ntitle: Fine\nstatus: draft\n---\n\nBody.\n")
        self.assertEqual(self._rules("t.md"), [])


class ValueTests(SchemaTestCase):
    def test_a_value_outside_one_of_is_an_error(self) -> None:
        self._write("t.md", "---\ntitle: Fine\nstatus: published\n---\n\nB.\n")
        rows = self._rows("t.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.frontmatter-value"])
        self.assertIn("expected one of", rows[0]["message"])

    def test_a_wrong_type_does_not_also_report_a_bad_value(self) -> None:
        # One problem, one finding. Reporting both would make a single typo
        # look like two, which is how a report stops being read.
        (self.dir / "mdtools.toml").write_text(
            '[mdtools.frontmatter]\n'
            '[mdtools.frontmatter.fields.status]\n'
            'type = "string"\none_of = ["draft", "final"]\n',
            encoding="utf-8")
        self._write("t.md", "---\nstatus: [draft]\n---\n\nB.\n")
        self.assertEqual(self._rules("t.md"), ["check.frontmatter-type"])


class UnknownFieldTests(SchemaTestCase):
    def test_warn_reports_a_field_with_no_schema(self) -> None:
        self._write("t.md", "---\ntitle: Fine\ntypo: x\n---\n\nB.\n")
        rows = self._rows("t.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.frontmatter-unknown"])
        self.assertEqual(rows[0]["severity"], "warning")

    def test_error_raises_the_same_finding(self) -> None:
        (self.dir / "mdtools.toml").write_text(
            SCHEMA.replace('unknown = "warn"', 'unknown = "error"'),
            encoding="utf-8")
        self._write("t.md", "---\ntitle: Fine\ntypo: x\n---\n\nB.\n")
        rows = self._rows("t.md")
        self.assertEqual(rows[0]["severity"], "error")

    def test_mixed_constructed_keys_do_not_crash(self) -> None:
        # yaml.safe_load keeps 2024 as int and (YAML 1.1) on as bool.
        # Sorting those with a string extra used to TypeError.
        self._write("t.md",
                    "---\ntitle: Fine\n2024: recap\nauthor: me\n---\n\nB.\n")
        rows = [r for r in self._rows("t.md")
                if r["rule"] == "check.frontmatter-unknown"]
        messages = " ".join(r["message"] for r in rows)
        self.assertIn("2024", messages)
        self.assertIn("author", messages)

    def test_a_yaml_bool_key_is_looked_up_as_written(self) -> None:
        (self.dir / "mdtools.toml").write_text(
            '[mdtools.frontmatter.fields.on]\ntype = "bool"\nrequired = true\n',
            encoding="utf-8")
        self._write("ok.md", "---\non: true\n---\n\nB.\n")
        self.assertEqual(self._rules("ok.md"), [])

    def test_allow_is_the_default_and_says_nothing(self) -> None:
        (self.dir / "mdtools.toml").write_text(
            '[mdtools.frontmatter.fields.title]\ntype = "string"\n',
            encoding="utf-8")
        self._write("t.md", "---\ntitle: Fine\nanything: goes\n---\n\nB.\n")
        self.assertEqual(self._rules("t.md"), [])


class MalformedTests(SchemaTestCase):
    def test_unparseable_yaml_is_reported(self) -> None:
        self._write("t.md", "---\ntitle: [not, closed\n---\n\nB.\n")
        self.assertEqual(self._rules("t.md"), ["check.frontmatter-invalid"])

    def test_the_message_stays_on_one_line(self) -> None:
        # It goes on a diagnostics stream, where a newline inside a record
        # would corrupt the JSONL. PyYAML's own error is multi-line.
        self._write("t.md", "---\ntitle: [not, closed\n---\n\nB.\n")
        result = self._run("--diagnostics", "t.md")
        for line in result.stdout.splitlines():
            json.loads(line)                      # every line still parses

    def test_a_sequence_is_not_a_mapping(self) -> None:
        self._write("t.md", "---\n- one\n- two\n---\n\nB.\n")
        rows = self._rows("t.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.frontmatter-invalid"])
        self.assertIn("mapping", rows[0]["message"])

    def test_empty_front_matter_is_just_missing_fields(self) -> None:
        self._write("t.md", "---\n---\n\nB.\n")
        self.assertEqual(self._rules("t.md"), ["check.frontmatter-missing"])


class LocationTests(SchemaTestCase):
    def test_a_finding_points_at_its_own_key(self) -> None:
        # From PyYAML's marks, not from scanning for `^key:` — a key name
        # appearing inside a value would fool that.
        self._write("t.md", "---\ntitle: Fine\ndate: nope\n"
                            "status: published\n---\n\nB.\n")
        rows = {r["rule"]: r["line"] for r in self._rows("t.md")}
        self.assertEqual(rows["check.frontmatter-type"], 3)
        self.assertEqual(rows["check.frontmatter-value"], 4)

    def test_a_key_named_inside_a_value_does_not_move_the_line(self) -> None:
        self._write("t.md", "---\ntitle: 'mentions status: here'\n"
                            "status: published\n---\n\nB.\n")
        rows = self._rows("t.md")
        self.assertEqual([r["rule"] for r in rows],
                         ["check.frontmatter-value"])
        self.assertEqual(rows[0]["line"], 3)

    def test_the_span_is_the_front_matter_block(self) -> None:
        path = self._write("t.md", "---\ndate: nope\n---\n\nB.\n")
        data = path.read_bytes()
        row = self._rows("t.md")[0]
        self.assertTrue(
            data[row["start"]:row["end"]].startswith(b"---\n"), row)


class NoSchemaTests(SchemaTestCase):
    def test_without_a_schema_nothing_is_checked(self) -> None:
        (self.dir / "mdtools.toml").unlink()
        self._write("t.md", "---\nanything: at all\n---\n\nB.\n")
        result = self._run("t.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    def test_without_a_schema_a_bare_document_is_fine(self) -> None:
        (self.dir / "mdtools.toml").unlink()
        self._write("t.md", "Just prose.\n")
        self.assertEqual(self._run("t.md").returncode, 0)


class SuppressionAndOutputTests(SchemaTestCase):
    def test_the_rules_can_be_suppressed(self) -> None:
        self._write("t.md", "---\ndate: nope\n---\n\nB.\n")
        result = self._run("--suppress", "check.frontmatter-*", "t.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    def test_the_rules_reach_sarif(self) -> None:
        self._write("t.md", "---\ndate: nope\n---\n\nB.\n")
        doc = json.loads(self._run("--sarif", "t.md").stdout)
        rules = [r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]]
        self.assertIn("check.frontmatter-type", rules)

    def test_the_schema_shows_in_resolved_config(self) -> None:
        result = subprocess.run([str(SCRIPTS / "mdtools"), "config"],
                                capture_output=True, text=True,
                                env=self._env(), cwd=str(self.dir))
        resolved = json.loads(result.stdout)
        self.assertIn("title", resolved["frontmatter"]["fields"])

    def test_toml_date_literals_in_one_of_are_json_safe(self) -> None:
        (self.dir / "mdtools.toml").write_text(
            '[mdtools.frontmatter.fields.when]\n'
            'type = "date"\none_of = [2026-08-13]\n',
            encoding="utf-8")
        result = subprocess.run([str(SCRIPTS / "mdtools"), "config"],
                                capture_output=True, text=True,
                                env=self._env(), cwd=str(self.dir))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        resolved = json.loads(result.stdout)
        self.assertEqual(
            resolved["frontmatter"]["fields"]["when"]["one_of"],
            ["2026-08-13"])
        self._write("ok.md", "---\nwhen: 2026-08-13\n---\n\nB.\n")
        self.assertEqual(self._rules("ok.md"), [])

    def test_one_of_dates_match_quoted_and_unquoted(self) -> None:
        (self.dir / "mdtools.toml").write_text(
            '[mdtools.frontmatter.fields.when]\n'
            'type = "date"\none_of = ["2026-08-13"]\n',
            encoding="utf-8")
        self._write("a.md", "---\nwhen: 2026-08-13\n---\n\nB.\n")
        self.assertEqual(self._rules("a.md"), [])
        self._write("b.md", '---\nwhen: "2026-08-13"\n---\n\nB.\n')
        self.assertEqual(self._rules("b.md"), [])


class SchemaValidationTests(SchemaTestCase):
    """A schema nobody checked is worse than no schema."""

    BAD = (
        ('[mdtools.frontmatter]\nrequried = ["title"]\n', "requried"),
        ('[mdtools.frontmatter]\nunknown = "shout"\n', "unknown"),
        ('[mdtools.frontmatter.fields.title]\ntype = "str"\n', "type"),
        ('[mdtools.frontmatter.fields.title]\nrequired = "yes"\n', "required"),
        ('[mdtools.frontmatter.fields.title]\none_of = []\n', "one_of"),
        ('[mdtools.frontmatter.fields.title]\nrequied = true\n', "requied"),
        ('[mdtools]\nfrontmatter = "schema.json"\n', "frontmatter"),
    )

    def test_a_schema_it_does_not_understand_is_refused(self) -> None:
        self._write("t.md", "---\ntitle: Fine\n---\n\nB.\n")
        for text, needle in self.BAD:
            with self.subTest(schema=text.strip().splitlines()[-1]):
                (self.dir / "mdtools.toml").write_text(text, encoding="utf-8")
                result = self._run("t.md")
                # 2, not 1: the project's settings are unusable, which is not
                # a finding about the prose (docs/cli.md).
                self.assertEqual(result.returncode, 2, msg=result.stdout)
                self.assertIn(needle, result.stderr)


if __name__ == "__main__":
    unittest.main()
