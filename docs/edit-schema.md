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
| `severity` | no | `error`, `warning` or `info` |
| `confidence` | no | `high`, `medium` or `low` |
| `explanation` | no | One line of prose for a human reviewing the change |

`expect` is the per-edit staleness guard, and it is worth sending. It costs
one string and turns "the file moved underneath me" from silent corruption
into a refusal naming the edit.

### Severity, confidence and explanation

These are issue #12's edit model, and mdfix **never acts on them**. A `low`
edit is applied exactly like a `high` one, because sending it was the
producer's decision — re-deciding here would put the same policy in two places
and make the applier's behaviour depend on how carefully a producer phrased
itself. They exist to be *read*: `--diff` shows them, so a reviewer sees the
judgement behind a change and not only its byte range.

They are validated anyway (**I4.2**). An unknown value is refused rather than
ignored, because a field that vanishes when misspelled is worse than one that
does not exist: a review step filtering on `confidence` would go on passing
everything, and nothing would say so.

Both vocabularies are closed sets, and confidence is deliberately not a
number. A float invites a precision nobody has calibrated — `0.82` is not
something a reader can check, while "medium" is a claim a producer can defend.
The tools reason in exactly these steps: mdlinks marks an anchor it matched
exactly as `high` and one it reached by edit distance as `medium`, and there
is no ratio between those two kinds of answer.

| Producer | Confidence it emits |
|---|---|
| mdterms | `high` — an exact spelling the glossary named |
| mdlinks | `high` for an identifier or basename match, `medium` for nearest-neighbour |

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
- a `severity` or `confidence` outside its vocabulary
- an `explanation` that is not well-formed UTF-8
- a `bytes` header that does not match the file
- an unknown `schema`
- malformed or nested JSON
- a span that cuts a multi-byte UTF-8 character

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
| `mdfix --apply-edits --diff f.md` | What would change, on stdout. Writes nothing |

The in-place path reuses the fixer's own writer, so it preserves permission
bits and ownership, writes through a temp file, and renames atomically with a
directory fsync. Nothing is written until the whole edit list has validated
and the result has passed the I4.3 check — a write that has to be undone is a
write that should not have happened.

## Previewing

```console
$ mdlinks --edits docs/guide.md | mdfix --apply-edits --diff docs/guide.md
@@ docs/guide.md:5 @@ 2 edits
#  links.broken-anchor [error] confidence: high
#  the heading's identifier for that text
#  links.broken-anchor [error] confidence: medium
#  the closest anchor in that file
- See [a](#instalation-guide) and [b](#overvew).
+ See [a](#installation-guide) and [b](#overview).
```

This is not a general diff, and it is not trying to be. The edit list already
says exactly which bytes change, so there is nothing to infer: each group of
edits landing on the same lines becomes one hunk of those lines before and
after. `git diff` can show you the bytes afterwards. Only this can tell you
*which rule* claimed them and how sure it was — which is the question a
reviewer actually has.

Edits that share a line are shown together, in one hunk. Printing the line
once per edit would show a state that never exists: the line with half its
changes applied.

`--diff` runs **after** the I4.3 check, so a preview never shows a change the
applier would then refuse to make.

## Not in schema 1

- **Composable overlaps.** Overlapping edits are refused outright. #12 leaves
  room for explicitly composable ones; nothing needs them yet.
- **stdin for the document.** The document is named by path so its bytes can
  be re-read for the `expect` and `bytes` checks.
