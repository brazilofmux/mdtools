
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
 *   -i  Edit in-place (creates .bak backup)
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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>


#line 62 "mdfix.c"
static const int mdfix_scanner_start = 14;
static const int mdfix_scanner_error = -1;

static const int mdfix_scanner_en_main = 14;


#line 61 "mdfix.rl"


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
    LT_TEXT         /* everything else: paragraphs, blockquotes, etc. */
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

static int  serial_comma_warnings = 0;
static int  number_style_warnings = 0;
static int  unterminated_fence_warnings = 0;

static char *lines[MAX_LINES];
static int   nlines = 0;

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
static int fence_prefix(
    const char *line,
    int max_indent,
    int *indent,
    char *marker,
    int *run_length,
    const char **rest)
{
    int i = 0;
    while (line[i] == ' ' || line[i] == '\t')
        i++;
    if (max_indent >= 0 && i > max_indent)
        return 0;

    char c = line[i];
    if (c != '`' && c != '~')
        return 0;
    int start = i;
    while (line[i] == c)
        i++;
    if (i - start < 3)
        return 0;

    *indent = start;
    *marker = c;
    *run_length = i - start;
    *rest = line + i;
    return 1;
}

/* CommonMark/Pandoc fence opener. Backtick info strings cannot contain `. */
static int parse_fence_opener(const char *line, struct fence_state *fence)
{
    const char *rest;
    int indent, run_length;
    char marker;
    if (!fence_prefix(line, -1, &indent, &marker, &run_length, &rest))
        return 0;
    if (marker == '`' && strchr(rest, '`') != NULL)
        return 0;

    fence->active = 1;
    fence->marker = marker;
    fence->length = run_length;
    fence->indent = indent;
    return 1;
}

