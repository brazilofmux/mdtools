
#line 1 "mdfix.rl"
/*
 * mdfix.rl — Markdown auto-fixer (behavior-parity source)
 *
 * Takes the rules from linter.py and actually fixes the damn problems
 * instead of just whining about them.
 *
 * Fixes applied:
 *   1. Bullet style normalization (* and + → -) (opt-in: --editorial)
 *   2. Missing blank line before lists (required / L2)
 *   3. Missing blank line after lists (required / L2)
 *   4. Bold/italic stripped from headings (opt-in: --editorial)
 *   5. Trailing whitespace normalized (opt-in: -w)
 *
 * Usage: mdfix [-i] [-n] [-v] [-q] [-w] [--editorial] [--no-required]
 *              [--chicago-punct] [--chicago-punct-2]
 *              [--serial-comma-lint] [--chicago-abbrev] [--chicago-number-lint]
 *              [--canonical] [--canonical-lint] [--footnote-canonical]
 *              [--heading-canonical] [--fence-canonical] [--pandoc-safe-links]
 *              [--spaced-emdash] [--wrap[=N]] [--technical]
 *              input.md [output.md]
 *   -i  Edit in-place (atomic temp write; collision-safe .bak)
 *   -n  Dry run — report what would change, touch nothing
 *   -v  Verbose — show every fix
 *   -q  Quiet — shut up, just fix it
 *   -w  Normalize trailing whitespace (collapse to max 1 space)
 *   --chicago-punct
 *       Chicago-style punctuation normalization (conservative)
 *   --chicago-punct-2
 *       Additional conservative Chicago punctuation spacing/placement
 *   --serial-comma-lint
 *       Warn-only lint for likely missing Oxford commas
 *   --chicago-abbrev
 *       Chicago abbreviation normalization (conservative)
 *   --chicago-number-lint
 *       Warn-only lint for possible Chicago number-style issues
 *   --canonical
 *       Enable the full canonical Markdown profile (safe passes)
 *   --canonical-lint
 *       Gate mode: canonical profile + nonzero exit if file is not canonical
 *   --footnote-canonical
 *       Normalize footnote refs/defs to canonical Pandoc-friendly style
 *   --heading-canonical
 *       Remove trailing ATX heading hashes (spacing is required, R3)
 *   --fence-canonical
 *       Normalize code fence delimiter lines
 *   --pandoc-safe-links
 *       Wrap bare http(s) URLs in autolink brackets
 *   --scrivener-repair
 *       Repair split heading emphasis from Scrivener-style exports
 *
 * Compile: ragel -G2 mdfix.rl -o mdfix.c && cc -O2 -o mdfix mdfix.c
 */

/* getline, fdopen, fsync: POSIX.1-2008. Must precede every include, or a
 * packaging build that overrides CFLAGS with -std=c11 loses the declarations
 * on glibc — a hard error on GCC 14+/Clang 16+, an implicit int-returning
 * call on anything older. */
#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>   /* strncasecmp */
#include <ctype.h>
#include "vendor/utf_width.h"
#include "vendor/utf_nfc.h"
#include "vendor/utf_word.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>


#line 78 "mdfix.c"
static const int mdfix_scanner_start = 14;
static const int mdfix_scanner_error = -1;

static const int mdfix_scanner_en_main = 14;


#line 77 "mdfix.rl"


#define MAX_LINE  8192
#define MAX_LINES 200000

/* ═══════════════════════════════════════════════════════════════════
 * Types
 * ═══════════════════════════════════════════════════════════════════ */

enum linetype {
    LT_BLANK,
    LT_HEADING,
    LT_BULLET,
    LT_ORDERED,
    LT_FMATTER,
    LT_CODEFENCE,
    LT_INDENTCODE,  /* four-column-indented code; emitted verbatim */
    LT_RAWHTML,     /* raw HTML block; leaf — not paragraph text */
    LT_TABLEBLOCK,  /* Pandoc grid/simple table; column-aligned, verbatim */
    LT_REFDEF,      /* link/footnote definition; not from classify() */
    LT_TEXT         /* everything else: paragraphs, blockquotes, etc. */
};

/* Kind of open raw HTML block (CommonMark types 1–5). */
enum raw_html_kind {
    RAW_HTML_NONE = 0,
    RAW_HTML_COMMENT,   /* <!-- … --> */
    RAW_HTML_CDATA,     /* <![CDATA[ … ]]> */
    RAW_HTML_PI,        /* <? … ?> */
    RAW_HTML_DECL,      /* <!NAME … > */
    RAW_HTML_TYPE1      /* <script|pre|style|textarea … </…> */
};

enum fixcat {
    FIX_BULLET_STYLE,
    FIX_BLANK_BEFORE_LIST,
    FIX_BLANK_AFTER_LIST,
    FIX_HEADER_FMT,
    FIX_TRAILING_WS,
    FIX_BOLD_COLON,
    FIX_ARROW_ASIDE,
    FIX_BLOCKQUOTE_SPACE,
    FIX_CHI_EMDASH_SPACING,
    FIX_CHI_ELLIPSIS,
    FIX_CHI_SENTENCE_SPACE,
    FIX_CHI_SPACE_BEFORE_PUNCT,
    FIX_CHI_SPACE_AFTER_PUNCT,
    FIX_CHI_QUOTE_TERMINAL_PUNCT,
    FIX_CHI_ABBREV_COMMA,
    FIX_CHI_ETAL_PERIOD,
    FIX_FOOTNOTE_REF_FMT,
    FIX_FOOTNOTE_DEF_FMT,
    FIX_HEADING_SPACE,
    FIX_HEADING_CANONICAL,
    FIX_FENCE_CANONICAL,
    FIX_PANDOC_SAFE_LINKS,
    FIX_SCRIVENER_SPLIT_EMPH,
    NUM_FIXES
};

static const char *fix_labels[] = {
    "bullet style (* or + → -)",
    "blank line inserted before list",
    "blank line inserted after list",
    "bold/italic stripped from heading",
    "trailing whitespace normalized",
    "colon moved inside bold tags (**Term**: → **Term:**)",
    "arrow aside (→) converted to em-dash (—)",
    "space added after blockquote marker (>)",
    "Chicago punctuation: em-dash spacing normalized",
    "Chicago punctuation: ellipsis normalized",
    "Chicago punctuation: sentence double-space collapsed",
    "Chicago punctuation: space before punctuation removed",
    "Chicago punctuation: space after punctuation normalized",
    "Chicago punctuation: period/comma moved inside quotes",
    "Chicago abbreviations: normalize e.g./i.e. commas",
    "Chicago abbreviations: enforce et al. period",
    "footnotes: reference token normalized",
    "footnotes: definition format normalized",
    "headings: space after ATX marker",
    "headings: trailing closing hashes removed",
    "fences: canonical delimiter formatting",
    "links: bare URLs wrapped for Pandoc",
    "scrivener: split heading emphasis repaired",
};

/*
 * Stable rule identifiers — architecture ID.2.
 *
 * A consumer gates or suppresses on these, so they are API: the English in
 * fix_labels[] may be reworded, these may not. Parallel to fix_labels and
 * fixcat; a mismatch is caught by tests/test_diagnostics.py.
 */
static const char *fix_rules[] = {
    "list.bullet-style",
    "list.blank-before",
    "list.blank-after",
    "heading.emphasis",
    "whitespace.trailing",
    "emphasis.bold-colon",
    "punct.arrow-aside",
    "blockquote.space",
    "chicago.emdash-spacing",
    "chicago.ellipsis",
    "chicago.sentence-space",
    "chicago.space-before-punct",
    "chicago.space-after-punct",
    "chicago.quote-terminal-punct",
    "chicago.abbrev-comma",
    "chicago.etal-period",
    "footnote.ref-format",
    "footnote.def-format",
    "heading.atx-space",
    "heading.canonical",
    "fence.canonical",
    "link.autolink-bare",
    "heading.scrivener-split",
};


/* ═══════════════════════════════════════════════════════════════════
 * Globals
 * ═══════════════════════════════════════════════════════════════════ */

static int  fix_counts[NUM_FIXES];
static int  opt_verbose  = 0;
static int  opt_dryrun   = 0;
static int  opt_inplace  = 0;
static int  opt_quiet    = 0;
static int  opt_trail_ws = 0;
static int  opt_no_arrow_aside = 0;
static int  opt_chicago_punct = 0;
static int  opt_chicago_punct2 = 0;
static int  opt_serial_comma_lint = 0;
static int  opt_chicago_abbrev = 0;
static int  opt_chicago_number_lint = 0;
static int  opt_canonical = 0;
static int  opt_canonical_lint = 0;
static int  opt_footnote_canonical = 0;
static int  opt_heading_canonical = 0;
static int  opt_fence_canonical = 0;
static int  opt_pandoc_safe_links = 0;
static int  opt_scrivener_repair = 0;
static int  opt_spaced_emdash = 0;
static int  opt_required   = 1;       /* L2: on unless --no-required */
static int  opt_editorial  = 0;       /* L3 editorial bundle; --editorial */
static int  opt_apply_edits = 0;      /* L5 applier; reads JSONL on stdin */
static int  opt_diff = 0;             /* --diff: preview edits, write nothing */
static int  opt_wrap_width = 0;       /* 0 = disabled */
static int  opt_emit_ir   = 0;        /* structural IR to stdout; never writes */
static int  opt_normalize_nfc = 0;    /* L3: rewrite to NFC; --normalize-nfc */

static int  serial_comma_warnings = 0;
static int  number_style_warnings = 0;
static int  unterminated_fence_warnings = 0;
static int  non_nfc_warnings = 0;

static char *lines[MAX_LINES];
static int   nlines = 0;

/*
 * Where each line came from in the *original* file.
 *
 * read_all strips line terminators and normalizes CRLF, so lines[] alone
 * cannot locate anything: a CRLF file and its LF twin produce identical
 * lines[] and different byte offsets, and a missing final newline is
 * invisible. The structural IR promises spans that slice the source exactly,
 * which is the one guarantee that lets a consumer edit without re-parsing —
 * so the offsets are captured at read time, before any of that is lost.
 *
 * line_off is the offset of the line's first byte; line_bytes is its length
 * with the terminator excluded, so [off, off + bytes) is the line's text.
 */
static long long line_off[MAX_LINES];
static int       line_bytes[MAX_LINES];
static long long src_bytes = 0;   /* total size of the input, terminators included */


/*
 * Diagnostics — architecture ID.1 (located), ID.2 (identified), ID.3
 * (machine-readable).
 *
 * JSONL on stderr, so the document keeps stdout to itself: --emit-ir and
 * --apply-edits both write there, and a warning mixed into that stream would
 * corrupt it. Spans are line-level for now, which satisfies ID.1 — carrying a
 * sub-span through every fixer is a wider change than this needs.
 */
static void ir_json_string(FILE *out, const char *s);

static int opt_diagnostics = 0;
static const char *diag_path = "";

static void emit_diagnostic_span(const char *rule, const char *severity,
                                 int linenum, long long start, long long end,
                                 const char *message)
{
    if (!opt_diagnostics)
        return;
    fputs("{\"kind\":\"diagnostic\",\"path\":", stderr);
    ir_json_string(stderr, diag_path);
    fprintf(stderr, ",\"rule\":\"%s\",\"severity\":\"%s\","
                    "\"line\":%d,\"start\":%lld,\"end\":%lld,\"message\":",
            rule, severity, linenum, start, end);
    ir_json_string(stderr, message);
    fputs("}\n", stderr);
}

static void emit_diagnostic(const char *rule, const char *severity,
                            int linenum, const char *message)
{
    if (!opt_diagnostics)
        return;
    long long start = 0, end = 0;
    if (linenum >= 1 && linenum <= nlines) {
        start = line_off[linenum - 1];
        end = start + line_bytes[linenum - 1];
    }
    fputs("{\"kind\":\"diagnostic\",\"path\":", stderr);
    ir_json_string(stderr, diag_path);
    fprintf(stderr, ",\"rule\":\"%s\",\"severity\":\"%s\","
                    "\"line\":%d,\"start\":%lld,\"end\":%lld,\"message\":",
            rule, severity, linenum, start, end);
    ir_json_string(stderr, message);
    fputs("}\n", stderr);
}

/* Record a fix: the count, and a diagnostic when one was asked for. */
static void record_fix(enum fixcat cat, int linenum)
{
    fix_counts[cat]++;
    emit_diagnostic(fix_rules[cat], "fix", linenum, fix_labels[cat]);
}

static int total_issues(void)
{
    int total = serial_comma_warnings + number_style_warnings
              + unterminated_fence_warnings;
    for (int i = 0; i < NUM_FIXES; i++)
        total += fix_counts[i];
    return total;
}

static void enable_canonical_profile(void)
{
    /* Profiles keep the former always-on editorial bundle so downstream
     * output is unchanged. */
    opt_editorial = 1;
    opt_trail_ws = 1;
    opt_chicago_punct = 1;
    opt_chicago_punct2 = 1;
    opt_chicago_abbrev = 1;
    opt_footnote_canonical = 1;
    opt_heading_canonical = 1;
    opt_fence_canonical = 1;
}

static void enable_technical_profile(void)
{
    enable_canonical_profile();
    opt_spaced_emdash = 1;
    if (opt_wrap_width == 0)
        opt_wrap_width = 78;
}

/* ═══════════════════════════════════════════════════════════════════
 * Classification helpers
 * ═══════════════════════════════════════════════════════════════════ */

static int is_blank(const char *s)
{
    while (*s) {
        if (!isspace((unsigned char)*s))
            return 0;
        s++;
    }
    return 1;
}

/*
 * Find a bullet marker in line.  Returns offset of the marker char,
 * or -1 if this isn't a bullet line.
 * Matches: "- ", "* ", "+ " with optional leading whitespace.
 */
static int find_bullet(const char *line)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    if ((line[i] == '-' || line[i] == '*' || line[i] == '+')
        && line[i + 1] == ' ')
        return i;
    return -1;
}

/*
 * An ordered-list marker, in any form the pinned profile supports.
 *
 * dialect-policy §3 pins `+fancy_lists`, `+startnum` and `+example_lists`, so
 * Pandoc reads all of these as an `OrderedList`:
 *
 *     1. x    1) x       decimal
 *     a. x    A) x       alpha        (+fancy_lists)
 *     i. x    iv) x      roman        (+fancy_lists)
 *     @lab. x (@lab) x   example      (+example_lists)
 *
 * mdfix recognized only the first, so the rest were paragraphs — and a list
 * read as a paragraph is a list the prose passes rewrite (issue #90).
 *
 * `is_ordered` answers what a line *is*, which is a different question from
 * whether a blank line should be inserted before it. That separation is
 * load-bearing here: `blank_before_list_marker` below stays narrower on
 * purpose, and the measurement in #90 is why.
 */
static int ordered_marker_len(const char *line, int *digits_only)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    int start = i;
    if (digits_only)
        *digits_only = 0;

    /* Example list: `(@label)` or `@label.` */
    if (line[i] == '(' && line[i + 1] == '@') {
        i += 2;
        while (isalnum((unsigned char)line[i]) || line[i] == '_'
               || line[i] == '-')
            i++;
        if (line[i] == ')' && line[i + 1] == ' ')
            return i + 2 - start;
        return 0;
    }
    if (line[i] == '@') {
        i++;
        while (isalnum((unsigned char)line[i]) || line[i] == '_'
               || line[i] == '-')
            i++;
        if (line[i] == '.' && (line[i + 1] == ' ' || line[i + 1] == '\0'))
            return i + 2 - start;
        return 0;
    }

    if (isdigit((unsigned char)line[i])) {
        while (isdigit((unsigned char)line[i]))
            i++;
        if (digits_only)
            *digits_only = 1;
    } else {
        /*
         * Alpha and roman markers (`a.`, `iv)`) are deliberately absent.
         *
         * They are indistinguishable from hard-wrapped prose, and #90
         * measured how often that matters: of 56 lines matching `a. ` after
         * a prose line in the downstream corpora, 52 are sentence
         * continuations — "…eventually work" / "out. I want to point at
         * something else." Recognizing them turned those into lists, and the
         * blank-after-list repair then fired on the line following each one,
         * which is how this was caught: `make test` failed on this
         * repository's own documentation.
         *
         * Telling the two apart needs the context Pandoc uses — a list
         * cannot interrupt a paragraph, so a marker after prose is not a
         * marker — and that is a wider change than widening a predicate.
         * Left to #90; the forms below have zero occurrences after prose in
         * the same corpora, so they carry none of that risk.
         */
        return 0;
    }

    if ((line[i] == '.' || line[i] == ')') && line[i + 1] == ' ')
        return i + 2 - start;
    return 0;
}

static int is_ordered(const char *line)
{
    return ordered_marker_len(line, NULL) > 0;
}

/*
 * Should a blank line be inserted before this marker (required repair R2)?
 *
 * Narrower than `is_ordered`, and measured rather than chosen. R2 *creates* a
 * list — Pandoc reads no marker as interrupting a paragraph, not even `1.` —
 * which is the I2.1 exception it is allowed. For bullets that is right: in
 * 320 downstream files, all 669 occurrences are a sentence introducing real
 * items.
 *
 * For alpha and roman it is wrong. Of 56 occurrences of `a. ` / `i. ` after a
 * prose line, 52 are hard-wrapped sentences — "…eventually work" / "out. I
 * want to point at something else." Fabricating a list there would be a 93%
 * false-positive rate, and it is exactly what Pandoc's no-interruption rule
 * protects against.
 *
 * So R2 keeps to the decimal forms, which is what it already had.
 */
static int blank_before_list_marker(const char *line)
{
    int digits = 0;
    if (find_bullet(line) >= 0)
        return 1;
    return ordered_marker_len(line, &digits) > 0 && digits;
}

/* ATX heading: up to 3 leading spaces, then one or more #, then space or EOL */
static int is_heading(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    if (line[i] != '#')
        return 0;
    while (line[i] == '#')
        i++;
    return (line[i] == ' ' || line[i] == '\0');
}

struct fence_state {
    int  active;
    char marker;
    int  length;
    int  indent;
    int  open_line;   /* 1-based, for the unterminated-fence diagnostic */
};

/*
 * Parse the indentation and marker run shared by openers and closers.
 *
 * max_indent is the deepest indentation accepted, or -1 for any. Openers and
 * closers differ here on purpose:
 *
 *   Openers pass -1. A fence inside an ordered list item sits at content
 *   column 4+, which is a real fence in CommonMark/GFM. mdfix does not track
 *   list-content indentation, so it cannot tell that from an indented code
 *   block — and capping the indent drops real fences, handing shell commands
 *   to the prose pipeline to be reflowed. Being permissive is the safe side.
 *
 *   Closers pass the opening fence's indent + 3, which is the CommonMark rule
 *   relative to the container. Being permissive here would be the unsafe
 *   side: a deeper-indented delimiter *inside* the block is content, and
 *   treating it as a closer would truncate the block.
 */
/*
 * Markdown indentation, measured in columns rather than characters.
 *
 * CommonMark uses a tab stop of four, so a leading tab is four columns of
 * indentation even though it is one byte. Counting bytes let a tab-indented
 * delimiter pass a three-space limit and close a fence the dialect still
 * considers open — mdfix then applied prose fixes to the remaining code.
 *
 * out_chars receives the byte length of that whitespace, which callers still
 * need when copying the original indentation verbatim.
 */
#define MD_TAB_STOP 4

static int indent_columns(const char *line, int *out_chars)
{
    int col = 0;
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t') {
        col += (line[i] == '\t') ? (MD_TAB_STOP - (col % MD_TAB_STOP)) : 1;
        i++;
    }
    if (out_chars)
        *out_chars = i;
    return col;
}

static int fence_prefix(
    const char *line,
    int max_indent_cols,
    int *indent_chars,
    int *indent_cols,
    char *marker,
    int *run_length,
    const char **rest)
{
    int i = 0;
    int cols = indent_columns(line, &i);
    if (max_indent_cols >= 0 && cols > max_indent_cols)
        return 0;

    char c = line[i];
    if (c != '`' && c != '~')
        return 0;
    int start = i;
    while (line[i] == c)
        i++;
    if (i - start < 3)
        return 0;

    *indent_chars = start;
    *indent_cols = cols;
    *marker = c;
    *run_length = i - start;
    *rest = line + i;
    return 1;
}

/* CommonMark/Pandoc fence opener. Backtick info strings cannot contain `. */
static int parse_fence_opener(const char *line, struct fence_state *fence)
{
    const char *rest;
    int indent_chars, indent_cols, run_length;
    char marker;
    if (!fence_prefix(line, -1, &indent_chars, &indent_cols,
                      &marker, &run_length, &rest))
        return 0;
    if (marker == '`' && strchr(rest, '`') != NULL)
        return 0;

    fence->active = 1;
    fence->marker = marker;
    fence->length = run_length;
    /* Columns: the closer's allowance is measured relative to this. */
    fence->indent = indent_cols;
    return 1;
}

static int is_fence_closer(const char *line, const struct fence_state *fence)
{
    const char *rest;
    int indent_chars, indent_cols, run_length;
    char marker;
    if (!fence_prefix(line, fence->indent + 3, &indent_chars, &indent_cols,
                      &marker, &run_length, &rest))
        return 0;
    if (marker != fence->marker || run_length < fence->length)
        return 0;
    while (*rest == ' ' || *rest == '\t')
        rest++;
    return *rest == '\0';
}

static int is_code_fence(const char *line)
{
    struct fence_state fence;
    return parse_fence_opener(line, &fence);
}

/* YAML frontmatter delimiter: exactly "---" then whitespace/EOL */
/*
 * A YAML metadata delimiter is exactly three of its character and then
 * nothing but whitespace. The old test accepted `---` followed by a space
 * and anything after it, so a Pandoc dash row (`---    ----`) or any
 * thematic break at line 1 opened front matter — and since an unclosed
 * opener ran to EOF, one mis-read line swallowed the document.
 *
 * Verified with `pandoc -t json`: `----` is not a delimiter (four dashes
 * parse as a table row), `---   ` is, and `...` closes a block that `---`
 * opened.
 */
static int fmatter_delim_of(const char *line, char c)
{
    if (line[0] != c || line[1] != c || line[2] != c)
        return 0;
    int i = 3;
    if (line[i] == c)
        return 0;               /* four or more is not a delimiter */
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    return line[i] == '\0';
}

static int is_fmatter_delim(const char *line)
{
    return fmatter_delim_of(line, '-');
}

/* Pandoc closes a metadata block with `---` or `...`. */
static int is_fmatter_close(const char *line)
{
    return fmatter_delim_of(line, '-') || fmatter_delim_of(line, '.');
}

/*
 * Index of the closing delimiter, or -1 when this file has no front matter.
 *
 * Returning -1 for an unclosed opener is the point. Pandoc reads `---` with
 * no closer as a thematic break and carries on parsing; treating it as an
 * unterminated metadata block instead freezes everything after it.
 */
static int frontmatter_close_line(void)
{
    if (nlines == 0 || !is_fmatter_delim(lines[0]))
        return -1;
    for (int j = 1; j < nlines; j++)
        if (is_fmatter_close(lines[j]))
            return j;
    return -1;
}

static enum linetype classify(const char *line)
{
    if (is_blank(line))         return LT_BLANK;
    if (is_fmatter_delim(line)) return LT_FMATTER;
    if (is_code_fence(line))    return LT_CODEFENCE;
    if (is_heading(line))       return LT_HEADING;
    if (find_bullet(line) >= 0) return LT_BULLET;
    if (is_ordered(line))       return LT_ORDERED;
    return LT_TEXT;
}

/*
 * Raw HTML blocks (CommonMark types 1–5) and their kind-specific ends.
 *
 * Pandoc keeps these as a RawBlock running to their own terminator; a blank
 * line does not end them, and the contents are passed through verbatim. mdfix
 * had no notion of them at all, so it converted arrows and normalized
 * punctuation inside <script>, <pre>, <style> and comments — rewriting
 * JavaScript and CSS as though it were prose.
 *
 * <div> and other block-level tags are deliberately absent: Pandoc parses
 * those into a Div whose contents are markdown, so prose inside them is
 * ordinary prose and mdfix should keep fixing it.
 *
 * Openers require a tag-name boundary (`\b` on the Python side) so prefixes
 * like <preview> or <scripture> do not enter raw mode. Type-1 closers are a
 * full end tag `</name\s*>` (any of the four names, per CommonMark), not a
 * bare `</script` prefix that would fire on `"</script"` in JavaScript.
 */

/* After a type-1 tag name: space, tab, '>', '/', or end of string. */
static int is_html_tag_name_end(char c)
{
    return c == '\0' || c == ' ' || c == '\t' || c == '>' || c == '/';
}

/* Case-insensitive `<name` at s, with a tag-name boundary after the name. */
static int match_html_open_tag(const char *s, const char *name)
{
    size_t n = strlen(name);
    if (s[0] != '<')
        return 0;
    if (strncasecmp(s + 1, name, n) != 0)
        return 0;
    return is_html_tag_name_end(s[1 + n]);
}

/* True if s contains a type-1 end tag: </script|pre|style|textarea\s*>. */
static int has_type1_end_tag(const char *s)
{
    static const char *const names[] = {
        "script", "pre", "style", "textarea"
    };
    for (const char *p = s; *p; p++) {
        if (p[0] != '<' || p[1] != '/')
            continue;
        for (size_t i = 0; i < sizeof names / sizeof names[0]; i++) {
            size_t n = strlen(names[i]);
            if (strncasecmp(p + 2, names[i], n) != 0)
                continue;
            const char *q = p + 2 + n;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q == '>')
                return 1;
        }
    }
    return 0;
}

static enum raw_html_kind raw_html_open_kind(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    const char *s = line + i;

    if (strncmp(s, "<!--", 4) == 0)
        return RAW_HTML_COMMENT;
    if (strncmp(s, "<![CDATA[", 9) == 0)
        return RAW_HTML_CDATA;
    if (s[0] == '<' && s[1] == '?')
        return RAW_HTML_PI;
    if (s[0] == '<' && s[1] == '!' && isalpha((unsigned char)s[2]))
        return RAW_HTML_DECL;
    if (match_html_open_tag(s, "script")
        || match_html_open_tag(s, "pre")
        || match_html_open_tag(s, "style")
        || match_html_open_tag(s, "textarea"))
        return RAW_HTML_TYPE1;
    return RAW_HTML_NONE;
}

/* Does this line (or suffix) contain the end for the given open kind? */
static int raw_html_line_has_end(const char *s, enum raw_html_kind kind)
{
    switch (kind) {
    case RAW_HTML_COMMENT: return strstr(s, "-->") != NULL;
    case RAW_HTML_CDATA:   return strstr(s, "]]>") != NULL;
    case RAW_HTML_PI:      return strstr(s, "?>") != NULL;
    case RAW_HTML_DECL:    return strchr(s, '>') != NULL;
    case RAW_HTML_TYPE1:   return has_type1_end_tag(s);
    default:              return 0;
    }
}

static int is_list_type(enum linetype t)
{
    return t == LT_BULLET || t == LT_ORDERED;
}

/*
 * Detect list-item continuation lines: LT_TEXT lines indented by 2+ spaces,
 * appearing after a bullet or ordered list item.  These are part of the
 * list and should not trigger blank-line-after-list insertion.
 */
static int is_list_continuation(const char *line)
{
    return (line[0] == ' ' && line[1] == ' ');
}

/*
 * Column where a list item's content begins — past the marker and the
 * whitespace after it. `- x` gives 2, `1. x` gives 3, `    - x` gives 6.
 *
 * Indented code nested in a list starts four columns past *this*, not four
 * past the margin, so without it either list continuations get frozen as code
 * or code inside a list gets rewritten as prose. Returns -1 when the line is
 * not a list item.
 */
static int list_content_column(const char *line)
{
    int chars = 0;
    int col = indent_columns(line, &chars);
    int i = chars;

    if (line[i] == '-' || line[i] == '*' || line[i] == '+') {
        col++;
        i++;
    } else {
        /* The third and last place that used to spell out the marker forms.
         * All of them go through ordered_marker_len now, so `a.` and
         * `@lab.` items get the same content column — and therefore the same
         * nested-prose records — as `1.` ones. */
        int len = ordered_marker_len(line, NULL);
        if (len <= 0)
            return -1;
        int marker = len - 1;            /* len counts one trailing space */
        col += marker;
        i += marker;
    }

    int spaces = 0;
    while (line[i] == ' ' || line[i] == '\t') {
        col += (line[i] == '\t') ? (MD_TAB_STOP - (col % MD_TAB_STOP)) : 1;
        i++;
        spaces++;
    }
    /* A marker with no following space is not a list item. */
    return spaces ? col : -1;
}

static int is_table_line(const char *line)
{
    const char *p = line;
    while (*p == ' ' || *p == '\t')
        p++;
    return *p == '|';
}

/*
 * Pandoc grid and simple tables.
 *
 * mdfix only recognized a row when the first non-space character was '|', so
 * these forms went through the prose scanner: punctuation was rewritten and
 * --technical reflowed them. Unlike a pipe table, column *position* carries
 * the structure here — shortening a cell by converting an arrow to an em-dash
 * moves every column after it — so these lines are verbatim, not merely
 * unwrappable.
 */
static int is_grid_border(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    if (line[i] != '+')
        return 0;
    int seen = 0;
    for (i++; line[i]; i++) {
        if (line[i] == '-' || line[i] == '=' || line[i] == '+') {
            seen = 1;
            continue;
        }
        if (line[i] == ' ' || line[i] == '\t') {
            for (; line[i]; i++)
                if (line[i] != ' ' && line[i] != '\t')
                    return 0;
            break;
        }
        return 0;
    }
    /* Must end on '+' once trailing whitespace is ignored. */
    int last = (int)strlen(line) - 1;
    while (last >= 0 && (line[last] == ' ' || line[last] == '\t'))
        last--;
    return seen && last >= 0 && line[last] == '+';
}

static int is_grid_row(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    if (line[i] != '|')
        return 0;
    int last = (int)strlen(line) - 1;
    while (last >= 0 && (line[last] == ' ' || line[last] == '\t'))
        last--;
    return last > i && line[last] == '|';
}

