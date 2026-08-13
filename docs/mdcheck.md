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
| `dialect.*` | error | mdfix's required repairs — the document is not canonical |
| `links.*` | mixed | everything [mdlinks](mdlinks.md) reports |

`check.anchor-collision` is the one that only makes sense repository-wide.
Within a single file Pandoc disambiguates with `-1` and `-2` suffixes, so a
collision matters only when two *files* claim the same anchor.

## Suppression

```console
$ mdcheck --suppress check.image-alt --suppress 'links.*' docs/
```

A trailing `*` matches a prefix. `mdtools.toml` may carry a `suppress` list
under `[mdtools]`, which is added to whatever the command line gives.

## Output

| | |
|---|---|
| default | human, `path:line: severity: message [rule]` |
| `--diagnostics` | JSONL, per [diagnostics.md](diagnostics.md) |
| `--sarif` | SARIF 2.1.0, which CI systems already ingest |

Errors exit 1. Warnings alone exit 0 unless `--warnings` is passed, so a gate
can start strict about errors and tighten later.

A directory argument is walked for `*.md`, skipping `.git`.

## Not yet

Front-matter schema validation and unresolved citations. Both are in #13 and
both want a schema to validate against, which `mdtools.toml` does not yet
describe.
