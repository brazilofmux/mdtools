# mdlinks — the Markdown link graph

Status: shipped, 2026-08-12. Answers issue #14 for checking and for repair
after renames. External-link validation is still out of scope.

mdlinks finds links that will not resolve. It is read-only, and it is the
first consumer of the IR's inline records — links are inline, so before those
existed mdlinks could not have been written without a second parser.

```console
$ mdlinks README.md docs/*.md
docs/guide.md:14: error: no heading with anchor #instalation
docs/guide.md:14: note: did you mean '#installation'? (the closest anchor in that file)
docs/guide.md:22: error: no definition for [spec]
docs/guide.md:41: warning: [legacy] is defined but never used
```

## Anchors are Pandoc's

Anchors come from mdquery's slug rules, which follow Pandoc's
`auto_identifiers`. **A link mdlinks accepts is one Pandoc resolves** — a link
checker that disagrees with the renderer is worse than none.

That includes the awkward parts: duplicate headings get `-1`, `-2` suffixes,
punctuation is dropped, and a heading whose text is a link uses the link's
text. Percent-escapes in the fragment are decoded before comparison.

## What it checks

| Rule | Severity | |
|---|---|---|
| `links.broken-anchor` | error | `#anchor` no heading provides |
| `links.undefined-reference` | error | `[x][label]`, collapsed `[text][]`, or shortcut `[label]` with no definition |
| `links.undefined-footnote` | error | a footnote reference with no definition |
| `links.missing-file` | error | a relative path not on disk (inline **or** via a resolved reference definition) |
| `links.unused-definition` | warning | a definition nothing uses |
| `links.unused-footnote` | warning | a footnote definition nothing references |

Errors exit 1; warnings alone exit 0 unless `--warnings` is passed.

Reference-style and shortcut links are **followed** to their definition
destination, then checked the same way as inline links (missing file, broken
anchor). Collapsed forms `[text][]` resolve with the link text as the label.
Labels match CommonMark rules (whitespace collapsed, case-insensitive).

Undefined **shortcuts** (`[brackets]` with no matching definition) are
errors. CommonMark would leave them as plain text; mdlinks treats them as
broken references so accidental editorial markers stay visible.

Links inside **table cells, list items and headings** are checked, because the
inline records cover them. That was the point of scanning those: five of the
eleven links in this repository's own architecture document live in tables.

## What it does not do

**External URLs are never fetched.** `http://…` is left alone — checking it
would make the tool slow, non-deterministic and dependent on the network,
none of which belongs in a build gate.

**A file outside the run is not judged.** If `a.md` links to `b.md#anchor` and
`b.md` was not passed in, the anchor is not checked: its headings are unknown,
and claiming the link is broken would be a false positive. For a link checker
that is the expensive kind of mistake — one false alarm and the tool gets
switched off. Pass the whole set to check across files.

**Nothing is renamed or moved.** Repair edits the *link*, never the file it
points at. If the answer is that the file should move back, that is a decision
mdlinks has no business making.

## Repair

Two failures are repairable, and they are the two #14 names: a heading was
renamed, or a file was moved. Both show up as a `note:` line under the finding,
so the preview costs nothing and needs no flag.

`--edits` turns the confident ones into an edit list for
[`mdfix --apply-edits`](edit-schema.md). The tool that decides what to change
is never the tool that writes the file:

```console
$ mdlinks --edits docs/guide.md README.md docs/*.md | mdfix --apply-edits -i docs/guide.md
```

`--edits` names one file because the applier reads one document. The other
paths are not decoration — they are the scope a repair searches, the same set
the checker judges against.

### How a candidate is chosen

For a **broken anchor**, in this order, first hit wins:

1. The heading's identifier for the text as written — `#Installation Guide`
   for `installation-guide`. Deterministic, and the most common way a
   hand-written anchor is wrong.
2. The same anchor, differently cased.
3. The closest anchor by edit distance, within `len // 4`.

For a **missing file**, a file of that name among the documents given or
sitting beside them. The `#fragment` is carried across unchanged.

### When it declines

- **More than one candidate.** #14's rule is that ambiguous targets require
  human choice, so mdlinks prints every candidate and emits nothing. A link
  fixer that is usually right is worse than one that is sometimes silent: a
  wrong repair is a plausible diff pointing at the wrong section.
- **No candidate within the bound.** A distant anchor is not offered at all.
- **A destination that cannot be written bare** — one needing `<>` or
  backslash escapes. Re-spelling it means knowing how the original was
  spelled, which is grammar mdlinks does not hold.
- **`links.undefined-reference`.** Renaming the label to whichever definition
  is closest would point the text at a different destination entirely. That is
  not a repair; it is a guess with a convincing diff. The honest fix is
  usually to add the missing definition.

### Two passes

A file that moved *and* whose anchor is wrong needs two runs: the anchor
cannot be checked until the path resolves. Repair converges — run it until
the report is clean.

## Commands

| | |
|---|---|
| `mdlinks FILE...` | Human report |
| `mdlinks --diagnostics FILE...` | JSONL, per [diagnostics.md](diagnostics.md) |
| `mdlinks --graph FILE...` | The graph: anchors, definitions, footnotes, every link and footnote ref |
| `mdlinks --edits FILE OTHERS...` | Edits repairing `FILE`, for `mdfix --apply-edits` |
| `--warnings` | Fail on warnings too |
