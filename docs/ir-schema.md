# Structural IR — schema `mdtools-ir-3`

Status: shipped, 2026-08-12. Implements the reader half of the boundary in
[dialect-policy.md](dialect-policy.md) §2.

`mdfix --emit-ir file.md` writes the block structure of a Markdown file as
JSONL on stdout and touches nothing on disk. It is the interface every other
tool is meant to consume instead of re-deriving the grammar.

```console
$ mdfix --emit-ir README.md | head -4
{"kind":"document","schema":"mdtools-ir-3","source":"README.md","bytes":3184,"lines":118}
{"kind":"heading","start":0,"end":9,"line":1,"endLine":1,"protected":false,"level":1,"text":"mdtools","plain":"mdtools"}
{"kind":"gap","start":9,"end":11,"line":1,"endLine":2,"protected":false}
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
3. **Top-level records are total, contiguous, and in source order.**
   Concatenating every `depth`-0 record's span reproduces the input **byte for
   byte**. Nested records (`depth > 0`) live inside their parent's span and do
   not participate — a consumer summing every record would double-count. Nothing is skipped:
   line terminators, blank runs, a leading BOM and any trailing bytes arrive as
   `gap` records. This is what lets a transform know exactly what it is
   changing, and it is checkable without reference to the parser that produced
   it — see architecture.md I5.3.
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
| `gap` | | `false` | *none — inter-block bytes* |
| `frontmatter` | | `true` | metadata |
| `heading` | `level`, `style`, `text`, `plain` | `false` | `Header` |
| `paragraph` | | `false` | `Para` |
| `list` | | `false` | `BulletList` / `OrderedList` |
| `block_quote` | | `false` | `BlockQuote` |
| `code_fence` | `unterminated` | `true` | `CodeBlock` |
| `code_indented` | | `true` | `CodeBlock` |
| `table` | `form` | see below | `Table` |
| `line_block` | | `false` | `LineBlock` |
| `raw_html` | `htmlKind` | `true` | `RawBlock` |
| `thematic_break` | | `true` | `HorizontalRule` |
| `reference_def` | | `false` | *none — a definition* |
| `footnote_def` | | `false` | *none — a definition* |

`gap` records carry everything between content blocks: the terminator ending
one block, the blank lines before the next, a leading BOM, and whatever
trails the last block. They are **not protected** — mdfix's list-spacing fixes
insert and remove blank lines, so claiming otherwise would be false. A
consumer asking "what blocks are in this file" filters them out; a serializer
must not.

`table.form` is one of `pipe`, `simple`, `grid`, `multiline`. The last three
are `protected`; **`pipe` is not** — mdfix rewrites punctuation inside pipe
cells (dialect-policy §7 gap 4). `htmlKind` is one of `comment`, `cdata`,
`processing-instruction`, `declaration`, `element`.

`heading.style` is `atx` or `setext`. A setext record spans both lines, text
and underline. The underline must start at **column 0** — CommonMark allows
up to three spaces, pandoc's `markdown` reader does not, and pandoc is the
output dialect. The text line may itself be indented 0–3, may look like a thematic
break (`-----` under `-----` is a heading), and must be a single line.

Front matter opens only when line 1 is exactly `---` (trailing whitespace
allowed, a fourth dash disqualifies) **and** a closing `---` or `...` follows.
An unclosed opener is a thematic break, not an unterminated metadata block:
treating it as one meant a single mis-read line froze the whole document
(#64).

`reference_def` and `footnote_def` carry no counterpart in Pandoc's block list
at all — like front matter, they are *definitions*, and `[id]: http://x` on its
own yields an empty block list. They are separate kinds rather than paragraphs
because a prose pass must never be handed one: paraphrasing a link definition
breaks every reference to it. The two continue differently, both verified:
a reference definition takes only a quoted title on the following line (an
indented plain line after it is a code block), while a footnote definition
takes indented continuations and survives a blank line.

`heading.text` is the content after the marker with a closing `#` run removed,
so `## Sub ##` yields `Sub`. A `#` that is part of the text survives: `# C#`
yields `C#`.

### `heading.plain`

The heading text as Pandoc's identifier pass sees it. A consumer computing an
anchor must use this and never `text`, which is raw source.

Exactly three constructs are stripped, because they are the only ones whose
raw form differs from what Pandoc slugs. Pinned with `pandoc -t json`:

| Heading | `text` | `plain` | Pandoc identifier |
|---|---|---|---|
| `[inline](http://u)` | raw | `inline` | `inline` |
| `![img](i.png)` | raw | `img` | `img` |
| `_under_` | raw | `under` | `under` |
| `<span>html</span>` | raw | `html` | `html` |
| `[text][id]` | raw | **unchanged** | `textid` |
| `` `code` ``, `<http://a>`, `note[^1]`, `a_b_c` | raw | unchanged | already agree |

**Reference links are left raw on purpose.** Pandoc computes header
identifiers *before* it resolves references, so `## [text][id]` is `textid`
whether or not the definition exists — verified both ways. Reducing it to
`text` would be more principled and would not match.

**Also left raw (known under-strips until inline records land):**

- Spaced inline links: `## [link] (http://x)` — pandoc `markdown` does not
  treat the space form as a link either (id `link-httpx`); CommonMark would.
- Bracketed spans / attributes: `## [text]{.class}` — `]` is followed by `{`,
  not `(`, so it is not an inline link; dots are slug-kept, so the id can
  still diverge from Pandoc's plain `text` until attributes are stripped.

Splitting the work here is deliberate. Stripping markup is Markdown grammar
and belongs in mdfix; the character filtering and lowercasing that turn
`plain` into an anchor are Unicode text processing, which the consumer does
because C is the wrong language for it.

