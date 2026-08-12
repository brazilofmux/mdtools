"""
Read the structural IR from mdfix.

This module is the only place mdquery learns anything about a Markdown file,
and it learns it by running `mdfix --emit-ir`. There is no Markdown grammar
anywhere in this package — that is the boundary in docs/dialect-policy.md §2,
and the tests assert it by grepping this source tree for block-level patterns.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

SCHEMA = "mdtools-ir-2"

# Flat parents that can hide nested blocks (no nesting yet). Used by
# under-report warnings; an unknown kind is still opaque-but-located.
CONTAINER_KINDS = frozenset({"list", "block_quote"})

# Schema 2 attributes every byte, so the runs between blocks arrive as `gap`
# records. They are structure, not content: a query for "the blocks in this
# file" means the content ones, so they are dropped on load and the totality
# guarantee is exercised by mdfix's own tests rather than carried into every
# consumer.
STRUCTURAL_KINDS = frozenset({"gap"})


class IRError(RuntimeError):
    """mdfix could not be run, or produced something this version cannot read."""


def find_mdfix() -> str:
    """
    Locate the mdfix binary.

    MDFIX wins so a developer can point at a build under test; then the
    sibling of this package (a git clone), then PATH (a make install).
    """
    override = os.environ.get("MDFIX")
    if override:
        if not Path(override).is_file():
            raise IRError(f"MDFIX={override} is not a file")
        return override

    sibling = Path(__file__).resolve().parent.parent / "mdfix" / "mdfix"
    if sibling.is_file():
        return str(sibling)

    found = shutil.which("mdfix")
    if found:
        return found
    raise IRError(
        "mdfix not found. Build it with `make -C mdfix`, install it, or set "
        "MDFIX to its path."
    )


@dataclass
class Block:
    """One IR record, enriched with the ancestry mdfix does not track."""

    kind: str
    start: int
    end: int
    line: int
    end_line: int
    protected: bool
    source: str
    raw: dict = field(default_factory=dict)
    # Heading-only, filled in by query.annotate().
    level: Optional[int] = None
    text: Optional[str] = None
    # Heading text with inline markup stripped by mdfix. Identifiers are
    # computed from this, never from `text`: reducing `[a](u)` to `a` is
    # Markdown grammar and belongs on the other side of the boundary.
    plain: Optional[str] = None
    slug: Optional[str] = None
    # Slugs of the enclosing headings, outermost first.
    ancestors: List[str] = field(default_factory=list)

    @property
    def form(self) -> Optional[str]:
        return self.raw.get("form")

    def to_dict(self) -> dict:
        out = {
            "path": self.source,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "line": self.line,
            "endLine": self.end_line,
            "protected": self.protected,
        }
        for key in ("level", "form", "htmlKind", "unterminated", "style"):
            if key in self.raw:
                out[key] = self.raw[key]
        if self.text is not None:
            out["text"] = self.text
        if self.plain is not None and self.plain != self.text:
            out["plain"] = self.plain
        if self.slug is not None:
            out["slug"] = self.slug
        if self.ancestors:
            out["ancestors"] = self.ancestors
        return out


@dataclass
class Document:
    path: Path
    schema: str
    byte_length: int
    line_count: int
    blocks: List[Block]

    def slice(self, block: Block) -> str:
        """The block's source text, read back from disk by its span."""
        data = self.path.read_bytes()
        return data[block.start:block.end].decode("utf-8", errors="replace")


def _records(paths: Iterable[Path], mdfix: Optional[str] = None) -> List[dict]:
    argv = [mdfix or find_mdfix(), "--emit-ir", *[str(p) for p in paths]]
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        raise IRError(f"could not run {argv[0]}: {exc}") from exc
    if result.returncode != 0:
        raise IRError(
            f"{argv[0]} --emit-ir failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    out = []
    for n, line in enumerate(result.stdout.splitlines(), 1):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise IRError(f"malformed IR on line {n}: {exc}") from exc
    return out


def raw_records(paths: Iterable[Path],
                mdfix: Optional[str] = None) -> List[dict]:
    """
    Every record as mdfix emitted it, `gap` records included.

    `load` drops gaps because a query for "the blocks in this file" means the
    content ones. A caller that has to reproduce the file — prosevary's
    reconstruct, or any serializer — needs the whole total sequence.
    """
    return _records(paths, mdfix)


def load(paths: Iterable[Path], mdfix: Optional[str] = None) -> List[Document]:
    """
    Parse `mdfix --emit-ir` output into one Document per input file.

    Several files share one stream; a `document` record starts each one.
    """
    documents: List[Document] = []
    current: Optional[Document] = None
    for record in _records(paths, mdfix):
        kind = record.get("kind")
        if kind == "document":
            schema = record.get("schema", "")
            if schema != SCHEMA:
                # Refusing is the point of the header record: a consumer that
                # guesses at an unknown schema reports wrong spans silently.
                raise IRError(
                    f"IR schema {schema!r} is not {SCHEMA!r}; "
                    "mdquery and mdfix are out of step"
                )
            current = Document(
                path=Path(record.get("source", "")),
                schema=schema,
                byte_length=record.get("bytes", 0),
                line_count=record.get("lines", 0),
                blocks=[],
            )
            documents.append(current)
            continue
        if current is None:
            raise IRError("IR began with a block record, before any document")
        if kind in STRUCTURAL_KINDS:
            continue
        current.blocks.append(Block(
            kind=kind or "unknown",
            start=record["start"],
            end=record["end"],
            line=record["line"],
            end_line=record["endLine"],
            protected=bool(record.get("protected", False)),
            source=str(current.path),
            raw=record,
            level=record.get("level"),
            text=record.get("text"),
            plain=record.get("plain"),
        ))
    return documents
