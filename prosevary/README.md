# prosevary — controlled lexical variation for markdown prose

Part of **mdtools** (sibling to `mdfix`, not an extension of it). `mdfix` is a
deterministic Ragel scanner for structure and punctuation. This tool is a
**generate-then-gate** pass over sentence-level prose:

1. Structure-aware markdown segmentation (skip fences, tables, pure quotes)
2. Sentence boundaries in real paragraph text
3. Freeze list (glossary terms, code spans, proper-name patterns)
4. Generate *K* candidates (local LLM paraphrase, or constrained synonym swap)
5. Accept only if freeze set survives, embedding cosine ≥ τ, and a local LLM
   judge signs off on meaning + voice
6. Reassemble; default is dry-run

## Language choice

| Layer | Language | Why |
|---|---|---|
| Orchestration, YAML freeze, JSON logs, HTTP to models | **Python 3** | Matches Hyperia embed clients; rapid prompt/iteration work |
| Synonym lexicon + run/embedding cache | **SQLite** (one file) | Large-ish word data without a server; first-class in C *and* Python if we split later |
| Markdown segmenter / freeze tokenizer (future) | **C optional** | Only if Python becomes the bottleneck or we want mdfix-grade structural guarantees |

**C is a poor fit for the whole tool.** The hard dependencies are:

- A sentence embedding model (PyTorch / ONNX / Ollama embeddings API)
- A local instruct model for generate + judge (Ollama, llama.cpp HTTP, etc.)
- YAML glossary import, JSON candidate logs, rapid iteration on prompts

Those dominate both runtime and engineering cost. Writing the *glue* in C
buys nothing; writing a future segmenter in C is plausible once the protocol
stabilizes.

**Do not vendor Hyperia as a hard dependency.** Reuse *ideas* and optionally
point at a running `embed-svc` gRPC endpoint. Default path is local:

- embeddings: Ollama `/api/embeddings` or `sentence-transformers` in-process
- generate/judge: Ollama `/api/chat`

If you reuse Hyperia’s preprocessor, turn **lowercase off** — this book is
full of `SLOW-32`, `DBT`, and commit-shaped identifiers.

## Database: what needs one, and how big

You do **not** need Postgres, Redis, or a vector DB for a four-volume book.

| Data | Scale | Store |
|---|---|---|
| Freeze terms (glossary + aliases) | hundreds | YAML → in-memory set at start |
| Style synonym map (curated) | 5k–50k rows | SQLite `synonyms` |
| Full WordNet (optional later) | ~150k lemmas, tens of MB | SQLite import, same schema |
| Embedding cache (orig + candidates) | ~20k sentences × 384×4B ≈ 30 MB | SQLite `embed_cache` BLOBs, or skip and recompute |
| Run log (accept/reject reasons) | per invocation | SQLite `runs` / JSONL alongside |

**SQLite is the right ceiling.** It handles synonym fan-out, exact-key lookup,
and optional FTS for exploration. A “large language” vocabulary is still a
laptop file. Ship a small curated `data/synonyms.sqlite` for v1; offer a
WordNet import script only if constrained synonym mode proves useful.

WordNet caveat: many “synonyms” are wrong in technical prose (`run` ≠
`execute` in a bootstrap chapter). Prefer a **curated** table with POS tags
and a `domain` flag (`general` / `forbid-tech`), and let the LLM path do most
of the variation.

## What this is not

- Not a silent CI auto-fixer. Human `git diff` before commit.
- Not a watermark stripper product. Lexical diversity and technical freeze
  fidelity are the stated goals; watermark fragility is a side effect of
  paraphrase, not the README pitch.
- Not allowed to rewrite blockquotes, fenced code, or inline code spans.

## CLI (scaffold)

```bash
# dry-run (default): print candidates, write nothing
python3 -m prosevary 1-07-Relocations.md

# tighter semantic leash, more candidates
python3 -m prosevary --tau 0.94 --k 4 2-07-TheBenchmarkThatLied.md

# in-place only after you trust it (creates .bak); needs live semantic
# embedder + enforcing judge, or the explicit unsafe override
python3 -m prosevary -i --apply 2-07-TheBenchmarkThatLied.md

# before/after editorial metrics (advisory; works in dry run)
python3 -m prosevary --report 1-07-Relocations.md

# re-report a stored run without re-paraphrasing — for tau sweeps
python3 -m prosevary --report-run 12
```

