# mdtools — one entry point

Status: shipped, 2026-08-12. Answers issue #17 for dispatch and configuration.

```console
$ mdtools fix     chapter.md out.md
$ mdtools query   outline chapter.md
$ mdtools terms   chapter.md
$ mdtools links   chapter.md
$ mdtools vary    chapter.md
$ mdtools config
```

The standalone commands keep working and are not deprecated. `mdtools`
dispatches to exactly the same code — in process, so a traceback points at the
real module — and adds project configuration plus one set of exit codes.

## Exit codes

Shared by every verb, which is most of what #12 asked for:

| | |
|---|---|
| `0` | clean |
| `1` | findings — something a human should look at |
| `2` | usage or environment error: bad flags, missing file, bad config |

The distinction that matters is 1 versus 2. A build gate wants to fail on
findings; a broken invocation is a different problem and should not look like
a document defect.

## `mdtools.toml`

Optional. Discovered by walking up from the **input file** (not the working
directory), so running against another repository picks up that repository's
settings rather than the caller's. For `query`, the start path is the file
argument after the subcommand (`outline` / `blocks` / …), not the subcommand
name.

With no config file, defaults match bare tools: `profile = "none"`, wrap off.
Set a profile only when the project wants one.

```toml
[mdtools]
profile   = "technical"     # none | canonical | technical
wrap      = 78              # 0 disables
editorial = false           # only meaningful with profile = "none"
glossary  = "terms/glossary_terms.yaml"   # terms and vary
state_dir = ".mdtools"                    # prosevary --db lives here
mdfix     = "bin/mdfix"                 # absolute, root-relative, or PATH name
suppress  = ["check.image-alt"]           # mdcheck rule ids / prefix*
```

Paths resolve against the **project root**, never against the installed
package — no mutable state is written into the package tree. `mdfix` that
looks like a path is root-relative; a bare name is looked up on `PATH`.
`glossary` is passed to both `terms` and `vary`. `state_dir` sets prosevary's
database to `<state_dir>/prosevary.sqlite`. Configured `mdfix` is used by
`fix`, `query`, `links`, `terms`, and `check`. `suppress` is applied by
[mdcheck](mdcheck.md) (and `mdtools check`) in addition to CLI `--suppress`.

An unknown setting or a bad value is an **error**, not a warning. Silently
ignoring one is how a project comes to believe a setting applies when it does
not.

`mdtools config` prints what was resolved and where it came from, which is the
acceptance criterion and also the fastest way to answer why it did what it did.

### Flags win

If you pass any long `--flag` to `mdtools fix`, the configured profile is not
added. Short options (`-q`, `-i`, `-n`) still take the project profile — that
is the common install path. A long flag on the command line is a deliberate
override, and merging it with the profile would make the result hard to
predict.

### Python 3.11

`tomllib` is stdlib from 3.11. On 3.10 a config file is an **error** rather
than being ignored, for the reason above. Everything works without a config
file on every supported version.

## Verbs

| Verb | Tool |
|---|---|
| `fix` | mdfix |
| `query` | [mdquery](mdquery.md) |
| `terms` | [mdterms](mdterms.md) |
| `links` | [mdlinks](mdlinks.md) |
| `vary` | prosevary |
| `config` | the resolved configuration |
| `check` | [mdcheck](mdcheck.md) |


