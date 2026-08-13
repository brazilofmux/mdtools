"""
Generated documents, and the properties mdfix must hold over all of them.

Issue #10 asks for fuzzing. This is the generator, the property set and the
shrinker; `test_fuzz.py` runs a bounded sweep of it inside `make test` and
`make fuzz` runs a deeper one under the sanitizers.

**Why structured and not random bytes.** L1 refuses anything that is not
well-formed UTF-8, so a byte fuzzer spends its budget being rejected at the
door and never reaches a fixer. Documents are assembled from a vocabulary of
real Markdown constructs — the ones mdfix is intricate about — and then
*damaged*: cut, duplicated, spliced with a stray delimiter. That is how real
files arrive, half-edited, and it is where the bugs were.

**Deterministic.** Fixed seeds, no network, no clock. A failure is
reproducible by number, and CI does not flake.

**Shrunk.** A forty-line random document that breaks an invariant is not a bug
report. `shrink` cuts lines while the same property still fails, which took
every finding here down to two or three lines — small enough to read, and to
copy into `test_fuzz_regressions.py`, which is where they belong permanently.
A seed number is not a regression test: change the generator and seed 630 is a
different document.

**The properties are the architecture's invariants**, not invented for this:
I1.3, I2.1/I3.1, I3.2, I4.1, I5.1. Each already had example tests; the point
of generating inputs is that examples are written by the same person who wrote
the code, and share their blind spots.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

PANDOC = shutil.which("pandoc")

# Block shapes, chosen where mdfix has to make a decision: fence delimiters,
# list markers, table forms, front matter, raw HTML, footnotes, link forms,
# the Chicago triggers, hard breaks, and scripts whose width or normalization
# is not ASCII's.
BLOCKS = [
    "# Heading {n}", "#Heading {n}", "## Sub {n} ##",
    "Setext {n}\n=======", "Setext {n}\n-------",
    "A paragraph of prose number {n} with words.",
    "Prose {n} with [a link](http://x/{n}) and ![img](i{n}.png).",
    "Prose {n} with [ref][r{n}] and [shortcut] and <http://a.b/{n}>.",
    "[r{n}]: ./t{n}.md \"Title\"",
    "Text{n}[^f{n}] here.", "[^f{n}]: Footnote {n}.",
    "- item {n}\n- item {n}b", "* star {n}\n+ plus {n}",
    "1. one {n}\n2. two {n}", "1) paren {n}",
    "- outer {n}\n  - inner {n}\n\n    para in item",
    "> quote {n}\n> more", ">quote{n}", "> > nested {n}",
    "```\ncode {n}\n```", "```python\ncode {n}\n```", "~~~\ntilde {n}\n~~~",
    "```\nunterminated {n}",
    "    indented code {n}",
    "| a | b |\n|---|---|\n| {n} | x |",
    "Col A | Col B\n--- | ---\nv{n} | w",
    "+---+---+\n| a | b |\n+===+===+\n| {n} | y |\n+---+---+",
    "---", "***", "___",
    "| line block {n}\n| second",
    "<div>\nraw html {n}\n</div>", "Inline <b>tag {n}</b> here.",
    "**bold {n}:** value", "*emph {n}*", "Note {n} -> aside",
    "He paused . . . then spoke {n}.", "Dash{n}--dash", "Ellipsis{n}...",
    "e.g. this {n}", "et al. {n}", "Really?  Two spaces {n}.",
    "hard break {n}  \nnext line", "tab break {n}\t\nnext line",
    "Sentence ending in a number {n}. Another sentence follows it here.",
    "漢字とテキスト {n}。",
    "Ελληνικά {n}",
    "Combińing {n} m̈arks", "한 jamo {n}",
    "Math $a+b$ and $$x^2{n}$$", "Emoji \U0001F389 {n}",
    "Zero​width {n}", "RTL אבג {n}",
    "", "   ",
]

# Stray characters spliced in by `damage`: each one is a delimiter mdfix has
# to decide about, so inserting one mid-construct is how a half-edit looks.
STRAYS = ["`", "```", "|", ">", "*", "-", "[", "]", "(", ")", "#", "\\",
          "  ", "\t", "́", "​"]

SEPARATORS = ["\n\n", "\n", "\n\n\n"]

# Flag sets swept. The empty one is the required-only default, which is also
# the baseline every optional set is compared against for I3.1.
FLAG_SETS: Tuple[Tuple[str, ...], ...] = (
    (), ("-w",), ("--canonical",), ("--technical",), ("--wrap=40",),
    ("--editorial",), ("--normalize-nfc",), ("--canonical", "--wrap=60"),
)


def _document(rng: random.Random, nblocks: int) -> str:
    parts: List[str] = []
    if rng.random() < 0.15:
        parts.append("---\ntitle: front {0}\n---".format(rng.randrange(99))
                     if rng.random() < 0.8 else "---\nunclosed: yes")
    for i in range(nblocks):
        parts.append(rng.choice(BLOCKS).format(n=i))
    text = rng.choice(SEPARATORS).join(parts)
    if rng.random() < 0.12:
        text = text.replace("\n", "\r\n")
    if rng.random() < 0.10 and text:
        text = text[:-1]                     # no final newline
    elif not text.endswith("\n"):
        text += "\n"
    if rng.random() < 0.05:
        text = "﻿" + text               # BOM
    return text


def _damage(rng: random.Random, text: str) -> str:
    for _ in range(rng.randrange(1, 4)):
        if not text:
            break
        mode = rng.randrange(5)
        i = rng.randrange(len(text))
        j = min(len(text), i + rng.randrange(1, 12))
        if mode == 0:
            text = text[:i] + text[j:]                       # cut
        elif mode == 1:
            text = text[:i] + text[i:j] * 2 + text[j:]       # duplicate
        elif mode == 2:
            text = text[:i] + rng.choice(STRAYS) + text[i:]  # splice
        elif mode == 3:
            text = text[:i] + text[i:j].upper() + text[j:]   # recase
        else:
            text = text[:i] + "\n" + text[i:]                # break a line
    return text


def case(seed: int) -> bytes:
    """The document for `seed`. Same seed, same bytes, on every machine."""
    rng = random.Random(seed)
    text = _document(rng, rng.randrange(1, 9))
    if rng.random() < 0.5:
        text = _damage(rng, text)
    return text.encode("utf-8")


class Runner:
    """mdfix invocations against one scratch directory."""

    def __init__(self, mdfix: Path, workdir: Path) -> None:
        self.mdfix = str(mdfix)
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _fix(self, data: bytes, flags: Sequence[str]):
        src = self.dir / "in.md"
        out = self.dir / "out.md"
        src.write_bytes(data)
        if out.exists():
            out.unlink()
        result = subprocess.run([self.mdfix, "-q", *flags, str(src), str(out)],
                                capture_output=True)
        return result, (out.read_bytes() if out.exists() else None)

    def _run(self, args: Sequence[str], data: bytes, stdin: bytes = None):
        src = self.dir / "in.md"
        src.write_bytes(data)
        return subprocess.run([self.mdfix, "-q", *args, str(src)],
                              input=stdin, capture_output=True)

    def _blocks(self, data: bytes) -> Optional[list]:
        if not PANDOC:
            return None
        result = subprocess.run([PANDOC, "-f", "markdown", "-t", "json"],
                                input=data, capture_output=True)
        if result.returncode != 0:
            return None
        return [b["t"] for b in json.loads(result.stdout)["blocks"]]

    def violations(self, data: bytes) -> List[Tuple[str, str]]:
        """Every property this input breaks, as (rule, detail) pairs."""
        bad: List[Tuple[str, str]] = []
        baseline = None

        for flags in FLAG_SETS:
            label = " ".join(flags) or "(default)"
            result, out = self._fix(data, flags)
            if result.returncode < 0:
                bad.append(("crash", f"{label}: signal {-result.returncode}"))
                continue
            if result.returncode not in (0, 1, 2):
                bad.append(("exit", f"{label}: rc {result.returncode}"))
                continue
            if out is None:
                continue

            # I3.2: one pass is a fixed point.
            _r2, again = self._fix(out, flags)
            if again is not None and again != out:
                bad.append(("I3.2 idempotence", label))

            # I3.1: an optional transform must not change block structure
            # relative to the required-only output. Comparing against the
            # *input* would flag the required repairs, which exist precisely
            # to change what Pandoc reads (I2.1's stated exception).
            if not flags:
                baseline = self._blocks(out)
            elif baseline is not None:
                shape = self._blocks(out)
                if shape is not None and shape != baseline:
                    bad.append(("I3.1 block structure",
                                f"{label}: {shape} != {baseline}"))

        # I4.1 totality and I1.3 span bounds.
        result = self._run(["--emit-ir"], data)
        if result.returncode < 0:
            bad.append(("crash", f"--emit-ir: signal {-result.returncode}"))
        elif result.returncode == 0:
            rows = [json.loads(line)
                    for line in result.stdout.decode("utf-8").splitlines()]
            if rows:
                total = rows[0].get("bytes", 0)
                cursor = 0
                for record in rows[1:]:
                    if record.get("depth"):
                        continue
                    if record["start"] != cursor:
                        bad.append(("I4.1 totality",
                                    f"{record['kind']} at {record['start']}, "
                                    f"expected {cursor}"))
                        break
                    if record["end"] > total:
                        bad.append(("I1.3 bounds",
                                    f"{record['kind']} ends past {total}"))
                        break
                    cursor = record["end"]
                else:
                    if cursor != total:
                        bad.append(("I4.1 totality",
                                    f"covered {cursor} of {total}"))

        # I5.1: an empty edit list is byte-identical.
        result = self._run(["--apply-edits"], data, stdin=b"")
        if result.returncode < 0:
            bad.append(("crash", f"--apply-edits: signal {-result.returncode}"))
        elif result.returncode == 0 and result.stdout != data:
            bad.append(("I5.1 identity", "empty edit list changed the file"))

        return bad

    def shrink(self, data: bytes, failing: Sequence[Tuple[str, str]]) -> bytes:
        """
        The smallest input still breaking the same rules.

        Rules, not details: the detail names a flag set or an offset and both
        move as lines come out. Requiring the rule set to *remain* (rather than
        match exactly) keeps a reduction that also happens to expose a second
        problem, which is usually the more interesting document.
        """
        rules = {rule for rule, _ in failing}
        lines = data.split(b"\n")
        shrinking = True
        while shrinking and len(lines) > 1:
            shrinking = False
            for i in range(len(lines)):
                trial = b"\n".join(lines[:i] + lines[i + 1:])
                if not trial.strip():
                    continue
                if rules <= {rule for rule, _ in self.violations(trial)}:
                    lines = lines[:i] + lines[i + 1:]
                    shrinking = True
                    break
        return b"\n".join(lines)


# Divergences the sweep knows about and does not re-report.
#
# Same discipline as the transform matrix's pin set: recorded so it cannot be
# mistaken for done, and matched by *shape* rather than by seed number, since
# a seed is only a document until the generator changes.
#
# `tests/test_fuzz_regressions.py` asserts each of these still reproduces, so
# fixing one fails that file and forces this entry out with it.
_LAZY_CONTINUATION = re.compile(rb"(?m)^[ ]{4,}\S")
_ORDERED_MARKER = re.compile(rb"(?m)^\d+[.)][ \t]")


def _blank_before_list_after_continuation(data: bytes) -> bool:
    """
    A paragraph, an indented lazy continuation, then an ordered marker.

    mdfix's required blank-before-list repair fires when a list marker
    directly follows paragraph text, and does not when a lazy continuation
    line sits between them. `--wrap` joins that continuation away, so the
    repair fires on the wrapped output and not on the unwrapped one — the
    same document, two block structures.

    Which way that should resolve is a question about **R2's premise**, not a
    bug with an obvious fix. Pandoc 3.10 reads *no* ordered marker as
    interrupting a paragraph — not even `1.` — so the repair is deliberately
    creating a list the reader would not have seen, which is allowed (I2.1's
    stated exception) but worth deciding on purpose rather than by whether a
    continuation line happened to be joined first.
    """
    return bool(_LAZY_CONTINUATION.search(data)
                and _ORDERED_MARKER.search(data))


KNOWN_DIVERGENCES = (
    ("blank-before-list after a lazy continuation",
     _blank_before_list_after_continuation),
)


def is_known(data: bytes) -> Optional[str]:
    for name, matches in KNOWN_DIVERGENCES:
        if matches(data):
            return name
    return None


def sweep(runner: Runner, seeds: range) -> List[Tuple[int, list, bytes]]:
    """Every failing seed, with its violations and a shrunk reproducer."""
    failures = []
    for seed in seeds:
        data = case(seed)
        bad = runner.violations(data)
        if not bad:
            continue
        shrunk = runner.shrink(data, bad)
        if is_known(shrunk):
            continue
        failures.append((seed, bad, shrunk))
    return failures
