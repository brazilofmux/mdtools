"""
Ancestry, filters, and section extraction over the structural IR.

Everything here works from spans and heading levels. Nothing inspects Markdown
syntax: the one place that would be tempting — computing a heading's anchor —
delegates to slug.py, which operates on the text mdfix already extracted.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from .ir import Block, Document
from .slug import assign_slugs


def annotate(document: Document) -> Document:
    """
    Fill in heading slugs and, for every block, its enclosing heading slugs.

    Ancestry is by heading level, so a level-3 block under `## Two` under
    `# One` carries ["one", "two"]. Pandoc's duplicate suffixes mean slugs are
    a whole-document property, which is why this runs over all headings at once
    rather than per block.
    """
    headings = [b for b in document.blocks if b.kind == "heading"]
    for block, slug in zip(headings, assign_slugs([b.text or "" for b in headings])):
        block.slug = slug

    # stack[i] is the innermost heading at each level seen so far.
    stack: List[Block] = []
    for block in document.blocks:
        if block.kind == "heading":
            level = block.level or 1
            while stack and (stack[-1].level or 1) >= level:
                stack.pop()
            block.ancestors = [b.slug or "" for b in stack]
            stack.append(block)
        else:
            block.ancestors = [b.slug or "" for b in stack]
    return document


def outline(document: Document) -> List[Block]:
    return [b for b in document.blocks if b.kind == "heading"]


def section_span(document: Document, slug: str) -> Optional[Tuple[int, int]]:
    """
    Byte span of the section introduced by `slug`, heading included.

    A section runs to the next heading at the same or a shallower level, or to
    end of file. The end is the *document* length rather than the previous
    block's end, so trailing blank lines inside the section are preserved —
    a consumer extracting a section should get back what was there.
    """
    headings = outline(document)
    for i, heading in enumerate(headings):
        if heading.slug != slug:
            continue
        level = heading.level or 1
        for later in headings[i + 1:]:
            if (later.level or 1) <= level:
                return heading.start, later.start
        return heading.start, document.byte_length
    return None


def filter_blocks(
    blocks: Iterable[Block],
    kinds: Optional[Sequence[str]] = None,
    under: Optional[str] = None,
    protected: Optional[bool] = None,
    forms: Optional[Sequence[str]] = None,
) -> List[Block]:
    """
    Apply the filters #15 asks for: construct, heading ancestry, attributes.

    `under` matches a block inside that heading's section, which includes the
    heading itself — asking for what is under `## Install` and not being told
    about `## Install` would be surprising.
    """
    out = []
    for block in blocks:
        if kinds and block.kind not in kinds:
            continue
        if forms and block.form not in forms:
            continue
        if protected is not None and block.protected is not protected:
            continue
        if under is not None:
            if under not in block.ancestors and block.slug != under:
                continue
        out.append(block)
    return out


def hidden_nested_blocks(document: Document) -> List[Block]:
    """
    Containers whose span holds a verbatim construct the IR cannot see.

    Schema 1 emits a flat sequence, so a fenced block inside a list item is
    part of the `list` record rather than a record of its own — and that record
    says protected=false. Any query over code blocks or protection therefore
    under-reports inside containers, so mdquery reports it rather than letting
    the answer look complete. docs/ir-schema.md, "Not in schema 1".
    """
    suspect = []
    data = document.path.read_bytes() if document.path.is_file() else b""
    for block in document.blocks:
        if block.kind not in ("list", "block_quote"):
            continue
        segment = data[block.start:block.end]
        if b"```" in segment or b"~~~" in segment:
            suspect.append(block)
    return suspect
