# Edit schema — `mdtools-edits-1`

Status: shipped, 2026-08-12. Implements the applier half of the boundary in
[dialect-policy.md](dialect-policy.md) §2, and
[architecture.md](architecture.md) L4/L5.

`mdfix --apply-edits file.md` reads byte-span replacements as JSONL on stdin
and splices them into the original bytes. It is how a consumer changes a
document without knowing any Markdown.

```console
$ mdfix --emit-ir chapter.md > ir.jsonl        # find what to change
$ ...                                          # decide, using the spans
$ mdfix --apply-edits -i chapter.md < edits.jsonl
```

## Why splice rather than serialize

Regenerating the file from the IR would normalize regions nobody touched — a
blank run collapsed, a hard break lost, a line ending rewritten — and turn a
one-word change into a whole-file diff. Splicing keeps every untouched byte,
so a manuscript under review stays reviewable.

Two guarantees follow directly, and both have tests:

- **I5.1** — an empty edit list reproduces the file byte for byte, including
  CRLF, CR-only line endings, and a missing final newline.
- **I5.2** — a one-sentence change produces a one-sentence diff.

The applier is in fact the only path that preserves line endings: the *fixer*
normalizes CRLF to LF by design.

## Format

JSONL. An optional header record, then one record per edit. Records are flat
objects — nested values are refused rather than half-parsed.

### Header (optional, recommended)

```json
{"kind":"edits","schema":"mdtools-edits-1","source":"chapter.md","bytes":4425}
```

| Field | Meaning |
|---|---|
| `kind` | `"edits"` — marks this as the header |
| `schema` | Refused if it is not `mdtools-edits-1` |
| `bytes` | The file size the consumer saw. Cheap staleness detection |

`bytes` matters more than it looks. If the file changed between `--emit-ir`
and `--apply-edits`, every span is wrong, and splicing would corrupt the
document rather than fail. One integer catches it.

### Edit

```json
{"start":26,"end":31,"replacement":"nimble","rule":"prosevary.vary","expect":"quick"}
```

| Field | Required | Meaning |
|---|---|---|
| `start`, `end` | yes | Byte offsets, half-open, as `--emit-ir` reports them |
| `replacement` | no (default `""`) | UTF-8 text to splice in. Empty deletes |
| `expect` | no | The original bytes the consumer saw at that span |
| `rule` | no | Stable rule ID, for diagnostics and suppression |

`expect` is the per-edit staleness guard, and it is worth sending. It costs
one string and turns "the file moved underneath me" from silent corruption
into a refusal naming the edit.

`start == end` inserts. An edit list may be in any order; the applier sorts
by start, then end, then original input order (so same-offset inserts are
stable).

## Validation (I4.2)

Incoming edits are checked, not trusted. Each of these is a way a consumer
corrupts a manuscript, and each is refused with a diagnostic:

- spans outside the file, reversed spans, negative offsets
- overlapping edits
- a `replacement` that is not well-formed UTF-8
- an `expect` that does not match the bytes at that span
- a `bytes` header that does not match the file
- an unknown `schema`
- malformed or nested JSON

The input file itself must be valid UTF-8, the same L1 contract as
`--emit-ir` (see #53).

## Validate, do not repair (I4.3)

An edit that would leave the document needing a **required repair** is
**refused**, not silently fixed:

```console
$ echo '{"start":48,"end":70,"replacement":"Intro:\n- one\n- two"}' \
    | mdfix --apply-edits chapter.md
error: applying these edits would leave chapter.md needing a required repair,
so they are refused rather than silently fixed (architecture I4.3).
```

Repairing would insert a blank line the consumer never asked for — touching
bytes outside its own edit and destroying the minimal-diff guarantee it came
for. Refusing keeps both guarantees and makes the mistake visible. The same
edit written correctly (`"Intro:\n\n- one\n- two"`) is accepted.

The check is the required set from [transforms.md](transforms.md) run over the
spliced result: if it would change anything, the result is not L2-clean.

## Who produces edits

| Producer | What it repairs |
|---|---|
| [mdterms](mdterms.md) `--edits` | Forbidden spellings in prose |
| [mdlinks](mdlinks.md) `--edits` | Broken anchors and moved-file paths |
| prosevary | Sentence-level variation |

Each names one file, because the `bytes` header describes one document. The
pattern is the same in all three, and it is the point of the schema: the tool
that decides what to change is never the tool that writes the file.

## Writing

| Form | Result |
|---|---|
| `mdfix --apply-edits f.md` | Spliced document on stdout; `f.md` untouched |
| `mdfix --apply-edits -i f.md` | In place, with a `.bak` |
| `mdfix --apply-edits f.md out.md` | To `out.md` |

The in-place path reuses the fixer's own writer, so it preserves permission
bits and ownership, writes through a temp file, and renames atomically with a
directory fsync. Nothing is written until the whole edit list has validated
and the result has passed the I4.3 check — a write that has to be undone is a
write that should not have happened.

## Not in schema 1

- **Severity, confidence and explanation** on an edit. #12 asks for them and
  they belong here, alongside the diagnostics contract (ID.1–ID.3) that has
  the same shape.
- **Composable overlaps.** Overlapping edits are refused outright. #12 leaves
  room for explicitly composable ones; nothing needs them yet.
- **stdin for the document.** The document is named by path so its bytes can
  be re-read for the `expect` and `bytes` checks.
