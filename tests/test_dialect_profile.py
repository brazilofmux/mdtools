"""
The canonical output profile is real, not aspirational (issue #11).

docs/dialect-policy.md §3 pins the Pandoc extensions mdtools depends on. Each
one buys a specific behavior — `-four_space_rule` makes list continuation
content-column-relative, `+markdown_in_html_blocks` makes `<div>` contents
Markdown — and a future Pandoc that flips one changes what our output *means*,
silently.

So the document is the source of truth and this test reads it. Pinning the set
in Python instead would let the two drift, which is the failure mode a policy
document exists to prevent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "dialect-policy.md"
PANDOC = shutil.which("pandoc")

# `+name` / `-name` inside backticks. Restricted to §3's table: §4 and §7
# discuss `-smart` as a reader flag a *consumer* might pass, which is not a
# claim about the default.
_PINNED = re.compile(r"`([+-])([a-z_]+)`")
_SECTION_3 = re.compile(
    r"^## 3\. .*?^(## 4\.)", re.MULTILINE | re.DOTALL
)


def _pinned_extensions() -> dict[str, bool]:
    """Map extension name -> expected-enabled, from the policy document."""
    text = POLICY.read_text(encoding="utf-8")
    match = _SECTION_3.search(text)
    if not match:
        raise AssertionError(
            "docs/dialect-policy.md has no '## 3.' section ending at '## 4.'; "
            "this test reads the pinned profile from there"
        )
    return {
        name: (sign == "+")
        for sign, name in _PINNED.findall(match.group(0))
    }


def _pandoc_defaults() -> dict[str, bool]:
    result = subprocess.run(
        [PANDOC, "--list-extensions=markdown"],
        capture_output=True, text=True, check=True,
    )
    out = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line[:1] in "+-":
            out[line[1:]] = line[0] == "+"
    return out


class PolicyDocumentTests(unittest.TestCase):
    """These run without pandoc, so a doc typo fails fast everywhere."""

    def test_policy_document_exists(self) -> None:
        self.assertTrue(POLICY.is_file(), f"{POLICY} is missing")

    def test_section_3_pins_extensions(self) -> None:
        pinned = _pinned_extensions()
        # A rewrite that drops the table should fail here rather than silently
        # reduce this whole file to asserting nothing.
        self.assertGreaterEqual(
            len(pinned), 20,
            f"only {len(pinned)} extensions pinned in §3; the table looks lost",
        )

    def test_load_bearing_extensions_are_pinned(self) -> None:
        # Each of these is depended on by name somewhere in the code: the
        # column-based indent model, the raw-vs-Markdown HTML asymmetry, the
        # four protected table forms.
        pinned = _pinned_extensions()
        for name, enabled in (
            ("four_space_rule", False),
            ("markdown_in_html_blocks", True),
            ("raw_html", True),
            ("pipe_tables", True),
            ("simple_tables", True),
            ("grid_tables", True),
            ("multiline_tables", True),
            ("line_blocks", True),
            ("smart", True),
        ):
            with self.subTest(extension=name):
                self.assertIn(name, pinned)
                self.assertEqual(pinned[name], enabled)


def _pandoc_version() -> str:
    result = subprocess.run(
        [PANDOC, "--version"], capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()[0] if result.stdout else "pandoc (unknown)"


@unittest.skipUnless(PANDOC, "pandoc not installed")
class PandocProfileTests(unittest.TestCase):
    def test_pinned_extensions_match_pandoc_defaults(self) -> None:
        pinned = _pinned_extensions()
        actual = _pandoc_defaults()
        drift = {
            name: (want, actual[name])
            for name, want in pinned.items()
            if name in actual and actual[name] != want
        }
        self.assertFalse(
            drift,
            "pandoc's markdown defaults no longer match docs/dialect-policy.md "
            f"§3 (name: policy-says, pandoc-says): {drift}. "
            f"Oracle: {_pandoc_version()}. The document pins pandoc 3.10 in "
            "prose; CI may use a different install. Update the document "
            "deliberately or pin the extension on every invocation.",
        )

    def test_pinned_extensions_still_exist(self) -> None:
        pinned = _pinned_extensions()
        actual = _pandoc_defaults()
        missing = sorted(set(pinned) - set(actual))
        self.assertFalse(
            missing,
            f"pandoc no longer knows these extensions: {missing} "
            f"(oracle: {_pandoc_version()})",
        )


@unittest.skipUnless(PANDOC, "pandoc not installed")
class ProfileBehaviorTests(unittest.TestCase):
    """
    The behaviors §3 claims each extension buys. An extension can keep its
    default while its semantics move, so assert the outcomes too.
    """

    def _native(self, source: str, fmt: str = "markdown") -> str:
        result = subprocess.run(
            [PANDOC, "-f", fmt, "-t", "native"],
            input=source, capture_output=True, text=True, check=True,
        )
        return result.stdout

    def test_list_continuation_is_content_column_relative(self) -> None:
        # §5. Content column for `- item` is 2, so four spaces is still list
        # content and indented code needs six.
        self.assertIn("Para", self._native("- item\n\n    four spaces\n"))
        self.assertIn("CodeBlock", self._native("- item\n\n      six spaces\n"))

    def test_div_is_markdown_but_script_is_raw(self) -> None:
        # §3, §7. This asymmetry is why `<div>` contents stay prose-variable
        # while `<script>` contents must survive byte for byte.
        native = self._native(
            '<div class="x">\n\n*emph*\n\n</div>\n\n<script>\n*no*\n</script>\n'
        )
        self.assertIn("Div", native)
        self.assertIn("Emph", native)
        self.assertIn("RawBlock", native)

    def _html(self, source: str, fmt: str) -> str:
        result = subprocess.run(
            [PANDOC, "-f", fmt, "-t", "html"],
            input=source, capture_output=True, text=True, check=True,
        )
        return result.stdout

    def test_literal_unicode_punctuation_renders_the_same_either_way(self) -> None:
        # §4, the reason canonical output never emits ASCII shorthand.
        #
        # Asserted on rendered output, not on the AST: pandoc's internal
        # representation of literal curly quotes is version-dependent (3.10
        # keeps a plain Str under both flags; 2.x folds them into Quoted under
        # +smart). The typography that reaches the reader is stable in every
        # combination, and that is the property mdtools actually depends on.
        source = "A “quoted” thing — an ellipsis…\n"
        self.assertEqual(
            self._html(source, "markdown"),
            self._html(source, "markdown-smart"),
        )

    def test_ascii_shorthand_renders_differently(self) -> None:
        # The negative that gives the rule its force: had this been stable
        # too, §4 would be a style preference rather than a correctness one.
        source = 'A "quoted" thing -- an ellipsis...\n'
        self.assertNotEqual(
            self._html(source, "markdown"),
            self._html(source, "markdown-smart"),
        )
        # A consumer passing -smart gets the author's shorthand verbatim.
        self.assertIn('"quoted"', self._html(source, "markdown-smart"))

    def test_ascii_double_hyphen_is_not_an_em_dash(self) -> None:
        # `--` reads as an en dash, so the shorthand is wrong even with smart
        # on. Literal `—` is the only way to mean an em dash.
        self.assertIn("–", self._html("a -- b\n", "markdown"))
        self.assertNotIn("—", self._html("a -- b\n", "markdown"))
        self.assertIn("—", self._html("a — b\n", "markdown"))

    def test_tabs_and_spaces_indent_alike(self) -> None:
        # §5. Pandoc expands tabs before parsing, so mdtools measures columns.
        self.assertEqual(
            self._native("\tcode\n"), self._native("    code\n")
        )


if __name__ == "__main__":
    unittest.main()
