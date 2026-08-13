"""
The `mdtools` dispatcher and project configuration (issue #17).

Two things are being checked. That every verb reaches the same code the
standalone command does — a dispatcher that drifts from the tools it fronts is
worse than no dispatcher. And that configuration is *discovered* the same way
everywhere: walking up from the input, so running against another repository
picks up that repository's settings rather than the caller's.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mdtools_cli import __main__ as cli
from mdtools_cli.config import ConfigError, fix_flags, find_root, load

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
HAS_TOML = sys.version_info >= (3, 11)


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / ".git").mkdir()

    def _run(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def _doc(self, text: str, name: str = "a.md") -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _config(self, body: str) -> Path:
        path = self.dir / "mdtools.toml"
        path.write_text(body, encoding="utf-8")
        return path


class DispatchTests(CliTestCase):
    def test_no_arguments_prints_usage_and_fails(self) -> None:
        rc, out, _ = self._run()
        self.assertEqual(rc, 2)
        self.assertIn("usage: mdtools", out)

    def test_help_succeeds(self) -> None:
        rc, out, _ = self._run("--help")
        self.assertEqual(rc, 0)
        for verb in ("fix", "query", "terms", "links", "vary", "config"):
            self.assertIn(verb, out)

    def test_unknown_verb_is_an_environment_error(self) -> None:
        rc, _, err = self._run("frobnicate")
        self.assertEqual(rc, 2)
        self.assertIn("unknown command", err)

    def test_query_reaches_mdquery(self) -> None:
        path = self._doc("# Title\n\nBody.\n")
        rc, out, _ = self._run("query", "outline", str(path))
        self.assertEqual(rc, 0)
        self.assertIn("[title]", out)

    def test_links_reaches_mdlinks(self) -> None:
        path = self._doc("# T\n\nSee [x](#nope).\n")
        rc, out, _ = self._run("links", str(path))
        self.assertEqual(rc, 1)
        self.assertIn("no heading with anchor", out)

    def test_terms_reaches_mdterms(self) -> None:
        (self.dir / "glossary_terms.yaml").write_text(
            "terms:\n  - term: SLOW-32\n    forbidden: [SLOW32]\n",
            encoding="utf-8")
        path = self._doc("About SLOW32 here.\n")
        rc, out, _ = self._run("terms", str(path))
        self.assertEqual(rc, 1)
        self.assertIn("SLOW-32", out)

    def test_fix_reaches_mdfix(self) -> None:
        src = self._doc("#Title\n\nBody.\n")
        out_path = self.dir / "out.md"
        rc, _, _ = self._run("fix", "-q", str(src), str(out_path))
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.read_text(encoding="utf-8").startswith("# Title"))

    def test_check_says_it_is_not_implemented(self) -> None:
        # Better than a confusing failure: the verb is reserved and says so.
        rc, _, err = self._run("check", "x.md")
        self.assertEqual(rc, 2)
        self.assertIn("issue #13", err)

    def test_missing_mdfix_is_exit_two(self) -> None:
        src = self._doc("Body.\n")
        with mock.patch.object(cli, "_find_mdfix", return_value="/no/such/mdfix"):
            rc, _, err = self._run("fix", "-q", str(src), str(self.dir / "out.md"))
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)


class ExitCodeTests(CliTestCase):
    """0 clean, 1 findings, 2 usage or environment."""

    def test_clean_is_zero(self) -> None:
        path = self._doc("# T\n\nSee [x](#t).\n")
        self.assertEqual(self._run("links", str(path))[0], 0)

    def test_findings_are_one(self) -> None:
        path = self._doc("# T\n\nSee [x](#nope).\n")
        self.assertEqual(self._run("links", str(path))[0], 1)

    def test_missing_file_is_two_for_links(self) -> None:
        self.assertEqual(self._run("links", str(self.dir / "nope.md"))[0], 2)

    def test_missing_file_is_two_for_query(self) -> None:
        self.assertEqual(
            self._run("query", "outline", str(self.dir / "nope.md"))[0], 2)


class ConfigTests(CliTestCase):
    def test_config_prints_resolved_settings(self) -> None:
        rc, out, _ = self._run("config")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        for key in ("root", "config", "profile", "wrap", "editorial"):
            self.assertIn(key, data)

    def test_root_is_found_by_walking_up(self) -> None:
        nested = self.dir / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(find_root(nested), self.dir.resolve())

    def test_no_config_file_still_works(self) -> None:
        rc, out, _ = self._run("config")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertIsNone(data["config"])
        self.assertEqual(data["profile"], "none")

    def test_no_config_fix_does_not_inject_canonical(self) -> None:
        # Parity with bare mdfix: no project file means no profile flags.
        src = self._doc("Body.\n")
        out_path = self.dir / "out.md"
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0)
            self._run("fix", "-q", str(src), str(out_path))
            argv = run.call_args[0][0]
        self.assertNotIn("--canonical", argv)
        self.assertNotIn("--technical", argv)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_settings_are_read(self) -> None:
        self._config('[mdtools]\nprofile = "technical"\nwrap = 40\n')
        config = load(self.dir)
        self.assertEqual((config.profile, config.wrap), ("technical", 40))
        self.assertEqual(fix_flags(config), ["--technical", "--wrap=40"])

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_configured_wrap_reaches_mdfix(self) -> None:
        self._config('[mdtools]\nprofile = "technical"\nwrap = 40\n')
        src = self._doc("Some prose that is definitely longer than forty "
                        "columns wide for certain.\n")
        out_path = self.dir / "out.md"
        self._run("fix", "-q", str(src), str(out_path))
        for line in out_path.read_text(encoding="utf-8").splitlines():
            self.assertLessEqual(len(line), 40)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_short_flags_still_take_the_profile(self) -> None:
        self._config('[mdtools]\nprofile = "technical"\nwrap = 40\n')
        src = self._doc("Some prose that is definitely longer than forty "
                        "columns wide for certain.\n")
        out_path = self.dir / "out.md"
        # Only -q: project wrap/profile must still apply.
        self._run("fix", "-q", str(src), str(out_path))
        for line in out_path.read_text(encoding="utf-8").splitlines():
            self.assertLessEqual(len(line), 40)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_explicit_flags_win_over_config(self) -> None:
        # A long flag on the command line is a deliberate override; silently
        # merging it with the profile would make the result hard to predict.
        self._config('[mdtools]\nprofile = "technical"\nwrap = 40\n')
        src = self._doc("word " * 40 + "\n")
        out_path = self.dir / "out.md"
        self._run("fix", "-q", "--no-required", str(src), str(out_path))
        self.assertEqual(len(out_path.read_text(encoding="utf-8").splitlines()), 1)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_an_unknown_setting_is_refused(self) -> None:
        # Silently ignoring it is how a project ends up believing a setting
        # applies when it does not.
        self._config('[mdtools]\nfrobnicate = true\n')
        with self.assertRaises(ConfigError) as caught:
            load(self.dir)
        self.assertIn("frobnicate", str(caught.exception))

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_a_bad_value_is_refused(self) -> None:
        self._config('[mdtools]\nprofile = "sideways"\n')
        with self.assertRaises(ConfigError):
            load(self.dir)
        self._config('[mdtools]\nwrap = -3\n')
        with self.assertRaises(ConfigError):
            load(self.dir)
        self._config('[mdtools]\nwrap = true\n')
        with self.assertRaises(ConfigError):
            load(self.dir)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_paths_resolve_against_the_project_root(self) -> None:
        # Never against the installed package: that is the acceptance
        # criterion about not writing mutable state into the package tree.
        self._config(
            '[mdtools]\n'
            'glossary = "terms/g.yaml"\n'
            'mdfix = "bin/mdfix"\n'
            'state_dir = ".state"\n'
        )
        config = load(self.dir)
        self.assertEqual(config.glossary, (self.dir / "terms/g.yaml").resolve())
        self.assertEqual(config.mdfix, str((self.dir / "bin/mdfix").resolve()))
        self.assertEqual(config.state_dir, (self.dir / ".state").resolve())
        self.assertNotIn("site-packages", str(config.glossary))

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_config_is_discovered_from_the_input_not_the_cwd(self) -> None:
        self._config('[mdtools]\nprofile = "technical"\nwrap = 40\n')
        nested = self.dir / "chapters"
        nested.mkdir()
        doc = nested / "one.md"
        doc.write_text("Text.\n", encoding="utf-8")
        self.assertEqual(load(doc).wrap, 40)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_query_discovers_config_from_the_file_not_the_subcommand(self) -> None:
        # cwd in a different tree must not win over the file's project.
        other = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(other, True))
        (other / ".git").mkdir()
        (other / "mdtools.toml").write_text(
            '[mdtools]\nprofile = "technical"\nwrap = 40\n', encoding="utf-8")
        doc = other / "chapter.md"
        doc.write_text("# Title\n\nBody.\n", encoding="utf-8")
        # Run from self.dir (has its own .git, no toml) against other's file.
        with mock.patch.object(Path, "cwd", return_value=self.dir):
            rc, out, _ = self._run("query", "outline", str(doc))
        self.assertEqual(rc, 0)
        self.assertIn("[title]", out)
        # Config start for that invocation must be the other project's wrap.
        self.assertEqual(load(doc).wrap, 40)
        self.assertEqual(load(self.dir).wrap, 0)

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_configured_mdfix_is_passed_to_query(self) -> None:
        self._config(f'[mdtools]\nmdfix = "{MDFIX}"\n')
        path = self._doc("# Title\n\nBody.\n")
        with mock.patch("mdquery.__main__.main", return_value=0) as main:
            rc, _, _ = self._run("query", "outline", str(path))
        self.assertEqual(rc, 0)
        argv = main.call_args[0][0]
        self.assertIn("--mdfix", argv)
        self.assertEqual(argv[argv.index("--mdfix") + 1], str(MDFIX.resolve()))

    @unittest.skipUnless(HAS_TOML, "tomllib needs Python 3.11")
    def test_glossary_is_passed_to_vary(self) -> None:
        gloss = self.dir / "g.yaml"
        gloss.write_text("terms: []\n", encoding="utf-8")
        self._config('[mdtools]\nglossary = "g.yaml"\nstate_dir = ".state"\n')
        path = self._doc("Body.\n")
        with mock.patch("prosevary.__main__.main", return_value=0) as main:
            rc, _, _ = self._run("vary", str(path), "--dry-run")
        self.assertEqual(rc, 0)
        argv = main.call_args[0][0]
        self.assertIn("--glossary", argv)
        self.assertEqual(argv[argv.index("--glossary") + 1], str(gloss.resolve()))
        self.assertIn("--db", argv)
        self.assertEqual(
            argv[argv.index("--db") + 1],
            str((self.dir / ".state" / "prosevary.sqlite").resolve()),
        )


class LauncherTests(unittest.TestCase):
    def test_launcher_runs(self) -> None:
        launcher = ROOT / "scripts" / "mdtools"
        if not launcher.is_file():
            self.skipTest("launcher not present")
        result = subprocess.run([str(launcher), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("usage: mdtools", result.stdout)


if __name__ == "__main__":
    unittest.main()
