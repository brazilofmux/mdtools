# mdtools

Markdown structure and controlled prose variation.

| Tool | Language | Job |
|---|---|---|
| **mdtools** | Python 3 | One entry point: fix, query, terms, links, vary, config |
| **mdfix** | Ragel → C | Deterministic markdown auto-fixer (lists, headings, Chicago passes, …) |
| **mdquery** | Python 3 | Structural queries and extraction, via mdfix's IR |
| **mdterms** | Python 3 | Glossary and terminology enforcement; emits edits for mdfix |
| **mdlinks** | Python 3 | Link graph: broken anchors, undefined references, dead files |
| **mdcheck** | Python 3 | Repository-aware validation; JSONL or SARIF |
| **prosevary** | Python 3 | Generate-then-gate lexical variation (freeze terms, embeddings, local LLM) |

These used to be copy-pasted across the manuscripts for
[*Build Your Own Universe*](https://www.amazon.com/dp/B0HDK4DXWS) (SLOW-32),
[*MUSH Reference Library*](https://www.amazon.com/dp/B0H8PXRGBS),
[*Evolution of the Sacred*](https://www.amazon.com/dp/B0H1KPY947),
and others. This repo is the source of truth.

Writing a manuscript rather than a pass? [docs/writing.md](docs/writing.md) is
the dialect from the author's side — what you can write, and what the tools
will do with it.

Read [docs/architecture.md](docs/architecture.md) for the layering and the
invariants each stage guarantees,
[docs/transforms.md](docs/transforms.md) for which fixes run by default, then
[docs/dialect-policy.md](docs/dialect-policy.md) before adding a pass or a
tool. It fixes the input dialects, the single canonical output profile, and the
rule that Markdown grammar lives in exactly one implementation.
[docs/ir-schema.md](docs/ir-schema.md) is the interface that rule implies:
`mdfix --emit-ir` reports block structure with byte spans and
[docs/edit-schema.md](docs/edit-schema.md) is how changes come back, so a
consumer never has to re-derive the grammar. [docs/mdquery.md](docs/mdquery.md) is the first
tool built on it; [docs/mdterms.md](docs/mdterms.md) and
[docs/mdlinks.md](docs/mdlinks.md) followed.
[docs/cli.md](docs/cli.md) is the surface they all share: verbs, exit codes
and configuration. [docs/vendoring.md](docs/vendoring.md) covers the Unicode
tables mdfix carries and how a refresh is verified.

## Canonical history (mdfix)

As of the import (2026-08-11), copies ranked:

1. **`slow32-book/mdfix.rl`** ([*Build Your Own Universe*](https://www.amazon.com/dp/B0HDK4DXWS)) — newest; adds `--no-arrow-aside` (notation arrows stay `→`)
2. **`mush/mdfix.rl`** ([*MUSH Reference Library*](https://www.amazon.com/dp/B0H8PXRGBS)) — one feature behind (missing that flag)
3. **recycledreply / religions** ([*Evolution of the Sacred*](https://www.amazon.com/dp/B0H1KPY947)) **/ slow-32 examples** — older shared baseline

Import took (1). Downstream trees should install from here instead of vendoring.

## Build & install

### Requirements

- **mdfix:** `cc`, optionally `ragel` (to regenerate `mdfix.c` from `mdfix.rl`)
- **mdquery:** Python 3.10+ (stdlib only; needs a built `mdfix`)
- **mdterms:** Python 3.10+, `PyYAML`; needs a built `mdfix`
- **prosevary:** Python 3.10+, `PyYAML`; optional Ollama / sentence-transformers

### Install to `~/.local`

```bash
make install PREFIX=$HOME/.local
# ensure ~/.local/bin is on PATH
mdfix -h
mdtools --help
mdquery --help
mdterms --help
mdlinks --help
mdcheck --help
prosevary --help
```

Every tool answers the same three questions, and `--check` is the default:

```bash
mdlinks --check docs/*.md    # report
mdlinks --diff  docs/*.md    # what would change
mdlinks --fix   docs/*.md    # change it
```

Exit codes are the same everywhere: `0` clean, `1` findings, `2` the tool
could not run. The full contract, including `--config` and which tool has
which verb, is [docs/cli.md](docs/cli.md).

Or work from a clone without installing:

```bash
make -C mdfix
./mdfix/mdfix -n --no-arrow-aside chapter.md

PYTHONPATH=. python3 -m mdquery outline README.md
./scripts/mdquery stats docs/ir-schema.md

PYTHONPATH=. python3 -m prosevary -v chapter.md
```

### Checks

```bash
make test         # offline suite for mdfix + mdquery + prosevary (no network)
make check-sync   # committed mdfix.c is ragel's output for mdfix.rl
make asan         # address + UB sanitizers over the repo's own markdown
make check        # all three — what CI runs
make fuzz         # deep generative sweep under the sanitizers (minutes)
```

`make test` deliberately does **not** require ragel, so the committed
`mdfix.c` stays buildable without it. `check-sync` does require ragel and
fails rather than skipping: a source-integrity check that quietly passes when
it cannot run is worse than none.

`asan` exists because the test suite cannot see a few bytes written past a
heap allocation. A right-sized line buffer once overflowed on a
six-character input while 140 tests stayed green.

CI (`.github/workflows/ci.yml`) runs those three plus a Python 3.10–3.13
matrix — development happens on 3.14, so nothing else verifies the floor —
and a `-Wall -Wextra` pass. It is not `-Werror`: three warnings predate the
workflow and live in generated code.

### Book / mush consumers

```makefile
MDFIX    ?= mdfix
PROSEVARY ?= prosevary

fix-md:
	$(MDFIX) -i -v --no-arrow-aside *.md
```

For the SLOW-32 book ([*Build Your Own Universe*](https://www.amazon.com/dp/B0HDK4DXWS)),
**always** pass `--no-arrow-aside` when the editorial bundle is on. The
arrow-to-em-dash pass is correct for pure prose (e.g.
[*MUSH Reference Library*](https://www.amazon.com/dp/B0H8PXRGBS)) and wrong
for technical notation pipelines (`C → IR → asm`).

Since #60 the five editorial fixes need `--editorial` (implied by
`--canonical` and `--technical`). Profile consumers keep that behavior.
The bare recipe above does not enable editorial, so it only runs the L2
required repairs in [docs/transforms.md](docs/transforms.md); add
`--editorial --no-arrow-aside` if the other four rewrites (bullets, heading
emphasis, bold colons, blockquote spacing) are still wanted without a profile.

## Layout

```text
mdfix/           # Ragel source, generated C, Makefile
mdquery/          # Python package (python -m mdquery)
mdterms/          # Python package (python -m mdterms)
mdlinks/          # Python package (python -m mdlinks)
mdcheck/          # Python package (python -m mdcheck)
mdtools_cli/      # Python package behind the mdtools dispatcher
prosevary/        # Python package (python -m prosevary)
docs/             # architecture decisions (start with architecture.md)
scripts/          # install helpers
Makefile          # top-level build/install
```

## prosevary status

Scaffold: segmentation, freeze extraction, SQLite synonym/cache schema,
embed/LLM backends (Ollama / ST / offline hash), dry-run pipeline, and
advisory `--report` editorial metrics. Not a silent CI auto-fixer — human
`git diff` before commit. Expect a long road to `mdfix`-grade maturity; the
segmenter is the pacing item.

Optional later: a gRPC `VarySentence` service for warm model residency
(local embed service / local-model adapter).

## License

[MIT](LICENSE). The Unicode tables under `mdfix/vendor/` are extracts from
[libutf](https://github.com/brazilofmux/utf), also MIT.
