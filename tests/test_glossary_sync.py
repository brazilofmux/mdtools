"""
Glossary freeze terms are synchronized, not accumulated (issue #32).

YAML is authoritative for glossary-sourced rows; the table is a cache of the
file. Import only ever upserted, so deleting or renaming an entry never
unfroze it — the term stayed frozen until someone hand-edited the database,
contradicting the README's description of the glossary as a file loaded at
startup.

Rows added by hand are a different source and are left alone.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prosevary import __main__ as cli
from prosevary.store import Store


def _sources(store: Store) -> dict[str, str]:
    return {
        row["term"]: row["source"]
        for row in store.conn.execute("SELECT term, source FROM freeze_terms")
    }


class GlossarySyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "d.sqlite")
        self.addCleanup(self.store.close)

    def test_removed_entries_are_unfrozen(self) -> None:
        # The acceptance criterion: import {A, B}, then {B, C}, observe {B, C}.
        self.store.import_glossary({"alpha", "beta"})
        self.assertEqual(sorted(self.store.all_freeze_terms()), ["alpha", "beta"])
        self.store.import_glossary({"beta", "gamma"})
        self.assertEqual(sorted(self.store.all_freeze_terms()), ["beta", "gamma"])

    def test_manual_rows_are_never_touched(self) -> None:
        self.store.upsert_freeze("handmade", "manual")
        self.store.import_glossary({"beta"})
        self.assertEqual(_sources(self.store),
                         {"handmade": "manual", "beta": "glossary"})
        self.store.import_glossary(set())
        self.assertEqual(_sources(self.store), {"handmade": "manual"})

    def test_a_term_in_both_stays_manual(self) -> None:
        # Converting it to glossary-backed would make it vanish the next time
        # the entry left the YAML, silently unfreezing a hand-curated term.
        self.store.upsert_freeze("shared", "manual")
        self.store.import_glossary({"shared", "beta"})
        self.assertEqual(_sources(self.store)["shared"], "manual")
        self.store.import_glossary({"beta"})
        self.assertIn("shared", self.store.all_freeze_terms())

    def test_case_change_takes_effect(self) -> None:
        # The conflict target collates NOCASE, so an upsert kept the old
        # spelling. Deleting the glossary rows first is what lets it change.
        self.store.import_glossary({"Fixup"})
        self.assertEqual(self.store.all_freeze_terms(), ["Fixup"])
        self.store.import_glossary({"FIXUP"})
        self.assertEqual(self.store.all_freeze_terms(), ["FIXUP"])

    def test_rename_takes_effect(self) -> None:
        self.store.import_glossary({"relocation"})
        self.store.import_glossary({"relocations"})
        self.assertEqual(self.store.all_freeze_terms(), ["relocations"])

    def test_empty_import_clears_glossary_rows(self) -> None:
        self.store.import_glossary({"alpha"})
        self.store.import_glossary(set())
        self.assertEqual(self.store.all_freeze_terms(), [])

    def test_import_is_idempotent(self) -> None:
        self.store.import_glossary({"alpha", "beta"})
        first = sorted(self.store.all_freeze_terms())
        self.store.import_glossary({"alpha", "beta"})
        self.assertEqual(sorted(self.store.all_freeze_terms()), first)


class GlossarySyncThroughCliTests(unittest.TestCase):
    """
    The CLI half. Importing only a non-empty glossary left exactly the
    staleness the sync removes: deleting the last entry, or the file, kept
    every term frozen from the database.
    """

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("PROSEVARY_DB", "PROSEVARY_GLOSSARY", "PROSEVARY_EMBED_MODEL"):
            os.environ.pop(name, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "d.sqlite"
        self.doc = self.dir / "ch.md"
        self.doc.write_text("We run the fixup pass.\n", encoding="utf-8")

    def _run(self, glossary_yaml: str | None) -> None:
        path = self.dir / "glossary_terms.yaml"
        argv = ["--db", str(self.db), "--embed", "hash",
                "--gen", "null", "--judge", "null"]
        if glossary_yaml is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(glossary_yaml, encoding="utf-8")
            argv += ["--glossary", str(path)]
        argv.append(str(self.doc))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = cli.main(argv)
        self.assertEqual(rc, 0)

    def _terms(self) -> list[str]:
        store = Store(self.db)
        try:
            return sorted(store.all_freeze_terms())
        finally:
            store.close()

    def test_deleting_an_entry_unfreezes_it_next_run(self) -> None:
        self._run("terms:\n  - term: fixup\n  - term: relocation\n")
        self.assertEqual(self._terms(), ["fixup", "relocation"])
        self._run("terms:\n  - term: fixup\n")
        self.assertEqual(self._terms(), ["fixup"])

    def test_emptying_the_glossary_unfreezes_everything(self) -> None:
        self._run("terms:\n  - term: fixup\n")
        self.assertEqual(self._terms(), ["fixup"])
        self._run("terms: []\n")
        self.assertEqual(self._terms(), [])


if __name__ == "__main__":
    unittest.main()
