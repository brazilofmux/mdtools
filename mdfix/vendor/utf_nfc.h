/* Unicode NFC quick-check and normalization. Vendored from libutf. */
#ifndef MDFIX_UTF_NFC_H
#define MDFIX_UTF_NFC_H

#include <stddef.h>

/*
 * Canonical Combining Class and NFC_QC for the code point at [p, pEnd).
 *
 * *pCCC is 0..254; *pQC is 0 for Yes, 1 for No, 2 for Maybe. Returns the
 * combined value libutf packs them into, which mdfix does not use.
 *
 * mdfix walks code points itself so it can report *where* a document leaves
 * NFC (architecture I1.2, ID.1) — a whole-string yes/no cannot.
 */
int mdfix_nfc_ccc_qc(const unsigned char *pStart, const unsigned char *pEnd,
                     int *pCCC, int *pQC);

/* 1 if the string is definitely NFC, 0 if it is not or might not be. */
int mdfix_nfc_is_nfc(const unsigned char *src, size_t nSrc);

/*
 * Why the output is a prefix of the correct answer.
 *
 * Anything but OK means text was lost. `*pnDst` alone cannot tell you that —
 * NFC legitimately shrinks text, so a short result is not by itself evidence
 * of loss. This enum is what makes the difference visible; before libutf had
 * it, mdfix could not distinguish a normalized document from a truncated one
 * (brazilofmux/utf#2).
 */
typedef enum {
    MDFIX_NFC_OK = 0,
    MDFIX_NFC_TRUNCATED = 1,          /* dst too small; size with the bound */
    MDFIX_NFC_SEGMENT_TOO_LONG = 2    /* one combining sequence past the cap */
} mdfix_nfc_status;

/*
 * A destination size that can never truncate.
 *
 * NFC can *expand*: U+0958 is 3 bytes in and 6 out, so `nSrc` is not a safe
 * capacity. UAX #15 bounds NFC expansion at 3x for UTF-8.
 */
size_t mdfix_nfc_normalize_bound(size_t nSrc);

/* Normalize to NFC. Returns OK, or why the result is a prefix. */
mdfix_nfc_status mdfix_nfc_normalize(const unsigned char *src, size_t nSrc,
                                     unsigned char *dst, size_t nDstMax,
                                     size_t *pnDst);

#endif
