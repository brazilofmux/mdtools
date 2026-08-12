# Writing for mdtools

Status: 2026-08-12. The dialect from the author's side.

Every other document here faces the implementer — a policy, a set of
invariants, three schemas. This one answers the question they don't: **what
can I write, and what will the tools do with it?**

The promise, in one line:

> What you write, Pandoc reads as you meant — and mdtools will not quietly
> change it into something else.

Everything below was verified by running `pandoc -t json` and `mdfix
--emit-ir`, not read off a specification. Where the two disagree, Pandoc wins
and the gap is recorded.

## Text

**UTF-8, and only UTF-8.** Anything else is refused with the byte offset,
rather than guessed at. Greek, Cyrillic, CJK, Hangul, mathematical symbols and
emoji all pass through untouched.

A **byte-order mark** at the start of a file is stripped, matching Pandoc. A
U+FEFF anywhere else is a zero-width no-break space and is left alone.

You do **not** need to normalize. mdtools does not rewrite your text into NFC,
because doing so would silently move heading anchors — a precomposed `Héading`
anchors as `héading`, the decomposed spelling as `heading`, and links break
either way you choose for someone.

### Punctuation: write the real characters

Use the real characters: em dash —, en dash –, ellipsis …, and the
curly quotes “ ” ‘ ’. Do not write two hyphens, three dots, or straight
quotes and expect them to become typography.

This is not a style preference. ASCII shorthand renders differently depending
on a flag your reader controls, and you do not control:

| You write | Default `pandoc` | With `-smart` disabled |
|---|---|---|
| two hyphens | an en dash | unchanged, as typed |
| three dots | an ellipsis | unchanged, as typed |
| a literal `—` | an em dash | an em dash |

And `--` does not even mean an em dash — Pandoc reads it as an **en dash**. The
literal character is the only spelling that means what you meant regardless of
how the document is later processed.

## Blocks

### Headings

Both forms work. ATX needs the space:

```markdown
# Title
## Section
```

Setext works too, but the underline must start at **column 0** — CommonMark
allows three spaces of indent, Pandoc does not:

```markdown
Title
=====
```

A setext underline makes a heading of a **single** preceding line. Two lines
of prose followed by `=====` is a paragraph, not a heading.

Heading anchors follow Pandoc's rules: lowercased, punctuation dropped,
spaces to hyphens, duplicates suffixed `-1`, `-2`. `mdquery outline` reports
the exact anchor Pandoc will emit, so you can link to it with confidence.

### Lists

```markdown
- item one
- item two
  continued on the next line

1. ordered
2. also ordered
```

Indentation inside an item is measured from the item's **content column**, not
from the margin. For `- item` that column is 2, so:

- four spaces is still item prose
- **six** spaces is a code block inside the item

A blank line before and after a list is required, and mdtools inserts it if
you forget — without it Pandoc swallows the list into the surrounding
paragraph.

### Code

Fenced, with an info string:

````markdown
```python
code = 1
```
````

Or indented four columns past the enclosing content column. Both are
reproduced byte for byte: nothing inside a code block is ever rewritten.

A fence indented inside a list item is a fence, not indented code.

### Tables

All four Pandoc forms are recognized and their column positions preserved:

Pipe, with and without the leading bar:

```markdown
| a | b |
|---|---|
| 1 | 2 |

a | b
--|--
1 | 2
```

Simple and grid:

```markdown
Right  Left
-----  ----
12     34

+---+---+
| a | b |
+---+---+
```

The multiline form — a dash run, a header, a column row, body rows that may
span blank lines, and a closing dash run — works too.

Simple, grid and multiline tables carry their structure in **column
position**, so they are reproduced byte for byte. Pipe tables are not: when
editorial or Chicago passes run (including under `--canonical` /
`--technical`), a `→` inside a pipe cell may become an em dash. If that
matters, use a grid table.

### Block quotes, breaks, definitions

```markdown
> quoted text

---

[link-id]: http://example.com "Optional title"
[^1]: A footnote.
```

Link and footnote definitions are never offered to a prose pass — paraphrasing
a link definition would break every reference to it.

### Line blocks

```markdown
| The limerick packs laughs anatomical
| Into space that is quite economical
```