/* Two or more dash runs separated by spaces. The spaces distinguish this from
 * a setext underline or thematic break, which are one unbroken run. */
static int is_simple_dash_row(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    int groups = 0;
    while (line[i]) {
        if (line[i] == '-') {
            int run = 0;
            while (line[i] == '-') {
                run++;
                i++;
            }
            if (run < 2)
                return 0;
            groups++;
        } else if (line[i] == ' ' || line[i] == '\t') {
            i++;
        } else {
            return 0;
        }
    }
    return groups >= 2;
}

/*
 * Extent of a grid or simple table starting at line i, or -1.
 *
 * Pandoc requires a simple table to have all three of a header line, a spaced
 * dash row, and at least one body row — verified with `pandoc -t json`:
 *
 *   Right Left / --- ---- / 12 34  -> Table
 *   Right Left / --- ----          -> Para Para        (no body row)
 *   --- ----   / 12 34             -> HorizontalRule   (no header)
 *
 * Without those conditions a spaced dash run is a thematic break, and an
 * unspaced one is a setext underline; treating either as a table would freeze
 * ordinary prose.
 */
/* An unbroken dash run: the opener and closer of a multiline table. Also what
 * a setext underline and a thematic break look like, which is why it only
 * counts as an opener under the conditions in multiline_table_end. */
static int is_full_dash_row(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    int run = 0;
    while (line[i] == '-') {
        run++;
        i++;
    }
    if (run < 2)
        return 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    return line[i] == '\0';
}

/*
 * Index just past a Pandoc multiline table starting at line i, or -1.
 *
 *     ----------      unbroken dash run (opener)
 *      A    B         one or more header lines
 *     ----- -----     spaced dash row
 *      1    2         body rows, which may include blank lines
 *
 *      3    4
 *     ----------      unbroken dash run (closer)
 *
 * The opener is what lets blank lines stay inside: without it the content
 * ends at the first blank and the trailing run becomes a setext underline.
 * A closer is required too — pandoc otherwise ends the table at the first
 * blank. Those conditions keep a lone dash run a thematic break.
 */
static int multiline_table_end(int i)
{
    if (!is_full_dash_row(lines[i]))
        return -1;

    int j = i + 1;
    int saw_header = 0;
    int found_columns = 0;
    for (; j < nlines; j++) {
        if (is_blank(lines[j]))
            return -1;                  /* blank before the column row */
        if (is_simple_dash_row(lines[j])) {
            found_columns = 1;
            break;
        }
        if (is_full_dash_row(lines[j]))
            return -1;                  /* two runs, no column row */
        saw_header = 1;
    }
    if (!found_columns || !saw_header)
        return -1;

    for (j++; j < nlines; j++) {
        if (is_full_dash_row(lines[j]))
            return j + 1;               /* closer belongs to the table */
    }
    return -1;                          /* unterminated: not a table */
}

static int table_block_end(int i)
{
    int multiline = multiline_table_end(i);
    if (multiline > i)
        return multiline;
    if (is_grid_border(lines[i])) {
        int j = i;
        while (j < nlines && (is_grid_border(lines[j]) || is_grid_row(lines[j])))
            j++;
        return j;
    }
    if (i + 2 < nlines
        && !is_blank(lines[i])
        && !is_simple_dash_row(lines[i])
        && is_simple_dash_row(lines[i + 1])
        && !is_blank(lines[i + 2]))
    {
        int j = i + 2;
        while (j < nlines && !is_blank(lines[j]))
            j++;
        return j;
    }
    return -1;
}

static int is_blockquote_line(const char *line)
{
    const char *p = line;
    while (*p == ' ' || *p == '\t')
        p++;
    return *p == '>';
}

/*
 * Setext headings, and link/footnote definitions.
 *
 * All three are structure that the block IR reported as `paragraph`, which is
 * only safe for a reader. prosevary edits prose, and handing it a section
 * heading or a link definition to paraphrase corrupts the manuscript — so
 * these are the constructs the IR had to grow before consumers could stop
 * carrying their own classifier. Each rule is pinned with `pandoc -t json`.
 *
 * The underline must start at column 0. CommonMark allows up to three spaces;
 * pandoc's `markdown` reader does not, and it is the output dialect:
 *
 *     Title / ===        -> Header      text may be indented 0-3
 *     Title /  ===       -> Para        one space is already too far
 *     ----- / -----      -> Header      the text line may itself look like a rule
 *     Para. / <blank> / -----  -> HorizontalRule
 */
static int is_setext_underline(const char *line)
{
    char c = line[0];
    if (c != '=' && c != '-')
        return 0;
    int i = 0;
    while (line[i] == c)
        i++;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    return line[i] == '\0';
}

/*
 * `[label]:` — a link reference definition, or `[^label]:` a footnote one.
 * Returns 1 for a reference definition, 2 for a footnote definition, else 0.
 *
 * No whitespace is required after the colon: `[id]:x` is a definition to
 * pandoc, which produces no block at all for it. The label must be non-empty
 * (`[]:` / `[^]:` are not definitions).
 */
static int ref_def_kind(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    if (line[i] != '[')
        return 0;
    int footnote = (line[i + 1] == '^');
    int label_start = i + 1 + (footnote ? 1 : 0);
    i = label_start;
    for (; line[i] && line[i] != ']'; i++) {
        if (line[i] == '\\' && line[i + 1])
            i++;
    }
    if (line[i] != ']' || line[i + 1] != ':')
        return 0;
    if (i <= label_start)
        return 0;
    return footnote ? 2 : 1;
}

/* A line that can carry setext text: not blank, and not itself a block
 * opener or a link/footnote definition. Pandoc takes only a single line. */
static int setext_text_ok(const char *line)
{
    if (is_blank(line))
        return 0;
    if (is_heading(line))
        return 0;
    if (is_blockquote_line(line))
        return 0;
    if (find_bullet(line) >= 0 || is_ordered(line))
        return 0;
    /* Else `[id]: url\n====` invents a Header pandoc does not emit. */
    if (ref_def_kind(line))
        return 0;
    return 1;
}

/* A reference definition's optional title, carried onto the next line. Only
 * a quote or paren continues it — an indented plain line is a code block,
 * verified with `[id]: http://x` followed by four spaces of text. */
static int is_ref_title_cont(const char *line)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    if (i == 0)
        return 0;
    return line[i] == '"' || line[i] == '\'' || line[i] == '(';
}

static int is_thematic_break(const char *line)
{
    const char *p = line;
    while (*p == ' ' || *p == '\t')
        p++;
    char c = *p;
    if (c != '-' && c != '*' && c != '_')
        return 0;
    int count = 0;
    while (*p) {
        if (*p == c)
            count++;
        else if (*p != ' ' && *p != '\t')
            return 0;
        p++;
    }
    return count >= 3;
}

static int is_pipe_delim_row(const char *line);
static int is_headerless_table_header(const char *line);

/*
 * Lines belonging to a pipe table, marked once per file.
 *
 * is_wrappable sees one line at a time; a headerless table needs the next-line
 * delim to be recognized, so --wrap must use the same multi-line rule as
 * emit_ir or it will join header to delimiter.
 */
static unsigned char pipe_table_line[MAX_LINES];

static void mark_pipe_tables(void)
{
    memset(pipe_table_line, 0, (size_t)nlines);
    for (int i = 0; i + 1 < nlines; i++) {
        int start = 0;
        if (is_pipe_delim_row(lines[i + 1])) {
            if (is_table_line(lines[i]) || is_headerless_table_header(lines[i]))
                start = 1;
        }
        if (!start)
            continue;
        int j = i;
        while (j < nlines && strchr(lines[j], '|'))
            pipe_table_line[j++] = 1;
        i = j - 1;
    }
}

static int is_wrappable_at(const char *line, enum linetype type, int index)
{
    if (index >= 0 && index < nlines && pipe_table_line[index])
        return 0;
    if (type != LT_TEXT)
        return 0;
    if (is_table_line(line))
        return 0;
    if (is_blockquote_line(line))
        return 0;
    if (is_thematic_break(line))
        return 0;
    return 1;
}


/* ═══════════════════════════════════════════════════════════════════
 * Structural IR — see docs/ir-schema.md
 *
 * `--emit-ir` writes one JSON object per line (JSONL): a header record, then
 * one record per block, in source order. Every record carries byte offsets
 * that slice the original file exactly, so a consumer can locate and edit a
 * region without re-deriving the grammar. That is the whole point of the
 * boundary in docs/dialect-policy.md §2.
 *
 * This walk deliberately mirrors the branch order in process(). The IR
 * describes what mdfix *actually does*, not an idealized parse: each record
 * carries "protected", which is true when mdfix reproduces the block byte for
 * byte and false when prose passes may rewrite inside it. A consumer that
 * needs to know whether a fixer will touch a region can read it off the IR
 * rather than rediscover §7's compatibility table by experiment.
 * ═══════════════════════════════════════════════════════════════════ */

#define IR_SCHEMA "mdtools-ir-3"

static void emit_inline(FILE *out, const char *text, long long base,
                        int line, int depth, long long parent,
                        int at_block_start);

/*
 * A pipe-table delimiter row: `|---|---|`, `--|--`, `|:--|--:|`.
 *
 * This is the whole difference between a table and a line block, both of
 * which begin with '|'. Verified with `pandoc -t json`:
 *
 *     | a | b | / |---|---| / | 1 | 2 |   -> Table
 *     | a | b | / | 1 | 2 |               -> LineBlock
 *
 * Requires a '|' as well as a '-', so a thematic break or a multiline-table
 * dash run is never mistaken for one.
 */
static int is_pipe_delim_row(const char *line)
{
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    int dash = 0, bar = 0;
    for (; line[i]; i++) {
        switch (line[i]) {
        case '-': dash = 1; break;
        case '|': bar = 1;  break;
        case ':': case ' ': case '\t': break;
        default: return 0;
        }
    }
    return dash && bar;
}

/*
 * Header of a pipe table without a leading '|'. Must be prose: not a block
 * opener Pandoc would take instead (heading, list, quote, ref-def, fence).
 * Shared by emit_ir and mark_pipe_tables so wrap and IR agree.
 */
static int is_headerless_table_header(const char *line)
{
    if (strchr(line, '|') == NULL)
        return 0;
    if (is_table_line(line))
        return 0;
    if (is_blank(line))
        return 0;
    if (is_heading(line))
        return 0;
    if (find_bullet(line) >= 0 || is_ordered(line))
        return 0;
    if (is_blockquote_line(line))
        return 0;
    if (ref_def_kind(line))
        return 0;
    if (is_code_fence(line))
        return 0;
    return 1;
}

static void ir_json_string(FILE *out, const char *s)
{
    fputc('"', out);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
        case '"':  fputs("\\\"", out); break;
        case '\\': fputs("\\\\", out); break;
        case '\b': fputs("\\b", out);  break;
        case '\f': fputs("\\f", out);  break;
        case '\n': fputs("\\n", out);  break;
        case '\r': fputs("\\r", out);  break;
        case '\t': fputs("\\t", out);  break;
        default:
            /* UTF-8 passes through as raw bytes, which is valid JSON. Only
             * C0 controls need escaping, and they cannot appear mid-sequence. */
            if (*p < 0x20)
                fprintf(out, "\\u%04x", *p);
            else
                fputc(*p, out);
        }
    }
    fputc('"', out);
}

/*
 * Common fields. `end` excludes the line terminator, so source[start:end] is
 * the block's text and nothing else — a consumer splicing a replacement never
 * has to guess whether it owns the newline.
 */
/*
 * Totality — architecture.md I5.3, issue #56.
 *
 * Schema 1 covered the blocks and nothing else: line terminators, the blank
 * runs between blocks, a leading BOM, and any trailing bytes belonged to no
 * record at all. A serializer built on that would have silently normalized
 * every one of them — one blank line where the author left three, a lost hard
 * break, a rewritten line ending.
 *
 * Schema 2 attributes every byte. `gap` records carry the runs between blocks,
 * so concatenating all record spans in source order reproduces the input byte
 * for byte, which is a property a test can check without reference to the
 * parser that produced it.
 *
 * A gap is *not* protected: mdfix's list-spacing fixes insert and remove blank
 * lines, so claiming it reproduces them byte for byte would be false.
 */
static long long ir_cursor;      /* first byte not yet attributed to a record */
static int       ir_prev_last;   /* 0-based last line of the previous record */

static void ir_gap(FILE *out, long long from, long long to, int line, int end_line)
{
    if (to <= from)
        return;
    if (line < 1)
        line = 1;
    if (end_line < line)
        end_line = line;
    fprintf(out,
        "{\"kind\":\"gap\",\"start\":%lld,\"end\":%lld,"
        "\"line\":%d,\"endLine\":%d,\"protected\":false}\n",
        from, to, line, end_line);
}

static void ir_open(FILE *out, const char *kind, int i0, int i1, int protectd)
{
    /*
     * Everything between the previous record and this one. First gap byte is
     * the terminator of the previous record's last line (1-based line
     * ir_prev_last+1); last gap byte sits on the line before this record
     * (1-based endLine = i0, since next content is 0-based i0).
     */
    int gap_start_line = ir_prev_last + 1;  /* -1 → 0, clamped in ir_gap */
    int gap_end_line = i0 > 0 ? i0 : 1;
    ir_gap(out, ir_cursor, line_off[i0], gap_start_line, gap_end_line);
    fprintf(out,
        "{\"kind\":\"%s\",\"start\":%lld,\"end\":%lld,"
        "\"line\":%d,\"endLine\":%d,\"protected\":%s",
        kind,
        line_off[i0],
        line_off[i1] + line_bytes[i1],
        i0 + 1, i1 + 1,
        protectd ? "true" : "false");
    ir_cursor = line_off[i1] + line_bytes[i1];
    ir_prev_last = i1;
}

static void ir_block(FILE *out, const char *kind, int i0, int i1, int protectd)
{
    ir_open(out, kind, i0, i1, protectd);
    fputs("}\n", out);
}

/* Heading level and the text after the marker, trailing '#' run removed. */
/*
 * Inline markup stripped to the text Pandoc's identifier pass sees.
 *
 * `heading.text` is raw source, so a consumer computing an anchor from it
 * diverges wherever markup carries text that is not literal. Rather than let
 * every consumer grow an inline parser — the leak dialect-policy §2 exists to
 * prevent — mdfix does the stripping and the consumer does the character
 * filtering and Unicode lowercasing, which C is the wrong language for.
 *
 * Exactly three constructs need handling; everything else already agrees once
 * a consumer drops non-identifier characters. Pinned with `pandoc -t json`:
 *
 *     [inline](http://u)      -> 'inline'      destination must go
 *     ![img](i.png)           -> 'img'
 *     _under_                 -> 'under'       delimiters must go
 *     <span>html</span>       -> 'html'        tags must go
 *     [text][id]              -> 'textid'      LEFT RAW, see below
 *     `code`, <http://a>      -> already agree (backticks/brackets filtered)
 *     note[^1], 2*3*4, a_b_c  -> already agree
 *
 * Reference links stay raw on purpose. Pandoc computes header identifiers
 * before it resolves references, so `## [text][id]` is 'textid' whether or not
 * the definition exists — verified both ways. Reducing it to 'text' would be
 * more principled and would not match.
 */

/* A raw inline HTML tag, not an autolink: `<http://x>` has ':' after the
 * name, where a tag has whitespace, '/' or '>'. Returns bytes consumed. */
static int inline_html_tag_len(const char *s)
{
    int i = 0;
    if (s[i] != '<')
        return 0;
    i++;
    if (s[i] == '/')
        i++;
    if (!isalpha((unsigned char)s[i]))
        return 0;
    while (isalnum((unsigned char)s[i]) || s[i] == '-')
        i++;
    if (s[i] != ' ' && s[i] != '\t' && s[i] != '/' && s[i] != '>')
        return 0;
    while (s[i] && s[i] != '>' && s[i] != '<')
        i++;
    return (s[i] == '>') ? i + 1 : 0;
}

/*
 * Span of an inline link or image starting at `s`, or 0.
 *
 * Fills text_off/text_len with the bracketed text. Only the inline form
 * counts: '(' must follow ']' with no intervening characters. Optional
 * whitespace before '(' is accepted by CommonMark, but pandoc's default
 * `markdown` reader treats `] (` as ordinary text (identifier `link-httpx`
 * for `## [link] (http://x)`), so we require the tight form for parity.
 */
static int inline_link_len(const char *s, int *text_off, int *text_len)
{
    int i = 0;
    if (s[i] == '!')
        i++;
    if (s[i] != '[')
        return 0;
    int open = ++i;
    int depth = 1;
    for (; s[i]; i++) {
        if (s[i] == '\\' && s[i + 1]) {
            i++;
            continue;
        }
        if (s[i] == '[')
            depth++;
        else if (s[i] == ']' && --depth == 0)
            break;
    }
    if (s[i] != ']')
        return 0;
    int close = i;
    if (s[i + 1] != '(')
        return 0;               /* reference, shortcut, or spaced form: leave raw */
    i += 2;
    depth = 1;
    for (; s[i]; i++) {
        if (s[i] == '\\' && s[i + 1]) {
            i++;
            continue;
        }
        if (s[i] == '(')
            depth++;
        else if (s[i] == ')' && --depth == 0)
            break;
    }
    if (s[i] != ')')
        return 0;
    *text_off = open;
    *text_len = close - open;
    return i + 1;
}

/*
 * Bare destination from the raw body between '(' and ')': drop optional
 * surrounding <…> and a trailing title (space + "…" / '…' / (…)).
 */
static void dest_bare(const char *s, int len, int *off, int *out_len)
{
    int i = 0, j = len;
    while (i < j && (s[i] == ' ' || s[i] == '\t'))
        i++;
    while (j > i && (s[j - 1] == ' ' || s[j - 1] == '\t'))
        j--;
    if (i < j && s[i] == '<') {
        int k = i + 1;
        while (k < j && s[k] != '>')
            k++;
        if (k < j) {
            *off = i + 1;
            *out_len = k - (i + 1);
            return;
        }
    }
    int k = i;
    while (k < j) {
        if (s[k] == '\\' && k + 1 < j) {
            k += 2;
            continue;
        }
        if (s[k] == ' ' || s[k] == '\t')
            break;
        k++;
    }
    *off = i;
    *out_len = k - i;
}

/*
 * Start of the code point ending at `at`, or -1 if there is none.
 *
 * Continuation bytes are 10xxxxxx, so walking back past them lands on the
 * lead byte. Bounded by `from` so a malformed sequence cannot walk out of
 * the buffer.
 */
static int utf8_prev_start(const char *s, int from, int at)
{
    if (at <= from)
        return -1;
    int k = at - 1;
    while (k > from && ((unsigned char)s[k] & 0xC0) == 0x80)
        k--;
    return k;
}

/*
 * Is the code point starting at `at` a word character?
 *
 * `mdfix_is_word` is Unicode's answer — Alphabetic + Nd + Mn + Mc, from the
 * table libutf grew for exactly these two call sites (brazilofmux/utf#3).
 * Both used to approximate it as "any byte >= 0x80", which kept
 * `漢字_の_強調` literal but also called `。@key` an email address.
 *
 * Pc is *not* in the set, and that is what this call site needs: the
 * intraword-underscore rule is deciding about the underscore, and Pandoc
 * agrees — `_@key` is a citation, so `_` does not make the `@` an address.
 */
static int is_word_at(const char *s, int at, int end)
{
    if (at < 0 || at >= end)
        return 0;
    return mdfix_is_word((const unsigned char *)s + at,
                         (const unsigned char *)s + end);
}

/* Emphasis flanking, simplified from CommonMark. `_` additionally refuses to
 * open after, or close before, a word character — that is
 * +intraword_underscores, which keeps `a_b_c` and `漢字_の_強調` literal. */
static int emphasis_can_open(char marker, const char *s, int from, int at,
                             int after_at, int end)
{
    unsigned char after = (after_at < end) ? (unsigned char)s[after_at] : '\0';
    if (after == '\0' || after == ' ' || after == '\t')
        return 0;
    if (marker == '_' && is_word_at(s, utf8_prev_start(s, from, at), end))
        return 0;
    return 1;
}

static int emphasis_can_close(char marker, const char *s, int from, int at,
                              int after_at, int end)
{
    int prev = utf8_prev_start(s, from, at);
    unsigned char before = (prev >= 0) ? (unsigned char)s[prev] : '\0';
    if (before == '\0' || before == ' ' || before == '\t')
        return 0;
    if (marker == '_' && is_word_at(s, after_at, end))
        return 0;
    return 1;
}

#define IR_EMPH_STACK 32

/*
 * Link text is scanned in place (range, not a fresh buffer). Depth is capped
 * so nested `[…](…)` cannot blow the stack; past the cap, remaining text is
 * copied verbatim (under-report rather than crash).
 */
#define IR_INLINE_MAX_DEPTH 24

static size_t inline_plain_range(const char *src, int from, int to,
                                 char *out, size_t n, size_t outsz, int depth)
{
    struct { int pos; int len; char marker; } stack[IR_EMPH_STACK];
    int open_count = 0;
    int i = from;

    if (depth >= IR_INLINE_MAX_DEPTH) {
        while (i < to && n + 1 < outsz)
            out[n++] = src[i++];
        return n;
    }

    while (i < to && n + 1 < outsz) {
        /* Escapes first: a backslashed marker is never markup. */
        if (src[i] == '\\' && i + 1 < to) {
            if (n + 2 >= outsz)
                break;
            out[n++] = src[i++];
            out[n++] = src[i++];
            continue;
        }

        /* Code spans are opaque — a '*' inside is not a delimiter. */
        if (src[i] == '`') {
            int run = 0;
            while (i + run < to && src[i + run] == '`')
                run++;
            int j = i + run;
            int found = 0;
            while (j < to) {
                int close = 0;
                while (j + close < to && src[j + close] == '`')
                    close++;
                if (close == run && close > 0) {
                    found = 1;
                    break;
                }
                j += close ? close : 1;
            }
            int end = found ? j + run : to;
            while (i < end && n + 1 < outsz)
                out[n++] = src[i++];
            continue;
        }

        if (src[i] == '<') {
            int tag = inline_html_tag_len(src + i);
            if (tag && i + tag <= to) {
                i += tag;       /* RawInline contributes no text */
                continue;
            }
        }

        if (src[i] == '[' || (src[i] == '!' && i + 1 < to && src[i + 1] == '[')) {
            int text_off = 0, text_len = 0;
            int span = inline_link_len(src + i, &text_off, &text_len);
            if (span && i + span <= to) {
                n = inline_plain_range(src, i + text_off, i + text_off + text_len,
                                       out, n, outsz, depth + 1);
                i += span;
                continue;
            }
        }

        if (src[i] == '*' || src[i] == '_') {
            char marker = src[i];
            int run = 0;
            while (i + run < to && src[i + run] == marker)
                run++;
            int can_open = emphasis_can_open(marker, src, from, i,
                                             i + run, to);
            int can_close = emphasis_can_close(marker, src, from, i,
                                               i + run, to);

            int matched = -1;
            if (can_close) {
                for (int k = open_count - 1; k >= 0; k--) {
                    if (stack[k].marker == marker) {
                        matched = k;
                        break;
                    }
                }
            }
            if (matched >= 0) {
                /*
                 * Consume min(opener, closer) from each side. Residual closer
                 * bytes are left for the next iteration; residual opener
                 * stays on the stack. Full-run consumption made `_a__` drop
                 * every underscore (plain "a") where a trailing `_` is
                 * slug-significant.
                 */
                int pos = stack[matched].pos;
                int opener_len = stack[matched].len;
                int use = opener_len < run ? opener_len : run;
                memmove(out + pos, out + pos + use, n - (size_t)(pos + use));
                n -= (size_t)use;
                for (int k = matched + 1; k < open_count; k++) {
                    if (stack[k].pos > pos)
                        stack[k].pos -= use;
                }
                /* Drop unmatched openers after this one (already literal). */
                if (opener_len > use) {
                    stack[matched].len = opener_len - use;
                    open_count = matched + 1;
                } else {
                    open_count = matched;
                }
                i += use;
                continue;
            }
            /* Unmatched openers stay in the output until something closes
             * them, so `_unclosed` keeps its underscore the way Pandoc does. */
            if (can_open && open_count < IR_EMPH_STACK) {
                stack[open_count].pos = (int)n;
                stack[open_count].len = run;
                stack[open_count].marker = marker;
                open_count++;
            }
            for (int k = 0; k < run && n + 1 < outsz; k++)
                out[n++] = marker;
            i += run;
            continue;
        }

        out[n++] = src[i++];
    }
    return n;
}

static void inline_plain(const char *src, char *out, size_t outsz)
{
    if (outsz == 0)
        return;
    size_t n = inline_plain_range(src, 0, (int)strlen(src), out, 0, outsz, 0);
    out[n] = '\0';
}

static void ir_emit_heading(FILE *out, int i)
{
    const char *line = lines[i];
    int p = 0;
    while (p < 3 && line[p] == ' ')
        p++;
    int level = 0;
    while (line[p] == '#') {
        level++;
        p++;
    }
    while (line[p] == ' ' || line[p] == '\t')
        p++;

    int end = (int)strlen(line);
    while (end > p && (line[end - 1] == ' ' || line[end - 1] == '\t'))
        end--;
    int hashes = end;
    while (hashes > p && line[hashes - 1] == '#')
        hashes--;
    /* A closing run only counts when whitespace separates it from the text,
     * so `# C#` keeps its '#' while `# Title ###` does not. */
    if (hashes < end
        && (hashes == p || line[hashes - 1] == ' ' || line[hashes - 1] == '\t')) {
        end = hashes;
        while (end > p && (line[end - 1] == ' ' || line[end - 1] == '\t'))
            end--;
    }

    char text[MAX_LINE];
    int n = end - p;
    if (n < 0)
        n = 0;
    memcpy(text, line + p, (size_t)n);
    text[n] = '\0';

    char plain[MAX_LINE];
    inline_plain(text, plain, sizeof plain);

    ir_open(out, "heading", i, i, 0);
    fprintf(out, ",\"level\":%d,\"style\":\"atx\",\"text\":", level);
    ir_json_string(out, text);
    fputs(",\"plain\":", out);
    ir_json_string(out, plain);
    fputs("}\n", out);

    /* A link in a heading is where anchors and cross-references live. */
    emit_inline(out, text, line_off[i] + p, i + 1, 1, line_off[i], 0);
}

static const char *ir_raw_html_name(enum raw_html_kind kind)
{
    switch (kind) {
    case RAW_HTML_COMMENT: return "comment";
    case RAW_HTML_CDATA:   return "cdata";
    case RAW_HTML_PI:      return "processing-instruction";
    case RAW_HTML_DECL:    return "declaration";
    case RAW_HTML_TYPE1:   return "element";
    default:               return "unknown";
    }
}


/*
 * Nested prose inside list items (schema 3, issue #65).
 *
 * Children only for items that are plainly prose. Fence, table, line block,
 * raw HTML, indented code, heading, or block quote inside an item keeps the
 * whole item opaque — under-report, not mis-report. Nested list markers end
 * the outer item and start a sibling item (they are not an opacity case).
 * Full recursive nesting is the rest of #65.
 */

/* Byte offset where an item's content starts, or -1 if this is not a marker. */
static int list_marker_bytes(const char *line)
{
    int chars = 0;
    indent_columns(line, &chars);
    int i = chars;
    if (line[i] == '-' || line[i] == '*' || line[i] == '+') {
        i++;
        if (line[i] != ' ' && line[i] != '\t')
            return -1;
    } else {
        /* One definition of an ordered marker, shared with classify(). Before
         * this, nested item prose was emitted for `1.` and not for `a.` or
         * `@lab.` — the same list, two answers, because the marker rule was
         * written twice. */
        int len = ordered_marker_len(line, NULL);
        if (len <= 0)
            return -1;
        i = chars + len - 1;      /* len counts the single trailing space */
    }
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    return i;
}

/* A line that is ordinary prose once the item's indentation is discounted.
 * On a marker line, openers are checked on the content after the marker so
 * `- > quote` and `- # Head` stay opaque rather than mis-reporting prose. */
static int item_line_is_plain(int i, int content_col)
{
    const char *line = lines[i];
    int marker = list_marker_bytes(line);
    const char *body = (marker >= 0) ? line + marker : line;
    struct fence_state probe;

    if (parse_fence_opener(line, &probe) || parse_fence_opener(body, &probe))
        return 0;
    if (table_block_end(i) > i)
        return 0;
    if (raw_html_open_kind(line) != RAW_HTML_NONE
        || raw_html_open_kind(body) != RAW_HTML_NONE)
        return 0;
    if (is_thematic_break(line) || is_thematic_break(body))
        return 0;
    /* Pipe table / line block — same discrimination as top-level IR. */
    if (is_table_line(line) || is_table_line(body))
        return 0;
    if (is_pipe_delim_row(line) || is_pipe_delim_row(body))
        return 0;
    if (i + 1 < nlines && is_pipe_delim_row(lines[i + 1])
        && (is_headerless_table_header(line)
            || is_headerless_table_header(body)))
        return 0;
    if (is_blockquote_line(line) || is_blockquote_line(body))
        return 0;
    if (ref_def_kind(line) || ref_def_kind(body))
        return 0;
    if (indent_columns(line, NULL) >= content_col + 4)
        return 0;               /* indented code relative to the item */
    if (is_heading(line) || is_heading(body))
        return 0;
    return 1;
}


/* Inline IR at depth>0: links, images, code spans, footnote refs, raw HTML.
 * Reference/shortcut forms keep labels, not resolved destinations. */

/* A code span: matched backtick runs. Returns total length, or 0. */
static int inline_code_len(const char *s, int *body_off, int *body_len)
{
    if (*s != '`')
        return 0;
    int run = 0;
    while (s[run] == '`')
        run++;
    int j = run;
    while (s[j]) {
        int close = 0;
        while (s[j + close] == '`')
            close++;
        if (close == run) {
            *body_off = run;
            *body_len = j - run;
            return j + run;
        }
        j += close ? close : 1;
    }
    return 0;
}

