"""
Where a project gathers its assets from (issue #101).

`check.missing-asset` resolves an image relative to the file that references
it, which is right for a repository whose markdown sits next to its pictures
and wrong for one whose build gathers them. *An Agnostic's Guide to the Bible*
keeps chapters at the repository root and timelines in `timelines/`, and the
assembler copies each volume's timeline beside the manuscript it belongs to.
The path is correct at the moment Pandoc reads it — the PNG is in the shipped
EPUB — and there was no way to tell the check so.

That made it the one *false error* in that repository, which is the kind that
stops a gate exiting 0.

`asset_paths` is the answer: a search path tried after the referencing file's
own directory, which stays first so a layout that resolves today keeps
resolving.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mdcheck import __main__ as cli
from mdcheck.checks import run
from mdtools_cli.config import ConfigError, load_file

try:                       # tomllib is stdlib from 3.11
    import tomllib          # noqa: F401
    HAVE_TOML = True
except ModuleNotFoundError:
    HAVE_TOML = False

needs_toml = unittest.skipUnless(HAVE_TOML,
                                 "project config needs tomllib (3.11+)")

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"


class AssetTestCase(unittest.TestCase):
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

    def _asset(self, relative: str) -> Path:
        path = self.dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG")
        return path

    def _rules(self, **kwargs) -> list:
        return sorted(f.rule for f in run([self.dir], **kwargs))


class ResolutionTests(AssetTestCase):
    IMAGE = "# T\n\n![Volume 1 Chronology](timeline_vol1.png){ height=7in }\n"

    def test_without_a_search_path_it_is_an_error(self) -> None:
        self._doc(self.IMAGE)
        self._asset("timelines/timeline_vol1.png")
        self.assertIn("check.missing-asset", self._rules())

    def test_an_asset_on_the_search_path_resolves(self) -> None:
        self._doc(self.IMAGE)
        self._asset("timelines/timeline_vol1.png")
        self.assertEqual(
            self._rules(asset_paths=[self.dir / "timelines"]), [])

    def test_the_search_path_may_hold_the_written_path(self) -> None:
        # `![x](timelines/a.png)` with `asset_paths = ["build"]` and the file
        # at `build/timelines/a.png`: the path as written, under the root.
        self._doc("# T\n\n![x](timelines/a.png)\n")
        self._asset("build/timelines/a.png")
        self.assertEqual(self._rules(asset_paths=[self.dir / "build"]), [])

    def test_a_genuinely_missing_asset_is_still_an_error(self) -> None:
        self._doc(self.IMAGE)
        self._asset("timelines/a-different-file.png")
        self.assertIn("check.missing-asset",
                      self._rules(asset_paths=[self.dir / "timelines"]))

    def test_the_files_own_directory_is_still_tried_first(self) -> None:
        # What the search path must not break: a repository whose markdown
        # sits next to its pictures keeps working, search path or not.
        self._doc("# T\n\n![x](beside.png)\n")
        self._asset("beside.png")
        self.assertEqual(self._rules(asset_paths=[self.dir / "elsewhere"]), [])

    def test_a_path_that_does_not_exist_is_not_an_error_in_itself(self) -> None:
        # A configured directory that is absent (not built yet, say) leaves
        # the check where it was rather than crashing the run.
        self._doc(self.IMAGE)
        self.assertIn("check.missing-asset",
                      self._rules(asset_paths=[self.dir / "nowhere"]))

    def test_a_remote_image_is_unaffected(self) -> None:
        self._doc("# T\n\n![x](http://example.com/a.png)\n")
        self.assertEqual(self._rules(asset_paths=[self.dir / "timelines"]), [])

    def test_alt_text_is_still_checked(self) -> None:
        # The search path decides where a file is, not whether the image is
        # described. `check.image-alt` never needed the file at all.
        self._doc("# T\n\n![](timeline_vol1.png)\n")
        self._asset("timelines/timeline_vol1.png")
        self.assertEqual(self._rules(asset_paths=[self.dir / "timelines"]),
                         ["check.image-alt"])


class CompositionTests(AssetTestCase):
    """mdlinks does not know about the project, and must not be asked to."""

    def test_the_link_checker_does_not_report_it_either(self) -> None:
        # mdlinks resolves an image destination against the referencing file,
        # which is all a link checker can know — so without this, silencing
        # check.missing-asset simply swapped the error for
        # links.missing-file at the same span.
        self._doc("# T\n\n![x](timeline_vol1.png)\n")
        self._asset("timelines/timeline_vol1.png")
        self.assertEqual(self._rules(asset_paths=[self.dir / "timelines"]), [])

    def test_a_missing_link_target_is_still_reported(self) -> None:
        self._doc("# T\n\n[x](./nope.md)\n")
        self.assertIn("links.missing-file",
                      self._rules(asset_paths=[self.dir / "timelines"]))


@needs_toml
class ConfigTests(AssetTestCase):
    def _config(self, body: str) -> Path:
        path = self.dir / "mdtools.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_a_list_is_resolved_against_the_config(self) -> None:
        config = load_file(self._config(
            '[mdtools]\nasset_paths = ["timelines", "images"]\n'))
        self.assertEqual([p.name for p in config.asset_paths],
                         ["timelines", "images"])
        self.assertTrue(all(p.is_absolute() for p in config.asset_paths))

    def test_a_bare_string_is_one_path(self) -> None:
        config = load_file(self._config('[mdtools]\nasset_paths = "art"\n'))
        self.assertEqual([p.name for p in config.asset_paths], ["art"])

    def test_the_default_is_empty(self) -> None:
        config = load_file(self._config('[mdtools]\nwrap = 78\n'))
        self.assertEqual(config.asset_paths, [])

    def test_a_non_path_value_is_a_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            load_file(self._config('[mdtools]\nasset_paths = 3\n'))

    def test_it_appears_in_the_resolved_view(self) -> None:
        # `mdtools config` prints what every tool would use, and a setting
        # that changes a gate's exit code belongs in that answer.
        config = load_file(self._config(
            '[mdtools]\nasset_paths = ["timelines"]\n'))
        resolved = config.resolved()
        self.assertEqual([Path(p).name for p in resolved["asset_paths"]],
                         ["timelines"])

    def test_the_cli_reads_it(self) -> None:
        self._config('[mdtools]\nasset_paths = ["timelines"]\n')
        self._doc("# T\n\n![x](timeline_vol1.png)\n")
        self._asset("timelines/timeline_vol1.png")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([str(self.dir)])
        self.assertEqual(code, 0, msg=out.getvalue() + err.getvalue())
        self.assertNotIn("missing-asset", out.getvalue())


if __name__ == "__main__":
    unittest.main()