Whitespace and line count are significant here, so these are recognized as
line blocks rather than tables. See the caveat below.

## HTML

This is the part worth knowing, because the rule is not what most people
expect. **Some HTML is opaque and some is not:**

`<script>` is raw — its contents are never treated as prose:

```markdown
<script>
var x = 1;
</script>
```

`<div>` is **not** raw. Its contents are Markdown, and the emphasis below is
real emphasis:

```markdown
<div class="note">

*this is emphasis*

</div>
```

Raw, contents preserved exactly: `<script>`, `<style>`, `<pre>`, `<textarea>`,
HTML comments, CDATA, processing instructions and declarations.

Everything else — `<div>`, `<span>`, `<section>` and friends — is a container
whose **contents are parsed as Markdown**, exactly as Pandoc does it. Prose
inside a `<div>` is ordinary prose and will be treated as such.

An inline tag on its own line between two prose lines does **not** open a
block. `Before.` / `<br>` / `After.` is a single paragraph.

## Front matter

```markdown
---
title: A Chapter
---
```

Only at the very top, only when a closing `---` or `...` follows. **A `---`
with no closer is a thematic break**, not an unterminated metadata block — so
starting a file with a horizontal rule is safe.

## What the tools do by default

A bare `mdfix` performs three repairs and **nothing else**. Each is a case
where Pandoc otherwise reads your document as something you did not write:

| Repair | Without it |
|---|---|
| Blank line before a list | the list is swallowed into the paragraph |
| Blank line after a list | the next paragraph is swallowed into the item |
| Space after `#` | `#Title` is a paragraph, not a heading |

Everything else is opt-in. Bullet normalization, emphasis stripped from
headings, arrow asides, Chicago punctuation, wrapping — all of it waits for a
flag. `--editorial` turns on the editorial set; `--canonical` and
`--technical` are the bundles a book pipeline usually wants. See
[transforms.md](transforms.md).

## Traps

Things that are easy to write and will not mean what you expect.

**Two trailing spaces are a hard break** — and `-w`, `--canonical`, `--wrap`
and `--technical` all collapse them, turning the hard break into a soft one
(dialect-policy §7 gap 5). Until hard-break preservation is fixed, use an
explicit `<br>` if the break matters.

**Unspaced CJK will not wrap.** `--wrap` measures display columns, so Greek,
Cyrillic and mixed scripts fill the line properly and a wide character counts
as two. But a run of CJK with no spaces offers nowhere to break: Pandoc's
`east_asian_line_breaks` is off in the pinned profile, so mdtools does not
invent a break opportunity it would not recognize. Such a paragraph is left on
one long line.

**A pipe anywhere in a line following a table makes it a table row.** A pipe
table runs to the first line with *no* pipe, which is what Pandoc does — so
leave a blank line after a table if the next paragraph might contain one.

**An unclosed fence swallows the rest of the file.** mdfix warns, and
`--canonical-lint` fails, but the file will be largely unprocessed until you
close it.

## Under-supported today

Written honestly, because it is better to know:

| Construct | Status |
|---|---|
| Definition lists | Pandoc parses them; mdtools sees a paragraph, so they get prose passes |
| Display math `$$` | same — the contents are treated as prose |
| Raw LaTeX blocks | same |
| Line blocks | recognized, but not yet protected from prose passes |
| Prose in a list item holding a fence or table | not reachable by prose tools; the item stays opaque |
| Inline structure | links, images, footnote references and citations are not queryable |

None of these will corrupt your document silently in the structural sense —
the block skeleton survives — but a prose pass may rewrite inside them. If you
are running `prosevary` or a Chicago pass over a chapter with heavy math, look
at the diff.

## Checking your work

```console
$ mdfix --canonical-lint chapter.md      # fails if it is not canonical
$ mdquery outline chapter.md             # headings, with the anchors pandoc emits
$ mdquery blocks chapter.md --kind table # what the tools think is a table
$ mdfix --emit-ir chapter.md             # everything, with byte spans
```

`mdquery outline` is the fastest way to check that a document is structured
the way you think it is. If a heading is missing from the outline, Pandoc will
not see it either.
