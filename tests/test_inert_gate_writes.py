"""Refuse writes when semantic/judge gates are inert (issue #4)."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prosevary import __main__ as cli
from prosevary.embed import Embedder, HashEmbedder
from prosevary.llm import NullGenerator, NullJudge
from prosevary.pipeline import active_gates
from prosevary.store import Store


class StubSemanticEmbedder(Embedder):
    """Minimal semantic embedder for mixed-gate tests (no model download)."""

    semantic = True
    model_id = "stub-semantic"

    def embed(self, text: str):
        # Distinct vectors so cosine is defined; values unused when not writing.
        return [float(len(text) % 7), 1.0, 0.0]


class StubEnforcingJudge:
    model_id = "stub-judge"
    enforcing = True

    def judge(self, original: str, candidate: str):
        from prosevary.llm import JudgeResult

        return JudgeResult(accept=True, reason="stub")


def _run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class InertGateWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.md = self.root / "doc.md"
        self.md.write_text("However we still utilize tooling.\n", encoding="utf-8")
        self.db = self.root / "t.sqlite"
        self.base = [
            "--db",
            str(self.db),
            "--embed",
            "hash",
            "--gen",
            "null",
            "--judge",
            "null",
            "--seed",
            "0",
            "--max-sentences",
            "2",
            str(self.md),
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dry_run_allowed_with_inert_gates(self) -> None:
        rc, out, err = _run(self.base)  # default dry-run
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(self.md.read_text(encoding="utf-8"), "However we still utilize tooling.\n")
        self.assertNotIn("refusing to write", err)

    def test_dry_run_flag_overrides_apply(self) -> None:
        before = self.md.read_text(encoding="utf-8")
        rc, out, err = _run(self.base + ["-n", "--apply"])
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("overrides", err)
        # Decision report may go to stdout; the file must stay untouched.
        self.assertEqual(self.md.read_text(encoding="utf-8"), before)
        self.assertNotIn("refusing to write", err)

    def test_apply_refused_when_gates_inert(self) -> None:
        before = self.md.read_text(encoding="utf-8")
        rc, out, err = _run(self.base + ["--apply"])
        self.assertEqual(rc, 2)
        self.assertIn("refusing to write with inert gates", err)
        self.assertEqual(out, "")
        self.assertEqual(self.md.read_text(encoding="utf-8"), before)

    def test_inplace_refused_when_gates_inert(self) -> None:
        before = self.md.read_text(encoding="utf-8")
        rc, out, err = _run(self.base + ["-i"])
        self.assertEqual(rc, 2)
        self.assertIn("refusing to write with inert gates", err)
        self.assertEqual(self.md.read_text(encoding="utf-8"), before)
        self.assertFalse(self.md.with_suffix(".md.bak").exists())

    def test_allow_inert_gates_permits_apply_and_logs_notes(self) -> None:
        rc, out, err = _run(self.base + ["--apply", "--allow-inert-gates"])
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("WARNING: writing with inert gates", err)
        self.assertIn("--allow-inert-gates", err)
        # stdout is the rewritten document, never the decision report.
        self.assertIn("However", out)
        self.assertNotIn("[0] accepted", out)
        store = Store(self.db)
        # Highest run_id should carry the unsafe note.
        row = store.conn.execute(
            "SELECT notes FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        store.close()
        self.assertIsNotNone(row)
        self.assertIn("UNSAFE: --allow-inert-gates", row["notes"])
        self.assertIn("inert=", row["notes"])

    def test_mixed_inert_judge_only_blocks_write(self) -> None:
        # Semantic embedder + null judge → judge inert → refuse write.
        def fake_embed(_kind="auto", model=None):
            return StubSemanticEmbedder()

        def fake_judge(kind="auto", model=None, base_url=None):
            return NullJudge()

        def fake_gen(kind="auto", model=None, base_url=None):
            return NullGenerator()

        with mock.patch.object(cli, "make_embedder", fake_embed), mock.patch.object(
            cli, "make_judge", fake_judge
        ), mock.patch.object(cli, "make_generator", fake_gen):
            rc, out, err = _run(
                [
                    "--db",
                    str(self.db),
                    "--apply",
                    "--seed",
                    "0",
                    str(self.md),
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("refusing to write", err)
        self.assertIn("accepts everything", err)
        self.assertEqual(out, "")

    def test_mixed_inert_tau_only_blocks_write(self) -> None:
        def fake_embed(_kind="auto", model=None):
            return HashEmbedder()

        def fake_judge(kind="auto", model=None, base_url=None):
            return StubEnforcingJudge()

        def fake_gen(kind="auto", model=None, base_url=None):
            return NullGenerator()

        with mock.patch.object(cli, "make_embedder", fake_embed), mock.patch.object(
            cli, "make_judge", fake_judge
        ), mock.patch.object(cli, "make_generator", fake_gen):
            rc, out, err = _run(
                [
                    "--db",
                    str(self.db),
                    "--apply",
                    "--seed",
                    "0",
                    str(self.md),
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("refusing to write", err)
        self.assertIn("not semantic", err)

    def test_both_gates_active_allows_apply(self) -> None:
        def fake_embed(_kind="auto", model=None):
            return StubSemanticEmbedder()

        def fake_judge(kind="auto", model=None, base_url=None):
            return StubEnforcingJudge()

        def fake_gen(kind="auto", model=None, base_url=None):
            return NullGenerator()

        with mock.patch.object(cli, "make_embedder", fake_embed), mock.patch.object(
            cli, "make_judge", fake_judge
        ), mock.patch.object(cli, "make_generator", fake_gen):
            rc, out, err = _run(
                [
                    "--db",
                    str(self.db),
                    "--apply",
                    "--seed",
                    "0",
                    str(self.md),
                ]
            )
        self.assertEqual(rc, 0, msg=err)
        self.assertNotIn("refusing to write", err)
        # Pipeline ran; document emitted to stdout (possibly unchanged).
        self.assertIn("However", out)


class DemoSynonymSafetyTests(unittest.TestCase):
    def test_demonstrate_prove_not_in_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "s.sqlite"
            store = Store(db)
            store.seed_demo_synonyms()
            syns = store.synonyms_for("demonstrate")
            self.assertIn("show", syns)
            self.assertNotIn("prove", syns)
            self.assertNotIn("broke", store.synonyms_for("failed"))
            store.close()

    def test_seed_purges_legacy_bad_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "s.sqlite"
            store = Store(db)
            store.add_synonym("demonstrate", "prove", "v", "general")
            store.add_synonym("failed", "broke", "v", "general")
            store.conn.commit()
            self.assertIn("prove", store.synonyms_for("demonstrate"))
            store.seed_demo_synonyms()
            self.assertNotIn("prove", store.synonyms_for("demonstrate"))
            self.assertNotIn("broke", store.synonyms_for("failed"))
            store.close()

    def test_active_gates_reports_inert_backends(self) -> None:
        self.assertEqual(active_gates(HashEmbedder(), NullJudge()), ["freeze"])
        self.assertEqual(
            active_gates(StubSemanticEmbedder(), NullJudge()), ["freeze", "tau"]
        )
        self.assertEqual(
            active_gates(HashEmbedder(), StubEnforcingJudge()), ["freeze", "judge"]
        )
        self.assertEqual(
            active_gates(StubSemanticEmbedder(), StubEnforcingJudge()),
            ["freeze", "tau", "judge"],
        )


class PurgeReachabilityTests(unittest.TestCase):
    """
    The purge must run on every invocation, not only when seeding.

    seed_demo_synonyms() is called only when the DB has no `however` rows, so
    testing the purge by calling that method directly proves the mechanism
    while leaving the wiring untested — and every pre-existing DB unhealed.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "d.sqlite"
        self.doc = Path(self.tmp.name) / "c.md"
        self.doc.write_text("However the backend is separate.\n", encoding="utf-8")

    def _legacy_db(self) -> None:
        """A database as an older version would have left it."""
        store = Store(self.db)
        store.seed_demo_synonyms()
        store.add_synonym("demonstrate", "prove", "v", "general")
        store.add_synonym("failed", "broke", "v", "general")
        store.conn.commit()
        store.close()

    def test_normal_run_heals_a_legacy_database(self) -> None:
        self._legacy_db()
        _run(["--db", str(self.db), str(self.doc)])  # plain dry run, no flags
        store = Store(self.db)
        self.addCleanup(store.close)
        self.assertNotIn("prove", store.synonyms_for("demonstrate"))
        self.assertNotIn("broke", store.synonyms_for("failed"))
        # The rest of the seed must survive the purge.
        self.assertIn("show", store.synonyms_for("demonstrate"))
        self.assertTrue(store.synonyms_for("however"))

    def test_purge_is_case_insensitive_on_both_columns(self) -> None:
        store = Store(self.db)
        store.seed_demo_synonyms()
        store.add_synonym("demonstrate", "Prove", "v", "general")
        store.add_synonym("Failed", "BROKE", "v", "general")
        store.conn.commit()
        self.assertEqual(store.purge_unsafe_synonyms(), 2)
        self.assertNotIn("Prove", store.synonyms_for("demonstrate"))
        self.assertNotIn("BROKE", store.synonyms_for("failed"))
        store.close()

    def test_purge_is_idempotent(self) -> None:
        store = Store(self.db)
        store.seed_demo_synonyms()
        self.assertEqual(store.purge_unsafe_synonyms(), 0)
        store.close()


class RunNotesVisibleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "d.sqlite"
        self.doc = Path(self.tmp.name) / "c.md"
        self.doc.write_text("However the backend is separate.\n", encoding="utf-8")

    def test_report_run_surfaces_the_unsafe_note(self) -> None:
        # Writing the audit record but never showing it made the
        # "logged in run metadata" promise unverifiable from the CLI.
        _run(["--db", str(self.db), "-i", "--allow-inert-gates", str(self.doc)])
        rc, out, err = _run(["--db", str(self.db), "--report-run", "1"])
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("notes: UNSAFE: --allow-inert-gates", out)

    def test_report_run_omits_notes_line_when_empty(self) -> None:
        _run(["--db", str(self.db), str(self.doc)])  # dry run leaves notes empty
        rc, out, err = _run(["--db", str(self.db), "--report-run", "1"])
        self.assertEqual(rc, 0, msg=err)
        self.assertNotIn("notes:", out)


if __name__ == "__main__":
    unittest.main()
