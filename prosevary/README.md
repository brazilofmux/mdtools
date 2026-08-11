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

# in-place only after you trust it (creates .bak)
python3 -m prosevary -i --accept 2-07-TheBenchmarkThatLied.md
```

Flags mirror `mdfix` muscle memory where it makes sense: `-i`, `-n`, `-v`.

## Status

Scaffold only: segmentation, freeze extraction, SQLite schema, embed/LLM
stubs, dry-run pipeline. No `-i` writes until the segmenter is proven on a
full chapter and the judge path is wired to a real local model.
