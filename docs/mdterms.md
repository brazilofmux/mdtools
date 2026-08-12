# mdterms — glossary and terminology enforcement

Status: shipped, 2026-08-12. Answers issue #16 for forbidden spellings.

mdterms finds terminology violations in **prose** and hands mdfix an edit list
to fix them. It is the first tool that writes, and it writes by asking:

```console
$ mdterms chapter.md
chapter.md:1: 'SLOW32' should be 'SLOW-32'
chapter.md:3: 'pandoc' should be 'Pandoc'
chapter.md:15: 'SLOW32' should be 'SLOW-32' (inside a code span)  [not auto-fixable]

$ mdterms --edits chapter.md | mdfix --apply-edits -i chapter.md
```

The tool that decides what to change is never the tool that writes the file.
mdfix validates every edit — bounds, overlap, encoding, staleness — and
refuses any that would leave the document needing a required repair.

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
```

`aliases` and `forbidden` differ in intent, and the difference is the point.
An alias is a spelling you tolerate — prosevary freezes it so a paraphrase
cannot touch it. A forbidden variant is one you want gone.

`case_sensitive: true` is what makes a capitalization rule expressible:
`Pandoc` preferred with `pandoc` forbidden differ only in case.

Refused at load, because each would make a fix non-deterministic: a spelling
listed as both alias and forbidden, a term that forbids its own preferred
spelling, and the same term defined twice.

## Commands

| | |
|---|---|
| `mdterms FILE...` | Human report. Exit 1 if anything was found |
| `mdterms --diagnostics FILE...` | JSONL, per [diagnostics.md](diagnostics.md) |
| `mdterms --edits FILE` | An edit list for `mdfix --apply-edits` |
| `mdterms --freeze` | Every spelling prosevary must preserve |

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

## Not yet

The rest of #16: first-use definitions, acronym introduction, repository
consistency reports, and SARIF output. The `terms.forbidden` rule is the only
one emitted so far.
