# Architecture

Status: adopted, 2026-08-12. Sits above
[dialect-policy.md](dialect-policy.md), which fixes *which* dialects mdtools
reads and writes; this document fixes *where the work happens* and *what each
stage guarantees*.

It exists to be measured against. Every layer below carries invariants stated
so a test can fail, and §5 records what is not built yet.

## 1. The layers

```text
                          ┌─────────────────────────────┐
  bytes on disk  ────────▶│ L1  Input                   │
                          │     validate, parse          │
                          └──────────────┬──────────────┘
                                         │ IR
                          ┌──────────────▼──────────────┐
                          │ L2  Required transforms      │
                          │     dialect conformance      │
                          └──────────────┬──────────────┘
                                         │ IR
                          ┌──────────────▼──────────────┐
                          │ L3  Optional transforms      │
                          │     wrap, Chicago, flags     │
                          └──────────────┬──────────────┘
                                         │ IR
       prosevary,         ┌──────────────▼──────────────┐
       mdquery,   ◀──────▶│ L4  External IR interface    │
       mdterms, …         │     emit + accept, validated │
                          └──────────────┬──────────────┘
                                         │ IR
                          ┌──────────────▼──────────────┐
                          │ L5  Output                   │
                          │     spans applied, or        │
                          │     serialized               │
                          └──────────────┬──────────────┘
                                         ▼
                              pandoc-friendly Markdown

  Diagnostics (D) are produced by every layer and are not a layer.
```

L1 through L5 and D live in **mdfix**. **Target:** everything else is a
consumer and holds no Markdown grammar (dialect-policy §2). **Today:** that is
true of mdquery; prosevary still re-derives blocks in `segment.py` until
`--apply-edits` ships and the dual-grammar interim ends.

| Layer | Owns |
|---|---|
| **L1 Input** | Encoding validation, normalization policy, block and inline parsing, IR production |
| **L2 Required** | Transforms without which output is not reliably Pandoc-readable |
| **L3 Optional** | Transforms the caller asks for, which may never break L2 |
| **L4 Interface** | Emitting IR, accepting IR and edits, validating both |
| **L5 Output** | Turning IR plus edits back into bytes |
| **D Diagnostics** | Located, rule-identified warnings and errors from any layer |

## 2. Invariants

Each has an identifier so issues and tests can cite it.

### L1 — Input

