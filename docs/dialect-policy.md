# Dialect policy

Status: adopted, 2026-08-12. Supersedes the informal rules scattered through
`mdfix.rl` comments and `prosevary/README.md`.

This document answers issue #11. It states which Markdown dialects mdtools
reads, which one it writes, where the grammar that decides those questions is
allowed to live, and what happens to a construct the tools do not understand.

## 1. The asymmetry

mdtools is deliberately asymmetric:

```text
CommonMark / GFM / Obsidian / Pandoc-ish / AI-generated input
                          ↓
              loss-aware structural IR
                          ↓
          canonical Pandoc Markdown (mdtools-pandoc-1)
```

Input support is broad because manuscripts arrive from everywhere — imported
documents, other tools, and above all language models, which emit a loose
CommonMark-plus-GFM blend with no guarantee of consistency. That last source is
the primary one; the rest are welcome but secondary.

Output support is singular. There is exactly one output dialect, and Pandoc is
its judge. Additional output dialects would multiply behavior across every pass
without serving the workflow, and Pandoc itself already performs explicit
exports when a different target is needed.

**The requirement, stated plainly:** any file mdtools writes must be consumed
by Pandoc as the same document the author meant. Not "usually parses" — the
same AST, verified.

## 2. Where grammar is allowed to live

This is the load-bearing architectural rule, and everything in sections 3
through 7 depends on it.

> **Markdown grammar lives in exactly one implementation. Tools that are not
> that implementation consume its output and must not re-derive it.**

The rule exists because the repository has already paid for its absence.
`prosevary/segment.py` was 891 lines, roughly 685 of which restated the block
grammar in `mdfix/mdfix.rl`: fence tracking, setext detection, raw-HTML block
kinds, the four table forms, indented code, list content columns. Written
twice, in two languages, from one spec. It is now 404 lines and contains no
Markdown grammar at all.

Every structural bug so far arrived in pairs. Raw HTML blocks, dash rows
containing tabs, and the list-context rule each had to be fixed on both sides,
and the list-context rule was re-derived and re-broken in each new block branch
until the contract was written down. `tests/test_tool_parity.py` existed solely
because neither implementation could be trusted to agree with the other; on its
first run it caught only two of the three divergences it was written to find.
It retired when prosevary cut over to the IR.

### Target mechanism (reader and applier shipped)

mdfix owns the grammar in both directions:

- **Reader** — `mdfix --emit-ir` parses Markdown and emits the IR as JSONL on
  stdout. A pure function of the input bytes, testable against Pandoc.
  **Shipped**, schema `mdtools-ir-3`; see [ir-schema.md](ir-schema.md).
- **Applier** — `mdfix --apply-edits` reads a list of byte-span replacements
  and splices them into the original bytes. **Shipped**, schema
  `mdtools-edits-1`; see [edit-schema.md](edit-schema.md).

Both halves exist, and both consumers have migrated: prosevary takes its block
structure from the IR, and `tests/test_tool_parity.py` is retired — there is no
longer a second grammar for it to check against. The IR under-reports structure in a handful of places (setext headings,
definition lists, math, raw LaTeX), all recorded in ir-schema.md and pinned by
tests; those are fixed in `mdfix.rl`, never in a consumer.

Consumers speak to mdfix over a **wire format, not an ABI**. That
distinction is the whole point: a JSON protocol over a subprocess needs no
compiler in the install path, adds no refcounting to a codebase that has
already produced one heap overflow, is inspectable by hand, is versionable,
and is testable independently from both sides. A CPython extension module
would gain nothing in exchange — the runtime here is dominated by HTTP
round-trips to language models, not by parsing.

### Apply edits; do not serialize

There are two things "IR to Markdown" can mean, and only one of them is the
default path.

| | Full writer | Span applier |
|---|---|---|
| Input | Mutated IR | List of `(start, end, replacement)` |
| Untouched regions | Re-serialized | **Original bytes, unchanged** |
| Diff for a one-word edit | Whole file, normalized | One line |
| Identity check | Hard | `[]` edits ⇒ byte-identical output |

The default is the span applier. A tool that changes one word must produce a
one-word diff; a manuscript under version control is unreviewable otherwise,
and #11's own constraints say so. This requires the IR to carry **byte offsets
into the original source** for every node — the single design detail most
likely to sink the boundary if it is got wrong, because a consumer that cannot
locate its edit will re-parse to find it, and grammar leaks straight back into
Python.

