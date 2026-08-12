# mdquery — structural queries over Markdown

Status: shipped, 2026-08-12. Answers issue #15 for block structure; inline
structure is not covered yet, see [Limits](#limits).

mdquery answers questions about a Markdown file's structure and extracts parts
of it by name. It is **read-only** and it contains **no Markdown grammar** —
every fact it reports comes from `mdfix --emit-ir`, per
[dialect-policy.md](dialect-policy.md) §2. `tests/test_mdquery.py` enforces
that by scanning this package for block-level patterns.

## Commands

```console
$ mdquery outline docs/ir-schema.md
docs/ir-schema.md:1: # Structural IR — schema `mdtools-ir-1`  [structural-ir-schema-mdtools-ir-1]
docs/ir-schema.md:17:   ## Guarantees  [guarantees]
docs/ir-schema.md:35:   ## Common fields  [common-fields]
```

| Command | What it does |
|---|---|
| `outline FILE...` | Heading tree with levels, identifiers, and spans |
| `blocks FILE...` | Every block, with filters |
| `section FILE --id SLUG` | Print one section's source text |
| `stats FILE...` | Block counts by kind |

`--json` switches any command to JSONL, one object per result. That is the
machine interface; the human output is for reading.

### Filters

`blocks` takes the filters #15 asks for, and they compose:

```console
$ mdquery blocks docs/dialect-policy.md --kind table --form pipe
$ mdquery blocks README.md --under build-install
$ mdquery blocks README.md --kind code_fence --protected
```

| Filter | Selects |
|---|---|
| `--kind KIND` | One block kind; repeatable |
| `--form FORM` | Tables of one form: `pipe`, `simple`, `grid`, `multiline` |
| `--under SLUG` | Blocks inside that heading's section, the heading included |
| `--protected` / `--unprotected` | Whether mdfix reproduces the block byte for byte |

`--protected` comes straight from the IR, so it is a claim about what the
fixer actually does rather than about what the construct is in principle. A
pipe table is a table and is *not* protected, because mdfix rewrites
punctuation inside its cells.

### Sections

`section` extracts by heading identifier, and a section runs to the next
heading at the same or a shallower level:

```console
$ mdquery section docs/ir-schema.md --id stability
## Stability

`mdtools-ir-1` may gain **new optional fields** …
```

The output is the file's own bytes, sliced by span — not a re-rendering. An
unknown identifier exits 1 and lists the ones that exist.

## Heading identifiers

Identifiers follow Pandoc's `auto_identifiers`, which
[dialect-policy §3](dialect-policy.md) pins, so an anchor mdquery reports is
the anchor Pandoc will emit. The rules were read off `pandoc -t json`:

| Heading | Identifier |
|---|---|
| `Simple Heading` | `simple-heading` |
| `Punctuation: colons, commas!` | `punctuation-colons-commas` |
| `2. Numbers first` | `numbers-first` |
| `123` | `section` |
| `Héading with accents` | `héading-with-accents` |
| `Emoji 🎉 here` | `emoji-here` |
| `C#` | `c` |
| duplicates | `-1`, `-2`, … in document order |

Identifiers are computed from `heading.plain`, which mdfix supplies with
inline markup already stripped — mdquery never parses Markdown to get there.

Two details worth knowing. Pandoc does **not** Unicode-normalize, so a
precomposed `Héading` gives `héading` while the decomposed spelling loses its
combining mark and gives `heading`; mdquery matches that rather than being
more principled than the tool it has to agree with. And `+smart` is folded
first, so `A--B` gives `ab` — the en dash it becomes is not a slug character.

Reference links are an exception worth knowing about: `## [text][id]` slugs
as `textid`, not `text`, because Pandoc computes header identifiers before it
resolves references. mdquery matches that deliberately.

## Limits

These are consequences of what the IR carries today. Each is fixed in
`mdfix.rl` or the schema, never by teaching mdquery Markdown.

- **No inline structure.** Links, images, footnote references and citations
  are not queryable — schema 1 stops at blocks. Identifiers, however, now
  match Pandoc: mdfix supplies `heading.plain` with inline markup already
  stripped, so `## [link](url)` and `## _emphasis_` slug correctly without
  mdquery knowing what a link is. Verified across Latin, Greek, Cyrillic,
  CJK, Hangul and mathematical text.
- **Containers hide nested blocks.** Schema 1 is a flat sequence, so a fenced
  block inside a list item is part of the `list` record. Queries over code
  blocks or protection therefore under-report inside containers. mdquery
  **warns on stderr** when it sees a container whose span holds a fence, so a
  result never looks exhaustive when it is not; `-q` silences it.
- **Under-reported constructs.** Setext headings, definition lists, display
  math and raw LaTeX arrive as `paragraph`, so they are invisible to
  `--kind`. See ir-schema.md, "Known divergences from Pandoc".

## Finding mdfix

`$MDFIX` first, then a sibling `mdfix/mdfix` build, then `PATH`. A schema the
package does not recognize is refused rather than guessed at — that is what
the IR header record is for.
