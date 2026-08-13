# mdterms — glossary and terminology enforcement

Status: shipped, 2026-08-13. Answers issue #16.

mdterms finds terminology violations in **prose** and hands mdfix an edit list
to fix them. It is the first tool that writes, and it writes by asking:

```console
$ mdterms chapter.md
chapter.md:1: 'SLOW32' should be 'SLOW-32'
chapter.md:3: 'pandoc' should be 'Pandoc'
chapter.md:15: 'SLOW32' should be 'SLOW-32' (inside a code span)  [not auto-fixable]

$ mdterms --diff chapter.md          # see the changes
$ mdterms --fix  chapter.md          # make them
```

The tool that decides what to change is never the tool that writes the file.
mdfix validates every edit — bounds, overlap, encoding, staleness — and
refuses any that would leave the document needing a required repair.

`--fix` builds an edit list and hands it to `mdfix --apply-edits`; it never
writes the file itself. `--edits` still prints that list if you want it. See
[edit-schema.md](edit-schema.md) and [cli.md](cli.md).

## Only prose

A forbidden spelling inside a code block, a table cell, a link definition or
front matter is left alone. Not because mdterms knows what a fence is — it
holds no Markdown grammar at all — but because the IR says those are not
prose. `tests/test_mdterms.py` enforces that with the same scan mdquery is
held to.

Headings **are** checked: a heading is where a term is most visible, and a
document titled with the forbidden spelling should not pass. Fixing one
changes its anchor, so links to it need updating.

Prose nested in a list item is checked too, which needed schema 3 — before
nested records, a term inside a bullet was unreachable.

**Block quotes are not checked.** Schema 3 does not emit nested quote prose,
so the whole quote is one opaque IR span. That is deliberate until quote
nesting lands; quoted manuscript text with product names is left alone.

## The glossary

Extends the schema prosevary already reads, so one file serves both:

```yaml
terms:
  - term: SLOW-32              # the preferred spelling
    aliases: [Slow-32]         # acceptable; frozen, never rewritten
    forbidden: [SLOW32, slow32]  # rewritten to `term`
    case_sensitive: true       # default
    expansion: SLOW 32-bit ISA # must be introduced at first use
    exempt: ["CHANGELOG.md"]   # patterns where this term's rules do not apply
```

`aliases` and `forbidden` differ in intent, and the difference is the point.
An alias is a spelling you tolerate — prosevary freezes it so a paraphrase
cannot touch it. A forbidden variant is one you want gone.

`case_sensitive: true` is what makes a capitalization rule expressible:
`Pandoc` preferred with `pandoc` forbidden differ only in case.

Refused at load, because each would make a fix non-deterministic: a spelling
listed as both alias and forbidden, a term that forbids its own preferred
spelling, the same term defined twice, and a term that expands to itself —
that last one could never be satisfied, so it would report forever.

## First use: `expansion`

First-use definitions and acronym introduction are one rule seen twice. An
acronym is a term whose definition is the words it stands for, and *"define
it the first time you use it"* is the same instruction either way. So there is
one field, and one question: at the first prose use of this term **in this
document**, are those words next to it?

```console
$ mdterms chapter.md
chapter.md:3: 'IR' is used before it is introduced; write 'intermediate representation (IR)' at first use
```

Two shapes count, and only two, because a rule that accepts anything nearby
stops being a rule:

```markdown
The intermediate representation (IR) is a stream of records.
We emit IR (intermediate representation) here.
```

The expansion is compared case-insensitively — it may start a sentence — while
the term itself is matched however `case_sensitive` says.

**Per document, not per repository.** A term introduced in chapter 3 is not
introduced for a reader who opened chapter 7. `--report` is the cross-file
view.

**Never auto-fixed.** Rewriting a sentence to introduce a term is a wording
decision, and mdterms only makes the changes that have exactly one right
answer.

A term inside inline code or a fence is not a use, so it neither triggers the
rule nor satisfies it. `` `intermediate representation (IR)` `` does not
introduce anything: a reader sees a literal, not a definition.

## Exceptions: `exempt`

Glob patterns where a term's rules — all of them, not just the introduction —
do not apply. A changelog quoting old release notes should not be told to
introduce an acronym it is only citing.

A pattern with no `/` is a **name** and matches wherever the file lives, so
`CHANGELOG.md` keeps working the day someone passes `docs/CHANGELOG.md`. A
pattern containing `/` is matched against the whole path, because that is
someone being specific about where.

## Repository consistency

```console
$ mdterms --report docs/*.md
IR: used in docs/architecture.md, docs/ir-schema.md
  introduced in docs/ir-schema.md
```

The question a per-file report cannot answer: a term introduced in one chapter
and assumed in the next reads fine in isolation and badly in order.

It **reports rather than judges** — exit 0 even on an untidy corpus, because a
gate that fires on every report is one nobody runs. Add `--diagnostics` for
JSONL.

## Commands

| | |
|---|---|
| `mdterms FILE...` | Human report. Exit 1 if anything was found |
| `mdterms --diagnostics FILE...` | JSONL, per [diagnostics.md](diagnostics.md) |
| `mdterms --diff FILE...` | What `--fix` would change |
| `mdterms --fix FILE...` | Apply the unambiguous fixes, via mdfix |
| `mdterms --edits FILE` | An edit list for `mdfix --apply-edits` |
| `mdterms --sarif FILE...` | SARIF 2.1.0, for a CI system that ingests it |
| `mdterms --report FILE...` | Which files use each term, and which introduce it |
| `mdterms --freeze` | Every spelling prosevary must preserve |

See [cli.md](cli.md) for the verbs and exit codes every tool shares.

`--edits` takes one file at a time, because the applier applies to one
document and its `bytes` header is that document's size.

## What is not fixed automatically

**Matches inside protected inlines.** A term inside `` `SLOW32` ``, a link or
image destination, an autolink, or a raw HTML tag is reported and never
auto-fixed — rewriting those would change a literal or a URL. The message
says `(inside a protected span; not fixed automatically)`. This is the one
place mdterms looks at inline syntax, scoped to `check.py` and asserted by
the boundary test; proper handling needs inline records in the IR.

**Overlapping findings.** The whole overlapping cluster is dropped — not
“keep the first.” Silently picking a winner would make the result depend on
glossary order, and the applier refuses overlapping edits anyway.

## Rules

| Rule | Severity | Fixable | |
|---|---|---|---|
| `terms.forbidden` | warning | yes | a spelling the glossary forbids |
| `terms.undefined-acronym` | warning | no | a term used before its `expansion` |

## Not yet

**Immutable technical terms** beyond what `aliases` already freezes, and a
glossary in TOML as well as YAML. Neither is blocking: `aliases` covers the
freeze set prosevary consumes, and one glossary format is one fewer thing to
keep in step.
