"""
The vendored tables are the bytes somebody verified.

`mdfix/vendor/*.c` are extracts from libutf, each carrying "VENDORED, DO NOT
EDIT". That is a comment, and a comment stops nobody. A hand-edit to fix
something locally, or a re-extraction that ran against a dirty tree, produces
a file that looks exactly like the one that was reviewed — 4,700 lines of
generated table where a changed digit is invisible to reading.

`mdfix/vendor/MANIFEST` is the fingerprint, and this is the check.

**What it does not do.** It cannot compare the copy against libutf; that needs
libutf present, and these files exist precisely so a build does not need it.
The upstream comparison is a sweep over every code point, run by hand at each
refresh and recorded in the commit — see `docs/vendoring.md`. This covers the
other half: that nothing has moved *since*.

The pairing is the same one `check-sync` makes for `mdfix.c`. A generated file
in the tree can drift from what generated it, and reviewing the generator then
tells you nothing about what ships.
"""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "mdfix" / "vendor"
MANIFEST = VENDOR / "MANIFEST"


def _entries() -> list:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


class ManifestTests(unittest.TestCase):
    def test_every_vendored_file_matches_its_fingerprint(self) -> None:
        for digest, name, commit in _entries():
            with self.subTest(file=name):
                path = VENDOR / name
                self.assertTrue(path.is_file(), f"{name} is in the manifest "
                                                "but not in vendor/")
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(
                    actual, digest,
                    f"{name} is not the extract that was verified.\n"
                    f"If this is a deliberate refresh, re-run the oracle "
                    f"sweep in docs/vendoring.md and update MANIFEST in the "
                    f"same commit. If it is not, something edited a "
                    f"generated file.")

    def test_every_vendored_file_is_in_the_manifest(self) -> None:
        # Otherwise adding a fourth table would silently opt out of the check.
        listed = {name for _, name, _ in _entries()}
        present = {p.name for p in VENDOR.glob("*.c")}
        self.assertEqual(present, listed)

    def test_each_entry_names_an_upstream_commit(self) -> None:
        # A fingerprint with no provenance says the bytes have not changed,
        # which is only half of what a refresh needs to be reviewable.
        for digest, name, commit in _entries():
            with self.subTest(file=name):
                self.assertTrue(commit, f"{name} has no upstream commit")
                self.assertRegex(commit, r"^[0-9a-f]{7,40}$")

    def test_every_extract_carries_the_upstream_notice(self) -> None:
        """
        MIT requires the copyright and permission notice in "all copies or
        substantial portions". A 4,700-line table extract is a substantial
        portion, and a vendored file is exactly the case where the notice
        would otherwise be lost — upstream keeps it in one LICENSE at the root
        of a repository this file does not travel with.
        """
        for _, name, _ in _entries():
            with self.subTest(file=name):
                head = (VENDOR / name).read_text(encoding="utf-8")[:1200]
                self.assertIn("Copyright", head)
                self.assertIn("MIT", head)
                self.assertIn("LICENSE.libutf", head)

    def test_the_full_licence_text_is_present(self) -> None:
        licence = (VENDOR / "LICENSE.libutf").read_text(encoding="utf-8")
        self.assertIn("MIT License", licence)
        self.assertIn("Copyright", licence)
        self.assertIn("without restriction", licence)

    def test_the_commit_matches_the_file_header(self) -> None:
        # The banner and the manifest are two records of the same fact, and
        # two records that can disagree eventually do.
        for _, name, commit in _entries():
            with self.subTest(file=name):
                head = (VENDOR / name).read_text(encoding="utf-8")[:2500]
                found = re.search(r"commit\s+([0-9a-f]{7,40})", head)
                self.assertIsNotNone(found, f"{name} banner names no commit")
                self.assertEqual(found.group(1), commit)


if __name__ == "__main__":
    unittest.main()
