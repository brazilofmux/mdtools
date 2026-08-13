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
| `check.missing-asset` | error | an image file that is not on disk |
| `check.image-alt` | warning | an image with no alt text |
| `check.fence-language` | warning | a code fence with no language |
| `check.unterminated-fence` | error | a fence that is never closed |
| `check.duplicate-definition` | error | a link label defined twice |
| `check.anchor-collision` | warning | two files claiming one anchor |
| `check.lossy-math`, `check.lossy-latex` | warning | constructs the IR treats as prose |
| `check.frontmatter-missing` | error | a required field, or no front matter at all |
| `check.frontmatter-type` | error | a field of the wrong kind |
| `check.frontmatter-value` | error | a value outside its `one_of` |
| `check.frontmatter-unknown` | warning or error | a field the schema does not mention |
| `check.frontmatter-invalid` | error | front matter that is not a YAML mapping |
| `dialect.*` | mixed | mdfix diagnostics: `fix` → error (required repairs), warnings stay warnings |
| `links.*` | mixed | everything [mdlinks](mdlinks.md) reports |

`check.anchor-collision` is the one that only makes sense repository-wide.
Within a single file Pandoc disambiguates with `-1` and `-2` suffixes; every
cross-file slug collision is reported (whether or not something links to it).
Unterminated fences use `check.unterminated-fence` only — the matching
`dialect.fence.unterminated` row is dropped so the gate does not double-count.

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

## Not yet

**Unresolved citations.** The IR does not emit citations yet, so there is
nothing for mdcheck to resolve — that is #88, and this check follows it.