/* `<http://x>` or `<a@b.com>`: a '<' whose contents hold no space and which
 * is not a tag. Returns length, or 0. */
static int inline_autolink_len(const char *s)
{
    if (*s != '<')
        return 0;
    int i = 1;
    int has_colon_or_at = 0;
    for (; s[i] && s[i] != '>'; i++) {
        if (s[i] == ' ' || s[i] == '\t' || s[i] == '<')
            return 0;
        if (s[i] == ':' || s[i] == '@')
            has_colon_or_at = 1;
    }
    return (s[i] == '>' && has_colon_or_at && i > 1) ? i + 1 : 0;
}

/* `[^label]` — a footnote reference, not a link. */
static int inline_footnote_ref_len(const char *s, int *label_off, int *label_len)
{
    if (s[0] != '[' || s[1] != '^')
        return 0;
    int i = 2;
    for (; s[i] && s[i] != ']'; i++)
        if (s[i] == '[')
            return 0;
    if (s[i] != ']')
        return 0;
    *label_off = 2;
    *label_len = i - 2;
    return i + 1;
}

/* Key starts with letter/digit/_; internal punct only when a key
 * character follows, so `@a.` at sentence end is the key `a`. */
static int citation_key_len(const char *s)
{
    unsigned char first = (unsigned char)s[0];
    if (!isalnum(first) && first != '_')
        return 0;

    int i = 0;
    while (s[i]) {
        unsigned char c = (unsigned char)s[i];
        if (isalnum(c) || c == '_') {
            i++;
            continue;
        }
        /* Internal punctuation only with a key character after it. */
        if (strchr(":.#$%&+?<>~/-", (char)c)) {
            unsigned char next = (unsigned char)s[i + 1];
            if (isalnum(next) || next == '_') {
                i += 2;
                continue;
            }
        }
        break;
    }
    return i;
}

/*
 * A word character or `.` before `@` makes an email, not a citation.
 *
 * Unicode's answer now, not a byte test: `café@x`, `текст@key` and `用户@host`
 * are addresses, while `。@key`, `—@key` and `”@key` are citations. The old
 * `>= 0x80` approximation got the first group right and the second wrong,
 * because it could not tell a letter from a full-width stop.
 *
 * `_` is not a word character here, and Pandoc agrees — `_@key` and `a_@key`
 * are both citations. That is why libutf keeps Pc out of `utf_is_word` and
 * exposes it separately.
 */
static int citation_follows_word(const char *text, int at)
{
    if (at <= 0)
        return 0;
    if (text[at - 1] == '.')
        return 1;
    int prev = utf8_prev_start(text, 0, at);
    if (prev < 0)
        return 0;
    return mdfix_is_word((const unsigned char *)text + prev,
                         (const unsigned char *)text + at);
}

/* Does the bracket opening at `at` hold at least one citation key?
 * `](` keeps `[@a](url)` a link. */
static int bracket_has_citation(const char *text, int at)
{
    int close = at + 1;
    while (text[close] && text[close] != ']' && text[close] != '[')
        close++;
    if (text[close] != ']')
        return 0;
    if (text[close + 1] == '(' || text[close + 1] == '[')
        return 0;
    for (int k = at + 1; k < close; k++) {
        if (text[k] != '@')
            continue;
        if (citation_follows_word(text, k))
            continue;
        if (citation_key_len(text + k + 1) > 0)
            return 1;
    }
    return 0;
}

/* Mode only: inside `[...]` unless `](` makes it a link. */
static int in_citation_bracket(const char *text, int at)
{
    int open = -1;
    for (int k = at - 1; k >= 0; k--) {
        if (text[k] == ']')
            return 0;                  /* a closed bracket, not ours */
        if (text[k] == '[') {
            open = k;
            break;
        }
    }
    if (open < 0)
        return 0;
    for (int k = at + 1; text[k]; k++) {
        if (text[k] == '[')
            return 0;
        if (text[k] == ']')
            return text[k + 1] != '(';  /* `](` makes it a link's text */
    }
    return 0;
}

/* First content byte after indent and one `>` prefix. */
static int citation_content_start(const char *text)
{
    int k = 0;
    while (text[k] == ' ' || text[k] == '\t')
        k++;
    if (text[k] == '>') {
        k++;
        if (text[k] == ' ')
            k++;
        while (text[k] == ' ' || text[k] == '\t')
            k++;
    }
    return k;
}

/* `@label.` or `(@label)` / `(@)` at the start of a block is an
 * example list (+example_lists), not a citation. */
static int is_example_list_marker(const char *text, int at, int at_block_start)
{
    if (!at_block_start)
        return 0;
    int start = citation_content_start(text);
    if (text[start] == '(' && at == start + 1 && text[at] == '@') {
        int n = citation_key_len(text + at + 1);
        return text[at + 1 + n] == ')';
    }
    if (at != start)
        return 0;
    int n = citation_key_len(text + at + 1);
    const char *after = text + at + 1 + n;
    return after[0] == '.' && (after[1] == ' ' || after[1] == '\0');
}

/* `[text][label]` or `[text]`, neither followed by '('. Returns length. */
static int inline_ref_link_len(const char *s, int *text_off, int *text_len,
                               int *label_off, int *label_len, int *shortcut)
{
    if (*s != '[')
        return 0;
    int i = 1, depth = 1;
    for (; s[i]; i++) {
        if (s[i] == '\\' && s[i + 1]) { i++; continue; }
        if (s[i] == '[') depth++;
        else if (s[i] == ']' && --depth == 0) break;
    }
    if (s[i] != ']')
        return 0;
    *text_off = 1;
    *text_len = i - 1;
    int after = i + 1;
    if (s[after] == '(')
        return 0;                      /* inline form; handled elsewhere */
    if (s[after] == '[') {
        int j = after + 1;
        for (; s[j] && s[j] != ']'; j++)
            if (s[j] == '[')
                return 0;
        if (s[j] != ']')
            return 0;
        *label_off = after + 1;
        *label_len = j - (after + 1);
        *shortcut = 0;
        return j + 1;
    }
    *label_off = 1;
    *label_len = i - 1;
    *shortcut = 1;
    return after;
}

static void ir_inline(FILE *out, const char *kind, long long start,
                      long long end, int line, int protectd, int depth,
                      long long parent)
{
    fprintf(out,
        "{\"kind\":\"%s\",\"start\":%lld,\"end\":%lld,"
        "\"line\":%d,\"endLine\":%d,\"protected\":%s,"
        "\"depth\":%d,\"parent\":%lld",
        kind, start, end, line, line, protectd ? "true" : "false",
        depth, parent);
}

static void ir_inline_field(FILE *out, const char *name,
                            const char *text, int len)
{
    char buf[MAX_LINE];
    int n = len < MAX_LINE - 1 ? len : MAX_LINE - 1;
    if (n < 0)
        n = 0;
    memcpy(buf, text, (size_t)n);
    buf[n] = '\0';
    fprintf(out, ",\"%s\":", name);
    ir_json_string(out, buf);
}

/* Half-open byte span of the bare destination; omitted when empty so it is
 * not mistaken for an insertion point. */
static void ir_dest_span(FILE *out, long long start, int len)
{
    if (len <= 0)
        return;
    fprintf(out, ",\"destinationStart\":%lld,\"destinationEnd\":%lld",
            start, start + len);
}

/*
 * Walk one line's content (no terminator), emitting inline records.
 * `base` is the file offset of text[0]; spans are base + index so CRLF
 * multi-line blocks must call this once per line with that line's line_off.
 */
static void emit_inline(FILE *out, const char *text, long long base,
                        int line, int depth, long long parent,
                        int at_block_start)
{
    int i = 0;
    while (text[i]) {
        if (text[i] == '\\' && text[i + 1]) {
            i += 2;               /* an escaped bracket opens nothing */
            continue;
        }

        int body_off = 0, body_len = 0;
        int span = inline_code_len(text + i, &body_off, &body_len);
        if (span) {
            ir_inline(out, "code_span", base + i, base + i + span, line,
                      1, depth, parent);
            ir_inline_field(out, "text", text + i + body_off, body_len);
            fputs("}\n", out);
            i += span;
            continue;
        }

        /* Bare `@key`. A `[` that bracket_has_citation accepts is claimed
         * below so `[@a]` is not a shortcut link. */
        if (text[i] == '@' && !is_example_list_marker(text, i, at_block_start)
            && !citation_follows_word(text, i)) {
            /* `email@example.com` is not a citation, and neither is `a@b`:
             * Pandoc requires the `@` not to follow a word character. */
            int klen = citation_key_len(text + i + 1);
            if (klen > 0) {
                int bracketed = in_citation_bracket(text, i);
                const char *mode = "in-text";
                if (bracketed)
                    mode = (i > 0 && text[i - 1] == '-') ? "suppress-author"
                                                         : "normal";
                ir_inline(out, "citation", base + i, base + i + 1 + klen,
                          line, 0, depth, parent);
                ir_inline_field(out, "key", text + i + 1, klen);
                fprintf(out, ",\"keyStart\":%lld,\"keyEnd\":%lld",
                        base + i + 1, base + i + 1 + klen);
                fprintf(out, ",\"mode\":\"%s\"}\n", mode);
                i += 1 + klen;
                continue;
            }
        }

        if (text[i] == '<') {
            span = inline_autolink_len(text + i);
            if (span) {
                ir_inline(out, "link", base + i, base + i + span, line,
                          0, depth, parent);
                ir_inline_field(out, "destination", text + i + 1, span - 2);
                ir_dest_span(out, base + i + 1, span - 2);
                fputs(",\"form\":\"autolink\"}\n", out);
                i += span;
                continue;
            }
            span = inline_html_tag_len(text + i);
            if (span) {
                ir_inline(out, "raw_inline", base + i, base + i + span,
                          line, 1, depth, parent);
                fputs("}\n", out);
                i += span;
                continue;
            }
        }

        /* Citation bracket, before the link scanners. `](` is a link. */
        if (text[i] == '[' && bracket_has_citation(text, i)) {
            int close = i + 1;
            while (text[close] && text[close] != ']')
                close++;
            for (int k = i + 1; k < close; k++) {
                if (text[k] != '@')
                    continue;
                if (citation_follows_word(text, k))
                    continue;
                int klen = citation_key_len(text + k + 1);
                if (klen <= 0)
                    continue;
                const char *mode = (text[k - 1] == '-') ? "suppress-author"
                                                        : "normal";
                ir_inline(out, "citation", base + k, base + k + 1 + klen,
                          line, 0, depth, parent);
                ir_inline_field(out, "key", text + k + 1, klen);
                fprintf(out, ",\"keyStart\":%lld,\"keyEnd\":%lld",
                        base + k + 1, base + k + 1 + klen);
                fprintf(out, ",\"mode\":\"%s\"}\n", mode);
                k += klen;
            }
            i = text[close] ? close + 1 : close;
            continue;
        }

        if (text[i] == '[' || (text[i] == '!' && text[i + 1] == '[')) {
            int image = (text[i] == '!');
            int off = image ? 1 : 0;
            int label_off = 0, label_len = 0, shortcut = 0;

            span = inline_footnote_ref_len(text + i, &label_off, &label_len);
            if (!image && span) {
                ir_inline(out, "footnote_ref", base + i, base + i + span,
                          line, 0, depth, parent);
                ir_inline_field(out, "label", text + i + label_off, label_len);
                fputs("}\n", out);
                i += span;
                continue;
            }

            int text_off = 0, text_len = 0;
            span = inline_link_len(text + i, &text_off, &text_len);
            if (span) {
                int raw_off = text_off + text_len + 2;
                int raw_len = span - raw_off - 1;
                int bare_off = 0, bare_len = 0;
                if (raw_len > 0)
                    dest_bare(text + i + raw_off, raw_len, &bare_off, &bare_len);
                ir_inline(out, image ? "image" : "link", base + i,
                          base + i + span, line, 0, depth, parent);
                ir_inline_field(out, "text", text + i + text_off, text_len);
                ir_inline_field(out, "destination",
                                text + i + raw_off + bare_off, bare_len);
                ir_dest_span(out, base + i + raw_off + bare_off, bare_len);
                fputs(",\"form\":\"inline\"}\n", out);
                i += span;
                continue;
            }

            span = inline_ref_link_len(text + i + off, &text_off, &text_len,
                                       &label_off, &label_len, &shortcut);
            if (span) {
                ir_inline(out, image ? "image" : "link", base + i,
                          base + i + off + span, line, 0, depth, parent);
                ir_inline_field(out, "text", text + i + off + text_off, text_len);
                ir_inline_field(out, "label", text + i + off + label_off,
                                label_len);
                fprintf(out, ",\"form\":\"%s\"}\n",
                        shortcut ? "shortcut" : "reference");
                i += off + span;
                continue;
            }
        }
        i++;
    }
}

/* Scan each line with its real line_off — never invent terminators (CRLF). */
static void emit_inline_lines(FILE *out, int from, int to, int depth)
{
    long long parent = line_off[from];
    for (int k = from; k <= to; k++)
        emit_inline(out, lines[k], line_off[k], k + 1, depth, parent,
                    k == from);
}

static void emit_list_children(FILE *out, int from, int to, long long parent)
{
    int i = from;
    while (i <= to) {
        int marker = list_marker_bytes(lines[i]);
        if (marker < 0) {
            i++;
            continue;
        }
        int content_col = list_content_column(lines[i]);
        if (content_col < 0) {
            i++;
            continue;
        }

        /* The item runs to the next marker or the end of the list. */
        int item_end = i;
        for (int j = i + 1; j <= to; j++) {
            if (list_marker_bytes(lines[j]) >= 0)
                break;
            if (!is_blank(lines[j])
                && indent_columns(lines[j], NULL) < content_col)
                break;
            item_end = j;
        }

        /* Paragraph runs inside the item, split on blank lines. Any run that
         * is not plainly prose is skipped whole. */
        int run_start = -1;
        for (int j = i; j <= item_end + 1; j++) {
            int blank = (j > item_end) || is_blank(lines[j]);
            if (!blank && run_start < 0)
                run_start = j;
            if (!blank)
                continue;
            if (run_start < 0)
                continue;
            int ok = 1;
            for (int k = run_start; k < j; k++)
                if (!item_line_is_plain(k, content_col)) {
                    ok = 0;
                    break;
                }
            if (ok) {
                long long start = line_off[run_start];
                if (run_start == i)
                    start += marker;   /* skip the marker on the first line */
                long long end = line_off[j - 1] + line_bytes[j - 1];
                if (end > start) {
                    fprintf(out,
                        "{\"kind\":\"paragraph\",\"start\":%lld,\"end\":%lld,"
                        "\"line\":%d,\"endLine\":%d,\"protected\":false,"
                        "\"depth\":1,\"parent\":%lld}\n",
                        start, end, run_start + 1, j, parent);
                    for (int k = run_start; k < j; k++) {
                        int skip = (k == i) ? marker : 0;
                        emit_inline(out, lines[k] + skip,
                                    line_off[k] + skip, k + 1, 2, start,
                                    k == i);
                    }
                }
            }
            run_start = -1;
        }
        i = item_end + 1;
    }
}

static void emit_ir(FILE *out, const char *source)
{
    /* The source path is part of the header so several files can share one
     * JSONL stream and stay tellable apart. */
    fputs("{\"kind\":\"document\",\"schema\":\"" IR_SCHEMA "\",\"source\":", out);
    ir_json_string(out, source ? source : "");
    fprintf(out, ",\"bytes\":%lld,\"lines\":%d}\n", src_bytes, nlines);

    struct fence_state fence = {0, 0, 0, 0, 0};
    enum linetype prev_content_type = LT_BLANK;
    int list_content_col = 0;
    int had_blank = 1;
    int i = 0;

    /* Byte 0 onwards is unattributed until the first record claims it; a
     * leading BOM is the usual occupant. ir_prev_last = -1 makes the first
     * gap report line 1. */
    ir_cursor = 0;
    ir_prev_last = -1;

    /* Front matter: only the very first line can open it, and only when a
     * closing delimiter exists. An unclosed `---` is a thematic break. */
    {
        int close = frontmatter_close_line();
        if (close > 0) {
            ir_block(out, "frontmatter", 0, close, 1);
            i = close + 1;
            prev_content_type = LT_TEXT;
            had_blank = 0;
        }
    }

    for (; i < nlines; i++) {
        const char *line = lines[i];
        enum linetype type = classify(line);

        /* ── Code fence ── */
        if (parse_fence_opener(line, &fence)) {
            if (indent_columns(line, NULL) < list_content_col) {
                list_content_col = 0;
            }
            int j = i + 1;
            while (j < nlines && !is_fence_closer(lines[j], &fence))
                j++;
            int end = (j < nlines) ? j : nlines - 1;
            ir_open(out, "code_fence", i, end, 1);
            fprintf(out, ",\"unterminated\":%s",
                    (j < nlines) ? "false" : "true");
            fputs("}\n", out);
            i = end;
            prev_content_type = LT_CODEFENCE;
            had_blank = 0;
            continue;
        }

        /* ── Pandoc grid / simple / multiline table ── */
        {
            int table_end = table_block_end(i);
            if (table_end > i) {
                if (indent_columns(line, NULL) < list_content_col) {
                    list_content_col = 0;
                }
                const char *form = "simple";
                if (multiline_table_end(i) > i)
                    form = "multiline";
                else if (is_grid_border(line))
                    form = "grid";
                ir_open(out, "table", i, table_end - 1, 1);
                fprintf(out, ",\"form\":\"%s\"}\n", form);
                emit_inline_lines(out, i, table_end - 1, 1);
                i = table_end - 1;
                prev_content_type = LT_TABLEBLOCK;
                had_blank = 0;
                continue;
            }
        }

        /* ── Raw HTML block ── */
        {
            enum raw_html_kind kind = raw_html_open_kind(line);
            if (kind != RAW_HTML_NONE) {
                const char *lt = strchr(line, '<');
                const char *after = lt ? lt + 1 : line + 1;
                int end = i;
                if (!raw_html_line_has_end(after, kind)) {
                    int j = i + 1;
                    while (j < nlines && !raw_html_line_has_end(lines[j], kind))
                        j++;
                    end = (j < nlines) ? j : nlines - 1;
                }
                ir_open(out, "raw_html", i, end, 1);
                fprintf(out, ",\"htmlKind\":\"%s\"}\n", ir_raw_html_name(kind));
                i = end;
                prev_content_type = LT_RAWHTML;
                had_blank = 0;
                continue;
            }
        }

        /* ── Blank ── */
        if (type == LT_BLANK) {
            had_blank = 1;
            continue;
        }

        /* ── Indented code ──
         * Same threshold and same paragraph-interruption rule as process().
         * Interior blank lines stay inside the block (Pandoc keeps them), so
         * the run is consumed greedily and then trimmed back.
         * A pipe table's rows classify as TEXT but are not a paragraph, so
         * the line after one is indented code. */
        if (indent_columns(line, NULL) >= list_content_col + 4
            && (had_blank || prev_content_type != LT_TEXT
                || (i > 0 && pipe_table_line[i - 1])))
        {
            int j = i;
            int last = i;
            while (j < nlines
                   && (is_blank(lines[j])
                       || indent_columns(lines[j], NULL) >= list_content_col + 4))
            {
                if (!is_blank(lines[j]))
                    last = j;
                j++;
            }
            ir_block(out, "code_indented", i, last, 1);
            i = last;
            prev_content_type = LT_INDENTCODE;
            had_blank = 0;
            continue;
        }

        /*
         * ── Pipe table, or the line block it would otherwise be mistaken for ──
         *
         * The delimiter row is the discriminator (is_pipe_delim_row). A leading
         * '|' is not required: `a | b` over `--|--` is a Table (#65). Headerless
         * headers must be prose only (is_headerless_table_header) — heading,
         * list, quote, and ref-def openers still win. A header continuing a
         * paragraph is absorbed earlier (lazy continuation), as in pandoc.
         * Both forms run to the first line with no '|'. Neither is
         * byte-protected today (dialect-policy §7 gaps 1 and 4).
         */
        int leading_pipe = is_table_line(line);
        int headerless = is_headerless_table_header(line)
                         && i + 1 < nlines && is_pipe_delim_row(lines[i + 1]);
        if (leading_pipe || headerless) {
            int is_table = headerless
                || (i + 1 < nlines && is_pipe_delim_row(lines[i + 1]));
            int j = i;
            if (is_table)
                while (j < nlines && strchr(lines[j], '|') != NULL)
                    j++;
            else
                while (j < nlines && is_table_line(lines[j]))
                    j++;
            int end = j - 1;
            if (is_table && end > i) {
                ir_open(out, "table", i, end, 0);
                fputs(",\"form\":\"pipe\"}\n", out);
                emit_inline_lines(out, i, end, 1);
            } else {
                ir_block(out, "line_block", i, end, 0);
            }
            i = end;
            prev_content_type = LT_TEXT;
            list_content_col = 0;
            had_blank = 0;
            continue;
        }

        /* ── Heading ── */
        if (type == LT_HEADING) {
            ir_emit_heading(out, i);
            prev_content_type = type;
            list_content_col = 0;
            had_blank = 0;
            continue;
        }

        /*
         * ── Setext heading ──
         * Before the thematic-break branch on purpose: `-----` under `-----`
         * is a heading whose text happens to look like a rule, and pandoc
         * agrees. A dash run after a *blank* has no text line above it and
         * falls through to the break branch below.
         */
        if (i + 1 < nlines
            && setext_text_ok(line)
            && is_setext_underline(lines[i + 1]))
        {
            int level = (lines[i + 1][0] == '=') ? 1 : 2;
            char text[MAX_LINE];
            int start = 0;
            while (line[start] == ' ' || line[start] == '\t')
                start++;
            int end = (int)strlen(line);
            while (end > start
                   && (line[end - 1] == ' ' || line[end - 1] == '\t'))
                end--;
            int n = end - start;
            memcpy(text, line + start, (size_t)n);
            text[n] = '\0';

            char plain[MAX_LINE];
            inline_plain(text, plain, sizeof plain);

            ir_open(out, "heading", i, i + 1, 0);
            fprintf(out, ",\"level\":%d,\"style\":\"setext\",\"text\":", level);
            ir_json_string(out, text);
            fputs(",\"plain\":", out);
            ir_json_string(out, plain);
            fputs("}\n", out);
            emit_inline(out, text, line_off[i] + start, i + 1, 1, line_off[i],
                        0);
            i++;
            prev_content_type = LT_HEADING;
            list_content_col = 0;
            had_blank = 0;
            continue;
        }

        /* ── Thematic break ── */
        if (is_thematic_break(line)) {
            ir_block(out, "thematic_break", i, i, 1);
            prev_content_type = LT_TEXT;
            list_content_col = 0;
            had_blank = 0;
            continue;
        }

        /*
         * ── Link and footnote definitions ──
         * Pandoc emits no block for either: they are definitions, like front
         * matter. They must never reach a prose pass, which is why they are
         * their own kinds rather than paragraphs.
         *
         * The two continue differently, and both were checked against pandoc.
         * A reference definition takes only a quoted title on the next line —
         * an indented plain line after it is a code block. A footnote
         * definition takes indented continuations and survives a blank line.
         */
        {
            int def = ref_def_kind(line);
            if (def) {
                int last = i;
                if (def == 1) {
                    while (last + 1 < nlines && is_ref_title_cont(lines[last + 1]))
                        last++;
                } else {
                    int j = last + 1;
                    while (j < nlines) {
                        if (is_blank(lines[j])) {
                            j++;
                            continue;
                        }
                        if (indent_columns(lines[j], NULL) < 4)
                            break;
                        last = j;
                        j++;
                    }
                }
                /* Label + destination so mdlinks need not re-parse the span. */
                {
                    const char *l = line;
                    int b = 0;
                    while (b < 3 && l[b] == ' ')
                        b++;
                    int label_start = b + 1 + (def == 2 ? 1 : 0);
                    int label_end = label_start;
                    while (l[label_end] && l[label_end] != ']') {
                        if (l[label_end] == '\\' && l[label_end + 1])
                            label_end += 2;
                        else
                            label_end++;
                    }
                    ir_open(out, def == 1 ? "reference_def" : "footnote_def",
                            i, last, 0);
                    ir_inline_field(out, "label", l + label_start,
                                    label_end - label_start);
                    if (def == 1) {
                        int d = label_end + 2;   /* past "]:" */
                        while (l[d] == ' ' || l[d] == '\t')
                            d++;
                        int bare_off = 0, bare_len = 0;
                        /* Rest of the line is destination [+ optional title]. */
                        dest_bare(l + d, (int)strlen(l + d),
                                  &bare_off, &bare_len);
                        ir_inline_field(out, "destination",
                                        l + d + bare_off, bare_len);
                        ir_dest_span(out, line_off[i] + d + bare_off,
                                     bare_len);
                    }
                    fputs("}\n", out);
                    /* Footnote bodies are prose. Start after `]:` so the
                     * definition label is not a footnote_ref. Continuations
                     * parent at the def; fences and extra-indented code
                     * are not scanned. */
                    if (def == 2) {
                        int body = label_end + 2;   /* past "]:" */
                        emit_inline(out, l + body, line_off[i] + body,
                                    i + 1, 1, line_off[i], 1);
                        struct fence_state in_fence = {0, 0, 0, 0, 0};
                        for (int k = i + 1; k <= last; k++) {
                            if (is_blank(lines[k]))
                                continue;
                            if (in_fence.active) {
                                if (is_fence_closer(lines[k], &in_fence))
                                    in_fence.active = 0;
                                continue;
                            }
                            if (parse_fence_opener(lines[k], &in_fence))
                                continue;
                            if (indent_columns(lines[k], NULL) >= 8)
                                continue;
                            if (raw_html_open_kind(lines[k]) != RAW_HTML_NONE)
                                continue;
                            int chars = 0;
                            indent_columns(lines[k], &chars);
                            if (is_blockquote_line(lines[k] + chars))
                                continue;
                            emit_inline(out, lines[k], line_off[k],
                                        k + 1, 1, line_off[i], 0);
                        }
                    }
                }
                i = last;
                /* Not LT_TEXT: a definition is not paragraph text, so
                 * indented code may follow it with no blank line. Pandoc
                 * reads `[id]: http://x` then four spaces as a CodeBlock. */
                prev_content_type = LT_REFDEF;
                list_content_col = 0;
                had_blank = 0;
                continue;
            }
        }

        /* ── List ──
         * One record per list, not per item: a run of markers, their
         * continuation lines, and the blank lines between them. Tight versus
         * loose is not represented in schema 1. */
        if (is_list_type(type)) {
            int j = i;
            int last = i;
            int col = list_content_column(lines[i]);
            if (col >= 0)
                list_content_col = col;
            for (j = i + 1; j < nlines; j++) {
                if (is_blank(lines[j]))
                    continue;
                enum linetype t = classify(lines[j]);
                if (is_list_type(t)) {
                    int c = list_content_column(lines[j]);
                    if (c >= 0)
                        list_content_col = c;
                    last = j;
                    continue;
                }
                if (is_list_continuation(lines[j])) {
                    int c = indent_columns(lines[j], NULL);
                    if (c < list_content_col)
                        list_content_col = c;
                    last = j;
                    continue;
                }
                break;
            }
            ir_block(out, "list", i, last, 0);
            emit_list_children(out, i, last, line_off[i]);
            i = last;
            prev_content_type = LT_BULLET;
            had_blank = 0;
            continue;
        }

        /* ── Block quote ── */
        if (is_blockquote_line(line)) {
            int j = i;
            while (j + 1 < nlines
                   && !is_blank(lines[j + 1])
                   && is_blockquote_line(lines[j + 1]))
                j++;
            ir_block(out, "block_quote", i, j, 0);
            emit_inline_lines(out, i, j, 1);
            i = j;
            prev_content_type = LT_TEXT;
            list_content_col = 0;
            had_blank = 0;
            continue;
        }

        /* ── Paragraph: to the next blank or the next block opener ── */
        {
            int j = i;
            while (j + 1 < nlines) {
                const char *next = lines[j + 1];
                if (is_blank(next) || is_blockquote_line(next)
                    || is_table_line(next) || is_thematic_break(next)
                    || classify(next) == LT_HEADING
                    || is_list_type(classify(next))
                    || raw_html_open_kind(next) != RAW_HTML_NONE
                    || table_block_end(j + 1) > j + 1)
                    break;
                struct fence_state probe;
                if (parse_fence_opener(next, &probe))
                    break;
                j++;
            }
            ir_block(out, "paragraph", i, j, 0);
            /* Per-line bases keep CRLF offsets honest; no synthetic join. */
            for (int k = i; k <= j; k++)
                emit_inline(out, lines[k], line_off[k], k + 1, 1, line_off[i],
                            k == i);
            i = j;
            prev_content_type = LT_TEXT;
            list_content_col = 0;
            had_blank = 0;
        }
    }

    /* Whatever is left: the final terminator, trailing blank lines, or the
     * whole file when it contains no blocks at all. */
    ir_gap(out, ir_cursor, src_bytes,
           ir_prev_last + 1, nlines > 0 ? nlines : 1);
}

/* ═══════════════════════════════════════════════════════════════════
 * Fixers — each modifies line in place, returns 1 if changed
 * ═══════════════════════════════════════════════════════════════════ */

/* Fix 1: Normalize bullet markers to - */
static int fix_bullet(char *line, int linenum)
{
    if (!opt_editorial)
        return 0;
    /* Spaced "* * *" is a thematic break, not a list item. */
    if (is_thematic_break(line))
        return 0;
    int pos = find_bullet(line);
    if (pos < 0 || line[pos] == '-')
        return 0;

    if (opt_verbose)
        fprintf(stderr, "  line %d: bullet '%c' → '-'\n", linenum, line[pos]);
    line[pos] = '-';
    record_fix(FIX_BULLET_STYLE, linenum);
    return 1;
}

/*
 * Fix 4: Strip bold/italic markers from heading text.
 * "## **The Big Idea**" → "## The Big Idea"
 * Handles **, *, and *** (bold-italic).  Preserves escaped \*.
 */
