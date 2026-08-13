"""
Word-character classification (brazilofmux/utf#3).

Two rules in mdfix need to know whether a code point is a word character, and
both used to approximate it as "any byte >= 0x80":

  * **intraword underscore** — Pandoc leaves `_` alone inside a word, so
    `漢字_の_強調` must stay literal. The byte test got this right.
  * **citation versus email** — `@key` is a citation only when the `@` does
    not follow a word character. The byte test got `café@x` right and
    `。@key` wrong, because it could not tell a letter from a full-width stop.

libutf grew `utf_is_word` for these, and mdfix vendors it. The set is
Alphabetic + Nd + Mn + Mc; `Pc`, where `_` lives, is deliberately outside it,
which is what the first rule needs — it is deciding *about* the underscore.

Pandoc is the oracle for both, as everywhere else here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
VENDOR = ROOT / "mdfix" / "vendor" / "utf_word.c"
PANDOC = shutil.which("pandoc")


class WordTestCase(unittest.TestCase):
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

    def _html(self, text: str) -> str:
        return subprocess.run([PANDOC, "-f", "markdown", "-t", "html"],
                              input=text, capture_output=True, text=True,
                              check=True).stdout

    def _plain(self, heading: str) -> str:
        path = self.dir / "w.md"
        path.write_text(f"# {heading}\n", encoding="utf-8")
        result = subprocess.run([str(MDFIX), "--emit-ir", str(path)],
                                capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            rec = json.loads(line)
            if rec.get("kind") == "heading":
                return rec["plain"]
        self.fail(f"no heading record for {heading!r}")


class IntrawordUnderscoreTests(WordTestCase):
    """`+intraword_underscores` is pinned by dialect-policy §3."""

    LITERAL = ("a_b_c\n", "snake_case_name\n", "漢字_の_強調\n",
               "Ελλη_νικά_x\n", "текст_с_подчёркиванием\n", "한글_단어_x\n")

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_an_intraword_underscore_is_not_emphasis(self) -> None:
        for text in self.LITERAL:
            with self.subTest(text=text.strip()):
                self.assertNotIn("<em>", self._html(text))

    def test_an_intraword_underscore_stays_in_plain(self) -> None:
        # heading.plain is where +intraword_underscores actually runs.
        # --canonical does not rewrite emphasis, so identity under that
        # flag would stay green even if is_word_at were deleted.
        for text in self.LITERAL:
            heading = text.strip()
            with self.subTest(text=heading):
                self.assertEqual(self._plain(heading), heading)

    def test_a_symbol_neighbour_strips_like_pandoc(self) -> None:
        # ∈ and 。 are not word characters, so the underscores are emphasis
        # and leave heading.plain. The old byte test treated both as word-ish.
        for text, expected in (("∈_x_", "∈x"), ("。_foo_", "。foo")):
            with self.subTest(text=text):
                self.assertEqual(self._plain(text), expected)

    @unittest.skipUnless(PANDOC, "pandoc not installed")
    def test_a_between_word_underscore_is_still_emphasis(self) -> None:
        # The other side of the rule — otherwise "leave underscores alone"
        # would be indistinguishable from "never emphasize".
        for text in ("x _emph_ y\n", "_emph_\n", "漢字 _強調_ です\n"):
            with self.subTest(text=text.strip()):
                self.assertIn("<em>", self._html(text))


class VendorTests(unittest.TestCase):
    """Same hygiene as the other two vendored tables."""

    def test_provenance_is_recorded(self) -> None:
        head = VENDOR.read_text(encoding="utf-8")[:2000]
        self.assertIn("VENDORED, DO NOT EDIT", head)
        self.assertIn("github.com/brazilofmux/utf", head)
        self.assertIn("commit", head)

    def test_upstream_names_are_gone(self) -> None:
        text = VENDOR.read_text(encoding="utf-8")
        for symbol in ("utf_is_word", "utf_is_word_connector"):
            with self.subTest(symbol=symbol):
                self.assertNotIn(f" {symbol}(", text)
        self.assertIn("mdfix_is_word", text)

    def test_nothing_else_is_exported(self) -> None:
        text = VENDOR.read_text(encoding="utf-8")
        exported = re.findall(r"^(?!static\b)(?:const |unsigned |int |void |"
                              r"size_t )[^;{]*\b(\w+)\s*[\(\[]", text, re.M)
        self.assertEqual(sorted(set(exported)),
                         ["mdfix_is_word", "mdfix_is_word_connector"])

    def test_every_build_target_compiles_it(self) -> None:
        makefile = (ROOT / "mdfix" / "Makefile").read_text(encoding="utf-8")
        for target in ("mdfix:", "asan:"):
            with self.subTest(target=target):
                recipe = makefile.split(target, 1)[1].split("\n\n", 1)[0]
                self.assertIn("vendor/utf_word.c", recipe)


if __name__ == "__main__":
    unittest.main()
