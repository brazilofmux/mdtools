# mdtools

Private toolkit for markdown structure and controlled prose variation.

| Tool | Language | Job |
|---|---|---|
| **mdfix** | Ragel → C | Deterministic markdown auto-fixer (lists, headings, Chicago passes, …) |
| **mdquery** | Python 3 | Structural queries and extraction, via mdfix's IR |
| **prosevary** | Python 3 | Generate-then-gate lexical variation (freeze terms, embeddings, local LLM) |

These used to be copy-pasted across `slow32-book`, `mush`, `religions`, and others.
This repo is the source of truth.

Read [docs/architecture.md](docs/architecture.md) for the layering and the
invariants each stage guarantees,
[docs/transforms.md](docs/transforms.md) for which fixes run by default, then
[docs/dialect-policy.md](docs/dialect-policy.md) before adding a pass or a
tool. It fixes the input dialects, the single canonical output profile, and the
rule that Markdown grammar lives in exactly one implementation.
[docs/ir-schema.md](docs/ir-schema.md) is the interface that rule implies:
`mdfix --emit-ir` reports block structure with byte spans, so a consumer never
has to re-derive the grammar. [docs/mdquery.md](docs/mdquery.md) is the first
tool built on it.

## Canonical history (mdfix)

As of the import (2026-08-11), copies ranked:

1. **`slow32-book/mdfix.rl`** — newest; adds `--no-arrow-aside` (notation arrows stay `→`)
2. **`mush/mdfix.rl`** — one feature behind (missing that flag)
3. **recycledreply / religions / slow-32 examples** — older shared baseline

Import took (1). Downstream trees should install from here instead of vendoring.

## Build & install

### Requirements

- **mdfix:** `cc`, optionally `ragel` (to regenerate `mdfix.c` from `mdfix.rl`)
- **mdquery:** Python 3.10+ (stdlib only; needs a built `mdfix`)
- **prosevary:** Python 3.10+, `PyYAML`; optional Ollama / sentence-transformers

### Install to `~/.local`

```bash
make install PREFIX=$HOME/.local
# ensure ~/.local/bin is on PATH
mdfix -h
mdquery --help
prosevary --help
```

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

For the SLOW-32 book series, **always** pass `--no-arrow-aside`. The default
arrow→em-dash pass is correct for pure prose projects (e.g., mush) and wrong
for technical notation pipelines (`C → IR → asm`).

## Layout

```text
mdfix/           # Ragel source, generated C, Makefile
mdquery/          # Python package (python -m mdquery)
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

Optional later: gRPC `VarySentence` service for warm model residency
(Hyperia-style composition with embed-svc / local-model adapter).

## License

Private. All rights reserved unless noted otherwise.
