# The CLI contract

Status: shipped, 2026-08-12. Answers the CLI half of issue #12.

Seven tools, one surface. Every tool answers the same three questions in the
same words, and reports the same way when it cannot answer at all.

## Verbs

| Verb | Meaning |
|---|---|
| `--check` | Report. Change nothing. **The default** |
| `--diff` | Show what `--fix` would change. Change nothing |
| `--fix` | Make the changes |

Not every tool has all three. `mdcheck` reports things no tool can decide how
to repair — a missing asset, an anchor two files disagree about — so it has
only `--check`. It accepts the flag anyway, so a script can pass the verb
without knowing which tool it is driving.

| | `--check` | `--diff` | `--fix` |
|---|---|---|---|
| `mdterms` | ✓ | ✓ | ✓ |
| `mdlinks` | ✓ | ✓ | ✓ |
| `mdcheck` | ✓ | | |
| `mdquery` | — read-only by construction | | |
| `mdfix` | `-n` | `--diff` (with `--apply-edits`) | default |

`mdfix` predates this and keeps its own spelling: it *is* the fixer, so
fixing is what it does without being asked.

## `--fix` never writes the file

It builds an edit list and hands it to `mdfix --apply-edits`, which checks
spans, encoding, staleness and L2 conformance before splicing. The tool that
decides what to change is never the tool that writes it.

That is not ceremony. It means a repair cannot skip validation by taking a
shortcut through a second write path, and it means `--fix` fails loudly when
mdfix is missing rather than falling back to writing bytes itself. There is a
test for exactly that.

```console
$ mdlinks --diff docs/*.md          # see it
$ mdlinks --fix docs/*.md           # do it
```

The plumbing form is still there when you want the edit list itself —
`mdterms --edits FILE` and `mdlinks --edits FILE` write `mdtools-edits-1` on
stdout, per [edit-schema.md](edit-schema.md). `--fix` is that pipeline with
the pipe already connected.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean |
| `1` | Findings |
| `2` | The tool could not run |

**The distinction that matters is 1 versus 2.** A gate that treats them alike
turns *"your glossary file has a syntax error"* into *"your prose is fine"*,
and the build goes green on a check that never ran. So an unreadable
`mdtools.toml`, an unknown setting in it, a missing input, a `--config` naming
a file that is not there, and an mdfix that cannot be found are all `2` — in
every tool, with a test that sweeps them.

After `--fix`, `1` means *still findings*, not *the fix failed*. `mdterms
--fix` returns 1 when a forbidden spelling was inside a code span, because
that one was never fixable; the file was still repaired everywhere else.

## Configuration

Every tool takes `--config PATH`. Without it, `mdtools.toml` is discovered by
walking up from the first input file, stopping at the first `mdtools.toml` or
`.git`.

Precedence is the obvious one, and it runs the same way everywhere:

```text
explicit flag   →   mdtools.toml   →   discovery / default
```

A `--config` naming a file that does not exist is an error rather than a
fall back to discovery. A caller who names a config file and silently gets a
different one has no way to notice.

`mdtools config` prints what was resolved and from where.

## Machine-readable output

| Flag | What it emits |
|---|---|
| `--diagnostics` | The shared diagnostics stream — see [diagnostics.md](diagnostics.md) |
| `--json` | A tool's own results as JSONL (`mdquery` only) |
| `--sarif` | SARIF 2.1.0 (`mdcheck` only) |
| `--edits` | An edit list — see [edit-schema.md](edit-schema.md) |

`--diagnostics` and `--json` are different things and are spelled differently
on purpose. `--diagnostics` is *findings*, one schema shared by every tool.
`--json` is *answers* — mdquery's outline is not a complaint about anything.

## Streams

Results go to **stdout**. Progress, summaries and hints go to **stderr**, so a
pipeline gets only the data.

That matters most for `--diagnostics`, `--edits` and `--emit-ir`, where a
human-readable line mixed into the stream would make it unparseable — a
consumer cannot skip what it cannot recognize. See diagnostics.md §"Which
stream".

## Adding a tool

Import the contract; do not re-implement it:

```python
from mdtools_cli.contract import (
    FINDINGS, OK, USAGE, add_common, add_verbs, apply_edits, fail,
    resolve_config, resolve_mdfix,
)
```

`tests/test_cli_contract.py` sweeps the tools rather than testing each one, so
a new tool that does not join in fails it — including a check that no tool
spells an exit code as a bare integer. A contract checked per tool is a
convention, and conventions drift.
