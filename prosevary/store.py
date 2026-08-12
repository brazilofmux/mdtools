"""
SQLite store for synonyms, freeze terms, embedding cache, and run logs.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Synonym pairs that change meaning rather than wording. Offline runs have only
# the freeze check as a real gate, so a curated synonym is an unsupervised
# accept — these must never be proposable. Both are rewrites the judge probes
# explicitly reject: `demonstrate -> prove` is probe #1, and `failed -> broke`
# shifts an outcome claim.
UNSAFE_SYNONYMS: Tuple[Tuple[str, str], ...] = (
    ("demonstrate", "prove"),
    ("failed", "broke"),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = _connect(db_path)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    return conn


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack_vector(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


class Store:
    def __init__(self, db_path: Path):
        self.path = db_path
        self.conn = init_db(db_path)

    def close(self) -> None:
        self.conn.close()

    # --- synonyms ---

    def add_synonym(
        self,
        lemma: str,
        synonym: str,
        pos: str = "",
        domain: str = "general",
        weight: float = 1.0,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO synonyms (lemma, synonym, pos, domain, weight)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(lemma, synonym, pos) DO UPDATE SET
                domain=excluded.domain,
                weight=excluded.weight
            """,
            (lemma, synonym, pos, domain, weight),
        )

    def synonyms_for(self, lemma: str, domain: Optional[str] = None) -> List[str]:
        if domain:
            rows = self.conn.execute(
                """
                SELECT synonym FROM synonyms
                WHERE lemma = ? COLLATE NOCASE AND domain = ?
                ORDER BY weight DESC
                """,
                (lemma, domain),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT synonym FROM synonyms
                WHERE lemma = ? COLLATE NOCASE AND domain != 'forbid-tech'
                ORDER BY weight DESC
                """,
                (lemma,),
            ).fetchall()
        return [r["synonym"] for r in rows]

    def purge_unsafe_synonyms(self) -> int:
        """
        Remove meaning-changing pairs an older seed may have left behind.

        Callers must invoke this on every run, not only when seeding. The CLI
        seeds only when the DB has no `however` rows, so a database written by
        an earlier version is never re-seeded and would keep these forever —
        which is precisely where they do harm.

        Both columns compare COLLATE NOCASE: `synonym` has no column-level
        collation, so a hand-added or imported `Prove` row would otherwise
        survive while its lowercase twin was removed.
        """
        removed = 0
        for lemma, syn in UNSAFE_SYNONYMS:
            cur = self.conn.execute(
                "DELETE FROM synonyms "
                "WHERE lemma = ? COLLATE NOCASE AND synonym = ? COLLATE NOCASE",
                (lemma, syn),
            )
            removed += cur.rowcount or 0
        if removed:
            self.conn.commit()
        return removed

    def seed_demo_synonyms(self) -> int:
        """Tiny built-in seed so dry-runs do something without WordNet."""
        # Meaning-changing pairs that the judge probes treat as rejects must
        # never appear here. Offline mode has only freeze as a real gate, so a
        # curated synonym is an unsupervised accept. Dropped on purpose:
        #   demonstrate → prove   (judge probe #1)
        #   failed → broke        (outcome shift)
        pairs = [
            ("however", "still", "r", "general"),
            ("however", "even so", "r", "general"),
            ("therefore", "so", "r", "general"),
            ("therefore", "thus", "r", "general"),
            ("utilize", "use", "v", "general"),
            ("utilize", "employ", "v", "general"),
            ("significant", "large", "a", "general"),
            ("significant", "substantial", "a", "general"),
            ("demonstrate", "show", "v", "general"),
            ("subsequently", "later", "r", "general"),
            ("subsequently", "afterward", "r", "general"),
            ("approximately", "about", "r", "general"),
            ("approximately", "roughly", "r", "general"),
            ("commence", "start", "v", "general"),
            ("commence", "begin", "v", "general"),
            ("prior", "earlier", "a", "general"),
            ("additional", "more", "a", "general"),
            ("additional", "further", "a", "general"),
            ("obtain", "get", "v", "general"),
            ("requires", "needs", "v", "general"),
            ("require", "need", "v", "general"),
            ("ensure", "make sure", "v", "general"),
            ("regarding", "about", "r", "general"),
            ("various", "several", "a", "general"),
            ("numerous", "many", "a", "general"),
            ("fundamental", "basic", "a", "general"),
            ("attempt", "try", "v", "general"),
            ("incorrect", "wrong", "a", "general"),
        ]
        self.purge_unsafe_synonyms()
        n = 0
        for lemma, syn, pos, domain in pairs:
            self.add_synonym(lemma, syn, pos, domain)
            n += 1
        self.conn.commit()
        return n

    # --- freeze ---

    def upsert_freeze(self, term: str, source: str = "manual") -> None:
        self.conn.execute(
            """
            INSERT INTO freeze_terms (term, source) VALUES (?, ?)
            ON CONFLICT(term) DO UPDATE SET source=excluded.source
            """,
            (term, source),
        )

    def import_glossary(self, terms: Iterable[str]) -> int:
        n = 0
        for t in terms:
            self.upsert_freeze(t, "glossary")
            n += 1
        self.conn.commit()
        return n

    def all_freeze_terms(self) -> List[str]:
        rows = self.conn.execute("SELECT term FROM freeze_terms").fetchall()
        return [r["term"] for r in rows]

    # --- embed cache ---

    def get_embedding(self, model_id: str, text: str) -> Optional[List[float]]:
        h = text_hash(text)
        row = self.conn.execute(
            "SELECT vector FROM embed_cache WHERE model_id = ? AND text_hash = ?",
            (model_id, h),
        ).fetchone()
        if not row:
            return None
        return unpack_vector(row["vector"])

    def put_embedding(self, model_id: str, text: str, vec: Sequence[float]) -> None:
        self.conn.execute(
            """
            INSERT INTO embed_cache (model_id, text_hash, dim, vector)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(model_id, text_hash) DO UPDATE SET
                dim=excluded.dim, vector=excluded.vector
            """,
            (model_id, text_hash(text), len(vec), pack_vector(vec)),
        )
        self.conn.commit()

    # --- runs ---

    def start_run(
        self,
        source: str,
        tau: float,
        k: int,
        model_gen: str = "",
        model_judge: str = "",
        model_embed: str = "",
        notes: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO runs (source, model_gen, model_judge, model_embed, tau, k, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, model_gen, model_judge, model_embed, tau, k, notes),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_run(self, run_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()

    def decisions_for_run(self, run_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT sent_index, original, candidate, cosine, status, reason
            FROM decisions WHERE run_id = ? ORDER BY sent_index
            """,
            (run_id,),
        ).fetchall()

    def log_decision(
        self,
        run_id: int,
        sent_index: int,
        original: str,
        candidate: Optional[str],
        cosine: Optional[float],
        status: str,
        reason: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO decisions
                (run_id, sent_index, original, candidate, cosine, status, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, sent_index, original, candidate, cosine, status, reason),
        )
        self.conn.commit()