static int fix_heading_fmt(char *line, int linenum)
{
    if (!opt_editorial)
        return 0;
    if (!is_heading(line))
        return 0;

    /* Find start of heading text (after "### ") */
    char *p = line;
    while (*p == ' ') p++;
    while (*p == '#') p++;
    if (*p == ' ') p++;

    /* Quick check — any asterisks at all? */
    if (!strchr(p, '*'))
        return 0;

    char buf[MAX_LINE];
    int prefix_len = (int)(p - line);
    memcpy(buf, line, prefix_len);
    int bi = prefix_len;
    int changed = 0;

    while (*p && *p != '\n' && *p != '\r') {
        if (p[0] == '\\' && p[1] == '*') {
            /* Escaped asterisk — leave it alone */
            buf[bi++] = *p++;
            buf[bi++] = *p++;
        } else if (p[0] == '*' && p[1] == '*') {
            p += 2;     /* eat ** */
            changed = 1;
        } else if (p[0] == '*') {
            p += 1;     /* eat * */
            changed = 1;
        } else {
            buf[bi++] = *p++;
        }
    }
    buf[bi] = '\0';

    if (changed) {
        if (opt_verbose)
            fprintf(stderr, "  line %d: stripped bold/italic from heading\n",
                    linenum);
        strcpy(line, buf);
        record_fix(FIX_HEADER_FMT, linenum);
    }
    return changed;
}

/* fix_bold_colon — now handled by Ragel scanner */
/* fix_arrow_aside — now handled by Ragel scanner */

/*
 * Fix: Add missing space after blockquote marker (e.g. ">Text" -> "> Text")
 */
static int fix_blockquote_space(char *line, int linenum)
{
    if (!opt_editorial)
        return 0;
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
        
    if (line[i] == '>' && line[i+1] != ' ' && line[i+1] != '\0' && line[i+1] != '>') {
        char buf[MAX_LINE];
        memcpy(buf, line, i + 1);
        buf[i+1] = ' ';
        strcpy(buf + i + 2, line + i + 1);
        strcpy(line, buf);
        
        if (opt_verbose)
            fprintf(stderr, "  line %d: added space after blockquote marker\n", linenum);
        record_fix(FIX_BLOCKQUOTE_SPACE, linenum);
        return 1;
    }
    return 0;
}

/*
 * Footnote canonicalization: normalize reference tokens.
 * "[^ 1 ]" -> "[^1]"
 */
static int fix_footnote_refs(char *line, int linenum)
{
    if (!opt_footnote_canonical)
        return 0;

    char buf[MAX_LINE];
    int bi = 0;
    int i = 0;
    int len = (int)strlen(line);
    int changed = 0;

    while (i < len && bi < MAX_LINE - 1) {
        if (line[i] == '[' && i + 2 < len && line[i + 1] == '^') {
            int j = i + 2;
            while (j < len && (line[j] == ' ' || line[j] == '\t'))
                j++;

            int id_start = j;
            while (j < len && (isalnum((unsigned char)line[j]) || line[j] == '_' || line[j] == '-'))
                j++;
            int id_end = j;
            while (j < len && (line[j] == ' ' || line[j] == '\t'))
                j++;

            if (id_end > id_start && j < len && line[j] == ']') {
                buf[bi++] = '[';
                buf[bi++] = '^';
                for (int k = id_start; k < id_end && bi < MAX_LINE - 1; k++)
                    buf[bi++] = line[k];
                buf[bi++] = ']';
                if (id_start != i + 2 || j != id_end)
                    changed = 1;
                i = j + 1;
                continue;
            }
        }

        buf[bi++] = line[i++];
    }

    while (i < len && bi < MAX_LINE - 1)
        buf[bi++] = line[i++];
    buf[bi] = '\0';

    if (changed) {
        strcpy(line, buf);
        if (opt_verbose)
            fprintf(stderr, "  line %d: normalized footnote reference token\n", linenum);
        record_fix(FIX_FOOTNOTE_REF_FMT, linenum);
        return 1;
    }
    return 0;
}

/*
 * Footnote canonicalization: normalize definition line format.
 * "  [^ 1 ]  :text" -> "  [^1]: text"
 */
static int fix_footnote_def(char *line, int linenum)
{
    if (!opt_footnote_canonical)
        return 0;

    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;

    if (!(line[i] == '[' && line[i + 1] == '^'))
        return 0;

    int j = i + 2;
    while (line[j] == ' ' || line[j] == '\t')
        j++;
    int id_start = j;
    while (isalnum((unsigned char)line[j]) || line[j] == '_' || line[j] == '-')
        j++;
    int id_end = j;
    while (line[j] == ' ' || line[j] == '\t')
        j++;
    if (id_end <= id_start || line[j] != ']')
        return 0;
    j++;
    while (line[j] == ' ' || line[j] == '\t')
        j++;
    if (line[j] != ':')
        return 0;
    j++;
    while (line[j] == ' ' || line[j] == '\t')
        j++;

    char buf[MAX_LINE];
    int bi = 0;
    for (int k = 0; k < i && bi < MAX_LINE - 1; k++)
        buf[bi++] = line[k];
    if (bi < MAX_LINE - 1) buf[bi++] = '[';
    if (bi < MAX_LINE - 1) buf[bi++] = '^';
    for (int k = id_start; k < id_end && bi < MAX_LINE - 1; k++)
        buf[bi++] = line[k];
    if (bi < MAX_LINE - 1) buf[bi++] = ']';
    if (bi < MAX_LINE - 1) buf[bi++] = ':';
    if (line[j] != '\0' && bi < MAX_LINE - 1)
        buf[bi++] = ' ';
    while (line[j] != '\0' && bi < MAX_LINE - 1)
        buf[bi++] = line[j++];
    buf[bi] = '\0';

    if (strcmp(line, buf) != 0) {
        strcpy(line, buf);
        if (opt_verbose)
            fprintf(stderr, "  line %d: normalized footnote definition format\n", linenum);
        record_fix(FIX_FOOTNOTE_DEF_FMT, linenum);
        return 1;
    }
    return 0;
}

/* `#Title` is a Para to Pandoc; space after the marker is L2 (I2.1).
 * Multi-space collapse is AST-neutral and kept here to avoid a second pass. */
static int fix_heading_space(char *line, int linenum)
{
    if (!opt_required)
        return 0;

    int len = (int)strlen(line);
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    int hstart = i;
    while (line[i] == '#')
        i++;
    int hend = i;
    if (hend == hstart || hend - hstart > 6)
        return 0;

    int changed = 0;
    if (line[i] != ' ' && line[i] != '\0') {
        if (len + 1 < MAX_LINE) {
            memmove(line + i + 1, line + i, (size_t)(len - i + 1));
            line[i] = ' ';
            changed = 1;
        }
    } else if (line[i] == ' ') {
        int j = i;
        while (line[j] == ' ')
            j++;
        if (j > i + 1) {
            memmove(line + i + 1, line + j, (size_t)(len - j + 1));
            changed = 1;
        }
    }

    if (changed) {
        if (opt_verbose)
            fprintf(stderr, "  line %d: ATX heading spacing\n", linenum);
        record_fix(FIX_HEADING_SPACE, linenum);
    }
    return changed;
}

/* Trailing '#' run is AST-neutral under Pandoc, so opt-in only. */
static int fix_heading_canonical(char *line, int linenum)
{
    if (!opt_heading_canonical)
        return 0;

    int changed = 0;
    int len = (int)strlen(line);
    int i = 0;
    while (i < 3 && line[i] == ' ')
        i++;
    int hstart = i;
    while (line[i] == '#')
        i++;
    int hend = i;
    if (hend == hstart)
        return 0;
    if (hend - hstart > 6)
        return 0;

    int end = len - 1;
    while (end >= 0 && line[end] == ' ')
        end--;
    int k = end;
    while (k >= 0 && line[k] == '#')
        k--;
    if (k < end) {
        int text_end = k + 1;
        while (text_end > 0 && line[text_end - 1] == ' ')
            text_end--;
        if (text_end > hend + 1) {
            line[text_end] = '\0';
            changed = 1;
        }
    }

    if (changed) {
        if (opt_verbose)
            fprintf(stderr, "  line %d: heading canonicalized\n", linenum);
        record_fix(FIX_HEADING_CANONICAL, linenum);
        return 1;
    }
    return 0;
}

/*
 * Fence canonicalization:
 * - Preserve the marker run length; shortening it can expose fenced content.
 * - Opening: trim spacing before the info string.
 * - Closing: remove trailing whitespace.
 */
static int fix_fence_canonical(char *line, int linenum, int is_opening)
{
    if (!opt_fence_canonical)
        return 0;

    const char *rest;
    int indent_chars, indent_cols, run_length;
    char marker;
    if (!fence_prefix(line, -1, &indent_chars, &indent_cols,
                      &marker, &run_length, &rest))
        return 0;

    char buf[MAX_LINE];
    int bi = 0;
    /* Bytes, not columns: the original indentation is copied verbatim. */
    for (int k = 0; k < indent_chars && bi < MAX_LINE - 1; k++)
        buf[bi++] = line[k];
    for (int k = 0; k < run_length && bi < MAX_LINE - 1; k++)
        buf[bi++] = marker;

    if (is_opening) {
        while (*rest == ' ' || *rest == '\t')
            rest++;
        while (*rest != '\0' && bi < MAX_LINE - 1)
            buf[bi++] = *rest++;
        while (bi > 0 && (buf[bi - 1] == ' ' || buf[bi - 1] == '\t'))
            bi--;
    }
    buf[bi] = '\0';

    if (strcmp(line, buf) != 0) {
        strcpy(line, buf);
        if (opt_verbose)
            fprintf(stderr, "  line %d: fence delimiter canonicalized\n", linenum);
        record_fix(FIX_FENCE_CANONICAL, linenum);
        return 1;
    }
    return 0;
}

static int is_url_boundary_char(int c)
{
    return c == '\0' || isspace((unsigned char)c) || c == '<' || c == '>';
}

/*
 * Pandoc-safe links:
 * Wrap bare http(s) URLs in <...> autolink form.
 */
static int fix_pandoc_safe_links(char *line, int linenum)
{
    if (!opt_pandoc_safe_links)
        return 0;
    if (strstr(line, "](") != NULL || strchr(line, '`') != NULL)
        return 0;

    int len = (int)strlen(line);
    char buf[MAX_LINE];
    int bi = 0;
    int i = 0;
    int changed = 0;

    while (i < len && bi < MAX_LINE - 1) {
        int is_http = 0;
        int pref = 0;
        if (i + 7 < len && strncmp(line + i, "https://", 8) == 0) {
            is_http = 1;
            pref = 8;
        } else if (i + 6 < len && strncmp(line + i, "http://", 7) == 0) {
            is_http = 1;
            pref = 7;
        }

        if (!is_http) {
            buf[bi++] = line[i++];
            continue;
        }

        int prev = (i > 0) ? line[i - 1] : '\0';
        if (i > 0 && !is_url_boundary_char(prev) && prev != '(' && prev != '"' && prev != '\'') {
            buf[bi++] = line[i++];
            continue;
        }
        if (i > 0 && line[i - 1] == '<') {
            buf[bi++] = line[i++];
            continue;
        }

        int j = i + pref;
        while (j < len && !isspace((unsigned char)line[j]) && line[j] != '<' && line[j] != '>')
            j++;
        int url_end = j;
        while (url_end > i && (line[url_end - 1] == '.' || line[url_end - 1] == ','
            || line[url_end - 1] == ';' || line[url_end - 1] == ':' || line[url_end - 1] == '!'
            || line[url_end - 1] == '?'))
            url_end--;

        if (url_end <= i + pref) {
            buf[bi++] = line[i++];
            continue;
        }

        if (bi < MAX_LINE - 1) buf[bi++] = '<';
        for (int k = i; k < url_end && bi < MAX_LINE - 1; k++)
            buf[bi++] = line[k];
        if (bi < MAX_LINE - 1) buf[bi++] = '>';
        changed = 1;
        i = url_end;
    }

    while (i < len && bi < MAX_LINE - 1)
        buf[bi++] = line[i++];
    buf[bi] = '\0';

    if (changed) {
        strcpy(line, buf);
        if (opt_verbose)
            fprintf(stderr, "  line %d: wrapped bare URL for Pandoc\n", linenum);
        record_fix(FIX_PANDOC_SAFE_LINKS, linenum);
        return 1;
    }
    return 0;
}

/*
 * Scrivener repair:
 * "# *Heading" + next prose line "...*" -> "# Heading" + next prose line "..."
 * Also supports "**" markers.
 */
static int fix_scrivener_split_heading_emphasis(
    char *heading_line,
    char *next_line,
    int heading_lineno,
    int next_lineno)
{
    if (!opt_scrivener_repair)
        return 0;
    if (!is_heading(heading_line) || is_blank(next_line))
        return 0;
    if (classify(next_line) != LT_TEXT)
        return 0;

    char *p = heading_line;
    while (*p == ' ')
        p++;
    while (*p == '#')
        p++;
    if (*p == ' ')
        p++;

    int marker_len = 0;
    if (p[0] == '*' && p[1] == '*')
        marker_len = 2;
    else if (p[0] == '*')
        marker_len = 1;
    else
        return 0;

    const char *marker = (marker_len == 2) ? "**" : "*";

    /* If heading already closes marker, do nothing. */
    if (strstr(p + marker_len, marker) != NULL)
        return 0;

    char *close_pos = strstr(next_line, marker);
    if (close_pos == NULL)
        return 0;

    memmove(p, p + marker_len, strlen(p + marker_len) + 1);
    memmove(close_pos, close_pos + marker_len, strlen(close_pos + marker_len) + 1);

    if (opt_verbose) {
        fprintf(stderr,
            "  line %d/%d: repaired split heading emphasis marker\n",
            heading_lineno, next_lineno);
    }
    record_fix(FIX_SCRIVENER_SPLIT_EMPH, heading_lineno);
    return 1;
}

static int is_dash_join_char(unsigned char c)
{
    return isalnum(c) || c == '"' || c == '\'' || c == ')' || c == ']' || c == '}';
}

/* fix_chicago_emdash_spacing — now handled by Ragel scanner */

/* fix_chicago_ellipsis — now handled by Ragel scanner */

static int is_sentence_end_char(unsigned char c)
{
    return c == '.' || c == '!' || c == '?';
}

/* fix_chicago_sentence_spacing — now handled by Ragel scanner */

static int should_skip_chicago_punct2(const char *line)
{
    if (strchr(line, '`') != NULL)
        return 1;
    if (strstr(line, "](") != NULL)
        return 1;
    if (strstr(line, "http://") != NULL || strstr(line, "https://") != NULL)
        return 1;
    if (strstr(line, "www.") != NULL)
        return 1;
    return 0;
}

static int should_skip_chicago_abbrev(const char *line)
{
    return should_skip_chicago_punct2(line);
}

static int is_token_boundary_char(unsigned char c)
{
    return !isalnum(c) && c != '_';
}

/* fix_chicago_abbrev_commas — now handled by Ragel scanner */
/* fix_chicago_etal_period — now handled by Ragel scanner */

static int is_punct_for_spacing(unsigned char c)
{
    return c == ',' || c == ';' || c == ':' || c == '.';
}

/*
 * Space-before-punct only if the mark ends a word. A letter, digit, slash,
 * or underscore means it is inside a token (`./path`, `1.5`, `a:b`); a
 * closer (`) ] " '`) is still sentence punctuation (`word.)`).
 */
static int punct_ends_a_word(const char *p, const char *pe)
{
    const char *next = p + 1;
    if (next >= pe)
        return 1;                       /* end of line */
    unsigned char c = (unsigned char)*next;
    if (c == ' ' || c == '\t')
        return 1;
    if (c >= 0x80 || isalnum(c) || c == '/' || c == '_')
        return 0;
    return 1;
}

/* fix_chicago_space_before_punct — now handled by Ragel scanner */

static int should_insert_space_after_punct(unsigned char punct, unsigned char next)
{
    if (next == '\0' || next == '\n' || next == '\r')
        return 0;
    if (isspace(next))
        return 0;
    if ((punct == ',' || punct == '.') && isdigit(next))
        return 0;
    if (next == '"' || next == '\'')
        return 0;
    if (next == '[' || next == ')' || next == ']' || next == '}' || next == '/')
        return 0;
    if (next == '*' || next == '_' || next == '`')
        return 0;
    return isalpha(next) || next == '(';
}

/* fix_chicago_space_after_punct — now handled by Ragel scanner */
/* fix_chicago_quote_terminal_punct — now handled by Ragel scanner */

static int is_wordish(unsigned char c)
{
    return isalnum(c) || c == '\'' || c == '-';
}

static int is_month_token(const char *tok, int tlen)
{
    static const char *months[] = {
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec"
    };
    char tmp[16];
    if (tlen <= 0 || tlen >= (int)sizeof(tmp))
        return 0;
    for (int i = 0; i < tlen; i++)
        tmp[i] = (char)tolower((unsigned char)tok[i]);
    tmp[tlen] = '\0';
    for (size_t i = 0; i < sizeof(months) / sizeof(months[0]); i++) {
        if (strcmp(tmp, months[i]) == 0)
            return 1;
    }
    return 0;
}

static int is_number_unit_token(const char *tok, int tlen)
{
    static const char *units[] = {
        "cm", "mm", "m", "km", "in", "ft", "yd", "mi",
        "g", "kg", "mg", "lb", "lbs", "oz",
        "l", "ml", "cl",
        "mph", "kph", "fps",
        "s", "sec", "secs", "min", "mins", "hr", "hrs",
        "am", "pm"
    };
    char tmp[16];
    if (tlen <= 0 || tlen >= (int)sizeof(tmp))
        return 0;
    for (int i = 0; i < tlen; i++)
        tmp[i] = (char)tolower((unsigned char)tok[i]);
    tmp[tlen] = '\0';
    for (size_t i = 0; i < sizeof(units) / sizeof(units[0]); i++) {
        if (strcmp(tmp, units[i]) == 0)
            return 1;
    }
    return 0;
}

static int is_reference_label_token(const char *tok, int tlen)
{
    static const char *labels[] = {
        "chapter", "ch", "volume", "vol", "book", "part", "section", "sec",
        "verse", "v"
    };
    char tmp[16];
    if (tlen <= 0 || tlen >= (int)sizeof(tmp))
        return 0;
    for (int i = 0; i < tlen; i++)
        tmp[i] = (char)tolower((unsigned char)tok[i]);
    tmp[tlen] = '\0';
    for (size_t i = 0; i < sizeof(labels) / sizeof(labels[0]); i++) {
        if (strcmp(tmp, labels[i]) == 0)
            return 1;
    }
    return 0;
}

static int contains_number_lint_skip_construct(const char *line)
{
    if (strchr(line, '`') != NULL)
        return 1;
    if (strstr(line, "](") != NULL)
        return 1;
    if (strstr(line, "http://") != NULL || strstr(line, "https://") != NULL)
        return 1;
    if (strstr(line, "www.") != NULL)
        return 1;
    return 0;
}

static int is_word_one_to_nine(const char *tok, int tlen)
{
    static const char *words[] = {
        "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"
    };
    char tmp[16];
    if (tlen <= 0 || tlen >= (int)sizeof(tmp))
        return 0;
    for (int i = 0; i < tlen; i++)
        tmp[i] = (char)tolower((unsigned char)tok[i]);
    tmp[tlen] = '\0';
    for (size_t i = 0; i < sizeof(words) / sizeof(words[0]); i++) {
        if (strcmp(tmp, words[i]) == 0)
            return 1;
    }
    return 0;
}

/*
 * Warn-only lint: possible Chicago number-style issues.
 * - Numerals 1-9 in running prose
 * - Mixed style in a line (word one-nine + numeral >=10)
 * Skips obvious dates, measurements, percentages, code/link/url lines.
 */
static void lint_chicago_numbers(const char *line, int linenum)
{
    if (!opt_chicago_number_lint)
        return;
    if (contains_number_lint_skip_construct(line))
        return;

    int len = (int)strlen(line);
    int saw_word_small = 0;
    int saw_numeric_large = 0;

    for (int i = 0; i < len; i++) {
        if (isdigit((unsigned char)line[i])) {
            if (i > 0 && isalnum((unsigned char)line[i - 1]))
                continue;

            int j = i;
            while (j < len && isdigit((unsigned char)line[j]))
                j++;
            int num_len = j - i;
            long val = strtol(line + i, NULL, 10);

            int prev_tok_start = i - 1;
            while (prev_tok_start >= 0 && !isalpha((unsigned char)line[prev_tok_start]))
                prev_tok_start--;
            int prev_tok_end = prev_tok_start;
            while (prev_tok_start >= 0 && isalpha((unsigned char)line[prev_tok_start]))
                prev_tok_start--;
            prev_tok_start++;
            int prev_tok_len = (prev_tok_end >= prev_tok_start)
                ? (prev_tok_end - prev_tok_start + 1) : 0;

            int next = j;
            while (next < len && isspace((unsigned char)line[next]))
                next++;
            int next_tok_start = next;
            while (next < len && isalpha((unsigned char)line[next]))
                next++;
            int next_tok_len = next - next_tok_start;

            int skip_number = 0;
            if (j < len && line[j] == '%')
                skip_number = 1;
            if (i > 0 && line[i - 1] == '$')
                skip_number = 1;
            if (j < len && line[j] == ':')
                skip_number = 1; /* refs/time like 3:16 */
            if (prev_tok_len > 0 && is_month_token(line + prev_tok_start, prev_tok_len))
                skip_number = 1;
            if (next_tok_len > 0 && is_month_token(line + next_tok_start, next_tok_len))
                skip_number = 1;
            if (next_tok_len > 0 && is_number_unit_token(line + next_tok_start, next_tok_len))
                skip_number = 1;
            if (prev_tok_len > 0 && is_reference_label_token(line + prev_tok_start, prev_tok_len))
                skip_number = 1;

            if (!skip_number && val >= 1 && val <= 9 && num_len == 1) {
                number_style_warnings++;
                emit_diagnostic("chicago.number-style", "warning", linenum,
                                "likely Chicago number-style issue");
                if (!opt_quiet) {
                    fprintf(stderr,
                        "  line %d: numeral '%ld' in prose (Chicago often spells out 1-9)\n",
                        linenum, val);
                }
                return;
            }

            if (!skip_number && val >= 10)
                saw_numeric_large = 1;

            i = j - 1;
            continue;
        }

        if (isalpha((unsigned char)line[i])) {
            int j = i;
            while (j < len && isalpha((unsigned char)line[j]))
                j++;
            if (is_word_one_to_nine(line + i, j - i))
                saw_word_small = 1;
            i = j - 1;
        }
    }

    if (saw_word_small && saw_numeric_large && strstr(line, " and ") != NULL) {
        number_style_warnings++;
        emit_diagnostic("chicago.number-style", "warning", linenum,
                        "likely Chicago number-style issue");
        if (!opt_quiet) {
            fprintf(stderr,
                "  line %d: possible mixed number style (spelled-out + numeral)\n",
                linenum);
        }
    }
}

/*
 * Warn-only lint: likely missing Oxford/serial comma.
 * Detects simple patterns like "A, B and C" / "A, B or C".
 */
static void lint_serial_comma(const char *line, int linenum)
{
    if (!opt_serial_comma_lint)
        return;
    if (strchr(line, '`') != NULL)
        return;
    if (strstr(line, "](") != NULL)
        return;
    if (strstr(line, "http://") != NULL || strstr(line, "https://") != NULL)
        return;
    if (strstr(line, "www.") != NULL)
        return;

    int len = (int)strlen(line);
    for (int i = 0; i + 5 < len; i++) {
        int conj_len = 0;
        if (strncmp(line + i, " and ", 5) == 0)
            conj_len = 5;
        else if (strncmp(line + i, " or ", 4) == 0)
            conj_len = 4;
        else
            continue;

        int right = i + conj_len;
        while (right < len && line[right] == ' ')
            right++;
        if (right >= len || !is_wordish((unsigned char)line[right]))
            continue;

        int left = i - 1;
        while (left >= 0 && line[left] == ' ')
            left--;
        if (left < 0 || !is_wordish((unsigned char)line[left]))
            continue;

        if (line[left] == ',')
            continue;

        int clause_start = 0;
        for (int j = left; j >= 0; j--) {
            if (line[j] == '.' || line[j] == '!' || line[j] == '?'
                || line[j] == ';' || line[j] == ':' || line[j] == '(') {
                clause_start = j + 1;
                break;
            }
        }

        int commas = 0;
        for (int j = clause_start; j < i; j++) {
            if (line[j] == ',')
                commas++;
        }
        if (commas < 1)
            continue;

        serial_comma_warnings++;
        emit_diagnostic("chicago.serial-comma", "warning", linenum,
                        "likely missing serial comma");
        if (!opt_quiet) {
            fprintf(stderr,
                "  line %d: possible missing serial comma before '%.*s'\n",
                linenum, conj_len - 2, line + i + 1);
        }
        return; /* one warning per line max */
    }
}

/* Trailing tab: leave the line alone — expansion depends on width and
 * --tab-stop, which the fixer must not encode. */
static int trailing_has_tab(const char *line)
{
    int len = (int)strlen(line);
    int k = len;
    while (k > 0 && (line[k - 1] == ' ' || line[k - 1] == '\t'))
        k--;
    if (k == 0)
        return 0;                  /* whitespace-only: a blank line */
    for (int j = k; j < len; j++)
        if (line[j] == '\t')
            return 1;
    return 0;
}

/* Two+ trailing spaces, some content, and a following content line.
 * index < 0 keeps the break (conservative). */
static int is_hard_break(const char *line, int index)
{
    int len = (int)strlen(line);
    if (len < 3 || line[len - 1] != ' ' || line[len - 2] != ' ')
        return 0;

    int k = len;
    while (k > 0 && (line[k - 1] == ' ' || line[k - 1] == '\t'))
        k--;
    if (k == 0)
        return 0;

    if (index < 0)
        return 1;
    if (index + 1 >= nlines)
        return 0;
    const char *next = lines[index + 1];
    while (*next == ' ' || *next == '\t')
        next++;
    return *next != '\0';
}

/* Odd-length trailing backslash run: `foo\ ` is a literal `\`, not a break. */
static int ends_with_unescaped_backslash(const char *line, int content_len)
{
    int n = 0;
    while (content_len > 0 && line[content_len - 1] == '\\') {
        n++;
        content_len--;
    }
    return n % 2 == 1;
}

static int fix_trailing_ws(char *line, int linenum, int index)
{
    if (!opt_trail_ws)
        return 0;
    if (trailing_has_tab(line))
        return 0;

    if (is_hard_break(line, index)) {
        /* Normalize to exactly two: the break survives, and a five-space
         * ending stops being five bytes nobody can see. */
        int len = (int)strlen(line);
        int orig = len;
        while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\t'))
            len--;
        line[len] = ' ';
        line[len + 1] = ' ';
        line[len + 2] = '\0';
        if (len + 2 != orig)
            record_fix(FIX_TRAILING_WS, linenum);
        return len + 2 != orig;
    }

    int len = (int)strlen(line);
    int orig = len;

    while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\t'))
        len--;

    if (len == orig)
        return 0;

    /* Keep one space after an unescaped `\`: stripping it makes
     * `foo\ \nbar` into an escaped LineBreak under +escaped_line_breaks. */
    if (ends_with_unescaped_backslash(line, len) && orig > len
        && line[len] == ' ') {
        line[len] = ' ';
        line[len + 1] = '\0';
        if (len + 1 == orig)
            return 0;
        record_fix(FIX_TRAILING_WS, linenum);
        return 1;
    }

    line[len] = '\0';
    record_fix(FIX_TRAILING_WS, linenum);
    return 1;
}

/* ═══════════════════════════════════════════════════════════════════
 * Paragraph wrapping
 * ═══════════════════════════════════════════════════════════════════ */

#define MAX_PARA (MAX_LINE * 50)

static const char *para_lines_buf[MAX_LINES];
static int npara = 0;

/* Validated UTF-8 length of the code point at s[i] (I1.1 already enforced). */
static int utf8_sequence_len(const unsigned char *s, int avail, const char **why);

static int utf8_cp_len(const char *s, int i, int end)
{
    const char *why = NULL;
    int n = utf8_sequence_len((const unsigned char *)s + i, end - i, &why);
    return n > 0 ? n : 1;
}

/* Display columns spanned by [from, to). Wide = 2, combining = 0. */
static int display_columns(const char *text, int from, int to)
{
    int cols = 0;
    for (int i = from; i < to; ) {
        int n = utf8_cp_len(text, i, to);
        if (i + n > to)
            break;
        cols += mdfix_display_width((const unsigned char *)text + i);
        i += n;
    }
    return cols;
}

/*
 * Wrap on display columns (mdfix_display_width): break only at ASCII spaces.
 * Unspaced tokens (including CJK without spaces) are not split.
 */
/* Break only where the next line would still be prose. */
static enum linetype classify(const char *line);

static int is_paren_ordered(const char *line)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    if (!isdigit((unsigned char)line[i]))
        return 0;
    while (isdigit((unsigned char)line[i]))
        i++;
    return line[i] == ')' && line[i + 1] == ' ';
}

static int is_definition_marker(const char *line)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    return line[i] == ':' && (line[i + 1] == ' ' || line[i + 1] == '\t');
}

static int starts_a_block(const char *text, int off)
{
    char probe[MAX_LINE];
    int n = 0;
    while (text[off] == ' ')
        off++;
    while (text[off] && text[off] != '\n' && n < MAX_LINE - 1)
        probe[n++] = text[off++];
    probe[n] = '\0';
    enum linetype t = classify(probe);
    if (t != LT_TEXT && t != LT_BLANK)
        return 1;
    /* classify() is six-way; these are structure it reports as TEXT. */
    return is_blockquote_line(probe) || is_thematic_break(probe)
        || is_setext_underline(probe) || ref_def_kind(probe)
        || is_paren_ordered(probe) || is_definition_marker(probe);
}

