"""
A colon inside a token is syntax, not punctuation (issue #118).

`chicago.space-after-punct` inserted a space after `:` and `;` wherever a
letter followed. In a technical manuscript those characters are usually syntax
being quoted, and the inserted space falsifies it: `dbref: timestamp` is not a
valid MUSH objid, and `"Out; out; o"` documents exit aliases the server would
reject. Same shape as the `!Kung` bug — a rule that is right for an English
sentence and wrong for a token that merely contains the character.

**Measured before choosing a rule.** Over 511 files of manuscript the branch
fired nine times: eight were damage, and the one repair it has ever made is

    …create a summary document for you:Here's the summary of…

which is a language model dropping the space, and the shape the tool exists
for. The discriminator is on the page: every damaged case joins two lowercase
tokens, and the repair introduces a capitalized clause.

So `:` inserts only before an uppercase letter and only after a word — which
also leaves `Foo::Bar` alone, since the second colon follows a colon. `;`
inserts never: English does not run a semicolon against the next word, and
both semicolons in the corpus were alias lists.

The third case in the report is `chicago.quote-terminal-punct` moving a period
inside a closing quote. That is Chicago's actual American rule and stays the
default; a verbatim quotation of computer output is the standard exception to
it, so `--no-quote-punct` turns it off.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"

# Every shape from the report, plus the ones the corpus added.
LITERALS = (
    "Returns 1 if the string is a valid object identifier (dbref or\n"
    "dbref:timestamp), and 0 otherwise.\n",
    'The semicolons define aliases. "Out;out;o" means the exit can be\n'
    "used by typing Out, out, or o.\n",
    "The ratio (and reality) has it as height:width:length = 1:2:4.\n",
    "They made the same mistake: **layout:run-noise ~25:1** — twice.\n",
    "The namespace Foo::Bar is spelled with two colons.\n",
)

# The one repair this branch has ever made, in 511 files.
TYPO = ("Sure, I can create a summary document for you:Here's the summary.\n",
        "Sure, I can create a summary document for you: Here's the summary.\n")


class LiteralTestCase(unittest.TestCase):
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

    def _fix(self, text: str, *flags: str) -> str:
        src, out = self.dir / "in.md", self.dir / "out.md"
        src.write_text(text, encoding="utf-8")
        if out.exists():
            out.unlink()
        result = subprocess.run([str(MDFIX), "-q", *flags, str(src), str(out)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")


class LiteralTests(LiteralTestCase):
    def test_a_literal_survives_every_profile_that_reaches_it(self) -> None:
        for source in LITERALS:
            for flags in ("--chicago-punct-2", "--canonical"):
                with self.subTest(source=source[:28], flags=flags):
                    self.assertEqual(self._fix(source, flags), source)

    def test_the_literal_itself_survives_a_wrapping_profile(self) -> None:
        # `--technical` reflows, so the file is not byte-identical for
        # reasons that have nothing to do with this rule. The token is.
        for source, literal in ((LITERALS[0], "dbref:timestamp"),
                                (LITERALS[1], '"Out;out;o"'),
                                (LITERALS[2], "height:width:length"),
                                (LITERALS[4], "Foo::Bar")):
            with self.subTest(literal=literal):
                self.assertIn(literal, self._fix(source, "--technical"))

    def test_the_one_repair_still_fires(self) -> None:
        source, expected = TYPO
        for flags in ("--chicago-punct-2", "--canonical"):
            with self.subTest(flags=flags):
                self.assertEqual(self._fix(source, flags), expected)

    def test_a_semicolon_is_never_a_sentence_break(self) -> None:
        # Not even before a capital: English does not run a semicolon against
        # the next word, so the shape is always a separator inside a token.
        source = "The flags are Read;Write;Execute here.\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_a_colon_before_a_lowercase_word_is_left_alone(self) -> None:
        source = "The key is note:this and nothing else.\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_a_namespace_is_not_split(self) -> None:
        # The second colon is preceded by a colon, not a word.
        source = "Call Foo::Bar and std::Vector from there.\n"
        self.assertEqual(self._fix(source, "--canonical"), source)

    def test_the_comma_is_unchanged(self) -> None:
        # Only `:` and `;` were narrowed. A comma still gets its space.
        self.assertEqual(self._fix("Hi,there and more.\n",
                                   "--chicago-punct-2"),
                         "Hi, there and more.\n")

    def test_a_period_was_never_in_this_branch(self) -> None:
        # I assumed it was, and the suite said otherwise. The scanner rule is
        # `[,;:?!]` — a period is claimed by the ellipsis and abbreviation
        # machinery instead, and never gets a space inserted after it. Pinned
        # so the next person does not have to rediscover it.
        source = "One end.Next one.\n"
        self.assertEqual(self._fix(source, "--chicago-punct-2"), source)


class QuoteTerminalTests(LiteralTestCase):
    """
    Chicago's American rule, kept as the default and made optional.

    The report's own view: "This third one is Chicago's actual American rule,
    so it is arguably working as designed — but a verbatim quotation of
    computer output is the standard exception to it, and `--canonical` has no
    way to be told so." Now it has one.
    """

    OUTPUT = 'You get "Huh? (Type X for help)".\n'

    def test_the_rule_still_runs_by_default(self) -> None:
        self.assertEqual(self._fix(self.OUTPUT, "--chicago-punct-2"),
                         'You get "Huh? (Type X for help)."\n')

    def test_the_opt_out_leaves_it_alone(self) -> None:
        for flags in (("--chicago-punct-2", "--no-quote-punct"),
                      ("--canonical", "--no-quote-punct"),
                      ("--technical", "--no-quote-punct")):
            with self.subTest(flags=flags):
                self.assertEqual(self._fix(self.OUTPUT, *flags), self.OUTPUT)

    def test_the_opt_out_leaves_the_rest_of_the_profile_alone(self) -> None:
        # It turns off one rule, not the pass: a heading still gets its space.
        out = self._fix("#Title\n\nBody.\n", "--canonical", "--no-quote-punct")
        self.assertTrue(out.startswith("# Title"))


if __name__ == "__main__":
    unittest.main()