Full serialization stays available for explicitly reformatting operations
(`--wrap`, `--canonical`), where rewriting the file *is* the request. It is
never the path for a content edit.

### What this buys beyond prosevary

The protocol is generic, so the remaining tools become consumers rather than
re-implementations:

- Read-only consumers — mdquery (#15), mdcheck (#13) — need the reader half
  only, and cannot corrupt a manuscript while the format settles. This is why
  mdquery is the right first consumer.
- Editing consumers — mdterms (#16), mdlinks (#14), and any future
  spellcheck, translation, or style pass — return span edits and never learn
  what a grid table is.
- prosevary keeps orchestration, SQLite, metrics, the CLI, and the gate policy.
  None of that is grammar, and all of it is work C would make worse.

### Corollary: the dependency rule

Python's failure mode is unbounded dependency growth. The boundary above
removes the pressure to add parsing libraries; this rule removes the rest:

> **No runtime dependency outside the Python standard library.** Remote models
> are reached over HTTP, never through a vendor SDK. PyYAML is the sole
> exception and is optional, guarded by `ImportError` with an actionable
> message.

This is already true, and it is why the CI matrix runs 3.10 through 3.13
without a lockfile. It is recorded here so it stays true.

## 3. The canonical output profile: `mdtools-pandoc-1`

The profile is Pandoc's default `markdown` reader as of **pandoc 3.10**, with
the extensions below pinned explicitly. Pinning matters because a future Pandoc
that flips one of these defaults changes what our output *means*, silently. CI
asserts the pinned set rather than trusting the default.

Load-bearing extensions, with the behavior each one buys:

| Extension | Why mdtools depends on it |
|---|---|
| `-four_space_rule` | List continuation is measured from the item's **content column**, not a fixed four spaces. `list_content_column()` implements exactly this. |
| `+markdown_in_html_blocks` | `<div>` contents are parsed as Markdown, so they stay prose-variable. |
| `+raw_html` | `<script>`, `<style>`, `<pre>`, `<textarea>`, comments, CDATA, PIs, and declarations become `RawBlock` and must survive byte-for-byte. |
| `+pipe_tables`, `+simple_tables`, `+grid_tables`, `+multiline_tables` | All four forms are recognized. Grid, simple, and multiline are byte-protected in mdfix; **pipe cells still take prose passes** (deliberate for `|`-delimited cells today — see §7). |
| `+line_blocks` | `\|`-prefixed lines are `LineBlock`, whitespace- and line-count-significant. See §7. |
| `+yaml_metadata_block` | Front matter is metadata, not prose. |
| `+footnotes`, `+inline_notes` | Footnote definitions are structure, not paragraphs. |
| `+escaped_line_breaks`, `-hard_line_breaks` | A two-space line ending is a hard break under this profile, and every pass preserves it (normalizing a longer run to exactly two). |
| `+smart` | See §4 — typographic output should be invariant under this flag. |
| `+auto_identifiers`, `-gfm_auto_identifiers` | Heading anchors follow Pandoc's slug rules, which mdlinks (#14) will depend on. |
| `+fenced_divs`, `+native_divs`, `+bracketed_spans`, `+native_spans` | Div and span syntax is structure to preserve, not prose. |
| `+tex_math_dollars`, `+raw_tex` | Math and raw LaTeX are verbatim. See §7. |
| `+definition_lists`, `+fancy_lists`, `+startnum`, `+example_lists`, `+task_lists` | List forms whose markers must not be normalized away. |

A GFM-friendly display subset is useful where it happens to coincide, but it is
not a second canonical output and no pass may trade Pandoc correctness for it.

## 4. Punctuation: literal Unicode, not ASCII shorthand

When canonical output emits typographic dashes or quotes, it uses literal
`—`, `–`, `“`, `”`, `‘`, `’` — not `--` or straight quotes as shorthand.

The reason is not aesthetic. Verified by rendering to HTML both ways — pandoc's
default `+smart`, and the `-smart` a consumer may pass and we do not control:

| Source | Renders under `+smart` | Renders under `-smart` |
|---|---|---|
| `"quoted"` | “quoted” | "quoted" |
| `--` | – (en dash) | -- |
| `...` | … | ... |
| `“quoted”` | “quoted” | “quoted” |
| `—` | — | — |
| `…` | … | … |

ASCII shorthand **renders differently depending on a flag mdtools does not
control**, so a consumer invoking `pandoc -f markdown-smart` gets straight
quotes and literal hyphens where the author meant typography. Worse, `--` does
not even mean an em dash: Pandoc reads it as an **en dash**, so the shorthand is
wrong even when smart is on. Literal Unicode renders identically under both.

The rule is stated in terms of rendered output rather than AST shape on
purpose. Pandoc's internal representation of literal curly quotes is
version-dependent — 3.10 keeps them as a plain `Str` under both flags, while
2.x folds them into `Quoted` under `+smart` — but the typography that reaches
the reader is the same in every combination. It is the output that has to be
reliable.

**Held.** mdfix's Chicago passes emit Unicode for every mark they write —
arrows, dashes, quotes, and since the ellipsis fix, `…`. Nothing mdtools
emits is smart-dependent, and `tests/test_transform_matrix.py` asserts it by
rendering the output under both `markdown` and `markdown-smart`.

The rule is about **output**. An ellipsis the author wrote as `...` is passed
through untouched: §4 constrains what mdtools writes, not what it tolerates.

## 5. The indentation model

Indentation is measured in **columns, with a tab stop of 4**, never in
characters. Pandoc expands tabs before parsing, so a tab and four spaces are
indistinguishable to it and must be to us.

Combined with `-four_space_rule`, this determines list context. Verified:

```text
- item

    four spaces      ⇒ Para       (list content: content column is 2)

- item

      six spaces     ⇒ CodeBlock  (content column 2 + 4)
```

The contract for anything that consumes this is recorded at the
`list_content_col` declaration in `mdfix.rl`: a new block branch clears list
context **only** when the block starts strictly left of the content column.
That rule was re-derived and got wrong in every block branch added before it
was written down.

## 6. Preservation, warning, and failure rules

Every construct falls into exactly one **target** class. Current tool
compliance is the inventory in §7 — a construct listed under Verbatim here may
still be a gap until both tools match that class.

**Verbatim.** Reproduced byte for byte. No pass may alter these, including
whitespace passes, because their meaning is carried by column position or by
being raw. Fenced and indented code, all four table forms, raw HTML blocks,
front matter, math, raw TeX, line blocks.

**Structural.** Recognized, and normalized only by passes that explicitly own
the construct. Heading markers, list markers, fence delimiters, footnote and
reference definitions. `--canonical` may normalize these; a content pass may
not.

**Prose.** The only text eligible for rewriting, wrapping, Chicago punctuation,
or lexical variation. Paragraphs, list item content, block quote content, and
the contents of `<div>` blocks.

**Unknown.** A construct the reader does not classify is treated as **verbatim
and diagnosed**, never as prose. This is the default, and it is the direction
in which the tools must fail: mistaking structure for prose corrupts a
document, while mistaking prose for structure merely leaves it unimproved.

Failure rules:

- A pass must never widen its own class. A prose pass touching a verbatim
  region is a bug, not a configuration choice.
- Output that does not reparse to the same Pandoc AST as its input — for
  passes that claim to preserve meaning — is a failure, not a warning.
- `--canonical-lint` is the gate form: it reports non-canonical input without
  writing.

## 7. Construct compatibility and loss

Verified against pandoc 3.10 with `-t json` / `-t native`. "Protected" means
reproduced byte for byte; "prose" means eligible for rewriting.

| Construct | Pandoc block | mdfix | prosevary | Status |
|---|---|---|---|---|
| ATX / setext heading | `Header` | structural | protected | ok |
| Fenced code | `CodeBlock` | protected | protected | ok |
| Indented code | `CodeBlock` | protected | protected | ok |
| Pipe table | `Table` | **prose rewrites in cells** | protected | **partial** |
| Simple table | `Table` | protected | protected | ok |
| Grid table | `Table` | protected | protected | ok |
| Multiline table | `Table` | protected | protected | ok |
| Raw HTML block | `RawBlock` | protected | protected | ok |
| `<div>` / `<span>` | `Div` / `Span` | prose inside | prose inside | ok, by design |
| YAML front matter | metadata | protected | protected | ok |
| Block quote | `BlockQuote` | prose inside | prose inside | ok |
| Bullet / ordered list | `BulletList` / `OrderedList` | marker normalized | prose inside | ok |
| Reference / footnote def | — | structural | protected | ok |
| Thematic break | `HorizontalRule` | protected | protected | ok |
| Definition list | `DefinitionList` | prose inside | prose inside | ok, structure survives |
| **Line block** | `LineBlock` | **leaks — treated as prose** | protected via IR `line_block` | **gap** (mdfix) |
| **Display math `$$`** | `Math` | **leaks — rewrites inside** | **leaks — offered to the LLM as a sentence** | **gap** |
| **Raw LaTeX block** | `RawBlock` | **leaks — rewrites inside** | **leaks** | **gap** |
| **Hard break (two trailing spaces)** | `LineBreak` | preserved; a longer run normalized to two | n/a | ok |
| **Ellipsis under Chicago** | text | emits U+2026 `…` | n/a | ok |

### Required repairs may create structure

Three pinned divergences turned out to be one question, and it is now decided.

R2 inserts a blank line before a list that follows prose. R3 separates a lazy
continuation from the item above it. The fuzzer's `KNOWN_DIVERGENCES` entry is
R2 again, seen through `--wrap`. In every case mdfix emits a document whose
block structure differs from what Pandoc read out of the input: Pandoc reads
one `Para`, and mdfix produces the `OrderedList` the author was plainly
writing.

**That is the point, not a defect.** It is why mdfix exists. Generated
Markdown routinely writes a list directly under a sentence; Pandoc silently
folds the whole thing into one paragraph and reports nothing, and the author
finds out from the rendered book. I2.1's exception for the required set covers
this, and the required set is where a repair like it belongs.

Two consequences follow, and both are load-bearing:

- **Where mdfix cannot tell, it must not guess.** The fancy marker forms below
  are recognized only where Pandoc recognizes them, which is where no
  paragraph is open. Mid-paragraph, `C. They built a real toolchain.` is the
  third line of a hard-wrapped sentence, and no repair fires on it.
- **Where it cannot tell and the stakes are structural, it should say so.**
  A diagnostic is the right answer to an ambiguity mdfix must not resolve
  silently — issue #97.

### Known gaps

Each was found by running the tools against Pandoc while pinning this profile
(or by matching the tools against the §4 / §3 contracts above).

1. **Line blocks.** `| text` is `LineBlock`: whitespace inside is significant
   (Pandoc converts leading spaces to non-breaking spaces) and the line count
   is part of the content. mdfix still rewrites punctuation inside one
   (`protected: false` on the IR record). prosevary freezes the whole block
   via IR `line_block` and no longer misclassifies it as a table. Structure
   survives; the remaining gap is mdfix content damage.

2. **Display math.** `$$ … $$` is verbatim mathematics. mdfix applied the arrow
   pass inside it. prosevary is worse: it hands the entire block to the
   language model as a single sentence to paraphrase.

3. **Raw LaTeX.** `\begin{verbatim} … \end{verbatim}` is a `RawBlock`. Both
   tools treat its contents as prose.

4. **Pipe table cells in mdfix.** Structure is preserved, but punctuation
   inside cells is rewritten (arrows, Chicago) when editorial/Chicago
   (or a profile that implies them) runs. That is intentional for
   `|`-delimited cells today and recorded in [ir-schema.md](ir-schema.md);
   it is still not “protected” in the byte-for-byte sense of this table.
   prosevary freezes the whole row.

All four remaining gaps are cases where a verbatim construct reaches a prose
pass — the duplication argument in §2 restated as a bug report. They become
single-site fixes once the grammar lives in one place, and none of them is a
profile/contract mismatch: `tests/test_transform_matrix.py` now pins **no**
violations at all, so I3.1 holds for every optional transform over every
document in its corpus.

### Closed

**Hard breaks** under `-w`, `--canonical`, `--wrap` and `--technical`. Both
sites destroyed them — `fix_trailing_ws` collapsed any trailing run to a
single space, which is not a break, and wrap’s `flush_paragraph` trimmed all
trailing whitespace before joining. Both now recognize a break and preserve
it, normalized to exactly two spaces and placed on the last line the wrapper
emits. A line whose trailing whitespace contains a **tab** is left byte for
byte: Pandoc expands it to the next tab stop, so whether it is a break depends
on the line’s width and on `--tab-stop`, and mdfix will not encode a reader
flag (§4).

**Fancy and example-list markers** (#90). `a. `, `A) `, `iv) `, `IV. `,
`@lab. `, `(1) `, `#. ` and `(a) ` are `OrderedList` to Pandoc and were
paragraphs to mdfix, which meant the prose passes rewrote inside a list. They
are now classified wherever Pandoc classifies them — which is wherever no
paragraph is open, `lists_without_preceding_blankline` being off — together
with Pandoc's two-column rule for uppercase markers ending in a period, its
loose roman parser, and the `p. 1` page-number exception. See
[ir-schema.md](ir-schema.md).

**Marker separators** — the follow-up sweep, and three more of the same kind.
A tab separates (`1.\tx`; Pandoc expands tabs before parsing). A marker with
nothing after it is an empty item, in every form, and end of line satisfies the
two-column rule that `A. x` fails. And Pandoc's page-number exclusion for
`p. 1` is one space wide, so `p.  1` and `p.\t1` are lists.

The empty-item rule is the one part of the marker predicates that needs
context, and the corpora said so immediately: `--wrap` puts a year at the head
of a line, and `... learned since\n2003.` is not a one-item list. It is
recognized only where a list may start.

Two marker-normalization hazards came out with it, both found by the fuzzer
within minutes of empty items becoming markers. `*` alone is a bullet, and so
is `-` alone — but `-` alone is also a setext underline and a table's dash
row, so normalizing the marker turned the paragraph above into a heading in
one document and the whole thing into a table in another. The bullet pass now
leaves an empty item alone: it is the one marker whose spelling carries
structure.

**Indented lines no longer enter list context on their own** (found by the
same sweep). A line indented two spaces was treated as a list continuation
whether or not any list existed, so a paragraph whose *first* line happened to
be indented got the blank-after-list repair inserted into the middle of it.
Across the manuscripts that was splitting wrapped sentences in two — in
`outline-volume3.md`, five paragraphs cut mid-sentence under `--technical`.
The guard is `list_content_col`, which survives a blank line where
`prev_was_list_ctx` deliberately does not, so nested content after a blank is
still inside its item.

That also closed the fuzzer's last recorded divergence. R2 fired on a wrapped
document and not on the same document unwrapped, because the continuation line
hid the paragraph above from the repair; with no false list context there is
nothing to hide behind, and `KNOWN_DIVERGENCES` is now empty.

Closing it also closed a **silent structure loss** that had nothing to do with
classification. `A.  First.` is a list; the Chicago sentence-space rule saw a
period followed by two spaces, collapsed them, and `A. First.` is a name being
abbreviated. `apply_scanner()` already refused prose edits that change a
line's block type — it was asking the narrow reading, to which a fancy marker
is prose both before and after. It now asks the widest one.

**Click consonants** (#102). The space-after-punctuation rule keyed on what
*follows* the mark, which is right for `,` `;` `:` `.` and wrong for `!`.
Khoisan orthography writes a click with a leading `!` — `!Kung`, `!kia`,
`!kanna` — so "`!` then a letter" re-spelled every San term it met. `!` and
`?` are now sentence-final only after a letter or digit, which the vendored
word table answers, so a letter in any script counts.

This is the most serious defect the tools have produced. It is a content
change to a proper noun in a language that uses the character phonemically; it
is invisible on the page, because `! Kung` reads as prose that happens to end
a sentence; and it **shipped** — Volume 1 of *Evolution of the Sacred* went to
print with it, and `git log -S'! Kung'` names the commit `Ran mdfix.`

Two things are worth keeping from how it stayed hidden. The rule's only
legitimate repair is `Wow!Next`, and over 511 files of manuscript it fired
four times, all clicks, all damage — a branch with no true positive in its
history. And the document disagreed with itself: footnote definitions skip the
prose scanner (deliberately — they are structural, like reference
definitions), so the same term survived in a note and broke in the body two
lines above. A rule that damages text is easier to see than a rule that
damages it inconsistently.

**NFC over-reporting** (#103). `unicode.non-nfc` reported the UAX #15 quick
check's *maybe* as a finding, which the documentation called the safe
direction for a warning that rewrites nothing. It was not safe: Yorùbá needs
U+1ECC followed by U+0300 constantly and Unicode has no precomposed character
for that pair, so the warning was permanent on correct text and
`--normalize-nfc` reported lines whose bytes it had not touched. A candidate
is now confirmed by normalizing and comparing. Verified against CPython's
`unicodedata` over 202,240 base-and-mark sequences, zero disagreements in
either direction.

Worth keeping: *over-reporting is safe* is a claim about a rule, not a
property of warnings. Here it meant a gate could not be run clean on a correct
chapter — the same "warning people learn to skip" that #97 argues against.

**Chicago ellipsis.** A spaced run (`. . .`) or a run of four or more dots now
becomes U+2026 `…` rather than ASCII `...`, under every flag that rewrites
one. `--chicago-punct-2` was the awkward half: it reached `...` by stripping
the spaces *between* the dots, so no rule had decided to emit an ellipsis and
no rule could be blamed for the form. Whether a run of dots becomes an
ellipsis is now answered in one place, for both Chicago flags.

## 8. Verification

Pandoc is the oracle. Behavior is pinned by running it, not by reading the
spec — the spec has been overturned repeatedly here. A simple table requires
all three of header, dash row, and body row; tabs are valid separators in a
dash row; `<div>` contents are Markdown while `<script>` contents are not. None
of those were obvious in advance.

Assert on **rendered output, not AST shape**, wherever the two would differ.
The §4 rule was first written as an AST claim, passed against pandoc 3.10, and
failed CI against the older Pandoc on the runner — which represents literal
curly quotes differently while rendering them identically. AST shape is an
implementation detail that moves between versions; the typography reaching the
reader is the thing mdtools promises. Where a claim must hold across versions,
check it against a container (`pandoc/core:2.19`) as well as the local install.

In place today:

- `tests/test_pandoc_equivalence.py` — output reparses to the input's AST.
- `tests/test_pandoc_tables.py` — the four table forms, with the negatives
  (setext underline, thematic break, header-without-body) each pinned against
  `pandoc -t json`.
- `tests/test_raw_html_blocks.py` — the raw-versus-Markdown asymmetry.
- `tests/test_span_properties.py` — property tests over span reconstruction.
- `tests/test_dialect_profile.py` — reads §3's table out of *this document* and
  asserts it against `pandoc --list-extensions=markdown`, so an upstream
  default flip fails CI instead of silently changing what our output means. It
  also asserts the behaviors, since an extension can keep its default while its
  semantics move.

The document is the source of truth for that test, deliberately. Pinning the
set in Python instead would let the two drift, which is the failure a policy
document exists to prevent.

- `tests/test_ir_schema.py` — the reader half of §2: spans slice the source
  exactly across LF/CRLF/no-final-newline, the block taxonomy, and a Pandoc
  oracle over the repository's own documentation. The `paragraph`-shaped
  divergences are pinned so closing one is deliberate.

Still needed:

- prosevary reconstruct still splices in-process; migrate it onto
  `--apply-edits` (schema `mdtools-edits-1`; see
  [edit-schema.md](edit-schema.md)) so edit-lists are the only write path.
  Dual grammar and `test_tool_parity.py` are already gone.
- Coverage for remaining §7 gaps, each with the Pandoc AST (or rendered
  output) as the assertion. Hard-break preservation is covered by
  `HardBreakTests` and the transform matrix; ellipsis is covered by
  `ChicagoEllipsisTests`.
- CI oracle version: §3 pins pandoc 3.10 in prose; CI installs distro
  pandoc. Failures should name the binary/version so an apt bump is
  diagnosable.

## 9. Non-goals

- **Multiple output dialects.** Pandoc performs explicit exports.
- **A full Markdown-to-Markdown normalizer as the default path.** Span edits,
  per §2.
- **Matching CommonMark where it diverges from Pandoc.** CommonMark is an
  input dialect. Pandoc decides the output.
- **A CPython extension module.** §2 explains why the wire format wins.
- **Migration or compatibility shims.** The repository is days old and has no
  external consumers.
