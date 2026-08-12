
#line 1 "mdfix.rl"
/*
 * mdfix.rl — Markdown auto-fixer (behavior-parity source)
 *
 * Takes the rules from linter.py and actually fixes the damn problems
 * instead of just whining about them.
 *
 * Fixes applied:
 *   1. Bullet style normalization (* and + → -)
 *   2. Missing blank line before lists (the pandoc-killer)
 *   3. Missing blank line after lists
 *   4. Bold/italic stripped from headings
 *   5. Trailing whitespace normalized (opt-in: -w)
 *
 * Usage: mdfix [-i] [-n] [-v] [-q] [-w] [--chicago-punct] [--chicago-punct-2]
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
 *       Normalize ATX heading spacing/trailing hashes
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
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>


#line 74 "mdfix.c"
static const int mdfix_scanner_start = 14;
static const int mdfix_scanner_error = -1;

static const int mdfix_scanner_en_main = 14;


#line 73 "mdfix.rl"


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
    "headings: canonical ATX spacing/closing",
    "fences: canonical delimiter formatting",
    "links: bare URLs wrapped for Pandoc",
    "scrivener: split heading emphasis repaired",
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
static int  opt_wrap_width = 0;       /* 0 = disabled */
static int  opt_emit_ir   = 0;        /* structural IR to stdout; never writes */

static int  serial_comma_warnings = 0;
static int  number_style_warnings = 0;
static int  unterminated_fence_warnings = 0;

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