static int is_fence_closer(const char *line, const struct fence_state *fence)
{
    const char *rest;
    int indent, run_length;
    char marker;
    if (!fence_prefix(line, fence->indent + 3, &indent, &marker, &run_length, &rest))
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

static int is_table_line(const char *line)
{
    const char *p = line;
    while (*p == ' ' || *p == '\t')
        p++;
    return *p == '|';
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
 * Fixers — each modifies line in place, returns 1 if changed
 * ═══════════════════════════════════════════════════════════════════ */

/* Fix 1: Normalize bullet markers to - */
static int fix_bullet(char *line, int linenum)
{
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
    int indent, run_length;
    char marker;
    if (!fence_prefix(line, -1, &indent, &marker, &run_length, &rest))
        return 0;

    char buf[MAX_LINE];
    int bi = 0;
    for (int k = 0; k < indent && bi < MAX_LINE - 1; k++)
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

static void read_all(FILE *fp)
{
    char buf[MAX_LINE];
    nlines = 0;

    while (fgets(buf, sizeof(buf), fp)) {
        /* Strip line endings — we add our own on output */
        size_t len = strlen(buf);
        while (len > 0 && (buf[len - 1] == '\n' || buf[len - 1] == '\r'))
            buf[--len] = '\0';

        if (nlines >= MAX_LINES) {
            fprintf(stderr,
                "Holy shit, %d lines? Write a shorter book.\n", MAX_LINES);
            exit(1);
        }
        lines[nlines] = malloc(MAX_LINE);
        if (!lines[nlines]) {
            perror("malloc failed, out of memory");
            exit(1);
        }
        strncpy(lines[nlines], buf, MAX_LINE - 1);
        lines[nlines][MAX_LINE - 1] = '\0';
        nlines++;
    }
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

    
#line 1484 "mdfix.c"
	{
	cs = mdfix_scanner_start;
	ts = 0;
	te = 0;
	act = 0;
	}

#line 1492 "mdfix.c"
	{
	if ( p == pe )
		goto _test_eof;
	switch ( cs )
	{
tr0:
#line 1861 "mdfix.rl"
	{{p = ((te))-1;}{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr1:
#line 1612 "mdfix.rl"
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
#line 1504 "mdfix.rl"
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
#line 1497 "mdfix.rl"
	{te = p+1;{
                EMIT_DATA(ts, te);
            }}
	goto st14;
tr8:
#line 1497 "mdfix.rl"
	{{p = ((te))-1;}{
                EMIT_DATA(ts, te);
            }}
	goto st14;
tr12:
#line 1796 "mdfix.rl"
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
#line 1841 "mdfix.rl"
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
#line 1819 "mdfix.rl"
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
#line 1861 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr21:
#line 1741 "mdfix.rl"
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
#line 1654 "mdfix.rl"
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
#line 1861 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR((*p));
            }}
	goto st14;
tr32:
#line 1704 "mdfix.rl"
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
#line 1763 "mdfix.rl"
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
#line 1558 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr36:
#line 1540 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                EMIT_CHAR(' ');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr37:
#line 1566 "mdfix.rl"
	{te = p;p--;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr38:
#line 1549 "mdfix.rl"
	{te = p+1;{
                EMIT_CHAR(':');
                EMIT_CHAR('*');
                EMIT_CHAR('*');
                EMIT_CHAR(' ');
                BUMP(FIX_BOLD_COLON);
            }}
	goto st14;
tr39:
#line 1574 "mdfix.rl"
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
#line 1497 "mdfix.rl"
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
#line 1910 "mdfix.c"
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
#line 1936 "mdfix.c"
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
#line 1980 "mdfix.c"
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
#line 2029 "mdfix.c"
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
#line 2048 "mdfix.c"
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
#line 2074 "mdfix.c"
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
#line 2123 "mdfix.c"
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

#line 1868 "mdfix.rl"


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

    enum linetype prev_content_type = LT_BLANK;
    int prev_was_list_ctx = 0;    /* was previous content in a list context? */
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
            flush_paragraph(out);
            fix_fence_canonical(line, i + 1, 1);
            opener.open_line = i + 1;
            fence = opener;
            fix_trailing_ws(line, i + 1);
            fprintf(out, "%s\n", line);
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
         * Determine if we're in a "list context" — the previous content
         * was a list item or a continuation of one (indented text).
         */
        int in_list_context = is_list_type(prev_content_type)
            || (prev_content_type == LT_TEXT && prev_was_list_ctx);

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
        if (is_list_type(type))
            prev_was_list_ctx = 1;
        else if (type == LT_TEXT && is_list_continuation(line))
            prev_was_list_ctx = prev_was_list_ctx; /* preserve */
        else
            prev_was_list_ctx = 0;

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
    read_all(in);
    fclose(in);

    if (opt_verbose)
        fprintf(stderr, "Read %d lines from %s\n", nlines, input_path);

    /* ── Open output ── */
    FILE *out = NULL;
    char bak_path[4096];

    if (opt_dryrun) {
        out = fopen("/dev/null", "w");
    } else if (opt_inplace) {
        snprintf(bak_path, sizeof(bak_path), "%s.bak", input_path);
        if (rename(input_path, bak_path) != 0) {
            fprintf(stderr, "Can't create backup '%s': ", bak_path);
            perror(NULL);
            return 1;
        }
        out = fopen(input_path, "w");
    } else {
        out = fopen(output_path, "w");
    }

    if (!out) {
        fprintf(stderr, "Can't open output: ");
        perror(NULL);
        if (opt_inplace)
            rename(bak_path, input_path);
        return 1;
    }

    /* ── Do the work ── */
    process(out);
    fclose(out);

    /* ── Report ── */
    if (!opt_quiet)
        print_summary(input_path);

    if (opt_dryrun && !opt_canonical_lint) {
        printf("(dry run — no files were harmed)\n");
    } else if (opt_inplace) {
        /* Judge "changed" by content, not fix_counts: some
         * normalizations (CRLF, wrap) don't increment a counter. */
        if (files_identical(input_path, bak_path)) {
            /* No changes — remove the unnecessary backup */
            rename(bak_path, input_path);
        } else if (!opt_quiet) {
            printf("Backup: %s\n", bak_path);
        }
    }

    if (opt_canonical_lint) {
        int issues = total_issues();
        if (issues > 0) {
            if (!opt_quiet) {
                fprintf(stderr,
                    "canonical-lint: failed with %d issue%s.\n",
                    issues, issues == 1 ? "" : "s");
            }
            free_lines();
            return 2;
        }
        if (!opt_quiet)
            fprintf(stderr, "canonical-lint: clean.\n");
    }

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

    /* Multi-file mode: -i (in-place) and -n/--canonical-lint (no-write)
     * treat every positional as an input file. */
    if (opt_inplace || opt_dryrun) {
        int exit_code = 0;
        for (int i = 0; i < npos; i++) {
            int rc = process_file(pos[i], NULL);
            if (rc != 0)
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
