RAGEL  ?= ragel
CC     ?= cc
CFLAGS ?= -O2

.PHONY: all clean asan check-sync

all: mdfix

mdfix: mdfix.c
	$(CC) $(CFLAGS) -o $@ $<

# Sanitizer build. Every fixer mutates its line in place and several of them
# lengthen it, so a wrong-sized line allocation overflows the heap by a few
# bytes — invisible to the test suite, obvious to ASan. Run this after
# touching read_all or any fixer that grows a line.
asan: mdfix.c
	$(CC) -O1 -g -fsanitize=address,undefined -o mdfix-asan mdfix.c
	@err=$$(mktemp); \
	find .. -name '*.md' -not -path '*/.git/*' -print0 \
	| while IFS= read -r -d '' f; do \
		for p in --canonical --technical "--canonical --pandoc-safe-links"; do \
			./mdfix-asan -n -q $$p "$$f" >/dev/null 2>$$err || \
				{ cat $$err; rm -f $$err; exit 1; }; \
		done; \
	done || { rm -f $$err; exit 1; }; \
	rm -f $$err; \
	echo "asan: clean over repo markdown"

# Regenerate C from Ragel when the .rl changes (requires ragel).
# Pre-generated mdfix.c is committed so builds work without ragel.
mdfix.c: mdfix.rl
	$(RAGEL) -G2 $< -o $@

# Verify the committed mdfix.c really is ragel's output for mdfix.rl.
#
# The .c is generated but committed, so the two can drift: a hand-edit to the
# .c, or a .rl change committed without regenerating, both produce a binary
# that does not match its source. Reviewing the .rl then tells you nothing
# about what ships.
#
# Regenerating into a temp directory under the *same filename* matters —
# ragel writes the -o path into #line directives, so comparing against a
# differently-named file reports differences that are pure artifact.
#
# Fails rather than skips when ragel is missing: a source-integrity check that
# silently passes when it cannot run is worse than no check. This target is
# deliberately not part of `make test`, so contributors without ragel can
# still build and test from the committed .c.
check-sync:
	@command -v $(RAGEL) >/dev/null 2>&1 || { \
		echo "check-sync: $(RAGEL) not found — cannot verify mdfix.c against mdfix.rl"; \
		exit 1; }
	@tmp=$$(mktemp -d) || exit 1; \
	cp mdfix.rl "$$tmp/" && (cd "$$tmp" && $(RAGEL) -G2 mdfix.rl -o mdfix.c) || { \
		rm -rf "$$tmp"; echo "check-sync: ragel failed"; exit 1; }; \
	if cmp -s mdfix.c "$$tmp/mdfix.c"; then \
		rm -rf "$$tmp"; \
		echo "check-sync: mdfix.c matches mdfix.rl"; \
	else \
		echo "check-sync: mdfix.c is NOT ragel's output for mdfix.rl."; \
		echo "  Regenerate with: rm -f mdfix/mdfix.c && make -C mdfix"; \
		diff mdfix.c "$$tmp/mdfix.c" | head -20; \
		rm -rf "$$tmp"; \
		exit 1; \
	fi

clean:
	rm -rf mdfix mdfix-asan mdfix-asan.dSYM
	# Keep mdfix.c in the tree; only drop the binary.
