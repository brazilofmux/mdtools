# Vendored Unicode tables

Status: shipped, 2026-08-13.

`mdfix/vendor/` holds three extracts from
[libutf](https://github.com/brazilofmux/utf):

| | |
|---|---|
| `utf_width.c` | display column width — East Asian Width, combining marks |
| `utf_nfc.c` | NFC quick check and normalization |
| `utf_word.c` | word-character classification |

## Why copies rather than a link

`mdfix` must build from committed sources with nothing but a C compiler. That
is not an aesthetic preference — it is what the "builds from committed
`mdfix.c`" CI job exists to protect, and it is what lets a downstream tree use
mdtools without acquiring a second build system.

The cost is that a copy can drift from what it was copied from, silently, in
4,700 lines of generated table where a changed digit is invisible to reading.
Two checks answer that, and they answer different halves of it.

## The two checks

**`mdfix/vendor/MANIFEST`** fingerprints each file and names its upstream
commit. `tests/test_vendor_manifest.py` verifies it on every run, so a
hand-edit or a re-extraction from a dirty tree fails immediately. "VENDORED,
DO NOT EDIT" is a comment; this is what makes it true.

It cannot compare the copy to libutf — that needs libutf present, and these
files exist so a build does not.

**The oracle sweep** is the other half, run by hand at each refresh because it
needs the real library. Compile the vendored file *and* `libutf.a` into one
program and compare them over every code point:

```c
for (uint32_t cp = 1; cp <= 0x10FFFF; cp++) {
    if (cp >= 0xD800 && cp <= 0xDFFF) continue;
    /* ... encode cp, call both, compare every output including status ... */
}
```

For `utf_nfc.c` the sweep also runs each scalar followed by six combining
marks — 7,784,441 sequences — because normalization is about *sequences*, and
a table that agrees on single code points can still disagree on a pair.

Record the result in the commit. Both refreshes so far report zero
mismatches, and both found real bugs on the way in.

## Refreshing

1. Rebuild libutf and re-extract, keeping the two mechanical changes: tables
   go `static`, and entry points are renamed `utf_*` → `mdfix_*` so a build
   that one day links libutf cannot bind to this copy instead.
2. Run the oracle sweep. Put the numbers in the commit message.
3. Update `MANIFEST` — hash and upstream commit — in the same commit as the
   extract.
4. Run `make check`. The banner comment and the manifest are two records of
   the same fact, and a test asserts they agree.

## What the dependency has returned

Taking it was a deliberate bet, stated in #50: *"only by using it can problems
in both libutf and mdtools emerge."* The ledger so far, from this side:

- **utf#1** — a run of composition exclusions truncated in silence. 2700
  copies of U+0958 normalized to 64, and nothing said so. Found by mdfix
  vendoring the copy and testing it against `unicodedata`.
- **utf#2** — that truncation was unreportable at all. The normalizer now
  returns a status and offers a bound that makes truncation impossible.
- **utf#3** — no word-character classification existed. mdfix had been
  approximating it as "any byte ≥ 0x80", which keeps `漢字_の_強調` literal
  but reads `。@key` as an email address. `utf_word.c` is the answer, and both
  approximations are gone.

The loop is only closed if the client is also the oracle. It is not: the
oracle here is Pandoc, CPython's `unicodedata`, and ICU on the libutf side —
none of which share ancestry with any of this.
