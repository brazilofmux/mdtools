# mdcheck — repository-aware validation

Status: shipped, 2026-08-12. Answers issue #13 for the offline core.

```console
$ mdcheck README.md docs/
docs/guide.md:14: error: no heading with anchor #instalation [links.broken-anchor]
docs/guide.md:31: warning: code fence has no language [check.fence-language]

0 error(s), 2 warning(s)
```

Read-only, offline, deterministic. No network and no model — a validator that
cannot run in a build gate is not one, which is why #13 asks for the offline
core first.

## Mostly composition

mdlinks already knows the link graph; mdfix already knows the dialect. mdcheck
runs both, adds the checks nothing else performs, and applies one policy over
the result.

That composition needs one piece of care. mdlinks sees an image as a link with
a destination, so a missing image would be reported twice — once as
`links.missing-file` and once as `check.missing-asset`. The more specific rule
wins at the same span. Two diagnostics for one problem is how a gate loses
trust.

## Rules

| Rule | Severity | |
|---|---|---|
| `check.missing-asset` | error | an image file that is neither beside the document nor on `asset_paths` |
| `check.image-alt` | warning | an image with no alt text |
| `check.fence-language` | warning | a code fence with no language |
| `check.unterminated-fence` | error | a fence that is never closed |
| `check.duplicate-definition` | error | a link label defined twice |
| `check.heading-skip` | warning | a heading level reached without passing through the one above it |
| `check.anchor-collision` | warning | two files claiming one anchor |
| `check.lossy-math`, `check.lossy-latex` | warning | constructs the IR treats as prose |
| `check.frontmatter-missing` | error | a required field, or no front matter at all |
| `check.frontmatter-type` | error | a field of the wrong kind |
| `check.frontmatter-value` | error | a value outside its `one_of` |
| `check.frontmatter-unknown` | warning or error | a field the schema does not mention |
| `check.frontmatter-invalid` | error | front matter that is not a YAML mapping |
| `check.unresolved-citation` | error | a citation key with no bibliography entry |
| `check.bibliography-unreadable` | error | a bibliography that could not be read |
| `dialect.*` | mixed | mdfix diagnostics: `fix` → error (required repairs), warnings stay warnings |
| `links.*` | mixed | everything [mdlinks](mdlinks.md) reports |

`check.anchor-collision` is the one that only makes sense repository-wide.
Within a single file Pandoc disambiguates with `-1` and `-2` suffixes; every
cross-file slug collision is reported (whether or not something links to it).
Unterminated fences use `check.unterminated-fence` only — the matching
`dialect.fence.unterminated` row is dropped so the gate does not double-count.

`check.heading-skip` is the opposite: a level sequence is a within-document
property, so it never looks across files. A file may **start** at any level —
a chapter that opens at `##` has nothing to descend from, and requiring `#`
would report every included fragment in a book. Climbing back up is not a
skip either; only descending more than one level at a time is.

It is a warning because there is no safe repair. `### A Voice in the Rubble`
under an `#` might want to be `##`, or the file might want a `##` above it,
and picking either is guessing at intent. Nothing is broken by a skip, which
is exactly how one survives: two chapters of *An Agnostic's Guide to the
Bible* carried one through six volumes and a full editorial pass, rendering a
size smaller than their peers and nesting a level deeper in the navigation.
Across 511 files of manuscript the rule fires four times — those two chapters,
and the same two again in the assembled volume — and no false positives.

## Where assets are gathered from

`check.missing-asset` resolves an image against the file that references it,
which is right for a repository whose markdown sits beside its pictures and
wrong for one whose build gathers them. `asset_paths` says where else to look:

```toml
[mdtools]
asset_paths = ["timelines", "images"]
```

Relative to the project root, like `bibliography` — the search path describes
the project's layout, not the directory a tool was run from. The referencing
file's own directory is tried **first and always**, so a layout that resolves
today keeps resolving. Each root is tried with the path as written and with
the bare file name, because a build that flattens `timelines/x.png` into the
output directory is the case this exists for.

