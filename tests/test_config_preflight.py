"""Configuration, storage defaults, and backend preflight (issue #9)."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from prosevary import __main__ as cli
from prosevary.paths import default_db_path, default_glossary_path
from prosevary.store import Store


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class GlossaryDiscoveryTests(unittest.TestCase):
    def test_walks_from_input_not_only_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "book"
            book.mkdir()
            (book / "glossary_terms.yaml").write_text(
                "terms:\n  - term: fixup\n", encoding="utf-8"
            )
            chap = book / "ch"
            chap.mkdir()
            md = chap / "1.md"
            md.write_text("We run the fixup pass.\n", encoding="utf-8")

            # cwd is tmp (no glossary); discovery must still find book/.
            old = Path.cwd()
            try:
                os.chdir(tmp)
                found = default_glossary_path(md)
            finally:
                os.chdir(old)
            self.assertEqual(found.resolve(), (book / "glossary_terms.yaml").resolve())

    def test_explicit_missing_glossary_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text("Hello.\n", encoding="utf-8")
            missing = Path(tmp) / "nope.yaml"
            rc, _, err = _run(
                [
                    "--db",
                    str(Path(tmp) / "t.sqlite"),
                    "--glossary",
                    str(missing),
                    "--embed",
                    "hash",
                    "--gen",
                    "null",
                    "--judge",
                    "null",
                    str(md),
                ]
            )
            self.assertEqual(rc, 2)
            self.assertIn("glossary not found", err)


class DbDefaultTests(unittest.TestCase):
    def test_default_db_not_inside_package(self) -> None:
        pkg = Path(__file__).resolve().parents[1] / "prosevary"
        path = default_db_path(None)
        self.assertFalse(str(path).startswith(str(pkg)))

    def test_project_marker_places_db_under_prosevary_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            md = root / "ch.md"
            md.write_text("Hi.\n", encoding="utf-8")
            self.assertEqual(
                default_db_path(md).resolve(),
                (root / ".prosevary" / "prosevary.sqlite").resolve(),
            )


class SeedSynonymsNoInputTests(unittest.TestCase):
    def test_seed_synonyms_without_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "s.sqlite"
            rc, out, err = _run(["--db", str(db), "--seed-synonyms"])
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("Seeded", out)
            store = Store(db)
            self.assertTrue(store.synonyms_for("however"))
            store.close()


class NumericValidationTests(unittest.TestCase):
    def test_tau_out_of_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text("Hi.\n", encoding="utf-8")
            rc, _, err = _run(
                ["--db", str(Path(tmp) / "t.sqlite"), "--tau", "1.5", str(md)]
            )
            self.assertEqual(rc, 2)
            self.assertIn("--tau", err)

    def test_k_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text("Hi.\n", encoding="utf-8")
            rc, _, err = _run(
                ["--db", str(Path(tmp) / "t.sqlite"), "--k", "0", str(md)]
            )
            self.assertEqual(rc, 2)
            self.assertIn("--k", err)

    def test_max_sentences_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text("Hi.\n", encoding="utf-8")
            rc, _, err = _run(
                [
                    "--db",
                    str(Path(tmp) / "t.sqlite"),
                    "--max-sentences",
                    "0",
                    str(md),
                ]
            )
            self.assertEqual(rc, 2)
            self.assertIn("--max-sentences", err)


class OpenAIPreflightTests(unittest.TestCase):
    def test_openai_gen_requires_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text("Hi.\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PROSEVARY_GEN_MODEL": ""}, clear=False):
                os.environ.pop("PROSEVARY_GEN_MODEL", None)
                rc, _, err = _run(
                    [
                        "--db",
                        str(Path(tmp) / "t.sqlite"),
                        "--gen",
                        "openai",
                        "--judge",
                        "null",
                        "--embed",
                        "hash",
                        str(md),
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertIn("gen-model", err.lower())

    def test_openai_available_sends_api_key(self) -> None:
        from prosevary import llm as llm_mod

        seen = {}

        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=1.5):
            seen["headers"] = dict(req.headers)
            return FakeResp()

        with mock.patch.object(llm_mod.urllib.request, "urlopen", fake_urlopen):
            with mock.patch.dict(os.environ, {"PROSEVARY_API_KEY": "secret-token"}):
                ok = llm_mod.openai_available("http://127.0.0.1:9")
        self.assertTrue(ok)
        # urllib may title-case header names
        auth = seen["headers"].get("Authorization") or seen["headers"].get(
            "authorization"
        )
        self.assertEqual(auth, "Bearer secret-token")


class OllamaEmbedPreflightTests(unittest.TestCase):
    def test_explicit_ollama_embed_requires_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "x.md"
            md.write_text("Hi.\n", encoding="utf-8")
            with mock.patch.object(cli, "ollama_available", return_value=True), mock.patch.object(
                cli, "ollama_has_model", return_value=False
            ):
                rc, _, err = _run(
                    [
                        "--db",
                        str(Path(tmp) / "t.sqlite"),
                        "--embed",
                        "ollama",
                        "--gen",
                        "null",
                        "--judge",
                        "null",
                        str(md),
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertIn("no embedding model", err)


if __name__ == "__main__":
    unittest.main()
