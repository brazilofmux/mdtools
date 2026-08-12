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


# The variables this PR tells users to export are exactly the ones that make
# these tests lie. With PROSEVARY_DB and PROSEVARY_GLOSSARY set, three of them
# failed — and one failed for the wrong reason entirely, dying at "glossary
# not found" long before reaching the Ollama assertion it claimed to test.
_CONFIG_ENV = (
    "PROSEVARY_DB",
    "PROSEVARY_GLOSSARY",
    "PROSEVARY_EMBED_MODEL",
    "PROSEVARY_GEN_MODEL",
    "PROSEVARY_JUDGE_MODEL",
    "PROSEVARY_BASE_URL",
    "PROSEVARY_ST_MODEL",
    "PROSEVARY_API_KEY",
    "XDG_STATE_HOME",
)


class _CleanEnv(unittest.TestCase):
    """Base class: run with prosevary's configuration variables unset."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in _CONFIG_ENV:
            os.environ.pop(name, None)


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class GlossaryDiscoveryTests(_CleanEnv):
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


class DbDefaultTests(_CleanEnv):
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


class OllamaEmbedPreflightTests(_CleanEnv):
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


class InputValidationOrderTests(_CleanEnv):
    def test_bad_input_creates_nothing(self) -> None:
        # Store() creates parent directories and default_db_path() derives one
        # from the input, so validating the input late turned a typo into a
        # *directory* named after the file — making "not a file" true only
        # because prosevary had just made it one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, _, err = _run(
                ["--embed", "hash", "--gen", "null", "--judge", "null",
                 str(root / "typo.md")]
            )
            self.assertEqual(rc, 2)
            self.assertIn("not a file", err)
            self.assertEqual(list(root.iterdir()), [], "nothing may be created")

    def test_missing_path_is_not_treated_as_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            missing = root / "sub" / "nope.md"
            # Resolves to the project root's .prosevary, never inside nope.md/
            db = default_db_path(missing)
            self.assertNotIn("nope.md", db.parts)


class StateDirSelfIgnoreTests(_CleanEnv):
    def test_state_dir_ignores_itself(self) -> None:
        # The DB lands next to the manuscript, so without a self-ignore every
        # consuming repo sees `?? .prosevary/`.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / ".prosevary" / "prosevary.sqlite"
            Store(db).close()
            marker = db.parent / ".gitignore"
            self.assertTrue(marker.is_file())
            self.assertIn("*", marker.read_text(encoding="utf-8"))

    def test_non_state_dir_gets_no_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "custom" / "x.sqlite"
            Store(db).close()
            self.assertFalse((db.parent / ".gitignore").exists())


class OllamaModelPresenceTests(_CleanEnv):
    def test_auto_chat_backends_require_a_pulled_model(self) -> None:
        # Checking only that the server is up picked llama3.2 on a host where
        # the user had pulled nomic-embed-text and nothing else; the 404 then
        # surfaced as an unhandled HTTPError from the first paraphrase.
        from prosevary import llm as llm_mod

        with mock.patch.object(llm_mod, "openai_available", lambda *a, **k: False):
            with mock.patch("prosevary.embed.ollama_available", lambda *a, **k: True):
                with mock.patch("prosevary.embed.ollama_has_model", lambda *a, **k: False):
                    self.assertEqual(llm_mod.make_generator("auto").model_id, "null")
                    self.assertEqual(llm_mod.make_judge("auto").model_id, "null-accept")
                with mock.patch("prosevary.embed.ollama_has_model", lambda *a, **k: True):
                    self.assertTrue(
                        llm_mod.make_generator("auto").model_id.startswith("ollama:")
                    )

    def test_explicit_ollama_still_bypasses_the_check(self) -> None:
        # An explicit --gen ollama is the user asserting it is there.
        from prosevary import llm as llm_mod

        with mock.patch("prosevary.embed.ollama_available", lambda *a, **k: False):
            self.assertTrue(
                llm_mod.make_generator("ollama").model_id.startswith("ollama:")
            )

    def test_non_ollama_json_on_the_port_does_not_raise(self) -> None:
        # A proxy or dev server answering on 11434 returns a JSON array; this
        # runs on the default --embed auto path whenever the port responds.
        from prosevary import embed as embed_mod

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'["not", "a", "dict"]'

        with mock.patch.object(
            embed_mod.urllib.request, "urlopen", lambda *a, **k: FakeResp()
        ):
            self.assertIsNone(embed_mod.ollama_list_models())
            self.assertFalse(embed_mod.ollama_has_model("anything"))


if __name__ == "__main__":
    unittest.main()