- **I1.1 UTF-8 or refuse.** Input that is not well-formed UTF-8 is rejected
  with a diagnostic naming the byte offset. It is never parsed, and never
  copied into the IR. NUL is refused with it: U+0000 is valid Unicode but
  every fixer is `strlen`-bounded, so a NUL silently truncated the line and
  dropped the rest on output. A leading BOM is stripped, matching Pandoc,
  and offsets skip past it so I1.3 still holds. *(Issue #53, done.)*
- **I1.2 Normalization is reported, not performed.** L1 detects text that is
  not NFC and says so. It does not rewrite it. Normalizing belongs to L3 —
  see Q2.
- **I1.3 Spans address the file on disk.** Every IR offset indexes the input
  bytes exactly as they were read, including CRLF and a missing final newline.
- **I1.4 Parsing is whole-file.** Block classification is context-dependent,
  so there is no partial parse without an explicit resume state. See Q4.

### L2 — Required transforms

- **I2.1 AST preservation.** For any input, `pandoc -t json` of the output
  equals `pandoc -t json` of the input, except where a required transform
  exists precisely to repair a construct Pandoc would misread.
- **I2.2 Reader-flag independence for emitted typography.** Marks that mdtools
  *emits* (em dashes, curly quotes, ellipsis) render identically under
  `markdown` and `markdown-smart`. This is dialect-policy §4: smart-invariance
  of **output we produce**, not a requirement that every bare pass convert
  author ASCII shorthand. *(Chicago/`--canonical` live in L3 and must still
  satisfy this when they run — see I3.1.)*
- **I2.3 Required is the default.** Producing Pandoc-friendly output is not
  opt-in. *(Today: always-on structural fixes exist — blank lines around lists,
  bullets, arrow aside, … — but no profile is designated L2/required; I2.2 and
  the rest of `--canonical` remain opt-in, so I2.3 is false.)*

### L3 — Optional transforms

- **I3.1 Non-interference.** For every optional transform, applied alone and
  in every shipped profile, the output still satisfies I2.1 and I2.2. This is
  a test matrix, not a promise. *(Today false: `-w` destroys two-space hard
  breaks; `--wrap` miscounts non-ASCII width, issue #49; Chicago emits ASCII
  `...`, which violates I2.2 for emitted typography.)*
- **I3.2 Idempotence.** Applying a transform twice equals applying it once.
- **I3.3 Opt-in.** No optional transform runs unless requested.

### L4 — External IR interface

- **I4.1 Valid IR out.** Emitted IR is well-formed JSONL, valid UTF-8, with a
  schema header, non-overlapping block records in source order, and spans
  within bounds.
- **I4.2 Validated IR in.** Accepted IR and edit lists are checked, not
  trusted: schema version, UTF-8, span bounds, ordering, and non-overlap.
  A violation is refused with a diagnostic.
- **I4.3 Validate, do not repair.** An incoming edit that would break L2 is
  **rejected**, not silently fixed. See Q3.
- **I4.4 Schema compatibility is detectable.** New optional fields and new
  block kinds may appear without a version bump; anything else changes the
  schema name in the header record.

### L5 — Output

- **I5.1 Empty edit list is byte-identical.** Applying no edits reproduces the
  input exactly.
- **I5.2 Minimal diff.** A one-sentence change produces a one-sentence diff.
  Untouched regions keep their original bytes.
- **I5.3 Round-trip identity.** If the IR is ever serialized rather than
  spliced, `parse → serialize` is byte-identical for unmodified input. This is
  the invariant that forces the IR to be **lossless** rather than merely
  descriptive. See Q1.

### D — Diagnostics

- **ID.1 Located.** Every diagnostic carries a path, a byte span, and a line.
- **ID.2 Identified.** Every diagnostic carries a stable rule ID, so a
  consumer can suppress or gate on one without matching English text.
- **ID.3 Machine-readable.** Diagnostics are available as JSONL on a stream
  separate from the document.

## 3. What "Pandoc-friendly" means

I2.1 and I2.2 are the whole definition, and both are executable. Pandoc is the
oracle: behaviour is pinned by running it, not by reading a specification,
because the specification has been overturned repeatedly here. A simple table
needs all three of header, dash row, and body row; tabs are valid separators in
a dash row; `<div>` contents are Markdown while `<script>` contents are not; a
setext underline must start at column 0 even though CommonMark allows three
spaces.

## 4. Consumers

**Target:** a consumer speaks JSONL to mdfix over a subprocess and contains no
Markdown grammar. It reads structure from the IR and writes changes as
byte-span edits.

- **Read-only** — mdquery (#15), mdcheck (#13). mdquery already matches this
  model. Cannot corrupt a manuscript.
- **Editing (target)** — prosevary, mdterms (#16), mdlinks (#14). Return span
  edits and never learn what a grid table is. **Today:** prosevary still
  classifies blocks itself and reconstructs via `Document.reconstruct`; it
  does not yet speak edit-lists to mdfix (`--apply-edits` is unshipped; §5).

prosevary is Python, external, and does what it does: orchestration, SQLite,
metrics, gate policy. None of that *needs* to be grammar once the applier
lands.

## 5. Where we are

| Layer | State |
|---|---|
| L1 | Block parsing broad and Pandoc-verified. Inline parsing partial (identifiers only). **No UTF-8 validation, no NFC detection.** Whole-file only; `MAX_LINES` 200000, `MAX_LINE` 8192. |
| L2 | **Not separated from L3.** No flag or profile is designated required, and I2.3 is false. |
| L3 | ~20 transforms exist. I3.1 is untested and currently false in three known cases. |
| L4 | Emits IR (`--emit-ir`, schema `mdtools-ir-1`). **Cannot accept IR**; no validator; no `--apply-edits`. |
| L5 | **Absent.** Output is line-based passthrough. Nothing writes from the IR. I5.1–I5.3 are untestable because the path does not exist. |
| D | English prose on stderr. No IDs, no spans, no machine format. |

Known dialect gaps are tracked in dialect-policy §7 and ir-schema's divergence
list, not here.

## 6. Open questions, with recommended answers

### Q1. Does L5 serialize the IR, or splice spans into the original bytes?

**Recommend: splice by default; serialize only under an explicit reformat
request, and only once I5.3 holds.**

Serializing on every run makes a one-word change produce a whole-file diff,
which a manuscript under review cannot absorb. Splicing gives I5.1 and I5.2 for
free.

Serialization is still worth having — `--wrap` and `--canonical` are reformat
requests, where rewriting the file *is* the ask. But it requires the IR to
become **lossless**, and today it is not: the gaps between records — blank-line
runs, trailing whitespace, line-ending style — are recorded nowhere. Deciding
this before building L5 is the difference between adding a field and reshaping
the schema.

### Q2. Does L1 normalize to NFC?

**Recommend: no. Detect in L1, normalize only as an opt-in L3 transform.**

Three reasons, all load-bearing. Normalizing breaks I1.3, because offsets would
then address a buffer the consumer never saw. It changes Pandoc identifiers —
precomposed `Héading` anchors as `héading`, decomposed anchors as `heading`, so
normalizing can move an anchor and break every link to it. And it rewrites the
author's file as a side effect of reading it.

### Q3. When a consumer's edit would break L2, does the applier fix it or reject it?

**Recommend: reject, with a diagnostic.**

Fixing means touching bytes the consumer never edited, which breaks I5.2 and
would reformat prosevary's sentence-level replacements underneath it. Rejecting
keeps both the dialect guarantee and the minimal-diff guarantee, and it makes
the failure visible instead of silent.

### Q4. What does "look at just parts of the file" mean?

**Recommend: partial *edit*, yes. Partial *parse*, only with an explicit
resume state.**

Partial edit is what spans are *for*; the L5 applier that makes it true is
not shipped (§5 / #12). prosevary-local reconstruct is a different,
dual-grammar mechanism. Partial parse is a different claim: classification
depends on fence state, front-matter state, list content column, and open raw
HTML, and `table_block_end` needs forward lookahead. Starting mid-file without
carrying that state produces confidently wrong answers. If streaming is wanted
later, the resume state must be part of the schema; designing it in is cheap
now and a retrofit later.

### Q5. How is I3.1 enforced?

**Recommend: a property test over transforms, not a review habit.**

For each optional transform alone, and for each shipped profile, assert I2.1
and I2.2 over a corpus. This is the check that would have caught `--wrap`
breaking width for non-ASCII and `-w` destroying hard breaks.

### Q6. How do L2 and L3 move from line-based fixers to IR-based transforms?

**Recommend: dual path, corpus diff, then switch.**

Today the fixers mutate line buffers in place while the IR is a separate walk.
Making transforms operate on the IR means rewriting `process()`, which is the
riskiest change in the repository and the origin of most past bugs. Worse, it
re-creates the dual-implementation problem *inside* mdfix while in flight.

So: build the IR-based path beside the existing one, diff their output over the
repository and the downstream corpora until byte-identical, and only then
delete the old path. The same discipline that `test_tool_parity.py` provided for
the C/Python split, applied to a C/C split.

### Q7. Where do diagnostics live?

**Recommend: a cross-cutting concern with its own stream, not a layer.**

Any layer can emit one. They need ID.1–ID.3 because mdcheck (#13) and the
shared CLI contract (#12) both depend on locations and stable IDs. The document
goes to stdout; diagnostics go elsewhere, so a JSONL document stream is never
polluted by a warning.

## 7. Non-goals

- **A second output dialect.** dialect-policy §1.
- **Serializing the IR as the default write path.** Q1.
- **A CPython extension.** dialect-policy §2 — the wire format wins.
- **Streaming or partial parsing** until Q4's resume state exists.
- **Migration shims.** The repository has no external consumers.
