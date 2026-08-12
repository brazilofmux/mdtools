"""
CLI: python3 -m prosevary [options] file.md
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .embed import make_embedder
from .freeze import default_glossary_path, load_glossary_terms
from .llm import OPENAI_URL, make_generator, make_judge, openai_available, probe_judge
from .metrics import compare, format_report
from .pipeline import active_gates, run_pipeline
from .segment import parse
from .store import Store


def run_judge_probes(judge) -> int:
    """Report whether a judge actually rejects meaning-changing rewrites."""
    print(f"judge probes — {judge.model_id}")
    if not getattr(judge, "enforcing", True):
        print("  this judge accepts unconditionally; probes are a formality.")

    results = probe_judge(judge)
    for r in results:
        if r["error"]:
            verdict = f"ERROR  {r['error']}"
        else:
            want = "accept" if r["want_accept"] else "reject"
            got = "accept" if r["got_accept"] else "reject"
            verdict = f"want {want}, got {got}  {'ok' if r['correct'] else '<-- WRONG'}"
        print(f"\n  {r['note']}")
        print(f"    ORIG: {r['original']}")
        print(f"    CAND: {r['candidate']}")
        print(f"    {verdict}")
        if r["reason"]:
            print(f"    judge said: {r['reason'][:160]}")

    correct = sum(1 for r in results if r["correct"])
    errors = sum(1 for r in results if r["error"])
    should_reject = [r for r in results if not r["want_accept"] and not r["error"]]
    rubber_stamp = should_reject and all(r["got_accept"] for r in should_reject)

    print(f"\n  {correct}/{len(results)} correct" + (f", {errors} error(s)" if errors else ""))
    if rubber_stamp:
        print(
            "  RUBBER STAMP: accepted every meaning-changing rewrite.\n"
            "  This judge is a NullJudge with latency — do not trust it with -i."
        )
        return 1
    if correct < len(results):
        print("  Judge is imperfect but not rubber-stamping. Weigh before -i.")
    return 0 if correct == len(results) else 1


def report_stored_run(store: Store, run_id: int) -> int:
    """Metrics for a completed run, rebuilt from the decisions log."""
    run = store.get_run(run_id)
    if run is None:
        print(f"error: no such run_id: {run_id}", file=sys.stderr)
        return 2
    rows = store.decisions_for_run(run_id)
    if not rows:
        print(f"error: run {run_id} logged no decisions", file=sys.stderr)
        return 2

    # Join sentences into pseudo-documents. Paragraph breaks are lost, so
    # per-sentence metrics stay valid but the text is not the original file.
    before = "\n\n".join(r["original"].strip() for r in rows)
    after = "\n\n".join(
        (r["candidate"] if r["status"] == "accepted" and r["candidate"] else r["original"]).strip()
        for r in rows
    )
    cosines = [
        r["cosine"] for r in rows if r["status"] == "accepted" and r["cosine"] is not None
    ]
    embed_model = run["model_embed"] or ""
    # Historical rows only carry the model_id string, so recover semantic-ness
    # from the name the backend registered under.
    semantic = not embed_model.startswith("hash-embed")

    accepted = sum(1 for r in rows if r["status"] == "accepted")
    # notes carries the --allow-inert-gates record. Writing it and never
    # showing it made the "logged in run metadata" promise unverifiable
    # without opening sqlite by hand.
    notes = (run["notes"] or "").strip()
    print(
        f"run {run_id}: {run['source']} — {len(rows)} sentences, {accepted} accepted\n"
        f"  tau={run['tau']}  k={run['k']}  embed={embed_model or '(unknown)'}  "
        f"judge={run['model_judge'] or '(unknown)'}"
        + (f"\n  notes: {notes}" if notes else "")
    )
    metrics = compare(
        before, after, cosines=cosines, semantic=semantic, embed_model=embed_model
    )
    print(format_report(metrics, title=f"run {run_id}"))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="prosevary",
        description="Controlled lexical variation for markdown prose (scaffold).",
    )
    p.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="Markdown file to process (not needed with --report-run)",
    )
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
        help="Force dry run: report decisions, write nothing. Overrides -i/--apply "
        "and implies -v, matching mdfix. Dry run is also the default when neither "
        "-i nor --apply is given.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Allow writes (still needs -i for in-place). Without -i, print rewritten text to stdout.",
    )
    p.add_argument(
        "--allow-inert-gates",
        action="store_true",
        help="UNSAFE: permit -i/--apply when the tau and/or judge gates are inert "
        "(hash embedder is not semantic; null judge accepts everything). "
        "Dry-run never needs this. Logged in run metadata when used.",
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
        choices=["auto", "null", "ollama", "openai"],
        default="auto",
        help="Paraphrase generator (default auto; null if no server is up)",
    )
    p.add_argument(
        "--judge",
        choices=["auto", "null", "ollama", "openai"],
        default="auto",
        help="Accept/reject judge (default auto)",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("PROSEVARY_BASE_URL") or None,
        metavar="URL",
        help="OpenAI-compatible endpoint for --gen/--judge openai "
        f"(default $PROSEVARY_BASE_URL, else {OPENAI_URL}; "
        "mlx_lm.server, llama.cpp, LM Studio, vLLM)",
    )
    p.add_argument(
        "--gen-model",
        default=None,
        help="Model name for the generator (else $PROSEVARY_GEN_MODEL)",
    )
    p.add_argument(
        "--judge-model",
        default=None,
        help="Model name for the judge (else $PROSEVARY_JUDGE_MODEL)",
    )
    p.add_argument(
        "--test-judge",
        action="store_true",
        help="Run the judge probes (meaning-changing rewrites it must reject) "
        "and exit. Use before trusting a model with -i.",
    )
    p.add_argument(
        "--seed-synonyms",
        action="store_true",
        help="Load demo synonym rows into the DB and exit",
    )
    p.add_argument(
        "--report",
        action="store_true",
        help="Print before/after editorial metrics for this run (advisory; "
        "never gates acceptance). Works in dry run.",
    )
    p.add_argument(
        "--report-run",
        type=int,
        metavar="ID",
        default=None,
        help="Print metrics for a stored run_id and exit — compare tau sweeps "
        "without re-paraphrasing. Ignores the input file.",
    )
    p.add_argument("--version", action="version", version=f"prosevary {__version__}")
    args = p.parse_args(argv)

    # mdfix contract: -n wins over -i/--apply, and -n implies -v.
    want_write = (args.apply or args.in_place) and not args.dry_run
    if args.dry_run:
        args.verbose = True
        if args.apply or args.in_place:
            print(
                "note: -n overrides -i/--apply — nothing will be written",
                file=sys.stderr,
            )

    store = Store(args.db)
    if args.report_run is not None:
        rc = report_stored_run(store, args.report_run)
        store.close()
        return rc

    # Fail with instructions rather than a urllib traceback deep in the run.
    if "openai" in (args.gen, args.judge):
        url = args.base_url or OPENAI_URL
        if not openai_available(url):
            print(
                f"error: no OpenAI-compatible server at {url}\n"
                f"  start one, e.g.:\n"
                f"    mlx_lm.server --model <hf-repo-or-path> --port "
                f"{url.rsplit(':', 1)[-1]}",
                file=sys.stderr,
            )
            store.close()
            return 2

    if args.test_judge:
        judge = make_judge(args.judge, args.judge_model, args.base_url)
        rc = run_judge_probes(judge)
        store.close()
        return rc

    if args.input is None:
        p.error("input file required (or use --report-run ID)")

    if args.seed_synonyms:
        n = store.seed_demo_synonyms()
        print(f"Seeded {n} synonym rows into {args.db}")
        store.close()
        return 0

    # Unconditional: the seed below runs only on a fresh DB, so a database
    # written by an earlier version would otherwise keep the meaning-changing
    # pairs forever — exactly where they can still be applied.
    store.purge_unsafe_synonyms()

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
    generator = make_generator(args.gen, args.gen_model, args.base_url)
    judge = make_judge(args.judge, args.judge_model, args.base_url)

    gates = active_gates(embedder, judge)
    inert = [g for g in ("tau", "judge") if g not in gates]
    run_notes = ""

    if args.verbose:
        n_sent = sum(len(r.sentences) for r in doc.regions)
        print(
            f"prosevary {__version__}: {args.input} — "
            f"{len(doc.regions)} prose regions, {n_sent} sentences\n"
            f"  embed={embedder.model_id}  gen={generator.model_id}  "
            f"judge={judge.model_id}  tau={args.tau}  k={args.k}\n"
            f"  gates={'+'.join(gates)}"
            f"{'  inert=' + '+'.join(inert) if inert else ''}\n"
            f"  glossary={gloss_path or '(none)'} ({len(glossary)} terms)  db={args.db}",
            file=sys.stderr,
        )

    if inert and want_write:
        detail = []
        if "tau" in inert:
            detail.append(f"cosine from {embedder.model_id} is not semantic")
        if "judge" in inert:
            detail.append(f"{judge.model_id} accepts everything")
        detail_s = "; ".join(detail)
        if not args.allow_inert_gates:
            print(
                "error: refusing to write with inert gates — " + detail_s + ".\n"
                "  Only the freeze check would vet these rewrites.\n"
                "  Use a semantic embedder (--embed st or Ollama) and an enforcing\n"
                "  judge, run dry-run (default / -n) to inspect candidates, or pass\n"
                "  --allow-inert-gates if you explicitly accept the risk.",
                file=sys.stderr,
            )
            store.close()
            return 2
        run_notes = "UNSAFE: --allow-inert-gates; inert=" + "+".join(inert)
        print(
            "WARNING: writing with inert gates via --allow-inert-gates — "
            + detail_s
            + ".\n"
            "         Only the freeze check vetted these rewrites. "
            "This is logged in run metadata.",
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
        notes=run_notes,
    )

    accepted = result.accepted
    # When the rewritten document goes to stdout, the report must not: it would
    # corrupt `prosevary --apply f.md > out.md`.
    report = sys.stderr if (want_write and not args.in_place) else sys.stdout
    for d in result.decisions:
        show = args.verbose or d.status == "accepted"
        if not show:
            continue
        cos = f"{d.cosine:.4f}" if d.cosine is not None else "—"
        print(f"[{d.global_index}] {d.status} cos={cos}  {d.reason}", file=report)
        print(f"    ORIG: {d.original!r}", file=report)
        if d.candidate and d.candidate != d.original:
            print(f"    CAND: {d.candidate!r}", file=report)

    print(
        f"— {len(result.decisions)} sentences, {accepted} accepted, "
        f"{result.kept} kept, run_id={result.run_id}",
        file=sys.stderr,
    )

    new_text = doc.reconstruct(result.replacements)

    if args.report:
        cosines = [
            d.cosine
            for d in result.decisions
            if d.status == "accepted" and d.cosine is not None
        ]
        print(
            format_report(
                compare(
                    source,
                    new_text,
                    cosines=cosines,
                    semantic=getattr(embedder, "semantic", True),
                    embed_model=embedder.model_id,
                ),
                title=str(args.input),
            ),
            file=report,
        )

    if not want_write:
        store.close()
        return 0

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
