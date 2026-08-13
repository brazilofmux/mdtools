/* Unicode NFC quick-check and normalization. Vendored from libutf. */
#ifndef MDFIX_UTF_NFC_H
#define MDFIX_UTF_NFC_H

#include <stddef.h>

/*
 * Canonical Combining Class and NFC_QC for the code point at [p, pEnd).
 *
 * *pCCC is 0..254; *pQC is 0 for Yes, non-zero for No or Maybe. Returns the
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
 * Normalize to NFC. Writes at most nDstMax bytes and reports the count in
 * *pnDst.
 *
 * Caution: on a short buffer this truncates silently — *pnDst is simply
 * smaller, with nothing to distinguish that from a genuinely shorter result.
 * Callers must size dst past the UAX #15 worst case and check.
 */
void mdfix_nfc_normalize(const unsigned char *src, size_t nSrc,
                         unsigned char *dst, size_t nDstMax, size_t *pnDst);

#endif