One approximation, and it is one-sided. The intraword-underscore rule asks
whether the neighbouring character is alphanumeric, and mdfix treats every
byte above U+007F as alphanumeric rather than carrying Unicode tables. That
is correct for Greek, Cyrillic, CJK and Hangul prose — `漢字_の_強調` keeps
its underscores, as Pandoc does — but a *symbol* neighbour such as `∈_x_`
stays literal where Pandoc emphasises. Erring that way keeps text as written
instead of deleting a character.

### Pipe tables and line blocks

A leading `|` is not required: `a | b` over `--|--` is a Table. The delimiter
row is the whole discriminator, which is what keeps prose containing a pipe
from becoming a table. Both forms run to the first line with no `|`, and a
header line that merely continues a paragraph starts nothing — pandoc reads
`Intro.` / `a | b` / `--|--` as one Para.

For the leading-`|` forms, the delimiter row is also what separates a table
from a line block. Pinned with `pandoc -t json`:

```text
| a | b | / |---|---| / | 1 | 2 |   -> Table
| a | b | / | 1 | 2 |               -> LineBlock
```

Distinguishing them is new in this schema. mdfix's fixer still treats both as
prose, which is why both carry `"protected": false` — the IR reports the
structure correctly while being honest that the fixer does not yet respect it.

## Nested prose

Schema 3 emits `paragraph` records **inside list items**, carrying optional
fields `depth: 1` and `parent` (the parent record's `start`). A consumer
rewriting prose can reach into a list item without learning what a list marker
is:

```console
$ mdfix --emit-ir chapter.md | grep '"depth"'
{"kind":"paragraph","start":12,"end":28,...,"depth":1,"parent":10}
```

The span excludes the marker, so replacing it through `--apply-edits` leaves
`- ` and every other byte untouched. Nested records do not participate in the
totality sum — only `depth`-0 spans concatenate to the file.

**Children are blank-separated runs of plain prose only.** A run that holds a
fence, table, line block, raw HTML, indented code, heading, or block quote is
skipped whole (under-report, not mis-report). A plain run earlier in the same
item may still nest. Nested list markers end the outer item and start a
sibling item.

On this repository's markdown that reaches most previously list-trapped
sentences; remaining opaque items hold nested constructs. Full recursive
walks (and nested block quotes) are the rest of #65.

## Inline records

Links, images, code spans and footnote references inside prose, so a consumer
can find them without learning inline Markdown:

```console
$ mdfix --emit-ir chapter.md | grep '"kind":"link"'
{"kind":"link","start":4,"end":20,...,"depth":1,"parent":0,"destination":"http://x","text":"text","form":"inline"}
```

| `kind` | Extra fields |
|---|---|
| `link` | `form`, plus `destination` (inline, autolink) or `text` + `label` (reference, shortcut) |
| `image` | as `link` |
| `code_span` | `text`; **protected** |
| `footnote_ref` | `label` |
| `raw_inline` | — ; **protected** |

`form` is `inline`, `autolink`, `reference` or `shortcut`. Reference and
shortcut links carry their **label rather than a resolved destination** — the
consumer already has the `reference_def` records, and holding the document's
link table in the emitter would buy nothing.

These are **purely additive** — new kinds at `depth > 0`, which the nesting rule
above already excludes from totality. No existing consumer changed and the
schema name did not move.

Scanned: paragraphs, headings, prose nested in list items, table cells and
block quotes. Over this repository's own markdown the link, image and code-span
counts match `pandoc -t json` exactly, in all thirteen documents.

Cell boundaries are not modelled — a record inside a table locates the
construct, and a consumer that edits must respect `protected` on the table
record above it. Emphasis and strong are not emitted; `heading.plain` already
resolves them where it matters.

## Known divergences from Pandoc

The IR is mdfix's block segmentation, and mdfix is a line classifier rather
than a full parser. Where the two disagree today, the IR reports `paragraph`
and Pandoc reports something richer:

| Construct | Pandoc | IR | Consequence |
|---|---|---|---|
| Definition list | `DefinitionList` | `paragraph` | not queryable |
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

Schema 2 changed guarantee 3 — records became total rather than merely
non-overlapping — which is why the name moved from `mdtools-ir-1`. Adding the
`gap` kind alone would not have required it.

`mdtools-ir-3` may gain **new optional fields** and **new block kinds** without
a schema bump; a consumer must ignore fields it does not recognize and should
treat an unknown `kind` as opaque-but-located. Removing a field, changing a
field's meaning, or changing what `start`/`end` measure requires a new schema
name, which the header record makes detectable.

## Not yet in schema 3

- **Inline structure.** Links, images, footnote references, citations, emphasis
  and inline code are not represented. A consumer that needs them today must
  slice the span and scan it, which is exactly the grammar leak the boundary
  exists to prevent — so this is the next thing to add, not a permanent shape.
- **Partial nesting only.** Schema 3 emits plain list-item prose as nested
  `paragraph` records (`depth` / `parent`). A list is still one parent record,
  not a full tree of items; fence, table, raw HTML, indented code, heading, and
  block quote inside an item stay opaque (no separate child). Block quotes are
  not nested. Tight versus loose lists are not represented. **`protected` on a
  list parent is therefore not a byte-level freeze map for nested verbatim
  constructs** that `process()` still freezes. Do not treat
  `list.protected == false` as “everything under this span is rewritable.”
- **The applier half.** `--apply-edits` and the edit schema are issue #12
  (shipped; see [edit-schema.md](edit-schema.md)).