/* "1. ", "23. ", etc. with optional leading whitespace */
static int is_ordered(const char *line)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    if (!isdigit((unsigned char)line[i]))
        return 0;
    while (isdigit((unsigned char)line[i]))
        i++;
    return (line[i] == '.' && line[i + 1] == ' ');
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
static int is_fmatter_delim(const char *line)
{
    return line[0] == '-' && line[1] == '-' && line[2] == '-'
        && (line[3] == '\0' || line[3] == '\n' || line[3] == '\r'
            || line[3] == ' '  || line[3] == '\t');
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
    } else if (isdigit((unsigned char)line[i])) {
        while (isdigit((unsigned char)line[i])) {
            col++;
            i++;
        }
        if (line[i] != '.' && line[i] != ')')
            return -1;
        col++;
        i++;
    } else {
        return -1;
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

static int is_wrappable(const char *line, enum linetype type)
{
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

#define IR_SCHEMA "mdtools-ir-1"

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
static void ir_open(FILE *out, const char *kind, int i0, int i1, int protectd)
{
    fprintf(out,
        "{\"kind\":\"%s\",\"start\":%lld,\"end\":%lld,"
        "\"line\":%d,\"endLine\":%d,\"protected\":%s",
        kind,
        line_off[i0],
        line_off[i1] + line_bytes[i1],
        i0 + 1, i1 + 1,
        protectd ? "true" : "false");
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
 * Word-ish for the intraword-underscore rule, UTF-8 aware.
 *
 * Byte-based isalnum treats every multibyte letter as punctuation, so
 * `漢字_の_強調` lost its underscores. Any lead/continuation byte counts as
 * word-ish here so CJK/Greek/Cyrillic keep intraword underscores like Pandoc.
 * Symbols above U+007F are over-accepted (one-sided: keep text rather than
 * delete); proper classification needs Unicode tables, not mdfix.rl.
 */
static int is_wordish_byte(unsigned char c)
{
    return isalnum(c) || c >= 0x80;
}

/* Emphasis flanking, simplified from CommonMark. `_` additionally refuses to
 * open after, or close before, an alphanumeric — that is +intraword_underscores,
 * which keeps `a_b_c` and `漢字_の_強調` literal. */
static int emphasis_can_open(char marker, unsigned char before, unsigned char after)
{
    if (after == '\0' || after == ' ' || after == '\t')
        return 0;
    if (marker == '_' && is_wordish_byte(before))
        return 0;
    return 1;
}

static int emphasis_can_close(char marker, unsigned char before, unsigned char after)
{
    if (before == '\0' || before == ' ' || before == '\t')
        return 0;
    if (marker == '_' && is_wordish_byte(after))
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
            unsigned char before = (i > from) ? (unsigned char)src[i - 1] : '\0';
            unsigned char after = (i + run < to) ? (unsigned char)src[i + run] : '\0';
            int can_open = emphasis_can_open(marker, before, after);
            int can_close = emphasis_can_close(marker, before, after);

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
    fprintf(out, ",\"level\":%d,\"text\":", level);
    ir_json_string(out, text);
    fputs(",\"plain\":", out);
    ir_json_string(out, plain);
    fputs("}\n", out);
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

    /* Front matter: only the very first line can open it, and an unclosed
     * block runs to EOF — both exactly as process() treats it. */
    if (nlines > 0 && is_fmatter_delim(lines[0])) {
        int j = 1;
        while (j < nlines && !is_fmatter_delim(lines[j]))
            j++;
        int end = (j < nlines) ? j : nlines - 1;
        ir_block(out, "frontmatter", 0, end, 1);
        i = end + 1;
        prev_content_type = LT_TEXT;
        had_blank = 0;
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
         * the run is consumed greedily and then trimmed back. */
        if (indent_columns(line, NULL) >= list_content_col + 4
            && (had_blank || prev_content_type != LT_TEXT))
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

        /* ── Pipe table, or the line block it would otherwise be mistaken for ──
         * The delimiter row is the discriminator; see is_pipe_delim_row.
         * Neither is byte-protected by mdfix today (dialect-policy §7 gaps
         * 1 and 4), which is what "protected": false records. */
        if (is_table_line(line)) {
            int j = i;
            while (j < nlines && is_table_line(lines[j]))
                j++;
            int end = j - 1;
            if (end > i && is_pipe_delim_row(lines[i + 1])) {
                ir_open(out, "table", i, end, 0);
                fputs(",\"form\":\"pipe\"}\n", out);
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

        /* ── Thematic break ── */
        if (is_thematic_break(line)) {
            ir_block(out, "thematic_break", i, i, 1);
            prev_content_type = LT_TEXT;
            list_content_col = 0;
            had_blank = 0;
            continue;
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
            i = j;
            prev_content_type = LT_TEXT;
            list_content_col = 0;
            had_blank = 0;
        }
    }
}

/* ═══════════════════════════════════════════════════════════════════
 * Fixers — each modifies line in place, returns 1 if changed
 * ═══════════════════════════════════════════════════════════════════ */

/* Fix 1: Normalize bullet markers to - */
static int fix_bullet(char *line, int linenum)
{
    /* Spaced "* * *" is a thematic break, not a list item. */
    if (is_thematic_break(line))
        return 0;
    int pos = find_bullet(line);
    if (pos < 0 || line[pos] == '-')
        return 0;

    if (opt_verbose)
        fprintf(stderr, "  line %d: bullet '%c' → '-'\n", linenum, line[pos]);
    line[pos] = '-';
    fix_counts[FIX_BULLET_STYLE]++;
    return 1;
}

/*
 * Fix 4: Strip bold/italic markers from heading text.
 * "## **The Big Idea**" → "## The Big Idea"
 * Handles **, *, and *** (bold-italic).  Preserves escaped \*.
 */
static int fix_heading_fmt(char *line, int linenum)
{
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
        fix_counts[FIX_HEADER_FMT]++;
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
        fix_counts[FIX_BLOCKQUOTE_SPACE]++;
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
        fix_counts[FIX_FOOTNOTE_REF_FMT]++;
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
        fix_counts[FIX_FOOTNOTE_DEF_FMT]++;
        return 1;
    }
    return 0;
}

/*
 * Heading canonicalization:
 * - Ensure single space after ATX hashes.
 * - Remove trailing closing hashes ("## Title ##" -> "## Title").
 */
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

    if (line[i] != ' ' && line[i] != '\0') {
        if (len + 1 < MAX_LINE) {
            memmove(line + i + 1, line + i, len - i + 1);
            line[i] = ' ';
            len++;
            changed = 1;
        }
    } else if (line[i] == ' ') {
        int j = i;
        while (line[j] == ' ')
            j++;
        if (j > i + 1) {
            memmove(line + i + 1, line + j, len - j + 1);
            len -= (j - (i + 1));
            changed = 1;
        }
    }

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
        fix_counts[FIX_HEADING_CANONICAL]++;
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
        fix_counts[FIX_FENCE_CANONICAL]++;
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
        fix_counts[FIX_PANDOC_SAFE_LINKS]++;
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
    fix_counts[FIX_SCRIVENER_SPLIT_EMPH]++;
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
        if (!opt_quiet) {
            fprintf(stderr,
                "  line %d: possible missing serial comma before '%.*s'\n",
                linenum, conj_len - 2, line + i + 1);
        }
        return; /* one warning per line max */
    }
}

/*
 * Fix 5: Normalize trailing whitespace (opt-in via -w).
 *
 * In markdown, a single trailing space is a deliberate line break.
 * So we don't nuke all trailing whitespace — instead we:
 *   - Strip all trailing tabs
 *   - Collapse multiple trailing spaces down to at most one
 * Net effect: intentional line breaks survive, sloppy whitespace doesn't.
 */
static int fix_trailing_ws(char *line, int linenum)
{
    (void)linenum;
    if (!opt_trail_ws)
        return 0;

    int len = (int)strlen(line);
    int orig = len;

    /* Strip all trailing whitespace first */
    while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\t'))
        len--;

    if (len == orig)
        return 0;   /* nothing to do */

    /* Count how many trailing spaces (not tabs) we had */
    int trailing_spaces = 0;
    for (int j = len; j < orig; j++) {
        if (line[j] == ' ')
            trailing_spaces++;
    }

    /* If there were any spaces, preserve exactly one */
    if (trailing_spaces > 0) {
        line[len] = ' ';
        line[len + 1] = '\0';
    } else {
        line[len] = '\0';
    }

    /* Only count as a fix if we actually changed something */
    if ((int)strlen(line) != orig) {
        fix_counts[FIX_TRAILING_WS]++;
        return 1;
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * Paragraph wrapping
 * ═══════════════════════════════════════════════════════════════════ */

#define MAX_PARA (MAX_LINE * 50)

static const char *para_lines_buf[MAX_LINES];
static int npara = 0;

static void emit_wrapped(FILE *out, const char *text, int width)
{
    int len = (int)strlen(text);
    int pos = 0;

    while (pos < len) {
        if (len - pos <= width) {
            fprintf(out, "%s\n", text + pos);
            return;
        }

        /* Find last space at or before pos + width */
        int break_at = -1;
        for (int i = pos; i <= pos + width && i < len; i++) {
            if (text[i] == ' ')
                break_at = i;
        }

        if (break_at <= pos) {
            /* No space within width — find next space (long word) */
            break_at = pos + width;
            while (break_at < len && text[break_at] != ' ')
                break_at++;
        }

        fwrite(text + pos, 1, break_at - pos, out);
        fputc('\n', out);
        pos = break_at;
        while (pos < len && text[pos] == ' ')
            pos++;
    }
}

/*
 * Should line i be joined to line i+1?  Only if the current line looks
 * like it was hard-wrapped (long enough to be near the target width).
 * Short lines signal an intentional paragraph/stanza break.
 */
static int should_join(const char *line, int wrap_width)
{
    int len = (int)strlen(line);
    /* Trim trailing whitespace for length check */
    while (len > 0 && (line[len - 1] == ' ' || line[len - 1] == '\t'))
        len--;
    /* A line shorter than 60% of the wrap width is probably intentionally
     * short — a title, a metadata line, a list-like structure, etc. */
    return len >= (wrap_width * 3 / 5);
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

    /* Build joined paragraphs, breaking where lines are intentionally short */
    char joined[MAX_PARA];
    int pos = 0;

    for (int i = 0; i < npara; i++) {
        const char *s = para_lines_buf[i];
        int slen = (int)strlen(s);
        /* Trim trailing whitespace before joining */
        while (slen > 0 && (s[slen - 1] == ' ' || s[slen - 1] == '\t'))
            slen--;

        if (pos + slen >= MAX_PARA)
            slen = MAX_PARA - pos - 1;
        memcpy(joined + pos, s, slen);
        pos += slen;

        /* If this line is short or is the last, flush the accumulated text */
        if (i == npara - 1 || !should_join(s, opt_wrap_width)) {
            joined[pos] = '\0';
            emit_wrapped(out, joined, opt_wrap_width);
            pos = 0;
        } else {
            /* Join with next line via space */
            if (pos < MAX_PARA - 1)
                joined[pos++] = ' ';
        }
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
        line_off[nlines]   = src_bytes;
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
    int    do_chicago_punct;
    int    do_chicago_punct2;
    int    do_chicago_abbrev;
    int    skip_punct2;
    int    skip_abbrev;
    int    spaced_emdash;
    int    linenum;
};

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

    
#line 2620 "mdfix.c"
	{
	cs = mdfix_scanner_start;
	ts = 0;
	te = 0;
	act = 0;
	}

#line 2628 "mdfix.c"
	{
	if ( p == pe )
		goto _test_eof;
	switch ( cs )
	{
tr0:
#line 2997 "mdfix.rl"
	{{p = ((te))-1;}{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr1:
#line 2748 "mdfix.rl"
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
#line 2640 "mdfix.rl"
	{te = p+1;{
                if (ctx->no_arrow_aside) {
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
#line 2633 "mdfix.rl"
	{te = p+1;{
                EMIT_DATA(ts, te);
            }}
	goto st14;
tr8:
#line 2633 "mdfix.rl"
	{{p = ((te))-1;}{
                EMIT_DATA(ts, te);
            }}
	goto st14;
tr12:
#line 2932 "mdfix.rl"
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
#line 2977 "mdfix.rl"
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
#line 2955 "mdfix.rl"
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
#line 2997 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr21:
#line 2877 "mdfix.rl"
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
#line 2790 "mdfix.rl"
	{te = p+1;{
                if (!ctx->do_chicago_punct) {
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
                        EMIT_CHAR('.');
                        EMIT_CHAR('.');
                        EMIT_CHAR('.');
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
                            EMIT_CHAR('.');
                            EMIT_CHAR('.');
                            EMIT_CHAR('.');
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
#line 2997 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr32:
#line 2840 "mdfix.rl"
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
#line 2899 "mdfix.rl"
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
#line 2694 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr36:
#line 2676 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                EMIT_CHAR(' ');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr37:
#line 2702 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr38:
#line 2685 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                EMIT_CHAR(' ');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr39:
#line 2710 "mdfix.rl"
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
#line 2633 "mdfix.rl"
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
#line 3046 "mdfix.c"
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
#line 3072 "mdfix.c"
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
#line 3116 "mdfix.c"
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
#line 3165 "mdfix.c"
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
#line 3184 "mdfix.c"
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
#line 3210 "mdfix.c"
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
#line 3259 "mdfix.c"
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

#line 3004 "mdfix.rl"


    ctx->out[ctx->oi] = '\0';
}

/* Apply scanner to a line: returns 1 if changed, merges fix counts to globals */
static int apply_scanner(char *line, int linenum)
{
    struct scan_ctx ctx;
    memset(&ctx, 0, sizeof(ctx));

    ctx.no_arrow_aside    = opt_no_arrow_aside;
    ctx.do_chicago_punct  = opt_chicago_punct;
    ctx.do_chicago_punct2 = opt_chicago_punct2;
    ctx.do_chicago_abbrev = opt_chicago_abbrev;
    ctx.skip_punct2       = should_skip_chicago_punct2(line);
    ctx.skip_abbrev       = should_skip_chicago_abbrev(line);
    ctx.spaced_emdash     = opt_spaced_emdash;
    ctx.linenum           = linenum;

    int len = (int)strlen(line);
    run_scanner(&ctx, line, len);

    if (strcmp(line, ctx.out) != 0) {
        strcpy(line, ctx.out);
        /* Merge per-invocation hits into globals */
        for (int i = 0; i < NUM_FIXES; i++) {
            if (ctx.fix_hits[i] > 0) {
                fix_counts[i] += ctx.fix_hits[i];
                if (opt_verbose) {
                    fprintf(stderr, "  line %d: %s\n", linenum, fix_labels[i]);
                }
            }
        }
        return 1;
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════
 * Main processing — single-pass, state-machine style
 * ═══════════════════════════════════════════════════════════════════ */

static void process(FILE *out)
{
    int in_frontmatter     = 0;
    int frontmatter_opened = 0;   /* have we seen the opening --- ? */
    int frontmatter_closed = 0;   /* have we seen the closing --- ? */
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

        /* ── YAML frontmatter handling ──
         * Only the very first line can open frontmatter.
         * The next --- closes it.  After that, --- is a thematic break.
         */
        if (type == LT_FMATTER && !fence.active) {
            if (!frontmatter_opened && i == 0) {
                frontmatter_opened = 1;
                in_frontmatter = 1;
                fix_trailing_ws(line, i + 1);
                fprintf(out, "%s\n", line);
                prev_content_type = LT_TEXT;
                had_blank = 0;
                continue;
            }
            if (in_frontmatter && !frontmatter_closed) {
                frontmatter_closed = 1;
                in_frontmatter = 0;
                fix_trailing_ws(line, i + 1);
                fprintf(out, "%s\n", line);
                prev_content_type = LT_TEXT;
                had_blank = 0;
                continue;
            }
            /* Past frontmatter — this is a thematic break, treat as text */
            type = LT_TEXT;
        }

        /* ── Inside frontmatter: pass through, just trim whitespace ── */
        if (in_frontmatter) {
            fix_trailing_ws(line, i + 1);
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
                fix_trailing_ws(line, i + 1);
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
            fix_trailing_ws(line, i + 1);
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
         * Indented code block: four or more columns past the enclosing
         * container's content column.
         *
         * Two rules keep this from swallowing prose. Indented code cannot
         * interrupt a paragraph, so a line following text is a lazy
         * continuation no matter how far it is indented. And the threshold is
         * relative to the list item's content column, so a continuation line
         * inside `- item` stays prose while genuinely nested code does not.
         *
         * Everything here is emitted verbatim: mdfix was converting arrows
         * and reflowing long lines inside blocks that Pandoc and CommonMark
         * both parse as code.
         */
        if (indent_columns(line, NULL) >= list_content_col + 4
            && (had_blank || prev_content_type != LT_TEXT))
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
        if (!had_blank
            && is_list_type(type)
            && !in_list_context
            && prev_content_type != LT_BLANK)
        {
            flush_paragraph(out);
            if (opt_verbose)
                fprintf(stderr, "  line %d: inserted blank line before list\n",
                        i + 1);
            fprintf(out, "\n");
            fix_counts[FIX_BLANK_BEFORE_LIST]++;
        }

        /*
         * Fix 3: Insert blank line AFTER list.
         * If we're leaving a list into non-list content with no
         * intervening blank line, the markdown structure is ambiguous.
         * Exception: indented continuation lines are part of the list item.
         */
        if (!had_blank
            && !is_list_type(type)
            && in_list_context
            && !is_list_continuation(line))
        {
            flush_paragraph(out);
            if (opt_verbose)
                fprintf(stderr, "  line %d: inserted blank line after list\n",
                        i + 1);
            fprintf(out, "\n");
            fix_counts[FIX_BLANK_AFTER_LIST]++;
        }

        /* Apply pre-scanner C fixers */
        fix_footnote_def(line, i + 1);
        fix_footnote_refs(line, i + 1);
        fix_pandoc_safe_links(line, i + 1);
        fix_blockquote_space(line, i + 1);

        /* Ragel scanner: arrow aside, bold-colon, Chicago punct, abbrevs */
        apply_scanner(line, i + 1);

        /* Apply post-scanner C fixers */
        fix_trailing_ws(line, i + 1);
        fix_bullet(line, i + 1);
        fix_heading_fmt(line, i + 1);
        fix_heading_canonical(line, i + 1);
        if (type == LT_TEXT) {
            lint_serial_comma(line, i + 1);
            lint_chicago_numbers(line, i + 1);
        }

        /* Write the (possibly modified) line */
        if (opt_wrap_width > 0 && is_wrappable(line, type)) {
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

    if (total == 0 && serial_comma_warnings == 0 && number_style_warnings == 0
        && unterminated_fence_warnings == 0) {
        printf("%s: clean. Nothing to fix.\n", path);
        return;
    }

    if (total > 0) {
        printf("\n%s: %d fix%s applied\n", path, total, total == 1 ? "" : "es");
        for (int i = 0; i < NUM_FIXES; i++) {
            if (fix_counts[i] > 0)
                printf("  %-40s %d\n", fix_labels[i], fix_counts[i]);
        }
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
        "  --emit-ir\n"
        "        Emit the structural IR as JSONL on stdout and write nothing.\n"
        "        Byte spans slice the input exactly; see docs/ir-schema.md\n"
        "  --footnote-canonical\n"
        "        Normalize footnote refs/defs to canonical style\n"
        "  --heading-canonical\n"
        "        Normalize ATX heading spacing/trailing hashes\n"
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
        "Fixes (always on):\n"
        "  1. Bullet markers normalized to -  (linter: list_bullet_style)\n"
        "  2. Blank line before lists         (linter: pandoc_list_error,\n"
        "                                      list_spacing_before)\n"
        "  3. Blank line after lists          (linter: list_spacing)\n"
        "  4. Bold/italic stripped from heads  (linter: header_formatting)\n"
        "  5. Bold colons moved inside tags   (**Term**: → **Term:**)\n"
        "  6. Arrow asides converted to em-dash (→ → —)\n"
        "  7. Space added after blockquote    (>Text → > Text)\n"
        "\n"
        "Fix (opt-in with -w):\n"
        "  8. Trailing whitespace normalized  (collapse multiple spaces to one)\n"
        "\n"
        "Fixes (opt-in with --chicago-punct):\n"
        "  9. Em-dash spacing normalized      (word -- word → word—word)\n"
        " 10. Ellipsis normalized             (. . . or .... → ...)\n"
        " 11. Sentence double-space collapsed (\"End.  Next\" → \"End. Next\")\n"
        "\n"
        "Fixes (opt-in with --chicago-punct-2):\n"
        " 12. Remove space before punctuation (word , -> word,)\n"
        " 13. Normalize space after ,;:?!     (\"Hi,there\" -> \"Hi, there\")\n"
        " 14. Move . and , inside quotes      (\"word\". -> \"word.\")\n"
        "\n"
        "Lint (opt-in with --serial-comma-lint):\n"
        "  Warn on likely missing serial commas in simple lists\n"
        "\n"
        "Fixes (opt-in with --chicago-abbrev):\n"
        "  15. Normalize comma after e.g./i.e. (e.g. text -> e.g., text)\n"
        "  16. Enforce period in et al.       (et al -> et al.)\n"
        "\n"
        "Fixes (opt-in with --footnote-canonical):\n"
        "  17. Normalize footnote refs        ([^ 1 ] -> [^1])\n"
        "  18. Normalize footnote defs        ([^1]: text)\n"
        "\n"
        "Fixes (opt-in with --heading-canonical):\n"
        "  19. Normalize heading spacing      (##Title -> ## Title)\n"
        "  20. Remove trailing heading hashes (## Title ## -> ## Title)\n"
        "\n"
        "Fixes (opt-in with --fence-canonical):\n"
        "  21. Normalize fence delimiters     (opening/closing fence lines)\n"
        "\n"
        "Fixes (opt-in with --pandoc-safe-links):\n"
        "  22. Wrap bare URLs as autolinks    (https://x -> <https://x>)\n"
        "\n"
        "Fixes (opt-in with --scrivener-repair):\n"
        "  23. Repair split heading emphasis  (# *Head ... tail* -> # Head ... tail)\n"
        "\n"
        "Lint (opt-in with --chicago-number-lint):\n"
        "  Warn on likely Chicago number-style issues in prose\n"
        "\n"
        "Fixes (opt-in with --spaced-emdash):\n"
        " 24. Em-dashes keep surrounding spaces (word — word, not word—word)\n"
        "\n"
        "Fixes (opt-in with --wrap[=N]):\n"
        " 25. Hard-wrap paragraph text at N columns (default 78)\n"
        "     Skips headings, lists, tables, code blocks, blockquotes\n"
        "\n"
        "Profile:\n"
        "  --canonical enables: -w, --chicago-punct, --chicago-punct-2,\n"
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
 * Finish writing out, flush, fsync, and close. On any error, unlink tmp_path
 * (if non-NULL) and return -1. On success return 0; *out_slot is set NULL.
 */
static int finalize_output(FILE **out_slot, const char *tmp_path)
{
    FILE *out = *out_slot;
    if (!out)
        return -1;
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
static int write_inplace(const char *input_path)
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

static int process_file(const char *input_path, const char *output_path)
{
    /* Reset per-file state */
    memset(fix_counts, 0, sizeof(fix_counts));
    serial_comma_warnings = 0;
    number_style_warnings = 0;
    unterminated_fence_warnings = 0;
    npara = 0;

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
        if (fflush(stdout) != 0) {
            fprintf(stderr, "error writing IR: ");
            perror(NULL);
            return 1;
        }
        return 0;
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

    if (opt_dryrun) {
        FILE *out = fopen("/dev/null", "w");
        if (!out) {
            fprintf(stderr, "Can't open /dev/null: ");
            perror(NULL);
            free_lines();
            return 1;
        }
        process(out);
        fclose(out);
    } else if (opt_inplace) {
        write_rc = write_inplace(input_path);
        if (write_rc != 0) {
            free_lines();
            return write_rc;
        }
    } else {
        FILE *out = fopen(output_path, "w");
        if (!out) {
            fprintf(stderr, "Can't open output '%s': ", output_path);
            perror(NULL);
            free_lines();
            return 1;
        }
        process(out);
        /*
         * Pass the output path so a flush/fsync/close failure removes the
         * partial file. Leaving it behind was doubly bad: main refuses to
         * overwrite an existing output, so the retry also failed and the user
         * was stuck with a silently truncated file until deleting it by hand.
         */
        if (finalize_output(&out, output_path) != 0) {
            free_lines();
            return 1;
        }
    }

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
        if (strcmp(argv[argi], "--emit-ir") == 0) {
            opt_emit_ir = 1;
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