static void emit_wrapped_break(FILE *out, const char *text, int width,
                               int hard)
{
    int len = (int)strlen(text);
    int pos = 0;

    /* `hard` puts the two spaces back on the *last* line this unit emits, not
     * on the first. Wrapping may turn one source line into several, and a
     * break belongs where the author put it — at the end. */
    while (pos < len) {
        if (display_columns(text, pos, len) <= width) {
            fprintf(out, "%s%s\n", text + pos, hard ? "  " : "");
            return;
        }

        /* Last ASCII space whose preceding display width is still <= width. */
        int break_at = -1;
        int cols = 0;
        for (int i = pos; i < len; ) {
            if (text[i] == ' ' && cols <= width)
                break_at = i;
            cols += mdfix_display_width((const unsigned char *)text + i);
            if (cols > width)
                break;
            i += utf8_cp_len(text, i, len);
        }

        /*
         * Back off from a break that would put a block marker at the start of
         * the next line. If every in-budget candidate does, take none of them
         * and let the line run long — an over-wide line is cosmetic.
         */
        int all_invent_a_block = 0;
        while (break_at > pos && starts_a_block(text, break_at)) {
            int earlier = -1;
            for (int i = pos; i < break_at; i++)
                if (text[i] == ' ')
                    earlier = i;
            if (earlier > pos)
                break_at = earlier;
            else {
                all_invent_a_block = 1;
                break;
            }
        }

        if (all_invent_a_block) {
            fprintf(out, "%s%s\n", text + pos, hard ? "  " : "");
            return;
        }

        if (break_at <= pos) {
            /* No break opportunity in budget: emit the whole token
             * through the next space (do not split), unless that space
             * would invent a block too. */
            break_at = pos;
            while (break_at < len && text[break_at] != ' ')
                break_at += utf8_cp_len(text, break_at, len);
            if (break_at >= len || starts_a_block(text, break_at)) {
                fprintf(out, "%s%s\n", text + pos, hard ? "  " : "");
                return;
            }
        }

        int stop = break_at;
        while (stop > pos && (text[stop - 1] == ' ' || text[stop - 1] == '\t'))
            stop--;
        fwrite(text + pos, 1, (size_t)(stop - pos), out);
        fputc('\n', out);
        pos = break_at;
        while (pos < len && text[pos] == ' ')
            pos++;
    }
}

/* Is this line near the target width — i.e. does it look machine-wrapped? */
static int near_width(const char *line, int wrap_width)
{
    int len = (int)strlen(line);
    while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\t'))
        len--;
    return display_columns(line, 0, len) >= (wrap_width * 3 / 5);
}

/*
 * Decide the segment once. Per line, joining makes a longer line that then
 * joins again, so --wrap was not a fixed point.
 */
static int segment_is_wrapped(int from, int to, int wrap_width)
{
    for (int i = from; i < to; i++)
        if (near_width(para_lines_buf[i], wrap_width))
            return 1;
    return 0;
}

static void flush_paragraph(FILE *out)
{
    if (npara == 0)
        return;

    if (opt_wrap_width <= 0) {
        for (int i = 0; i < npara; i++)
            fprintf(out, "%s\n", para_lines_buf[i]);
        npara = 0;
        return;
    }

    /*
     * Split the paragraph into segments, then decide each segment whole.
     *
     * A segment ends at a hard break (which must stay a break) or before a
     * line whose trailing whitespace holds a tab (which is emitted byte for
     * byte). Everything between is one reflow decision.
     */
    char joined[MAX_PARA];
    int i = 0;

    while (i < npara) {
        if (trailing_has_tab(para_lines_buf[i])) {
            fprintf(out, "%s\n", para_lines_buf[i]);
            i++;
            continue;
        }

        int end = i;
        int hard = 0;
        while (end < npara && !trailing_has_tab(para_lines_buf[end])) {
            if (end < npara - 1 && is_hard_break(para_lines_buf[end], -1)) {
                hard = 1;
                end++;
                break;
            }
            end++;
        }

        if (!segment_is_wrapped(i, end, opt_wrap_width)) {
            /* Deliberately short lines: left exactly as written. */
            for (int k = i; k < end; k++)
                fprintf(out, "%s\n", para_lines_buf[k]);
            i = end;
            continue;
        }

        int pos = 0;
        for (int k = i; k < end; k++) {
            const char *s = para_lines_buf[k];
            /*
             * Drop a continuation line's leading indent. It is Markdown's
             * lazy continuation, not content, and carrying it into the join
             * produced a run of spaces mid-sentence that a later pass then
             * collapsed — so `--wrap` needed two runs to settle. The first
             * line keeps its indent: that one is the block's own, and a list
             * item's text depends on it.
             */
            if (k > i)
                while (*s == ' ' || *s == '\t')
                    s++;
            int slen = (int)strlen(s);
            while (slen > 0 && (s[slen - 1] == ' ' || s[slen - 1] == '\t'))
                slen--;
            /* Keep one space after `\` when this fragment ends the segment;
             * a join space already protects a mid-paragraph `\`. */
            if (k == end - 1 && !hard
                && ends_with_unescaped_backslash(s, slen)
                && slen < (int)strlen(s) && s[slen] == ' ')
                slen++;

            if (pos + slen >= MAX_PARA)
                slen = MAX_PARA - pos - 1;
            memcpy(joined + pos, s, slen);
            pos += slen;
            if (k < end - 1 && pos < MAX_PARA - 1)
                joined[pos++] = ' ';
        }
        joined[pos] = '\0';
        emit_wrapped_break(out, joined, opt_wrap_width, hard);
        i = end;
    }

    npara = 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * I/O
 * ═══════════════════════════════════════════════════════════════════ */

static void free_lines(void);

/*
 * Read every physical line with getline. Never silently split a long line the
 * way fgets(MAX_LINE) did (a 9000-byte line became two "lines" and a longer
 * file while reporting clean).
 *
 * Processing still uses MAX_LINE-sized work buffers, so a line that would not
 * fit is a hard error rather than a silent truncate. Returns 0 on success,
 * 1 on I/O or capacity failure (caller free_lines).
 */
/*
 * L1 encoding validation — architecture.md I1.1.
 *
 * mdtools expects UTF-8. Malformed input used to be accepted silently and
 * copied straight into the IR, so `mdfix --emit-ir` emitted JSON that no
 * parser could read: I4.1 was false for a reason that had nothing to do with
 * Markdown, and every consumer inherited the failure.
 *
 * Rejecting rather than substituting U+FFFD is deliberate. Replacement would
 * change byte lengths and invalidate I1.3 — spans must address the file on
 * disk — and silently repairing an author's encoding is the wrong default for
 * a tool that edits manuscripts.
 *
 * Returns the length of the sequence starting at s, or 0 with *why set.
 * The ranges below are RFC 3629, which excludes overlong forms, UTF-16
 * surrogates (U+D800..U+DFFF), and anything past U+10FFFF.
 */
static int utf8_sequence_len(const unsigned char *s, int avail, const char **why)
{
    unsigned char c = s[0];

    if (c < 0x80) {
        if (c == 0x00) {
            /*
             * U+0000 is valid Unicode but not valid in a text document, and
             * every fixer here is strlen-bounded: a NUL truncated the line and
             * the remainder was silently dropped on output. A 36-byte file
             * came back 22 bytes with the tail gone.
             */
            *why = "NUL byte (would silently truncate the line)";
            return 0;
        }
        return 1;
    }
    if (c < 0xC2) {
        *why = (c < 0xC0) ? "unexpected continuation byte"
                          : "overlong two-byte sequence";
        return 0;
    }
    if (c < 0xE0) {
        if (avail < 2 || (s[1] & 0xC0) != 0x80) {
            *why = "truncated two-byte sequence";
            return 0;
        }
        return 2;
    }
    if (c < 0xF0) {
        if (avail < 3 || (s[1] & 0xC0) != 0x80 || (s[2] & 0xC0) != 0x80) {
            *why = "truncated three-byte sequence";
            return 0;
        }
        if (c == 0xE0 && s[1] < 0xA0) {
            *why = "overlong three-byte sequence";
            return 0;
        }
        if (c == 0xED && s[1] >= 0xA0) {
            *why = "UTF-16 surrogate (U+D800..U+DFFF)";
            return 0;
        }
        return 3;
    }
    if (c < 0xF5) {
        if (avail < 4 || (s[1] & 0xC0) != 0x80 || (s[2] & 0xC0) != 0x80
            || (s[3] & 0xC0) != 0x80) {
            *why = "truncated four-byte sequence";
            return 0;
        }
        if (c == 0xF0 && s[1] < 0x90) {
            *why = "overlong four-byte sequence";
            return 0;
        }
        if (c == 0xF4 && s[1] >= 0x90) {
            *why = "codepoint above U+10FFFF";
            return 0;
        }
        return 4;
    }
    *why = "invalid lead byte";
    return 0;
}

/* Offset of the first malformed byte within [s, s+len), or -1. */
static long long utf8_first_bad(const char *s, int len, const char **why)
{
    int i = 0;
    while (i < len) {
        int n = utf8_sequence_len((const unsigned char *)s + i, len - i, why);
        if (n == 0)
            return i;
        i += n;
    }
    return -1;
}

/*
 * L1 normalization check — architecture.md I1.2.
 *
 * Reports where a document leaves NFC. It does not rewrite it: normalizing at
 * input would move every offset after the change and so break I1.3, and it
 * would edit the author's file as a side effect of reading it. Rewriting is
 * L3 and opt-in (--normalize-nfc).
 *
 * This is the UAX #15 quick check, not normalize-and-compare: per code point,
 * NFC_QC must be Yes and the canonical combining class must not go backwards.
 * Quick check is allowed to answer "maybe" (it reports a Maybe as not-NFC),
 * so a run may name a sequence that full normalization would leave alone.
 * Over-reporting is the safe direction for a warning that changes nothing.
 *
 * Called per line rather than per file, which is exact rather than merely
 * convenient: a line terminator is ASCII, so it is a starter with class 0 and
 * ends any combining sequence. No sequence spans a newline.
 *
 * Returns the byte offset within [s, s+len) of the first code point that
 * fails and stores its length in *plen, or -1. Assumes s is already known to
 * be well-formed UTF-8 — I1.1 runs first and refuses the file otherwise.
 */
static long long nfc_first_bad(const char *s, int len, int *plen)
{
    *plen = 0;
    const unsigned char *p = (const unsigned char *)s;
    int i = 0, last_ccc = 0;

    while (i < len) {
        if (p[i] < 0x80) {          /* ASCII: NFC_QC=Yes, ccc=0 */
            last_ccc = 0;
            i++;
            continue;
        }
        const char *why = NULL;
        int n = utf8_sequence_len(p + i, len - i, &why);
        if (n == 0)
            return -1;              /* I1.1 refuses this file; do not guess */

        int ccc, qc;
        mdfix_nfc_ccc_qc(p + i, p + i + n, &ccc, &qc);
        *plen = n;
        if (qc != 0)
            return i;               /* NFC_QC No or Maybe */
        if (ccc != 0 && last_ccc > ccc)
            return i;               /* marks out of canonical order */

        last_ccc = ccc;
        i += n;
    }
    *plen = 0;
    return -1;
}

/*
 * L3: rewrite lines[] to NFC when --normalize-nfc is set (off by default).
 * Runs after the IR early-return so emitted spans still address the file on
 * disk.
 *
 * NFC can expand, so the destination is sized with
 * `mdfix_nfc_normalize_bound` (UAX #15's 3x). Any non-OK status means
 * refuse rather than emit a prefix.
 */
#define NFC_DST_MAX (MAX_LINE * 3 + 8)

static int normalize_lines_nfc(void)
{
    static unsigned char dst[NFC_DST_MAX];

    for (int i = 0; i < nlines; i++) {
        int len = (int)strlen(lines[i]);
        int bad_len = 0;
        if (nfc_first_bad(lines[i], len, &bad_len) < 0)
            continue;               /* already NFC — nothing to write */

        if (mdfix_nfc_normalize_bound((size_t)len) > sizeof dst) {
            /* Unreachable while MAX_LINE bounds a line and NFC_DST_MAX is
             * 3x it, which is the same bound. Stated so that changing either
             * one is caught here rather than in someone's manuscript. */
            fprintf(stderr,
                "error: line %d needs a larger normalization buffer than "
                "mdfix has.\nThis should be unreachable; please report it.\n",
                i + 1);
            return 1;
        }

        size_t nout = 0;
        mdfix_nfc_status st = mdfix_nfc_normalize(
            (const unsigned char *)lines[i], (size_t)len, dst, sizeof dst,
            &nout);
        if (st != MDFIX_NFC_OK) {
            fprintf(stderr,
                "error: line %d could not be normalized (status %d); the "
                "result would be\na prefix of the correct answer. The file is "
                "unchanged; please report this input.\n", i + 1, (int)st);
            return 1;
        }
        if (nout > (size_t)(MAX_LINE - 1)) {
            fprintf(stderr,
                "error: line %d is %zu bytes after NFC normalization "
                "(limit %d).\n"
                "mdfix refuses to silently split or truncate long lines.\n",
                i + 1, nout, MAX_LINE - 1);
            return 1;
        }
        memcpy(lines[i], dst, nout);
        lines[i][nout] = '\0';
    }
    return 0;
}

static int read_all(FILE *fp)
{
    char *buf = NULL;
    size_t cap = 0;
    ssize_t nread;
    nlines = 0;
    src_bytes = 0;

    while ((nread = getline(&buf, &cap, fp)) != -1) {
        /* Raw width including the terminator, captured before stripping —
         * this is what advances the offset, and it is the only place CRLF
         * and a missing final newline are still visible. */
        long long raw = (long long)nread;

        /* Strip line endings — we add our own on output (normalizes CRLF). */
        while (nread > 0 && (buf[nread - 1] == '\n' || buf[nread - 1] == '\r'))
            buf[--nread] = '\0';

        /*
         * A leading BOM belongs to the file, not to the first heading.
         * Pandoc strips it — `\xEF\xBB\xBF# Title` is a Header with the
         * identifier `title` — while mdfix classified the line by its first
         * byte and so saw no heading at all, mis-parsing the whole file.
         *
         * Skipping the bytes rather than rewriting the buffer keeps I1.3:
         * line_off still points at the first *content* byte in the file.
         */
        int skip = 0;
        if (nlines == 0 && nread >= 3
            && (unsigned char)buf[0] == 0xEF
            && (unsigned char)buf[1] == 0xBB
            && (unsigned char)buf[2] == 0xBF)
        {
            skip = 3;
        }

        const char *why = NULL;
        long long bad = utf8_first_bad(buf + skip, (int)nread - skip, &why);
        if (bad >= 0) {
            fprintf(stderr,
                "error: line %d is not valid UTF-8 at byte offset %lld: %s.\n"
                "mdtools expects UTF-8; refusing to guess at the encoding.\n",
                nlines + 1, src_bytes + skip + bad, why);
            free(buf);
            free_lines();
            return 1;
        }

        nread -= skip;
        memmove(buf, buf + skip, (size_t)nread + 1);

        /*
         * I1.2: report, do not rewrite. One diagnostic per line, at the first
         * offending code point — a decomposed document would otherwise emit a
         * diagnostic per accent and bury everything else on the stream.
         */
        int bad_len = 0;
        long long non_nfc = nfc_first_bad(buf, (int)nread, &bad_len);
        if (non_nfc >= 0) {
            non_nfc_warnings++;
            long long at = src_bytes + skip + non_nfc;
            emit_diagnostic_span("unicode.non-nfc", "warning", nlines + 1,
                                 at, at + bad_len,
                                 opt_normalize_nfc
                                   ? "not NFC; will rewrite with --normalize-nfc"
                                   : "not NFC; mdfix reports but does not "
                                     "rewrite (use --normalize-nfc)");
        }

        /*
         * ">" not ">=": a line of exactly MAX_LINE-1 content bytes plus its
         * NUL fills the buffer exactly. The old guard rejected that length
         * while the message named it as the limit, so a user at the boundary
         * was told 8191 was both too long and the maximum.
         */
        if (nread > MAX_LINE - 1) {
            fprintf(stderr,
                "error: line %d is %zd bytes (limit %d). "
                "mdfix refuses to silently split or truncate long lines.\n",
                nlines + 1, (ssize_t)nread, MAX_LINE - 1);
            free(buf);
            free_lines();
            return 1;
        }

        if (nlines >= MAX_LINES) {
            fprintf(stderr,
                "Holy shit, %d lines? Write a shorter book.\n", MAX_LINES);
            free(buf);
            free_lines();
            return 1;
        }
        /*
         * MAX_LINE, not nread + 1. Every fixer mutates lines[i] in place and
         * several of them lengthen it — heading `#Title` -> `# Title`,
         * blockquote `>q` -> `> q`, footnote defs, abbreviation commas,
         * autolink brackets. They bound themselves by MAX_LINE because that
         * is how large this allocation has always been.
         *
         * Right-sizing here left those guards checking the wrong number, so
         * any expanding fix wrote past the end of the heap block. ASan
         * confirmed overflows on inputs as small as "#Title\n", on this
         * repo's own README under --technical, and on the -i write path —
         * corrupting the heap while writing the user's file. The test suite
         * passed throughout, because a few bytes past a small malloc rarely
         * shows without a sanitizer.
         */
        lines[nlines] = malloc(MAX_LINE);
        if (!lines[nlines]) {
            perror("malloc failed, out of memory");
            free(buf);
            free_lines();
            return 1;
        }
        memcpy(lines[nlines], buf, (size_t)nread + 1);
        /* skip: the BOM is part of the file but not of the line's text, so
         * the content offset moves past it while src_bytes still counts it. */
        line_off[nlines]   = src_bytes + skip;
        line_bytes[nlines] = (int)nread;
        src_bytes += raw;
        nlines++;
    }
    free(buf);
    if (ferror(fp)) {
        fprintf(stderr, "error reading input: ");
        perror(NULL);
        free_lines();
        return 1;
    }
    return 0;
}

static void free_lines(void)
{
    for (int i = 0; i < nlines; i++)
        free(lines[i]);
    nlines = 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * Ragel scanner — inline text transformations
 * ═══════════════════════════════════════════════════════════════════ */

struct scan_ctx {
    /* Output buffer */
    char   out[MAX_LINE];
    int    oi;

    /* Per-invocation fix hit counts (merged to globals only if output differs) */
    int    fix_hits[NUM_FIXES];

    /* Flag copies — set once per line before scanning */
    int    no_arrow_aside;
    int    editorial;      /* L3 editorial passes: arrow aside, bold colon */
    int    do_chicago_punct;
    int    do_chicago_punct2;
    int    do_chicago_abbrev;
    int    skip_punct2;
    int    skip_abbrev;
    int    spaced_emdash;
    int    linenum;
};

/*
 * U+2026 HORIZONTAL ELLIPSIS, the mark this profile emits.
 *
 * dialect-policy §4: typography mdtools *writes* must render the same with
 * and without Pandoc's `smart`. ASCII `...` does not — `smart` folds it to
 * U+2026 and a bare reader leaves three periods — so emitting it makes the
 * output depend on a flag the reader controls and the author does not.
 *
 * This is only about what mdfix emits. An ellipsis the author already wrote
 * as `...` is passed through untouched; §4 constrains our output, not their
 * input.
 */
#define ELLIPSIS "\xE2\x80\xA6"

#define EMIT_CHAR(c) do { \
    if (ctx->oi < MAX_LINE - 1) ctx->out[ctx->oi++] = (c); \
} while (0)

#define EMIT_STR(s, n) do { \
    for (int _i = 0; _i < (n) && ctx->oi < MAX_LINE - 1; _i++) \
        ctx->out[ctx->oi++] = (s)[_i]; \
} while (0)

#define EMIT_EMDASH() do { \
    if (ctx->oi < MAX_LINE - 3) { \
        ctx->out[ctx->oi++] = '\xE2'; \
        ctx->out[ctx->oi++] = '\x80'; \
        ctx->out[ctx->oi++] = '\x94'; \
    } \
} while (0)

#define EMIT_DATA(from, to) do { \
    const char *_p = (from); \
    const char *_e = (to); \
    while (_p < _e && ctx->oi < MAX_LINE - 1) \
        ctx->out[ctx->oi++] = *_p++; \
} while (0)

#define BUMP(cat) ctx->fix_hits[(cat)]++

