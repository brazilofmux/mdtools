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
| `list.` | bullet style, blank lines around lists, marker columns and doubts |
| `heading.` | ATX spacing, trailing hashes, emphasis, Scrivener repair |
| `chicago.` | punctuation, abbreviations, and the two lint-only checks |
| `footnote.` | reference and definition formatting |
| `fence.` | code fence delimiters |
| `unicode.` | normalization: `unicode.non-nfc` |
| `terms.` | glossary: `terms.forbidden`, `terms.undefined-acronym` |
| `blockquote.`, `whitespace.`, `emphasis.`, `punct.`, `link.` | one rule each |

`heading.atx-space`, `list.blank-before`, `list.blank-after` and
`list.marker-column` are the four **required** repairs from
[transforms.md](transforms.md) — a document that produces none of them at
default settings is Pandoc-clean in the sense §3 means.

`chicago.serial-comma` and `chicago.number-style` are lint-only: they never
change the file and always carry `severity: "warning"`. So does
`unicode.non-nfc`, which reports text that is not in Unicode NFC —
architecture **I1.2** says L1 detects normalization problems and does not fix
them. `mdfix --normalize-nfc` is the opt-in rewrite; see
[transforms.md](transforms.md).

## Gating in CI

```bash
# fail if anything but the four required repairs would fire
mdfix -n --diagnostics --canonical *.md 2>&1 >/dev/null \
  | jq -e 'select(.severity == "fix"
                  and .rule != "heading.atx-space"
                  and .rule != "list.blank-before"
                  and .rule != "list.blank-after"
                  and .rule != "list.marker-column")' \
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

`list.marker-ambiguous` is the other half of `list.marker-column`, and the
reason it is a warning is the whole of its design: it fires where Pandoc's
reading and the author's intent can come apart and mdfix must not pick. A lone
`A. text` opening a block is a list item one column short, or a name
abbreviated. `@key.` there is an example-list marker to Pandoc and a citation
to a reader. A word that parses as a roman numeral is a marker to Pandoc and a
word to everyone else. Never rewritten; reported so a person decides (#97).

It is deliberately silent mid-paragraph, where Pandoc and the author agree the
line is prose — that is where the shapes actually occur, and reporting them
would be pure noise. Over 511 files of manuscript the rule fires **zero**
times. It is a trap for what arrives, not a backlog to clear, which is what
makes a hit worth reading.

`unicode.non-nfc` is the exception to the line-level span above: it reports
the exact code point, because "somewhere on this line is a combining mark in
the wrong order" is not something a human can act on.

It used to over-report, and that was written down here as safe. It was not.
The UAX #15 quick check answers *maybe* — "this mark could compose with what
precedes it" — and a maybe was reported. Whether it composes depends on the
pair: U+1ECC followed by U+0300 has no precomposed form, so Yorùbá `Ọ̀ṣun` is
its own normal form and the warning was permanent on correct text (#103). A
candidate is now confirmed by normalizing the line and comparing, so the rule
agrees with `unicodedata` in both directions. The span still names the mark;
confirmation answers *whether*, not *where*.

**Not yet emitted:** the L1 encoding errors from #53 and the under-report
warnings mdquery prints. Both are diagnostics in everything but format, and
should move to this stream.
