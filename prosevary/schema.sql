-- prosevary SQLite schema
-- One file on disk. No server. Safe to blow away and rebuild.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Curated (or WordNet-imported) synonym edges.
-- lemma is the surface form we match; synonym is a candidate replacement.
-- pos: n/v/a/r or '' if unknown.
-- domain: 'general' | 'tech-ok' | 'forbid-tech' (forbid-tech never used in
-- chapters that look technical — which is all of this book for now).
CREATE TABLE IF NOT EXISTS synonyms (
    id        INTEGER PRIMARY KEY,
    lemma     TEXT    NOT NULL COLLATE NOCASE,
    synonym   TEXT    NOT NULL,
    pos       TEXT    NOT NULL DEFAULT '',
    domain    TEXT    NOT NULL DEFAULT 'general',
    weight    REAL    NOT NULL DEFAULT 1.0,
    UNIQUE (lemma, synonym, pos)
);

CREATE INDEX IF NOT EXISTS idx_synonyms_lemma ON synonyms (lemma);

-- Optional: terms that must never be substituted (imported from glossary).
CREATE TABLE IF NOT EXISTS freeze_terms (
    term      TEXT PRIMARY KEY COLLATE NOCASE,
    source    TEXT NOT NULL DEFAULT 'manual'  -- 'glossary' | 'manual' | 'pattern'
);

-- Embedding cache. model_id distinguishes backends so a model swap invalidates
-- nothing by accident (lookups always include model_id).
CREATE TABLE IF NOT EXISTS embed_cache (
    model_id  TEXT    NOT NULL,
    text_hash TEXT    NOT NULL,   -- sha256 hex of exact text bytes
    dim       INTEGER NOT NULL,
    vector    BLOB    NOT NULL,   -- float32 little-endian
    created   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (model_id, text_hash)
);

-- Per-run decision log. Useful when tuning τ and prompts.
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    started    TEXT    NOT NULL DEFAULT (datetime('now')),
    source     TEXT    NOT NULL,   -- input path
    model_gen  TEXT,
    model_judge TEXT,
    model_embed TEXT,
    tau        REAL,
    k          INTEGER,
    notes      TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sent_index    INTEGER NOT NULL,
    original      TEXT    NOT NULL,
    candidate     TEXT,
    cosine        REAL,
    status        TEXT    NOT NULL,  -- 'kept' | 'accepted' | 'reject-freeze'
                                     -- | 'reject-tau' | 'reject-judge' | 'no-candidate'
    reason        TEXT,
    UNIQUE (run_id, sent_index, candidate)
);