static void run_scanner(struct scan_ctx *ctx, const char *input, int len)
{
    const char *p   = input;
    const char *pe  = input + len;
    const char *eof = pe;
    const char *ts, *te;
    int cs, act;

    ctx->oi = 0;

    
#line 4418 "mdfix.c"
	{
	cs = mdfix_scanner_start;
	ts = 0;
	te = 0;
	act = 0;
	}

#line 4426 "mdfix.c"
	{
	if ( p == pe )
		goto _test_eof;
	switch ( cs )
	{
tr0:
#line 4812 "mdfix.rl"
	{{p = ((te))-1;}{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr1:
#line 4562 "mdfix.rl"
	{te = p+1;{
                if (!ctx->do_chicago_punct) {
                    EMIT_DATA(ts, te);
                } else {
                    int prev = ctx->oi - 1;
                    while (prev >= 0 && (ctx->out[prev] == ' ' || ctx->out[prev] == '\t'))
                        prev--;

                    const char *next = te;
                    while (next < pe && (*next == ' ' || *next == '\t'))
                        next++;

                    if (prev >= 0 && next < pe
                        && is_dash_join_char((unsigned char)ctx->out[prev])
                        && is_dash_join_char((unsigned char)*next)) {
                        int old_oi = ctx->oi;
                        while (ctx->oi > 0
                               && (ctx->out[ctx->oi-1] == ' '
                                   || ctx->out[ctx->oi-1] == '\t'))
                            ctx->oi--;
                        if (ctx->spaced_emdash) EMIT_CHAR(' ');
                        EMIT_EMDASH();
                        if (ctx->spaced_emdash) EMIT_CHAR(' ');
                        /* Only count if we actually changed spacing */
                        if (ctx->spaced_emdash) {
                            if (old_oi != ctx->oi - 5 || next != te)
                                BUMP(FIX_CHI_EMDASH_SPACING);
                        } else {
                            if (old_oi != ctx->oi - 3 || next != te)
                                BUMP(FIX_CHI_EMDASH_SPACING);
                        }
                        {p = (( next))-1;}
                    } else {
                        EMIT_DATA(ts, te);
                    }
                }
            }}
	goto st14;
tr2:
#line 4438 "mdfix.rl"
	{te = p+1;{
                if (!ctx->editorial || ctx->no_arrow_aside) {
                    /* Arrows are notation here (A -> B pipelines, ISD node ->
                     * lowering-fn mappings), not prose asides. Pass through. */
                    EMIT_DATA(ts, te);
                } else if (ctx->do_chicago_punct) {
                    int prev = ctx->oi - 1;
                    while (prev >= 0 && (ctx->out[prev] == ' ' || ctx->out[prev] == '\t'))
                        prev--;
                    const char *next = te;
                    while (next < pe && (*next == ' ' || *next == '\t'))
                        next++;
                    if (prev >= 0 && next < pe
                        && is_dash_join_char((unsigned char)ctx->out[prev])
                        && is_dash_join_char((unsigned char)*next)) {
                        while (ctx->oi > 0
                               && (ctx->out[ctx->oi-1] == ' '
                                   || ctx->out[ctx->oi-1] == '\t'))
                            ctx->oi--;
                        if (ctx->spaced_emdash) EMIT_CHAR(' ');
                        EMIT_EMDASH();
                        if (ctx->spaced_emdash) EMIT_CHAR(' ');
                        BUMP(FIX_ARROW_ASIDE);
                        BUMP(FIX_CHI_EMDASH_SPACING);
                        {p = (( next))-1;}
                    } else {
                        EMIT_EMDASH();
                        BUMP(FIX_ARROW_ASIDE);
                    }
                } else {
                    EMIT_EMDASH();
                    BUMP(FIX_ARROW_ASIDE);
                }
            }}
	goto st14;
tr7:
#line 4431 "mdfix.rl"
	{te = p+1;{
                EMIT_DATA(ts, te);
            }}
	goto st14;
tr8:
#line 4431 "mdfix.rl"
	{{p = ((te))-1;}{
                EMIT_DATA(ts, te);
            }}
	goto st14;
tr12:
#line 4747 "mdfix.rl"
	{te = p+1;{
                if (!ctx->skip_abbrev && ctx->do_chicago_abbrev) {
                    /* Word-boundary guard */
                    int at_boundary = (ts == input)
                        || is_token_boundary_char((unsigned char)ts[-1]);
                    char next = (te < pe) ? *te : '\0';
                    if (at_boundary && next != ','
                        && next != '\0'
                        && (next == ' ' || next == '\t'
                            || isalnum((unsigned char)next)
                            || next == '"' || next == '\''
                            || next == '(')) {
                        EMIT_STR("e.g.,", 5);
                        BUMP(FIX_CHI_ABBREV_COMMA);
                    } else {
                        EMIT_DATA(ts, te);
                    }
                } else {
                    EMIT_DATA(ts, te);
                }
            }}
	goto st14;
tr15:
#line 4792 "mdfix.rl"
	{te = p+1;{
                if (!ctx->skip_abbrev && ctx->do_chicago_abbrev) {
                    int at_boundary = (ts == input)
                        || is_token_boundary_char((unsigned char)ts[-1]);
                    char next = (te < pe) ? *te : '\0';
                    /* Don't match "et algorithm" etc. */
                    if (at_boundary && next != '.'
                        && (next == '\0'
                            || is_token_boundary_char((unsigned char)next))) {
                        EMIT_STR("et al.", 6);
                        BUMP(FIX_CHI_ETAL_PERIOD);
                    } else {
                        EMIT_DATA(ts, te);
                    }
                } else {
                    EMIT_DATA(ts, te);
                }
            }}
	goto st14;
tr17:
#line 4770 "mdfix.rl"
	{te = p+1;{
                if (!ctx->skip_abbrev && ctx->do_chicago_abbrev) {
                    int at_boundary = (ts == input)
                        || is_token_boundary_char((unsigned char)ts[-1]);
                    char next = (te < pe) ? *te : '\0';
                    if (at_boundary && next != ','
                        && next != '\0'
                        && (next == ' ' || next == '\t'
                            || isalnum((unsigned char)next)
                            || next == '"' || next == '\''
                            || next == '(')) {
                        EMIT_STR("i.e.,", 5);
                        BUMP(FIX_CHI_ABBREV_COMMA);
                    } else {
                        EMIT_DATA(ts, te);
                    }
                } else {
                    EMIT_DATA(ts, te);
                }
            }}
	goto st14;
tr18:
#line 4812 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr21:
#line 4692 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR((*p));
                if (!ctx->skip_punct2 && ctx->do_chicago_punct2 && te < pe) {
                    unsigned char next = (unsigned char)*te;
                    if (should_insert_space_after_punct((unsigned char)(*p), next)) {
                        EMIT_CHAR(' ');
                        BUMP(FIX_CHI_SPACE_AFTER_PUNCT);
                    } else if (next == ' ') {
                        /* Check for multi-space run after punct */
                        const char *sp = te;
                        while (sp < pe && *sp == ' ')
                            sp++;
                        if (sp - te > 1) {
                            EMIT_CHAR(' ');
                            BUMP(FIX_CHI_SPACE_AFTER_PUNCT);
                            {p = (( sp))-1;}
                        }
                    }
                }
            }}
	goto st14;
tr25:
#line 4604 "mdfix.rl"
	{te = p+1;{
                /*
                 * Either Chicago flag answers "is this run an ellipsis?"
                 * here so the emit form cannot be assembled elsewhere.
                 */
                if (!ctx->do_chicago_punct && !ctx->do_chicago_punct2) {
                    EMIT_CHAR('.');
                } else {
                    /* Look ahead for spaced-dot pattern: ". . ." */
                    const char *look = te;
                    int dots = 1;

                    /* Try spaced dots first */
                    const char *scan = te;
                    while (scan < pe) {
                        const char *sp = scan;
                        while (sp < pe && (*sp == ' ' || *sp == '\t'))
                            sp++;
                        if (sp > scan && sp < pe && *sp == '.') {
                            dots++;
                            scan = sp + 1;
                        } else {
                            break;
                        }
                    }

                    if (dots >= 3) {
                        EMIT_STR(ELLIPSIS, 3);
                        BUMP(FIX_CHI_ELLIPSIS);
                        {p = (( scan))-1;}
                    } else {
                        /* Try consecutive dot run */
                        int run = 1;
                        look = te;
                        while (look < pe && *look == '.') {
                            run++;
                            look++;
                        }
                        if (run >= 4) {
                            EMIT_STR(ELLIPSIS, 3);
                            BUMP(FIX_CHI_ELLIPSIS);
                            {p = (( look))-1;}
                        } else {
                            EMIT_CHAR('.');
                        }
                    }
                }
            }}
	goto st14;
tr29:
#line 4812 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr32:
#line 4654 "mdfix.rl"
	{te = p;p--;{
                int run = (int)(te - ts);

                if (run > 1 && ctx->do_chicago_punct) {
                    /* Check for sentence-end before this space run */
                    int sentence_break = 0;
                    if (ctx->oi > 0) {
                        unsigned char last = (unsigned char)ctx->out[ctx->oi - 1];
                        if (is_sentence_end_char(last)) {
                            sentence_break = 1;
                        } else if ((last == '"' || last == '\''
                                    || last == ')' || last == ']')
                                   && ctx->oi > 1
                                   && is_sentence_end_char(
                                          (unsigned char)ctx->out[ctx->oi - 2])) {
                            sentence_break = 1;
                        }
                    }
                    if (sentence_break) {
                        EMIT_CHAR(' ');
                        BUMP(FIX_CHI_SENTENCE_SPACE);
                    } else {
                        EMIT_DATA(ts, te);
                    }
                } else if (!ctx->skip_punct2 && ctx->do_chicago_punct2
                           && te < pe
                           && is_punct_for_spacing((unsigned char)*te)
                           && punct_ends_a_word(te, pe)
                           && ctx->oi > 0
                           && !isspace((unsigned char)ctx->out[ctx->oi - 1])) {
                    /* Space before punctuation — drop the spaces */
                    BUMP(FIX_CHI_SPACE_BEFORE_PUNCT);
                } else {
                    EMIT_DATA(ts, te);
                }
            }}
	goto st14;
tr33:
#line 4714 "mdfix.rl"
	{te = p+1;{
                if (!ctx->skip_punct2 || !ctx->do_chicago_punct2) {
                    /* Check context for conservative swap */
                    int do_swap = 0;
                    if (ctx->oi > 0
                        && !isspace((unsigned char)ctx->out[ctx->oi - 1])) {
                        char punct = ts[1];
                        /* Conservative: only at EOL or before capital letter */
                        if (te >= pe) {
                            do_swap = 1;
                        } else if (*te == ' ') {
                            const char *sp = te;
                            while (sp < pe && *sp == ' ')
                                sp++;
                            if (sp < pe && isupper((unsigned char)*sp))
                                do_swap = 1;
                        }
                        if (do_swap && !ctx->skip_punct2 && ctx->do_chicago_punct2) {
                            EMIT_CHAR(punct);
                            EMIT_CHAR('"');
                            BUMP(FIX_CHI_QUOTE_TERMINAL_PUNCT);
                        } else {
                            EMIT_DATA(ts, te);
                        }
                    } else {
                        EMIT_DATA(ts, te);
                    }
                } else {
                    EMIT_DATA(ts, te);
                }
            }}
	goto st14;
tr35:
#line 4500 "mdfix.rl"
	{te = p;p--;{
                if (!ctx->editorial) {
                    EMIT_DATA(ts, te);
                } else {
                    EMIT_CHAR(':');
                    EMIT_CHAR('*');
                    EMIT_CHAR('*');
                    BUMP(FIX_BOLD_COLON);
                }
            }}
	goto st14;
tr36:
#line 4474 "mdfix.rl"
	{te = p+1;{
                if (!ctx->editorial) {
                    EMIT_DATA(ts, te);
                } else {
                    EMIT_CHAR(':');
                    EMIT_CHAR('*');
                    EMIT_CHAR('*');
                    EMIT_CHAR(' ');
                    BUMP(FIX_BOLD_COLON);
                }
            }}
	goto st14;
tr37:
#line 4512 "mdfix.rl"
	{te = p;p--;{
                if (!ctx->editorial) {
                    EMIT_DATA(ts, te);
                } else {
                    EMIT_CHAR(':');
                    EMIT_CHAR('*');
                    EMIT_CHAR('*');
                    BUMP(FIX_BOLD_COLON);
                }
            }}
	goto st14;
tr38:
#line 4487 "mdfix.rl"
	{te = p+1;{
                if (!ctx->editorial) {
                    EMIT_DATA(ts, te);
                } else {
                    EMIT_CHAR(':');
                    EMIT_CHAR('*');
                    EMIT_CHAR('*');
                    EMIT_CHAR(' ');
                    BUMP(FIX_BOLD_COLON);
                }
            }}
	goto st14;
tr39:
#line 4524 "mdfix.rl"
	{te = p+1;{
                /* Check context: is this between word-ish chars? */
                int prev = ctx->oi - 1;
                while (prev >= 0 && (ctx->out[prev] == ' ' || ctx->out[prev] == '\t'))
                    prev--;

                int had_space_before = (ts > input && (ts[-1] == ' ' || ts[-1] == '\t'));
                int had_space_after  = (te < pe && (*te == ' ' || *te == '\t'));

                /* Skip "--flag" patterns (space before, no space after) */
                if (!ctx->do_chicago_punct || (had_space_before && !had_space_after)) {
                    EMIT_DATA(ts, te);
                } else {
                    const char *next = te;
                    while (next < pe && (*next == ' ' || *next == '\t'))
                        next++;

                    if (prev >= 0 && next < pe
                        && is_dash_join_char((unsigned char)ctx->out[prev])
                        && is_dash_join_char((unsigned char)*next)) {
                        /* Trim trailing spaces from output */
                        while (ctx->oi > 0
                               && (ctx->out[ctx->oi-1] == ' '
                                   || ctx->out[ctx->oi-1] == '\t'))
                            ctx->oi--;
                        if (ctx->spaced_emdash) EMIT_CHAR(' ');
                        EMIT_EMDASH();
                        if (ctx->spaced_emdash) EMIT_CHAR(' ');
                        BUMP(FIX_CHI_EMDASH_SPACING);
                        /* Skip past trailing spaces in input */
                        {p = (( next))-1;}
                    } else {
                        EMIT_DATA(ts, te);
                    }
                }
            }}
	goto st14;
tr41:
#line 4431 "mdfix.rl"
	{te = p;p--;{
                EMIT_DATA(ts, te);
            }}
	goto st14;
st14:
#line 1 "NONE"
	{ts = 0;}
	if ( ++p == pe )
		goto _test_eof14;
case 14:
#line 1 "NONE"
	{ts = p;}
#line 4861 "mdfix.c"
	switch( (*p) ) {
		case -30: goto tr19;
		case 32: goto st16;
		case 33: goto tr21;
		case 34: goto st17;
		case 42: goto tr23;
		case 44: goto tr21;
		case 45: goto st21;
		case 46: goto tr25;
		case 63: goto tr21;
		case 96: goto tr26;
		case 101: goto tr27;
		case 105: goto tr28;
	}
	if ( 58 <= (*p) && (*p) <= 59 )
		goto tr21;
	goto tr18;
tr19:
#line 1 "NONE"
	{te = p+1;}
	goto st15;
st15:
	if ( ++p == pe )
		goto _test_eof15;
case 15:
#line 4887 "mdfix.c"
	switch( (*p) ) {
		case -128: goto st0;
		case -122: goto st1;
	}
	goto tr29;
st0:
	if ( ++p == pe )
		goto _test_eof0;
case 0:
	if ( (*p) == -108 )
		goto tr1;
	goto tr0;
st1:
	if ( ++p == pe )
		goto _test_eof1;
case 1:
	if ( (*p) == -110 )
		goto tr2;
	goto tr0;
st16:
	if ( ++p == pe )
		goto _test_eof16;
case 16:
	if ( (*p) == 32 )
		goto st16;
	goto tr32;
st17:
	if ( ++p == pe )
		goto _test_eof17;
case 17:
	switch( (*p) ) {
		case 44: goto tr33;
		case 46: goto tr33;
	}
	goto tr29;
tr23:
#line 1 "NONE"
	{te = p+1;}
	goto st18;
st18:
	if ( ++p == pe )
		goto _test_eof18;
case 18:
#line 4931 "mdfix.c"
	if ( (*p) == 42 )
		goto st2;
	goto tr29;
st2:
	if ( ++p == pe )
		goto _test_eof2;
case 2:
	switch( (*p) ) {
		case 32: goto st3;
		case 58: goto st20;
	}
	goto tr0;
st3:
	if ( ++p == pe )
		goto _test_eof3;
case 3:
	if ( (*p) == 58 )
		goto st19;
	goto tr0;
st19:
	if ( ++p == pe )
		goto _test_eof19;
case 19:
	if ( (*p) == 32 )
		goto tr36;
	goto tr35;
st20:
	if ( ++p == pe )
		goto _test_eof20;
case 20:
	if ( (*p) == 32 )
		goto tr38;
	goto tr37;
st21:
	if ( ++p == pe )
		goto _test_eof21;
case 21:
	if ( (*p) == 45 )
		goto tr39;
	goto tr29;
tr26:
#line 1 "NONE"
	{te = p+1;}
	goto st22;
st22:
	if ( ++p == pe )
		goto _test_eof22;
case 22:
#line 4980 "mdfix.c"
	if ( (*p) == 96 )
		goto tr40;
	goto st4;
st4:
	if ( ++p == pe )
		goto _test_eof4;
case 4:
	if ( (*p) == 96 )
		goto tr7;
	goto st4;
tr40:
#line 1 "NONE"
	{te = p+1;}
	goto st23;
st23:
	if ( ++p == pe )
		goto _test_eof23;
case 23:
#line 4999 "mdfix.c"
	if ( (*p) == 96 )
		goto st6;
	goto st5;
st5:
	if ( ++p == pe )
		goto _test_eof5;
case 5:
	if ( (*p) == 96 )
		goto st6;
	goto st5;
st6:
	if ( ++p == pe )
		goto _test_eof6;
case 6:
	if ( (*p) == 96 )
		goto tr7;
	goto st5;
tr27:
#line 1 "NONE"
	{te = p+1;}
	goto st24;
st24:
	if ( ++p == pe )
		goto _test_eof24;
case 24:
#line 5025 "mdfix.c"
	switch( (*p) ) {
		case 46: goto st7;
		case 116: goto st9;
	}
	goto tr29;
st7:
	if ( ++p == pe )
		goto _test_eof7;
case 7:
	if ( (*p) == 103 )
		goto st8;
	goto tr0;
st8:
	if ( ++p == pe )
		goto _test_eof8;
case 8:
	if ( (*p) == 46 )
		goto tr12;
	goto tr0;
st9:
	if ( ++p == pe )
		goto _test_eof9;
case 9:
	if ( (*p) == 32 )
		goto st10;
	goto tr0;
st10:
	if ( ++p == pe )
		goto _test_eof10;
case 10:
	if ( (*p) == 97 )
		goto st11;
	goto tr0;
st11:
	if ( ++p == pe )
		goto _test_eof11;
case 11:
	if ( (*p) == 108 )
		goto tr15;
	goto tr0;
tr28:
#line 1 "NONE"
	{te = p+1;}
	goto st25;
st25:
	if ( ++p == pe )
		goto _test_eof25;
case 25:
#line 5074 "mdfix.c"
	if ( (*p) == 46 )
		goto st12;
	goto tr29;
st12:
	if ( ++p == pe )
		goto _test_eof12;
case 12:
	if ( (*p) == 101 )
		goto st13;
	goto tr0;
st13:
	if ( ++p == pe )
		goto _test_eof13;
case 13:
	if ( (*p) == 46 )
		goto tr17;
	goto tr0;
	}
	_test_eof14: cs = 14; goto _test_eof; 
	_test_eof15: cs = 15; goto _test_eof; 
	_test_eof0: cs = 0; goto _test_eof; 
	_test_eof1: cs = 1; goto _test_eof; 
	_test_eof16: cs = 16; goto _test_eof; 
	_test_eof17: cs = 17; goto _test_eof; 
	_test_eof18: cs = 18; goto _test_eof; 
	_test_eof2: cs = 2; goto _test_eof; 
	_test_eof3: cs = 3; goto _test_eof; 
	_test_eof19: cs = 19; goto _test_eof; 
	_test_eof20: cs = 20; goto _test_eof; 
	_test_eof21: cs = 21; goto _test_eof; 
	_test_eof22: cs = 22; goto _test_eof; 
	_test_eof4: cs = 4; goto _test_eof; 
	_test_eof23: cs = 23; goto _test_eof; 
	_test_eof5: cs = 5; goto _test_eof; 
	_test_eof6: cs = 6; goto _test_eof; 
	_test_eof24: cs = 24; goto _test_eof; 
	_test_eof7: cs = 7; goto _test_eof; 
	_test_eof8: cs = 8; goto _test_eof; 
	_test_eof9: cs = 9; goto _test_eof; 
	_test_eof10: cs = 10; goto _test_eof; 
	_test_eof11: cs = 11; goto _test_eof; 
	_test_eof25: cs = 25; goto _test_eof; 
	_test_eof12: cs = 12; goto _test_eof; 
	_test_eof13: cs = 13; goto _test_eof; 

	_test_eof: {}
	if ( p == eof )
	{
	switch ( cs ) {
	case 15: goto tr29;
	case 0: goto tr0;
	case 1: goto tr0;
	case 16: goto tr32;
	case 17: goto tr29;
	case 18: goto tr29;
	case 2: goto tr0;
	case 3: goto tr0;
	case 19: goto tr35;
	case 20: goto tr37;
	case 21: goto tr29;
	case 22: goto tr29;
	case 4: goto tr0;
	case 23: goto tr41;
	case 5: goto tr8;
	case 6: goto tr8;
	case 24: goto tr29;
	case 7: goto tr0;
	case 8: goto tr0;
	case 9: goto tr0;
	case 10: goto tr0;
	case 11: goto tr0;
	case 25: goto tr29;
	case 12: goto tr0;
	case 13: goto tr0;
	}
	}

	}

#line 4819 "mdfix.rl"


    ctx->out[ctx->oi] = '\0';
}

/* The scanner is prose typography. Undo it if it changed the line's
 * block type — those edits are not individually reversible. */
static int apply_scanner_raw(char *line, int hits[NUM_FIXES]);

static int apply_scanner(char *line, int linenum)
{
    char before[MAX_LINE];
    enum linetype was = classify(line);
    strcpy(before, line);

    int hits[NUM_FIXES];
    memset(hits, 0, sizeof hits);
    int changed = apply_scanner_raw(line, hits);
    if (!changed)
        return 0;
    if (classify(line) != was) {
        strcpy(line, before);
        return 0;
    }
    for (int i = 0; i < NUM_FIXES; i++) {
        if (hits[i] > 0) {
            fix_counts[i] += hits[i];
            emit_diagnostic(fix_rules[i], "fix", linenum, fix_labels[i]);
            if (opt_verbose)
                fprintf(stderr, "  line %d: %s\n", linenum, fix_labels[i]);
        }
    }
    return 1;
}

static int apply_scanner_raw(char *line, int hits[NUM_FIXES])
{
    struct scan_ctx ctx;
    memset(&ctx, 0, sizeof(ctx));

    ctx.no_arrow_aside    = opt_no_arrow_aside;
    ctx.editorial         = opt_editorial;
    ctx.do_chicago_punct  = opt_chicago_punct;
    ctx.do_chicago_punct2 = opt_chicago_punct2;
    ctx.do_chicago_abbrev = opt_chicago_abbrev;
    ctx.skip_punct2       = should_skip_chicago_punct2(line);
    ctx.skip_abbrev       = should_skip_chicago_abbrev(line);
    ctx.spaced_emdash     = opt_spaced_emdash;

    int len = (int)strlen(line);
    run_scanner(&ctx, line, len);

    if (strcmp(line, ctx.out) != 0) {
        strcpy(line, ctx.out);
        memcpy(hits, ctx.fix_hits, sizeof ctx.fix_hits);
        return 1;
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * Main processing — single-pass, state-machine style
 * ═══════════════════════════════════════════════════════════════════ */

static void process(FILE *out)
{
    /*
     * Decided once, up front: front matter exists only when line 0 opens it
     * *and* a closing delimiter follows. Deciding line by line let an
     * unclosed `---` swallow the file, since nothing ever reconsidered.
     */
    const int fmatter_close = frontmatter_close_line();
    int in_frontmatter     = 0;

    mark_pipe_tables();
    struct fence_state fence = {0, 0, 0, 0, 0};
    enum raw_html_kind raw_html = RAW_HTML_NONE;

    enum linetype prev_content_type = LT_BLANK;
    int prev_was_list_ctx = 0;    /* was previous content in a list context? */
    /*
     * Content column of the enclosing list item. Indented code starts at
     * list_content_col + 4, not at margin + 4.
     *
     * Branches that consume a block and continue (fence, grid/simple table,
     * and any similar sibling) clear this only when the block starts
     * strictly left of list_content_col — that means the list item ended.
     * A block at or past that column is nested and must leave the value
     * standing. Blank lines keep the column so nested-code detection still
     * works after a blank inside an item.
     */
    int list_content_col = 0;
    int had_blank = 1;            /* start-of-file counts as separation */

    for (int i = 0; i < nlines; i++) {
        char *line = lines[i];
        enum linetype type = classify(line);

        /* YAML frontmatter: open only at line 0 when a closer exists.
         * Close at the precomputed line (--- or ...), not by LT_FMATTER —
         * classify() only tags dashes, so a Pandoc `...` closer must be
         * index-based. Later `---` is a thematic break. */
        if (!fence.active && fmatter_close > 0) {
            if (i == 0) {
                in_frontmatter = 1;
                fix_trailing_ws(line, i + 1, i);
                fprintf(out, "%s\n", line);
                prev_content_type = LT_TEXT;
                had_blank = 0;
                continue;
            }
            if (i == fmatter_close) {
                in_frontmatter = 0;
                fix_trailing_ws(line, i + 1, i);
                fprintf(out, "%s\n", line);
                prev_content_type = LT_TEXT;
                had_blank = 0;
                continue;
            }
            if (type == LT_FMATTER)
                type = LT_TEXT;
        }

        /* ── Inside frontmatter: pass through, just trim whitespace ── */
        if (in_frontmatter) {
            fix_trailing_ws(line, i + 1, i);
            fprintf(out, "%s\n", line);
            continue;
        }

        /*
         * ── Inside a raw HTML block: hands off ──
         * Runs to its own terminator; blank lines do not end it, and nothing
         * inside is prose. Checked before fences so a ``` inside a <script>
         * cannot open one.
         */
        if (raw_html != RAW_HTML_NONE) {
            fprintf(out, "%s\n", line);
            if (raw_html_line_has_end(line, raw_html))
                raw_html = RAW_HTML_NONE;
            /* Not LT_TEXT: indented code may follow a raw block with no blank. */
            prev_content_type = LT_RAWHTML;
            had_blank = 0;
            continue;
        }

        /* ── Inside code block: hands off ── */
        if (fence.active) {
            if (is_fence_closer(line, &fence)) {
                fix_fence_canonical(line, i + 1, 0);
                fence.active = 0;
                fix_trailing_ws(line, i + 1, i);
            }
            fprintf(out, "%s\n", line);
            continue;
        }

        /* ── Opening code fence ── */
        struct fence_state opener;
        if (parse_fence_opener(line, &opener)) {
            /*
             * A fence strictly left of the enclosing item's content column
             * (indent < list_content_col) ends the list; one at or past that
             * column is inside the item and leaves it standing. Without this
             * the list context leaked past the fenced block, so a later
             * four-column code block was measured against a stale threshold
             * and went back to being rewritten as prose.
             */
            if (indent_columns(line, NULL) < list_content_col) {
                prev_was_list_ctx = 0;
                list_content_col = 0;
            }
            flush_paragraph(out);
            fix_fence_canonical(line, i + 1, 1);
            opener.open_line = i + 1;
            fence = opener;
            fix_trailing_ws(line, i + 1, i);
            fprintf(out, "%s\n", line);
            /* Not LT_TEXT: indented code may follow a fence with no blank. */
            prev_content_type = LT_CODEFENCE;
            had_blank = 0;
            continue;
        }

        /*
         * ── Pandoc grid / simple table: verbatim ──
         * Column position is the structure, so no fix may change a cell's
         * width. Checked before the raw-HTML and blank branches so a grid
         * border is never mistaken for anything else.
         */
        {
            int table_end = table_block_end(i);
            if (table_end > i) {
                /*
                 * A table strictly left of the enclosing item's content column
                 * ends the list; one at or past that column is inside the item.
                 * Always clearing (the previous behaviour) made a later
                 * four-space list continuation look like margin indented code.
                 */
                if (indent_columns(line, NULL) < list_content_col) {
                    prev_was_list_ctx = 0;
                    list_content_col = 0;
                }
                flush_paragraph(out);
                for (; i < table_end; i++)
                    fprintf(out, "%s\n", lines[i]);
                i--;  /* the loop's own i++ moves past the last table line */
                prev_content_type = LT_TABLEBLOCK;
                had_blank = 0;
                continue;
            }
        }

        /* ── Opening raw HTML block ── */
        {
            enum raw_html_kind kind = raw_html_open_kind(line);
            if (kind != RAW_HTML_NONE) {
                flush_paragraph(out);
                fprintf(out, "%s\n", line);
                /* Search past the opening '<' so a one-line block —
                 * `<!-- note -->`, `<script>x()</script>`, `<!DOCTYPE html>` —
                 * closes immediately instead of swallowing the document. */
                const char *lt = strchr(line, '<');
                const char *after = lt ? lt + 1 : line + 1;
                if (!raw_html_line_has_end(after, kind))
                    raw_html = kind;
                /* Not LT_TEXT: indented code may follow with no blank. */
                prev_content_type = LT_RAWHTML;
                had_blank = 0;
                continue;
            }
        }

        /*
         * ── Setext heading ──
         * Same rules as emit_ir: column-0 underline, single text line, before
         * thematic break so `-----\n-----` is a heading. Must set
         * prev_content_type so bare indented code after the heading is
         * protected the way ATX headings already are.
         */
        if (i + 1 < nlines
            && setext_text_ok(line)
            && is_setext_underline(lines[i + 1]))
        {
            flush_paragraph(out);
            /* Title is structural but not byte-protected (same as ATX). */
            apply_scanner(line, i + 1);
            fprintf(out, "%s\n", line);
            fprintf(out, "%s\n", lines[i + 1]);
            i++;
            prev_was_list_ctx = 0;
            list_content_col = 0;
            prev_content_type = LT_HEADING;
            had_blank = 0;
            continue;
        }

        /*
         * ── Thematic break ──
         * Must beat list handling: "* * *" is both is_thematic_break and
         * find_bullet. Without this, fix_bullet rewrote the first marker to
         * "-" while --emit-ir reported a protected thematic_break — the IR
         * claimed a protection process() did not provide.
         */
        if (is_thematic_break(line)) {
            flush_paragraph(out);
            fprintf(out, "%s\n", line);
            prev_was_list_ctx = 0;
            list_content_col = 0;
            prev_content_type = LT_TEXT;
            had_blank = 0;
            continue;
        }

        /*
         * ── Link and footnote definitions ──
         * Not paragraph text: skip prose passes, and set prev so a following
         * four-column line is indented code (pandoc CodeBlock), matching
         * emit_ir's LT_REFDEF. Title continuations (quoted) ride with a
         * reference def; footnote defs take indented lines across blanks.
         */
        {
            int def = ref_def_kind(line);
            if (def) {
                int last = i;
                if (def == 1) {
                    while (last + 1 < nlines && is_ref_title_cont(lines[last + 1]))
                        last++;
                } else {
                    int j = last + 1;
                    while (j < nlines) {
                        if (is_blank(lines[j])) {
                            j++;
                            continue;
                        }
                        if (indent_columns(lines[j], NULL) < 4)
                            break;
                        last = j;
                        j++;
                    }
                }
                flush_paragraph(out);
                for (; i <= last; i++) {
                    /* Structural canonicalization only — no prose scanner. */
                    if (def == 2)
                        fix_footnote_def(lines[i], i + 1);
                    fprintf(out, "%s\n", lines[i]);
                }
                i = last;
                prev_was_list_ctx = 0;
                list_content_col = 0;
                prev_content_type = LT_REFDEF;
                had_blank = 0;
                continue;
            }
        }

        /* ── Blank line ── */
        if (type == LT_BLANK) {
            flush_paragraph(out);
            had_blank = 1;
            prev_was_list_ctx = 0;
            fprintf(out, "\n");
            continue;
        }

        /* ═══ CONTENT LINE — apply fixes ═══ */

        if (type == LT_HEADING && opt_scrivener_repair) {
            int next_idx = i + 1;
            while (next_idx < nlines && is_blank(lines[next_idx]))
                next_idx++;
            if (next_idx < nlines) {
                fix_scrivener_split_heading_emphasis(
                    line, lines[next_idx], i + 1, next_idx + 1);
            }
        }

        /*
         * Indented code: four or more columns past the enclosing content
         * column. Cannot interrupt a paragraph (that line is a lazy
         * continuation). A pipe table's rows classify as TEXT but are not a
         * paragraph, so the line after one is indented code.
         */
        if (indent_columns(line, NULL) >= list_content_col + 4
            && (had_blank || prev_content_type != LT_TEXT
                || (i > 0 && pipe_table_line[i - 1])))
        {
            type = LT_INDENTCODE;
            flush_paragraph(out);
            fprintf(out, "%s\n", line);
            prev_content_type = type;
            had_blank = 0;
            /* prev_was_list_ctx is left alone: code nested in a list item is
             * still inside that item. */
            continue;
        }

        /*
         * Determine if we're in a "list context" — the previous content
         * was a list item, or a continuation / nested block that left the
         * list flag standing (text, fence, or indented code inside an item).
         */
        int in_list_context = is_list_type(prev_content_type) || prev_was_list_ctx;

        /*
         * Fix 2: Insert blank line BEFORE list.
         * If we're entering a list from non-list content with no
         * intervening blank line, pandoc will choke — especially
         * when the preceding line ends with a colon.
         */
        if (opt_required
            && !had_blank
            && is_list_type(type)
            && blank_before_list_marker(line)
            && !in_list_context
            && prev_content_type != LT_BLANK)
        {
            flush_paragraph(out);
            if (opt_verbose)
                fprintf(stderr, "  line %d: inserted blank line before list\n",
                        i + 1);
            fprintf(out, "\n");
            record_fix(FIX_BLANK_BEFORE_LIST, i + 1);
        }

        /*
         * Fix 3: Insert blank line AFTER list.
         * If we're leaving a list into non-list content with no
         * intervening blank line, the markdown structure is ambiguous.
         * Exception: indented continuation lines are part of the list item.
         */
        if (opt_required
            && !had_blank
            && !is_list_type(type)
            && in_list_context
            && !is_list_continuation(line))
        {
            flush_paragraph(out);
            if (opt_verbose)
                fprintf(stderr, "  line %d: inserted blank line after list\n",
                        i + 1);
            fprintf(out, "\n");
            record_fix(FIX_BLANK_AFTER_LIST, i + 1);
        }

        /* Apply pre-scanner C fixers */
        fix_footnote_def(line, i + 1);
        fix_footnote_refs(line, i + 1);
        fix_pandoc_safe_links(line, i + 1);
        fix_blockquote_space(line, i + 1);

        /* Ragel scanner: arrow aside, bold-colon, Chicago punct, abbrevs */
        apply_scanner(line, i + 1);

        /* Apply post-scanner C fixers */
        fix_trailing_ws(line, i + 1, i);
        fix_bullet(line, i + 1);
        fix_heading_fmt(line, i + 1);
        fix_heading_space(line, i + 1);
        fix_heading_canonical(line, i + 1);
        if (type == LT_TEXT) {
            lint_serial_comma(line, i + 1);
            lint_chicago_numbers(line, i + 1);
        }

        /* Write the (possibly modified) line */
        if (opt_wrap_width > 0 && is_wrappable_at(line, type, i)) {
            para_lines_buf[npara++] = line;
        } else {
            flush_paragraph(out);
            fprintf(out, "%s\n", line);
        }

        /* Update list context tracking */
        if (is_list_type(type)) {
            prev_was_list_ctx = 1;
            int content_col = list_content_column(line);
            if (content_col >= 0)
                list_content_col = content_col;
        } else if (type == LT_TEXT && is_list_continuation(line)) {
            prev_was_list_ctx = 1;
            /*
             * A nested item raises list_content_col; an outdented continuation
             * of the outer item must restore the outer content column, or
             * later nested code is measured against the inner threshold and
             * rewritten as prose.
             */
            int cont_col = indent_columns(line, NULL);
            if (cont_col < list_content_col)
                list_content_col = cont_col;
        } else {
            prev_was_list_ctx = 0;
            /* Left the list: indented code is measured from the margin again. */
            list_content_col = 0;
        }

        prev_content_type = type;
        had_blank = 0;
    }

    /*
     * A fence whose closer never matched swallows the rest of the file: every
     * later line is emitted verbatim and no pass ever sees it. Silence here
     * made --canonical-lint report a clean exit 0 on a file it had largely
     * skipped, so a CI gate stopped covering anything past the first
     * mismatched delimiter. Count it as an issue so the gate fails.
     */
    if (fence.active) {
        unterminated_fence_warnings++;
        emit_diagnostic("fence.unterminated", "warning", fence.open_line,
                        "unterminated code fence");
        if (!opt_quiet) {
            fprintf(stderr,
                "  warning: unterminated code fence opened at line %d "
                "(%d '%c'); rest of file left unchecked\n",
                fence.open_line, fence.length, fence.marker);
        }
    }

    flush_paragraph(out);
}

/* ═══════════════════════════════════════════════════════════════════
 * Reporting
 * ═══════════════════════════════════════════════════════════════════ */

static void print_summary(const char *path)
{
    int total = 0;
    for (int i = 0; i < NUM_FIXES; i++)
        total += fix_counts[i];

    int nfc_rewrote = opt_normalize_nfc && non_nfc_warnings > 0;
    int lint_only = serial_comma_warnings + number_style_warnings
                    + unterminated_fence_warnings
                    + (non_nfc_warnings > 0 && !opt_normalize_nfc ? 1 : 0);

    if (total == 0 && !nfc_rewrote && lint_only == 0) {
        printf("%s: clean. Nothing to fix.\n", path);
        return;
    }

    if (total > 0) {
        printf("\n%s: %d fix%s applied\n", path, total, total == 1 ? "" : "es");
        for (int i = 0; i < NUM_FIXES; i++) {
            if (fix_counts[i] > 0)
                printf("  %-40s %d\n", fix_labels[i], fix_counts[i]);
        }
    } else if (nfc_rewrote) {
        /* Applied rewrite, not a pure lint pass — avoid "nothing to fix". */
        printf("\n%s: %d line%s normalized to NFC\n", path, non_nfc_warnings,
               non_nfc_warnings == 1 ? "" : "s");
    } else {
        /* Warnings without fixes still need a file header for the counts. */
        printf("\n%s: nothing to fix, but:\n", path);
    }
    if (serial_comma_warnings > 0) {
        printf("  %-40s %d\n",
            "serial comma warnings (lint-only)",
            serial_comma_warnings);
    }
    if (number_style_warnings > 0) {
        printf("  %-40s %d\n",
            "number style warnings (lint-only)",
            number_style_warnings);
    }
    if (unterminated_fence_warnings > 0) {
        printf("  %-40s %d\n",
            "unterminated code fence",
            unterminated_fence_warnings);
    }
    if (non_nfc_warnings > 0 && opt_normalize_nfc && total > 0) {
        printf("  %-40s %d\n", "line(s) normalized to NFC", non_nfc_warnings);
    } else if (non_nfc_warnings > 0 && !opt_normalize_nfc) {
        printf("  %-40s %d\n",
            "line(s) not NFC (--normalize-nfc fixes)",
            non_nfc_warnings);
    }
}

/* ═══════════════════════════════════════════════════════════════════
 * L5 applier — splice edits into original bytes (docs/edit-schema.md).
 * Validate strictly (I4.2); refuse introduced L2 dirt (I4.3); empty list
 * is byte-identical including CRLF (I5.1).
 * ═══════════════════════════════════════════════════════════════════ */

/* Defined with the other write-path helpers, below. */
static void fsync_parent_dir(const char *path);
static int  finish_stdout(const char *what);
static int  finalize_output(FILE **out_slot, const char *tmp_path);
static int  write_inplace_buf(const char *input_path,
                             const char *buf, size_t buflen);

#define EDITS_SCHEMA "mdtools-edits-1"
#define MAX_EDITS    100000

struct edit {
    long long start;
    long long end;
    int       order;         /* input order; tertiary sort key for ties */
    char     *replacement;   /* owned */
    char     *rule;          /* owned, may be NULL */
    char     *expect;        /* owned, may be NULL: original bytes as seen */
    /*
     * Issue #12's edit model: what kind of change this is, how sure the
     * producer is, and why. All optional and all owned.
     *
     * mdfix does not act on them — a low-confidence edit is applied exactly
     * like a high-confidence one, because the producer already decided by
     * sending it. They exist so a human reviewing `--diff` sees the same
     * judgement the producer made, rather than a bare byte range. Validating
     * them anyway is I4.2: accepted input is checked, not trusted, and a
     * typo'd `confidance` that silently vanished would be worse than useless.
     */
    char     *severity;      /* owned, may be NULL */
    char     *confidence;    /* owned, may be NULL */
    char     *explanation;   /* owned, may be NULL */
};

static struct edit edits[MAX_EDITS];
static int nedits = 0;

static void free_edit_fields(struct edit *e)
{
    free(e->replacement);
    free(e->rule);
    free(e->expect);
    free(e->severity);
    free(e->confidence);
    free(e->explanation);
}

static void free_edits(void)
{
    for (int i = 0; i < nedits; i++)
        free_edit_fields(&edits[i]);
    nedits = 0;
}

/*
 * A deliberately small JSON reader: flat objects of string, integer, boolean
 * and null. That is the whole edit record, and a general parser would be more
 * code to audit than the format needs. Anything nested is refused rather than
 * skipped, so an unsupported record fails loudly instead of half-parsing.
 */
static const char *json_skip_ws(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')
        p++;
    return p;
}

static int json_hex4(const char *p, unsigned *out)
{
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9')       v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f')  v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F')  v |= (unsigned)(c - 'A' + 10);
        else return 0;
    }
    *out = v;
    return 1;
}

