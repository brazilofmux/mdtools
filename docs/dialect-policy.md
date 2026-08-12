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
until the contract was written down. `tests/test_tool_parity.py` exists solely
because neither implementation can be trusted to agree with the other, and on
its first run it caught only two of the three divergences it was written to
find.

### Target mechanism (reader and applier shipped)

mdfix owns the grammar in both directions:

- **Reader** — `mdfix --emit-ir` parses Markdown and emits the IR as JSONL on
  stdout. A pure function of the input bytes, testable against Pandoc.
  **Shipped**, schema `mdtools-ir-2`; see [ir-schema.md](ir-schema.md).
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
| `+escaped_line_breaks`, `-hard_line_breaks` | A two-space line ending is a hard break under this profile. **`mdfix -w` / `--canonical` / `--wrap` / `--technical` currently destroy hard breaks** — see §7. |
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

**Target:** fully **smart-invariant** typography (including ellipsis as `…`).
**Today:** mdfix's Chicago arrow/dash/quote passes emit Unicode for those
marks, which is why they are correctness features. The Chicago ellipsis pass
still normalizes to ASCII `...` under `--canonical` — the smart-dependent form
in the table above. That is a known non-invariant until fixed (see §7).

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
| **Line block** | `LineBlock` | **leaks — treated as prose** | protected, but misclassified as a table | **gap** |
| **Display math `$$`** | `Math` | **leaks — rewrites inside** | **leaks — offered to the LLM as a sentence** | **gap** |
| **Raw LaTeX block** | `RawBlock` | **leaks — rewrites inside** | **leaks** | **gap** |
| **Hard break (two trailing spaces)** | soft/hard break | **collapsed by `-w`, `--canonical`, `--wrap`, `--technical`** | n/a | **gap** |
| **Ellipsis under Chicago** | text | **emits ASCII `...`, not `…`** | n/a | **gap** |

### Known gaps

Each was found by running the tools against Pandoc while pinning this profile
(or by matching the tools against the §4 / §3 contracts above).

1. **Line blocks.** `| text` is `LineBlock`: whitespace inside is significant
   (Pandoc converts leading spaces to non-breaking spaces) and the line count
   is part of the content. mdfix rewrote `→` to `—` inside one. prosevary
   protects it only by accident, classifying it as a table because
   `_TABLE_LEADING` matches a leading `|`. Both need a real `LineBlock` kind.
   Structure survived in testing, so this is content damage, not corruption.

2. **Display math.** `$$ … $$` is verbatim mathematics. mdfix applied the arrow
   pass inside it. prosevary is worse: it hands the entire block to the
   language model as a single sentence to paraphrase.

3. **Raw LaTeX.** `\begin{verbatim} … \end{verbatim}` is a `RawBlock`. Both
   tools treat its contents as prose.

4. **Pipe table cells in mdfix.** Structure is preserved, but punctuation
   inside cells is rewritten (arrows, Chicago). That is intentional for
   `|`-delimited cells today and documented in `tests/test_tool_parity.py`;
   it is still not “protected” in the byte-for-byte sense of this table.
   prosevary freezes the whole row.

5. **Hard breaks under `-w`, `--canonical`, `--wrap` and `--technical`.**
   Profile requires two trailing spaces to mean a hard break. Two separate
   sites destroy them: `fix_trailing_ws` (when `opt_trail_ws` is set — `-w`
   and profiles that enable it) collapses any trailing run to one space;
   wrap’s `flush_paragraph` (pure `--wrap`, and `--technical` which enables
   wrap) trims *all* trailing whitespace before emit/join. Closing this gap
   needs both paths. See `tests/test_transform_matrix.py`.

6. **Chicago ellipsis.** Under `--chicago-punct`, `--chicago-punct-2`,
   `--canonical` and `--technical`, spaced or run ellipses become ASCII
   `...`, which is smart-dependent under Pandoc (see §4). Target is U+2026
   `…`. An ellipsis the author already wrote as `...` is passed through
   unchanged and is *not* a violation — §4 constrains what mdtools emits,
   not what it tolerates.

The first three are cases where a verbatim construct reaches a prose pass —
the duplication argument in §2 restated as a bug report. Those become
single-site fixes once the grammar lives in one place. The last three are
mdfix profile/contract mismatches that the policy now records so CI and
future work cannot treat them as already done.

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
- `tests/test_tool_parity.py` — the two implementations agree. Retired once
  §2 is implemented and there is only one implementation to check.
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

- Consumer migration onto `--apply-edits` (schema `mdtools-edits-1`; see
  [edit-schema.md](edit-schema.md)) so prosevary can drop its dual grammar
  and `test_tool_parity.py` can retire.
- Coverage for the §7 gaps, each with the Pandoc AST (or rendered output)
  as the assertion — including hard-break preservation under `-w` and
  Unicode ellipsis under Chicago.
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
