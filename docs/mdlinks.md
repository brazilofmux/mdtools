# mdlinks — the Markdown link graph

Status: shipped, 2026-08-12. Answers issue #14 for checking; repair is not
implemented.

mdlinks finds links that will not resolve. It is read-only, and it is the
first consumer of the IR's inline records — links are inline, so before those
existed mdlinks could not have been written without a second parser.

```console
$ mdlinks README.md docs/*.md
docs/guide.md:14: error: no heading with anchor #instalation
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

**Repair is not implemented.** #14 asks for it; the shape is clear, since the
edit schema already carries what a fix needs. A correct anchor is usually one
slug away from a broken one.

## Commands

| | |
|---|---|
| `mdlinks FILE...` | Human report |
| `mdlinks --diagnostics FILE...` | JSONL, per [diagnostics.md](diagnostics.md) |
| `mdlinks --graph FILE...` | The graph: anchors, definitions, footnotes, every link and footnote ref |
| `--warnings` | Fail on warnings too |