static int utf8_encode(unsigned cp, char *out)
{
    if (cp < 0x80)    { out[0] = (char)cp; return 1; }
    if (cp < 0x800)   { out[0] = (char)(0xC0 | (cp >> 6));
                        out[1] = (char)(0x80 | (cp & 0x3F)); return 2; }
    if (cp < 0x10000) { out[0] = (char)(0xE0 | (cp >> 12));
                        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
                        out[2] = (char)(0x80 | (cp & 0x3F)); return 3; }
    out[0] = (char)(0xF0 | (cp >> 18));
    out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    out[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

/* Parse a JSON string into a fresh buffer. Returns the position after the
 * closing quote, or NULL. */
static const char *json_string(const char *p, char **out)
{
    if (*p != '"')
        return NULL;
    p++;
    size_t cap = 64, n = 0;
    char *buf = malloc(cap);
    if (!buf)
        return NULL;
    while (*p && *p != '"') {
        if (n + 5 >= cap) {
            cap *= 2;
            char *bigger = realloc(buf, cap);
            if (!bigger) { free(buf); return NULL; }
            buf = bigger;
        }
        if (*p != '\\') {
            buf[n++] = *p++;
            continue;
        }
        p++;
        switch (*p) {
        case '"':  buf[n++] = '"';  p++; break;
        case '\\': buf[n++] = '\\'; p++; break;
        case '/':  buf[n++] = '/';  p++; break;
        case 'b':  buf[n++] = '\b'; p++; break;
        case 'f':  buf[n++] = '\f'; p++; break;
        case 'n':  buf[n++] = '\n'; p++; break;
        case 'r':  buf[n++] = '\r'; p++; break;
        case 't':  buf[n++] = '\t'; p++; break;
        case 'u': {
            unsigned cp;
            if (!json_hex4(p + 1, &cp)) { free(buf); return NULL; }
            p += 5;
            if (cp >= 0xD800 && cp <= 0xDBFF && p[0] == '\\' && p[1] == 'u') {
                unsigned lo;
                if (json_hex4(p + 2, &lo) && lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    p += 6;
                }
            }
            if (cp >= 0xD800 && cp <= 0xDFFF) { free(buf); return NULL; }
            n += (size_t)utf8_encode(cp, buf + n);
            break;
        }
        default: free(buf); return NULL;
        }
    }
    if (*p != '"') { free(buf); return NULL; }
    buf[n] = '\0';
    *out = buf;
    return p + 1;
}

/* One flat JSON object. Recognized keys are stored; unknown keys are skipped
 * so the format can grow, per I4.4. */
static int parse_edit_object(const char *line, struct edit *e,
                             char **kind, long long *bytes, char **schema)
{
    const char *p = json_skip_ws(line);
    if (*p != '{')
        return 0;
    p = json_skip_ws(p + 1);
    if (*p == '}')
        return 1;

    for (;;) {
        char *key = NULL;
        p = json_string(p, &key);
        if (!p) return 0;
        p = json_skip_ws(p);
        if (*p != ':') { free(key); return 0; }
        p = json_skip_ws(p + 1);

        if (*p == '"') {
            char *val = NULL;
            p = json_string(p, &val);
            if (!p) { free(key); return 0; }
            char **slot = NULL;
            if (e && strcmp(key, "replacement") == 0) slot = &e->replacement;
            else if (e && strcmp(key, "rule") == 0)   slot = &e->rule;
            else if (e && strcmp(key, "expect") == 0) slot = &e->expect;
            else if (e && strcmp(key, "severity") == 0) slot = &e->severity;
            else if (e && strcmp(key, "confidence") == 0)
                slot = &e->confidence;
            else if (e && strcmp(key, "explanation") == 0)
                slot = &e->explanation;
            else if (kind && strcmp(key, "kind") == 0)     slot = kind;
            else if (schema && strcmp(key, "schema") == 0) slot = schema;
            if (slot) { free(*slot); *slot = val; } else free(val);
        } else if ((*p >= '0' && *p <= '9') || *p == '-') {
            char *endp = NULL;
            long long v = strtoll(p, &endp, 10);
            if (endp == p) { free(key); return 0; }
            if (e && strcmp(key, "start") == 0)      e->start = v;
            else if (e && strcmp(key, "end") == 0)   e->end = v;
            else if (bytes && strcmp(key, "bytes") == 0) *bytes = v;
            p = endp;
        } else if (strncmp(p, "true", 4) == 0)  { p += 4; }
        else if (strncmp(p, "false", 5) == 0)   { p += 5; }
        else if (strncmp(p, "null", 4) == 0)    { p += 4; }
        else { free(key); return 0; }   /* nested values are refused */

        free(key);
        p = json_skip_ws(p);
        if (*p == ',') { p = json_skip_ws(p + 1); continue; }
        if (*p == '}') return 1;
        return 0;
    }
}

static char *read_stream(FILE *fp, long long *out_len)
{
    size_t cap = 65536, n = 0;
    char *buf = malloc(cap);
    if (!buf)
        return NULL;
    size_t got;
    while ((got = fread(buf + n, 1, cap - n - 1, fp)) > 0) {
        n += got;
        if (n + 1 >= cap) {
            cap *= 2;
            char *bigger = realloc(buf, cap);
            if (!bigger) { free(buf); return NULL; }
            buf = bigger;
        }
    }
    buf[n] = '\0';
    *out_len = (long long)n;
    return buf;
}

/* Load edits from stdin. Header `bytes` is cheap staleness detection. */
static int load_edits(const char *path, long long file_len)
{
    long long len = 0;
    char *text = read_stream(stdin, &len);
    if (!text) {
        fprintf(stderr, "error: reading edits: out of memory\n");
        return 1;
    }

    int rc = 0;
    int lineno = 0;
    char *save = text;
    for (char *line = text; line && *line; ) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        lineno++;
        const char *trimmed = json_skip_ws(line);
        if (*trimmed == '\0') {
            line = nl ? nl + 1 : NULL;
            continue;
        }

        if (nedits >= MAX_EDITS) {
            fprintf(stderr, "error: more than %d edits\n", MAX_EDITS);
            rc = 1;
            break;
        }

        struct edit e = {0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL};
        char *kind = NULL, *schema = NULL;
        long long bytes = -1;
        if (!parse_edit_object(trimmed, &e, &kind, &bytes, &schema)) {
            fprintf(stderr, "error: edit line %d is not a flat JSON object\n",
                    lineno);
            free_edit_fields(&e);
            free(kind); free(schema);
            rc = 1;
            break;
        }

        if (kind && strcmp(kind, "edits") == 0) {
            if (schema && strcmp(schema, EDITS_SCHEMA) != 0) {
                fprintf(stderr,
                    "error: edit schema '%s' is not '%s'\n",
                    schema, EDITS_SCHEMA);
                rc = 1;
            } else if (bytes >= 0 && bytes != file_len) {
                fprintf(stderr,
                    "error: edits were computed against %lld bytes but %s is "
                    "%lld bytes. The file changed; re-run --emit-ir.\n",
                    bytes, path, file_len);
                rc = 1;
            }
            free_edit_fields(&e);
            free(kind); free(schema);
            if (rc) break;
            line = nl ? nl + 1 : NULL;
            continue;
        }
        free(kind);
        free(schema);

        if (!e.replacement)
            e.replacement = strdup("");
        if (!e.replacement) { rc = 1; break; }
        e.order = nedits;
        edits[nedits++] = e;
        line = nl ? nl + 1 : NULL;
    }
    free(save);
    return rc;
}

static int edit_cmp(const void *a, const void *b)
{
    const struct edit *x = a, *y = b;
    if (x->start < y->start) return -1;
    if (x->start > y->start) return 1;
    if (x->end   < y->end)   return -1;
    if (x->end   > y->end)   return 1;
    /* Stable order for same-offset inserts (qsort is not stable). */
    if (x->order < y->order) return -1;
    if (x->order > y->order) return 1;
    return 0;
}

/* True if offset is at a UTF-8 character boundary (or at EOF). */
static int utf8_is_boundary(const char *s, long long off, long long len)
{
    if (off <= 0 || off >= len)
        return 1;
    return (((unsigned char)s[off]) & 0xC0) != 0x80;
}

/* I4.2: bounds, ordering, overlap, encoding, and staleness. */
/*
 * The vocabularies for `severity` and `confidence` (docs/edit-schema.md).
 *
 * Closed sets, and deliberately not numbers. A float confidence invites a
 * precision nobody has calibrated — 0.82 means nothing a reader can check,
 * while "medium" is a claim a producer can defend. mdlinks already reasons in
 * exactly these steps: an exact identifier match is not the same kind of
 * answer as a nearest-neighbour guess, and there is no ratio between them.
 *
 * Refusing an unknown value rather than ignoring it is I4.2. A producer that
 * writes `"confidence":"certain"` has a bug; discovering it here costs one
 * error message, and discovering it because a review step silently stopped
 * filtering costs a wrong edit in the file.
 */
static const char *edit_severities[] = {"error", "warning", "info", NULL};
static const char *edit_confidences[] = {"high", "medium", "low", NULL};

static int in_vocabulary(const char *value, const char *const *allowed)
{
    for (int i = 0; allowed[i]; i++)
        if (strcmp(value, allowed[i]) == 0)
            return 1;
    return 0;
}

static void vocabulary_error(int index, const char *field, const char *value,
                             const char *const *allowed)
{
    fprintf(stderr, "error: edit %d has %s \"%s\"; expected one of",
            index + 1, field, value);
    for (int i = 0; allowed[i]; i++)
        fprintf(stderr, "%s %s", i ? "," : "", allowed[i]);
    fputs(".\n", stderr);
}

static int validate_edits(const char *src, long long len)
{
    qsort(edits, (size_t)nedits, sizeof edits[0], edit_cmp);

    long long prev_end = 0;
    for (int i = 0; i < nedits; i++) {
        struct edit *e = &edits[i];
        if (e->start < 0 || e->end < e->start || e->end > len) {
            fprintf(stderr,
                "error: edit %d spans [%lld,%lld), outside the file's %lld "
                "bytes\n", i + 1, e->start, e->end, len);
            return 1;
        }
        if (!utf8_is_boundary(src, e->start, len)
            || !utf8_is_boundary(src, e->end, len)) {
            fprintf(stderr,
                "error: edit %d spans [%lld,%lld), which cuts a multi-byte "
                "UTF-8 character\n", i + 1, e->start, e->end);
            return 1;
        }
        if (i > 0 && e->start < prev_end) {
            fprintf(stderr,
                "error: edit %d starts at %lld, inside the previous edit "
                "which ends at %lld. Overlapping edits are refused.\n",
                i + 1, e->start, prev_end);
            return 1;
        }
        const char *why = NULL;
        if (utf8_first_bad(e->replacement, (int)strlen(e->replacement), &why) >= 0) {
            fprintf(stderr, "error: edit %d replacement is not valid UTF-8: "
                    "%s\n", i + 1, why);
            return 1;
        }
        if (e->severity && !in_vocabulary(e->severity, edit_severities)) {
            vocabulary_error(i, "severity", e->severity, edit_severities);
            return 1;
        }
        if (e->confidence && !in_vocabulary(e->confidence, edit_confidences)) {
            vocabulary_error(i, "confidence", e->confidence, edit_confidences);
            return 1;
        }
        /* `explanation` is prose for a human, so there is nothing to check
         * beyond the UTF-8 every accepted string already gets. */
        if (e->explanation
            && utf8_first_bad(e->explanation, (int)strlen(e->explanation),
                              &why) >= 0) {
            fprintf(stderr, "error: edit %d explanation is not valid UTF-8: "
                    "%s\n", i + 1, why);
            return 1;
        }
        if (e->expect) {
            long long n = (long long)strlen(e->expect);
            if (n != e->end - e->start
                || memcmp(src + e->start, e->expect, (size_t)n) != 0) {
                fprintf(stderr,
                    "error: edit %d expected different bytes at [%lld,%lld). "
                    "The file changed since the spans were computed.\n",
                    i + 1, e->start, e->end);
                return 1;
            }
        }
        prev_end = e->end;
    }
    return 0;
}

static void splice_edits(FILE *out, const char *src, long long len)
{
    long long cursor = 0;
    for (int i = 0; i < nedits; i++) {
        if (edits[i].start > cursor)
            fwrite(src + cursor, 1, (size_t)(edits[i].start - cursor), out);
        fputs(edits[i].replacement, out);
        cursor = edits[i].end;
    }
    if (cursor < len)
        fwrite(src + cursor, 1, (size_t)(len - cursor), out);
}

/* Line number (1-based) of the byte at `off`, and the offset of that line. */
static int line_at(const char *src, long long off, long long *line_start)
{
    int line = 1;
    long long start = 0;
    for (long long i = 0; i < off; i++) {
        if (src[i] == '\n') {
            line++;
            start = i + 1;
        }
    }
    *line_start = start;
    return line;
}

/* Offset just past the newline that ends the line containing `off`. */
static long long line_end_after(const char *src, long long len, long long off)
{
    long long i = off;
    while (i < len && src[i] != '\n')
        i++;
    return i < len ? i + 1 : len;
}

/*
 * Last byte included by half-open [start, end). Empty inserts (start == end)
 * use start so the line of the insertion point is still covered.
 */
static long long span_last_byte(long long start, long long end)
{
    return end > start ? end - 1 : start;
}

static void print_diff_lines(const char *text, long long len, char marker)
{
    long long i = 0;
    while (i < len) {
        long long j = i;
        while (j < len && text[j] != '\n')
            j++;
        printf("%c %.*s\n", marker, (int)(j - i), text + i);
        i = j < len ? j + 1 : len;
    }
    /* A range with no trailing newline is the file's last line; say so
     * rather than let the diff imply one that is not there. */
    if (len > 0 && text[len - 1] != '\n')
        printf("\\ No newline at end of file\n");
}

/*
 * Show what the edits would do, and write nothing (issue #12's `--diff`).
 *
 * Not a general diff: the edit list already says exactly which bytes change,
 * so there is nothing to infer and no algorithm to get wrong. Each group of
 * edits that lands on the same lines becomes one hunk of those lines before
 * and after — which is also what makes the annotation possible. `git diff`
 * can show the bytes; only this can say *which rule* claimed them and how
 * sure it was.
 *
 * Edits are already sorted and non-overlapping by the time this runs.
 * Returns 0, or 1 on I/O failure.
 */
static int print_edit_diff(const char *path, const char *src, long long len)
{
    int i = 0;
    while (i < nedits) {
        long long first_line_start = 0;
        int first_line = line_at(src, edits[i].start, &first_line_start);
        long long stop = line_end_after(src, len,
            span_last_byte(edits[i].start, edits[i].end));

        /* Extend the group while the next edit falls inside the lines this
         * hunk already covers. Two fixes on one line are one hunk; printing
         * the line twice, each time showing only one of the two changes,
         * would show a state that never exists. */
        int j = i + 1;
        while (j < nedits && edits[j].start < stop) {
            stop = line_end_after(src, len,
                span_last_byte(edits[j].start, edits[j].end));
            j++;
        }

        int count = j - i;
        printf("@@ %s:%d @@ %d edit%s\n", path, first_line,
               count, count == 1 ? "" : "s");
        for (int k = i; k < j; k++) {
            const struct edit *e = &edits[k];
            printf("#  %s", e->rule ? e->rule : "(no rule)");
            if (e->severity)
                printf(" [%s]", e->severity);
            if (e->confidence)
                printf(" confidence: %s", e->confidence);
            putchar('\n');
            if (e->explanation)
                printf("#  %s\n", e->explanation);
        }

        print_diff_lines(src + first_line_start, stop - first_line_start, '-');

        /* The same byte range with this group's edits spliced in. Built here
         * rather than diffed out of the whole result: the hunk must show the
         * lines these edits touch, not whatever a line-matching heuristic
         * decided lines up. */
        long long cursor = first_line_start;
        char *after = NULL;
        size_t after_len = 0;
        FILE *mem = open_memstream(&after, &after_len);
        if (!mem) {
            fprintf(stderr, "error: cannot buffer a --diff hunk\n");
            return 1;
        }
        for (int k = i; k < j; k++) {
            if (edits[k].start > cursor)
                fwrite(src + cursor, 1,
                       (size_t)(edits[k].start - cursor), mem);
            fputs(edits[k].replacement, mem);
            cursor = edits[k].end;
        }
        if (cursor < stop)
            fwrite(src + cursor, 1, (size_t)(stop - cursor), mem);
        fclose(mem);
        print_diff_lines(after, (long long)after_len, '+');
        free(after);
        i = j;
    }
    return 0;
}

/*
 * Count how many required (L2) repairs `text` would receive. Forces L2-only
 * flags so CLI options cannot weaken or widen the gate. Returns -1 if the
 * check cannot run (invalid UTF-8, I/O, overlong line) — fail closed.
 */
static int count_required_repairs(const char *text, long long len)
{
    if (len < 0 || len > (long long)INT_MAX)
        return -1;
    const char *why = NULL;
    if (utf8_first_bad(text, (int)len, &why) >= 0)
        return -1;
    if (len == 0)
        return 0;

    FILE *mem = fmemopen((void *)text, (size_t)len, "r");
    if (!mem)
        return -1;

    int saved_required = opt_required;
    int saved_editorial = opt_editorial, saved_ws = opt_trail_ws;
    int saved_wrap = opt_wrap_width, saved_quiet = opt_quiet;
    int saved_verbose = opt_verbose, saved_diagnostics = opt_diagnostics;
    int saved_chi = opt_chicago_punct, saved_chi2 = opt_chicago_punct2;
    int saved_abbrev = opt_chicago_abbrev;
    int saved_fn = opt_footnote_canonical, saved_head = opt_heading_canonical;
    int saved_fence = opt_fence_canonical, saved_links = opt_pandoc_safe_links;
    int saved_scriv = opt_scrivener_repair, saved_em = opt_spaced_emdash;
    int saved_serial = opt_serial_comma_lint, saved_num = opt_chicago_number_lint;
    int saved_no_arrow = opt_no_arrow_aside;

    opt_required = 1;
    opt_editorial = 0;
    opt_trail_ws = 0;
    opt_wrap_width = 0;
    opt_chicago_punct = 0;
    opt_chicago_punct2 = 0;
    opt_chicago_abbrev = 0;
    opt_footnote_canonical = 0;
    opt_heading_canonical = 0;
    opt_fence_canonical = 0;
    opt_pandoc_safe_links = 0;
    opt_scrivener_repair = 0;
    opt_spaced_emdash = 0;
    opt_serial_comma_lint = 0;
    opt_chicago_number_lint = 0;
    opt_no_arrow_aside = 1;
    opt_quiet = 1;
    opt_verbose = 0;
    /* Internal dirt check must not leak JSONL for temp buffers. */
    opt_diagnostics = 0;
    memset(fix_counts, 0, sizeof fix_counts);
    /* read_all counts non-NFC lines; the temp buffer's count is not the
     * user's file, so it must not reach the summary. */
    int saved_nfc_warnings = non_nfc_warnings;

    int dirty = -1;
    if (read_all(mem) == 0) {
        FILE *sink = fopen("/dev/null", "w");
        if (sink) {
            process(sink);
            fclose(sink);
            dirty = 0;
            for (int i = 0; i < NUM_FIXES; i++)
                dirty += fix_counts[i];
        }
        free_lines();
    }
    fclose(mem);

    opt_required = saved_required;
    opt_editorial = saved_editorial;
    opt_trail_ws = saved_ws;
    opt_wrap_width = saved_wrap;
    opt_chicago_punct = saved_chi;
    opt_chicago_punct2 = saved_chi2;
    opt_chicago_abbrev = saved_abbrev;
    opt_footnote_canonical = saved_fn;
    opt_heading_canonical = saved_head;
    opt_fence_canonical = saved_fence;
    opt_pandoc_safe_links = saved_links;
    opt_scrivener_repair = saved_scriv;
    opt_spaced_emdash = saved_em;
    opt_serial_comma_lint = saved_serial;
    opt_chicago_number_lint = saved_num;
    opt_no_arrow_aside = saved_no_arrow;
    opt_quiet = saved_quiet;
    opt_verbose = saved_verbose;
    opt_diagnostics = saved_diagnostics;
    non_nfc_warnings = saved_nfc_warnings;
    memset(fix_counts, 0, sizeof fix_counts);
    return dirty;
}

static int apply_edits_file(const char *input_path, const char *output_path)
{
    FILE *in = fopen(input_path, "rb");
    if (!in) {
        fprintf(stderr, "Can't open '%s': ", input_path);
        perror(NULL);
        return 1;
    }
    long long len = 0;
    char *src = read_stream(in, &len);
    fclose(in);
    if (!src) {
        fprintf(stderr, "error: reading %s: out of memory\n", input_path);
        return 1;
    }

    /* L1 still applies to the applier: the same encoding contract as #53. */
    const char *why = NULL;
    long long bad = utf8_first_bad(src, (int)len, &why);
    if (bad >= 0) {
        fprintf(stderr,
            "error: %s is not valid UTF-8 at byte offset %lld: %s.\n",
            input_path, bad, why);
        free(src);
        return 1;
    }

    nedits = 0;
    if (load_edits(input_path, len) != 0 || validate_edits(src, len) != 0) {
        free_edits();
        free(src);
        return 1;
    }

    /* Buffer first so I4.3 can refuse before any write. */
    char *tmpbuf = NULL;
    size_t tmplen = 0;
    FILE *mem = open_memstream(&tmpbuf, &tmplen);
    if (!mem) {
        fprintf(stderr, "error: cannot buffer the result\n");
        free_edits();
        free(src);
        return 1;
    }
    splice_edits(mem, src, len);
    fclose(mem);

    /* I4.3: refuse only dirt the *edits introduced*. Empty list is I5.1 —
     * always identity, even on an already-dirty manuscript. */
    if (nedits > 0) {
        int before = count_required_repairs(src, len);
        int after = count_required_repairs(tmpbuf, (long long)tmplen);
        if (before < 0 || after < 0) {
            fprintf(stderr,
                "error: cannot validate the spliced result of %s against the "
                "required repairs (invalid UTF-8 or unreadable). Refused.\n",
                input_path);
            free(tmpbuf);
            free_edits();
            free(src);
            return 1;
        }
        if (after > before) {
            fprintf(stderr,
                "error: applying these edits would leave %s needing a required "
                "repair, so they are refused rather than silently fixed "
                "(architecture I4.3). Run mdfix on the result to see what.\n",
                input_path);
            free(tmpbuf);
            free_edits();
            free(src);
            return 1;
        }
    }

    int rc = 0;
    if (opt_diff) {
        /* Preview only. Deliberately checked after I4.3 above, so a diff
         * never shows a change the applier would go on to refuse. */
        if (print_edit_diff(input_path, src, len) != 0)
            rc = 1;
        if (finish_stdout("diff") != 0)
            rc = 1;
        if (!opt_quiet)
            fprintf(stderr, "%s: %d edit%s, nothing written\n",
                    input_path, nedits, nedits == 1 ? "" : "s");
    } else if (opt_dryrun) {
        if (!opt_quiet)
            fprintf(stderr, "%s: would apply %d edit%s (dry run)\n",
                    input_path, nedits, nedits == 1 ? "" : "s");
    } else if (opt_inplace) {
        rc = write_inplace_buf(input_path, tmpbuf, tmplen);
    } else if (output_path) {
        FILE *out = fopen(output_path, "w");
        if (!out) {
            fprintf(stderr, "Can't open output '%s': ", output_path);
            perror(NULL);
            rc = 1;
        } else {
            fwrite(tmpbuf, 1, tmplen, out);
            if (finalize_output(&out, output_path) != 0)
                rc = 1;
        }
    } else {
        fwrite(tmpbuf, 1, tmplen, stdout);
        if (finish_stdout("result") != 0)
            rc = 1;
    }

    if (!opt_quiet && rc == 0 && !opt_dryrun && !opt_diff)
        fprintf(stderr, "%s: applied %d edit%s\n",
                input_path, nedits, nedits == 1 ? "" : "s");

    free(tmpbuf);
    free_edits();
    free(src);
    return rc;
}

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options] input.md [output.md]\n"
        "       %s [options] -i|-n file.md [file2.md ...]\n"
        "\n"
        "Markdown auto-fixer. Fixes the crap that linter.py complains about.\n"
        "\n"
        "Options:\n"
        "  -i    Edit in-place (creates .bak backup)\n"
        "  -n    Dry run — count fixes without writing anything\n"
        "  -v    Verbose — report every fix to stderr\n"
        "  -q    Quiet — no summary, just do it\n"
        "  -w    Normalize trailing whitespace (collapse to max 1 space;\n"
        "        preserves markdown line-break semantics)\n"
        "  --chicago-punct\n"
        "        Chicago punctuation fixes (em-dash spacing, ellipsis,\n"
        "        sentence double-space collapse)\n"
        "  --chicago-punct-2\n"
        "        Additional Chicago punctuation fixes (punctuation spacing,\n"
        "        simple quote-final period/comma placement)\n"
        "  --serial-comma-lint\n"
        "        Warn-only lint for likely missing Oxford commas\n"
        "  --chicago-abbrev\n"
        "        Chicago abbreviation fixes (e.g./i.e. commas, et al. period)\n"
        "  --chicago-number-lint\n"
        "        Warn-only Chicago number-style checks\n"
        "  --canonical\n"
        "        Enable full canonical Markdown profile (safe passes)\n"
        "  --canonical-lint\n"
        "        Canonical gate mode: fail if file is not canonical\n"
        "  --diagnostics\n"
        "        Emit findings as JSONL on stderr: path, byte span, line,\n"
        "        stable rule id, severity. See docs/diagnostics.md\n"
        "  --apply-edits\n"
        "        Read byte-span edits as JSONL on stdin and splice them into\n"
        "        the file. Untouched bytes are preserved exactly; overlapping\n"
        "        or out-of-range edits are refused. See docs/edit-schema.md\n"
        "  --diff\n"
        "        With --apply-edits, print what the edits would change and\n"
        "        write nothing. Each hunk names the rules that claimed it\n"
        "  --editorial\n"
        "        Editorial passes: bullet style, emphasis in headings,\n"
        "        bold colons, arrow asides, blockquote spacing.\n"
        "        Implied by --canonical and --technical\n"
        "  --no-required\n"
        "        Disable the required (L2) repairs. Output is then not\n"
        "        guaranteed Pandoc-readable; for inspection, not for writing\n"
        "  --emit-ir\n"
        "        Emit the structural IR as JSONL on stdout and write nothing.\n"
        "        Byte spans slice the input exactly; see docs/ir-schema.md\n"
        "  --normalize-nfc\n"
        "        Rewrite text to Unicode NFC. mdfix always *reports* non-NFC\n"
        "        input (rule unicode.non-nfc); this asks it to fix it, which\n"
        "        changes byte offsets and heading anchors\n"
        "  --footnote-canonical\n"
        "        Normalize footnote refs/defs to canonical style\n"
        "  --heading-canonical\n"
        "        Remove trailing heading hashes (spacing is required, R3)\n"
        "  --fence-canonical\n"
        "        Normalize code fence delimiter lines\n"
        "  --pandoc-safe-links\n"
        "        Wrap bare http(s) URLs in <...> for Pandoc\n"
        "  --scrivener-repair\n"
        "        Repair split heading emphasis across blocks\n"
        "  --spaced-emdash\n"
        "        Preserve spaces around em-dashes (word — word, not word—word)\n"
        "  --wrap[=N]\n"
        "        Hard-wrap paragraph text to N columns (default: 78)\n"
        "  --technical\n"
        "        Technical docs profile: --canonical + --spaced-emdash + --wrap=78\n"
        "  -h    This help\n"
        "\n"
        "Required repairs (on by default; --no-required disables).\n"
        "Without these Pandoc reads the document as something else —\n"
        "see docs/transforms.md:\n"
        "  R1. Blank line before lists        (else the list is swallowed\n"
        "                                      into the paragraph)\n"
        "  R2. Blank line after lists         (else the next paragraph is\n"
        "                                      swallowed into the item)\n"
        "  R3. Space after the ATX marker     (#Title is a paragraph,\n"
        "                                      not a heading)\n"
        "\n"
        "Fixes (opt-in with --editorial):\n"
        "  1. Bullet markers normalized to -  (linter: list_bullet_style)\n"
        "  2. Bold/italic stripped from heads  (linter: header_formatting)\n"
        "  3. Bold colons moved inside tags   (**Term**: → **Term:**)\n"
        "  4. Arrow asides converted to em-dash (→ → —)\n"
        "  5. Space added after blockquote    (>Text → > Text)\n"
        "\n"
        "Fix (opt-in with -w):\n"
        "  6. Trailing whitespace normalized  (collapse multiple spaces to one)\n"
        "\n"
        "Fixes (opt-in with --chicago-punct):\n"
        "  7. Em-dash spacing normalized      (word -- word → word—word)\n"
        "  8. Ellipsis normalized             (. . . or .... → …; also --chicago-punct-2)\n"
        "  9. Sentence double-space collapsed (\"End.  Next\" → \"End. Next\")\n"
        "\n"
        "Fixes (opt-in with --chicago-punct-2):\n"
        " 10. Remove space before punctuation (word , -> word,)\n"
        " 11. Normalize space after ,;:?!     (\"Hi,there\" -> \"Hi, there\")\n"
        " 12. Move . and , inside quotes      (\"word\". -> \"word.\")\n"
        "\n"
        "Lint (opt-in with --serial-comma-lint):\n"
        "  Warn on likely missing serial commas in simple lists\n"
        "\n"
        "Fixes (opt-in with --chicago-abbrev):\n"
        " 13. Normalize comma after e.g./i.e. (e.g. text -> e.g., text)\n"
        " 14. Enforce period in et al.       (et al -> et al.)\n"
        "\n"
        "Fixes (opt-in with --footnote-canonical):\n"
        " 15. Normalize footnote refs        ([^ 1 ] -> [^1])\n"
        " 16. Normalize footnote defs        ([^1]: text)\n"
        "\n"
        "Fixes (opt-in with --heading-canonical):\n"
        " 17. Remove trailing heading hashes (## Title ## -> ## Title)\n"
        "      (heading spacing is now a required repair, R3)\n"
        "\n"
        "Fixes (opt-in with --fence-canonical):\n"
        " 18. Normalize fence delimiters     (opening/closing fence lines)\n"
        "\n"
        "Fixes (opt-in with --pandoc-safe-links):\n"
        " 19. Wrap bare URLs as autolinks    (https://x -> <https://x>)\n"
        "\n"
        "Fixes (opt-in with --scrivener-repair):\n"
        " 20. Repair split heading emphasis  (# *Head ... tail* -> # Head ... tail)\n"
        "\n"
        "Lint (opt-in with --chicago-number-lint):\n"
        "  Warn on likely Chicago number-style issues in prose\n"
        "\n"
        "Fixes (opt-in with --spaced-emdash):\n"
        " 21. Em-dashes keep surrounding spaces (word — word, not word—word)\n"
        "\n"
        "Fixes (opt-in with --wrap[=N]):\n"
        " 22. Hard-wrap paragraph text at N columns (default 78)\n"
        "     Skips headings, lists, tables, code blocks, blockquotes\n"
        "\n"
        "Profile:\n"
        "  --canonical enables: --editorial, -w, --chicago-punct, --chicago-punct-2,\n"
        "  --chicago-abbrev, --footnote-canonical,\n"
        "  --heading-canonical, --fence-canonical\n"
        "  --technical enables: --canonical, --spaced-emdash, --wrap=78\n"
        "  --canonical-lint runs --canonical in no-write gate mode and exits\n"
        "  nonzero if any fix or lint warning is detected\n"
        "\n"
        "If no output.md and no -i, you need -n (or --canonical-lint).\n",
        prog, prog);
}

