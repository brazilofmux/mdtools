"""
Unicode normalization at L1 and L3 (issue #54, architecture I1.2).

    I1.2  L1 detects text that is not NFC and says so. It does not rewrite it.

The split matters more than it looks. Normalizing at input would move every
byte after the first change, so IR spans would address a buffer the consumer
never saw (I1.3), and it would edit the author's file as a side effect of
reading it. So detection is free and always on; rewriting is `--normalize-nfc`,
an opt-in L3 transform (I3.3).

`unicodedata` is the oracle. It is an independent implementation of UAX #15
shipped with CPython, so agreeing with it is evidence about mdfix rather than
a restatement of mdfix. Where the two Unicode versions could disagree the
sweep stays inside long-settled blocks and says so.

The three scripts issue #54 names all appear: Latin with combining marks,
Greek with polytonic diacritics, and Hangul, whose composition is algorithmic
rather than table-driven and so exercises a different path entirely.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"
PANDOC = shutil.which("pandoc")

# Decomposed spellings, written as escapes on purpose: as literals this file
# would itself stop being NFC, and any editor that tidied it would quietly
# delete the only inputs these tests have.
LATIN = "He\u0301ading"                 # e + COMBINING ACUTE ACCENT
GREEK = "\u03b1\u0301"                 # alpha + COMBINING ACUTE (oxia)
HANGUL = "\u1112\u1161\u11ab"         # HIEUH + A + NIEUN -> U+D55C

COMPOSED = {
    LATIN: "H\u00e9ading",
    GREEK: "\u03ac",
    HANGUL: "\ud55c",
}


class NFCTestCase(unittest.TestCase):
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

    def _write(self, text: str, name: str = "n.md") -> Path:
        path = self.dir / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def _diagnose(self, text: str, *flags: str) -> list[dict]:
        path = self._write(text)
        result = subprocess.run(
            [str(MDFIX), "-n", "-q", "--diagnostics", *flags, str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return [json.loads(line) for line in result.stderr.splitlines()]

    def _nfc_rows(self, text: str, *flags: str) -> list[dict]:
        return [r for r in self._diagnose(text, *flags)
                if r["rule"] == "unicode.non-nfc"]

    def _fix(self, text: str, *flags: str) -> str:
        src = self._write(text)
        out = self.dir / "out.md"
        if out.exists():
            out.unlink()
        result = subprocess.run(
            [str(MDFIX), "-q", *flags, str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return out.read_text(encoding="utf-8")


class DetectionTests(NFCTestCase):
    """I1.2, first half: L1 says so."""

    def test_each_script_is_reported(self) -> None:
        for name, sample in (("latin", LATIN), ("greek", GREEK),
                             ("hangul", HANGUL)):
            with self.subTest(script=name):
                rows = self._nfc_rows(f"Text {sample} here.\n")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["severity"], "warning")

    def test_nfc_input_is_silent(self) -> None:
        for decomposed, composed in COMPOSED.items():
            with self.subTest(sample=composed):
                self.assertEqual(unicodedata.normalize("NFC", decomposed),
                                 composed)
                self.assertEqual(self._nfc_rows(f"Text {composed} here.\n"), [])

    def test_detection_needs_no_flag(self) -> None:
        # "Cheap enough to leave on" is the acceptance criterion. A bare run
        # with no transform requested still reports.
        rows = self._nfc_rows(f"# {LATIN}\n")
        self.assertEqual(len(rows), 1)

    def test_the_span_slices_the_offending_code_point(self) -> None:
        # ID.1 with the precision the issue asks for: not "this line", but
        # this mark. The span must index the file on disk (I1.3).
        text = f"Text {LATIN} here.\n"
        data = text.encode("utf-8")
        row = self._nfc_rows(text)[0]
        self.assertEqual(data[row["start"]:row["end"]],
                         "\u0301".encode("utf-8"))

    def test_offsets_survive_a_bom(self) -> None:
        # The BOM is counted in the file's bytes but skipped as content;
        # a span that forgot it would be three bytes early.
        text = f"\ufeffText {LATIN} here.\n"
        data = text.encode("utf-8")
        row = self._nfc_rows(text)[0]
        self.assertEqual(data[row["start"]:row["end"]],
                         "\u0301".encode("utf-8"))

    def test_offsets_survive_crlf(self) -> None:
        text = f"First line.\r\nText {LATIN} here.\r\n"
        data = text.encode("utf-8")
        row = self._nfc_rows(text)[0]
        self.assertEqual(row["line"], 2)
        self.assertEqual(data[row["start"]:row["end"]],
                         "\u0301".encode("utf-8"))

    def test_one_diagnostic_per_line(self) -> None:
        # A decomposed manuscript has a mark on every other word. Reporting
        # each one would bury every other diagnostic on the stream, so the
        # report is per line, at the first offence.
        text = "Some " + " ".join([LATIN] * 20) + " end.\n"
        self.assertEqual(len(self._nfc_rows(text)), 1)

    def test_detection_changes_nothing(self) -> None:
        # The other half of I1.2: it does not rewrite.
        text = f"# {LATIN}\n\nBody {GREEK} text.\n"
        self.assertEqual(self._fix(text), text)

    def test_the_repository_is_nfc(self) -> None:
        # A gate on our own corpus, and the reason the sweep below can trust
        # that a finding is a real regression rather than pre-existing dirt.
        docs = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").glob("*.md"))
        self.assertTrue(docs)
        result = subprocess.run(
            [str(MDFIX), "-n", "-q", "--diagnostics", *map(str, docs)],
            capture_output=True, text=True,
        )
        offenders = [json.loads(line) for line in result.stderr.splitlines()
                     if json.loads(line)["rule"] == "unicode.non-nfc"]
        self.assertEqual(offenders, [])


class QuickCheckAgreementTests(NFCTestCase):
    """
    The quick check may answer Maybe, and mdfix reports Maybe as not-NFC.

    That makes one direction a hard requirement and the other merely usual:
    if mdfix says a document is clean, `unicodedata` must agree it is already
    NFC. A false *clean* is a missed report; a false *dirty* is only noise.
    """

    # Long-settled blocks only. libutf carries Unicode 16.0 and CPython's
    # unicodedata may be a version behind, so a code point assigned in the
    # last release or two could differ for reasons that are not a bug here.
    RANGES = (
        (0x00C0, 0x0250),   # Latin-1 Supplement through Latin Extended-B
        (0x0370, 0x0400),   # Greek and Coptic, including polytonic
        (0x0400, 0x0460),   # Cyrillic
        (0x0590, 0x0600),   # Hebrew
        (0x0900, 0x0980),   # Devanagari, with its composition exclusions
        (0x1E00, 0x1F00),   # Latin Extended Additional
        (0x1F00, 0x2000),   # Greek Extended
        (0xAC00, 0xAC64),   # Hangul syllables
        (0x1100, 0x1160),   # Hangul jamo (leading)
    )

    def _samples(self) -> list[str]:
        out = []
        for lo, hi in self.RANGES:
            for cp in range(lo, hi):
                ch = chr(cp)
                out.append(ch)
                out.append(unicodedata.normalize("NFD", ch))
                out.append(ch + "\u0301")
                out.append(ch + "\u0327\u0301")   # ccc 202 then 230: in order
                out.append(ch + "\u0301\u0327")   # ccc 230 then 202: reversed
                # Both of these are NFC_QC=Yes: neither is the second
                # element of any canonical composition, so only the
                # combining-class ordering half of the quick check can catch
                # them. Without this pair, deleting that half of
                # nfc_first_bad left every test in this file passing.
                out.append(ch + "\u0316\u0305")   # ccc 220 then 230: in order
                out.append(ch + "\u0305\u0316")   # ccc 230 then 220: reversed
        return out

    def test_clean_means_already_normalized(self) -> None:
        samples = self._samples()
        self.assertGreater(len(samples), 4000)
        # One document, one line per sample: a single mdfix run rather than
        # thousands, and the line number identifies the sample exactly.
        text = "".join(f"x {s} x\n" for s in samples)
        rows = self._nfc_rows(text)
        reported = {r["line"] for r in rows}

        missed = []
        for i, sample in enumerate(samples, start=1):
            already_nfc = unicodedata.normalize("NFC", sample) == sample
            if i not in reported and not already_nfc:
                missed.append((i, sample))
        self.assertEqual(
            missed[:10], [],
            f"mdfix called these NFC but unicodedata "
            f"{unicodedata.unidata_version} disagrees: {missed[:10]}")


class NormalizeTests(NFCTestCase):
    """I1.2, second half: rewriting is opt-in and lives in L3."""

    def test_off_by_default(self) -> None:
        # I3.3. Also the point of the whole issue: reading a file must not
        # change it.
        text = f"Body {LATIN} text.\n"
        self.assertEqual(self._fix(text), text)

    def test_profiles_do_not_normalize(self) -> None:
        # --normalize-nfc must stay out of --canonical / --technical: those
        # are "safe without looking", and composing a heading moves its anchor.
        text = f"# {LATIN}\n\nBody {LATIN} text.\n"
        for profile in ("--canonical", "--technical"):
            with self.subTest(profile=profile):
                out = self._fix(text, profile)
                self.assertIn("\u0301", out)
                self.assertEqual(
                    out.count(LATIN) + out.count("H\u00e9ading"),
                    text.count(LATIN),
                )
                # Combining form must survive; precomposed must not appear
                # unless it was already there.
                self.assertNotIn("H\u00e9ading", out)

    def test_each_script_composes(self) -> None:
        for name, sample in (("latin", LATIN), ("greek", GREEK),
                             ("hangul", HANGUL)):
            with self.subTest(script=name):
                out = self._fix(f"Body {sample} text.\n", "--normalize-nfc")
                self.assertEqual(out, f"Body {COMPOSED[sample]} text.\n")

    def test_output_matches_the_oracle(self) -> None:
        text = (f"# {LATIN}\n\n"
                f"Greek {GREEK}, Hangul {HANGUL}, and plain ASCII.\n\n"
                f"- item {LATIN}\n- item {GREEK}\n\n"
                "```\ncode " + LATIN + "\n```\n")
        out = self._fix(text, "--normalize-nfc")
        self.assertEqual(out, unicodedata.normalize("NFC", text))

    def test_code_blocks_are_normalized_too(self) -> None:
        # Deliberate, and worth pinning so it is not later "fixed": NFC is a
        # spelling of the same text, not an edit to it, so there is nothing
        # for a fence to protect. Skipping fences would leave a document that
        # is still not NFC after being asked to be.
        text = "```\nliteral " + LATIN + "\n```\n"
        out = self._fix(text, "--normalize-nfc")
        self.assertNotIn("\u0301", out)

    def test_the_fixture_document_matches_the_oracle(self) -> None:
        # End to end over one document carrying headings, a list, a fence, a
        # pipe table and a long paragraph, in three scripts. The per-construct
        # tests above each hold one thing still; this one asserts that the
        # whole file, through the whole pipeline, is exactly what UAX #15
        # says it should be.
        fixture = ROOT / "tests" / "fixtures" / "nfc" / "decomposed.md"
        text = fixture.read_text(encoding="utf-8")
        self.assertNotEqual(unicodedata.normalize("NFC", text), text,
                            "the fixture stopped being decomposed")
        out = self._fix(text, "--normalize-nfc")
        self.assertEqual(out, unicodedata.normalize("NFC", text))

    def test_idempotent(self) -> None:
        # I3.2, for these scripts specifically. The matrix sweeps it too.
        text = f"# {LATIN}\n\nGreek {GREEK} and Hangul {HANGUL}.\n"
        once = self._fix(text, "--normalize-nfc")
        self.assertEqual(self._fix(once, "--normalize-nfc"), once)

    def test_normalizing_silences_the_report(self) -> None:
        out = self._fix(f"# {LATIN}\n", "--normalize-nfc")
        self.assertEqual(self._nfc_rows(out), [])

    def test_already_nfc_is_byte_identical(self) -> None:
        text = "# Title\n\nA plain \u00e9 paragraph.\n"
        self.assertEqual(self._fix(text, "--normalize-nfc"), text)

    def test_the_ir_still_describes_the_file_on_disk(self) -> None:
        # I1.3. --emit-ir never writes, so --normalize-nfc must not reach it:
        # spans that addressed the normalized text would splice into the
        # wrong place in the file the consumer holds.
        text = f"# {LATIN}\n\nBody.\n"
        path = self._write(text)
        data = path.read_bytes()
        result = subprocess.run(
            [str(MDFIX), "--normalize-nfc", "--emit-ir", str(path)],
            capture_output=True, text=True, check=True,
        )
        records = [json.loads(line) for line in result.stdout.splitlines()]
        heading = next(r for r in records if r.get("kind") == "heading")
        self.assertEqual(data[heading["start"]:heading["end"]],
                         f"# {LATIN}".encode("utf-8"))
        self.assertEqual(path.read_bytes(), data)


@unittest.skipUnless(PANDOC, "pandoc not installed")
class AnchorTests(NFCTestCase):
    """
    Why normalization cannot be silent, verified rather than asserted.

    Pandoc's identifier filter drops combining marks, so the two spellings of
    the same heading get different anchors. Normalizing on read would move
    every link to that heading without saying a word.
    """

    def _identifier(self, text: str) -> str:
        result = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json"],
            input=text, capture_output=True, text=True, check=True,
        )
        for block in json.loads(result.stdout)["blocks"]:
            if block["t"] == "Header":
                return block["c"][1][0]
        raise AssertionError("no heading in %r" % text)

    def test_the_two_spellings_anchor_differently(self) -> None:
        decomposed = self._identifier(f"# {LATIN}\n")
        composed = self._identifier(f"# {COMPOSED[LATIN]}\n")
        self.assertNotEqual(decomposed, composed)
        self.assertEqual(decomposed, "heading")
        self.assertEqual(composed, "h\u00e9ading")

    def test_normalizing_moves_the_anchor(self) -> None:
        # So the transform is opt-in, and a caller who takes it must expect
        # to recompute identifiers rather than carry them over.
        out = self._fix(f"# {LATIN}\n", "--normalize-nfc")
        self.assertEqual(self._identifier(out), "h\u00e9ading")


class SegmentOverrunTests(NFCTestCase):
    """
    The vendored normalizer truncates a long combining sequence silently.

    A "segment" ends at the next code point that is both a starter and
    NFC_QC=Yes. A composition exclusion such as U+0958 is a starter with
    NFC_QC=No, so a run of them never ends one, and everything past 128
    decomposed code points is dropped with no error and no short return the
    caller could detect. Reproduced against libutf directly.

    mdfix cannot patch a verbatim copy, so it declines the call. These tests
    pin the decline, because the failure mode they replace is the worst one a
    text tool has: the file comes back shorter and nothing says so.
    """

    # 2700 x U+0958 normalizes to 64 characters instead of 2700.
    PATHOLOGICAL = "\u0958" * 2700

    def test_the_input_really_does_break_the_normalizer(self) -> None:
        # If a libutf refresh ever fixes this, this assertion still holds
        # (the input is still not NFC) but the refusal below should be
        # revisited. Kept adjacent so the two are read together.
        self.assertNotEqual(
            unicodedata.normalize("NFC", self.PATHOLOGICAL), self.PATHOLOGICAL)
        self.assertEqual(
            len(unicodedata.normalize("NFC", self.PATHOLOGICAL)), 5400)

    def test_it_is_refused_rather_than_truncated(self) -> None:
        src = self._write(self.PATHOLOGICAL + "\n")
        out = self.dir / "out.md"
        result = subprocess.run(
            [str(MDFIX), "-q", "--normalize-nfc", str(src), str(out)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refuses to normalize", result.stderr)
        self.assertFalse(out.exists(), "a partial file was left behind")
        self.assertEqual(src.read_text(encoding="utf-8"),
                         self.PATHOLOGICAL + "\n")

    def test_the_refusal_names_a_location(self) -> None:
        text = "A clean first line.\n" + self.PATHOLOGICAL + "\n"
        src = self._write(text)
        result = subprocess.run(
            [str(MDFIX), "-q", "--normalize-nfc", str(src),
             str(self.dir / "out.md")],
            capture_output=True, text=True,
        )
        self.assertIn("line 2", result.stderr)
        self.assertIn(f"byte {len('A clean first line.') + 1}", result.stderr)

    def test_detection_still_works_on_it(self) -> None:
        # Refusing to *rewrite* must not cost the report. I1.2's first half
        # stands on its own.
        rows = self._nfc_rows(self.PATHOLOGICAL + "\n")
        self.assertEqual(len(rows), 1)

    def test_ordinary_stacked_marks_are_untouched_by_the_guard(self) -> None:
        # The guard must not become a second bug. Twenty marks on one letter
        # is already far past anything real and still normalizes.
        text = "a" + "\u0301" * 20 + "\n"
        self.assertEqual(self._fix(text, "--normalize-nfc"),
                         unicodedata.normalize("NFC", text))

    def test_the_whole_repository_decomposed_still_normalizes(self) -> None:
        # The realistic upper bound: every document this project ships, run
        # through NFD and back. If the guard fired here it would be refusing
        # ordinary prose.
        docs = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
        text = "\n".join(unicodedata.normalize("NFD",
                                               p.read_text(encoding="utf-8"))
                         for p in docs)
        self.assertNotEqual(unicodedata.normalize("NFC", text), text)
        self.assertEqual(self._fix(text, "--normalize-nfc"),
                         unicodedata.normalize("NFC", text))


class VendorTests(unittest.TestCase):
    """
    The NFC tables are generated and vendored, like utf_width.c before them.

    Two properties keep that honest: the file must say where it came from, so
    a refresh is possible at all, and every symbol it defines must be either
    static or renamed, so that a build which one day links libutf for real
    cannot quietly bind to this copy instead.
    """

    VENDOR = ROOT / "mdfix" / "vendor" / "utf_nfc.c"

    def test_provenance_is_recorded(self) -> None:
        head = self.VENDOR.read_text(encoding="utf-8")[:2400]
        self.assertIn("VENDORED, DO NOT EDIT", head)
        self.assertIn("github.com/brazilofmux/utf", head)
        self.assertIn("commit", head)

    def test_upstream_names_are_gone(self) -> None:
        text = self.VENDOR.read_text(encoding="utf-8")
        for symbol in ("utf_nfc_is_nfc", "utf_nfc_normalize"):
            with self.subTest(symbol=symbol):
                self.assertNotIn(f" {symbol}(", text)
        self.assertIn("mdfix_nfc_normalize", text)
        self.assertIn("mdfix_nfc_ccc_qc", text)

    def test_nothing_else_is_exported(self) -> None:
        # The header declares three functions. Anything else with external
        # linkage — a table, a helper — is a collision waiting to happen.
        text = self.VENDOR.read_text(encoding="utf-8")
        exported = re.findall(r"^(?!static\b)(?:const |unsigned |int |void |"
                              r"size_t |uint32_t )[^;{]*\b(\w+)\s*[\(\[]",
                              text, re.M)
        self.assertEqual(sorted(set(exported)),
                         ["mdfix_nfc_ccc_qc", "mdfix_nfc_is_nfc",
                          "mdfix_nfc_normalize"])

    def test_every_build_target_compiles_it(self) -> None:
        # Including the sanitizer target. A vendored file added to `mdfix`
        # but not to `asan` links fine and is simply never sanitized, which
        # is the one place a table walk would show a bad index.
        makefile = (ROOT / "mdfix" / "Makefile").read_text(encoding="utf-8")
        for target in ("mdfix:", "asan:"):
            with self.subTest(target=target):
                recipe = makefile.split(target, 1)[1].split("\n\n", 1)[0]
                self.assertIn("vendor/utf_nfc.c", recipe)


if __name__ == "__main__":
    unittest.main()