Flags mirror `mdfix` muscle memory where it makes sense: `-i`, `-n`, `-v`.
As in `mdfix`, `-n` beats `-i`/`--apply` and implies `-v`.

**Write safety.** `-i` / `--apply` require both a semantic embedder and an
enforcing judge. The offline default (hash embed + null judge) only freezes
structural tokens — that is enough for dry-run inspection, not for writing.
To force a write with inert gates, pass `--allow-inert-gates` (logged in run
metadata). Demo synonyms never include meaning-changing pairs such as
`demonstrate → prove` (see issue #4).

## Model backends

Three transports for generate/judge: `null` (offline), `ollama` (native
`/api/chat`), and `openai` (`/v1/chat/completions`). The OpenAI-compatible
path is deliberately not MLX-specific — it also covers llama.cpp, LM Studio,
and vLLM.

```bash
# terminal 1 — any OpenAI-compatible server; mlx_lm.server defaults to :8080
mlx_lm.server --model mlx-community/Josiefied-Qwen3-30B-A3B-abliterated-v2-6bit

# terminal 2 — prove the judge actually rejects before trusting it
python3 -m prosevary --test-judge --judge openai \
    --judge-model mlx-community/Josiefied-Qwen3-30B-A3B-abliterated-v2-6bit

# then a real dry run with metrics
python3 -m prosevary --gen openai --judge openai --report chapter.md
```

`--base-url` points elsewhere, defaulting to `$PROSEVARY_BASE_URL`;
`$PROSEVARY_GEN_MODEL` / `$PROSEVARY_JUDGE_MODEL` name the models. Judges run
at temperature 0 (classification, not composition); generators at 0.7.

Port 8080 is popular — Podman/Docker's `gvproxy` and many dev servers grab it.
If `mlx_lm.server` dies with `Errno 48: Address already in use`, pick another
port and point prosevary at it:

```bash
mlx_lm.server --model <repo> --port 8081
export PROSEVARY_BASE_URL=http://127.0.0.1:8081
```

The preflight check requires `GET /v1/models` to return 200, so prosevary
refuses to talk to a non-LLM service that happens to hold the port.

**Reasoning models are handled.** Qwen3-style `<think>…</think>` blocks are
stripped before JSON parsing, including unclosed blocks from a token-limited
reply, and the *last* JSON object wins so a model that reconsiders mid-answer
is read correctly. Unparseable judge output falls back to **reject**. Only a
JSON object whose `accept` field is the literal boolean `true` accepts;
string `"false"`, numbers, missing keys, and free-form prose all reject
(fail closed — see issue #3).

### Test the judge before trusting it

`--test-judge` runs five probes: four rewrites that change a technical claim
(`demonstrate` → `prove`, a changed number, a negated outcome, a reversed
ordering) plus one genuine paraphrase as a control. Every probe preserves all
freeze terms and stays fluent, so freeze and tau cannot catch them — the judge
is the only gate that can.

This matters most for **abliterated or compliance-tuned models**, which are
trained away from refusing. The judge's whole job is to refuse. A model that
accepts all four is reported as a RUBBER STAMP and exits nonzero:

```
  1/5 correct
  RUBBER STAMP: accepted every meaning-changing rewrite.
  This judge is a NullJudge with latency — do not trust it with -i.
```

Note `mlx_lm.server` serves chat only — it has **no embeddings endpoint**, so
on an MLX-only setup the tau gate is **skipped, not enforced**, and acceptance
rests on freeze + judge. That is deliberate: the `hash-embed` cosine is a
token-overlap score, so it rates a one-word synonym swap ~0.92 and a genuine
full-sentence rewrite ~0.24 — exactly backwards. Letting it gate would pass
trivial edits and block good ones. The cosine is still logged as telemetry.

To get a real tau gate, use `--embed st` or run Ollama with `nomic-embed-text`
purely as an embedder alongside the chat server.

## Measuring whether it helped

The tool has no second goal. There is no watermark objective to chase — if
the editorial metrics improve, it is doing useful work, and that is the whole
scoreboard. `--report` prints before/after over prose regions only (fences,
tables, and quotes never reach the counters):

| Metric | Direction | Why |
|---|---|---|
| repetition (3-gram) | telemetry | paraphrase lowers this by construction |
| lexical variety (MATTR) | telemetry | gameable by churn; length-robust vs plain TTR |
| sentence shape (stdev) | **higher better** | a drop = rhythm homogenized |
| opener variety | **higher better** | a drop = sentences start alike |
| readability (Flesch) | telemetry | large swing either way = register moved |
| semantic preservation | **higher better** | mean cosine over accepted rewrites |

Repetition and lexical variety are deliberately *not* treated as goals.
A paraphraser raises both mechanically, so optimizing on them rewards churn.
The load-bearing ones are the guardrails: LLM paraphrase homogenizes rhythm,
and sentence-shape/opener variety catch exactly that. Regressions are flagged.

Semantic preservation reports `n/a` on the `hash-embed` fallback rather than
printing a number that looks like evidence — that gate needs a real embedder.
Metrics are advisory and never feed the accept path.

## Field notes — first real-model run (2026-08-11)

`mlx-community/Josiefied-Qwen3-30B-A3B-abliterated-v2-6bit` via
`mlx_lm.server` 0.31.3 on an M5 Max, as both generator and judge. Recorded
because the results were not what was predicted.

**The judge scored 5/5 on `--test-judge`.** The prediction was that an
abliterated model would rubber-stamp, since such models are tuned away from
refusing. It did not. It rejected all four meaning-changing rewrites,
including `demonstrate` → `prove` ("subtle shift in technical meaning"), and
accepted the control. Roughly 1.9 s per verdict. Abliteration did not
compromise judging here — but run the probes on any new model rather than
assuming, which is what they are for.

**Two bugs only a live model could surface:**

1. `mlx_lm.server` returns thinking in a separate `reasoning` field, and Qwen3
   spent 236 completion tokens on a one-sentence verdict. With no `max_tokens`
   set, a longer sentence exhausts the budget and `content` comes back empty —
   which the judge parser reads as a **reject**. Every verdict would have been
   silently wrong in the safe-looking direction. Now capped, and empty content
   with `finish_reason=length` raises instead of returning `""`.
2. The tau gate was enforced with a non-semantic embedder and rejected every
   candidate at cosines of 0.14–0.73. See the backwards-scoring note above.

**Where the judge is weak: voice, not meaning.** It accepted
`Various fundamental problems appear at this stage.` →
`Numerous core challenges emerge during this phase.` — precisely the corporate
register its own system prompt forbids. It also allowed `fixup` →
`adjustment`, losing linker terminology, because no glossary was loaded.
Meaning is well defended; register and terminology are not.

**The metrics guardrail fired on that same run**, unprompted:

```text
sentence shape (stdev)   3.4351 -> 2.7928   -0.6423  <-- REGRESSION
```

Exactly the homogenization the metric exists to catch, on the first real
output. Treat a shape regression as a signal to read the diff closely.

## Status

Scaffold: segmentation, freeze extraction, SQLite schema, embed/LLM backends,
dry-run pipeline, advisory metrics, and a judge proven against probes. Still
no `-i` writes recommended until the segmenter is proven on a full chapter.

Nowhere near `mdfix` maturity, and the gap is mostly the segmenter. Known
sharp edges:

- Sentences that span a wrapped line carry the embedded newline. Reconstruction
  is byte-exact today, but a real LLM candidate will come back as one line, so
  accepted rewrites will reflow the paragraph. Trailing newlines are preserved;
  mid-sentence wrapping is not yet.
- Sentence splitting is regex-based and will mis-split on `Dr.`-style abbrevs.
- On the offline default stack the tau and judge gates are inert; only the
  freeze check would vet a rewrite. The CLI refuses `-i`/`--apply` unless
  `--allow-inert-gates` is passed (and logs that override).
- No glossary is loaded by default, so terms of art (`fixup`, `relocation`)
  are unprotected unless `glossary_terms.yaml` is found. See the field notes.
- Block protection now covers fenced code, GFM tables (with or without a
  leading `|`), indented code, HTML blocks/comments, Setext headings, and
  reference/footnote definitions. Inline freeze covers links, images,
  autolinks, footnote refs, Pandoc attributes, and simple citations. Matching
  backtick-run inline code, occurrence counts, and full Pandoc IR remain open
  (see issues #2 follow-ups and #6).
- List marker lines are frozen; list-continuation paragraphs that are not
  indented code can still be exposed as prose.
- The first shared regression fixture covers delimiter-aware fenced blocks and
  byte-exact prosevary reconstruction. Broader round-trip, idempotence, and
  failure-path coverage is still needed before `-i` is trusted.