/* ═══════════════════════════════════════════════════════════════════
 * Process one file
 * ═══════════════════════════════════════════════════════════════════ */

/* Byte-compare two files. Returns 1 if identical, 0 if different or
 * unreadable. Used to decide whether an in-place run actually changed
 * anything — fix_counts alone misses uncounted normalizations
 * (CRLF stripping, re-wrapping), which the backup restore used to
 * silently revert. */
static int files_identical(const char *path_a, const char *path_b)
{
    FILE *a = fopen(path_a, "rb");
    FILE *b = fopen(path_b, "rb");
    int same = (a && b);
    while (same) {
        int ca = getc(a), cb = getc(b);
        if (ca != cb) same = 0;
        else if (ca == EOF) break;
    }
    if (a) fclose(a);
    if (b) fclose(b);
    return same;
}

/*
 * Build the backup path: always "<input>.bak", always the immediately
 * preceding version.
 *
 * An earlier revision hunted for a free name (.bak, then .bak.1, .bak.2, …)
 * so it would never clobber anything. That inverted the contract -i
 * documents: after four edits, .bak held the *oldest* preimage, so the
 * conventional undo `mv doc.md.bak doc.md` silently restored a version four
 * edits stale. It also littered a tree on any repeat run and refused to fix
 * the file at all once 10000 names were taken.
 *
 * A backup that is not the previous version is worse than no backup, because
 * it looks like one. Overwriting the previous .bak is what a backup is for.
 */
static int build_backup_path(const char *input_path, char *bak, size_t bak_sz)
{
    int n = snprintf(bak, bak_sz, "%s.bak", input_path);
    return (n < 0 || (size_t)n >= bak_sz) ? -1 : 0;
}

/*
 * fsync the directory containing path, so a completed rename survives power
 * loss. Best-effort: a filesystem that refuses to open or sync a directory is
 * not a reason to fail an edit that already succeeded.
 */
static void fsync_parent_dir(const char *path)
{
    char dir[PATH_MAX];
    int n = snprintf(dir, sizeof(dir), "%s", path);
    if (n < 0 || (size_t)n >= sizeof(dir))
        return;

    char *slash = strrchr(dir, '/');
    if (slash == dir)
        dir[1] = '\0';          /* "/file" -> "/" */
    else if (slash)
        *slash = '\0';
    else
        snprintf(dir, sizeof(dir), ".");

    int dfd = open(dir, O_RDONLY);
    if (dfd < 0)
        return;
    (void)fsync(dfd);
    close(dfd);
}

/*
 * Finish writing to stdout, and say so if any of it failed.
 *
 * `fflush` alone is not enough: a write that already failed has drained the
 * buffer, so there is nothing left to flush and the flush succeeds. `ferror`
 * is what remembers. Getting this wrong on stdout is quieter than on a file
 * and worse in a pipeline — a truncated `--emit-ir` stream is still valid
 * JSONL, just with records missing, and the consumer has no way to tell.
 */
static int finish_stdout(const char *what)
{
    if (ferror(stdout)) {
        fprintf(stderr, "error writing %s: ", what);
        perror(NULL);
        clearerr(stdout);
        return 1;
    }
    if (fflush(stdout) != 0) {
        fprintf(stderr, "error writing %s: ", what);
        perror(NULL);
        return 1;
    }
    return 0;
}

/*
 * Finish writing out, flush, fsync, and close. On any error, unlink tmp_path
 * (if non-NULL) and return -1. On success return 0; *out_slot is set NULL.
 *
 * Check `ferror` before flush: a short `fwrite` can drain the FILE and
 * leave flush/fsync/close succeeding.
 */
static int finalize_output(FILE **out_slot, const char *tmp_path)
{
    FILE *out = *out_slot;
    if (!out)
        return -1;
    if (ferror(out)) {
        fprintf(stderr, "Can't write output (disk full or quota exceeded?): ");
        perror(NULL);
        fclose(out);
        *out_slot = NULL;
        if (tmp_path)
            unlink(tmp_path);
        return -1;
    }
    if (fflush(out) != 0) {
        fprintf(stderr, "Can't flush output: ");
        perror(NULL);
        fclose(out);
        *out_slot = NULL;
        if (tmp_path)
            unlink(tmp_path);
        return -1;
    }
    if (fsync(fileno(out)) != 0) {
        fprintf(stderr, "Can't fsync output: ");
        perror(NULL);
        fclose(out);
        *out_slot = NULL;
        if (tmp_path)
            unlink(tmp_path);
        return -1;
    }
    if (fclose(out) != 0) {
        fprintf(stderr, "Can't close output: ");
        perror(NULL);
        *out_slot = NULL;
        if (tmp_path)
            unlink(tmp_path);
        return -1;
    }
    *out_slot = NULL;
    return 0;
}

/*
 * In-place write: never open the primary path for writing until the new
 * content is fully on disk.
 *
 *   1. Write to a unique same-directory temp (mkstemp).
 *   2. Copy mode (and best-effort owner) from the original.
 *   3. fflush + fsync + close; fail → unlink temp, original untouched.
 *   4. If content is identical, unlink temp (no .bak, inode preserved).
 *   5. Else rename original → collision-safe .bak, then temp → original.
 *      If the second rename fails, restore original from .bak.
 */
/*
 * In-place write. `buf` non-NULL writes those bytes instead of running the
 * fixers, which is how --apply-edits reuses this: mode and ownership
 * preservation, the .bak, the atomic rename and the directory fsync are all
 * hard-won and should exist once, not twice.
 */
static int write_inplace_buf(const char *input_path,
                             const char *buf, size_t buflen)
{
    struct stat st;
    char tmp_path[4096];
    char bak_path[4096];
    int fd;
    FILE *out;
    int n;

    if (stat(input_path, &st) != 0) {
        fprintf(stderr, "Can't stat '%s': ", input_path);
        perror(NULL);
        return 1;
    }

    n = snprintf(tmp_path, sizeof(tmp_path), "%s.mdfix.XXXXXX", input_path);
    if (n < 0 || (size_t)n >= sizeof(tmp_path)) {
        fprintf(stderr, "Path too long for temp file: %s\n", input_path);
        return 1;
    }
    fd = mkstemp(tmp_path);
    if (fd < 0) {
        fprintf(stderr, "Can't create temp file for '%s': ", input_path);
        perror(NULL);
        return 1;
    }

    /* Preserve permission bits the user actually set (0600 stays 0600). */
    if (fchmod(fd, st.st_mode & 07777) != 0) {
        fprintf(stderr, "Can't set mode on temp file: ");
        perror(NULL);
        close(fd);
        unlink(tmp_path);
        return 1;
    }
    /*
     * Best-effort ownership, and best-effort means ignore every failure.
     * Treating anything but EPERM as fatal made mdfix refuse to edit *any*
     * file on mounts that simply do not implement ownership — vfat/exfat,
     * some CIFS/9p/virtiofs and FUSE mounts return ENOTSUP, EINVAL or EROFS.
     * The old code never touched ownership at all, so failing here would be a
     * hard regression for a cosmetic gain.
     */
    (void)fchown(fd, st.st_uid, st.st_gid);

    out = fdopen(fd, "w");
    if (!out) {
        fprintf(stderr, "Can't fdopen temp file: ");
        perror(NULL);
        close(fd);
        unlink(tmp_path);
        return 1;
    }

    if (buf)
        fwrite(buf, 1, buflen, out);
    else
        process(out);
    if (finalize_output(&out, tmp_path) != 0)
        return 1;

    /* No content change: drop the temp, leave the original inode alone. */
    if (files_identical(tmp_path, input_path)) {
        unlink(tmp_path);
        return 0;
    }

    if (build_backup_path(input_path, bak_path, sizeof(bak_path)) != 0) {
        fprintf(stderr, "Path too long for backup file: %s\n", input_path);
        unlink(tmp_path);
        return 1;
    }

    /*
     * Hard-link the original aside rather than renaming it, so input_path
     * names a valid file at every instant. Rename-aside-then-rename-in leaves
     * a window where the path does not exist: a concurrent reader gets
     * ENOENT, and a crash inside it leaves no file at all.
     *
     * lstat, not stat: a dangling symlink at the backup path reports ENOENT
     * under stat, and we would then destroy the link we meant to notice.
     */
    {
        struct stat bst;
        if (lstat(bak_path, &bst) == 0 && unlink(bak_path) != 0) {
            fprintf(stderr, "Can't replace old backup '%s': ", bak_path);
            perror(NULL);
            unlink(tmp_path);
            return 1;
        }
    }

    int linked = (link(input_path, bak_path) == 0);
    if (!linked) {
        /* Filesystems without hard links (some FUSE/SMB mounts) fall back to
         * the rename-aside sequence, losing atomicity but not the backup. */
        if (rename(input_path, bak_path) != 0) {
            fprintf(stderr, "Can't create backup '%s': ", bak_path);
            perror(NULL);
            unlink(tmp_path);
            return 1;
        }
    }

    if (rename(tmp_path, input_path) != 0) {
        fprintf(stderr, "Can't install new file over '%s': ", input_path);
        perror(NULL);
        if (linked) {
            /* input_path was never moved; just drop our extra link. */
            unlink(bak_path);
        } else if (rename(bak_path, input_path) != 0) {
            fprintf(stderr,
                "CRITICAL: failed to restore '%s' from '%s': ",
                input_path, bak_path);
            perror(NULL);
        }
        unlink(tmp_path);
        return 1;
    }

    /*
     * fsync the directory. The file's bytes are already durable, but the
     * rename that made them reachable is a directory operation: without this,
     * a power loss can leave the entry still naming the old inode.
     */
    fsync_parent_dir(input_path);

    if (!opt_quiet)
        printf("Backup: %s\n", bak_path);
    return 0;
}

/*
 * --canonical-lint: produce the real canonical bytes and compare them to the
 * input. Relying on fix_counts alone missed silent normalizations (CRLF→LF,
 * final newline) that change the file while reporting "clean".
 *
 * Returns 0 clean, 2 not canonical, 1 hard error.
 */
static int run_canonical_lint(const char *input_path)
{
    /*
     * Honour TMPDIR. Lint must not need write access to the tree it inspects
     * (a read-only checkout is a normal thing to lint), so the temp lives
     * outside it — but hardcoding /tmp fails in sandboxes where /tmp is
     * absent or read-only and TMPDIR points somewhere usable.
     */
    const char *tmpdir = getenv("TMPDIR");
    if (!tmpdir || !*tmpdir)
        tmpdir = "/tmp";
    char tmp_path[PATH_MAX];
    int n = snprintf(tmp_path, sizeof(tmp_path),
                     "%s%smdfix-lint.XXXXXX",
                     tmpdir, (tmpdir[strlen(tmpdir) - 1] == '/') ? "" : "/");
    if (n < 0 || (size_t)n >= sizeof(tmp_path)) {
        fprintf(stderr, "canonical-lint: temp path too long\n");
        return 1;
    }
    int fd = mkstemp(tmp_path);
    if (fd < 0) {
        fprintf(stderr, "canonical-lint: can't create temp file: ");
        perror(NULL);
        return 1;
    }
    FILE *out = fdopen(fd, "w");
    if (!out) {
        fprintf(stderr, "canonical-lint: can't fdopen temp file: ");
        perror(NULL);
        close(fd);
        unlink(tmp_path);
        return 1;
    }

    process(out);
    if (finalize_output(&out, tmp_path) != 0)
        return 1;

    int same = files_identical(tmp_path, input_path);
    unlink(tmp_path);

    int issues = total_issues();
    if (!same || issues > 0) {
        if (!opt_quiet) {
            if (!same && issues == 0) {
                fprintf(stderr,
                    "canonical-lint: output differs from input "
                    "(normalization not reflected in fix counts).\n");
            }
            int report = issues > 0 ? issues : 1;
            fprintf(stderr,
                "canonical-lint: failed with %d issue%s.\n",
                report, report == 1 ? "" : "s");
        }
        return 2;
    }
    if (!opt_quiet)
        fprintf(stderr, "canonical-lint: clean.\n");
    return 0;
}

static int write_inplace(const char *input_path)
{
    return write_inplace_buf(input_path, NULL, 0);
}

/*
 * Render until the document stops changing. A fixer can change what a line
 * is after the repair that cares has already looked. Counts and diagnostics
 * come from the first pass: ID.1 spans index the file on disk. Fail the run
 * if it does not settle.
 */
#define MAX_RENDER_PASSES 4

static char *render_converged(const char *path, size_t *out_len)
{
    char *buf = NULL;
    size_t len = 0;
    FILE *mem = open_memstream(&buf, &len);
    if (!mem) {
        fprintf(stderr, "error: cannot buffer the result\n");
        return NULL;
    }
    npara = 0;
    process(mem);
    fclose(mem);

    int saved_diag = opt_diagnostics, saved_verbose = opt_verbose;
    int saved_quiet = opt_quiet;
    int saved_counts[NUM_FIXES];
    memcpy(saved_counts, fix_counts, sizeof saved_counts);
    int saved_serial = serial_comma_warnings;
    int saved_number = number_style_warnings;
    int saved_fence = unterminated_fence_warnings;
    int saved_nfc = non_nfc_warnings;

    int settled = 0;
    int io_failed = 0;
    int pass = 1;
    for (; pass < MAX_RENDER_PASSES; pass++) {
        FILE *again = fmemopen(buf, len, "r");
        if (!again) {
            if (len == 0) {
                settled = 1;
                break;
            }
            io_failed = 1;
            break;
        }
        free_lines();
        opt_diagnostics = 0;
        opt_verbose = 0;
        opt_quiet = 1;
        int rc = read_all(again);
        fclose(again);
        if (rc != 0) {
            io_failed = 1;
            break;
        }

        char *next = NULL;
        size_t next_len = 0;
        FILE *sink = open_memstream(&next, &next_len);
        if (!sink) {
            io_failed = 1;
            break;
        }
        npara = 0;
        process(sink);
        fclose(sink);

        if (next_len == len && memcmp(next, buf, len) == 0) {
            free(next);
            settled = 1;
            break;
        }
        free(buf);
        buf = next;
        len = next_len;
    }

    opt_diagnostics = saved_diag;
    opt_verbose = saved_verbose;
    opt_quiet = saved_quiet;
    memcpy(fix_counts, saved_counts, sizeof saved_counts);
    serial_comma_warnings = saved_serial;
    number_style_warnings = saved_number;
    unterminated_fence_warnings = saved_fence;
    non_nfc_warnings = saved_nfc;

    if (io_failed) {
        fprintf(stderr, "error: %s: cannot re-read the rendered buffer\n",
                path);
        free(buf);
        return NULL;
    }
    if (!settled) {
        fprintf(stderr,
            "warning: %s did not settle in %d passes; two fixes may be "
            "undoing each other. Please report this input.\n",
            path, MAX_RENDER_PASSES);
        free(buf);
        return NULL;
    }

    *out_len = len;
    return buf;
}

static int process_file(const char *input_path, const char *output_path)
{
    /* Reset per-file state */
    diag_path = input_path;
    memset(fix_counts, 0, sizeof(fix_counts));
    serial_comma_warnings = 0;
    number_style_warnings = 0;
    unterminated_fence_warnings = 0;
    non_nfc_warnings = 0;
    npara = 0;

    if (opt_apply_edits)
        return apply_edits_file(input_path, output_path);

    /* ── Read the entire input into memory ── */
    FILE *in = fopen(input_path, "r");
    if (!in) {
        fprintf(stderr, "Can't open '%s': ", input_path);
        perror(NULL);
        return 1;
    }
    if (read_all(in) != 0) {
        fclose(in);
        return 1;
    }
    fclose(in);

    if (opt_verbose)
        fprintf(stderr, "Read %d lines from %s\n", nlines, input_path);

    /*
     * ── Read-only IR ──
     * Checked before every write path and before any fixer runs, so the
     * emitted spans describe the file on disk rather than a partially fixed
     * copy. No summary either: the output is a machine interface, and a
     * "3 fixes applied" line on stdout would corrupt the JSONL stream.
     */
    if (opt_emit_ir) {
        emit_ir(stdout, input_path);
        free_lines();
        return finish_stdout("IR");
    }

    /*
     * ── L3 normalization ──
     * After the IR return, before any other transform: everything downstream,
     * including the heading text a consumer slugs into an anchor, then sees
     * one spelling. Recomputing rather than carrying an identifier over is
     * the whole point — a decomposed `Héading` anchors as `heading` in
     * Pandoc, the precomposed one as `héading`.
     */
    if (opt_normalize_nfc && normalize_lines_nfc() != 0) {
        free_lines();
        return 1;
    }

    /* ── Write / lint ── */
    int write_rc = 0;
    if (opt_canonical_lint) {
        write_rc = run_canonical_lint(input_path);
        /* Print the fix/warning summary when there is something to count, or
         * when the gate is clean. Skip it for pure content-diff failures
         * (CRLF / final newline) so we do not print "clean" after a fail. */
        if (!opt_quiet && (write_rc == 0 || total_issues() > 0))
            print_summary(input_path);
        free_lines();
        return write_rc;
    }

    /* One converged rendering, then one write. The three paths below used to
     * call process() each, which is also how they could have disagreed. */
    size_t rendered_len = 0;
    char *rendered = render_converged(input_path, &rendered_len);
    if (!rendered) {
        free_lines();
        return 1;
    }

    if (opt_dryrun) {
        /* Nothing to write; the counts above are the whole point. */
    } else if (opt_inplace) {
        write_rc = write_inplace_buf(input_path, rendered, rendered_len);
        if (write_rc != 0) {
            free(rendered);
            free_lines();
            return write_rc;
        }
    } else {
        FILE *out = fopen(output_path, "w");
        if (!out) {
            fprintf(stderr, "Can't open output '%s': ", output_path);
            perror(NULL);
            free(rendered);
            free_lines();
            return 1;
        }
        fwrite(rendered, 1, rendered_len, out);
        /*
         * Pass the output path so a flush/fsync/close failure removes the
         * partial file. Leaving it behind was doubly bad: main refuses to
         * overwrite an existing output, so the retry also failed and the user
         * was stuck with a silently truncated file until deleting it by hand.
         */
        if (finalize_output(&out, output_path) != 0) {
            free(rendered);
            free_lines();
            return 1;
        }
    }
    free(rendered);

    /* ── Report ── */
    if (!opt_quiet)
        print_summary(input_path);

    if (opt_dryrun)
        printf("(dry run — no files were harmed)\n");

    free_lines();
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * Entry
 * ═══════════════════════════════════════════════════════════════════ */

int main(int argc, char *argv[])
{
    const char *input_path  = NULL;
    const char *output_path = NULL;
    const char **pos = (const char **)malloc(sizeof(char *) * (size_t)(argc + 1));
    int npos = 0;

    /* Parse flags — options may appear anywhere in argv, so
     * `mdfix *.md -i -v` no longer silently drops the trailing flags. */
    int argi = 1;
    while (argi < argc) {
        if (argv[argi][0] != '-' || argv[argi][1] == '\0'
            || isdigit((unsigned char)argv[argi][1])) {
            /* Not an option (don't eat "-1" — could be a filename) */
            pos[npos++] = argv[argi++];
            continue;
        }
        if (strcmp(argv[argi], "--no-arrow-aside") == 0) {
            opt_no_arrow_aside = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--chicago-punct") == 0) {
            opt_chicago_punct = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--chicago-punct-2") == 0) {
            opt_chicago_punct2 = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--serial-comma-lint") == 0) {
            opt_serial_comma_lint = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--chicago-abbrev") == 0) {
            opt_chicago_abbrev = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--chicago-number-lint") == 0) {
            opt_chicago_number_lint = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--canonical") == 0) {
            opt_canonical = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--canonical-lint") == 0) {
            opt_canonical_lint = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--diagnostics") == 0) {
            opt_diagnostics = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--apply-edits") == 0) {
            opt_apply_edits = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--editorial") == 0) {
            opt_editorial = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--no-required") == 0) {
            opt_required = 0;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--emit-ir") == 0) {
            opt_emit_ir = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--diff") == 0) {
            opt_diff = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--normalize-nfc") == 0) {
            opt_normalize_nfc = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--footnote-canonical") == 0) {
            opt_footnote_canonical = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--heading-canonical") == 0) {
            opt_heading_canonical = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--fence-canonical") == 0) {
            opt_fence_canonical = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--pandoc-safe-links") == 0) {
            opt_pandoc_safe_links = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--scrivener-repair") == 0) {
            opt_scrivener_repair = 1;
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--spaced-emdash") == 0) {
            opt_spaced_emdash = 1;
            argi++;
            continue;
        }
        if (strncmp(argv[argi], "--wrap=", 7) == 0) {
            opt_wrap_width = atoi(argv[argi] + 7);
            if (opt_wrap_width < 20) {
                fprintf(stderr, "--wrap width must be >= 20.\n");
                return 1;
            }
            argi++;
            continue;
        }
        if (strcmp(argv[argi], "--wrap") == 0) {
            if (argi + 1 < argc && isdigit((unsigned char)argv[argi + 1][0])) {
                opt_wrap_width = atoi(argv[argi + 1]);
                if (opt_wrap_width < 20) {
                    fprintf(stderr, "--wrap width must be >= 20.\n");
                    return 1;
                }
                argi += 2;
            } else {
                opt_wrap_width = 78;
                argi++;
            }
            continue;
        }
        if (strcmp(argv[argi], "--technical") == 0) {
            opt_canonical = 1;  /* technical implies canonical */
            opt_spaced_emdash = 1;
            if (opt_wrap_width == 0)
                opt_wrap_width = 78;
            argi++;
            continue;
        }
        const char *opt = argv[argi] + 1;
        while (*opt) {
            switch (*opt) {
            case 'i': opt_inplace = 1; break;
            case 'n': opt_dryrun  = 1; opt_verbose = 1; break;
            case 'v': opt_verbose = 1; break;
            case 'q': opt_quiet   = 1; break;
            case 'w': opt_trail_ws = 1; break;
            case 'h': usage(argv[0]); return 0;
            default:
                fprintf(stderr, "Unknown option: -%c\nTry -h, genius.\n", *opt);
                return 1;
            }
            opt++;
        }
        argi++;
    }

    /*
     * ID.3: diagnostics are machine-readable, so they own stderr. Human
     * progress lines interleaved with the JSONL would make the stream
     * unparseable — a consumer cannot skip what it cannot recognize.
     */
    if (opt_diagnostics) {
        opt_verbose = 0;
        opt_quiet = 1;
    }

    if (opt_canonical || opt_canonical_lint)
        enable_canonical_profile();
    if (opt_canonical_lint)
        opt_dryrun = 1;

    /* Positional args */
    if (npos == 0) {
        fprintf(stderr, "No input file? Really?\n\n");
        usage(argv[0]);
        return 1;
    }

    if (opt_canonical_lint && opt_inplace) {
        fprintf(stderr,
            "--canonical-lint is no-write gate mode. Omit -i.\n");
        return 1;
    }

    if (opt_diff && !opt_apply_edits) {
        fprintf(stderr, "--diff previews an edit list; pass --apply-edits.\n");
        return 1;
    }
    if (opt_apply_edits && opt_canonical_lint) {
        fprintf(stderr,
            "--apply-edits writes a document; --canonical-lint is a gate. "
            "Pick one.\n");
        return 1;
    }
    if (opt_apply_edits && opt_emit_ir) {
        fprintf(stderr, "--apply-edits and --emit-ir are opposite halves; "
                        "run them separately.\n");
        return 1;
    }
    /* Stdin holds one edit list for one document. */
    if (opt_apply_edits && opt_inplace && npos != 1) {
        fprintf(stderr,
            "--apply-edits -i takes exactly one input file "
            "(stdin is one edit list).\n");
        return 1;
    }
    if (opt_apply_edits && !opt_inplace && npos > 2) {
        fprintf(stderr,
            "--apply-edits takes one input, or input plus output. "
            "Stdin is one edit list.\n");
        return 1;
    }
    if (opt_apply_edits && !opt_inplace && npos == 1) {
        /* Result goes to stdout; no output file is needed. */
        return process_file(pos[0], NULL);
    }

    if (opt_emit_ir && (opt_inplace || opt_canonical_lint)) {
        fprintf(stderr,
            "--emit-ir only reads: it writes JSONL to stdout and never "
            "touches the input. Omit -i and --canonical-lint.\n");
        return 1;
    }

    /* Multi-file mode: -i (in-place), -n/--canonical-lint (no-write), and
     * --emit-ir (read-only) treat every positional as an input file. */
    if (opt_inplace || opt_dryrun || opt_emit_ir) {
        int exit_code = 0;
        for (int i = 0; i < npos; i++) {
            int rc = process_file(pos[i], NULL);
            /*
             * A hard error (1: unreadable, overlong line, I/O failure) must
             * outrank a lint failure (2). Last-writer-wins let a later
             * non-canonical file overwrite an earlier 1, so CI reported "not
             * canonical" and nobody learned a file had been skipped entirely.
             */
            if (rc == 1)
                exit_code = 1;
            else if (rc != 0 && exit_code == 0)
                exit_code = rc;
        }
        return exit_code;
    }

    /* Explicit-output mode: exactly "input.md output.md". */
    if (npos == 1) {
        fprintf(stderr,
            "Need either -i, -n, or an output file. Try -h.\n");
        return 1;
    }
    if (npos > 2) {
        fprintf(stderr,
            "Multiple input files need -i (or -n). Without -i the second\n"
            "name is the OUTPUT file and the rest would be ignored.\n");
        return 1;
    }

    input_path  = pos[0];
    output_path = pos[1];

    /* Refuse to clobber: `mdfix a.md b.md` without -i is almost always
     * a forgotten -i, and used to overwrite b.md with fixed a.md. */
    FILE *existing = fopen(output_path, "r");
    if (existing) {
        fclose(existing);
        fprintf(stderr,
            "Output file '%s' already exists — refusing to overwrite.\n"
            "Did you forget -i? Otherwise delete the output file first.\n",
            output_path);
        return 1;
    }

    return process_file(input_path, output_path);
}
