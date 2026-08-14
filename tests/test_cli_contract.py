"""
The shared CLI contract (issue #12).

Seven tools, one surface. These tests are deliberately written *across* the
tools rather than inside each: a contract that is only checked per tool is a
convention, and conventions drift. Adding an eighth tool should fail this file
until it joins in.

The exit codes are the part CI actually depends on:

    0   clean
    1   findings
    2   the tool could not run

The distinction that earns its own tests is 1 versus 2. A gate treating them
alike turns "your glossary has a syntax error" into "your prose is fine", and
the build goes green on a check that never ran. So every tool gets a
deliberately broken environment and has to say 2.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
SCRIPTS = ROOT / "scripts"

try:                       # tomllib is stdlib from 3.11
    import tomllib
    HAS_TOML = True
except ImportError:
    HAS_TOML = False

# On 3.10 a config file cannot be *read*, and mdtools refuses rather than
# ignoring it — quietly proceeding is how a tool ends up doing something the
# project did not ask for. That is the documented behaviour, so tests which
# need a config to take effect are skipped there rather than weakened here.
#
# The exit-code tests below are unaffected, because they assert 2 either way.
# Worth naming: on 3.10 they cannot tell "refused because malformed" from
# "refused because tomllib is missing". Both are 2, which is the contract, but
# only 3.11+ exercises the reason.
needs_toml = unittest.skipUnless(
    HAS_TOML, "reading mdtools.toml needs tomllib (Python 3.11+)")

# Every Python tool, with an invocation that should succeed on a clean file.
# prosevary is absent on purpose: it holds a model and a corpus, so it is not
# a check-shaped tool and the verbs would not mean the same thing.
TOOLS = {
    "mdquery": ["stats"],
    "mdterms": [],
    "mdlinks": [],
    "mdcheck": [],
}

# Tools that take --config. All of them: a project setting a tool ignores is
# worse than one it refuses.
CONFIG_TOOLS = tuple(TOOLS)

# Tools that report findings and so must honour --check.
CHECK_TOOLS = ("mdterms", "mdlinks", "mdcheck")

# Tools that can repair, and so must honour --diff and --fix.
FIX_TOOLS = ("mdterms", "mdlinks")

GLOSSARY = ("terms:\n"
            "  - term: Pandoc\n"
            "    forbidden: [pandoc]\n"
            "    case_sensitive: true\n")


class ContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "glossary_terms.yaml").write_text(GLOSSARY,
                                                      encoding="utf-8")

    def _env(self) -> dict:
        env = dict(os.environ)
        env["MDTOOLS_LIB"] = str(ROOT)
        env["MDFIX"] = str(MDFIX)
        return env

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _help(self, tool: str):
        # Bare tool, no subcommand: mdquery's subparsers each have their own
        # --help, and asking one of those tells you nothing about the tool.
        return subprocess.run([str(SCRIPTS / tool), "--help"],
                              capture_output=True, text=True, env=self._env(),
                              cwd=str(self.dir))

    def _run(self, tool: str, *args: str, cwd=None):
        return subprocess.run(
            [str(SCRIPTS / tool), *TOOLS.get(tool, []), *args],
            capture_output=True, text=True, env=self._env(),
            cwd=str(cwd or self.dir),
        )


class ExitCodeTests(ContractTestCase):
    def test_clean_input_exits_zero(self) -> None:
        self._write("clean.md", "# Title\n\nSome ordinary prose here.\n")
        for tool in TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "clean.md")
                self.assertEqual(result.returncode, 0,
                                 msg=f"{tool}: {result.stderr}")

    def test_findings_exit_one(self) -> None:
        self._write("bad.md",
                    "# Title\n\nWe use pandoc and link [x](#nope).\n")
        for tool in CHECK_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "bad.md")
                self.assertEqual(result.returncode, 1,
                                 msg=f"{tool}: {result.stdout}{result.stderr}")

    def test_a_missing_file_exits_two(self) -> None:
        for tool in TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "no-such-file.md")
                self.assertEqual(result.returncode, 2,
                                 msg=f"{tool}: {result.stdout}")

    def test_an_unreadable_config_exits_two(self) -> None:
        # The case the 1-versus-2 split exists for. The prose is fine; the
        # project's own settings are not, and saying "clean" would be a lie
        # that a CI gate believes.
        self._write("clean.md", "# Title\n\nOrdinary prose.\n")
        self._write("mdtools.toml", "this is not valid toml = = =\n")
        for tool in CONFIG_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "clean.md")
                self.assertEqual(result.returncode, 2,
                                 msg=f"{tool}: {result.stdout}{result.stderr}")
                self.assertTrue(result.stderr.strip())

    def test_an_unknown_config_setting_exits_two(self) -> None:
        self._write("clean.md", "# Title\n\nOrdinary prose.\n")
        self._write("mdtools.toml", "[mdtools]\nnot_a_setting = 1\n")
        for tool in CONFIG_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "clean.md")
                self.assertEqual(result.returncode, 2,
                                 msg=f"{tool}: {result.stdout}{result.stderr}")

    def test_a_named_config_that_is_missing_exits_two(self) -> None:
        # Not a silent fall back to discovery: a caller who names a config and
        # gets a different one has no way to notice.
        self._write("clean.md", "# Title\n\nOrdinary prose.\n")
        for tool in CONFIG_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "--config", "absent.toml", "clean.md")
                self.assertEqual(result.returncode, 2,
                                 msg=f"{tool}: {result.stdout}{result.stderr}")

    def test_a_broken_mdfix_exits_two(self) -> None:
        # An environment failure, not a finding about the prose.
        self._write("clean.md", "# Title\n\nOrdinary prose.\n")
        for tool in TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "--mdfix", "/nonexistent/mdfix",
                                   "clean.md")
                self.assertEqual(result.returncode, 2,
                                 msg=f"{tool}: {result.stdout}{result.stderr}")


class ConfigTests(ContractTestCase):
    def test_every_tool_accepts_config_and_mdfix(self) -> None:
        for tool in CONFIG_TOOLS:
            with self.subTest(tool=tool):
                help_text = self._help(tool).stdout
                self.assertIn("--config", help_text)
                self.assertIn("--mdfix", help_text)

    @needs_toml
    def test_a_named_config_is_read(self) -> None:
        # mdcheck's `suppress` is the observable one: with the setting in
        # force the finding disappears.
        self._write("a.md", "# T\n\n![missing](gone.png)\n")
        self.assertEqual(self._run("mdcheck", "a.md").returncode, 1)
        self._write("ci.toml", '[mdtools]\nsuppress = ["check.*", "links.*"]\n')
        result = self._run("mdcheck", "--config", "ci.toml", "a.md")
        self.assertEqual(result.returncode, 0, msg=result.stdout)

    @needs_toml
    def test_the_config_glossary_is_used(self) -> None:
        # mdterms would otherwise walk up and find the default glossary; the
        # setting has to beat discovery or it is decoration.
        other = self._write("elsewhere/terms.yaml",
                            "terms:\n  - term: Widget\n"
                            "    forbidden: [widgit]\n")
        self._write("a.md", "# T\n\nA widgit here.\n")
        self.assertEqual(self._run("mdterms", "a.md").returncode, 0)
        self._write("mdtools.toml",
                    f'[mdtools]\nglossary = "{other.relative_to(self.dir)}"\n')
        self.assertEqual(self._run("mdterms", "a.md").returncode, 1)

    @needs_toml
    def test_an_explicit_flag_beats_the_config(self) -> None:
        self._write("mdtools.toml", '[mdtools]\nglossary = "absent.yaml"\n')
        self._write("a.md", "# T\n\nWe use pandoc here.\n")
        result = self._run("mdterms", "--glossary", "glossary_terms.yaml",
                           "a.md")
        self.assertEqual(result.returncode, 1, msg=result.stderr)


class VerbTests(ContractTestCase):
    def test_check_is_accepted_by_every_reporting_tool(self) -> None:
        self._write("clean.md", "# Title\n\nOrdinary prose.\n")
        for tool in CHECK_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "--check", "clean.md")
                self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_check_is_the_default(self) -> None:
        self._write("a.md", "# T\n\nWe use pandoc here.\n")
        before = (self.dir / "a.md").read_bytes()
        for tool in CHECK_TOOLS:
            with self.subTest(tool=tool):
                self._run(tool, "a.md")
                self.assertEqual((self.dir / "a.md").read_bytes(), before)

    def test_diff_changes_nothing(self) -> None:
        self._write("a.md", "# Overview\n\nWe use pandoc: [x](#overvew).\n")
        before = (self.dir / "a.md").read_bytes()
        for tool in FIX_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "--diff", "a.md")
                self.assertEqual((self.dir / "a.md").read_bytes(), before)
                self.assertIn("@@", result.stdout)

    def test_fix_repairs_and_then_the_check_is_clean(self) -> None:
        self._write("a.md", "# Overview\n\nWe use pandoc: [x](#overvew).\n")
        for tool in FIX_TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual(self._run(tool, "--fix", "a.md").returncode, 0)
        text = (self.dir / "a.md").read_text(encoding="utf-8")
        self.assertIn("Pandoc", text)
        self.assertIn("#overview", text)

    def test_fix_leaves_what_it_cannot_repair(self) -> None:
        # Exit 1 after --fix means "still findings", not "the fix failed".
        self._write("a.md", "# T\n\nA `pandoc` code span.\n")
        result = self._run("mdterms", "--fix", "a.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("protected", result.stdout)
        self.assertIn("`pandoc`", (self.dir / "a.md").read_text("utf-8"))

    def test_fix_still_reports_overlapping_terms(self) -> None:
        # Two glossary entries that both forbid the same span: edits_for
        # drops the cluster, so nothing is applied. --fix must still exit 1.
        (self.dir / "glossary_terms.yaml").write_text(
            "terms:\n"
            "  - term: Foo\n    forbidden: [ABC]\n"
            "  - term: Bar\n    forbidden: [ABC]\n",
            encoding="utf-8")
        self._write("a.md", "# T\n\nSee ABC here.\n")
        result = self._run("mdterms", "--fix", "a.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ABC", (self.dir / "a.md").read_text("utf-8"))
        self.assertTrue(result.stdout.strip())

    def test_fix_prints_remaining_link_findings(self) -> None:
        self._write("a.md", "# T\n\nSee [x](#nope).\n")
        result = self._run("mdlinks", "--fix", "a.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no heading with anchor", result.stdout)

    def test_the_verbs_are_mutually_exclusive(self) -> None:
        self._write("a.md", "# T\n\nBody.\n")
        for tool in FIX_TOOLS:
            with self.subTest(tool=tool):
                result = self._run(tool, "--fix", "--diff", "a.md")
                self.assertEqual(result.returncode, 2)

    def test_fix_goes_through_the_applier(self) -> None:
        # The stub implements --emit-ir (so IR succeeds) and fails on
        # --apply-edits. If --fix wrote the file itself, this would still
        # change the document and exit 0.
        stub = self.dir / "mdfix-stub"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys\n"
            "if '--apply-edits' in sys.argv:\n"
            "    sys.stderr.write('stub: apply refused\\n')\n"
            "    sys.exit(2)\n"
            f"raise SystemExit(subprocess.call([{str(MDFIX)!r}] + sys.argv[1:]))\n",
            encoding="utf-8")
        stub.chmod(0o755)
        self._write("a.md", "# Overview\n\nSee [x](#overvew).\n")
        before = (self.dir / "a.md").read_bytes()
        result = self._run("mdlinks", "--fix", "--mdfix", str(stub), "a.md")
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertEqual((self.dir / "a.md").read_bytes(), before)
        self.assertIn("apply refused", result.stderr)


class DispatcherTests(ContractTestCase):
    """`mdtools <verb>` must not quietly disagree with the tool it dispatches."""

    def _mdtools(self, *args: str):
        return subprocess.run([str(SCRIPTS / "mdtools"), *args],
                              capture_output=True, text=True, env=self._env(),
                              cwd=str(self.dir))

    @needs_toml
    def test_a_named_config_reaches_the_tool(self) -> None:
        # The dispatcher used to inject its own discovered settings as
        # explicit flags, which beat --config in the tool's precedence order.
        # `mdtools terms --config ci.toml` would then run with the discovered
        # glossary and say nothing about it.
        self._write("elsewhere/terms.yaml",
                    "terms:\n  - term: Widget\n    forbidden: [widgit]\n")
        self._write("mdtools.toml", '[mdtools]\nglossary = "glossary_terms.yaml"\n')
        self._write("ci.toml", '[mdtools]\nglossary = "elsewhere/terms.yaml"\n')
        self._write("a.md", "# T\n\nA widgit here.\n")
        # Discovered config: the widget glossary is not in play, so clean.
        self.assertEqual(self._mdtools("terms", "a.md").returncode, 0)
        # Named config: it is.
        result = self._mdtools("terms", "--config", "ci.toml", "a.md")
        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)

    @needs_toml
    def test_config_can_inspect_a_named_file(self) -> None:
        self._write("ci.toml", '[mdtools]\nprofile = "canonical"\n')
        result = self._mdtools("config", "--config", "ci.toml")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"profile": "canonical"', result.stdout)

    def test_a_named_config_that_is_missing_exits_two(self) -> None:
        result = self._mdtools("config", "--config", "absent.toml")
        self.assertEqual(result.returncode, 2)

    def test_config_without_a_path_exits_two(self) -> None:
        result = self._mdtools("config", "--config")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--config", result.stderr)

    @needs_toml
    def test_a_broken_discovered_toml_does_not_block_named_config(self) -> None:
        # Discovery must not run when --config is given: a bad mdtools.toml
        # must not prevent inspecting a named good one.
        self._write("mdtools.toml", "this is not valid toml = = =\n")
        self._write("ci.toml", '[mdtools]\nprofile = "technical"\n')
        result = self._mdtools("config", "--config", "ci.toml")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"profile": "technical"', result.stdout)

    def test_the_verbs_reach_the_tools(self) -> None:
        self._write("a.md", "# Overview\n\nWe use pandoc: [x](#overvew).\n")
        before = (self.dir / "a.md").read_bytes()
        for verb in ("terms", "links"):
            with self.subTest(verb=verb):
                result = self._mdtools(verb, "--diff", "a.md")
                self.assertIn("@@", result.stdout)
                self.assertEqual((self.dir / "a.md").read_bytes(), before)


class MdfixHelpTests(unittest.TestCase):
    """
    Every flag mdfix accepts is a flag `-h` names (issue #105).

    A flag that works and is not listed is a flag nobody finds:
    `--no-arrow-aside` is the one feature the slow32-book copy had that the
    others did not — "notation arrows stay `→`" — so it is exactly what
    someone arriving from a technical manuscript goes looking for, and `-h`
    told them it did not exist.

    The other direction matters as much: a documented flag the parser rejects
    sends a reader to a usage error. mdfix parses its own arguments in C, so
    neither can be derived from the other and both have to be checked.

    Read out of `mdfix.rl` rather than the generated `.c` — the same source
    `check-sync` pins — and matched on the parser's own idiom, so a `"--"` in
    a scanner rule or a `--flag` in a comment is not mistaken for an option.
    """

    ACCEPTED = re.compile(r'strn?cmp\(argv\[argi\], "(--[a-z0-9-]+)"')
    LISTED = re.compile(r'(--[a-z0-9-]+)')

    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.source = (ROOT / "mdfix" / "mdfix.rl").read_text("utf-8")

    def _accepted(self) -> set:
        return set(self.ACCEPTED.findall(self.source))

    def _listed(self) -> set:
        result = subprocess.run([str(MDFIX), "-h"],
                                capture_output=True, text=True)
        return set(self.LISTED.findall(result.stdout + result.stderr))

    def test_the_parser_is_not_empty(self) -> None:
        # Both sets come from pattern matching, and two empty sets agree.
        accepted = self._accepted()
        self.assertGreater(len(accepted), 15, accepted)
        self.assertIn("--canonical", accepted)
        self.assertGreater(len(self._listed()), 15)

    def test_every_accepted_flag_is_documented(self) -> None:
        undocumented = sorted(self._accepted() - self._listed())
        self.assertEqual(undocumented, [],
                         f"mdfix accepts these but `-h` does not list them: "
                         f"{undocumented}")

    def test_every_documented_flag_is_accepted(self) -> None:
        # `--flag` here would be a promise the parser breaks.
        unparsed = sorted(self._listed() - self._accepted())
        self.assertEqual(unparsed, [],
                         f"`-h` lists these but mdfix does not accept them: "
                         f"{unparsed}")

    def test_the_flag_it_was_filed_for_works(self) -> None:
        # The scanner matches U+2192, not ASCII `->`. That character must
        # survive --editorial when this flag is set.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            source = "A note \u2192 an aside.\n"
            path.write_text(source, encoding="utf-8")
            out = Path(tmp) / "b.md"
            result = subprocess.run(
                [str(MDFIX), "-q", "--editorial", "--no-arrow-aside",
                 str(path), str(out)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(out.read_text(encoding="utf-8"), source)


class SourceContractTests(unittest.TestCase):
    """
    The contract lives in one module, and the tools use it.

    Exit codes spelled as bare integers are how a contract erodes: each site
    is individually correct and collectively unenforceable.
    """

    PACKAGES = ("mdterms", "mdlinks", "mdcheck", "mdquery")

    def test_every_tool_imports_the_contract(self) -> None:
        for package in self.PACKAGES:
            with self.subTest(package=package):
                source = (ROOT / package / "__main__.py").read_text("utf-8")
                self.assertIn("mdtools_cli.contract", source)

    def test_no_tool_spells_its_exit_codes_by_hand(self) -> None:
        for package in self.PACKAGES:
            with self.subTest(package=package):
                source = (ROOT / package / "__main__.py").read_text("utf-8")
                for literal in ("return 1\n", "return 2\n"):
                    self.assertNotIn(
                        literal, source,
                        f"{package} spells an exit code by hand; use "
                        f"OK / FINDINGS / USAGE from mdtools_cli.contract")


if __name__ == "__main__":
    unittest.main()
