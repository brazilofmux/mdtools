# Structural IR — schema `mdtools-ir-1`

Status: shipped, 2026-08-12. Implements the reader half of the boundary in
[dialect-policy.md](dialect-policy.md) §2.

`mdfix --emit-ir file.md` writes the block structure of a Markdown file as
JSONL on stdout and touches nothing on disk. It is the interface every other
tool is meant to consume instead of re-deriving the grammar.

```console
$ mdfix --emit-ir README.md | head -3
{"kind":"document","schema":"mdtools-ir-1","source":"README.md","bytes":3184,"lines":118}
{"kind":"heading","start":0,"end":9,"line":1,"endLine":1,"protected":false,"level":1,"text":"mdtools"}
{"kind":"paragraph","start":11,"end":76,"line":3,"endLine":3,"protected":false}
```

## Guarantees

These are the properties a consumer may rely on, and each has a test.

1. **Spans slice the source exactly.** `source_bytes[start:end]` is the block's
   text. Offsets are byte offsets into the file **as it exists on disk**, not
   into a normalized copy, so CRLF files and files without a final newline
   locate correctly.
2. **`end` excludes the line terminator.** A consumer splicing a replacement
   never has to guess whether it owns the newline.
3. **Records are non-overlapping and in source order.** Blank lines between
   blocks are simply not covered by any record; the gaps are separators.
4. **The first record is the header** (`"kind":"document"`), carrying the
   schema name so a consumer can refuse a version it does not understand.
   Several files may share one stream — each begins a new header.
5. **Read-only.** `--emit-ir` writes no files and prints no summary; combining
   it with `-i` or `--canonical-lint` is an error rather than a surprise.

## Common fields

| Field | Meaning |
|---|---|
| `kind` | Block kind, from the table below |
| `start`, `end` | Byte offsets, half-open, into the original file |
| `line`, `endLine` | 1-based inclusive line numbers |
| `protected` | `true` when mdfix reproduces the block byte for byte |

`protected` is the field worth explaining. The IR describes **what mdfix
actually does**, not an idealized parse. A `true` means no fixer will alter a
byte inside the block; a `false` means prose passes may rewrite inside it. So
the compatibility table in dialect-policy §7 is machine-readable rather than
something a consumer has to rediscover by experiment — and when a §7 gap is
closed, the flag flips and the consumer sees it.

## Block kinds

| `kind` | Extra fields | `protected` | Pandoc block |
|---|---|---|---|
| `document` | `schema`, `source`, `bytes`, `lines` | — | header record |
| `frontmatter` | | `true` | metadata |
| `heading` | `level`, `text` | `false` | `Header` |
| `paragraph` | | `false` | `Para` |
| `list` | | `false` | `BulletList` / `OrderedList` |
| `block_quote` | | `false` | `BlockQuote` |
| `code_fence` | `unterminated` | `true` | `CodeBlock` |
| `code_indented` | | `true` | `CodeBlock` |
| `table` | `form` | see below | `Table` |
| `line_block` | | `false` | `LineBlock` |
| `raw_html` | `htmlKind` | `true` | `RawBlock` |
| `thematic_break` | | `true` | `HorizontalRule` |

`table.form` is one of `pipe`, `simple`, `grid`, `multiline`. The last three
are `protected`; **`pipe` is not** — mdfix rewrites punctuation inside pipe
cells (dialect-policy §7 gap 4). `htmlKind` is one of `comment`, `cdata`,
`processing-instruction`, `declaration`, `element`.

`heading.text` is the content after the marker with a closing `#` run removed,
so `## Sub ##` yields `Sub`. A `#` that is part of the text survives: `# C#`
yields `C#`.

### Pipe tables and line blocks

Both start with `|`, and the delimiter row is the only thing that separates
them. Pinned with `pandoc -t json`:

```text
| a | b | / |---|---| / | 1 | 2 |   -> Table
| a | b | / | 1 | 2 |               -> LineBlock
```

Distinguishing them is new in this schema. mdfix's fixer still treats both as
prose, which is why both carry `"protected": false` — the IR reports the
structure correctly while being honest that the fixer does not yet respect it.

## Known divergences from Pandoc

The IR is mdfix's block segmentation, and mdfix is a line classifier rather
than a full parser. Where the two disagree today, the IR reports `paragraph`
and Pandoc reports something richer:

| Construct | Pandoc | IR | Consequence |
|---|---|---|---|
| Setext heading (`Title` / `=====`) | `Header` | `paragraph` | missing from outlines |
| Definition list | `DefinitionList` | `paragraph` | not queryable |
| Pipe table without leading `\|` | `Table` | `paragraph` | missing from table queries |
| Display math `$$` | `Para` with `Math` | `paragraph` | §7 gap 2 |
| Raw LaTeX block | `RawBlock` | `paragraph` | §7 gap 3 |

Every divergence is in the same direction: the IR under-reports structure and
never invents it. That is the safe side for a reader, but it is under-reporting
all the same, and each row is pinned by a test so closing one is a deliberate
change rather than a surprise.

These are **not** fixed by adding grammar to the consumer. They are fixed in
`mdfix.rl` — that is the whole point of dialect-policy §2 — and closing each
one benefits every consumer at once.

## Stability

`mdtools-ir-1` may gain **new optional fields** and **new block kinds** without
a schema bump; a consumer must ignore fields it does not recognize and should
treat an unknown `kind` as opaque-but-located. Removing a field, changing a
field's meaning, or changing what `start`/`end` measure requires a new schema
name, which the header record makes detectable.

## Not in schema 1

- **Inline structure.** Links, images, footnote references, citations, emphasis
  and inline code are not represented. A consumer that needs them today must
  slice the span and scan it, which is exactly the grammar leak the boundary
  exists to prevent — so this is the next thing to add, not a permanent shape.
- **Nesting.** Records are a flat sequence. A list is one record, not a tree of
  items, and blocks nested inside list items or block quotes are not emitted
  separately. Tight versus loose lists are not represented. **`protected` on a
  flat parent is therefore not a byte-level freeze map for nested verbatim
  constructs** (fenced code, raw HTML, grid tables inside a list item still
  freeze in `process()` but are invisible as separate IR records). Do not
  treat `list.protected == false` as “everything under this span is rewritable.”
- **The applier half.** `--apply-edits` and the edit schema are issue #12.
