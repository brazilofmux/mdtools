"""
CLI: python3 -m prosevary [options] file.md
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .embed import make_embedder
from .freeze import default_glossary_path, load_glossary_terms
from .llm import make_generator, make_judge
from .pipeline import run_pipeline
from .segment import parse
from .store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="prosevary",
        description="Controlled lexical variation for markdown prose (scaffold).",
    )
    p.add_argument("input", type=Path, help="Markdown file to process")
    p.add_argument(
        "-i",
        "--in-place",
        action="store_true",
        help="Write changes in place (creates .bak). Off by default; scaffold prefers dry-run.",
    )
    p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run (default): report decisions, write nothing",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Allow writes (still needs -i for in-place). Without -i, print rewritten text to stdout.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Show every decision")
    p.add_argument("--tau", type=float, default=0.92, help="Min cosine similarity (default 0.92)")
    p.add_argument("--k", type=int, default=4, help="Candidates per sentence (default 4)")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for synonym swaps")
    p.add_argument(
        "--max-sentences",
        type=int,
        default=None,
        help="Only process first N sentences (scaffold smoke tests)",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "prosevary.sqlite",
        help="SQLite database path",
    )
    p.add_argument(
        "--glossary",
        type=Path,
        default=None,
        help="glossary_terms.yaml (default: book root)",
    )
    p.add_argument(
        "--embed",
        choices=["auto", "hash", "ollama", "st"],
        default="auto",
        help="Embedding backend (default auto)",
    )
    p.add_argument(
        "--gen",
        choices=["auto", "null", "ollama"],
        default="auto",
        help="Paraphrase generator (default auto; null if no Ollama)",
    )
    p.add_argument(
        "--judge",
        choices=["auto", "null", "ollama"],
        default="auto",
        help="Accept/reject judge (default auto)",
    )
    p.add_argument(
        "--seed-synonyms",
        action="store_true",
        help="Load demo synonym rows into the DB and exit",
    )
    p.add_argument("--version", action="version", version=f"prosevary {__version__}")
    args = p.parse_args(argv)

    store = Store(args.db)
    if args.seed_synonyms:
        n = store.seed_demo_synonyms()
        print(f"Seeded {n} synonym rows into {args.db}")
        store.close()
        return 0

    # Always ensure demo synonyms exist for offline mode
    if not store.synonyms_for("however"):
        store.seed_demo_synonyms()

    gloss_path = args.glossary or default_glossary_path()
    glossary = (
        load_glossary_terms(gloss_path)
        if gloss_path is not None and gloss_path.is_file()
        else set()
    )
    if glossary:
        store.import_glossary(glossary)

    if not args.input.is_file():
        print(f"error: not a file: {args.input}", file=sys.stderr)
        return 2

    source = args.input.read_text(encoding="utf-8")
    doc = parse(source)

    embedder = make_embedder(args.embed)
    generator = make_generator(args.gen)
    judge = make_judge(args.judge)

    if args.verbose:
        n_sent = sum(len(r.sentences) for r in doc.regions)
        print(
            f"prosevary {__version__}: {args.input} — "
            f"{len(doc.regions)} prose regions, {n_sent} sentences\n"
            f"  embed={embedder.model_id}  gen={generator.model_id}  "
            f"judge={judge.model_id}  tau={args.tau}  k={args.k}\n"
            f"  glossary={gloss_path or '(none)'} ({len(glossary)} terms)  db={args.db}",
            file=sys.stderr,
        )

    result = run_pipeline(
        doc,
        store,
        embedder,
        generator,
        judge,
        glossary_terms=glossary,
        tau=args.tau,
        k=args.k,
        seed=args.seed,
        source_path=str(args.input),
        max_sentences=args.max_sentences,
    )

    accepted = result.accepted
    for d in result.decisions:
        show = args.verbose or d.status == "accepted"
        if not show:
            continue
        cos = f"{d.cosine:.4f}" if d.cosine is not None else "—"
        print(f"[{d.global_index}] {d.status} cos={cos}  {d.reason}")
        print(f"    ORIG: {d.original!r}")
        if d.candidate and d.candidate != d.original:
            print(f"    CAND: {d.candidate!r}")

    print(
        f"— {len(result.decisions)} sentences, {accepted} accepted, "
        f"{result.kept} kept, run_id={result.run_id}",
        file=sys.stderr,
    )

    want_write = args.apply or args.in_place
    if not want_write:
        store.close()
        return 0

    new_text = doc.reconstruct(result.replacements)
    if args.in_place:
        bak = args.input.with_suffix(args.input.suffix + ".bak")
        shutil.copy2(args.input, bak)
        args.input.write_text(new_text, encoding="utf-8")
        print(f"Wrote {args.input} (backup {bak})", file=sys.stderr)
    else:
        sys.stdout.write(new_text)

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
