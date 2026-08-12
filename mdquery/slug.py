"""
Pandoc's `auto_identifiers` algorithm.

dialect-policy §3 pins `+auto_identifiers -gfm_auto_identifiers`, so heading
anchors follow Pandoc's rules and not GitHub's. Every rule below was read off
`pandoc -t json` rather than the manual:

    'Simple Heading'                -> 'simple-heading'
    'Punctuation: colons, commas!'  -> 'punctuation-colons-commas'
    '2. Numbers first'              -> 'numbers-first'
    '123'                           -> 'section'
    'Héading with accents'          -> 'héading-with-accents'
    'under_score', 'dot.separated'  -> unchanged
    'Emoji 🎉 here'                  -> 'emoji-here'
    duplicates                      -> '-1', '-2', …

Slugs are computed from the IR's raw heading text, which still contains inline
markup. That is deliberate: stripping `[link](url)` or `_emphasis_` here would
mean teaching a consumer Markdown, which is precisely the leak dialect-policy
§2 forbids. Markers that are not slug characters fall out for free (`*`, `` ` ``,
`#`), so star-emphasis and code-spanned headings agree with Pandoc. Underscore
emphasis and links do not — `_` is a kept character and link syntax survives
as raw text until the IR carries inlines. See docs/mdquery.md Limits.
"""

from __future__ import annotations

# Kept as-is by Pandoc: letters, digits, underscore, hyphen, period, space.
# Underscore is why "_emphasis_" does not fall out the way "*emphasis*" does.
_KEEP_PUNCT = frozenset("_-. ")

# `+smart` is pinned by dialect-policy §3, so Pandoc folds these before the
# identifier is computed. The replacements are then dropped by the character
# filter, which is why `A--B` slugs as 'ab' and not 'a-b'. Longest first.
#
# This is the reader's text normalization, not inline structure — the same
# algorithm's input, not a second parser. It matters in practice because §7
# gap 6 has mdfix itself still emitting ASCII `...` under Chicago.
_SMART = (("---", "—"), ("--", "–"), ("...", "…"))


def slugify(text: str) -> str:
    """Pandoc's identifier for a heading with this text, before deduplication."""
    for ascii_form, unicode_form in _SMART:
        text = text.replace(ascii_form, unicode_form)
    # Everything before the first letter is dropped, which is what turns
    # '2. Numbers first' into 'numbers-first' and leaves '123' with nothing.
    start = 0
    for i, ch in enumerate(text):
        if ch.isalpha():
            start = i
            break
    else:
        return "section"

    out: list[str] = []
    for ch in text[start:]:
        if ch.isalnum() or ch in _KEEP_PUNCT:
            out.append("-" if ch == " " else ch)
        # Anything else — punctuation, symbols, emoji, dashes — is dropped
        # rather than replaced, so 'C#' becomes 'c' and not 'c-'.

    # A run of spaces became a run of hyphens; Pandoc collapses it because it
    # normalizes inline whitespace before slugging.
    slug = "-".join(part for part in "".join(out).split("-") if part != "")
    if not slug:
        return "section"
    # Lowercased but not normalized, because Pandoc does not normalize either:
    # a precomposed 'Héading' slugs as 'héading', while the decomposed spelling
    # loses its combining mark to the filter above and slugs as 'heading'.
    # Verified with `pandoc -t json` on both spellings in one file. Normalizing
    # here would be more principled and would not match, which is what counts.
    return slug.lower()


def assign_slugs(texts: list[str]) -> list[str]:
    """
    Slugs for headings in document order, with Pandoc's duplicate suffixes.

    The first occurrence keeps the bare slug; later ones get '-1', '-2', …
    Order matters, so this takes the whole document rather than one heading.
    """
    seen: dict[str, int] = {}
    slugs: list[str] = []
    for text in texts:
        base = slugify(text)
        n = seen.get(base, 0)
        seen[base] = n + 1
        slugs.append(base if n == 0 else f"{base}-{n}")
    return slugs
