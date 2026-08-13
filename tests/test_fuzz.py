"""
The generative sweep (issue #10).

`fuzz.py` holds the generator and the properties; this runs a bounded slice of
it as part of `make test`, and `make fuzz` runs a much deeper one under the
sanitizers.

Two budgets, because the properties are not equally cheap. The mdfix-only ones
cost about 40 ms a document; the block-structure one shells out to Pandoc for
every flag set and costs several times that. So the fast set sweeps widely and
the Pandoc set sweeps a slice — the deep run is where breadth against Pandoc
comes from.

**A failure here prints a shrunk reproducer, and that reproducer belongs in
`test_fuzz_regressions.py`.** A seed number is not a regression test: change
the generator and the same number is a different document. This file finds
things; that file remembers them.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fuzz

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"

# Deliberately modest, and deliberately fixed. `make test` is run constantly
# and a suite that takes a minute longer is a suite people stop running; the
# depth lives in `make fuzz`.
FAST_SEEDS = range(150)
PANDOC_SEEDS = range(40)


class FuzzTestCase(unittest.TestCase):
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
        self.runner = fuzz.Runner(MDFIX, Path(self.tmp.name))

    def _report(self, failures) -> str:
        lines = []
        for seed, violations, shrunk in failures:
            rules = ", ".join(sorted({rule for rule, _ in violations}))
            lines.append(f"\nseed {seed} breaks {rules}")
            for rule, detail in violations:
                lines.append(f"    {rule}: {detail}")
            lines.append(f"  shrunk to:\n{shrunk.decode('utf-8', 'replace')}")
        lines.append("\nAdd the shrunk input to tests/test_fuzz_regressions.py "
                     "— a seed number is not a regression test.")
        return "\n".join(lines)


class FastSweepTests(FuzzTestCase):
    def test_generated_documents_hold_the_invariants(self) -> None:
        # No crash, I3.2 idempotence, I4.1 totality, I1.3 bounds, I5.1
        # identity. Every one of these already had example tests; the point of
        # generating inputs is that examples share their author's blind spots.
        original = fuzz.PANDOC
        fuzz.PANDOC = None                      # keep this slice cheap
        try:
            failures = fuzz.sweep(self.runner, FAST_SEEDS)
        finally:
            fuzz.PANDOC = original
        self.assertEqual(failures, [], self._report(failures))


@unittest.skipUnless(fuzz.PANDOC, "pandoc not installed")
class PandocSweepTests(FuzzTestCase):
    def test_optional_transforms_preserve_block_structure(self) -> None:
        # I3.1 against the oracle, on documents nobody chose. This is the
        # property that caught `--wrap` inventing an OrderedList out of a
        # sentence ending in a number, and `--canonical` turning a definition
        # list into a paragraph. Neither showed up as an idempotence failure:
        # both were perfectly stable, and wrong.
        failures = fuzz.sweep(self.runner, PANDOC_SEEDS)
        self.assertEqual(failures, [], self._report(failures))


class GeneratorTests(FuzzTestCase):
    """The sweep is only worth its runtime if it is really varied."""

    def test_seeds_are_deterministic(self) -> None:
        # A flaky fuzzer is worse than none: the failure that cannot be
        # reproduced is the one that gets ignored.
        self.assertEqual(fuzz.case(7), fuzz.case(7))
        self.assertNotEqual(fuzz.case(7), fuzz.case(8))

    def test_documents_are_mostly_distinct(self) -> None:
        seen = {fuzz.case(s) for s in FAST_SEEDS}
        self.assertGreater(len(seen), len(FAST_SEEDS) * 0.9)

    def test_the_corpus_reaches_the_awkward_constructs(self) -> None:
        # Without this, trimming BLOCKS one day would quietly narrow the sweep
        # to prose and everything would still be green.
        corpus = b"\n".join(fuzz.case(s) for s in FAST_SEEDS)
        for needle in (b"```", b"~~~", b"|---|", b"    indented",
                       b"> quote", b"[^f", b"](", b"---\ntitle:",
                       b"  \n", b"\t\n", b"<div>", b"$$"):
            with self.subTest(construct=needle):
                self.assertIn(needle, corpus)

    def test_the_corpus_is_not_all_ascii(self) -> None:
        # Code point ranges, not literal strings. The first version searched
        # for a literal Hangul syllable and failed: the generator's is
        # decomposed jamo and the one written here was precomposed, so two
        # spellings of the same syllable did not match. Ranges say what the
        # test means and cannot be tripped by normalization form.
        ranges = {
            "CJK": (0x4E00, 0x9FFF),
            "Greek": (0x0370, 0x03FF),
            "Hangul jamo": (0x1100, 0x11FF),
            "combining marks": (0x0300, 0x036F),
            "Hebrew": (0x0590, 0x05FF),
            "emoji": (0x1F300, 0x1FAFF),
        }
        text = b"".join(fuzz.case(s) for s in FAST_SEEDS).decode("utf-8",
                                                                "replace")
        points = {ord(c) for c in text}
        for name, (lo, hi) in ranges.items():
            with self.subTest(script=name):
                self.assertTrue(any(lo <= p <= hi for p in points),
                                f"no {name} in the generated corpus")

    def test_shrinking_actually_shrinks(self) -> None:
        # A shrinker that returns its input is a shrinker nobody notices is
        # broken, because the tests still pass and the reports get longer.
        big = b"\n".join([b"# Heading", b"", b"para one", b"", b"para two",
                          b"", b"1. one 2", b"2. "])
        violations = self.runner.violations(big)
        if not violations:                      # the bug it came from is fixed
            self.skipTest("no violation to shrink")
        self.assertLess(len(self.runner.shrink(big, violations)), len(big))


if __name__ == "__main__":
    unittest.main()
