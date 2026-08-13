/* Unicode word-character classification. Vendored from libutf. */
#ifndef MDFIX_UTF_WORD_H
#define MDFIX_UTF_WORD_H

/*
 * Is the code point at [p, pEnd) a word character?
 *
 * Alphabetic + Nd + Mn + Mc. `Pc` — where `_` lives — is deliberately not
 * included; see mdfix_is_word_connector. Returns 0 for a malformed sequence,
 * which is the right answer to "is this text a word character".
 */
int mdfix_is_word(const unsigned char *p, const unsigned char *pEnd);

/* Is it connector punctuation (Pc)? `_` and nine others. */
int mdfix_is_word_connector(const unsigned char *p, const unsigned char *pEnd);

#endif
