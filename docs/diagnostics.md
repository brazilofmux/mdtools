# Diagnostics

Status: shipped, 2026-08-12. Implements **ID.1–ID.3** in
[architecture.md](architecture.md); part of issue #12.

`mdfix --diagnostics` reports what it found as JSONL, so CI, an editor or
mdcheck can act on it without parsing English.

```console
$ mdfix -n --diagnostics --canonical chapter.md
{"kind":"diagnostic","path":"chapter.md","rule":"heading.atx-space","severity":"fix","line":1,"start":0,"end":6,"message":"headings: space after ATX marker"}
{"kind":"diagnostic","path":"chapter.md","rule":"list.blank-before","severity":"fix","line":4,"start":15,"end":20,"message":"blank line inserted before list"}
```

## Fields

| Field | Meaning |
|---|---|
| `kind` | Always `"diagnostic"`, so a mixed stream stays sortable |
| `path` | The file it came from |
| `rule` | Stable identifier — **this is the API** |
| `severity` | `fix` for something changed, `warning` for lint-only |
| `line` | 1-based |
| `start`, `end` | Byte span, half-open, into the file on disk |
| `message` | English. Useful to show a human, never to match on |

## Which stream

**stderr, and diagnostics own it.** `--emit-ir` and `--apply-edits` both write
the document to stdout, so a diagnostic there would corrupt the stream a
consumer is parsing.

`--diagnostics` therefore silences the human output — the summary, the
per-fix `-v` lines, the "Read N lines" banner. A progress line interleaved
with the JSONL would make the whole stream unparseable, because a consumer
cannot skip what it cannot recognize. Passing `-v` alongside is harmless; it
simply has no effect on the stream.

## Rule identifiers

The `rule` is the field to gate on. English messages may be reworded; these
may not. They are namespaced by area:

| Prefix | Covers |
|---|---|
| `list.` | bullet style, blank lines around lists |
| `heading.` | ATX spacing, trailing hashes, emphasis, Scrivener repair |
| `chicago.` | punctuation, abbreviations, and the two lint-only checks |
| `footnote.` | reference and definition formatting |
| `fence.` | code fence delimiters |
| `blockquote.`, `whitespace.`, `emphasis.`, `punct.`, `link.` | one rule each |

`heading.atx-space`, `list.blank-before` and `list.blank-after` are the three
**required** repairs from [transforms.md](transforms.md) — a document that
produces none of them at default settings is Pandoc-clean in the sense §3
means.

`chicago.serial-comma` and `chicago.number-style` are lint-only: they never
change the file and always carry `severity: "warning"`.

## Gating in CI

```bash
# fail if anything but the three required repairs would fire
mdfix -n --diagnostics --canonical *.md 2>&1 >/dev/null \
  | jq -e 'select(.severity == "fix"
                  and .rule != "heading.atx-space"
                  and .rule != "list.blank-before"
                  and .rule != "list.blank-after")' \
  && exit 1 || exit 0
```

`--canonical-lint` remains the blunt form: exit non-zero if the file is not
canonical. Diagnostics are for when you want to know *which* rule and *where*.

## Limits

**Spans are line-level.** A diagnostic points at the line that produced it,
not the exact characters. That satisfies ID.1, and carrying a sub-span
through every fixer is a wider change than the contract needs. When a fix
becomes an *edit* — the same shape, per
[edit-schema.md](edit-schema.md) — it will carry an exact span because the
applier requires one.

**One diagnostic per rule per line.** The scanner merges its hits per line
before reporting, so two ellipses on one line report once.

`fence.unterminated` is a warning when a fence closer never matches (the rest
of the file was left unchecked).

**Not yet emitted:** the L1 encoding errors from #53 and the under-report
warnings mdquery prints. Both are diagnostics in everything but format, and
should move to this stream.