*An Agnostic's Guide to the Bible* is that case: chapters at the repository
root, timelines in `timelines/`, and an assembler that copies each volume's
timeline beside the manuscript it belongs to. The path is correct at the
moment Pandoc reads it — the PNG is in the shipped EPUB — and five volumes
each reported a false error. It was the one finding class in that repository
that stopped `mdcheck` exiting 0 (issue #101).

mdlinks is not told any of this and should not be: resolving a destination
against the referencing file is all a link checker can know. mdcheck knows the
project, so it drops the `links.missing-file` that would otherwise take the
silenced error's place — the same "more specific rule wins" as above, applied
to the case where neither should fire.

## Suppression

```console
$ mdcheck --suppress check.image-alt --suppress 'links.*' docs/
```

A trailing `*` matches a prefix. `mdtools.toml` may carry a `suppress` list
under `[mdtools]`, which is added to whatever the command line gives:

```toml
[mdtools]
suppress = ["check.image-alt", "links.unused-*"]
```

## Output

| | |
|---|---|
| default | human, `path:line: severity: message [rule]` |
| `--diagnostics` | JSONL, per [diagnostics.md](diagnostics.md) |
| `--sarif` | SARIF 2.1.0, which CI systems already ingest |

Errors exit 1. Warnings alone exit 0 unless `--warnings` is passed, so a gate
can start strict about errors and tighten later.

A directory argument is walked for `*.md`, skipping `.git`.

## Front-matter schema

Off unless configured. A project with no `[frontmatter]` table is not failing
a check it never asked for, so with no schema this does nothing at all — not
even "every document should have front matter".

The schema lives in `mdtools.toml`. The needs are modest — is this key here,
is it the right kind of thing, is its value one of these — and a second file
in a second schema language would be more machinery than the question
deserves.

```toml
[mdtools.frontmatter]
unknown = "warn"                 # allow (default) | warn | error

[mdtools.frontmatter.fields.title]
type = "string"
required = true

[mdtools.frontmatter.fields.date]
type = "date"

[mdtools.frontmatter.fields.status]
one_of = ["draft", "review", "final"]
```

A field may declare `type`, `required` and `one_of`. Types are `string`,
`number`, `bool`, `list`, `date` and `any`, which is the default — a field
that declares only `one_of` accepts any kind of value from that set. `bool` is
not a `number`, though YAML and Python both blur that. A quoted ISO date
(`"2026-08-13"`) is still a `date`. `one_of` dates may be TOML date
literals or ISO strings; both compare as ISO.

A configured schema needs PyYAML. If it is missing, mdcheck exits 2 rather
than emitting a suppressible finding.

```console
$ mdcheck chapter.md
chapter.md:1: error: front matter is missing required field 'title' [check.frontmatter-missing]
chapter.md:3: error: front matter field 'date' should be a date, not a string [check.frontmatter-type]
chapter.md:4: error: front matter field 'status' is 'published'; expected one of 'draft', 'review', 'final' [check.frontmatter-value]
```

Three details worth knowing:

**A finding points at its own key.** The line comes from PyYAML's composer,
not from scanning for `^key:` — a key name appearing inside a value would fool
that. The *span* is the whole block, because that is what the IR records.

**No front matter is still missing fields.** A schema with required fields is
not satisfied by having no front matter at all; reporting only when a block
exists would mean deleting the block silently passes.

**One problem, one finding.** A field of the wrong type does not also report a
bad value — a single typo showing up as two findings is how a report stops
being read.

The schema itself is validated as strictly as it validates. A typo in
`requried` would silently stop requiring anything, so an unknown key, an
unknown type name or a malformed field table is an error — exit `2`, because
the project's settings are unusable and that is not a finding about the prose
([cli.md](cli.md)).

## Citations

Off unless a bibliography is named. A document with citations and no
bibliography is not making a mistake — it may be assembled later, or cited
into a system mdtools knows nothing about.

Sources:

| | |
|---|---|
| front matter `references:` | inline CSL, extra keys, merged with files |
| front matter `bibliography:` | a path, or a list of them, relative to the document |
| `mdtools.toml` `bibliography` | the project default, relative to its root |

A document that names `bibliography:` is saying what it cites against, and
the project default is not used — including an explicit empty list, which is
named-but-empty rather than not named. `references:` adds keys; it does not
replace a bibliography file. Formats are BibTeX/BibLaTeX (`.bib`), CSL JSON
and CSL YAML; only the keys are read, so none of this is a citation formatter.

```console
$ mdcheck paper.md
paper.md:7: error: no bibliography entry for @ghost [check.unresolved-citation]
```

**Named-but-empty is not the same as not named.** With no bibliography the
check does not run; with an empty one every citation really is unresolved.
Collapsing the two would make every citation in every unconfigured document an
error, which is the fastest way to have a check switched off.

**A bibliography that will not load is one finding, not one per citation.** An
unreadable file looks exactly like an empty one, so reporting each citation
would bury the finding that matters — the file — under noise from the
document.

## Not yet

**Unused references** — a bibliography entry nothing cites. It is the mirror
of the check above and much noisier: a shared `refs.bib` across a book is
expected to hold entries a given chapter does not cite, so it would need to be
a repository-wide question rather than a per-document one.
