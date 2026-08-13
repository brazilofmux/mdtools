# Transform classification

Status: adopted, 2026-08-12. Answers issues #55 and #60.
Referenced by [architecture.md](architecture.md) L2 and L3.

Every mdfix transform is **required** or **optional**. The distinction is not a
matter of taste, and the test is executable:

> A transform is **required** when omitting it leaves Pandoc reading the
> document as something other than what the author wrote — that is, when its
> absence violates **I2.1** or **I2.2**.

Everything else is optional, however desirable. "This makes the file tidier"
is not a reason to run by default; "without this the document means something
else" is.

## Required (L2) — run by default

Each was verified by giving Pandoc the unfixed construct and reading the block
list it produced.

| Transform | Unfixed | Fixed | Why required |
|---|---|---|---|
| Blank line **before** a list | `Intro:` then `- one` reads as `Para` | `Para`, `BulletList` | The list is swallowed into the paragraph and stops being a list |
| Blank line **after** a list | `- one` then `After.` reads as `BulletList` | `BulletList`, `Para` | The following paragraph is swallowed into the last item |
| Space after the ATX marker | `#Title` reads as `Para` | `Header` | A heading stops being a heading |

That is the whole set. Three repairs, each of which changes the document's
meaning by its absence.

`--no-required` disables them. Output is then not guaranteed Pandoc-readable,
so it exists for inspection — seeing what a file looks like untouched — and
not for writing manuscripts.

### On the ATX fix in particular

This one is why **I2.3** was false. It ran only under `--heading-canonical`,
so a bare `mdfix` run left `#Title` as a paragraph and called the file fixed.
It now runs by default, split out from the optional half of that flag.

`--heading-canonical` keeps the *cosmetic* remainder: removing a trailing `#`
run. Pandoc reads `# Title ###` and `# Title` as the same `Header`, so that
half changes nothing a reader sees and stays opt-in.

## Optional (L3) — opt-in, and may never break L2

Verified as **not** required: Pandoc reads each construct the same way with or
without the transform, so applying one is an editorial choice.

| Transform | What it does | Why optional |
|---|---|---|
| `--chicago-punct`, `--chicago-punct-2` | Em-dash spacing, ellipsis, sentence spacing | Editorial house style |
| `--chicago-abbrev` | `e.g.`/`i.e.` commas, `et al.` | Editorial house style |
| `--serial-comma-lint`, `--chicago-number-lint` | Warn only | Never modify |
| `--footnote-canonical` | Footnote ref/def style | Spaced and unspaced refs both parse |
| `--heading-canonical` | Trailing `#` removal | Same `Header` either way |
| `--fence-canonical` | Fence delimiter style | Same `CodeBlock` either way |
| `--pandoc-safe-links` | Wrap bare URLs in `<…>` | Bare URLs are `Para` text either way; wrapping *adds* a `Link`, so it changes the AST rather than repairing it |
| `--scrivener-repair` | Rejoin emphasis split across blocks | A repair, but of an authoring accident, not of a dialect misread |
| `--spaced-emdash` | Preserve `word — word` | Typographic preference |
| `--normalize-nfc` | Compose text to Unicode NFC | Pandoc reads both spellings; but it changes byte offsets and heading anchors, so it must be asked for — architecture **I1.2** |
| `-w` | Collapse trailing whitespace | **Breaks I2.1** — see §7 gap 5 |
| `--wrap[=N]` | Hard-wrap paragraphs | Presentation; **breaks I2.1** via `-w` |
| `--canonical` | Profile: the above minus wrap | Convenience bundle |
| `--technical` | Profile: `--canonical` + spaced em-dash + wrap 78 | Convenience bundle |

`tests/test_transform_matrix.py` asserts **I3.1** across this table: every
optional transform, alone and in each profile, must still satisfy I2.1 and
I2.2. The violations it finds are pinned there and in dialect-policy §7.

Note that `--normalize-nfc` is not in `--canonical` or `--technical`, and
should not be added to them. A profile is a bundle of things safe to apply
without looking; moving a heading anchor is not one of those. It is also
the only optional transform that applies inside a code fence, because NFC
is a spelling of the same text rather than an edit to it — skipping fences
would leave a document that is still not NFC after being asked to be.

## The editorial bundle: `--editorial`

Five transforms were always-on since before the classification existed, in
violation of **I3.3**. Pandoc reads the document identically without each, so
none is a repair:

| Transform | Pandoc without it |
|---|---|
| Bullet markers normalized to `-` | `* one` is already a `BulletList` |
| Bold/italic stripped from headings | `# **Bold** Title` is already a `Header` |
| Bold colon moved inside tags | Same `Strong` either way |
| Arrow aside converted to an em dash | A **content** change, applied by default |
| Space after `>` in a block quote | `>Text` is already a `BlockQuote` |

They now require `--editorial`, which **`--canonical` and `--technical`
imply** — so the profiles downstream actually invokes are unchanged, verified
byte for byte against the previous binary across the repository.

The arrow rule is the one that most wanted this. It rewrites *prose* rather
than markup, and it was doing so by default: while this classification was
being written, the always-on pass rewrote the arrows in the table above.
`--no-arrow-aside` still overrides it, which the SLOW-32 book pipeline
depends on.

A bare `mdfix` now performs the three required repairs and nothing else. On
this repository's own markdown it changes nothing at all.
